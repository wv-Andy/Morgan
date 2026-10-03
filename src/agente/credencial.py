"""
La credencial del agente, cifrada con el almacén de Windows (DPAPI).

**Por qué DPAPI.** El §26 del plan pide no guardar credenciales en texto plano si el
sistema operativo ofrece algo seguro. DPAPI (`CryptProtectData`) cifra con una clave
ligada **a tu usuario de Windows**: otro usuario del mismo PC, o alguien que se lleve
el fichero a otra máquina, no puede descifrarlo. Se llama con `ctypes`, sin
dependencias nuevas.

**Lo que no protege**, dicho claro: un programa que ya corre con tu usuario puede
pedirle a Windows que lo descifre, igual que el agente. Es el límite de alcance del
contrato (§16): contra malware con tus permisos, el agente no puede hacer nada.

Fuera de Windows no hay DPAPI y **no se guarda en claro como plan B**: se lanza. El
agente es un programa de Windows (§18 del plan); en Linux solo corren sus pruebas.
"""

import ctypes
import sys

#: Qué dice el blob cifrado sobre sí mismo. Solo informativo, no es un secreto.
DESCRIPCION = "Morgan: credencial del agente local"


class SinAlmacenSeguro(RuntimeError):
    """No hay DPAPI (no es Windows): el agente no guarda credenciales en claro."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(datos: bytes) -> _Blob:
    buffer = ctypes.create_string_buffer(datos, len(datos))
    blob = _Blob(len(datos), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob._buffer = buffer  # que no lo recoja el recolector mientras se usa
    return blob


def _dpapi():
    if sys.platform != "win32":
        raise SinAlmacenSeguro(
            "El agente local es un programa de Windows y guarda su credencial con DPAPI. "
            "Aquí no hay DPAPI, y no se guarda en claro."
        )
    return ctypes.windll.crypt32, ctypes.windll.kernel32


def _sacar(blob: _Blob) -> bytes:
    _, kernel32 = _dpapi()
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        kernel32.LocalFree(blob.pbData)


def cifrar(texto: str) -> bytes:
    """El texto, cifrado para el usuario de Windows actual."""
    crypt32, _ = _dpapi()
    entrada = _blob(texto.encode("utf-8"))
    salida = _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN (0x1): nunca una ventana; el agente corre solo.
    if not crypt32.CryptProtectData(
        ctypes.byref(entrada), DESCRIPCION, None, None, None, 0x1, ctypes.byref(salida)
    ):
        raise OSError(ctypes.GetLastError(), "Windows no pudo cifrar la credencial")
    return _sacar(salida)


def descifrar(datos: bytes) -> str:
    """Lo que cifró `cifrar`, con el mismo usuario de Windows."""
    crypt32, _ = _dpapi()
    entrada = _blob(datos)
    salida = _Blob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(entrada), None, None, None, None, 0x1, ctypes.byref(salida)
    ):
        raise OSError(ctypes.GetLastError(), "Windows no pudo descifrar la credencial")
    return _sacar(salida).decode("utf-8")
