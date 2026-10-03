"""
Verificación: comprobar que lo que se pidió ocurrió de verdad (V1.7).

Que una herramienta devuelva `success: True` significa que **la llamada no falló**,
no que el objetivo se cumpliera. Un `create_file` puede escribir un archivo vacío,
un `patch_file` puede aplicar una sustitución que no era la que hacía falta, y un
`run_tests` puede terminar sin excepciones con la mitad de las pruebas en rojo.

La V1.7 mete un paso más:

    Ejecutar → Verificar → ¿correcto?
                            ├── sí → Completar
                            └── no → Diagnosticar → Corregir → Ejecutar → Verificar

## Quién decide si algo salió bien

**No el modelo.** Es la misma lección que dejó el gate de la V1.6: preguntarle si
lo consiguió invita a que responda que sí. Aquí se comprueba **el efecto** de la
herramienta contra el mundo real — si el archivo existe, si desapareció, si el
texto está dentro— y ese resultado no depende de lo que nadie afirme.

## Lo que no se puede verificar se dice, no se supone

Hay efectos que Morgan no sabe comprobar: si un correo llegó, si una búsqueda web
devolvió lo relevante, si un texto es *bueno*. Para esos, la respuesta es
`NO_VERIFICABLE`, y eso es distinto de `CORRECTO`.

La diferencia es toda la razón de ser de este módulo. Un sistema que llama
«verificado» a lo que no ha comprobado es peor que uno que no verifica nada,
porque además te convence de que sí.
"""

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Veredicto(str, Enum):
    CORRECTO = "correcto"          # se comprobó y el efecto está
    INCORRECTO = "incorrecto"      # se comprobó y NO está
    NO_VERIFICABLE = "no_verificable"  # Morgan no sabe comprobar esto

    def __str__(self) -> str:
        return self.value


@dataclass
class Comprobacion:
    """El resultado de verificar un paso."""

    veredicto: str
    motivo: str
    # Qué se miró exactamente. Sin esto, un "correcto" es tan poco informativo
    # como el "success: True" que vino a sustituir.
    evidencia: str | None = None

    @property
    def correcto(self) -> bool:
        return self.veredicto == Veredicto.CORRECTO.value

    @property
    def fallido(self) -> bool:
        """Solo lo que se comprobó y salió mal.

        `NO_VERIFICABLE` no es un fallo: es no saber. Tratarlo como fallo haría
        que Morgan reintentara cosas que quizá salieron perfectamente.
        """
        return self.veredicto == Veredicto.INCORRECTO.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "veredicto": self.veredicto,
            "motivo": self.motivo,
            "evidencia": self.evidencia,
        }


def _correcto(motivo: str, evidencia: str | None = None) -> Comprobacion:
    return Comprobacion(Veredicto.CORRECTO.value, motivo, evidencia)


def _incorrecto(motivo: str, evidencia: str | None = None) -> Comprobacion:
    return Comprobacion(Veredicto.INCORRECTO.value, motivo, evidencia)


def _no_verificable(motivo: str) -> Comprobacion:
    return Comprobacion(Veredicto.NO_VERIFICABLE.value, motivo)


# --- Comprobaciones por herramienta ------------------------------------------
#
# Cada una mira EL EFECTO, no el valor devuelto. Son deliberadamente simples: una
# comprobación que necesitara su propia lógica compleja tendría sus propios
# errores, y entonces habría que verificar al verificador.


def _archivo_existe(args: dict) -> Comprobacion:
    ruta = args.get("path") or args.get("ruta") or ""
    if not ruta:
        return _no_verificable("No se sabe qué archivo debía crearse.")

    if not os.path.exists(ruta):
        return _incorrecto(f"El archivo '{ruta}' no existe.", f"os.path.exists('{ruta}') = False")

    tamano = os.path.getsize(ruta)
    contenido_pedido = args.get("content") or args.get("contenido") or ""

    if contenido_pedido and tamano == 0:
        # Se pidio contenido y el archivo esta vacio: la llamada no fallo, pero
        # el objetivo tampoco se cumplio. Es justo el caso que motiva la V1.7.
        return _incorrecto(
            f"'{ruta}' existe pero está vacío, y se pidió escribir contenido.",
            "tamaño = 0 bytes",
        )

    return _correcto(f"'{ruta}' existe.", f"{tamano} bytes")


