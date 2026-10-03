"""
Que una clave de proveedor sea **usable**, antes de gastar un turno en descubrirlo.

## El fallo que da origen a esto

Al recrear el servicio de producción en Virginia hubo que teclear 18 secretos a
mano, y tres salieron tocados. Dos eran claves de modelo:

```
Groq   → 401 Invalid API Key
Gemini → 'ascii' codec can't encode character '\\xa3' in position 19
```

La de Groq estaba mal transcrita. La de Gemini tenía **un símbolo de libra
dentro**, seguramente de un copiado a medias.

Y el síntoma fue el peor posible: **todo parecía funcionar.** Los turnos se
contestaban, la web iba bien, y `/status` decía que el modelo estaba disponible
listando la cadena entera. Lo único distinto era que **el proveedor de pago
contestaba todos los turnos**, porque los dos gratuitos fallaban en silencio.
Nadie lo vio hasta que una medición miró quién había respondido.

## Qué se comprueba aquí, y qué no

Solo lo que se puede saber **sin salir a la red**: que la clave pueda siquiera
viajar en una cabecera HTTP, y que tenga una pinta razonable.

- **No ASCII → imposible.** Una cabecera HTTP no admite caracteres fuera de
  ASCII. Una clave con un símbolo de libra no falla «a veces»: no puede
  funcionar nunca, y eso es exactamente lo que merece un aviso al arrancar en
  lugar de un `UnicodeEncodeError` por turno.
- **Espacios en los bordes** son el error de copiado más común y el más fácil de
  perdonar: se recortan y se avisa, no se rechaza.
- **Longitudes absurdas** —una clave de tres caracteres— delatan un pegado a
  medias.

Lo que **NO** se comprueba es si el proveedor la acepta. Eso solo lo sabe el
proveedor, cuesta una llamada de red en cada arranque, y un arranque que depende
de tres servicios externos es un arranque frágil. Una clave sintácticamente
válida y rechazada sigue apareciendo en el registro al primer turno, con su
`401`, que es donde corresponde.

Así que esto no promete que la clave sirva. Promete que **si no puede servir, se
dice antes y no se monta el proveedor.**
"""

import logging
import re

logger = logging.getLogger(__name__)

#: Longitud por debajo de la cual una clave es, con certeza, un pegado a medias.
#: La más corta de las que Morgan usa tiene 39 caracteres; se deja margen de
#: sobra para no rechazar la clave de un proveedor futuro más escueto.
MINIMO_RAZONABLE = 16

#: Y por encima de la cual es otra cosa: un fichero entero pegado, o un JSON.
MAXIMO_RAZONABLE = 512


class ClaveInutilizable(ValueError):
    """La clave no puede funcionar, y se sabe sin preguntarle al proveedor."""


def revisar(nombre_variable: str, clave: str | None) -> str | None:
    """Devuelve la clave lista para usar, o lanza si no puede funcionar.

    `None` y la cadena vacía no son un error: significan «este proveedor no está
    configurado», que es una situación normal y ya la trata la cadena.
    """
    if clave is None:
        return None

    limpia = clave.strip()
    if not limpia:
        return None

    if limpia != clave:
        # Se perdona porque es el accidente de copiado más común y no impide
        # nada, pero se dice: si el proveedor rechaza la clave, conviene saber
        # que venía con espacios.
        logger.warning(
            "%s tenía espacios al principio o al final. Se han quitado.",
            nombre_variable,
        )

    if not limpia.isascii():
        malos = sorted({c for c in limpia if not c.isascii()})
        posiciones = [i for i, c in enumerate(limpia) if not c.isascii()]
        raise ClaveInutilizable(
            f"{nombre_variable} tiene {len(posiciones)} carácter(es) que no son "
            f"ASCII, en la posición {posiciones[0] + 1}: "
            f"{', '.join(repr(c) for c in malos)}. "
            "Una clave así NO PUEDE funcionar: las cabeceras HTTP solo admiten "
            "ASCII, así que revienta antes de salir a la red. "
            "Vuelve a copiarla del panel del proveedor."
        )

    if any(c.isspace() for c in limpia):
        raise ClaveInutilizable(
            f"{nombre_variable} tiene un espacio o un salto de línea dentro. "
            "Suele ser un pegado que se partió en dos líneas. "
            "Vuelve a copiarla del panel del proveedor."
        )

    if len(limpia) < MINIMO_RAZONABLE:
        raise ClaveInutilizable(
            f"{nombre_variable} tiene solo {len(limpia)} caracteres, y ninguna "
            f"clave de proveedor es tan corta. Parece un pegado a medias."
        )

    if len(limpia) > MAXIMO_RAZONABLE:
        raise ClaveInutilizable(
            f"{nombre_variable} tiene {len(limpia)} caracteres, demasiados para "
            "una clave. ¿Se ha pegado un fichero entero?"
        )

    # Caracteres de control invisibles: un \x00 o un \x1b pegado desde una
    # terminal no se ve al mirar la variable y rompe la cabecera igual.
    if re.search(r"[\x00-\x1f\x7f]", limpia):
        raise ClaveInutilizable(
            f"{nombre_variable} contiene caracteres de control invisibles. "
            "Vuelve a copiarla del panel del proveedor."
        )

    return limpia
