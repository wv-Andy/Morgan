# Cómo trabaja el agente: el turno, tareas, planes y verificación

[English](agente.md) · **Español**

> Junta lo que antes eran cuatro documentos: tareas (V1.5), planificación (V1.6),
> verificación (V1.7) y el streaming del turno (V2.0.14). Última revisión:
> 2026-09-25, V3.5.0.

```
Petición → (plan, si hace falta aprobarlo) → herramientas → verificación → respuesta
                                   └─ todo queda anotado en una tarea ─┘
```

## 1. El turno

El agente no ejecuta una herramienta por mensaje: **itera**. Pide una herramienta,
lee el resultado, decide si necesita otra y encadena hasta tener la respuesta. Corta
al terminar, al repetir la misma llamada con los mismos argumentos tres veces, o al
agotar el tiempo del turno.

**Lo que ya falló no se repite** (3.1). Una llamada con la misma herramienta y los
mismos argumentos que acaba de fallar **no se vuelve a ejecutar**: se le devuelve al
modelo el error de la primera vez y que pruebe otro camino. Otros argumentos, u otra
herramienta, sí se intentan; y lo que sale bien se puede repetir cuantas veces haga
falta. Medido en mi PC el 2026-09-19: `copy_file` falló por un error interno y el
modelo la repitió **cuatro veces** con los mismos argumentos hasta agotar las vueltas y
contestar «he alcanzado el límite máximo de pasos». Repetir lo que acaba de fallar da el
mismo error; decírselo le deja vueltas para buscar otra salida.

| Plazo | Local | Nube, `/chat` | Nube, `/chat/stream` (la web) |
|---|---|---|---|
| Tope del turno | 180 s | 85 s | **170 s** |
| Por qué | — | Una respuesta callada tiene que caber en los 120 s que tolera el proxy de Vercel | Con latidos no hay silencio, y lo medido llega a 180 s |

**Hay un solo agente para todos los usuarios.** Por eso todo lo que es «de este
turno» —el usuario, la medición del tiempo, el canal de eventos— vive en una
`ContextVar`, y el hilo del turno la recibe con `copy_context()`. Conectarlo al
agente compartido haría que los eventos de Ana salieran en la respuesta de Bea.

### «Detener» (3.4)

El turno **no se para** porque nadie escuche: termina en su hilo y se guarda (decisión B
del streaming). Pero el botón «Detener» de la web sí para **lo que el turno esté haciendo
en el PC**: el evento `inicio` trae un `turno`, y `POST /chat/parar` con él cancela la
orden en marcha en su siguiente punto seguro y no deja salir órdenes nuevas de ese turno
hacia el PC (`src/canal/paradas.py`). Solo el botón: una conexión que se cae (cambiar de
aplicación en el móvil) no para nada.

**Un turno a la vez por conversación (4.5).** Como el turno sigue aunque nadie escuche, un
reintento tras un corte lanzaba otro turno de lo mismo con el primero en marcha (medido en
mi prueba: una pregunta procesada 3 veces). `/chat` y `/chat/stream` marcan la
conversación como ocupada **antes** de contar el mensaje en el cupo, y la liberan cuando el
turno termina de verdad (no cuando se deja de esperarlo); mientras, otro turno en ella es
`409 TURNO_EN_CURSO`. La web espera la respuesta en vez de reintentar (web.md §3).

### El streaming: `POST /chat/stream`

Lo aprobé con tres decisiones: tope de 170 s en la nube, «Detener» deja de
mirar pero el turno termina y se guarda, y **sin texto palabra a palabra** (el
tiempo largo está en las herramientas, no en escribir la respuesta).

Una línea JSON por evento (NDJSON). Se descartaron SSE (solo admite `GET`) y
WebSocket (no está medido que el proxy de Vercel los reenvíe).

| Evento | Cuándo |
|---|---|
| `inicio` | Nada más empezar, **antes de cualquier espera**: si no llega enseguida, hay un búfer en medio |
| `pensando` | Cada vuelta al modelo |
| `herramienta` | Al empezar y terminar cada una. **Solo el nombre**: un argumento puede llevar un secreto |
| `respaldo` | Cuando contesta un modelo que no es el principal |
| `latido` | Tras 10 s sin otro evento |
| `fin` / `error` | El resultado, o `code` y `message` **sin** el texto de la excepción |

