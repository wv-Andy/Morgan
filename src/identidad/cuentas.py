"""
Cuentas y sesiones de Morgan (identidad, V2.0 adelantada).

Autenticación **propia**: nombre de usuario o correo, y contraseña. No hay
proveedor externo de identidad, y el identificador de la cuenta es de Morgan.

Eso no cierra ninguna puerta: conectar GitHub o Google más adelante será una
**integración**, con su propia tabla y sus propios permisos, colgando de esta
cuenta. Iniciar sesión con GitHub y dejar que Morgan lea tus repositorios son dos
cosas distintas, y mezclarlas es lo que obliga después a rehacerlo todo.

Este módulo es la **política**: cómo se hashea, cuándo caduca un token, cuántos
intentos se toleran. Dónde se guarda todo eso es cosa de `repositorio.py`, que
tiene una implementación para SQLite y otra para Supabase.

Decisiones que conviene tener presentes:

- **En la base se guarda el hash del identificador de sesión**, no el valor. Quien
  lea la base no debe poder suplantar a nadie, igual que con las contraseñas.
- **Los errores de acceso no distinguen** entre «esa cuenta no existe» y «la
  contraseña es incorrecta»: decirlo permite averiguar quién está registrado.
- **Los intentos se cuentan por identificador y por origen.** Solo por
  identificador, cualquiera podría bloquear la cuenta de otro fallando adrede.
"""

import hashlib
import logging
import secrets
import time

from src.identidad.modelos import USUARIO_LOCAL, MorganUser
from src.identidad.password import (
    IdentificadorInvalido,
    hashear,
    necesita_rehash,
    normalizar_email,
    normalizar_username,
    validar,
    verificar,
)
from src.identidad.repositorio import CuentasSQLite, RepositorioDeCuentas
from src.identidad.roles import Rol

logger = logging.getLogger(__name__)

# Cuánto dura una sesión. Treinta días es lo habitual en una aplicación de uso
# personal: pedir la contraseña cada semana no aporta seguridad real y empuja a
# la gente a elegir contraseñas más simples.
DURACION_SESION_DIAS = 30

# Los enlaces de recuperación caducan pronto: llegan por correo, y un correo
# antiguo reenviado o filtrado no debe seguir sirviendo para entrar.
DURACION_RESET_MINUTOS = 30

# La verificacion dura mas que una recuperacion —un dia frente a media hora—
# porque el riesgo es distinto: quien intercepte un enlace de recuperacion se
# lleva la cuenta; quien intercepte uno de verificacion solo consigue marcar
# como verificada una direccion que ya controla.
DURACION_VERIFICACION_HORAS = 24

#: Cada cuánto se anota como mucho el «último uso» de una sesión. Un minuto de
#: imprecisión en un dato informativo, a cambio de un viaje de red menos en cada
#: petición autenticada. Ver `ServicioDeCuentas._toca_ahora`.
MINUTOS_ENTRE_TOQUES = 5

#: Tope del registro en memoria, para que no crezca sin límite.
MAXIMOS_TOQUES_RECORDADOS = 5000

#: Cada cuánto se barren las sesiones y los tokens caducados, como mucho.
#:
#: El barrido son dos DELETE, y contra una base que está en otro continente eso
#: son ~600 ms. Hacerlo en cada entrada sería pagarlos por algo que solo hace
#: falta de vez en cuando: lo que se limpia no molesta a nadie mientras espera.
HORAS_ENTRE_BARRIDOS = 6


