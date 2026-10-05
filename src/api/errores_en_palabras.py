"""
Los errores de la API, en palabras que entiende cualquiera (4.23).

La web enseña tal cual el `message` de un error. Antes, alguien nuevo podía leer «Not Found»,
«Method Not Allowed», «Datos de petición no válidos según el esquema» o «Consulta
logs/morgan.log para el detalle» (un fichero que en la nube nadie tiene). El `code` sigue
siendo el de siempre, para los programas que usan la API; el `message` dice qué pasó y qué
hacer.
"""

from http import HTTPStatus

#: Un fallo que no se esperaba: el detalle va al registro del servidor, nunca a la pantalla.
ERROR_INESPERADO = "Algo falló en Morgan. Vuelve a intentarlo en un momento; si se repite, avísanos."
#: Un fallo durante un turno del agente.
ERROR_EN_EL_TURNO = ("Algo falló mientras Morgan trabajaba en esto. Vuelve a intentarlo; si se repite, "
                     "pídeselo con otras palabras o por partes.")

#: Para los errores que da el propio servidor web (una ruta que no existe, un método que no
#: vale…), que llegan con la frase en inglés de HTTP.
_POR_ESTADO = {
    400: "Lo que se envió no se entiende. Revísalo y vuelve a intentarlo.",
    401: "Tienes que entrar en tu cuenta para esto.",
    403: "No tienes permiso para esto.",
    404: "No se encuentra: puede que se haya borrado o que la dirección no sea esa.",
    405: "Eso no se puede hacer aquí.",
    408: "Tardó demasiado. Vuelve a intentarlo.",
    413: "Es demasiado grande.",
    415: "Ese tipo de archivo o de datos no se acepta aquí.",
    429: "Demasiadas peticiones seguidas: espera un momento y vuelve a intentarlo.",
    500: ERROR_INESPERADO,
    502: "Morgan no pudo contestar ahora mismo. Vuelve a intentarlo en un momento.",
    503: "Morgan no está disponible ahora mismo. Vuelve a intentarlo en un momento.",
    504: "Tardó demasiado en contestar. Vuelve a intentarlo; si era un encargo largo, pídelo por partes.",
}


def en_palabras(estado: int, detalle: str) -> str:
    """El mensaje de un error con texto: el de Morgan si lo escribió Morgan; si es la frase de
    HTTP por defecto (o nada), uno que se entienda."""
    try:
        frase = HTTPStatus(estado).phrase
    except ValueError:
        frase = ""
    if not detalle or detalle == frase:
        return _POR_ESTADO.get(estado, ERROR_INESPERADO if estado >= 500 else _POR_ESTADO[400])
    return detalle
