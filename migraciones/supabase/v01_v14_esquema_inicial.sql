-- Las 14 primeras migraciones de Supabase (V1.3 → V1.6), tal como se aplicaron (4.20).
--
-- Se aplicaron directamente en Supabase y nunca llegaron al repositorio: sin este fichero,
-- un proyecto perdido no se podía rehacer desde aquí (la copia de los datos no tenía dónde
-- volver). Recuperadas del historial de Supabase (`supabase_migrations.schema_migrations`)
-- y comprobadas: el MD5 de cada bloque coincide con el que guarda Postgres.
--
-- Para rehacer la base desde cero: este fichero y después v15…v30, en orden.
-- Cada bloque va entre `-- >>> <versión> <nombre>` y `-- <<<`.

-- >>> 20260905212010 morgan_v13_core_schema
-- Morgan V1.3 — esquema cloud, espejo del modelo local de SQLite.
--
-- Diferencias deliberadas frente a la base local:
--   * updated_at en messages: hace falta para resolver conflictos por
--     last-write-wins durante la sincronizacion (ADR de V1.3).
--   * origin_device: permite saber que equipo escribio cada fila.
--   * RLS activado en las tres tablas. Morgan es un agente personal y el
--     backend accede con la clave de servicio, pero dejar las tablas abiertas
--     al rol anonimo significaria que cualquiera con la URL del proyecto podria
--     leer las conversaciones del usuario.

create table if not exists public.sessions (
    id text primary key,
    title text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb,
    origin_device text
);

create index if not exists idx_sessions_updated on public.sessions (updated_at desc);

create table if not exists public.messages (
    id bigserial primary key,
    session_id text not null references public.sessions (id) on delete cascade,
    role text not null check (role in ('user', 'assistant', 'system', 'tool')),
    content text not null default '',
    tool_name text,
    tool_call_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb,
    origin_device text,
    -- Identidad estable entre local y nube: el id local es autoincremental y
    -- distinto en cada equipo, asi que no sirve para deduplicar al sincronizar.
    client_id text unique
);

create index if not exists idx_messages_session on public.messages (session_id, id);
create index if not exists idx_messages_created on public.messages (created_at);

create table if not exists public.memories (
    id bigserial primary key,
    key text not null unique,
    value text not null,
    category text not null default 'general',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    origin_device text
);

create index if not exists idx_memories_category on public.memories (category);
create index if not exists idx_memories_updated on public.memories (updated_at desc);

-- Sin politicas: el acceso queda restringido a la clave de servicio, que es la
-- que usa el backend de Morgan. El rol anonimo no puede leer ni escribir nada.
alter table public.sessions enable row level security;
alter table public.messages enable row level security;
alter table public.memories enable row level security;
-- <<<

-- >>> 20260906054214 v5_organizacion_del_historial
-- Espejo de la migracion v5 de SQLite (src/memory/db.py).
-- Aditiva: no toca ni borra datos existentes.
ALTER TABLE public.sessions ADD COLUMN IF NOT EXISTS archived boolean NOT NULL DEFAULT false;
ALTER TABLE public.sessions ADD COLUMN IF NOT EXISTS pinned boolean NOT NULL DEFAULT false;
ALTER TABLE public.sessions ADD COLUMN IF NOT EXISTS group_name text;

