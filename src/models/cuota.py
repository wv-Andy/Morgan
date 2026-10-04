"""
Cuánta cuota le queda a cada proveedor, y a cuál no merece la pena llamar.

Cierra los dos últimos puntos de 2.0-F del roadmap:

> 3. **Contar los tokens gastados** para poder avisar antes de agotar la cuota,
>    no después. Groq devuelve el gasto en cada respuesta y hoy se descarta.
> 4. **Cambiar el orden de la cadena** cuando el principal está agotado, para no
>    pagar un 429 en cada llamada.

## Por qué importa, con los números

La [medición de la 2.0.5](../../docs/mediciones.md) dejó claro que a Morgan
ya no le sobra latencia: un turno trivial son 0,66 s. Lo que sí tiene es un
techo de **capacidad**: unos 35 turnos al día con una cuenta de Groq, y unos 70
con las dos. Y otro que muerde antes, los 8.000 tokens **por minuto**, medido en
[las llamadas por turno](../../docs/mediciones.md).

Y ese techo llegaba **sin avisar**. Groq devuelve el gasto en cada respuesta y
Morgan lo tiraba, así que la primera señal de haberse pasado era un 429 en
mitad de un turno.

## La propiedad de seguridad que gobierna este módulo

**Si todos los proveedores parecen agotados, se llama igual.**

La ventana de agotamiento es una *estimación*: sale de lo que dice el proveedor
en su 429, y puede equivocarse —por un reloj desfasado, por un mensaje que
cambió de formato, por una cuota que se renovó antes—. Y el coste de los dos
errores posibles no es simétrico:

| Si la estimación se equivoca | Consecuencia |
|---|---|
| Llamamos a uno agotado | Un 429. Cuesta 0,02 s |
| **Saltamos a uno que sí funcionaba** | **Morgan no contesta** |

Así que saltar es una optimización, no una regla, y nunca puede dejar la cadena
vacía. Hay una prueba que lo fija.

## Vive en memoria, y eso es deliberado

Un reinicio lo olvida. Con el plan gratuito de Render eso pasa a menudo, y el
efecto es que Morgan vuelve a pagar un 429 para redescubrir que Groq está
agotado. Es un coste de 0,02 s cada pocas horas.

La alternativa —persistirlo— significa una consulta a la base en cada llamada
al modelo para leer un dato que sirve para ahorrar 0,02 s. Sería gastar 45 ms
para ahorrar 20. Es el mismo razonamiento que ya llevan
[los frenos de las cuentas](../identidad/cuentas.py).
"""

import logging
import re
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Cuánto se da por agotado un proveedor cuando su 429 no dice cuándo volver.
#:
#: Conservador a propósito: si nos pasamos, saltamos a alguien que ya funcionaba
#: y perdemos un proveedor bueno durante ese rato. Un minuto es suficiente para
#: no repetir el 429 en el mismo turno, que es el caso que se quiere evitar.
VENTANA_POR_DEFECTO = 60.0

#: Tope de la ventana, para que un proveedor no quede fuera medio día por un
#: mensaje mal interpretado.
VENTANA_MAXIMA = 1800.0

