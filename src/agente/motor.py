"""
El motor de política del agente (3.2): el **único** sitio donde se decide si una
operación local puede hacerse.

## Por qué existe, si ya había una política

Hasta la 3.1 cada capacidad llamaba por su cuenta a `Politica.resolver()` y se acordaba
por sí misma de sus topes. Funcionaba, pero la decisión estaba repartida: una capacidad
nueva podía olvidarse de preguntar, y no había un sitio donde leer «qué deja hacer este
PC». El plan lo pide explícito (§15): *toda operación local relevante pasa por el motor
de política*.

Aquí se junta lo que el plan llama `Capability + Permission + Availability + Local
Policy`:

1. **¿Está encendida esa capacidad?** La persona las apaga una a una en su PC (decisión
   mía, 3.2). Lo apagado ni se anuncia a la nube.
2. **¿La ruta está permitida?** Carpetas permitidas, **carpetas bloqueadas** (ganan
   siempre, aunque estén dentro de una permitida), el objeto real, enlaces, flujos,
   nombres sensibles: eso lo resuelve `politica.py`, que es quien sabe de rutas.
3. **¿Cuánto puede salir?** Los topes, que la persona solo puede **bajar**.
4. **¿Hace falta que la persona lo confirme?** Leer, nunca. Borrar, sí (3.3, decisión
   mía); escribir, no, porque ya pasa por un plan aprobado en el móvil. Se pregunta
   con una **notificación de Windows** con «Permitir» y «Rechazar» (`aviso.py`).

Desde la 3.3, **escribir y borrar se miden contra otras carpetas**: las de escritura,
una lista aparte y vacía al empezar (`Politica.resolver_escritura`).

## Lo que el motor NO hace

**No se puede cambiar desde la nube** (§16 del plan). No hay ningún mensaje del
protocolo que toque la política, y este módulo no expone nada para escribirla: eso vive
en `Politica` y lo llama la consola del PC (`python -m src.agente`).
"""

from dataclasses import dataclass
from pathlib import Path

from src.agente.politica import DE_ESCRITURA, ENCENDIDA_POR_DEFECTO, Denegado, Politica

#: Qué operación se pide, en palabras del plan. `ejecutar` todavía no existe (3.5): el
#: motor la conoce para poder decir que no.
LECTURA = ("listar", "leer", "buscar", "copiar", "estado", "comando", "procesos", "info", "diagnostico",
           "abrir", "portapapeles", "avisar", "ventanas", "captura", "interfaz_leer", "interfaz", "contexto")
MUTACION = ("escribir", "borrar", "ejecutar", "terminar")

#: Las que pueden pedirse sin ninguna ruta: `system_info` no toca el disco, `list_files`
#: sin ruta enseña las carpetas permitidas y `search_files` sin ruta busca en todas.
SIN_RUTA = ("estado", "listar", "buscar", "comando", "procesos", "diagnostico", "abrir", "portapapeles",
            "avisar", "ventanas", "captura", "interfaz_leer", "interfaz", "contexto")

#: Qué capacidad del canal corresponde a cada operación.
CAPACIDAD_DE = {
    "listar": "list_files",
    "leer": "read_file",
    "buscar": "search_files",
    "copiar": "copy_file",
    "estado": "system_info",
    "comando": "run_command",           # un programa del catálogo que consulta (3.5)
    "procesos": "get_processes",
    "info": "file_info",                # los metadatos (4.6)
    "diagnostico": "pc_diagnostics",    # el PC por dentro (4.7)
    "abrir": "open_app",                # aplicaciones, archivos, páginas y carpetas (4.8)
    "portapapeles": "clipboard",        # leer (con «Permitir» siempre) y escribir (4.10)
    "avisar": "notify",                 # una notificación sin botones (4.10)
    "ventanas": "windows",              # listar, enfocar, mover… (4.11)
    "captura": "screenshot",            # con «Permitir» siempre (4.11)
    "interfaz_leer": "ui_read",         # los controles de una ventana (4.12)
    "interfaz": "ui_control",           # ratón y teclado, con plan y «Permitir» (4.12)
    "contexto": "pc_context",           # los proyectos y los editores (4.13)
}

#: Y las de escritura (3.3): varias capacidades por operación, así que aquí es al revés.
OPERACION_DE = {"create_file": "escribir", "edit_file": "escribir", "append_file": "escribir",
                "create_folder": "escribir",
                "move_file": "escribir", "delete_file": "borrar",
                # Copiar y comprimir (4.6): escriben algo nuevo en una carpeta de escritura.
                "copy_path": "escribir", "compress": "escribir",
                # La terminal y los procesos (3.5): un comando que cambia cosas se ejecuta
                # en una carpeta de escritura; terminar un proceso no tiene ruta.
                "run_change_command": "ejecutar", "kill_process": "terminar",
                # Cerrar una aplicación (4.8): como terminar un proceso, con buenos modales.
                "close_app": "terminar",
                # Un servicio (4.9): como terminar un proceso, sin ruta y con «Permitir».
                "service_control": "terminar"}
