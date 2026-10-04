# Registro de Decisiones Técnicas

[English](decisions.md) · **Español**

## ADR-001: Python como lenguaje principal
- **Fecha**: 2026-09-04
- **Estado**: Aceptado
- **Contexto**: Necesitamos un lenguaje para el backend del agente.
- **Decisión**: Python, por su ecosistema de IA/ML, facilidad de scripting, y amplia disponibilidad de bibliotecas.
- **Consecuencias**: Excelente soporte para APIs de LLM, psutil, y automatización de sistema.

## ADR-002: Google Gemini como modelo inicial
- **Fecha**: 2026-09-04
- **Estado**: Aceptado
- **Contexto**: Necesitamos un LLM con soporte para function calling.
- **Decisión**: Google Gemini vía API (gratuita con límites generosos).
- **Alternativas**: OpenAI GPT-4, Anthropic Claude, modelos locales.
- **Consecuencias**: API key gratuita para desarrollo. Function calling nativo. Diseño modular permite cambiar de proveedor.

## ADR-003: Sistema de permisos por niveles
- **Fecha**: 2026-09-04
- **Estado**: Aceptado
- **Contexto**: La IA no debe tener acceso ilimitado al computador.
- **Decisión**: Tres niveles de permiso (🟢 safe, 🟡 moderate, 🔴 sensitive) con control independiente por herramienta.
- **Consecuencias**: Seguridad controlada desde el inicio. Escalable para nuevas herramientas.

## ADR-004: CLI con Rich antes de GUI
- **Fecha**: 2026-09-04
- **Estado**: Aceptado
- **Contexto**: Necesitamos una interfaz para V0.1.
- **Decisión**: CLI interactiva usando Rich para formato y colores.
- **Alternativas**: GUI inmediata (React), Textual (TUI).
- **Consecuencias**: Desarrollo rápido. Funcional desde el primer día. GUI vendrá en V0.7.

## ADR-005: Principio "funcionalidad primero"
- **Fecha**: 2026-09-04
- **Estado**: Aceptado
- **Contexto**: Riesgo de over-engineering con arquitecturas complejas.
- **Decisión**: Cada versión debe agregar una capacidad real. No construir infraestructura sin funcionalidad que la justifique.
- **Consecuencias**: Progreso medible. Un solo agente inicialmente, múltiples agentes solo si se demuestra la necesidad.

## ADR-006: SQLite se mantiene; se reescribe la capa de acceso
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: La auditoría técnica encontró fugas de conexiones, ausencia de manejo de
  errores, sin migraciones y sin configuración de concurrencia. Se planteó si el motor
  era el problema.
- **Decisión**: Conservar SQLite y reescribir la capa de acceso (`src/memory/db.py`).
  Morgan es un agente personal mono-usuario en local: PostgreSQL añadiría un servicio
  que administrar sin ningún beneficio real.
- **Consecuencias**: Conexiones que se cierran, WAL para el acceso multihilo de la API,
  errores traducidos a `MemoryStorageError` y migraciones incrementales con
  `schema_version`. Añadir tareas, configuración o historial es agregar una migración y
  un repositorio, sin tocar lo existente.

## ADR-007: Los permisos deniegan, nunca bloquean, en contexto no interactivo
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: `Confirm.ask()` leía de stdin del proceso servidor. Al lanzar uvicorn
  desde una terminal, `isatty()` era cierto y una petición HTTP a una herramienta de
  nivel moderado dejaba el worker bloqueado esperando que alguien escribiera en la
  consola del servidor.
- **Decisión**: `PermissionManager` recibe un flag `interactive` explícito. Sin consola
  la política es denegar de inmediato. El contenedor de la API lo construye con
  `interactive=False`.
- **Alternativas**: Auto-aprobar por HTTP (inaceptable), o un timeout en el prompt
  (sigue bloqueando el hilo el tiempo del timeout).
- **Consecuencias**: La API nunca se cuelga. Como contrapartida, las herramientas
  `moderate`, `high_risk` y `critical` no se pueden ejecutar por HTTP hasta que exista
  un flujo de aprobación en la interfaz. Es una limitación consciente y documentada. (Ese flujo
  llegó después: los planes aprobados en la web, V1.6 y V2.0.16.)

