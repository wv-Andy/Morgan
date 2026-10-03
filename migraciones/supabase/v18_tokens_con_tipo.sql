-- v18 — Verificacion del correo (V1.9). Espeja la migracion 17 de SQLite.
--
-- Se REUTILIZA la tabla de tokens de recuperacion en lugar de crear otra. Un
-- token de verificacion es exactamente lo mismo: un secreto de un solo uso,
-- atado a un usuario, con caducidad. Duplicar la tabla habria duplicado tambien
-- su limpieza, su comprobacion de caducidad y sus propiedades de seguridad — y
-- son justo las cosas que no conviene tener por duplicado.
--
-- El nombre de la tabla se queda como esta. Renombrarla en produccion, con
-- datos vivos, no vale lo que cuesta.
--
-- El valor por defecto 'reset' importa: las filas que ya existen son todas de
-- recuperacion, y sin el quedarian con tipo nulo y dejarian de encontrarse.
alter table public.password_reset_tokens
    add column if not exists tipo text not null default 'reset';

-- Las consultas filtran SIEMPRE por tipo. Sin este indice, cada verificacion
-- recorreria tambien los tokens de recuperacion.
create index if not exists idx_tokens_tipo
    on public.password_reset_tokens (tipo, token_hash);

comment on column public.password_reset_tokens.tipo is
    'reset (recuperar contrasena) o verificacion (confirmar el correo). NO son intercambiables: uno abre la cuenta y el otro solo confirma una direccion.';
