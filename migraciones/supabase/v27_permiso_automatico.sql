-- v27 — El permiso automatico (4.6, pedido por mí el 2026-09-30).
--
-- Con el encendido, lo verde y amarillo (safe, low_risk, moderate) se aprueba y se
-- ejecuta solo; lo rojo sigue esperando a la persona. Vive en la cuenta y no en la
-- memoria: el modelo puede escribir recuerdos y no puede escribir aqui. Solo lo cambia
-- la ruta de Ajustes, con la sesion de la web. Ver src/identidad/permiso_automatico.py.
--
-- Aditiva: el codigo que corre hoy en produccion no conoce esta columna y sigue igual.
-- Las politicas no cambian: la tabla ya tiene RLS activo.

alter table public.morgan_users
    add column if not exists permiso_automatico boolean not null default false;
