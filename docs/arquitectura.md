# Arquitectura de Morgan

> La arquitectura real, verificada contra el código. Junta lo que antes eran la
> arquitectura, el modo online/local/degradado y la revisión arquitectónica de la
> 2.2. Última revisión: 2026-09-25, V3.5.0.

Un agente personal de IA que conversa en lenguaje natural y ejecuta acciones reales
bajo permisos y auditoría. Funciona en dos entornos con capacidades distintas y se
degrada de forma controlada.

> **Online cuando pueda, local cuando deba y degradado cuando no pueda hacer algo.**

## 1. Vista general

```
   CLI (Rich)                Interfaz web (React, Vercel)
        │                            │  /api/* (proxy mismo origen)
        │                     API REST (FastAPI, Render)
        │                            │
        │                   Identidad y espacio (ContextVar)
        └─────────────┬──────────────┘
                      ▼
                 Agent (Core) ── un solo agente para todos los usuarios
                      │
   ┌──────────┬───────┴───────┬──────────────┬──────────────┐
   ▼          ▼               ▼              ▼              ▼
LLMProvider  ToolRegistry  Permission-   Repositorios   ServiceHealth
+ Fallback   (por entorno)  Manager       (interfaz)     (estado)
   │              │         + Validator        │
Groq · Gemini ·  49 / 29    + AuditLogger ┌────┴────┐
OpenAI           herramientas             SQLite  Supabase
```

**Ninguna flecha va del Core a una tecnología concreta: siempre a una interfaz.** La
CLI y la API se montan sobre el **mismo** contenedor (`CoreContainer` en
`src/api/dependencies.py`).

## 2. Capas

