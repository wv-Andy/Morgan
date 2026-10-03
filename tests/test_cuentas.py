"""
Cuentas, contraseñas y sesiones (identidad, V2.0 adelantada).

Autenticación propia de Morgan. Estas pruebas cubren lo que el plan pide revisar:
hasheo, seguridad de la sesión, freno a la fuerza bruta, recuperación, caducidad
de tokens, invalidación de sesiones y **enumeración de usuarios** — que es la que
más fácil se cuela, porque el sistema «funciona» igual de bien con ella dentro.
"""

import time

import pytest

from src.identidad import password as pw
from src.identidad.cuentas import (
    MAX_INTENTOS,
    CuentaDuplicada,
    DemasiadosIntentos,
    ErrorDeAutenticacion,
    ServicioDeCuentas,
    _hash_token,
)
from src.identidad.password import IdentificadorInvalido, PasswordInvalida
from src.memory.db import Database

BUENA = "contrasena-larga"


@pytest.fixture
def servicio(tmp_path):
    return ServicioDeCuentas(Database(tmp_path / "cuentas.db"))


@pytest.fixture
def cuenta(servicio):
    return servicio.registrar("andy", "andy@ejemplo.co", BUENA)


class TestElHasheoDeContrasenas:
    def test_no_se_guarda_en_claro(self, servicio, cuenta):
        with servicio.repo.db.connect() as conn:
            guardado = conn.execute(
                "SELECT password_hash FROM morgan_users WHERE id = ?", (cuenta.id,)
            ).fetchone()["password_hash"]

        assert BUENA not in guardado
        assert guardado.startswith("scrypt$")

    def test_dos_iguales_dan_hashes_distintos(self):
        """Con sal. Sin ella, hashes repetidos delatan quién comparte contraseña."""
        assert pw.hashear(BUENA) != pw.hashear(BUENA)

    def test_verifica_la_correcta_y_rechaza_la_otra(self):
        h = pw.hashear(BUENA)

        assert pw.verificar(BUENA, h)
        assert not pw.verificar(BUENA + "x", h)

    def test_un_hash_corrupto_no_deja_entrar_ni_revienta(self):
        """Un registro roto no debe tumbar el login de todo el mundo, y tampoco
        debe dejar pasar."""
        for basura in ["", "cualquier-cosa", "scrypt$mal", "scrypt$a$b$c$d$e"]:
            assert pw.verificar(BUENA, basura) is False

    @pytest.mark.parametrize("mala", ["", "corta", " " + BUENA, BUENA + " ", "x" * 500])
    def test_rechaza_contrasenas_inaceptables(self, mala):
        with pytest.raises(PasswordInvalida):
            pw.hashear(mala)

    def test_detecta_hashes_con_parametros_flojos(self):
        viejo = pw.hashear(BUENA).replace(f"scrypt${pw.N}$", "scrypt$1024$", 1)

        assert pw.necesita_rehash(viejo)
        assert not pw.necesita_rehash(pw.hashear(BUENA))

    def test_nunca_hay_mas_de_cuatro_hashes_a_la_vez(self, monkeypatch):
        """Cada uno reserva 16 MB (V2.0.27).

        Medido en la prueba de carga: 25 altas simultáneas llevaron el servidor a
        498 MB, y Render da 512. Vale igual para inicios de sesión.
        """
        import threading

        en_curso = 0
        maximo = 0
        cerrojo = threading.Lock()
        scrypt_real = pw.hashlib.scrypt

        def scrypt_contado(*args, **kwargs):
            nonlocal en_curso, maximo
            with cerrojo:
                en_curso += 1
                maximo = max(maximo, en_curso)
            time.sleep(0.05)
            try:
                return scrypt_real(*args, **kwargs)
            finally:
                with cerrojo:
                    en_curso -= 1

        monkeypatch.setattr(pw.hashlib, "scrypt", scrypt_contado)
        guardado = pw.hashear(BUENA)
        hilos = [
            threading.Thread(target=pw.hashear, args=(BUENA,)) if n % 2
            else threading.Thread(target=pw.verificar, args=(BUENA, guardado))
            for n in range(16)
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(30)

        assert maximo == pw.MAX_HASHES_A_LA_VEZ == 4


class TestElRegistro:
    def test_crea_la_cuenta(self, cuenta):
        assert cuenta.id.startswith("usr-")
        assert cuenta.email == "andy@ejemplo.co"

    def test_normaliza_usuario_y_correo(self, servicio):
        """`Andy` y `andy` deben ser la misma cuenta: si no, dos personas podrían
        registrar nombres que se leen igual."""
        servicio.registrar("Andy", "A@Ejemplo.CO", BUENA)

        with pytest.raises(CuentaDuplicada):
            servicio.registrar("andy", "otro@ejemplo.co", BUENA)

    @pytest.mark.parametrize(
        "usuario, correo",
        [("andy", "otro@ejemplo.co"), ("otro", "andy@ejemplo.co")],
    )
    def test_no_admite_duplicados(self, servicio, cuenta, usuario, correo):
        with pytest.raises(CuentaDuplicada):
            servicio.registrar(usuario, correo, BUENA)

    def test_el_usuario_local_esta_reservado(self, servicio):
        """`local` es el dueño implícito de todo lo que existía antes de las
        cuentas. Registrarlo daría acceso a esos datos."""
        with pytest.raises(IdentificadorInvalido):
            servicio.registrar("local", "otro@ejemplo.co", BUENA)

    @pytest.mark.parametrize("usuario", ["ab", "x" * 33, "con espacio", "raro@aqui"])
    def test_rechaza_nombres_invalidos(self, servicio, usuario):
        with pytest.raises(IdentificadorInvalido):
            servicio.registrar(usuario, "x@ejemplo.co", BUENA)

    @pytest.mark.parametrize("correo", ["sinarroba", "@ejemplo.co", "a@b", "a b@c.co"])
    def test_rechaza_correos_invalidos(self, servicio, correo):
        with pytest.raises(IdentificadorInvalido):
            servicio.registrar("alguien", correo, BUENA)


class TestElInicioDeSesion:
    def test_entra_con_usuario_o_con_correo(self, servicio, cuenta):
        for identificador in ("andy", "andy@ejemplo.co", "ANDY"):
            usuario, token = servicio.iniciar_sesion(identificador, BUENA)
            assert usuario.id == cuenta.id
            assert token

    def test_la_contrasena_incorrecta_no_entra(self, servicio, cuenta):
        with pytest.raises(ErrorDeAutenticacion):
            servicio.iniciar_sesion("andy", "otra-cosa-larga")

    def test_una_cuenta_deshabilitada_no_entra(self, servicio, cuenta):
        with servicio.repo.db.connect() as conn:
            conn.execute(
                "UPDATE morgan_users SET status = 'suspendido' WHERE id = ?",
                (cuenta.id,),
            )

        with pytest.raises(ErrorDeAutenticacion):
            servicio.iniciar_sesion("andy", BUENA)

    def test_el_usuario_local_no_puede_iniciar_sesion(self, servicio):
        """No tiene contraseña, y sin esta comprobación un `password_hash` vacío
        podría abrir la cuenta que lo posee todo."""
        with pytest.raises(ErrorDeAutenticacion):
            servicio.iniciar_sesion("local", "")


class TestNoSePuedeAveriguarQuienTieneCuenta:
    """Enumeración de usuarios. Si el error distingue «no existe» de «contraseña
    incorrecta», el formulario sirve para comprobar quién está registrado."""

    def test_el_mensaje_es_el_mismo(self, servicio, cuenta):
        with pytest.raises(ErrorDeAutenticacion) as existente:
            servicio.iniciar_sesion("andy", "no-es-esta-tampoco")

        with pytest.raises(ErrorDeAutenticacion) as inexistente:
            servicio.iniciar_sesion("nadie", "no-es-esta-tampoco")

        assert str(existente.value) == str(inexistente.value)

    def test_recuperar_no_delata_al_que_no_existe(self, servicio, cuenta):
        """El servicio devuelve None, y la ruta responde lo mismo en ambos casos.
        Lo que no puede pasar es que **lance**: eso ya sería una diferencia
        observable desde fuera."""
        assert servicio.solicitar_recuperacion("nadie@ejemplo.co") is None
        assert servicio.solicitar_recuperacion("ni-siquiera-un-correo") is None
        assert servicio.solicitar_recuperacion("andy@ejemplo.co") is not None


class TestLasSesiones:
    def test_el_identificador_no_se_guarda_en_claro(self, servicio, cuenta):
        """Misma razón que con las contraseñas: quien lea la base no debe poder
        suplantar a nadie."""
        _, token = servicio.iniciar_sesion("andy", BUENA)

        with servicio.repo.db.connect() as conn:
            guardados = [f["id"] for f in conn.execute("SELECT id FROM auth_sessions")]

        assert token not in guardados
        assert _hash_token(token) in guardados

    def test_una_sesion_valida_identifica_al_usuario(self, servicio, cuenta):
        _, token = servicio.iniciar_sesion("andy", BUENA)

        assert servicio.usuario_de_sesion(token).id == cuenta.id

    @pytest.mark.parametrize("token", ["", None, "inventado", "x" * 43])
    def test_un_token_invalido_no_identifica_a_nadie(self, servicio, cuenta, token):
        assert servicio.usuario_de_sesion(token) is None

    def test_cerrar_sesion_la_invalida(self, servicio, cuenta):
        _, token = servicio.iniciar_sesion("andy", BUENA)
        servicio.cerrar_sesion(token)

        assert servicio.usuario_de_sesion(token) is None

    def test_una_sesion_caducada_no_vale(self, servicio, cuenta):
        _, token = servicio.iniciar_sesion("andy", BUENA)

        with servicio.repo.db.connect() as conn:
            conn.execute(
                "UPDATE auth_sessions SET expira_en = ? WHERE id = ?",
                (time.time() - 1, _hash_token(token)),
            )

        assert servicio.usuario_de_sesion(token) is None

    def test_suspender_la_cuenta_tumba_sus_sesiones(self, servicio, cuenta):
        """Sin esto, deshabilitar a alguien no lo echaría hasta dentro de 30 días."""
        _, token = servicio.iniciar_sesion("andy", BUENA)

        with servicio.repo.db.connect() as conn:
            conn.execute(
                "UPDATE morgan_users SET status = 'suspendido' WHERE id = ?",
                (cuenta.id,),
            )

        assert servicio.usuario_de_sesion(token) is None

    def test_las_sesiones_listadas_no_incluyen_su_identificador(self, servicio, cuenta):
        """Ni siquiera hasheado: no le sirve de nada al usuario y es material para
        suplantar."""
        servicio.iniciar_sesion("andy", BUENA, user_agent="Firefox")
        activas = servicio.sesiones_activas(cuenta.id)

        assert len(activas) == 1
        assert activas[0]["user_agent"] == "Firefox"
        assert "id" not in activas[0]

    def test_cerrar_todas_menos_la_actual(self, servicio, cuenta):
        _, viejo = servicio.iniciar_sesion("andy", BUENA)
        _, actual = servicio.iniciar_sesion("andy", BUENA)

        assert servicio.cerrar_todas(cuenta.id, excepto=actual) == 1
        assert servicio.usuario_de_sesion(viejo) is None
        assert servicio.usuario_de_sesion(actual) is not None


class TestElFrenoALaFuerzaBruta:
    """El defecto que apareció al ejercitarlo: los intentos fallidos se apuntaban
    dentro del mismo `with` que lanzaba la excepción, y `Database.connect` solo
    confirma si el bloque termina bien. Cada apunte se revertía con su propio
    fallo, así que el contador nunca pasaba de cero y el freno no frenaba nada.
    Leyendo el código parecía correcto."""

    def _fallar(self, servicio, veces, origen="1.1.1.1"):
        for _ in range(veces):
            with pytest.raises(ErrorDeAutenticacion):
                servicio.iniciar_sesion("andy", "no-es-esta", origen=origen)

    def test_los_intentos_fallidos_se_guardan(self, servicio, cuenta):
        self._fallar(servicio, 3)

        with servicio.repo.db.connect() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM login_intentos").fetchone()["n"]

        assert n == 3

    def test_bloquea_al_pasarse(self, servicio, cuenta):
        self._fallar(servicio, MAX_INTENTOS)

        with pytest.raises(DemasiadosIntentos):
            servicio.iniciar_sesion("andy", BUENA, origen="1.1.1.1")

    def test_no_se_puede_bloquear_la_cuenta_de_otro(self, servicio, cuenta):
        """Se cuenta por identificador **y** origen. Solo por identificador,
        cualquiera podría dejar fuera a quien quisiera fallando adrede."""
        self._fallar(servicio, MAX_INTENTOS, origen="el-atacante")

        usuario, _ = servicio.iniciar_sesion("andy", BUENA, origen="el-de-verdad")
        assert usuario.id == cuenta.id

    def test_acertar_limpia_el_contador(self, servicio, cuenta):
        """Si no, quien falla cuatro veces y acierta quedaría a un fallo del
        bloqueo para siempre."""
        self._fallar(servicio, MAX_INTENTOS - 1)
        servicio.iniciar_sesion("andy", BUENA, origen="1.1.1.1")
        self._fallar(servicio, MAX_INTENTOS - 1)

        servicio.iniciar_sesion("andy", BUENA, origen="1.1.1.1")

    def test_los_intentos_viejos_no_cuentan(self, servicio, cuenta):
        """El bloqueo se levanta solo. Si no, un despiste dejaría la cuenta
        inaccesible sin más remedio que tocar la base."""
        self._fallar(servicio, MAX_INTENTOS)

        with servicio.repo.db.connect() as conn:
            conn.execute("UPDATE login_intentos SET momento = momento - 3600")

        servicio.iniciar_sesion("andy", BUENA, origen="1.1.1.1")


class TestLaRecuperacionDeContrasena:
    def test_el_token_no_se_guarda_en_claro(self, servicio, cuenta):
        token = servicio.solicitar_recuperacion("andy@ejemplo.co")

        with servicio.repo.db.connect() as conn:
            guardados = [
                f["token_hash"]
                for f in conn.execute("SELECT token_hash FROM password_reset_tokens")
            ]

        assert token not in guardados
        assert _hash_token(token) in guardados

    def test_restablece_y_deja_entrar_con_la_nueva(self, servicio, cuenta):
        token = servicio.solicitar_recuperacion("andy@ejemplo.co")
        servicio.restablecer_password(token, "la-nueva-de-andy")

        usuario, _ = servicio.iniciar_sesion("andy", "la-nueva-de-andy")
        assert usuario.id == cuenta.id

        with pytest.raises(ErrorDeAutenticacion):
            servicio.iniciar_sesion("andy", BUENA)

    def test_el_token_sirve_una_sola_vez(self, servicio, cuenta):
        token = servicio.solicitar_recuperacion("andy@ejemplo.co")
        servicio.restablecer_password(token, "la-nueva-de-andy")

        with pytest.raises(ErrorDeAutenticacion):
            servicio.restablecer_password(token, "y-otra-mas-todavia")

    def test_un_token_caducado_no_vale(self, servicio, cuenta):
        """Llega por correo, y un correo viejo reenviado o filtrado no debe seguir
        abriendo la cuenta."""
        token = servicio.solicitar_recuperacion("andy@ejemplo.co")

        with servicio.repo.db.connect() as conn:
            conn.execute(
                "UPDATE password_reset_tokens SET expira_en = ?", (time.time() - 1,)
            )

        with pytest.raises(ErrorDeAutenticacion):
            servicio.restablecer_password(token, "la-nueva-de-andy")

    def test_pedir_uno_nuevo_invalida_el_anterior(self, servicio, cuenta):
        viejo = servicio.solicitar_recuperacion("andy@ejemplo.co")
        servicio.solicitar_recuperacion("andy@ejemplo.co")

        with pytest.raises(ErrorDeAutenticacion):
            servicio.restablecer_password(viejo, "la-nueva-de-andy")

    def test_un_token_inventado_no_vale(self, servicio, cuenta):
        with pytest.raises(ErrorDeAutenticacion):
            servicio.restablecer_password("me-lo-invento", "la-nueva-de-andy")

    def test_recuperar_tumba_las_sesiones_abiertas(self, servicio, cuenta):
        """Quien recupera la contraseña puede estar haciéndolo porque se la
        robaron: cambiarla no sirve de nada si la sesión del ladrón sigue viva."""
        _, token_sesion = servicio.iniciar_sesion("andy", BUENA)
        reset = servicio.solicitar_recuperacion("andy@ejemplo.co")

        servicio.restablecer_password(reset, "la-nueva-de-andy")

        assert servicio.usuario_de_sesion(token_sesion) is None

    def test_no_admite_una_contrasena_invalida(self, servicio, cuenta):
        token = servicio.solicitar_recuperacion("andy@ejemplo.co")

        with pytest.raises(PasswordInvalida):
            servicio.restablecer_password(token, "corta")


class TestElCambioDeContrasena:
    def test_exige_la_actual(self, servicio, cuenta):
        """Sin esto, quien pillara una sesión abierta se quedaría con la cuenta."""
        with pytest.raises(ErrorDeAutenticacion):
            servicio.cambiar_password(cuenta.id, "no-es-esta", "la-nueva-de-andy")

    def test_cambia(self, servicio, cuenta):
        servicio.cambiar_password(cuenta.id, BUENA, "la-nueva-de-andy")

        usuario, _ = servicio.iniciar_sesion("andy", "la-nueva-de-andy")
        assert usuario.id == cuenta.id

    def test_no_admite_una_contrasena_invalida(self, servicio, cuenta):
        with pytest.raises(PasswordInvalida):
            servicio.cambiar_password(cuenta.id, BUENA, "corta")


class TestElMantenimiento:
    def test_limpia_lo_caducado_y_respeta_lo_vivo(self, servicio, cuenta):
        _, viva = servicio.iniciar_sesion("andy", BUENA)
        _, muerta = servicio.iniciar_sesion("andy", BUENA)
        servicio.cerrar_sesion(muerta)

        assert servicio.limpiar_caducado() == 1
        assert servicio.usuario_de_sesion(viva) is not None
