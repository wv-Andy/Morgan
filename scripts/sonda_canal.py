"""
El «agente» de la sonda del canal (3.0-B): mide qué conexión aguanta Render.

Corre en el PC y habla con un **despliegue de prueba** (nunca producción: se niega,
igual que `scripts/carga.py`). El servidor es `src/api/routes/sonda_canal.py`.

Tres experimentos:

    # 1. Latencia de los cuatro canales (unos 3 minutos)
    venv/Scripts/python.exe scripts/sonda_canal.py latencia --url https://morgan-carga.onrender.com

    # 2. Cuánto silencio aguanta cada canal, y si Render se duerme con él abierto
    venv/Scripts/python.exe scripts/sonda_canal.py silencio --minutos 25 --url ...

    # 3. Reinicio de la nube: un socket con reconexión; se reinicia el servicio a mano
    venv/Scripts/python.exe scripts/sonda_canal.py reinicio --minutos 8 --url ...

Cada uno escribe su resultado en JSON junto a lo que imprime (`--salida`).

**La ida y vuelta que cuenta es la del servidor**: desde que le llega la emisión
hasta que le llega la respuesta, con un solo reloj. Lo que ve el cliente incluye el
viaje del `POST` de emisión, que en el agente de verdad no existe.
"""

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

CLAVE = "contrasena-de-la-sonda"


def percentil(valores: list[float], p: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(round(p / 100 * (len(ordenados) - 1))))]


def resumen(valores: list[float]) -> str:
    if not valores:
        return "sin datos"
    return (f"p50 {percentil(valores, 50):.0f} ms · p95 {percentil(valores, 95):.0f} ms · "
            f"mín {min(valores):.0f} · máx {max(valores):.0f} · n={len(valores)}")


async def preparar(http, base: str) -> str:
    """Una cuenta y un token de la sonda en el despliegue de prueba."""
    nombre = f"sonda{int(time.time()) % 10**7}{random.randint(10, 99)}"
    r = await http.post(f"{base}/auth/registro", json={
        "username": nombre, "email": f"{nombre}@ejemplo.co", "password": CLAVE,
    })
    r.raise_for_status()
    r = await http.post(
        f"{base}/auth/tokens", json={"nombre": "sonda del canal", "alcances": ["lectura", "escritura"]},
        headers={"x-morgan-csrf": r.json()["csrf"]},
    )
    r.raise_for_status()
    return r.json()["valor"]


def ws_url(base: str, sesion: str, latido: float) -> str:
    return base.replace("https://", "wss://").replace("http://", "ws://") + f"/sonda-canal/{sesion}/ws?latido={latido:g}"


# --- 1. Latencia -------------------------------------------------------------


