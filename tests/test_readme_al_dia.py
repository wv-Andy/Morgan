"""
Los números del README salen del código, no de la memoria de nadie.

**Por qué existe.** El README anunció durante ocho versiones «25 herramientas en
7 dominios» y «384 tests» mientras el proyecto iba por 49 herramientas en 8
dominios y 1500 pruebas. Es la primera página que lee cualquiera, y describía
otro proyecto.

Un README desfasado no rompe nada, y por eso se queda desfasado: nadie se entera
hasta que alguien lo lee y saca conclusiones falsas sobre qué puede hacer Morgan.

Desde el 2026-10-04 hay dos: `README.md` en inglés (la portada) y `README.es.md` en español.
Las cifras se vigilan en los dos: si solo se mirase uno, el otro se quedaría atrás.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _catalogo(entorno: str) -> tuple[int, set[str]]:
    """Cuántas herramientas y qué dominios registra Morgan en ese entorno.

    En un proceso aparte a propósito. El contenedor es un singleton y el entorno
    se lee al construirlo: cambiar la variable dentro de la misma sesión de
    pytest devuelve el catálogo del primero que se pidiera, y la prueba pasaría
    comparando el número equivocado consigo mismo.
    """
    guion = (
        "import os, json;"
        f"os.environ['MORGAN_ENVIRONMENT']={entorno!r};"
        "from src.api.dependencies import get_container;"
        "hs=get_container().tool_registry.list_tools();"
        "print(json.dumps([len(hs), sorted({h.category for h in hs})]))"
    )
    salida = subprocess.run(
        [sys.executable, "-c", guion],
        capture_output=True, text=True, cwd=RAIZ, timeout=300,
    )
    assert salida.returncode == 0, f"No se pudo montar el catálogo: {salida.stderr[-400:]}"

    cuantas, dominios = json.loads(salida.stdout.strip().splitlines()[-1])
    return cuantas, set(dominios)


@pytest.fixture(scope="module")
def readme() -> str:
    return (RAIZ / "README.es.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def readme_en() -> str:
    return (RAIZ / "README.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def catalogo_local():
    return _catalogo("local")


@pytest.fixture(scope="module")
def catalogo_nube():
    return _catalogo("cloud")


class TestElReadmeCuentaBien:
    def test_las_herramientas_de_tu_equipo(self, readme, readme_en, catalogo_local):
        cuantas, _ = catalogo_local

        assert re.search(rf"\*\*{cuantas} en tu equipo", readme), (
            f"En local hay {cuantas} herramientas y el README en español no lo dice"
        )
        assert re.search(rf"\*\*{cuantas} on your computer", readme_en), (
            f"En local hay {cuantas} herramientas y el README en inglés no lo dice"
        )

    def test_las_de_la_nube(self, readme, readme_en, catalogo_nube):
        cuantas, _ = catalogo_nube

        assert re.search(rf"\*\*{cuantas} en la nube", readme), (
            f"En la nube hay {cuantas} herramientas y el README en español no lo dice"
        )
        assert re.search(rf"\*\*{cuantas} in the cloud", readme_en), (
            f"En la nube hay {cuantas} herramientas y el README en inglés no lo dice"
        )

    def test_los_dominios(self, readme, readme_en, catalogo_local):
        _, dominios = catalogo_local

        assert f"{len(dominios)} dominios" in readme, (
            f"Hay {len(dominios)} dominios ({sorted(dominios)}) y el README en español dice otra cosa"
        )
        assert f"{len(dominios)} domains" in readme_en, (
            f"Hay {len(dominios)} dominios ({sorted(dominios)}) y el README en inglés dice otra cosa"
        )

    def test_el_enlace_de_descarga_es_el_de_verdad(self, readme, readme_en):
        """Estuvo con `tu-usuario` de plantilla: `git clone` fallaba tal cual."""
        for texto in (readme, readme_en):
            assert "tu-usuario" not in texto, (
                "El README sigue con la URL de plantilla: quien copie el comando de "
                "instalación se lleva un error"
            )


class TestLaCifraDePruebasSoloViveEnElReadme:
    """El total de la suite se quedaba desfasado en siete sitios a la vez.

    Estaba escrito en `arquitectura.md` (1006), `autenticacion.md` (857),
    `capacidades.md` (629), `web.md` (1358) y tres veces en el
    README (1500), cuando eran 1643. Cada número era correcto el día que se
    escribió.

    La salida no fue actualizar los siete: fue **quitarlos de donde no
    significan nada**. Que `autenticacion.md` diga cuántas pruebas tiene el
    proyecto entero no ayuda a entender la autenticación, y garantiza que un día
    esté mal. Lo que sí importa —que ninguna esté desactivada— no es un número.

    En el README se queda, porque es la portada y ahí estar mal es peor. Y se
    vigila desde aquí.
    """

    def test_el_readme_dice_un_suelo_que_se_cumple(self, readme, readme_en):
        """Un **suelo** redondeado, no la cifra exacta, y hay una razón dura.

        La primera versión de esta prueba exigía el número exacto. Al añadirla,
        la suite pasó de 1643 a 1647 y la prueba se invalidó a sí misma: el
        propio hecho de contar añade pruebas que cambian la cuenta.

        Con un suelo —«más de 1.600»— añadir pruebas nunca lo rompe, y la
        afirmación no puede volverse falsa: como mucho se queda conservadora, y
        entonces se sube el suelo de cien en cien.
        """
        salida = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:warnings"],
            capture_output=True, text=True, cwd=RAIZ, timeout=600,
        )
        encontrada = re.search(r"(\d+) tests collected", salida.stdout)
        assert encontrada, f"No se pudo contar la suite: {salida.stdout[-300:]}"
        cuantas = int(encontrada.group(1))

        suelos = [int(s.replace(".", "")) for s in re.findall(r"más de ([\d.]+)", readme)]
        assert suelos, "El README ya no dice cuántas pruebas hay, ni siquiera un suelo"
        suelos_en = [int(s.replace(",", "")) for s in re.findall(r"more than ([\d,]+)", readme_en)]
        assert suelos_en, "El README en inglés ya no dice cuántas pruebas hay, ni siquiera un suelo"
        suelos += suelos_en

        anunciado = max(suelos)
        assert cuantas >= anunciado, (
            f"El README anuncia más de {anunciado} pruebas y hay {cuantas}. "
            "La cifra de la portada no puede ser una promesa que no se cumple"
        )

    @pytest.mark.parametrize("documento", [
        "arquitectura.md", "autenticacion.md", "capacidades.md",
    ])
    def test_y_los_demas_no_la_repiten(self, documento):
        """Repetirla en cada documento multiplica por siete las veces que puede
        estar mal, sin añadir nada.
        """
        texto = (RAIZ / "docs" / documento).read_text(encoding="utf-8")
        cifras = [
            int(n.replace(".", ""))
            for n in re.findall(r"(\d[\d.]{2,})\s*(?:pruebas|tests|casos)", texto, re.I)
        ]
        grandes = [n for n in cifras if n > 300]

        assert not grandes, (
            f"{documento} vuelve a escribir el total de la suite: {grandes}. "
            "Se queda desfasado el mismo día y no dice nada del tema del "
            "documento. Lo que importa —ninguna desactivada— no es un número"
        )
