# Seguridad: permisos, validación, auditoría y roles

[English](seguridad.md) · **Español**

> Junta lo que antes eran el documento de seguridad y el de roles. Las cuentas,
> sesiones, CSRF y frenos de acceso están en [autenticacion.md](autenticacion.es.md).
> Última revisión: 2026-09-25, V3.5.0 (el agente local, §5).

La IA **no tiene acceso ilimitado** al equipo. Cada herramienta declara su riesgo, y
ninguna se ejecuta sin pasar por `PermissionManager`, que valida comandos y rutas y
deja constancia en la auditoría.

## 1. Niveles de riesgo

| Nivel | Comportamiento | Herramientas |
|---|---|---|
| 🟢 `safe` | Automático | Lecturas: archivos, procesos, memoria, web, código, `git_status`, `git_diff`, tareas, planes |
| 🟢 `low_risk` | Automático | Reservado |
| 🟡 `moderate` | Pregunta (o automático con `MODERATE_PERMISSION_MODE=auto`) | Crear, copiar, mover y renombrar archivos, `patch_file`, `run_tests`, `git_commit`, `forget_fact`, `remove_knowledge` |
| 🟠 `high_risk` | Siempre pregunta | Reservado |
| 🔴 `critical` | Siempre pregunta | `delete_file`, `execute_command`, `kill_process` |

### Orden de evaluación (la primera que decide, corta)

1. Nivel desconocido → **denegada** (fail-safe).
2. `CommandValidator` en `execute_command`: bloquea `format`, `diskpart`, `bcdedit`,
   `reg delete hklm`, `shutdown`, borrados recursivos de raíz y fork-bombs **antes de
   preguntar**.
3. `PathValidator` en todo lo que escribe: deniega raíces de unidad, `C:\Windows` y
   `Program Files`, con sus subdirectorios.
4. Bloqueada explícitamente → denegada. Permitida explícitamente o en la sesión → aprobada.
5. `safe` y `low_risk` → aprobadas; `moderate` según el modo; `high_risk` y `critical`
   → siempre preguntan.

