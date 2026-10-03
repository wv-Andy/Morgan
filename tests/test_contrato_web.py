"""
El contrato entre la web y la API.

Nada comprobaba que las rutas que llama el frontend **existan** en el backend. Es
una clase de fallo especialmente desagradable: renombrar un endpoint deja la
interfaz rota en silencio, la suite entera sigue en verde, y el fallo aparece
cuando alguien pulsa un botón en producción.

Estas pruebas leen [`web/src/lib/api.ts`](../web/src/lib/api.ts), sacan las rutas
que llama de verdad y comprueban que la aplicación FastAPI las tiene. No hay que
mantener una lista a mano: la lista **es** el cliente.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app

CLIENTE_TS = Path(__file__).resolve().parent.parent / "web" / "src" / "lib" / "api.ts"

# Las llamadas del cliente: request('/algo'), request<Tipo>(`/algo/${x}`)...
LLAMADA = re.compile(r"request(?:<[^>]*>)?\(\s*[`'\"]([^`'\"]+)")

def sin_interpolaciones(ruta: str) -> str:
    """Sustituye cada `${...}` por `X`, contando llaves.

    Una expresión regular no vale: el cliente tiene interpolaciones con llaves
    dentro —`${query ? `?q=${x}` : ''}`— y una regex no codiciosa corta en la
    primera llave de cierre, dejando basura pegada a la ruta.
    """
    salida = []
    i = 0
    while i < len(ruta):
        if ruta.startswith("${", i):
            profundidad = 1
            i += 2
            while i < len(ruta) and profundidad:
                if ruta[i] == "{":
                    profundidad += 1
                elif ruta[i] == "}":
                    profundidad -= 1
                i += 1
            salida.append("X")
        else:
            salida.append(ruta[i])
            i += 1

    return "".join(salida)


def rutas_del_cliente() -> set[str]:
    """Las rutas que el frontend llama, normalizadas."""
    codigo = CLIENTE_TS.read_text(encoding="utf-8")
    rutas = set()

    for cruda in LLAMADA.findall(codigo):
        if not cruda.startswith("/"):
            continue

        ruta = sin_interpolaciones(cruda)
        # Fuera la cadena de consulta: no forma parte de la ruta.
        ruta = ruta.split("?")[0].rstrip("/")
        # Una interpolación condicional puede dejar una X pegada al final
        # (`/memory` + `${query ? ...}`); ahí la ruta base es la que vale.
        if ruta.endswith("X") and not ruta.endswith("/X"):
            ruta = ruta[:-1].rstrip("/")

        if ruta:
            rutas.add(ruta)

    return rutas


def rutas_de_la_api() -> set[str]:
    """Las que la aplicación publica, con los parámetros normalizados igual."""
    esquema = TestClient(create_app()).get("/openapi.json").json()
    return {
        re.sub(r"\{[^}]*\}", "X", ruta).rstrip("/") or "/"
        for ruta in esquema["paths"]
    }


class TestTodaRutaQueLlamaLaWebExiste:
    def test_el_fichero_del_cliente_esta_donde_se_espera(self):
        assert CLIENTE_TS.exists(), f"No se encontró {CLIENTE_TS}"

    def test_se_detectan_las_llamadas(self):
        """Si el patrón dejara de casar, las pruebas siguientes pasarían sin
        comprobar nada. Este es el guardián del guardián."""
        rutas = rutas_del_cliente()

        assert len(rutas) > 15, f"Solo se detectaron {len(rutas)}: ¿cambió la forma de llamar?"
        assert "/chat" in rutas
        assert "/auth/yo" in rutas

    def test_ninguna_ruta_de_la_web_falta_en_la_api(self):
        """El fallo que esto caza: renombrar un endpoint y romper la interfaz sin
        que nada se ponga rojo."""
        faltan = rutas_del_cliente() - rutas_de_la_api()

        assert not faltan, (
            "La web llama a rutas que la API no tiene: "
            + ", ".join(sorted(faltan))
        )


class TestLasRutasCriticasSiguenAhi:
    """Las que, si desaparecen, dejan la web inservible. Se nombran una a una
    porque su ausencia no es «una ruta menos»: es la aplicación rota."""

    @pytest.fixture
    def rutas(self):
        return rutas_de_la_api()

    @pytest.mark.parametrize(
        "ruta, por_que",
        [
            ("/health", "sin esto la web no sabe si el backend vive"),
            ("/status", "es lo que consulta la pantalla de acceso; protegerlo o "
                        "quitarlo hace que diga «API desconectada»"),
            ("/chat", "es Morgan"),
            ("/auth/yo", "decide entre enseñar el formulario de acceso o la aplicación"),
            ("/auth/login", "sin esto no se entra"),
            ("/auth/registro", "sin esto no hay cuentas nuevas"),
            ("/auth/restablecer", "es el destino del enlace del correo"),
            ("/sessions", "las conversaciones"),
            ("/planes", "los planes pendientes de aprobación"),
            ("/tasks", "el progreso de los trabajos largos"),
        ],
    )
    def test_existe(self, rutas, ruta, por_que):
        assert ruta in rutas, f"Falta {ruta}: {por_que}"


class TestLaFormaDeLasRespuestasQueLaWebDaPorHecha:
    """Un cambio de nombre en un campo rompe la interfaz igual que quitar una
    ruta, y es aún más fácil de hacer sin darse cuenta."""

    @pytest.fixture
    def cliente(self):
        from src.api import dependencies

        dependencies.reset_container()
        return TestClient(create_app())

    def test_auth_yo_trae_lo_que_la_puerta_de_entrada_mira(self, cliente):
        cuerpo = cliente.get("/auth/yo").json()

        # App.tsx decide con estos tres. Sin cualquiera de ellos, o se queda en
        # blanco o enseña lo que no toca.
        for campo in ("autenticado", "local", "usuario"):
            assert campo in cuerpo, f"/auth/yo ya no trae '{campo}'"

    def test_status_trae_los_servicios(self, cliente):
        cuerpo = cliente.get("/status").json()

        assert "services" in cuerpo
        assert "mode" in cuerpo

    def test_los_planes_traen_lo_que_el_panel_pinta(self, cliente):
        cuerpo = cliente.get("/planes").json()

        assert "planes" in cuerpo
        assert "count" in cuerpo

    def test_las_sesiones_traen_su_lista(self, cliente):
        cuerpo = cliente.get("/sessions").json()

        assert "sessions" in cuerpo
