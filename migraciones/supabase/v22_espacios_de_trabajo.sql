-- v22 — Espacios de trabajo (V2.2).
--
-- Un espacio agrupa conversaciones, archivos y documentos de conocimiento, y
-- tiene instrucciones propias que Morgan tiene en cuenta en cada turno. La
-- memoria sobre la persona NO pertenece a ningun espacio. Ver docs/espacios.md.
--
-- Todo es aditivo. NULL en `espacio_id` es «General», que es donde queda todo lo
-- que ya existia. Por eso el codigo que corre hoy en produccion sigue funcionando
-- entre que se aplica esta migracion y se despliega el nuevo: escribe NULL, lee
-- sin filtrar, y la busqueda de conocimiento recibe un parametro nuevo con valor
-- por defecto.

create table if not exists public.espacios (
    id text not null,
    user_id text not null,
    nombre text not null,
    instrucciones text not null default '',
    creado_en double precision not null,
    actualizado_en double precision not null,
    primary key (user_id, id)
);

-- Dos espacios con el mismo nombre no se distinguen en la barra lateral.
create unique index if not exists idx_espacios_nombre
    on public.espacios (user_id, nombre);

alter table public.espacios enable row level security;

drop policy if exists espacios_propios on public.espacios;
create policy espacios_propios on public.espacios
    using (morgan_priv.es_mi_fila(user_id));

alter table public.sessions add column if not exists espacio_id text;
alter table public.uploads add column if not exists espacio_id text;
alter table public.conocimiento add column if not exists espacio_id text;

create index if not exists idx_sessions_espacio
    on public.sessions (user_id, espacio_id);
create index if not exists idx_uploads_espacio
    on public.uploads (user_id, espacio_id);
create index if not exists idx_conocimiento_espacio
    on public.conocimiento (user_id, espacio_id);

-- Los grupos que ya existian se convierten en espacios. `group_name` era la
-- media version: texto libre en cada conversacion. Se conserva la columna.
insert into public.espacios (id, user_id, nombre, instrucciones, creado_en, actualizado_en)
select 'esp-' || substr(md5(random()::text || g.user_id || g.nombre), 1, 12),
       g.user_id, g.nombre, '',
       extract(epoch from now()), extract(epoch from now())
from (
    select distinct user_id, btrim(group_name) as nombre
    from public.sessions
    where group_name is not null and btrim(group_name) <> ''
) as g
on conflict do nothing;

update public.sessions s
set espacio_id = e.id
from public.espacios e
where e.user_id = s.user_id
  and e.nombre = btrim(s.group_name)
  and s.group_name is not null
  and btrim(s.group_name) <> '';

-- La busqueda de conocimiento, ahora dentro de un espacio.
--
-- Se BORRA la version de cuatro parametros antes de crear la de cinco. Con las
-- dos a la vez, PostgREST no sabria cual elegir para una llamada con cuatro
-- argumentos con nombre y responderia con un error de ambiguedad. El quinto tiene
-- valor por defecto, asi que las llamadas de cuatro siguen valiendo: buscan en
-- «General», que es donde esta todo hasta hoy.
drop function if exists public.buscar_conocimiento(text, text[], text, integer);
drop function if exists morgan_priv.buscar_conocimiento(text, text[], text, integer);

create or replace function morgan_priv.buscar_conocimiento(
    p_user_id text,
    p_raices text[],
    p_coleccion text default null,
    p_limite integer default 25,
    p_espacio_id text default null
) returns table (
    documento_id text,
    orden integer,
    texto text,
    texto_norm text,
    titulo text,
    titulo_norm text,
    fuente text,
    coleccion text
)
language sql
stable
security definer
set search_path to 'public', 'pg_temp'
as $$
    select f.documento_id, f.orden, f.texto, f.texto_norm,
           d.titulo, d.titulo_norm, d.fuente, d.coleccion
    from public.conocimiento_fragmentos f
    join public.conocimiento d
      on d.id = f.documento_id and d.user_id = f.user_id
    where f.user_id = p_user_id
      -- `is not distinct from` y no `=`: con `=`, NULL = NULL es desconocido y
      -- «General» no encontraria nunca nada.
      and d.espacio_id is not distinct from p_espacio_id
      and (p_coleccion is null or d.coleccion = p_coleccion)
      and exists (
          select 1 from unnest(p_raices) as raiz
          where f.texto_norm like '%' || raiz || '%'
             or d.titulo_norm like '%' || raiz || '%'
      )
    limit p_limite;
$$;

create or replace function public.buscar_conocimiento(
    p_user_id text,
    p_raices text[],
    p_coleccion text default null,
    p_limite integer default 25,
    p_espacio_id text default null
) returns table (
    documento_id text,
    orden integer,
    texto text,
    texto_norm text,
    titulo text,
    titulo_norm text,
    fuente text,
    coleccion text
)
language sql
stable
security definer
set search_path to 'morgan_priv', 'pg_temp'
as $$
    select * from morgan_priv.buscar_conocimiento(
        p_user_id, p_raices, p_coleccion, p_limite, p_espacio_id
    );
$$;

-- Los mismos permisos que dejaron v20 y v21, para la firma nueva. Sin esto,
-- CREATE FUNCTION concede EXECUTE a PUBLIC por defecto, y la funcion recibe el
-- user_id por parametro: serviria para leer el conocimiento de otro.
revoke all on function morgan_priv.buscar_conocimiento(text, text[], text, integer, text) from public;
grant execute on function morgan_priv.buscar_conocimiento(text, text[], text, integer, text) to service_role;

revoke all on function public.buscar_conocimiento(text, text[], text, integer, text) from public;
revoke all on function public.buscar_conocimiento(text, text[], text, integer, text) from anon;
revoke all on function public.buscar_conocimiento(text, text[], text, integer, text) from authenticated;
grant execute on function public.buscar_conocimiento(text, text[], text, integer, text) to service_role;
