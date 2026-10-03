"""
La API se sirve por el mismo origen que la web. Aquí se fija por qué.

**El fallo que dio origen a esto.** La web vivía en `morgan-ia.vercel.app` y la
API en otro dominio de Render (entonces `morgan-api.onrender.com`; hoy
`morgan-ia-2-0.onrender.com`, que se recreó en Virginia). Son dominios registrables distintos, así que
la cookie de sesión que emitía la API era, para el navegador, una **cookie de
terceros**. En un ordenador con Chrome eso funcionaba y por eso no se vio. En un
móvil no: Safari en iPhone descarta esas cookies sin excepción desde que existe
ITP, y Firefox móvil las aísla por sitio, con el mismo efecto práctico.

El síntoma era exacto y desconcertante: te registrabas, el servidor mandaba la
cookie, el navegador la tiraba, `/auth/yo` respondía «no autenticado» y la web
volvía al login. Para siempre, y sin ningún error a la vista.

**No era configuración.** Por muy bien puesta que estuviera la URL de la API, el
navegador no iba a guardar esa cookie. Lo único que lo arregla es que la API
responda desde el mismo origen que la página, y eso es lo que hace el proxy de
`vercel.json`.

Estas comprobaciones no reproducen el fallo —haría falta un navegador móvil de
verdad—, pero sí impiden las formas de deshacer el arreglo sin darse cuenta:
quitar el proxy, dejarlo detrás del comodín de la SPA, o volver a apuntar el
frontend al dominio de Render.

**Y una más, que costó un despliegue roto.** Había dos `vercel.json`: uno en la
raíz y otro en `web/`. El proxy se puso en el de `web/`, que **Vercel no lee** —
el proyecto tiene su raíz en el repositorio y construye `web/` con
`npm --prefix web`. El resultado fue peor que el fallo original: el frontend
empezó a pedir `/api/...`, no había proxy, y el comodín de la SPA devolvió el
HTML de la página a cada llamada a la API. Un fichero de configuración que no se
aplica no da ningún aviso, así que ahora hay una prueba que exige que solo haya
uno.
"""

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "web"

#: El dominio del backend. Solo debe aparecer en un sitio: el destino del proxy.
#:
#: Cambio el 2026-09-12 al recrear el servicio en Virginia. Render no permite
#: mover la region de un servicio ya creado, asi que hubo que crear otro, y de
#: su URL cuelgan tres cosas: este proxy, MORGAN_API_URL y la URL de retorno de
#: la app OAuth de GitHub. Ver docs/despliegue.md.
DOMINIO_API = "morgan-ia-2-0.onrender.com"

#: El de Frankfurt, que se apago. Aqui para que no vuelva a colarse en el
#: codigo: apuntar a el nadie lo notaria hasta que el servicio dejara de
#: existir, y entonces el sintoma seria la web entera sin API.
DOMINIO_VIEJO = "morgan-api.onrender.com"


def configs_de_vercel() -> list[Path]:
    """Todos los `vercel.json` del repositorio, sin dependencias instaladas."""
    return [
        p for p in RAIZ.rglob("vercel.json")
        if "node_modules" not in p.parts and "dist" not in p.parts
    ]


@pytest.fixture(scope="module")
def vercel() -> dict:
    return json.loads((RAIZ / "vercel.json").read_text(encoding="utf-8"))


class TestSoloHayUnaConfiguracionDeVercel:
    def test_no_hay_dos_vercel_json(self):
        """El que manda es el de la raíz. Un segundo fichero es una trampa.

        Pasó: el proxy se escribió en `web/vercel.json` y Vercel nunca lo leyó,
        porque el proyecto construye desde la raíz con `npm --prefix web`. El
        frontend ya pedía `/api/...`, no había proxy que lo atendiera, y el
        comodín de la SPA devolvió el HTML de la página a cada llamada. Peor que
        el fallo que se estaba arreglando, y sin ningún aviso: un fichero de
        configuración que no se aplica no se queja.
        """
        encontrados = sorted(
            p.relative_to(RAIZ).as_posix() for p in configs_de_vercel()
        )
        assert encontrados == ["vercel.json"], (
            f"Hay más de un vercel.json: {encontrados}. Vercel solo lee el de la "
            "raíz del proyecto; los demás parecen configuración y no lo son"
        )


