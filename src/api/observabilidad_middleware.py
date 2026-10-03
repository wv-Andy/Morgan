"""
Cada petición dice en qué se le fue el tiempo (V2.0).

Abre una medición por petición y devuelve el reparto en dos sitios:

- **La cabecera `X-Morgan-Duracion`**, que se lee de un vistazo en las
  herramientas del navegador: `total=1420;bd=310;modelo=520;resto=590`.
- **El registro**, cuando la petición pasa del umbral de lenta.

Va en un middleware y no en cada ruta por el mismo motivo que el filtrado por
usuario: una ruta nueva nace medida sin tener que acordarse. Y las 52 que ya
existen no cambian ni una línea.

## Por qué una cabecera y no solo el cuerpo

Porque las lecturas —`/sessions`, `/tools`, `/settings`— cuestan ~1 segundo cada
una en producción y no tienen dónde poner un campo de tiempos: su cuerpo es el
dato que se pidió. Una cabecera sirve para las 52 rutas por igual, y para las
que sí tienen cuerpo propio —`/chat`— se añade además ahí, que es donde lo puede
leer la web.

## Dónde se registra

Último de los middlewares que se instalan, o sea **el más externo de los
propios**: así mide también lo que tardan la identidad, el CSRF y la
autenticación por token, que son parte de lo que cuesta una petición.

CORS queda por fuera, y es correcto: la cabecera tiene que existir antes de que
CORS decida si el navegador puede leerla, y para eso hay que exponerla —está en
`app.py`, en `expose_headers`.
"""

import logging

from fastapi import FastAPI, Request

from src.observabilidad import LENTA_SEGUNDOS, midiendo

logger = logging.getLogger(__name__)

CABECERA = "X-Morgan-Duracion"

#: No se mide lo que no dice nada. `/health` existe para responder rápido y que
#: alguien lo consulte cada pocos segundos; medirlo llena el registro de líneas
#: que no llevan a ninguna parte.
SIN_MEDIR = ("/health", "/openapi.json", "/docs", "/redoc", "/assets")


def install_observabilidad(app: FastAPI) -> None:
    @app.middleware("http")
    async def medir(request: Request, call_next):
        if request.url.path.startswith(SIN_MEDIR):
            return await call_next(request)

        with midiendo() as medicion:
            respuesta = await call_next(request)

            # Se lee dentro del `with`: fuera, el contexto ya se ha restaurado.
            # El objeto seguiria siendo válido, pero dejarlo dentro es lo que
            # hace evidente que pertenece a esta petición.
            resumen = medicion.resumen()
            respuesta.headers[CABECERA] = medicion.cabecera()

        if resumen["total_ms"] >= LENTA_SEGUNDOS * 1000:
            # Con el reparto, no solo el total. Un total sin reparto obliga a
            # reproducirlo para saber algo, y reproducirlo es justo lo que esto
            # existe para no tener que hacer.
            logger.warning(
                "Petición lenta: %s %s → %s",
                request.method,
                request.url.path,
                medicion.cabecera(),
            )

        return respuesta