-- El listado por defecto es "las no archivadas, fijadas primero, por reciente".
CREATE INDEX IF NOT EXISTS idx_sessions_orden
  ON public.sessions (archived, pinned DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_grupo
  ON public.sessions (group_name);
-- <<<

-- >>> 20260906070252 v6_indice_de_subidas_y_bucket
-- Espejo de la migracion v6 de SQLite (src/memory/db.py).
-- Aditiva: crea una tabla nueva y un bucket privado; no toca nada existente.

CREATE TABLE IF NOT EXISTS public.uploads (
  id              text PRIMARY KEY,
  nombre_original text NOT NULL,
  mime            text NOT NULL,
  familia         text NOT NULL,
  tamano          bigint NOT NULL,
  creado_en       double precision NOT NULL,
  almacenamiento  text NOT NULL DEFAULT 'supabase',
  origin_device   text
);

CREATE INDEX IF NOT EXISTS idx_uploads_creado
  ON public.uploads (creado_en DESC);

-- Coherente con el resto de tablas: RLS activo. El backend usa la service key,
-- que la salta; sin politicas, nadie mas puede leer estos registros.
ALTER TABLE public.uploads ENABLE ROW LEVEL SECURITY;

-- Bucket PRIVADO para los bytes. Publico dejaria los documentos del usuario
-- accesibles con solo adivinar la URL.
INSERT INTO storage.buckets (id, name, public)
VALUES ('morgan-uploads', 'morgan-uploads', false)
ON CONFLICT (id) DO NOTHING;
-- <<<

-- >>> 20260906071137 v7_sistema_de_tareas
-- Espejo de la migracion v7 de SQLite (src/memory/db.py). Aditiva.
CREATE TABLE IF NOT EXISTS public.tasks (
  id             text PRIMARY KEY,
  objetivo       text NOT NULL,
  estado         text NOT NULL DEFAULT 'pending',
  session_id     text,
  -- jsonb y no text: en Postgres es el tipo natural y permite consultar dentro
  -- si algun dia hace falta, sin migrar la columna.
  pasos          jsonb NOT NULL DEFAULT '[]'::jsonb,
  resultado      text,
  error          text,
  intentos       integer NOT NULL DEFAULT 0,
  creado_en      double precision NOT NULL,
  actualizado_en double precision NOT NULL,
  origin_device  text
);

CREATE INDEX IF NOT EXISTS idx_tasks_sesion
  ON public.tasks (session_id, creado_en DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_estado
  ON public.tasks (estado, actualizado_en DESC);

-- Coherente con el resto de tablas.
ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;
-- <<<

-- >>> 20260906122131 v8_cuentas_de_usuario
-- Espejo de la migracion v8 de SQLite. Aditiva: no borra nada.

-- La cuenta de Morgan es propia y NO es la del proveedor. auth_user_id apunta a
-- Supabase, pero el identificador con el que se relacionan los datos es 'id'.
CREATE TABLE IF NOT EXISTS public.morgan_users (
  id               text PRIMARY KEY,
  auth_user_id     uuid UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
  email            text,
  display_name     text,
  avatar_url       text,
  creado_en        double precision NOT NULL,
  ultima_actividad double precision
);

CREATE INDEX IF NOT EXISTS idx_users_auth ON public.morgan_users (auth_user_id);

-- El usuario implicito, para que el Morgan local tenga siempre a quien pertenecer.
INSERT INTO public.morgan_users (id, display_name, creado_en)
VALUES ('local', 'Usuario local', extract(epoch from now()))
ON CONFLICT (id) DO NOTHING;

-- user_id nullable a proposito: obligarlo rompería lo que ya existe.
ALTER TABLE public.sessions ADD COLUMN IF NOT EXISTS user_id text;
ALTER TABLE public.messages ADD COLUMN IF NOT EXISTS user_id text;
ALTER TABLE public.memories ADD COLUMN IF NOT EXISTS user_id text;
ALTER TABLE public.tasks    ADD COLUMN IF NOT EXISTS user_id text;
ALTER TABLE public.uploads  ADD COLUMN IF NOT EXISTS user_id text;

-- Lo que ya existia es de quien tenia Morgan hasta ahora. No se borra: se adopta.
UPDATE public.sessions SET user_id = 'local' WHERE user_id IS NULL;
UPDATE public.messages SET user_id = 'local' WHERE user_id IS NULL;
UPDATE public.memories SET user_id = 'local' WHERE user_id IS NULL;
UPDATE public.tasks    SET user_id = 'local' WHERE user_id IS NULL;
UPDATE public.uploads  SET user_id = 'local' WHERE user_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_sessions_usuario ON public.sessions (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_usuario ON public.messages (user_id, id);
CREATE INDEX IF NOT EXISTS idx_memories_usuario ON public.memories (user_id, key);
CREATE INDEX IF NOT EXISTS idx_tasks_usuario    ON public.tasks (user_id, creado_en DESC);
CREATE INDEX IF NOT EXISTS idx_uploads_usuario  ON public.uploads (user_id, creado_en DESC);

ALTER TABLE public.morgan_users ENABLE ROW LEVEL SECURITY;
-- <<<

-- >>> 20260906122151 v8b_politicas_rls_por_usuario
-- Segunda barrera de aislamiento.
--
-- El backend filtra por user_id en la capa de repositorios. Estas politicas son
-- la red debajo: si alguna consulta se escapa sin filtrar, la base la detiene.
-- Dos cierres independientes, como se decidio.
--
-- IMPORTANTE: la service key que usa el backend SALTA estas politicas. Aplican a
-- cualquier acceso con la clave anonima o con el JWT de un usuario, que es la via
-- por la que llegaria un cliente que no deberia ver estos datos.

-- Cada usuario ve y edita SOLO su propia fila de cuenta.
DROP POLICY IF EXISTS usuarios_propios ON public.morgan_users;
CREATE POLICY usuarios_propios ON public.morgan_users
  FOR ALL TO authenticated
  USING (auth_user_id = auth.uid())
  WITH CHECK (auth_user_id = auth.uid());

-- Para el resto de tablas, la pertenencia se resuelve contra morgan_users: el
-- user_id de la fila tiene que ser el de la cuenta de quien consulta.
CREATE OR REPLACE FUNCTION public.es_mi_fila(fila_user_id text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.morgan_users u
    WHERE u.id = fila_user_id AND u.auth_user_id = auth.uid()
  );
$$;

DROP POLICY IF EXISTS sessions_propias ON public.sessions;
CREATE POLICY sessions_propias ON public.sessions
  FOR ALL TO authenticated
  USING (public.es_mi_fila(user_id))
  WITH CHECK (public.es_mi_fila(user_id));

DROP POLICY IF EXISTS messages_propios ON public.messages;
CREATE POLICY messages_propios ON public.messages
  FOR ALL TO authenticated
  USING (public.es_mi_fila(user_id))
  WITH CHECK (public.es_mi_fila(user_id));

DROP POLICY IF EXISTS memories_propias ON public.memories;
CREATE POLICY memories_propias ON public.memories
  FOR ALL TO authenticated
  USING (public.es_mi_fila(user_id))
  WITH CHECK (public.es_mi_fila(user_id));

DROP POLICY IF EXISTS tasks_propias ON public.tasks;
CREATE POLICY tasks_propias ON public.tasks
  FOR ALL TO authenticated
  USING (public.es_mi_fila(user_id))
  WITH CHECK (public.es_mi_fila(user_id));

DROP POLICY IF EXISTS uploads_propias ON public.uploads;
CREATE POLICY uploads_propias ON public.uploads
  FOR ALL TO authenticated
  USING (public.es_mi_fila(user_id))
  WITH CHECK (public.es_mi_fila(user_id));
-- <<<

-- >>> 20260906122221 v8c_funcion_rls_fuera_del_esquema_publico
-- El asesor de seguridad avisó de que `public.es_mi_fila` era invocable por REST
-- (`/rest/v1/rpc/es_mi_fila`) tanto por anon como por usuarios autenticados. Al
-- ser SECURITY DEFINER, eso permitiría sondear qué user_id existen.
--
-- La funcion solo la necesitan las politicas, no ningun cliente. Se mueve a un
-- esquema privado que PostgREST no expone: sigue funcionando en las politicas y
-- deja de ser alcanzable desde fuera.

CREATE SCHEMA IF NOT EXISTS morgan_priv;

-- Nadie usa este esquema salvo las politicas, que corren con los privilegios del
-- definidor.
REVOKE ALL ON SCHEMA morgan_priv FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION morgan_priv.es_mi_fila(fila_user_id text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.morgan_users u
    WHERE u.id = fila_user_id AND u.auth_user_id = auth.uid()
  );
$$;

REVOKE ALL ON FUNCTION morgan_priv.es_mi_fila(text) FROM PUBLIC, anon, authenticated;

-- Las politicas pasan a usar la version privada.
DROP POLICY IF EXISTS sessions_propias ON public.sessions;
CREATE POLICY sessions_propias ON public.sessions
  FOR ALL TO authenticated
  USING (morgan_priv.es_mi_fila(user_id))
  WITH CHECK (morgan_priv.es_mi_fila(user_id));

DROP POLICY IF EXISTS messages_propios ON public.messages;
CREATE POLICY messages_propios ON public.messages
  FOR ALL TO authenticated
  USING (morgan_priv.es_mi_fila(user_id))
  WITH CHECK (morgan_priv.es_mi_fila(user_id));

DROP POLICY IF EXISTS memories_propias ON public.memories;
CREATE POLICY memories_propias ON public.memories
  FOR ALL TO authenticated
  USING (morgan_priv.es_mi_fila(user_id))
  WITH CHECK (morgan_priv.es_mi_fila(user_id));

DROP POLICY IF EXISTS tasks_propias ON public.tasks;
CREATE POLICY tasks_propias ON public.tasks
  FOR ALL TO authenticated
  USING (morgan_priv.es_mi_fila(user_id))
  WITH CHECK (morgan_priv.es_mi_fila(user_id));

DROP POLICY IF EXISTS uploads_propias ON public.uploads;
CREATE POLICY uploads_propias ON public.uploads
  FOR ALL TO authenticated
  USING (morgan_priv.es_mi_fila(user_id))
  WITH CHECK (morgan_priv.es_mi_fila(user_id));

DROP FUNCTION IF EXISTS public.es_mi_fila(text);
-- <<<

-- >>> 20260906125657 v10_uso_por_usuario
-- Contador de uso por usuario y dia (V1.6).
--
-- Morgan usa las claves del dueno, asi que sin un cupo una sola persona podria
-- agotar la cuota de todas. Se cuenta por dia natural y no con una ventana
-- deslizante: es mas facil de explicar ("te quedan 12 mensajes hoy") y una fila
-- por usuario y dia se limpia sola con un borrado por fecha.

CREATE TABLE IF NOT EXISTS public.uso_diario (
  user_id       text NOT NULL,
  dia           date NOT NULL,
  mensajes      integer NOT NULL DEFAULT 0,
  transcripciones integer NOT NULL DEFAULT 0,
  imagenes      integer NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, dia)
);

CREATE INDEX IF NOT EXISTS idx_uso_dia ON public.uso_diario (dia);

ALTER TABLE public.uso_diario ENABLE ROW LEVEL SECURITY;

-- Cada usuario ve su propio consumo, y nada mas. El backend escribe con la
-- service key, que salta estas politicas.
DROP POLICY IF EXISTS uso_propio ON public.uso_diario;
CREATE POLICY uso_propio ON public.uso_diario
  FOR SELECT TO authenticated
  USING (morgan_priv.es_mi_fila(user_id));
-- <<<

-- >>> 20260906131354 v9_memoria_unica_por_usuario
-- La clave de memoria era unica GLOBALMENTE: el 'color_favorito' de una persona
-- pisaba el de otra. Debe ser unica POR USUARIO. En SQLite hubo que recrear la
-- tabla; aqui basta con cambiar la restriccion.
alter table public.memories drop constraint if exists memories_key_key;
alter table public.memories add constraint memories_user_key_key unique (user_id, key);
-- <<<

-- >>> 20260906131409 v11_autenticacion_propia
-- Autenticacion propia de Morgan: usuario/correo y contrasena.
-- Espejo de la migracion v11 de SQLite (src/memory/db.py).

alter table public.morgan_users add column if not exists username text;
alter table public.morgan_users add column if not exists password_hash text;
alter table public.morgan_users add column if not exists status text not null default 'activo';
alter table public.morgan_users add column if not exists email_verificado boolean not null default false;
alter table public.morgan_users add column if not exists actualizado_en double precision;

-- El usuario implicito no tiene credenciales: no inicia sesion.
update public.morgan_users set username = 'local' where id = 'local';

-- Unicos, pero permitiendo NULL: el usuario local no tiene correo.
create unique index if not exists idx_users_username on public.morgan_users(username);
create unique index if not exists idx_users_email on public.morgan_users(email);

-- Sesiones de autenticacion. Se llaman 'auth_sessions' y NO 'sessions' a
-- proposito: esa tabla ya existe y son las CONVERSACIONES.
-- El 'id' es el HASH del identificador de sesion, nunca el valor en claro.
create table if not exists public.auth_sessions (
    id text primary key,
    user_id text not null references public.morgan_users(id) on delete cascade,
    creado_en double precision not null,
    expira_en double precision not null,
    ultimo_uso double precision,
    user_agent text,
    revocada boolean not null default false
);
create index if not exists idx_auth_sessions_usuario on public.auth_sessions(user_id, expira_en);

-- Recuperacion de contrasena. El token se guarda HASHEADO: quien lea la base no
-- debe poder entrar en ninguna cuenta.
create table if not exists public.password_reset_tokens (
    token_hash text primary key,
    user_id text not null references public.morgan_users(id) on delete cascade,
    creado_en double precision not null,
    expira_en double precision not null,
    usado_en double precision
);
create index if not exists idx_reset_usuario on public.password_reset_tokens(user_id, expira_en);

-- Intentos de inicio de sesion, para frenar la fuerza bruta. Se cuenta por
-- identificador Y por origen: solo por identificador, cualquiera podria bloquear
-- la cuenta de otro fallando adrede.
create table if not exists public.login_intentos (
    id bigserial primary key,
    identificador text not null,
    origen text not null,
    momento double precision not null
);
create index if not exists idx_intentos on public.login_intentos(identificador, momento);

-- RLS. Estas tres tablas guardan material con el que se suplanta a alguien, y
-- ninguna se consulta jamas desde el navegador: solo el backend, con la clave de
-- servicio, que salta RLS. Por eso se activa sin ninguna politica: el efecto es
-- que nadie mas puede leerlas, que es exactamente lo que se quiere.
alter table public.auth_sessions enable row level security;
alter table public.password_reset_tokens enable row level security;
alter table public.login_intentos enable row level security;
-- <<<

-- >>> 20260906134116 v12_apuntar_uso_atomico
-- Apuntar consumo y comprobar el cupo TIENEN que ser una sola operacion. Si se
-- separan, dos peticiones simultaneas leen el mismo contador y pasan las dos.
--
-- En SQLite eso se resuelve con "UPDATE ... WHERE contador < limite" y mirando
-- rowcount. PostgREST no sabe escribir "columna = columna + 1", asi que aqui
-- hace falta una funcion.
--
-- Vive en morgan_priv y NO en public: en public quedaria expuesta por REST y
-- cualquiera con la clave publicable podria manipular su propio consumo.
create schema if not exists morgan_priv;

create or replace function morgan_priv.apuntar_uso(
    p_user_id text,
    p_dia text,
    p_concepto text,
    p_limite integer
) returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_filas integer;
begin
    -- Lista blanca: p_concepto acaba dentro de SQL dinamico, y sin esto seria
    -- una inyeccion.
    if p_concepto not in ('mensajes', 'transcripciones', 'imagenes') then
        raise exception 'Concepto de uso desconocido: %', p_concepto;
    end if;

    insert into public.uso_diario (user_id, dia)
    values (p_user_id, p_dia)
    on conflict (user_id, dia) do nothing;

    execute format(
        'update public.uso_diario set %I = %I + 1
         where user_id = $1 and dia = $2 and %I < $3',
        p_concepto, p_concepto, p_concepto
    ) using p_user_id, p_dia, p_limite;

    get diagnostics v_filas = row_count;

    -- true: cabia y queda apuntado. false: ya estaba en el limite.
    return v_filas > 0;
end;
$$;

revoke all on function morgan_priv.apuntar_uso(text, text, text, integer) from public, anon, authenticated;
-- <<<

-- >>> 20260906212510 v12_conversacion_unica_por_usuario
-- El identificador de conversacion era unico GLOBALMENTE, y lo propone el
-- cliente: otro usuario podia enviar un mensaje con el id de una conversacion
-- ajena y quedaba guardado dentro de ella. Mismo defecto que tenia 'memories'.
--
-- En Postgres no hace falta recrear las tablas: se cambia la clave primaria y
-- se rehace la clave ajena para que apunte a la nueva.

update public.sessions set user_id = 'local' where user_id is null;
update public.messages set user_id = 'local' where user_id is null;

alter table public.sessions alter column user_id set not null;
alter table public.messages alter column user_id set not null;

-- La clave ajena tiene que soltarse ANTES de tocar la clave primaria a la que
-- apunta. En SQLite el equivalente costo 24 mensajes al probarlo sobre una
-- copia: el DROP TABLE disparaba el ON DELETE CASCADE.
alter table public.messages drop constraint if exists messages_session_id_fkey;

alter table public.sessions drop constraint if exists sessions_pkey;
alter table public.sessions add constraint sessions_pkey primary key (user_id, id);

alter table public.messages
    add constraint messages_session_fkey
    foreign key (user_id, session_id)
    references public.sessions(user_id, id)
    on delete cascade;

create index if not exists idx_sessions_usuario on public.sessions(user_id, updated_at desc);
create index if not exists idx_messages_usuario on public.messages(user_id, session_id, id);
-- <<<

-- >>> 20260906223045 v13_roles
-- El rol vive en la fila del usuario, no en una condicion repartida por el
-- codigo. Cambiar quien es el propietario tiene que ser cambiar un dato, no
-- editar ficheros y desplegar.
--
-- Por defecto 'user': lo que se registra sin mas es un usuario normal. Nadie
-- llega a 'owner' registrandose.
alter table public.morgan_users
    add column if not exists role text not null default 'user';

create index if not exists idx_users_role on public.morgan_users(role);

-- Como mucho un propietario. La restriccion la pone la base y no solo el
-- codigo: una via que se olvide de comprobarlo no puede crear un segundo.
create unique index if not exists idx_un_solo_owner
    on public.morgan_users((true)) where role = 'owner';
-- <<<

-- >>> 20260907002642 v14_planes
-- Planes (V1.6): lo que Morgan piensa hacer ANTES de hacerlo.
--
-- Viven aparte de las tareas porque describen intenciones y no hechos: mezclarlos
-- llevaria a mirar una lista y no saber que ya paso.
--
-- Sobreviven al proceso a proposito: un plan pendiente de aprobacion que se
-- perdiera al reiniciar dejaria a la persona sin poder decidir sobre un trabajo
-- que Morgan ya habia preparado.
create table if not exists public.planes (
    id text not null,
    objetivo text not null,
    estado text not null default 'borrador',
    pasos jsonb not null default '[]'::jsonb,
    session_id text,
    task_id text,
    creado_en double precision not null,
    decidido_en double precision,
    decidido_por text,
    motivo_rechazo text,
    user_id text not null default 'local',
    origin_device text,
    primary key (user_id, id)
);

create index if not exists idx_planes_usuario on public.planes(user_id, creado_en desc);
create index if not exists idx_planes_estado on public.planes(user_id, estado);

-- Segunda barrera, como en el resto de entidades: el filtro por usuario del
-- repositorio es la primera, y esta vale aunque una consulta se olvide.
alter table public.planes enable row level security;

drop policy if exists planes_propias on public.planes;
create policy planes_propias on public.planes
    for all
    using (morgan_priv.es_mi_fila(user_id))
    with check (morgan_priv.es_mi_fila(user_id));
-- <<<
