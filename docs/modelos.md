# Modelos: proveedores, cadena, cuota y router

> Junta lo que antes eran el documento de proveedores LLM y el del router de
> modelos. Última revisión: 2026-09-25, V3.5.0. Las mediciones que lo justifican
> están resumidas en mediciones.md.

## 1. La cadena

El Core habla con la interfaz `LLMProvider` (`src/models/base.py`); cada proveedor
traduce en su frontera. `FallbackProvider` prueba los eslabones en orden,
**filtra por capacidad** (una imagen nunca va a un modelo sin visión) y **pospone
al que se sabe agotado**.

| Eslabón | Papel | Modelo | Variable |
|---|---|---|---|
| **Groq** | Principal, gratis | `openai/gpt-oss-120b` | `GROQ_API_KEY`, `_2`, `_3`… |
| **Groq, relevo** | Si el principal se queda sin cuota (V2.0.20) | `openai/gpt-oss-20b` | `GROQ_MODELOS_RELEVO` |
| **Gemini** | Respaldo gratis (20 peticiones/día **por proyecto**). El único con visión (Groq ya no sirve modelos con visión, medido 2026-09-27) | `gemini-3.6-flash` | `GEMINI_API_KEY`, `_2`, `_3`… (4.1.5): cada una de otro proyecto suma 20 |
| **OpenAI** | **Último, y de pago** | `gpt-5.6-luna` | `OPENAI_API_KEY` |
| NVIDIA | Existe, apagado | `deepseek-v4-pro-0813` | Añadir `nvidia` a `MORGAN_LLM_ORDER` |

Orden por defecto `MORGAN_LLM_ORDER=groq,gemini,openai`, definido en un solo sitio
(`Settings.llm_order`). **La regla: el gratis más rápido primero y el de pago
último.** OpenAI no va al final por ser peor —es el más fiable, 1,0 s de texto y
1,7 s con herramientas— sino porque cobra: lo que compra es que no haya turnos sin
respuesta cuando los gratuitos se agotan.

Sin ninguna clave, Morgan arranca en **modo degradado**: `/chat` da 503 y todo lo
que no depende del modelo sigue. **Un modelo local está descartado.**

