-- v24 — Agentes locales (3.0-C): identidad y emparejamiento.
--
-- Un programa en el PC de alguien que ejecuta, con sus propias reglas, lo que la
-- nube le pide. Ver docs/agente-local.md. `1 user = N agents` desde el primer dia:
-- nada aqui impide varios por persona.
--
-- Como auth_sessions y api_tokens: RLS activo y NINGUNA politica, asi que solo la
-- clave de servicio del backend llega a estas tablas. De la credencial y de los
-- codigos, solo el hash.
--
-- Aditiva: el codigo que corre hoy en produccion no las conoce y sigue igual.

create table if not exists public.agentes (
    id text primary key,
    user_id text not null references public.morgan_users (id) on delete cascade,
    nombre text not null,
    sistema text,
    agent_version text,
    protocol_version integer,
    credencial_hash text not null unique,
    estado text not null default 'activo',
    creado_en double precision not null,
    last_seen double precision
);

create index if not exists idx_agentes_usuario on public.agentes (user_id);

alter table public.agentes enable row level security;

-- Los codigos de emparejamiento: diez minutos, un solo uso. No son credenciales:
-- el agente los cambia por una.
create table if not exists public.agente_codigos (
    codigo_hash text primary key,
    user_id text not null references public.morgan_users (id) on delete cascade,
    creado_en double precision not null,
    caduca_en double precision not null,
    usado_en double precision
);

create index if not exists idx_agente_codigos_usuario on public.agente_codigos (user_id);

alter table public.agente_codigos enable row level security;
