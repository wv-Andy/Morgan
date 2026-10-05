"""
Lo básico de accesibilidad de la web (4.23), medido con axe-core en un navegador de verdad.

Abre cada página en Edge sin ventana, le inyecta axe-core (la biblioteca de referencia, la que
usan las herramientas de Chrome y Edge) por el protocolo de depuración y pasa las reglas de
**WCAG 2.1 A y AA**: nombres de botones y campos, contraste, idioma, encabezados, foco… Imprime
cada fallo con su gravedad y dónde está, y sale con 1 si hay alguno grave o crítico.

    python scripts/auditar_accesibilidad.py                                # la publicada
    python scripts/auditar_accesibilidad.py http://localhost:5173/ --tema claro

Sin cuenta, la publicada enseña la portada y la pantalla de entrar: es lo primero que ve
alguien nuevo. Lo de dentro se mide contra la web local (`morgan api` en modo local, sin
cuenta), sin crear cuentas en producción.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client (requirements-dev.txt)

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
AXE_VERSION = "4.12.0"
AXE_URL = f"https://cdnjs.cloudflare.com/ajax/libs/axe-core/{AXE_VERSION}/axe.min.js"
CACHE = Path(tempfile.gettempdir()) / f"axe-{AXE_VERSION}.min.js"
REGLAS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
GRAVES = {"serious", "critical"}


def axe() -> str:
    if not CACHE.exists():
        with urllib.request.urlopen(AXE_URL, timeout=30) as r:
            CACHE.write_bytes(r.read())
    return CACHE.read_text(encoding="utf-8")


class Navegador:
    def __init__(self, puerto: int = 9444):
        self.perfil = tempfile.TemporaryDirectory()
        self.proceso = subprocess.Popen([str(EDGE), "--headless=new", f"--remote-debugging-port={puerto}",
                                         f"--user-data-dir={self.perfil.name}", "--window-size=1280,900",
                                         "--no-first-run", "about:blank"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ids = itertools.count(1)
        limite = time.time() + 20
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/json", timeout=2) as r:
                    pagina = next(o for o in json.load(r) if o.get("type") == "page")
                break
            except (OSError, StopIteration):
                if time.time() > limite:
                    raise
                time.sleep(0.5)
        self.ws = websocket.create_connection(pagina["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)
        self.orden("Page.enable")

    def orden(self, metodo: str, **params):
        n = next(self.ids)
        self.ws.send(json.dumps({"id": n, "method": metodo, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == n:
                return m.get("result", {})

    def evaluar(self, expresion: str):
        r = self.orden("Runtime.evaluate", expression=expresion, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("text"))
        return r.get("result", {}).get("value")

    def cerrar(self):
        try:
            self.ws.close()
        finally:
            self.proceso.kill()
            self.proceso.wait(timeout=10)
            time.sleep(0.5)
            try:
                self.perfil.cleanup()
            except OSError:
                pass


def auditar(nav: Navegador, url: str, tema: str | None, espera: float) -> list[dict]:
    nav.orden("Page.setBypassCSP", enabled=True)      # para inyectar axe; la página es la misma
    if tema:
        nav.orden("Emulation.setEmulatedMedia", features=[
            {"name": "prefers-color-scheme", "value": "dark" if tema == "oscuro" else "light"}])
    nav.orden("Page.navigate", url=url)
    time.sleep(espera)
    nav.evaluar(axe())
    resultado = nav.evaluar(
        "axe.run(document, {runOnly: {type: 'tag', values: %s}, resultTypes: ['violations']})"
        ".then(r => r.violations.map(v => ({id: v.id, impacto: v.impact, ayuda: v.help, nodos: v.nodes.length,"
        " ejemplos: v.nodes.slice(0, 3).map(n => n.target.join(' ') + (n.any[0] ? ' — ' + n.any[0].message : ''))})))"
        % json.dumps(REGLAS))
    return resultado or []


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("urls", nargs="*", default=["https://morgan-ia.vercel.app/"])
    p.add_argument("--tema", choices=["claro", "oscuro"], help="el tema del sistema que se simula")
    p.add_argument("--espera", type=float, default=6.0, help="segundos para que cargue la página")
    args = p.parse_args()

    nav = Navegador()
    graves = 0
    try:
        for url in args.urls:
            fallos = auditar(nav, url, args.tema, args.espera)
            print(f"\n{url} ({args.tema or 'tema por defecto'}): {len(fallos)} reglas incumplidas")
            for f in sorted(fallos, key=lambda f: ["critical", "serious", "moderate", "minor"].index(f["impacto"] or "minor")):
                graves += f["impacto"] in GRAVES
                print(f"  [{f['impacto']}] {f['id']} ({f['nodos']}): {f['ayuda']}")
                for e in f["ejemplos"]:
                    print(f"      {e[:220]}")
    finally:
        nav.cerrar()
    return 1 if graves else 0


if __name__ == "__main__":
    sys.exit(main())
