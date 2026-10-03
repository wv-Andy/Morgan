"""
Proveedor de OpenAI (V2.0.3). **El primero que cuesta dinero.**

Habla el dialecto de OpenAI, así que la conversión de mensajes y herramientas es
la misma que usan Groq y NVIDIA y se comparte en `openai_format`. Se llama por
HTTP con `httpx` en lugar de instalar el SDK, igual que los otros: aquí hacen
falta una ruta y cinco campos, y el SDK completo traería un cliente asíncrono,
streaming, asistentes y embeddings que Morgan no usa.

## Lo que lo hace distinto de los demás: se paga

Los otros tres proveedores son gratuitos y su castigo por pasarse es un 429. Este
cobra, y eso cambia dos cosas del diseño:

1. **Va al final de la cadena.** No porque sea peor —es el más fiable de los
   cuatro— sino porque mientras quede cuota gratis no hay razón para pagar. Así
   el dinero solo se gasta cuando Groq y Gemini se han agotado, que es
   exactamente cuando Morgan dejaba de funcionar.
2. **No se reintenta un plazo agotado.** En un proveedor gratuito eso solo
   cuesta tiempo; aquí puede costar dos respuestas facturadas por una que no
   llegó. Está explicado abajo, y es el mismo defecto que la auditoría encontró
   en `nvidia.py`.

## Tres parámetros que este modelo rechaza, y hay medición

Copiar `nvidia.py` habría dado un 400 en cada llamada. Comprobado contra el
servicio real, `gpt-5.6-luna` rechaza:

| Lo que manda el resto de Morgan | Lo que dice el servicio |
|---|---|
| `max_tokens` | «is not supported with this model. Use `max_completion_tokens`» |
| `temperature: 0.2` | «does not support 0.2. Only the default (1) value is supported» |
| herramientas sin `reasoning_effort` | «Function tools with reasoning_effort are not supported … set reasoning_effort to 'none'» |

El tercero es el importante: **sin `reasoning_effort: "none"` las herramientas
no funcionan**, y Morgan sin herramientas es un chat. Se manda siempre, y no
solo cuando hay herramientas, porque además el razonamiento se factura como
salida: apagarlo es a la vez lo que hace funcionar las llamadas y lo que evita
pagar por texto que Morgan tira.

Los tres rechazos son 400, y un 400 no consume tokens. Descubrirlos costó cero.
"""

import logging
import threading
import time
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
from src.observabilidad import anotar, contar

logger = logging.getLogger(__name__)

BASE_URL_POR_DEFECTO = "https://api.openai.com/v1"


class PresupuestoAgotado(RuntimeError):
    """Se alcanzó el tope de tokens que este despliegue se permite gastar.

    Es distinto de «el proveedor falló»: el proveedor está perfectamente. Lo que
    pasa es que Morgan ha decidido no gastar más hoy, y esa decisión no se
    arregla reintentando.
    """


