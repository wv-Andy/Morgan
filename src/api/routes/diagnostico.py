"""
Sondas de diagnóstico (V2.0).

Rutas que no sirven para usar Morgan: sirven para **medirlo**. Existen porque
este proyecto ya lleva dos diagnósticos equivocados por deducir en lugar de
medir, y las dos veces lo que faltaba era poder provocar el caso a voluntad.

## La primera sonda: el goteo

Responde una pregunta concreta del roadmap (2.0-D) que hoy bloquea la decisión
sobre el streaming:

> El proxy de Vercel corta a los 120 segundos. ¿Cuenta la **duración total** de
> la respuesta, o el tiempo **sin recibir nada**?

**CONTESTADA el 2026-09-11: no es por duración total.** Con `segundos=180`, la
respuesta llegó entera:

```
linea=180 t=180.000
fin t=180.001 lineas=180
```

Y cada línea salió en su segundo exacto —`linea=167 t=167.001`— así que no hubo
búfer por el camino: la medida vale.

Lo que eso significa: **el streaming levanta el techo de los turnos largos** sin
tocar la infraestructura. Era la mejor de las dos respuestas posibles.

Había un indicio a favor —Vercel escribe «if the external server does not
**respond** within 120 seconds»— y un indicio no es una medición. Esta vez la
lectura optimista de la frase resultó ser la correcta, pero eso se supo después
de medir, que es el orden que importa.

**Lo que queda por medir: cuánto silencio tolera.** Que una respuesta que habla
cada segundo sobreviva 180 no dice cuánto puede callar entre líneas. Para eso
está `cada`, que admite hasta 150 segundos: con `segundos=300&cada=130` las
líneas salen en 0, 130 y 260, y si el proxy corta por inactividad a los 120 se
verá morir entre la primera y la segunda.

No es bloqueante para diseñar el streaming —una respuesta que emite progreso
cada pocos segundos está muy por debajo de cualquier límite plausible— pero sí
decide cada cuánto hay que mandar señal de vida durante una herramienta lenta.

**Cómo se mide.** Se pide `/diagnostico/goteo?segundos=180` a través del dominio
de la web —no directo a Render, que es lo que se quiere medir— y se mira hasta
qué línea llega. Cada línea trae el segundo en que se emitió, así que el corte
se lee solo:

- Llega a `180`: el reloj es por inactividad. **El streaming sirve.**
- Se corta cerca de `120`: el reloj es por duración total. El streaming no
  levanta el techo.

## Por qué la cabecera contra el búfer no es un detalle

Si algo entre Morgan y el navegador acumula la respuesta y la entrega junta, la
medición **no mide lo que dice medir**: se vería un corte a los 120 aunque el
reloj fuera por inactividad, porque el primer byte no habría salido nunca.

Por eso van `X-Accel-Buffering: no` y `Cache-Control: no-store`, y por eso la
primera línea sale **de inmediato**, antes de la primera espera: si el primer
byte no llega en el primer segundo, hay un búfer en medio y lo que venga después
no se puede interpretar.

## Por qué exige permiso de sistema

Una petición que retiene una conexión tres minutos es, abierta al público, una
forma barata de agotar las conexiones del servidor. En el plan gratuito de Render
eso es especialmente fácil. No es una ruta de uso: es un instrumento, y los
instrumentos los usa quien administra.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from src.api.autorizacion import exigir
from src.api.sesion_web import origen_de
from src.identidad.modelos import MorganUser
from src.identidad.roles import Permiso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostico", tags=["Diagnóstico"])

#: Tope duro. La pregunta se contesta con 180 s —el corte que se busca está en
#: 120— y dejar pedir una hora solo sirve para bloquear una conexión una hora.
SEGUNDOS_MAXIMOS = 300

#: Cabeceras que impiden que alguien acumule la respuesta por el camino. Sin
#: esto la sonda mide el búfer, no el proxy.
SIN_BUFER = {
    "Cache-Control": "no-store, no-transform",
    "X-Accel-Buffering": "no",
    "Content-Type": "text/plain; charset=utf-8",
}


async def _goteo(segundos: int, cada: float):
    """Emite una línea por intervalo, con el segundo en que sale.

    La primera va **antes** de la primera espera, a propósito: es la que dice si
    hay un búfer en medio, y sin ese dato el resto de la medición no se puede
    interpretar.
    """
    inicio = time.monotonic()
    emitidas = 0

    yield f"linea={emitidas} t=0.000 inicio\n"

    try:
        while True:
            transcurrido = time.monotonic() - inicio
            if transcurrido >= segundos:
                break

            # Se duerme lo que falta para el siguiente tic, no `cada` entero:
            # así el reloj no se va desviando con lo que cuesta cada vuelta.
            siguiente = (emitidas + 1) * cada
            espera = siguiente - transcurrido
            if espera > 0:
                await asyncio.sleep(min(espera, segundos - transcurrido))

            emitidas += 1
            yield f"linea={emitidas} t={time.monotonic() - inicio:.3f}\n"

        yield f"fin t={time.monotonic() - inicio:.3f} lineas={emitidas}\n"

    except asyncio.CancelledError:
        # El cliente o un intermediario cortó. Queda registrado CON el segundo,
        # que es el dato que se estaba buscando: si pone ~120, el reloj del
        # proxy es por duración total.
        logger.warning(
            "Goteo cortado a los %.3f s tras %d líneas (pedidos %d s)",
            time.monotonic() - inicio, emitidas, segundos,
        )
        raise


@router.get("/goteo", summary="Emite una línea por segundo, para medir cortes")
def goteo(
    segundos: int = Query(180, ge=1, le=SEGUNDOS_MAXIMOS,
                          description="Cuánto durar. El corte que se busca está en 120."),
    cada: float = Query(1.0, ge=0.1, le=150.0,
                        description="Segundos entre líneas. Subirlo por encima "
                                    "de 120 mide cuánto SILENCIO tolera el "
                                    "proxy, que es la pregunta que queda."),
    usuario: MorganUser = Depends(exigir(Permiso.SISTEMA_GESTIONAR)),
) -> StreamingResponse:
    """Una respuesta que tarda a propósito, emitiendo señales de vida.

    No calcula nada ni consulta nada. Solo existe para que se pueda ver **dónde
    corta** lo que haya entre Morgan y el navegador.
    """
    logger.info(
        "Goteo pedido por %s: %d s, una línea cada %.1f s",
        usuario.id, segundos, cada,
    )
    return StreamingResponse(_goteo(segundos, cada), headers=SIN_BUFER)


#: Las cabeceras de origen que ponen los proxies conocidos. Se enseñan tal cual
#: llegan: la pregunta es precisamente qué llega.
CABECERAS_DE_ORIGEN = (
    "x-forwarded-for", "x-real-ip", "x-vercel-forwarded-for", "true-client-ip",
    "cf-connecting-ip", "forwarded",
)


@router.get("/origen", summary="Cómo ve Morgan el origen de esta petición")
def origen(request: Request) -> dict:
    """La sonda del origen (auditoría 2.3). **No escribe nada.**

    Los frenos de acceso cuentan intentos por origen, y el origen sale de
    `X-Forwarded-For`. La pregunta que quedaba abierta era qué valor llega de
    verdad a producción, detrás de Vercel y de Render: si el primero de la cadena
    es tu IP, o algo que cualquiera puede escribir.

    Es pública a propósito: no hay forma de medir el caso de alguien sin sesión
    —que es el caso del registro y del inicio de sesión— con una ruta que la
    exige. Lo que enseña es **lo que tu propia petición trae**: tu IP tal como la
    ven los proxies, lo mismo que dice cualquier servicio de «cuál es mi IP».
    """
    return {
        "origen_contado": origen_de(request),
        "cliente_directo": request.client.host if request.client else None,
        "cabeceras": {
            nombre: request.headers.get(nombre)
            for nombre in CABECERAS_DE_ORIGEN
            if request.headers.get(nombre) is not None
        },
    }
