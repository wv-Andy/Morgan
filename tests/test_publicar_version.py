"""
`empaquetado/publicar_version.py` (5.3): lo que lee el programa para actualizarse.

Si el `latest.json` de una versión dice otra versión, otra dirección o una firma con basura, el
actualizador de Tauri la rechaza (o, peor, baja otra cosa) y nadie se actualiza. Y la vuelta atrás
busca el instalador de cada versión por su nombre exacto.
"""

import datetime
import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publicar_version", RAIZ / "empaquetado" / "publicar_version.py")
pv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pv)

FECHA = datetime.datetime(2026, 10, 4, 20, 0, tzinfo=datetime.timezone.utc)


def test_el_latest_json_de_una_version():
    datos = pv.latest("5.3.0", "dW50cnVzdGVk...\n", "**Actualizaciones.** Al pulsar.\n\nMás texto.", FECHA)
    assert datos["version"] == "5.3.0"
    assert datos["pub_date"] == "2026-10-04T20:00:00Z"
    plataforma = datos["platforms"]["windows-x86_64"]
    assert plataforma["signature"] == "dW50cnVzdGVk..."
    assert plataforma["url"] == ("https://github.com/wv-Andy/Morgan/releases/download/v5.3.0/"
                                 "Morgan.para.Windows_5.3.0_x64-setup.exe")
    assert datos["notes"] == "**Actualizaciones.** Al pulsar."


def test_el_nombre_es_el_que_busca_la_vuelta_atras():
    """`actualizar.rs` baja `Morgan.para.Windows_{version}_x64-setup.exe` y su `.sig`."""
    rust = (RAIZ / "escritorio" / "src-tauri" / "src" / "actualizar.rs").read_text(encoding="utf-8")
    assert 'format!("Morgan.para.Windows_{version}_x64-setup.exe")' in rust
    assert pv.nombre("5.3.0") == "Morgan.para.Windows_5.3.0_x64-setup.exe"


def test_la_version_es_la_del_codigo():
    from src import __version__

    assert pv.version_actual() == __version__