class _Frenos:
    """El estado de los dos frenos, **fuera de la instancia del servicio**.

    Aquí hay un defecto arreglado que conviene entender, porque se repitió dos
    veces en este archivo y la segunda la cometí yo.

    `ServicioDeCuentas` **se construye en cada petición**: `get_servicio` es una
    dependencia de FastAPI y `repositorio_de_cuentas(...)` devuelve un objeto
    nuevo cada vez. Comprobado:

        get_servicio(c) is get_servicio(c)   ->   False

    Los dos frenos vivían en `self`, así que cada petición empezaba con el
    contador a cero y **ninguno frenaba nunca**:

    - El del «último uso» se añadió para ahorrar un viaje de red por petición
      autenticada, está documentado como tal, y no ha ahorrado ninguno. Contra
      una base en otro continente son ~300 ms tirados en cada petición.
    - El del barrido lo añadí yo en esta misma tanda, y habría barrido en cada
      entrada en lugar de una vez cada seis horas.

    Lo cazó una prueba que comprobaba si el freno frena, no si el método
    funciona. Es la misma distinción que hizo falta para descubrir que nadie
    llamaba al barrido.

    Vive en memoria del proceso, con lo que eso implica: un reinicio lo olvida y
    varias instancias no se coordinan. Para lo que hace —evitar escrituras
    repetidas— eso basta y es lo que ya asumía el diseño original.
    """

    def __init__(self) -> None:
        #: Cuándo se anotó por última vez el uso de cada sesión.
        self.ultimos_toques: dict[str, float] = {}
        #: Cuándo se barrió por última vez lo caducado.
        self.ultimo_barrido: float = 0.0

    def reiniciar(self) -> None:
        """Para las pruebas: deja los frenos sin echar."""
        self.ultimos_toques.clear()
        self.ultimo_barrido = 0.0


#: Compartido por todas las instancias del servicio, que es justo el punto.
FRENOS = _Frenos()

# Los dos tipos de token de un solo uso. Comparten tabla porque son la misma
# cosa —un secreto atado a un usuario, con caducidad— pero NO son
# intercambiables: uno abre la cuenta y el otro solo confirma una direccion.
TIPO_RESET = "reset"
TIPO_VERIFICACION = "verificacion"

# Freno a la fuerza bruta.
MAX_INTENTOS = 5
VENTANA_INTENTOS_MINUTOS = 15

#: Fallos contra UNA cuenta, vengan del origen que vengan (V2.0.22).
#:
#: El freno de arriba es por cuenta Y origen, y el origen sale de
#: `X-Forwarded-For`, que puede escribir quien llama. Medido en la auditoría de la
#: 2.3: cambiando esa cabecera en cada intento, 20 contraseñas equivocadas
#: seguidas no frenaron nada y la correcta entró después. Este tope no depende de
#: ninguna cabecera.
#:
#: El precio, aceptado a sabiendas: quien ataque una cuenta puede dejarla sin
#: poder entrar durante la ventana, también a su dueño. Es un bloqueo temporal,
#: no un robo, y restablecer la contraseña lo levanta.
MAX_INTENTOS_POR_CUENTA = 20

#: El registro está abierto a cualquiera con el enlace (decidido por mí). Sin
#: freno, un script creó 30 cuentas en 4 segundos desde el mismo origen, cada una
#: con su cupo diario de mensajes: suficiente para agotar en minutos la cuota
#: gratuita de todos y empujar el resto al proveedor de pago.
#:
#: Dos topes en la misma ventana de 15 minutos. El de origen deja crear cuenta a
#: una familia o una clase detrás de la misma IP; el total no depende de ninguna
#: cabecera y es el que de verdad acota a un script.
MAX_REGISTROS_POR_ORIGEN = 3
MAX_REGISTROS_EN_TOTAL = 20

#: Con qué identificador se apuntan las altas en la tabla de intentos.
#:
#: Comparte tabla con los fallos de inicio de sesión, y eso obliga a dos cosas.
#: Que no pueda ser una cuenta: `#` no es válido ni en un nombre de usuario ni en
#: un correo. Y que el inicio de sesión la rechace **sin apuntar nada**: si no,
#: veinte fallos tecleando `#registro` cerrarían el registro a todo el mundo. La
#: primera versión usaba `__registro__`, que sí es un nombre de usuario válido.
CLAVE_REGISTRO = "#registro"