#: Por debajo de esta espera, **no se abandona al proveedor: se espera**.
#:
#: ## Los dos limites de Groq, que no son el mismo
#:
#: | Limite | Lo que dice el 429 | Que conviene |
#: |---|---|---|
#: | 8.000 tokens por MINUTO | «try again in 112ms» … «in 9.25s» | esperar |
#: | 200.000 tokens por DIA | «try again in 10m53s» | cambiar de proveedor |
#:
#: Tratarlos igual costo 93 segundos y dinero: Morgan dejo Groq por una espera
#: corta, se fue a Gemini —que tardo 29 s en dar un 504— y acabo pagando a
#: OpenAI.
#:
#: ## De donde sale el 10, que no es una intuicion
#:
#: Los cuatro caminos posibles cuando Groq esta al limite del minuto, medidos:
#:
#: | Camino | Coste |
#: |---|---|
#: | **Esperar lo que Groq pide** | **0,1 a 9,3 s, y gratis** |
#: | Dejar que el SDK reintente solo | 13 a 26 s |
#: | Pasar a Gemini | 29 a 36 s, y acabo en un 504 |
#: | Pasar a OpenAI | 1 a 1,7 s, y **cuesta dinero** |
#:
#: Esperar gana a los dos gratuitos. Pierde en tiempo contra OpenAI, y aun asi
#: se prefiere: el orden de la cadena existe justamente para que el dinero solo
#: se gaste cuando los gratuitos se han AGOTADO, y un freno que se suelta en
#: nueve segundos no es un agotamiento.
#:
#: Diez segundos cubre las esperas del minuto medidas y deja fuera las del dia,
#: que son de minutos. Por encima de eso la cadena tiene razon.
#:
#: ## Y por que no hace falta acotar cuantas veces se espera en un turno
#:
#: Un turno hace hasta 6 llamadas, asi que 6 esperas de 10 s no cabrian en el
#: plazo de 85 s de la nube. Pero no pueden darse: **lo que se espera es
#: exactamente el tiempo que tarda en rellenarse la ventana**, asi que la
#: llamada siguiente arranca con la ventana limpia. Una espera la arregla.
ESPERA_CORTA_MAXIMA = 10.0

#: Un proveedor **caído** (4.20: tiempo agotado, sin conexión, un 5xx) pasa al final de la
#: cadena esta ventana, que se dobla con cada caída seguida hasta el tope. Antes se le
#: volvía a llamar en cada petición y cada una pagaba su plazo entero (medido: NVIDIA agota
#: sus 30 s y contesta Gemini a los 64). Como con la cuota, **se pospone, nunca se quita**:
#: si los demás fallan, se le llama igual. Y el primer acierto lo devuelve a su sitio.
CAIDO_PRIMERA = 30.0
CAIDO_MAXIMA = 600.0
#: Un fallo que tarda esto o más ya ha costado: se pospone a la primera.
CAIDO_LENTO = 5.0

#: A partir de qué fracción de la cuota conocida se avisa. El aviso solo sirve
#: si llega con margen para hacer algo.
AVISAR_AL = 0.8

#: Lo que Morgan sabe de la cuota diaria de cada proveedor, en tokens. Sale de
#: los propios mensajes de rechazo: Groq dice «tokens per day (TPD): Limit
#: 200000». `None` significa que no se sabe, y entonces no se avisa de nada.
CUOTA_DIARIA_CONOCIDA = {
    "groq": 200_000,
}


def familia(nombre: str) -> str:
    """De qué proveedor es una etiqueta, sea la forma que sea.

    Las etiquetas que llegan aquí tienen tres formas, y todas significan Groq:

    | etiqueta | de dónde sale |
    |---|---|
    | `groq` | el nombre en el orden de la cadena |
    | `Groq:openai/gpt-oss-120b` | `model_name` del proveedor |
    | `Groq#2` | una clave concreta del llavero |
    | `Groq@openai/gpt-oss-20b#2` | esa clave, con un modelo de relevo (V2.0.20) |

    Y el tope de cuota se conoce por proveedor, no por etiqueta. Esto estaba
    escrito a mano en dos sitios, cortando solo por los dos puntos, y con la
    llegada del llavero eso pasaba a devolver `groq#2` y a **no encontrar el
    tope de nadie**: el aviso del 80% habría dejado de salir sin decir nada,
    que es exactamente la clase de fallo que este módulo vino a evitar.
    """
    return nombre.split("#")[0].split("@")[0].split(":")[0].lower()


@dataclass
class _Estado:
    """Lo que se sabe de un proveedor."""

    tokens_entrada: int = 0
    tokens_salida: int = 0
    llamadas: int = 0
    rechazos: int = 0
    #: Instante hasta el que se le da por agotado. `0.0` = disponible.
    agotado_hasta: float = 0.0
    #: Lo mismo, pero por estar **caído** (4.20): tiempo agotado, sin conexión, un 5xx.
    caido_hasta: float = 0.0
    #: Caídas seguidas, sin un acierto en medio: cada una dobla la ventana.
    caidas_seguidas: int = 0
    #: Cuándo empezó a contar la ventana de tokens.
    desde: float = field(default_factory=time.time)

    @property
    def tokens(self) -> int:
        return self.tokens_entrada + self.tokens_salida


