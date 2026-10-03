"""
Dónde se guardan las automatizaciones y los avisos de la bandeja (4.14).

Dos tablas, en SQLite (el Morgan de tu equipo) y en Supabase (la nube), con el mismo
contrato. Como las de cuentas y agentes, en Supabase tienen RLS activo y **ninguna
política**: solo la clave de servicio del backend llega a ellas.

**Reclamar una ejecución es atómico** (`reclamar`): la fila lleva un contador (`reclamo`) y
solo se lanza si nadie lo ha subido antes. El reloj de Supabase llama cada minuto y Render
puede tardar en despertar; dos llamadas que se cruzan no ejecutan dos veces lo mismo.
"""

import json
import secrets
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import quote

#: Los avisos se guardan 30 días (como el historial de órdenes a los agentes).
DURAN_LOS_AVISOS = 30 * 86400
#: Y como mucho tantos por persona en la bandeja (los más nuevos).
MAX_AVISOS = 200

COLUMNAS = ("id", "user_id", "nombre", "instruccion", "horario", "zona", "necesita_pc", "activa",
            "proxima", "esperando_pc_desde", "reclamo", "fallos_seguidos", "ultima", "ultimo_estado",
            "creado_en", "pasos")
COLUMNAS_AVISO = ("id", "user_id", "automatizacion_id", "titulo", "texto", "estado", "herramientas",
                  "leido", "creado_en")


def nuevo_id(prefijo: str) -> str:
    return f"{prefijo}_{secrets.token_hex(8)}"


def _normalizar(fila: dict | None) -> dict | None:
    """Lo mismo venga de SQLite (enteros, texto) o de Postgres (booleanos)."""
    if fila is None:
        return None
    fila = dict(fila)
    if isinstance(fila.get("horario"), str):
        try:
            fila["horario"] = json.loads(fila["horario"])
        except ValueError:
            fila["horario"] = {}
    if isinstance(fila.get("pasos"), str):
        try:
            fila["pasos"] = json.loads(fila["pasos"])
        except ValueError:
            fila["pasos"] = None
    if isinstance(fila.get("herramientas"), str):
        try:
            fila["herramientas"] = json.loads(fila["herramientas"])
        except ValueError:
            fila["herramientas"] = []
    for clave in ("necesita_pc", "activa", "leido"):
        if clave in fila:
            fila[clave] = bool(fila[clave])
    return fila


def _para_guardar(datos: dict) -> dict:
    datos = dict(datos)
    if isinstance(datos.get("horario"), dict):
        datos["horario"] = json.dumps(datos["horario"], ensure_ascii=False)
    if isinstance(datos.get("pasos"), list):
        datos["pasos"] = json.dumps(datos["pasos"], ensure_ascii=False)
    if isinstance(datos.get("herramientas"), list):
        datos["herramientas"] = json.dumps(datos["herramientas"], ensure_ascii=False)
    return datos


class RepositorioDeAutomatizaciones(ABC):
    # --- Automatizaciones (de una persona) ---
    @abstractmethod
    def crear(self, datos: dict) -> None: ...

    @abstractmethod
    def listar(self, user_id: str) -> list[dict]: ...

    @abstractmethod
    def obtener(self, user_id: str, automatizacion_id: str) -> dict | None: ...

    @abstractmethod
    def actualizar(self, user_id: str, automatizacion_id: str, valores: dict) -> bool: ...

    @abstractmethod
    def borrar(self, user_id: str, automatizacion_id: str) -> bool: ...

    def contar_activas(self, user_id: str) -> int:
        return sum(1 for a in self.listar(user_id) if a["activa"])

    # --- El reloj (de todas las personas) ---
    @abstractmethod
    def pendientes(self, ahora: float, limite: int = 20) -> list[dict]:
        """Las activas cuya hora ya llegó, la más atrasada primero."""

    @abstractmethod
    def reclamar(self, automatizacion_id: str, reclamo: int, valores: dict) -> bool:
        """Aplica `valores` **solo si** `reclamo` sigue siendo ese, y lo sube. Si otro
        reloj se adelantó, no toca nada y devuelve False."""

    @abstractmethod
    def anotar(self, automatizacion_id: str, valores: dict) -> None:
        """Cómo acabó una ejecución (sin mirar de quién: la reclamó el reloj)."""

    # --- La bandeja ---
    @abstractmethod
    def crear_aviso(self, datos: dict) -> None: ...

    @abstractmethod
    def avisos(self, user_id: str, limite: int = 50) -> list[dict]: ...

    @abstractmethod
    def sin_leer(self, user_id: str) -> int: ...

    @abstractmethod
    def marcar_leidos(self, user_id: str, ids: list[str] | None = None) -> int: ...

    @abstractmethod
    def limpiar(self, ahora: float) -> int: ...


