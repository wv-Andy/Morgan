"""
Dónde se guardan los espacios de trabajo: SQLite en local, Supabase en la nube.

Las dos implementaciones tienen la misma interfaz y las mismas reglas, y las dos
filtran **siempre** por el usuario de la petición, igual que el resto de
repositorios. Un espacio de otra persona no existe: pedirlo por su identificador
devuelve `None`, que la API convierte en 404.

## Borrar un espacio no borra lo que había dentro

Sus conversaciones, archivos y documentos vuelven a «General». Borrar un espacio
es una decisión de organización, y convertirla en la pérdida de todo su contenido
sería castigar a quien reordena. Quien quiera borrar el contenido lo borra aparte,
con la confirmación que ya tiene cada cosa.
"""

import time

from src.espacios.modelos import (
    Espacio,
    EspacioDuplicado,
    nuevo_id,
    validar_instrucciones,
    validar_nombre,
)
from src.identidad.contexto import usuario_actual
from src.memory.db import guard

#: Las tablas cuyo contenido pertenece a un espacio. Al borrar uno, sus filas
#: vuelven a «General» en todas ellas.
TABLAS_CON_ESPACIO = ("sessions", "uploads", "conocimiento")


def _duplicado(nombre: str) -> EspacioDuplicado:
    return EspacioDuplicado(f"Ya tienes un espacio llamado «{nombre}».")


class RepositorioDeEspaciosSQLite:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _a_espacio(fila) -> Espacio:
        return Espacio(
            id=fila["id"],
            nombre=fila["nombre"],
            instrucciones=fila["instrucciones"] or "",
            creado_en=fila["creado_en"] or 0.0,
            actualizado_en=fila["actualizado_en"] or 0.0,
        )

    def _id_por_nombre(self, nombre: str) -> str | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT id FROM espacios WHERE user_id = ? AND nombre = ?",
                (usuario_actual(), nombre),
            ).fetchone()
        return fila["id"] if fila else None

    @guard("crear el espacio de trabajo")
    def crear(self, nombre: str, instrucciones: str = "") -> Espacio:
        nombre = validar_nombre(nombre)
        instrucciones = validar_instrucciones(instrucciones)
        if self._id_por_nombre(nombre):
            raise _duplicado(nombre)

        ahora = time.time()
        espacio = Espacio(nuevo_id(), nombre, instrucciones, ahora, ahora)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO espacios "
                "(id, user_id, nombre, instrucciones, creado_en, actualizado_en) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (espacio.id, usuario_actual(), espacio.nombre, espacio.instrucciones,
                 ahora, ahora),
            )
        return espacio

    @guard("recuperar el espacio de trabajo")
    def obtener(self, espacio_id: str) -> Espacio | None:
        if not espacio_id:
            return None
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT * FROM espacios WHERE id = ? AND user_id = ?",
                (espacio_id, usuario_actual()),
            ).fetchone()
        return self._a_espacio(fila) if fila else None

    @guard("listar los espacios de trabajo")
    def listar(self) -> list[Espacio]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM espacios WHERE user_id = ? "
                "ORDER BY nombre COLLATE NOCASE",
                (usuario_actual(),),
            ).fetchall()
        return [self._a_espacio(f) for f in filas]

    @guard("actualizar el espacio de trabajo")
    def actualizar(
        self,
        espacio_id: str,
        *,
        nombre: str | None = None,
        instrucciones: str | None = None,
    ) -> Espacio | None:
        actual = self.obtener(espacio_id)
        if actual is None:
            return None

        if nombre is not None:
            nombre = validar_nombre(nombre)
            otro = self._id_por_nombre(nombre)
            if otro and otro != espacio_id:
                raise _duplicado(nombre)
            actual.nombre = nombre
        if instrucciones is not None:
            actual.instrucciones = validar_instrucciones(instrucciones)

        actual.actualizado_en = time.time()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE espacios SET nombre = ?, instrucciones = ?, actualizado_en = ? "
                "WHERE id = ? AND user_id = ?",
                (actual.nombre, actual.instrucciones, actual.actualizado_en,
                 espacio_id, usuario_actual()),
            )
        return actual

    @guard("eliminar el espacio de trabajo")
    def eliminar(self, espacio_id: str) -> bool:
        with self.db.connect() as conn:
            # Primero lo que había dentro vuelve a «General». En la misma
            # transacción: si el borrado fallara a medias, no puede quedar
            # contenido apuntando a un espacio que ya no existe.
            for tabla in TABLAS_CON_ESPACIO:
                conn.execute(
                    f"UPDATE {tabla} SET espacio_id = NULL "
                    "WHERE espacio_id = ? AND user_id = ?",
                    (espacio_id, usuario_actual()),
                )
            return conn.execute(
                "DELETE FROM espacios WHERE id = ? AND user_id = ?",
                (espacio_id, usuario_actual()),
            ).rowcount > 0


