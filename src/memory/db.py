"""
Capa de acceso a SQLite para Morgan.

Concentra aquí todo lo que la implementación anterior daba por supuesto:

- **Cierre real de conexiones**: `with sqlite3.connect(...)` hace commit o rollback,
  pero *no cierra*. Cada operación dejaba una conexión viva hasta que el recolector
  de basura la liberaba.
- **Concurrencia**: la API es multihilo. Se activa WAL (lectores y escritor
  simultáneos) y un `timeout` de espera antes de fallar por bloqueo.
- **Errores controlados**: un problema de disco, un fichero bloqueado o una base
  corrupta no deben propagarse como excepción cruda hasta una herramienta o un
  endpoint. Se traducen a `MemoryStorageError`.
- **Migraciones**: una tabla `schema_version` permite evolucionar el esquema
  (tareas, configuración, historial) sin romper las bases ya existentes.
"""

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from src.config import get_settings

logger = logging.getLogger(__name__)

# Espera máxima ante un bloqueo de escritura antes de dar error.
DEFAULT_TIMEOUT_SECONDS = None  # se resuelve desde la configuración al construir


class MemoryStorageError(RuntimeError):
    """Error controlado de la capa de persistencia."""


# --- Migraciones ------------------------------------------------------------
#
# Cada entrada es (versión, sentencias). Para evolucionar el esquema se añade una
# versión nueva al final; nunca se edita una ya publicada, porque las bases
# existentes sólo aplican las versiones que les faltan.

