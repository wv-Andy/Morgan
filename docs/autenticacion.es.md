# Autenticación y cuentas

[English](autenticacion.md) · **Español**

> Cómo entra la gente en Morgan, cómo se guardan las contraseñas y qué separa los
> datos de una persona de los de otra. **Identidad, de la V2.0 adelantada.**

Morgan tiene su **propio** sistema de acceso: nombre de usuario o correo, y
contraseña. No hay proveedor externo de identidad.

## Por qué propio y no «entrar con Google»

Se evaluó Supabase Auth con Google y GitHub y se descartó por complejidad: obliga
a registrar aplicaciones en dos paneles ajenos, a mantener secretos en tres
sitios, a lidiar con URLs de retorno que fallan con mensajes que no explican nada,
y a que el arranque del proyecto dependa de pantallas de verificación de Google.
Para un asistente personal que quiere abrirse a unos cuantos usuarios, es mucho
aparato.

**Esto no cierra la puerta a Google ni a GitHub.** Al contrario, y conviene
insistir porque es la confusión más cara de este terreno:

| | Qué significa | Estado |
|---|---|---|
| **Login con GitHub** | GitHub confirma quién eres, y esa es tu forma de entrar | Descartado |
| **Conectar GitHub a Morgan** | Morgan lee tus repos, tus *issues* y tus PRs | ✅ Hecho en la V1.9, y en uso: [integraciones.md](integraciones.es.md) |

Son cosas distintas y se diseñan por separado. La segunda será una **integración**:
su propia tabla, sus propios permisos, colgando de tu cuenta de Morgan. Mezclarlas
—que tu identidad *sea* tu cuenta de GitHub— es lo que obliga después a rehacerlo
todo cuando alguien quiere entrar con correo, o conectar dos servicios, o
desconectar uno sin perder el acceso.

## Las piezas

```
Navegador                     API                        Almacén
─────────                     ───                        ───────
cookie morgan_sesion  ──►  identidad_middleware  ──►  auth_sessions
(HttpOnly)                        │                    (id = hash del token)
                                  ▼
cookie morgan_csrf    ──►  fijar_usuario(id)      ──►  ContextVar
(legible)                         │
                                  ▼
X-Morgan-CSRF         ──►  toda consulta filtra por usuario
```

| Fichero | Qué hace |
|---|---|
| [`src/identidad/password.py`](../src/identidad/password.py) | Hasheo y validación de contraseñas e identificadores |
| [`src/identidad/cuentas.py`](../src/identidad/cuentas.py) | La política: registro, acceso, sesiones, recuperación |
| [`src/identidad/repositorio.py`](../src/identidad/repositorio.py) | Dónde se guarda: SQLite o Supabase |
| [`src/identidad/correo.py`](../src/identidad/correo.py) | Envío real del enlace de recuperación |
| [`src/api/sesion_web.py`](../src/api/sesion_web.py) | Cookies, CSRF, origen de la petición |
| [`src/api/identidad_middleware.py`](../src/api/identidad_middleware.py) | Quién hace cada petición, y qué pasa si nadie |
| [`src/api/routes/cuentas.py`](../src/api/routes/cuentas.py) | Las rutas `/auth/*` |
| [`src/identidad/tokens.py`](../src/identidad/tokens.py) | Tokens personales de API: crear, resolver, alcances y lo que un token no puede nunca |
| [`web/src/components/Cuenta.tsx`](../web/src/components/Cuenta.tsx) | Pantalla de acceso y estado de sesión |
| [`web/src/components/PanelCuenta.tsx`](../web/src/components/PanelCuenta.tsx) | Cuenta, contraseña y sesiones, dentro de Ajustes |

## Contraseñas

Se usa **`scrypt`**, de `hashlib`, estandarizado en el RFC 7914. Es un algoritmo
pensado para contraseñas: costoso en memoria además de en tiempo, que es lo que
encarece los ataques con tarjetas gráficas.

Se eligió frente a `bcrypt` y `argon2-cffi` por no añadir una dependencia con
extensión en C. Las tres son defendibles; esta no obliga a compilar nada ni en
Windows ni en el contenedor de Render.

El formato guarda los parámetros junto al hash:

```
scrypt$16384$8$1$<sal en base64>$<hash en base64>
```

Así, si dentro de dos años hay que subir el coste, las contraseñas antiguas siguen
verificándose con sus parámetros originales y **se rehashean solas al entrar**,
aprovechando el único momento en que la contraseña está en claro.

Medido en el equipo de desarrollo: **44 ms por hash**. Suficiente para encarecer
un ataque por fuerza bruta sin que iniciar sesión se note lento.

**Como mucho 4 hashes a la vez** (V2.0.27). Cada uno reserva 16 MB, y la prueba de
carga midió 498 MB con 25 altas simultáneas: el plan gratuito de Render tiene 512.
Un pico de inicios de sesión —también fallidos— habría tumbado el servicio, y los
frenos no lo evitan porque cuentan intentos, no simultaneidad. Con el semáforo el
techo de los hashes es 64 MB, y el pico medido del servidor con 60 altas a la vez,
176 MB. No cuesta tiempo: el cálculo es de CPU, y más en paralelo solo repartía la
misma CPU (mediciones.md).

Requisitos: mínimo 8 caracteres, máximo 200. **No se exigen mayúsculas, números ni
símbolos**: esas reglas empujan a la gente hacia contraseñas cortas y predecibles
del tipo `Passw0rd!`. La longitud es lo que importa.

## Sesiones

- Viven en una cookie **`HttpOnly`**. Guardarlas en `localStorage` las pondría al
  alcance de cualquier script inyectado, y en una aplicación que renderiza texto
  de un modelo eso no es una hipótesis remota.
- En la base se guarda el **hash** del identificador, no el valor. Quien lea la
  base no debe poder suplantar a nadie, igual que con las contraseñas.