## ADR-008: No se introduce C++ en esta fase
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: Se evaluó si alguna parte de Morgan se beneficiaría de código nativo.
- **Decisión**: No introducir C++. No existe hoy ningún candidato justificado.
- **Fundamento**: El perfil de Morgan es espera de red y E/S, no cómputo. El tiempo de
  respuesta lo domina la llamada al LLM (segundos), seguida de los subprocesos de
  PowerShell y git. Los recorridos de `os.walk` son E/S de disco, donde C++ no aporta
  ventaja significativa, y `psutil` ya es nativo por debajo. Medido durante la auditoría:
  `get_schemas()` cuesta 0,013 ms por llamada y `search_files` bajó de 79 ms a 1 ms
  simplemente podando directorios, sin salir de Python.
- **Consecuencias**: Se evita una toolchain de compilación, complejidad de empaquetado y
  una superficie de fallos nueva a cambio de cero mejora medible.
- **Criterios de reevaluación**: se reconsiderará si aparece indexación de código a gran
  escala, hashing o diffing masivo de archivos, o integración profunda con la API Win32.
  En ese caso el módulo debe quedar aislado tras una interfaz limpia, con sus pruebas y
  su documentación. (Cuando llegó el programa de escritorio en la 5.0, Rust entró con Tauri,
  aislado en `escritorio/`, justo en esas condiciones.)

## ADR-009: El log de auditoría no rota
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: `logs/audit.log` crece sin límite y su lectura completa degradaba
  `/audit` y `/status` de forma lineal.
- **Decisión**: Mantener el log sin rotación (es un registro de seguridad: rotar sería
  descartar evidencia) y resolver el rendimiento leyendo sólo la cola del fichero.
- **Consecuencias**: 50 entradas de un log de 3,34 MB se leen en 8,4 ms. El log de
  aplicación (`logs/morgan.log`), que sí es prescindible, rota a 2 MB con 3 copias.

## ADR-010: Capa de repositorios; SQLite por defecto, proveedor sustituible
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: La V1.2 pedía evaluar Supabase/PostgreSQL y, sobre todo, que el Core no
  quedara acoplado a un proveedor concreto. Antes de la V1.2 el `MemoryManager` hablaba
  directamente con el almacenamiento y no existía ninguna abstracción por entidad.
- **Decisión**: Introducir `SessionRepository`, `MessageRepository` y `MemoryRepository`
  como interfaces abstractas, más una `RepositoryFactory` que las reúne. Se envía una
  única implementación, la de SQLite. El Core trabaja con dataclases (`Session`,
  `Message`, `MemoryRecord`) y nunca ve SQL.
- **Alternativas consideradas**:
  1. *Implementar Supabase ya*: sincronizaría entre dispositivos, pero la memoria
     personal pasaría a la nube, Morgan dejaría de funcionar sin conexión y cada lectura
     sumaría latencia de red. Para un agente que actúa sobre la máquina local, el
     beneficio no compensa.
  2. *Ambos backends seleccionables por configuración*: más superficie que mantener y
     probar sin una necesidad demostrada.
  3. *Seguir sin abstracción*: incumple el requisito de proveedor sustituible.
- **Consecuencias**: Añadir PostgreSQL o Supabase consiste en implementar tres clases y
  cambiar qué fábrica se construye; nada por encima cambia. No existe ninguna llamada a
  `sqlite3` fuera de `src/memory/`. A cambio, hay una capa más de indirección.

## ADR-011: El historial se persiste entero, pero se recupera por ventana
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: Al persistir conversaciones aparece la pregunta de cuánto reinyectar al
  reanudar. Cargarlo todo da continuidad, pero infla el contexto de cada petición al LLM.
- **Decisión**: Guardar todos los mensajes, y al reanudar cargar solo los
  `MORGAN_HISTORY_WINDOW` más recientes (20 por defecto), y únicamente los de rol `user`
  y `assistant`.
- **Razón de excluir los mensajes de herramienta**: un resultado de herramienta sin la
  llamada que lo originó es un mensaje huérfano, y los proveedores rechazan el historial.
- **Consecuencias**: El coste por petición queda acotado y no se repite el problema del
  prompt creciente corregido en la V1.0. A cambio, en conversaciones muy largas Morgan
  no recuerda lo dicho al principio; la solución futura es resumen o búsqueda semántica,
  no ampliar la ventana.

## ADR-012: Autenticación por token compartido, no por usuarios
- **Fecha**: 2026-09-05
- **Estado**: Aceptado (superado por las cuentas propias en la V2.0; el token compartido sigue
  para un Morgan privado, ver [autenticacion.md](autenticacion.es.md))
