"""
Los pasos fijos de una automatización que cambia cosas (4.15, mis decisiones del
2026-10-01): **pasos pre-aprobados**, verdes y amarillos, que se ejecutan exactamente así
cada vez, sin que el modelo elija nada.

Lo único que cambia de una ejecución a otra son dos marcas que la persona ve escritas en el
plan que aprueba: `{fecha}` (AAAA-MM-DD) y `{hora}` (HH-MM, con guion: vale en un nombre de
archivo), con la fecha y la hora de cada ejecución en su zona. Sin ellas, «comprime Proyectos
en Copias/proyectos.zip» fallaría la segunda vez: Morgan nunca pisa un archivo que existe.
"""

import re
from datetime import datetime

from src.automatizacion import horario

MARCAS = ("{fecha}", "{hora}")
MAX_PASOS = 5
#: Una marca entre llaves. Medido con el modelo real: escribió `{fecha_actual}`, que no se
#: sustituye, y mi registro se llenó de «{fecha_actual} revisado».
#: Solo lo que parece un nombre: un JSON como `{"a":1}` que se quiera escribir no es una marca.
_MARCA = re.compile(r"\{[^\W\d][\w]{0,39}\}")


def marcas_desconocidas(valor) -> set[str]:
    """Las marcas `{…}` que no son `{fecha}` ni `{hora}`, en cualquier argumento."""
    if isinstance(valor, str):
        raras = {m for m in _MARCA.findall(valor) if m not in MARCAS}
        # Las llaves dobles tampoco (medido: `{{fecha}}`, costumbre de las plantillas, dejó
        # «{2026-10-01} revisado» en el registro).
        if any(f"{{{m}}}" in valor for m in MARCAS):
            raras.add("{{…}}")
        return raras
    if isinstance(valor, list):
        return set().union(*(marcas_desconocidas(v) for v in valor)) if valor else set()
    if isinstance(valor, dict):
        return set().union(*(marcas_desconocidas(v) for v in valor.values())) if valor else set()
    return set()


def expandir(valor, momento: float, zona: str | None):
    """Los pasos (o cualquier valor) con `{fecha}` y `{hora}` sustituidas."""
    local = datetime.fromtimestamp(momento, tz=horario.zona(zona))
    if isinstance(valor, str):
        return valor.replace("{fecha}", local.strftime("%Y-%m-%d")).replace("{hora}", local.strftime("%H-%M"))
    if isinstance(valor, list):
        return [expandir(v, momento, zona) for v in valor]
    if isinstance(valor, dict):
        return {k: expandir(v, momento, zona) for k, v in valor.items()}
    return valor


def normalizar(pasos) -> tuple[list[dict] | None, str | None]:
    """Los pasos como se guardan (herramienta, argumentos, descripción), o el motivo de que
    no valgan. Lo que depende del catálogo (riesgo, que exista) lo mira la herramienta."""
    if not isinstance(pasos, list) or not pasos:
        return None, "Faltan los pasos."
    if len(pasos) > MAX_PASOS:
        return None, f"Como mucho {MAX_PASOS} pasos fijos; si hace falta más, que sean dos automatizaciones."
    salida = []
    for i, paso in enumerate(pasos, start=1):
        if not isinstance(paso, dict) or not str(paso.get("herramienta") or "").strip():
            return None, f"El paso {i} no dice qué herramienta usa."
        argumentos = paso.get("argumentos") or {}
        if not isinstance(argumentos, dict):
            return None, f"Los argumentos del paso {i} no valen."
        salida.append({"herramienta": str(paso["herramienta"]).strip(), "argumentos": argumentos,
                       "descripcion": str(paso.get("descripcion") or "").strip()[:200]})
    return salida, None
