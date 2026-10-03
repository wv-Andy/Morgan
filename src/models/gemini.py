"""
Implementación del proveedor LLM para Google Gemini utilizando el SDK oficial google-genai.
Incluye preservación estricta de thought_signature para function calling y reintentos automáticos ante 503.
"""

import base64
import json
import time

from google import genai
from google.genai import types
from google.genai.errors import ServerError

from src.config import get_settings
from src.models.base import LLMProvider, ChatMessage, LLMResponse, ToolCallRequest
from src.models.capabilities import TEXTO_Y_VISION, Capability
from src.models.llavero import Llavero


class GeminiProvider(LLMProvider):
    """Proveedor de Gemini que implementa la interfaz universal LLMProvider."""

    def __init__(self, api_key: str | tuple[str, ...] | None = None, model_name: str | None = None):
        settings = get_settings()
        if isinstance(api_key, str):
            claves: tuple[str, ...] = (api_key,)
        elif api_key:
            claves = tuple(api_key)
        else:
            claves = settings.gemini_api_keys or (
                (settings.gemini_api_key,) if settings.gemini_api_key else ()
            )
        if not claves:
            raise ValueError(
                "GEMINI_API_KEY no configurada. "
                "Copia .env.example a .env y agrega tu API key.\n"
                "Obtén una gratis en: https://aistudio.google.com"
            )

        self._model_name = model_name or settings.gemini_model
        self._timeout = settings.llm_timeout
        # Varias claves, como Groq (4.1.5): se gastan en serie y un rechazo por cuota
        # pasa a la siguiente sin cambiar de modelo. Ver `src/models/llavero.py`.
        self._llavero = Llavero("Gemini", claves)
        # HttpOptions.timeout va en milisegundos. Sin él se hereda el del SDK y
        # una llamada colgada puede consumir minutos.
        self._clientes = {
            etiqueta: genai.Client(
                api_key=clave,
                http_options=types.HttpOptions(timeout=settings.llm_timeout * 1000),
            )
            for etiqueta, clave in self._llavero.turnos()
        }

    @property
    def client(self):
        """El cliente de la clave que toca ahora (las pruebas lo leen y lo sustituyen)."""
        etiqueta, _ = self._llavero.turnos()[0]
        return self._clientes[etiqueta]

    @client.setter
    def client(self, cliente) -> None:
        """Sustituye el de **todas** las claves: un doble a medias llamaría a Gemini de
        verdad al rotar (como en `GroqProvider.client`)."""
        self._clientes = {e: cliente for e in self._clientes}

    @property
    def claves_disponibles(self) -> int:
        return len(self._llavero)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def capabilities(self) -> frozenset[Capability]:
        # Toda la familia Gemini que Morgan usa entiende imágenes.
        return TEXTO_Y_VISION

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        requires: Capability = Capability.TEXT,
    ) -> LLMResponse:
        contents = self._build_contents(messages)

        config = types.GenerateContentConfig()
        if system_prompt:
            config.system_instruction = system_prompt

        if tools:
            function_declarations = []
            for schema in tools:
                properties = {
                    nombre: self._a_schema(definicion)
                    for nombre, definicion in schema["parameters"].get("properties", {}).items()
                }

                func_decl = types.FunctionDeclaration(
                    name=schema["name"],
                    description=schema["description"],
                    parameters=types.Schema(
                        type="OBJECT",
                        properties=properties,
                        required=schema["parameters"].get("required", []),
                    ) if properties else None,
                )
                function_declarations.append(func_decl)

            config.tools = [types.Tool(function_declarations=function_declarations)]

        # Reintentos para errores transitorios de servidor (503 / 429). El número
        # sale de la configuración: con un proveedor de respaldo detrás, insistir
        # mucho aquí solo alarga la espera antes de conmutar.
        response, etiqueta = self._con_el_llavero(contents, config)

        # Una respuesta bloqueada por los filtros de seguridad llega sin candidatos o
        # sin contenido. Antes eso provocaba IndexError o AttributeError en mitad del
        # bucle del agente; ahora se traduce a una respuesta de texto explicativa.
        self._apuntar_gasto(response, etiqueta)

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            reason = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
            detail = f" (motivo: {reason})" if reason else ""
            return LLMResponse(
                type="text",
                content=f"El modelo no devolvió ninguna respuesta{detail}.",
            )

        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        parts = (getattr(content, "parts", None) or []) if content is not None else []

        # Recoger TODAS las llamadas a función: quedarse con la primera descartaba
        # silenciosamente el resto de herramientas que el modelo había pedido.
        tool_calls = [
            ToolCallRequest(
                name=part.function_call.name,
                arguments=dict(part.function_call.args) if part.function_call.args else {},
            )
            for part in parts
            if getattr(part, "function_call", None)
        ]

        if tool_calls:
            return LLMResponse(
                type="tool_call",
                tool_calls=tool_calls,
                # Preservar exactamente las partes generadas por Gemini (con su thought_signature intacto)
                raw_parts=parts,
            )

        text = "".join(part.text for part in parts if getattr(part, "text", None))
        if not text:
            text = getattr(response, "text", "") or ""

        return LLMResponse(type="text", content=text)

    def _con_el_llavero(self, contents, config):
        """Llama a Gemini probando las claves en serie (4.1.5). Devuelve `(respuesta,
        etiqueta)`. Un rechazo **por cuota** pasa a la siguiente clave; si no queda
        ninguna, se lanza y la cadena pasa a otro proveedor. Un 503 se reintenta en la
        misma clave, como antes."""
        from src.models.cuota import CUOTAS, es_rechazo_por_cuota, segundos_hasta_reintentar

        turnos = self._llavero.turnos()
        max_retries = max(1, get_settings().llm_max_retries + 1)
        for numero, (etiqueta, _) in enumerate(turnos, start=1):
            delay = 2
            for attempt in range(max_retries):
                try:
                    respuesta = self._clientes[etiqueta].models.generate_content(
                        model=self._model_name,
                        contents=contents,
                        config=config,
                    )
                    return respuesta, etiqueta
                except Exception as e:
                    if es_rechazo_por_cuota(e):
                        CUOTAS.marcar_agotado(etiqueta, segundos_hasta_reintentar(str(e)))
                        if numero < len(turnos):
                            break           # a la siguiente clave: otro proyecto, otro cupo
                        raise
                    if isinstance(e, ServerError) and e.code == 503 and attempt < max_retries - 1:
                        time.sleep(delay)
                        delay *= 2
                        continue
                    raise
        raise RuntimeError("El llavero de Gemini no ha devuelto ninguna clave")

    def _apuntar_gasto(self, response, etiqueta: str | None = None) -> None:
        """Apunta los tokens que Gemini dice haber gastado.

        Como en Groq, el dato venia en cada respuesta y se tiraba. Gemini lo
        pone en `usage_metadata` y con otros nombres, asi que se traduce aqui.
        """
        uso = getattr(response, "usage_metadata", None)
        if uso is None:
            return
        from src.models.cuota import CUOTAS

        CUOTAS.apuntar_uso(
            etiqueta or self.model_name,
            int(getattr(uso, "prompt_token_count", 0) or 0),
            int(getattr(uso, "candidates_token_count", 0) or 0),
        )

    def _build_contents(self, messages: list[ChatMessage]) -> list[types.Content]:
        """Traduce el historial de Morgan al formato de Gemini.

        La sutileza está en las llamadas a herramienta. Gemini exige que cada
        `functionCall` que se le devuelve lleve su `thought_signature`, la firma
        que él mismo generó. Solo podemos cumplir eso con las llamadas que salieron
        de Gemini, que son las que llegan con `raw_parts`.

        Las que no traen firma vienen de otro sitio: las produjo Groq antes de
        conmutar de proveedor, o el historial se recuperó de SQLite, donde
        `raw_parts` no sobrevive. Reenviarlas como `functionCall` provoca un
        400 INVALID_ARGUMENT que aborta el turno entero.

        Esas se transcriben a texto. Gemini pierde la estructura, pero conserva
        lo que importa —qué se llamó y qué devolvió— y la conversación continúa.
        """
        contents = []
        # Indica si el ultimo bloque de llamadas era de Gemini y llevaba firma.
        # Un functionResponse sin su functionCall delante tambien es invalido, asi
        # que los resultados siguen la misma suerte que su llamada.
        llamadas_con_firma = False

        for msg in messages:
            if msg.role == "tool":
                if llamadas_con_firma:
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[types.Part.from_function_response(
                                name=msg.tool_name or "tool",
                                response={"result": msg.tool_result or {}},
                            )],
                        )
                    )
                else:
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=self._resultado_como_texto(msg))],
                        )
                    )
            elif msg.tool_calls:
                if getattr(msg, "raw_parts", None):
                    llamadas_con_firma = True
                    contents.append(types.Content(role="model", parts=msg.raw_parts))
                else:
                    llamadas_con_firma = False
                    contents.append(
                        types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=self._llamadas_como_texto(msg))],
                        )
                    )
            else:
                llamadas_con_firma = False
                role = "model" if msg.role in ("model", "assistant") else "user"
                partes = [types.Part.from_text(text=msg.content or "")]

                # Imagen adjunta (V1.4). Va despues del texto para que la
                # instruccion se lea primero.
                if msg.image:
                    partes.append(
                        types.Part.from_bytes(
                            data=base64.b64decode(msg.image["base64"]),
                            mime_type=msg.image.get("mime", "image/png"),
                        )
                    )

                contents.append(types.Content(role=role, parts=partes))
        return contents

    @staticmethod
    def _resumen(valor: object, limite: int = 2000) -> str:
        """Serializa un valor de forma segura y acotada.

        El resultado de `read_webpage` puede ocupar cientos de miles de caracteres;
        volcarlo entero al contexto agota la ventana del modelo.
        """
        try:
            texto = json.dumps(valor, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            texto = str(valor)
        return texto if len(texto) <= limite else texto[:limite] + "… (truncado)"

    def _llamadas_como_texto(self, msg: ChatMessage) -> str:
        lineas = [
            f"- {tc.name}({self._resumen(tc.arguments or {}, 500)})"
            for tc in (msg.tool_calls or [])
        ]
        cuerpo = "\n".join(lineas) or "- (ninguna)"
        prefijo = f"{msg.content}\n" if msg.content else ""
        return f"{prefijo}He usado estas herramientas:\n{cuerpo}"

    def _resultado_como_texto(self, msg: ChatMessage) -> str:
        return (
            f"Resultado de {msg.tool_name or 'la herramienta'}: "
            f"{self._resumen(msg.tool_result or {})}"
        )

    @classmethod
    def _a_schema(cls, definicion: dict) -> "types.Schema":
        """Traduce un parámetro de JSON Schema al de Gemini.

        Gemini **exige** `items` en los parámetros de tipo array. Sin él rechaza
        la petición entera con
        `parameters.properties[x].items: missing field`, y no solo esa
        herramienta: **el catálogo completo**, así que Gemini deja de servir.

        Salió al añadir `create_task`, la primera herramienta con un parámetro de
        tipo lista. Hasta entonces ninguna lo tenía y el fallo estaba latente.
        """
        # JSON Schema dice «opcional» con una lista de tipos, `["string",
        # "null"]`; Gemini, con `nullable`. Sin traducirlo, la lista llegaba a
        # `_map_type` y el catálogo entero dejaba de construirse (2.3-D).
        crudo = definicion.get("type", "string")
        anulable = isinstance(crudo, list) and "null" in crudo
        if isinstance(crudo, list):
            crudo = next((t for t in crudo if t != "null"), "string")
        tipo = cls._map_type(crudo)
        campos = {
            "type": tipo,
            "description": definicion.get("description", ""),
        }
        if anulable:
            campos["nullable"] = True

        if tipo == "ARRAY":
            # Si el esquema no dice de qué son los elementos, se asume texto:
            # mejor una suposición razonable que una petición inválida.
            campos["items"] = cls._a_schema(definicion.get("items") or {"type": "string"})

        if tipo == "OBJECT" and definicion.get("properties"):
            campos["properties"] = {
                nombre: cls._a_schema(sub)
                for nombre, sub in definicion["properties"].items()
            }

        if definicion.get("enum"):
            campos["enum"] = [str(v) for v in definicion["enum"]]

        return types.Schema(**campos)

    @staticmethod
    def _map_type(type_str: str) -> str:
        type_map = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }
        return type_map.get(type_str, "STRING")
