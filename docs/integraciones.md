# Servicios externos (V1.9)

> Conectar GitHub —y, más adelante, otros servicios— a la cuenta de cada
> persona.

## Lo primero: esto no es el login de Morgan

Son dos sistemas distintos, y confundirlos es el error más caro que se puede
cometer aquí:

| | Qué significa |
|---|---|
| **Login de Morgan** | Cuentas propias: usuario, contraseña, sesión. Dice **quién eres** |
| **Integración** | *Tú* autorizas a Morgan a usar **tu cuenta de otro servicio** |

Hay una ironía que conviene nombrar: se descartó OAuth para *iniciar sesión* por
complejidad, y aquí reaparece para *conectar servicios*. No es una
contradicción. En el primer caso GitHub diría quién eres, y Morgan quedaría atado
a que su servicio esté en pie para poder entrar; en el segundo ya se sabe quién
eres, y lo único que se obtiene es permiso para actuar en tu nombre en un sitio
concreto.

La consecuencia práctica: **una integración cuelga siempre de una cuenta de
Morgan**. Sin sesión no hay a quién atribuir la autorización, y en un Morgan
compartido la conectaría una persona y la usarían todas.

## El intercambio, y dónde está cada protección

```
1. La web pide conectar        ──►  Morgan emite un `state` y lo guarda
2. El navegador va a GitHub    ──►  la persona autoriza (o no)
3. GitHub devuelve al backend  ──►  con `code` y el mismo `state`
4. Morgan valida el `state`    ──►  y solo entonces canjea el `code`
5. El token se guarda cifrado  ──►  y el navegador vuelve a la web
```

### El paso 4 es el que importa

Sin comprobar que el `state` que vuelve es uno que se emitió, **una web ajena
podría completar el flujo y dejar su cuenta de GitHub conectada a la sesión de
otra persona**. Morgan actuaría después sobre los repositorios del atacante
creyendo que son los tuyos, y todo lo que se le pidiera «sobre mi repo» pasaría
por un repositorio ajeno.

El estado caduca en diez minutos —es el tiempo de autorizar en una pantalla, no
el de una sesión— y **se borra al usarse**. Uno reutilizable deja de proteger de
nada.

### De quién es la autorización lo dice el `state`, no la cookie

La vuelta de GitHub es una **navegación del navegador**, no una petición de la
web. Fiarse de la cookie de sesión sería preguntarle al mismo canal que se está
intentando verificar. El `state` se emitió con una sesión válida y se guardó con
su `user_id`; el callback lo consume y actúa como ese usuario, que puede no ser
el de la cookie de esa petición.

Por eso `/integraciones/{servicio}/callback` es **la única ruta pública** bajo
ese prefijo. Exigir sesión ahí rompía el flujo justo al volver, después de
autorizar — el peor momento posible para fallar.

### La URL de retorno apunta al backend

A Render, no a Vercel. El secreto de la aplicación vive en el servidor y es allí
donde se canjea el código: **el frontend nunca ve ni el secreto ni el token**.
Poner la de la web da un `redirect_uri_mismatch` que no explica gran cosa.

## El token

### Se guarda cifrado

Con un token de GitHub se puede leer código privado y —según los permisos—
escribir. En claro, una copia de seguridad extraviada deja de ser un problema de
privacidad y pasa a ser uno de acceso.

Se usa **Fernet**, de `cryptography`: AES-128-CBC con HMAC-SHA256. No hay
criptografía propia; [`secretos.py`](../src/integraciones/secretos.py) solo
resuelve de dónde sale la clave.

La clave es `MORGAN_SECRET_KEY`. **No se deriva de otra variable ni se genera al
arrancar**, y las dos cosas son deliberadas:

- Derivarla de la clave de Supabase ataría dos secretos que deben poder rotarse
  por separado: rotar una dejaría ilegibles todos los tokens.
- Generarla al arrancar haría que cada reinicio de Render inutilizara las
  integraciones **sin ningún error**: simplemente dejarían de descifrarse.

**Sin clave no se ofrecen las integraciones.** No se guardan tokens en claro
«mientras tanto», que es la solución que parece pragmática y es la que acaba en
producción: un token sin cifrar no se distingue de uno cifrado mirando la tabla,
así que el día que alguien lo note llevará meses ahí.

Si la clave cambia, descifrar **lanza** en lugar de devolver una cadena vacía. Un
token vacío se usaría como válido y daría un 401 de GitHub — un síntoma que no
apunta a su causa.

### Nunca sale del backend

Ni en la lista, ni en el detalle, ni en la auditoría. La interfaz no lo necesita:
quien habla con GitHub es el servidor. Devolverlo lo pondría al alcance de
cualquier script inyectado en la página, y el daño no sería de Morgan sino de los
repositorios de esa persona.

`Integracion.to_dict()` —lo publicable— tiene exactamente siete campos, y hay una
prueba que fija el conjunto para que añadir uno sea una decisión y no un
descuido.

