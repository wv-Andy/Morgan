"""
Las cabeceras de seguridad de la web (4.22, en `vercel.json`).

La CSP deja ejecutar un solo script en línea, el del tema de `web/index.html`, **por su
hash**. Si alguien cambia ese script y no el hash, producción se queda con el tema por
defecto un instante y una violación de CSP en cada carga, y ninguna otra prueba lo ve. El
hash se calcula con LF, que es como compila Vercel desde git.

En un navegador de verdad, con todo: `python scripts/probar_cabeceras_web.py`.
"""

import base64
import hashlib
import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _cabeceras() -> dict[str, str]:
    datos = json.loads((RAIZ / "vercel.json").read_text(encoding="utf-8"))
    todas = next(h for h in datos["headers"] if h["source"] == "/(.*)")["headers"]
    return {h["key"]: h["value"] for h in todas}


def _directivas() -> dict[str, list[str]]:
    csp = _cabeceras()["Content-Security-Policy"]
    return {d.split()[0]: d.split()[1:] for d in (x.strip() for x in csp.split(";")) if d}


def test_el_hash_es_el_del_script_del_tema():
    html = (RAIZ / "web" / "index.html").read_bytes().decode("utf-8").replace("\r\n", "\n")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) == 1, "un script en línea nuevo necesita su hash en la CSP"
    esperado = "'sha256-" + base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode() + "'"
    assert esperado in _directivas()["script-src"]


def test_ningun_script_de_fuera_ni_en_linea_sin_hash():
    script = _directivas()["script-src"]
    assert "'unsafe-inline'" not in script and "'unsafe-eval'" not in script
    assert all(f == "'self'" or f.startswith("'sha256-") for f in script)


def test_solo_habla_con_su_propio_dominio():
    """La API va por /api (el mismo dominio): nada más."""
    assert _directivas()["connect-src"] == ["'self'"]


def test_no_se_puede_meter_en_otra_pagina():
    assert _directivas()["frame-ancestors"] == ["'none'"]
    assert _cabeceras()["X-Frame-Options"] == "DENY"


def test_ni_camara_ni_ubicacion_y_el_microfono_solo_ella():
    permisos = _cabeceras()["Permissions-Policy"]
    for apagado in ("camera=()", "geolocation=()", "payment=()", "usb=()"):
        assert apagado in permisos
    assert "microphone=(self)" in permisos
