-- v25 — El historial de ordenes a los agentes locales (3.7).
--
-- Que se pidio a cada PC, cuando, como acabo y cuanto tardo. NUNCA los argumentos ni
-- el contenido (decision mia, 2026-09-25): una ruta o un texto escrito no tienen
-- por que quedarse en la nube. Se guarda 30 dias: la limpieza periodica del backend
-- borra lo mas viejo.
--
-- Antes esto iba solo al fichero de auditoria del servidor, que en Render se borra con
-- cada despliegue.
--
-- Como el resto de tablas de cuentas y agentes: RLS activo y NINGUNA politica, asi que
-- solo la clave de servicio del backend llega a ella.
--
-- Aditiva: el codigo que corre hoy en produccion no la conoce y sigue igual.

create table if not exists public.ordenes_agente (
    command_id text primary key,
    user_id text not null references public.morgan_users (id) on delete cascade,
    agent_id text,
    request_id text,
    capability text not null,
    estado text not null,
    ms double precision,
    creado_en double precision not null
);

create index if not exists idx_ordenes_agente_agente
    on public.ordenes_agente (user_id, agent_id, creado_en);

alter table public.ordenes_agente enable row level security;
