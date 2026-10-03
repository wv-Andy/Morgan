"""
Conocimiento: lo que Morgan puede consultar, frente a lo que recuerda (V1.8).

Hasta aquí, «memoria» servía para dos cosas distintas metidas en la misma caja:

- **Hechos sobre ti.** Que prefieres español, que trabajas en Morgan, que tu editor
  es VS Code. Son pocos, cortos, y viajan en **cada** conversación porque el modelo
  necesita saberlos antes de responder nada.
- **Material que puedes querer consultar.** La documentación de un proyecto, unas
  notas, un manual. Es mucho, largo, y solo hace falta **cuando viene a cuento**.

Meterlo todo en el mismo sitio tiene un coste que no se ve hasta que aparece: el
system prompt engorda con material que casi nunca se usa, y lo que sí importa —los
cuatro hechos que definen cómo tratarte— se diluye entre ellos.

La V1.8 los separa:

    Memoria                          Conocimiento
    ├── quién eres                   ├── documentos
    ├── qué prefieres                ├── fuentes
    ├── decisiones tomadas           ├── colecciones
    └── va en CADA prompt            └── se BUSCA cuando hace falta

## La regla que gobierna esto

> **No convertir automáticamente toda conversación en memoria permanente.**

Es del roadmap y es la que evita que Morgan acumule basura. Lo que se recuerda se
elige; lo que se guarda como conocimiento se añade a propósito. Nada entra solo.
"""

from src.conocimiento.modelos import Documento, Fragmento
from src.conocimiento.almacen import AlmacenDeConocimiento

__all__ = ["AlmacenDeConocimiento", "Documento", "Fragmento"]
