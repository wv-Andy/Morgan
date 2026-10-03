-- v23 — Tokens personales de API (plan de la API, fase 1).
--
-- Lo que usa un cliente que no es el navegador —un script, un editor, el agente
-- local de la 3.0— para hablar con Morgan sin fingir que es uno. Ver
-- docs/autenticacion.md.
--
-- Se guarda el SHA-256, nunca el valor: quien lea la tabla no obtiene nada usable.
-- Como auth_sessions: RLS activo y NINGUNA politica, asi que solo la clave de
-- servicio del backend llega a ella. Con la clave publicable no se ve nada.
--
-- Aditiva: el codigo que corre hoy en produccion no la conoce y sigue igual.

create table if not exists public.api_tokens (
    id text primary key,
    user_id text not null references public.morgan_users (id) on delete cascade,
    token_hash text not null unique,
    nombre text not null,
    alcances text not null,
    creado_en double precision not null,
    caduca_en double precision not null,
    ultimo_uso double precision,
    revocado boolean not null default false
);

create index if not exists idx_api_tokens_usuario on public.api_tokens (user_id);

alter table public.api_tokens enable row level security;
