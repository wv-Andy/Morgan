"""
Las cuentas a través de HTTP: cookies, CSRF y protección de rutas (identidad, V2.0 adelantada).

`test_cuentas.py` prueba la lógica; esto prueba la costura con HTTP, que es donde
se rompen las cosas de verdad: una cookie sin `HttpOnly`, un 401 sin cabeceras
CORS, una ruta que se olvidó de exigir sesión. Ninguna de esas se ve leyendo el
servicio.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.routes.cuentas import RecuperarRequest
from src.api.sesion_web import CABECERA_CSRF, COOKIE_CSRF, COOKIE_SESION
from src.config import reset_settings

BUENA = "contrasena-larga"

REGISTRO = {
    "username": "andy",
    "email": "andy@ejemplo.co",
    "password": "contrasena-larga",
}


@pytest.fixture
def cliente():
    """Morgan en local: no exige cuenta, pero permite crearlas."""
    return TestClient(create_app())


@pytest.fixture
def cliente_nube(monkeypatch):
    """Morgan en la nube: exige cuenta para todo lo que no sea entrar."""
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    # Un despliegue publico bien configurado. Sin esto, los enlaces del correo
    # apuntarian a localhost y el estado lo marca como averia — con razon, pero
    # tapando lo que estas pruebas quieren mirar.
    monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield TestClient(create_app())
    reset_settings()


def _entrar(cliente) -> str:
    """Registra y devuelve el token CSRF."""
    respuesta = cliente.post("/auth/registro", json=REGISTRO)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["csrf"]


class TestElRegistroPorHttp:
    def test_crea_la_cuenta_y_deja_la_sesion_abierta(self, cliente):
        respuesta = cliente.post("/auth/registro", json=REGISTRO)

        assert respuesta.status_code == 200
        assert COOKIE_SESION in respuesta.cookies
        assert cliente.get("/auth/yo").json()["autenticado"] is True

    def test_la_cookie_de_sesion_es_httponly(self, cliente):
        """Si el JavaScript puede leerla, cualquier script inyectado se lleva la
        sesión. En una aplicación que muestra texto de un modelo, eso no es una
        hipótesis remota."""
        respuesta = cliente.post("/auth/registro", json=REGISTRO)
        cabecera = "".join(respuesta.headers.get_list("set-cookie"))

        assert "morgan_sesion=" in cabecera
        assert "HttpOnly" in cabecera.split("morgan_csrf")[0]

    def test_la_cookie_csrf_no_es_httponly(self, cliente):
        """Esta sí tiene que leerse: su valor va en la cabecera. No es un secreto
        de sesión, es una prueba de mismo origen."""
        respuesta = cliente.post("/auth/registro", json=REGISTRO)
        trozo = [c for c in respuesta.headers.get_list("set-cookie") if "morgan_csrf" in c]

        assert trozo and "HttpOnly" not in trozo[0]

    def test_la_respuesta_no_incluye_la_contrasena_ni_el_hash(self, cliente):
        cuerpo = cliente.post("/auth/registro", json=REGISTRO).text

        assert REGISTRO["password"] not in cuerpo
        assert "password_hash" not in cuerpo
        assert "scrypt" not in cuerpo

    def test_los_datos_invalidos_dan_400_con_explicacion(self, cliente):
        respuesta = cliente.post(
            "/auth/registro", json={**REGISTRO, "password": "corta"}
        )

        assert respuesta.status_code == 400
        assert "8 caracteres" in respuesta.json()["error"]["message"]

    def test_el_duplicado_da_400(self, cliente):
        cliente.post("/auth/registro", json=REGISTRO)
        respuesta = cliente.post("/auth/registro", json=REGISTRO)

        assert respuesta.status_code == 400


class TestElAccesoPorHttp:
    def test_entra_y_sale(self, cliente):
        cliente.post("/auth/registro", json=REGISTRO)
        cliente.post("/auth/logout")

        assert cliente.get("/auth/yo").json()["autenticado"] is False

        respuesta = cliente.post(
            "/auth/login",
            json={"identificador": "andy", "password": REGISTRO["password"]},
        )

        assert respuesta.status_code == 200
        assert cliente.get("/auth/yo").json()["autenticado"] is True

    def test_la_contrasena_incorrecta_da_401(self, cliente):
        cliente.post("/auth/registro", json=REGISTRO)
        cliente.post("/auth/logout")

        respuesta = cliente.post(
            "/auth/login", json={"identificador": "andy", "password": "otra-larga"}
        )

        assert respuesta.status_code == 401

    def test_demasiados_intentos_dan_429_y_no_401(self, cliente):
        """El cliente tiene que poder distinguir «espera» de «prueba otra
        contraseña»: son consejos opuestos."""
        cliente.post("/auth/registro", json=REGISTRO)
        cliente.post("/auth/logout")

        for _ in range(5):
            cliente.post(
                "/auth/login", json={"identificador": "andy", "password": "no-es-esta"}
            )

        respuesta = cliente.post(
            "/auth/login",
            json={"identificador": "andy", "password": REGISTRO["password"]},
        )

        assert respuesta.status_code == 429

    def test_salir_invalida_la_sesion_en_el_servidor(self, cliente):
        """Borrar la cookie sin más dejaría la sesión viva treinta días para
        quien tuviera copia del token."""
        cliente.post("/auth/registro", json=REGISTRO)
        robada = cliente.cookies[COOKIE_SESION]
        cliente.post("/auth/logout")

        cliente.cookies.set(COOKIE_SESION, robada)
        assert cliente.get("/auth/yo").json()["autenticado"] is False

    def test_yo_no_da_401_sin_sesion(self, cliente):
        """Es el caso más corriente de todos: abrir la web sin haber entrado. Un
        401 aquí llenaría la consola de errores que no lo son."""
        respuesta = cliente.get("/auth/yo")

        assert respuesta.status_code == 200
        assert respuesta.json()["autenticado"] is False


class TestElCsrf:
    def test_una_peticion_con_sesion_y_sin_cabecera_se_rechaza(self, cliente):
        """El escenario del ataque: otra web provoca la petición, el navegador
        adjunta la cookie, pero no puede leerla para rellenar la cabecera."""
        _entrar(cliente)
        cliente.headers.pop(CABECERA_CSRF, None)

        respuesta = cliente.post("/auth/sesiones/cerrar-otras")

        assert respuesta.status_code == 403

    def test_con_la_cabecera_correcta_pasa(self, cliente):
        csrf = _entrar(cliente)

        respuesta = cliente.post(
            "/auth/sesiones/cerrar-otras", headers={CABECERA_CSRF: csrf}
        )

        assert respuesta.status_code == 200

    def test_una_cabecera_que_no_coincide_se_rechaza(self, cliente):
        _entrar(cliente)

        respuesta = cliente.post(
            "/auth/sesiones/cerrar-otras", headers={CABECERA_CSRF: "me-lo-invento"}
        )

        assert respuesta.status_code == 403

    def test_leer_no_necesita_csrf(self, cliente):
        """El ataque consiste en provocar un efecto, y leer no lo tiene. Exigirlo
        en los GET solo rompería la navegación normal."""
        _entrar(cliente)

        assert cliente.get("/auth/yo").status_code == 200

    def test_entrar_no_necesita_csrf(self, cliente):
        """Sin sesión todavía no hay nada que proteger, y exigirlo impediría
        iniciar sesión: no habría cookie de la que sacar el valor."""
        assert cliente.post("/auth/registro", json=REGISTRO).status_code == 200


class TestElTokenCsrfLlegaAlClienteQueViveEnOtroDominio:
    """El fallo que dejó la web entera en solo lectura.

    La cookie CSRF la pone el dominio de la API. En el despliegue real la
    interfaz vive en otro —Vercel contra Render—, y `document.cookie` **no puede
    leer una cookie de otro dominio**. El navegador la enviaba en cada petición,
    así que el backend la veía y todo parecía correcto; el cliente no, así que
    nunca ponía la cabecera y **todos** los POST se rechazaban con 403.

    Lo desconcertante era el síntoma: la sesión estaba abierta, `/auth/yo`
    respondía bien y los GET funcionaban. Solo fallaba *hacer* cosas. Desde
    fuera se leía como «Morgan cree que no he iniciado sesión».

    La cookie sigue existiendo —en local se comparte dominio y es la fuente
    directa—, pero ya no es la única vía.
    """

    def test_yo_devuelve_el_token_de_la_sesion(self, cliente):
        csrf = _entrar(cliente)

        assert cliente.get("/auth/yo").json()["csrf"] == csrf

    def test_ese_token_sirve_para_una_peticion_que_cambia_algo(self, cliente):
        """La comprobación de verdad: que con lo que devuelve `/auth/yo`, y sin
        leer ninguna cookie, se pueda actuar."""
        _entrar(cliente)
        del cliente.cookies[COOKIE_CSRF]  # lo que ve el JavaScript en Vercel

        csrf = cliente.get("/auth/yo").json()["csrf"]
        respuesta = cliente.post(
            "/auth/sesiones/cerrar-otras", headers={CABECERA_CSRF: csrf}
        )

        assert respuesta.status_code == 200

    def test_sin_sesion_no_se_emite_ningun_token(self, cliente):
        """Quien solo ha abierto la página no necesita uno, y emitirlo dejaría
        cookies a cualquiera que pase."""
        assert cliente.get("/auth/yo").json()["csrf"] == ""

    def test_una_sesion_que_perdio_la_cookie_puede_recuperarla(self, cliente):
        """Pasa de verdad: navegadores que limpian cookies de terceros se llevan
        esta y dejan la de sesión. Sin reponerla, la sesión quedaba en solo
        lectura hasta volver a entrar."""
        _entrar(cliente)
        del cliente.cookies[COOKIE_CSRF]

        respuesta = cliente.get("/auth/yo")
        nuevo = respuesta.json()["csrf"]

        assert nuevo
        assert cliente.cookies.get(COOKIE_CSRF) == nuevo
        assert cliente.post(
            "/auth/sesiones/cerrar-otras", headers={CABECERA_CSRF: nuevo}
        ).status_code == 200

    def test_el_token_no_se_regala_a_quien_no_tiene_sesion(self, cliente_nube):
        """Si bastara con pedirlo, el doble envío no protegería de nada."""
        respuesta = cliente_nube.get("/auth/yo")

        assert respuesta.json()["autenticado"] is False
        assert not respuesta.json()["csrf"]


class TestLaCookieMuertaSeBorraDeVerdad:
    """Una cookie de sesión que ya no vale y que el navegador sigue enviando deja
    a la interfaz dando vueltas: cree que hay sesión y el servidor dice que no.

    El middleware la borraba con un `delete_cookie` sin `secure` ni `samesite`,
    o sea emitiendo `SameSite=Lax` para una cookie puesta con `SameSite=None`.
    Entre dominios el navegador rechaza esa escritura, así que no borraba nada
    —precisamente en el único despliegue donde el problema existe—.
    """

    @pytest.fixture
    def cliente_https(self, monkeypatch):
        """Un despliegue de verdad: `cloud` es lo que hace `Secure` y
        `SameSite=None` las cookies, y sin eso el fallo no se reproduce."""
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")
        reset_settings()
        from src.api import dependencies

        dependencies.reset_container()
        yield TestClient(create_app())
        reset_settings()
        dependencies.reset_container()

    def test_el_borrado_lleva_los_mismos_atributos_que_la_puesta(self, cliente_https):
        cliente_https.cookies.set(COOKIE_SESION, "una-sesion-que-ya-no-existe")

        respuesta = cliente_https.get("/sessions")
        borrados = [
            c for c in respuesta.headers.get_list("set-cookie")
            if c.startswith(f"{COOKIE_SESION}=")
        ]

        assert respuesta.status_code == 401
        assert borrados, "no se emitió ningún borrado de la cookie"
        assert "samesite=none" in borrados[0].lower()
        assert "secure" in borrados[0].lower()


class TestLaProteccionDeRutas:
    def test_en_la_nube_sin_sesion_no_se_pasa(self, cliente_nube):
        respuesta = cliente_nube.get("/sessions")

        assert respuesta.status_code == 401
        assert respuesta.json()["error"]["code"] == "SIN_SESION"

    def test_en_la_nube_con_sesion_si(self, cliente_nube):
        _entrar(cliente_nube)

        assert cliente_nube.get("/sessions").status_code == 200

    def test_en_local_no_se_exige_nada(self, cliente):
        """Es tu equipo y tus claves: obligarte a inventar una contraseña para
        hablar con tu propio ordenador no protege de nada, y el Morgan de
        escritorio depende de que siga siendo así."""
        assert cliente.get("/sessions").status_code == 200

    def test_las_rutas_para_entrar_siguen_abiertas_en_la_nube(self, cliente_nube):
        """Exigir sesión para poder iniciar sesión dejaría a todo el mundo fuera
        sin remedio."""
        for ruta in ("/health", "/auth/yo"):
            assert cliente_nube.get(ruta).status_code == 200

        assert cliente_nube.post("/auth/registro", json=REGISTRO).status_code == 200

    def test_status_sigue_abierto_en_la_nube(self, cliente_nube):
        """La interfaz consulta /status para saber si el backend vive, y es lo
        que despierta a Render cuando lleva un rato dormido. Protegerlo hace que
        la PROPIA PANTALLA DE ACCESO diga «API desconectada»: no puedes entrar
        porque no has entrado.

        Esta prueba existe para que nadie lo «arregle» mas adelante metiendolo
        entre las rutas protegidas, que es lo que parece correcto de un vistazo.
        """
        respuesta = cliente_nube.get("/status")

        assert respuesta.status_code == 200
        # No publica datos de nadie: estado, modo y version.
        assert "usuario" not in respuesta.text.lower().replace("usuarios", "")

    def test_una_cookie_caducada_se_borra_al_rechazarla(self, cliente_nube):
        """Si no, el navegador la reenvía en cada petición y la interfaz se queda
        dando vueltas entre «tengo sesión» y «el servidor dice que no»."""
        cliente_nube.cookies.set(COOKIE_SESION, "ya-no-vale")

        respuesta = cliente_nube.get("/sessions")

        assert respuesta.status_code == 401
        assert 'morgan_sesion=""' in "".join(respuesta.headers.get_list("set-cookie"))


class TestCadaUnoVeLoSuyo:
    """La prueba que de verdad importa: que las cuentas aíslen datos, no solo que
    dejen entrar."""

    def _cuenta(self, cliente, nombre):
        cliente.cookies.clear()
        respuesta = cliente.post(
            "/auth/registro",
            json={
                "username": nombre,
                "email": f"{nombre}@ejemplo.co",
                "password": "contrasena-larga",
            },
        )
        return respuesta.json()["csrf"]

    def test_las_conversaciones_de_uno_no_las_ve_el_otro(self, cliente_nube):
        csrf = self._cuenta(cliente_nube, "ana")
        cliente_nube.post(
            "/sessions",
            json={"title": "Secreto de Ana"},
            headers={CABECERA_CSRF: csrf},
        )

        assert any(
            s["title"] == "Secreto de Ana"
            for s in cliente_nube.get("/sessions").json()["sessions"]
        )

        self._cuenta(cliente_nube, "bruno")
        titulos = [s["title"] for s in cliente_nube.get("/sessions").json()["sessions"]]

        assert "Secreto de Ana" not in titulos

    def test_no_se_puede_abrir_la_conversacion_de_otro_por_su_id(self, cliente_nube):
        """Aislar la lista no basta: hay que aislar el acceso directo por
        identificador, que es lo primero que probaria alguien.

        Se comprueban las dos cosas a la vez, y la segunda importa tanto como la
        primera: la respuesta para la conversacion de Ana es **identica** a la de
        un identificador inventado. Si se distinguieran, este endpoint serviria
        para averiguar que conversaciones existen aunque no dejara leerlas.
        """
        csrf = self._cuenta(cliente_nube, "ana")
        creada = cliente_nube.post(
            "/sessions", json={"title": "Privado"}, headers={CABECERA_CSRF: csrf}
        )
        assert creada.status_code == 200, creada.text
        session_id = creada.json()["id"]

        self._cuenta(cliente_nube, "bruno")

        de_ana = cliente_nube.get(f"/sessions/{session_id}")
        inventada = cliente_nube.get("/sessions/no-existe-esta")

        assert de_ana.status_code == inventada.status_code == 404

    def test_los_mensajes_de_otro_no_se_leen_ni_se_intuyen(self, cliente_nube):
        csrf = self._cuenta(cliente_nube, "ana")
        session_id = cliente_nube.post(
            "/sessions", json={"title": "Privado"}, headers={CABECERA_CSRF: csrf}
        ).json()["id"]

        self._cuenta(cliente_nube, "bruno")

        de_ana = cliente_nube.get(f"/sessions/{session_id}/messages")
        inventada = cliente_nube.get("/sessions/no-existe-esta/messages")

        assert de_ana.json()["messages"] == []
        # Misma forma de respuesta: no hay nada que distinga una de otra.
        assert de_ana.status_code == inventada.status_code
        assert de_ana.json()["total"] == inventada.json()["total"] == 0

    def test_no_se_puede_renombrar_ni_borrar_la_conversacion_de_otro(self, cliente_nube):
        """Leer no es lo unico que hay que impedir."""
        csrf = self._cuenta(cliente_nube, "ana")
        session_id = cliente_nube.post(
            "/sessions", json={"title": "Privado"}, headers={CABECERA_CSRF: csrf}
        ).json()["id"]

        csrf_bruno = self._cuenta(cliente_nube, "bruno")

        renombrar = cliente_nube.patch(
            f"/sessions/{session_id}",
            json={"title": "Mio ahora"},
            headers={CABECERA_CSRF: csrf_bruno},
        )
        borrar = cliente_nube.delete(
            f"/sessions/{session_id}", headers={CABECERA_CSRF: csrf_bruno}
        )

        assert renombrar.status_code == 404
        assert borrar.status_code == 404


class TestElPerfil:
    def test_cambiar_la_contrasena_cierra_las_demas_sesiones(self, cliente):
        """Si te la habían robado, cambiarla no sirve de nada mientras la sesión
        del otro siga viva."""
        csrf = _entrar(cliente)

        respuesta = cliente.post(
            "/auth/password",
            json={"actual": REGISTRO["password"], "nueva": "la-nueva-de-andy"},
            headers={CABECERA_CSRF: csrf},
        )

        assert respuesta.status_code == 200
        # La actual se respeta: quien acaba de cambiarla no debe quedar fuera.
        assert cliente.get("/auth/yo").json()["autenticado"] is True

    def test_la_actual_incorrecta_da_401(self, cliente):
        csrf = _entrar(cliente)

        respuesta = cliente.post(
            "/auth/password",
            json={"actual": "no-es-esta", "nueva": "la-nueva-de-andy"},
            headers={CABECERA_CSRF: csrf},
        )

        assert respuesta.status_code == 401

    def test_el_usuario_local_no_tiene_perfil_que_cambiar(self, cliente):
        """No tiene contraseña ni correo: cambiar «la suya» no significa nada."""
        respuesta = cliente.post(
            "/auth/password", json={"actual": "x", "nueva": "la-nueva-de-andy"}
        )

        assert respuesta.status_code == 401
        assert respuesta.json()["error"]["code"] == "SIN_CUENTA"

    def test_las_sesiones_listadas_no_traen_su_identificador(self, cliente):
        _entrar(cliente)

        sesiones = cliente.get("/auth/sesiones").json()["sesiones"]

        assert sesiones
        assert all("id" not in s for s in sesiones)


class TestLaRecuperacionPorHttp:
    def test_responde_igual_exista_o_no_la_cuenta(self, cliente):
        """Si cambiara, este formulario serviría para averiguar quién está
        registrado."""
        cliente.post("/auth/registro", json=REGISTRO)

        existe = cliente.post("/auth/recuperar", json={"email": "andy@ejemplo.co"})
        no_existe = cliente.post("/auth/recuperar", json={"email": "nadie@ejemplo.co"})

        assert existe.status_code == no_existe.status_code == 200
        assert existe.json() == no_existe.json()

    def test_el_token_no_viaja_en_la_respuesta(self, cliente):
        """Solo llega al correo. Devolverlo convertiría la recuperación en una
        forma de entrar en cualquier cuenta sabiendo su dirección."""
        cliente.post("/auth/registro", json=REGISTRO)

        cuerpo = cliente.post(
            "/auth/recuperar", json={"email": "andy@ejemplo.co"}
        ).json()

        assert "token" not in cuerpo
        assert len(str(cuerpo)) < 300

    def test_un_token_inventado_no_restablece(self, cliente):
        respuesta = cliente.post(
            "/auth/restablecer",
            json={"token": "me-lo-invento", "password": "la-nueva-de-andy"},
        )

        assert respuesta.status_code == 401


class TestElRegistroSePuedeCerrar:
    """Un Morgan en la web con el registro abierto acepta a cualquiera que
    encuentre la URL. La cuota limita el daño, pero hace falta poder cerrarlo."""

    @pytest.fixture
    def cerrado(self, monkeypatch):
        monkeypatch.setenv("MORGAN_REGISTRO_ABIERTO", "false")
        reset_settings()
        from src.api import dependencies

        dependencies.reset_container()
        yield TestClient(create_app())
        reset_settings()

    def test_no_admite_cuentas_nuevas(self, cerrado):
        respuesta = cerrado.post("/auth/registro", json=REGISTRO)

        assert respuesta.status_code == 403
        assert respuesta.json()["error"]["code"] == "REGISTRO_CERRADO"

    def test_el_mensaje_dice_que_hacer(self, cerrado):
        """«No autorizado» a secas deja a la persona sin saber si es culpa suya."""
        mensaje = cerrado.post("/auth/registro", json=REGISTRO).json()["error"]["message"]

        assert "administra" in mensaje

    def test_quien_ya_tiene_cuenta_sigue_entrando(self, cliente, monkeypatch):
        """Cerrar el registro no puede dejar fuera a quien ya estaba dentro.

        Se usa un solo cliente y se cierra el registro a mitad, que es como
        ocurre de verdad: el ajuste se lee en cada petición, no al arrancar.
        """
        assert cliente.post("/auth/registro", json=REGISTRO).status_code == 200
        cliente.post("/auth/logout")

        monkeypatch.setenv("MORGAN_REGISTRO_ABIERTO", "false")
        reset_settings()

        assert cliente.post("/auth/registro", json=REGISTRO).status_code == 403

        respuesta = cliente.post(
            "/auth/login",
            json={"identificador": "andy", "password": REGISTRO["password"]},
        )

        assert respuesta.status_code == 200

    def test_abierto_por_defecto(self, cliente):
        """Un Morgan en la web al que nadie puede registrarse no sirve de nada."""
        assert cliente.post("/auth/registro", json=REGISTRO).status_code == 200


class TestLaWebSabeSiTieneQuePedirAcceso:
    """`/auth/yo` es lo unico que mira la interfaz para decidir entre enseñar el
    formulario de acceso o Morgan entero. Si miente, la web se queda sin forma de
    entrar — y eso paso en produccion.

    El hueco que lo dejo pasar: habia una prueba de que esta ruta responde 200 en
    la nube, pero ninguna de QUE responde.
    """

    def test_en_la_nube_sin_sesion_dice_que_hacen_falta_cuentas(self, cliente_nube):
        """El caso que fallaba. `local` describe el DESPLIEGUE, no a quien
        pregunta: esta ruta es publica, asi que llega con el usuario implicito
        puesto, y deducirlo de ahi respondia «aqui no hacen falta cuentas»."""
        cuerpo = cliente_nube.get("/auth/yo").json()

        assert cuerpo["local"] is False
        assert cuerpo["autenticado"] is False
        assert cuerpo["usuario"] is None

    def test_en_la_nube_con_sesion_dice_quien_eres(self, cliente_nube):
        _entrar(cliente_nube)
        cuerpo = cliente_nube.get("/auth/yo").json()

        assert cuerpo["local"] is False
        assert cuerpo["autenticado"] is True
        assert cuerpo["usuario"]["display_name"] == "andy"

    def test_en_local_dice_que_no_hacen_falta(self, cliente):
        """Es tu equipo y tus claves. Aqui la web debe entrar directa, sin
        pedirte que te inventes una contraseña."""
        cuerpo = cliente.get("/auth/yo").json()

        assert cuerpo["local"] is True
        assert cuerpo["autenticado"] is False

    def test_nunca_publica_el_hash_de_la_contrasena(self, cliente_nube):
        _entrar(cliente_nube)

        assert "password" not in cliente_nube.get("/auth/yo").text.lower()


class TestElCorreoNoBloqueaLaRespuesta:
    """Hablar con un servidor SMTP puede pasar de quince segundos. Esperandolo, la
    petición se alargaba tanto que el navegador se cansaba antes: la persona veía
    «La solicitud superó los 15 s» **habiéndose enviado el correo**."""

    def test_el_envio_queda_diferido_y_no_corre_durante_la_peticion(
        self, tmp_path, monkeypatch
    ):
        """Se comprueba que el envío se **encola**, no cuánto tarda la respuesta.

        Medir el tiempo con `TestClient` no serviría: ejecuta las tareas de fondo
        antes de devolver la respuesta, cosa que el servidor real no hace. Daría
        siempre «lento» aunque el código sea correcto.

        Lo que sí es observable, y es lo que importa, es que al terminar la
        función la tarea está en la cola y el envío todavía no ha ocurrido.
        """
        from fastapi import BackgroundTasks

        from src.api.routes.cuentas import recuperar
        from src.identidad.cuentas import ServicioDeCuentas
        from src.memory.db import Database

        enviados = []
        import src.identidad.correo as correo

        monkeypatch.setattr(correo, "enviar_recuperacion", lambda *a, **k: enviados.append(a))

        servicio = ServicioDeCuentas(Database(tmp_path / "recuperar.db"))
        servicio.registrar("andy", "andy@ejemplo.co", BUENA)

        tareas = BackgroundTasks()
        respuesta = recuperar(
            RecuperarRequest(email="andy@ejemplo.co"), tareas, servicio
        )

        assert respuesta["success"] is True
        assert len(tareas.tasks) == 1, "el envío no quedó encolado"
        assert enviados == [], "el envío se hizo durante la petición"

        # Y al ejecutar la cola, entonces sí sale.
        import anyio

        anyio.run(tareas)
        assert len(enviados) == 1

    def test_no_se_encola_nada_si_la_cuenta_no_existe(self, tmp_path):
        """Sin cuenta no hay token, y sin token no hay correo que mandar. La
        respuesta, en cambio, es la misma: es lo que impide averiguar quién está
        registrado."""
        from fastapi import BackgroundTasks

        from src.api.routes.cuentas import recuperar
        from src.identidad.cuentas import ServicioDeCuentas
        from src.memory.db import Database

        servicio = ServicioDeCuentas(Database(tmp_path / "vacia.db"))
        tareas = BackgroundTasks()

        recuperar(RecuperarRequest(email="nadie@ejemplo.co"), tareas, servicio)

        assert tareas.tasks == []

    def test_un_fallo_al_enviar_no_rompe_la_peticion(self, cliente, monkeypatch):
        """Ni cambia la respuesta: decir «no se pudo enviar» delataría que la
        cuenta existe, que es justo lo que esta ruta evita."""
        import src.identidad.correo as correo

        def revienta(*a, **k):
            raise RuntimeError("el servidor de correo no responde")

        monkeypatch.setattr(correo, "enviar_recuperacion", revienta)
        cliente.post("/auth/registro", json=REGISTRO)

        existe = cliente.post("/auth/recuperar", json={"email": REGISTRO["email"]})
        no_existe = cliente.post("/auth/recuperar", json={"email": "nadie@ejemplo.co"})

        assert existe.status_code == no_existe.status_code == 200
        assert existe.json() == no_existe.json()

    def test_el_token_se_guarda_aunque_el_correo_falle(self, cliente, monkeypatch):
        """El envío es lo accesorio. Si el token no se hubiera guardado, un fallo
        pasajero del correo dejaría a la persona sin poder reintentar."""
        import src.identidad.correo as correo

        monkeypatch.setattr(correo, "enviar_recuperacion", lambda *a, **k: False)
        cliente.post("/auth/registro", json=REGISTRO)
        cliente.post("/auth/recuperar", json={"email": REGISTRO["email"]})

        from src.api import dependencies
        from src.identidad.cuentas import ServicioDeCuentas

        servicio = ServicioDeCuentas(dependencies.get_container().repositories.db)
        with servicio.repo.db.connect() as conn:
            # Se cuenta POR TIPO. La tabla la comparten los tokens de
            # recuperacion y los de verificacion del correo, asi que un conteo
            # global mezclaria dos cosas que no tienen nada que ver — y de hecho
            # asi empezo: al añadir la verificacion, esta prueba veia dos tokens
            # vivos y fallaba sin que nada estuviera mal.
            por_tipo = {
                fila["tipo"]: fila["n"]
                for fila in conn.execute(
                    "SELECT tipo, COUNT(*) AS n FROM password_reset_tokens "
                    "WHERE usado_en IS NULL GROUP BY tipo"
                ).fetchall()
            }

        assert por_tipo.get("reset") == 1, "el token de recuperacion no se guardo"
        # Y el del registro sigue vivo: cambiar de contraseña no tiene por que
        # invalidar la confirmacion del correo.
        assert por_tipo.get("verificacion") == 1


class TestElEstadoDelCorreoSeVeSinEntrarEnLosLogs:
    """El fallo que cubre es silencioso: sin SMTP, pedir un enlace de recuperación
    responde **exactamente igual** que con él —esa respuesta es vaga a propósito,
    para no delatar si la cuenta existe— y el único sitio donde se ve que no salió
    nada es el log del servidor."""

    def test_en_la_nube_sin_smtp_el_estado_lo_dice(self, cliente_nube, monkeypatch):
        for var in ("MORGAN_SMTP_HOST", "MORGAN_SMTP_USER", "MORGAN_SMTP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)

        correo = self._servicio(cliente_nube)

        assert correo["state"] == "unavailable"
        # El mensaje tiene que nombrar las variables: quien lo lea esta buscando
        # que poner, no una descripcion del problema.
        assert "MORGAN_SMTP_HOST" in correo["detail"]

    def test_con_smtp_configurado_esta_disponible(self, cliente_nube, monkeypatch):
        monkeypatch.setenv("MORGAN_SMTP_HOST", "smtp.ejemplo.co")
        monkeypatch.setenv("MORGAN_SMTP_USER", "morgan@ejemplo.co")
        monkeypatch.setenv("MORGAN_SMTP_PASSWORD", "lo-que-sea")

        assert self._servicio(cliente_nube)["state"] == "available"

    def test_en_local_no_se_marca_como_averia(self, cliente, monkeypatch):
        """Sin cuentas no hay contraseñas que recuperar. Marcarlo en rojo sería
        avisar de algo que no falta."""
        for var in ("MORGAN_SMTP_HOST", "MORGAN_SMTP_USER", "MORGAN_SMTP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)

        assert self._servicio(cliente)["state"] == "available"

    def test_no_publica_las_credenciales(self, cliente_nube, monkeypatch):
        """`/status` es público: no puede enseñar ni el usuario ni la contraseña
        del correo."""
        monkeypatch.setenv("MORGAN_SMTP_HOST", "smtp.ejemplo.co")
        monkeypatch.setenv("MORGAN_SMTP_USER", "morgan@ejemplo.co")
        monkeypatch.setenv("MORGAN_SMTP_PASSWORD", "secreto-de-verdad")

        cuerpo = cliente_nube.get("/status").text

        assert "secreto-de-verdad" not in cuerpo
        assert "morgan@ejemplo.co" not in cuerpo

    def test_un_envio_fallido_se_refleja_en_el_estado(self, cliente_nube, monkeypatch):
        """Lo que de verdad hacía falta para diagnosticar: una configuración
        completa pero con la contraseña equivocada tiene **el mismo aspecto** que
        una correcta hasta que se usa."""
        monkeypatch.setenv("MORGAN_SMTP_HOST", "smtp.ejemplo.co")
        monkeypatch.setenv("MORGAN_SMTP_USER", "morgan@ejemplo.co")
        monkeypatch.setenv("MORGAN_SMTP_PASSWORD", "la-que-no-es")

        correo = self._servicio(cliente_nube)
        assert correo["state"] == "available"  # todavia nadie lo ha usado

        from src.identidad.correo import _anotar

        _anotar(False, "El servidor rechazó las credenciales: 535")

        # El estado se cachea 30 s: sin forzarlo se leeria el de hace un momento.
        from src.api import dependencies

        dependencies.get_container().health.check_all(force=True)
        servicios = cliente_nube.get("/status").json()["services"]
        correo = next(s for s in servicios if s["name"] == "correo")

        assert correo["state"] == "unavailable"
        assert "rechazó las credenciales" in correo["detail"]

    def test_el_detalle_del_fallo_no_filtra_la_contrasena(self, cliente_nube, monkeypatch):
        """Los errores de smtplib traen el texto que devuelve el servidor, y
        `/status` es público."""
        monkeypatch.setenv("MORGAN_SMTP_HOST", "smtp.ejemplo.co")
        monkeypatch.setenv("MORGAN_SMTP_USER", "morgan@ejemplo.co")
        monkeypatch.setenv("MORGAN_SMTP_PASSWORD", "secreto-de-verdad")
        self._servicio(cliente_nube)

        from src.identidad.correo import _anotar

        _anotar(False, "rechazado para morgan@ejemplo.co con secreto-de-verdad")

        from src.api import dependencies

        dependencies.get_container().health.check_all(force=True)
        cuerpo = cliente_nube.get("/status").text

        assert "secreto-de-verdad" not in cuerpo
        assert "morgan@ejemplo.co" not in cuerpo

    @staticmethod
    def _servicio(cliente) -> dict:
        from src.api import dependencies
        from src.config import reset_settings
        from src.identidad.correo import olvidar_ultimo_intento

        # El estado se cachea unos segundos; hay que rehacerlo tras tocar el
        # entorno o se leeria el de antes.
        reset_settings()
        dependencies.reset_container()
        # Y lo anotado por otra prueba tambien: es estado de modulo, asi que un
        # fallo de envio provocado antes se lo dejaria puesto a esta.
        olvidar_ultimo_intento()

        servicios = cliente.get("/status").json()["services"]
        return next(s for s in servicios if s["name"] == "correo")
