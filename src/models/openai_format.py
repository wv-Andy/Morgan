"""
Conversión al formato de la API de OpenAI, compartida por varios proveedores.

Groq y NVIDIA NIM hablan el mismo dialecto, y la conversión tiene detalles sutiles
que ya costaron un fallo real: sin `tool_call_id`, los resultados de herramienta
no se emparejaban con la llamada que los originó y el modelo perdía el hilo.
Duplicar esto en dos proveedores era garantizar que las copias divergieran.
"""

import logging
import json
from typing import Any

logger = logging.getLogger(__name__)

from src.models.base import ChatMessage, LLMResponse, ToolCallRequest


class SoloRazonamiento(RuntimeError):
    """El modelo dejó la respuesta vacía y solo su razonamiento (4.0.5).

    Cuenta como un fallo de ese proveedor, para que la cadena pruebe el siguiente. Medido
    con el modelo real: `openai/gpt-oss-20b` (relevo de Groq) contestó a la persona, en
    inglés, «The user says plan approved, but also includes note that…»: su razonamiento,
    con las instrucciones internas dentro. El texto viaja en la excepción: si nadie más
    contesta, la cadena lo usa como último recurso (ver `FallbackProvider`)."""

    def __init__(self, razonamiento: str):
        super().__init__("El modelo solo devolvió su razonamiento, sin respuesta")
        self.razonamiento = razonamiento


def to_openai_messages(
    messages: list[ChatMessage],
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Traduce el historial de Morgan al formato de OpenAI."""
    salida: list[dict[str, Any]] = []

    if system_prompt:
        salida.append({"role": "system", "content": system_prompt})

    for msg in messages:
        if msg.role == "tool":
            # El identificador correlaciona el resultado con su llamada. Sin él,
            # el modelo recibe un resultado huérfano y pierde el hilo.
            call_id = msg.tool_call_id or f"call_{msg.tool_name or 'default'}"
            salida.append({
                "role": "tool",
                "name": msg.tool_name or "tool",
                # `ensure_ascii=False`: con el valor por defecto, cada «á» viaja
                # como `\u00e1`, seis caracteres. Gemini ya los mandaba tal cual.
                "content": json.dumps(msg.tool_result or {}, ensure_ascii=False, default=str),
                "tool_call_id": call_id,
            })

        elif msg.tool_calls:
            llamadas = []
            for tc in msg.tool_calls:
                argumentos = (
                    json.dumps(tc.arguments, ensure_ascii=False)
                    if isinstance(tc.arguments, dict)
                    else str(tc.arguments or "{}")
                )
                llamadas.append({
                    "id": tc.id or f"call_{tc.name}",
                    "type": "function",
                    "function": {"name": tc.name, "arguments": argumentos},
                })
            salida.append({
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": llamadas,
            })

        else:
            rol = "assistant" if msg.role in ("model", "assistant") else "user"
            salida.append({"role": rol, "content": msg.content or ""})

    return salida


def to_openai_tools(tools: list[dict] | None) -> list[dict[str, Any]] | None:
    """Traduce el catálogo de herramientas. Devuelve None si no hay ninguna.

    Devolver None y no una lista vacía es importante: los campos `tools` y
    `tool_choice` deben **omitirse** cuando no hay herramientas, no enviarse
    nulos. Groq rechaza `tool_choice=null` con un 400.
    """
    if not tools:
        return None

    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object"}),
            },
        }
        for t in tools
    ]


def from_openai_message(mensaje: Any) -> LLMResponse:
    """Traduce la respuesta a la forma normalizada de Morgan.

    Acepta tanto el objeto del SDK de Groq como el diccionario que devuelve una
    respuesta HTTP en crudo: se accede por atributo y, si no lo hay, por clave.
    """

    def campo(obj: Any, nombre: str, defecto: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(nombre, defecto)
        return getattr(obj, nombre, defecto)

    llamadas = campo(mensaje, "tool_calls") or []
    if llamadas:
        peticiones = []
        for tc in llamadas:
            funcion = campo(tc, "function") or {}
            crudos = campo(funcion, "arguments") or ""
            try:
                argumentos = json.loads(crudos) if crudos else {}
            except (ValueError, TypeError):
                # Un modelo puede devolver argumentos mal formados. Mejor una
                # llamada sin argumentos, que el agente rechazará por validación,
                # que reventar el turno entero.
                argumentos = {}

            peticiones.append(
                ToolCallRequest(
                    id=campo(tc, "id") or "",
                    name=campo(funcion, "name") or "",
                    arguments=argumentos,
                )
            )

        return LLMResponse(type="tool_call", tool_calls=peticiones)

    texto = campo(mensaje, "content") or ""
    if texto.strip():
        return LLMResponse(type="text", content=texto)

    # Los modelos de razonamiento devuelven su cadena de pensamiento en
    # `reasoning_content` —o `reasoning`, segun el proveedor— y a veces dejan
    # `content` vacio. Sin esto, Morgan contestaba una cadena vacia: la persona
    # veia un turno que tardaba diez segundos y no decia nada.
    #
    # Medido con `openai/gpt-oss-20b` en NVIDIA: `content` de 0 caracteres y
    # `reasoning_content` de 109. Devolver el razonamiento no es ideal —no esta
    # redactado para leerse— pero es infinitamente mejor que no devolver nada,
    # y deja rastro de que paso.
    #
    # Desde la 4.0.5, solo como ÚLTIMO recurso: se lanza `SoloRazonamiento` y la cadena
    # prueba antes el siguiente proveedor, porque el razonamiento llegó a la persona en
    # inglés y con las instrucciones internas dentro.
    razonamiento = campo(mensaje, "reasoning_content") or campo(mensaje, "reasoning") or ""
    if razonamiento.strip():
        logger.info(
            "El modelo dejo la respuesta en el campo de razonamiento (%d caracteres)",
            len(razonamiento),
        )
        raise SoloRazonamiento(razonamiento)

    return LLMResponse(type="text", content="")
