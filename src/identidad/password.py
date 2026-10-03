"""
Contraseñas: cómo se guardan y cómo se comprueban.

**No hay criptografía propia aquí.** Se usa `scrypt`, que viene en `hashlib` de la
biblioteca estándar y está estandarizado en el RFC 7914. Es un algoritmo diseñado
específicamente para contraseñas: costoso en memoria además de en tiempo, que es
lo que encarece los ataques con tarjetas gráficas. Lo único que hace este módulo
es elegir parámetros sensatos y guardar el resultado en un formato que permita
cambiarlos más adelante.

Se eligió frente a `bcrypt` y `argon2-cffi` por no añadir una dependencia con
extensión en C: las tres son opciones defendibles, y esta no obliga a compilar
nada ni en Windows ni en el contenedor de Render.

El formato almacenado incluye los parámetros:

    scrypt$16384$8$1$<sal en base64>$<hash en base64>

Así, si dentro de dos años hay que subir el coste, las contraseñas antiguas
siguen verificándose con sus parámetros originales y se rehashean al entrar.
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading

# Parametros de coste. n=2^14 tarda del orden de 100 ms en un equipo normal:
# suficiente para encarecer un ataque por fuerza bruta sin que iniciar sesion se
# note lento. r y p son los valores habituales del RFC.
N = 2 ** 14
R = 8
P = 1
LONGITUD_SAL = 16
LONGITUD_HASH = 32

# Cuántos hashes a la vez, como mucho, en todo el proceso (V2.0.27).
#
# scrypt con estos parámetros reserva 128·r·n = **16 MB por cálculo**. Medido en la
# prueba de carga de la 2.3-C: 25 altas simultáneas llevaron el servidor a 498 MB,
# y el plan gratuito de Render tiene 512. Un pico así de inicios de sesión —también
# fallidos, también de alguien probando contraseñas desde muchas direcciones— lo
# habría tumbado por memoria, y los frenos por origen no lo evitan porque cuentan
# intentos, no simultaneidad.
#
# Con 4 el techo es 64 MB. No se pierde nada: el cálculo es de CPU, y el servidor
# de Render tiene una fracción de una, así que más en paralelo solo repartía la
# misma CPU entre más memoria. Los que esperan, esperan su turno en la cola.
MAX_HASHES_A_LA_VEZ = 4
_hashes_en_curso = threading.BoundedSemaphore(MAX_HASHES_A_LA_VEZ)


def _scrypt(password: str, sal: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    with _hashes_en_curso:
        return hashlib.scrypt(password.encode("utf-8"), salt=sal, n=n, r=r, p=p, dklen=dklen)

ALGORITMO = "scrypt"

# Requisitos minimos. Cortos a proposito: exigir mayusculas, numeros y simbolos
# empuja a la gente hacia contrasenas cortas y predecibles del tipo "Passw0rd!".
# La longitud es lo que de verdad importa.
LONGITUD_MINIMA = 8
LONGITUD_MAXIMA = 200


class PasswordInvalida(ValueError):
    """La contraseña no cumple los requisitos. El mensaje se enseña al usuario."""


def validar(password: str) -> None:
    """Comprueba que la contraseña sea aceptable, o explica por qué no."""
    if not password:
        raise PasswordInvalida("La contraseña no puede estar vacía.")

    if len(password) < LONGITUD_MINIMA:
        raise PasswordInvalida(
            f"La contraseña debe tener al menos {LONGITUD_MINIMA} caracteres."
        )

    if len(password) > LONGITUD_MAXIMA:
        # Un limite alto, pero limite: sin el, alguien podria enviar megabytes y
        # hacer que el servidor gaste memoria hasheandolos.
        raise PasswordInvalida(
            f"La contraseña no puede pasar de {LONGITUD_MAXIMA} caracteres."
        )

    if password.strip() != password:
        raise PasswordInvalida(
            "La contraseña no puede empezar ni terminar con espacios: es una "
            "fuente habitual de no poder entrar después."
        )


def hashear(password: str) -> str:
    """Devuelve la contraseña hasheada, lista para guardar."""
    validar(password)

    sal = secrets.token_bytes(LONGITUD_SAL)
    derivado = _scrypt(password, sal, N, R, P, LONGITUD_HASH)

    return "$".join([
        ALGORITMO,
        str(N), str(R), str(P),
        base64.b64encode(sal).decode("ascii"),
        base64.b64encode(derivado).decode("ascii"),
    ])


def verificar(password: str, almacenado: str) -> bool:
    """Comprueba una contraseña contra su hash.

    Nunca lanza por un hash mal formado: devuelve False. Un registro corrupto no
    debe tumbar el inicio de sesión de todo el mundo, y tampoco debe dejar entrar.
    """
    if not password or not almacenado:
        return False

    try:
        algoritmo, n, r, p, sal_b64, hash_b64 = almacenado.split("$")
        if algoritmo != ALGORITMO:
            return False

        derivado = _scrypt(
            password, base64.b64decode(sal_b64), int(n), int(r), int(p),
            len(base64.b64decode(hash_b64)),
        )
    except (ValueError, TypeError, MemoryError):
        return False

    # compare_digest y no ==: comparar byte a byte filtra por el tiempo de
    # respuesta cuantos caracteres coinciden.
    return hmac.compare_digest(derivado, base64.b64decode(hash_b64))


def necesita_rehash(almacenado: str) -> bool:
    """Si el hash se generó con parámetros más flojos que los actuales.

    Permite subir el coste con el tiempo: al entrar con una contraseña correcta
    se vuelve a hashear con los parámetros nuevos, sin pedirle nada al usuario.
    """
    try:
        algoritmo, n, r, p, _, _ = almacenado.split("$")
    except (ValueError, AttributeError):
        return True

    return algoritmo != ALGORITMO or int(n) < N or int(r) < R or int(p) < P


# --- Identificadores de cuenta ------------------------------------------------

_USERNAME = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
# Deliberadamente permisiva: validar direcciones de correo con precision es un
# problema sin solucion buena, y rechazar una valida es peor que aceptar una que
# rebote. La verificacion real es enviar un correo y ver si llega.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class IdentificadorInvalido(ValueError):
    """El nombre de usuario o el correo no son aceptables."""


def normalizar_username(username: str) -> str:
    """Valida y normaliza un nombre de usuario.

    Se guarda en minúsculas para que `Andy` y `andy` sean la misma cuenta: si no,
    dos personas podrían registrar nombres que se leen igual.
    """
    limpio = (username or "").strip()
    if not _USERNAME.match(limpio):
        raise IdentificadorInvalido(
            "El nombre de usuario debe tener entre 3 y 32 caracteres y solo "
            "puede llevar letras, números, guiones, guiones bajos y puntos."
        )
    return limpio.lower()


def normalizar_email(email: str) -> str:
    limpio = (email or "").strip().lower()
    if not _EMAIL.match(limpio) or len(limpio) > 254:
        raise IdentificadorInvalido("Ese correo electrónico no parece válido.")
    return limpio