- Duran **30 días**. Pedir la contraseña cada semana no aporta seguridad real y
  empuja a elegir contraseñas más simples.
- Se invalidan **en el servidor** al salir. Borrar la cookie sin más dejaría la
  sesión viva treinta días para quien tuviera copia del token.
- Cambiar o recuperar la contraseña **cierra las demás sesiones**. Si alguien te
  la había robado, cambiarla no serviría de nada mientras su sesión siguiera viva.

## Dónde vive la API, y por qué importa para la cookie

**El navegador tiene que ver la API en el mismo origen que la página.** No es una
preferencia de arquitectura: es la condición para que la sesión exista.

El frontend está en Vercel y la API en Render, que son dominios distintos. Con la
web llamando directamente a Render, la cookie de sesión es una **cookie de
terceros**, y Safari en iPhone las descarta sin excepción. El efecto era exacto:
te registrabas y la web te devolvía al login para siempre, sin ningún error en
ninguna parte. Está contado entero en
[web.md](web.es.md).

Por eso `vercel.json` reenvía `/api/*` a Render **desde el mismo
origen**. La página llama a `morgan-ia.vercel.app/api/auth/yo`, y la cookie
que vuelve pertenece a `morgan-ia.vercel.app`: primera parte, y ningún
navegador la discute.

Lo que hay que saber si se toca esto:

- El proxy va **antes** que la regla comodín de la SPA en `vercel.json`.
  Detrás, no se aplica nunca.
- El borde **no cachea** `/api` (`x-vercel-enable-rewrite-caching: 0`).
  Una respuesta de sesión cacheada y servida a otra persona sería una fuga entre
  cuentas.
- El frontend decide su URL **en el build**, no en el panel de Vercel.
- Lo fija [`test_proxy_mismo_origen.py`](../tests/test_proxy_mismo_origen.py).

## CSRF

En la nube la cookie se sigue marcando `SameSite=None; Secure`. Ya no hace falta
para cruzar dominios —el proxy los unificó— pero se mantiene: la API también se
alcanza directamente, y una cookie que solo funcione a través de Vercel ata la
sesión a un despliegue concreto.

`SameSite=None` significa que la cookie viaja también en peticiones que provoque
otra web, así que **la protección contra CSRF tiene que estar en otro sitio**.

La defensa es el **doble envío**: además de la cookie de sesión hay un token
CSRF que el cliente repite en la cabecera `X-Morgan-CSRF`. Una web ajena puede
provocar la petición, pero no puede **conseguir** el token para rellenar la
cabecera, ni ponerla sin disparar un *preflight* que CORS rechaza.

En local se usa `SameSite=Lax` y las cookies no se marcan `Secure`: `http://localhost`
no es HTTPS y el navegador las descartaría, con el efecto de no poder entrar en
desarrollo.

### De dónde saca el cliente el token

De dos sitios, y el segundo existe por un fallo que dejó **la web entera en solo
lectura durante toda la V1.8**:

1. **La cookie `morgan_csrf`**, si puede leerla. Es la fuente directa y siempre
   está al día.
2. **El JSON de `/auth/login`, `/auth/registro` y `/auth/yo`**, que lo devuelven
   en el campo `csrf`. El cliente lo guarda en `localStorage`.

El primer camino **solo funciona cuando la web y la API comparten dominio**.
Hoy lo comparten, gracias al proxy; durante toda la V1.8 no, y ahí estuvo el
fallo: la cookie pertenecía a `morgan-api.onrender.com` y el JavaScript
corría en `morgan-ia.vercel.app`. El navegador *enviaba* la cookie en cada
petición —así que desde el backend todo parecía correcto— pero
`document.cookie` **no podía leerla desde otro dominio**. La cabecera nunca
se ponía, y **todos** los POST se rechazaban con 403.

El segundo camino se queda aunque el primero ya funcione. Cuesta poco, y es lo
que sostiene la sesión cuando un navegador limpia la cookie legible por su
cuenta — que es justo el caso que dejó la web en solo lectura.

El síntoma no se parecía a la causa. La sesión estaba abierta, `/auth/yo`
respondía bien, los GET funcionaban y la pantalla mostraba tu nombre. Solo fallaba
*hacer* cosas: enviar un mensaje, crear una conversación, subir un archivo,
renombrar. Desde fuera se leía como «Morgan cree que no he iniciado sesión».

**Devolver el token por `/auth/yo` no debilita la defensa.** El doble envío
protege porque una web ajena no puede conseguir el token, y sigue sin poder:
`/auth/yo` responde únicamente a los orígenes de la lista de CORS —comprobado en
vivo: a cualquier otro no le devuelve la cabecera `Access-Control-Allow-Origin`,
y sin ella el navegador le impide leer la respuesta—. Es la misma política de
mismo origen que hacía ilegible la cookie, aplicada un escalón más arriba.

`/auth/yo` **emite un token nuevo** si hay sesión viva y falta la cookie. Pasa de
verdad: los navegadores que limpian cookies de terceros se llevan esta y dejan la
de sesión, y sin reponerla la sesión quedaba en solo lectura hasta volver a
entrar. A quien no tiene sesión no se le da ninguno: si bastara con pedirlo, el
doble envío no protegería de nada.

### Lo que este diseño no cubre

Las rutas de entrada —`/auth/login`, `/auth/registro`, `/auth/recuperar`,
`/auth/restablecer`, `/auth/logout`— están **exentas** de la comprobación CSRF.
Tienen que estarlo: exigir un token que solo se obtiene teniendo sesión impediría
iniciar sesión, y dejaba atrapado a quien volvía con una cookie caducada.

Eso dejaba abierto el **«login CSRF»**: una web ajena podía mandar desde tu navegador un
formulario a `/auth/login` con las credenciales del atacante, y entrabas en *su cuenta* sin
darte cuenta (y lo que escribieras quedaba allí). Hasta la 4.22 se aceptaba a sabiendas.

