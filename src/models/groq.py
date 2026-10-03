"""
Proveedor LLM para Groq Cloud utilizando el SDK oficial groq.
Proporciona inferencia de ultra alta velocidad y soporte nativo de function calling.
"""

import logging
import time
from typing import Any
from groq import Groq

from src.config import get_settings
from src.models.base import LLMProvider, ChatMessage, LLMResponse
from src.models.capabilities import Capability, capacidades_de_modelo_groq
from src.models.llavero import Llavero
from src.models.openai_format import (
    from_openai_message,
    to_openai_messages,
    to_openai_tools,
)

logger = logging.getLogger(__name__)


class GroqProvider(LLMProvider):
    """Proveedor de Groq que implementa la interfaz universal LLMProvider."""

    def __init__(
        self,
        api_key: str | tuple[str, ...] | list[str] | None = None,
        model_name: str | None = None,
    ):
        """`api_key` admite una clave o varias.

        Varias porque la cuota de Groq es de la cuenta: dos claves de dos
        cuentas son dos cupos, y eso es lo único que sube el techo de capacidad.
        Está medido y razonado en `src/models/llavero.py`.

        Una sola sigue valiendo, y en la misma posición, porque así la llaman
        las pruebas y la CLI cuando quieren una concreta. Lo que se pase a mano
        manda sobre la configuración.
        """
        settings = get_settings()
        if isinstance(api_key, str):
            claves: tuple[str, ...] = (api_key,)
        elif api_key:
            claves = tuple(api_key)
        else:
            claves = settings.groq_api_keys or (
                (settings.groq_api_key,) if settings.groq_api_key else ()
            )
        if not claves:
            raise ValueError(
                "GROQ_API_KEY no configurada. "
                "Agrega tu GROQ_API_KEY en el archivo .env"
            )

        self._model_name = model_name or settings.groq_model

        # Las claves de un modelo de relevo se apuntan con el modelo en la
        # etiqueta: `Groq@openai/gpt-oss-20b#1`. La cuota de Groq es por modelo
        # (medido, ver `groq_modelos_relevo` en config.py), asi que la clave 1
        # agotada para 120b NO esta agotada para 20b, y con la misma etiqueta el
        # llavero del relevo la habria pospuesto sin motivo. El principal
        # conserva `Groq#1`: es lo que ya estaba apuntado y lo que leen /status y
        # los registros.
        base = "Groq" if self._model_name == settings.groq_model else f"Groq@{self._model_name}"
        self._llavero = Llavero(base, claves)

        # Un cliente por clave, montado al arrancar. Construir un `Groq` no sale
        # a la red —solo guarda la clave y los plazos— así que tenerlos hechos
        # no cuesta nada y evita montar uno en mitad de un turno.
        #
        # Sin el plazo se hereda el del SDK (connect 5 s, read 60 s), lo que
        # permite que una sola llamada consuma minutos.
        #
        # **`max_retries=0` a propósito, y con medición detrás.** El SDK
        # reintenta por su cuenta los 429, con su propia espera, y eso hacía dos
        # cosas malas a la vez:
        #
        # | | SDK reintentando | sin reintentar |
        # |---|---|---|
        # | 6 llamadas seguidas | **83,4 s** | **3,7 s** |
        # | reparto entre las 2 claves | 8 y 2 | 3 y 3 |
        #
        # Esperaba 13 a 26 segundos cuando el propio mensaje de Groq pedía
        # décimas, y **se tragaba el 429**, así que el llavero no llegaba a
        # enterarse y la segunda clave apenas se usaba. Morgan sabe hacerlo
        # mejor: rotar de clave es instantáneo y gratis, y esperar es esperar lo
        # que el mensaje dice y no lo que el SDK calcula.
        #
        # `MORGAN_LLM_MAX_RETRIES` sigue valiendo para los demás proveedores,
        # que no tienen llavero con el que rotar.
        self._clientes = {
            etiqueta: Groq(
                api_key=clave,
                timeout=float(settings.llm_timeout),
                max_retries=0,
            )
            for etiqueta, clave in self._llavero.turnos()
        }

    @property
    def client(self) -> Groq:
        """El cliente de la clave que toca ahora.

        Era un atributo cuando solo había una clave, y hay pruebas que lo leen y
        lo sustituyen. Se mantiene el nombre para no romperlas.
        """
        etiqueta, _ = self._llavero.turnos()[0]
        return self._clientes[etiqueta]

    @client.setter
    def client(self, cliente: Groq) -> None:
        """Sustituye el cliente de **todas** las claves.

        Parece más de lo que se pide, y es a propósito. Lo único que asigna esto
        son las pruebas, que ponen un doble para no salir a la red. Si sustituyera
        solo el cliente de la clave que toca, una rotación durante la prueba
        pasaría al de la siguiente —que es de verdad— y la prueba llamaría a
        Groq sin querer. Un doble a medias es peor que ninguno.
        """
        self._clientes = {e: cliente for e in self._clientes}

    @property
    def claves_disponibles(self) -> int:
        """Cuántas claves lleva el llavero. Para `/status` y los registros."""
        return len(self._llavero)

    @property
    def es_relevo_de_cuota(self) -> bool:
        """Si este eslabon solo debe usarse cuando el anterior se quedo sin cuota.

        Un modelo de relevo vive en la misma infraestructura que el principal: si
        Groq esta caido, o rechaza la peticion por su forma, el relevo fallara
        igual y solo anadira espera antes de llegar a Gemini. Solo tiene sentido
        cuando el rechazo fue de cuota, que es lo unico que otro modelo si puede
        atender. Ver `FallbackProvider.generate`.
        """
        return self._model_name != get_settings().groq_model

    @property
    def model_name(self) -> str:
        return f"Groq:{self._model_name}"

    @property
    def capabilities(self) -> frozenset[Capability]:
        # Groq sirve familias muy distintas bajo la misma API: la capacidad
        # depende del modelo configurado, no del proveedor.
        return capacidades_de_modelo_groq(self._model_name)

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        requires: Capability = Capability.TEXT,
    ) -> LLMResponse:
        # La conversion vive en openai_format porque NVIDIA NIM habla el mismo
        # dialecto: dos copias de esto acabarian divergiendo.
        peticion: dict[str, Any] = {
            "model": self._model_name,
            "messages": to_openai_messages(messages, system_prompt),
        }

        # Sin herramientas hay que OMITIR los dos campos, no enviarlos a None:
        # Groq rechaza tool_choice=null con un 400 ("Only allowed string values
        # for 'tool_choice' are [none, auto, required]").
        herramientas = to_openai_tools(tools)
        if herramientas:
            peticion["tools"] = herramientas
            peticion["tool_choice"] = "auto"

        return self._con_las_claves_en_orden(peticion)

    def _con_las_claves_en_orden(self, peticion: dict[str, Any]) -> LLMResponse:
        """Llama a Groq probando las claves del llavero hasta que una responda.

        **Solo se pasa a la siguiente clave si el rechazo es por cuota.** Un 401
        o un 400 le pasarían igual a todas —es la misma petición y el mismo
        modelo— así que reintentar solo serviría para multiplicar por N el
        tiempo que tarda en fallar. Un 429 es lo contrario: es exactamente lo
        que otra cuenta sí puede atender.

        Y el rechazo se apunta **siempre**, también el de la última clave. Es lo
        que permite que el próximo turno no vuelva a empezar por una clave que
        ya se sabe agotada, y lo que hace que la cadena posponga Groq entero
        cuando de verdad no queda ninguna. Ver `src/models/cuota.py`.

        ## Groq tiene DOS límites, y confundirlos costaba dinero

        | Límite | Lo que dice el 429 | Qué conviene hacer |
        |---|---|---|
        | 8.000 tokens por **minuto** | «try again in 112ms» | esperar |
        | 200.000 tokens por **día** | «try again in 10m53s» | irse |

        Los dos son un `429` y Morgan los trataba igual. Medido en la medición
        del primer punto de la 2.1: un turno **costó 93 segundos y dinero de
        OpenAI** porque Morgan abandonó Groq por una espera de 0,1 s, se fue a
        Gemini —que tardó 29 s en dar un 504— y acabó pagando.

        El orden correcto, y el que hace este método, es de más barato a más
        caro:

        1. **Otra clave**, si queda alguna. Es gratis y es instantáneo: cada
           cuenta tiene su propio cupo por minuto.
        2. **Esperar**, si todas dijeron una espera corta. Cuesta esos
           milisegundos.
        3. **Rendirse** y dejar que la cadena pase a otro proveedor. Solo
           cuando la espera es larga, que es cuando de verdad es la cuota
           diaria.
        """
        from src.models.cuota import (
            CUOTAS,
            ESPERA_CORTA_MAXIMA,
            es_rechazo_por_cuota,
            segundos_hasta_reintentar,
        )

        # Lo que acota las vueltas es un PRESUPUESTO DE RELOJ, no un contador.
        #
        # Empezó siendo «una vuelta extra y ya», y la traza contra Groq mostró
        # que no bastaba: una llamada esperó 1,65 s, volvió a encontrarse el
        # límite pidiendo 0,44 s más, y se rindió **a 0,44 segundos de
        # funcionar**. La ventana del minuto se rellena por partes, así que la
        # espera correcta puede llegar en dos trozos.
        #
        # Y el presupuesto se mide con el RELOJ y no sumando las siestas, que
        # fue el segundo intento. Sumar siestas acota el tiempo dormido y deja
        # libres los viajes: con esperas de 100 ms y 10 s de presupuesto salen
        # cien vueltas, y cada vuelta son dos peticiones de verdad a Groq. El
        # reloj lo acota todo con un solo número, y el número dice lo que
        # significa: **esta llamada no gastará más de 10 segundos intentándolo
        # con Groq antes de dejar paso al siguiente proveedor.**
        limite: float | None = None

        # Y un tope de vueltas además del reloj, que **no es la política sino
        # la garantía**. El presupuesto de reloj expresa la intención; este
        # número asegura que el bucle termina aunque el reloj no avance como se
        # espera. Ocho vueltas no llegan a estorbar: la traza contra Groq
        # necesitó dos, y cada vuelta cuesta una petición rechazada por clave.
        MAXIMO_VUELTAS = 8
        vueltas = 0

        while True:
            vueltas += 1
            turnos = self._llavero.turnos()
            if not turnos:
                # Inalcanzable hoy: `Llavero.turnos()` nunca devuelve una lista
                # vacía y hay pruebas que lo fijan. Pero si alguna vez lo
                # hiciera, este `while True` giraría para siempre sin llamar a
                # nadie ni lanzar nada. Lo descubrió la batería de mutaciones:
                # al filtrar en lugar de reordenar, la prueba se COLGABA en vez
                # de fallar. Mejor un error claro que un turno que no vuelve.
                raise RuntimeError("El llavero de Groq no ha devuelto ninguna clave")
            esperas: list[float] = []

            for numero, (etiqueta, _) in enumerate(turnos, start=1):
                try:
                    completion = self._clientes[etiqueta].chat.completions.create(
                        **peticion
                    )
                except Exception as exc:
                    if not es_rechazo_por_cuota(exc):
                        raise
                    espera = segundos_hasta_reintentar(str(exc))
                    CUOTAS.marcar_agotado(etiqueta, espera)
                    # Sin «vuelve en» no se sabe si son décimas o diez minutos,
                    # y esperar a ciegas puede costar el plazo del turno. El
                    # infinito hace que nunca quepa en el presupuesto.
                    esperas.append(
                        espera if espera is not None else float("inf")
                    )
                    if numero < len(turnos):
                        logger.info(
                            "%s se ha quedado sin cuota. Se prueba con %s: es "
                            "otra cuenta, así que tiene su propio cupo.",
                            etiqueta, turnos[numero][0],
                        )
                        continue

                    # Se han probado todas las claves. ¿Cabe esperar?
                    ahora = time.monotonic()
                    if limite is None:
                        limite = ahora + ESPERA_CORTA_MAXIMA
                    minima = min(esperas)
                    if ahora + minima > limite or vueltas >= MAXIMO_VUELTAS:
                        raise

                    logger.info(
                        "Las %d clave(s) de Groq están al límite por minuto, y "
                        "la más cercana vuelve en %.0f ms. Se espera en lugar "
                        "de cambiar de proveedor: cambiar cuesta más que "
                        "esperar. Queda %.1f s de los %.0f de presupuesto.",
                        len(turnos), minima * 1000, limite - ahora,
                        ESPERA_CORTA_MAXIMA,
                    )
                    time.sleep(minima)
                    break  # otra vuelta

                self._apuntar_gasto(completion, etiqueta)
                return from_openai_message(completion.choices[0].message)

    def _apuntar_gasto(self, completion, etiqueta: str | None = None) -> None:
        """Apunta los tokens que Groq dice haber gastado.

        **Groq devuelve esto en cada respuesta y Morgan lo tiraba.** Era el
        punto 3 de 2.0-F: sin contarlo, la primera senal de haberse pasado de
        los 200.000 tokens diarios era un 429 en mitad de un turno. Ahora se
        avisa al 80%, que llega con margen para hacer algo.
        """
        uso = getattr(completion, "usage", None)
        if uso is None:
            return
        from src.models.cuota import CUOTAS

        # Se apunta a nombre de LA CLAVE, no del proveedor. La cuota de Groq es
        # de la cuenta, asi que dos claves son dos cupos y sumarlos juntos haria
        # que el aviso del 80% saltara a mitad del primero.
        CUOTAS.apuntar_uso(
            etiqueta or self.model_name,
            int(getattr(uso, "prompt_tokens", 0) or 0),
            int(getattr(uso, "completion_tokens", 0) or 0),
        )
