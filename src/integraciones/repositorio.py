"""
Dónde viven las integraciones conectadas (V1.9).

Dos implementaciones, SQLite y Supabase, como el resto de la capa de datos. Y
con la lección ya aprendida: **el filtro por usuario va en las dos desde el
primer día**. La versión de Supabase de los repositorios de conversaciones
estuvo cinco versiones sin filtrar porque las pruebas solo ejercitaban SQLite;
aquí las pruebas recorren ambas.

El `user_id` se lee del contexto de la petición, nunca se recibe por parámetro.
Si fuera un argumento, un método nuevo podría olvidarlo y seguir compilando.
"""

import json
import logging
import secrets
import time
from abc import ABC, abstractmethod
from urllib.parse import quote

from src.identidad import usuario_actual
from src.integraciones.modelos import Integracion

logger = logging.getLogger(__name__)

#: Cuánto vale un estado de OAuth. Corto a propósito: es el tiempo que tarda una
#: persona en autorizar en la pantalla de GitHub, no el de una sesión.
VIDA_DEL_ESTADO = 600.0


class RepositorioDeIntegraciones(ABC):
    """Lo que hace falta para conectar y desconectar servicios."""

    @abstractmethod
    def obtener(self, servicio: str) -> Integracion | None:
        """La integración del usuario actual con ese servicio, si la hay."""

    @abstractmethod
    def token_de(self, servicio: str) -> str | None:
        """El token descifrado. Solo lo llama el backend, nunca sale de él."""

    @abstractmethod
    def credenciales_de(self, servicio: str) -> tuple[str, str | None, float | None] | None:
        """(token, token de renovación, caducidad), descifrados. Solo para el backend.

        Existe desde la 2.0.17: los tokens de Google caducan a la hora, y renovarlos
        exige el de renovación y saber cuándo caduca el actual.
        """

    @abstractmethod
    def actualizar_token(self, servicio: str, token: str, expira_en: float | None) -> None:
        """Guarda un token renovado **sin tocar la cuenta, los permisos ni el de renovación**.

        `guardar` sobrescribe todo, y renovar con él dejaría la cuenta y los
        permisos en blanco cada hora.
        """

    @abstractmethod
    def guardar(
        self, servicio: str, token: str, *,
        cuenta: str | None = None, scopes: tuple[str, ...] = (),
        refresco: str | None = None, expira_en: float | None = None,
        metadatos: dict | None = None,
    ) -> Integracion:
        """Conecta el servicio, o actualiza la conexión existente."""

    @abstractmethod
    def listar(self) -> list[Integracion]:
        """Las integraciones del usuario actual."""

    @abstractmethod
    def eliminar(self, servicio: str) -> bool:
        """Desconecta. Devuelve si había algo que desconectar."""

    @abstractmethod
    def anotar_error(self, servicio: str, error: str | None) -> None:
        """Deja constancia del último fallo, para distinguirlo de «no conectado»."""

    # --- Estados del intercambio OAuth ---

    @abstractmethod
    def crear_estado(self, servicio: str) -> str:
        """Emite un estado nuevo y lo guarda. Devuelve el valor."""

    @abstractmethod
    def consumir_estado(self, estado: str, servicio: str) -> str | None:
        """Valida y BORRA el estado. Devuelve el `user_id` que lo pidió.

        Se borra al usarse: un estado reutilizable deja de proteger de nada.
        """


def _ahora() -> float:
    return time.time()


def _nuevo_estado() -> str:
    return secrets.token_urlsafe(32)


