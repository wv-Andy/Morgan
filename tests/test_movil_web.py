"""
La web en un teléfono.

**El fallo que dio origen a esto.** El botón que abre el menú llevaba su
`display: flex` dentro de un `@media (max-width: 820px)` colocado **antes** de
la regla base `.sidebar-toggle { display: none }`. Misma especificidad, y en CSS
gana la última: el botón quedaba oculto a *todos* los anchos.

Lo que eso significaba en un móvil, medido en un iPhone 13 contra producción con
la sesión abierta:

    .sidebar-toggle        ->  display: none
    .sidebar               ->  left: -248px, fuera de la pantalla
    navegación alcanzable  ->  0 elementos

Morgan en el móvil era el compositor del chat y nada más. Ni conversaciones, ni
Tareas, ni Archivos, ni Herramientas, ni Memoria, ni Estado, ni Auditoría, ni
Servicios, ni Ajustes. No era incómodo: **era inaccesible**, y no se veía porque
en un escritorio estrecho la barra lateral sigue en su sitio.

**Por qué se comprueba desde Python y no con un navegador.** Un navegador de
verdad lo caza mejor, y así se encontró. Pero meter Playwright en el proyecto
por esto es una dependencia grande para una regla que se puede leer del fichero:
lo que hay que fijar es el **orden del CSS**, y eso está en el texto.

El resto de reglas de aquí salieron del mismo recorrido: 33 combinaciones de
pantalla y vista, en un iPhone SE (375), un iPhone 13 (390) y un Galaxy S9+
(320).
"""

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "web" / "src" / "App.css"

#: El ancho a partir del cual la barra lateral pasa a ser un cajón.
ANCHO_MOVIL = 820

#: El teléfono más estrecho que se probó: un Galaxy S9+. Una regla dentro de
#: cualquier `max-width` por encima de esto le aplica.
ANCHO_ESTRECHO = 320

#: Donde empieza el bloque de reglas móviles, que va al final del fichero.
MARCA_MOVIL = "MOVIL — y va al final"