class RepositorioDeEspaciosSupabase:
    def __init__(self, client, device: str | None = None):
        # `device` no se usa: está para que se construya igual que el resto de
        # repositorios de Supabase, y el barrido de aislamiento lo cubra sin
        # tratarlo aparte.
        self.client = client
        self.device = device

    @staticmethod
    def _a_espacio(fila: dict) -> Espacio:
        return Espacio(
            id=fila["id"],
            nombre=fila.get("nombre", ""),
            instrucciones=fila.get("instrucciones") or "",
            creado_en=fila.get("creado_en") or 0.0,
            actualizado_en=fila.get("actualizado_en") or 0.0,
        )

    def _id_por_nombre(self, nombre: str) -> str | None:
        from src.memory.supabase_repositories import _esc, _mio

        filas = self.client.select(
            "espacios", f"nombre=eq.{_esc(nombre)}&{_mio()}&select=id&limit=1"
        )
        return filas[0]["id"] if filas else None

    def crear(self, nombre: str, instrucciones: str = "") -> Espacio:
        nombre = validar_nombre(nombre)
        instrucciones = validar_instrucciones(instrucciones)
        if self._id_por_nombre(nombre):
            raise _duplicado(nombre)

        ahora = time.time()
        espacio = Espacio(nuevo_id(), nombre, instrucciones, ahora, ahora)
        self.client.upsert("espacios", [{
            **espacio.to_dict(),
            "user_id": usuario_actual(),
        }], on_conflict="user_id,id")
        return espacio

    def obtener(self, espacio_id: str) -> Espacio | None:
        from src.memory.supabase_repositories import _esc, _mio

        if not espacio_id:
            return None
        filas = self.client.select(
            "espacios", f"id=eq.{_esc(espacio_id)}&{_mio()}&select=*&limit=1"
        )
        return self._a_espacio(filas[0]) if filas else None

    def listar(self) -> list[Espacio]:
        from src.memory.supabase_repositories import _mio

        filas = self.client.select("espacios", f"{_mio()}&select=*&order=nombre.asc")
        return [self._a_espacio(f) for f in filas]

    def actualizar(
        self,
        espacio_id: str,
        *,
        nombre: str | None = None,
        instrucciones: str | None = None,
    ) -> Espacio | None:
        from src.memory.supabase_repositories import _esc, _mio

        actual = self.obtener(espacio_id)
        if actual is None:
            return None

        if nombre is not None:
            nombre = validar_nombre(nombre)
            otro = self._id_por_nombre(nombre)
            if otro and otro != espacio_id:
                raise _duplicado(nombre)
            actual.nombre = nombre
        if instrucciones is not None:
            actual.instrucciones = validar_instrucciones(instrucciones)

        actual.actualizado_en = time.time()
        self.client.update(
            "espacios",
            f"id=eq.{_esc(espacio_id)}&{_mio()}",
            {"nombre": actual.nombre, "instrucciones": actual.instrucciones,
             "actualizado_en": actual.actualizado_en},
        )
        return actual

    def eliminar(self, espacio_id: str) -> bool:
        from src.memory.supabase_repositories import _esc, _mio

        # PostgREST no da transacciones entre peticiones. Se devuelve primero el
        # contenido a «General» y después se borra el espacio: si algo falla en
        # medio, lo peor que queda es un espacio vacío, nunca contenido que
        # apunte a un espacio inexistente.
        for tabla in TABLAS_CON_ESPACIO:
            self.client.update(
                tabla,
                f"espacio_id=eq.{_esc(espacio_id)}&{_mio()}",
                {"espacio_id": None},
            )
        borrados = self.client.delete(
            "espacios", f"id=eq.{_esc(espacio_id)}&{_mio()}"
        )
        return len(borrados) > 0


def repositorio_de_espacios(repositories):
    """El repositorio que corresponde al almacén en uso, o `None` si no hay."""
    client = getattr(repositories, "client", None)
    if client is not None:
        return RepositorioDeEspaciosSupabase(client)
    db = getattr(repositories, "db", None)
    if db is not None:
        return RepositorioDeEspaciosSQLite(db)
    return None
