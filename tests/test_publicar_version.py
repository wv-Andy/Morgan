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


class _Gh:
    """`gh` de mentira: qué se le pidió, y si la versión ya existe."""

    def __init__(self, existe: bool):
        self.existe, self.llamadas = existe, []

    def __call__(self, orden, **_):
        import subprocess

        self.llamadas.append(orden)
        if orden[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(orden, 0 if self.existe else 1, b"", b"")
        return subprocess.CompletedProcess(orden, 0, b"", b"")


def _carpeta(tmp_path, version):
    carpeta = tmp_path / "nsis"
    carpeta.mkdir()
    (carpeta / f"Morgan para Windows_{version}_x64-setup.exe").write_bytes(b"MZ instalador")
    (carpeta / f"Morgan para Windows_{version}_x64-setup.exe.sig").write_text("firma", encoding="utf-8")
    return carpeta


def test_desde_github_publica_las_tres_cosas(tmp_path, monkeypatch):
    """5.4: «Run workflow» con «publicar»: el instalador ya está en una carpeta de la compilación."""
    version = pv.version_actual()
    gh = _Gh(existe=False)
    monkeypatch.setattr(pv.subprocess, "run", gh)
    monkeypatch.setattr("sys.argv", ["publicar", "--desde", str(_carpeta(tmp_path, version)), "--notas-texto", "Lo nuevo"])
    assert pv.main() == 0
    crear = next(o for o in gh.llamadas if o[:3] == ["gh", "release", "create"])
    assert crear[3] == f"v{version}"
    subidos = [Path(x).name for x in crear if x.endswith((".exe", ".sig", ".json"))]
    assert subidos == [pv.nombre(version), f"{pv.nombre(version)}.sig", "latest.json"]


def test_una_version_ya_publicada_no_se_vuelve_a_publicar(tmp_path, monkeypatch):
    """Publicar dos veces el mismo número cambiaría el instalador que ya bajó la gente (y la
    Store exige que no cambie)."""
    gh = _Gh(existe=True)
    monkeypatch.setattr(pv.subprocess, "run", gh)
    monkeypatch.setattr("sys.argv", ["publicar", "--desde", str(_carpeta(tmp_path, pv.version_actual())), "--notas-texto", "x"])
    assert pv.main() == 3
    assert not [o for o in gh.llamadas if o[:3] == ["gh", "release", "create"]]


def test_la_firma_de_windows_solo_con_signpath_y_publicar_solo_a_mano():
    import yaml

    flujo = yaml.safe_load((RAIZ / ".github" / "workflows" / "escritorio.yml").read_text(encoding="utf-8"))
    pasos = flujo["jobs"]["instalador"]["steps"]
    # «Lo de dentro, sin firma» corre siempre: solo cuenta y copia a una carpeta temporal.
    firma = [p for p in pasos if ("signpath" in str(p.get("uses", "")) or "firm" in str(p.get("name", "")).lower())
             and p.get("name") not in ("Lo de dentro, sin firma", "La firma sobrevive a empaquetar (prueba)")]
    assert len(firma) >= 4
    subir = [p for p in pasos if "upload-artifact" in str(p.get("uses", "")) and p.get("id")]
    assert len(subir) == 2 and all("vars.SIGNPATH_ORGANIZATION_ID != ''" in p["if"] for p in subir)
    for p in firma:
        assert "vars.SIGNPATH_ORGANIZATION_ID != ''" in p.get("if", ""), p.get("name") or p.get("uses")
    for p in (p for p in pasos if "signpath" in str(p.get("uses", ""))):
        assert p["with"]["api-token"] == "${{ secrets.SIGNPATH_API_TOKEN }}"
    publicar = next(p for p in pasos if p.get("name") == "Publicar la versión")
    assert "success()" in publicar["if"] and "workflow_dispatch" in publicar["if"] and "inputs.publicar" in publicar["if"]
    assert "${{" not in publicar["run"], "lo que se escribe en «notas» va por variable, nunca dentro del script"
    assert pasos[-2] is publicar, "publicar, después de todas las pruebas"
    prueba = pasos[-1]
    assert prueba["name"] == "La firma sobrevive a empaquetar (prueba)"
    assert prueba["if"] == "${{ github.event_name == 'push' }}", "el certificado de prueba nunca en una que publica"
