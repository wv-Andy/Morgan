"""
Sistema de registro de auditoría inmutable para Morgan (V0.9).
Registra cada invocación de herramientas, nivel de riesgo, usuario y resultado.
"""

import json
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Any

from src.config import get_settings
from src.tools.terminal import SECRET_KEYWORDS

logger = logging.getLogger(__name__)


def _anotar_token(entrada: dict) -> None:
    """Si la petición va con un token personal de API, anota cuál (plan de la API).

    Su id, nunca el valor: sirve para saber qué cliente hizo qué y revocar ese
    en concreto. Solo cuando lo hay, para no cambiar las entradas de siempre.
    """
    from src.identidad.tokens import token_actual

    token = token_actual()
    if token:
        entrada["token"] = token


class AuditLogger:
    """Registra y consulta eventos de auditoría de seguridad en formato JSON Lines."""

    def __init__(self, log_path: str | Path | None = None):
        if log_path is None:
            logs_dir = get_settings().log_dir
            logs_dir.mkdir(parents=True, exist_ok=True)
            self.log_file = logs_dir / "audit.log"
        else:
            self.log_file = Path(log_path)
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        tool_name: str,
        risk_level: str,
        authorized: bool,
        args: dict[str, Any] | None = None,
        success: bool = True,
        error: str | None = None,
        duration_ms: float = 0.0,
    ) -> None:
        """Registra un evento de auditoría."""
        sanitized_args = {}
        if args:
            for k, v in args.items():
                if any(sec in k.upper() for sec in SECRET_KEYWORDS):
                    sanitized_args[k] = "********"
                else:
                    v_str = str(v)
                    sanitized_args[k] = v_str[:250] if len(v_str) > 250 else v

        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool": tool_name,
            "risk_level": risk_level,
            "authorized": authorized,
            "args": sanitized_args,
            "success": success,
            "error": error,
            "duration_ms": round(duration_ms, 2),
        }
        _anotar_token(entry)

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def registrar_evento(
        self,
        accion: str,
        actor: str,
        objetivo: str | None = None,
        resultado: str = "ok",
        detalle: str | None = None,
    ) -> None:
        """Registra una acción administrativa.

        Distinto de `log`, que registra la ejecución de una herramienta. Aquí se
        anotan las decisiones sobre el propio Morgan —cambiar el rol de alguien,
        promocionar al propietario, conectar un servicio externo— que no pasan
        por el catálogo de herramientas y aun así conviene poder reconstruir.

        Nunca recibe credenciales: quien llama pasa identificadores y un texto
        corto, no cuerpos de petición ni tokens.
        """
        entrada = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tipo": "evento",
            "accion": accion,
            "actor": actor,
            "objetivo": objetivo,
            "resultado": resultado,
            "detalle": (detalle or "")[:250] or None,
        }
        _anotar_token(entrada)

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")

    def get_recent(self, limit: int = 25) -> list[dict]:
        """Obtiene las entradas más recientes del log de auditoría.

        Lee sólo la cola del fichero en lugar de cargarlo entero: el log crece sin
        límite y una lectura completa hacía que /audit y /status se degradaran de
        forma lineal con el uso.
        """
        if not self.log_file.exists():
            return []

        try:
            raw_lines = self._read_tail_lines(limit)
        except OSError:
            logger.warning("No se pudo leer el log de auditoría %s", self.log_file, exc_info=True)
            return []

        entries = []
        for line in raw_lines:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                entries.append(json.loads(line_str))
            except json.JSONDecodeError:
                # Una línea truncada (por ejemplo, tras un corte de energía) no debe
                # invalidar la consulta del resto del historial.
                continue

        return entries[-limit:]

    def _read_tail_lines(self, limit: int) -> list[str]:
        """Devuelve las últimas líneas del fichero leyendo desde el final."""
        # Margen generoso por si hay líneas corruptas o vacías entre las útiles.
        wanted = max(limit * 2, 50)
        block_size = 8192
        with open(self.log_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = position = f.tell()
            chunks: list[bytes] = []
            newlines = 0

            while position > 0 and newlines <= wanted:
                read_size = min(block_size, position)
                position -= read_size
                f.seek(position)
                chunk = f.read(read_size)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")

            data = b"".join(reversed(chunks))

        if position > 0:
            # Descartar la primera línea, que puede haber quedado cortada a la mitad.
            data = data.split(b"\n", 1)[-1] if file_size > len(data) else data

        return data.decode("utf-8", errors="replace").splitlines()[-wanted:]

    get_recent_logs = get_recent