class _Registro:
    """El estado de todos los proveedores, compartido por el proceso.

    **Módulo y no instancia**, y hay motivo escrito: los dos frenos de
    `ServicioDeCuentas` vivían en `self`, el servicio se construye en cada
    petición, y por eso **ninguno frenó nunca**. Aquí se evita el mismo error de
    entrada.
    """

    #: Cada cuánto se reinicia el contador de tokens. Las cuotas de los
    #: proveedores son diarias.
    VENTANA_TOKENS = 86_400.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._estados: dict[str, _Estado] = {}

    def _de(self, proveedor: str) -> _Estado:
        return self._estados.setdefault(proveedor, _Estado())

    # --- Lo que se apunta ---------------------------------------------------

    def apuntar_uso(self, proveedor: str, entrada: int, salida: int) -> None:
        """Suma lo gastado en una llamada, y avisa si se acerca al tope."""
        # Y a la petición en curso, para saber lo que cuesta cada turno (4.1.5).
        from src.observabilidad import sumar_tokens

        sumar_tokens(entrada, salida)
        with self._lock:
            e = self._de(proveedor)
            ahora = time.time()
            if ahora - e.desde >= self.VENTANA_TOKENS:
                e.tokens_entrada = e.tokens_salida = e.llamadas = 0
                e.desde = ahora
            e.tokens_entrada += max(0, entrada)
            e.tokens_salida += max(0, salida)
            e.llamadas += 1
            gastado, llamadas = e.tokens, e.llamadas

        tope = CUOTA_DIARIA_CONOCIDA.get(familia(proveedor))
        if tope and gastado >= tope * AVISAR_AL:
            # El aviso que no existía. Antes, la primera señal de haberse pasado
            # era un 429 en mitad de un turno.
            logger.warning(
                "%s ha gastado %d de sus %d tokens diarios (%.0f%%) en %d "
                "llamadas. Quedan unos %d turnos antes de agotarlo.",
                proveedor, gastado, tope, 100 * gastado / tope, llamadas,
                max(0, (tope - gastado) // max(1, gastado // max(1, llamadas))),
            )

    def marcar_agotado(self, proveedor: str, segundos: float | None = None) -> float:
        """Da por agotado un proveedor. Devuelve cuántos segundos."""
        espera = VENTANA_POR_DEFECTO if segundos is None else segundos
        # El suelo es 0 y no 1 segundo a proposito: el limite por MINUTO da
        # esperas de decimas, y redondearlas a un segundo entero convertia una
        # pausa inapreciable en un veto que duraba mas que la propia espera.
        espera = max(0.0, min(float(espera), VENTANA_MAXIMA))
        with self._lock:
            e = self._de(proveedor)
            e.agotado_hasta = time.time() + espera
            e.rechazos += 1
        logger.info(
            "%s se da por agotado durante %.0f s: no se le llamará hasta "
            "entonces, para no pagar un rechazo en cada llamada.",
            proveedor, espera,
        )
        return espera

    def marcar_caido(self, proveedor: str, segundos_perdidos: float = 0.0) -> float:
        """Apunta una caída del proveedor (4.20). Se le pospone si **costó**: si tardó
        `CAIDO_LENTO` o más en fallar, o si es la segunda seguida. Un fallo rápido y suelto
        (un corte de red de 0,1 s) puede estar arreglado en la llamada siguiente, y volver
        a probar casi no cuesta. La ventana se dobla con cada caída seguida, de
        `CAIDO_PRIMERA` a `CAIDO_MAXIMA`. Devuelve cuántos segundos (0 si no se pospone)."""
        with self._lock:
            e = self._de(proveedor)
            e.caidas_seguidas += 1
            seguidas = e.caidas_seguidas
            if segundos_perdidos < CAIDO_LENTO and seguidas < 2:
                return 0.0
            espera = min(CAIDO_PRIMERA * 2 ** (seguidas - 1), CAIDO_MAXIMA)
            e.caido_hasta = time.time() + espera
        logger.warning(
            "%s se da por caído durante %.0f s (%d caída(s) seguida(s)): pasa al final de "
            "la cadena para no esperar su plazo en cada petición.", proveedor, espera, seguidas,
        )
        return espera

    def apuntar_acierto(self, proveedor: str) -> None:
        """Contestó: deja de estar caído y la ventana vuelve a empezar."""
        with self._lock:
            e = self._estados.get(proveedor)
            if e is not None and (e.caidas_seguidas or e.caido_hasta):
                e.caidas_seguidas = 0
                e.caido_hasta = 0.0
                logger.info("%s vuelve a contestar: deja de estar caído.", proveedor)

    # --- Lo que se pregunta -------------------------------------------------

    def caido(self, proveedor: str) -> bool:
        with self._lock:
            e = self._estados.get(proveedor)
            return e is not None and time.time() < e.caido_hasta

    def pospuesto(self, proveedor: str) -> bool:
        """Agotado o caído: va al final de la cadena (nunca fuera de ella)."""
        return self.agotado(proveedor) or self.caido(proveedor)

    def agotado(self, proveedor: str) -> bool:
        with self._lock:
            e = self._estados.get(proveedor)
            if e is None or not e.agotado_hasta:
                return False
            if time.time() >= e.agotado_hasta:
                e.agotado_hasta = 0.0
                return False
            return True

    def disponibles(self, proveedores: list[str]) -> list[str]:
        """Los que no están agotados. **Si ninguno lo está, devuelve todos.**

        Es la propiedad de seguridad del módulo: saltar es una optimización y
        nunca puede dejar la cadena vacía. Ver la cabecera.
        """
        vivos = [p for p in proveedores if not self.agotado(p)]
        return vivos or list(proveedores)

    def resumen(self) -> dict[str, dict]:
        ahora = time.time()
        with self._lock:
            return {
                nombre: {
                    "tokens": e.tokens,
                    "llamadas": e.llamadas,
                    "rechazos": e.rechazos,
                    "agotado": bool(e.agotado_hasta and ahora < e.agotado_hasta),
                    "vuelve_en": max(0, int(e.agotado_hasta - ahora)) if e.agotado_hasta else 0,
                    "caido": ahora < e.caido_hasta,
                    "caidas_seguidas": e.caidas_seguidas,
                    "tope_conocido": CUOTA_DIARIA_CONOCIDA.get(familia(nombre)),
                }
                for nombre, e in self._estados.items()
            }

    def reiniciar(self) -> None:
        """Para las pruebas: es estado de proceso."""
        with self._lock:
            self._estados.clear()


#: Compartido a propósito.
CUOTAS = _Registro()


# --- Leer el «vuelve en» de un rechazo ---------------------------------------
#
# Cada proveedor lo dice a su manera, y ninguno usa `Retry-After` en el cuerpo.
# Se leen los dos formatos vistos en produccion y se cae a la ventana por
# defecto si no se reconoce nada: adivinar mal aqui solo cuesta un 429.

#: Groq: «Please try again in 6m27.504s»
_GROQ = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.I)

