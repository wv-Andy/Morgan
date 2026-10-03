"""
Validación de los argumentos propuestos para una herramienta.

Vive fuera del agente porque la API también ejecuta herramientas directamente
(`POST /tools/{nombre}`) y debe aplicar exactamente las mismas comprobaciones.
"""

from typing import Any

# Tipos de Python aceptados para cada tipo de JSON Schema.
TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def validate_tool_args(tool, args: dict[str, Any]) -> str | None:
    """Comprueba obligatorios, tipos y argumentos desconocidos.

    Devuelve el mensaje de error, o None si los argumentos son válidos.
    """
    schema = tool.parameters or {}
    properties = schema.get("properties", {}) or {}
    required = schema.get("required", []) or []

    for field in required:
        if field not in args:
            return f"Falta el parámetro obligatorio '{field}'."

    for name, value in args.items():
        definition = properties.get(name)
        if definition is None:
            return (
                f"El parámetro '{name}' no existe en esta herramienta. "
                f"Parámetros válidos: {', '.join(properties) or 'ninguno'}."
            )

        if value is None:
            continue

        expected = definition.get("type")
        accepted = TYPE_CHECKS.get(expected)
        if accepted is None:
            continue

        # En Python bool es subclase de int: sin esta comprobación, timeout=True
        # llegaría a subprocess.run como un 1 silencioso.
        if expected in ("integer", "number") and isinstance(value, bool):
            return f"El parámetro '{name}' debe ser de tipo {expected}, no booleano."

        if not isinstance(value, accepted):
            return (
                f"El parámetro '{name}' debe ser de tipo {expected}, "
                f"pero se recibió {type(value).__name__}."
            )

    return None
