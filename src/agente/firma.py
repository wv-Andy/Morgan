"""
Las actualizaciones del agente, firmadas por mí (3.8).

Decisión mía (2026-09-25): **una actualización solo vale si la firmó él**, con una
clave Ed25519 que vive solo en su PC (`scripts/publicar_agente.py`), cifrada con DPAPI,
fuera del repositorio y de Render. La nube **solo aloja** el paquete. El agente lleva
dentro la clave pública y comprueba:

1. que el manifiesto (versión, protocolo, hash y tamaño del paquete) lo firmó esa clave;
2. que es **más nuevo** que él: una nube tomada podría servir un paquete viejo, firmado
   de verdad, con un fallo ya arreglado (volver atrás a propósito, *downgrade*);
3. que el paquete que baja es exactamente el del manifiesto (SHA-256 y tamaño);
4. y, al abrirlo, que no trae nada fuera de su sitio (ni rutas absolutas ni `..`).

Así, ni quien tome la nube ni quien se ponga en medio de la conexión puede hacer que un
PC ejecute código que yo no publiqué (§V3.8 del plan: nunca `Cloud → ejecutar código
de actualización arbitrario`).
"""

import base64
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

#: La clave pública con la que firmo (Ed25519, 32 bytes en hexadecimal). La
#: privada no está en ningún sitio de este repositorio.
CLAVE_PUBLICA = "8dcad1b076fbd26a3f5fe9ca14f4949418e10d06291f47e8736bfc8de6eb3d88"

#: Lo único que puede traer un paquete del agente.
PERMITIDO = (re.compile(r"^src/__init__\.py$"), re.compile(r"^src/agente/[A-Za-z0-9_/]+\.py$"),
             re.compile(r"^requirements-agente\.txt$"))

#: Lo más que puede ocupar: el agente son unos cientos de KB de Python.
MAX_PAQUETE = 5 * 1024 * 1024


class FirmaNoValida(ValueError):
    """El manifiesto o el paquete no son lo que publiqué. No se instala nada."""


def clave_de_version(version: str) -> tuple:
    """`3.8.0` → (3, 8, 0, 1); `3.8.0-dev` → (3, 8, 0, 0): una -dev va antes que la final."""
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(-dev)?", str(version or "").strip())
    if not m:
        raise FirmaNoValida(f"Versión que no se entiende: {version!r}")
    return int(m[1]), int(m[2]), int(m[3] or 0), 0 if m[4] else 1


def verificar(manifiesto: str, firma: str, clave_publica: str | None = None) -> dict:
    """El manifiesto, si lo firmó mi clave. Si no, `FirmaNoValida`."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    clave = clave_publica if clave_publica is not None else CLAVE_PUBLICA
    if not clave:
        raise FirmaNoValida("Este agente no lleva clave para comprobar actualizaciones.")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(clave)).verify(
            base64.b64decode(firma, validate=True), manifiesto.encode("utf-8"))
    except (InvalidSignature, ValueError, TypeError):
        raise FirmaNoValida("La firma no es la del autor de Morgan: no se instala.") from None
    try:
        datos = json.loads(manifiesto)
    except ValueError:
        raise FirmaNoValida("Manifiesto ilegible.") from None
    if not isinstance(datos, dict) or datos.get("producto") != "morgan-agente":
        raise FirmaNoValida("Ese manifiesto no es de un agente de Morgan.")
    clave_de_version(datos.get("version"))
    if not isinstance(datos.get("sha256"), str) or not isinstance(datos.get("bytes"), int):
        raise FirmaNoValida("Al manifiesto le falta el hash o el tamaño.")
    return datos


def es_mas_nueva(manifiesto: dict, actual: str) -> bool:
    return clave_de_version(manifiesto["version"]) > clave_de_version(actual)


def comprobar_paquete(datos: bytes, manifiesto: dict) -> None:
    """Que el paquete bajado es exactamente el del manifiesto firmado."""
    if len(datos) != manifiesto["bytes"] or len(datos) > MAX_PAQUETE:
        raise FirmaNoValida("El paquete no mide lo que dice el manifiesto.")
    if hashlib.sha256(datos).hexdigest() != manifiesto["sha256"]:
        raise FirmaNoValida("El paquete no es el del manifiesto (hash distinto).")


def abrir(datos: bytes, destino: Path) -> list[str]:
    """Abre el paquete en `destino`, solo con lo permitido. Devuelve lo que escribió."""
    import io

    escritos = []
    with zipfile.ZipFile(io.BytesIO(datos)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            nombre = info.filename
            partes = PurePosixPath(nombre).parts
            # `..` ya no cabe en PERMITIDO (sin puntos en las carpetas): se mira aparte a
            # propósito, para que ampliar la lista no abra la puerta a salirse.
            if (nombre.startswith(("/", "\\")) or "\\" in nombre or ":" in nombre or ".." in partes
                    or not any(p.match(nombre) for p in PERMITIDO)):
                raise FirmaNoValida(f"El paquete trae algo fuera de su sitio: {nombre!r}")
            ruta = destino.joinpath(*partes)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(zf.read(info))
            escritos.append(nombre)
    if "src/__init__.py" not in escritos or "src/agente/__main__.py" not in escritos:
        raise FirmaNoValida("Al paquete le falta el agente.")
    return escritos