**Desde la 4.22 esas rutas miran `Origin`** (`sesion_web.origen_propio`). Un navegador dice
siempre de qué página sale una petición, y esa página no puede cambiarlo: si no es la web de
Morgan (la lista de CORS, su expresión de las vistas previas o la misma dirección que la API),
**403 `ORIGEN_AJENO`**. Sin `Origin` no hay navegador de por medio (un programa, la línea de
comandos) ni nadie a quien engañar, así que pasa; `Origin: null`, lo que manda un marco aislado
para esconder la página, se rechaza. No hizo falta un token antes de la sesión.
`tests/test_login_csrf.py`: 14 pruebas, 8 de 8 mutaciones.

## Fuerza bruta

Dos topes en la misma ventana de quince minutos, y los bloqueos se levantan solos:

| Tope | Cuenta | Para qué |
|---|---|---|
| **5 fallos** | por cuenta **y origen** | Que alguien que se equivoca no bloquee a los demás desde otro sitio |
| **20 fallos** | por cuenta, **de cualquier origen** (V2.0.22) | Que no se pueda esquivar el de arriba |

El origen se toma de `X-Forwarded-For` cuando existe, porque detrás de Render o
Vercel `request.client.host` es el proxy y sería el mismo para todo el mundo —
contar por él bloquearía a todos los usuarios a la vez.

> **Aquí decía que falsear esa cabecera «solo sirve para no acumular intentos,
> que es el peor caso de esta protección, no un fallo nuevo».** No acumular
> intentos es no tener freno. La auditoría de la 2.3 lo midió: con un
> `X-Forwarded-For` distinto en cada intento, 20 contraseñas equivocadas seguidas
> dieron 401 —ninguna 429— y la correcta entró después. Por eso existe el segundo
> tope, que no depende de ninguna cabecera.

**Los fallos se apuntan a la cuenta, no a lo tecleado.** Con el nombre de usuario y
el correo como claves separadas, alternarlos daba el doble de intentos.

**El precio, aceptado a sabiendas:** quien ataque una cuenta puede dejarla
bloqueada quince minutos, también para su dueño. Es un bloqueo temporal y no un
robo, y **restablecer la contraseña lo levanta** en el acto.

Acertar borra los intentos previos de ese origen. Sin eso, quien falla cuatro veces
y acierta quedaría a un solo fallo del bloqueo para siempre.

> **Un defecto que solo apareció ejercitándolo.** La primera versión apuntaba el
> intento fallido y lanzaba la excepción dentro del mismo bloque `with`, y
> `Database.connect` solo confirma la transacción si el bloque termina bien: cada
> apunte se revertía con su propio fallo, el contador nunca pasaba de cero y **el
> freno no frenaba nada**. Leyendo el código parecía correcto. Está fijado en
> `TestElFrenoALaFuerzaBruta`.

## Altas: el registro está abierto, y frenado (V2.0.22)

Decidí que cualquiera con el enlace puede crear una cuenta. La auditoría de
la 2.3 midió qué podía hacer un script con eso: **30 cuentas en 4 segundos** desde
el mismo origen, cada una con su cupo diario de mensajes. Bastan para agotar en
minutos la cuota gratuita de todos y empujar el resto al proveedor de pago.

| Tope, en 15 minutos | Por qué ese |
|---|---|
| **3 altas por origen** | Una familia o una clase detrás de la misma IP puede crear sus cuentas |
| **20 altas en total** | No depende de ninguna cabecera: es el que acota a un script que cambie de origen |

Solo cuentan las altas que salen bien: un formulario mal rellenado no gasta el cupo
de nadie. Se apuntan en `login_intentos` con la clave `#registro`, que no puede ser
ni usuario ni correo, y el inicio de sesión la rechaza sin apuntar nada: si no,
veinte fallos tecleándola cerrarían el registro a todo el mundo. (La primera versión
usaba `__registro__`, que sí es un nombre de usuario válido.)

Si se alcanza, la web recibe `429 DEMASIADOS_REGISTROS` con un mensaje que dice
cuánto esperar.

## No se puede averiguar quién tiene cuenta

La enumeración de usuarios se cuela con facilidad porque el sistema «funciona»
igual de bien con ella dentro.

- **Al entrar**, «esa cuenta no existe» y «la contraseña es incorrecta» dan el
  mismo error y el mismo mensaje.
- **Al recuperar**, la respuesta es idéntica exista o no la cuenta, y el token
  nunca viaja en ella: solo llega al correo. Ni siquiera un fallo de envío cambia
  la respuesta, porque decir «no se pudo enviar» también delataría que existe.
- **Al registrarse sí se distingue** entre usuario y correo duplicados. Es
  inevitable: la persona necesita saber cuál de los dos cambiar, y la fuga es la
  misma que produce cualquier formulario de alta.

## Recuperación de contraseña

El token es aleatorio de 256 bits, se guarda **hasheado** (SHA-256 a secas: no es
una contraseña elegida por una persona, no hay nada que adivinar por fuerza
bruta), **caduca en 30 minutos**, sirve **una sola vez**, y pedir uno nuevo
invalida el anterior — que puede estar en un correo viejo.

### El correo es una dependencia real

**No hay sistema de correo simulado.** O hay un servidor SMTP configurado y el
correo sale de verdad, o no sale y se dice claramente. Un `enviar()` que devuelve
`True` sin mandar nada convierte la recuperación en una función rota que parece
funcionar, y el fallo se descubre el día que alguien pierde su contraseña.

| Variable | Para qué |
|---|---|
| `MORGAN_EMAIL_API` | `brevo` o `resend`. **Lo que funciona en la nube** |
| `MORGAN_EMAIL_API_KEY` | La clave del proveedor |
| `MORGAN_EMAIL_FROM` | Remitente; en Brevo, una dirección verificada |
| `MORGAN_SMTP_HOST/PORT/USER/PASSWORD/FROM` | SMTP, para local |
| `MORGAN_WEB_URL` | Base de la web, para construir el enlace |

