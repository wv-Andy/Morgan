"""
Las actualizaciones de Morgan para Windows (5.3): que la configuración no se desincronice.

Decisiones del 2026-10-04: se instalan **al pulsar**; la clave que las firma vive como secreto del
repositorio público; y **vuelta atrás automática** si la versión nueva no conecta el PC en 3
minutos. La lógica del veredicto se prueba (y se muta) en Rust (`escritorio/mutaciones.ps1`) y el
camino entero en GitHub (`empaquetado/probar_actualizacion.ps1`); aquí, lo que si se descuadra
deja a todos sin actualizaciones sin que nada falle al compilar:

- la clave pública del programa (con la que comprueba el instalador de la vuelta atrás) y la
  de la configuración (con la que el actualizador comprueba lo que baja) tienen que ser la misma;
- de dónde se leen, por https y del repositorio de Morgan;
- que GitHub firme al compilar, con el secreto.
"""

import base64
import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CONF = json.loads((RAIZ / "escritorio" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
ACTUALIZAR = (RAIZ / "escritorio" / "src-tauri" / "src" / "actualizar.rs").read_text(encoding="utf-8")
FLUJO = (RAIZ / ".github" / "workflows" / "escritorio.yml").read_text(encoding="utf-8")


def test_la_misma_clave_publica_en_el_programa_y_en_la_configuracion():
    en_rust = re.search(r'pub const CLAVE_PUBLICA: &str = "([^"]+)";', ACTUALIZAR).group(1)
    assert en_rust == CONF["plugins"]["updater"]["pubkey"]


def test_la_clave_es_una_clave_publica_de_minisign():
    texto = base64.b64decode(CONF["plugins"]["updater"]["pubkey"]).decode("utf-8")
    assert texto.startswith("untrusted comment: minisign public key")
    assert texto.splitlines()[1].startswith("RW")


def test_se_leen_de_la_ultima_version_publicada_de_morgan_por_https():
    assert CONF["plugins"]["updater"]["endpoints"] == [
        "https://github.com/wv-Andy/Morgan/releases/latest/download/latest.json"]


def test_se_instalan_sin_ventanas_y_con_su_firma():
    assert CONF["plugins"]["updater"]["windows"]["installMode"] == "passive"
    assert CONF["bundle"]["createUpdaterArtifacts"] is True


def test_github_firma_al_compilar_con_el_secreto():
    assert "TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}" in FLUJO
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}" in FLUJO


def test_la_clave_privada_no_esta_en_el_repositorio():
    """Ni la clave ni su contraseña: viven en los secretos de GitHub y en `%USERPROFILE%\\.morgan`."""
    for ruta in (RAIZ / "escritorio").rglob("*"):
        if ruta.is_file() and "node_modules" not in ruta.parts and "target" not in ruta.parts \
                and ruta.suffix in (".json", ".rs", ".toml", ".ps1", ".nsh", ".key", ".js", ".html"):
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
            assert "rsign encrypted secret key" not in texto, ruta
            assert "dW50cnVzdGVkIGNvbW1lbnQ6IHJzaWduIGVuY3J5cHRlZCBzZWNyZXQga2V5" not in texto, ruta
