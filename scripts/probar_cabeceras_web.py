"""
Las cabeceras de seguridad de la web (4.22), probadas en un navegador de verdad antes de
publicarlas: una CSP mal hecha deja la web en blanco en producción, y en las pruebas unitarias
no se ve.

Sirve `web/dist` (compilada con `npm --prefix web run build`) con **las cabeceras de
`vercel.json`** y saltos de línea LF (como la compila Vercel desde git: el hash del script en
línea depende de ellos), abre la página en Edge sin ventana y busca en su consola cualquier
cosa que la CSP haya bloqueado. `/api/*` contesta 401, como sin sesión.

    python scripts/probar_cabeceras_web.py      # 0 si no hay ningún bloqueo
    python scripts/probar_cabeceras_web.py https://morgan-ia.vercel.app/   # la publicada
"""

import http.server
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DIST = RAIZ / "web" / "dist"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def cabeceras() -> list[dict]:
    datos = json.loads((RAIZ / "vercel.json").read_text(encoding="utf-8"))
    return next(h for h in datos["headers"] if h["source"] == "/(.*)")["headers"]


class Servidor(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(DIST), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        for h in cabeceras():
            self.send_header(h["key"], h["value"])
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/api/"):
            cuerpo = b'{"detail": "sin sesion"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            return
        ruta = DIST / self.path.split("?")[0].lstrip("/")
        if self.path == "/" or not ruta.is_file():
            # La aplicación de una página, con LF como en Vercel.
            cuerpo = (DIST / "index.html").read_bytes().replace(b"\r\n", b"\n")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            return
        super().do_GET()

    do_POST = do_GET


def main(publicada: str | None = None) -> int:
    servidor = None
    if publicada:
        url = publicada
    else:
        if not (DIST / "index.html").exists():
            print("Falta web/dist: npm --prefix web run build")
            return 2
        servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Servidor)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    with tempfile.TemporaryDirectory() as perfil:
        r = subprocess.run([str(EDGE), "--headless=new", f"--user-data-dir={perfil}", "--enable-logging=stderr",
                            "--v=0", "--virtual-time-budget=8000", "--dump-dom", url],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if servidor is not None:
        servidor.shutdown()
    # Las de las extensiones de Edge no son de la página.
    consola = [l for l in r.stderr.splitlines() if "CONSOLE" in l and "chrome-extension://" not in l]
    bloqueos = [l for l in consola if "Content Security Policy" in l or "Refused to" in l]
    # React montó la aplicación (su menú, o en la nube sin sesión la pantalla de acceso) y ya no
    # está la pantalla de carga estática.
    pinto = ("Nueva conversaci" in r.stdout or "Entrar en Morgan" in r.stdout) and 'class="carga"' not in r.stdout
    print(f"La página se pintó: {'sí' if pinto else 'NO'} ({len(r.stdout)} caracteres de DOM)")
    print(f"Mensajes de consola: {len(consola)}; bloqueados por la CSP: {len(bloqueos)}")
    for l in bloqueos:
        print("  " + l[:400])
    return 0 if pinto and not bloqueos else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