**Sin consola, se deniega de inmediato**, nunca se espera una respuesta que nadie va a
dar ([ADR-007](decisions.es.md)). En la web, lo que actúa fuera de Morgan se autoriza con
**un plan aprobado** ([agente.md](agente.es.md#3-planes-decir-qué-se-va-a-hacer-antes-de-hacerlo)).

## 2. Otras defensas

- **La memoria es de quien pregunta** (4.0): el resumen de recuerdos, nombre y
  preferencias se lee en cada turno para la persona del turno y nunca se escribe en el
  prompt del agente, que es uno para todas las cuentas. Hasta la 4.0 se escribía ahí, y los
  turnos de todos llevaban la memoria de quien la guardó la última vez
  (`tests/test_memoria_por_persona.py`).
- **Procesos protegidos**: `kill_process` rechaza `lsass`, `csrss`, `winlogon`,
  `services`, `svchost`, `explorer` y demás críticos.
- **Secretos enmascarados** en `get_environment` y en la auditoría (`KEY`, `SECRET`,
  `PASSWORD`, `TOKEN`, `CREDENTIAL`, `AUTH`, `APIKEY`, `PRIVATE`), y por patrón en el
  registro de la aplicación (`gsk_`, `AIza`, `sk-`, `api_key=`, `token=`, `password=`).
- **Inyección de instrucciones**: lo descargado va en `<untrusted_web_data>` y lo de
  archivos subidos en `<untrusted_file_data>`; el prompt enseña a tratarlo como dato.
  Descargas de hasta 250 KB y 12.000 caracteres.
- **Páginas comprimidas** (2.0.15): gzip y deflate con el mismo tope de 250 KB; una
  compresión desconocida se rechaza por su nombre, y un cuerpo con más de un 5 % de
  caracteres de reemplazo no se entrega (python.org llegaba como basura comprimida).
- **SSRF**: `read_webpage` se ejecuta sin confirmación, así que rechaza `localhost`,
  redes privadas, link-local (`169.254.169.254`), multicast, reservadas y todo lo que
  no sea http/https.
- **Las cabeceras de la web** (`vercel.json`, completadas en la 5.2 desde la lista de la
  4.22): HSTS, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, y además una **CSP**
  que solo deja scripts propios más el del tema por su hash (sin `unsafe-inline` ni
  `unsafe-eval`), conexiones solo al propio dominio (la API va por `/api`) y que nadie
  meta la web en un marco; **Permissions-Policy** (sin cámara, ubicación, pagos ni USB; el
  micrófono, solo ella) y **COOP**. Probadas en un navegador de verdad antes de publicarlas
  (`scripts/probar_cabeceras_web.py`: Edge sin ventana, la web compilada servida con esas
  cabeceras, cero bloqueos; y caza un hash equivocado, las fuentes sin permitir y las
  conexiones cortadas). `tests/test_cabeceras_web.py` vigila que el hash siga al script.
- **Validación de argumentos** contra el JSON Schema antes de ejecutar, en el agente y
  en `POST /tools/{nombre}`.
- **Opciones inyectadas**: `git_diff` pone la ruta tras `--`. Sin él,
  `file_path='--output=/donde/sea'` hacía que una herramienta de lectura **escribiera
  un fichero**.
- **Lo que el modelo escribe no cambia a qué se llama**: nombres con `.` o `..` se
  rechazan, se codifican, y GitHub comprueba que la URL que sale es la pedida. Con
  `ruta='../../../../user/emails'` una herramienta de «leer un archivo del repositorio»
  devolvía los correos privados de la persona ([integraciones.md](integraciones.es.md)).

## 3. Auditoría

`logs/audit.log` registra **todo intento**, autorizado o no, en JSON Lines: fecha,
herramienta, riesgo, argumentos saneados, autorización, resultado y error. También las
acciones administrativas (quién, sobre quién, qué cambió), subidas y rechazos de
archivos. **No rota**: es evidencia; se lee la cola del fichero ([ADR-009](decisions.es.md)).
**No se borra al borrar una cuenta**: registra qué hizo Morgan, y borrarla sería una
forma de tapar un rastro. Se consulta con `/audit` en la CLI, `GET /audit` o la vista
Auditoría.

**Qué significa `success`** (aclarado en la 4.1, tras leer la auditoría de la evaluación
4.0.5): en la anotación de cada **permiso** repite la autorización (se escribe antes de
ejecutar, cuando aún no hay resultado). El **resultado** real de lo que cambia algo va en
una segunda anotación, la de la ejecución autorizada por un plan (`autorizado_por_plan` en
los argumentos). Para saber si algo salió, se mira esa, o el veredicto de la verificación.

## 4. Roles y propietario

```
USER  →  ADMIN  →  OWNER
```

> **La autorización se decide por permisos, nunca por quién eres.** Nada de
> `if user.email == "..."`: la cadena es usuario → rol → permisos → autorización, y el
> rol se resuelve **siempre en el servidor**.

| Permiso | Autoriza | USER | ADMIN | OWNER |
|---|---|:--:|:--:|:--:|
| `users.read` | Ver cuentas | | ✅ | ✅ |
| `users.manage` | Cambiar roles, suspender | | | ✅ |
| `audit.read` | Leer la auditoría | | ✅ | ✅ |
| `system.manage`, `integrations.manage`, `settings.manage` | La instalación | | | ✅ |

Lo propio de cada uno (conversaciones, memoria, archivos) no necesita permiso: ya está
aislado por usuario.

### Qué se le levanta al propietario, y qué no

| Se levanta | No se levanta |
|---|---|
| Cupo diario (paga las claves) | Validación de comandos y rutas: protege al dueño de lo que **el modelo** proponga |
| Límites de archivos | Auditoría |
| Confirmaciones en la web (sin consola, preguntar denegaría) | Herramientas bloqueadas a mano |
| | Las que no existen en la nube: esa máquina es un contenedor de Render, no su PC |
| | **El aislamiento**: ser dueño no da acceso a los datos de nadie |
| | **`exige_plan`**: también el propietario necesita el plan aprobado |

**El modo sin cuentas no es un propietario con privilegios.** El Morgan de tu equipo
tiene rol de propietario, pero hay consola y las confirmaciones sirven. Las exenciones
son solo para el dueño **que ha entrado con su cuenta** donde no hay a quién preguntar,
y viven en una sola función (`propietario_con_cuenta`).

### Cómo se establece

`MORGAN_OWNER_EMAIL=tu@correo`. Al arrancar: si ya hay Owner, nada; si existe una cuenta
con ese correo, se promociona y se audita; si no existe, **no se crea** (habría que
inventar una contraseña). Te registras, reinicias y eres propietario; después la
variable sobra. **No hay contraseña maestra, no traspasa la propiedad** si ya hay otro
dueño, y un **índice único parcial** en la base impide dos propietarios aunque el código
lo olvide.

### Rutas administrativas

`GET /admin/usuarios` (`users.read`), `GET /admin/yo/permisos` (para que la interfaz no
ofrezca botones que darán 403: **no es seguridad**), `POST /admin/usuarios/{id}/rol` y
`POST /admin/usuarios/{id}/estado` (`users.manage`).

Incluso al propietario: no puede cambiarse el rol ni suspenderse a sí mismo, crear un
segundo propietario ni tocar el rol del propietario. Un rol inventado se **rechaza**
(al leer de la base, uno desconocido cae a `user`). **Suspender revoca las sesiones en
el momento.**

## 5. El agente local: otra frontera

Desde la 3.0, Morgan en la nube actúa en el PC de la persona a través de un **agente
local** que ella instala. Es la parte más delicada del proyecto, y **no se fía de la
nube**: aunque la nube lo haya comprobado todo, el agente lo vuelve a comprobar con sus
propias reglas, que solo se cambian en el PC. Resumen de las capas; el detalle, el modelo
de amenazas y las evaluaciones en agente-local.md.

| Capa | Qué para |
|---|---|
| Emparejamiento y credencial `mga_` (cifrada con DPAPI) | Que un PC ajeno se haga pasar por el tuyo, o la nube mande a quien no toca |
| Política local (3.2): carpetas, bloqueadas, capacidades, límites | Todo nace cerrado; lo bloqueado gana sobre lo permitido; los límites solo bajan; la nube no puede cambiarla |
| Frontera de salida (3.1) | Secretos tapados, nombres como datos, tope de lo que sale, y que una web no reciba lo leído del PC |
| Plan aprobado (`exige_plan`) | Que se escriba, se ejecute o se termine algo sin que la persona apruebe esos argumentos exactos |
| Confirmación en el PC (3.3) | Borrar, ejecutar lo que cambia y terminar procesos piden «Permitir» en una notificación de Windows |
| Zonas donde no se escribe nunca | La carpeta del agente, Inicio, el sistema, los datos de las aplicaciones, `.git`; ni programas ni scripts |
| Terminal de catálogo sin shell (3.5) | Que la nube ejecute cualquier cosa: no hay PowerShell, y git va sin *hooks* ni `fsmonitor` |
| Motor de ejecución (3.4) | Hacer algo dos veces, o decir lo contrario de lo que hay en el disco |
| Órdenes con su hora de salida (3.6) | Una orden fantasma: retenida en una conexión medio abierta, que llega cuando la nube ya dijo que no se hizo |
| Actualizaciones firmadas por mí (3.8) | Que quien tome la nube (o se ponga en medio) haga que los PC ejecuten código que yo no publiqué, o los devuelva a una versión vieja con un fallo |
| Credencial en dos pasos, cada 90 días (3.8) | Que una credencial robada sirva para siempre; y que rotarla deje un PC fuera por un corte a mitad |
| Registro por PC y elegir el equipo (3.7) | Con varios PC, una orden (o su recuperación, o su cancelación) en el equivocado; y que Morgan elija uno sin decirlo, también mientras el otro se reconecta (3.7.5) |

**La terminal del Morgan local no es esta.** `execute_command` (§2) ejecuta PowerShell con
una lista de patrones prohibidos: vale como red en tu equipo, con tu consola delante, pero
una lista de lo prohibido se salta. Desde la nube solo existe la del agente.

**Morgan para Windows (5.x)** añade dos más: la ventana de la conversación carga la web **sin
ningún permiso sobre el programa** (no puede pausar, emparejar ni tocar el PC desde ahí) y solo
navega por Morgan; y su desinstalador e instalador solo desemparejan el agente si lo emparejó el
programa, y nunca al actualizar.

## 5 bis. Las automatizaciones: nadie delante (4.14)

Una automatización actúa a una hora en la que nadie mira: la frontera de confianza más
grande desde el agente local. Diseño y mis decisiones en plan-4.x.md.

| Capa | Qué para |
|---|---|
| Crearla es un **plan rojo** (`create_automation`, `exige_plan`) | Que un archivo con instrucciones escondidas deje algo programado: siempre la aprueba la persona, también con el permiso automático |
| **Solo consultas** en la 4.14 (`src/automatizacion/contexto.py`, lista blanca) | Que cambie algo sin nadie delante. El núcleo lo comprueba al ofrecer el catálogo **y** al ejecutar; nada que pida «Permitir» en el PC |
| Lo que cambia algo ni se propone (`prevalidar`) | Que la persona apruebe «borra cada noche…» creyendo que se hará |
| Corre como su dueño, **con su rol y su cupo** | Que una automatización gaste sin límite o con más privilegio del que tiene quien la creó; una cuenta borrada o bloqueada no ejecuta nada |
| El reloj llama con **su propio secreto** (`MORGAN_RELOJ_SECRETO`, en tiempo constante; sin él, la ruta no existe) | Que alguien de fuera lance ejecuciones. Aun con el secreto, solo se ejecuta lo que ya tocaba |
| **Reclamo atómico** (un contador en la fila) | Que dos relojes cruzados la ejecuten dos veces |
| Conversación temporal, sin permiso automático | Que lo que haga quede mezclado con el historial de la persona, o que se apruebe algo «porque estaba encendido» |
| **Pasos fijos** (4.15): solo llamadas **idénticas** a un paso aprobado (`contexto.permitida`) | Que se haga algo distinto de lo aprobado: otra ruta, otra herramienta, o «otro camino» tras un fallo. Al modelo no se le ofrece ninguna herramienta |
| Solo pasos verdes y amarillos, ni consultas, ni planes, tareas o memoria | Que se programe algo que pide «Permitir» (y no se haría) o que siembre algo en Morgan |
| Solo `{fecha}` y `{hora}`; argumentos contra el esquema de cada herramienta | Que se apruebe un paso que nunca saldría, o que escriba marcas sin sustituir |
| El aviso de un fallo es el informe real, sin modelo | Que la bandeja cuente lo que no pasó (medido: el modelo lo inventaba) |

## 6. Límites conocidos

- **Sin `MORGAN_API_TOKEN` ni cuentas, la API no exige nada.** Aceptable solo
  escuchando en `127.0.0.1`. En la nube el arranque **aborta** sin una de las dos.
- **El «login CSRF» no está cubierto**, a sabiendas ([autenticacion.md](autenticacion.es.md)).
- **El registro está abierto** (decisión mía). El total diario lo acota el **cupo
  global** (2.0.24: 150 mensajes entre todas las cuentas que no son la del
  propietario), y el dinero, el tope del panel de OpenAI.
- `MODERATE_PERMISSION_MODE=auto` quita la confirmación de escrituras y commits; las
  validaciones siguen.
- **El plan que autoriza escribir en el PC lo exige la nube.** Una nube comprometida
  podría pedir crear, editar o mover sin plan; con la decisión mía (solo borrar,
  ejecutar y terminar se confirman en el PC), eso no se confirma allí. Lo para la
  política del PC; quien quiera más, enciende `confirmar escribir si` (aceptado por mí
  en la 3.3.5).
- **El fichero de la política es del usuario de Windows**: un programa que ya corra con
  sus permisos puede editarlo. Fuera del modelo de amenazas (un PC con malware con tus
  permisos está fuera de alcance).

## Pruebas

`test_permissions.py`, `test_permiso_confirmacion.py`, `test_security_advanced.py`,
`test_regressions_audit.py`, `test_roles.py` (47),
`test_privilegios_owner.py` (24), `test_web_comprimida.py`, y las de aislamiento y
frenos listadas en [autenticacion.md](autenticacion.es.md). Del agente local:
`test_agente_*.py`, `test_motor_politica.py`, `test_motor_ejecucion.py`,
`test_canal_agente.py`, `test_bandeja.py` y la batería de estrés (`test_estres_ejecucion.py`, con
`MORGAN_ESTRES=1`).
