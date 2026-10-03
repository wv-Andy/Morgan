"""
Pruebas de carga de Morgan (2.3-C). Resultados en docs/mediciones.md, apartado 4.

La pregunta: ¿cuántas personas a la vez aguanta el servidor antes de degradarse, y
qué se rompe primero? Lo que se mide es **Morgan** —cuentas, base, sesiones,
streaming—, no el modelo:

- El servidor corre en **otro proceso**, como en Render: un solo uvicorn, un worker.
  Medir con cliente y servidor en el mismo proceso mediría también el GIL del cliente.
- **Aislado de todo lo real.** Se vacían los secretos (Supabase, modelos, correo),
  se usa un directorio temporal y el servidor **comprueba** `has_supabase=False` y
  que los repositorios no son los de Supabase antes de aceptar una petición. Un
  script que creía estar aislado escribió en producción el 2026-09-16.
- **Modelo simulado** con la latencia medida de Groq por llamada. Un turno con
  herramienta hace dos llamadas, como el real.
- Cada escalón arranca un servidor nuevo con su base vacía.

Uso:

    venv/Scripts/python.exe scripts/carga.py                  # 1 5 10 25 50
    venv/Scripts/python.exe scripts/carga.py 25 50 100 --turnos 4 --latencia 0.6

**Contra un despliegue de prueba** (`--url`): el servicio aparte de Render con su
propio Supabase, arrancado con `MORGAN_PRUEBA_DE_CARGA=1` (src/prueba_de_carga.py).
Dos salvaguardas antes de enviar nada: se niega a cualquier dirección de producción,
y exige que `/status` diga que el modelo es el simulado. Ahí no se mide CPU ni
memoria desde aquí: se leen en el panel de Render.

    venv/Scripts/python.exe scripts/carga.py 1 5 10 25 --url https://morgan-carga.onrender.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

SECRETOS = (
    "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SECRET_KEY",
    "GROQ_API_KEY", *(f"GROQ_API_KEY_{n}" for n in range(2, 10)),
    "GEMINI_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY",
    "MORGAN_EMAIL_API", "MORGAN_EMAIL_API_KEY", "MORGAN_SMTP_HOST", "MORGAN_SMTP_PASSWORD",
    "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    "MORGAN_API_TOKEN", "MORGAN_OWNER_EMAIL", "MORGAN_SECRET_KEY", "MORGAN_API_URL",
)
CLAVE = "contrasena-de-carga"


# ── El servidor ────────────────────────────────────────────────────────────────


def entorno_aislado(directorio: str, puerto: int) -> dict:
    """Vacíos y no borrados: `load_dotenv()` volvería a leer los que falten."""
    entorno = dict(os.environ)
    for nombre in SECRETOS:
        entorno[nombre] = ""
        entorno[nombre.lower()] = ""
    entorno.update({
        "MORGAN_ENVIRONMENT": "cloud",
        "MORGAN_REQUIRE_AUTH": "true",
        "MORGAN_REGISTRO_ABIERTO": "true",
        "MORGAN_CLOUD_ENABLED": "false",
        "MORGAN_SERVE_WEB": "false",
        "MORGAN_DATA_DIR": str(Path(directorio) / "data"),
        "MORGAN_LOG_DIR": str(Path(directorio) / "logs"),
        "MORGAN_LOG_LEVEL": "WARNING",
        # El cupo global (150 al día) pararía la medición a los pocos escalones.
        # Es un techo de producto, no de rendimiento: se anota aparte.
        "MORGAN_CUPO_GLOBAL_MENSAJES": "0",
        "PYTHONUNBUFFERED": "1",
    })
    return entorno


def servir(puerto: int, latencia: float) -> None:
    """Lo que corre en el proceso del servidor."""
    sys.path.insert(0, str(RAIZ))
    import threading
    import uuid

    import uvicorn

    from src.api import dependencies
    from src.config import get_settings
    from src.identidad import cuentas
    from src.models.base import LLMResponse, ToolCallRequest
    from src.models.mock import MockLLMProvider

    class ModeloConLatencia(MockLLMProvider):
        """Sin estado compartido entre turnos: decide mirando solo los mensajes."""

        def generate(self, messages, tools=None, system_prompt=None):
            time.sleep(latencia)
            ultimo = messages[-1]
            if ultimo.role == "user" and "recuerda" in (ultimo.content or "").lower():
                return LLMResponse(type="tool_call", tool_calls=[ToolCallRequest(
                    name="remember_fact", id=uuid.uuid4().hex,
                    arguments={"key": f"dato-{uuid.uuid4().hex[:8]}", "value": "verde",
                               "category": "preference"},
                )])
            return LLMResponse(type="text", content="Hecho. " + "texto de relleno " * 40)

    # Veinte altas cada quince minutos es el freno del registro abierto: con 50
    # personas a la vez no se llegaría a medir el chat. Se anota aparte.
    cuentas.MAX_REGISTROS_EN_TOTAL = 10**6
    cuentas.MAX_REGISTROS_POR_ORIGEN = 10**6
    dependencies.build_provider_chain = lambda settings: (ModeloConLatencia(), ["simulado"])

    ajustes = get_settings()
    assert not ajustes.has_supabase, "ABORTADO: la configuración ve un Supabase"
    contenedor = dependencies.get_container()
    assert "Supabase" not in type(contenedor.repositories).__name__, "ABORTADO: repositorios remotos"
    assert isinstance(contenedor.agent.model, ModeloConLatencia), "ABORTADO: modelo real"
    print(f"AISLADO data={ajustes.data_dir} hilos={threading.active_count()}", flush=True)

    from src.api.app import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=puerto, log_level="warning")


# ── Los clientes ───────────────────────────────────────────────────────────────


def p(valores: list[float], q: float) -> float:
    if not valores:
        return float("nan")
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(round(q * (len(ordenados) - 1))))]


#: Cada ejecución da de alta nombres nuevos. En local la base es nueva en cada
#: escalón, pero un despliegue de prueba la conserva, y repetir `carga0` fallaría.
PREFIJO = f"c{int(time.time()) % 10**7}"


async def registrar(http, base: str, n: int, datos: dict, escalon: int = 0) -> dict | None:
    import httpx

    origen = {"X-Forwarded-For": f"10.0.{n // 250}.{n % 250 + 1}"}
    nombre = f"{PREFIJO}e{escalon}p{n}"
    t0 = time.perf_counter()
    try:
        r = await http.post(f"{base}/auth/registro", headers=origen, json={
            "username": nombre, "email": f"{nombre}@ejemplo.co", "password": CLAVE,
        })
    except httpx.HTTPError as exc:
        datos["errores"].append(f"registro: {type(exc).__name__}")
        return None
    datos["registro"].append(time.perf_counter() - t0)
    if r.status_code != 200:
        datos["errores"].append(f"registro {r.status_code}")
        return None
    # Las cookies son `Secure` en modo nube y esto es http: se mandan a mano.
    cabeceras = {
        **origen,
        "X-Morgan-CSRF": r.json()["csrf"],
        "Cookie": "; ".join(f"{k}={v}" for k, v in r.cookies.items()),
    }
    return cabeceras


async def conversar(http, base: str, n: int, cabeceras: dict | None, turnos: int, datos: dict) -> None:
    import httpx

    if cabeceras is None:
        return

    for turno in range(turnos):
        mensaje = "recuerda que mi color es verde" if turno % 2 else "hola, ¿qué tal?"
        tipo = "herramienta" if turno % 2 else "simple"
        t0 = time.perf_counter()
        primero = None
        final = None
        try:
            async with http.stream("POST", f"{base}/chat/stream", headers=cabeceras,
                                   json={"message": mensaje, "session_id": f"c{n}"}) as resp:
                if resp.status_code != 200:
                    datos["errores"].append(f"stream {resp.status_code}")
                    continue
                async for linea in resp.aiter_lines():
                    if not linea.strip():
                        continue
                    if primero is None:
                        primero = time.perf_counter() - t0
                    final = json.loads(linea)
                    if final.get("tipo") == "herramienta" and final.get("estado") == "ok":
                        datos["herramientas_ok"].append(1)
        except httpx.HTTPError as exc:
            datos["errores"].append(f"stream: {type(exc).__name__}")
            continue
        total = time.perf_counter() - t0
        if not final or final.get("tipo") != "fin":
            datos["errores"].append(f"turno sin fin: {(final or {}).get('tipo')}")
            continue
        datos["primer_evento"].append(primero)
        datos[f"turno_{tipo}"].append(total)

        # Lo que hace la web al terminar un turno: recargar la lista.
        t0 = time.perf_counter()
        try:
            r = await http.get(f"{base}/sessions", headers=cabeceras)
            if r.status_code == 200:
                datos["lista"].append(time.perf_counter() - t0)
            else:
                datos["errores"].append(f"sessions {r.status_code}")
        except httpx.HTTPError as exc:
            datos["errores"].append(f"sessions: {type(exc).__name__}")


async def escalon(base: str, personas: int, turnos: int, proceso) -> dict:
    import httpx
    import psutil

    datos = {k: [] for k in ("registro", "primer_evento", "turno_simple",
                             "turno_herramienta", "lista", "errores", "herramientas_ok", "health")}
    # En Windows, el python.exe de un venv es un lanzador que arranca el
    # intérprete de verdad como hijo: se mide el árbol entero. Contra un
    # despliegue remoto no hay proceso que medir desde aquí.
    raiz = psutil.Process(proceso.pid) if proceso is not None else None

    def arbol():
        return [raiz, *raiz.children(recursive=True)] if raiz is not None else []

    pico = {"rss": 0, "hilos": 0}
    parar = asyncio.Event()

    async def vigilar():
        while not parar.is_set():
            try:
                procesos = arbol()
                pico["rss"] = max(pico["rss"], sum(x.memory_info().rss for x in procesos))
                pico["hilos"] = max(pico["hilos"], sum(x.num_threads() for x in procesos))
            except psutil.Error:
                pass
            await asyncio.sleep(0.1)

    def cpu():
        return sum(x.cpu_times().user + x.cpu_times().system for x in arbol())

    limites = httpx.Limits(max_connections=personas * 2 + 10, max_keepalive_connections=personas * 2)
    vigia = asyncio.create_task(vigilar())
    async with httpx.AsyncClient(timeout=200, limits=limites) as http:
        # Primero todas las altas y después todas las conversaciones: el hash de
        # la contraseña es caro a propósito y mezclado ocultaría lo que cuesta un turno.
        cpu0 = cpu()
        sesiones = await asyncio.gather(*(registrar(http, base, n, datos, personas) for n in range(personas)))
        cpu1 = cpu()
        await asyncio.sleep(0.3)
        datos["rss_registro"] = pico["rss"]
        pico["rss"] = 0
        # Lo que hace Render cada pocos segundos: si /health no contesta a
        # tiempo, reinicia el servicio con todas las conversaciones dentro.
        async def sondear_health():
            while not fin_del_chat.is_set():
                t = time.perf_counter()
                try:
                    r = await http.get(f"{base}/health", timeout=30)
                    datos["health"].append(time.perf_counter() - t)
                    if r.status_code != 200:
                        datos["errores"].append(f"health {r.status_code}")
                except httpx.HTTPError as exc:
                    datos["errores"].append(f"health: {type(exc).__name__}")
                await asyncio.sleep(0.5)

        fin_del_chat = asyncio.Event()
        sonda = asyncio.create_task(sondear_health())
        t0 = time.perf_counter()
        await asyncio.gather(*(conversar(http, base, n, c, turnos, datos)
                               for n, c in enumerate(sesiones)))
        duracion = time.perf_counter() - t0
        fin_del_chat.set()
        await sonda
        cpu2 = cpu()
    parar.set()
    await vigia
    datos["cpu_registro_s"] = cpu1 - cpu0
    datos["cpu_s"] = cpu2 - cpu1
    datos["duracion"] = duracion
    datos["pico"] = pico
    return datos


def arrancar(puerto: int, latencia: float, directorio: str):
    proceso = subprocess.Popen(
        [sys.executable, __file__, "--servir", str(puerto), "--latencia", str(latencia)],
        cwd=RAIZ, env=entorno_aislado(directorio, puerto),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace",
    )
    linea = proceso.stdout.readline()
    if not linea.startswith("AISLADO"):
        parar_servidor(proceso)
        raise SystemExit(f"El servidor no confirmó el aislamiento: {linea}{proceso.stdout.read()[-2000:]}")
    # Si nadie lee la tubería, el servidor se bloquea al llenarla con sus avisos
    # y parece que Morgan no aguanta la carga. Se vacía en un hilo y se guarda
    # la cola para enseñarla.
    import collections
    import threading

    proceso.salida = collections.deque(maxlen=200)
    threading.Thread(target=lambda: proceso.salida.extend(proceso.stdout), daemon=True).start()

    import httpx

    for _ in range(200):
        try:
            if httpx.get(f"http://127.0.0.1:{puerto}/health", timeout=1).status_code == 200:
                return proceso, linea.strip()
        except httpx.HTTPError:
            time.sleep(0.1)
    parar_servidor(proceso)
    raise SystemExit("El servidor no respondió a /health")


def parar_servidor(proceso) -> None:
    """El árbol entero: matar solo el lanzador del venv deja el servidor vivo."""
    import psutil

    try:
        hijos = psutil.Process(proceso.pid).children(recursive=True)
    except psutil.Error:
        hijos = []
    for hijo in hijos:
        try:
            hijo.kill()
        except psutil.Error:
            pass
    proceso.kill()
    proceso.wait()
    psutil.wait_procs(hijos, timeout=10)


def resumen(personas: int, turnos: int, d: dict) -> str:
    def fila(nombre, valores):
        return (f"  {nombre:<18} n={len(valores):<4} p50={p(valores, .5):6.2f}s "
                f"p95={p(valores, .95):6.2f}s max={max(valores, default=float('nan')):6.2f}s")

    hechos = len(d["turno_simple"]) + len(d["turno_herramienta"])
    return "\n".join([
        f"── {personas} a la vez · {turnos} turnos cada una · {d['duracion']:.1f} s ──",
        fila("registro", d["registro"]),
        fila("primer evento", d["primer_evento"]),
        fila("turno simple", d["turno_simple"]),
        fila("turno herramienta", d["turno_herramienta"]),
        fila("GET /sessions", d["lista"]),
        fila("GET /health", d["health"]),
        f"  turnos hechos {hechos}/{personas * turnos} · herramientas ok {len(d['herramientas_ok'])} · errores {len(d['errores'])}"
        + (f" {sorted(set(d['errores']))[:5]}" if d["errores"] else ""),
    ] + ([
        f"  CPU del servidor: {1000 * d['cpu_registro_s'] / max(len(d['registro']), 1):.0f} ms por alta, "
        f"{1000 * d['cpu_s'] / max(hechos, 1):.0f} ms por turno (con su GET /sessions)",
        f"  pico RSS en las altas {d['rss_registro'] / 2**20:.0f} MB, en el chat {d['pico']['rss'] / 2**20:.0f} MB · pico hilos {d['pico']['hilos']}",
    ] if d["pico"]["hilos"] else ["  CPU y memoria: en el panel de Render (servidor remoto)"]))


#: Nunca contra esto. Ni por la web ni directo contra Render.
PRODUCCION = ("morgan-ia.vercel.app", "morgan-ia-2-0.onrender.com")


def comprobar_despliegue_de_prueba(url: str) -> str:
    """Devuelve la base si es un despliegue de prueba de verdad; si no, aborta."""
    base = url.rstrip("/")
    # Lo primero, antes de importar o pedir nada.
    if any(host in base for host in PRODUCCION):
        raise SystemExit(f"ABORTADO: {base} es producción. La carga va contra un despliegue aparte.")

    import httpx

    sys.path.insert(0, str(RAIZ))
    from src.prueba_de_carga import NOMBRE_DEL_MODELO
    estado = httpx.get(f"{base}/status", timeout=120).json()
    llm = json.dumps(estado.get("components", {}).get("llm", {}), ensure_ascii=False)
    if NOMBRE_DEL_MODELO not in llm:
        raise SystemExit(
            f"ABORTADO: {base} no dice que su modelo sea el simulado ({llm[:200]}). "
            "Sin MORGAN_PRUEBA_DE_CARGA=1 cada turno gastaría un modelo real."
        )
    print(f"Despliegue de prueba confirmado: {base} · modelo {NOMBRE_DEL_MODELO}", flush=True)
    return base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("personas", nargs="*", type=int, default=[1, 5, 10, 25, 50])
    parser.add_argument("--turnos", type=int, default=4)
    parser.add_argument("--latencia", type=float, default=0.6)
    parser.add_argument("--servir", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--url", help="un despliegue de prueba (nunca producción)")
    args = parser.parse_args()

    if args.servir:
        servir(args.servir, args.latencia)
        return

    if args.url:
        base = comprobar_despliegue_de_prueba(args.url)
        for personas in args.personas:
            d = asyncio.run(escalon(base, personas, args.turnos, None))
            print(resumen(personas, args.turnos, d), flush=True)
        return

    import socket

    print(f"Latencia simulada por llamada al modelo: {args.latencia} s")
    for personas in args.personas:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            puerto = s.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="morgan-carga-") as directorio:
            proceso, confirmacion = arrancar(puerto, args.latencia, directorio)
            try:
                d = asyncio.run(escalon(f"http://127.0.0.1:{puerto}", personas, args.turnos, proceso))
            finally:
                parar_servidor(proceso)
            print(resumen(personas, args.turnos, d), flush=True)
            avisos = [linea.rstrip() for linea in proceso.salida if linea.strip()]
            if avisos:
                print(f"  el servidor escribió {len(avisos)} líneas; las últimas:")
                for linea in avisos[-6:]:
                    print("    " + linea[:200])


if __name__ == "__main__":
    main()