def _archivo_no_existe(args: dict) -> Comprobacion:
    ruta = args.get("path") or args.get("ruta") or ""
    if not ruta:
        return _no_verificable("No se sabe qué archivo debía borrarse.")

    if os.path.exists(ruta):
        return _incorrecto(
            f"'{ruta}' sigue existiendo.", f"os.path.exists('{ruta}') = True"
        )

    return _correcto(f"'{ruta}' ya no está.")


def _parche_aplicado(args: dict) -> Comprobacion:
    """El texto nuevo está en el archivo, y el viejo ya no."""
    ruta = args.get("path") or args.get("ruta") or ""
    nuevo = args.get("new_text") or args.get("nuevo") or ""

    if not ruta or not nuevo:
        return _no_verificable("Faltan la ruta o el texto nuevo para comprobarlo.")

    if not os.path.exists(ruta):
        return _incorrecto(f"'{ruta}' no existe.")

    try:
        contenido = _leer(ruta)
    except OSError as exc:
        return _no_verificable(f"No se pudo leer '{ruta}': {exc}")

    if nuevo not in contenido:
        return _incorrecto(
            f"El texto nuevo no aparece en '{ruta}'.",
            f"se buscaron {len(nuevo)} caracteres y no están",
        )

    return _correcto(f"El cambio está en '{ruta}'.")


def _copiado(args: dict) -> Comprobacion:
    """Copiar (4.0-B): el destino existe y mide lo mismo que el origen."""
    origen, destino = args.get("src") or "", args.get("dst") or ""
    if not origen or not destino:
        return _no_verificable("No se sabe qué se copiaba ni adónde.")
    if os.path.isdir(destino):
        destino = os.path.join(destino, os.path.basename(origen))
    if not os.path.exists(destino):
        return _incorrecto(f"La copia '{destino}' no existe.", f"os.path.exists('{destino}') = False")
    if os.path.isfile(origen) and os.path.getsize(origen) != os.path.getsize(destino):
        return _incorrecto(f"La copia '{destino}' no mide lo mismo que el original.",
                           f"{os.path.getsize(origen)} ≠ {os.path.getsize(destino)} bytes")
    return _correcto(f"'{destino}' existe.", f"{os.path.getsize(destino)} bytes")


def _movido(args: dict) -> Comprobacion:
    """Mover (4.0-B): ya no está en el origen y sí en el destino."""
    origen, destino = args.get("src") or "", args.get("dst") or ""
    if not origen or not destino:
        return _no_verificable("No se sabe qué se movía ni adónde.")
    if os.path.isdir(destino) and os.path.abspath(destino) != os.path.abspath(origen):
        destino = os.path.join(destino, os.path.basename(origen))
    if not os.path.exists(destino):
        return _incorrecto(f"'{destino}' no existe después de moverlo.")
    if os.path.exists(origen) and os.path.abspath(origen) != os.path.abspath(destino):
        return _incorrecto(f"'{origen}' sigue en su sitio.")
    return _correcto(f"Está en '{destino}' y ya no en '{origen}'.")


def _renombrado(args: dict) -> Comprobacion:
    """Renombrar (4.0-B): el nombre nuevo existe y el viejo no."""
    origen, nuevo = args.get("src") or "", args.get("new_name") or ""
    if not origen or not nuevo:
        return _no_verificable("No se sabe qué se renombraba.")
    return _movido({"src": origen, "dst": os.path.join(os.path.dirname(origen), nuevo)})


def _leer(ruta: str, limite: int = 2_000_000) -> str:
    """Lee un archivo tolerando codificaciones raras.

    Un fallo de decodificación no debe convertir «no pude verificar» en
    «incorrecto»: son cosas distintas.
    """
    with open(ruta, "r", encoding="utf-8", errors="replace") as f:
        return f.read(limite)


def _tests_en_verde(args: dict, resultado: dict | None) -> Comprobacion:
    """Que `run_tests` termine no significa que las pruebas pasaran.

    Es el ejemplo canónico de por qué existe este módulo: la herramienta hace su
    trabajo —ejecutar pytest— y devuelve `success: True` habiendo fallado nueve
    pruebas.
    """
    if not isinstance(resultado, dict):
        return _no_verificable("No hay salida de las pruebas que mirar.")

    datos = resultado.get("data") or {}
    salida = " ".join(str(datos.get(c, "")) for c in ("stdout", "output", "salida", "resumen"))

    if not salida.strip():
        return _no_verificable("Las pruebas no dejaron salida legible.")

    minuscula = salida.lower()

    if "failed" in minuscula or "error" in minuscula:
        return _incorrecto("Las pruebas no están en verde.", salida[-200:].strip())

    if "passed" in minuscula or "ok" in minuscula:
        return _correcto("Las pruebas pasaron.", salida[-200:].strip())

    return _no_verificable("No se pudo interpretar la salida de las pruebas.")


