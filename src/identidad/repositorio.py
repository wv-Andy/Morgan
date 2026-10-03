"""
Dónde viven las cuentas: SQLite o Supabase (identidad, V2.0 adelantada).

El servicio de cuentas guarda la política —cómo se hashea, cuándo caduca un
token, cuántos intentos se toleran— y este módulo guarda el almacén. Separarlos
no es ceremonia: en local Morgan habla con un fichero SQLite, y en la nube el
disco es efímero, así que las cuentas tienen que vivir en Supabase o se perderían
en el primer reinicio. Sin esta separación, el sistema de cuentas funcionaría en
el único sitio donde no hace falta y fallaría en el único donde sí.

Es el mismo patrón que ya usan conversaciones, memoria, archivos y tareas.

Las dos implementaciones devuelven **diccionarios sencillos**, no filas de la
base: así el servicio no tiene que saber si detrás hay un `sqlite3.Row` o el JSON
de PostgREST.
"""

import logging
from abc import ABC, abstractmethod
from urllib.parse import quote
from typing import Any

logger = logging.getLogger(__name__)

#: Cuánto se guarda el historial de órdenes a los agentes (decisión mía, 3.7).
DURA_HISTORIAL = 30 * 24 * 3600

CAMPOS_USUARIO = (
    "id, username, email, password_hash, display_name, avatar_url, status, "
    "email_verificado, creado_en, actualizado_en, ultima_actividad"
)


