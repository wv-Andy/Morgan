"""
Cuándo toca una automatización (4.14). Puro: sin base, sin red, sin reloj propio (el
momento se pasa), para poder probarlo con cualquier fecha, cambios de hora incluidos.

Un horario es un diccionario pequeño, el mismo que propone el modelo y que se guarda:

- `{"tipo": "diaria", "hora": "09:00"}`
- `{"tipo": "semanal", "dias": [0, 2], "hora": "08:30"}` (0 = lunes … 6 = domingo)
- `{"tipo": "cada_horas", "cada": 3}`

Las horas son **de la zona de la persona** (la de su navegador al crearla): «a las 9» es a
las 9 de su reloj, también tras un cambio de hora. Frecuencia mínima, **cada hora** (límite
de partida aprobado por mí).
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIPOS = ("diaria", "semanal", "cada_horas")
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
#: Cada hora como mínimo; como mucho, una vez por semana en `cada_horas`.
CADA_MIN, CADA_MAX = 1, 168
_HORA = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def zona(nombre: str | None) -> ZoneInfo:
    """La zona por su nombre IANA; si no vale, UTC (y quien la guarde lo dice)."""
    try:
        return ZoneInfo(str(nombre or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def zona_valida(nombre: str | None) -> str | None:
    """El nombre, si es una zona que existe; si no, `None`."""
    if not nombre or not isinstance(nombre, str) or len(nombre) > 64:
        return None
    try:
        ZoneInfo(nombre)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return nombre


def validar(horario) -> tuple[dict | None, str | None]:
    """El horario normalizado, o el motivo de que no valga (escrito para la persona)."""
    if not isinstance(horario, dict):
        return None, "Falta el horario: diaria (hora), semanal (dias y hora) o cada_horas (cada)."
    tipo = str(horario.get("tipo") or "").strip().lower()
    if tipo not in TIPOS:
        return None, "El horario tiene que ser diaria, semanal o cada_horas."
    if tipo == "cada_horas":
        try:
            cada = int(horario.get("cada"))
        except (TypeError, ValueError):
            return None, "Falta cada cuántas horas (cada)."
        if not CADA_MIN <= cada <= CADA_MAX:
            return None, f"Como mucho, una vez por hora; y como poco, cada {CADA_MAX} horas."
        return {"tipo": tipo, "cada": cada}, None
    coincide = _HORA.match(str(horario.get("hora") or "").strip())
    if not coincide:
        return None, "Falta la hora, como HH:MM (por ejemplo 09:00)."
    hora = f"{int(coincide.group(1)):02d}:{coincide.group(2)}"
    if tipo == "diaria":
        return {"tipo": tipo, "hora": hora}, None
    dias = horario.get("dias")
    if not isinstance(dias, list) or not dias:
        return None, "Faltan los días de la semana (dias: 0 = lunes … 6 = domingo)."
    try:
        dias = sorted({int(d) for d in dias})
    except (TypeError, ValueError):
        return None, "Los días van como números: 0 = lunes … 6 = domingo."
    if any(not 0 <= d <= 6 for d in dias):
        return None, "Los días van del 0 (lunes) al 6 (domingo)."
    return {"tipo": tipo, "dias": dias, "hora": hora}, None


def siguiente(horario: dict, nombre_zona: str | None, despues: float) -> float:
    """El siguiente momento (epoch, UTC) **estrictamente posterior** a `despues`."""
    if horario["tipo"] == "cada_horas":
        # Al minuto en punto: el reloj mira cada minuto.
        return float(int(despues // 60) * 60 + horario["cada"] * 3600)
    z = zona(nombre_zona)
    local = datetime.fromtimestamp(despues, tz=timezone.utc).astimezone(z)
    h, m = (int(x) for x in horario["hora"].split(":"))
    dias = range(7) if horario["tipo"] == "diaria" else horario["dias"]
    for adelante in range(0, 9):
        dia = (local + timedelta(days=adelante)).date()
        if dia.weekday() not in dias:
            continue
        candidato = datetime(dia.year, dia.month, dia.day, h, m, tzinfo=z).timestamp()
        if candidato > despues:
            return float(candidato)
    raise ValueError("horario sin ningún día")  # validar() no deja llegar aquí


def describir(horario: dict) -> str:
    """En palabras: «cada día a las 09:00», «los lunes y miércoles a las 08:30»…"""
    if horario["tipo"] == "cada_horas":
        return "cada hora" if horario["cada"] == 1 else f"cada {horario['cada']} horas"
    if horario["tipo"] == "diaria":
        return f"cada día a las {horario['hora']}"
    nombres = [DIAS[d] for d in horario["dias"]]
    if len(nombres) == 7:
        return f"cada día a las {horario['hora']}"
    lista = nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]
    plural = lista.replace("sábado", "sábados").replace("domingo", "domingos")
    return f"los {plural} a las {horario['hora']}"