def sin_comentarios(css: str) -> str:
    """El CSS sin sus comentarios.

    Hace falta de verdad: los comentarios de este proyecto explican los fallos
    citando el CSS que los causaba, así que una regla como
    `.sidebar-toggle { display: none }` aparece **escrita en la prosa** que
    cuenta por qué estuvo mal. Sin quitarlos, estas comprobaciones leen la
    explicación del fallo y la toman por el fallo.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


@pytest.fixture(scope="module")
def css() -> str:
    return sin_comentarios(CSS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def css_con_comentarios() -> str:
    """Para las comprobaciones que buscan la marca del bloque móvil."""
    return CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def bloque_movil(css: str, css_con_comentarios: str) -> str:
    """Las reglas móviles: desde el ÚLTIMO `@media (max-width: 820px)` al final.

    Se localiza por la media query y no por el comentario que la encabeza. El
    primer intento cortaba el fichero por el texto del comentario, y el corte
    caía **dentro** del propio comentario: se quedaba media explicación sin su
    `/*` de apertura, así que ya no se podía quitar, y las comprobaciones leían
    la prosa que describe el fallo como si fuera el fallo.
    """
    assert MARCA_MOVIL in css_con_comentarios, (
        "El bloque de reglas móviles ya no está marcado. Va al final del "
        "fichero a propósito: puesto antes, cualquier regla posterior lo tapa, "
        "y eso es lo que dejó el móvil sin navegación"
    )

    marcas = [m.start() for m in re.finditer(
        rf"@media\s*\(max-width:\s*{ANCHO_MOVIL}px\)", css
    )]
    assert marcas, f"Ya no hay ninguna media query de {ANCHO_MOVIL}px"
    return css[marcas[-1]:]


def declaraciones_de(css: str, selector: str, propiedad: str) -> list[tuple[int, str, bool]]:
    """Cada valor que el fichero le da a esa propiedad, en orden de aparición.

    Devuelve `(posición, valor, aplica_en_movil)`. `aplica_en_movil` es si la
    declaración está fuera de todo `@media` —y entonces vale en cualquier
    ancho— o dentro de uno que cubra el móvil.

    No es un motor de CSS: solo mira esta propiedad en este selector, que es lo
    que hacía falta para el fallo. Un analizador completo sería más de lo que la
    regla necesita, y más difícil de leer que el propio CSS.
    """
    encontradas = []
    patron = rf"(?<![\w.-]){re.escape(selector)}\s*(?:,[^{{]*?)?{{([^}}]*)}}"
    for bloque in re.finditer(patron, css):
        valor = re.search(rf"(?<![\w-]){propiedad}\s*:\s*([^;}}]+)", bloque.group(1))
        if not valor:
            continue

        anterior = css[: bloque.start()]
        # Con las llaves se sabe si algún @media sigue abierto en este punto.
        dentro_de_media = anterior.count("{") - anterior.count("}") > 0
        medias = re.findall(r"@media([^{]*){", anterior)

        if not dentro_de_media or not medias:
            aplica = True
        else:
            tope = re.search(r"max-width\s*:\s*(\d+)px", medias[-1])
            # Cualquier tope que cubra un telefono estrecho vale: hay reglas en
            # el bloque de 620px que son igual de moviles que las de 820.
            aplica = bool(tope) and int(tope.group(1)) >= ANCHO_ESTRECHO

        encontradas.append((bloque.start(), valor.group(1).strip(), aplica))
    return encontradas


class TestElBotonDelMenuSeVeEnElMovil:
    """Es la única puerta a todo lo que no es el chat."""

    def test_la_ultima_regla_que_aplica_no_lo_esconde(self, css):
        aplicables = [
            (posicion, valor)
            for posicion, valor, aplica in declaraciones_de(css, ".sidebar-toggle", "display")
            if aplica
        ]

        assert aplicables, "Nadie le da un `display` al botón del menú"

        _, ultimo = max(aplicables, key=lambda par: par[0])
        assert ultimo != "none", (
            "La última regla de `display` que aplica en el móvil deja el botón "
            "del menú en `display: none`. En CSS gana la última con la misma "
            "especificidad, así que su `display: flex` tiene que ir DESPUÉS de "
            "la regla base. Sin ese botón no hay forma de llegar a ninguna vista"
        )

    def test_y_se_puede_tocar_con_el_dedo(self, bloque_movil):
        """44px es el mínimo táctil de Apple. Estaba en 32."""
        regla = re.search(r"\.sidebar-toggle\s*{([^}]*)}", bloque_movil)

        assert regla, "El bloque móvil ya no dimensiona el botón del menú"
        for propiedad in ("width", "height"):
            medida = re.search(rf"{propiedad}\s*:\s*(\d+)px", regla.group(1))
            assert medida and int(medida.group(1)) >= 44, (
                f"El botón del menú tiene {propiedad} por debajo de 44px: con "
                "el pulgar se falla y se toca el título de la vista"
            )


class TestNingunCampoHaceZoomEnIOS:
    """Safari en iPhone amplía la página entera cuando el campo que recibe el
    foco tiene la letra por debajo de 16px, y al terminar la deja ampliada. Se
    sale con dos dedos, y parece que algo se ha roto.

    16px es un umbral del navegador, no una preferencia de tamaño.
    """

    #: Los que se encontraron por debajo, cada uno con su clase propia. Un
    #: `input` a secas tiene especificidad (0,0,1) y pierde contra todos ellos:
    #: con la regla general puesta, el buscador seguía en 12px y los cinco
    #: campos de Ajustes en 13px.
    CON_CLASE_PROPIA = [
        ".session-search input",
        ".ajuste input",
        ".ajuste textarea",
        ".ajustes-campo input",
        ".token-input",
    ]

    @pytest.mark.parametrize("selector", CON_CLASE_PROPIA)
    def test_el_bloque_movil_los_nombra_uno_a_uno(self, bloque_movil, selector):
        assert selector in bloque_movil, (
            f"`{selector}` no aparece en el bloque móvil. Tiene una clase, así "
            "que la regla general de `input` no le llega y ese campo hará zoom"
        )

    def test_y_los_pone_en_16(self, bloque_movil):
        regla = re.search(r"\.token-input,[^{]*{([^}]*)}", bloque_movil)

        assert regla, "El bloque de tamaños de campo cambió de forma"
        medida = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", regla.group(1))
        assert medida and float(medida.group(1)) >= 16, (
            f"Los campos quedan en {medida.group(1) if medida else '?'}px. Por "
            "debajo de 16, iOS amplía la página al tocarlos"
        )


class TestLaAlturaSeMideEnLoQueSeVe:
    def test_la_aplicacion_usa_dvh(self, css):
        """`100vh` cuenta con la barra de direcciones retraída, así que la
        aplicación sale más alta que lo visible. Con `overflow: hidden`, lo que
        sobra —el compositor, abajo— no se alcanza de ninguna manera.
        """
        regla = re.search(r"\.app-shell\s*{([^}]*)}", css)

        assert regla, "Falta .app-shell"
        assert "100dvh" in regla.group(1), (
            ".app-shell no usa `100dvh`. Con `100vh` el compositor queda fuera "
            "de la pantalla en el móvil"
        )
        assert "100vh" in regla.group(1), (
            "Falta el `100vh` de respaldo para quien no entienda `dvh`"
        )


class TestNadaSeSaleDeLaPantalla:
    def test_las_sugerencias_pueden_encogerse(self, css):
        """`1fr` es `minmax(auto, 1fr)`, y ese `auto` no baja del contenido
        mínimo. El detalle de la sugerencia lleva `white-space: nowrap`, así que
        su mínimo es la frase entera: en 320px las tarjetas salían de 341px.
        """
        # Se busca la ÚLTIMA declaración que aplica en el móvil, como haría el
        # navegador. Un `re.search` con DOTALL desde la media query encontraba
        # la siguiente regla `.sugerencias` que hubiera en el fichero, no la de
        # dentro del bloque, y pasaba leyendo el `minmax(0` de otra.
        aplicables = [
            (posicion, valor)
            for posicion, valor, aplica in
            declaraciones_de(css, ".sugerencias", "grid-template-columns")
            if aplica
        ]

        assert aplicables, "Ya no hay regla móvil para las sugerencias"
        _, ultimo = max(aplicables, key=lambda par: par[0])
        assert "minmax(0" in ultimo, (
            "El grid de sugerencias usa `1fr` sin `minmax(0, ...)`: las "
            "tarjetas no pueden encogerse y se desbordan en pantallas estrechas"
        )

    def test_lo_de_arriba_va_en_el_flujo_y_no_flota(self, css):
        """El botón del menú y la cuenta flotaban en posición absoluta, y el
        contenido empezaba en el píxel 0. Se solapaban con lo primero que hubiera:
        el primer mensaje, y sobre todo el aviso de confirmar el correo —lo que ve
        toda cuenta nueva—, cuyo título salía por debajo del icono del menú.

        El arreglo de entonces fue reservar 52px arriba del contenedor. Desde la
        V2.0.28 los dos viven en `.barra-superior`, **dentro del flujo**: el
        contenido empieza debajo por construcción. Lo que hay que fijar ahora es
        que sigan ahí y que nada los vuelva a sacar del flujo, porque entonces el
        solape vuelve sin que falle nada.
        """
        app = (CSS.parent / "App.tsx").read_text(encoding="utf-8")
        inicio = app.find('<header className="barra-superior">')
        assert inicio >= 0, "Ya no hay barra superior en App.tsx"
        cabecera = app[inicio:app.index("</header>", inicio)]
        assert "sidebar-toggle" in cabecera, "El botón del menú salió de la barra superior"
        assert "<BarraCuenta" in cabecera, "La cuenta salió de la barra superior"

        for selector in (".barra-superior", ".barra-superior .sidebar-toggle", ".barra-superior .barra-cuenta"):
            for bloque in re.finditer(rf"(?<![\w.-]){re.escape(selector)}\s*{{([^}}]*)}}", css):
                posicion = re.search(r"position\s*:\s*([a-z]+)", bloque.group(1))
                assert not posicion or posicion.group(1) not in ("absolute", "fixed"), (
                    f"`{selector}` vuelve a flotar ({posicion.group(1)}): se solapará "
                    "con lo primero de cada vista"
                )

    def test_el_compositor_queda_pegado_abajo_en_el_saludo(self, bloque_movil):
        """Con el aviso del correo puesto, el saludo mide 790px y solo hay 483:
        se desplaza bien, pero el compositor caía por debajo del pliegue y había
        que buscarlo. Es lo primero que se usa.
        """
        regla = re.search(r"\.chat-hero \.composer-wrapper\s*{([^}]*)}", bloque_movil)

        assert regla, "El compositor ya no se queda a la vista en el saludo"
        assert "sticky" in regla.group(1)
        assert "background" in regla.group(1), (
            "El compositor pegado necesita fondo propio, o se le ve el texto "
            "que pasa por detrás"
        )

    def test_el_saludo_puede_encogerse(self, bloque_movil):
        """`overflow-y: auto` no sirve de nada sin `min-height: 0`: un elemento
        flex tiene `min-height: auto`, que lo obliga a ser al menos tan alto
        como su contenido, así que en lugar de desplazarse crece y se sale.
        Medido: el compositor acababa en 816 con la ventana en 664.
        """
        regla = re.search(r"\.chat-hero\s*{([^}]*)}", bloque_movil)

        assert regla, "El bloque móvil ya no toca el saludo"
        assert "min-height: 0" in regla.group(1)
        assert "overflow-y: auto" in regla.group(1)

    def test_el_titulo_de_la_vista_se_recorta_antes_de_empujar(self, bloque_movil):
        """El botón «Actualizar» va con `margin-left: auto`, así que si el
        título no encoge, el que se sale es el botón. Medido en 320px:
        terminaba en 332.
        """
        regla = re.search(r"\.view-header h2\s*{([^}]*)}", bloque_movil)

        assert regla, "El título de la vista ya no se recorta en el móvil"
        assert "ellipsis" in regla.group(1)

    def test_las_filas_de_auditoria_pueden_envolver(self, bloque_movil):
        """Hora, herramienta y veredicto en una línea que no se partía: en
        320px el veredicto acababa en 354.
        """
        regla = re.search(r"\.audit-item\s*{([^}]*)}", bloque_movil)

        assert regla, "Las filas de auditoría ya no envuelven en el móvil"
        assert "wrap" in regla.group(1)


class TestElChatRepartLaAlturaConLoQueTengaEncima:
    """`height: 100%` se resuelve contra la altura del contenedor entero, no
    contra lo que queda libre. Con el aviso de confirmar el correo arriba —166px
    en un móvil, 93 en escritorio— el chat seguía pidiendo la ventana completa y
    empezaba por debajo del aviso: terminaba en 838 con la ventana en 664, y el
    compositor en 809.

    Pasaba en escritorio también, solo que ahí sobra alto y no se veía tanto.
    """

    def test_usa_flex_y_no_una_altura_fija(self, css):
        regla = re.search(r"\.chat-view\s*{([^}]*)}", css)

        assert regla, "Falta .chat-view"
        cuerpo = regla.group(1)
        assert "height: 100%" not in cuerpo, (
            ".chat-view vuelve a pedir `height: 100%`. Con cualquier cosa más "
            "dentro de .main-content —el aviso del correo— el compositor se sale"
        )
        assert "flex: 1" in cuerpo
        assert "min-height: 0" in cuerpo, (
            "Sin `min-height: 0` la lista de mensajes no puede encogerse y "
            "empuja en lugar de desplazarse"
        )


class TestElCajonSeComportaComoUnCajon:
    def test_deja_ver_algo_detras(self, bloque_movil):
        """A pantalla completa parece otra página, y entonces se busca un botón
        de volver que no hay. Dejando ver un trozo de la conversación se
        entiende que está encima y que tocando fuera se cierra.
        """
        regla = re.search(r"\.sidebar\s*{([^}]*)}", bloque_movil)

        assert regla, "El bloque móvil ya no dimensiona el cajón"
        ancho = re.search(r"width\s*:\s*min\((\d+)vw", regla.group(1))
        assert ancho and int(ancho.group(1)) <= 90, (
            "El cajón ocupa toda la pantalla: sin ver nada detrás, no se "
            "adivina que se cierra tocando fuera"
        )

    def test_hay_un_velo_que_lo_cierra(self, css):
        """Existía ya, y conviene que no desaparezca: es lo que hace que
        tocar fuera cierre el menú.
        """
        assert ".sidebar-backdrop" in css
        assert re.search(r"\.sidebar-backdrop\.visible\s*{\s*display:\s*block", css), (
            "El velo del cajón ya no se muestra: sin él, tocar fuera no cierra "
            "el menú y hay que acertar otra vez en el botón"
        )
