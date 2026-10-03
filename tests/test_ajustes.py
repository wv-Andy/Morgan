"""
Perfil y preferencias del usuario (Etapa B de la especificación web).

La regla que gobierna esta etapa está en la especificación: «no crear
configuraciones que Morgan no pueda respetar realmente». Por eso la prueba central
no es que se guarde el dato, sino que **llegue al system prompt**: si no llega,
el ajuste es decorativo.
"""

import pytest
from fastapi.testclient import TestClient

import src.config as config
from src.memory.manager import MemoryManager
from src.memory.settings import SettingsStore

TOKEN = "secreto-de-prueba"
CABECERA = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    return SettingsStore(MemoryManager())


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_API_TOKEN", TOKEN)
    config.reset_settings()
    from src.api.app import create_app

    yield TestClient(create_app())
    config.reset_settings()


class TestLosAjustesLleganAMorgan:
    """Lo que distingue un ajuste real de uno decorativo."""

    def test_el_perfil_acaba_en_el_contexto_del_prompt(self, store, tmp_path, monkeypatch):
        store.save({"nombre": "Andy", "ocupacion": "estudiante"})

        contexto = MemoryManager().get_context_summary()

        assert "Andy" in contexto
        assert "estudiante" in contexto

    def test_las_preferencias_tambien(self, store):
        store.save({"idioma": "español", "estilo_respuesta": "conciso"})

        contexto = MemoryManager().get_context_summary()

        assert "español" in contexto
        assert "conciso" in contexto

    def test_guardar_por_la_api_llega_al_turno_siguiente(self, modelo_simulado, api):
        """El cambio tiene efecto sin reiniciar el proceso. Desde la 4.0 la memoria se lee
        en cada turno para quien pregunta, y nunca va al prompt compartido del agente
        (ver `test_memoria_por_persona.py`)."""
        assert api.put("/settings", json={"nombre": "Andy"}, headers=CABECERA).status_code == 200

        from src.api.dependencies import get_container

        agente = get_container().agent
        assert agente is not None, "con el modelo simulado siempre hay agente"
        modelo_simulado.queue_text("hola, Andy")
        assert api.post("/chat", json={"message": "hola"}, headers=CABECERA).status_code == 200

        assert "Andy" in modelo_simulado.prompts[-1]
        assert "Andy" not in agente.system_prompt


class TestGuardarYRecuperar:
    def test_arranca_vacio(self, store):
        assert store.load().to_dict() == {
            "nombre": "", "ocupacion": "", "sobre_mi": "",
            "idioma": "", "estilo_respuesta": "",
        }

    def test_lo_omitido_no_se_toca(self, store):
        store.save({"nombre": "Andy", "idioma": "español"})

        store.save({"nombre": "Andrés"})

        actual = store.load()
        assert actual.nombre == "Andrés"
        assert actual.idioma == "español"

    def test_un_campo_vacio_borra_el_dato(self, store):
        """Un «usuario_ocupacion: » en el prompt es ruido que confunde al modelo."""
        store.save({"ocupacion": "estudiante"})

        store.save({"ocupacion": ""})

        assert store.load().ocupacion == ""
        assert "usuario_ocupacion" not in MemoryManager().get_context_summary()

    def test_los_espacios_sobrantes_se_recortan(self, store):
        assert store.save({"nombre": "  Andy  "}).nombre == "Andy"

    def test_un_campo_desconocido_se_ignora(self, store):
        """Enviar basura no debe crear recuerdos con claves inventadas."""
        store.save({"campo_inventado": "valor"})

        assert "campo_inventado" not in MemoryManager().get_context_summary()

    def test_el_texto_larguisimo_se_recorta(self, store):
        """Todo esto viaja en cada petición al modelo."""
        guardado = store.save({"sobre_mi": "x" * 5000})

        assert len(guardado.sobre_mi) == 1000


class TestDesdeLaAPI:
    def test_exige_token(self, api):
        """El perfil es dato personal: en una URL pública no puede ir abierto."""
        assert api.get("/settings").status_code == 401
        assert api.put("/settings", json={"nombre": "X"}).status_code == 401

    def test_ciclo_completo(self, api):
        api.put("/settings", json={"nombre": "Andy", "idioma": "español"}, headers=CABECERA)

        recuperado = api.get("/settings", headers=CABECERA).json()["settings"]

        assert recuperado["nombre"] == "Andy"
        assert recuperado["idioma"] == "español"

    @pytest.mark.parametrize("campo,largo", [("nombre", 600), ("sobre_mi", 1200)])
    def test_rechaza_lo_que_excede_el_limite(self, api, campo, largo):
        respuesta = api.put("/settings", json={campo: "x" * largo}, headers=CABECERA)

        assert respuesta.status_code == 422


class TestNingunRouterSeQuedaSinProteger:
    """Prueba estructural, no de un caso concreto.

    Al añadir `/settings` casi se olvida incluirlo en `PROTECTED_PREFIXES`, lo que
    habría dejado el perfil del usuario legible y escribible sin token en una URL
    pública. Esta prueba hace que ese olvido sea imposible de repetir: compara los
    routers realmente registrados con la lista de rutas protegidas.
    """

    def test_toda_ruta_registrada_exige_token_salvo_las_publicas(self, api):
        from src.api.auth import PUBLIC_PATHS, _requires_token

        # La raíz es el descriptor del servicio; no lleva datos personales.
        permitidas_sin_token = set(PUBLIC_PATHS) | {"/", "/{full_path}"}

        # Del esquema OpenAPI, no de `app.routes`: esta versión de FastAPI envuelve
        # cada router y `app.routes` solo enseñaba /docs, /redoc y la raíz. La prueba
        # pasaba sin mirar ni una ruta de la API, y 32 quedaron sin token (V3.0.0-dev).
        rutas = list(api.app.openapi()["paths"])
        assert len(rutas) > 40, f"Solo ve {len(rutas)} rutas: la comprobación ha dejado de mirar"

        # La vuelta de OAuth es la única excepción, por lo que explica `_requires_token`.
        desprotegidas = [
            ruta for ruta in rutas
            if ruta not in permitidas_sin_token
            and not ruta.endswith("/callback")
            and not _requires_token(ruta)
        ]

        assert not desprotegidas, (
            "Estas rutas no exigen token; añade su prefijo a PROTECTED_PREFIXES "
            f"en src/api/auth.py: {desprotegidas}"
        )

    def test_solo_la_vuelta_de_oauth_queda_fuera(self):
        """Con el sufijo suelto, cualquier ruta futura acabada en /callback quedaría
        abierta sin que nadie lo decidiera. Solo la de las integraciones."""
        from src.api.auth import _requires_token

        assert not _requires_token("/integraciones/github/callback")
        assert _requires_token("/sessions/x/callback")
        assert _requires_token("/planes/p1/aprobar")