class AutomatizacionesSQLite(RepositorioDeAutomatizaciones):
    def __init__(self, db):
        self.db = db

    def crear(self, datos: dict) -> None:
        datos = _para_guardar(datos)
        columnas = [c for c in COLUMNAS if c in datos]
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO automatizaciones ({', '.join(columnas)}) VALUES ({', '.join('?' * len(columnas))})",
                [datos[c] for c in columnas],
            )

    def listar(self, user_id: str) -> list[dict]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM automatizaciones WHERE user_id = ? ORDER BY creado_en", (user_id,)
            ).fetchall()
        return [_normalizar(dict(f)) for f in filas]

    def obtener(self, user_id: str, automatizacion_id: str) -> dict | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT * FROM automatizaciones WHERE user_id = ? AND id = ?", (user_id, automatizacion_id)
            ).fetchone()
        return _normalizar(dict(fila)) if fila else None

    def actualizar(self, user_id: str, automatizacion_id: str, valores: dict) -> bool:
        valores = _para_guardar(valores)
        columnas = [c for c in valores if c in COLUMNAS and c not in ("id", "user_id")]
        if not columnas:
            return False
        with self.db.connect() as conn:
            return conn.execute(
                f"UPDATE automatizaciones SET {', '.join(f'{c} = ?' for c in columnas)} "
                "WHERE user_id = ? AND id = ?",
                [valores[c] for c in columnas] + [user_id, automatizacion_id],
            ).rowcount > 0

    def borrar(self, user_id: str, automatizacion_id: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "DELETE FROM automatizaciones WHERE user_id = ? AND id = ?", (user_id, automatizacion_id)
            ).rowcount > 0

    def pendientes(self, ahora: float, limite: int = 20) -> list[dict]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM automatizaciones WHERE activa = 1 AND proxima <= ? ORDER BY proxima LIMIT ?",
                (ahora, int(limite)),
            ).fetchall()
        return [_normalizar(dict(f)) for f in filas]

    def reclamar(self, automatizacion_id: str, reclamo: int, valores: dict) -> bool:
        valores = {**_para_guardar(valores), "reclamo": int(reclamo) + 1}
        columnas = [c for c in valores if c in COLUMNAS and c not in ("id", "user_id")]
        with self.db.connect() as conn:
            return conn.execute(
                f"UPDATE automatizaciones SET {', '.join(f'{c} = ?' for c in columnas)} "
                "WHERE id = ? AND reclamo = ?",
                [valores[c] for c in columnas] + [automatizacion_id, int(reclamo)],
            ).rowcount > 0

    def anotar(self, automatizacion_id: str, valores: dict) -> None:
        valores = _para_guardar(valores)
        columnas = [c for c in valores if c in COLUMNAS and c not in ("id", "user_id")]
        if columnas:
            with self.db.connect() as conn:
                conn.execute(
                    f"UPDATE automatizaciones SET {', '.join(f'{c} = ?' for c in columnas)} WHERE id = ?",
                    [valores[c] for c in columnas] + [automatizacion_id],
                )

    def crear_aviso(self, datos: dict) -> None:
        datos = _para_guardar(datos)
        columnas = [c for c in COLUMNAS_AVISO if c in datos]
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO avisos ({', '.join(columnas)}) VALUES ({', '.join('?' * len(columnas))})",
                [datos[c] for c in columnas],
            )

    def avisos(self, user_id: str, limite: int = 50) -> list[dict]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM avisos WHERE user_id = ? ORDER BY creado_en DESC LIMIT ?", (user_id, int(limite))
            ).fetchall()
        return [_normalizar(dict(f)) for f in filas]

    def sin_leer(self, user_id: str) -> int:
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM avisos WHERE user_id = ? AND leido = 0", (user_id,)
            ).fetchone()[0]

    def marcar_leidos(self, user_id: str, ids: list[str] | None = None) -> int:
        with self.db.connect() as conn:
            if ids is None:
                return conn.execute(
                    "UPDATE avisos SET leido = 1 WHERE user_id = ? AND leido = 0", (user_id,)
                ).rowcount
            if not ids:
                return 0
            return conn.execute(
                f"UPDATE avisos SET leido = 1 WHERE user_id = ? AND id IN ({', '.join('?' * len(ids))})",
                [user_id, *ids],
            ).rowcount

    def limpiar(self, ahora: float) -> int:
        with self.db.connect() as conn:
            n = conn.execute("DELETE FROM avisos WHERE creado_en < ?", (ahora - DURAN_LOS_AVISOS,)).rowcount
            # Y lo que pase de MAX_AVISOS por persona, lo más viejo.
            n += conn.execute(
                "DELETE FROM avisos WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER "
                "(PARTITION BY user_id ORDER BY creado_en DESC) AS n FROM avisos) WHERE n > ?)",
                (MAX_AVISOS,),
            ).rowcount
        return n


