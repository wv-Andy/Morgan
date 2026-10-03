"""
Cifrado en reposo de los tokens de servicios externos (V1.9).

Un token de OAuth de GitHub es una credencial viva: con él se puede leer código
privado, abrir *issues* y —según los permisos concedidos— escribir. Guardarlo en
texto plano significa que cualquiera con acceso de lectura a la base de datos
tiene acceso a los repositorios de todos los usuarios. Una copia de seguridad
extraviada deja de ser un problema de privacidad y pasa a ser uno de acceso.

**No hay criptografía propia aquí.** Se usa Fernet, de la biblioteca
`cryptography`: AES-128 en CBC con HMAC-SHA256 y su marca de tiempo, con la
implementación y los modos ya decididos por gente que sabe. Este módulo solo
resuelve de dónde sale la clave y qué hacer cuando no hay.

## De dónde sale la clave

De `MORGAN_SECRET_KEY`, una variable de entorno. No se deriva de nada ni se
genera automáticamente al arrancar, y las dos cosas son deliberadas:

- **Derivarla de otra variable** —la clave de Supabase, por ejemplo— ata dos
  secretos que deberían poder rotarse por separado. Rotar la de Supabase
  dejaría ilegibles todos los tokens guardados.
- **Generarla al arrancar** haría que cada reinicio de Render inutilizara todas
  las integraciones conectadas, y sin ningún error: simplemente dejarían de
  descifrarse.

## Qué pasa si no hay clave

Las integraciones **no se ofrecen**. No se guardan tokens en claro «mientras
tanto», que es la solución que parece pragmática y es la que acaba en
producción: un token en claro no se distingue de uno cifrado mirando la tabla,
así que el día que alguien lo note ya llevará meses ahí.
"""

import base64
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

VARIABLE = "MORGAN_SECRET_KEY"


class SinClaveDeCifrado(RuntimeError):
    """No hay con qué cifrar, así que no se guarda nada."""

    def __init__(self) -> None:
        super().__init__(
            "Falta MORGAN_SECRET_KEY: sin ella no se pueden guardar las "
            "credenciales de servicios externos de forma segura."
        )


def _clave_fernet(secreto: str) -> bytes:
    """Convierte el secreto de entorno en una clave con el formato de Fernet.

    Fernet exige 32 bytes en base64 *url-safe*. Pedirle a una persona que genere
    exactamente eso y lo pegue en un panel es pedirle que se equivoque, así que
    se admite cualquier cadena y se pasa por SHA-256.

    Esto **no estira** la clave: no es una contraseña de usuario que haya que
    proteger contra fuerza bruta, es un secreto de servidor que debe ser largo
    por su cuenta. Un `sha256` aquí solo normaliza la longitud.
    """
    return base64.urlsafe_b64encode(hashlib.sha256(secreto.encode("utf-8")).digest())


def hay_clave() -> bool:
    """Si este despliegue puede guardar credenciales externas."""
    return bool((os.getenv(VARIABLE) or "").strip())


def _cifrador():
    secreto = (os.getenv(VARIABLE) or "").strip()
    if not secreto:
        raise SinClaveDeCifrado()

    from cryptography.fernet import Fernet

    return Fernet(_clave_fernet(secreto))


def cifrar(valor: str) -> str:
    """Devuelve el valor cifrado, listo para guardar."""
    if not valor:
        raise ValueError("No se cifra una cadena vacía.")

    return _cifrador().encrypt(valor.encode("utf-8")).decode("ascii")


def descifrar(cifrado: str) -> str:
    """Recupera el valor. Lanza si la clave cambió o el dato está corrupto.

    **No devuelve una cadena vacía cuando falla.** Un token vacío se usaría como
    si fuera válido y produciría un 401 del servicio externo, que es un síntoma
    que no apunta a su causa. Que reviente aquí, donde se ve el motivo.
    """
    from cryptography.fernet import InvalidToken

    try:
        return _cifrador().decrypt(cifrado.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "No se pudo descifrar la credencial guardada. Lo más probable es "
            f"que {VARIABLE} haya cambiado: si es así, hay que volver a "
            "conectar los servicios."
        ) from exc