Medido con un turno real: cada evento llega ~50 ms después de emitirse y los
latidos salen cada 10 s exactos. Esa medición destapó otro defecto: una página
comprimida con gzip se leía como texto y pesaba 25.789 tokens (arreglado en la
2.0.15; el mismo turno pasó de 34,5 s y un fallo a 5,1 s).

**Quien escucha no ocupa un hilo** (V2.0.27). El generador del stream es asíncrono:
espera en el bucle de eventos y el hilo del turno lo despierta al encolar
(`CanalDelTurno.al_poner`). Cuando era síncrono, Starlette lo iteraba en su reserva
de 40 hilos —la de todas las rutas síncronas— y cada turno abierto retenía uno. La
prueba de carga lo midió: con 60 turnos largos, `/health` tardaba 9,7 s y el
`inicio` de un turno nuevo 11,5 s; después, 0,2 y 3,3 s
(mediciones.md).

La web usa **un solo `fetch`** para todo, y traduce el nombre de la herramienta a
una frase («Buscando en internet…»). Si el stream falla, enseña el error: no
reintenta con `/chat` a escondidas, porque pagaría el modelo dos veces.

## 2. Tareas

Un encargo largo deja registro: qué se intentó, con qué herramienta, qué devolvió y
qué falló. Es lo que permite a Morgan decir «no pude» en lugar de «listo».

```
pending ──► running ──► completed
             ├──► waiting ──► running
             ├──► failed ────► retry ──► running
             └──► cancelled ─► retry ──► running
```

- **Las transiciones se validan.** Sin eso, reintentar una tarea completada la
  reabriría. Máximo **tres intentos**: una tarea que falla siempre tiene que acabar
  diciéndolo.
- **Los pasos se anotan solos.** Antes el modelo los pedía con `advance_task`, y
  cada paso costaba un viaje al proveedor: un encargo de tres pasos tardaba 126 s.
  El agente ya sabe qué ejecutó; quitándolo, bajó a 106 s.
- **El progreso se calcula, no se guarda.** Una tarea completada está al 100 %
  aunque le sobraran pasos previstos.
- **Las huérfanas se cierran solas**: una tarea viva sin novedades en diez minutos
  (el turno murió) se cierra como fallida al listar.
- **No hay `update_task` genérica** ni se crean tareas por la API: permitirían
  escribir estados que no corresponden a nada ejecutado. Un conflicto de estado
  devuelve 409.

Herramientas: `create_task`, `get_task`, `list_tasks`, `complete_task`,
`fail_task`, `cancel_task`, `retry_task`. La vista **Tareas** se refresca sola
mientras hay alguna viva.

## 3. Planes: decir qué se va a hacer antes de hacerlo

**Aprobar es la orden** (4.0-A, decisión mía). Al aprobar en la web, se manda solo el
turno con `ejecutar_plan`, y **el agente ejecuta los pasos**, no el modelo: en orden, con
los argumentos aprobados, por el mismo camino que cualquier llamada (`_ejecutar_una`:
permisos, autorización del plan, verificación, anotación). **Se para en el primer paso
que no sale** (4.0.5). Si todo salió, el resumen lo escribe el núcleo («Listo: crear las
notas (comprobado).») y no se llama al modelo (4.1.5, decisión mía); si algo falló o
queda algo que explicar o preguntar, el modelo recibe el informe («1. crear las notas
(create_file): hecho, y comprobado») y se lo cuenta a la persona. Solo un plan de esta
conversación y aprobado. **Al proponerlo** pasa lo mismo (4.2): si `create_plan` deja un
plan pendiente y sin avisos, el turno lo cierra el núcleo («Te propongo este plan…»), porque
la web ya lo enseña entero encima del chat.

**El riesgo lo pone el registro de herramientas, no el modelo.** Si viniera en la
propuesta, bastaría con escribir `"riesgo": "safe"` junto a un `delete_file`. Una
herramienta desconocida cuenta como **crítica**.