class RepositorioDeCuentas(ABC):
    """Lo que el servicio de cuentas necesita del almacén."""

    # --- Usuarios ---

    @abstractmethod
    def obtener(self, user_id: str) -> dict | None: ...

    @abstractmethod
    def buscar(self, identificador: str) -> dict | None:
        """Por nombre de usuario **o** por correo, indistintamente."""

    @abstractmethod
    def duplicado(self, username: str, email: str) -> dict | None:
        """La cuenta que chocaría con estos datos, si existe."""

    @abstractmethod
    def propietario(self) -> dict | None:
        """La cuenta con rol de propietario, si la hay. Nunca hay dos."""

    @abstractmethod
    def listar_usuarios(self, limite: int = 200) -> list[dict]:
        """Todas las cuentas. Solo lo usa la administración."""

    @abstractmethod
    def crear(self, datos: dict) -> None: ...

    @abstractmethod
    def actualizar(self, user_id: str, valores: dict) -> None: ...

    @abstractmethod
    def eliminar(self, user_id: str) -> bool:
        """Borra la cuenta. Devuelve si existía.

        Sus sesiones, sus tokens de recuperación y sus tokens de API caen con ella —hay clave
        foránea en cascada—, pero **sus datos no**: conversaciones, recuerdos,
        tareas y archivos viven en otras tablas sin relación con esta. Quien
        llame tiene que borrarlos antes; el servicio lo hace.
        """

    # --- Sesiones ---

    @abstractmethod
    def crear_sesion(self, datos: dict) -> None: ...

    @abstractmethod
    def usuario_de_sesion(self, sesion_id: str, ahora: float) -> dict | None:
        """El usuario de una sesión viva, o None. Filtra caducidad y revocación."""

    @abstractmethod
    def tocar_sesion(self, sesion_id: str, ahora: float) -> None: ...

    @abstractmethod
    def revocar_sesion(self, sesion_id: str) -> bool: ...

    @abstractmethod
    def revocar_sesiones(self, user_id: str, excepto: str | None) -> int: ...

    @abstractmethod
    def listar_sesiones(self, user_id: str, ahora: float) -> list[dict]: ...

    # --- Intentos de acceso ---

    @abstractmethod
    def contar_intentos(self, identificador: str, origen: str, desde: float) -> int: ...

    @abstractmethod
    def apuntar_intento(self, identificador: str, origen: str, momento: float) -> None: ...

    @abstractmethod
    def olvidar_intentos(self, identificador: str, origen: str) -> None: ...

    @abstractmethod
    def olvidar_todos_los_intentos(self, identificador: str) -> None:
        """Borra los intentos de `identificador` desde cualquier origen (V2.0.22)."""

    @abstractmethod
    def origenes_de_intentos(self, identificador: str, desde: float) -> list[str]:
        """El origen de cada intento sobre `identificador` desde `desde` (V2.0.22).

        Una sola consulta para los dos frenos: la longitud es el total, venga de
        donde venga, y contar un origen da el suyo. Barre lo anterior a `desde`,
        como `contar_intentos`.
        """

    # --- Recuperación ---

    @abstractmethod
    def crear_reset(self, datos: dict) -> None: ...

    @abstractmethod
    def reset_valido(
        self, token_hash: str, ahora: float, tipo: str = "reset"
    ) -> dict | None:
        """El usuario de un token vivo, sin usar y del tipo pedido.

        **El tipo se filtra siempre.** Sin él, un token de verificación de
        correo serviría para restablecer una contraseña: los dos viajan por el
        mismo canal —un enlace en un correo— y valen lo mismo para quien los
        intercepte, pero conceden cosas muy distintas.
        """

    @abstractmethod
    def marcar_reset_usado(self, token_hash: str, ahora: float) -> None: ...

    @abstractmethod
    def invalidar_resets(
        self, user_id: str, ahora: float, tipo: str = "reset"
    ) -> None:
        """Anula los tokens vivos de ese tipo.

        Con tipo, y no todos: cambiar la contraseña invalida los enlaces de
        recuperación pendientes —que es lo correcto— pero **no** el de
        verificación del correo, que no tiene nada que ver y obligaría a pedirlo
        otra vez sin motivo.
        """

    # --- Tokens personales de API (plan de la API, fase 1) ---

    @abstractmethod
    def crear_token(self, datos: dict) -> None: ...

    @abstractmethod
    def token_por_hash(self, token_hash: str, ahora: float) -> dict | None:
        """El token vivo con ese hash y su usuario, o None.

        Devuelve `{"token": {...}, "usuario": {...}}`. Filtra lo mismo que una
        sesión: revocado, caducado y cuenta que no esté activa. Un token de una
        cuenta suspendida no vale aunque no haya caducado.
        """

    @abstractmethod
    def listar_tokens(self, user_id: str, ahora: float) -> list[dict]:
        """Los tokens vivos de alguien, sin el hash: lo que se le puede enseñar."""

    @abstractmethod
    def revocar_token(self, user_id: str, token_id: str) -> bool:
        """Revoca un token **de esa persona**. Uno de otra no se encuentra."""

    @abstractmethod
    def revocar_tokens(self, user_id: str) -> int: ...

    @abstractmethod
    def tocar_token(self, token_id: str, ahora: float) -> None: ...

    # --- Agentes locales (3.0-C) ---

    @abstractmethod
    def crear_codigo_agente(self, datos: dict) -> None: ...

    @abstractmethod
    def invalidar_codigos_agente(self, user_id: str, ahora: float) -> None:
        """Anula los códigos de emparejamiento pendientes de alguien."""

    @abstractmethod
    def codigo_agente(self, codigo_hash: str, ahora: float) -> dict | None:
        """El código vivo y sin usar, con su usuario: `{"codigo", "usuario"}`, o None.

        Filtra lo mismo que un token: caducado, usado y cuenta que no esté activa.
        """

    @abstractmethod
    def consumir_codigo_agente(self, codigo_hash: str, ahora: float) -> bool:
        """Lo marca usado **solo si seguía vivo**. Devuelve si lo consumió esta llamada.

        Es la única forma de que dos agentes con el mismo código no emparejen los
        dos: el segundo encuentra el código ya usado.
        """

    @abstractmethod
    def crear_agente(self, datos: dict) -> None: ...

    @abstractmethod
    def agente_por_credencial(self, credencial_hash: str) -> dict | None:
        """El agente activo con esa credencial (la actual **o la pendiente** de una rotación,
        3.8) y su usuario activo: `{"agente", "usuario"}`. El agente lleva `por_nueva` si
        casó con la pendiente, y `credencial_desde`: cuándo se estrenó la actual."""

    @abstractmethod
    def preparar_rotacion(self, agent_id: str, actual_hash: str, nueva_hash: str) -> bool:
        """Deja `nueva_hash` pendiente **si** `actual_hash` sigue siendo la actual (3.8)."""

    @abstractmethod
    def promover_credencial(self, agent_id: str, nueva_hash: str, ahora: float) -> bool:
        """La pendiente pasa a ser la actual; la vieja deja de valer (3.8)."""

    @abstractmethod
    def listar_agentes(self, user_id: str) -> list[dict]:
        """Los agentes activos de alguien, sin el hash de la credencial."""

    @abstractmethod
    def revocar_agente(self, user_id: str, agent_id: str) -> bool:
        """Revoca un agente **de esa persona**. Uno de otra no se encuentra."""

    @abstractmethod
    def revocar_agentes(self, user_id: str) -> int: ...

    @abstractmethod
    def tocar_agente(self, agent_id: str, valores: dict) -> None:
        """Anota `last_seen` y lo que diga el saludo (versión, sistema)."""

    # --- El historial de órdenes (3.7) ---

    @abstractmethod
    def anotar_orden(self, datos: dict) -> None:
        """Una orden a un agente: `command_id`, persona, PC, capacidad, estado, ms, hora.
        Nunca argumentos ni contenido."""

    @abstractmethod
    def ordenes_de_agente(self, user_id: str, agent_id: str, desde: float, limite: int) -> list[dict]:
        """Las de **ese** PC **de esa persona**, de la más nueva a la más vieja."""

    # --- Mantenimiento ---

    @abstractmethod
    def limpiar(self, ahora: float) -> int:
        """Borra sesiones muertas y tokens caducados. Devuelve cuántas filas."""


