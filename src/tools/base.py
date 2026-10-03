"""
Clase base abstracta para todas las herramientas del agente Morgan (V0.8).

Define la estructura formal de una herramienta: nombre, descripción, esquema JSON,
nivel de permiso/riesgo, categoría temática y método execute().
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class ToolCategory(str, Enum):
    """Categorías de dominio para organización modular de herramientas."""
    SYSTEM = "system"
    FILESYSTEM = "filesystem"
    TERMINAL = "terminal"
    MEMORY = "memory"
    WEB = "web"
    CODING = "coding"
    GIT = "git"
    GENERAL = "general"


class RiskLevel(str, Enum):
    """Niveles de riesgo avanzados para el sistema de seguridad de Morgan."""
    SAFE = "safe"              # Solo lectura, diagnóstico, inocuo
    LOW_RISK = "low_risk"      # Inspección de código, búsquedas locales
    MODERATE = "moderate"      # Creación de archivos, recordar hechos, mover
    HIGH_RISK = "high_risk"    # Patch de código, commits de git, tests
    CRITICAL = "critical"      # Comandos de terminal, borrado, kill de procesos


#: Los niveles de lo que **cambia** algo (4.0). Incluye `sensitive`, el nombre antiguo de
#: `critical` que aún declaran `delete_file`, `execute_command` y `kill_process` en local:
#: sin él, la 4.0-C no contaba un borrado fallido como un cambio que no salió.
NIVELES_QUE_CAMBIAN = frozenset({"moderate", "high_risk", "sensitive", "critical"})


class Tool(ABC):
    """Clase base para herramientas del agente Morgan."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identificador único de la herramienta."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Descripción de la herramienta para guiar la selección del LLM."""
        ...

    @property
    def parameters(self) -> dict:
        """Esquema JSON de los parámetros que acepta la herramienta."""
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    @property
    def permission_level(self) -> str:
        """
        Nivel de permiso (compatible hacia atrás: safe | moderate | sensitive).
        """
        return "safe"

    @property
    def category(self) -> str:
        """Categoría o dominio temático al que pertenece la herramienta."""
        return ToolCategory.GENERAL.value

    @property
    def requires_local(self) -> bool:
        """Si la herramienta necesita acceso a la máquina del usuario.

        El valor por defecto es True a propósito: una herramienta nueva queda
        fuera del entorno cloud salvo que declare explícitamente que es segura
        ahí. Equivocarse por exceso deja una capacidad sin exponer; equivocarse
        por defecto expondría el ordenador del usuario a un servidor remoto.
        """
        return True

    def disponible(self) -> bool:
        """Si se puede usar **en este turno** (3.0-E). Casi todas, siempre.

        Las del equipo de la persona (`src/canal/herramientas.py`) solo lo están
        mientras su agente local está conectado: el núcleo quita del catálogo del
        turno las que no, y así Morgan sabe que el PC está apagado antes de intentarlo.
        """
        return True

    #: Qué se le dice al modelo si pide una que no está disponible (4.2). Cada familia dice
    #: su motivo: antes todas decían «el PC no está conectado», también las de adjuntos,
    #: conocimiento o GitHub, y Morgan se lo habría repetido a la persona.
    motivo_no_disponible = "no está disponible ahora."

    @property
    def exige_plan(self) -> bool:
        """Si la herramienta **solo** se ejecuta con un paso de plan aprobado (V2.0.16).

        Para lo que actúa fuera de Morgan en nombre de alguien: crear un evento en
        su calendario, escribir en su Notion. La confirmación de siempre no basta
        ahí por dos motivos:

        - en la web no hay consola, así que a quien no es propietario se le
          deniega todo lo confirmable, con plan aprobado o sin él;
        - al propietario no se le pregunta nunca, y escribir en su calendario sin
          que lo haya visto antes es justo lo que no se quiere.

        Con `True`, la herramienta se ejecuta **si y solo si** hay en esta
        conversación un plan aprobado con un paso de esta herramienta **con estos
        mismos argumentos** que no se haya ejecutado ya. Lo aprobado es lo que se
        hace, una vez. Ver `Agent._autorizado_por_plan`.
        """
        return False

    @property
    def risk_level(self) -> str:
        """Nivel de riesgo categorizado para políticas de seguridad avanzadas."""
        mapping = {
            "safe": RiskLevel.SAFE.value,
            "low_risk": RiskLevel.LOW_RISK.value,
            "moderate": RiskLevel.MODERATE.value,
            "high_risk": RiskLevel.HIGH_RISK.value,
            "sensitive": RiskLevel.CRITICAL.value,
            "critical": RiskLevel.CRITICAL.value,
        }
        return mapping.get(self.permission_level, self.permission_level)

    @abstractmethod
    def execute(self, **kwargs: Any) -> dict:
        """
        Ejecuta la herramienta con los parámetros proporcionados.

        Returns:
            dict con {"success": bool, "data": Any, "error": str | None}
        """
        ...

    def get_schema(self) -> dict:
        """Devuelve el esquema de la herramienta para function calling."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def __repr__(self) -> str:
        level_icons = {
            "safe": "\U0001f7e2",
            "low_risk": "\U0001f7e2",
            "moderate": "\U0001f7e1",
            "high_risk": "\U0001f7e0",
            "sensitive": "\U0001f534",
            "critical": "\U0001f534",
        }
        icon = level_icons.get(self.permission_level, level_icons.get(self.risk_level, "\u2699"))
        return f"<Tool: {self.name} {icon} [{self.category.upper()}] - {self.risk_level.upper()}>"
