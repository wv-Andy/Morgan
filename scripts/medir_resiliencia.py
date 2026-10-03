"""
Medir la resiliencia del agente real durante horas (3.6.5), sin tocarlo.

Solo **lee**: el registro (`agente.log`), la salud (`salud.json`), el diario y la
auditoría del agente, y los recursos de sus procesos (memoria, CPU, hilos, handles).
Cada `--cada` segundos apunta una muestra en `--salida` (líneas JSON); al acabar, o con
`--resumen`, saca las cifras del plan (§V3.6.5): tiempo de reconexión, reinicios del
vigilante, peticiones fallidas, operaciones duplicadas, órdenes fantasma rechazadas,
conexiones zombi (periodos sin pulso) y consumo.

    .\\venv\\Scripts\\python.exe scripts\\medir_resiliencia.py --horas 8 --salida resiliencia.jsonl
    .\\venv\\Scripts\\python.exe scripts\\medir_resiliencia.py --resumen resiliencia.jsonl
"""

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agente import estado as almacen  # noqa: E402


def _procesos():
    """Los procesos del agente y del vigilante (con sus lanzadores)."""
    import psutil

    encontrados = {"agente": [], "vigilante": []}
    for p in psutil.process_iter(["pid", "cmdline"]):
        linea = " ".join(p.info.get("cmdline") or [])
        if "src.agente conectar" in linea:
            encontrados["agente"].append(p)
        elif "src.agente vigilar" in linea:
            encontrados["vigilante"].append(p)
    return encontrados


def _recursos(procesos) -> dict:
    datos = {"mb": 0.0, "hilos": 0, "handles": 0, "cpu_s": 0.0, "pids": []}
    for p in procesos:
        try:
            with p.oneshot():
                datos["mb"] += p.memory_info().rss / 2**20
                datos["hilos"] += p.num_threads()
                datos["handles"] += getattr(p, "num_handles", lambda: 0)()
                t = p.cpu_times()
                datos["cpu_s"] += t.user + t.system
                datos["pids"].append(p.pid)
        except Exception:
            continue
    datos["mb"] = round(datos["mb"], 1)
    datos["cpu_s"] = round(datos["cpu_s"], 2)
    return datos


def muestra() -> dict:
    carpeta = almacen.carpeta()
    try:
        salud = json.loads((carpeta / "salud.json").read_text(encoding="utf-8"))
    except Exception:
        salud = {}
    procesos = _procesos()
    ahora = time.time()
    pulso = salud.get("pulso")
    return {
        "t": round(ahora, 1),
        "estado": salud.get("estado"),
        "pulso_hace": round(ahora - pulso, 1) if isinstance(pulso, (int, float)) else None,
        "cola": salud.get("cola"),
        "agente": _recursos(procesos["agente"]),
        "vigilante": _recursos(procesos["vigilante"]),
    }


_LINEA = re.compile(r"^\[(\d\d:\d\d:\d\d)\] (.*)$")


def _tamano_registro() -> int:
    try:
        return (almacen.carpeta() / "agente.log").stat().st_size
    except OSError:
        return 0


def _registro_desde(inicio: float, desde_byte: int) -> list[tuple[float, str]]:
    """Las líneas que el agente escribió **después** de empezar a medir.

    Las del registro llevan la hora sin la fecha: contarlas por la hora metía las de días
    anteriores (la primera prueba del medidor vio 49 «reconexiones» en 36 s). Se lee
    desde el byte donde acababa el registro al empezar; si se vació (tope de 1 MB), entero.
    La fecha se deduce: cada vez que la hora retrocede, un día más.
    """
    ruta = almacen.carpeta() / "agente.log"
    try:
        with open(ruta, "rb") as f:
            if ruta.stat().st_size >= desde_byte:
                f.seek(desde_byte)
            texto = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    dia = datetime.fromtimestamp(inicio).date().toordinal()
    fuera, anterior = [], None
    for linea in texto.splitlines():
        m = _LINEA.match(linea)
        if not m:
            continue
        hora = datetime.strptime(m.group(1), "%H:%M:%S").time()
        if anterior is not None and hora < anterior:
            dia += 1
        anterior = hora
        fuera.append((datetime.combine(datetime.fromordinal(dia).date(), hora).timestamp(), m.group(2)))
    return fuera


