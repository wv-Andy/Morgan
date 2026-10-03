-- v17 — Servicios externos conectados por cada usuario (V1.9).
--
-- Espeja la migracion 16 de SQLite. Se aplica A LA VEZ, no «cuando haga
-- falta»: dejar la mitad de la nube para despues es exactamente como se llego
-- a tener repositorios sin filtrar por usuario en produccion durante cinco
-- versiones.

create table if not exists public.integraciones (
    user_id text not null,
    servicio text not null,
    -- CIFRADO con Fernet, nunca en claro. Con un token de GitHub se puede leer
    -- codigo privado y escribir; en claro, una copia de esta tabla es una copia
    -- de las llaves. La columna se llama asi para que nadie escriba aqui un
    -- token sin cifrar por descuido.
    token_cifrado text not null,
    refresco_cifrado text,
    expira_en double precision,
    -- El nombre de la cuenta conectada. NO es secreto: es lo que se enseña para
    -- que se vea a nombre de quien actua Morgan.
    cuenta text,
    scopes text not null default '',
    creado_en double precision not null,
    actualizado_en double precision not null,
    -- El ultimo fallo al hablar con el servicio. Distingue «conectado y
    -- fallando» de «no conectado», que no son lo mismo.
    error text,
    metadatos jsonb not null default '{}'::jsonb,
    -- (user_id, servicio): dos personas pueden conectar el mismo servicio sin
    -- pisarse. Con la clave solo en `servicio`, el segundo en conectar
    -- machacaria al primero.
    primary key (user_id, servicio)
);

create index if not exists idx_integraciones_usuario
    on public.integraciones (user_id);

-- El estado del intercambio OAuth, mientras dura.
--
-- Es lo que impide el CSRF de la autorizacion: sin comprobar que el 'state'
-- que vuelve es el que se emitio, una web ajena podria completar el flujo y
-- dejar SU cuenta de GitHub conectada a la sesion de otra persona. Morgan
-- actuaria despues sobre los repositorios del atacante creyendo que son los
-- tuyos.
create table if not exists public.oauth_estados (
    estado text primary key,
    user_id text not null,
    servicio text not null,
    creado_en double precision not null,
    expira_en double precision not null
);

create index if not exists idx_oauth_estados_caducidad
    on public.oauth_estados (expira_en);

-- RLS con el mismo patron que el resto de tablas: la clave de servicio del
-- backend la salta, y cualquier cliente con la clave publicable solo ve lo
-- suyo. Que el backend filtre no quita que esto tenga que estar: son dos
-- capas, y la de aqui es la que protege si alguna consulta se olvidara.
alter table public.integraciones enable row level security;
alter table public.oauth_estados enable row level security;

drop policy if exists integraciones_propias on public.integraciones;
create policy integraciones_propias on public.integraciones
    using (morgan_priv.es_mi_fila(user_id));

-- Los estados no llevan politica por usuario a proposito: se consultan por su
-- valor —que es un secreto de 32 bytes— y precisamente para averiguar de quien
-- son. Una politica que filtrara por el usuario actual haria imposible
-- resolver la vuelta de OAuth, que es cuando todavia no se sabe quien es.
-- Sin politica, con RLS activo, ningun cliente publicable lee nada: solo la
-- clave de servicio.