class _Contador:
    """Cuenta los tokens gastados y dice cuándo parar.

    **Vive en memoria, y eso es una limitación real, no un descuido.** Un
    reinicio del proceso lo pone a cero, y en Render el plan gratuito reinicia a
    menudo. Así que esto no es un límite de gasto: es un **freno de mano** contra
    lo que de verdad da miedo, que es un bucle que se desboca y hace mil
    llamadas en diez minutos.

    El límite de verdad, el que no se puede saltar, es el que se pone en el panel
    de OpenAI (Settings > Limits), y solo lo puede poner el dueño de la cuenta.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.entrada = 0
        self.salida = 0
        self.llamadas = 0
        self.rechazadas = 0
        self.desde = time.time()

    def _reiniciar_si_toca(self, ventana_segundos: float) -> None:
        if ventana_segundos > 0 and time.time() - self.desde >= ventana_segundos:
            logger.info(
                "Presupuesto de OpenAI reiniciado: se habían gastado %d tokens "
                "en %d llamadas",
                self.entrada + self.salida, self.llamadas,
            )
            self.entrada = self.salida = self.llamadas = self.rechazadas = 0
            self.desde = time.time()

    def comprobar(self, tope_tokens: int, ventana_segundos: float) -> None:
        """Lanza si ya no queda presupuesto. Se llama ANTES de gastar."""
        with self._lock:
            self._reiniciar_si_toca(ventana_segundos)
            gastado = self.entrada + self.salida
            if tope_tokens > 0 and gastado >= tope_tokens:
                self.rechazadas += 1
                raise PresupuestoAgotado(
                    f"Morgan ha gastado {gastado} tokens de OpenAI en esta "
                    f"ventana y el tope está en {tope_tokens}. No se llama más "
                    "hasta que la ventana se renueve."
                )

    def apuntar(self, entrada: int, salida: int) -> None:
        with self._lock:
            self.entrada += entrada
            self.salida += salida
            self.llamadas += 1

    def resumen(self) -> dict[str, int]:
        with self._lock:
            return {
                "llamadas": self.llamadas,
                "tokens_entrada": self.entrada,
                "tokens_salida": self.salida,
                "rechazadas_por_presupuesto": self.rechazadas,
            }


#: Compartido por todas las instancias del proveedor a propósito: lo que importa
#: es cuánto gasta este proceso en total, no cada objeto por su cuenta.
CONTADOR = _Contador()


class OpenAIProvider(LLMProvider):
    """Modelos de OpenAI por su API de completions de chat."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
    ):
        settings = get_settings()
        clave = api_key or settings.openai_api_key
        if not clave:
            raise ValueError(
                "OPENAI_API_KEY no configurada. "
                "Añádela en .env u obtén una en https://platform.openai.com"
            )

        self._api_key = clave
        self._model_name = model_name or settings.openai_model
        self._base_url = (base_url or settings.openai_base_url).rstrip("/")
        self._timeout = settings.llm_timeout
        self._max_retries = settings.llm_max_retries
        self._max_salida = settings.openai_max_salida
        self._tope_tokens = settings.openai_tope_tokens
        self._ventana = settings.openai_ventana_segundos

    @property
    def model_name(self) -> str:
        return f"OpenAI:{self._model_name}"

    @property
    def capabilities(self) -> frozenset[Capability]:
        # Solo texto mientras no se compruebe la visión contra el servicio real.
        # El valor por defecto va del lado seguro: nunca se le manda una imagen
        # a un modelo que quizá no la entienda, y en un proveedor de pago
        # equivocarse aquí cuesta una llamada facturada que devuelve un 400.
        return SOLO_TEXTO

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        requires: Capability = Capability.TEXT,
    ) -> LLMResponse:
        # Antes de construir nada: ¿queda presupuesto?
        CONTADOR.comprobar(self._tope_tokens, self._ventana)

        peticion: dict[str, Any] = {
            "model": self._model_name,
            "messages": to_openai_messages(messages, system_prompt),
            # `max_completion_tokens`, no `max_tokens`: este modelo rechaza el
            # segundo con un 400. Y el tope es de gasto, no de calidad: una
            # respuesta cortada se nota y se puede subir; una factura no.
            "max_completion_tokens": self._max_salida,
            # Sin esto las herramientas no funcionan, y además el razonamiento
            # se factura como salida. Ver la cabecera del módulo.
            "reasoning_effort": "none",
            "stream": False,
        }
        # `temperature` NO se manda: este modelo solo acepta su valor por
        # defecto y rechaza cualquier otro con un 400.

        herramientas = to_openai_tools(tools)
        if herramientas:
            peticion["tools"] = herramientas
            peticion["tool_choice"] = "auto"

        respuesta = self._enviar(peticion)

        self._apuntar_gasto(respuesta)

        opciones = respuesta.get("choices") or []
        if not opciones:
            # Un filtro de contenido puede devolver esto. Antes de que existiera
            # esta comprobación en los otros proveedores era un IndexError en
            # mitad del bucle del agente.
            logger.warning("OpenAI devolvió una respuesta sin opciones")
            return LLMResponse(
                type="text",
                content="El modelo no devolvió ninguna respuesta.",
            )

        return from_openai_message(opciones[0].get("message") or {})

    def _apuntar_gasto(self, respuesta: dict[str, Any]) -> None:
        """Lleva la cuenta, y la deja en la medición de la petición.

        Los tokens de razonamiento se cuentan aparte porque se facturan como
        salida y no se ven en la respuesta: si algún día suben sin que nadie
        haya cambiado nada, esto es lo que lo enseña.
        """
        uso = respuesta.get("usage") or {}
        entrada = int(uso.get("prompt_tokens") or 0)
        salida = int(uso.get("completion_tokens") or 0)
        CONTADOR.apuntar(entrada, salida)

        # Y al registro comun, para que `/status` vea los cuatro proveedores en
        # el mismo sitio. El contador de arriba es el freno del gasto, que es
        # otra cosa: uno decide si se llama, el otro solo informa.
        from src.models.cuota import CUOTAS

        CUOTAS.apuntar_uso(self.model_name, entrada, salida)

        detalle = uso.get("completion_tokens_details") or {}
        razonamiento = int(detalle.get("reasoning_tokens") or 0)

        anotar("openai_tokens_entrada", entrada)
        anotar("openai_tokens_salida", salida)
        if razonamiento:
            # No debería pasar con `reasoning_effort: none`. Si pasa, se ve.
            anotar("openai_tokens_razonamiento", razonamiento)
            logger.warning(
                "OpenAI facturó %d tokens de razonamiento con reasoning_effort "
                "en 'none'. Alguien cambió algo, o el servicio cambió.",
                razonamiento,
            )
        contar("openai.llamadas")

    def _enviar(self, peticion: dict[str, Any]) -> dict[str, Any]:
        """Hace la llamada. Reintenta lo transitorio y **nunca un plazo agotado**.

        La diferencia importa aquí más que en ningún otro proveedor. Un
        `ReadTimeout` significa que el servicio no contestó en el plazo, y
        reintentarlo tiene dos costes: se espera el plazo otra vez, y **la
        primera respuesta puede haberse generado y facturado igualmente** aunque
        no llegara a tiempo. Se paga dos veces por una respuesta.

        Es el defecto que la auditoría de la V2.0.2 encontró en `nvidia.py`:
        capturaba `httpx.RequestError`, que incluye los plazos agotados, y los
        reintentaba. Allí costaba 60 segundos en lugar de 30; aquí costaría
        dinero.
        """
        cabeceras = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
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
                # Nunca se reintenta: ver el docstring.
                logger.warning(
                    "OpenAI no contestó en %s s. No se reintenta, para no pagar "
                    "dos veces por una respuesta que no llegó.",
                    self._timeout,
                )
                raise RuntimeError(
                    f"OpenAI no respondió en {self._timeout} s: {exc}"
                ) from exc
            except httpx.RequestError as exc:
                # Un fallo de conexión sí: no ha llegado a generarse nada.
                ultimo = exc
                if intento < intentos - 1:
                    continue
                raise RuntimeError(f"No se pudo conectar con OpenAI: {exc}") from exc

            if r.status_code == 200:
                return r.json()

            # 429 y 5xx son transitorios. Un 400 o un 401 no mejoran repitiendo,
            # y aquí además un 400 es gratis: conviene que suba enseguida con su
            # motivo, que es lo que dice qué parámetro no acepta el modelo.
            if r.status_code in (429, 500, 502, 503, 504) and intento < intentos - 1:
                logger.warning(
                    "OpenAI devolvió %s; reintentando (%d/%d)",
                    r.status_code, intento + 1, intentos - 1,
                )
                continue

            # El cuerpo va en la excepción para que quede en el log; el traductor
            # de errores se encarga de que no llegue a la interfaz.
            raise RuntimeError(f"OpenAI devolvió {r.status_code}: {r.text[:400]}")

        raise RuntimeError(f"OpenAI no respondió: {ultimo}")