class IntegracionesSQLite(RepositorioDeIntegraciones):
    """Las integraciones en el fichero local."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _a_integracion(fila) -> Integracion:
        datos = dict(fila)
        return Integracion(
            user_id=datos["user_id"],
            servicio=datos["servicio"],
            cuenta=datos.get("cuenta"),
            scopes=tuple(s for s in (datos.get("scopes") or "").split(",") if s),
            creado_en=datos.get("creado_en", 0.0) or 0.0,
            actualizado_en=datos.get("actualizado_en", 0.0) or 0.0,
            error=datos.get("error"),
            metadatos=json.loads(datos.get("metadatos") or "{}"),
        )

    def obtener(self, servicio: str) -> Integracion | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT * FROM integraciones WHERE user_id = ? AND servicio = ?",
                (usuario_actual(), servicio),
            ).fetchone()
        return self._a_integracion(fila) if fila else None

    def token_de(self, servicio: str) -> str | None:
        from src.integraciones.secretos import descifrar

        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT token_cifrado FROM integraciones "
                "WHERE user_id = ? AND servicio = ?",
                (usuario_actual(), servicio),
            ).fetchone()

        return descifrar(fila["token_cifrado"]) if fila else None

    def credenciales_de(self, servicio: str) -> tuple[str, str | None, float | None] | None:
        from src.integraciones.secretos import descifrar

        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT token_cifrado, refresco_cifrado, expira_en FROM integraciones "
                "WHERE user_id = ? AND servicio = ?",
                (usuario_actual(), servicio),
            ).fetchone()
        if fila is None:
            return None
        return (
            descifrar(fila["token_cifrado"]),
            descifrar(fila["refresco_cifrado"]) if fila["refresco_cifrado"] else None,
            fila["expira_en"],
        )

    def actualizar_token(self, servicio: str, token: str, expira_en: float | None) -> None:
        from src.integraciones.secretos import cifrar

        with self.db.connect() as conn:
            conn.execute(
                "UPDATE integraciones SET token_cifrado = ?, expira_en = ?, "
                "actualizado_en = ?, error = NULL WHERE user_id = ? AND servicio = ?",
                (cifrar(token), expira_en, _ahora(), usuario_actual(), servicio),
            )

    def guardar(
        self, servicio: str, token: str, *,
        cuenta: str | None = None, scopes: tuple[str, ...] = (),
        refresco: str | None = None, expira_en: float | None = None,
        metadatos: dict | None = None,
    ) -> Integracion:
        from src.integraciones.secretos import cifrar

        ahora = _ahora()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO integraciones (
                    user_id, servicio, token_cifrado, refresco_cifrado, expira_en,
                    cuenta, scopes, creado_en, actualizado_en, error, metadatos
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(user_id, servicio) DO UPDATE SET
                    token_cifrado = excluded.token_cifrado,
                    refresco_cifrado = excluded.refresco_cifrado,
                    expira_en = excluded.expira_en,
                    cuenta = excluded.cuenta,
                    scopes = excluded.scopes,
                    actualizado_en = excluded.actualizado_en,
                    -- Reconectar limpia el error anterior: si acaba de
                    -- autorizar, lo que fallara antes ya no describe el estado.
                    error = NULL,
                    metadatos = excluded.metadatos
                """,
                (
                    usuario_actual(), servicio, cifrar(token),
                    cifrar(refresco) if refresco else None, expira_en,
                    cuenta, ",".join(scopes), ahora, ahora,
                    json.dumps(metadatos or {}),
                ),
            )
        resultado = self.obtener(servicio)
        assert resultado is not None
        return resultado

    def listar(self) -> list[Integracion]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM integraciones WHERE user_id = ? ORDER BY servicio",
                (usuario_actual(),),
            ).fetchall()
        return [self._a_integracion(f) for f in filas]

    def eliminar(self, servicio: str) -> bool:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM integraciones WHERE user_id = ? AND servicio = ?",
                (usuario_actual(), servicio),
            )
            return cursor.rowcount > 0

    def anotar_error(self, servicio: str, error: str | None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE integraciones SET error = ?, actualizado_en = ? "
                "WHERE user_id = ? AND servicio = ?",
                (error, _ahora(), usuario_actual(), servicio),
            )

    def crear_estado(self, servicio: str) -> str:
        estado = _nuevo_estado()
        ahora = _ahora()
        with self.db.connect() as conn:
            # Se aprovecha para barrer los caducados. Sin esto la tabla solo
            # crece: cada autorizacion abandonada a medias deja una fila.
            conn.execute("DELETE FROM oauth_estados WHERE expira_en < ?", (ahora,))
            conn.execute(
                "INSERT INTO oauth_estados (estado, user_id, servicio, creado_en, expira_en) "
                "VALUES (?, ?, ?, ?, ?)",
                (estado, usuario_actual(), servicio, ahora, ahora + VIDA_DEL_ESTADO),
            )
        return estado

    def consumir_estado(self, estado: str, servicio: str) -> str | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT user_id FROM oauth_estados "
                "WHERE estado = ? AND servicio = ? AND expira_en > ?",
                (estado, servicio, _ahora()),
            ).fetchone()
            if fila is None:
                return None
            conn.execute("DELETE FROM oauth_estados WHERE estado = ?", (estado,))
            return fila["user_id"]


