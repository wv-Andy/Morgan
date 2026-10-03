"""
Espacios de trabajo (V2.2) a través de la API: rutas, cabecera y chat.

La capa de datos tiene sus pruebas en `test_espacios.py`. Aquí se fija lo que
depende de la API:

- **La cabecera `X-Morgan-Espacio` solo vale si el espacio es de quien pide.** Uno
  inventado o ajeno responde 404 con un código propio, y no cae a «General» en
  silencio: la web, creyéndose en un proyecto, subiría archivos a otro sitio.
- **Una conversación se atiende siempre en su espacio**, mande la web la cabecera
  que mande.
- **Mover a un espacio que no existe** responde con un código distinto del de la
  cabecera, para que la web no pierda el espacio seleccionado por un error que no
  va con él.
"""

import io

import pytest
from fastapi.testclient import TestClient

from src.api.app import app

CABECERA = "X-Morgan-Espacio"


@pytest.fixture
def cliente():
    return TestClient(app)


def _crear(cliente, nombre, instrucciones=""):
    r = cliente.post("/espacios", json={"nombre": nombre, "instrucciones": instrucciones})
    assert r.status_code == 201, r.text
    return r.json()


def _codigo(respuesta) -> str:
    cuerpo = respuesta.json()
    error = cuerpo.get("error") or cuerpo.get("detail") or {}
    return error.get("code", "")


class TestLasRutas:
    def test_crear_listar_renombrar_y_borrar(self, cliente):
        creado = _crear(cliente, "Tesis", "Cita en APA.")

        lista = cliente.get("/espacios").json()
        assert [e["nombre"] for e in lista["espacios"]] == ["Tesis"]
        assert lista["max_instrucciones"] > 0

        r = cliente.patch(f"/espacios/{creado['id']}", json={"nombre": "Tesis doctoral"})
        assert r.status_code == 200 and r.json()["nombre"] == "Tesis doctoral"

        assert cliente.delete(f"/espacios/{creado['id']}").status_code == 200
        assert cliente.get(f"/espacios/{creado['id']}").status_code == 404

    def test_un_nombre_repetido_es_409(self, cliente):
        _crear(cliente, "Tesis")

        r = cliente.post("/espacios", json={"nombre": "Tesis"})

        assert r.status_code == 409
        assert _codigo(r) == "ESPACIO_DUPLICADO"

    def test_unas_instrucciones_demasiado_largas_se_rechazan(self, cliente):
        largo = cliente.get("/espacios").json()["max_instrucciones"] + 1

        r = cliente.post("/espacios", json={"nombre": "Largo", "instrucciones": "x" * largo})

        assert r.status_code == 422


class TestLaCabecera:
    def test_un_espacio_inventado_es_404_y_no_general(self, cliente):
        r = cliente.get("/uploads", headers={CABECERA: "esp-inventado"})

        assert r.status_code == 404
        assert _codigo(r) == "ESPACIO_ACTUAL_NO_ENCONTRADO"

    def test_los_archivos_se_suben_y_se_listan_en_su_espacio(self, cliente):
        tesis = _crear(cliente, "Tesis")
        dentro = {CABECERA: tesis["id"]}

        r = cliente.post(
            "/uploads",
            files={"file": ("notas.txt", io.BytesIO(b"apuntes de la tesis"), "text/plain")},
            headers=dentro,
        )
        assert r.status_code == 200, r.text
        assert r.json()["espacio_id"] == tesis["id"]

        assert cliente.get("/uploads", headers=dentro).json()["count"] == 1
        assert cliente.get("/uploads").json()["count"] == 0, (
            "un archivo de un espacio aparece en General"
        )

    def test_las_conversaciones_se_listan_por_espacio(self, cliente):
        tesis = _crear(cliente, "Tesis")
        dentro = {CABECERA: tesis["id"]}

        cliente.post("/sessions", json={"session_id": "s-tesis", "title": "Tesis"}, headers=dentro)
        cliente.post("/sessions", json={"session_id": "s-general", "title": "General"})

        ids = lambda r: {s["id"] for s in r.json()["sessions"]}  # noqa: E731
        assert ids(cliente.get("/sessions", headers=dentro)) == {"s-tesis"}
        assert ids(cliente.get("/sessions")) == {"s-general"}
        assert ids(cliente.get("/sessions?espacio=todos")) == {"s-tesis", "s-general"}