- **Contexto**: Morgan ejecuta comandos sobre la máquina donde corre. Sin autenticación
  solo puede escuchar de forma segura en `127.0.0.1`, lo que impide usarlo desde otro
  dispositivo.
- **Decisión**: Un token compartido opcional (`MORGAN_API_TOKEN`). Sin él, el
  comportamiento no cambia y se registra un aviso. Con él, toda la API lo exige.
- **Alternativas**: un sistema de usuarios y sesiones sería lo correcto para varios
  usuarios, pero Morgan es un agente personal: sobra complejidad para un solo dueño.
- **Consecuencias**: Permite exponer Morgan detrás de HTTPS con un secreto. No sustituye
  a un control de acceso real: quien tenga el token tiene el equipo. `/health` queda
  abierta para poder comprobar disponibilidad sin repartir el token.

## ADR-013: Separación de capacidades por entorno de ejecución
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: Morgan debe poder funcionar como asistente web accesible desde un
  navegador y, además, como agente local con acceso al sistema operativo. Sin una
  separación explícita, un despliegue en la nube expondría `execute_command`,
  `delete_file` y `kill_process` sobre el servidor, y cualquiera que alcanzara la API
  tendría una consola.
- **Decisión**: Cada herramienta declara `requires_local`. Con `MORGAN_ENVIRONMENT=cloud`,
  las que lo tienen a `True` **se excluyen del registro**, no se limitan por permisos.
- **Por qué excluir en lugar de bloquear**: lo que no está registrado no puede invocarse
  ni aparece en el esquema que ve el modelo. Depender solo de la capa de permisos deja
  la puerta cerrada pero puesta: un cambio de configuración o un fallo la abriría.
- **Por qué `True` por defecto**: es fail-safe. Olvidarse de clasificar una herramienta
  nueva la deja fuera del cloud, no dentro. Equivocarse por exceso cuesta una capacidad
  sin exponer; equivocarse por defecto expondría el ordenador del usuario.
- **Consecuencias**: en cloud quedan 5 herramientas de 25 (memoria y web). El acceso a
  la máquina desde un Morgan remoto exigiría en el futuro un agente local explícito con
  su propia autenticación; queda como dirección, no implementado. (Se implementó en la 3.0: el
  agente local, con su propia credencial y su política.)

## ADR-014: Límites de tiempo explícitos en el razonamiento
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: Los clientes LLM se construían sin timeout propio y heredaban los del
  SDK (`connect 5 s`, `read 60 s`, 2 reintentos). Con `max_iterations=6` y un
  `FallbackProvider` que después intenta Gemini con sus propios reintentos, un solo
  turno podía bloquearse más de 15 minutos con la red degradada.
- **Decisión**: tres límites combinados: 30 s por llamada al modelo, 1 reintento del SDK
  y un **tope de reloj de 120 s para el turno completo**.
- **Cómo se eligieron los valores**: midiendo. La latencia normal de Groq es de 0,42 s
  de media (5 llamadas: 0,37–0,48 s); bajo 6 peticiones concurrentes se observaron
  hasta 44 s. 30 s deja ~70× el caso normal sin permitir que una llamada colgada consuma
  minutos. Un solo reintento porque, con un proveedor de respaldo detrás, insistir más
  solo alarga la espera antes de conmutar.
- **Por qué hace falta el tope de turno**: acotar las iteraciones no acota el tiempo.
  Seis iteraciones de duración indefinida siguen siendo indefinidas.
- **Consecuencias**: una petición no puede bloquear Morgan más de ~2 minutos. Si se
  alcanza el tope, el usuario recibe una explicación y una sugerencia, no un cuelgue.

## ADR-015: Estado por servicio, calculado y no bloqueante
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: Un indicador único `internet = sí/no` es engañoso: puede haber conexión
  y estar caído Supabase, o responder Supabase y estar caído el proveedor LLM.
- **Decisión**: un registro con una entrada por dependencia, cada una con su propia
  comprobación, su TTL y su retroceso exponencial. El estado global se **calcula** a
  partir de los individuales en lugar de almacenarse.
- **Por qué calculado**: un estado global guardado aparte se desincroniza del real en
  cuanto una dependencia cambia.
- **Por qué no bloqueante**: quien consulta lee el último valor conocido. Si una petición
  del usuario tuviera que esperar a un sondeo, la comprobación de salud se convertiría
  en la causa de la lentitud que pretende detectar.
