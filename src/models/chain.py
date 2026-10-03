"""
Construcción de la cadena de proveedores LLM.

Vivía duplicada en `dependencies.py` (API) y en `main.py` (CLI), así que añadir un
proveedor obligaba a tocar los dos y era fácil que se desincronizaran. Aquí se
construye una vez y ambos la usan.

El orden se puede cambiar con `MORGAN_LLM_ORDER`, porque cuál conviene primero
depende de qué claves tengas, de su saldo y de su latencia; no es una constante
del proyecto.
"""

import logging

from src.config import Settings
from src.models.base import LLMProvider
from src.models.fallback import FallbackProvider

logger = logging.getLogger(__name__)

# El orden vive en `Settings` y aqui solo se re-exporta.
#
# Estaba escrito dos veces —aqui y en `config.py`— y al cambiarlo en un sitio
# las dos dejaron de coincidir: `load_settings()` devolvia un orden y este
# modulo usaba otro como respaldo. Lo cazo una prueba, y de no haberla habria
# quedado un desacuerdo silencioso sobre que proveedor va primero.
#
# Por que ese orden, y los dos cambios que lo trajeron aqui:
#
# Groq PRIMERO por velocidad (0,42 s) y porque es gratis.
# Gemini SEGUNDO porque contesta de forma estable y tambien es gratis. Es,
#   ademas, el unico que entiende imagenes.
# OpenAI ULTIMO porque **es el unico que cuesta dinero**. No por ser peor:
#   medido, es el mas fiable de los cuatro. Va al final para que solo se pague
#   cuando los gratuitos se han agotado.
#
# NVIDIA ESTUVO AQUI Y SE QUITO. Primero paso de segundo a ultimo porque su
# latencia era impredecible; despues la auditoria de la V2.0.2 midio lo que
# hacia en produccion de verdad: **quince llamadas, quince plazos agotados a los
# 60 s, ningun exito**. Un eslabon con 100% de fallos no es un respaldo, es un
# peaje de un minuto antes de rendirse. El codigo del proveedor sigue ahi y
# funciona —basta poner "nvidia" en MORGAN_LLM_ORDER— pero no se construye por
# defecto.
DEFAULT_ORDER = Settings.llm_order


#: De qué variable sale la clave de cada proveedor. Se nombra la VARIABLE y no
#: el atributo porque el mensaje de error lo lee una persona que va a ir al panel
#: de Render a arreglarlo, y allí lo que ve son variables.
VARIABLE_DE = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def _claves_de(nombre: str, settings: Settings) -> list[tuple[str, str | None]]:
    """Las claves de un proveedor, cada una con **el nombre de su variable**.

    Casi todos tienen una. Groq puede tener varias, porque su cuota es por
    cuenta y varias cuentas son varios cupos; ver `src/models/llavero.py`.

    Se devuelve el nombre de la variable junto a cada clave, y no solo la clave,
    porque el mensaje de error lo va a leer alguien que va a ir al panel de
    Render a arreglarlo. Decirle «la clave de Groq está mal» cuando hay tres no
    le sirve de nada; decirle `GROQ_API_KEY_2` sí.
    """
    if nombre in ("groq", "gemini"):
        varias = settings.groq_api_keys if nombre == "groq" else settings.gemini_api_keys
        una = settings.groq_api_key if nombre == "groq" else settings.gemini_api_key
        claves = varias or ((una,) if una else ())
        return [
            (VARIABLE_DE[nombre] if i == 0 else f"{VARIABLE_DE[nombre]}_{i + 1}", c)
            for i, c in enumerate(claves)
        ]

    unica = {
        "nvidia": settings.nvidia_api_key,
        "openai": settings.openai_api_key,
    }.get(nombre)
    return [(VARIABLE_DE.get(nombre, nombre.upper()), unica)]


