"""
Que `docs/api.md` no se quede atrás.

**Por qué existe.** Documentaba 20 rutas de las 45 que hay. Faltaban las cuatro
de administración, las cuatro de integraciones, las cuatro de planes y cuatro de
cuenta, entre ellas **borrar la cuenta y exportar tus datos**, que son justo las
que alguien busca en un documento y no en el código.

Una tabla incompleta es peor que ninguna: quien la lee concluye que lo que no
aparece no existe. Y la forma de que no vuelva a pasar no es acordarse, es que
algo falle.

Mismo criterio que [`test_readme_al_dia.py`](test_readme_al_dia.py) y
[`test_variables_documentadas.py`](test_variables_documentadas.py): la
documentación que se puede comprobar contra el código se comprueba.
"""

import pathlib
import re

import pytest

DOC = pathlib.Path("docs/api.md")

#: Rutas que no se documentan a propósito, con el motivo. Vacío hoy; existe para
#: que añadir una excepción obligue a escribir por qué, en lugar de ampliar
#: calladamente lo que la prueba deja pasar.
EXENTAS: dict[str, str] = {}


def _normalizar(ruta: str) -> str:
    """`/tasks/{task_id}` y `/tasks/{id}` son la misma ruta documentada.

    El documento usa nombres cortos para los parámetros y el esquema los usa
    largos. Exigir que coincidan sería exigir que el documento se escriba en el
    idioma del código, que es al revés de lo que hace falta.
    """
    return re.sub(r"\{[^}]+\}", "{}", ruta)


def _metodos_reales() -> set[tuple[str, str]]:
    import os
    import tempfile

    os.environ.update({
        "MORGAN_DATA_DIR": tempfile.mkdtemp(),
        "MORGAN_LOG_DIR": tempfile.mkdtemp(),
        "MORGAN_SERVE_WEB": "false",
    })
    from src.api.app import create_app
    from src.config import reset_settings

    reset_settings()
    try:
        esquema = create_app().openapi()
    finally:
        reset_settings()
    return {
        (metodo.upper(), _normalizar(ruta))
        for ruta, operaciones in esquema["paths"].items()
        if not ruta.startswith(("/openapi", "/docs", "/redoc"))
        for metodo in operaciones
    }


class TestElMetodoTambienCoincide:
    """La ruta existía y el método no (auditoría de la 2.3).

    `api.md` decía `PATCH` en las dos rutas de administración y `GET` en la
    transcripción, y las tres son `POST`. La prueba de arriba compara solo las
    rutas, así que pasaba: quien integrara siguiendo el documento recibía un 405.
    """

    def test_cada_fila_de_la_tabla_existe_con_ese_metodo(self):
        texto = DOC.read_text(encoding="utf-8")
        documentadas = {
            (m, _normalizar(r))
            for m, r in re.findall(r"^\|\s*`([A-Z]+)`\s*\|\s*`(/[^`]*)`", texto, re.MULTILINE)
        }
        reales = _metodos_reales()

        mal = sorted(documentadas - reales)
        assert not mal, (
            "Estas filas de docs/api.md no existen con ese método:\n  "
            + "\n  ".join(f"{m} {r}" for m, r in mal)
        )


@pytest.fixture(scope="module")
def rutas_reales() -> set[str]:
    import os
    import tempfile

    os.environ.update({
        "MORGAN_DATA_DIR": tempfile.mkdtemp(),
        "MORGAN_LOG_DIR": tempfile.mkdtemp(),
        "MORGAN_SERVE_WEB": "false",
    })
    from src.api.app import create_app
    from src.config import reset_settings

    reset_settings()
    try:
        esquema = create_app().openapi()
    finally:
        reset_settings()

    # Lo que FastAPI sirve de su propia documentación no es de Morgan.
    propias = {
        r for r in esquema["paths"]
        if not r.startswith(("/openapi", "/docs", "/redoc"))
    }
    return {_normalizar(r) for r in propias}


@pytest.fixture(scope="module")
def rutas_documentadas() -> set[str]:
    texto = DOC.read_text(encoding="utf-8")
    # Solo las de la tabla: `| `GET` | `/ruta` | ...`, para no recoger de paso
    # cualquier ruta mencionada de pasada en la prosa.
    filas = re.findall(r"^\|\s*`[A-Z]+`\s*\|\s*`(/[^`]*)`", texto, re.MULTILINE)
    return {_normalizar(r) for r in filas}


class TestTodaRutaEstaEnLaTabla:
    def test_no_falta_ninguna(self, rutas_reales, rutas_documentadas):
        faltan = rutas_reales - rutas_documentadas - {
            _normalizar(r) for r in EXENTAS
        }

        assert not faltan, (
            "Estas rutas existen y no están en docs/api.md:\n  "
            + "\n  ".join(sorted(faltan))
            + "\n\nAñádelas a la tabla, o a EXENTAS con el motivo."
        )

    def test_ni_sobra_ninguna(self, rutas_reales, rutas_documentadas):
        """Una ruta documentada que ya no existe manda a alguien a llamar algo
        que da 404, y eso cuesta más que no documentarla.
        """
        sobran = rutas_documentadas - rutas_reales

        assert not sobran, (
            "Estas rutas están en docs/api.md y no existen:\n  "
            + "\n  ".join(sorted(sobran))
        )

    def test_hay_bastantes_como_para_que_esto_signifique_algo(
        self, rutas_documentadas
    ):
        """Si un cambio dejara la tabla vacía, las dos pruebas de arriba
        pasarían: dos conjuntos vacíos son iguales. Esta es la que avisa.
        """
        assert len(rutas_documentadas) >= 40, (
            f"Solo se leen {len(rutas_documentadas)} rutas de la tabla, y hay "
            "más de 40. Probablemente el formato cambió y la comprobación ya "
            "no lee nada."
        )


class TestLasExencionesSeExplican:
    def test_cada_exencion_dice_por_que(self):
        """Una lista de excepciones sin motivos se llena sola."""
        sin_motivo = [r for r, motivo in EXENTAS.items() if not motivo.strip()]

        assert not sin_motivo, f"Exenciones sin motivo: {sin_motivo}"

    def test_no_se_exime_lo_que_no_existe(self, rutas_reales):
        """Una exención para una ruta ya borrada solo estorba."""
        fantasmas = [
            r for r in EXENTAS if _normalizar(r) not in rutas_reales
        ]

        assert not fantasmas, f"Exenciones de rutas que no existen: {fantasmas}"