## Los permisos

Mínimos y explícitos, que es lo que pide la especificación:

| Permiso | Para qué |
|---|---|
| `read:user` | Saber de quién es la cuenta conectada |
| `repo` | Ver los repositorios privados |

`repo` incluye escritura, y conviene decir por qué: **GitHub no ofrece un permiso
de solo lectura sobre repositorios privados** en las OAuth Apps clásicas. Es el
mínimo que permite verlos. Por eso la interfaz enseña **qué habilita en lenguaje
llano antes de conectar**, en lugar de dar el permiso por supuesto.

Que el token permita escribir no significa que Morgan escriba. Las acciones
siguen pasando por el sistema de permisos y de confirmación de siempre:
**conectar GitHub no es una autorización en blanco**.

## Desconectar

Se le pide a GitHub que invalide el token, y **el borrado local ocurre pase lo
que pase**. Si solo se borrara aquí, una copia filtrada del token seguiría
funcionando y la persona creería haber revocado el acceso. Y si la llamada a
GitHub falla, quedarse conectado sería el peor resultado: ha dicho que no quiere
seguir conectada.

La respuesta dice si la revocación remota se confirmó, y la interfaz avisa cuando
no, para que se revise en la web del servicio.

## Los cuatro estados

La interfaz los distingue porque significan cosas distintas:

| Estado | Qué pasa |
|---|---|
| **No configurado** | Al servidor le faltan credenciales. **No hay botón** |
| **Sin conectar** | Se puede conectar |
| **Conectado** | Con el nombre de la cuenta y los permisos concedidos |
| **Con problemas** | Hay integración, pero la última operación falló |

Los dos últimos son muy distintos y se confunden con facilidad. «Conectado y
fallando» no se arregla volviendo a conectar si el problema es que GitHub está
caído.

`disponible` es del **servidor** (¿tiene credenciales?) y `conectado` es de la
**persona** (¿ha autorizado?).

## La regla que gobierna la interfaz

**Si algo no puede funcionar, se dice; no se ofrece.** Sin credenciales, la
sección explica qué falta y no pinta ningún botón. Un botón que siempre da error
hace perder el tiempo y parece una avería de Morgan cuando es configuración que
falta.

### El Morgan local no puede conectar servicios, y desde la V2.0 lo explica

Una integración cuelga de una cuenta: es *alguien* autorizando a Morgan a usar
su cuenta de otro sitio. En el Morgan local, con `MORGAN_REQUIRE_AUTH=false`, no
hay cuentas y por tanto no hay a quién atribuir la autorización. Conectar y
desconectar responden 401, y eso no cambia: en un Morgan compartido sin cuentas,
la conectaría una persona y la usarían todas.

Lo que sí cambió es que **preguntar qué servicios hay ya no exige cuenta**.
Antes `GET /integraciones` también respondía 401, y el resultado era que el
panel entero se pintaba como un error en rojo —el caso que esta misma regla
dice que no debe pasar—. Ahora responde con GitHub apagado y su motivo:

> Para conectar GitHub hace falta una cuenta de Morgan. Este Morgan no pide
> cuentas, así que no habría a quién atribuir la autorización: una la
> conectaría una persona y la usarían todas.

Y el motivo de la cuenta manda sobre el de las variables que le falten al
servidor. Con las credenciales puestas y sin cuentas, decir «faltan
`GITHUB_CLIENT_ID` y `GITHUB_CLIENT_SECRET`» mandaría a quien administra a
arreglar lo que no toca.

**Cómo apareció.** No leyendo el código: quitando cosas para ver qué se rompía.
`_exigir_cuenta()` en `desconectar` parecía redundante —sin él no fallaba
ninguna prueba, porque con cuentas exigidas el middleware ya devuelve 401 antes
de llegar— y buscando dónde *sí* importa apareció el Morgan local, donde esa
línea es la única defensa. Mirando ahí se vio que `listar` lo exigía también.
Está contado en
auditorias.md.

## Configuración

Hecha y en uso: hay una cuenta de GitHub conectada. Se deja escrito para el día que
haya que rehacer el despliegue.

Una aplicación OAuth en GitHub (*Settings → Developer settings → OAuth Apps → New*,
unos diez minutos). En Render, tres variables:

```
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
MORGAN_SECRET_KEY=...        # cualquier cadena larga y aleatoria
```

Y la URL de retorno registrada en GitHub:

```
https://morgan-ia-2-0.onrender.com/integraciones/github/callback
```

Tiene que coincidir **exactamente**, esquema incluido y sin barra final. Si el
servicio de Render cambia de URL —pasó el 2026-09-12, al recrearlo en Virginia—,
esta URL se cambia en el panel de GitHub o conectar de nuevo falla con un
`redirect_uri_mismatch` que no explica nada. Lo ya conectado sigue funcionando,
porque usa el token guardado y no el retorno.