def _construir(nombre: str, settings: Settings) -> LLMProvider | None:
    """Instancia un proveedor si tiene una clave **usable**. Nunca lanza.

    **Por qué se revisa la clave antes de montar nada.** Al recrear el servicio
    de producción hubo que teclear 18 secretos, y dos claves de modelo salieron
    tocadas: la de Groq mal transcrita y la de Gemini con un símbolo de libra
    dentro. El resultado fue que los dos proveedores gratuitos fallaban en
    silencio y **el de pago contestaba todos los turnos**, sin que nada lo
    dijera: `/status` seguía diciendo que el modelo estaba disponible y listando
    la cadena entera.

    Una clave con un carácter no ASCII no puede funcionar nunca. Montar un
    proveedor con ella es garantizar un fallo por turno, así que no se monta y
    se dice cuál y por qué. Ver `src/models/claves.py`.
    """
    from src.models.claves import ClaveInutilizable, revisar

    # Se revisan TODAS las claves del proveedor, no solo la primera.
    #
    # Con varias claves la revisión no puede ser «vale o no vale»: una mal
    # copiada no debe llevarse por delante a las buenas. Así que las que no
    # pueden funcionar se caen una a una, cada una con su aviso y el nombre de
    # SU variable, y el proveedor se monta con las que quedan.
    usables: list[str] = []
    for variable, cruda in _claves_de(nombre, settings):
        try:
            clave = revisar(variable, cruda)
        except ClaveInutilizable as exc:
            # A gritos y con el motivo: es lo único que separa esto de un turno
            # que se paga sin que nadie sepa por qué.
            logger.error(
                "%s NO se usa porque no puede funcionar. %s", variable, exc,
            )
            continue
        if clave:
            usables.append(clave)

    if not usables:
        return None

    try:
        if nombre == "groq":
            from src.models.groq import GroqProvider

            # Se le pasan las claves ya revisadas en lugar de dejar que las
            # relea: si una se descartó aquí, no puede volver por la puerta de
            # atrás.
            return GroqProvider(api_key=tuple(usables))

        if nombre == "nvidia":
            from src.models.nvidia import NvidiaProvider

            return NvidiaProvider()

        if nombre == "gemini":
            from src.models.gemini import GeminiProvider

            return GeminiProvider()

        if nombre == "openai":
            from src.models.openai_chat import OpenAIProvider

            return OpenAIProvider()

    except Exception as exc:
        # Un proveedor que no arranca no puede impedir que lo hagan los demás.
        logger.warning("No se pudo inicializar el proveedor '%s': %s", nombre, exc)

    return None


def _relevos_de_groq(principal: LLMProvider, settings: Settings) -> list[LLMProvider]:
    """Los modelos de relevo de Groq, con las MISMAS claves ya revisadas (V2.0.20).

    Van justo detras del principal y antes de Gemini y OpenAI: son gratis, son
    otro cupo (la cuota de Groq es por modelo, medido) y responden en menos de un
    segundo, cuando Gemini tiene 20 peticiones al dia y OpenAI es de pago. Es la
    regla que ya gobernaba la cadena —el gratis mas rapido primero y el de pago
    ultimo— aplicada a los modelos y no solo a los proveedores.

    Se reutilizan las claves del principal en lugar de releerlas: si una se
    descarto por inutilizable, no puede volver por aqui.
    """
    from src.models.groq import GroqProvider

    llavero = getattr(principal, "_llavero", None)
    claves = tuple(llavero._claves) if llavero is not None else ()
    relevos: list[LLMProvider] = []
    for modelo in settings.groq_modelos_relevo:
        if not claves or modelo == settings.groq_model:
            continue
        try:
            relevos.append(GroqProvider(api_key=claves, model_name=modelo))
        except Exception as exc:
            logger.warning("No se pudo montar el relevo de Groq '%s': %s", modelo, exc)
    return relevos


def build_provider_chain(settings: Settings) -> tuple[LLMProvider | None, list[str]]:
    """Devuelve el proveedor a usar y los nombres de los que quedaron activos.

    Con varios disponibles se envuelven en un `FallbackProvider`, que prueba cada
    uno hasta que alguno responda. Con uno solo se devuelve tal cual: envolverlo
    no aportaría nada.
    """
    # El modo de prueba de carga (src/prueba_de_carga.py): modelo simulado, y solo
    # si no hay ninguna clave real ni es el Supabase de producción. Si está pedido
    # y no puede, `comprobar` lanza y el servidor no arranca.
    from src import prueba_de_carga

    if prueba_de_carga.comprobar(settings):
        prueba_de_carga.levantar_frenos_de_altas()
        return prueba_de_carga.ModeloDePrueba(), ["prueba_de_carga"]

    orden = settings.llm_order or list(DEFAULT_ORDER)

    proveedores: list[LLMProvider] = []
    activos: list[str] = []
    for nombre in orden:
        proveedor = _construir(nombre, settings)
        if proveedor is not None:
            proveedores.append(proveedor)
            activos.append(nombre)
            if nombre == "groq":
                for relevo in _relevos_de_groq(proveedor, settings):
                    proveedores.append(relevo)
                    activos.append(f"groq@{relevo._model_name}")

    if not proveedores:
        return None, []

    if len(proveedores) == 1:
        return proveedores[0], activos

    return FallbackProvider(proveedores[0], proveedores[1], *proveedores[2:]), activos
