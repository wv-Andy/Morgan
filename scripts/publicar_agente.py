"""
Publicar una versión del agente local, firmada (3.8). **Solo en mi PC.**

    python scripts/publicar_agente.py claves      # una vez: crea la clave de firma
    python scripts/publicar_agente.py publicar    # cada versión: paquete + manifiesto + firma

La clave privada (Ed25519) se guarda **cifrada con DPAPI** en `%USERPROFILE%\\.morgan\\`:
fuera del repositorio, fuera de OneDrive y fuera de Render; solo tu usuario de Windows
la puede descifrar. **Nunca se imprime.** `claves` escribe la pública en
`src/agente/firma.py`, que es la que los agentes llevan dentro.

`publicar` deja en `publicado/agente/` el paquete (`paquete.zip`, solo `src/agente`, la
versión y `requirements-agente.txt`), su `manifiesto.json` y la `firma.txt`. Van al
repositorio y la nube los sirve tal cual: **la nube no firma nada**. Un paquete con la
misma versión que el publicado no se puede publicar otra vez (los agentes no instalan
una versión que ya tienen).
"""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

PUBLICADO = RAIZ / "publicado" / "agente"
FIRMA_PY = RAIZ / "src" / "agente" / "firma.py"


def carpeta_de_la_clave() -> Path:
    return Path(os.environ.get("MORGAN_FIRMA_DIR") or Path.home() / ".morgan")


def _privada():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from src.agente import credencial

    ruta = carpeta_de_la_clave() / "firma-agente.bin"
    if not ruta.exists():
        raise SystemExit("No hay clave de firma en este PC. Primero: python scripts/publicar_agente.py claves")
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(credencial.descifrar(ruta.read_bytes())))


def _publica_hex(privada) -> str:
    from cryptography.hazmat.primitives import serialization

    return privada.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def claves(reemplazar: bool = False) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from src.agente import credencial

    ruta = carpeta_de_la_clave() / "firma-agente.bin"
    if ruta.exists() and not reemplazar:
        print(f"Ya hay una clave en {ruta}. Cambiarla deja a los agentes instalados sin poder "
              "comprobar lo que publiques después, salvo que antes publiques una versión que lleve "
              "la pública nueva. Si es lo que quieres: claves --reemplazar")
        return 1
    privada = Ed25519PrivateKey.generate()
    crudo = privada.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                  serialization.NoEncryption())
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(credencial.cifrar(crudo.hex()))
    publica = _publica_hex(privada)
    _escribir_publica(publica)
    print(f"Clave de firma creada y cifrada con DPAPI en {ruta}.")
    print(f"Pública (ya escrita en src/agente/firma.py): {publica}")
    return 0


def _escribir_publica(publica: str) -> None:
    texto = FIRMA_PY.read_text(encoding="utf-8")
    nuevo, n = re.subn(r'^CLAVE_PUBLICA = "[0-9a-f]*"$', f'CLAVE_PUBLICA = "{publica}"', texto, flags=re.M)
    if n != 1:
        raise SystemExit("No encuentro CLAVE_PUBLICA en src/agente/firma.py")
    FIRMA_PY.write_text(nuevo, encoding="utf-8")


def paquete() -> bytes:
    """El zip del agente, siempre igual para el mismo código (orden y fechas fijos)."""
    from src.agente.firma import PERMITIDO

    ficheros = [RAIZ / "src" / "__init__.py", RAIZ / "requirements-agente.txt",
                *sorted((RAIZ / "src" / "agente").rglob("*.py"))]
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as zf:
        for ruta in ficheros:
            nombre = ruta.relative_to(RAIZ).as_posix()
            if "__pycache__" in nombre:
                continue
            assert any(p.match(nombre) for p in PERMITIDO), nombre
            info = zipfile.ZipInfo(nombre, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, ruta.read_bytes().replace(b"\r\n", b"\n"))
    return salida.getvalue()


def _version() -> str:
    """Del fichero, no de `import src`: Python puede usar el `.pyc` de antes si la versión
    cambió en el mismo segundo y con la misma longitud (visto en la evaluación de la 3.8.5:
    se «publicó» la 9.0.4 dos veces)."""
    texto = (RAIZ / "src" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__ = "([^"]+)"', texto, flags=re.M).group(1)


def publicar() -> int:
    from src.agente.firma import CLAVE_PUBLICA, clave_de_version
    from src.agente.protocolo import PROTOCOLO_ACTUAL

    privada = _privada()
    if _publica_hex(privada) != CLAVE_PUBLICA:
        print("La clave de este PC no es la que llevan los agentes (src/agente/firma.py): "
              "lo que publicaras no lo aceptaría ninguno.")
        return 1
    __version__ = _version()
    anterior = PUBLICADO / "manifiesto.json"
    if anterior.exists():
        publicada = json.loads(anterior.read_text(encoding="utf-8"))["version"]
        if clave_de_version(__version__) <= clave_de_version(publicada):
            print(f"Ya está publicada la {publicada}; esta es la {__version__}. Sube la versión antes.")
            return 1
    datos = paquete()
    manifiesto = json.dumps({
        "producto": "morgan-agente", "version": __version__, "protocolo": PROTOCOLO_ACTUAL,
        "sha256": hashlib.sha256(datos).hexdigest(), "bytes": len(datos),
        "publicado": int(time.time()), "python_minimo": "3.12",
    }, ensure_ascii=False, sort_keys=True)
    firma = base64.b64encode(privada.sign(manifiesto.encode("utf-8"))).decode("ascii")
    PUBLICADO.mkdir(parents=True, exist_ok=True)
    (PUBLICADO / "paquete.zip").write_bytes(datos)
    (PUBLICADO / "manifiesto.json").write_text(manifiesto, encoding="utf-8")
    (PUBLICADO / "firma.txt").write_text(firma, encoding="utf-8")
    print(f"Publicada la {__version__} ({len(datos) // 1024} KB) en {PUBLICADO}. "
          "Súbela al repositorio para que la nube la sirva.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ordenes = parser.add_subparsers(dest="orden", required=True)
    c = ordenes.add_parser("claves")
    c.add_argument("--reemplazar", action="store_true")
    ordenes.add_parser("publicar")
    args = parser.parse_args(argv)
    if args.orden == "claves":
        return claves(args.reemplazar)
    return publicar()


if __name__ == "__main__":
    sys.exit(main())
