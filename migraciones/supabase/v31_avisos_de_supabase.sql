-- v31 — Los dos avisos WARN de Supabase (4.20; criterio 5 del gate de la 5.0).
--
-- 1. `pg_net` estaba en el esquema `public` (lo puso la v29): una extensión ahí expone sus
--    objetos donde vive la API. pg_net no admite `ALTER EXTENSION ... SET SCHEMA`, así que
--    se quita y se vuelve a crear en `extensions`. Sus funciones siguen en el esquema `net`
--    (`net.http_post`), que es como las llama el reloj (`morgan-reloj`): el trabajo de
--    pg_cron no cambia. Lo único que se pierde es una petición que estuviera en cola en ese
--    instante; el reloj vuelve a llamar al minuto siguiente.
-- 2. La política `usuarios_propios` de `morgan_users` evaluaba `auth.uid()` en cada fila.
--    Con `(select auth.uid())` se evalúa una vez por consulta. Hace lo mismo.
--
-- Probada antes en morgan-carga.

drop extension if exists pg_net;
create extension if not exists pg_net schema extensions;

drop policy if exists usuarios_propios on public.morgan_users;
create policy usuarios_propios on public.morgan_users
    for all to authenticated
    using (auth_user_id = (select auth.uid()))
    with check (auth_user_id = (select auth.uid()));
