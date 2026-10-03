"""
Regresión de los flujos principales, de extremo a extremo (2.3-B).

Muchas piezas de Morgan tienen sus pruebas por separado. Lo que faltaba era el
**recorrido completo** que hace una persona: registrarse, hablar, que Morgan use
una herramienta, aprobar un plan, trabajar en un espacio… Un defecto en la costura
entre dos piezas probadas no lo ve ninguna de las dos pruebas.

Cómo están montadas:

- **Por HTTP**, con `TestClient`, contra la aplicación entera.
- **En el modo de la web en la nube** (`cloud` y cuentas exigidas), que es lo que
  usa la gente. Sin Supabase: el `conftest` vacía los secretos y la base es SQLite.
- **Con el agente real** y un modelo simulado que obedece un guion, incluido
  pedir herramientas. Nunca se llama a un modelo de verdad.

Un flujo por clase, en el orden de `docs/plan-3.0.md` (2.3-B).
"""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from src.api import dependencies
from src.api.app import create_app
from src.config import reset_settings
from src.identidad.cuotas import Cuotas
from src.models.base import LLMResponse, ToolCallRequest
from src.models.mock import MockLLMProvider

CLAVE = "contrasena-larga"


class ModeloDeGuion(MockLLMProvider):
    """El simulado de siempre, que además apunta el prompt de sistema de cada llamada."""

    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []

    def generate(self, messages, tools=None, system_prompt=None):
        self.prompts.append(system_prompt or "")
        return super().generate(messages, tools=tools, system_prompt=system_prompt)

    def herramienta(self, nombre: str, **argumentos):
        self.queue_response(LLMResponse(
            type="tool_call",
            tool_calls=[ToolCallRequest(name=nombre, arguments=argumentos, id=f"llamada-{nombre}")],
        ))

    def resultados_de_herramientas(self) -> list[str]:
        """Lo que el agente le devolvió al modelo como resultado de herramientas."""
        vistos = []
        for llamada in self._calls:
            for m in llamada:
                if getattr(m, "role", "") == "tool":
                    vistos.append(m.content or json.dumps(m.tool_result or {}, ensure_ascii=False))
        return vistos


@pytest.fixture
def modelo(monkeypatch):
    guion = ModeloDeGuion()
    monkeypatch.setattr(dependencies, "build_provider_chain", lambda settings: (guion, ["guion"]))
    return guion


@pytest.fixture
def app(monkeypatch, modelo):
    monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    reset_settings()
    dependencies.reset_container()
    aplicacion = create_app()
    contenedor = dependencies.get_container()
    assert contenedor.agent is not None and contenedor.agent.model is modelo
    assert "Supabase" not in type(contenedor.repositories).__name__
    yield aplicacion
    reset_settings()
    dependencies.reset_container()


class Persona:
    """Un navegador con su sesión: cookies y la cabecera CSRF ya puestas."""

    def __init__(self, app, nombre: str):
        self.nombre = nombre
        self.web = TestClient(app, base_url="https://testserver")
        r = self.web.post("/auth/registro", json={
            "username": nombre, "email": f"{nombre}@ejemplo.co", "password": CLAVE,
        })
        assert r.status_code == 200, r.text
        self.id = r.json()["usuario"]["id"]
        self._csrf(r.json()["csrf"])

    def _csrf(self, valor):
        self.web.headers["X-Morgan-CSRF"] = valor

    def get(self, ruta, **k):
        return self.web.get(ruta, **k)

    def post(self, ruta, **k):
        return self.web.post(ruta, **k)

    def chat(self, mensaje, sesion, **extra):
        r = self.web.post("/chat", json={"message": mensaje, "session_id": sesion, **extra})
        assert r.status_code == 200, r.text
        return r.json()

    def stream(self, mensaje, sesion):
        r = self.web.post("/chat/stream", json={"message": mensaje, "session_id": sesion})
        assert r.status_code == 200, r.text
        return [json.loads(linea) for linea in r.text.splitlines() if linea.strip()]


# ── 1. La cuenta ───────────────────────────────────────────────────────────────


