"""
Cuánto tarda un turno de verdad, por tipo (2.3-D). Resultados en docs/mediciones.md.

La prueba de carga (`scripts/carga.py`) mide a Morgan con un modelo simulado. Esto
mide lo contrario: **turnos reales, con el modelo real**, para poner objetivos de
tiempo que salgan de medir y no de desearlos.

Tres tipos, que son los que distingue el plan:

- **simple**: una pregunta que no necesita herramientas.
- **herramienta**: una que necesita buscar en internet.
- **plan**: una que pide proponer un plan antes de actuar.

El tipo que cuenta es **el que pasó**, no el que se pidió: el modelo puede no usar
la herramienta, y entonces el turno es simple aunque la pregunta no lo fuera.

Aislado como las demás mediciones:

- **Nunca OpenAI**, que es de pago: su clave se vacía y el orden es `groq,gemini`.
  Se comprueba en la cadena montada **antes** de enviar nada.
- Base temporal y sin Supabase: se comprueba `has_supabase=False`.
- Modo nube, que es el que usa la gente: el mismo catálogo de 29 herramientas.
- Pausa entre turnos (`--pausa`, 20 s por defecto): Groq tiene un límite por
  minuto, y sin pausa se mide la cola y no el turno.

Uso:

    venv/Scripts/python.exe scripts/medir_turnos.py --veces 5
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

PREGUNTAS = {
    "simple": "Hola. En dos frases: ¿qué cosas puedes hacer por mí?",
    "herramienta": "Busca en internet cuál es la capital de Australia y contéstame en una frase.",
    "plan": ("Quiero ordenar mis apuntes en tres carpetas por asignatura. Antes de hacer "
             "nada, crea un plan con los pasos para que yo lo apruebe."),
}

# Todo lo que no es el modelo gratuito se vacía. Vacío y no borrado:
# load_dotenv() volvería a leer lo que falte.
VACIAR = (
    "OPENAI_API_KEY", "NVIDIA_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SECRET_KEY",
    "MORGAN_API_TOKEN", "MORGAN_OWNER_EMAIL", "MORGAN_EMAIL_API", "MORGAN_EMAIL_API_KEY",
    "MORGAN_SMTP_HOST", "MORGAN_SMTP_PASSWORD", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "MORGAN_SECRET_KEY",
)


def aislar(directorio: str) -> None:
    for nombre in VACIAR:
        os.environ[nombre] = ""
        os.environ[nombre.lower()] = ""
    os.environ.update({
        "MORGAN_ENVIRONMENT": "cloud",
        "MORGAN_REQUIRE_AUTH": "true",
        "MORGAN_CLOUD_ENABLED": "false",
        "MORGAN_SERVE_WEB": "false",
        "MORGAN_LLM_ORDER": "groq,gemini",
        "MORGAN_DATA_DIR": str(Path(directorio) / "data"),
        "MORGAN_LOG_DIR": str(Path(directorio) / "logs"),
        "MORGAN_LOG_LEVEL": "WARNING",
        "MORGAN_CUPO_GLOBAL_MENSAJES": "0",
    })


def p(valores: list[float], q: float) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(round(q * (len(ordenados) - 1))))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--veces", type=int, default=5, help="turnos por tipo")
    parser.add_argument("--pausa", type=float, default=20.0, help="segundos entre turnos")
    parser.add_argument("--salida", type=Path, help="JSON con cada turno")
    parser.add_argument("--tipos", default=",".join(PREGUNTAS),
                        help="cuáles medir, separados por comas (simple,herramienta,plan)")
    parser.add_argument("--objetivos", action="store_true",
                        help="salir con error si algún p95 pasa de su objetivo (src/observabilidad.py)")
    args = parser.parse_args()

    directorio = tempfile.mkdtemp(prefix="morgan-turnos-")
    aislar(directorio)

    from fastapi.testclient import TestClient

    from src.api import dependencies
    from src.api.app import create_app
    from src.config import get_settings

    ajustes = get_settings()
    assert not ajustes.has_supabase, "ABORTADO: la configuración ve un Supabase"
    assert "openai" not in ajustes.llm_order, "ABORTADO: OpenAI está en el orden"
    app = create_app()
    contenedor = dependencies.get_container()
    assert "Supabase" not in type(contenedor.repositories).__name__, "ABORTADO: repositorios remotos"
    cadena = contenedor.agent.model.model_name
    # El eslabón de pago se llama «OpenAI:…». No vale buscar «openai» a secas: el
    # modelo principal de Groq se llama «openai/gpt-oss-120b» y corre en Groq.
    assert "OpenAI:" not in cadena, f"ABORTADO: OpenAI en la cadena montada ({cadena})"
    print(f"Aislado en {directorio}\nCadena: {cadena}\n", flush=True)

    web = TestClient(app, base_url="https://testserver")
    r = web.post("/auth/registro", json={
        "username": "medicion", "email": "medicion@ejemplo.co", "password": "contrasena-larga",
    })
    r.raise_for_status()
    web.headers["X-Morgan-CSRF"] = r.json()["csrf"]

    turnos = []
    tipos = [t for t in args.tipos.split(",") if t in PREGUNTAS]
    orden = [t for _ in range(args.veces) for t in tipos]  # intercalados
    for n, pedido in enumerate(orden):
        if n:
            time.sleep(args.pausa)
        t0 = time.perf_counter()
        r = web.post("/chat/stream", json={"message": PREGUNTAS[pedido], "session_id": f"m{n}"})
        muro = time.perf_counter() - t0
        eventos = [json.loads(linea) for linea in r.text.splitlines() if linea.strip()]
        fin = eventos[-1] if eventos else {}
        herramientas = [e["nombre"] for e in eventos if e.get("tipo") == "herramienta" and e.get("estado") == "empieza"]
        paso = "plan" if "create_plan" in herramientas else "herramienta" if herramientas else "simple"
        etapas = fin.get("etapas") or {}
        turno = {
            "pedido": pedido, "paso": paso, "ok": fin.get("tipo") == "fin",
            "segundos": fin.get("elapsed_seconds", muro), "herramientas": herramientas,
            "modelo_ms": etapas.get("modelo_ms"), "llamadas": etapas.get("modelo_veces", 1),
            "respaldo": bool(etapas.get("respaldo")), "respondio": fin.get("model"),
        }
        turnos.append(turno)
        print(f"{n + 1:>2}/{len(orden)} {pedido:<11} → {paso:<11} {turno['segundos']:6.2f} s "
              f"modelo {turno['modelo_ms'] or 0:>6} ms x{turno['llamadas']} "
              f"{'RESPALDO ' if turno['respaldo'] else ''}{'' if turno['ok'] else 'FALLO'} {herramientas}",
              flush=True)

    from src.observabilidad import OBJETIVOS_TURNO

    print("\nPor lo que pasó de verdad (todos, respaldos incluidos: es lo que vive la persona):")
    incumplidos = []
    for tipo in PREGUNTAS:
        tiempos = [t["segundos"] for t in turnos if t["paso"] == tipo and t["ok"]]
        if tiempos:
            p95 = p(tiempos, .95)
            objetivo = OBJETIVOS_TURNO[tipo]
            marca = "ok" if p95 <= objetivo else "INCUMPLE"
            if p95 > objetivo:
                incumplidos.append(tipo)
            print(f"  {tipo:<11} n={len(tiempos):<2} p50={statistics.median(tiempos):5.2f} s "
                  f"p95={p95:5.2f} s max={max(tiempos):5.2f} s  objetivo {objetivo:.0f} s {marca}")
    print(f"  aparte: {sum(t['respaldo'] for t in turnos)} con respaldo, "
          f"{sum(not t['ok'] for t in turnos)} fallidos (no entran en los percentiles)")

    if args.salida:
        args.salida.write_text(json.dumps(turnos, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.objetivos and incumplidos:
        raise SystemExit(f"Fuera de objetivo: {', '.join(incumplidos)}")


if __name__ == "__main__":
    main()