def resumen(ruta: Path) -> dict:
    muestras = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not muestras:
        return {"muestras": 0}
    inicio, fin = muestras[0]["t"], muestras[-1]["t"]
    eventos = _registro_desde(inicio, muestras[0].get("registro_desde", 0))

    # Reconexiones: de un corte (RECONNECTING/CONNECTING) al siguiente READY.
    reconexiones, cortado_en = [], None
    for cuando, texto in eventos:
        if texto.startswith("RECONNECTING") and cortado_en is None:
            cortado_en = cuando
        elif texto.startswith("READY") and cortado_en is not None:
            reconexiones.append(cuando - cortado_en)
            cortado_en = None
    reinicios = [t for _, t in eventos if t.startswith("vigilante:") and ("atascado" in t or "se cayó" in t)]
    motivos = Counter(re.sub(r"; vuelve en .*$", "", t).replace("RECONNECTING ", "")
                      for _, t in eventos if t.startswith("RECONNECTING"))

    # La auditoría del PC: órdenes, fallos, duplicadas y fantasmas en la ventana.
    try:
        lineas = (almacen.carpeta() / "auditoria.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lineas = []
    ejecutadas, rechazos = Counter(), Counter()
    estados = Counter()
    for linea in lineas:
        try:
            e = json.loads(linea)
            cuando = datetime.strptime(e["momento"], "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            continue
        if not inicio <= cuando <= fin + 60:
            continue
        if e.get("fase") == "ejecutada":
            ejecutadas[e.get("command_id")] += 1
            estados[e.get("estado")] += 1
        elif e.get("fase") == "rechazada":
            rechazos[e.get("motivo")] += 1
    duplicadas = [c for c, n in ejecutadas.items() if c and n > 1]

    memoria = [m["agente"]["mb"] for m in muestras if m["agente"]["pids"]]
    handles = [m["agente"]["handles"] for m in muestras if m["agente"]["pids"]]
    pulsos = [m["pulso_hace"] for m in muestras if m["pulso_hace"] is not None]
    cpu = [m["agente"]["cpu_s"] for m in muestras if m["agente"]["pids"]]
    horas = (fin - inicio) / 3600
    return {
        "horas": round(horas, 2),
        "muestras": len(muestras),
        "sin_agente_en_muestras": sum(1 for m in muestras if not m["agente"]["pids"]),
        "reconexiones": len(reconexiones),
        "reconexion_s": {"p50": round(statistics.median(reconexiones), 1), "max": round(max(reconexiones), 1)} if reconexiones else None,
        "motivos_de_corte": dict(motivos),
        "reinicios_del_vigilante": len(reinicios),
        "pulso_mas_viejo_s": max(pulsos) if pulsos else None,
        "ordenes": dict(estados),
        "rechazadas": dict(rechazos),
        "duplicadas": duplicadas,
        "memoria_mb": {"inicio": memoria[0], "fin": memoria[-1], "max": max(memoria)} if memoria else None,
        "handles": {"inicio": handles[0], "fin": handles[-1], "max": max(handles)} if handles else None,
        "cpu_por_hora_s": round((cpu[-1] - cpu[0]) / horas, 1) if len(cpu) > 1 and horas > 0 and cpu[-1] >= cpu[0] else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--horas", type=float, default=8)
    parser.add_argument("--cada", type=float, default=60)
    parser.add_argument("--salida", type=Path, default=Path("resiliencia.jsonl"))
    parser.add_argument("--resumen", type=Path)
    args = parser.parse_args()
    if args.resumen:
        print(json.dumps(resumen(args.resumen), ensure_ascii=False, indent=2))
        return 0
    fin = time.time() + args.horas * 3600
    primera = True
    with open(args.salida, "a", encoding="utf-8") as f:
        while time.time() < fin:
            datos = muestra()
            if primera:
                datos["registro_desde"] = _tamano_registro()
                primera = False
            f.write(json.dumps(datos, ensure_ascii=False) + "\n")
            f.flush()
            time.sleep(args.cada)
    print(json.dumps(resumen(args.salida), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