class ErrorDeAutenticacion(RuntimeError):
    """Credenciales incorrectas, cuenta inexistente o cuenta deshabilitada.

    Una sola excepción para los tres casos, con el mismo mensaje: distinguirlos
    convertiría el formulario en una herramienta para averiguar quién tiene
    cuenta.
    """

    def __init__(self, mensaje: str = "El usuario o la contraseña no son correctos."):
        super().__init__(mensaje)


class DemasiadosIntentos(RuntimeError):
    """Se ha superado el límite de intentos fallidos."""

    def __init__(self, minutos: int = VENTANA_INTENTOS_MINUTOS):
        self.minutos = minutos
        super().__init__(
            f"Demasiados intentos fallidos. Prueba de nuevo en {minutos} minutos."
        )


class DemasiadosRegistros(RuntimeError):
    """Se han creado demasiadas cuentas en poco tiempo (V2.0.22)."""

    def __init__(self, minutos: int = VENTANA_INTENTOS_MINUTOS):
        self.minutos = minutos
        super().__init__(
            "Se han creado muchas cuentas en muy poco tiempo. "
            f"Prueba de nuevo en {minutos} minutos."
        )


class CuentaProtegida(RuntimeError):
    """La operación es válida, pero esta cuenta concreta no la admite.

    Hoy solo la del propietario, que no puede eliminarse: dejaría la
    instalación sin nadie que administre roles ni cuentas, y el propietario se
    establece con una variable de entorno al arrancar, así que no habría forma
    de nombrar otro desde dentro.
    """


class CuentaDuplicada(ValueError):
    """Ya existe una cuenta con ese nombre de usuario o correo."""