- **Consecuencias**: el estado puede tener hasta 30 s de antigüedad, lo que se expone en
  `age_seconds`. Es un intercambio deliberado: información ligeramente vieja a cambio de
  no penalizar ninguna petición.

## ADR-016: SQLite y Supabase coexisten; quién manda depende del entorno
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: La V1.3 pide que Morgan funcione online mediante web y también en local
  con acceso al sistema, sin que una modalidad destruya la otra.
- **Decisión**: no sustituir SQLite por Supabase. En el entorno `local`, SQLite es la
  fuente de verdad y Supabase el destino de la sincronización. En el entorno `cloud`,
  Supabase pasa a ser el almacén principal.
- **Por qué cambia según el entorno**: en la nube no hay disco local que sobreviva a un
  redespliegue, así que un SQLite allí sería memoria volátil disfrazada de persistencia.
  En el equipo del usuario, en cambio, depender de la red para leer su propia memoria
  contradice el principio de que Internet mejora Morgan pero no lo condiciona.
- **Consecuencias**: ambas implementaciones cumplen las mismas interfaces, así que el
  Core no distingue cuál está debajo. En cloud no hay cola de sincronización, porque no
  hay nada local que subir.

## ADR-017: Cliente PostgREST propio en lugar del SDK de Supabase
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: Hay que hablar con Supabase desde Python.
- **Decisión**: implementar un cliente mínimo sobre `httpx` contra la API PostgREST,
  en lugar de añadir `supabase-py`.
- **Razón**: aquí solo hacen falta cuatro operaciones (select, insert, upsert, delete)
  sobre tres tablas. El SDK arrastraría `gotrue`, `realtime` y `storage3`, que Morgan
  no usa, más sus dependencias transitivas. La especificación pide explícitamente no
  añadir dependencias innecesarias.
- **Coste asumido**: hay que mantener el manejo de errores y las cabeceras a mano. Se
  mitiga traduciendo todo fallo a `MemoryStorageError`, el mismo error que usa la capa
  local, para que quien llama no tenga que saber con quién habló.

## ADR-018: La sincronización sube, pero todavía no baja
- **Fecha**: 2026-09-05
- **Estado**: Aceptado
- **Contexto**: El §11 de la especificación limita la V1.3 a preparar la
  infraestructura, y el §12 advierte de no convertir la versión en un proyecto de
  sincronización.
- **Decisión**: implementar la cola outbox y la subida local → nube. Dejar la bajada y
  la fusión para la V1.4.
- **Razón**: bajar datos remotos y mezclarlos con los locales es donde vive la
  corrupción de datos: exige resolver identidad entre equipos, orden de eventos,
  borrados propagados y conflictos reales. Hacerlo deprisa en una versión cuya
  prioridad declarada es la estabilidad sería contradecirse.
- **Preparación dejada hecha**: `updated_at` en las tres tablas, `origin_device` para
  saber qué equipo escribió cada fila, y `client_id` en `messages` como identidad
  estable entre equipos (el id local es autoincremental y distinto en cada máquina,
  así que no sirve para deduplicar).
- **Nota del 2026-09-10 (V2.0)**: la V1.4 llegó y pasó, y la bajada no se hizo.
  Tampoco está en el roadmap de la 2.0. El texto de arriba se deja como estaba
  —un ADR registra lo que se decidió entonces, no lo que pasó después— pero la
  fecha que prometía ya no vale y decirlo importa: el estado real es **aplazada
  sin fecha**. Lo que cambió entretanto es que la nube pasó a ser el sitio
  principal, así que la bajada solo hace falta para quien use los dos Morgan a
  la vez. Retirarla del plan o programarla es decisión del creador; está
  explicado en [datos.md](datos.es.md).
- **Retirada el 2026-09-18 (V2.0.34)**, por decisión mía. La bajada no se va a
  hacer: la nube es la fuente de verdad, y con el agente local de la 3.0 también lo
  será para el equipo de la persona, que trabajará contra ella y no contra una copia
  que haya que fusionar. Se quita de los planes y de los pendientes; el esquema
  conserva `updated_at`, `origin_device` y `client_id` porque los usa la subida.
- **Estrategia de conflictos elegida para V1.4**: last-write-wins por `updated_at`,
  registrando el valor descartado. Con un solo usuario en varios dispositivos, los
  conflictos reales son raros y un merge automático añadiría más riesgo que valor.
