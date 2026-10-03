"""Espacios de trabajo de Morgan (V2.2).

Un espacio agrupa conversaciones, archivos y documentos de conocimiento, y tiene
unas instrucciones propias que Morgan tiene en cuenta en cada turno. La memoria
sobre la persona **no** es de ningún espacio: es común a todos.

Ver `docs/datos.md`.

Aquí solo se exportan el contexto y el modelo. Los repositorios se importan desde
`src.espacios.repositorio` a propósito: importan la capa de Supabase, y la capa de
Supabase importa el contexto de este paquete. Exportarlos desde aquí cerraría el
ciclo.
"""

from src.espacios.contexto import (
    en_espacio,
    espacio_actual,
    fijar_espacio,
    restaurar_espacio,
)
from src.espacios.modelos import (
    MAX_INSTRUCCIONES,
    MAX_NOMBRE,
    Espacio,
    EspacioDuplicado,
    EspacioInvalido,
)

__all__ = [
    "MAX_INSTRUCCIONES",
    "MAX_NOMBRE",
    "Espacio",
    "EspacioDuplicado",
    "EspacioInvalido",
    "en_espacio",
    "espacio_actual",
    "fijar_espacio",
    "restaurar_espacio",
]
