"""
Capacidades de un proveedor LLM (V1.4).

Hasta ahora todos los proveedores hacían lo mismo: texto. Con la entrada
multimodal dejan de ser intercambiables —Gemini entiende imágenes y el modelo de
Groq configurado por defecto no—, así que la cadena de respaldo ya no puede
conmutar a ciegas: enviar una imagen a un proveedor sin visión no da una respuesta
peor, da un error.

El valor por defecto es **solo texto**. Es la misma decisión que `requires_local`
en las herramientas: si alguien añade un proveedor y olvida declarar lo que
admite, el fallo va del lado seguro. Nunca se le mandará una imagen a un modelo
que no pueda leerla; a lo sumo, Morgan dirá que no tiene con qué.
"""

from enum import Enum


class Capability(str, Enum):
    """Lo que un proveedor puede aceptar como entrada."""

    TEXT = "text"
    VISION = "vision"
    AUDIO = "audio"

    def __str__(self) -> str:  # para mensajes de error legibles
        return self.value


SOLO_TEXTO = frozenset({Capability.TEXT})
TEXTO_Y_VISION = frozenset({Capability.TEXT, Capability.VISION})


class CapacidadNoDisponible(RuntimeError):
    """Ningún proveedor de la cadena admite lo que se le pide.

    Es distinto de «el proveedor falló»: aquí no hay nada que reintentar ni a
    quién conmutar, así que la cadena no debe agotar los eslabones probando.
    """

    def __init__(self, capacidad: Capability, disponibles: list[str] | None = None):
        self.capacidad = capacidad
        self.disponibles = disponibles or []

        detalle = f" Proveedores configurados: {', '.join(self.disponibles)}." if self.disponibles else ""
        super().__init__(
            f"Ningún proveedor configurado admite '{capacidad}'.{detalle}"
        )


# Modelos de Groq con visión, por fragmento del nombre. Groq sirve familias muy
# distintas bajo la misma API, así que la capacidad depende del modelo elegido y
# no del proveedor. La lista se queda corta a propósito: es preferible negarse a
# enviar una imagen que el modelo sí habría entendido, a mandársela a uno que no.
MARCADORES_VISION_GROQ = ("scout", "maverick", "vision", "llava")


def capacidades_de_modelo_groq(nombre: str) -> frozenset[Capability]:
    """Deduce qué admite un modelo de Groq a partir de su nombre."""
    minusculas = (nombre or "").lower()
    if any(marca in minusculas for marca in MARCADORES_VISION_GROQ):
        return TEXTO_Y_VISION
    return SOLO_TEXTO
