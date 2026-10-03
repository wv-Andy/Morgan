"""
Proveedor LLM para NVIDIA NIM (V1.4).

NVIDIA expone modelos alojados tras una API compatible con la de OpenAI, así que
la conversión de mensajes y herramientas es la misma que usa Groq y se comparte
en `openai_format`.

**Se habla por HTTP directamente, con httpx, en lugar de instalar el SDK de
OpenAI.** Es la misma decisión que ya se tomó con Supabase: aquí hacen falta una
ruta y cuatro campos, y el SDK completo traería un cliente asíncrono, streaming,
asistentes y embeddings que Morgan no usa. httpx ya es dependencia.

Verificado contra el servicio real: responde texto y **admite function calling**,
así que es un eslabón de pleno derecho y no solo un respaldo para conversar.
"""

import logging
from typing import Any

import httpx

from src.config import get_settings
from src.models.base import ChatMessage, LLMProvider, LLMResponse
from src.models.capabilities import SOLO_TEXTO, Capability
from src.models.openai_format import (
    from_openai_message,
    to_openai_messages,
    to_openai_tools,
)

logger = logging.getLogger(__name__)

BASE_URL_POR_DEFECTO = "https://integrate.api.nvidia.com/v1"


class NvidiaProvider(LLMProvider):
    """Modelos alojados en NVIDIA NIM, por su API compatible con OpenAI."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
    ):
        settings = get_settings()
        clave = api_key or settings.nvidia_api_key
        if not clave:
            raise ValueError(
                "NVIDIA_API_KEY no configurada. "
                "Añádela en .env u obtén una en https://build.nvidia.com"
            )

        self._api_key = clave
        self._model_name = model_name or settings.nvidia_model
        self._base_url = (base_url or settings.nvidia_base_url).rstrip("/")
        self._timeout = settings.llm_timeout
        self._max_retries = settings.llm_max_retries

    @property
    def model_name(self) -> str:
        return f"NVIDIA:{self._model_name}"

    @property
    def capabilities(self) -> frozenset[Capability]:
        # Los modelos de razonamiento servidos aquí son de texto. Cuando se
        # configure uno con visión, esto pasará a depender del modelo como en Groq.
        return SOLO_TEXTO

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        requires: Capability = Capability.TEXT,
    ) -> LLMResponse:
        peticion: dict[str, Any] = {
            "model": self._model_name,
            "messages": to_openai_messages(messages, system_prompt),
            "temperature": 1,
            "top_p": 0.95,
            "max_tokens": 8192,
            # Estos modelos razonan en voz alta antes de responder. Ese texto no
            # aporta nada a Morgan, que ya tiene su propio bucle de herramientas,
            # y multiplica la latencia y el gasto de tokens.
            "chat_template_kwargs": {"thinking": False},
            "stream": False,
        }

        herramientas = to_openai_tools(tools)
        if herramientas:
            peticion["tools"] = herramientas
            peticion["tool_choice"] = "auto"

        respuesta = self._enviar(peticion)

        opciones = respuesta.get("choices") or []
        if not opciones:
            # Un filtro de contenido o un corte del servicio pueden devolver una
            # respuesta sin opciones. Antes eso era un IndexError en mitad del
            # bucle del agente.
            return LLMResponse(
                type="text",
                content="El modelo no devolvió ninguna respuesta.",
            )

        return from_openai_message(opciones[0].get("message") or {})

    def _enviar(self, peticion: dict[str, Any]) -> dict[str, Any]:
        """Hace la llamada, reintentando solo lo que tiene sentido reintentar."""
        cabeceras = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        intentos = max(1, self._max_retries + 1)
        ultimo: Exception | None = None

        for intento in range(intentos):
            try:
                r = httpx.post(
                    f"{self._base_url}/chat/completions",
                    headers=cabeceras,
                    json=peticion,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                # Un plazo agotado NO se reintenta, y es lo unico de aqui que
                # habia que arreglar. La auditoria de la V2.0.2 lo midio en
                # produccion: quince llamadas, quince plazos agotados, y cada
                # una tardaba 60 s en lugar de 30 porque `ReadTimeout` es un
                # `RequestError` y caia en la rama de abajo.
                #
                # Esperaste el plazo entero precisamente porque no esta
                # contestando; volver a esperarlo no cambia nada y duplica lo
                # que cuesta un proveedor muerto.
                raise RuntimeError(
                    f"NVIDIA NIM no respondio en {self._timeout} s: {exc}"
                ) from exc
            except httpx.RequestError as exc:
                # Un fallo de conexion si: no ha llegado a empezar nada.
                ultimo = exc
                if intento < intentos - 1:
                    continue
                raise RuntimeError(f"No se pudo conectar con NVIDIA NIM: {exc}") from exc

            if r.status_code == 200:
                return r.json()

            # 429 y 5xx son transitorios; un 400 o un 401 no mejoran repitiendo.
            if r.status_code in (429, 500, 502, 503, 504) and intento < intentos - 1:
                logger.warning(
                    "NVIDIA NIM devolvió %s; reintentando (%d/%d)",
                    r.status_code, intento + 1, intentos - 1,
                )
                continue

            # El cuerpo va en la excepción para que quede en el log; el traductor
            # de errores se encarga de que no llegue a la interfaz.
            raise RuntimeError(f"NVIDIA NIM devolvió {r.status_code}: {r.text[:400]}")

        raise RuntimeError(f"NVIDIA NIM no respondió: {ultimo}")