# Qué se sabe comprobar. Lo que no está aquí devuelve NO_VERIFICABLE, que es
# honesto: mejor decir «no lo sé» que inventarse un veredicto.
COMPROBACIONES = {
    "create_file": _archivo_existe,
    "delete_file": _archivo_no_existe,
    "patch_file": _parche_aplicado,
    "copy_file": _copiado,
    "move_file": _movido,
    "rename_file": _renombrado,
}

# Estas necesitan además el resultado de la herramienta, no solo sus argumentos.
COMPROBACIONES_CON_RESULTADO = {
    "run_tests": _tests_en_verde,
}


def _comprobado_en_el_pc(resultado: dict | None) -> Comprobacion:
    """El efecto de una herramienta del PC, según lo que comprobó el agente **allí**.

    **Nunca el disco de la nube**: la ruta es de Windows y del PC de la persona. Medido en
    la auditoría de la 3.x: en producción (Render, Linux), un archivo creado bien en el PC
    se daba por no creado («no existe») y a Morgan se le decía que había fallado; y uno que
    seguía en el PC se daba por borrado. El agente comprueba la huella de lo que escribe
    (3.3) y confirma la Papelera: eso es la evidencia."""
    datos = (resultado or {}).get("data") if isinstance(resultado, dict) else None
    if not isinstance(datos, dict):
        return _no_verificable("Lo comprueba el agente en el PC.")
    # Lo que el agente dice haber comprobado allí (4.0-B): crear carpeta, mover, un
    # comando que cambia algo, terminar un proceso. Solo lo pone cuando lo comprobó.
    if isinstance(datos.get("comprobado"), str) and datos["comprobado"].strip():
        return _correcto(f"Comprobado en el PC: {datos['comprobado'][:120]}.")
    if isinstance(datos.get("sha256"), str):
        return _correcto("Comprobado en el PC por su huella.",
                         f"sha256 {datos['sha256'][:12]}…")
    if datos.get("papelera") is True:
        return _correcto("Comprobado en el PC: está en la Papelera.")
    return _no_verificable("Lo comprueba el agente en el PC.")


class Verificador:
    """Comprueba que el efecto de una herramienta ocurrió de verdad."""

    def verificar(
        self,
        herramienta: str,
        argumentos: dict | None = None,
        resultado: dict | None = None,
        en_el_pc: bool = False,
    ) -> Comprobacion:
        """El veredicto sobre un paso ya ejecutado. `en_el_pc`: la herramienta actuó en el
        PC de la persona, a través del agente, y no en el disco de este proceso."""
        args = argumentos or {}

        # Si la propia herramienta dijo que fallo, no hay nada que comprobar: el
        # efecto no se intento siquiera.
        if isinstance(resultado, dict) and resultado.get("success") is False:
            return _incorrecto(
                "La herramienta falló.",
                str(resultado.get("error") or "")[:200] or None,
            )

        if en_el_pc:
            return _comprobado_en_el_pc(resultado)

        if herramienta in COMPROBACIONES_CON_RESULTADO:
            return COMPROBACIONES_CON_RESULTADO[herramienta](args, resultado)

        comprobacion = COMPROBACIONES.get(herramienta)
        if comprobacion is None:
            return _no_verificable(
                f"Morgan no sabe comprobar el efecto de '{herramienta}'."
            )

        try:
            return comprobacion(args)
        except Exception as exc:
            # Un fallo del verificador no puede convertirse en un veredicto: eso
            # seria afirmar algo que no se comprobo.
            logger.warning("Falló la verificación de %s", herramienta, exc_info=True)
            return _no_verificable(f"No se pudo comprobar: {exc}")

    def sabe_verificar(self, herramienta: str) -> bool:
        return (
            herramienta in COMPROBACIONES
            or herramienta in COMPROBACIONES_CON_RESULTADO
        )