| Plan | Qué pasa |
|---|---|
| Todo lectura | Nace **aprobado** y se ejecuta ya. Hacer aprobar «voy a leer tres archivos» enseña a aprobar sin mirar |
| Algún paso moderado o más | **Pendiente**: aparece arriba del chat hasta que la persona decide |

```
borrador ──► pendiente ──► aprobado ──► ejecutando ──► completado / fallido
                 └──────────────┴──► rechazado
```

- **Se aprueba en la interfaz** porque en la web no hay consola a la que
  preguntar. Y se decide sobre el trabajo entero, no sobre preguntas sueltas.
- **A quien decide se le enseñan los argumentos enmascarados** (secretos a
  `********`, recortados a 120 caracteres), pero se guardan enteros: ejecutar
  con argumentos truncados sería un fallo silencioso.
- **La conversación del plan la pone el agente**, no el modelo, que no la conoce.
  Pedírsela hacía que el plan quedara huérfano y que el turno siguiente ya no
  ofreciera `get_plan`.
- **Una regla en el prompt es una sugerencia; una respuesta en el momento del fallo
  es un camino.** El gate de la V1.6 vio a un modelo ir directo a borrar e
  ignorar la regla del prompt. Lo que funciona es que, al denegarse la
  herramienta, el sistema le diga «usa `create_plan` con estos pasos».
- Rechazado, se le dice al modelo **que no proponga uno equivalente**.
- **Un paso puede no usar herramienta** («preguntar a la persona»): los campos
  opcionales del paso admiten `null` (2.0.32). Con el esquema en solo `string`,
  Groq rechazaba la llamada entera con un 400 y el turno caía al respaldo:
  24-35 s por plan, medido (mediciones.md).
- **Si un paso usa una herramienta que aquí no existe**, se guarda igual (cuenta
  como crítica), pero `create_plan` le devuelve al modelo cuáles son para que
  rehaga el plan o lo explique. En la nube proponía `move_file` y `mkdir`.
- **En la nube el prompt dice que no hay equipo** (2.0.33): cuando el catálogo no
  tiene herramientas del equipo, se añade «Dónde trabajas: en la nube». Sin ella, a
  una persona nueva le prometía comandos «con tu autorización» y procesar CSV con
  pandas. Se decide por el catálogo, como el resto del prompt.
- **El prompt pide proponer el plan sin explorar antes**: medido, el modelo miraba
  archivos, conocimiento, memoria y repositorios «por si acaso» y encadenaba hasta
  siete llamadas.

### Lo que actúa fuera de Morgan exige plan (V2.0.16)

Una herramienta con `exige_plan = True` se ejecuta **solo** si en esta conversación
hay un plan aprobado con un paso de esa herramienta, **con los mismos argumentos**
y **sin ejecutar todavía**. Vale para todos, propietario incluido. La aprobación se
gasta solo si la herramienta funciona, y una herramienta que exige plan nunca
cuenta como segura (si no, su plan se aprobaría solo). Lo usan **las que cambian cosas
en el PC de la persona**: las cinco de escritura (3.3), `run_change_command` y
`kill_process` (3.5). Calendar, que lo estrenó, está aparcado. Probado en
`tests/test_exige_plan.py` (10 de 10 mutaciones). La web enseña los argumentos de cada
paso tal cual, con sus saltos de línea: lo que se va a escribir se aprueba viéndolo.

Rutas: `GET /planes`, `GET /planes/{id}`, `POST /planes/{id}/aprobar`,
`POST /planes/{id}/rechazar`. Uno ajeno da **404**, no 403: decir «existe pero no
es tuyo» delataría que existe.

## 4. Verificación: comprobar que pasó de verdad

`success: True` significa que **la llamada no falló**, no que el objetivo se
cumpliera. Así que tras cada herramienta el agente mira el efecto **contra el mundo
real**, sin preguntarle al modelo:

| Herramienta | Se comprueba |
|---|---|
| `create_file` | ¿Existe? ¿Tiene contenido? |
| `delete_file` | ¿Desapareció? |
| `patch_file` | ¿Está el texto nuevo? |
| `run_tests` | ¿La salida dice «failed»? |

