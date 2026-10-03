-- Huella del esquema de Morgan en Supabase: una línea por aspecto, con su md5.
--
-- Para comprobar que dos proyectos tienen EXACTAMENTE el mismo esquema: se ejecuta
-- en los dos y se comparan las siete huellas. Se usó el 2026-09-18 para verificar
-- que el proyecto de prueba de carga (morgan-carga) era copia fiel de producción.
-- Solo lee el catálogo: ni una fila de datos de nadie.
--
-- Mide: columnas (con tipo, nulabilidad y valor por defecto), restricciones,
-- índices, políticas RLS, qué tablas tienen RLS activo, funciones de public y
-- morgan_priv (código y permisos) y los buckets de Storage.
-- OJO: en una función, los comentarios forman parte de su código guardado.

with partes as (
  select 'col' as k, string_agg(format('%s.%s:%s:%s:%s', table_name, column_name, data_type, is_nullable, coalesce(column_default,'')), '|' order by table_name, column_name) as v
    from information_schema.columns where table_schema = 'public'
  union all
  select 'con', string_agg(format('%s:%s', conrelid::regclass, pg_get_constraintdef(oid)), '|' order by conrelid::regclass::text, pg_get_constraintdef(oid))
    from pg_constraint where connamespace = 'public'::regnamespace
  union all
  select 'idx', string_agg(indexdef, '|' order by indexdef) from pg_indexes where schemaname = 'public'
  union all
  select 'pol', string_agg(format('%s:%s:%s:%s:%s', tablename, policyname, cmd, qual, with_check), '|' order by tablename, policyname) from pg_policies where schemaname = 'public'
  union all
  select 'fun', string_agg(format('%s.%s(%s):%s:%s', n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), md5(p.prosrc), coalesce(array_to_string(p.proacl, ','), '')), '|' order by n.nspname, p.proname)
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in ('public', 'morgan_priv')
  union all
  select 'rls', string_agg(format('%s:%s', relname, relrowsecurity), '|' order by relname) from pg_class where relnamespace = 'public'::regnamespace and relkind = 'r'
  union all
  select 'bucket', string_agg(format('%s:%s', id, public), '|') from storage.buckets
)
select k, md5(coalesce(v,'')) as huella from partes order by k
;
