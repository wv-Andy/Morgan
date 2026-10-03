"""Identidad y cuentas de usuario de Morgan (identidad, V2.0 adelantada)."""

from src.identidad.contexto import (
    como_usuario,
    fijar_usuario,
    restaurar_usuario,
    rol_actual,
    usuario_actual,
)
from src.identidad.modelos import USUARIO_LOCAL, MorganUser, usuario_local

__all__ = [
    "USUARIO_LOCAL",
    "MorganUser",
    "como_usuario",
    "fijar_usuario",
    "restaurar_usuario",
    "rol_actual",
    "usuario_actual",
    "usuario_local",
]