| Veredicto | ¿Bloquea completar la tarea? |
|---|---|
| `correcto` | No |
| `incorrecto` | **Sí**, y el mensaje dice qué paso y por qué |
| `no_verificable` | No. **Y no es «correcto»**: un sistema que llama verificado a lo que no comprobó es peor que uno que no verifica |

**Si la herramienta dice éxito y el efecto dice que no, gana el efecto**: el
resultado pasa a `success: False` y el modelo puede reaccionar. En la interfaz solo
lleva sello lo comprobado.

**Copiar, mover y renombrar** también se verifican (4.0-B): la copia existe y mide lo
mismo que el original; lo movido o renombrado ya no está en el origen y sí en el destino.

**Lo que se hace en el PC de la persona se comprueba allí** (4.0.0-dev). Las herramientas
del agente local llevan `en_el_pc`, y su veredicto sale de lo que comprobó el agente en su
disco: la huella sha256 de lo escrito o la Papelera. **Nunca del disco de la nube**: hasta
entonces se miraba con `os.path.exists` en el servidor, y en producción (Render, Linux) un
archivo creado bien en el PC se daba por no creado. Desde la 4.0-B, además, el agente
dice qué comprobó (`comprobado`) al crear una carpeta, mover, ejecutar un comando que
cambia algo o terminar un proceso.

Verificado con el modelo real: ante una unidad inexistente, Morgan respondió «No
pude crear el archivo. La unidad `Z:\` no existe», en lugar de «listo».

### Corregir, con tope (4.0-C)

Decidí que, si algo que cambia cosas no sale —la herramienta falla o la verificación
dice que el efecto no está—, el modelo tiene **un** intento con otro camino, y se le dice en
ese momento. Si ese intento tampoco sale, **ningún otro cambio se ejecuta en el turno** y se
le pide que se lo cuente a la persona con lo que intentó. Consultar sigue pudiéndose, y un
intento que sale cierra el asunto. «Cambiar» es lo de riesgo moderado o más y lo que exige
plan (que sigue sin ejecutarse sin aprobarlo). Lo idéntico ya se bloqueaba desde la 3.1.
Probado en `tests/test_corregir.py`.

### El contexto en el plan (4.0-D)

Un plan aprobado se ejecuta tal cual, así que **lo que hace falta para escribirlo se lee
antes**, sin plan: un adjunto, un archivo, un documento de su conocimiento. `create_plan`
rechaza un plan con una consulta antes de un cambio (`CONSULTAS` en
`src/tools/planificacion.py`): el cambio esperaría un dato que nunca le llega, y el modelo
rellenaba el hueco con un marcador que acababa en el disco. Una consulta después de los
cambios, o un plan solo de consultas, no se tocan. Y en el turno que ejecuta un plan
aprobado que salió entero bien, no se puede proponer otro: se repetía lo ya hecho.
Probado en `tests/test_contexto_en_el_plan.py`; medido en `mediciones.md`.

## Lo que aún no hace

- **No aprende de los rechazos** de una conversación a otra.

## Pruebas

| Fichero | Qué fija |
|---|---|
| `tests/test_tareas.py` | Estados, transiciones, anotación automática |
| `tests/test_planificacion.py` | Que el riesgo escrito por el modelo se ignora, estados, aprobación |
| `tests/test_planes_de_la_conversacion.py` | Que el agente ata el plan a su conversación, por el bucle real |
| `tests/test_exige_plan.py` | Mismos argumentos, una vez, esta conversación |
| `tests/test_verificacion.py` | Que no verificable no es correcto ni fallo |
| `tests/test_ejecutar_plan.py` | El plan aprobado se ejecuta solo, con sus argumentos (4.0-A) |
| `tests/test_corregir.py` | Un intento con otro camino, y ni uno más (4.0-C) |
| `tests/test_contexto_en_el_plan.py` | Consultar antes de planificar; ningún plan repetido tras ejecutar (4.0-D) |
| `tests/test_memoria_por_persona.py` | La memoria de una cuenta nunca va en los turnos de otra (4.0) |
| `tests/test_chat_stream.py` | Orden de eventos, latido, aislamiento entre dos usuarios, error sin excepción |