class IntegracionesSupabase(RepositorioDeIntegraciones):
    """Las integraciones en la nube.

    Se escribe a la vez que la de SQLite y con las mismas pruebas. La alternativa
    —dejarla para cuando haga falta— es como se llegó a tener media capa de datos
    sin filtrar por usuario en producción.
    """

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _esc(valor: str) -> str:
        return quote(str(valor), safe="")

    def _mio(self) -> str:
        return f"user_id=eq.{self._esc(usuario_actual())}"

    @staticmethod
    def _a_integracion(fila: dict) -> Integracion:
        crudos = fila.get("metadatos") or {}
        if isinstance(crudos, str):
            try:
                crudos = json.loads(crudos)
            except (ValueError, TypeError):
                crudos = {}

        return Integracion(
            user_id=fila["user_id"],
            servicio=fila["servicio"],
            cuenta=fila.get("cuenta"),
            scopes=tuple(s for s in (fila.get("scopes") or "").split(",") if s),
            creado_en=fila.get("creado_en", 0.0) or 0.0,
            actualizado_en=fila.get("actualizado_en", 0.0) or 0.0,
            error=fila.get("error"),
            metadatos=crudos,
        )

    def obtener(self, servicio: str) -> Integracion | None:
        filas = self.client.select(
            "integraciones",
            f"servicio=eq.{self._esc(servicio)}&{self._mio()}&select=*&limit=1",
        )
        return self._a_integracion(filas[0]) if filas else None

    def token_de(self, servicio: str) -> str | None:
        from src.integraciones.secretos import descifrar

        filas = self.client.select(
            "integraciones",
            f"servicio=eq.{self._esc(servicio)}&{self._mio()}"
            "&select=token_cifrado&limit=1",
        )
        return descifrar(filas[0]["token_cifrado"]) if filas else None

    def credenciales_de(self, servicio: str) -> tuple[str, str | None, float | None] | None:
        from src.integraciones.secretos import descifrar

        filas = self.client.select(
            "integraciones",
            f"servicio=eq.{self._esc(servicio)}&{self._mio()}"
            "&select=token_cifrado,refresco_cifrado,expira_en&limit=1",
        )
        if not filas:
            return None
        fila = filas[0]
        return (
            descifrar(fila["token_cifrado"]),
            descifrar(fila["refresco_cifrado"]) if fila.get("refresco_cifrado") else None,
            fila.get("expira_en"),
        )

    def actualizar_token(self, servicio: str, token: str, expira_en: float | None) -> None:
        from src.integraciones.secretos import cifrar

        self.client.update(
            "integraciones",
            f"servicio=eq.{self._esc(servicio)}&{self._mio()}",
            {
                "token_cifrado": cifrar(token),
                "expira_en": expira_en,
                "actualizado_en": _ahora(),
                "error": None,
            },
        )

    def guardar(
        self, servicio: str, token: str, *,
        cuenta: str | None = None, scopes: tuple[str, ...] = (),
        refresco: str | None = None, expira_en: float | None = None,
        metadatos: dict | None = None,
    ) -> Integracion:
        from src.integraciones.secretos import cifrar

        ahora = _ahora()
        filas = self.client.upsert(
            "integraciones",
            [{
                "user_id": usuario_actual(),
                "servicio": servicio,
                "token_cifrado": cifrar(token),
                "refresco_cifrado": cifrar(refresco) if refresco else None,
                "expira_en": expira_en,
                "cuenta": cuenta,
                "scopes": ",".join(scopes),
                "creado_en": ahora,
                "actualizado_en": ahora,
                "error": None,
                "metadatos": metadatos or {},
            }],
            # La clave primaria es (user_id, servicio). Nombrar solo una columna
            # da «no unique or exclusion constraint matching», que llega al
            # navegador como un 500 sin explicacion. Ya paso con `sessions`.
            on_conflict="user_id,servicio",
        )
        return self._a_integracion(filas[0]) if filas else Integracion(
            user_id=usuario_actual(), servicio=servicio, cuenta=cuenta,
            scopes=scopes, creado_en=ahora, actualizado_en=ahora,
        )

    def listar(self) -> list[Integracion]:
        filas = self.client.select(
            "integraciones", f"{self._mio()}&select=*&order=servicio",
        )
        return [self._a_integracion(f) for f in filas]

    def eliminar(self, servicio: str) -> bool:
        borrados = self.client.delete(
            "integraciones", f"servicio=eq.{self._esc(servicio)}&{self._mio()}",
        )
        return len(borrados) > 0

    def anotar_error(self, servicio: str, error: str | None) -> None:
        self.client.update(
            "integraciones",
            f"servicio=eq.{self._esc(servicio)}&{self._mio()}",
            {"error": error, "actualizado_en": _ahora()},
        )

    def crear_estado(self, servicio: str) -> str:
        estado = _nuevo_estado()
        ahora = _ahora()
        self.client.delete("oauth_estados", f"expira_en=lt.{ahora}")
        self.client.insert("oauth_estados", [{
            "estado": estado,
            "user_id": usuario_actual(),
            "servicio": servicio,
            "creado_en": ahora,
            "expira_en": ahora + VIDA_DEL_ESTADO,
        }])
        return estado

    def consumir_estado(self, estado: str, servicio: str) -> str | None:
        # Aqui NO se filtra por usuario, y es correcto: la vuelta de OAuth llega
        # como una navegacion del navegador, y el estado es precisamente lo que
        # dice de quien era. Filtrar por el usuario de la peticion seria pedirle
        # la respuesta a la pregunta que se esta haciendo.
        filas = self.client.select(
            "oauth_estados",
            f"estado=eq.{self._esc(estado)}&servicio=eq.{self._esc(servicio)}"
            f"&expira_en=gt.{_ahora()}&select=user_id&limit=1",
        )
        if not filas:
            return None

        self.client.delete("oauth_estados", f"estado=eq.{self._esc(estado)}")
        return filas[0]["user_id"]


def repositorio_de_integraciones(factoria) -> RepositorioDeIntegraciones:
    """El repositorio que corresponda al almacén en uso.

    Mismo patrón que las cuentas: se mira si la fábrica trae un cliente de
    Supabase y, si no, se usa su base local.
    """
    client = getattr(factoria, "client", None)
    if client is not None:
        return IntegracionesSupabase(client)
    return IntegracionesSQLite(getattr(factoria, "db", factoria))