Hay dos transportes: **API HTTP** (Brevo, Resend) y **SMTP**. La API manda cuando
están las dos.

> **SMTP no funciona en la nube.** Render y la mayoría de planes gratuitos
> bloquean los puertos SMTP salientes, y el envío muere con
> `Network is unreachable` **aunque las credenciales sean correctas**. Se
> descubrió en producción, con Gmail bien configurado. La API HTTP va por el 443
> y ningún hosting la bloquea.

Y el proveedor lo decide otra restricción: **Brevo escribe a cualquiera
verificando solo tu dirección**; Resend, sin un dominio propio, solo escribe a la
tuya. Para Morgan, donde el correo tiene que llegarle a otras personas, esa
diferencia es la que importa.

`/status` incluye un servicio `correo` que dice qué transporte usa y qué pasó en
el último envío. Existe porque el fallo es invisible desde fuera: pedir un enlace
responde igual salga o no salga el correo.

**Sin configurar** no se envía nada y se registra un error: la recuperación no
funciona hasta que se configure, que es justo lo que conviene que se note.

El enlace **no** queda escrito en el log, ni siquiera en local. Se intentó «para
poder probar en desarrollo» y no servía: el filtro de secretos del log enmascara
todo lo que parece `token=`, así que lo que quedaba era `?token=***`. Burlar ese
filtro sería publicar un secreto en el log a propósito. Para probar sin proveedor,
`solicitar_recuperacion()` devuelve el token directamente.

## Verificación del correo

**Verificar no es una puerta.** La cuenta funciona desde el primer momento sin
confirmar nada, y eso es una decisión, no una carencia: obligar a abrir el correo
antes de dejar probar Morgan es la forma más rápida de perder a alguien.

Lo que se pierde sin confirmar es concreto y acotado, y el aviso lo dice tal cual
en vez de amenazar en abstracto:

> Puedes seguir usando Morgan, pero **no podrás recuperar tu contraseña** si la
> olvidas hasta que abras el enlace que te enviamos.

Es literalmente cierto: el enlace de recuperación va a esa dirección. Si estaba
mal escrita, no hay a dónde mandarlo.

### Los dos tipos de token comparten tabla, pero no valen lo mismo

Un token de verificación y uno de recuperación son la misma cosa desde el punto
de vista del almacén —un secreto de un solo uso, atado a un usuario, con
caducidad—, así que **comparten tabla**. Duplicarla habría duplicado también su
limpieza, su comprobación de caducidad y sus propiedades de seguridad, que son
justo las cosas que no conviene tener por duplicado.

Lo que **no** comparten es lo que conceden, y por eso todas las consultas filtran
por `tipo`:

| Tipo | Qué abre | Dura |
|---|---|---|
| `reset` | La cuenta: permite cambiar la contraseña | 30 minutos |
| `verificacion` | Solo confirma una dirección | 24 horas |

Si el tipo no se filtrara, un enlace de «confirma tu correo» valdría para entrar
en la cuenta. Y ese enlace se manda a una dirección que **puede no ser de quien
se registró** — que es precisamente lo que la verificación existe para averiguar.
Hay pruebas de los dos sentidos.

La duración distinta también sale de ahí: quien intercepte un enlace de
recuperación se lleva la cuenta; quien intercepte uno de verificación solo
consigue marcar como verificada una dirección que ya controla.

### Confirmar es público; reenviar exige sesión

| Ruta | Sesión | Por qué |
|---|---|---|
| `POST /auth/verificar` | **No** | Quien abre el enlace puede estar en otro navegador o en el móvil. Pedirle que inicie sesión convertiría un clic en un trámite |
| `POST /auth/verificar/reenviar` | **Sí** | Aquí se manda un correo. Una ruta abierta que dispara correos a partir de una dirección es una herramienta para molestar a terceros |

### Un fallo del correo no impide registrarse

El envío va en segundo plano y envuelto: si el proveedor no responde, la cuenta
se crea igual y el token queda guardado para reintentarlo desde Ajustes. El envío
es lo accesorio.

Cambiar la contraseña invalida los enlaces de recuperación pendientes —que es lo
correcto— pero **no** el de verificación, que no tiene nada que ver y obligaría a
pedirlo otra vez sin motivo.

## Llevarte tus datos, o borrarlos

Las dos operaciones existen, y **juntas** son lo que convierte «tus datos» en
algo real. Por separado cada una es media promesa: si solo se pudiera borrar, la
única forma de conservar una conversación sería copiarla a mano; si solo se
pudiera exportar, irse significaría dejarlo todo ahí.

| | Ruta | Qué hace |
|---|---|---|
| Descargar | `GET /auth/datos` | Un JSON con todo lo que has generado |
| Borrar | `DELETE /auth/cuenta` | La cuenta y sus datos. Sin deshacer |

### Lo que la exportación NO lleva dentro

Un fichero de exportación acaba en la carpeta de descargas, se comparte por
correo y se sube a sitios. Meter ahí una credencial es regalar material para
atacarla sin prisa y sin que nadie se entere.

| Fuera | Por qué |
|---|---|
| El hash de la contraseña | No es un dato tuyo que sirva de algo: es una credencial |
| Los tokens de servicios conectados | Peor: abren cuentas **ajenas a Morgan** |
| Los identificadores de sesión | Cada uno es una llave viva |

Sí se dice **qué** tienes conectado y con qué cuenta —saber que tu Morgan tiene
acceso a tu GitHub es un dato tuyo, y de los que importan—. Lo que no pinta ahí
es la llave.

Hay cuatro pruebas dedicadas solo a esto, porque es lo que costaría caro
equivocar.