Morgan compone su lado de esa URL con `MORGAN_API_URL` y, si no existe, con
`RENDER_EXTERNAL_URL`, que Render define solo. **Mejor no definir la primera**:
copiada de un servicio viejo, apunta a un dominio que ya no existe.

Mientras falte alguna, la sección funciona igual: enseña GitHub apagado y dice
exactamente qué falta, con **todas** las variables que falten y no la primera.
Es lo mismo que hace hoy en el Morgan local, donde lo que falta no es una
variable sino una cuenta.

## Las herramientas

Cuatro, y **todas de solo lectura**:

| Herramienta | Qué hace |
|---|---|
| `github_listar_repos` | Los repositorios, del más reciente al más antiguo |
| `github_listar_issues` | Las issues, **sin** los pull requests que GitHub mezcla |
| `github_listar_prs` | Los pull requests, con su rama de origen y destino |
| `github_leer_archivo` | Un archivo o el índice de un directorio |

### Solo leen, y es una decisión

El token permite escribir. Estas no escriben. La especificación lo pedía con
estas palabras: «no implementar automáticamente todas las capacidades de GitHub
solo porque OAuth esté conectado».

Añadir «crear issue» o «hacer commit» es fácil desde aquí, y por eso conviene
decir qué haría falta antes: **pasar por el sistema de planes**, para que la
persona vea qué se va a escribir y dónde antes de que ocurra. Escribir en el
repositorio de alguien sin ese paso es justo lo que el sistema de permisos de
Morgan existe para impedir.

### El usuario sale del contexto, nunca del argumento

Ninguna recibe un identificador de usuario. El token se busca en el repositorio
de integraciones, que filtra por el usuario de la petición. Si fuera un
parámetro, el modelo podría inventárselo — y un modelo que puede nombrar a otro
usuario en una llamada a herramienta es un modelo que puede leer sus repos.

### Se registran siempre, conectado o no

Al revés que las de conocimiento. Si solo aparecieran con GitHub ya conectado,
el modelo no sabría que existen y **nunca sugeriría conectarlo**. Sin conexión
responden diciendo dónde se conecta, que es más útil que no estar.

### Lo que el modelo escribe no puede cambiar a qué se llama

Esto empezó siendo una precaución y resultó ser un agujero abierto.

`github_leer_archivo` metía la ruta del archivo en la URL de la API tal cual.
`httpx` normaliza los `..` al construir la URL, así que:

```
/repos/duenyo/nombre/contents/../../../../user/emails   →   /user/emails
```

Las direcciones privadas de la persona, desde una herramienta que dice leer un
archivo de un repositorio. Por el mismo camino se alcanzaban
`/notifications`, `/user/keys` y `/gists`.

**Y comprobar el repositorio no lo tapaba.** `_repo_valido` miraba la
*forma*: `'../..'` tiene dos trozos y ninguna barra de más, así que pasaba, y
`/repos/../../issues` se normaliza a `/issues`.

Importa aquí más que en otro sitio por lo que dice el apartado siguiente: el
valor lo compone el modelo a partir de lo que le dicen, y lo que le dicen puede
venir de un archivo que **acaba de leer**. Un README con la ruta adecuada
bastaba.

Tres cosas lo cierran, y las tres hacen falta:

1. **Se rechazan los trozos `.` y `..` por su nombre.** Codificar no
   servía: `quote()` no toca los puntos, así que `..` sobrevive intacto.
2. **Se codifica lo demás**, para que un `?` o un `#` no puedan partir la
   URL en otra cosa.
3. **`_pedir` comprueba que la URL que sale es la que se pidió**, letra por
   letra. Es la red de seguridad: no depende de que una función nueva se acuerde
   de validar, que es exactamente como apareció el fallo.

Lo fija [`test_github_rutas.py`](../tests/test_github_rutas.py), 17 casos.

### Lo que viene de un repositorio es dato, no instrucción

El contenido de un archivo llega marcado como `untrusted_file_data`, igual
que una página web o un audio. Lo escribió alguien, y un comentario del código
que diga «ignora lo anterior» no puede tratarse como una orden.

## Pruebas

[`test_integraciones.py`](../tests/test_integraciones.py), 37 casos, más
[`test_github_rutas.py`](../tests/test_github_rutas.py), 17. Lo que fijan,
por orden de lo que costaría equivocarse:

0. Que lo que escribe el modelo no pueda cambiar a qué endpoint se llama. Estaba
   mal y se alcanzaban las direcciones privadas de la persona.


1. El `state` decide de quién es la autorización, no la cookie — incluida la
   comprobación de que un estado emitido por Ana devuelve `usr-ana` aunque lo
   consulte Bea.
2. El token no aparece en ninguna respuesta.
3. Se guarda cifrado, en las dos implementaciones.
4. Aislamiento entre usuarios, en SQLite **y en Supabase**. La de Supabase se
   escribió y se probó a la vez, que es justo lo que no se hizo con las
   conversaciones — y costó [ocho defectos](web.md).
5. Sin credenciales del servidor no hay botón de conectar.
