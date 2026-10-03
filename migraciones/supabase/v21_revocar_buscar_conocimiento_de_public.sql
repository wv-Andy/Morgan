-- v21 — Un permiso de mas: `buscar_conocimiento` era ejecutable por PUBLIC.
--
-- Lo encontro la auditoria de la V2.0.2 comparando las tres funciones del
-- esquema privado. Dos tenian revocado el acceso a PUBLIC y una no:
--
--   es_mi_fila            postgres=X/postgres
--   apuntar_uso           postgres=X/postgres
--   buscar_conocimiento   (por defecto: PUBLIC puede)       <-- esta
--
-- Y `buscar_conocimiento` es `security definer` y recibe `p_user_id` **como
-- parametro, sin comprobar que sea quien llama**. O sea que quien pueda
-- ejecutarla puede leer el conocimiento de cualquiera pasando su identificador.
--
-- NO ERA EXPLOTABLE, y conviene decirlo con precision para no exagerar el
-- hallazgo: el esquema `morgan_priv` no tiene USAGE para `anon` ni para
-- `authenticated`, y no se puede llamar a una funcion de un esquema al que no
-- tienes acceso. Comprobado:
--
--   morgan_priv   anon_usage: false   auth_usage: false
--
-- Lo que lo convierte en un descuido y no en una decision es justamente que a
-- sus dos hermanas si les revocaron PUBLIC. Alguien lo hizo en dos de tres.
--
-- Lo que esto cierra es el escenario de un solo paso: el dia que alguien haga
-- un `grant usage on schema morgan_priv` para depurar algo, esa concesion
-- pasaria de inofensiva a ser una lectura cruzada del conocimiento de todos.
-- Defensa en profundidad significa que haga falta equivocarse dos veces.
--
-- `service_role` sigue pudiendo, que es lo que Morgan usa. El envoltorio de
-- `public.buscar_conocimiento` ya estaba bien: postgres y service_role.

revoke execute on function morgan_priv.buscar_conocimiento(
    text, text[], text, integer
) from public;

-- Explicito, para que no dependa de los permisos por defecto de la base.
grant execute on function morgan_priv.buscar_conocimiento(
    text, text[], text, integer
) to service_role;
