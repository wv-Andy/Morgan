# Datos: base, memoria, conocimiento, espacios y sincronización

> Junta lo que antes eran cinco documentos. Última revisión: 2026-09-30, V4.6.0-dev,
> con 23 migraciones de SQLite y las de Supabase hasta la v27 (tokens de API,
> agentes locales, el historial de sus órdenes, la rotación de sus credenciales y el
> permiso automático). Lo que guarda el agente **en el PC de la persona**, en el §7.

## 1. Dónde viven los datos

| Entorno | Almacén principal | Por qué |
|---|---|---|
| `local` (por defecto) | **SQLite**, un fichero en `data/` | Tu equipo: funciona sin conexión y los datos no salen de él |
| `cloud` | **Supabase** | El disco de Render es efímero |

El Core **nunca** ve SQL ni el cliente de Supabase: habla con repositorios
(`src/memory/repositories.py`) y recibe dataclases. Cambiar de proveedor es
implementar las interfaces y construir la fábrica en `src/api/dependencies.py`,
que desde la 2.0.11 es el único sitio.

### El aislamiento hay que implementarlo en cada proveedor

La interfaz **no obliga** a filtrar por usuario. La versión de Supabase estuvo
desde la V1.6 sin filtrar nada salvo los planes: en la nube, cada usuario veía lo
de todos ([web.md](web.md#5-lo-que-rompía-la-web-en-producción)). Reglas al
escribir un proveedor:

- **El usuario se lee del contexto, no se recibe por parámetro** (`usuario_actual()`,
  `_mio()`): si fuera un argumento, un método nuevo podría olvidarlo y compilar.
- Toda lectura, actualización y borrado lleva el filtro, **`get()` incluido**.
- Toda escritura deja el `user_id` puesto.
- Los `on_conflict` nombran restricciones que existen (las claves son compuestas con `user_id`).

`tests/test_supabase_aislamiento.py` recorre las 32 operaciones con un cliente de
mentira y falla si aparece un método que no está en su lista.

## 2. SQLite

Conexión en `src/memory/db.py`, con un context manager que **siempre cierra** (antes
de la V1.0 no cerraba). Cada bloque es una transacción. PRAGMAs: `WAL` (la API es
multihilo), `foreign_keys=ON`, `busy_timeout`. Todas las consultas son
parametrizadas. Cada conversación tiene además su lock en memoria para que dos
peticiones suyas se atiendan en orden.

### Tablas

| Tabla | Qué guarda | Detalle que importa |
|---|---|---|
| `sessions` | Conversaciones | Clave `(user_id, id)` desde la v12. Archivar y renombrar **no** tocan `updated_at`. `espacio_id` NULL = General |
| `messages` | Mensajes | `ON DELETE CASCADE`. Índice por sesión, que es el acceso dominante |
| `memories` | Hechos que Morgan recuerda | `UNIQUE(user_id, key)` desde la v9: antes el recuerdo de una persona pisaba el de otra |
| `uploads` | Metadatos de archivos subidos | El id lo genera Morgan, nunca el nombre del usuario; MIME por contenido; caducan a las 24 h |
| `tasks`, `planes` | [Tareas y planes](agente.md) | Los pasos en una columna JSON: siempre se leen con su tarea |
| `conocimiento`, `conocimiento_fragmentos` | Documentos consultables | Ver abajo |
| `espacios` | Espacios de trabajo | Nombre único por usuario, instrucciones hasta 2.000 caracteres |
| `integraciones`, `oauth_estados` | Servicios conectados | Token cifrado ([integraciones.md](integraciones.md)) |
| `morgan_users`, `auth_sessions`, `password_reset_tokens`, `login_intentos`, `uso_diario` | Cuentas | Sesiones y tokens **hasheados**. Se llama `auth_sessions` porque `sessions` son las conversaciones ([autenticacion.md](autenticacion.md)) |
| `api_tokens` | Tokens personales de API (v19; Supabase v23) | Solo su hash; caducan a los 90 días ([autenticacion.md](autenticacion.md#tokens-personales-de-api-v2040)) |
| `ordenes_agente` | El historial de órdenes a cada PC (v21; Supabase v25, 3.7) | Qué, cuándo, cómo acabó y cuánto tardó: **nunca argumentos ni contenido**. 30 días |
| `agentes.credencial_nueva_hash`, `credencial_rotada_en` | La rotación de la credencial de cada PC (v22; Supabase v26, 3.8) | La nueva, **pendiente** hasta que el agente se conecta con ella; solo su hash |
| `automatizaciones` | Las órdenes con horario (v24; Supabase v28, 4.14) | Qué hacer (`instruccion`), cuándo (`horario` y `zona`, la del navegador al crearla), la próxima (`proxima`), si usa el PC, y cómo fue la última. `reclamo` sube con cada ejecución: solo la lanza quien lo sube. `pasos` (v25; Supabase v30, 4.15): los pasos fijos aprobados, si cambia algo |
| `avisos` | La bandeja (v24; Supabase v28, 4.14) | Lo que contó cada ejecución: hecha, no salió o saltada, y qué herramientas usó. 30 días |
| `morgan_users.permiso_automatico` | El permiso automático (v23; Supabase v27, 4.6) | Lo verde y amarillo se aprueba solo. En la cuenta y **no en la memoria**: el modelo escribe recuerdos y no puede escribir aquí. Solo lo cambia Ajustes → Permisos, con la sesión de la web |
| `sync_queue` | Cola hacia la nube | Solo en local |
| `schema_version` | Migraciones aplicadas | |

### Migraciones

En la lista `MIGRATIONS` de `db.py`; al abrir la base se aplican las que falten.
**Se añaden al final y nunca se edita una publicada**: cada instalación quedaría
distinta. Un `MORGAN_DATA_DIR` vacío crea el esquema entero al arrancar.

| v | Contenido |
|---|---|
| 1–3 | Memoria, sesiones y mensajes |
| 4–7 | Cola de sincronización, organización de conversaciones, archivos, tareas |
| 8–13 | Cuentas: `user_id` en todo, claves por usuario, cupo, autenticación propia, un solo propietario |
| 14–17 | Planes, conocimiento, integraciones, tipos de token |
| 18 | Espacios; cada grupo se convirtió en un espacio (Supabase v22) |

**Cada migración tiene su espejo en Supabase**, y desde la V1.8 se escribe también en
[`migraciones/supabase/`](../migraciones/supabase/) **en el mismo commit**. Antes no
dejaban rastro, y una función de cupo estuvo una versión entera rota sin aparecer en
ningún diff. Ese README lleva las tres comprobaciones que han fallado: lo que llama
PostgREST va en `public`, los tipos coinciden con las columnas, y se revoca el
`EXECUTE` que `CREATE FUNCTION` concede a `PUBLIC`.

### Errores y copias

Todo error se traduce a `MemoryStorageError`. **Un fallo de la base no tumba a
Morgan**: si falla persistir un turno, la respuesta se entrega igual; si falla leer
la memoria del prompt, va sin ella; si la base está corrupta, `/status` lo dice y el
resto funciona.

Copia en caliente: `sqlite3 data/morgan_memory.db ".backup 'copia.db'"`. Borrar el
fichero es seguro: se recrea vacío.

| Variable | Por defecto | Uso |
|---|---|---|
| `MORGAN_DATA_DIR` | `data/` | Dónde vive el fichero |
| `MORGAN_DB_TIMEOUT` | `10` | Segundos de espera ante bloqueo |
| `MORGAN_PERSIST_HISTORY` | `true` | Guardar y recuperar conversaciones |
| `MORGAN_HISTORY_WINDOW` | `20` | Mensajes recientes al reanudar |

### La copia de seguridad de Supabase (4.20)

El plan gratuito de Supabase no da copias descargables, así que `scripts/copia_supabase.py`
hace una **copia lógica**: las 22 tablas de `public`, enteras, en JSON, con la huella
(SHA-256) de cada una en un manifiesto. Se guarda en `~/.morgan/copias/` (fuera de OneDrive
y del repositorio: lleva correos, hashes de contraseñas y conversaciones).

```bash
python scripts/copia_supabase.py copiar                        # producción
python scripts/copia_supabase.py comprobar --desde <carpeta>   # ¿la base sigue igual?
python scripts/copia_supabase.py restaurar --desde <carpeta> --proyecto carga
```

**Rehacer la base desde cero**, si el proyecto se perdiera:

1. Un proyecto nuevo, y las migraciones en orden: `migraciones/supabase/v01_v14_esquema_inicial.sql`
   y después `v15` … `v30`. Las 14 primeras **no estaban en el repositorio** (se aplicaron
   directamente en Supabase): las recuperé de su historial en la 4.20, comprobando el MD5 de
   cada una contra el que guarda Postgres. Sin ellas, la copia no tenía dónde volver.
2. Los secretos del Vault (`morgan_reloj`, `morgan_reloj_url`), de las variables de Render
   (ver la cabecera de `v29_reloj.sql`).
3. `restaurar`: escribe las tablas en orden (las de las que dependen otras, primero) y
   comprueba antes que cada fichero es el que se copió; uno cambiado o roto no se restaura.
4. **Ajustar los contadores** con el `secuencias.sql` que deja `restaurar`. Sin eso, el
   primer mensaje nuevo chocaría con uno restaurado (mismo `id`).
5. `comprobar`: la base y la copia, tabla a tabla.

Lo que **no** va en la copia: los bytes de los archivos subidos (el bucket `morgan-uploads`;
su índice sí va, en `uploads`) y `auth.users`, que Morgan no usa (`auth_user_id` está vacío
en todas las cuentas). Restaurar sobre producción pide `--si-produccion`.

## 3. Memoria: los cinco tipos

Mezclarlos es lo que hace que un asistente arrastre basura en el contexto.

| Tipo | Qué es | Dónde | Cuándo se usa |
|---|---|---|---|
| **Sesión** | El contexto vivo | RAM | Durante la conversación |
| **Historial** | Los mensajes intercambiados | `messages` | Los 20 últimos al reanudar, sin los de herramienta |
| **Memoria** | Hechos y preferencias sobre ti | `memories` | **Siempre**, hasta 15 en el prompt |
| **Conocimiento** | Documentos | `conocimiento` | **Cuando se busca** |
| **Contexto** | Archivos adjuntos | `uploads` | En ese mensaje |

> **Nada entra solo.** Morgan no convierte lo hablado en memoria: guarda cuando usa
> `remember_fact` o cuando lo pides.

- **Al reanudar no se cargan los mensajes de herramienta**: uno sin su llamada hace
  que el proveedor rechace la petición. Y se carga una ventana, no todo, por coste.
  **Desde la 4.1.5 pasa lo mismo sin reiniciar**: al empezar cada turno, los anteriores se
  quedan en su texto (los últimos 20). Antes, en memoria, los resultados de herramientas
  de todos los turnos se reenviaban en cada llamada.
- **La memoria entra en el prompt en cada turno, para quien pregunta** (4.0): nunca en
  el prompt del agente, que es uno para todas las cuentas. Hasta la 4.0 se escribía ahí al
  guardar un recuerdo o los ajustes, y los turnos de todos llevaban la memoria de quien
  guardó el último (ver `seguridad.md` §2). Tampoco se acumula: antes de la 2.x, cada
  recuerdo añadía otra copia y el prompt crecía sin límite.
- **El mismo código en local y en la nube** desde la 2.0.13 (`MemoriaSobreRepositorio`):
  se olvida **solo por clave** (antes, en local, `forget('1')` borraba la fila 1) y
  `recall` devuelve como mucho 100. En la nube la memoria **no persistía** hasta que
  se contó filas en producción y la tabla estaba vacía.
- `forget_fact` pide confirmación. También `DELETE /memory/{clave}` y la vista Memoria.

Límites: se recupera por recencia, no por relevancia; sin resumen de conversaciones
largas; los recuerdos no se puntúan.

## 4. Conocimiento (V1.8)

Documentos que Morgan **consulta**, frente a los hechos que **recuerda**. Separarlos
evita que el prompt engorde con material que casi nunca se usa.

- **Se busca por fragmentos** de ~1.200 caracteres, **con solape** (una frase en la
  frontera no quedaría en ninguno) y cortando por párrafos.
- **Por términos, no por significado.** Los embeddings exigirían un modelo de
  vectores y una dependencia más que falla. Sin tildes, en minúsculas, por la raíz
  («configuro» encuentra «configuración»), sin palabras vacías.
- **Puntuación**: +2 en el título, +1 en el texto con rendimiento decreciente (repetir
  una palabra no envenena el ranking), multiplicado por cobertura de términos.
  Filtro y ranking tienen que mirar lo mismo: un documento titulado «Configurar el
  correo» no salía al buscar «correo».
- **Mismo título en la misma colección reemplaza**, y borra los fragmentos viejos.
- **La puntuación es la misma función en SQLite y en Supabase**; solo cambia el filtro
  grueso (en la nube, la función `buscar_conocimiento`, ejecutable solo por
  `service_role` porque recibe el usuario por parámetro).
- Sin resultados, se le dice al modelo que **no está en los documentos**, no que no
  exista. Con resultados, que cite el documento.

Herramientas: `search_knowledge`, `add_knowledge`, `list_knowledge_sources`,
`index_document`, `remove_knowledge` (pide confirmación).

## 5. Espacios de trabajo (V2.0.10)

Un proyecto: agrupa **conversaciones, archivos y documentos**, con **instrucciones
propias**. «General» es lo que no está en ninguno.

| Del espacio | Común a todos |
|---|---|
| Conversaciones, archivos, documentos, instrucciones | **La memoria** (lo decidí así: lo que Morgan sabe de ti no cambia de proyecto), tareas, planes, ajustes, **el cupo de archivos** (si no, repartir entre espacios lo saltaría) |

- **La web manda `X-Morgan-Espacio`** desde `request()`, y el middleware comprueba
  que existe **y es de quien pide**. Si no, `404 ESPACIO_ACTUAL_NO_ENCONTRADO`: nunca
  cae a General en silencio.
- **En el chat manda la conversación**: una existente se atiende en su espacio
  aunque la web tenga otro seleccionado.
- **Las instrucciones van en el prompt del turno, nunca en el del agente compartido**,
  y se siguen salvo que contradigan la seguridad.
- **Borrar un espacio no borra lo de dentro**: vuelve a General.
- `NULL` es General, así que se filtra con `IS ?` (SQLite), `is.null` (PostgREST) e
  `is not distinct from` (SQL).

## 6. Sincronización local → nube

> **La escritura local nunca espera al remoto.** Todo se guarda en SQLite y, si la
> sincronización está activa (`MORGAN_CLOUD_ENABLED`, apagada por defecto), la
> operación queda en `sync_queue`.

Cola *outbox* de hasta 5.000 entradas, 5 intentos por operación; si el remoto está
caído no se gastan intentos. En la nube no hay cola: los repositorios **son** Supabase.

**Estado real, dicho claro:**

- **Solo sube, y así se queda.** La bajada (traer a tu equipo lo que se escribió en
  la nube y fusionarlo) **la retiré el 2026-09-18**. Se aplazó a
  la V1.4 en su día y nunca se hizo; mientras tanto la nube pasó a ser donde Morgan
  vive de verdad, y con el agente local de la 3.0 será también **la fuente de verdad
  de tu equipo**: el agente trabaja contra la nube, no contra una copia local que
  haya que reconciliar. Bajar y fusionar era, además, lo más propenso a corromper
  datos de todo el diseño ([decisions.md](decisions.md)).
- **Nada llama a `SyncWorker.run_once()`** fuera de las pruebas: la cola se llena y
  nadie la vacía.
- **La memoria no se encola**: solo el historial escribe en la cola.
- **Antes de conectar un disparador**: la cola no guarda quién encoló cada entrada, y
  en un hilo de fondo todo se escribiría como el usuario `local`. Hay que guardar
  `user_id` en el payload y aplicar cada entrada dentro de `como_usuario(...)`.

El esquema conserva `updated_at`, `origin_device` y `client_id`, que se añadieron para
la bajada. No estorban y los usa la subida; no se quitan para no migrar tablas por
nada. Si algún día hiciera falta retomarla, la estrategia pensada era *last-write-wins*
guardando lo descartado.

## 7. En el PC de la persona: lo que guarda el agente local

Nada de esto sale del PC ni lo puede leer Morgan (la carpeta del agente es zona
prohibida para leer y para escribir). Vive en `%LOCALAPPDATA%\Morgan\agente\`.

| Fichero | Qué guarda | Cuánto |
|---|---|---|
| `agente.json` y la credencial | Con quién está emparejado; la credencial `mga_`, **cifrada con DPAPI** (solo ese usuario de Windows la descifra) | Hasta desemparejar |
| `politica.json` | Qué deja hacer este PC: carpetas, bloqueadas, de escritura, capacidades, programas, límites, confirmaciones | Lo cambia solo la persona, en el PC |
| `diario.jsonl` | El diario de órdenes (3.4): estado y tiempos de cada una; el resultado solo de lo que cambia cosas, **nunca lo leído** | 24 h |
| `hechas.json` | Lo hecho hace poco, para que repetir una orden no la haga dos veces (3.3.5) | 1 h |
| `respaldos/` | La versión anterior de lo que Morgan editó (3.3) | 7 días |
| `auditoria.jsonl` | Qué se recibió, rechazó o ejecutó, qué contestó la persona a una notificación y las huellas de antes y después | Crece; sin contenido de archivos |
| `agente.log` | El registro del agente (y del vigilante) cuando corre sin ventana | Tope de 1 MB |
| `salud.json` | Cómo está el agente: pulso cada 5 s, estado, último latido, cola (3.6) | Lo reescribe el agente |
| `agente.lock`, `vigilante.lock`, `parar` | Que solo corra uno de cada; la señal de parar | Mientras corren |
| `app\<versión>\`, `app\activa.json` | El agente instalado (3.8): cada versión con su entorno, y cuál es la activa, la anterior y si está por confirmar | La activa y la anterior |
| `morgan-agente.cmd` | Los comandos del agente con la versión activa (3.8) | Lo reescribe el agente al cambiar de versión |