class CuentasSQLite(RepositorioDeCuentas):
    """Las cuentas en el fichero local. Es el almacén del Morgan de escritorio."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _fila(fila) -> dict | None:
        return dict(fila) if fila is not None else None

    # --- Usuarios ---

    def obtener(self, user_id: str) -> dict | None:
        with self.db.connect() as conn:
            return self._fila(
                conn.execute(
                    "SELECT * FROM morgan_users WHERE id = ?", (user_id,)
                ).fetchone()
            )

    def buscar(self, identificador: str) -> dict | None:
        with self.db.connect() as conn:
            return self._fila(
                conn.execute(
                    "SELECT * FROM morgan_users WHERE username = ? OR email = ?",
                    (identificador, identificador),
                ).fetchone()
            )

    def propietario(self) -> dict | None:
        with self.db.connect() as conn:
            return self._fila(
                conn.execute(
                    "SELECT * FROM morgan_users WHERE role = 'owner' LIMIT 1"
                ).fetchone()
            )

    def listar_usuarios(self, limite: int = 200) -> list[dict]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM morgan_users WHERE id != 'local' "
                "ORDER BY creado_en DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(f) for f in filas]

    def duplicado(self, username: str, email: str) -> dict | None:
        with self.db.connect() as conn:
            return self._fila(
                conn.execute(
                    "SELECT username, email FROM morgan_users "
                    "WHERE username = ? OR email = ?",
                    (username, email),
                ).fetchone()
            )

    def crear(self, datos: dict) -> None:
        columnas = ", ".join(datos)
        huecos = ", ".join("?" for _ in datos)
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO morgan_users ({columnas}) VALUES ({huecos})",
                tuple(datos.values()),
            )

    def actualizar(self, user_id: str, valores: dict) -> None:
        asignaciones = ", ".join(f"{c} = ?" for c in valores)
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE morgan_users SET {asignaciones} WHERE id = ?",
                (*valores.values(), user_id),
            )

    def eliminar(self, user_id: str) -> bool:
        with self.db.connect() as conn:
            # Se borran a mano en lugar de confiar en la cascada: el esquema de
            # SQLite se construyo por migraciones sucesivas y no todas las
            # tablas la declaran. Borrar de mas aqui no rompe nada; borrar de
            # menos deja sesiones vivas de una cuenta que ya no existe.
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM agente_codigos WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM agentes WHERE user_id = ?", (user_id,))
            # Las automatizaciones y su bandeja (4.14): una cuenta borrada no sigue
            # ejecutando órdenes a su nombre.
            conn.execute("DELETE FROM automatizaciones WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM avisos WHERE user_id = ?", (user_id,))
            cursor = conn.execute("DELETE FROM morgan_users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0

    # --- Sesiones ---

    def crear_sesion(self, datos: dict) -> None:
        columnas = ", ".join(datos)
        huecos = ", ".join("?" for _ in datos)
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO auth_sessions ({columnas}) VALUES ({huecos})",
                tuple(datos.values()),
            )

    def usuario_de_sesion(self, sesion_id: str, ahora: float) -> dict | None:
        with self.db.connect() as conn:
            return self._fila(
                conn.execute(
                    "SELECT u.* FROM auth_sessions s "
                    "JOIN morgan_users u ON u.id = s.user_id "
                    "WHERE s.id = ? AND s.revocada = 0 AND s.expira_en > ? "
                    "AND u.status = 'activo'",
                    (sesion_id, ahora),
                ).fetchone()
            )

    def tocar_sesion(self, sesion_id: str, ahora: float) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE auth_sessions SET ultimo_uso = ? WHERE id = ?", (ahora, sesion_id)
            )

    def revocar_sesion(self, sesion_id: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE auth_sessions SET revocada = 1 WHERE id = ?", (sesion_id,)
            ).rowcount > 0

    def revocar_sesiones(self, user_id: str, excepto: str | None) -> int:
        with self.db.connect() as conn:
            if excepto:
                return conn.execute(
                    "UPDATE auth_sessions SET revocada = 1 "
                    "WHERE user_id = ? AND id != ? AND revocada = 0",
                    (user_id, excepto),
                ).rowcount
            return conn.execute(
                "UPDATE auth_sessions SET revocada = 1 "
                "WHERE user_id = ? AND revocada = 0",
                (user_id,),
            ).rowcount

    def listar_sesiones(self, user_id: str, ahora: float) -> list[dict]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT creado_en, ultimo_uso, user_agent FROM auth_sessions "
                "WHERE user_id = ? AND revocada = 0 AND expira_en > ? "
                "ORDER BY ultimo_uso DESC",
                (user_id, ahora),
            ).fetchall()
        return [dict(f) for f in filas]

    # --- Intentos ---

    def contar_intentos(self, identificador: str, origen: str, desde: float) -> int:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM login_intentos WHERE momento < ?", (desde,))
            fila = conn.execute(
                "SELECT COUNT(*) AS n FROM login_intentos "
                "WHERE identificador = ? AND origen = ? AND momento >= ?",
                (identificador, origen, desde),
            ).fetchone()
        return int(fila["n"]) if fila else 0

    def apuntar_intento(self, identificador: str, origen: str, momento: float) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO login_intentos (identificador, origen, momento) "
                "VALUES (?, ?, ?)",
                (identificador, origen, momento),
            )

    def olvidar_intentos(self, identificador: str, origen: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM login_intentos WHERE identificador = ? AND origen = ?",
                (identificador, origen),
            )

    def olvidar_todos_los_intentos(self, identificador: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM login_intentos WHERE identificador = ?", (identificador,))

    def origenes_de_intentos(self, identificador: str, desde: float) -> list[str]:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM login_intentos WHERE momento < ?", (desde,))
            filas = conn.execute(
                "SELECT origen FROM login_intentos WHERE identificador = ? AND momento >= ?",
                (identificador, desde),
            ).fetchall()
        return [f["origen"] for f in filas]

    # --- Recuperación ---

    def crear_reset(self, datos: dict) -> None:
        columnas = ", ".join(datos)
        huecos = ", ".join("?" for _ in datos)
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO password_reset_tokens ({columnas}) VALUES ({huecos})",
                tuple(datos.values()),
            )

    def reset_valido(
        self, token_hash: str, ahora: float, tipo: str = "reset"
    ) -> dict | None:
        with self.db.connect() as conn:
            return self._fila(
                conn.execute(
                    "SELECT user_id FROM password_reset_tokens "
                    "WHERE token_hash = ? AND tipo = ? "
                    "AND usado_en IS NULL AND expira_en > ?",
                    (token_hash, tipo, ahora),
                ).fetchone()
            )

    def marcar_reset_usado(self, token_hash: str, ahora: float) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE password_reset_tokens SET usado_en = ? WHERE token_hash = ?",
                (ahora, token_hash),
            )

    def invalidar_resets(
        self, user_id: str, ahora: float, tipo: str = "reset"
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE password_reset_tokens SET usado_en = ? "
                "WHERE user_id = ? AND tipo = ? AND usado_en IS NULL",
                (ahora, user_id, tipo),
            )

    # --- Tokens personales de API ---

    def crear_token(self, datos: dict) -> None:
        columnas = ", ".join(datos)
        huecos = ", ".join("?" for _ in datos)
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO api_tokens ({columnas}) VALUES ({huecos})",
                tuple(datos.values()),
            )

    def token_por_hash(self, token_hash: str, ahora: float) -> dict | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT t.id AS token_id, t.nombre AS token_nombre, "
                "t.alcances AS token_alcances, t.caduca_en AS token_caduca_en, u.* "
                "FROM api_tokens t JOIN morgan_users u ON u.id = t.user_id "
                "WHERE t.token_hash = ? AND t.revocado = 0 AND t.caduca_en > ? "
                "AND u.status = 'activo'",
                (token_hash, ahora),
            ).fetchone()
        if fila is None:
            return None
        usuario = dict(fila)
        token = {
            "id": usuario.pop("token_id"),
            "nombre": usuario.pop("token_nombre"),
            "alcances": usuario.pop("token_alcances"),
            "caduca_en": usuario.pop("token_caduca_en"),
        }
        return {"token": token, "usuario": usuario}

    def listar_tokens(self, user_id: str, ahora: float) -> list[dict]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT id, nombre, alcances, creado_en, caduca_en, ultimo_uso "
                "FROM api_tokens WHERE user_id = ? AND revocado = 0 AND caduca_en > ? "
                "ORDER BY creado_en DESC",
                (user_id, ahora),
            ).fetchall()
        return [dict(f) for f in filas]

    def revocar_token(self, user_id: str, token_id: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE api_tokens SET revocado = 1 "
                "WHERE id = ? AND user_id = ? AND revocado = 0",
                (token_id, user_id),
            ).rowcount > 0

    def revocar_tokens(self, user_id: str) -> int:
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE api_tokens SET revocado = 1 WHERE user_id = ? AND revocado = 0",
                (user_id,),
            ).rowcount

    def tocar_token(self, token_id: str, ahora: float) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE api_tokens SET ultimo_uso = ? WHERE id = ?", (ahora, token_id)
            )

    # --- Agentes locales ---

    #: Lo que se enseña de un agente. Nunca el hash de su credencial.
    COLUMNAS_AGENTE = (
        "id, nombre, sistema, agent_version, protocol_version, creado_en, last_seen, "
        "credencial_rotada_en"
    )

    def crear_codigo_agente(self, datos: dict) -> None:
        columnas = ", ".join(datos)
        huecos = ", ".join("?" for _ in datos)
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO agente_codigos ({columnas}) VALUES ({huecos})",
                tuple(datos.values()),
            )

    def invalidar_codigos_agente(self, user_id: str, ahora: float) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE agente_codigos SET usado_en = ? WHERE user_id = ? AND usado_en IS NULL",
                (ahora, user_id),
            )

    def codigo_agente(self, codigo_hash: str, ahora: float) -> dict | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT c.caduca_en AS codigo_caduca_en, u.* FROM agente_codigos c "
                "JOIN morgan_users u ON u.id = c.user_id "
                "WHERE c.codigo_hash = ? AND c.usado_en IS NULL AND c.caduca_en > ? "
                "AND u.status = 'activo'",
                (codigo_hash, ahora),
            ).fetchone()
        if fila is None:
            return None
        usuario = dict(fila)
        return {"codigo": {"caduca_en": usuario.pop("codigo_caduca_en")}, "usuario": usuario}

    def consumir_codigo_agente(self, codigo_hash: str, ahora: float) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE agente_codigos SET usado_en = ? "
                "WHERE codigo_hash = ? AND usado_en IS NULL AND caduca_en > ?",
                (ahora, codigo_hash, ahora),
            ).rowcount > 0

    def crear_agente(self, datos: dict) -> None:
        columnas = ", ".join(datos)
        huecos = ", ".join("?" for _ in datos)
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO agentes ({columnas}) VALUES ({huecos})", tuple(datos.values())
            )

    def agente_por_credencial(self, credencial_hash: str) -> dict | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT a.id AS agente_id, a.nombre AS agente_nombre, "
                "a.protocol_version AS agente_protocolo, "
                "a.credencial_nueva_hash AS agente_nueva, "
                "COALESCE(a.credencial_rotada_en, a.creado_en) AS agente_desde, u.* "
                "FROM agentes a JOIN morgan_users u ON u.id = a.user_id "
                "WHERE (a.credencial_hash = ? OR a.credencial_nueva_hash = ?) "
                "AND a.estado = 'activo' AND u.status = 'activo'",
                (credencial_hash, credencial_hash),
            ).fetchone()
        if fila is None:
            return None
        usuario = dict(fila)
        agente = {
            "id": usuario.pop("agente_id"),
            "nombre": usuario.pop("agente_nombre"),
            "protocol_version": usuario.pop("agente_protocolo"),
            "por_nueva": usuario.pop("agente_nueva") == credencial_hash,
            "credencial_desde": usuario.pop("agente_desde"),
        }
        return {"agente": agente, "usuario": usuario}

    def preparar_rotacion(self, agent_id: str, actual_hash: str, nueva_hash: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE agentes SET credencial_nueva_hash = ? "
                "WHERE id = ? AND credencial_hash = ? AND estado = 'activo'",
                (nueva_hash, agent_id, actual_hash),
            ).rowcount > 0

    def promover_credencial(self, agent_id: str, nueva_hash: str, ahora: float) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE agentes SET credencial_hash = credencial_nueva_hash, "
                "credencial_nueva_hash = NULL, credencial_rotada_en = ? "
                "WHERE id = ? AND credencial_nueva_hash = ? AND estado = 'activo'",
                (ahora, agent_id, nueva_hash),
            ).rowcount > 0

    def listar_agentes(self, user_id: str) -> list[dict]:
        with self.db.connect() as conn:
            filas = conn.execute(
                f"SELECT {self.COLUMNAS_AGENTE} FROM agentes "
                "WHERE user_id = ? AND estado = 'activo' ORDER BY creado_en DESC",
                (user_id,),
            ).fetchall()
        return [dict(f) for f in filas]

    def revocar_agente(self, user_id: str, agent_id: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE agentes SET estado = 'revocado' "
                "WHERE id = ? AND user_id = ? AND estado = 'activo'",
                (agent_id, user_id),
            ).rowcount > 0

    def revocar_agentes(self, user_id: str) -> int:
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE agentes SET estado = 'revocado' WHERE user_id = ? AND estado = 'activo'",
                (user_id,),
            ).rowcount

    def tocar_agente(self, agent_id: str, valores: dict) -> None:
        if not valores:
            return
        asignaciones = ", ".join(f"{columna} = ?" for columna in valores)
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE agentes SET {asignaciones} WHERE id = ?",
                (*valores.values(), agent_id),
            )

    COLUMNAS_ORDEN = ("command_id", "user_id", "agent_id", "request_id", "capability",
                      "estado", "ms", "creado_en")

    def anotar_orden(self, datos: dict) -> None:
        fila = tuple(datos.get(c) for c in self.COLUMNAS_ORDEN)
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO ordenes_agente ({', '.join(self.COLUMNAS_ORDEN)}) "
                f"VALUES ({', '.join('?' for _ in self.COLUMNAS_ORDEN)})", fila,
            )

    def ordenes_de_agente(self, user_id: str, agent_id: str, desde: float, limite: int) -> list[dict]:
        with self.db.connect() as conn:
            filas = conn.execute(
                f"SELECT {', '.join(self.COLUMNAS_ORDEN)} FROM ordenes_agente "
                "WHERE user_id = ? AND agent_id = ? AND creado_en >= ? "
                "ORDER BY creado_en DESC LIMIT ?",
                (user_id, agent_id, desde, limite),
            ).fetchall()
        return [dict(f) for f in filas]

    # --- Mantenimiento ---

    def limpiar(self, ahora: float) -> int:
        with self.db.connect() as conn:
            n = conn.execute(
                "DELETE FROM auth_sessions WHERE expira_en < ? OR revocada = 1",
                (ahora,),
            ).rowcount
            n += conn.execute(
                "DELETE FROM password_reset_tokens WHERE expira_en < ?", (ahora,)
            ).rowcount
            n += conn.execute(
                "DELETE FROM api_tokens WHERE caduca_en < ? OR revocado = 1", (ahora,)
            ).rowcount
            n += conn.execute(
                "DELETE FROM agente_codigos WHERE caduca_en < ? OR usado_en IS NOT NULL",
                (ahora,),
            ).rowcount
            n += conn.execute("DELETE FROM agentes WHERE estado = 'revocado'").rowcount
            n += conn.execute(
                "DELETE FROM ordenes_agente WHERE creado_en < ?", (ahora - DURA_HISTORIAL,)
            ).rowcount
        return n


class CuentasSupabase(RepositorioDeCuentas):
    """Las cuentas en Postgres, a través de PostgREST.

    Es el almacén del Morgan de la nube, donde el disco no sobrevive a un
    reinicio. Se habla con la **clave de servicio**, que salta las políticas RLS:
    estas tres tablas tienen RLS activo y ninguna política, así que nadie más
    puede leerlas ni con la clave publicable en la mano.
    """

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _uno(filas: list[dict]) -> dict | None:
        return filas[0] if filas else None

    @staticmethod
    def _cita(valor: str) -> str:
        """Prepara un valor para un filtro de PostgREST.

        **Codificación de URL, no comillas.** La primera versión envolvía el
        valor entre comillas dobles creyendo que así escapaba comas y
        paréntesis. No es así: en un filtro `eq.`, PostgREST toma las comillas
        como **parte del valor**, así que `eq."abc"` busca literalmente `"abc"`
        —con comillas— y no encuentra nada.

        El efecto fue que **todo el sistema de cuentas dejó de funcionar en la
        nube**: login, registro, sesiones y recuperación de contraseña. Todos
        devolvían «no existe» sobre filas que estaban ahí. En local no se notaba
        porque allí el almacén es SQLite, y las pruebas usaban ese.

        Las comillas dobles sí se usan en PostgREST, pero dentro de listas
        —`in.("a","b")`— no en una comparación simple.
        """
        return quote(str(valor), safe="")

    # --- Usuarios ---

    def obtener(self, user_id: str) -> dict | None:
        return self._uno(
            self.client.select("morgan_users", f"id=eq.{self._cita(user_id)}&limit=1")
        )

    def buscar(self, identificador: str) -> dict | None:
        v = self._cita(identificador)
        return self._uno(
            self.client.select("morgan_users", f"or=(username.eq.{v},email.eq.{v})&limit=1")
        )

    def propietario(self) -> dict | None:
        return self._uno(
            self.client.select("morgan_users", "role=eq.owner&limit=1")
        )

    def listar_usuarios(self, limite: int = 200) -> list[dict]:
        return self.client.select(
            "morgan_users",
            f"id=neq.local&order=creado_en.desc&limit={int(limite)}",
        )

    def duplicado(self, username: str, email: str) -> dict | None:
        u, e = self._cita(username), self._cita(email)
        return self._uno(
            self.client.select(
                "morgan_users",
                f"select=username,email&or=(username.eq.{u},email.eq.{e})&limit=1",
            )
        )

    def crear(self, datos: dict) -> None:
        self.client.insert("morgan_users", [datos])

    def actualizar(self, user_id: str, valores: dict) -> None:
        self.client.update("morgan_users", f"id=eq.{self._cita(user_id)}", valores)

    def eliminar(self, user_id: str) -> bool:
        # Aqui la cascada si esta declarada (auth_sessions y
        # password_reset_tokens referencian morgan_users con ON DELETE CASCADE),
        # pero se borran igual antes: que las dos implementaciones hagan lo
        # mismo vale mas que ahorrarse dos peticiones en una operacion que se
        # ejecuta una vez en la vida de una cuenta.
        self.client.delete("auth_sessions", f"user_id=eq.{self._cita(user_id)}")
        self.client.delete("password_reset_tokens", f"user_id=eq.{self._cita(user_id)}")
        self.client.delete("api_tokens", f"user_id=eq.{self._cita(user_id)}")
        self.client.delete("agente_codigos", f"user_id=eq.{self._cita(user_id)}")
        self.client.delete("agentes", f"user_id=eq.{self._cita(user_id)}")
        borrados = self.client.delete("morgan_users", f"id=eq.{self._cita(user_id)}")
        return len(borrados) > 0

    # --- Sesiones ---

    def crear_sesion(self, datos: dict) -> None:
        # 'revocada' es booleano en Postgres e INTEGER en SQLite.
        datos = {**datos, "revocada": bool(datos.get("revocada", 0))}
        self.client.insert("auth_sessions", [datos])

    def usuario_de_sesion(self, sesion_id: str, ahora: float) -> dict | None:
        # El usuario viaja embebido: con dos consultas, cada peticion
        # autenticada pagaria dos viajes de red a Supabase.
        filas = self.client.select(
            "auth_sessions",
            f"id=eq.{self._cita(sesion_id)}&revocada=is.false&expira_en=gt.{ahora}"
            "&select=morgan_users(*)&limit=1",
        )
        usuario = self._uno(filas)
        usuario = (usuario or {}).get("morgan_users")

        # El JOIN de SQLite filtra por estado; aqui se comprueba a mano para que
        # las dos implementaciones se comporten igual.
        if not usuario or usuario.get("status") != "activo":
            return None
        return usuario

    def tocar_sesion(self, sesion_id: str, ahora: float) -> None:
        self.client.update(
            "auth_sessions", f"id=eq.{self._cita(sesion_id)}", {"ultimo_uso": ahora}
        )

    def revocar_sesion(self, sesion_id: str) -> bool:
        return bool(
            self.client.update(
                "auth_sessions", f"id=eq.{self._cita(sesion_id)}", {"revocada": True}
            )
        )

    def revocar_sesiones(self, user_id: str, excepto: str | None) -> int:
        filtro = f"user_id=eq.{self._cita(user_id)}&revocada=is.false"
        if excepto:
            filtro += f"&id=neq.{self._cita(excepto)}"
        return len(self.client.update("auth_sessions", filtro, {"revocada": True}))

    def listar_sesiones(self, user_id: str, ahora: float) -> list[dict]:
        return self.client.select(
            "auth_sessions",
            f"user_id=eq.{self._cita(user_id)}&revocada=is.false&expira_en=gt.{ahora}"
            "&select=creado_en,ultimo_uso,user_agent&order=ultimo_uso.desc",
        )

    # --- Intentos ---

    def contar_intentos(self, identificador: str, origen: str, desde: float) -> int:
        self.client.delete("login_intentos", f"momento=lt.{desde}")
        return len(
            self.client.select(
                "login_intentos",
                f"identificador=eq.{self._cita(identificador)}"
                f"&origen=eq.{self._cita(origen)}&momento=gte.{desde}&select=momento",
            )
        )

    def apuntar_intento(self, identificador: str, origen: str, momento: float) -> None:
        self.client.insert(
            "login_intentos",
            [{"identificador": identificador, "origen": origen, "momento": momento}],
        )

    def olvidar_intentos(self, identificador: str, origen: str) -> None:
        self.client.delete(
            "login_intentos",
            f"identificador=eq.{self._cita(identificador)}&origen=eq.{self._cita(origen)}",
        )

    def olvidar_todos_los_intentos(self, identificador: str) -> None:
        self.client.delete("login_intentos", f"identificador=eq.{self._cita(identificador)}")

    def origenes_de_intentos(self, identificador: str, desde: float) -> list[str]:
        self.client.delete("login_intentos", f"momento=lt.{desde}")
        filas = self.client.select(
            "login_intentos",
            f"identificador=eq.{self._cita(identificador)}&momento=gte.{desde}&select=origen",
        )
        return [f.get("origen") for f in filas]

    # --- Recuperación ---

    def crear_reset(self, datos: dict) -> None:
        self.client.insert("password_reset_tokens", [datos])

    def reset_valido(
        self, token_hash: str, ahora: float, tipo: str = "reset"
    ) -> dict | None:
        return self._uno(
            self.client.select(
                "password_reset_tokens",
                f"token_hash=eq.{self._cita(token_hash)}"
                f"&tipo=eq.{self._cita(tipo)}&usado_en=is.null"
                f"&expira_en=gt.{ahora}&select=user_id&limit=1",
            )
        )

    def marcar_reset_usado(self, token_hash: str, ahora: float) -> None:
        self.client.update(
            "password_reset_tokens",
            f"token_hash=eq.{self._cita(token_hash)}",
            {"usado_en": ahora},
        )

    def invalidar_resets(
        self, user_id: str, ahora: float, tipo: str = "reset"
    ) -> None:
        self.client.update(
            "password_reset_tokens",
            f"user_id=eq.{self._cita(user_id)}"
            f"&tipo=eq.{self._cita(tipo)}&usado_en=is.null",
            {"usado_en": ahora},
        )

    # --- Tokens personales de API ---

    #: Lo que se enseña de un token. Nunca el hash.
    COLUMNAS_TOKEN = "id,nombre,alcances,creado_en,caduca_en,ultimo_uso"

    def crear_token(self, datos: dict) -> None:
        # 'revocado' es booleano en Postgres e INTEGER en SQLite.
        datos = {**datos, "revocado": bool(datos.get("revocado", 0))}
        self.client.insert("api_tokens", [datos])

    def token_por_hash(self, token_hash: str, ahora: float) -> dict | None:
        # El usuario viaja embebido, como en las sesiones: un viaje y no dos.
        fila = self._uno(
            self.client.select(
                "api_tokens",
                f"token_hash=eq.{self._cita(token_hash)}&revocado=is.false"
                f"&caduca_en=gt.{ahora}"
                "&select=id,nombre,alcances,caduca_en,morgan_users(*)&limit=1",
            )
        )
        if not fila:
            return None
        usuario = fila.pop("morgan_users", None)
        if not usuario or usuario.get("status") != "activo":
            return None
        return {"token": fila, "usuario": usuario}

    def listar_tokens(self, user_id: str, ahora: float) -> list[dict]:
        return self.client.select(
            "api_tokens",
            f"user_id=eq.{self._cita(user_id)}&revocado=is.false&caduca_en=gt.{ahora}"
            f"&select={self.COLUMNAS_TOKEN}&order=creado_en.desc",
        )

    def revocar_token(self, user_id: str, token_id: str) -> bool:
        return bool(
            self.client.update(
                "api_tokens",
                f"id=eq.{self._cita(token_id)}&user_id=eq.{self._cita(user_id)}"
                "&revocado=is.false",
                {"revocado": True},
            )
        )

    def revocar_tokens(self, user_id: str) -> int:
        return len(
            self.client.update(
                "api_tokens",
                f"user_id=eq.{self._cita(user_id)}&revocado=is.false",
                {"revocado": True},
            )
        )

    def tocar_token(self, token_id: str, ahora: float) -> None:
        self.client.update(
            "api_tokens", f"id=eq.{self._cita(token_id)}", {"ultimo_uso": ahora}
        )

    # --- Agentes locales ---

    #: Lo que se enseña de un agente. Nunca el hash de su credencial.
    COLUMNAS_AGENTE = "id,nombre,sistema,agent_version,protocol_version,creado_en,last_seen,credencial_rotada_en"

    def crear_codigo_agente(self, datos: dict) -> None:
        self.client.insert("agente_codigos", [datos])

    def invalidar_codigos_agente(self, user_id: str, ahora: float) -> None:
        self.client.update(
            "agente_codigos",
            f"user_id=eq.{self._cita(user_id)}&usado_en=is.null",
            {"usado_en": ahora},
        )

    def codigo_agente(self, codigo_hash: str, ahora: float) -> dict | None:
        fila = self._uno(
            self.client.select(
                "agente_codigos",
                f"codigo_hash=eq.{self._cita(codigo_hash)}&usado_en=is.null"
                f"&caduca_en=gt.{ahora}&select=caduca_en,morgan_users(*)&limit=1",
            )
        )
        if not fila:
            return None
        usuario = fila.pop("morgan_users", None)
        if not usuario or usuario.get("status") != "activo":
            return None
        return {"codigo": fila, "usuario": usuario}

    def consumir_codigo_agente(self, codigo_hash: str, ahora: float) -> bool:
        # Un UPDATE con el filtro de «vivo»: PostgREST devuelve las filas que
        # cambió, así que dos llamadas a la vez no pueden consumirlo las dos.
        return bool(
            self.client.update(
                "agente_codigos",
                f"codigo_hash=eq.{self._cita(codigo_hash)}&usado_en=is.null&caduca_en=gt.{ahora}",
                {"usado_en": ahora},
            )
        )

    def crear_agente(self, datos: dict) -> None:
        self.client.insert("agentes", [datos])

    def agente_por_credencial(self, credencial_hash: str) -> dict | None:
        cita = self._cita(credencial_hash)
        fila = self._uno(
            self.client.select(
                "agentes",
                f"or=(credencial_hash.eq.{cita},credencial_nueva_hash.eq.{cita})&estado=eq.activo"
                "&select=id,nombre,protocol_version,creado_en,credencial_rotada_en,"
                "credencial_nueva_hash,morgan_users(*)&limit=1",
            )
        )
        if not fila:
            return None
        usuario = fila.pop("morgan_users", None)
        if not usuario or usuario.get("status") != "activo":
            return None
        fila["por_nueva"] = fila.pop("credencial_nueva_hash", None) == credencial_hash
        fila["credencial_desde"] = fila.pop("credencial_rotada_en", None) or fila.pop("creado_en", None)
        fila.pop("creado_en", None)
        return {"agente": fila, "usuario": usuario}

    def preparar_rotacion(self, agent_id: str, actual_hash: str, nueva_hash: str) -> bool:
        return bool(
            self.client.update(
                "agentes",
                f"id=eq.{self._cita(agent_id)}&credencial_hash=eq.{self._cita(actual_hash)}&estado=eq.activo",
                {"credencial_nueva_hash": nueva_hash},
            )
        )

    def promover_credencial(self, agent_id: str, nueva_hash: str, ahora: float) -> bool:
        return bool(
            self.client.update(
                "agentes",
                f"id=eq.{self._cita(agent_id)}&credencial_nueva_hash=eq.{self._cita(nueva_hash)}&estado=eq.activo",
                {"credencial_hash": nueva_hash, "credencial_nueva_hash": None, "credencial_rotada_en": ahora},
            )
        )

    def listar_agentes(self, user_id: str) -> list[dict]:
        return self.client.select(
            "agentes",
            f"user_id=eq.{self._cita(user_id)}&estado=eq.activo"
            f"&select={self.COLUMNAS_AGENTE}&order=creado_en.desc",
        )

    def revocar_agente(self, user_id: str, agent_id: str) -> bool:
        return bool(
            self.client.update(
                "agentes",
                f"id=eq.{self._cita(agent_id)}&user_id=eq.{self._cita(user_id)}&estado=eq.activo",
                {"estado": "revocado"},
            )
        )

    def revocar_agentes(self, user_id: str) -> int:
        return len(
            self.client.update(
                "agentes",
                f"user_id=eq.{self._cita(user_id)}&estado=eq.activo",
                {"estado": "revocado"},
            )
        )

    def tocar_agente(self, agent_id: str, valores: dict) -> None:
        if valores:
            self.client.update("agentes", f"id=eq.{self._cita(agent_id)}", valores)

    def anotar_orden(self, datos: dict) -> None:
        self.client.insert("ordenes_agente", [{k: datos.get(k) for k in CuentasSQLite.COLUMNAS_ORDEN}])

    def ordenes_de_agente(self, user_id: str, agent_id: str, desde: float, limite: int) -> list[dict]:
        return self.client.select(
            "ordenes_agente",
            f"user_id=eq.{self._cita(user_id)}&agent_id=eq.{self._cita(agent_id)}"
            f"&creado_en=gte.{desde}&order=creado_en.desc&limit={int(limite)}",
        )

    # --- Mantenimiento ---

    def limpiar(self, ahora: float) -> int:
        n = len(
            self.client.delete("auth_sessions", f"or=(expira_en.lt.{ahora},revocada.is.true)")
        )
        n += len(self.client.delete("password_reset_tokens", f"expira_en=lt.{ahora}"))
        n += len(
            self.client.delete("api_tokens", f"or=(caduca_en.lt.{ahora},revocado.is.true)")
        )
        n += len(
            self.client.delete(
                "agente_codigos", f"or=(caduca_en.lt.{ahora},usado_en.not.is.null)"
            )
        )
        n += len(self.client.delete("agentes", "estado=eq.revocado"))
        # Aparte, y sin tumbar lo de arriba: si la tabla todavía no existe (el código
        # llegó antes que la migración v25), la limpieza de sesiones y tokens sigue.
        try:
            n += len(self.client.delete("ordenes_agente", f"creado_en=lt.{ahora - DURA_HISTORIAL}"))
        except Exception:
            logger.warning("No se pudo limpiar el historial de órdenes (¿falta la v25?)", exc_info=True)
        return n


def repositorio_de_cuentas(repositories: Any) -> RepositorioDeCuentas:
    """Elige el almacén de cuentas que corresponde a los repositorios en uso.

    Lanza si no puede decidir, en lugar de devolver `None`. Es deliberado: quien
    llama estaría tentado de tratar el `None` como «sigue sin autenticar», y un
    fallo de la capa de cuentas que abre la puerta es mucho peor que uno que la
    cierra.
    """
    db = getattr(repositories, "db", None)
    if db is not None:
        return CuentasSQLite(db)

    client = getattr(repositories, "client", None)
    if client is not None:
        return CuentasSupabase(client)

    raise RuntimeError(
        "Los repositorios en uso no ofrecen ni base local ni cliente de Supabase: "
        "no hay dónde guardar las cuentas."
    )
