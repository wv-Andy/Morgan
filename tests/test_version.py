"""
La versión se dice en un solo sitio, y `/health` no puede mentir.

**Por qué existe.** `/health` contestaba `"1.0.0"` con el proyecto en la 1.9.
Estaba escrita a mano en tres ficheros: la app de FastAPI, el esquema de la
respuesta y la propia ruta. No es cosmético: `/health` es lo que se mira desde
fuera para saber qué hay desplegado, y durante ocho versiones respondió un
número que no correspondía a nada.

Tres sitios con el mismo literal se desincronizan solos. Uno solo, y una prueba
que lo ata al CHANGELOG, no.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import __version__
from src.api.app import app

RAIZ = Path(__file__).resolve().parent.parent


def version_del_changelog() -> str:
    if not (RAIZ / "CHANGELOG.md").exists():
        # El espejo público (4.19) no lleva el CHANGELOG: ahí esta comprobación no aplica.
        pytest.skip("sin CHANGELOG (el espejo público)")
    texto = (RAIZ / "CHANGELOG.md").read_text(encoding="utf-8")
    encontrada = re.search(r"^## \[([^\]]+)\]", texto, re.MULTILINE)
    assert encontrada, "El CHANGELOG no tiene ninguna entrada con versión"
    return encontrada.group(1)


class TestLaVersionEsUnaSola:
    def test_el_health_dice_la_del_codigo(self):
        respuesta = TestClient(app).get("/health")

        assert respuesta.status_code == 200
        assert respuesta.json()["version"] == __version__

    def test_y_la_documentacion_de_la_api_tambien(self):
        assert app.openapi()["info"]["version"] == __version__

    def test_y_coincide_con_la_ultima_del_changelog(self):
        """Si se sube la versión y no se anota, o al revés, salta aquí."""
        anotada = version_del_changelog()

        assert anotada == __version__, (
            f"El código dice {__version__} y la última entrada del CHANGELOG "
            f"dice {anotada}. Una de las dos está sin actualizar"
        )

    def test_no_queda_ningun_numero_escrito_a_mano(self):
        """Tres literales iguales se desincronizan solos. Que no vuelvan."""
        culpables = []
        for fichero in ("src/api/app.py", "src/api/schemas.py", "src/api/routes/health.py"):
            texto = (RAIZ / fichero).read_text(encoding="utf-8")
            if re.search(r'version\s*[:=]\s*(str\s*=\s*)?["\']\d+\.\d+\.\d+["\']', texto):
                culpables.append(fichero)

        assert not culpables, (
            f"Estos ficheros vuelven a escribir la versión a mano: {culpables}. "
            "Tiene que salir de src.__version__"
        )
