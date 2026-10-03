# Migraciones de Supabase

El esquema de la nube. **Está aquí porque no estarlo salió caro.**

Hasta la V1.8, estas migraciones se aplicaban directamente sobre el proyecto de
Supabase y no quedaba rastro en el repositorio. Nadie las revisaba, no aparecían
en ningún *diff*, y el resultado fue una función de cuota que llevaba una versión
entera **rota y fuera del alcance de PostgREST** sin que nada lo delatara: solo
fallaba para usuarios que no fueran el propietario, que es exactamente el caso
que no se prueba a mano.

## La regla

Toda migración aplicada a Supabase se escribe también aquí, con el mismo nombre
que se le dio, **en el mismo commit**. Un fichero de aquí que no esté aplicado, o
una migración aplicada que no esté aquí, son las dos formas de que esto deje de
servir para nada.

Para ver qué hay aplicado de verdad:

```sql
select version, name from supabase_migrations.schema_migrations order by version;
```

## Lo que hay que mirar al escribir una

Las tres cosas que han fallado, por orden de lo caras que salieron:

1. **¿La va a llamar el backend a través de PostgREST?** Entonces tiene que
   estar en `public`. PostgREST no ve `morgan_priv`, y una función que no
   encuentra responde 404 — que llega al navegador convertido en un 500 sin
   explicación. Las funciones de las políticas RLS son el caso contrario: esas
   sí van en `morgan_priv`, porque se usan *dentro* de la política.
2. **¿Los tipos coinciden con los de las columnas?** Postgres convierte un
   literal de texto a fecha, pero **no un parámetro**. `dia` es `date`, y
   recibirlo como `text` da `42804` la primera vez que se ejecuta.
3. **¿Quién puede ejecutarla?** `CREATE FUNCTION` concede `EXECUTE` a `PUBLIC`
   por defecto. Si no se revoca, queda al alcance de cualquiera con la clave
   publicable.

## Histórico anterior

Las migraciones de la v1 a la v14 se aplicaron antes de esta regla y viven solo
en Supabase. Se pueden recuperar de `supabase_migrations.schema_migrations`, que
guarda su SQL. No se copian aquí a posteriori a propósito: escribir ficheros que
nadie ha verificado que coincidan con lo aplicado sería peor que no tenerlos.

## Recrear el esquema en un proyecto nuevo

Se hizo el 2026-09-18 para el proyecto de prueba de carga (`morgan-carga`), y el
procedimiento sirve para cualquier copia:

1. **De la v1 a la v14**, que no están en ficheros: se leen de producción, **solo el
   SQL de las migraciones**, sin tocar datos:

   ```sql
   select version, name, array_to_string(statements, E';
') as sql
   from supabase_migrations.schema_migrations order by version;
   ```

   y se aplican en ese orden, con el mismo nombre.
2. **De la v15 en adelante**, los ficheros de esta carpeta, en orden.
3. **Se comprueba** con [`huella_esquema.sql`](huella_esquema.sql) en los dos proyectos:
   siete huellas (columnas, restricciones, índices, políticas, RLS, funciones y
   buckets) que tienen que coincidir una a una.

La primera vez no coincidieron las funciones, y era cosa de la copia: se habían
aplicado los ficheros sin sus comentarios, y **en una función los comentarios forman
parte del código guardado**. Mismo comportamiento, distinta huella. Se reaplicaron
las dos funciones con `pg_get_functiondef` de producción y las siete coincidieron.

