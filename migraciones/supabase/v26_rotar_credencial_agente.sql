-- v26 — Rotar la credencial de los agentes locales (3.8).
--
-- Decision mia (2026-09-25): la credencial `mga_` de cada PC rota sola cada 90 dias,
-- EN DOS PASOS. La nueva queda en `credencial_nueva_hash`, pendiente; la vieja sigue
-- valiendo hasta que el agente se conecta con la nueva, y entonces la nueva pasa a
-- `credencial_hash` y la vieja deja de valer. Un corte a mitad no deja el PC sin
-- credencial. Como siempre, solo hashes.
--
-- Aditiva: el codigo que corre hoy en produccion no conoce estas columnas y sigue igual.
-- Las politicas no cambian: la tabla ya tiene RLS activo y ninguna politica.

alter table public.agentes add column if not exists credencial_nueva_hash text;
alter table public.agentes add column if not exists credencial_rotada_en double precision;

create unique index if not exists idx_agentes_credencial_nueva
    on public.agentes (credencial_nueva_hash);