### Un fallo parcial se dice

Cada bloque se lee por separado y un fallo en uno no impide exportar los demás.
Lo que no se pudo leer se marca en `incompleto`: una exportación incompleta
que **parece** completa es peor que ninguna, porque quien la guarda cree tener
sus conversaciones y no las tiene.

### En la interfaz, descargar va antes que borrar

No es casualidad del orden de los apartados. Quien llega a esa parte de los
ajustes pensando en irse debería tropezarse primero con la opción de llevarse
sus cosas; al revés, la exportación la encontraría solo quien ya decidió
quedarse.

## Tokens personales de API (V2.0.40)

Lo que usa un cliente **que no es el navegador** —un script, una extensión de
editor, el agente local de la 3.0— para hablar con Morgan. Es la fase 1 del
plan de la API, aprobado el 2026-09-17.

### El hueco, medido (fase 0)

Antes de construir nada se midió cómo entraba un script, **aislado**: base SQLite
temporal, sin Supabase y sin token compartido (el intento del 2026-09-16 quedó
invalidado por las dos cosas). Salida real, antes y después:

```
ANTES (solo sesión): un script sin navegador
  GET /sessions sin nada                                -> 401 SIN_SESION
  POST /auth/login con la CONTRASEÑA                    -> 200
    cookies que hay que guardar: ['morgan_csrf', 'morgan_sesion']
  GET /sessions con la cookie                           -> 200
  POST /sessions con la cookie, sin copiar el CSRF      -> 403 CSRF
  POST /sessions con cookie + cabecera x-morgan-csrf    -> 200

DESPUÉS (token personal): se crea una vez desde la web con sesión
  POST /auth/tokens (con sesión)                        -> 200
    valor: mgn_7VXm… (47 caracteres, se enseña una vez)
  GET /sessions con Authorization: Bearer mgn_…         -> 200
  POST /sessions con el token, sin CSRF                 -> 200
  POST /auth/tokens con el token                        -> 403 TOKEN_NO_PERMITIDO
  DELETE /auth/cuenta con el token                      -> 403 TOKEN_NO_PERMITIDO
```

Es decir: hasta ahora, un script tenía que **guardar la contraseña**, iniciar
sesión, conservar dos cookies y copiar una de ellas a una cabecera en cada
petición que cambiara algo. Y la sesión caduca a los 30 días. Con un token, es una
cabecera.

### Cómo es un token

| Qué | Cómo | Por qué |
|---|---|---|
| Forma | `mgn_` + 32 bytes aleatorios en base64url (47 caracteres) | El prefijo lo hace reconocible en un registro o en un escáner de secretos, y lo distingue del token compartido `MORGAN_API_TOKEN`, que es de la instalación y no de una persona |
| Se guarda | Solo el **SHA-256**, como las sesiones | Quien lea la base no obtiene nada usable |
| Se enseña | **Una vez**, en la respuesta de `POST /auth/tokens` | Morgan no puede volver a enseñarlo. Perdido, se revoca y se crea otro |
| Se presenta | `Authorization: Bearer mgn_...` y **solo así** | `X-Morgan-Token` es del token compartido: una sola forma de presentar un token personal es una cosa menos que auditar |
| Caduca | **Siempre**: 90 días por defecto, un año como mucho | Un token olvidado en un portátil viejo no puede valer para siempre |
| Cuántos | 20 vivos por cuenta | Un cliente por token; sin tope, un bucle roto llenaría la tabla |
| CSRF | **No se exige** | El CSRF protege lo que el navegador manda solo (las cookies). Una cabecera no viaja sola |
| Auditoría | Cada entrada hecha con token lleva `"token": "tok-…"`, su id | Para saber qué cliente hizo qué, y revocar ese |

### Alcances, y lo que un token no puede nunca

Cada token lleva uno o varios alcances, y cada petición pide uno:

| Alcance | Qué deja hacer |
|---|---|
| `chat` | `POST /chat` y `POST /chat/stream`. Va aparte porque es lo que pide casi cualquier cliente, y dar `escritura` para chatear sería dar de más |
| `lectura` | Cualquier `GET` o `HEAD`: listar conversaciones, leer mensajes, memoria, tareas… |
| `escritura` | Todo lo demás: crear, cambiar y borrar conversaciones, recuerdos, archivos, espacios… |

**Lo que no puede ningún token**, tenga los alcances que tenga: todo `/auth` salvo
`GET /auth/yo` —crear o revocar tokens, cambiar la contraseña, borrar la cuenta,
descargar todos los datos, ver o cerrar sesiones—, `/admin`, `/integraciones` y
`/diagnostico`. **Robar un token no puede convertirse en robar la cuenta.** La
comparación es por segmento: `/authors` no es `/auth`.

`GET /auth/yo` sí, porque saber de quién es el token es lo primero que hace un
cliente. Con token responde `csrf: ""`: no hay cookie que proteger.

### Dónde entra, y por qué no abre nada a la web

En `identidad_middleware`, **antes** que nada más: si llega `Bearer mgn_...` y la
petición **no trae cookie de sesión**, se resuelve el token, se comprueba el
alcance y se fija usuario y rol en el contexto, igual que con una sesión. A partir
de ahí todo lo que filtra por usuario —conversaciones, memoria, archivos,
espacios— queda aislado sin tocar una ruta.

- **Con cookie, se sigue el camino de siempre, con su CSRF**, traiga la cabecera que
  traiga. Si la cabecera bastara para saltarse el CSRF, una web ajena podría mandar
  la cookie de la víctima con un `Bearer mgn_` cualquiera. Hay una prueba para eso.
- **Un `mgn_` que no vale se rechaza siempre**, también en el Morgan de tu equipo,
  donde sin sesión se es el usuario local (que es el propietario). Por eso va antes
  de mirar si hay capa de cuentas: un token malo no puede heredar eso.