class TestLaCuenta:
    """Registro → sesión → verificar el correo → salir → no pasa → volver a entrar."""

    def test_recorrido_completo(self, app):
        ana = Persona(app, "ana")

        yo = ana.get("/auth/yo").json()
        assert yo["autenticado"] is True
        assert yo["usuario"]["email_verificado"] is False

        servicio = dependencies.get_container()
        from src.identidad.cuentas import ServicioDeCuentas
        from src.identidad.repositorio import repositorio_de_cuentas

        token, _ = ServicioDeCuentas(repositorio_de_cuentas(servicio.repositories)).solicitar_verificacion(ana.id)
        assert ana.post("/auth/verificar", json={"token": token}).status_code == 200
        assert ana.get("/auth/yo").json()["usuario"]["email_verificado"] is True

        assert ana.post("/auth/logout").status_code == 200
        assert ana.get("/sessions").status_code == 401

        r = ana.post("/auth/login", json={"identificador": "ana", "password": CLAVE})
        assert r.status_code == 200
        ana._csrf(r.json()["csrf"])
        assert ana.get("/sessions").status_code == 200


# ── 2. Un turno con herramienta, por streaming ─────────────────────────────────


class TestUnTurnoConHerramienta:
    """Lo que hace la web: `/chat/stream`, Morgan usa una herramienta, se guarda todo."""

    def test_eventos_en_orden_y_efecto_guardado(self, app, modelo):
        ana = Persona(app, "ana")
        modelo.herramienta("remember_fact", key="color", value="verde", category="preference")
        modelo.queue_text("Apuntado: tu color favorito es el verde.")

        eventos = ana.stream("Recuerda que mi color favorito es el verde", "s-color")

        tipos = [e["tipo"] for e in eventos]
        assert tipos[0] == "inicio"
        assert tipos[-1] == "fin"
        herramientas = [e for e in eventos if e["tipo"] == "herramienta"]
        assert [(e["nombre"], e["estado"]) for e in herramientas] == [
            ("remember_fact", "empieza"), ("remember_fact", "ok"),
        ]
        assert "verde" in eventos[-1]["response"]

        recuerdos = ana.get("/memory").json()
        assert any(m["key"] == "color" and m["value"] == "verde" for m in recuerdos["memories"])

        mensajes = ana.get("/sessions/s-color/messages").json()["messages"]
        roles = [m["role"] for m in mensajes]
        assert roles[0] == "user" and roles[-1] == "assistant"


# ── 3. Un plan ─────────────────────────────────────────────────────────────────


class TestUnPlan:
    """Morgan propone algo arriesgado → queda pendiente → la persona decide."""

    PASO = {"descripcion": "borrar el borrador", "herramienta": "delete_file", "argumentos": {"path": "b.txt"}}

    def _proponer(self, persona, modelo, sesion):
        modelo.herramienta("create_plan", objetivo="limpiar", pasos=[self.PASO])
        # Sin texto del modelo detrás: un plan pendiente lo cierra el núcleo (4.1.5).
        persona.chat("limpia el borrador", sesion)
        pendientes = persona.get(f"/planes?session_id={sesion}&solo_pendientes=true").json()["planes"]
        assert len(pendientes) == 1, "el plan no quedó pendiente en su conversación"
        return pendientes[0]["id"]

    def test_aprobar(self, app, modelo):
        ana = Persona(app, "ana")
        plan_id = self._proponer(ana, modelo, "s-plan")

        assert ana.post(f"/planes/{plan_id}/aprobar").status_code == 200
        assert ana.get(f"/planes/{plan_id}").json()["plan"]["estado"] == "aprobado"
        assert ana.get("/planes?session_id=s-plan&solo_pendientes=true").json()["planes"] == []

    def test_rechazar_y_que_el_modelo_lo_sepa(self, app, modelo):
        ana = Persona(app, "ana")
        plan_id = self._proponer(ana, modelo, "s-plan")

        assert ana.post(f"/planes/{plan_id}/rechazar", json={"motivo": "no"}).status_code == 200

        modelo.herramienta("get_plan", plan_id=plan_id)
        modelo.queue_text("Entendido, no lo hago.")
        ana.chat("¿lo apruebo?", "s-plan")

        assert any("RECHAZ" in r for r in modelo.resultados_de_herramientas())

    def test_el_plan_de_otra_persona_no_se_aprueba(self, app, modelo):
        ana = Persona(app, "ana")
        plan_id = self._proponer(ana, modelo, "s-plan")

        bruno = Persona(app, "bruno")
        assert bruno.post(f"/planes/{plan_id}/aprobar").status_code == 404
        assert ana.get(f"/planes/{plan_id}").json()["plan"]["estado"] == "pendiente"


