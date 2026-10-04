"""
Publicar una versión de Morgan para Windows (5.3): el instalador, su firma y `latest.json`.

Desde la 5.3 el programa se actualiza leyendo `latest.json` de la última versión publicada
(`releases/latest/download/latest.json`) y comprobando la firma de lo que baja; y para la vuelta
atrás baja el instalador **de su propia versión** con su `.sig`. Así que cada versión publicada
tiene que llevar las tres cosas, con estos nombres exactos. Hacerlo a mano era la forma de que un
día faltase una y nadie se enterase hasta no poder actualizar.

    python empaquetado/publicar_version.py --run <id de la ejecución de Escritorio> --notas notas.md

La versión sale de `src/__init__.py`; el instalador y su firma, del artefacto de esa ejecución
(compilado y firmado en GitHub, con la clave que solo está en sus secretos).
"""

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
REPO = "wv-Andy/Morgan"


def nombre(version: str) -> str:
    """Como lo deja GitHub al subirlo (los espacios, puntos): el que buscan el programa y la web."""
    return f"Morgan.para.Windows_{version}_x64-setup.exe"


def latest(version: str, firma: str, notas: str, fecha: datetime.datetime) -> dict:
    """El `latest.json` del actualizador de Tauri para esta versión."""
    return {
        "version": version,
        "notes": notas.strip().split("\n\n")[0][:500],
        "pub_date": fecha.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "platforms": {
            "windows-x86_64": {
                "signature": firma.strip(),
                "url": f"https://github.com/{REPO}/releases/download/v{version}/{nombre(version)}",
            }
        },
    }


def version_actual() -> str:
    texto = (RAIZ / "src" / "__init__.py").read_text(encoding="utf-8")
    return texto.split('__version__ = "')[1].split('"')[0]


def main() -> int:
    args = argparse.ArgumentParser()
    args.add_argument("--run", required=True, help="la ejecución de Escritorio que compiló esta versión")
    args.add_argument("--notas", required=True, type=Path)
    a = args.parse_args()
    version = version_actual()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        subprocess.run(["gh", "run", "download", a.run, "-R", REPO, "-n", "Morgan-instalador", "-D", str(tmp)],
                       check=True)
        origen = tmp / f"Morgan para Windows_{version}_x64-setup.exe"
        if not origen.exists() or not Path(f"{origen}.sig").exists():
            print(f"El artefacto no trae el instalador {version} y su firma: {sorted(p.name for p in tmp.iterdir())}")
            return 2
        instalador = tmp / nombre(version)
        shutil.copy(origen, instalador)
        shutil.copy(f"{origen}.sig", f"{instalador}.sig")
        firma = Path(f"{instalador}.sig").read_text(encoding="utf-8")
        notas = a.notas.read_text(encoding="utf-8")
        (tmp / "latest.json").write_text(
            json.dumps(latest(version, firma, notas, datetime.datetime.now(datetime.timezone.utc)), indent=2),
            encoding="utf-8")
        huella = hashlib.sha256(instalador.read_bytes()).hexdigest().upper()
        (tmp / "notas.md").write_text(f"{notas.rstrip()}\n\nSHA-256 del instalador: `{huella}`\n", encoding="utf-8")
        subprocess.run(["gh", "release", "create", f"v{version}", "-R", REPO, "--title", f"Morgan para Windows {version}",
                        "--notes-file", str(tmp / "notas.md"), "--latest",
                        str(instalador), f"{instalador}.sig", str(tmp / "latest.json")], check=True)
        print(f"Publicada la v{version} (SHA-256 {huella}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