- **El token compartido lo deja pasar** (`src/api/auth.py`): la identidad va por
  dentro y lo comprueba. Pararlo allí haría imposible usar tokens personales en un
  despliegue con token compartido. Lo que no empieza por `mgn_` sigue necesitando el
  compartido.

| Respuesta | Código | Qué significa para quien integra |
|---|---|---|
| 401 + `WWW-Authenticate: Bearer` | `TOKEN_INVALIDO` | No existe, caducó, se revocó o la cuenta está suspendida: crear otro |
| 401 | `TOKENS_DESACTIVADOS` | Este Morgan tiene los tokens apagados |
| 403 | `TOKEN_NO_PERMITIDO` | Esto no se hace con un token, solo desde la web |
| 403 | `ALCANCE_INSUFICIENTE` | El token vale, pero no tiene el alcance que pide la ruta |

### Desde la web

En **Ajustes → Acceso por API** (V2.0.41): crear con nombre, qué puede hacer y
caducidad; el valor se enseña una vez; revocar uno o todos. Descrito en
[web.md](web.es.md). Por defecto propone **chatear y leer, 90 días**: lo que pide casi
cualquier programa y nada que pueda borrar.

### Revocar

- **Uno**: `DELETE /auth/tokens/{id}`. Deja de valer **en la petición siguiente**: no
  hay caché. Uno de otra persona responde 404, igual que uno que no existe.
- **Todos**: `POST /auth/tokens/revocar-todos`.
- **Solos**: al **cambiar** o **restablecer** la contraseña caen todos, que es lo que
  espera quien la cambia porque sospecha algo. Y al borrar la cuenta. **Y el PC
  conectado** (3.1): esas tres cosas cierran también la conexión de su agente local al
  momento; al volver, la nube comprueba su credencial otra vez.
- **El interruptor**: `MORGAN_TOKENS_API=false` los apaga todos al momento, sin
  borrarlos, y no deja crear más. Es el freno de emergencia; por defecto están
  activos (aprobado para todas las cuentas).

La limpieza periódica borra los revocados y los caducados.

### Comprobado contra un Supabase de verdad

Las pruebas de la suite ejercitan la implementación de Supabase con un cliente falso,
que comprueba la forma de las consultas pero no que PostgREST las entienda (el
usuario viaja embebido en la misma consulta que el token). Por eso se repitió el
recorrido contra el despliegue de prueba `morgan-carga`, con su propio Supabase y el
modelo simulado (2026-09-18): alta, crear token, `GET /auth/yo`, chatear, listar la
conversación, `403 ALCANCE_INSUFICIENTE` sin `escritura`, `403 TOKEN_NO_PERMITIDO`
al crear tokens o borrar la cuenta, revocar y `401 TOKEN_INVALIDO` en la petición
siguiente. **Todo igual que en local.** En producción, un token inventado responde 401.

### Lo que no hace todavía

- **Freno por minuto por token** (fase 5). Hoy un token gasta del cupo diario de su
  dueño como cualquier mensaje suyo, y el cupo es el límite.
- **Aviso antes de caducar** por correo: la web lo marca en color a 7 días, pero
  nadie avisa a quien no entra.
- Los agentes locales **no usan tokens de API**: desde la 3.0 tienen su propia
  credencial (`mga_`, distinta de `mgn_`), que se consigue emparejando el PC desde la
  web (agente-local.md).

## Cuándo se exige cuenta

`MORGAN_REQUIRE_AUTH` decide, y su valor por defecto **sigue al entorno**: en la
nube sí, en local no.

No es un capricho. El Morgan de escritorio corre en tu equipo con tus claves, y
obligarte a inventar una contraseña para hablar con tu propio ordenador no protege
de nada. Sin cuentas exigidas, todo pertenece al usuario implícito `local`, que es
el dueño de todo lo que existía antes de que hubiera cuentas — y por eso `local`
es un nombre de usuario **reservado**: registrarlo daría acceso a esos datos.

`MORGAN_REGISTRO_ABIERTO` decide si cualquiera puede crearse una cuenta. Abierto
por defecto —un Morgan en la web al que nadie puede registrarse no sirve de nada—,
y cerrarlo devuelve **403** a los registros nuevos sin echar a quien ya estaba
dentro.

En la nube hay **dos formas válidas de cerrar el despliegue**, y basta con una:
`MORGAN_API_TOKEN`, un secreto compartido para un Morgan privado, o
`MORGAN_REQUIRE_AUTH`, para uno con cuentas. El arranque aborta si no hay ninguna.

> Antes se exigía el token **siempre** en la nube, y eso hacía imposible abrir
> Morgan a otra gente: para invitar a alguien había que darle el token, con lo que
> esa persona obtenía acceso a la API entera al margen de su cuenta. Las cuentas
> no son una protección más floja que el token: son más fuerte, porque además
> separan los datos.

Rutas abiertas sin sesión: `/health`, `/status`, y las de `/auth` que sirven para
conseguir una.

> **`/status` está abierto a propósito.** Es lo que la interfaz consulta para
> saber si el backend vive, y lo que despierta a Render cuando lleva un rato
> dormido. Protegerlo hace que la **propia pantalla de acceso** diga «API
> desconectada»: no puedes entrar porque no has entrado. No publica datos de
> nadie, y si hay `MORGAN_API_TOKEN` configurado, ese sigue cubriéndolo. Hay una
> prueba que lo fija, para que nadie lo «arregle» metiéndolo entre las
> protegidas.

## Cómo se separan los datos

Dos barreras, y la segunda existe porque la primera puede fallar por descuido.

**1. El filtro por usuario, en el middleware.** Cada petición fija el usuario en
un `ContextVar` y **toda** consulta de los repositorios filtra por él. Que esté
ahí y no en cada consulta es lo que hace que una ruta nueva nazca aislada por
defecto: olvidarse del filtro deja de ser posible, porque no hay filtro que
escribir.

