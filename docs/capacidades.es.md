# Qué puede hacer Morgan: capacidades y herramientas

[English](capacidades.md) · **Español**

> Junta lo que antes eran tres documentos: las capacidades en lenguaje llano, el
> catálogo de herramientas y la entrada multimodal. Última revisión: 2026-09-25,
> V3.5.0. **49 herramientas en `local` y 43 en `cloud`** (14 de ellas, en el PC de la
> persona a través de su agente), en 8 dominios, contadas del registro real
> (`tests/test_readme_al_dia.py` lo comprueba contra el README).

## 1. En una frase

Un agente que conversa, decide qué herramientas usar y las encadena hasta resolver lo
que le pides. En tu equipo actúa sobre tus archivos, tu terminal y tus repositorios; en
la web ([morgan-ia.vercel.app](https://morgan-ia.vercel.app)), con cuenta, hace todo lo
que no toca una máquina, y desde la 3.0, con el agente local en tu PC,
**lee** las carpetas que permitas allí; desde la 3.1 también puede **traerte una copia de
un archivo para descargarla** en el móvil; desde la 3.3, si lo enciendes, **crear,
editar, mover y borrar** en las carpetas que elijas, con un plan que apruebas tú; y desde
la 3.5, **ejecutar programas de un catálogo cerrado** (git, ping, ipconfig…) y ver o
terminar tus procesos.

| Vía | Cómo | Para qué |
|---|---|---|
| **Web en la nube** | `https://morgan-ia.vercel.app` | Con cuenta, siempre disponible, 43 herramientas (14 de ellas actúan en tu PC con el agente conectado: cuatro de lectura, la copia de un archivo para descargarlo, cinco de escritura y cuatro de terminal y procesos, estas nueve apagadas hasta que las enciendes) |
| **Morgan para Windows** (5.0) | El instalador: «Descargar Morgan para Windows» en el menú lateral de la web (desde Windows) o en Ajustes → Tu equipo | Lleva el agente dentro: lo empareja desde una ventana, sin consola, y lo deja arrancando con Windows. Lo recomendado para conectar un PC. Desde la 5.1 vive en la bandeja (estado, pausar, lo último que hizo); desde la 5.2 trae la conversación (Ctrl+Alt+M) y los avisos como notificación si enciendes «Mandarte avisos a este PC»; desde la 5.3 es **una sola ventana con la web de Morgan**, con lo del programa en Ajustes → Este PC, y **se actualiza** (firmado, al pulsar, y vuelve solo a la anterior si la nueva no conecta) |
| **CLI** | `.\venv\Scripts\python -m src.main` | El día a día en tu equipo, con las 49 |
| **Web local** | `.\venv\Scripts\python -m src.api.server` → `http://127.0.0.1:8000` | La interfaz sobre tu equipo |
| **API REST** | La misma que usa la web | [api.md](api.es.md) |

## 2. Lo que puedes pedirle

| Qué | Ejemplos | Dónde |
|---|---|---|
| **Archivos** | «¿Qué hay en Descargas?», «Crea un `notas.md` con el resumen» | Local. Desde la web, con el agente local: leer en las carpetas que permitas y, si lo enciendes, escribir en las que elijas para ello |
| **Tu PC** | «¿Qué se está comiendo la CPU?», «Ejecuta `npm run build`» | Local. Desde la web, con el agente: un catálogo cerrado (`git status`, `git pull`, `ipconfig`, `ping`…) y ver o terminar tus procesos |
| **Programar** | «Inspecciona este proyecto», «Cambia el timeout y corre los tests» | Local |
| **Git** | «¿Qué tengo sin commitear?», «Haz un commit con este diff». **No hace push**, a propósito | Local |
| **GitHub** | Repositorios, issues, pull requests y archivos, **solo lectura** | Los dos |
| **Internet** | «Busca cómo se configura…», «Léete esta URL» | Los dos |
| **Recordar** | «Recuerda que prefiero respuestas cortas», «¿Qué sabes de mí?» | Los dos |
| **Documentos** | Guardarlos y consultarlos cuando vienen a cuento | Los dos |
| **Archivos, imágenes y voz** | «Mira esta captura», «Resúmeme este PDF», grabar un audio | Los dos |
| **Trabajos largos** | Tareas con progreso, planes que apruebas antes de que se ejecuten | Los dos |
| **Proyectos** | Espacios de trabajo con conversaciones, archivos e instrucciones propias | Los dos |

**Comprueba lo que hace y admite cuando no puede**: tras crear, borrar o cambiar algo,
mira si de verdad pasó, y dice «no pude» en lugar de «listo» ([agente.md](agente.es.md)).

## 3. Lo que NO hace

- **No hace push ni despliega nada.** No existe la herramienta.
- **Desde la web, en tu PC no hace nada que no permitas allí.** Lee las carpetas que
  permitas (ninguna al principio). Escribir viene **apagado**: se enciende en el PC, en
  carpetas aparte, cada cambio necesita **un plan que apruebas** y borrar se confirma
  además **en el PC** (a la Papelera). **No crea programas ni scripts.** Ejecutar, solo
  programas de un **catálogo cerrado y sin shell** que enciendes uno a uno (3.5): nada de
  PowerShell ni de scripts. El resto de herramientas locales ni se registran en la nube.
- **No trabaja sin internet** salvo lo que no necesite modelo. Un modelo local está
  **descartado**.
- **No habla** (texto a voz). **No aprende solo.**
- **No se conecta a Google**: Calendar está aparcado (descartado el 2026-09-19).

## 4. El catálogo

🟢 `safe` · 🟡 `moderate` (pide confirmación) · 🔴 `critical` (siempre pide
confirmación). **L** = solo local · **L+N** = local y nube.

### Equipo (solo local)

| Herramienta | Nivel | Qué hace |
|---|---|---|
| `system_info` | 🟢 | CPU, RAM, disco y sistema en tiempo real |
| `list_files`, `read_file`, `search_files` | 🟢 | Listar, leer (paginado) y buscar por nombre o patrón |
| `create_file`, `copy_file`, `move_file`, `rename_file` | 🟡 | Crear (con carpetas intermedias), copiar, mover, renombrar |
| `delete_file` | 🔴 | Borrado permanente con rutas del sistema protegidas |
| `execute_command` | 🔴 | PowerShell con plazo y salida capturada |
| `get_processes`, `get_environment` | 🟢 | Procesos por CPU o RAM; variables **con secretos enmascarados** |
| `kill_process` | 🔴 | Terminar procesos, con los críticos protegidos |
| `inspect_project`, `search_code` | 🟢 | Stack y dependencias; definiciones y patrones |
| `patch_file`, `run_tests` | 🟡 | Sustitución exacta; ejecutar la suite y leer la traza |
| `git_status`, `git_diff` | 🟢 | Estado y diferencias (la ruta va tras `--`) |
| `git_commit` | 🟡 | Crear un commit |

### GitHub (L+N, solo lectura)

`github_listar_repos`, `github_listar_issues` (sin los PR que GitHub mezcla),
`github_listar_prs`, `github_leer_archivo` → [integraciones.md](integraciones.es.md).

### Memoria, conocimiento y web (L+N)

| Herramienta | Nivel | Qué hace |
|---|---|---|
| `remember_fact`, `recall_memory` | 🟢 | Guardar (por clave) y consultar hechos sobre ti |
| `forget_fact` | 🟡 | Olvidar uno |
| `search_knowledge`, `add_knowledge`, `list_knowledge_sources`, `index_document` | 🟢 | Documentos consultables → [datos.md](datos.es.md#4-conocimiento-v18) |
| `remove_knowledge` | 🟡 | Borrar un documento |
| `search_web`, `read_webpage` | 🟢 | Buscar con **Google** (vía Serper, `SERPER_API_KEY`, 4.8), Tavily, Brave y DuckDuckGo, en ese orden y cada uno solo con su clave; se puede pedir uno (`motor`) y cada resultado dice cuál contestó. **Varias búsquedas en una llamada** (`consultas`, hasta 4, a la vez; 4.21): una pregunta de varias partes ya no es una vuelta al modelo por cada parte. Un buscador que no responde descansa 10 min y, sin ninguno, se dice a la primera (4.5). Y leer páginas, aisladas en `<untrusted_web_data>`, sin alcanzar la red interna |
| `list_uploads`, `read_upload`, `analyze_image`, `transcribe_audio` | 🟢 | Archivos subidos por la web (abajo) |
| `copy_file` | 🟢 | **Solo con tu PC conectado** (3.1-E): trae una copia de un archivo tuyo y te da un enlace para descargarla en el móvil. Cualquier formato, hasta 20 MB; la copia se borra sola a las 24 h |
| `delete_file` | 🔴 | Igual, y además **se confirma en el PC** con una notificación. Va a la Papelera; solo carpetas vacías |
| `open_app` | 🟢 | **Solo con tu PC conectado y encendida allí** (4.8): abrir una aplicación instalada (desde la 5.3 también las de la Store y las de Windows, como la Calculadora, Fotos o Spotify, y los juegos de Steam y Epic; nunca consolas, scripts ni herramientas del sistema), un archivo con su programa, una página en el navegador o una carpeta. Sin plan |
| `close_app` | 🔴 | Cerrar un programa tuyo como su X, con plan y «Permitir» en el PC |
| `pc_context` | 🟢 | **Solo con tu PC conectado** (4.13; encendida al nacer): tus proyectos de código dentro de las carpetas permitidas (tipo, rama de git) y los editores instalados. Para «abre el proyecto X» sin dar la ruta |
| `ui_read` | 🟢 | **Solo con tu PC conectado y encendida allí** (4.12): los botones, campos y enlaces de una ventana, por su nombre |
| `ui_control` | 🔴 | Igual: hacer clic, escribir o pulsar teclas en una ventana, con plan y «Permitir» al empezarlo. Nunca contraseñas, consolas, el Explorador ni la tecla Windows |
| `windows` | 🟢 | **Solo con tu PC conectado y encendida allí** (4.11): ver tus ventanas, enfocar, minimizar, maximizar, mover o poner dos lado a lado. Sin plan |
| `screenshot` | 🟢 | Igual (4.11), con **«Permitir» en el PC cada vez**: una captura de la pantalla, una ventana o una zona; queda 24 h en tus archivos y Morgan la mira con `analyze_image` |
| `clipboard`, `notify` | 🟢 | **Solo con tu PC conectado y encendidas allí** (4.10): escribir en el portapapeles o leerlo (con «Permitir» en el PC cada vez, porque puede ser una contraseña); y una notificación en el PC. Sin plan. Desde la 5.2, los avisos de tus automatizaciones también llegan así, si `notify` está encendida |
| `pc_diagnostics` | 🟢 | **Solo con tu PC conectado y encendida allí** (4.7): el rendimiento («¿por qué va lento?»), la red y la IP, qué programa escucha en qué puerto y los servicios de Windows. `system_info` da, además, discos y espacio, GPU y batería |
| `copy_path`, `compress` | 🟡 | Igual que las de arriba (4.6): copiar un archivo o carpeta a un sitio nuevo del PC; comprimir en `.zip` o descomprimir. Nunca credenciales. Programas y scripts: comprimir **sí** los mete (4.16, decisión mía: si no, la copia de un proyecto salía sin el código); copiar y descomprimir, nunca. Un `.zip` peligroso no se saca |
| `file_info` | 🟢 | **Solo con tu PC conectado** (4.6): tipo, tamaño, fechas y atributos de un archivo; de una carpeta, cuánto ocupa. `search_files`, además, por extensión, fecha, tamaño y texto de dentro |
| `service_control` | 🔴 | **Solo con tu PC conectado y encendida allí** (4.9): iniciar, parar o reiniciar un servicio de Windows, con plan y «Permitir»; nunca los que sostienen Windows. Muchos piden administrador, y entonces no se puede |
| `run_change_command`, `kill_process` | 🔴 | Lo que cambia (`git pull`, `git commit`) y terminar un proceso **tuyo**: plan y «Permitir» en el PC |

### Tareas, planes y verificación (L+N)

`create_task`, `get_task`, `list_tasks`, `complete_task`, `fail_task`, `cancel_task`,
`retry_task`, `create_plan`, `get_plan`, `list_plans`. Todas 🟢: planificar no ejecuta
nada → [agente.md](agente.es.md). `verify_step` se retiró en la 4.1.5: la verificación ya es
automática tras cada cambio, y ella daba por fallido lo hecho en el PC.

### Automatizaciones (4.14)

| Herramienta | Riesgo | Qué hace |
|---|---|---|
| `create_automation` | 🔴 | Programa una orden para que Morgan la haga sola (diaria, ciertos días o cada N horas, como poco cada hora) y deje el resultado en la bandeja. **Siempre con un plan que apruebas**, también con el permiso automático. Dos tipos: una **consulta** (4.14), o **pasos fijos** que cambian algo (4.15): herramienta y argumentos exactos, solo verdes y amarillos, iguales cada vez salvo `{fecha}` y `{hora}`. Como mucho 10 activas |
| `list_automations` | 🟢 | Las que tienes, cuándo tocan y cómo fue la última |

Pausar, reanudar y borrar van en la vista **Automatizaciones** de la web. Lo que puede
usar una automatización mientras se ejecuta: buscar y leer en internet, tu conocimiento,
tu memoria y, del PC, solo leer (archivos, buscar, sistema, proyectos, diagnóstico y los
comandos de consulta) → [seguridad.md](seguridad.es.md#5-bis-las-automatizaciones-nadie-delante-414).

### Recuento

| Dominio | Local | Nube |
|---|---|---|
| filesystem | 8 | 0 |
| terminal | 4 | 0 |
| coding | 4 | 0 |
| system | 1 | 0 |
| git | 7 | 4 |
| memory | 8 | 8 |
| web | 6 | 6 |
| general | 10 | 10 |
| **Total** | **48** | **28** |

Del prompt de cada turno se recortan las herramientas que no pueden usarse en ese
momento: las de una tarea abierta cuando no hay ninguna, las del PC si no está conectado y,
desde la 4.1.5, las de GitHub si la persona no lo ha conectado; desde la 4.2, las de
adjuntos si no ha subido nada y las de buscar, listar o borrar documentos si no tiene
ninguno.

### Añadir una herramienta

1. Heredar de `Tool` (`src/tools/base.py`): `name`, `description`, `parameters` (JSON
   Schema), `permission_level`, `category`, `execute()` devolviendo
   `{"success", "data", "error"}`.
2. Declarar `requires_local` (por defecto `True`: fuera de la nube salvo que se diga).
3. Registrarla en `CoreContainer._build_tool_registry()`: **el único sitio**.
4. Si recibe una lista, darle `items`: **Gemini rechaza el catálogo entero** sin él.
5. Si actúa fuera de Morgan en nombre de alguien, `exige_plan = True`.
6. Actualizar este catálogo y los recuentos del README (en los dos idiomas).

## 5. Archivos, imágenes y voz

**Capacidades por proveedor.** Mandar una imagen a un modelo sin visión no da una
respuesta peor: da un error. La cadena filtra por capacidad **antes** de conmutar, y si
nadie sirve falla enseguida con `CapacidadNoDisponible`. Por defecto un proveedor solo
hace texto. Hoy la visión la atiende Gemini; el audio, Whisper por el SDK de Groq con la
misma clave.

**Un archivo subido es dato no confiable:**
- **El nombre del usuario nunca toca el disco**: se guarda con un id generado. La
  defensa contra *path traversal* es no construir la ruta.
- **El tipo se deduce de los bytes**, y si la extensión promete un formato con firma y
  la firma no está, se rechaza (un ejecutable renombrado a `.png` se aceptaba).
- **Lo extraído va en `<untrusted_file_data>`**, como las páginas web.
- **Sin previsualización**: renderizar HTML o SVG subido es superficie de ataque. SVG
  entra como texto.
- Subidas, borrados **y rechazos** se auditan, nunca el contenido.

| Límite | Valor | Variable |
|---|---|---|
| Tamaño por archivo | 20 MB | `MORGAN_UPLOAD_MAX_MB` |
| Archivos por persona | 50 | `MORGAN_UPLOAD_MAX_ARCHIVOS` |
| Espacio por persona | 200 MB | `MORGAN_UPLOAD_MAX_TOTAL_MB` |
| Vida | 24 h | `MORGAN_UPLOAD_TTL_HOURS` |
| Extracción | 30 s, 30.000 caracteres, 100 páginas | — |

**Formatos** (73 extensiones): imágenes PNG, JPEG, GIF y WebP; PDF; texto y datos (TXT,
Markdown, CSV, JSON, XML, YAML, TOML, INI, LOG); código de una veintena de lenguajes;
audio MP3, WAV, WebM, M4A, OGG, FLAC, Opus y AAC. Un PDF escaneado se detecta y se dice.

**Dónde se guardan**: el índice en la base (SQLite o Supabase); los bytes en disco en
local y en el bucket **privado** `morgan-uploads` de Supabase Storage en la nube. Antes
el índice vivía en memoria y en la nube se perdía al dormirse Render.

**El adjunto va ligado al mensaje** en sus metadatos, no pegado en el texto; uno que ya
no existe se rechaza con 422 antes de gastar un turno. Hasta 10 por mensaje. Con un
adjunto, un mensaje sin texto es legítimo. La voz se transcribe **para revisarla antes
de enviarla** ([web.md](web.es.md#3-control-sobre-el-turno)).

Verificado de extremo a extremo: un `.md` leído, y un PNG descrito por Gemini con el
principal sin visión.

**Fuera**: OCR de PDF escaneados, DOCX y texto a voz.