def _hash_token(token: str) -> str:
    """Hash de un identificador de sesión o de recuperación.

    SHA-256 a secas, no scrypt: estos tokens son aleatorios de 256 bits, no
    contraseñas elegidas por personas, así que no hay nada que adivinar por
    fuerza bruta y el coste añadido no compraría nada.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ServicioDeCuentas:
    """Registro, inicio de sesión, sesiones y recuperación."""

    def __init__(self, almacen):
        """Acepta un repositorio de cuentas o, por comodidad, una `Database`."""
        # El estado de los frenos NO va aquí: esta clase se construye en cada
        # petición, así que cualquier contador en `self` empieza a cero y no
        # frena nada. Ver `_Frenos`.
        self.repo: RepositorioDeCuentas = (
            almacen if isinstance(almacen, RepositorioDeCuentas) else CuentasSQLite(almacen)
        )

    # --- Consulta ------------------------------------------------------------

    @staticmethod
    def _a_usuario(fila: dict) -> MorganUser:
        return MorganUser(
            id=fila["id"],
            email=fila.get("email"),
            display_name=fila.get("display_name"),
            avatar_url=fila.get("avatar_url"),
            creado_en=fila.get("creado_en") or 0.0,
            ultima_actividad=fila.get("ultima_actividad"),
            rol=Rol.desde(fila.get("role")),
            email_verificado=bool(fila.get("email_verificado")),
        )

    def obtener(self, user_id: str) -> MorganUser | None:
        fila = self.repo.obtener(user_id)
        return self._a_usuario(fila) if fila else None

    # --- Registro ------------------------------------------------------------

    def registrar(
        self,
        username: str,
        email: str,
        password: str,
        display_name: str | None = None,
        origen: str | None = None,
    ) -> MorganUser:
        """Crea una cuenta.

        `origen` activa el freno de altas. Lo pasa la ruta pública de registro;
        sin él —la creación del propietario al arrancar, las pruebas de
        servicio— no se frena, porque no hay nadie de fuera al otro lado.
        """
        username = normalizar_username(username)
        email = normalizar_email(email)
        validar(password)

        ahora = time.time()
        if origen is not None:
            origenes = self.repo.origenes_de_intentos(
                CLAVE_REGISTRO, ahora - VENTANA_INTENTOS_MINUTOS * 60
            )
            if (
                len(origenes) >= MAX_REGISTROS_EN_TOTAL
                or origenes.count(origen) >= MAX_REGISTROS_POR_ORIGEN
            ):
                raise DemasiadosRegistros()

        if username == USUARIO_LOCAL:
            raise IdentificadorInvalido("Ese nombre de usuario está reservado.")

        existente = self.repo.duplicado(username, email)
        if existente is not None:
            # Aqui SI se distingue: en el registro, la persona necesita saber
            # cual de los dos campos cambiar. La fuga de informacion es la misma
            # que produce cualquier formulario de alta, y no hay forma de dar un
            # alta usable sin ella.
            cual = "nombre de usuario" if existente.get("username") == username else "correo"
            raise CuentaDuplicada(f"Ya hay una cuenta con ese {cual}.")

        user_id = f"usr-{secrets.token_urlsafe(12)}"

        self.repo.crear({
            "id": user_id,
            "username": username,
            "email": email,
            "password_hash": hashear(password),
            "display_name": (display_name or username).strip()[:80],
            "status": "activo",
            "creado_en": ahora,
            "actualizado_en": ahora,
        })

        if origen is not None:
            # Se apunta solo si la cuenta se creó: un formulario mal rellenado
            # no gasta el cupo de altas de nadie.
            self.repo.apuntar_intento(CLAVE_REGISTRO, origen, ahora)

        logger.info("Cuenta creada: %s", user_id)
        return MorganUser(
            id=user_id,
            email=email,
            display_name=(display_name or username).strip()[:80],
            creado_en=ahora,
        )

    # --- Inicio de sesión ----------------------------------------------------

    def iniciar_sesion(
        self,
        identificador: str,
        password: str,
        origen: str = "desconocido",
        user_agent: str | None = None,
    ) -> tuple[MorganUser, str]:
        """Comprueba las credenciales y abre una sesión.

        Devuelve el usuario y el **identificador de sesión en claro**, que es lo
        único que sale de aquí sin hashear: va a la cookie y no se guarda.
        """
        clave = (identificador or "").strip().lower()
        ahora = time.time()

        if clave == CLAVE_REGISTRO:
            # Nunca es una cuenta, y apuntar el fallo gastaría el cupo de altas.
            raise ErrorDeAutenticacion()

        fila = self.repo.buscar(clave)

        # Los fallos se apuntan a nombre de LA CUENTA, no de lo tecleado: con el
        # nombre de usuario y el correo como dos claves, alternarlos daba el
        # doble de intentos. Sin cuenta, a nombre de lo tecleado.
        cuenta = fila["id"] if fila is not None else clave

        # Una sola lectura para los dos frenos: el de este origen y el de la
        # cuenta entera. Ver MAX_INTENTOS_POR_CUENTA.
        origenes = self.repo.origenes_de_intentos(
            cuenta, ahora - VENTANA_INTENTOS_MINUTOS * 60
        )
        if (
            origenes.count(origen) >= MAX_INTENTOS
            or len(origenes) >= MAX_INTENTOS_POR_CUENTA
        ):
            raise DemasiadosIntentos()

        correcta = (
            fila is not None
            and fila.get("status") == "activo"
            and fila.get("password_hash")
            and verificar(password, fila["password_hash"])
        )

        if not correcta:
            # El apunte va **fuera** de cualquier bloque que vaya a lanzar. Con
            # SQLite, `Database.connect` solo confirma si el bloque termina bien:
            # apuntar el fallo y lanzar dentro del mismo `with` revertia el
            # apunte, el contador nunca pasaba de cero y el freno a la fuerza
            # bruta no frenaba nada. Leyendolo parecia correcto; se descubrio
            # ejercitandolo.
            self.repo.apuntar_intento(cuenta, origen, ahora)
            raise ErrorDeAutenticacion()

        # Los intentos previos se olvidan al acertar: si no, quien falla cuatro
        # veces y acierta quedaria a un solo fallo del bloqueo para siempre.
        self.repo.olvidar_intentos(cuenta, origen)

        if necesita_rehash(fila["password_hash"]):
            # Subir el coste sin pedirle nada a la persona, aprovechando que aqui
            # tenemos la contrasena en claro y ya sabemos que es la correcta.
            self.repo.actualizar(fila["id"], {"password_hash": hashear(password)})

        token = self._crear_sesion(fila["id"], user_agent)
        self.repo.actualizar(fila["id"], {"ultima_actividad": time.time()})

        return self._a_usuario(fila), token

    def abrir_sesion_tras_el_alta(self, usuario: MorganUser, user_agent: str | None = None) -> str:
        """Abre la sesión de una cuenta que se acaba de crear (V2.0.37).

        La ruta de registro hacía antes un `iniciar_sesion` completo: buscar la
        cuenta, leer y barrer los intentos, **volver a calcular el hash** de la
        contraseña que acababa de guardar, olvidar los intentos y apuntar la
        actividad. Medido en un despliegue como el de producción (2.3-C): un alta
        hacía 14 consultas a Supabase y dos hashes en 0,15 de CPU, y con 25 a la
        vez el servidor se reinició. Aquí no hay nada que comprobar —la contraseña
        se validó y se guardó hace un instante, en `registrar`—, así que se abre
        la sesión y ya: un hash y cinco consultas menos.

        Solo se llama con el `MorganUser` que devuelve `registrar`, nunca con uno
        que venga de fuera: si no, sería abrir sesiones sin contraseña.
        """
        return self._crear_sesion(usuario.id, user_agent)

    def _crear_sesion(self, user_id: str, user_agent: str | None) -> str:
        token = secrets.token_urlsafe(32)
        ahora = time.time()

        self.repo.crear_sesion({
            "id": _hash_token(token),
            "user_id": user_id,
            "creado_en": ahora,
            "expira_en": ahora + DURACION_SESION_DIAS * 86400,
            "ultimo_uso": ahora,
            "user_agent": (user_agent or "")[:200] or None,
        })

        # Se barre aquí porque es el único sitio por el que pasan las dos cosas
        # que hacen crecer estas tablas —registrarse y entrar— y porque es el
        # momento en el que acaba de añadirse una fila.
        #
        # Va DESPUÉS de crear la sesión, y con freno: entrar no puede depender
        # de que la limpieza salga bien. `barrer_si_toca` no lanza nunca.
        self.barrer_si_toca()

        return token

    # --- Validación de sesión ------------------------------------------------

    def usuario_de_sesion(self, token: str) -> MorganUser | None:
        """Devuelve el usuario de una sesión válida, o None.

        None cubre todos los casos —no existe, caducó, fue revocada, la cuenta
        está deshabilitada— porque a quien pregunta le da igual cuál: en todos
        ellos no hay sesión.
        """
        if not token:
            return None

        ahora = time.time()
        sesion_id = _hash_token(token)
        fila = self.repo.usuario_de_sesion(sesion_id, ahora)

        if fila is None:
            return None

        if self._toca_ahora(sesion_id, ahora):
            try:
                self.repo.tocar_sesion(sesion_id, ahora)
            except Exception:
                # Anotar «último uso» es informativo: sirve para que puedas
                # revisar tus sesiones abiertas. No puede tumbar una petición.
                #
                # Esto corre en cada petición autenticada que toque, así que un
                # hipo del almacén al escribir esta marca respondía 500 a quien
                # estaba correctamente identificado —y a todo el mundo a la vez—.
                logger.warning("No se pudo anotar el uso de la sesión", exc_info=True)

        return self._a_usuario(fila)

    def _toca_ahora(self, sesion_id: str, ahora: float) -> bool:
        """Si toca escribir la marca de «último uso», o si se puede esperar.

        **Por qué existe.** Escribirla costaba un viaje completo a Supabase en
        CADA petición autenticada. Medido en producción: ~220 ms, que en
        `/auth/yo` eran más de la mitad de los 400 ms que tardaba. Y lo que se
        gana con esa escritura es que la lista de sesiones abiertas diga «hace un
        momento» en lugar de «hace un minuto».

        Así que se escribe como mucho una vez cada `MINUTOS_ENTRE_TOQUES`. Lo que
        se pierde es precisión al segundo en un dato informativo; lo que se gana
        es un viaje de red menos en todas las peticiones de todo el mundo.

        El registro es en memoria y por proceso, no en la base: consultar la
        base para decidir si escribir en la base no ahorraría nada. La
        consecuencia es que tras un reinicio la primera petición de cada sesión
        escribe, y eso es exactamente lo que debe pasar.
        """
        ultimo = FRENOS.ultimos_toques.get(sesion_id)
        if ultimo is not None and ahora - ultimo < MINUTOS_ENTRE_TOQUES * 60:
            return False

        # Acotado: sin esto, un despliegue con muchas sesiones a lo largo de días
        # acumularia una entrada por cada una y nadie las quitaria nunca.
        if len(FRENOS.ultimos_toques) >= MAXIMOS_TOQUES_RECORDADOS:
            FRENOS.ultimos_toques.clear()

        FRENOS.ultimos_toques[sesion_id] = ahora
        return True

    def eliminar_cuenta(self, user_id: str, password: str) -> None:
        """Borra una cuenta, previa confirmación con su contraseña.

        **Se exige la contraseña aunque haya sesión abierta.** No es
        desconfianza del sistema de sesiones: es que una sesión abierta en un
        equipo prestado o sin bloquear basta para que otra persona borre la
        cuenta de un clic, y esto no tiene deshacer.

        **El propietario no puede borrarse.** Dejaría la instalación sin nadie
        que administre roles ni cuentas, y no hay forma de nombrar otro
        propietario desde dentro: se establece con una variable de entorno al
        arrancar. Se rechaza en lugar de dejar el sistema en un estado del que
        no se sale sin tocar el servidor.

        Los datos —conversaciones, recuerdos, archivos, tareas— **no se borran
        aquí**: viven en otra capa. De eso se encarga la ruta, que sí tiene
        acceso a los repositorios.
        """
        self.comprobar_para_eliminar(user_id, password)

        # Primero las sesiones: si el borrado de la cuenta fallara a medias, es
        # preferible una cuenta viva sin sesiones que sesiones vivas sin cuenta.
        self.repo.revocar_sesiones(user_id, None)
        self.repo.eliminar(user_id)
        logger.info("Cuenta eliminada a petición de su titular")

    def comprobar_para_eliminar(self, user_id: str, password: str) -> None:
        """Comprueba que esta cuenta se puede borrar y que quien lo pide es su titular.

        Va aparte del borrado porque quien llama tiene que eliminar los DATOS
        antes de eliminar la cuenta, y hacerlo con una contraseña equivocada
        dejaría a alguien sin sus conversaciones y con la cuenta intacta. Se
        comprueba primero, se borra después.

        `eliminar_cuenta` la vuelve a llamar: repetir una comprobación barata
        vale más que confiar en que todo el que llame se acuerde de hacerla.
        """
        fila = self.repo.obtener(user_id)
        if fila is None:
            raise ErrorDeAutenticacion("Esa cuenta no existe.")

        if self._a_usuario(fila).rol_efectivo is Rol.OWNER:
            raise CuentaProtegida(
                "La cuenta del propietario no se puede eliminar: dejaría este "
                "Morgan sin nadie que lo administre."
            )

        if not verificar(password, fila["password_hash"]):
            raise ErrorDeAutenticacion("La contraseña no es correcta.")

    def cerrar_sesion(self, token: str) -> bool:
        """Invalida una sesión. Reutilizarla después no funciona."""
        return self.repo.revocar_sesion(_hash_token(token)) if token else False

    def cerrar_todas(self, user_id: str, excepto: str | None = None) -> int:
        """Cierra las sesiones de un usuario, opcionalmente salvo una.

        Se usa al cambiar la contraseña: si alguien la había robado, cambiarla no
        sirve de nada mientras su sesión siga viva.
        """
        return self.repo.revocar_sesiones(
            user_id, _hash_token(excepto) if excepto else None
        )

    def sesiones_activas(self, user_id: str) -> list[dict]:
        """Las sesiones vivas del usuario, para poder revisarlas y cerrarlas.

        El identificador de sesión **no** se publica, ni siquiera hasheado: no le
        aporta nada a la persona y es material para suplantar.
        """
        return self.repo.listar_sesiones(user_id, time.time())

    # --- Contraseña ----------------------------------------------------------

    def cambiar_password(self, user_id: str, actual: str, nueva: str) -> None:
        validar(nueva)

        fila = self.repo.obtener(user_id)
        if fila is None or not verificar(actual, fila.get("password_hash") or ""):
            raise ErrorDeAutenticacion("La contraseña actual no es correcta.")

        self.repo.actualizar(
            user_id, {"password_hash": hashear(nueva), "actualizado_en": time.time()}
        )
        # Los tokens de API caen todos (plan de la API, fase 1). Quien cambia la
        # contraseña porque sospecha algo espera que nada de lo anterior siga
        # valiendo, y un token no pide la contraseña para nada.
        self.repo.revocar_tokens(user_id)
        # Y los agentes locales (3.0-C), por lo mismo: uno emparejado por un intruso
        # podría servir a esta cuenta archivos inventados.
        self.repo.revocar_agentes(user_id)
        self.repo.invalidar_codigos_agente(user_id, time.time())

    # --- Recuperación --------------------------------------------------------

    def solicitar_verificacion(self, user_id: str) -> tuple[str, str] | None:
        """Genera un token para verificar el correo. Devuelve (token, correo).

        `None` si la cuenta no existe o **ya está verificada**: reenviar un
        enlace a quien ya lo usó solo sirve para confundir.

        **Verificar no es una puerta.** La cuenta funciona sin verificar desde
        el primer momento, y eso es una decisión, no una carencia: obligar a
        abrir el correo antes de dejar probar nada es la forma más rápida de
        perder a alguien. Lo que se gana verificando es poder recuperar la
        contraseña — y eso es exactamente lo que se pierde si la dirección
        estaba mal escrita.

        Dura más que un enlace de recuperación (un día frente a media hora)
        porque el riesgo es otro: quien intercepte este solo consigue marcar
        como verificada una dirección que ya controla.
        """
        fila = self.repo.obtener(user_id)
        if fila is None or not fila.get("email"):
            return None
        if fila.get("email_verificado"):
            return None

        token = secrets.token_urlsafe(32)
        ahora = time.time()

        # Los anteriores se invalidan: pedir uno nuevo deja sin valor al viejo,
        # que puede estar en un correo de hace una semana.
        self.repo.invalidar_resets(user_id, ahora, tipo=TIPO_VERIFICACION)
        self.repo.crear_reset({
            "token_hash": _hash_token(token),
            "user_id": user_id,
            "creado_en": ahora,
            "expira_en": ahora + DURACION_VERIFICACION_HORAS * 3600,
            "tipo": TIPO_VERIFICACION,
        })

        return token, fila["email"]

    def verificar_email(self, token: str) -> bool:
        """Marca el correo como verificado. Devuelve si el token valía.

        El token se consume aunque la cuenta ya estuviera verificada: un enlace
        que sigue sirviendo después de usarse deja de ser de un solo uso.
        """
        if not token:
            return False

        ahora = time.time()
        token_hash = _hash_token(token)
        fila = self.repo.reset_valido(token_hash, ahora, tipo=TIPO_VERIFICACION)
        if fila is None:
            return False

        self.repo.marcar_reset_usado(token_hash, ahora)
        self.repo.actualizar(fila["user_id"], {"email_verificado": 1})
        logger.info("Correo verificado")
        return True

    def solicitar_recuperacion(self, email: str) -> str | None:
        """Genera un token de recuperación, o None si ese correo no tiene cuenta.

        **Quien llama debe responder lo mismo en ambos casos**: si la respuesta
        cambiara, el formulario serviría para averiguar quién está registrado.

        El token en claro se devuelve una sola vez, para enviarlo por correo. En
        la base solo queda su hash.
        """
        try:
            limpio = normalizar_email(email)
        except IdentificadorInvalido:
            return None

        fila = self.repo.buscar(limpio)
        if fila is None or fila.get("status") != "activo" or fila.get("email") != limpio:
            return None

        token = secrets.token_urlsafe(32)
        ahora = time.time()

        # Los anteriores de esa cuenta se invalidan: pedir un enlace nuevo debe
        # dejar sin valor al viejo, que puede estar en un correo antiguo.
        self.repo.invalidar_resets(fila["id"], ahora)
        self.repo.crear_reset({
            "token_hash": _hash_token(token),
            "user_id": fila["id"],
            "creado_en": ahora,
            "expira_en": ahora + DURACION_RESET_MINUTOS * 60,
            "tipo": TIPO_RESET,
        })

        return token

    def restablecer_password(self, token: str, nueva: str) -> str:
        """Cambia la contraseña con un token de recuperación. Devuelve de quién era."""
        validar(nueva)

        ahora = time.time()
        token_hash = _hash_token(token or "")
        fila = self.repo.reset_valido(token_hash, ahora)

        if fila is None:
            raise ErrorDeAutenticacion(
                "Ese enlace no es válido o ha caducado. Pide uno nuevo."
            )

        # Un solo uso: se marca antes de nada.
        self.repo.marcar_reset_usado(token_hash, ahora)
        self.repo.actualizar(
            fila["user_id"], {"password_hash": hashear(nueva), "actualizado_en": ahora}
        )
        # Quien recupera la contrasena puede estar haciendolo porque alguien se
        # la robo: sus sesiones tienen que caer.
        self.repo.revocar_sesiones(fila["user_id"], None)
        # Y sus tokens de API, por lo mismo.
        self.repo.revocar_tokens(fila["user_id"])
        self.repo.revocar_agentes(fila["user_id"])
        self.repo.invalidar_codigos_agente(fila["user_id"], ahora)
        # Y el bloqueo por fallos cae también (V2.0.22). Si alguien estaba
        # atacando la cuenta, su dueño recupera el acceso por aquí sin esperar.
        self.repo.olvidar_todos_los_intentos(fila["user_id"])
        return fila["user_id"]

    # --- Mantenimiento -------------------------------------------------------

    def limpiar_caducado(self) -> int:
        """Borra sesiones y tokens que ya no valen. Devuelve cuántas filas."""
        return self.repo.limpiar(time.time())

    def barrer_si_toca(self) -> int:
        """Barre como mucho una vez cada `HORAS_ENTRE_BARRIDOS`.

        **Por qué existe esto y no se llamaba al de arriba.** `limpiar_caducado`
        estaba escrito, implementado en los dos almacenes y probado... y **nadie
        lo llamaba**. Lo encontró la auditoría de la V2.0.2 al ver 26 sesiones y
        13 tokens de recuperación acumulados para tres usuarios: de esas 39
        filas, 33 se podían borrar. No faltaba el barrido, faltaba enchufarlo.

        Se llama al entrar y al registrarse, que es cuando la tabla crece, y con
        freno: dos DELETE contra una base en otro continente son ~600 ms, y
        pagarlos en cada entrada sería cobrarle a alguien por una limpieza que
        no le corre prisa a nadie.

        **Nunca lanza.** Que el mantenimiento falle no puede impedir entrar: el
        peor resultado de un barrido roto es una tabla grande, y el peor de un
        barrido que rompe la entrada es que nadie pueda usar Morgan.
        """
        ahora = time.time()
        if ahora - FRENOS.ultimo_barrido < HORAS_ENTRE_BARRIDOS * 3600:
            return 0

        # Se apunta ANTES de barrer, no después: si el barrido falla, no se
        # reintenta en la siguiente entrada y en la siguiente. Un fallo que se
        # repite en cada petición es peor que la basura que iba a limpiar.
        FRENOS.ultimo_barrido = ahora
        try:
            borradas = self.repo.limpiar(ahora)
        except Exception:
            logger.warning("No se pudo barrer lo caducado", exc_info=True)
            return 0

        if borradas:
            logger.info("Barrido: %d sesiones y tokens caducados", borradas)
        return borradas
