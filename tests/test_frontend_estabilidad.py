"""
Comprobaciones estáticas sobre el frontend, desde Python.

**Por qué existen.** Un fallo dejó la web entera atascada en la pantalla de
arranque: al añadir un reintento, el camino de éxito salía con un `return` que
saltaba por encima del `finally` donde se apagaba la bandera `cargando`. Solo
fallaba cuando la petición **funcionaba**, así que probar con el backend caído no
lo reproducía, y las verificaciones contra producción hablan con la API sin pasar
por React.

Hay pruebas de verdad del frontend en `web/src/**.test.tsx` (vitest), que es
donde se comprueba el comportamiento. Estas son otra cosa: reglas que se pueden
verificar leyendo el código y que valen para **todo** el proyecto, presente y
futuro, sin tener que acordarse de escribir una prueba por cada pantalla nueva.

Es el mismo enfoque de `test_tema_web.py`: Python mirando el frontend para fijar
invariantes que el compilador no ve.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web" / "src"

#: Encender / apagar. Si una pantalla nueva añade su propia bandera, se añade
#: aquí: la lista es corta a propósito, para que ampliarla obligue a pensar.
BANDERAS = [
    ("setCargando(true)", "setCargando(false)"),
    ("setLoading(true)", "setLoading(false)"),
    ("setGuardando(true)", "setGuardando(false)"),
    ("setEnviando(true)", "setEnviando(false)"),
    ("setBorrando(true)", "setBorrando(false)"),
    ("setSubiendo(true)", "setSubiendo(false)"),
]

#: Lo que se sale de la regla, con su motivo. Vacío sería lo ideal; una lista
#: con explicaciones es lo honesto.
JUSTIFICADAS = {
    # Al terminar bien recarga la página entera, así que el componente deja de
    # existir: apagar la bandera ahí sería escribir en algo desmontado.
    ("PanelCuenta.tsx", "setBorrando(true)"),
}


def _fuentes():
    for fichero in sorted(WEB.rglob("*.tsx")) + sorted(WEB.rglob("*.ts")):
        if fichero.name.endswith((".test.tsx", ".test.ts")):
            continue
        yield fichero


class TestNingunaPantallaSeQuedaCargandoParaSiempre:
    """La regla: una bandera de «estoy ocupado» se apaga **pase lo que pase**.

    Es lo único que separa la aplicación de una pantalla de espera perpetua, y
    no puede depender de por qué camino se llegó al final de la función.
    """

    def test_toda_bandera_encendida_tiene_su_finally(self):
        sospechosas = []

        for fichero in _fuentes():
            lineas = fichero.read_text(encoding="utf-8").splitlines()

            for encender, apagar in BANDERAS:
                for numero, linea in enumerate(lineas, 1):
                    if encender not in linea:
                        continue
                    if (fichero.name, encender) in JUSTIFICADAS:
                        continue

                    # El resto de la función, aproximado. No es análisis de
                    # flujo —eso pide un compilador—; es la comprobación barata
                    # que habría cazado el fallo real.
                    resto = "\n".join(lineas[numero - 1: numero + 120])

                    if apagar not in resto:
                        sospechosas.append(
                            f"{fichero.name}:{numero} enciende {encender} y nunca la apaga"
                        )
                    elif "finally" not in resto:
                        sospechosas.append(
                            f"{fichero.name}:{numero} apaga {apagar} pero SIN finally: "
                            "un camino de error la dejaría encendida"
                        )

        assert not sospechosas, (
            "Banderas que pueden quedarse encendidas:\n  " + "\n  ".join(sospechosas)
        )

    def test_ningun_return_salta_por_encima_de_su_finally(self):
        """El fallo exacto, en su forma reconocible.

        Dos bloques `try` seguidos, con un `return` en el primero y el `finally`
        en el segundo. Se lee como si el `finally` cubriera los dos, y no es así.
        """
        patron = re.compile(
            r"try\s*\{[^}]*\breturn\b[^}]*\}\s*catch[^}]*\}\s*\n\s*\n\s*try\s*\{",
            re.DOTALL,
        )
        sospechosas = []

        for fichero in _fuentes():
            texto = fichero.read_text(encoding="utf-8")
            for coincidencia in patron.finditer(texto):
                linea = texto[: coincidencia.start()].count("\n") + 1
                sospechosas.append(
                    f"{fichero.name}:{linea}: un `return` sale de un try cuyo "
                    "`finally` pertenece a OTRO bloque"
                )

        assert not sospechosas, "\n  ".join(["Encontrado:", *sospechosas])


class TestLasExcepcionesEstanExplicadas:
    """Una excepción sin motivo escrito se convierte en precedente."""

    @pytest.mark.parametrize("fichero, bandera", sorted(JUSTIFICADAS))
    def test_la_justificada_lleva_su_comentario(self, fichero, bandera):
        ruta = next(f for f in _fuentes() if f.name == fichero)
        texto = ruta.read_text(encoding="utf-8")

        assert "finally" in texto or "a proposito" in texto or "a propósito" in texto, (
            f"{fichero} se salta la regla sin decir por qué"
        )


class TestLaInterfazNoHablaDeQuienLaEscribio:
    """Morgan lo van a usar otras personas, y la interfaz tiene que estar
    escrita para cualquiera.

    **El caso real.** Los campos del perfil traían de fondo mi nombre, mi ocupación y
    lo que hago: los tres calcados de mí, que monté Morgan. A cualquier otra
    persona le llegaba un formulario que parecía ya relleno con la vida de un
    desconocido.

    No es un fallo funcional —el texto de fondo no se guarda— pero sí lo primero
    que ve alguien nuevo en la pantalla de su propio perfil.
    """

    #: Lo que no puede aparecer en texto que se ve. Se busca como palabra
    #: completa para no saltar con `andar`, `mando` o similares. Mis apellidos y mi
    #: correo van en `tests/personales.txt`, que git ignora: así la prueba los vigila en
    #: mi PC sin publicarlos.
    _FICHERO = Path(__file__).with_name("personales.txt")
    PERSONALES = ["andy"] + ([p.strip().lower() for p in _FICHERO.read_text(encoding="utf-8").splitlines()
                              if p.strip()] if _FICHERO.exists() else [])

    def _textos_visibles(self):
        """Cadenas entre comillas de los ficheros del frontend.

        No distingue una etiqueta de un identificador: es una red gruesa a
        propósito, porque el nombre de una persona tampoco pinta en un
        identificador de este proyecto.
        """
        import re

        for fichero in _fuentes():
            texto = fichero.read_text(encoding="utf-8")
            # Fuera los comentarios: ahí sí se explica por qué las cosas son
            # como son, y alguna explicación menciona nombres.
            sin_bloque = re.sub(r"/\*.*?\*/", "", texto, flags=re.DOTALL)
            sin_linea = re.sub(r"//[^\n]*", "", sin_bloque)

            for cadena in re.findall(r"'([^'\n]{2,120})'|\"([^\"\n]{2,120})\"", sin_linea):
                valor = cadena[0] or cadena[1]
                if valor:
                    yield fichero.name, valor

    def test_ningun_texto_lleva_datos_de_una_persona_concreta(self):
        import re

        encontrados = []
        for fichero, valor in self._textos_visibles():
            for personal in self.PERSONALES:
                if re.search(rf"\b{personal}\b", valor, re.IGNORECASE):
                    encontrados.append(f"{fichero}: {valor!r}")

        assert not encontrados, (
            "La interfaz habla de una persona concreta:\n  " + "\n  ".join(encontrados)
        )

    def test_los_textos_de_fondo_del_perfil_describen_el_campo(self):
        """Y no dan un ejemplo que parezca la respuesta de alguien.

        En un perfil no hay formato que adivinar, así que un ejemplo concreto no
        ayuda: lo útil es saber para qué sirve el dato. Donde SÍ hay formato
        —idioma, estilo— el ejemplo se mantiene, y no habla de nadie.
        """
        import re

        ajustes = (WEB / "components" / "SettingsView.tsx").read_text(encoding="utf-8")

        for campo in ("nombre", "ocupacion", "sobre_mi"):
            linea = next(
                l for l in ajustes.splitlines()
                if f"id: '{campo}'" in l
            )
            marcador = re.search(r"placeholder: '([^']*)'", linea)

            assert marcador, f"El campo {campo} perdió su texto de fondo"
            valor = marcador.group(1)
            # Un texto que describe empieza por «tu», «a qué», «con qué», «lo
            # que»… Uno que da un ejemplo empieza por un nombre propio o una
            # frase en primera persona.
            assert not valor.lower().startswith(("trabajo ", "soy ", "estudiante")), (
                f"El texto de fondo de {campo} suena a la respuesta de alguien: {valor!r}"
            )


class TestElFrontendTienePruebasPropias:
    """Python no puede comprobar la lógica de React. Si el runner desaparece,
    el frontend vuelve a quedarse sin red — que es como llegó a producción una
    web que no pasaba de la pantalla de arranque."""

    def test_hay_un_runner_configurado(self):
        import json

        paquete = json.loads(
            (WEB.parent / "package.json").read_text(encoding="utf-8")
        )

        assert "test" in paquete.get("scripts", {}), (
            "web/package.json ya no tiene script de pruebas"
        )

    def test_y_al_menos_una_prueba_escrita(self):
        pruebas = list(WEB.rglob("*.test.tsx")) + list(WEB.rglob("*.test.ts"))

        assert pruebas, "No queda ninguna prueba del frontend"

    def test_la_configuracion_de_pruebas_no_vive_en_la_del_build(self):
        """`vite.config.ts` es SOLO del build, y tiene que seguir siéndolo.

        Añadirle un bloque `test` es lo natural y es lo que se intentó primero.
        No funciona: vitest trae su propia copia de Vite, distinta de la del
        proyecto, y `tsc -b` falla por tipos incompatibles.

        Lo grave no es que no corran las pruebas. `tsc -b` es el primer paso de
        `npm run build`, que es **lo que ejecuta Vercel**: un error de tipos en
        la configuración de las pruebas deja la web sin desplegar. Pasó.
        """
        config = (WEB.parent / "vite.config.ts").read_text(encoding="utf-8")

        # Se busca el IMPORT, no cualquier mención: el propio fichero explica en
        # un comentario por qué la configuración de pruebas vive aparte, y una
        # comprobación por subcadena saltaría con esa explicación.
        importa_vitest = "from 'vitest" in config or 'from "vitest' in config

        assert not importa_vitest, (
            "vite.config.ts importa de vitest. Eso rompe `npm run build`, que es "
            "lo que despliega Vercel: la configuración de las pruebas va en "
            "vitest.config.ts"
        )
        assert "test:" not in config, (
            "vite.config.ts lleva un bloque `test`. Con los tipos de Vite, "
            "`tsc -b` lo rechaza y el despliegue falla"
        )
        assert (WEB.parent / "vitest.config.ts").exists(), (
            "Falta vitest.config.ts: sin él las pruebas del frontend no corren"
        )

    def test_se_cubre_el_arranque_de_la_sesion(self):
        """La pantalla que se quedó atascada. Es el sitio donde un fallo deja la
        aplicación entera inservible, así que su prueba no se borra."""
        assert (WEB / "components" / "Cuenta.test.tsx").exists()


class TestTodaPeticionPasaPorElMismoSitio:
    """Un `fetch` suelto se queda sin sesión, y no lo dice.

    `request()` pone tres cosas que ninguna ruta debería tener que recordar:
    `credentials: 'include'` para que viaje la cookie, la cabecera CSRF para que
    el servidor acepte el POST, y el aviso de sesión caducada.

    Subir un archivo tuvo su propio `fetch` durante cinco versiones, copiado del
    de `request()`. En la copia se quedaron fuera las dos primeras, así que
    **subir un archivo llegaba sin sesión**: el servidor contestaba «Necesitas
    iniciar sesión para usar Morgan» a quien la tenía abierta. Reproducido
    contra producción: sin cookie da 401, con cookie y sin CSRF da 403.

    El motivo de la copia era real —`request()` fijaba `Content-Type` a JSON y en
    multipart lo tiene que poner el navegador— pero la salida correcta era
    enseñarle multipart a `request()`, no tener dos caminos que se separan solos.
    """

    def test_solo_hay_un_fetch_en_el_cliente(self):
        cliente = (WEB / "lib" / "api.ts").read_text(encoding="utf-8")
        cuantos = len(re.findall(r"\bfetch\(", cliente))

        assert cuantos == 1, (
            f"Hay {cuantos} llamadas a fetch() en api.ts. Solo puede haber una, "
            "dentro de request(): cualquier otra se queda sin la cookie de "
            "sesión y sin la cabecera CSRF, y falla diciendo que no has entrado"
        )

    def test_y_ninguna_pantalla_llama_a_la_red_por_su_cuenta(self):
        """Lo mismo, un escalón más arriba: los componentes usan `morganAPI`."""
        culpables = [
            f.relative_to(WEB).as_posix()
            for f in WEB.rglob("*")
            if f.suffix in (".ts", ".tsx")
            and f.name not in ("api.ts",)
            and not f.name.endswith(".test.ts")
            and not f.name.endswith(".test.tsx")
            and re.search(r"\bfetch\(", f.read_text(encoding="utf-8"))
        ]

        assert not culpables, (
            f"Estos ficheros llaman a fetch() directamente: {culpables}. "
            "Tienen que pasar por morganAPI, o llegarán sin sesión"
        )


class TestElArranqueDeLaWebNoVaEnSerie:
    """Lo primero que se pide no puede esperar a lo más pesado.

    **El coste, medido.** `loadSessions` salía por la puerta de atrás si
    `apiStatus` no era `'online'`, así que la lista de conversaciones esperaba a
    que respondiera `/status` — el endpoint que comprueba todos los componentes.
    Con el backend dormido son **35,7 segundos** con la aplicación mostrando
    nada, y solo entonces empezaba a pedir datos.

    La lista no necesita saber si el sistema está sano para poder pedirse: si
    falla, no se muestra, que es lo que ya hacía su `catch`.
    """

    @staticmethod
    def _cuerpo_de_load_sessions() -> str:
        """Solo `loadSessions`, desde su declaración hasta sus dependencias.

        Se recorta con cuidado porque `apiStatus === 'online'` aparece por toda
        la aplicación para decidir qué pintar, y eso está bien. Lo que no puede
        estar es dentro de esta función ni en sus dependencias.
        """
        app = (WEB / "App.tsx").read_text(encoding="utf-8")
        desde = app.index("const loadSessions")
        # El primer cierre de `useCallback` que venga después es el suyo.
        cierre = app.index("}, [", desde)
        return app[desde : app.index("]", cierre) + 1]

    def test_la_lista_no_espera_a_la_comprobacion_de_estado(self):
        cuerpo = self._cuerpo_de_load_sessions()

        assert "apiStatus !== 'online'" not in cuerpo, (
            "La lista de conversaciones vuelve a esperar a que `/status` "
            "responda. Con el backend dormido son 35,7 segundos mostrando nada"
        )
        assert "=== 'offline'" in cuerpo, (
            "Se ha quitado la guarda entera. Insistir cuando ya se sabe que no "
            "hay nadie al otro lado tampoco tiene sentido"
        )

    def test_y_no_se_pide_dos_veces(self):
        """Con `apiStatus` en las dependencias, pasar de 'loading' a 'online'
        cambiaba la identidad de la función, el efecto se repetía y la lista se
        pedía **dos veces** al arrancar. Medido en producción: `/sessions` a los
        4.705 ms y otra vez a los 6.584. Un viaje de 1,2 s tirado.

        Lo introdujo el arreglo de la prueba anterior, así que las dos van
        juntas: una quiere que no espere, la otra que no insista.
        """
        cuerpo = self._cuerpo_de_load_sessions()
        dependencias = cuerpo[cuerpo.rindex("}, ["):]

        assert "apiStatus" not in dependencias, (
            "`apiStatus` ha vuelto a las dependencias de `loadSessions`, así "
            "que la lista se pedirá dos veces al arrancar"
        )
