-- v16 — La funcion de cuota nunca llego a ejecutarse con exito.
--
-- `uso_diario.dia` es de tipo `date`, y la funcion recibe el dia como `text`
-- (que es lo que envia el backend: `_hoy()` produce '2026-09-07'). Postgres no
-- convierte implicitamente un PARAMETRO de texto a fecha —si lo hace con un
-- literal escrito en la consulta, que es lo que despista— y responde:
--
--   column "dia" is of type date but expression is of type text  (42804)
--
-- El fallo estaba tapado por otro: la funcion vivia en `morgan_priv`, fuera del
-- alcance de PostgREST, asi que la llamada moria antes con un 404 y este error
-- no se llegaba a ver nunca. Al arreglar el alcance (v15) aparecio este.
--
-- La firma se mantiene con `text`: el backend manda una cadena, y cambiarla
-- obligaria a tocar el codigo para no ganar nada. La conversion se hace aqui,
-- una vez, en lugar de en los tres sitios donde se usa el valor.

create or replace function morgan_priv.apuntar_uso(
    p_user_id text,
    p_dia text,
    p_concepto text,
    p_limite integer
) returns boolean
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $function$
declare
    v_filas integer;
    v_dia date := p_dia::date;
begin
    -- Lista blanca: p_concepto acaba dentro de SQL dinamico, y sin esto seria
    -- una inyeccion.
    if p_concepto not in ('mensajes', 'transcripciones', 'imagenes') then
        raise exception 'Concepto de uso desconocido: %', p_concepto;
    end if;

    insert into public.uso_diario (user_id, dia)
    values (p_user_id, v_dia)
    on conflict (user_id, dia) do nothing;

    execute format(
        'update public.uso_diario set %I = %I + 1
         where user_id = $1 and dia = $2 and %I < $3',
        p_concepto, p_concepto, p_concepto
    ) using p_user_id, v_dia, p_limite;

    get diagnostics v_filas = row_count;

    -- true: cabia y queda apuntado. false: ya estaba en el limite.
    return v_filas > 0;
end;
$function$;