class TestElProxyExisteYEstaEnSuSitio:
    def test_hay_una_regla_que_lleva_api_al_backend(self, vercel):
        destinos = [
            r["destination"] for r in vercel["rewrites"]
            if r["source"].startswith("/api")
        ]
        assert destinos, (
            "No hay proxy de /api. Sin él la API vuelve a ser un tercero y la "
            "sesión no se sostiene en móvil"
        )
        assert DOMINIO_API in destinos[0], (
            f"El proxy de /api no apunta a {DOMINIO_API}: {destinos[0]}"
        )

    def test_el_proxy_va_antes_que_el_comodin_de_la_spa(self, vercel):
        """El orden es todo el arreglo.

        La regla de la SPA (`/(.*)` → `index.html`) se traga cualquier ruta. Si
        el proxy queda detrás, `/api/auth/yo` devuelve el HTML de la página en
        lugar de la respuesta de la API, y el fallo vuelve disfrazado de otra
        cosa: un error de JSON al arrancar.
        """
        fuentes = [r["source"] for r in vercel["rewrites"]]
        comodin = next(
            (i for i, s in enumerate(fuentes) if s in ("/(.*)", "/(.*)$")), None
        )
        api = next((i for i, s in enumerate(fuentes) if s.startswith("/api")), None)

        assert comodin is not None, "Falta el comodín de la SPA"
        assert api is not None and api < comodin, (
            f"El proxy de /api está en la posición {api} y el comodín en "
            f"{comodin}. Detrás del comodín el proxy no se aplica nunca"
        )

    def test_el_proxy_no_se_cachea(self, vercel):
        """Una respuesta de la API cacheada en el CDN es una fuga entre cuentas.

        Vercel respeta por defecto las cabeceras de caché del origen en los
        proxies externos. `/auth/yo` guardado en el borde y servido a otra
        persona le enseñaría la sesión ajena — el mismo tipo de fallo que ya
        costó un aislamiento roto en Supabase, esta vez en la capa de red.

        Se apaga explícitamente en lugar de confiar en que la API no mande
        nunca una cabecera de caché: eso es una promesa sobre todas las rutas,
        presentes y futuras.
        """
        cabeceras = {
            c["key"].lower(): c["value"]
            for bloque in vercel.get("headers", [])
            if bloque["source"].startswith("/api")
            for c in bloque["headers"]
        }
        assert cabeceras.get("x-vercel-enable-rewrite-caching") == "0", (
            "Falta x-vercel-enable-rewrite-caching: 0 en /api. Sin ella el CDN "
            "puede cachear respuestas de sesión y servírselas a otra cuenta"
        )


class TestElFrontendNoVuelveAApuntarAOtroDominio:
    def test_el_build_de_vercel_fuerza_el_mismo_origen(self):
        """El valor del panel de Vercel se ignora a propósito.

        Podría pedirse que se cambie ahí, pero olvidarlo deja el fallo intacto y
        sin ninguna señal de por qué la sesión no se sostiene. Que lo decida el
        código es lo único que no se puede olvidar.
        """
        config = (WEB / "vite.config.ts").read_text(encoding="utf-8")

        # Se busca la expresión entera, no sus trozos. Buscar
        # `process.env.VERCEL` por separado no vale: `VERCEL_GIT_COMMIT_SHA`
        # empieza igual, y la comprobación pasaba con el arreglo deshecho.
        # `'/api'` tampoco: aparece en el proxy de desarrollo, ahí abajo.
        forzado = re.search(
            r"VITE_MORGAN_API_URL'\s*:.*?process\.env\.VERCEL\s*\?\s*'/api'",
            config,
            re.DOTALL,
        )
        assert forzado, (
            "vite.config.ts ya no fuerza /api en los builds de Vercel: el "
            "frontend volvería a llamar a otro dominio"
        )

    def test_el_codigo_no_lleva_el_dominio_del_backend_escrito(self):
        """Ningún fichero del frontend nombra Render.

        Una URL absoluta al backend en cualquier sitio —una llamada suelta, un
        enlace, un valor por defecto— se salta el proxy y arrastra el problema
        de la cookie de terceros consigo.
        """
        culpables = [
            f.relative_to(RAIZ).as_posix()
            for f in (WEB / "src").rglob("*")
            if f.suffix in (".ts", ".tsx")
            and DOMINIO_API in f.read_text(encoding="utf-8")
        ]
        assert not culpables, (
            f"Estos ficheros nombran {DOMINIO_API} y se saltarían el proxy: "
            f"{culpables}"
        )