`etapas` dice **quién contestó de verdad** (antes el campo `model` devolvía la
cadena entera y afirmaba «Groq» mientras contestaba el respaldo). La web **no enseña
el modelo** desde la 2.0.29 (decisión mía): si contestó el de reserva, solo lo
explica el tiempo de la respuesta al pasar por encima, sin nombres
([web.md](web.md#2-estructura)).

## 2. Plazos

Antes de la V1.3 los clientes heredaban los plazos del SDK y un turno podía
bloquearse **más de 15 minutos**. **Acotar iteraciones no acota tiempo**: el tope que
lo garantiza es el del turno, por reloj.

| Variable | Por defecto | Acota |
|---|---|---|
| `MORGAN_LLM_TIMEOUT` | 30 s | Una llamada (la normal de Groq son 0,42 s) |
| `MORGAN_LLM_MAX_RETRIES` | 1 | Reintentos del SDK. **Groq va con 0** (abajo) |
| `MORGAN_TURN_TIMEOUT` | 180 s local, 85 s nube | El turno de `/chat` |
| `MORGAN_HTTP_DEADLINE` | sin tope local, 100 s nube | Cuándo contesta la petición; el turno sigue |
| `MORGAN_STREAM_TURN_TIMEOUT` | 180 s local, 170 s nube | El turno de `/chat/stream` (la web) |
| `MORGAN_MAX_ITERATIONS` | 6 | Vueltas al modelo. Si se acaban, una última llamada **sin herramientas** contesta con lo encontrado (4.20) |

Por qué 85 y 170 en la nube: [web.md](web.md#los-120-segundos-del-proxy) y
[agente.md](agente.md#1-el-turno).

## 3. Cuota: contarla y no pagar dos veces por saberla

`src/models/cuota.py`. Cuenta los tokens que ya venían en cada respuesta, **avisa al
80 %** del tope conocido de Groq (200.000/día) y **pospone al agotado** leyendo el
«vuelve en» del propio 429. De OpenAI no avisa: su límite lo pone su dueño en el panel.

**Se reordena, no se filtra.** La ventana de agotamiento es una estimación, y los
errores no cuestan lo mismo: llamar a uno agotado cuesta 0,02 s; saltarse a uno que
funcionaba deja a Morgan sin contestar. El agotado va al final, nunca fuera.

**Groq tiene dos límites, y confundirlos costaba dinero.** Medido: un turno costó
93 s y dinero de OpenAI por abandonar Groq ante una espera de décimas.

| Límite | Lo que dice el 429 | Qué se hace |
|---|---|---|
| 8.000 tokens por **minuto** | «try again in 112ms» … «9.25s» | **Esperar**, hasta 10 s de presupuesto de reloj |
| 200.000 tokens por **día** | «try again in 10m53s» | Pasar al siguiente eslabón |

El orden, de más barato a más caro: **otra clave** (gratis e instantáneo), **esperar**
(si cabe en 10 s), **rendirse**. El umbral sale de medir los caminos: esperar 0,1–9,3 s
gratis; el SDK reintentando, 13–26 s; Gemini, 29–36 s y un 504; OpenAI, 1–1,7 s pagando.

- **El SDK de Groq va con `max_retries=0`**: reintentando por su cuenta, 6 llamadas
  tardaban 83,4 s en vez de 3,7, y además **se tragaba el 429** y la segunda clave
  apenas se usaba.
- **Un 401 no es un agotamiento**: una clave inválida no se arregla esperando.
- **Una petición mínima no dice nada de la cuota diaria.** Las cabeceras son del
  límite por minuto; 73 tokens cabían cuando un turno de 3.100 ya no.
- **Vive en memoria a propósito**: persistirlo costaría 45 ms por llamada para
  ahorrar 20.

## 4. Varias claves: el llavero (V2.0.4)

`src/models/llavero.py`. **La cuota de Groq es de la cuenta**: dos claves de dos
cuentas son dos cupos. Se comprobó con el contador de **peticiones** diarias (el de
tokens se rellena en 570 ms y no distingue nada): la clave nueva leyó 999, no 996.
Hay **cuatro cuentas**: las cuatro claves están en el `.env` del Morgan de tu equipo
(comprobado: cuatro cargadas), y **producción usa tres** hasta que la cuarta se añada
en Render —`/status` dice `(3 claves)`— (pendientes.md, bloque 11).

- **Se gastan en serie**, no repartidas: así la segunda es una reserva de verdad y
  el aviso del 80 % llega a tiempo.
- **Solo se rota por cuota.** Un 400 o 401 le pasaría igual a todas.
- **La rotación vive en el proveedor, no en la cadena**: cambiar de clave no es
  cambiar de modelo y no debe anunciarse como respaldo.
- Se apunta por clave (`Groq#1`, `Groq#2`), **nunca con la clave dentro**.
- `/status` dice **cuántas** claves hay activas, para que una mal copiada se note.
- La transcripción de audio usa solo la primera clave (cuota de Whisper, sin medir).

## 5. Las claves se revisan al arrancar

`src/models/claves.py`. Al recrear producción se teclearon 18 secretos a mano y la
clave de Gemini llevaba **un símbolo de libra dentro**. Síntoma: todo funcionaba…
pagando OpenAI en cada turno, sin que nada lo dijera.

Se rechaza, sin salir a la red, lo que **no puede** funcionar: caracteres no ASCII,
espacios o saltos dentro, caracteres de control, menos de 16 o más de 512
caracteres. Cada clave mala cae sola, con el nombre de **su** variable y nivel ERROR,
y el proveedor se monta con las buenas. **No promete que la clave sirva**: eso solo
lo sabe el proveedor, al primer turno.

## 6. El router de modelos (V2.X del roadmap maestro)

```
Morgan → LLM Router → rápido | razonador | visión | audio | barato
```

| Rama | Estado |
|---|---|
| **visión / audio** | ✅ La cadena filtra por capacidad |
| **barato** | ✅ El orden de la cadena |
| **Relevos por capacidad** | ✅ **V2.0.20**, medido |

### Por qué hay relevos

El roadmap decía no adelantar el router hasta tener **perfiles de verdad distintos**.
Se midió y los hay: **la cuota de Groq también es por modelo** (con la misma clave,
120b bajó de 996 a 995 mientras 20b gastaba del suyo). Así que `gpt-oss-20b` es
otro cupo diario y otro por minuto, gratis.

| Candidato | Resultado |
|---|---|
| `gpt-oss-20b` | ✅ 0,44 s, y **5 de 5 turnos reales de Morgan con herramientas** |
| `qwen3.8-27b` | ❌ La cuenta gratuita le limita la salida a 1.000 tokens/min y Morgan pide 2.048: falla siempre |
| `allam-2-7b` | ❌ Respondió mal una multiplicación |
| `groq/compound` | Solo 250 peticiones/día |

### Cómo funciona

```
groq (120b) ──cuota──▶ groq@20b ──cuota──▶ gemini ──▶ openai (pago)
     └──caído u otro error──────────────▶ gemini
```

- **Solo tras un rechazo por cuota.** Con Groq caído, otro modelo suyo fallaría igual.
  Con el principal ya sabido agotado, se empieza por el relevo.
- **Las claves del relevo se apuntan con el modelo** (`Groq@openai/gpt-oss-20b#1`):
  la clave agotada para 120b no lo está para 20b.
- **Se anuncia como respaldo.** Eso destapó un defecto previo: con el principal
  agotado la lista se reordenaba y el respaldo contestaba sin aviso.
- `GROQ_MODELOS_RELEVO=` vacía lo desactiva.
- **Un rechazo por tamaño no se prueba en otro modelo del mismo proveedor** (3.1,
  `es_rechazo_por_tamano`). Groq manda igual «te pasaste de cuota» que «esta petición no
  cabe», pero esperar no arregla una petición demasiado grande y el otro modelo de Groq
  tiene el mismo tope de 8.000 tokens por minuto. Medido el 2026-09-19 con mi PC
  conectado: una llamada de 8.459 tokens fallaba en 120b y en 20b antes de llegar a
  Gemini. Ahora pasa directa a otro proveedor.

**Estimación, no medición al agotarse:** el techo gratuito pasa de ~105 a ~210 turnos
al día con tres cuentas, y el límite por minuto también se dobla. 20b solo es relevo
porque su calidad en tareas largas no se ha medido.

### Lo que falta para elegir por tarea

Casos de evaluación (decisión mía), medir 120b y 20b sobre ellos, y una regla
**solo si la medición la justifica**. Adivinar qué turnos «son fáciles» sería degradar
respuestas por un cupo que hoy no se agota.

## 7. Detalles de cada proveedor

**Groq.** Resultados de herramienta correlacionados por `tool_call_id`. Sin
herramientas hay que **omitir** `tools` y `tool_choice`, no mandarlos a `null` (400).

**Gemini.** Se preserva `raw_parts` para no perder el `thought_signature`; se recogen
**todas** las llamadas a herramienta; una respuesta bloqueada se traduce a texto.

**OpenAI (V2.0.3).** `gpt-5.6-luna` rechaza `max_tokens` (usa
`max_completion_tokens`), `temperature` distinta de 1, y herramientas sin
`reasoning_effort: "none"`: sin eso, **no funcionan las herramientas**. Los tres
rechazos son 400 y no costaron nada descubrirlos. Frenos de gasto:
`MORGAN_OPENAI_MAX_SALIDA` (2.048 por llamada), `MORGAN_OPENAI_TOPE_TOKENS` (200.000
por ventana, en memoria: es un freno contra bucles, no un límite de gasto) y **un plazo
agotado no se reintenta**, porque la respuesta puede haberse facturado. El límite de
verdad es el del panel de OpenAI. **Nunca se prueba contra OpenAI.**

**NVIDIA (apagado desde la V2.0.3).** En producción, **15 llamadas, 15 plazos agotados**,
aunque desde casa contesta en 3 s: se midió el efecto, no la causa. Latencia
impredecible, así que si vuelve, va el último. Con `thinking: false` es usable; con
`true`, `deepseek-v4-flash` agotó el plazo cinco de cinco veces. Si `content` llega
vacío y hay razonamiento, se usa el razonamiento.

## Seguridad y límites conocidos

- Las claves solo en `.env` y Render. `Settings.redacted()` dice si están, nunca su
  valor. El registro redacta `gsk_`, `AIza`, `sk-`, `api_key=`, `token=`, `password=`.
- El estado del modelo no se comprueba con una llamada real: `(3 claves)` dice que
  tienen buena forma, no que les quede cuota.
- No hay circuit breaker por caída, solo por cuota: un proveedor que no contesta paga
  sus 30 s en cada petición.

## Pruebas

`test_llm_chain.py`, `test_fallback_provider.py`, `test_cuota_de_proveedores.py`,
`test_cuotas.py`, `test_llavero_de_claves.py`, `test_claves_de_proveedor.py`,
`test_router_de_modelos.py` (18, 8 de 8 mutaciones), `test_llm_errors.py`,
`test_llm_resilience.py`.