class AutomatizacionesSupabase(RepositorioDeAutomatizaciones):
    """Por PostgREST con la clave de servicio, como las cuentas (`CuentasSupabase`)."""

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _c(valor: Any) -> str:
        return quote(str(valor), safe="")

    def crear(self, datos: dict) -> None:
        datos = _para_guardar(datos)
        self.client.insert("automatizaciones", [{c: datos[c] for c in COLUMNAS if c in datos}])

    def listar(self, user_id: str) -> list[dict]:
        return [_normalizar(f) for f in self.client.select(
            "automatizaciones", f"user_id=eq.{self._c(user_id)}&order=creado_en.asc")]

    def obtener(self, user_id: str, automatizacion_id: str) -> dict | None:
        filas = self.client.select(
            "automatizaciones", f"user_id=eq.{self._c(user_id)}&id=eq.{self._c(automatizacion_id)}&limit=1")
        return _normalizar(filas[0]) if filas else None

    def actualizar(self, user_id: str, automatizacion_id: str, valores: dict) -> bool:
        valores = {c: v for c, v in _para_guardar(valores).items() if c in COLUMNAS and c not in ("id", "user_id")}
        if not valores:
            return False
        return bool(self.client.update(
            "automatizaciones", f"user_id=eq.{self._c(user_id)}&id=eq.{self._c(automatizacion_id)}", valores))

    def borrar(self, user_id: str, automatizacion_id: str) -> bool:
        return bool(self.client.delete(
            "automatizaciones", f"user_id=eq.{self._c(user_id)}&id=eq.{self._c(automatizacion_id)}"))

    def pendientes(self, ahora: float, limite: int = 20) -> list[dict]:
        return [_normalizar(f) for f in self.client.select(
            "automatizaciones", f"activa=is.true&proxima=lte.{float(ahora)}&order=proxima.asc&limit={int(limite)}")]

    def reclamar(self, automatizacion_id: str, reclamo: int, valores: dict) -> bool:
        valores = {**{c: v for c, v in _para_guardar(valores).items() if c in COLUMNAS and c not in ("id", "user_id")},
                   "reclamo": int(reclamo) + 1}
        return bool(self.client.update(
            "automatizaciones", f"id=eq.{self._c(automatizacion_id)}&reclamo=eq.{int(reclamo)}", valores))

    def anotar(self, automatizacion_id: str, valores: dict) -> None:
        valores = {c: v for c, v in _para_guardar(valores).items() if c in COLUMNAS and c not in ("id", "user_id")}
        if valores:
            self.client.update("automatizaciones", f"id=eq.{self._c(automatizacion_id)}", valores)

    def crear_aviso(self, datos: dict) -> None:
        datos = _para_guardar(datos)
        self.client.insert("avisos", [{c: datos[c] for c in COLUMNAS_AVISO if c in datos}])

    def avisos(self, user_id: str, limite: int = 50) -> list[dict]:
        return [_normalizar(f) for f in self.client.select(
            "avisos", f"user_id=eq.{self._c(user_id)}&order=creado_en.desc&limit={int(limite)}")]

    def sin_leer(self, user_id: str) -> int:
        return len(self.client.select(
            "avisos", f"user_id=eq.{self._c(user_id)}&leido=is.false&select=id&limit={MAX_AVISOS}"))

    def marcar_leidos(self, user_id: str, ids: list[str] | None = None) -> int:
        filtro = f"user_id=eq.{self._c(user_id)}&leido=is.false"
        if ids is not None:
            if not ids:
                return 0
            filtro += "&id=in.(" + ",".join(f'"{self._c(i)}"' for i in ids) + ")"
        return len(self.client.update("avisos", filtro, {"leido": True}))

    def limpiar(self, ahora: float) -> int:
        return len(self.client.delete("avisos", f"creado_en=lt.{ahora - DURAN_LOS_AVISOS}"))


def repositorio_de_automatizaciones(repositories: Any) -> RepositorioDeAutomatizaciones:
    """El que corresponde a los repositorios en uso, como `repositorio_de_cuentas`."""
    db = getattr(repositories, "db", None)
    if db is not None:
        return AutomatizacionesSQLite(db)
    client = getattr(repositories, "client", None)
    if client is not None:
        return AutomatizacionesSupabase(client)
    raise RuntimeError("Los repositorios en uso no ofrecen dónde guardar las automatizaciones.")
