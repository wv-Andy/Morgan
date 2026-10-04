# Morgan API (REST)

[English](api.md) · **Español**

Servicio FastAPI que expone el Core de Morgan. Documentación interactiva en `/docs` (Swagger)
y `/redoc`; esquema OpenAPI en `/openapi.json`.

## Arranque

```bash
python -m src.api.server
# o
uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

Configurable con `MORGAN_API_HOST`, `MORGAN_API_PORT`, `MORGAN_API_RELOAD` y
`MORGAN_CORS_ORIGINS` (por defecto sólo el servidor de desarrollo de Vite, no `*`).

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Health check básico (estado, versión, timestamp). |
| `GET` | `/status` | Diagnóstico por subsistema: core, database, tools, permissions, llm. Indica modo `normal` o `degraded`. |
| `POST` | `/chat` | Envía un mensaje al agente y ejecuta el bucle de razonamiento. |
| `POST` | `/chat/stream` | Lo mismo, contando el progreso mientras pasa: una línea JSON por evento (NDJSON). Es lo que usa la web. |
| `POST` | `/chat/parar` | El botón «Detener» (3.4): con el `turno` del evento `inicio`, cancela lo que ese turno hace en el PC de la persona, en su siguiente punto seguro, y no deja salir órdenes nuevas hacia el PC. Solo lo para su dueño. El turno en sí termina y se guarda. |
| `GET` | `/tools` | Catálogo de herramientas; filtro opcional `?category=`. |
| `GET` | `/tools/{name}` | Metadatos y JSON Schema de una herramienta. |
| `POST` | `/tools/{name}` | Ejecución directa de una herramienta (pasa por permisos y auditoría). |
| `GET` | `/memory` | Lista recuerdos; filtros `?query=` y `?category=`. |
| `POST` | `/memory` | Guarda o actualiza un recuerdo. |
| `DELETE` | `/memory/{key}` | Elimina un recuerdo por clave. |
| `GET` | `/audit` | Registros de auditoría; `?limit=` entre 1 y 500 (por defecto 50). |
| `GET` | `/sessions` | Lista las conversaciones por recencia; `?limit=` y `?offset=`. |
| `POST` | `/sessions` | Crea una conversación; genera el id si no se indica. |
| `GET` | `/sessions/{id}` | Detalle de una conversación, con su número de mensajes. |
| `GET` | `/sessions/{id}/messages` | Mensajes más recientes, en orden cronológico. |
| `DELETE` | `/sessions/{id}` | Elimina la conversación y todos sus mensajes. |
| `PATCH` | `/sessions/{id}` | Renombra, archiva, fija o la mueve de espacio (`espacio_id`; `""` es General). Lo que no se envía no se toca. `404 ESPACIO_DESTINO_NO_ENCONTRADO` si el espacio no es tuyo. |
| `GET` | `/espacios` | Los espacios de trabajo de quien pide, con `max_instrucciones`. |
| `POST` | `/espacios` | Crea uno (`nombre`, `instrucciones`). `409 ESPACIO_DUPLICADO` si el nombre ya existe. |
| `GET` | `/espacios/{id}` | Un espacio. |
| `PATCH` | `/espacios/{id}` | Cambia nombre o instrucciones. |
| `DELETE` | `/espacios/{id}` | Lo borra; sus conversaciones, archivos y documentos vuelven a General. |
| `GET` | `/settings` | Perfil y preferencias del usuario. |
| `PUT` | `/settings` | Los guarda. Un campo vacío **borra** ese dato. |
| `POST` | `/uploads` | Sube un archivo. `422` con el motivo si se rechaza. |
| `GET` | `/uploads` | Lista lo subido, con los límites vigentes. |
| `GET` | `/uploads/{id}/contenido` | Descarga el archivo tal cual (3.1-E): siempre como adjunto y con `nosniff`, solo el propio. |
| `DELETE` | `/uploads/{id}` | Borra un archivo subido, índice y contenido. |
| `GET` | `/tasks` | Lista las tareas; `?session_id=` y `?solo_activas=`. |
| `GET` | `/tasks/{id}` | Detalle con pasos, progreso y resultado. |
| `POST` | `/tasks/{id}/cancel` | Cancela una tarea. `409` si ya había terminado. |
| `POST` | `/tasks/{id}/retry` | Reintenta una fallida o cancelada. `409` si no procede. |
| `DELETE` | `/tasks/{id}` | Elimina una tarea. |
| `POST` | `/auth/registro` | Crea una cuenta y deja la sesión iniciada. |
| `POST` | `/auth/login` | Inicia sesión. `401` si falla; **`429`** si hay bloqueo por intentos. |
| `POST` | `/auth/logout` | Cierra la sesión en el servidor y borra las cookies. |
| `GET` | `/auth/yo` | Quién hace la petición. **Nunca devuelve 401.** |
| `POST` | `/auth/recuperar` | Pide el enlace de recuperación. Responde igual exista o no la cuenta. |
| `POST` | `/auth/restablecer` | Cambia la contraseña con el token del correo. |
| `POST` | `/auth/password` | Cambia la contraseña sabiendo la actual. |
| `GET` | `/auth/sesiones` | Dónde hay sesión abierta. |
| `POST` | `/auth/sesiones/cerrar-otras` | Cierra el resto de sesiones. |
| `POST` | `/auth/verificar` | Confirma el correo con el token del enlace. |
| `POST` | `/auth/verificar/reenviar` | Reenvía el enlace de verificación. |
| `GET` | `/auth/datos` | Exporta todo lo que Morgan guarda de ti. |
| `DELETE` | `/auth/cuenta` | Borra la cuenta y sus datos. Exige la contraseña. |
| `GET` | `/auth/tokens` | Tus tokens de API vivos, sin su valor. Solo con sesión. |
| `POST` | `/auth/tokens` | Crea un token (`nombre`, `alcances`, `dias`) y devuelve su valor **una sola vez**. Solo con sesión. |
| `DELETE` | `/auth/tokens/{id}` | Revoca un token: deja de valer en la petición siguiente. |
| `POST` | `/auth/tokens/revocar-todos` | Revoca todos los tuyos. |
| `GET` | `/auth/permiso-automatico` | Si lo verde y amarillo se aprueba solo (4.6). Solo con la sesión de la web. |
| `PUT` | `/auth/permiso-automatico` | Lo enciende o lo apaga (`{"encendido": true}`); queda en la auditoría. |
| `POST` | `/auth/agentes/codigo` | Código para emparejar un PC (agente local, 3.0): 10 minutos, un solo uso. Solo con sesión. |
| `GET` | `/auth/agentes` | Tus equipos emparejados, sin su credencial. Desde la 3.7, de cada uno si está **conectado ahora** y qué capacidades ofrece |
| `DELETE` | `/auth/agentes/{id}` | Revoca un equipo: no puede volver a conectarse. |
| `POST` | `/auth/agentes/{id}/abrir-ajustes` | Abre «Morgan en tu PC» en ese equipo (4.17). **No cambia nada**: la política se cambia en el PC. `409 NO_CONECTADO` si no está conectado (o es de otra persona) y `409 AGENTE_ANTIGUO` si su agente es anterior a la 4.17 |
| `GET` | `/auth/agentes/{id}/ordenes` | El historial de órdenes de un equipo tuyo (3.7): qué se pidió, cuándo, cómo acabó y cuánto tardó. **Nunca argumentos ni contenido.** `?dias=` hasta 30. Uno de otra persona, 404 |
| `POST` | `/agente/emparejar/consultar` | Lo llama el agente: con qué cuenta se emparejaría un código. No lo gasta. |
| `POST` | `/agente/emparejar/confirmar` | Lo llama el agente: canjea el código por su `agent_id` y su credencial (`mga_…`, una sola vez). |
| `POST` | `/agente/desemparejar` | El agente se revoca a sí mismo, con su credencial en `Authorization`. |
| `GET` | `/agente/historial` | El agente pide **su** historial (3.7), con su credencial, para cruzarlo con su diario (`python -m src.agente cruzar`). `?horas=` |
| `POST` | `/agente/rotar` | El agente pide una credencial nueva con la actual (3.8). Queda **pendiente**: la vieja vale hasta que se conecta con la nueva |
| `GET` | `/agente/actualizacion` | La última versión publicada del agente: el manifiesto **tal cual se firmó** y mi firma (3.8). Con la credencial del agente o `X-Morgan-Codigo` (un código de emparejamiento vivo, que no se gasta) |
| `GET` | `/agente/paquete` | El paquete de esa versión (zip). Mismas credenciales. La nube solo lo aloja: lo comprueba el agente |
| `GET` | `/agente/instalar.ps1` | El instalador para Windows (3.8). Público: no lleva secretos y no hace nada sin un código vivo |
| `GET` | `/admin/yo/permisos` | Qué permisos tiene quien pregunta. |
| `GET` | `/admin/usuarios` | Lista las cuentas. Exige `users.read`. |
| `POST` | `/admin/usuarios/{user_id}/rol` | Cambia el rol. Nadie se eleva a sí mismo. |
| `POST` | `/admin/usuarios/{user_id}/estado` | Activa o suspende una cuenta. |
| `GET` | `/integraciones` | Servicios conectables y su estado. **No exige cuenta**, para que el panel pueda explicarse. |
| `POST` | `/integraciones/{servicio}/conectar` | Emite el `state` y devuelve a dónde ir. Exige cuenta. |
| `GET` | `/integraciones/{servicio}/callback` | La vuelta del servicio externo. Abierta, y el `state` dice de quién es. |
| `DELETE` | `/integraciones/{servicio}` | Desconecta y le pide al servicio que invalide el token. |
| `GET` | `/planes` | Planes de la conversación; `?session_id=`. |
| `GET` | `/planes/{plan_id}` | Detalle de un plan con sus pasos. |
| `POST` | `/planes/{plan_id}/aprobar` | Aprueba un plan para que se ejecute. |
| `POST` | `/planes/{plan_id}/rechazar` | Lo rechaza, con motivo opcional. |
| `GET` | `/automatizaciones` | Tus automatizaciones (4.14): qué hacen, cuándo toca la próxima y cómo fue la última. **No hay ruta para crearlas**: se piden en el chat, con un plan rojo que apruebas. |
| `POST` | `/automatizaciones/{automatizacion_id}/pausar` | La pausa. |
| `POST` | `/automatizaciones/{automatizacion_id}/reanudar` | La reanuda **desde ahora** (lo que tocó mientras estaba pausada no se recupera); `409 DEMASIADAS` con 10 activas. |
| `DELETE` | `/automatizaciones/{automatizacion_id}` | La borra. |
| `GET` | `/avisos` | La bandeja: lo que contó cada ejecución (hecha, no salió o saltada) y cuántos sin leer. |
| `GET` | `/avisos/sin-leer` | Solo el número sin leer, para la navegación (la web lo mira cada minuto). |
| `POST` | `/avisos/leidos` | Marca como leídos los de `ids`, o todos sin `ids`. |
| `POST` | `/automatizaciones/reloj` | **La llama el reloj de Supabase** (`pg_cron`), no la web: sin sesión, con la cabecera `X-Morgan-Reloj` (`MORGAN_RELOJ_SECRETO`); sin ese secreto configurado, `404`. Contesta enseguida y ejecuta lo que toque en otro hilo. |
| `POST` | `/uploads/{upload_id}/transcripcion` | Transcribe un audio subido para revisarlo antes de enviarlo. |
| `GET` | `/diagnostico/goteo` | Emite una línea por segundo. **Instrumento de medida**, exige `system.manage`. |
| `GET` | `/diagnostico/origen` | Cómo ve Morgan el origen de tu petición (tu IP y la cadena de proxies). **Pública y sin escrituras**: mide el caso sin sesión (auditoría 2.3). |

**Espacios de trabajo (V2.2).** La cabecera `X-Morgan-Espacio: <id>` dice en qué
espacio se trabaja; sin ella, «General». Afecta a `/sessions` (lista y creación),
`/uploads` y al conocimiento. El middleware comprueba que el espacio es de quien
pide: uno inventado o ajeno responde `404 ESPACIO_ACTUAL_NO_ENCONTRADO`, nunca cae
a General en silencio. `GET /sessions?espacio=todos` lista las de todos los
espacios. En `/chat` manda el espacio **de la conversación guardada**, no la
cabecera; la cabecera solo decide dónde nace una conversación nueva. Detalle en
[datos.md](datos.es.md).

**`ejecutar_plan`** (4.0), en el cuerpo de `/chat` y `/chat/stream`: el id de un plan de
esta conversación que la persona acaba de aprobar. Morgan ejecuta sus pasos, con los
argumentos aprobados, antes de que hable el modelo, y cuenta el resultado. Un plan de otra
conversación, sin aprobar o ya ejecutado no hace nada.

**`/chat/stream` (V2.0.14)** acepta el mismo cuerpo que `/chat` y responde
`application/x-ndjson`, un objeto por línea:

| `tipo` | Cuándo | Campos |
|---|---|---|
| `inicio` | Nada más empezar, antes de cualquier espera | `t` |
| `pensando` | Al empezar cada vuelta al modelo | `vuelta`, `t` |
| `herramienta` | Al empezar y al terminar cada una | `nombre`, `estado` (`empieza`, `ok`, `fallo`), `t`. **Sin argumentos** |
| `respaldo` | Contestó un proveedor que no es el principal | `proveedor`, `t` |
| `latido` | 10 s sin ningún otro evento | `t` |
| `fin` | Al terminar | Los de `/chat`: `success`, `response`, `model`, `elapsed_seconds`, `etapas` |
| `error` | Si el turno falla | `code`, `message`. Nunca el texto de la excepción |

Lo que falla **antes** de empezar —modo degradado, un adjunto que no existe, el
cupo— llega como un error normal con su código HTTP, no dentro del stream. El
turno tiene un tope de 170 s en la nube (85 en `/chat`) y no hay `http_deadline`:
el proxy de Vercel solo corta el silencio. Si la conexión se cierra, el turno
**termina y se guarda**. Diseño en [agente.md](agente.es.md).

**En `/sessions`**, el parámetro `archived` toma tres valores: `false` (solo activas,
por defecto), `true` (solo archivadas) y `all`. Un booleano no serviría: hacen falta
tres estados y HTTP no transporta `None`.

**Las rutas que cambian algo exigen la cabecera `X-Morgan-CSRF`** con el valor de
la cookie `morgan_csrf`, salvo las de `/auth` que sirven para entrar. Ver
[autenticacion.md](autenticacion.es.md#csrf).

**`/health` y `/status` quedan abiertos sin sesión.** `/status` es lo que la
interfaz consulta para saber si el backend vive: protegerlo hace que la propia
pantalla de acceso diga «API desconectada».

**No se pueden crear tareas por la API.** Crearlas y hacerlas avanzar es trabajo del
agente; exponerlo aquí permitiría escribir estados que no corresponden a nada
ejecutado.

**`/diagnostico/goteo` no es funcionalidad, es un instrumento.** Retiene la
conexión hasta tres minutos emitiendo una línea por segundo, y existe para medir
dónde corta el proxy (roadmap-2.0.md, 2.0-D). Exige permiso de
sistema porque, abierta al público, una petición que retiene una conexión tres
minutos es una forma barata de agotar las del plan gratuito.

**Esta tabla está completa, y hay una prueba que lo mantiene así.** Documentaba
20 de las 45 rutas —faltaban las de administración, las de integraciones, las de
planes y cuatro de cuenta— y una tabla incompleta es peor que ninguna: quien la
lee concluye que lo que no aparece no existe. `test_api_documentada.py` falla si
se añade una ruta y no se añade aquí.

**Y un WebSocket**, fuera de la tabla porque `/openapi.json` solo describe HTTP:
`/agente/canal`, el canal del agente local (3.0-D), con la credencial `mga_…` en
`Authorization`. **Protocolo 3** desde la 3.4: además de `orden`, `resultado` y
`fragmento`, el agente cuenta cada `estado` de una orden y la nube puede `cancelar` una y
`consultar` por ella tras un corte. Protocolo y códigos de cierre en
agente-local.md y
§15bis.

## Formato de error

Todas las excepciones se normalizan:

```json
{
  "success": false,
  "error": {
    "code": "TOOL_NOT_FOUND",
    "message": "La herramienta 'foo' no existe en el registro.",
    "details": null
  }
}
```

Códigos habituales: `VALIDATION_ERROR` (422), `PERMISSION_DENIED` (403), `TOOL_NOT_FOUND` /
`MEMORY_KEY_NOT_FOUND` (404), `INVALID_ARGUMENTS` (400), `DEGRADED_MODE` (503),
`AGENT_EXECUTION_ERROR` / `TOOL_EXECUTION_ERROR` / `STORAGE_ERROR` / `INTERNAL_SERVER_ERROR` (500),
`UNAUTHORIZED` (401), `SESSION_NOT_FOUND` (404).

Con un token de API: `TOKEN_INVALIDO` y `TOKENS_DESACTIVADOS` (401),
`TOKEN_NO_PERMITIDO` y `ALCANCE_INSUFICIENTE` (403). Qué hacer con cada uno, en
[autenticacion.md](autenticacion.es.md#tokens-personales-de-api-v2040).

## Modo degradado

Si no hay ninguna API key de LLM válida, el `CoreContainer` arranca sin agente:
`/chat` responde **503 DEGRADED_MODE**, mientras que `/health`, `/status`, `/tools`,
`/memory`, `/audit`, `/sessions`, `/settings`, `/uploads` y `/tasks` siguen
operativos. `/status` lo refleja en `mode: "degraded"`.

## Notas de seguridad

- **La credencial de un agente local (`mga_…`) no vale en la API**, y un token personal
  no vale en las rutas del agente. Ver agente-local.md.
- **Un cliente que no es el navegador entra con un token personal**:
  `Authorization: Bearer mgn_...`, creado en `POST /auth/tokens`. Sin CSRF, con
  alcances (`chat`, `lectura`, `escritura`) y sin acceso nunca a `/auth` (salvo
  `GET /auth/yo`), `/admin`, `/integraciones` ni `/diagnostico`. Ver
  [autenticacion.md](autenticacion.es.md#tokens-personales-de-api-v2040).
- **En la nube hace falta cerrar el despliegue de una de dos formas**: cuentas
  (`MORGAN_REQUIRE_AUTH`, lo que usa producción) o un token compartido
  (`MORGAN_API_TOKEN`, para un Morgan privado). Sin ninguna de las dos, el arranque
  aborta. `/health`, `/status` y la raíz quedan abiertas. Ver
  [autenticacion.md](autenticacion.es.md#cuándo-se-exige-cuenta).
- **CORS se registra el último a propósito.** Starlette aplica por fuera el último
  middleware, y CORS tiene que envolver también a la autenticación: un 401 sin
  cabeceras CORS lo bloquea el navegador, el cliente nunca llega a leerlo y concluye
  que no hay backend en vez de pedir el token. Fijado por
  `test_el_cors_es_el_middleware_mas_externo`.
- **Permisos sin consola**: la API construye su `PermissionManager` con
  `interactive=False`, así que las herramientas `moderate`, `high_risk` y `critical` se
  **deniegan de inmediato** vía HTTP en lugar de bloquear el hilo esperando una
  confirmación que nadie puede dar (ver ADR-007). Para permitirlas hay que usar
  `PermissionManager.allow_tool()` o `MODERATE_PERMISSION_MODE=auto` (esto último sólo
  eleva el nivel `moderate`).
- **Validación de argumentos**: `POST /tools/{nombre}` valida los argumentos contra el
  JSON Schema de la herramienta antes de evaluar permisos, igual que el bucle del agente.
- **Sesiones**: `session_id` en `/chat` sí se usa. Cada sesión tiene su propio historial
  y su propio lock, de modo que dos clientes concurrentes no mezclan conversaciones. Si
  se omite, se emplea la sesión `default`.
