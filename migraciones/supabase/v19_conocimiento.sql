-- v19 — El conocimiento en la nube (V1.8, completado en la V1.9).
--
-- Era lo unico de la V1.8 que quedo a medias: `AlmacenDeConocimiento` solo
-- hablaba con SQLite, asi que en la nube las herramientas de conocimiento ni se
-- registraban y la biblioteca solo existia en el Morgan de escritorio.
--
-- Separado de `memories` a proposito, igual que en SQLite: la memoria son unos
-- pocos hechos que viajan en CADA prompt; el conocimiento es material que se
-- consulta cuando viene a cuento.

create table if not exists public.conocimiento (
    id text not null,
    titulo text not null,
    titulo_norm text not null default '',
    contenido text not null,
    fuente text not null default 'manual',
    coleccion text not null default 'general',
    creado_en double precision not null,
    actualizado_en double precision not null,
    huella text,
    metadatos jsonb not null default '{}'::jsonb,
    user_id text not null default 'local',
    primary key (user_id, id)
);

create index if not exists idx_conocimiento_usuario
    on public.conocimiento (user_id, actualizado_en desc);
create index if not exists idx_conocimiento_coleccion
    on public.conocimiento (user_id, coleccion);

-- Los fragmentos son lo que se busca. Un documento entero no sirve como unidad:
-- si la respuesta esta en el parrafo 40 de 200, dar el documento completo llena
-- el contexto de lo que no hacia falta.
create table if not exists public.conocimiento_fragmentos (
    id bigserial primary key,
    documento_id text not null,
    orden integer not null,
    texto text not null,
    texto_norm text not null,
    user_id text not null default 'local',
    -- Con ON DELETE CASCADE: borrar un documento se lleva sus fragmentos, que
    -- solos no significan nada.
    constraint fk_fragmento_documento
        foreign key (user_id, documento_id)
        references public.conocimiento (user_id, id)
        on delete cascade
);

create index if not exists idx_fragmentos_documento
    on public.conocimiento_fragmentos (user_id, documento_id, orden);
create index if not exists idx_fragmentos_usuario
    on public.conocimiento_fragmentos (user_id);

alter table public.conocimiento enable row level security;
alter table public.conocimiento_fragmentos enable row level security;

drop policy if exists conocimiento_propio on public.conocimiento;
create policy conocimiento_propio on public.conocimiento
    using (morgan_priv.es_mi_fila(user_id));

drop policy if exists fragmentos_propios on public.conocimiento_fragmentos;
create policy fragmentos_propios on public.conocimiento_fragmentos
    using (morgan_priv.es_mi_fila(user_id));