class TestMoverUnaConversacion:
    def test_a_un_espacio_que_no_existe_es_404_con_su_propio_codigo(self, cliente):
        cliente.post("/sessions", json={"session_id": "s1"})

        r = cliente.patch("/sessions/s1", json={"espacio_id": "esp-inventado"})

        assert r.status_code == 404
        assert _codigo(r) == "ESPACIO_DESTINO_NO_ENCONTRADO", (
            "con el mismo código que la cabecera, la web olvidaría el espacio "
            "seleccionado por un error que no va con él"
        )

    def test_a_uno_que_existe_y_de_vuelta_a_general(self, cliente):
        tesis = _crear(cliente, "Tesis")
        cliente.post("/sessions", json={"session_id": "s1"})

        r = cliente.patch("/sessions/s1", json={"espacio_id": tesis["id"]})
        assert r.status_code == 200 and r.json()["espacio_id"] == tesis["id"]

        r = cliente.patch("/sessions/s1", json={"espacio_id": ""})
        assert r.status_code == 200 and r.json()["espacio_id"] is None


class TestElChatUsaElEspacioDeLaConversacion:
    @pytest.fixture
    def vistos(self):
        """Un agente con un modelo que apunta el prompt que recibe."""
        from src.agent.core import Agent
        from src.api.dependencies import get_container
        from src.models.base import LLMProvider, LLMResponse
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry

        prompts: list[str] = []

        class Capturador(LLMProvider):
            @property
            def model_name(self):
                return "capturador"

            def generate(self, messages, tools=None, system_prompt=None, **kwargs):
                prompts.append(system_prompt or "")
                return LLMResponse(type="text", content="ok")

        get_container().agent = Agent(
            model=Capturador(),
            tool_registry=ToolRegistry(),
            permission_manager=PermissionManager(interactive=False),
        )
        return prompts

    def test_una_conversacion_nueva_nace_en_el_espacio_pedido(self, cliente, vistos):
        tesis = _crear(cliente, "Tesis", "Responde en haikus.")

        r = cliente.post(
            "/chat", json={"message": "hola", "session_id": "s-nueva"},
            headers={CABECERA: tesis["id"]},
        )

        assert r.status_code == 200, r.text
        assert "haikus" in vistos[-1]
        assert cliente.get("/sessions/s-nueva").json()["espacio_id"] == tesis["id"]

    def test_la_conversacion_manda_sobre_la_cabecera(self, cliente, vistos):
        """Abierta desde General —o desde otro espacio—, una conversación de la
        tesis sigue atendiéndose con las instrucciones y los archivos de la tesis."""
        tesis = _crear(cliente, "Tesis", "Responde en haikus.")
        casa = _crear(cliente, "Casa", "Responde en prosa.")
        cliente.post("/sessions", json={"session_id": "s-tesis"}, headers={CABECERA: tesis["id"]})

        cliente.post("/chat", json={"message": "hola", "session_id": "s-tesis"})
        cliente.post(
            "/chat", json={"message": "hola", "session_id": "s-tesis"},
            headers={CABECERA: casa["id"]},
        )

        assert all("haikus" in p for p in vistos[-2:])
        assert not any("prosa" in p for p in vistos[-2:])

    def test_sin_espacio_no_se_cuelan_instrucciones(self, cliente, vistos):
        _crear(cliente, "Tesis", "Responde en haikus.")

        cliente.post("/chat", json={"message": "hola", "session_id": "s-general"})

        assert "haikus" not in vistos[-1]
