# Morgan — Personal AI Agent for Windows

Asistente personal de IA que conversa en lenguaje natural y ejecuta acciones controladas
sobre tu computador: archivos, terminal, git, web y memoria persistente, todo bajo un
sistema de permisos por niveles de riesgo y con auditoría completa.

**Estado (V3.5)**: 49 herramientas en 8 dominios · CLI + API REST + Web UI · cuentas
con datos separados por persona · espacios de trabajo · desplegado en la nube · **agente
local**: desde el móvil, Morgan lee las carpetas que permitas en tu PC, te trae una
copia de un archivo para descargarla y, si lo enciendes, **crea, edita, mueve y borra**
en las carpetas que elijas, siempre con un plan que apruebas tú, y ejecuta programas de un
catálogo cerrado, sin PowerShell · casi 4.000 pruebas
de Python y más de 60 del frontend, todas en verde y ninguna desactivada.

## Inicio rápido

### Requisitos
- Python 3.14 (es con el que se prueba, aquí y en la integración continua; `.python-version`)
- Node.js 20+ (sólo para la interfaz web)
- Una API key de [Groq](https://console.groq.com) o [Google Gemini](https://aistudio.google.com),
  que son gratuitos. Con una basta; con varias, Morgan cambia de proveedor solo cuando
  uno falla. [OpenAI](https://platform.openai.com) es opcional y **de pago**: va el último.

### Instalación

```bash
git clone https://github.com/wv-Andy/Morgan.git morgan
cd morgan

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt        # versiones fijadas: lo mismo que en producción
pip install -r requirements-dev.txt    # y pytest, para las pruebas

copy .env.example .env
# Editar .env con tu API key
```

> **No muevas ni copies la carpeta `venv`.** Un entorno virtual guarda la ruta
> donde se creó: movido, `pytest.exe` y los demás lanzadores fallan sin decir
> nada y las trazas apuntan a una carpeta que ya no existe. Si cambias el
> proyecto de sitio, bórralo y vuelve a crearlo. Me pasó de verdad (2.0.12).

### Ejecución

```bash
# CLI interactiva
python src/main.py

# API REST (http://127.0.0.1:8000, docs en /docs)
python -m src.api.server

# Interfaz web (requiere la API en marcha)
cd web && npm install && npm run dev
```

### Tests

```bash
pytest -q                 # casi 4.000, ninguna desactivada
cd web && npm test        # casi 100 más, las del navegador. Desde web/: fuera no lee su configuración
```

**Integración continua** (`.github/workflows/pruebas.yml`, desde la 4.19): en cada push, la
suite de Python en Windows con la cobertura y un suelo (si baja, falla), la web (tipos,
pruebas y compilación) y las vulnerabilidades conocidas de las dependencias (`pip-audit` y
`npm audit`). Allí no hay un escritorio delante: lo que maneja ventanas de verdad se salta con
`MORGAN_SIN_ESCRITORIO=1`, diciéndolo, y corre en local.

Las del frontend existen porque tres fallos que rompieron la web en producción
vivían justo ahí: en lo que hace el navegador, que no se ve hablando con la API
desde un script. Está contado en [docs/web.md](docs/web.md).

## Comandos de la CLI

| Comando    | Descripción                                        |
|------------|----------------------------------------------------|
| `/help`    | Muestra la ayuda                                    |
| `/tools`   | Lista las herramientas con dominio y nivel de riesgo|
| `/domains` | Agrupa las herramientas por dominio                 |
| `/memory`  | Muestra los recuerdos guardados en SQLite           |
| `/audit`   | Muestra los eventos recientes de auditoría          |
| `/clear`   | Limpia el historial de conversación en memoria      |
| `/exit`    | Salir                                               |

## Arquitectura

```
  CLI (Rich)            Web UI (React/Vite)
       |                        |
       |                 API REST (FastAPI)
       +----------+-------------+
                  v
             Agent (core)          <- Agent Loop multi-turno
                  |
     +------------+------------+--------------+
     v            v            v              v
 LLMProvider  ToolRegistry  Permission-   MemoryManager
 Groq -> Gemini  8 dominios  Manager      SQLite (local)
      -> OpenAI              + Validator  Supabase (nube)
 + Fallback                  + AuditLogger
```

El agente recibe un mensaje, consulta al LLM con los schemas de las herramientas, y por cada
llamada solicitada valida argumentos, comprueba permisos, ejecuta y reinyecta el resultado
hasta producir una respuesta final. Detalle completo en [docs/arquitectura.md](docs/arquitectura.md).

## Modelos LLM

Cadena con conmutación automática: **Groq** primero (gratis y el más rápido, con varias
cuentas que suman cuota), **Gemini** después (gratis, y el único que entiende imágenes) y
**OpenAI** al final, que es **de pago** y solo contesta cuando los gratuitos se agotan.
NVIDIA NIM sigue en el código, apagado: se activa con `MORGAN_LLM_ORDER`. Basta con
configurar un proveedor. Detalle en [docs/modelos.md](docs/modelos.md).

## Sistema de permisos

| Nivel | Comportamiento |
|---|---|
| 🟢 `safe` / `low_risk` | Ejecución automática |
| 🟡 `moderate` | Requiere confirmación (`MODERATE_PERMISSION_MODE=auto` la omite) |
| 🟠 `high_risk` | Siempre requiere autorización |
| 🔴 `critical` | Siempre requiere autorización |

Además: bloqueo proactivo de comandos destructivos, protección de rutas del sistema y de
procesos críticos, enmascaramiento de secretos y log inmutable en `logs/audit.log`.

## Herramientas

**50 en tu equipo** repartidas en ocho dominios: `system`, `filesystem`,
`terminal`, `memory`, `web`, `coding`, `git` y `general`.

**59 en la nube.** Las que tocan la máquina —archivos, terminal, procesos, git local— **no
se registran siquiera** cuando Morgan corre en un servidor: no es que estén prohibidas, es
que no existen ahí. Las de GitHub sí, porque hablan con una API y no con el disco. Y
veintinueve **de tu PC** a través del agente local, que no tocan el
servidor, solo aparecen mientras tu PC está conectado y solo alcanzan las carpetas que
permitas allí:

- Desde la 3.0, cuatro de **lectura** (`list_files`, `read_file`, `search_files`,
  `system_info`), y desde la 3.1 `copy_file`, que te trae **una copia de un archivo para
  descargarla** en el móvil.
- Desde la 3.3, cinco de **escritura** (`create_file`, `edit_file`, `create_folder`,
  `move_file`, `delete_file`), desde la 4.1 una sexta: `append_file`, añadir al final, y desde la 4.6
  copiar (`copy_path`) y comprimir o descomprimir (`compress`). Vienen **apagadas**; las enciendes en tu PC, una a una, y
  eliges en qué carpetas. Cada cambio necesita **un plan que apruebas** en la web, y
  borrar además se confirma **en el PC** con una notificación. Lo borrado va a la
  Papelera, y Morgan no crea ni cambia programas ni scripts.
- Desde la 3.5, la **terminal** y los **procesos** (`run_command`, `run_change_command`,
  `get_processes`, `kill_process`). **No hay PowerShell**: solo programas de un catálogo
  cerrado (git, ping, ipconfig…), sin shell, que enciendes uno a uno en tu PC. Lo que
  cambia cosas (`git pull`, `git commit`, terminar un proceso) pide plan y «Permitir» en
  el PC, y solo se terminan procesos tuyos.

Catálogo completo en [docs/capacidades.md](docs/capacidades.md).

## Estructura

```
proyectoAsistenteBeta/
├── src/
│   ├── agent/         # Agente central, prompt y eventos
│   ├── models/        # Proveedores LLM (Groq, Gemini, OpenAI, NVIDIA, Fallback, Mock)
│   ├── tools/         # Herramientas por dominio + registry
│   ├── security/      # Permisos, validadores y auditoría
│   ├── memory/        # Memoria persistente (SQLite y Supabase)
│   ├── identidad/     # Cuentas, sesiones, roles, cuotas y correo
│   ├── integraciones/ # Servicios externos por OAuth (GitHub; Google, aparcado)
│   ├── conocimiento/  # Documentos indexados y búsqueda
│   ├── espacios/      # Espacios de trabajo: proyectos con su contexto
│   ├── uploads/       # Archivos subidos, copias del PC y extracción de texto
│   ├── agente/        # El agente local que corre en TU PC (3.0): política y capacidades
│   ├── canal/         # En la nube: conexiones de agentes, despacho y herramientas del equipo
│   ├── tasks/         # Tareas y planes
│   ├── api/           # API REST FastAPI
│   ├── config.py      # Configuración central validada
│   └── main.py        # CLI
├── web/               # Interfaz React + TypeScript + Vite
├── migraciones/       # Esquema de Supabase, versionado
├── tests/             # casi 4.000 pruebas (pytest)
├── docs/              # Documentación
├── data/              # Base de datos SQLite
└── logs/              # Log de auditoría
```

## Documentación

**Índice completo en docs/README.md.** Lo más consultado:

| | |
|---|---|
| [Arquitectura](docs/arquitectura.md) | Capas, mapa de módulos y flujo de una petición |
| [Qué puede hacer Morgan](docs/capacidades.md) | Capacidades reales y límites, en lenguaje llano |
| [Autenticación y cuentas](docs/autenticacion.md) | Cómo entra la gente y qué separa los datos de cada uno |
| [Seguridad](docs/seguridad.md) | Permisos, validación, auditoría, roles y límites conocidos |
| [Decisiones técnicas](docs/decisions.md) | Por qué está hecho así, y qué se descartó |

La historia de la V0.1 a la V2.0, con lo que enseñó cada etapa, está resumida en
docs/historia.md.

## Roadmap

- [x] V0.1 — Hello Agent
- [x] V0.2 — Filesystem
- [x] V0.3 — Terminal y procesos
- [x] V0.4 — Agent Loop multi-turno y motor dual LLM
- [x] V0.5 — Memoria persistente (SQLite)
- [x] V0.6 — Internet y web segura
- [x] V0.7 — Coding Agent y Git
- [x] V0.8 — Sistema de herramientas por dominios
- [x] V0.9 — Seguridad avanzada y auditoría
- [x] V1.0 — API REST (FastAPI)
- [x] V1.1 — Interfaz web (React + TypeScript)
- [x] V1.2 — Persistencia, sesiones, memoria y rediseño
- [x] V1.3 — Arquitectura híbrida online/offline
- [x] V1.4 — Multimodal: imágenes, audio y archivos adjuntos
- [x] V1.5 — Sistema de tareas
- [x] V1.6 — Planificación y cuentas de usuario
- [x] V1.7 — Verificación de los pasos y recuperación
- [x] V1.8 — Conocimiento indexado y memoria avanzada
- [x] V1.9 — Integraciones con servicios externos (GitHub)
- [x] V2.0 — Plataforma: identidad, observabilidad, región y espacios de trabajo
- [x] V2.1–V2.3 — Optimización medida: agente, consolidación y preparación de la 3.0
- [x] V3.0 — Agente local: tu PC conectado a Morgan, de solo lectura
- [x] V3.0.5 — Evaluación de los fundamentos del agente
- [x] V3.1 — Frontera de datos, y una copia de tus archivos para descargar
- [x] V3.1.5 — Evaluación de seguridad de la lectura
- [x] V3.2 — El motor de política local (carpetas bloqueadas, capacidades, límites)
- [x] V3.2.5 — Evaluación de la política
- [x] V3.3 — Escritura en tu PC, con plan aprobado y confirmación en el PC para borrar
- [x] V3.3.5 — Evaluación de la escritura
- [x] V3.4 — Motor de ejecución: estados, diario en el PC, «Detener» que para de verdad
- [x] V3.4.5 — Evaluación de la ejecución (estrés)
- [x] V3.5 — Terminal y procesos: un catálogo cerrado, sin PowerShell
- [x] V3.6 — Resiliencia: sin órdenes fantasma, escrituras atómicas, salud y un vigilante
- [x] V3.6.5 — Evaluación de la resiliencia (horas del agente real y resistencia con fallos)
- [x] V3.7 — Varios agentes por persona, historial de órdenes y el cruce de las dos puntas
- [x] V3.7.5 — Evaluación de varios agentes (enrutado, aislamiento, revocación y cortes)
- [x] V3.8 — El agente instalable: paquete firmado, actualizaciones con vuelta atrás, credencial rotada
- [x] V3.8.5 — Evaluación del mantenimiento (14 escenarios con procesos de verdad)
- [x] V3.9 — Auditoría de la V3.x y la prueba real (la 3.5.5, en la 4.0)
- [x] V3.9.5 — La auditoría final de toda la 3.x
- [x] V4.0 — El ciclo cerrado: el plan aprobado se ejecuta solo, verifica lo del PC, corrige con tope y usa el contexto
- [x] V4.0.5 — Evaluación con el modelo real (queda mi prueba)
- [x] V4.1 — Los cabos de la evaluación, `append_file` y el diseño de la automatización
- [x] V4.1.5 — Eficiencia y errores: −22 % de tokens por turno, y los fallos que eso destapó
- [x] V4.2 — Eficiencia, segunda tanda: «crea un archivo» con −77 % de tokens, y solo las herramientas que pueden usarse
- [x] V4.3 — Auditoría de la 4.x: lo que se guardaba mal, la plantilla que tapaba un fallo y consultas de más
- [x] V4.4 — La guía de pruebas, hecha por Claude en lo que se puede: planes escritos sin crear, el «no» que se reintentaba, el PC desconectado
- [x] V4.18 — Un arranque en frío sin 503: el contenedor, uno solo aunque lo pidan dos hilos
- [x] V4.17 — Conectar tu PC, para cualquiera: la ventana «Morgan en tu PC», Python instalado solo, la web paso a paso y ejemplos de la API
- [x] V4.16 — Comprimir con el código dentro: las copias de un proyecto ya incluyen sus scripts
- [x] V4.15 — Los cambios pre-aprobados: automatizaciones con pasos fijos que cambian algo, aprobados una vez
- [x] V4.14 — Las automatizaciones: Morgan hace consultas sola a su hora y te deja el resultado en una bandeja
- [x] V4.13 — El contexto del PC: tus proyectos y editores, para «abre el proyecto» sin dar la ruta
- [x] V4.12 — El control de la interfaz: clics, escribir y teclas sobre los controles por su nombre, con plan y «Permitir»
- [x] V4.11 — Ventanas (listar, mover, lado a lado) y capturas de pantalla, con «Permitir» cada vez
- [x] V4.10 — El portapapeles (leerlo, con «Permitir» cada vez) y las notificaciones en el PC
- [x] V4.9 — La terminal ampliada: npm (sin cmd.exe), pip, scripts propios, winget y los servicios de Windows
- [x] V4.8 — Buscar con Google y otros motores; abrir aplicaciones, archivos, páginas y carpetas, y cerrar programas
- [x] V4.7 — El PC por dentro: discos, GPU, batería, rendimiento, red, puertos y servicios, sin administrador
- [x] V4.6 — Archivos (copiar, comprimir, metadatos, buscar por extensión, fecha, tamaño y contenido) y el permiso automático en Ajustes
- [x] V4.5 — Lo que salió en mis pruebas: la búsqueda en producción, un turno a la vez por conversación, el PC que reconecta
- [x] V4.19 — La base: integración continua (20 de 20 en verde), dependencias fijadas y auditadas, cobertura con suelo, y el código público en [wv-Andy/Morgan](https://github.com/wv-Andy/Morgan)
- [x] V4.20 — Aguante en producción y exacto al buscar: peticiones acotadas, un proveedor caído no cuesta su plazo, copia de seguridad restaurada de verdad, los errores llegan a la bandeja
- [x] V4.21 — Varias búsquedas en una sola vuelta: la pregunta de tres partes, de 29,7 s a 4,9 s
- [ ] V4.22-4.24 — El resto del endurecimiento hasta producción, con un gate medible (plan)
- [ ] V5.x — Morgan para Windows: instalador, bandeja, ventana propia, firma y Store (plan)

Detalle de cada una en docs/roadmap-maestro.md.

## Licencia

MIT