Es un `ContextVar` y no una variable global a propósito: FastAPI atiende las
rutas síncronas en un *pool* de hilos, y una global mezclaría los datos de dos
personas que pidan a la vez. Es la clase de fallo que no aparece probando a mano y
sí en cuanto hay dos usuarios.

**2. Row Level Security en Supabase.** Políticas por fila que valen aunque una
consulta se olvide del filtro. `auth_sessions`, `password_reset_tokens` y
`login_intentos` tienen RLS activo y **ninguna política**: guardan material con el
que se suplanta a alguien y nunca se consultan desde el navegador, así que el
efecto buscado es que nadie salvo el backend —que usa la clave de servicio— pueda
leerlas.

## Cupo por usuario

Morgan usa las claves de su dueño. Con una sola persona da igual; con varias,
**una sola podría agotar la cuota de todas** en una tarde. Por defecto: 50
mensajes, 20 transcripciones y 20 análisis de imagen al día, contados por día
natural. El usuario local está **exento**: es tu equipo y tus claves.

Se apunta **al empezar, no al terminar**. Si se contara al final, un turno que
falla a mitad saldría gratis y bastaría con provocar fallos para saltárselo.
Comprobar y apuntar son una sola operación, porque separarlos dejaría una ventana
por la que dos peticiones simultáneas pasarían las dos: en SQLite eso es un
`UPDATE ... WHERE contador < límite` mirando `rowcount`, y en Supabase una función
en el esquema privado `morgan_priv`, porque PostgREST no sabe escribir
`columna = columna + 1`.

Al llegar al límite, `/chat` responde **429**, y las herramientas devuelven un
`success: false` con el motivo — no una excepción, que abortaría el turno entero
con un fallo genérico en vez de dejar que Morgan lo explique.

> **Estuvo escrito y probado sin que lo llamara nadie.** El módulo existía, tenía
> sus pruebas en verde y era una pieza muerta: ninguna ruta lo invocaba. Ahora hay
> pruebas que comprueban que se aplica de verdad en los caminos que cuestan
> dinero, que es distinto de comprobar que el módulo funciona.

### El cupo global (V2.0.24)

Con el registro abierto, **el cupo por persona no acota el total**: diez cuentas son
diez cupos, y con OpenAI de pago al final de la cadena eso es dinero. La auditoría de
la 2.3 lo planteó y delegué el número. Además del cupo de cada persona hay un tope
diario para **la suma de todas las cuentas sujetas a cupo**:

| Concepto | Tope entre todos | Por qué ese número |
|---|---|---|
| Mensajes | **150** | La capacidad gratuita de modelos ronda los 210 turnos al día (tres cuentas de Groq y el relevo de modelo, estimado). Quedan unos 60 para el propietario antes de tocar el proveedor de pago |
| Imágenes | **15** | Solo Gemini ve imágenes, y su cuenta gratuita da 20 peticiones al día |
| Transcripciones | **60** | Holgado para un uso normal; acota un bucle |

- **El propietario y el usuario local ni cuentan ni se frenan**: son quienes pagan.
  Un administrador sí cuenta: administrar no es pagar.
- **Se comprueba antes del cupo de la persona**, así que un rechazo global no le gasta
  nada a nadie.
- **El mensaje dice que no es culpa de quien lo lee**: «Morgan ha llegado hoy a su
  límite de mensajes para todas las cuentas. No es por tu uso: se renueva mañana». La
  ruta responde el mismo 429 `CUOTA_AGOTADA`, que la web ya sabe enseñar.
- **Configurable** con `MORGAN_CUPO_GLOBAL_MENSAJES`, `_TRANSCRIPCIONES` e `_IMAGENES`;
  0 es sin tope.
- **No es atómico entre personas**: dos turnos simultáneos de dos cuentas pueden pasar
  los dos en el último hueco. El exceso posible son unas pocas llamadas; hacerlo
  atómico exigiría un bloqueo compartido por todos los turnos del servidor.
- En la nube se suma leyendo las filas del día (una por cuenta activa) sin llamar a la
  función de Postgres. Un fallo al sumar deja pasar: el cupo por persona sigue.

**Y un defecto que apareció al construirlo.** Las herramientas de imagen y audio
apuntaban el uso **sin el rol**, así que en la nube el propietario gastaba cupo de
imágenes y transcripciones como cualquier cuenta. La prueba que lo fija falla con el
código anterior. `tests/test_cupo_global.py`: 21 pruebas, 11 de 11 mutaciones.

Ver [`src/identidad/cuotas.py`](../src/identidad/cuotas.py).

### Lo que no cuesta cupo pero llena la base (4.22)

Lo que gasta dinero ya tenía tope (mensajes, imágenes y transcripciones, el cupo global, los
archivos, los tokens de API, los PC, las automatizaciones). La revisión de los límites por
cuenta buscó lo que **no** gasta cupo pero ocupa la base, y que el modelo o un script pueden
crear en bucle. Ahora tiene tope, holgado, y al llegar se dice qué hacer:

| Qué | Tope por cuenta | Al llegar |
|---|---|---|
| Recuerdos (`remember_fact`, `POST /memory`) | **500**, y **2000 caracteres** cada uno | 409 `MEMORIA_LLENA`: «olvida alguno que ya no sirva». Actualizar uno que ya existe siempre se puede |
| Espacios de trabajo | **50** | 409 `ESPACIOS_DEMASIADOS`: «borra alguno que no uses» |
| Documentos de conocimiento, entre todos los espacios | **300** | La herramienta lo dice («borra alguno con `remove_knowledge`»). Reemplazar uno (mismo título y colección) siempre se puede |

En la nube se cuentan solo las filas de la cuenta (con un `limit`, sin traer la tabla).
`tests/test_limites_por_cuenta.py`: 10 pruebas, 11 de 11 mutaciones.