async def latencia(base: str, token: str, n: int) -> dict:
    import httpx
    from websockets.asyncio.client import connect

    cabeceras = {"Authorization": f"Bearer {token}"}
    resultado: dict = {}

    async with httpx.AsyncClient(timeout=180, headers=cabeceras) as http:

        async def medir(canal: str, recibir, conectar_ms: float | None):
            locales = []
            for _ in range(n):
                t0 = time.perf_counter()
                ident = (await http.post(f"{base}/sonda-canal/{canal}/emitir")).json()["id"]
                mensaje = await recibir()
                assert mensaje["id"] == ident, (mensaje, ident)
                locales.append((time.perf_counter() - t0) * 1000)
                await contestar(canal, ident)
                # Pausa al azar: la nube no emite al compás del sondeo.
                await asyncio.sleep(random.uniform(1, 3))
            idas = [x["ms"] for x in (await http.get(f"{base}/sonda-canal/{canal}/resultados")).json()["idas_y_vueltas"]]
            resultado[canal] = {"servidor_ms": idas, "cliente_ms": locales, "conectar_ms": conectar_ms}
            print(f"  {canal:<7} ida y vuelta (servidor): {resumen(idas)}", flush=True)
            if conectar_ms is not None:
                print(f"  {'':<7} conectar: {conectar_ms:.0f} ms", flush=True)

        respuestas_ws: dict[str, object] = {}

        async def contestar(canal: str, ident: str):
            if canal == "ws":
                await respuestas_ws["ws"].send(json.dumps({"tipo": "respuesta", "id": ident}))
            else:
                await http.post(f"{base}/sonda-canal/{canal}/respuesta", json={"id": ident, "canal": canal})

        # WebSocket
        t0 = time.perf_counter()
        async with connect(ws_url(base, "ws", 30), additional_headers=cabeceras) as ws:
            conectar = (time.perf_counter() - t0) * 1000
            respuestas_ws["ws"] = ws
            json.loads(await ws.recv())  # inicio

            async def recibir_ws():
                while True:
                    datos = json.loads(await ws.recv())
                    if datos.get("tipo") == "mensaje":
                        return datos

            await medir("ws", recibir_ws, conectar)

        # Streaming
        cola: asyncio.Queue = asyncio.Queue()

        async def leer_flujo():
            async with http.stream("GET", f"{base}/sonda-canal/flujo/flujo?latido=15") as r:
                async for linea in r.aiter_lines():
                    datos = json.loads(linea)
                    if datos.get("tipo") == "mensaje":
                        await cola.put(datos)

        t0 = time.perf_counter()
        lector = asyncio.create_task(leer_flujo())
        await asyncio.sleep(1.5)
        await medir("flujo", cola.get, None)
        lector.cancel()

        # Los dos sondeos corren EN SEGUNDO PLANO, sin saber cuándo emite la nube,
        # y la emisión llega en un momento al azar (ver `medir`). Si se pregunta
        # justo después de emitir, el sondeo corto sale gratis y no mide nada: la
        # primera versión de este script dio 8 ms así.
        async def sondear(canal: str, maximo: float, pausa: float, cola: asyncio.Queue, cuenta: dict):
            while True:
                cuenta["n"] += 1
                m = (await http.get(f"{base}/sonda-canal/{canal}/esperar?max={maximo:g}")).json()["mensaje"]
                if m:
                    await cola.put(m)
                elif pausa:
                    await asyncio.sleep(pausa)

        for canal, maximo, pausa in (("largo", 25, 0), ("corto", 0, 2)):
            cola_sondeo: asyncio.Queue = asyncio.Queue()
            cuenta = {"n": 0}
            sondeo = asyncio.create_task(sondear(canal, maximo, pausa, cola_sondeo, cuenta))
            await medir(canal, cola_sondeo.get, None)
            sondeo.cancel()
            resultado[canal]["peticiones"] = cuenta["n"]
            print(f"  {canal:<7} {cuenta['n']} peticiones para {n} mensajes", flush=True)

    return resultado


# --- 2. Silencio -------------------------------------------------------------


async def silencio(base: str, token: str, minutos: float) -> dict:
    """Varias conexiones a la vez, y cuánto dura cada una sin tráfico (o con latido)."""
    import httpx
    from websockets.asyncio.client import connect

    cabeceras = {"Authorization": f"Bearer {token}"}
    fin = time.monotonic() + minutos * 60
    registros: list[dict] = []

    def anotar(variante: str, inicio: float, final: str):
        registro = {"variante": variante, "duracion_s": round(time.monotonic() - inicio, 1),
                    "final": final, "hora": time.strftime("%H:%M:%S")}
        registros.append(registro)
        print(f"  [{registro['hora']}] {variante}: {registro['duracion_s']} s → {final}", flush=True)

    async def socket(latido: float, contesta: bool):
        variante = f"ws latido={latido:g} {'contesta' if contesta else 'callado'}"
        while time.monotonic() < fin:
            inicio = time.monotonic()
            try:
                async with connect(ws_url(base, f"s{int(latido)}{int(contesta)}", latido),
                                   additional_headers=cabeceras, ping_interval=None) as ws:
                    while time.monotonic() < fin:
                        try:
                            datos = json.loads(await asyncio.wait_for(ws.recv(), timeout=fin - time.monotonic()))
                        except asyncio.TimeoutError:
                            anotar(variante, inicio, "sigue viva al acabar la prueba")
                            return
                        if contesta and datos.get("tipo") == "latido":
                            await ws.send(json.dumps({"tipo": "pong"}))
            except Exception as exc:
                codigo = getattr(getattr(exc, "rcvd", None), "code", None)
                anotar(variante, inicio, f"cortada: {type(exc).__name__} {codigo or ''}".strip())
                await asyncio.sleep(2)

    async def flujo(latido: float):
        variante = f"flujo latido={latido:g}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None), headers=cabeceras) as http:
            while time.monotonic() < fin:
                inicio = time.monotonic()
                try:
                    async with http.stream("GET", f"{base}/sonda-canal/f{int(latido)}/flujo?latido={latido:g}") as r:
                        async def leer():
                            async for _ in r.aiter_lines():
                                pass
                        await asyncio.wait_for(leer(), timeout=fin - time.monotonic())
                        anotar(variante, inicio, f"el servidor la cerró ({r.status_code})")
                except asyncio.TimeoutError:
                    anotar(variante, inicio, "sigue viva al acabar la prueba")
                    return
                except Exception as exc:
                    anotar(variante, inicio, f"cortada: {type(exc).__name__}")
                    await asyncio.sleep(2)

    async def largo(maximo: float):
        variante = f"sondeo largo max={maximo:g}"
        async with httpx.AsyncClient(timeout=maximo + 30, headers=cabeceras) as http:
            vueltas = cortes = 0
            while time.monotonic() < fin:
                inicio = time.monotonic()
                try:
                    r = await http.get(f"{base}/sonda-canal/l{int(maximo)}/esperar?max={maximo:g}")
                    if r.status_code == 200:
                        vueltas += 1
                    else:
                        cortes += 1
                        anotar(variante, inicio, f"respuesta {r.status_code}")
                except Exception as exc:
                    cortes += 1
                    anotar(variante, inicio, f"cortada: {type(exc).__name__}")
            registros.append({"variante": variante, "vueltas_completas": vueltas, "cortes": cortes})
            print(f"  {variante}: {vueltas} vueltas completas, {cortes} cortes", flush=True)

    await asyncio.gather(
        socket(0, False),     # sin latidos: ¿cuánto silencio aguanta el camino?
        socket(30, True),     # latido cada 30 s: ¿vive toda la prueba?
        flujo(0),
        flujo(30),
        largo(60),
        largo(100),
        largo(140),
    )
    return {"minutos": minutos, "registros": registros}