class TestElDominioApagadoNoVuelve:
    """El servicio de Fráncfort se apagó el 2026-09-12 al recrearlo en Virginia.

    Apuntar a un dominio que ya no existe es un fallo especialmente malo: **no
    da ningún aviso al escribirlo**. Pasa la revisión, pasa el build, y el
    síntoma aparece en producción como la web entera sin API.

    Y no es hipotético: este mismo proxy estuvo un despliegue apuntando a un
    `vercel.json` que Vercel no leía, con el mismo resultado práctico. Lo que
    protege de eso no es acordarse, es que algo falle.
    """

    def test_el_proxy_no_apunta_al_servicio_apagado(self, vercel):
        destino = next(
            r["destination"] for r in vercel["rewrites"]
            if r["source"].startswith("/api")
        )

        assert DOMINIO_VIEJO not in destino, (
            f"El proxy apunta a {DOMINIO_VIEJO}, que ya no existe. La web se "
            "queda sin API y el error no dice por qué."
        )

    def test_ningun_fichero_del_proyecto_lo_nombra(self):
        """Salvo esta prueba, que es la que lo vigila, y la documentación, que
        cuenta la historia de por qué cambió.
        """
        mirar = [
            *(RAIZ / "src").rglob("*.py"),
            *(WEB / "src").rglob("*.ts"),
            *(WEB / "src").rglob("*.tsx"),
            RAIZ / "vercel.json",
            RAIZ / "render.yaml",
        ]
        culpables = [
            f.relative_to(RAIZ).as_posix()
            for f in mirar
            if f.is_file() and DOMINIO_VIEJO in f.read_text(encoding="utf-8")
        ]

        assert not culpables, (
            f"Estos ficheros siguen nombrando el servicio apagado "
            f"({DOMINIO_VIEJO}): {culpables}"
        )


#: Lo que aguanta el proxy del borde antes de cortar con un 502 suyo. Medido
#: contra producción el 2026-09-09: dos cortes a 120.1 s y un éxito a 104.7 s.
#: No está documentado por Vercel, así que este número es una medición, no una
#: promesa: si algún día un turno vuelve a morir en un
#: `ROUTER_EXTERNAL_TARGET_ERROR`, se vuelve a medir y se baja.
TOPE_MEDIDO_DEL_PROXY = 120


class TestElTurnoCabeDentroDeLoQueAguantaElProxy:
    """El proxy trajo un reloj que no es nuestro, y hay que respetarlo.

    Con el tope del turno por encima del del proxy, un turno de entre 120 y 180
    segundos **no le llega a nadie**: el borde ya respondió una página HTML de
    error que no dice de qué va ni sugiere nada, y el trabajo de Morgan se tira.

    Con el tope por debajo, el que termina antes es Morgan, y contesta él
    explicando que ha parado por tiempo. No se gana tiempo de trabajo: se gana
    que el aviso venga de quien sabía lo que estaba haciendo.
    """

    def test_en_la_nube_morgan_se_rinde_antes_que_el_borde(self):
        from src.config import Settings

        assert Settings.turn_timeout_nube < TOPE_MEDIDO_DEL_PROXY, (
            f"El turno en la nube ({Settings.turn_timeout_nube} s) llega o pasa "
            f"del tope del proxy ({TOPE_MEDIDO_DEL_PROXY} s). Los turnos largos "
            "morirán en un 502 de Vercel en vez de en el aviso de Morgan"
        )

    def test_y_con_holgura_para_el_paso_que_ya_habia_empezado(self):
        """Rendirse a los 119 no vale, y por una razón concreta.

        El plazo se mira **entre** pasos, no dentro de uno: un paso que ya
        arrancó se termina. Así que el margen tiene que cubrir el paso más largo
        que pueda empezar justo antes de cumplirse el plazo, y ese es una
        llamada al modelo: `llm_timeout`.

        Con 110 s se probó, y un turno murió igual en el proxy a los 120,1 s.
        """
        from src.config import Settings

        margen = TOPE_MEDIDO_DEL_PROXY - Settings.turn_timeout_nube
        assert margen >= Settings.llm_timeout, (
            f"Solo quedan {margen} s entre que Morgan se rinde y que el proxy "
            f"corta, y una llamada al modelo puede durar {Settings.llm_timeout} s. "
            "Un paso que arranque justo antes del plazo se pasa del tope"
        )

    def test_en_local_no_manda_el_proxy(self):
        """En tu equipo no hay borde que valga, y el encargo largo cabe.

        Bajarlo también aquí sería pagar en el Morgan local el precio de un
        problema que solo existe en la nube.
        """
        from src.config import Settings

        assert Settings.turn_timeout > TOPE_MEDIDO_DEL_PROXY, (
            "El Morgan local se ha quedado con el tope de la nube. Ahí no hay "
            "proxy: el encargo de investigación de tres pasos (126-135 s) cabe"
        )

    def test_el_entorno_elige_cual_de_los_dos(self, monkeypatch):
        from src import config

        def ajustes():
            config.reset_settings()
            return config.get_settings()

        monkeypatch.delenv("MORGAN_TURN_TIMEOUT", raising=False)

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        assert ajustes().turn_timeout == config.Settings.turn_timeout_nube

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
        assert ajustes().turn_timeout == config.Settings.turn_timeout

        # Y quien lo ponga a mano manda sobre los dos: hay despliegues en la nube
        # que no pasan por Vercel, y ahí este tope no pinta nada.
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_TURN_TIMEOUT", "150")
        assert ajustes().turn_timeout == 150

        config.reset_settings()
