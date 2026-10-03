-- v30 — Los cambios pre-aprobados (4.15).
--
-- Los pasos fijos de una automatizacion que cambia algo (JSON: herramienta, argumentos y
-- descripcion), aprobados una vez por la persona. Cada ejecucion hace exactamente esos
-- pasos; lo unico que cambia son {fecha} y {hora}. Sin pasos, la automatizacion solo
-- consulta (4.14). Mis decisiones en docs/plan-4.x.md.
--
-- Aditiva: el codigo que corre hoy en produccion no la conoce y sigue igual.

alter table public.automatizaciones add column if not exists pasos text;
