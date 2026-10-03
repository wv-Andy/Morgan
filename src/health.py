"""
Estado de salud por servicio.

La especificación V1.3 lo pide explícitamente (§8): **no** un único indicador
`internet = true/false`, sino una entrada por dependencia. Puede haber conexión y
estar caído Supabase; puede responder Supabase y estar caído el proveedor LLM.
Cada dependencia mantiene su propio estado.

Dos reglas de diseño que rigen todo el módulo:

1. **Consultar el estado nunca bloquea.** Quien pregunta lee el último valor
   conocido. Las comprobaciones se hacen bajo demanda con un TTL, y si el valor
   sigue fresco no se toca la red. Una petición del usuario jamás debe esperar a
   un sondeo.
2. **Un servicio caído no se machaca a reintentos.** Tras varios fallos seguidos
   se espacia la siguiente comprobación con retroceso exponencial, para no
   entrar en un bucle agresivo de reconexión.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ServiceState(str, Enum):
    """Estados posibles de una dependencia."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


# Resultado de una comprobación: (estado, detalle)
CheckResult = tuple[ServiceState, str | None]
CheckFn = Callable[[], CheckResult]


@dataclass
class ServiceStatus:
    """Última información conocida de un servicio."""

    name: str
    state: ServiceState = ServiceState.UNKNOWN
    detail: str | None = None
    checked_at: float | None = None
    consecutive_failures: int = 0
    required_for: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_usable(self) -> bool:
        """Un servicio degradado sigue siendo utilizable, solo que peor."""
        return self.state in (ServiceState.AVAILABLE, ServiceState.DEGRADED)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "age_seconds": round(time.monotonic() - self.checked_at, 1) if self.checked_at else None,
            "required_for": list(self.required_for),
        }


class ServiceHealth:
    """Registro central del estado de las dependencias de Morgan."""

    def __init__(self, ttl: float = 30.0, max_backoff: float = 300.0):
        self.ttl = ttl
        self.max_backoff = max_backoff
        self._checks: dict[str, CheckFn] = {}
        self._status: dict[str, ServiceStatus] = {}
        self._lock = threading.Lock()

    def register(self, name: str, check: CheckFn, required_for: tuple[str, ...] = ()) -> None:
        """Da de alta un servicio y la función que comprueba su disponibilidad."""
        with self._lock:
            self._checks[name] = check
            self._status.setdefault(name, ServiceStatus(name=name, required_for=required_for))
            self._status[name].required_for = required_for

    def _next_check_delay(self, status: ServiceStatus) -> float:
        """TTL normal, o retroceso exponencial si el servicio viene fallando."""
        if status.consecutive_failures == 0:
            return self.ttl
        # 2^n acotado: 30 s, 60 s, 120 s, 240 s, ... hasta max_backoff.
        return min(self.ttl * (2 ** status.consecutive_failures), self.max_backoff)

    def get(self, name: str) -> ServiceStatus:
        """Devuelve el estado conocido, **sin** comprobar nada."""
        with self._lock:
            return self._status.get(name, ServiceStatus(name=name))

    def check(self, name: str, force: bool = False) -> ServiceStatus:
        """Comprueba un servicio si su valor ha caducado. Devuelve el estado."""
        with self._lock:
            status = self._status.get(name)
            check_fn = self._checks.get(name)

        if status is None or check_fn is None:
            return ServiceStatus(name=name)

        fresco = (
            status.checked_at is not None
            and (time.monotonic() - status.checked_at) < self._next_check_delay(status)
        )
        if fresco and not force:
            return status

        try:
            state, detail = check_fn()
        except Exception as exc:
            # Una comprobación que falla es información, no un error del sistema.
            state, detail = ServiceState.UNAVAILABLE, str(exc)
            logger.debug("La comprobación de '%s' lanzó una excepción: %s", name, exc)

        with self._lock:
            status.state = state
            status.detail = detail
            status.checked_at = time.monotonic()
            if state == ServiceState.AVAILABLE:
                if status.consecutive_failures:
                    logger.info("El servicio '%s' se ha recuperado", name)
                status.consecutive_failures = 0
            else:
                status.consecutive_failures += 1
                if status.consecutive_failures == 1:
                    logger.warning("El servicio '%s' pasa a %s: %s", name, state.value, detail)
            return status

    def check_all(self, force: bool = False) -> dict[str, ServiceStatus]:
        """Comprueba todos los servicios registrados."""
        for name in list(self._checks):
            self.check(name, force=force)
        return self.snapshot()

    def snapshot(self) -> dict[str, ServiceStatus]:
        with self._lock:
            return dict(self._status)

    def is_usable(self, name: str) -> bool:
        return self.get(name).is_usable

    def overall(self) -> ServiceState:
        """Estado global derivado del de los servicios.

        Se calcula, no se almacena: un estado global guardado aparte se
        desincroniza del real en cuanto una dependencia cambia.
        """
        estados = [s.state for s in self.snapshot().values()]
        if not estados:
            return ServiceState.UNKNOWN
        if all(e == ServiceState.AVAILABLE for e in estados):
            return ServiceState.AVAILABLE
        if any(e in (ServiceState.AVAILABLE, ServiceState.DEGRADED) for e in estados):
            return ServiceState.DEGRADED
        return ServiceState.UNAVAILABLE

    def reset(self) -> None:
        """Olvida lo conocido y fuerza a recomprobar (útil en pruebas)."""
        with self._lock:
            for status in self._status.values():
                status.state = ServiceState.UNKNOWN
                status.detail = None
                status.checked_at = None
                status.consecutive_failures = 0
