"""
El modo de prueba de carga: un despliegue de Morgan sin modelo real (2.3-C).

**Para qué existe.** La prueba de carga en local (`scripts/carga.py`) midió a
Morgan con SQLite; la cifra de producción necesita la base de verdad, Supabase por
HTTP. Eso se mide en un **despliegue aparte** —su propio servicio de Render y su
propio proyecto de Supabase, `morgan-carga`— con un modelo simulado: lo que se
mide es Morgan, no Groq, y no se gasta cuota de nadie.

Con `MORGAN_PRUEBA_DE_CARGA=1`:

- El modelo es simulado, con la latencia de `MORGAN_PRUEBA_LATENCIA_MS` (600 ms
  por defecto, la de Groq medida) y la misma conducta que en `carga.py`: pide una
  herramienta si el mensaje dice «recuerda», y si no, contesta.
- Se levantan los frenos de altas (20 cada 15 minutos, 3 por origen): la prueba da
  de alta a decenas de personas desde la misma IP. El cupo global se quita con su
  propia variable, `MORGAN_CUPO_GLOBAL_MENSAJES=0`.
- `/status` lo dice: el modelo se llama «simulado (prueba de carga)».

## Las salvaguardas: se niega a arrancar

Un modo que quita frenos y pone un modelo de mentira es peligroso si se enciende
donde no toca. Por eso **el servidor no arranca** (no «arranca sin el modo»: no
arranca) si, con la variable puesta:

- hay **cualquier clave de un modelo real** (Groq, Gemini, OpenAI, NVIDIA): un
  despliegue con claves es uno que atiende a personas;
- o el Supabase configurado es **el de producción**.

Callar y arrancar sin el modo sería peor: la prueba mediría otra cosa sin que
nadie lo notara.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from urllib.parse import urlparse

from src.models.base import LLMProvider, LLMResponse, ToolCallRequest

VARIABLE = "MORGAN_PRUEBA_DE_CARGA"
LATENCIA = "MORGAN_PRUEBA_LATENCIA_MS"

#: La huella (SHA-256) del identificador del proyecto de Supabase de producción, para que
#: el modo de prueba no pueda apuntarle nunca. Va la huella y no el nombre porque el código
#: es público (4.19): no es un secreto, pero no hace falta decir cuál es la base.
HUELLA_DE_PRODUCCION = "e3087704a1fdd2256c22e86baf0403f13d803faaf3a5347d61acbb976f337b5b"


def es_produccion(url: str | None) -> bool:
    """True si la URL de Supabase es la de producción (`https://<proyecto>.supabase.co`)."""
    anfitrion = urlparse(url or "").hostname or ""
    return any(hashlib.sha256(parte.encode()).hexdigest() == HUELLA_DE_PRODUCCION
               for parte in anfitrion.split("."))

NOMBRE_DEL_MODELO = "simulado (prueba de carga)"


class PruebaDeCargaInsegura(RuntimeError):
    """El modo de prueba de carga se pidió donde no puede encenderse."""


def pedido() -> bool:
    return (os.getenv(VARIABLE) or "").strip().lower() in ("1", "true", "si", "sí", "yes")


def motivos_para_negarse(settings) -> list[str]:
    """Por qué no puede encenderse aquí. Vacío si puede."""
    motivos = []
    claves = {
        "Groq": bool(settings.groq_api_key or settings.groq_api_keys),
        "Gemini": bool(settings.gemini_api_key),
        "OpenAI": bool(settings.openai_api_key),
        "NVIDIA": bool(settings.nvidia_api_key),
    }
    con_clave = [nombre for nombre, hay in claves.items() if hay]
    if con_clave:
        motivos.append(
            f"hay claves de modelos reales ({', '.join(con_clave)}): un despliegue con "
            "claves atiende a personas"
        )
    if es_produccion(settings.supabase_url):
        motivos.append("el Supabase configurado es el de producción")
    return motivos


def comprobar(settings) -> bool:
    """True si el modo está pedido y puede encenderse. Lanza si está pedido y no puede."""
    if not pedido():
        return False
    motivos = motivos_para_negarse(settings)
    if motivos:
        raise PruebaDeCargaInsegura(
            f"{VARIABLE} está puesto, pero no se enciende aquí: " + "; ".join(motivos) + ". "
            "El modo de prueba de carga es solo para un despliegue aparte, sin claves y "
            "con su propia base."
        )
    return True


class ModeloDePrueba(LLMProvider):
    """Sin estado entre turnos: decide mirando solo los mensajes del suyo."""

    def __init__(self, latencia_ms: int | None = None):
        if latencia_ms is None:
            latencia_ms = int(os.getenv(LATENCIA) or 600)
        self.latencia = max(0, latencia_ms) / 1000

    @property
    def model_name(self) -> str:
        return NOMBRE_DEL_MODELO

    def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        time.sleep(self.latencia)
        ultimo = messages[-1]
        if ultimo.role == "user" and "recuerda" in (ultimo.content or "").lower():
            return LLMResponse(type="tool_call", tool_calls=[ToolCallRequest(
                name="remember_fact", id=uuid.uuid4().hex,
                arguments={"key": f"dato-{uuid.uuid4().hex[:8]}", "value": "verde",
                           "category": "preference"},
            )])
        return LLMResponse(type="text", content="Hecho. " + "texto de relleno " * 40)


def levantar_frenos_de_altas() -> None:
    """La prueba da de alta a decenas de personas desde una misma IP."""
    from src.identidad import cuentas

    cuentas.MAX_REGISTROS_EN_TOTAL = 10**6
    cuentas.MAX_REGISTROS_POR_ORIGEN = 10**6
