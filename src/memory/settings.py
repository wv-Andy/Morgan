"""
Perfil y preferencias del usuario (Etapa B de la especificación web).

**No hay tabla nueva.** Estos datos se guardan como recuerdos, con dos categorías
reservadas, y eso no es un atajo: es la única forma de que las preferencias se
cumplan de verdad. `MemoryManager.get_context_summary()` los pone en el
prompt de cada turno de la persona (4.0), así que el nombre o el idioma preferido llegan a Morgan solos, sin
tener que enseñarle a leer una tabla de configuración.

La especificación es explícita en esto: «no crear configuraciones que Morgan no
pueda respetar realmente». De ahí que los campos admitidos sean pocos y todos
acaben en el prompt. La apariencia (tema claro u oscuro) no está aquí a propósito:
es una preferencia del navegador, no del agente, y el Core no puede hacer nada con
ella.
"""

import logging
from dataclasses import asdict, dataclass

from src.memory.db import MemoryStorageError
from src.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

CATEGORIA_PERFIL = "perfil"
CATEGORIA_PREFERENCIAS = "preferencias"

# Longitud máxima por campo. No es una restricción arbitraria: todo esto viaja en
# cada petición al modelo, y un texto largo desplaza contexto útil.
MAX_CAMPO = 500
MAX_SOBRE_MI = 1000


@dataclass
class UserSettings:
    """Lo que el usuario configura y Morgan puede respetar."""

    # --- Perfil ---
    nombre: str = ""
    ocupacion: str = ""
    sobre_mi: str = ""

    # --- Preferencias ---
    idioma: str = ""
    estilo_respuesta: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# Cómo se guarda cada campo: clave del recuerdo, categoría y límite.
_CAMPOS: dict[str, tuple[str, str, int]] = {
    "nombre": ("usuario_nombre", CATEGORIA_PERFIL, MAX_CAMPO),
    "ocupacion": ("usuario_ocupacion", CATEGORIA_PERFIL, MAX_CAMPO),
    "sobre_mi": ("usuario_sobre_mi", CATEGORIA_PERFIL, MAX_SOBRE_MI),
    "idioma": ("preferencia_idioma", CATEGORIA_PREFERENCIAS, MAX_CAMPO),
    "estilo_respuesta": ("preferencia_estilo", CATEGORIA_PREFERENCIAS, MAX_CAMPO),
}


class SettingsStore:
    """Lee y escribe el perfil sobre la memoria persistente."""

    def __init__(self, memory_manager: MemoryManager):
        self.memory = memory_manager

    def load(self) -> UserSettings:
        """Recupera los ajustes. Un fallo de la base devuelve valores vacíos.

        Que la configuración no se pueda leer no debe impedir usar Morgan: es
        información accesoria, no un requisito para conversar.
        """
        valores: dict[str, str] = {}
        try:
            for categoria in (CATEGORIA_PERFIL, CATEGORIA_PREFERENCIAS):
                for recuerdo in self.memory.recall(category=categoria):
                    valores[recuerdo["key"]] = recuerdo["value"]
        except MemoryStorageError:
            logger.warning("No se pudieron leer los ajustes del usuario", exc_info=True)
            return UserSettings()

        return UserSettings(**{
            campo: valores.get(clave, "")
            for campo, (clave, _, _) in _CAMPOS.items()
        })

    def save(self, cambios: dict[str, str | None]) -> UserSettings:
        """Escribe los campos indicados; los que lleguen como None no se tocan.

        Un campo vacío **borra** el recuerdo en lugar de guardar una cadena vacía:
        un «- [PERFIL] usuario_ocupacion: » en el prompt es ruido que confunde al
        modelo sin aportar nada.
        """
        for campo, valor in cambios.items():
            if valor is None or campo not in _CAMPOS:
                continue

            clave, categoria, limite = _CAMPOS[campo]
            limpio = valor.strip()[:limite]

            if limpio:
                self.memory.remember(key=clave, value=limpio, category=categoria)
            else:
                self.memory.forget(clave)

        return self.load()