# --- 3. Reinicio -------------------------------------------------------------


async def reinicio(base: str, token: str, minutos: float) -> dict:
    """Un socket que se reconecta con backoff exponencial y jitter; se anotan cortes y vueltas."""
    from websockets.asyncio.client import connect

    cabeceras = {"Authorization": f"Bearer {token}"}
    fin = time.monotonic() + minutos * 60
    eventos: list[dict] = []
    espera = 1.0
    caida: float | None = None

    def anotar(que: str, **datos):
        evento = {"hora": time.strftime("%H:%M:%S"), "que": que, **datos}
        eventos.append(evento)
        print(f"  [{evento['hora']}] {que} {datos if datos else ''}", flush=True)

    while time.monotonic() < fin:
        t0 = time.monotonic()
        try:
            async with connect(ws_url(base, "reinicio", 15), additional_headers=cabeceras,
                               ping_interval=None, open_timeout=30) as ws:
                if caida is not None:
                    anotar("reconectado", sin_canal_s=round(time.monotonic() - caida, 1),
                           conectar_ms=round((time.monotonic() - t0) * 1000))
                    caida = None
                else:
                    anotar("conectado")
                espera = 1.0
                while time.monotonic() < fin:
                    datos = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                    if datos.get("tipo") == "latido":
                        await ws.send(json.dumps({"tipo": "pong"}))
        except Exception as exc:
            if caida is None:
                caida = time.monotonic()
                codigo = getattr(getattr(exc, "rcvd", None), "code", None)
                anotar("cortado", motivo=f"{type(exc).__name__} {codigo or ''}".strip())
            # Backoff exponencial con jitter, como el agente de verdad (§27).
            await asyncio.sleep(espera + random.uniform(0, espera / 2))
            espera = min(espera * 2, 30)
    return {"minutos": minutos, "eventos": eventos}


def main() -> None:
    # La consola de Windows no es UTF-8 por defecto: un «→» tumbaba la prueba entera
    # a mitad de una medición de 25 minutos.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("experimento", choices=["latencia", "silencio", "reinicio"])
    parser.add_argument("--url", required=True, help="el despliegue de prueba (nunca producción)")
    parser.add_argument("--n", type=int, default=30, help="mensajes por canal en `latencia`")
    parser.add_argument("--minutos", type=float, default=25)
    parser.add_argument("--salida", default=None, help="fichero JSON con el resultado")
    args = parser.parse_args()

    import httpx

    from scripts.carga import comprobar_despliegue_de_prueba

    base = comprobar_despliegue_de_prueba(args.url)

    async def correr():
        async with httpx.AsyncClient(timeout=120) as http:
            token = await preparar(http, base)
        print(f"Cuenta y token de la sonda listos. Experimento: {args.experimento}", flush=True)
        if args.experimento == "latencia":
            return await latencia(base, token, args.n)
        if args.experimento == "silencio":
            return await silencio(base, token, args.minutos)
        return await reinicio(base, token, args.minutos)

    resultado = asyncio.run(correr())
    if args.salida:
        Path(args.salida).write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Resultado en {args.salida}")


if __name__ == "__main__":
    main()
