"""
La prueba de uso comprimida (4.20): en vez de una semana de uso, un recorrido con el modelo
real que dice **cuánto tarda, si acierta y si se queda sin pasos**.

Lo pidió Andy (2026-10-03): *«al buscar en la web o preguntar algo un poco largo Morgan no es
tan exacto y comete errores o dice límite de pasos para la solicitud alcanzado»*. Medido en
producción (30-09): de 30 preguntas, 3 acabaron en el límite de pasos. Esta prueba lo repite
con preguntas **cuya respuesta se puede comprobar** (la capital de Australia tiene que ser
Canberra), así que mide la exactitud además del tiempo.

Aislada como `medir_turnos.py` (de donde saca `aislar`): base temporal, sin Supabase, **nunca
OpenAI** (de pago) y nunca el PC de nadie: lo del PC lo cubren las suites de resistencia.

    venv/Scripts/python.exe scripts/prueba_comprimida.py --salida resultado.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from scripts.medir_turnos import aislar  # noqa: E402

LIMITE = "He alcanzado el límite máximo de pasos"

#: (nombre, sesión, pregunta, qué tiene que decir: cada grupo, al menos una de sus palabras)
PREGUNTAS: list[tuple[str, str, str, list[list[str]]]] = [
    ("simple", "s1", "Hola. En dos frases, ¿qué puedes hacer por mí?", []),
    ("dato corto", "s2", "Busca en internet cuál es la capital de Australia y contéstame en una frase.",
     [["canberra"]]),
    ("dato y autor", "s3", "¿En qué año se publicó «Cien años de soledad» y quién la escribió? Búscalo para confirmarlo.",
     [["1967"], ["garcía márquez", "garcia marquez"]]),
    ("comparar en tabla", "s4", "Busca y compárame Japón y Alemania: la capital, el idioma oficial y la moneda de cada uno, en una tabla.",
     [["tokio", "tokyo"], ["berlín", "berlin"], ["yen"], ["euro"]]),
    ("seguimiento", "s4", "¿Y cuál de los dos países tiene más habitantes?",
     [["japón", "japon"]]),
    ("tres datos", "s5", "Necesito tres datos, búscalos en internet: la altura del monte Everest, el río más largo de Sudamérica y el año en que el ser humano llegó a la Luna.",
     [["8848", "8.848", "8 848", "8,848"], ["amazonas"], ["1969"]]),
    ("explicar con fuentes", "s6", "Investiga en internet y explícame en un párrafo qué es el telescopio James Webb, cuándo se lanzó y qué agencias participan en él.",
     [["2021"], ["nasa"], ["esa", "europea"]]),
    ("leer una página", "s7", "Lee la página https://es.wikipedia.org/wiki/Python y dime en qué año apareció Python y quién lo creó.",
     [["1991"], ["guido", "van rossum"]]),
    ("noticias", "s8", "¿Cuáles son tres noticias importantes de esta semana sobre inteligencia artificial? Dame la fuente de cada una.",
     [["http", "fuente"]]),
    ("redactar", "s9", "Escríbeme un correo formal de tres párrafos pidiendo una reunión con mi profesor.", []),
]


def turno(web, mensaje: str, sesion: str, **extra) -> dict:
    t0 = time.perf_counter()
    r = web.post("/chat/stream", json={"message": mensaje, "session_id": sesion, **extra})
    muro = time.perf_counter() - t0
    eventos = [json.loads(linea) for linea in r.text.splitlines() if linea.strip()]
    fin = eventos[-1] if eventos else {}
    texto = "".join(e.get("texto", "") for e in eventos if e.get("tipo") == "texto") or fin.get("response") or ""
    herramientas = [e["nombre"] for e in eventos if e.get("tipo") == "herramienta" and e.get("estado") == "empieza"]
    vueltas = max([e.get("vuelta", 0) for e in eventos if e.get("tipo") == "pensando"] or [0])
    etapas = fin.get("etapas") or {}
    return {
        "ok": fin.get("tipo") == "fin", "segundos": round(fin.get("elapsed_seconds", muro), 2),
        "texto": texto, "herramientas": herramientas, "vueltas": vueltas,
        "limite": texto.startswith(LIMITE), "respaldo": bool(etapas.get("respaldo")),
        "llamadas": etapas.get("modelo_veces"), "eventos": [e.get("tipo") for e in eventos][-3:],
    }


def acierta(texto: str, esperado: list[list[str]]) -> list[str]:
    """Lo que falta en la respuesta: un grupo sin ninguna de sus palabras."""
    # Acentos compuestos o no, y espacios finos («8\u202f848»): se comparan igual.
    bajo = unicodedata.normalize("NFKC", texto).lower()
    return [" / ".join(g) for g in esperado if not any(p in bajo for p in g)]


def main() -> int:
    lector = argparse.ArgumentParser()
    lector.add_argument("--pausa", type=float, default=15.0, help="segundos entre turnos (límite por minuto de Groq)")
    lector.add_argument("--salida", type=Path)
    args = lector.parse_args()

    directorio = tempfile.mkdtemp(prefix="morgan-prueba-")
    aislar(directorio)

    from fastapi.testclient import TestClient

    from src.api import dependencies
    from src.api.app import create_app
    from src.config import get_settings

    ajustes = get_settings()
    assert not ajustes.has_supabase, "ABORTADO: la configuración ve un Supabase"
    app = create_app()
    contenedor = dependencies.get_container()
    cadena = contenedor.agent.model.model_name
    assert "OpenAI:" not in cadena, f"ABORTADO: OpenAI en la cadena ({cadena})"
    print(f"Aislado en {directorio}\nCadena: {cadena}\n", flush=True)

    web = TestClient(app, base_url="https://testserver")
    r = web.post("/auth/registro", json={"username": "prueba", "email": "prueba@ejemplo.co",
                                         "password": "contrasena-larga"})
    r.raise_for_status()
    web.headers["X-Morgan-CSRF"] = r.json()["csrf"]
    web.headers["X-Morgan-Zona"] = "America/Mexico_City"
    usuario = r.json()["usuario"]["id"]

    resultados = []
    for n, (nombre, sesion, pregunta, esperado) in enumerate(PREGUNTAS):
        if n:
            time.sleep(args.pausa)
        t = turno(web, pregunta, sesion)
        t.update(nombre=nombre, falta=acierta(t["texto"], esperado) if t["ok"] and not t["limite"] else ["(sin respuesta)"] if esperado else [])
        resultados.append(t)
        marca = "LÍMITE" if t["limite"] else ("BIEN" if not t["falta"] else f"FALTA {t['falta']}") if esperado else ("ok" if t["ok"] else "FALLO")
        print(f"{n + 1:>2}. {nombre:<20} {t['segundos']:6.1f} s  vueltas {t['vueltas']}  "
              f"{len(t['herramientas'])} herram. {'RESPALDO ' if t['respaldo'] else ''}{marca}", flush=True)

    # --- Una automatización, por el camino de la web: pedirla, aprobar el plan, ejecutarla ---
    time.sleep(args.pausa)
    auto = {"nombre": "automatización"}
    t = turno(web, "Crea una automatización que cada día a las 9:00 me busque las noticias de tecnología y me las resuma.", "s10")
    planes = web.get("/planes", params={"solo_pendientes": "true", "session_id": "s10"}).json()
    lista = planes.get("planes", planes) if isinstance(planes, dict) else planes
    auto.update(propuesta_s=t["segundos"], plan=bool(lista))
    if lista:
        plan_id = lista[0]["id"]
        web.post(f"/planes/{plan_id}/aprobar").raise_for_status()
        time.sleep(args.pausa)
        t2 = turno(web, "Aprobado.", "s10", ejecutar_plan=plan_id)
        auto["ejecucion_s"] = t2["segundos"]
        from src.automatizacion.repositorio import repositorio_de_automatizaciones

        repo = repositorio_de_automatizaciones(contenedor.repositories)
        creadas = repo.listar(usuario)
        auto["creada"] = bool(creadas)
        if creadas:
            time.sleep(args.pausa)
            a = creadas[0]
            t0 = time.perf_counter()
            contenedor.reloj.ejecutar(dict(a), time.time())
            auto["corrida_s"] = round(time.perf_counter() - t0, 1)
            avisos = repo.avisos(usuario)
            auto["aviso"] = avisos[0]["estado"] if avisos else None
    print(f"11. automatización: {auto}", flush=True)

    # --- Resumen ---
    comprobables = [r for r in resultados if any(p[3] for p in PREGUNTAS if p[0] == r["nombre"])]
    bien = [r for r in comprobables if not r["falta"] and not r["limite"]]
    print(f"\nExactitud: {len(bien)} de {len(comprobables)} con todo lo que tenían que decir")
    print(f"Límite de pasos: {sum(r['limite'] for r in resultados)} de {len(resultados)}")
    print(f"Respaldo: {sum(r['respaldo'] for r in resultados)} · fallos: {sum(not r['ok'] for r in resultados)}")
    tiempos = sorted(r["segundos"] for r in resultados if r["ok"])
    if tiempos:
        print(f"Tiempo: mediana {tiempos[len(tiempos) // 2]:.1f} s · máximo {tiempos[-1]:.1f} s")
    if args.salida:
        args.salida.write_text(json.dumps({"turnos": resultados, "automatizacion": auto},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