assert set(DE_ESCRITURA) <= set(OPERACION_DE)


@dataclass(frozen=True)
class Decision:
    """La respuesta del motor. **Nunca lanza**: quien pregunta tiene que poder explicar
    el no, y el modelo tiene que recibir el motivo."""

    permitida: bool
    motivo: str = ""
    mensaje: str = ""
    ruta: Path | None = None
    exige_confirmacion: bool = False
    limite_bytes: int | None = None

    def __bool__(self) -> bool:
        return self.permitida


def _no(motivo: str, mensaje: str) -> Decision:
    return Decision(False, motivo, mensaje)


def evaluar(operacion: str, ruta: str | None = None, *, archivo: bool | None = None,
            politica: Politica | None = None, capacidad: str | None = None,
            nueva: bool = False, sin_carpeta: bool = False) -> Decision:
    """Si esta operación puede hacerse en este PC, y con qué límite.

    `ruta` es opcional: `list_files` sin ruta (enseñar las carpetas permitidas) y
    `system_info` no tocan ninguna.

    Para escribir y borrar (3.3) hace falta decir **qué capacidad** lo pide, y tiene que
    ser de esa operación: `create_file` no puede pedir `borrar`. `nueva=True` es para lo
    que se va a crear (no puede existir todavía).
    """
    politica = politica if politica is not None else Politica.cargar()

    # Una operación que el motor no conoce se **deniega**, no se deja pasar (3.2.5).
    # Encontrado atacando: `evaluar("hackear", ruta)` decía que sí, porque no tenía
    # capacidad que mirar ni estaba entre las mutaciones. El día que una capacidad
    # nueva se olvide de registrarse aquí, tiene que fallar cerrando, no abriendo.
    if operacion not in LECTURA and operacion not in MUTACION:
        return _no("operacion_desconocida", f"Este PC no sabe hacer «{operacion}».")

    if operacion in MUTACION:
        # Fallar cerrando, como en la 3.2.5: una escritura sin capacidad, o con una que
        # no es de esa operación, no se evalúa.
        if OPERACION_DE.get(capacidad or "") != operacion:
            return _no("capacidad_desconocida", "Esa capacidad no hace esa operación.")
    else:
        capacidad = CAPACIDAD_DE.get(operacion)
    if capacidad is not None and not politica.capacidades.get(
            capacidad, ENCENDIDA_POR_DEFECTO.get(capacidad, False)):
        return _no("capacidad_apagada",
                   "Esta persona ha apagado eso en su PC. Solo se enciende allí.")

    confirmar = politica.confirmar.get(operacion, operacion in MUTACION)
    limite = politica.lectura_bytes if operacion == "leer" else (
        politica.copia_bytes or None) if operacion == "copiar" else None

    if operacion == "terminar" or (operacion == "ejecutar" and sin_carpeta and ruta is None):
        # Terminar un proceso no tiene ruta; y un comando que cambia algo sin necesitar una
        # carpeta (pip install, winget install, 4.9) tampoco: corre en una vacía del agente.
        return Decision(True, exige_confirmacion=confirmar)
    if operacion in MUTACION:
        if ruta is None:
            return _no("ruta_invalida", "Falta la ruta, o no es válida.")
        try:
            # Un comando se ejecuta en la carpeta de escritura misma (la raíz de un
            # repositorio), no solo dentro.
            real = politica.resolver_escritura(ruta, nueva=nueva, archivo=archivo,
                                               raiz=operacion == "ejecutar")
        except Denegado as exc:
            return _no(exc.motivo, str(exc))
        return Decision(True, ruta=real, exige_confirmacion=confirmar)

    if ruta is None:
        # Hay operaciones que no tocan ninguna ruta (`system_info`, listar las carpetas
        # permitidas). Las demás, sin ruta, no se pueden ni evaluar: encontrado por la
        # prueba de argumentos abusivos de la 3.1.5 al pasar `copy_file(None)`.
        if operacion in SIN_RUTA:
            return Decision(True, exige_confirmacion=confirmar, limite_bytes=limite)
        return _no("ruta_invalida", "Falta la ruta, o no es válida.")

    try:
        real = politica.resolver(ruta, archivo=archivo)
    except Denegado as exc:
        return _no(exc.motivo, str(exc))
    return Decision(True, ruta=real, exige_confirmacion=confirmar, limite_bytes=limite)


def capacidades_encendidas(politica: Politica | None = None) -> set[str]:
    """Las que la persona deja ofrecer. `estado` no se puede apagar: es quien dice que
    el agente está vivo y qué versión habla."""
    politica = politica if politica is not None else Politica.cargar()
    return {c for c, encendida in politica.capacidades.items() if encendida}
