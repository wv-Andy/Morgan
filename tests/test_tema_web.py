"""
La paleta de la interfaz.

Un tema se puede romper de dos formas, y ninguna la detecta el compilador:

1. **Escribiendo un color a mano** en un componente. Ese color no cambia con el
   tema, así que aparece como una mancha gris en un tema verde o al revés. Es lo
   que hace que cambiar de paleta pase de ser un bloque de variables a una
   cacería por todo el CSS.
2. **Bajando el contraste** hasta que el texto atenuado deja de leerse. Se ve
   bonito en la captura y es ilegible a las once de la noche.

Estas pruebas fijan las dos cosas.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web" / "src"
INDEX_CSS = WEB / "index.css"
APP_CSS = WEB / "App.css"

# Mínimos de WCAG: 4.5 para texto normal, 3.0 para texto grande y elementos de
# interfaz. Se aplican los mismos que exigiría cualquiera, no unos propios.
MINIMO_TEXTO = 4.5
MINIMO_INTERFAZ = 3.0


def luminancia(color: str) -> float:
    color = color.lstrip("#")
    canales = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    canales = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales
    ]
    return 0.2126 * canales[0] + 0.7152 * canales[1] + 0.0722 * canales[2]


def contraste(a: str, b: str) -> float:
    la, lb = luminancia(a), luminancia(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def variables(bloque: str) -> dict[str, str]:
    """Las variables CSS de un bloque `:root`."""
    texto = INDEX_CSS.read_text(encoding="utf-8")
    inicio = texto.index(bloque)
    fin = texto.index("}", inicio)
    return dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", texto[inicio:fin]))


@pytest.fixture(scope="module")
def oscuro():
    return variables(":root {")


@pytest.fixture(scope="module")
def claro():
    return variables(':root[data-tema="claro"]')


@pytest.fixture(scope="module")
def negro():
    return variables(':root[data-tema="negro"]')


@pytest.fixture(scope="module")
def temas_oscuros(oscuro, negro):
    """Los dos oscuros. Todo lo que se exige a uno se exige al otro."""
    return {"azul noche": oscuro, "oscuro": negro}


class TestSeLeeLoQueSeEscribe:
    """Un tema con mal contraste se ve bien en una captura y es ilegible a las
    once de la noche."""

    @pytest.mark.parametrize(
        "variable, minimo",
        [
            ("--text-primary", MINIMO_TEXTO),
            ("--text-secondary", MINIMO_TEXTO),
            ("--text-muted", MINIMO_INTERFAZ),
            ("--risk-safe", MINIMO_INTERFAZ),
            ("--risk-moderate", MINIMO_INTERFAZ),
            ("--risk-critical", MINIMO_INTERFAZ),
            ("--status-error", MINIMO_INTERFAZ),
        ],
    )
    def test_en_los_temas_oscuros(self, temas_oscuros, variable, minimo):
        """Se recorren los DOS. Un tema nuevo que no se comprobara sería un tema
        que se degrada sin que nada avise: es literalmente lo que este fichero
        existe para impedir."""
        for nombre, tema in temas_oscuros.items():
            ratio = contraste(tema[variable], tema["--bg-app"])

            assert ratio >= minimo, (
                f"[{nombre}] {variable} tiene {ratio:.2f}, hace falta {minimo}"
            )

    @pytest.mark.parametrize(
        "variable, minimo",
        [
            ("--text-primary", MINIMO_TEXTO),
            ("--text-secondary", MINIMO_TEXTO),
            ("--text-muted", MINIMO_INTERFAZ),
            ("--risk-critical", MINIMO_INTERFAZ),
        ],
    )
    def test_en_el_tema_claro(self, claro, variable, minimo):
        ratio = contraste(claro[variable], claro["--bg-app"])

        assert ratio >= minimo, f"{variable} tiene {ratio:.2f}, hace falta {minimo}"

    def test_tambien_sobre_las_tarjetas(self, temas_oscuros):
        """El texto no siempre está sobre el fondo de la aplicación."""
        for nombre, tema in temas_oscuros.items():
            ratio = contraste(tema["--text-secondary"], tema["--bg-card"])

            assert ratio >= MINIMO_TEXTO, f"[{nombre}] {ratio:.2f}"

    def test_el_boton_de_accion_se_lee(self, oscuro, claro, negro):
        """Contra los DOS tonos del degradado (V2.0.28): el texto tiene que
        leerse arriba y abajo del botón, no solo en el tono más oscuro."""
        for tema in (oscuro, claro, negro):
            assert contraste(tema["--action-fg"], tema["--action-bg"]) >= MINIMO_TEXTO
            assert contraste(tema["--action-fg"], tema["--action-bg-2"]) >= MINIMO_INTERFAZ

    @pytest.mark.parametrize("variable, fondo", [
        ("--accent", "--bg-card"),       # «Escribirlo →» en las tarjetas
        ("--accent-text", "--bg-app"),   # la palabra destacada del saludo
        ("--nav-active-fg", "--bg-sidebar"),
    ])
    def test_el_acento_se_lee(self, oscuro, claro, negro, variable, fondo):
        """El acento es texto, no decoración: enlaces y la fila activa."""
        for nombre, tema in {"azul noche": oscuro, "claro": claro, "oscuro": negro}.items():
            ratio = contraste(tema[variable], tema[fondo])
            assert ratio >= MINIMO_TEXTO, f"[{nombre}] {variable} tiene {ratio:.2f}"


class TestLaBarraLateralNoSeComeLaPantalla:
    """La barra lateral tiene un ancho fijo, y nada puede cancelarlo.

    **El fallo que trajo estas pruebas.** La capa de estilo de terminal le puso
    `flex: 1` pensando en el alto. Pero `.app-shell` es `display: flex` en
    **fila**, así que eso significa «crece a lo ancho», y su `flex-basis: 0%`
    anula el `width` de arriba. Con `.main-content` también en `flex: 1`, las dos
    se repartían la pantalla al 50%: la barra ocupaba media ventana y la
    conversación quedaba arrinconada.

    Es un fallo que se ve a simple vista y que ninguna comprobación miraba: ni
    los tipos, ni el linter, ni las pruebas de contraste. El CSS no tiene quien
    le diga que una regla contradice a otra.
    """

    @staticmethod
    def _bloques(selector: str) -> list[str]:
        """Todos los bloques de un selector, en orden. Importa que sean todos:
        el que rompía era el segundo, en una capa añadida al final."""
        import re

        texto = APP_CSS.read_text(encoding="utf-8")
        return re.findall(
            rf"^{re.escape(selector)}\s*\{{(.*?)\}}", texto, re.MULTILINE | re.DOTALL
        )

    def test_conserva_su_ancho_fijo(self):
        bloques = self._bloques(".sidebar")

        assert bloques, "No hay ninguna regla .sidebar"
        assert any("width:" in b and "--sidebar-width" in b for b in bloques), (
            "La barra lateral ya no fija su ancho con --sidebar-width"
        )

    def test_nada_le_da_flex_grow(self):
        """`flex: 1`, `flex-grow: 1` o un `flex-basis` que cancele el ancho."""
        sospechosas = []

        for numero, bloque in enumerate(self._bloques(".sidebar"), 1):
            for linea in bloque.splitlines():
                limpia = linea.split("/*")[0].strip()
                if not limpia:
                    continue
                if limpia.startswith("flex:") and not limpia.startswith("flex: none"):
                    sospechosas.append(f"bloque {numero}: {limpia}")
                if limpia.startswith("flex-grow:") and "0" not in limpia:
                    sospechosas.append(f"bloque {numero}: {limpia}")

        assert not sospechosas, (
            "La barra lateral crece a lo ancho y se come la pantalla:\n  "
            + "\n  ".join(sospechosas)
        )

    def test_el_contenido_si_ocupa_el_resto(self):
        """Lo contrario también importa: si `.main-content` perdiera su `flex`,
        la conversación se encogería al ancho de su contenido."""
        bloques = self._bloques(".main-content")

        assert any("flex: 1" in b for b in bloques), (
            ".main-content ya no ocupa el espacio restante"
        )


class TestNingunComponenteEscribeUnColorAMano:
    """Si un componente escribe su color, ese color no cambia con el tema.

    Es lo que convierte «cambiar la paleta» de tocar un bloque de variables a
    una cacería por todo el CSS — y lo que deja manchas grises en un tema verde.
    """

    # Lo que sí puede llevar color literal: las variables (que para eso están),
    # el blanco puro del botón de acción, y `transparent`.
    PERMITIDO = re.compile(r"--[a-z-]+:|:root|^\s*/\*|\*/")

    def test_app_css_solo_usa_variables(self):
        sospechosas = []

        for numero, linea in enumerate(APP_CSS.read_text(encoding="utf-8").splitlines(), 1):
            if self.PERMITIDO.search(linea):
                continue
            # Un color literal en una declaracion de estilo.
            if re.search(r":\s*#[0-9a-fA-F]{3,8}\b", linea):
                sospechosas.append(f"  App.css:{numero}: {linea.strip()[:70]}")

        assert not sospechosas, (
            "Hay colores escritos a mano; deberían ser variables:\n"
            + "\n".join(sospechosas[:10])
        )

    def test_los_componentes_no_llevan_estilos_de_color(self):
        """Un `style={{ color: '#...' }}` en un componente es lo mismo, pero peor:
        ni siquiera aparece al buscar en el CSS."""
        sospechosos = []

        for fichero in WEB.rglob("*.tsx"):
            for numero, linea in enumerate(fichero.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"(color|background)\s*:\s*['\"]#", linea):
                    sospechosos.append(f"  {fichero.name}:{numero}")

        assert not sospechosos, "Colores en línea:\n" + "\n".join(sospechosos[:10])


class TestElTemaClaroSigueSiendoClaro:
    """Cambiar la paleta oscura es fácil; olvidarse de la clara, más todavía. El
    resultado es un tema claro que hereda medio tema oscuro y no se lee."""

    def test_el_fondo_es_claro(self, claro):
        assert luminancia(claro["--bg-app"]) > 0.7

    def test_el_texto_es_oscuro(self, claro):
        assert luminancia(claro["--text-primary"]) < 0.2

    @pytest.mark.parametrize("nombre", ["claro", "negro"])
    def test_definen_las_mismas_variables_de_texto(self, oscuro, claro, negro, nombre):
        """Una variable que un tema no redefine la hereda del bloque `:root`, que
        es el azul noche. En el claro sería azul pálido sobre blanco; en el
        oscuro, una mancha azul en una paleta que no tiene ninguna."""
        tema = {"claro": claro, "negro": negro}[nombre]
        de_texto = {
            v for v in oscuro
            if v.startswith(("--text-", "--bg-", "--action-", "--accent", "--nav-"))
        }

        faltan = de_texto - set(tema)

        assert not faltan, f"El tema {nombre} no redefine: {', '.join(sorted(faltan))}"


class TestElTemaNegroNoTieneColorPropio:
    """El tema «Oscuro» (identificador `negro`): monocromo, donde el único color
    que queda es el que informa.

    Hasta la 2.0.27 el fondo era negro puro. El diseño nuevo lo pone en un gris
    casi negro (#0f0f0f); lo que no puede perder es la neutralidad."""

    def test_el_fondo_es_gris_neutro_y_casi_negro(self, negro):
        color = negro["--bg-app"].lstrip("#")
        assert len({color[i:i + 2] for i in (0, 2, 4)}) == 1, f"#{color} no es neutro"
        assert luminancia(negro["--bg-app"]) < 0.01

    @pytest.mark.parametrize("variable", ["--text-primary", "--text-secondary", "--text-muted"])
    def test_el_texto_es_gris_neutro(self, negro, variable):
        """Neutro significa que los tres canales valen lo mismo. Un azul colado
        aquí sería exactamente el fallo que este tema viene a evitar."""
        color = negro[variable].lstrip("#")
        canales = {color[i:i + 2] for i in (0, 2, 4)}

        assert len(canales) == 1, f"{variable} = #{color} no es gris neutro"

    def test_no_hereda_el_fondo_azul(self, oscuro, negro):
        assert negro["--bg-app"] != oscuro["--bg-app"]
