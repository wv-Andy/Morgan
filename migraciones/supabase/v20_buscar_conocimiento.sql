-- v20 — El filtro grueso de la busqueda de conocimiento.
--
-- Es una funcion y no una consulta de PostgREST porque lo que hace falta —un
-- JOIN entre fragmentos y documentos, con un OR de LIKE sobre DOS columnas por
-- cada termino— no se puede expresar en la sintaxis de filtros de PostgREST sin
-- retorcerla hasta hacerla ilegible.
--
-- **Solo filtra. No puntua.** El ranking se queda en Python, compartido palabra
-- por palabra con la version de SQLite: tener dos implementaciones de la
-- relevancia significaria que la misma busqueda ordena distinto segun donde
-- corra Morgan, y eso es peor que no buscar.
--
-- Se miran el texto Y el titulo. Sin lo segundo, un documento titulado
-- «Configurar el correo» no aparecia al buscar «correo» si la palabra solo
-- estaba en el titulo: el filtro lo descartaba antes de que el ranking, que si
-- premia el titulo, llegara a verlo. Filtro y ranking tienen que mirar lo mismo.

create or replace function morgan_priv.buscar_conocimiento(
    p_user_id text,
    p_raices text[],
    p_coleccion text default null,
    p_limite integer default 25
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
      and (p_coleccion is null or d.coleccion = p_coleccion)
      -- Al menos UNA de las raices, en el texto o en el titulo. Exigirlas todas
      -- dejaria sin resultados cualquier busqueda de mas de dos palabras; el
      -- ranking se encarga de poner arriba los que traen mas.
      and exists (
          select 1 from unnest(p_raices) as raiz
          where f.texto_norm like '%' || raiz || '%'
             or d.titulo_norm like '%' || raiz || '%'
      )
    limit p_limite;
$$;

-- Envoltorio en `public` para que PostgREST la alcance, con EXECUTE solo para
-- el backend. Mismo patron que `apuntar_uso`: la funcion vive en el esquema
-- privado y aqui solo se expone lo justo.
create or replace function public.buscar_conocimiento(
    p_user_id text,
    p_raices text[],
    p_coleccion text default null,
    p_limite integer default 25
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
        p_user_id, p_raices, p_coleccion, p_limite
    );
$$;

-- CREATE FUNCTION concede EXECUTE a PUBLIC por defecto. Sin este revoke, la
-- funcion quedaria al alcance de cualquiera con la clave publicable — y recibe
-- el user_id por parametro, asi que serviria para leer el conocimiento de otro.
revoke all on function public.buscar_conocimiento(text, text[], text, integer) from public;
revoke all on function public.buscar_conocimiento(text, text[], text, integer) from anon;
revoke all on function public.buscar_conocimiento(text, text[], text, integer) from authenticated;
grant execute on function public.buscar_conocimiento(text, text[], text, integer) to service_role;
