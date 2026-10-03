-- v28 — Las automatizaciones y la bandeja de avisos (4.14).
--
-- Una automatizacion es una orden guardada con un horario: Morgan la ejecuta sola, sin
-- nadie delante, y deja lo que hizo en la bandeja de la web. Mis decisiones en
-- docs/plan-4.x.md. Las horas son de `zona` (la del navegador de quien la creo); `proxima`
-- es cuando toca, en segundos desde 1970 (UTC), como el resto de tablas de cuentas.
--
-- `reclamo` sube con cada ejecucion y solo la lanza quien lo sube: el reloj llama cada
-- minuto y dos llamadas que se cruzan no ejecutan dos veces lo mismo.
--
-- Como el resto de tablas de cuentas y agentes: RLS activo y NINGUNA politica, asi que
-- solo la clave de servicio del backend llega a ellas.
--
-- Aditiva: el codigo que corre hoy en produccion no la conoce y sigue igual. El reloj
-- (pg_cron + pg_net) va aparte, en v29, porque necesita la direccion y el secreto.

create table if not exists public.automatizaciones (
    id text primary key,
    user_id text not null references public.morgan_users (id) on delete cascade,
    nombre text not null,
    instruccion text not null,
    horario text not null,
    zona text not null default 'UTC',
    necesita_pc boolean not null default false,
    activa boolean not null default true,
    proxima double precision not null,
    esperando_pc_desde double precision,
    reclamo integer not null default 0,
    fallos_seguidos integer not null default 0,
    ultima double precision,
    ultimo_estado text,
    creado_en double precision not null
);

create index if not exists idx_automatizaciones_usuario on public.automatizaciones (user_id);
create index if not exists idx_automatizaciones_proxima on public.automatizaciones (activa, proxima);

alter table public.automatizaciones enable row level security;

create table if not exists public.avisos (
    id text primary key,
    user_id text not null references public.morgan_users (id) on delete cascade,
    automatizacion_id text,
    titulo text not null,
    texto text not null,
    estado text not null,
    herramientas text,
    leido boolean not null default false,
    creado_en double precision not null
);

create index if not exists idx_avisos_usuario on public.avisos (user_id, creado_en);

alter table public.avisos enable row level security;
