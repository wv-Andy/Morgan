-- v15 — La cuota diaria fallaba con 500 para todo usuario no exento.
--
-- `morgan_priv.apuntar_uso` se creo en v12 dentro de un esquema privado,
-- siguiendo la decision de v8c de no dejar funciones en `public`. Para
-- `es_mi_fila` es correcto: se usa DENTRO de las politicas RLS, donde el
-- esquema no importa. Para esta no: el backend la llama A TRAVES de PostgREST,
-- que solo expone `public`, asi que respondia 404 y el 404 llegaba al navegador
-- convertido en un 500 al enviar cualquier mensaje.
--
-- El propietario esta exento de cuota, asi que el fallo NO se veia probando con
-- la cuenta de siempre: aparecia justo con los usuarios nuevos.
--
-- Se resuelve con un envoltorio en `public` que no relaja nada: SECURITY
-- DEFINER para poder entrar en el esquema privado, y EXECUTE revocado a todo el
-- mundo salvo `service_role`, que es el rol con el que habla el backend. Un
-- cliente anonimo o autenticado desde el navegador sigue sin poder llamarla.

create or replace function public.apuntar_uso(
    p_user_id text,
    p_dia text,
    p_concepto text,
    p_limite integer
) returns boolean
language sql
security definer
set search_path to 'morgan_priv', 'pg_temp'
as $$
    select morgan_priv.apuntar_uso(p_user_id, p_dia, p_concepto, p_limite);
$$;

-- CREATE FUNCTION concede EXECUTE a PUBLIC por defecto. Sin este revoke, la
-- funcion quedaria al alcance de cualquiera con la clave publicable.
revoke all on function public.apuntar_uso(text, text, text, integer) from public;
revoke all on function public.apuntar_uso(text, text, text, integer) from anon;
revoke all on function public.apuntar_uso(text, text, text, integer) from authenticated;

grant execute on function public.apuntar_uso(text, text, text, integer) to service_role;

comment on function public.apuntar_uso(text, text, text, integer) is
    'Envoltorio para que PostgREST alcance morgan_priv.apuntar_uso. Solo service_role.';