#: Y tambien «Please try again in 112.499999ms», que es el caso del limite por
#: MINUTO y no por dia.
#:
#: Este patron faltaba, y lo caro no era no leerlo: era lo que pasaba al no
#: leerlo. Sin reconocerlo, `segundos_hasta_reintentar` devolvia `None`, se
#: aplicaba `VENTANA_POR_DEFECTO` y **una espera de 112 milisegundos se
#: convertia en un veto de 60 segundos** sobre esa clave. Medido: un turno costo
#: 93 segundos y dinero de OpenAI porque Morgan abandono Groq por 0,1 s de
#: espera.
#:
#: Va ANTES que el de segundos en `segundos_hasta_reintentar`, porque «112ms»
#: no lo casa el otro patron pero conviene no depender de eso.
_GROQ_MS = re.compile(r"try again in ([\d.]+)ms", re.I)

#: Gemini: «Please retry in 25.342565212s» y «'retryDelay': '25s'»
_GEMINI = re.compile(r"(?:retry in |retryDelay['\"]?:\s*['\"])([\d.]+)s", re.I)


#: Gemini, cupo por día: «quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'».
_POR_DIA = re.compile(r"PerDay", re.I)


def segundos_hasta_reintentar(mensaje: str) -> float | None:
    """Cuánto dice el proveedor que hay que esperar, o `None` si no lo dice."""
    if not mensaje:
        return None

    m = _GROQ_MS.search(mensaje)
    if m:
        return float(m.group(1)) / 1000.0

    m = _GROQ.search(mensaje)
    if m:
        minutos = float(m.group(1) or 0)
        return minutos * 60 + float(m.group(2))

    # Un cupo **diario** sin una espera fiable (4.1.5). Medido con Gemini gratuito: 20
    # peticiones al día por modelo, y al agotarlas dice «retry in 28s». Fiarse de eso era
    # volver a probarlo cada medio minuto todo el día, pagando un rechazo en cada turno
    # que necesitara el respaldo. Se usa el tope (`VENTANA_MAXIMA`). Groq dice la espera
    # de verdad («try again in 10m53s») y se ha leído arriba.
    if _POR_DIA.search(mensaje):
        return VENTANA_MAXIMA

    m = _GEMINI.search(mensaje)
    if m:
        return float(m.group(1))

    return None


