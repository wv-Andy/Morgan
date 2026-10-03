-- v29 — El reloj de las automatizaciones (4.14, mi decision 2: el reloj en Supabase).
--
-- Render (plan gratuito) se duerme a los 15 minutos sin trafico: un temporizador en el
-- servidor no despertaria a su hora. Aqui, cada minuto, pg_cron mira DENTRO de la base si
-- toca alguna automatizacion y, SOLO si toca, llama a Render con pg_net (eso lo despierta).
-- Si Render tarda en despertar y la llamada se pierde, la del minuto siguiente vuelve a
-- llamar: la automatizacion sigue pendiente hasta que alguien la reclama.
--
-- El secreto NO va en este fichero (el repositorio es publico): vive en el Vault de
-- Supabase con el nombre 'morgan_reloj', y en Render como MORGAN_RELOJ_SECRETO. Se crea
-- una vez, a mano, con:
--
--   select vault.create_secret('<el secreto>', 'morgan_reloj');
--   select vault.create_secret('https://<servicio>.onrender.com', 'morgan_reloj_url');
--
-- Para quitar el reloj: select cron.unschedule('morgan-reloj');

create extension if not exists pg_cron;
create extension if not exists pg_net;

select cron.schedule(
    'morgan-reloj',
    '* * * * *',
    $$
    select net.http_post(
        url := (select decrypted_secret from vault.decrypted_secrets where name = 'morgan_reloj_url')
               || '/automatizaciones/reloj',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'X-Morgan-Reloj', (select decrypted_secret from vault.decrypted_secrets where name = 'morgan_reloj')
        ),
        body := '{}'::jsonb,
        timeout_milliseconds := 60000
    )
    where exists (
        select 1 from public.automatizaciones
        where activa and proxima <= extract(epoch from now())
    );
    $$
);