MIGRATIONS: list[tuple[int, tuple[str, ...]]] = [
    (
        1,
        (
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)",
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'general',
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_mem_category ON memories(category)",
            "CREATE INDEX IF NOT EXISTS idx_mem_key ON memories(key)",
        ),
    ),
    (
        2,
        (
            # Acelera el listado por recencia, que es el acceso más frecuente.
            "CREATE INDEX IF NOT EXISTS idx_mem_updated ON memories(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_conv_created ON conversations(created_at)",
        ),
    ),
    (
        3,
        (
            # --- Sesiones y mensajes (V1.2) ---
            #
            # La tabla 'conversations' de la version 1 nunca llego a usarse (0 filas,
            # sin ningun consumidor) y le faltaban la relacion con una sesion y el
            # campo de metadatos. Se sustituye por 'sessions' + 'messages'.
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)",
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tool_name TEXT,
                tool_call_id TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """,
            # Orden dentro de la sesion: es el acceso dominante al recuperar historial.
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)",
            "DROP TABLE IF EXISTS conversations",
        ),
    ),
    (
        4,
        (
            # --- Cola de sincronización local -> nube (V1.3) ---
            #
            # Modelo outbox: la escritura local nunca espera al remoto. Si la nube
            # no responde, la operación queda aquí y se sube cuando vuelva.
            """
            CREATE TABLE IF NOT EXISTS sync_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMP NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
            """,
            # El acceso dominante es "las pendientes en orden de llegada".
            "CREATE INDEX IF NOT EXISTS idx_sync_pending ON sync_queue(attempts, id)",
        ),
    ),
    (
        5,
        (
            # --- Organizacion del historial (V1.4) ---
            #
            # Estos tres campos podrian haber ido en la columna 'metadata', que es
            # JSON libre, pero se consultan en CADA listado: filtrar y ordenar por
            # un campo JSON es incomodo en SQLite y peor en PostgREST. Columnas
            # reales salen mas simples y mas rapidas. 'metadata' queda para lo que
            # de verdad es libre y no se filtra.
            "ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN group_name TEXT",
            # El listado por defecto es "las no archivadas, fijadas primero, y
            # dentro de cada grupo por reciente".
            "CREATE INDEX IF NOT EXISTS idx_sessions_orden "
            "ON sessions(archived, pinned DESC, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_grupo ON sessions(group_name)",
        ),
    ),
    (
        6,
        (
            # --- Indice de archivos subidos (V1.4) ---
            #
            # Vivia en memoria del proceso, asi que en la nube se perdia con cada
            # reinicio: el disco de Render es efimero y el servicio se duerme a
            # los 15 minutos. Persistirlo es lo que hace utiles las subidas fuera
            # del equipo del usuario.
            #
            # Los bytes NO estan aqui: van al disco en local y a Supabase Storage
            # en la nube. Esta tabla solo dice que existe, como se llama y donde.
            """
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY,
                nombre_original TEXT NOT NULL,
                mime TEXT NOT NULL,
                familia TEXT NOT NULL,
                tamano INTEGER NOT NULL,
                creado_en REAL NOT NULL,
                almacenamiento TEXT NOT NULL DEFAULT 'disco'
            )
            """,
            # El acceso dominante es "los mas recientes" y "los caducados".
            "CREATE INDEX IF NOT EXISTS idx_uploads_creado ON uploads(creado_en DESC)",
        ),
    ),
    (
        7,
        (
            # --- Sistema de tareas (V1.5) ---
            #
            # Los pasos van en una columna JSON y no en su propia tabla: siempre
            # se leen y escriben junto con su tarea, nunca por separado ni
            # filtrados, asi que una tabla aparte solo anadiria una union a cada
            # consulta. Si algun dia hiciera falta buscar pasos sueltos, se
            # normaliza entonces.
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                objetivo TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'pending',
                session_id TEXT,
                pasos TEXT NOT NULL DEFAULT '[]',
                resultado TEXT,
                error TEXT,
                intentos INTEGER NOT NULL DEFAULT 0,
                creado_en REAL NOT NULL,
                actualizado_en REAL NOT NULL
            )
            """,
            # Los dos accesos dominantes: "las de esta conversacion" y "las que
            # siguen vivas", que es lo que la interfaz consulta al pintar.
            "CREATE INDEX IF NOT EXISTS idx_tasks_sesion ON tasks(session_id, creado_en DESC)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_estado ON tasks(estado, actualizado_en DESC)",
        ),
    ),
    (
        8,
        (
            # --- Cuentas de usuario ---
            #
            # La cuenta de Morgan es propia y NO es la del proveedor: 'auth_user_id'
            # apunta a Supabase, pero el identificador con el que se relacionan
            # todos los datos es 'id'. Asi una misma cuenta puede asociarse mas
            # adelante con varios metodos de autenticacion sin migrar nada.
            """
            CREATE TABLE IF NOT EXISTS morgan_users (
                id TEXT PRIMARY KEY,
                auth_user_id TEXT UNIQUE,
                email TEXT,
                display_name TEXT,
                avatar_url TEXT,
                creado_en REAL NOT NULL,
                ultima_actividad REAL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_users_auth ON morgan_users(auth_user_id)",

            # El usuario implicito: existe siempre. Morgan local no debe pedir
            # login —es tu ordenador y la API escucha en 127.0.0.1—, pero si en
            # local no hubiera usuario, el aislamiento tendria dos caminos
            # distintos y uno de los dos acabaria sin filtrar.
            "INSERT OR IGNORE INTO morgan_users (id, display_name, creado_en) "
            "VALUES ('local', 'Usuario local', strftime('%s','now'))",

            # 'user_id' es nullable a proposito: obligarlo rompería el Morgan
            # local existente. El valor por defecto lo pone la capa de datos.
            "ALTER TABLE sessions ADD COLUMN user_id TEXT",
            "ALTER TABLE messages ADD COLUMN user_id TEXT",
            "ALTER TABLE memories ADD COLUMN user_id TEXT",
            "ALTER TABLE tasks    ADD COLUMN user_id TEXT",
            "ALTER TABLE uploads  ADD COLUMN user_id TEXT",

            # Los datos que ya existian son de quien tenia Morgan hasta ahora.
            # No se borra nada: se adoptan (§23 de la especificacion).
            "UPDATE sessions SET user_id = 'local' WHERE user_id IS NULL",
            "UPDATE messages SET user_id = 'local' WHERE user_id IS NULL",
            "UPDATE memories SET user_id = 'local' WHERE user_id IS NULL",
            "UPDATE tasks    SET user_id = 'local' WHERE user_id IS NULL",
            "UPDATE uploads  SET user_id = 'local' WHERE user_id IS NULL",

            # El acceso dominante pasa a ser "lo de este usuario, por reciente".
            "CREATE INDEX IF NOT EXISTS idx_sessions_usuario ON sessions(user_id, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_messages_usuario ON messages(user_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_memories_usuario ON memories(user_id, key)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_usuario ON tasks(user_id, creado_en DESC)",
            "CREATE INDEX IF NOT EXISTS idx_uploads_usuario ON uploads(user_id, creado_en DESC)",
        ),
    ),
    (
        9,
        (
            # --- La clave de un recuerdo es unica POR USUARIO ---
            #
            # 'key TEXT UNIQUE' era global. Con varios usuarios, el segundo que
            # guardara "color_favorito" sobrescribia el del primero: no es solo
            # una colision, es que Ana perdia su dato y pasaba a ver el de Bruno.
            # Lo detecto una prueba de aislamiento antes de que hubiera usuarios.
            #
            # SQLite no permite cambiar una restriccion: hay que recrear la tabla
            # y copiar. Se hace en una sola migracion, que es atomica.
            """
            CREATE TABLE IF NOT EXISTS memories_nueva (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'general',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT,
                UNIQUE(user_id, key)
            )
            """,
            """
            INSERT INTO memories_nueva
                (id, category, key, value, created_at, updated_at, user_id)
            SELECT id, category, key, value, created_at, updated_at,
                   COALESCE(user_id, 'local')
            FROM memories
            """,
            "DROP TABLE memories",
            "ALTER TABLE memories_nueva RENAME TO memories",
            "CREATE INDEX IF NOT EXISTS idx_mem_category ON memories(category)",
            "CREATE INDEX IF NOT EXISTS idx_mem_key ON memories(key)",
            "CREATE INDEX IF NOT EXISTS idx_memories_usuario ON memories(user_id, key)",
        ),
    ),
    (
        10,
        (
            # --- Uso por usuario ---
            #
            # Morgan usa las claves del dueno, asi que sin cupo una sola persona
            # podria agotar la cuota de todas. Se cuenta por dia natural y no con
            # una ventana deslizante: es mas facil de explicar ("te quedan 12
            # mensajes hoy") y una fila por usuario y dia se limpia sola.
            """
            CREATE TABLE IF NOT EXISTS uso_diario (
                user_id TEXT NOT NULL,
                dia TEXT NOT NULL,
                mensajes INTEGER NOT NULL DEFAULT 0,
                transcripciones INTEGER NOT NULL DEFAULT 0,
                imagenes INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, dia)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_uso_dia ON uso_diario(dia)",
        ),
    ),
    (
        11,
        (
            # --- Autenticacion propia de Morgan ---
            #
            # La cuenta es de Morgan. No hay identificador de proveedor externo:
            # 'auth_user_id' se retira porque apuntaba a Supabase Auth, que ya no
            # se usa para iniciar sesion. Conectar GitHub o Google mas adelante
            # sera otra cosa —una integracion, con su propia tabla— y no tocara
            # esta.
            "ALTER TABLE morgan_users ADD COLUMN username TEXT",
            "ALTER TABLE morgan_users ADD COLUMN password_hash TEXT",
            "ALTER TABLE morgan_users ADD COLUMN status TEXT NOT NULL DEFAULT 'activo'",
            "ALTER TABLE morgan_users ADD COLUMN email_verificado INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE morgan_users ADD COLUMN actualizado_en REAL",

            # El usuario implicito no tiene credenciales: no inicia sesion.
            "UPDATE morgan_users SET username = 'local' WHERE id = 'local'",

            # Unicos, pero permitiendo NULL: el usuario local no tiene email.
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON morgan_users(username)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON morgan_users(email)",

            # --- Sesiones de autenticacion ---
            #
            # Se llaman 'auth_sessions' y NO 'sessions' a proposito: esa tabla ya
            # existe y son las CONVERSACIONES. Reutilizar el nombre mezclaria dos
            # cosas que no tienen nada que ver y garantizaria confusion en cada
            # consulta que alguien escriba a partir de ahora.
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                creado_en REAL NOT NULL,
                expira_en REAL NOT NULL,
                ultimo_uso REAL,
                user_agent TEXT,
                revocada INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES morgan_users(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_auth_sessions_usuario ON auth_sessions(user_id, expira_en)",

            # --- Recuperacion de contrasena ---
            #
            # El token se guarda HASHEADO, no en claro: quien lea la base no debe
            # poder entrar en ninguna cuenta. Es la misma razon por la que no se
            # guardan contrasenas en claro.
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                creado_en REAL NOT NULL,
                expira_en REAL NOT NULL,
                usado_en REAL,
                FOREIGN KEY (user_id) REFERENCES morgan_users(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_reset_usuario ON password_reset_tokens(user_id, expira_en)",

            # --- Intentos de inicio de sesion ---
            #
            # Para frenar la fuerza bruta. Se cuenta por identificador Y por
            # origen: solo por identificador, cualquiera podria bloquear la cuenta
            # de otro a proposito fallando adrede.
            """
            CREATE TABLE IF NOT EXISTS login_intentos (
                identificador TEXT NOT NULL,
                origen TEXT NOT NULL,
                momento REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_intentos ON login_intentos(identificador, momento)",
        ),
    ),
    (
        12,
        (
            # --- El identificador de conversacion, unico POR USUARIO ---
            #
            # Era 'id TEXT PRIMARY KEY': unico de forma GLOBAL. Como el
            # identificador lo propone el cliente, otro usuario podia enviar un
            # mensaje con el id de una conversacion ajena y el mensaje se
            # guardaba dentro de ella. La lectura seguia aislada —nadie veia el
            # mensaje del otro— pero el contador lo contaba, y el historial que
            # lee el modelo quedaba contaminado.
            #
            # Es el mismo defecto que tenia 'memories' con su clave global, y se
            # arregla igual: el espacio de identificadores es de cada usuario.
            #
            # Antes se adoptan las filas sin dueno: en una base migrada desde la
            # v8 no deberian existir, pero una PRIMARY KEY no admite nulos y no
            # conviene que la migracion falle por una fila rara.
            "UPDATE sessions SET user_id = 'local' WHERE user_id IS NULL",
            "UPDATE messages SET user_id = 'local' WHERE user_id IS NULL",

            # --- PRIMERO 'messages', y el orden NO es indiferente ---
            #
            # 'messages' tenia una clave ajena a sessions(id) con ON DELETE
            # CASCADE. Recreando 'sessions' primero, el DROP TABLE disparaba esa
            # cascada y SE LLEVABA TODOS LOS MENSAJES POR DELANTE. Se descubrio
            # ejecutando la migracion sobre una copia de la base real: 24
            # mensajes entraron y salieron 0.
            #
            # La tabla nueva no lleva clave ajena. El borrado de mensajes ya es
            # explicito —el codigo nunca confio en el CASCADE, porque depende de
            # que el PRAGMA este activo en cada conexion— y una clave ajena
            # compuesta haria fallar la migracion ante un solo mensaje huerfano.
            """
            CREATE TABLE IF NOT EXISTS messages_v12 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tool_name TEXT,
                tool_call_id TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT NOT NULL DEFAULT '{}',
                user_id TEXT NOT NULL DEFAULT 'local'
            )
            """,
            """
            INSERT INTO messages_v12
                (id, session_id, role, content, tool_name, tool_call_id,
                 created_at, metadata, user_id)
            SELECT id, session_id, role, content, tool_name, tool_call_id,
                   created_at, metadata, COALESCE(user_id, 'local')
            FROM messages
            """,
            "DROP TABLE messages",
            "ALTER TABLE messages_v12 RENAME TO messages",
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_usuario ON messages(user_id, session_id, id)",

            # --- Y AHORA 'sessions', ya sin nadie colgando de ella ---
            """
            CREATE TABLE IF NOT EXISTS sessions_v12 (
                id TEXT NOT NULL,
                title TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT NOT NULL DEFAULT '{}',
                archived INTEGER NOT NULL DEFAULT 0,
                pinned INTEGER NOT NULL DEFAULT 0,
                group_name TEXT,
                user_id TEXT NOT NULL DEFAULT 'local',
                PRIMARY KEY (user_id, id)
            )
            """,
            """
            INSERT OR IGNORE INTO sessions_v12
                (id, title, created_at, updated_at, metadata, archived, pinned,
                 group_name, user_id)
            SELECT id, title, created_at, updated_at, metadata, archived, pinned,
                   group_name, COALESCE(user_id, 'local')
            FROM sessions
            """,
            "DROP TABLE sessions",
            "ALTER TABLE sessions_v12 RENAME TO sessions",
            "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_usuario ON sessions(user_id, updated_at DESC)",
        ),
    ),
    (
        13,
        (
            # --- Roles ---
            #
            # El rol vive en la fila del usuario, no en una condicion repartida
            # por el codigo. Cambiar quien es el propietario tiene que ser
            # cambiar un dato, no editar ficheros y desplegar.
            #
            # Por defecto 'user': lo que se registra sin mas es un usuario
            # normal. Nadie llega a 'owner' registrandose.
            "ALTER TABLE morgan_users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'",
            "CREATE INDEX IF NOT EXISTS idx_users_role ON morgan_users(role)",

            # Como mucho un propietario, y lo garantiza la BASE, no solo el
            # codigo. Una via que se olvide de comprobarlo no puede crear un
            # segundo: el indice lo rechaza.
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_un_solo_owner "
            "ON morgan_users(role) WHERE role = 'owner'",
        ),
    ),
    (
        14,
        (
            # --- Planes (V1.6) ---
            #
            # Un plan es lo que Morgan piensa hacer ANTES de hacerlo. Vive
            # aparte de las tareas porque describe intenciones y no hechos:
            # mezclarlos llevaria a mirar una lista y no saber que ya paso.
            #
            # Sobrevive al proceso a proposito: un plan pendiente de aprobacion
            # que se perdiera al reiniciar dejaria a la persona sin poder decidir
            # sobre un trabajo que Morgan ya habia preparado.
            """
            CREATE TABLE IF NOT EXISTS planes (
                id TEXT NOT NULL,
                objetivo TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'borrador',
                pasos TEXT NOT NULL DEFAULT '[]',
                session_id TEXT,
                task_id TEXT,
                creado_en REAL NOT NULL,
                decidido_en REAL,
                decidido_por TEXT,
                motivo_rechazo TEXT,
                user_id TEXT NOT NULL DEFAULT 'local',
                PRIMARY KEY (user_id, id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_planes_usuario ON planes(user_id, creado_en DESC)",
            "CREATE INDEX IF NOT EXISTS idx_planes_estado ON planes(user_id, estado)",
        ),
    ),
    (
        15,
        (
            # --- Conocimiento (V1.8) ---
            #
            # Separado de 'memories' a proposito. La memoria son cuatro hechos
            # sobre ti que viajan en CADA prompt; el conocimiento es material que
            # se consulta cuando viene a cuento. Meterlo todo en la misma tabla
            # engordaba el prompt con documentos que casi nunca se usan y diluia
            # lo que si importa.
            """
            CREATE TABLE IF NOT EXISTS conocimiento (
                id TEXT NOT NULL,
                titulo TEXT NOT NULL,
                -- Sin tildes y en minusculas, para que el filtro de busqueda
                -- pueda mirar el titulo igual que mira el texto.
                titulo_norm TEXT NOT NULL DEFAULT '',
                contenido TEXT NOT NULL,
                fuente TEXT NOT NULL DEFAULT 'manual',
                coleccion TEXT NOT NULL DEFAULT 'general',
                creado_en REAL NOT NULL,
                actualizado_en REAL NOT NULL,
                huella TEXT,
                metadatos TEXT NOT NULL DEFAULT '{}',
                user_id TEXT NOT NULL DEFAULT 'local',
                PRIMARY KEY (user_id, id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_conocimiento_usuario "
            "ON conocimiento(user_id, actualizado_en DESC)",
            "CREATE INDEX IF NOT EXISTS idx_conocimiento_coleccion "
            "ON conocimiento(user_id, coleccion)",

            # Los fragmentos son lo que se busca. Un documento entero no sirve
            # como unidad: si la respuesta esta en el parrafo 40 de 200, dar el
            # documento completo llena el contexto de lo que no hacia falta.
            #
            # 'texto_norm' guarda la version sin tildes y en minusculas. Se
            # guarda en vez de calcularse al buscar porque normalizar en cada
            # consulta obligaria a leer todos los fragmentos para descartarlos.
            """
            CREATE TABLE IF NOT EXISTS conocimiento_fragmentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                documento_id TEXT NOT NULL,
                orden INTEGER NOT NULL,
                texto TEXT NOT NULL,
                texto_norm TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'local'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_fragmentos_documento "
            "ON conocimiento_fragmentos(user_id, documento_id, orden)",
        ),
    ),
    (
        16,
        (
            # --- Integraciones con servicios externos (V1.9) ---
            #
            # Una fila por (usuario, servicio). Una persona no puede tener dos
            # cuentas de GitHub conectadas a la vez, y eso es intencionado:
            # «¿con cuál de tus dos GitHub quieres que mire?» es una pregunta
            # que Morgan tendria que hacer en cada peticion.
            #
            # El token va CIFRADO, nunca en claro. Con el se puede leer codigo
            # privado y escribir; una copia de seguridad extraviada dejaria de
            # ser un problema de privacidad y pasaria a ser uno de acceso.
            # Ver src/integraciones/secretos.py.
            """
            CREATE TABLE IF NOT EXISTS integraciones (
                user_id TEXT NOT NULL,
                servicio TEXT NOT NULL,
                -- Cifrado con Fernet. La columna se llama asi para que nadie
                -- escriba un token en claro aqui por descuido.
                token_cifrado TEXT NOT NULL,
                -- Los de refresco los usan Google y otros; GitHub no.
                refresco_cifrado TEXT,
                expira_en REAL,
                -- El nombre de la cuenta conectada. NO es secreto: es lo que se
                -- ensena para que se vea a nombre de quien actua Morgan.
                cuenta TEXT,
                scopes TEXT NOT NULL DEFAULT '',
                creado_en REAL NOT NULL,
                actualizado_en REAL NOT NULL,
                -- El ultimo fallo al hablar con el servicio. Distingue
                -- «no conectado» de «conectado y fallando», que para quien mira
                -- la pantalla son situaciones muy distintas.
                error TEXT,
                metadatos TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (user_id, servicio)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_integraciones_usuario "
            "ON integraciones(user_id)",

            # El estado del intercambio OAuth, mientras dura.
            #
            # Es lo que impide el CSRF de la autorizacion: sin comprobar que el
            # 'state' que vuelve es el que se emitio, una web ajena podria
            # completar el flujo y dejar SU cuenta de GitHub conectada a la
            # sesion de otra persona. Morgan actuaria despues sobre los
            # repositorios del atacante creyendo que son los tuyos.
            #
            # Caduca en minutos y se borra al usarse: un estado reutilizable
            # deja de ser una proteccion.
            """
            CREATE TABLE IF NOT EXISTS oauth_estados (
                estado TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                servicio TEXT NOT NULL,
                creado_en REAL NOT NULL,
                expira_en REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_oauth_estados_caducidad "
            "ON oauth_estados(expira_en)",
        ),
    ),
    (
        17,
        (
            # --- Verificacion del correo (V1.9) ---
            #
            # Se REUTILIZA la tabla de tokens de recuperacion en lugar de crear
            # otra. Un token de verificacion es exactamente lo mismo: un secreto
            # de un solo uso, atado a un usuario, con caducidad. Duplicar la
            # tabla habria duplicado tambien su limpieza, su comprobacion de
            # caducidad y sus propiedades de seguridad — y son justo las cosas
            # que no conviene tener por duplicado.
            #
            # El nombre de la tabla se queda como esta. Renombrarla en
            # produccion, con datos vivos, no vale lo que cuesta: el comentario
            # explica lo que el nombre ya no dice del todo.
            "ALTER TABLE password_reset_tokens ADD COLUMN tipo TEXT NOT NULL "
            "DEFAULT 'reset'",

            # Las consultas filtran SIEMPRE por tipo. Sin este indice, cada
            # verificacion recorreria tambien los tokens de recuperacion.
            "CREATE INDEX IF NOT EXISTS idx_tokens_tipo "
            "ON password_reset_tokens(tipo, token_hash)",
        ),
    ),
    (
        18,
        (
            # --- Espacios de trabajo (V2.2) ---
            #
            # Un espacio agrupa conversaciones, archivos y documentos, y tiene
            # instrucciones propias. La memoria NO pertenece a ningún espacio.
            # Ver docs/datos.md.
            """
            CREATE TABLE IF NOT EXISTS espacios (
                id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                nombre TEXT NOT NULL,
                instrucciones TEXT NOT NULL DEFAULT '',
                creado_en REAL NOT NULL,
                actualizado_en REAL NOT NULL,
                PRIMARY KEY (user_id, id)
            )
            """,
            # Dos espacios con el mismo nombre no se distinguen en la barra
            # lateral. La base lo impide además de la aplicación.
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_espacios_nombre "
            "ON espacios(user_id, nombre)",

            # NULL es «General»: lo que no está en ningún espacio. Todo lo que ya
            # existe queda ahí, así que nada cambia de sitio al migrar.
            "ALTER TABLE sessions ADD COLUMN espacio_id TEXT",
            "ALTER TABLE uploads ADD COLUMN espacio_id TEXT",
            "ALTER TABLE conocimiento ADD COLUMN espacio_id TEXT",
            "CREATE INDEX IF NOT EXISTS idx_sessions_espacio "
            "ON sessions(user_id, espacio_id)",
            "CREATE INDEX IF NOT EXISTS idx_uploads_espacio "
            "ON uploads(user_id, espacio_id)",
            "CREATE INDEX IF NOT EXISTS idx_conocimiento_espacio "
            "ON conocimiento(user_id, espacio_id)",

            # Los grupos que ya existían se convierten en espacios. `group_name`
            # era la media versión: texto libre en cada conversación, sin nada
            # que lo reuniera. Se conserva la columna; deja de usarse.
            """
            INSERT INTO espacios
                (id, user_id, nombre, instrucciones, creado_en, actualizado_en)
            SELECT 'esp-' || lower(hex(randomblob(6))), g.user_id, g.nombre, '',
                   CAST(strftime('%s', 'now') AS REAL),
                   CAST(strftime('%s', 'now') AS REAL)
            FROM (
                SELECT DISTINCT COALESCE(user_id, 'local') AS user_id,
                       trim(group_name) AS nombre
                FROM sessions
                WHERE group_name IS NOT NULL AND trim(group_name) <> ''
            ) AS g
            """,
            """
            UPDATE sessions SET espacio_id = (
                SELECT e.id FROM espacios e
                WHERE e.user_id = COALESCE(sessions.user_id, 'local')
                  AND e.nombre = trim(sessions.group_name)
            )
            WHERE group_name IS NOT NULL AND trim(group_name) <> ''
            """,
        ),
    ),
    (
        19,
        (
            # --- Tokens personales de API (plan de la API, fase 1) ---
            #
            # Lo que usa un cliente que no es el navegador —un script, un editor,
            # el agente local de la 3.0— para hablar con Morgan sin fingir que es
            # uno. Ver docs/autenticacion.md.
            #
            # Se guarda el SHA-256, nunca el valor: igual que las sesiones, quien
            # lea la base no obtiene nada usable. `alcances` es texto separado por
            # comas («chat,lectura»): son tres valores fijos y no hace falta una
            # tabla para ellos.
            """
            CREATE TABLE IF NOT EXISTS api_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                nombre TEXT NOT NULL,
                alcances TEXT NOT NULL,
                creado_en REAL NOT NULL,
                caduca_en REAL NOT NULL,
                ultimo_uso REAL,
                revocado INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES morgan_users(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_api_tokens_usuario ON api_tokens(user_id)",
        ),
    ),
    (
        20,
        (
            # --- Agentes locales (3.0-C) ---
            #
            # Un programa en el PC de alguien que ejecuta, con sus propias reglas, lo
            # que la nube le pide. Ver docs/agente-local.md. `1 user = N agents`
            # desde el primer día: nada aquí impide varios por persona.
            #
            # De la credencial, solo el hash; `estado` es el administrativo
            # (activo o revocado): si está conectado ahora es otra cosa, en memoria.
            """
            CREATE TABLE IF NOT EXISTS agentes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                nombre TEXT NOT NULL,
                sistema TEXT,
                agent_version TEXT,
                protocol_version INTEGER,
                credencial_hash TEXT NOT NULL UNIQUE,
                estado TEXT NOT NULL DEFAULT 'activo',
                creado_en REAL NOT NULL,
                last_seen REAL,
                FOREIGN KEY (user_id) REFERENCES morgan_users(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_agentes_usuario ON agentes(user_id)",
            # Los códigos de emparejamiento: diez minutos, un solo uso, y también
            # solo su hash. No son credenciales: el agente los cambia por una.
            """
            CREATE TABLE IF NOT EXISTS agente_codigos (
                codigo_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                creado_en REAL NOT NULL,
                caduca_en REAL NOT NULL,
                usado_en REAL,
                FOREIGN KEY (user_id) REFERENCES morgan_users(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_agente_codigos_usuario ON agente_codigos(user_id)",
        ),
    ),
    (
        21,
        (
            # --- El historial de órdenes a los agentes (3.7) ---
            #
            # Qué se pidió a cada PC, cuándo, cómo acabó y cuánto tardó. **Nunca los
            # argumentos ni el contenido** (decisión mía): una ruta o un texto que
            # se escribió no tienen por qué quedarse en la nube. 30 días.
            #
            # Antes esto iba solo al fichero de auditoría del servidor, que en Render
            # se borra con cada despliegue: la mitad de la nube de la «auditoría en las
            # dos puntas» no sobrevivía.
            """
            CREATE TABLE IF NOT EXISTS ordenes_agente (
                command_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                agent_id TEXT,
                request_id TEXT,
                capability TEXT NOT NULL,
                estado TEXT NOT NULL,
                ms REAL,
                creado_en REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_ordenes_agente_agente ON ordenes_agente(user_id, agent_id, creado_en)",
        ),
    ),
    (
        22,
        (
            # --- Rotar la credencial del agente (3.8) ---
            #
            # Decisión mía: sola cada 90 días, **en dos pasos**. La nueva queda aquí,
            # pendiente, y la vieja sigue valiendo hasta que el agente se conecta con la
            # nueva: un corte a mitad no deja el PC sin credencial. Solo hashes.
            "ALTER TABLE agentes ADD COLUMN credencial_nueva_hash TEXT",
            "ALTER TABLE agentes ADD COLUMN credencial_rotada_en REAL",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_agentes_credencial_nueva ON agentes(credencial_nueva_hash)",
        ),
    ),
    (
        23,
        (
            # --- El permiso automático (4.6, pedido por mí) ---
            #
            # Lo verde y amarillo se aprueba solo; lo rojo sigue esperando. En la cuenta y
            # no en la memoria: el modelo puede escribir recuerdos, y no puede escribir
            # aquí. Ver `src/identidad/permiso_automatico.py`.
            "ALTER TABLE morgan_users ADD COLUMN permiso_automatico INTEGER NOT NULL DEFAULT 0",
        ),
    ),
    (
        24,
        (
            # --- Las automatizaciones y la bandeja (4.14) ---
            #
            # Una orden guardada con un horario (`horario`, JSON; las horas, de `zona`) y
            # cuándo toca (`proxima`). `reclamo` sube con cada ejecución: solo la lanza
            # quien lo sube (ver `src/automatizacion/repositorio.py`). Los avisos son lo que
            # contó cada ejecución, para la bandeja de la web; 30 días.
            """
            CREATE TABLE IF NOT EXISTS automatizaciones (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                nombre TEXT NOT NULL,
                instruccion TEXT NOT NULL,
                horario TEXT NOT NULL,
                zona TEXT NOT NULL DEFAULT 'UTC',
                necesita_pc INTEGER NOT NULL DEFAULT 0,
                activa INTEGER NOT NULL DEFAULT 1,
                proxima REAL NOT NULL,
                esperando_pc_desde REAL,
                reclamo INTEGER NOT NULL DEFAULT 0,
                fallos_seguidos INTEGER NOT NULL DEFAULT 0,
                ultima REAL,
                ultimo_estado TEXT,
                creado_en REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_automatizaciones_usuario ON automatizaciones(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_automatizaciones_proxima ON automatizaciones(activa, proxima)",
            """
            CREATE TABLE IF NOT EXISTS avisos (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                automatizacion_id TEXT,
                titulo TEXT NOT NULL,
                texto TEXT NOT NULL,
                estado TEXT NOT NULL,
                herramientas TEXT,
                leido INTEGER NOT NULL DEFAULT 0,
                creado_en REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_avisos_usuario ON avisos(user_id, creado_en)",
        ),
    ),
    (
        25,
        (
            # --- Los cambios pre-aprobados (4.15) ---
            #
            # Los pasos fijos de una automatización que cambia cosas (JSON: herramienta,
            # argumentos y descripción), aprobados una vez. Sin pasos, solo consulta.
            "ALTER TABLE automatizaciones ADD COLUMN pasos TEXT",
        ),
    ),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]

# Roles admitidos en 'messages'. 'model' se acepta porque es el nombre que usa el
# nucleo del agente; se normaliza a 'assistant' al persistir.
VALID_ROLES = ("user", "assistant", "system", "tool")


class Database:
    """Gestiona el fichero SQLite: conexión, PRAGMAs, migraciones y errores."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        timeout: float | None = None,
    ):
        if db_path is None:
            data_dir = get_settings().data_dir
            data_dir.mkdir(parents=True, exist_ok=True)
            self.path = data_dir / "morgan_memory.db"
        else:
            self.path = Path(db_path)
            self.path.parent.mkdir(parents=True, exist_ok=True)

        self.timeout = timeout if timeout is not None else get_settings().db_timeout
        self._migration_lock = threading.Lock()
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Abre una conexión, la confirma o revierte, y **la cierra siempre**."""
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(self.path), timeout=self.timeout)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=%d" % int(self.timeout * 1000))
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    logger.warning("No se pudo revertir la transacción", exc_info=True)
            logger.error("Error de base de datos en %s: %s", self.path, exc)
            raise MemoryStorageError(f"Error de base de datos: {exc}") from exc
        except OSError as exc:
            logger.error("Error de acceso al fichero de base de datos %s: %s", self.path, exc)
            raise MemoryStorageError(f"No se pudo acceder a la base de datos: {exc}") from exc
        finally:
            if conn is not None:
                conn.close()

    def migrate(self) -> int:
        """Aplica las migraciones pendientes y devuelve la versión resultante."""
        with self._migration_lock, self.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                " version INTEGER PRIMARY KEY,"
                " applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0

            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                for statement in statements:
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                logger.info("Migración de base de datos aplicada: versión %d", version)
                current = version

            return current

    def version(self) -> int:
        """Versión de esquema registrada en el fichero."""
        with self.connect() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            return row["v"] or 0

    def healthy(self) -> tuple[bool, str | None]:
        """Comprueba que la base responde. Devuelve (ok, mensaje de error)."""
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True, None
        except MemoryStorageError as exc:
            return False, str(exc)


def guard(operation: str) -> Callable:
    """Decorador que traduce cualquier error de SQLite en `MemoryStorageError`."""

    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except MemoryStorageError:
                raise
            except sqlite3.Error as exc:
                logger.error("Fallo en la operación '%s': %s", operation, exc)
                raise MemoryStorageError(f"Error al {operation}: {exc}") from exc

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator
