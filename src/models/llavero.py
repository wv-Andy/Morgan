"""
Varias claves del mismo proveedor, y en qué orden usarlas.

## Qué problema resuelve, con el número

La [medición de la 2.0.5](../../docs/mediciones.md) dejó el diagnóstico
claro: a Morgan ya no le sobra latencia —un turno trivial son 0,66 s— y lo que
le falta es **capacidad**. Unos 35 turnos al día con una sola cuenta de Groq, y
[medido turno a turno](../../docs/mediciones.md) después de escribir
esto: la primera cuenta decía 54 porque dividía por el peso de una **llamada**,
y un turno hace entre una y tres.

Ese techo no se baja optimizando código. Se baja con más cuota, y la cuota de
Groq es **por cuenta**, no por clave. Así que dos claves de dos cuentas
distintas son el doble de techo, y dos claves de la misma cuenta son exactamente
lo mismo que una.

**Se comprobó antes de escribir esto**, porque de la respuesta dependía que el
módulo tuviera sentido. Groq devuelve su contador de peticiones en cada
respuesta, así que no hubo que deducirlo:

| llamada | clave A | clave B |
|---|---|---|
| 1ª | 999 | 999 |
| 2ª | 998 | 998 |
| 3ª | 997 | — |

Si el cubo fuera compartido, la primera llamada de B habría leído 996. Leyó 999.
Cubos separados, y el techo pasa de 200.000 tokens y 1.000 peticiones al día a
400.000 y 2.000.

Un detalle del método, porque costó un intento: el contador de **tokens** no
servía para decidirlo. Se rellena en 570 ms —8.000 por minuto son 133 por
segundo— así que en el segundo que pasa entre dos llamadas se rellena lo
gastado y las dos hipótesis dan la misma lectura. El de **peticiones** sí sirve,
porque su ventana es de un día y no se disimula.

## Por qué la rotación vive aquí y no en la cadena

La alternativa era meter las dos claves como dos eslabones del
`FallbackProvider`. Se descartó por lo que le diría al usuario: la cadena marca
`respaldo: sí` cuando el principal falla, y eso significa «te ha contestado otro
modelo». Cambiar de clave **no es cambiar de modelo**: es el mismo Groq, el
mismo `openai/gpt-oss-120b` y la misma respuesta. Contarlo como un respaldo
sería decirle al usuario algo que no ha pasado, y las métricas de conmutación
dejarían de medir lo que dicen medir.

## La regla de orden: en serie, no por turnos

Se gasta la primera hasta agotarla, y entonces se pasa a la segunda. No se
reparten las llamadas.

El total del día es el mismo de las dos formas, así que la decisión se toma por
lo que pasa cuando algo va mal. Repartiendo, las dos claves llegan al 80% a la
vez y el aviso llega cuando ya no queda reserva en ninguna. En serie, la segunda
sigue intacta mientras la primera se gasta, y eso es lo que hace que sea una
reserva de verdad.

## La propiedad de seguridad, heredada de `cuota.py`

**Si todas las claves parecen agotadas, se usa la primera igual.** El llavero
nunca se queda vacío: `turnos()` devuelve siempre todas las claves, reordenadas
y no filtradas. El agotamiento es una estimación, y los dos errores posibles no
cuestan lo mismo —un rechazo son 0,02 s, quedarse sin clave es que Morgan no
contesta—. Está razonado entero en [`cuota.py`](cuota.py) y aquí solo se
respeta.
"""

import logging

logger = logging.getLogger(__name__)


class Llavero:
    """Las claves de un proveedor, y cuál toca.

    Cada clave lleva una **etiqueta** —`Groq#1`, `Groq#2`— que es lo que se
    apunta en el registro de cuotas. La etiqueta nunca contiene la clave: un
    registro que lleva medio secreto dentro es un secreto filtrado, y estos
    registros se leen desde el panel de Render.
    """

    def __init__(self, proveedor: str, claves: tuple[str, ...] | list[str]) -> None:
        self._proveedor = proveedor
        self._claves = tuple(c for c in claves if c)
        if not self._claves:
            raise ValueError(f"Un llavero de {proveedor} sin ninguna clave")

    def __len__(self) -> int:
        return len(self._claves)

    @property
    def proveedor(self) -> str:
        return self._proveedor

    def etiqueta(self, indice: int) -> str:
        """Cómo se llama la clave nº `indice` en el registro de cuotas.

        Con una sola clave **no se numera**. Numerar algo que no tiene hermanos
        solo añade ruido a los registros y rompería la continuidad de lo que ya
        estaba apuntado como `Groq`.
        """
        if len(self._claves) == 1:
            return self._proveedor
        return f"{self._proveedor}#{indice + 1}"

    @property
    def etiquetas(self) -> tuple[str, ...]:
        return tuple(self.etiqueta(i) for i in range(len(self._claves)))

    def turnos(self) -> list[tuple[str, str]]:
        """`(etiqueta, clave)` en el orden en que hay que probarlas.

        **Devuelve siempre todas**: las que se saben agotadas van al final, no
        se descartan. Ver la cabecera del módulo.
        """
        from src.models.cuota import CUOTAS

        pares = [
            (self.etiqueta(i), clave) for i, clave in enumerate(self._claves)
        ]
        if len(pares) == 1:
            return pares

        vivas = [p for p in pares if not CUOTAS.agotado(p[0])]
        agotadas = [p for p in pares if CUOTAS.agotado(p[0])]
        if agotadas and vivas:
            logger.debug(
                "%s: se posponen %d clave(s) agotada(s) (%s) y se empieza por %s",
                self._proveedor, len(agotadas),
                ", ".join(e for e, _ in agotadas), vivas[0][0],
            )
        return vivas + agotadas
