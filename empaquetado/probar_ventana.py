"""
La ventana única de Morgan para Windows (5.3), medida desde dentro, en GitHub Actions.

El programa se abre con WebView2 escuchando el protocolo de depuración
(`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=…`, solo en la prueba) y este
script mira la página como lo haría quien la usa:

1. **Sin emparejar**, la ventana enseña la pantalla del programa (y no en negro: la 5.2 dejaba
   la conversación en negro, en mi captura del 2026-10-04).
2. Emparejado (de mentira), «Volver a Morgan» lleva la **misma ventana** a la web de Morgan,
   que sabe que está dentro del programa.
3. Desde la web, **solo lo inofensivo**: `resumen` contesta; `emparejar`, `consultar`,
   `desemparejar` y `estado` se rechazan (capabilities/web.json).
4. Una página ajena no se carga en la ventana (se abre en el navegador).
5. `pantalla_emparejar` lleva de la web a la pantalla del programa.

    python empaquetado/probar_ventana.py --puerto 9333 --estado <MORGAN_AGENTE_DIR>

Imprime una línea por comprobación y sale con 1 si alguna falla.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client: solo en la integración continua

WEB = "https://morgan-ia.vercel.app"
LOCAL = "http://tauri.localhost/"


class Pagina:
    def __init__(self, puerto: int):
        self.puerto = puerto
        self.ws = None
        self.ids = itertools.count(1)

    def objetivo(self) -> dict | None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.puerto}/json", timeout=5) as r:
                lista = json.load(r)
        except OSError:
            return None
        paginas = [o for o in lista if o.get("type") == "page"]
        return paginas[0] if paginas else None

    def conectar(self, segundos: float = 60) -> bool:
        limite = time.time() + segundos
        while time.time() < limite:
            o = self.objetivo()
            if o and o.get("webSocketDebuggerUrl"):
                self.ws = websocket.create_connection(o["webSocketDebuggerUrl"], timeout=30,
                                                      suppress_origin=True)
                return True
            time.sleep(1)
        return False

    def evaluar(self, expresion: str):
        n = next(self.ids)
        self.ws.send(json.dumps({"id": n, "method": "Runtime.evaluate", "params": {
            "expression": expresion, "awaitPromise": True, "returnByValue": True}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == n:
                r = m.get("result", {})
                if "exceptionDetails" in r:
                    return {"excepcion": r["exceptionDetails"].get("text")}
                return r.get("result", {}).get("value")

    def url(self) -> str:
        return self.evaluar("location.href") or ""

    def esperar(self, condicion, segundos: float = 60) -> bool:
        limite = time.time() + segundos
        while time.time() < limite:
            try:
                if condicion():
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def pedir(self, orden: str, args: dict | None = None) -> dict:
        """Una orden al programa, como la pediría la página: {ok, valor | error}."""
        return self.evaluar(
            f"window.__TAURI_INTERNALS__.invoke({json.dumps(orden)}, {json.dumps(args or {})})"
            ".then(v => ({ok: true, valor: v}), e => ({ok: false, error: String(e)}))") or {}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--puerto", type=int, required=True)
    p.add_argument("--estado", required=True, help="la carpeta del estado del agente (MORGAN_AGENTE_DIR)")
    args = p.parse_args()

    r: dict[str, object] = {}
    pagina = Pagina(args.puerto)
    r["depuracion"] = pagina.conectar()
    if not r["depuracion"]:
        print("depuracion                    False (WebView2 no abrió el puerto)")
        return 1

    # 1. Sin emparejar: la pantalla del programa, pintada.
    r["sin_emparejar_la_pantalla_del_programa"] = pagina.esperar(
        lambda: pagina.url().startswith(LOCAL) and "Conecta este PC" in (pagina.evaluar("document.body.innerText") or ""))
    r["no_en_negro"] = bool(pagina.evaluar("document.body.innerText.trim().length > 20"))
    r["la_pantalla_pide_el_estado"] = bool(pagina.pedir("resumen").get("ok"))

    # 2. Emparejado de mentira: «Volver a Morgan» lleva la misma ventana a la web.
    estado = Path(args.estado)
    estado.mkdir(parents=True, exist_ok=True)
    (estado / "agente.json").write_text(json.dumps({
        "agent_id": "agt-prueba", "nube": "http://127.0.0.1:9", "nombre": "CI",
        "cuenta": "ci@ejemplo.com", "emparejado_en": time.time()}), encoding="utf-8")
    (estado / "credencial.bin").write_bytes(b"prueba")
    pagina.pedir("abrir_web")
    r["la_misma_ventana_pasa_a_la_web"] = pagina.esperar(lambda: pagina.url().startswith(WEB), 90)
    r["la_web_se_pinta"] = pagina.esperar(
        lambda: len((pagina.evaluar("document.body.innerText") or "").strip()) > 20, 90)
    r["la_web_sabe_que_esta_en_el_programa"] = isinstance(pagina.evaluar("window.__MORGAN_PROGRAMA__"), str)

    # 3. Desde la web, solo lo inofensivo.
    resumen = pagina.pedir("resumen")
    r["la_web_pide_el_estado"] = bool(resumen.get("ok")) and isinstance(resumen.get("valor"), dict)
    r["y_la_version"] = bool(pagina.pedir("programa").get("ok"))
    for orden, argumentos in (("emparejar", {"codigo": "abc"}), ("consultar", {"codigo": "abc"}),
                              ("desemparejar", None), ("estado", None)):
        respuesta = pagina.pedir(orden, argumentos)
        r[f"la_web_no_puede_{orden}"] = respuesta.get("ok") is False and "not allowed" in respuesta.get("error", "")
        if not r[f"la_web_no_puede_{orden}"]:
            print(f"  {orden}: {respuesta}")

    # 4. Una página ajena no se carga en la ventana.
    pagina.evaluar("location.href = 'https://example.com/'; true")
    time.sleep(4)
    r["lo_ajeno_no_se_carga_aqui"] = pagina.url().startswith(WEB)

    # 5. De la web a la pantalla del programa, para emparejar con otra cuenta.
    pagina.pedir("pantalla_emparejar")
    r["la_web_lleva_a_emparejar"] = pagina.esperar(
        lambda: pagina.url().startswith(LOCAL) and "Este PC ya está conectado" in (pagina.evaluar("document.body.innerText") or ""))

    for k, v in r.items():
        print(f"{k:<38} {v}")
    fallos = [k for k, v in r.items() if v is not True]
    if fallos:
        print("Falla: " + ", ".join(fallos))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