# ── 3b. Planes de dos personas a la vez (auditoría 2.3) ────────────────────────


class ModeloQueEspera(MockLLMProvider):
    """Sin guion compartido: decide mirando solo los mensajes de SU turno.

    En la primera llamada de cada turno espera a que llegue el otro (`Barrier`):
    así los dos turnos están **a la vez** dentro del modelo, que es el momento en
    que un contexto mal copiado apuntaría el plan a nombre de quien no es.
    """

    def __init__(self):
        super().__init__()
        self.juntos = threading.Barrier(2, timeout=10)
        self.se_cruzaron = False

    def generate(self, messages, tools=None, system_prompt=None):
        ultimo = messages[-1]
        if ultimo.role == "tool":
            return LLMResponse(type="text", content="Te propongo un plan.")
        quien = "ana" if "de ana" in ultimo.content else "bruno"
        self.juntos.wait()
        self.se_cruzaron = True
        return LLMResponse(type="tool_call", tool_calls=[ToolCallRequest(
            name="create_plan", id=f"plan-{quien}",
            arguments={"objetivo": f"plan de {quien}", "pasos": [TestUnPlan.PASO]},
        )])


class TestPlanesDeDosALaVez:
    """Lo que quedaba de la auditoría: dos personas proponiendo planes a la vez.

    El agente es UNO para todos. Si el usuario del turno se leyera de un sitio
    compartido y no del contexto de cada petición, el plan de Ana podría quedar a
    nombre de Bruno —y aprobarlo él—. Con los dos turnos parados a la vez dentro
    del modelo, es el peor momento posible.
    """

    @pytest.fixture
    def modelo(self, monkeypatch):
        espera = ModeloQueEspera()
        monkeypatch.setattr(dependencies, "build_provider_chain", lambda settings: (espera, ["espera"]))
        return espera

    def test_cada_plan_queda_a_nombre_de_quien_lo_pidio(self, app, modelo):
        ana = Persona(app, "ana")
        bruno = Persona(app, "bruno")

        hilos = [
            threading.Thread(target=ana.chat, args=("organiza lo de ana", "s-ana")),
            threading.Thread(target=bruno.chat, args=("organiza lo de bruno", "s-bruno")),
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(30)
        assert modelo.se_cruzaron, "los dos turnos no llegaron a coincidir: la prueba no prueba nada"

        suyo = ana.get("/planes?session_id=s-ana&solo_pendientes=true").json()["planes"]
        del_otro = bruno.get("/planes?session_id=s-bruno&solo_pendientes=true").json()["planes"]
        assert [x["objetivo"] for x in suyo] == ["plan de ana"]
        assert [x["objetivo"] for x in del_otro] == ["plan de bruno"]

        # Ninguno ve ni aprueba el del otro; cada uno aprueba el suyo.
        assert ana.get(f"/planes/{del_otro[0]['id']}").status_code == 404
        assert bruno.post(f"/planes/{suyo[0]['id']}/aprobar").status_code == 404
        assert ana.post(f"/planes/{suyo[0]['id']}/aprobar").status_code == 200
        assert bruno.post(f"/planes/{del_otro[0]['id']}/aprobar").status_code == 200


# ── 4. La memoria ──────────────────────────────────────────────────────────────


class TestLaMemoria:
    """Recordar → consultar → que llegue al prompt → olvidar."""

    def test_recorrido_completo(self, app, modelo):
        ana = Persona(app, "ana")

        r = ana.post("/memory", json={"key": "editor", "value": "VS Code", "category": "preference"})
        assert r.status_code in (200, 201), r.text
        assert any(m["key"] == "editor" for m in ana.get("/memory").json()["memories"])

        modelo.herramienta("recall_memory", query="editor")
        modelo.queue_text("Usas VS Code.")
        ana.chat("¿qué editor uso?", "s-mem")
        assert any("VS Code" in r for r in modelo.resultados_de_herramientas())

        assert ana.web.delete("/memory/editor").status_code == 200
        assert not any(m["key"] == "editor" for m in ana.get("/memory").json()["memories"])

    def test_lo_que_recuerda_una_persona_no_lo_ve_otra(self, app, modelo):
        ana = Persona(app, "ana")
        ana.post("/memory", json={"key": "secreto", "value": "de ana"})

        bruno = Persona(app, "bruno")
        modelo.herramienta("recall_memory", query="secreto")
        modelo.queue_text("No tengo nada.")
        bruno.chat("¿qué sabes?", "s-b")

        assert not any("de ana" in r for r in modelo.resultados_de_herramientas())
        assert bruno.get("/memory").json()["memories"] == []


# ── 5. Un archivo que se vuelve conocimiento ───────────────────────────────────


class TestArchivoAConocimiento:
    """Subir → indexar desde un turno → encontrarlo en otro."""

    def test_recorrido_completo(self, app, modelo):
        ana = Persona(app, "ana")
        subido = ana.post("/uploads", files={"file": ("manual.md", b"# Correo\n\nEl servidor SMTP es smtp.ejemplo.co.")})
        assert subido.status_code == 200, subido.text
        upload_id = subido.json()["id"]

        modelo.herramienta("index_document", upload_id=upload_id)
        modelo.queue_text("Guardado.")
        ana.chat("guarda el manual", "s-doc", attachments=[upload_id])

        modelo.herramienta("search_knowledge", consulta="servidor smtp")
        modelo.queue_text("Es smtp.ejemplo.co.")
        ana.chat("¿cuál es el servidor de correo?", "s-doc")

        resultados = modelo.resultados_de_herramientas()
        assert any("smtp.ejemplo.co" in r for r in resultados[1:]), resultados

        bruno = Persona(app, "bruno")
        modelo.herramienta("search_knowledge", consulta="servidor smtp")
        modelo.queue_text("Nada.")
        antes = len(modelo.resultados_de_herramientas())
        bruno.chat("¿cuál es el servidor?", "s-b")
        assert not any("smtp.ejemplo.co" in r for r in modelo.resultados_de_herramientas()[antes:])


# ── 6. Un espacio de trabajo ───────────────────────────────────────────────────


class TestUnEspacio:
    """Crear → conversar dentro → sus instrucciones llegan al turno, y solo a ese."""

    def test_recorrido_completo(self, app, modelo):
        ana = Persona(app, "ana")
        r = ana.post("/espacios", json={"nombre": "Tesis", "instrucciones": "Responde en tono académico."})
        assert r.status_code in (200, 201), r.text
        espacio = (r.json().get("espacio") or r.json())["id"]

        modelo.queue_text("De acuerdo.")
        ana.web.post("/chat", json={"message": "hola", "session_id": "s-tesis"},
                     headers={"X-Morgan-Espacio": espacio})
        assert "tono académico" in modelo.prompts[-1]

        conversaciones = ana.get("/sessions", headers={"X-Morgan-Espacio": espacio}).json()["sessions"]
        assert [c["id"] for c in conversaciones] == ["s-tesis"]

        bruno = Persona(app, "bruno")
        modelo.queue_text("Hola.")
        bruno.chat("hola", "s-general")
        assert "tono académico" not in modelo.prompts[-1], "las instrucciones de Ana llegaron al turno de Bruno"

        cruzado = bruno.web.post("/chat", json={"message": "hola"}, headers={"X-Morgan-Espacio": espacio})
        assert cruzado.status_code == 404


# ── 7. El cupo ─────────────────────────────────────────────────────────────────


class TestElCupo:
    """Agotado el cupo, no se llama al modelo y se explica."""

    def test_cupo_personal_y_global(self, app, modelo):
        dependencies.get_container().uso.cuotas = Cuotas(mensajes=1, global_mensajes=2)

        ana = Persona(app, "ana")
        ana.chat("uno", "s-1")
        llamadas = modelo.call_count

        r = ana.web.post("/chat", json={"message": "dos", "session_id": "s-1"})
        assert r.status_code == 429
        assert "límite de 1" in r.json()["error"]["message"]

        bruno = Persona(app, "bruno")
        bruno.chat("uno", "s-2")
        carla = Persona(app, "carla")
        r = carla.web.post("/chat", json={"message": "uno", "session_id": "s-3"})
        assert r.status_code == 429
        assert "todas las cuentas" in r.json()["error"]["message"]

        assert modelo.call_count == llamadas + 1, "el modelo se llamó con el cupo agotado"


# ── 8. La cadena de modelos ────────────────────────────────────────────────────


class TestLaCadenaDeModelos:
    """Principal agotado → relevo de Groq → y si también, Gemini. La web lo sabe."""

    def test_recorrido_completo(self, monkeypatch):
        from src.models.fallback import FallbackProvider

        class Eslabon(MockLLMProvider):
            def __init__(self, nombre, fallo=None, relevo=False):
                super().__init__()
                self._model_name = nombre
                self.fallo = fallo
                self.es_relevo_de_cuota = relevo

            def generate(self, messages, tools=None, system_prompt=None):
                if self.fallo:
                    raise self.fallo
                return LLMResponse(type="text", content=f"contesta {self._model_name}")

        cuota = RuntimeError("Error code: 429 - Rate limit reached (TPD)")
        principal = Eslabon("Groq:openai/gpt-oss-120b", fallo=cuota)
        relevo = Eslabon("Groq:openai/gpt-oss-20b", relevo=True)
        gemini = Eslabon("gemini")
        cadena = FallbackProvider(principal, relevo, gemini)
        monkeypatch.setattr(dependencies, "build_provider_chain", lambda s: (cadena, ["groq", "groq@20b", "gemini"]))
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        dependencies.reset_container()
        try:
            app = create_app()
            ana = Persona(app, "ana")

            primero = ana.chat("hola", "s-1")
            relevo.fallo = cuota
            segundo = ana.chat("otra vez", "s-1")
        finally:
            reset_settings()
            dependencies.reset_container()

        assert primero["response"] == "contesta Groq:openai/gpt-oss-20b"
        assert primero["etapas"]["proveedor"] == "Groq:openai/gpt-oss-20b"
        assert primero["etapas"]["respaldo"] == "si"
        assert segundo["response"] == "contesta gemini"


# ── 9. Borrar la cuenta ────────────────────────────────────────────────────────


class TestBorrarLaCuenta:
    """Borrar → sus datos desaparecen → no puede entrar → otra cuenta con su nombre no hereda nada."""

    def test_recorrido_completo(self, app, modelo):
        ana = Persona(app, "ana")
        ana.post("/memory", json={"key": "privado", "value": "algo"})
        modelo.queue_text("hola")
        ana.chat("hola", "s-ana")

        r = ana.web.request("DELETE", "/auth/cuenta", json={"password": CLAVE})
        assert r.status_code == 200, r.text

        otra = TestClient(app, base_url="https://testserver")
        assert otra.post("/auth/login", json={"identificador": "ana", "password": CLAVE}).status_code == 401

        nueva = Persona(app, "ana")
        assert nueva.id != ana.id
        assert nueva.get("/memory").json()["memories"] == []
        assert nueva.get("/sessions").json()["sessions"] == []

        # Y en la base, no solo en lo que se ve: una cuenta nueva no ve lo viejo
        # porque tiene otro identificador, aunque los datos siguieran ahí. La
        # batería de mutaciones lo demostró: sin esto, «borrar la cuenta no borra
        # los recuerdos» pasaba.
        from src.identidad import como_usuario

        repos = dependencies.get_container().repositories
        with como_usuario(ana.id):
            assert repos.memories.search() == [], "quedaron recuerdos de la cuenta borrada"
            assert repos.sessions.list(limit=100, archived=None) == [], "quedaron conversaciones"