| Capa | Dónde | Detalle |
|---|---|---|
| **Core** | `src/agent/core.py` | Bucle multi-turno: consulta al modelo, valida argumentos, comprueba permisos, ejecuta, verifica y reinyecta. Topes por iteraciones **y por reloj** → [agente.md](agente.md) |
| **Modelos** | `src/models/` | `LLMProvider`, cadena con capacidades, cuota, llavero y relevos → [modelos.md](modelos.md) |
| **Herramientas** | `src/tools/` | `Tool` + `ToolRegistry`, 8 dominios. Cada una declara riesgo, `requires_local` y, si actúa fuera de Morgan, `exige_plan` → [capacidades.md](capacidades.md) |
| **Seguridad** | `src/security/` | 5 niveles de riesgo, validación de rutas y comandos, auditoría de **todo intento** → [seguridad.md](seguridad.md) |
| **Identidad** | `src/identidad/`, `src/api/identidad_middleware.py` | Usuario y rol de la petición en una `ContextVar`, fijados en el middleware → [autenticacion.md](autenticacion.md) |
| **Datos** | `src/memory/`, `src/conocimiento/`, `src/espacios/` | Repositorios SQLite y Supabase; ninguna referencia a `sqlite3` ni al cliente HTTP fuera de ahí → [datos.md](datos.md) |
| **Tareas y archivos** | `src/tasks/`, `src/uploads/` | Tareas, planes, verificación; archivos subidos → [agente.md](agente.md), [capacidades.md](capacidades.md#5-archivos-imágenes-y-voz) |
| **Integraciones** | `src/integraciones/` | OAuth con token cifrado → [integraciones.md](integraciones.md) |
| **Estado** | `src/health.py`, `src/api/health_checks.py` | Una entrada por dependencia, nunca bloquea (abajo) |
| **API** | `src/api/` | Factoría, rutas, CORS, errores uniformes, modo degradado → [api.md](api.md) |
| **Web** | `web/` | → [web.md](web.md) |
| **Configuración** | `src/config.py`, `.env.example` | Se carga y valida una vez; un valor inválido cae al defecto con aviso. Todas las variables, en [`.env.example`](../.env.example) |

### Lo que es «de este turno» vive en una `ContextVar`

Hay **un solo agente** y un solo registro para todos los usuarios. Por eso el usuario,
el espacio, la medición y el canal de eventos del turno viven en variables de
contexto, y el hilo del turno las recibe con `copy_context()`. Pasarlo por parámetro
haría que una función nueva pudiera olvidarlo; guardarlo en el agente haría que lo de
Ana apareciera en el turno de Bea. Las instrucciones de un espacio van en el prompt
**del turno**, nunca en el del agente.

## 3. Los dos entornos

`MORGAN_ENVIRONMENT`:

| | `local` (por defecto) | `cloud` |
|---|---|---|
| Dónde corre | Tu equipo | Render |
| Herramientas | Las 49 | Las que no tocan la máquina, **más las 5 del PC de la persona** cuando su agente local está conectado |
| Almacén | SQLite | Supabase |
| Cuentas | No se exigen | Sí |

**En `cloud`, las herramientas con `requires_local=True` no se registran**, no se
«bloquean por permisos»: lo que no está registrado no puede invocarse ni aparece en el
esquema que ve el modelo ([ADR-013](decisions.md)). `requires_local` es `True` por
defecto: olvidarse de clasificar una herramienta la deja fuera de la nube.

**Morgan en la nube no toca tu PC por su cuenta.** Desde la V3.0 puede **pedírselo al
agente local** que la persona instala en su equipo, y es el agente quien decide: tiene su
propia credencial, su política de carpetas y su auditoría, y trata a la nube como no
fiable (agente-local.md). En la V3.1 esas capacidades son leer,
buscar y **traer una copia de un archivo para descargarla**; escribir llega en la 3.3.

## 4. Estado por servicio y degradación

No hay un `internet = sí/no`: hay una entrada por dependencia (`internet`,
`database.local`, `database.remote`, `llm`, `correo`), con estados `AVAILABLE`,
`DEGRADED`, `UNAVAILABLE` y `UNKNOWN`.

- **Consultar el estado nunca bloquea**: se lee el último valor, con TTL de 30 s.
- **Un servicio caído no se machaca**: retroceso exponencial hasta 5 minutos.
- **El LLM no se comprueba con una llamada real** (costaría dinero): se informa de lo
  observado en uso, y hubo conmutaciones, sale `DEGRADED`.

| Situación | Comportamiento |
|---|---|
| Proveedor lento o caído | Corta a los 30 s y conmuta al siguiente |
| Todos los proveedores caídos | Error controlado; lo que no necesita modelo sigue |
| Sin internet | Funciona lo local; lo que sale a la red da error controlado |
| Base caída | Morgan arranca; `/status` lo dice |
| Turno demasiado largo | Se detiene con un mensaje claro; en la nube la petición contesta a los 100 s y el turno sigue |

La vista **Estado** de la web traduce esto a capacidades en lenguaje llano, con el
motivo cuando algo no está disponible en el entorno.

## 5. Flujo de una petición

```
Petición → middleware: token, usuario, CSRF, espacio, medición
  → Agent: ventana reciente del historial (filtrada por usuario)
  → modelo (prompt del turno + memoria + fecha) → ¿herramientas?
       sí → validar → permisos / plan → ejecutar → verificar → repetir
       no → respuesta
  → persistir el turno → respuesta (o eventos en /chat/stream)
```

## 6. Principios

1. **El Core depende de interfaces, nunca de proveedores.**
2. **Internet mejora Morgan, no lo condiciona.**
3. **La escritura local nunca espera al remoto.**
4. **Fail-safe por defecto.** Herramienta sin clasificar, fuera de la nube; sin
   consola, los permisos deniegan; riesgo desconocido, crítico; modelo sin
   capacidades declaradas, solo texto.
5. **Lo que no se ha comprobado no se da por bueno.**
6. **Un fallo externo no tumba Morgan.**
7. **El aislamiento entre usuarios se pone en un sitio, no en cada consulta.**
8. **Un solo camino para cada cosa**: un catálogo de herramientas, un `fetch` en la
   web, una función de puntuación del conocimiento. Dos copias divergen solas.

## 7. La revisión arquitectónica de la 2.2 (V2.0.11)

Se hizo midiendo, no leyendo en busca de algo que mejorar: tamaño por módulo,
nombres y bloques repetidos (con un detector propio que normaliza espacios),
`vulture`, `ruff` y cobertura (**91,4 %**).

- **La consola tenía un catálogo copiado con 12 herramientas de menos** (conocimiento,
  planes y GitHub), y la prueba que debía verlo comparaba en la dirección equivocada.
  Ahora la consola se monta sobre el mismo contenedor (`tests/test_consola.py`).
- **Los filtros de PostgREST estaban copiados** en el conocimiento de la nube; `_esc`
  es lo que impide colar un filtro ajeno, y dos copias podían divergir.
- **Código que nadie llamaba**, comprobado a mano uno a uno: **285 líneas menos** en
  `src/`, sin cambiar lo que hace Morgan.
- **Las dos memorias de SQLite** no se unificaron entonces porque no se comportaban
  igual; se hizo en la 2.0.13 con la prueba primero, y apareció un defecto
  ([datos.md](datos.md#3-memoria-los-cinco-tipos)).

No se tocó a propósito: las propiedades repetidas de cada `Tool` (es la forma de la
interfaz) ni el parecido entre repositorios (dos dialectos de lo mismo).

## Pruebas

`pytest`, **ninguna desactivada**. Cuántas hay lo dice `pytest -q`; escribirlo aquí solo
garantizaría que un día esté mal. Cada defecto encontrado deja una prueba que lo fija,
y cada prueba nueva se verifica rompiendo el código a propósito (mutaciones).
