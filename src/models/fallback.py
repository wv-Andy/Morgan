"""
Proveedor con tolerancia a fallos y conmutación automática (Fallback).
"""

import logging
import time

from rich.console import Console

from src.eventos_turno import emitir
from src.observabilidad import anotar, etapa
from src.models.base import ChatMessage, LLMProvider, LLMResponse
from src.models.capabilities import CapacidadNoDisponible, Capability

logger = logging.getLogger(__name__)
console = Console()


class FallbackProvider(LLMProvider):
    """Cadena de proveedores: intenta cada uno hasta que alguno responda.

    Admite cualquier número de eslabones. Se conserva la firma de dos argumentos
    (`primary` y `fallback`) porque es la que usaba la V1.2 y sigue siendo la
    forma más legible cuando solo hay dos.

    Las conmutaciones se registran en el log de aplicación además de en consola:
    imprimiéndolas solo con Rich, al ejecutar Morgan como servicio no quedaba
    ni rastro de por qué una petición había tardado de más.
    """

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider | None = None,
        *extra: LLMProvider,
    ):
        cadena = [primary]
        if fallback is not None:
            cadena.append(fallback)
        cadena.extend(p for p in extra if p is not None)

        self.providers = cadena
        # Diagnóstico: permite ver cuánto se está usando cada eslabón.
        self.primary_failures = 0
        self.fallback_uses = 0

    @property
    def primary(self) -> LLMProvider:
        return self.providers[0]

    @property
    def fallback(self) -> LLMProvider | None:
        return self.providers[1] if len(self.providers) > 1 else None

    @property
    def capabilities(self) -> frozenset[Capability]:
        """La union de lo que admite la cadena.

        Union y no interseccion: la cadena *puede* atender una imagen mientras
        exista un eslabon que la entienda, aunque el principal no lo haga. Cual
        de ellos la atiende lo decide `generate` al filtrar.
        """
        return frozenset().union(*(p.capabilities for p in self.providers))

    # --- Cuota de los proveedores (2.0-F.3 y 2.0-F.4) ----------------------

    @staticmethod
    def _agotados_al_final(candidatos: list) -> list:
        """Reordena dejando atras los que se sabe agotados.

        **Nunca devuelve una lista mas corta**: reordenar no puede dejar a
        Morgan sin proveedor, y por eso esto no filtra.
        """
        from src.models.cuota import CUOTAS

        # Agotados por cuota y, desde la 4.20, caídos: los dos van al final.
        vivos = [p for p in candidatos if not CUOTAS.pospuesto(p.model_name)]
        agotados = [p for p in candidatos if CUOTAS.pospuesto(p.model_name)]
        if agotados and vivos:
            logger.debug(
                "Se posponen %d proveedor(es) agotado(s): %s",
                len(agotados), ", ".join(p.model_name for p in agotados),
            )
        return vivos + agotados

    @staticmethod
    def _apuntar_si_es_de_cuota(proveedor: str, exc: BaseException, segundos: float = 0.0) -> None:
        """Si el fallo fue por cuota, o el proveedor está caído (4.20), se recuerda para
        no repetirlo enseguida."""
        from src.models.cuota import (
            CUOTAS,
            es_caida,
            es_rechazo_por_cuota,
            segundos_hasta_reintentar,
        )

        if es_rechazo_por_cuota(exc):
            CUOTAS.marcar_agotado(proveedor, segundos_hasta_reintentar(str(exc)))
        elif es_caida(exc):
            CUOTAS.marcar_caido(proveedor, segundos)

    def providers_for(self, capability: Capability) -> list[LLMProvider]:
        """Los eslabones que admiten esa capacidad, en el orden de la cadena."""
        return [p for p in self.providers if p.supports(capability)]

    @property
    def model_name(self) -> str:
        if len(self.providers) == 1:
            return self.providers[0].model_name

        respaldos = ", ".join(p.model_name for p in self.providers[1:])
        return f"{self.providers[0].model_name} [Respaldo: {respaldos}]"

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        requires: Capability = Capability.TEXT,
    ) -> LLMResponse:
        ultimo_error: Exception | None = None

        # Se conmuta solo entre los que admiten lo que se pide. Mandar una imagen
        # a un modelo sin vision no da una respuesta peor: da un error, y ademas
        # gasta una llamada y el tiempo de espera antes de conmutar.
        candidatos = self.providers_for(requires)
        if not candidatos:
            raise CapacidadNoDisponible(
                requires, [p.model_name for p in self.providers]
            )

        # Los que se sabe agotados van al FINAL, no se descartan.
        #
        # 2.0-F.4 del roadmap: sin esto se paga un rechazo en cada llamada, y un
        # turno hace entre una y cinco. Con el principal agotado, eso son hasta
        # cinco viajes de ida y vuelta para que nos digan lo que ya sabiamos.
        #
        # Se REORDENA en lugar de filtrar porque la ventana de agotamiento es una
        # estimacion, y los dos errores posibles no cuestan lo mismo: llamar a
        # uno agotado cuesta 0,02 s, y saltarse a uno que si funcionaba deja a
        # Morgan sin contestar. Ver `src/models/cuota.py`.
        candidatos = self._agotados_al_final(candidatos)

        from src.models.cuota import es_rechazo_por_cuota, es_rechazo_por_tamano

        #: Lo que dejó en su razonamiento un modelo que no contestó (4.0.5): solo si no
        #: contesta ningún otro. Ver `SoloRazonamiento`.
        razonamiento_de_reserva = ""
        for indice, proveedor in enumerate(candidatos):
            # Un relevo de cuota solo entra si lo anterior fallo POR CUOTA
            # (V2.0.20). Si Groq esta caido o rechazo la peticion por su forma,
            # otro modelo de Groq fallaria igual y solo retrasaria llegar a
            # Gemini. Sin error previo si entra: es que el principal ya se sabia
            # agotado y quedo al final.
            #
            # Y tampoco entra si la peticion NO CABIA (3.1): Groq manda ese rechazo
            # como `rate_limit_exceeded`, pero sus modelos comparten tope y fallarian
            # igual. Medido el 2026-09-19 en produccion: dos intentos perdidos antes
            # de llegar a Gemini.
            if (
                getattr(proveedor, "es_relevo_de_cuota", False) is True
                and ultimo_error is not None
                and (not es_rechazo_por_cuota(ultimo_error) or es_rechazo_por_tamano(ultimo_error))
            ):
                logger.info(
                    "Se salta el relevo %s: el fallo anterior fue %s.",
                    proveedor.model_name,
                    "de tamaño" if es_rechazo_por_tamano(ultimo_error) else "no de cuota",
                )
                continue

            inicio = time.monotonic()
            try:
                # Se mide cada intento, incluidos los que fallan. Un proveedor
                # que se cuelga treinta segundos antes de conmutar es tiempo
                # gastado, y esconderlo dejaria un hueco sin explicar.
                with etapa("modelo"):
                    respuesta = proveedor.generate(
                        messages=messages, tools=tools, system_prompt=system_prompt
                    )
                # Cual de la cadena contesto. El campo `model` de la respuesta
                # devuelve la cadena entera —«Groq [Respaldo: NVIDIA, Gemini]»—
                # asi que hasta ahora no habia forma de saber quien hablo.
                anotar("proveedor", proveedor.model_name)
                from src.models.cuota import CUOTAS

                CUOTAS.apuntar_acierto(proveedor.model_name)
                # Contra el PRINCIPAL configurado, no contra la posicion: con el
                # principal ya agotado, la cadena lo manda al final y quien
                # contesta queda primero. Comparar con `indice > 0` hacia que
                # contestara Gemini, o un relevo de Groq, sin que la web dijera
                # «de respaldo». Lo destapo la prueba del relevo (V2.0.20).
                if proveedor is not self.providers[0]:
                    # Que ha contestado un respaldo, dicho de forma que la web
                    # pueda leerlo sin conocer la cadena configurada. Hace falta
                    # porque con el principal agotado un turno pasa de segundos
                    # a un minuto, y sin decirlo se lee como que Morgan se ha
                    # roto. Medido: Groq 429, NVIDIA agota el plazo de 30 s, y
                    # contesta Gemini a los 64.
                    anotar("respaldo", "si")
                    emitir("respaldo", proveedor=proveedor.model_name)
                    self.fallback_uses += 1
                    logger.info(
                        "Respondió el proveedor de respaldo %s", proveedor.model_name
                    )
                else:
                    logger.debug(
                        "Proveedor principal respondió en %.2f s", time.monotonic() - inicio
                    )
                return respuesta

            except Exception as exc:
                ultimo_error = exc
                razonamiento_de_reserva = razonamiento_de_reserva or getattr(exc, "razonamiento", "")
                anotar("proveedor_fallo", proveedor.model_name)
                self._apuntar_si_es_de_cuota(proveedor.model_name, exc, time.monotonic() - inicio)
                if indice == 0:
                    self.primary_failures += 1

                siguiente = (
                    candidatos[indice + 1].model_name
                    if indice + 1 < len(candidatos)
                    else None
                )
                logger.warning(
                    "El proveedor %s falló tras %.2f s: %s.%s",
                    proveedor.model_name,
                    time.monotonic() - inicio,
                    exc,
                    f" Conmutando a {siguiente}" if siguiente else " No quedan alternativas.",
                )
                console.print()
                console.print(f"  [yellow]⚠ {proveedor.model_name} no respondió: {exc}[/yellow]")
                if siguiente:
                    console.print(f"  [dim cyan]↳ Conmutando automáticamente a {siguiente}...[/dim cyan]")
                console.print()

        if razonamiento_de_reserva:
            # Nadie más contestó: mejor el razonamiento que una respuesta vacía (V2.x).
            logger.warning("Ningún proveedor contestó; se usa el razonamiento de uno como respuesta")
            return LLMResponse(type="text", content=razonamiento_de_reserva)
        logger.error(
            "Fallaron todos los proveedores LLM capaces de '%s' (%d de %d)",
            requires, len(candidatos), len(self.providers),
        )
        # Se borra quién contestó. Un turno hace varias llamadas, y si la
        # primera la atendió Groq y la última no la atendió nadie, el turno
        # decía «model: Groq» sobre una respuesta que era el aviso de error de
        # Morgan. Medido el 2026-09-16: una lectura de página dejó la tercera
        # llamada en 25.789 tokens, Groq la rechazó, Gemini agotó el plazo, y la
        # respuesta seguía diciendo Groq. Sin proveedor anotado y con
        # `proveedor_fallo`, la ruta dice «ninguno respondio».
        anotar("proveedor", "")
        raise ultimo_error if ultimo_error else RuntimeError("Sin proveedores LLM disponibles")