def es_rechazo_por_cuota(exc: BaseException) -> bool:
    """Si el fallo es «te has pasado de cuota» y no otra cosa.

    Se mira el texto porque cada proveedor lanza su propio tipo de excepción y
    envolverlos a todos costaría más que esto. Lo que se busca son las señales
    que los tres usan de verdad, vistas en los registros de producción.
    """
    texto = str(exc).lower()
    return any(s in texto for s in (
        "429",
        "rate limit",
        "rate_limit",
        "resource_exhausted",
        "quota",
        "too many requests",
    ))


def es_caida(exc: BaseException) -> bool:
    """Si el fallo es del **proveedor** (no contesta, no se llega, se le rompió algo) y no
    de la petición (4.20).

    Un 400 no cuenta: lo rechaza por cómo es la petición, y otro igual no fallaría; ni la
    cuota, que tiene su ventana propia. Lo que sí: tiempo agotado, sin conexión y 5xx.
    """
    if es_rechazo_por_cuota(exc) or es_rechazo_por_tamano(exc):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    from src.models.errors import _codigo_http

    codigo = _codigo_http(exc)
    if codigo is not None:
        return codigo >= 500 or codigo == 408
    texto = str(exc).lower()
    return any(s in texto for s in (
        "timeout", "timed out", "deadline exceeded", "connection", "unavailable",
        "overloaded", "bad gateway", "internal server error", "unreachable",
    ))


def es_rechazo_por_tamano(exc: BaseException) -> bool:
    """Si el fallo es «esta petición no cabe», no «te has pasado de cuota».

    Se parecen —Groq manda las dos como `rate_limit_exceeded`— pero **piden cosas
    distintas**: esperar no arregla una petición demasiado grande, y **otro modelo del
    mismo proveedor tiene el mismo tope**, así que reintentar ahí solo retrasa llegar a
    uno con más sitio.

    Medido en producción el 2026-09-19, con mi PC conectado: una llamada de 8.459
    tokens (tope de Groq: 8.000 por minuto) se probó en `gpt-oss-120b` y en `gpt-oss-20b`,
    fallando igual, antes de pasar a Gemini.
    """
    texto = str(exc).lower()
    return any(s in texto for s in (
        "413",
        "request too large",
        "reduce your message size",
        "context length",
        "too many tokens",
    ))
