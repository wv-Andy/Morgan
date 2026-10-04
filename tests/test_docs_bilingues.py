"""
La documentación pública, en inglés y en español (decidido el 2026-10-04).

Cada documento que publica el espejo (`scripts/publicar_espejo.py`, `PUBLICADOS`) va **en inglés
con el nombre de siempre** (`docs/api.md`) y **en español al lado** (`docs/api.es.md`), cada uno
enlazando al otro arriba. Lo interno (planes, mediciones, CHANGELOG) se queda solo en español.

**Por qué una prueba.** Mantener dos versiones a mano es la forma más segura de que una se quede
atrás o de que falte: un documento nuevo que solo se escribe en un idioma, un enlace al otro
idioma que se pierde al reescribir la cabecera, o un «inglés» que en realidad es el español
copiado. Esto lo dice el día que pasa, no cuando alguien lo lee.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


#: El script del espejo no se publica (lleva justo las palabras que vigila): en el repositorio
#: público la lista sale de los propios ficheros. Medido en la integración continua de la 5.2.1.
ESPEJO = RAIZ / "scripts" / "publicar_espejo.py"


def _publicados() -> list[str] | None:
    if not ESPEJO.exists():
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location("espejo", ESPEJO)
    espejo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(espejo)
    return espejo.PUBLICADOS


PUBLICADOS = _publicados()
#: Los documentos en inglés que se publican, y los README de las carpetas publicadas.
EN_INGLES = sorted(
    ([p for p in PUBLICADOS if p.endswith(".md") and not p.endswith(".es.md")] if PUBLICADOS is not None
     else ["README.md"] + [f"docs/{d.name[:-6]}.md" for d in (RAIZ / "docs").glob("*.es.md")])
    + ["web/README.md", "migraciones/supabase/README.md"]
)


def _espanol(ruta: str) -> str:
    return ruta[:-3] + ".es.md"


def _palabras(texto: str, lista: tuple[str, ...]) -> int:
    palabras = re.findall(r"[a-záéíóúñ]+", texto.lower())
    return sum(palabras.count(p) for p in lista)


INGLESAS = ("the", "and", "of", "to", "is", "it", "that", "with")
ESPANOLAS = ("el", "la", "de", "que", "los", "las", "una", "para", "con")


@pytest.mark.parametrize("ruta", EN_INGLES)
class TestCadaDocumentoEnLosDos:
    def test_existe_en_espanol(self, ruta):
        assert (RAIZ / _espanol(ruta)).exists(), f"{ruta} no tiene su versión en español"

    def test_cada_uno_enlaza_al_otro_arriba(self, ruta):
        ingles = (RAIZ / ruta).read_text(encoding="utf-8").splitlines()[:6]
        espanol = (RAIZ / _espanol(ruta)).read_text(encoding="utf-8").splitlines()[:6]
        nombre = Path(ruta).name
        assert f"**English** · [Español]({nombre[:-3]}.es.md)" in "\n".join(ingles), ruta
        assert f"[English]({nombre}) · **Español**" in "\n".join(espanol), _espanol(ruta)

    def test_el_ingles_es_ingles_y_el_espanol_espanol(self, ruta):
        """Un «inglés» que es el español copiado (o al revés) pasaría todo lo demás."""
        ingles = (RAIZ / ruta).read_text(encoding="utf-8")
        espanol = (RAIZ / _espanol(ruta)).read_text(encoding="utf-8")
        assert _palabras(ingles, INGLESAS) > _palabras(ingles, ESPANOLAS), f"{ruta} no parece inglés"
        assert _palabras(espanol, ESPANOLAS) > _palabras(espanol, INGLESAS), f"{_espanol(ruta)} no parece español"

    def test_tienen_las_mismas_secciones(self, ruta):
        """Mismo número de títulos: una sección añadida en un idioma y no en el otro se ve aquí."""
        ingles = (RAIZ / ruta).read_text(encoding="utf-8")
        espanol = (RAIZ / _espanol(ruta)).read_text(encoding="utf-8")
        titulos = re.compile(r"^#{1,6} ", re.MULTILINE)
        assert len(titulos.findall(ingles)) == len(titulos.findall(espanol)), (
            f"{ruta} y su versión en español no tienen las mismas secciones")


def test_el_espejo_publica_los_dos():
    """En el repositorio público tienen que estar las dos versiones de lo público."""
    if PUBLICADOS is None:
        pytest.skip("en el repositorio público no está el script del espejo")
    for ruta in EN_INGLES:
        if ruta.startswith(("web/", "migraciones/")):
            continue                # van con su carpeta entera (`web/*`, `migraciones/*`)
        assert _espanol(ruta) in PUBLICADOS, f"El espejo publica {ruta} y no {_espanol(ruta)}"