## Esquema

Migración **v11**, espejada en Supabase.

| Tabla | Para qué |
|---|---|
| `morgan_users` | Ahora con `username`, `password_hash`, `status`, `email_verificado` |
| `auth_sessions` | Sesiones. El `id` es el **hash** del token |
| `password_reset_tokens` | Recuperación. El token también **hasheado** |
| `login_intentos` | Intentos fallidos, por identificador y origen |
| `api_tokens` | Tokens personales de API (SQLite **v19**, Supabase **v23**). También el **hash**, nunca el valor. RLS activo y ninguna política, como `auth_sessions` |

> Se llama `auth_sessions` y **no** `sessions` a propósito: esa tabla ya existe y
> son las **conversaciones**. Reutilizar el nombre mezclaría dos cosas que no
> tienen nada que ver y garantizaría confusión en cada consulta que alguien
> escriba a partir de ahora.

## Rutas

| Método | Ruta | Sesión | Qué hace |
|---|---|---|---|
| `POST` | `/auth/registro` | no | Crea la cuenta y deja la sesión iniciada |
| `POST` | `/auth/login` | no | Entra. 401 si falla, **429** si hay bloqueo |
| `POST` | `/auth/logout` | no | Cierra la sesión en el servidor y borra cookies |
| `GET` | `/auth/yo` | no | Quién eres. **Nunca da 401** |
| `POST` | `/auth/recuperar` | no | Envía el enlace, si esa dirección tiene cuenta |
| `POST` | `/auth/restablecer` | no | Cambia la contraseña con el token del correo |
| `POST` | `/auth/password` | sí | Cambia la contraseña sabiendo la actual |
| `GET` | `/auth/sesiones` | sí | Dónde tienes la sesión abierta |
| `POST` | `/auth/sesiones/cerrar-otras` | sí | Cierra el resto |
| `GET` | `/auth/tokens` | sí | Tus tokens de API vivos, sin su valor |
| `POST` | `/auth/tokens` | sí | Crea uno (`nombre`, `alcances`, `dias`). Devuelve el valor **una vez** |
| `DELETE` | `/auth/tokens/{id}` | sí | Revoca uno. 404 si no es tuyo |
| `POST` | `/auth/tokens/revocar-todos` | sí | Revoca todos |

`/auth/yo` no da 401 a propósito: abrir la web sin haber entrado es el caso más
corriente de todos, y tratarlo como error llenaría la consola de fallos que no lo
son.

El login devuelve **429** y no 401 cuando hay bloqueo porque el cliente tiene que
poder distinguir «espera» de «prueba otra contraseña»: son consejos opuestos.

## Pruebas

| Fichero | Qué cubre |
|---|---|
| [`tests/test_cuentas.py`](../tests/test_cuentas.py) | La lógica: hasheo, registro, sesiones, fuerza bruta, recuperación, enumeración |
| [`tests/test_api_cuentas.py`](../tests/test_api_cuentas.py) | La costura con HTTP: cookies, CSRF, protección de rutas, aislamiento entre usuarios |
| [`tests/test_cuotas.py`](../tests/test_cuotas.py) | El cupo diario |
| [`tests/test_frenos_de_acceso.py`](../tests/test_frenos_de_acceso.py) | Los dos ataques de la auditoría 2.3: origen falseado y altas en masa. 10 de 10 mutaciones |
| [`tests/test_aislamiento.py`](../tests/test_aislamiento.py) | Que cada uno ve solo lo suyo |
| [`tests/test_tokens_api.py`](../tests/test_tokens_api.py) | Tokens de API: la aceptación del plan (un script chatea y lista, no borra la cuenta ni crea tokens, el de A no ve nada de B, revocar corta al momento), alcances, CSRF con cookie, contraseña, interruptor y Supabase. 31 de 32 mutaciones; la que sobrevive es equivalente (la cascada de la tabla ya borra los tokens) |

**Ninguna desactivada.** El número total de la suite no se escribe aquí: se
queda desfasado el mismo día y no dice nada sobre la autenticación.

Las de HTTP existen porque hay fallos que no se ven leyendo el servicio: una
cookie sin `HttpOnly`, un 401 sin cabeceras CORS, una ruta que se olvidó de exigir
sesión. Y las de aislamiento comprueban dos cosas, no una: que Bruno no lee la
conversación de Ana **y** que la respuesta es idéntica a la de una conversación
inexistente. Si se distinguieran, el endpoint serviría para averiguar qué
conversaciones existen aunque no dejara leerlas.

## Poner esto en marcha

Para el despliegue de la nube, en Render:

Guía completa paso a paso en
despliegue.md. En resumen:

```
MORGAN_ENVIRONMENT=cloud        # ya activa MORGAN_REQUIRE_AUTH
MORGAN_EMAIL_API=brevo          # sin correo no hay recuperación; SMTP no sale de Render
MORGAN_EMAIL_API_KEY=...
MORGAN_EMAIL_FROM=...           # remitente verificado en Brevo
MORGAN_WEB_URL=https://morgan-ia.vercel.app
MORGAN_CORS_ORIGINS=https://morgan-ia.vercel.app
```

`MORGAN_CORS_ORIGINS` tiene que ser exacto: con `allow_credentials`, el navegador
**no acepta** el comodín `*` y las cookies no viajarían.

## Lo que falta

> Esta sección decía que faltaban la verificación del correo, borrar la cuenta,
> descargar los datos, las integraciones y un cupo global. **Están hechas** y
> descritas más arriba (y GitHub en [integraciones.md](integraciones.es.md)).

- **Integraciones con Google** (correo, calendario): **aparcadas** desde la 2.0.18 y
  las **descarté** el 2026-09-19. El código de Calendar sigue en el repositorio,
  sin registrar sus herramientas.
