# La interfaz web

[English](web.md) · **Español**

> Junta lo que antes eran cuatro documentos: el diseño de la interfaz, el control
> del turno, las pruebas del frontend y los diez defectos que rompían la web en
> producción. Última revisión: 2026-09-25, V3.5.0 (el agente local: descargas, planes y «Detener»).

React 19 + TypeScript + Vite, publicada en Vercel (`morgan-ia.vercel.app`). Habla
con el backend de Render por un proxy en el mismo origen, `/api/*`
(despliegue.md).

## 1. Principios de diseño

- **Un botón que no hace nada es peor que su ausencia.** No se copian de una
  referencia visual controles sin función: ni píldoras de «deep think», ni un `+`
  que en la portada estaba desactivado (se quitó en la 2.0.9). Del diseño de la
  2.0.28 se dejaron fuera compartir, avisos, un selector de modelo y los datos de
  consumo: en Morgan no harían nada.
- **Ningún componente escribe un color a mano**; todos leen variables de
  `web/src/index.css`, y `tests/test_tema_web.py` fija el contraste WCAG de los tres
  temas, también del acento y de los dos tonos del botón de enviar. El color de
  estado (riesgo, salud) sigue siendo información; el acento marca lo que se pulsa.
- **Tipografía**: Outfit en toda la interfaz y en las respuestas; Instrument Serif (tipo Times)
  en cursiva para la palabra destacada del saludo; JetBrains Mono para el código.
- **Las sugerencias de la portada escriben en el compositor, no envían.** Con un
  agente que ejecuta acciones reales, quien pulsa tiene que poder leer qué pide.
- **Iconos SVG, no emoji**: los emoji cambian entre sistemas y no heredan color.

### Temas

Desde la 2.0.28 salen de mis capturas de diseño:

| Tema | `data-tema` | Qué es |
|---|---|---|
| **Azul noche** (defecto) | `oscuro` | Azul marino muy oscuro, acento azul claro, botones con degradado azul |
| **Oscuro** | `negro` | Monocromo: grises neutros, acento blanco, enviar en blanco con texto negro |
| **Claro** | `claro` | El azul noche de día: fondo gris azulado, tarjetas blancas con sombra suave |
| Del sistema | — | Elige entre claro y azul noche |

Los identificadores no cambiaron (`oscuro` era el lima hasta la 2.0.27, `negro` el
monocromo de la V1.2): renombrarlos devolvería al tema por defecto a todo el que ya
tiene uno guardado.

### El logo (V2.0.19)

Mi espiral, vectorizada y comparada con el original. Es un componente,
`LogoMorgan` (`web/src/components/Logo.tsx`), con `fill="currentColor"`: vale en los
tres temas sin petición de red. Está en el favicon, la barra lateral, la barra de
arriba, el acceso y la insignia de la portada, que **gira mientras se comprueba el
servidor**. PNG de 180 y 512 px para iOS y para compartir el enlace.

**El icono de la pestaña** (2.0.29) es la espiral en azul sobre un cuadrado azul
noche, en SVG, PNG de 96 px y un `favicon.ico` de verdad. Se enlazan con `?v=`: el
rayo morado que yo veía en la pestaña era el icono de la plantilla de Vite,
**guardado por el navegador** desde antes de que existiera el logo (producción ya
servía la espiral). Los navegadores guardan el icono por su dirección durante
mucho tiempo, y `/favicon.ico` devolvía la página. Se borraron también los restos
de la plantilla (`vite.svg`, `react.svg`, `hero.png`, `icons.svg`), que nada usaba.

**La pantalla de carga es solo la espiral girando en el centro** (2.0.28). Está dos
veces a propósito: en línea en `web/index.html`, para verse desde el primer instante
mientras se descarga la aplicación (con un script que aplica el tema guardado antes
de pintar), y en `PantallaCargando`, idéntica, mientras se averigua la sesión. De
una a otra no hay salto. Con el backend dormido la espera puede pasar del minuto: que
la espiral siga girando es lo que dice que pasa algo. Con
`prefers-reduced-motion` se queda quieta.

### Cómo se comprobó el diseño de la 2.0.28

Con capturas de la web compilada, servida por un backend **aislado** (datos
temporales, sin claves de modelos ni Supabase):

- Los tres temas en escritorio, contra las capturas de referencia.
- 360 y 390 px dentro de un `iframe`, con el menú abierto y con el compositor a la
  vista. Encontró tres fallos, corregidos antes de publicar: las tarjetas no pasaban
  a una columna, la regla táctil de 40 px aplastaba «Adjuntar» y la barra de arriba
  se amontonaba sin sesión.
- La pantalla de carga sin JavaScript, y que la espiral **gira**: dos capturas en
  instantes distintos dan imágenes distintas.
- Una conversación con Markdown y código, y el acceso en modo nube.
- Encontró también una incoherencia: la barra decía «Modo degradado» y la portada
  «Operativo». Ahora las dos leen el mismo dato.

## 2. Estructura

Barra lateral y lienzo con una barra arriba (2.0.28):

- **Barra lateral**: la marca con el entorno (`Nube` o `Local`, de `/status`), el botón
  de nueva conversación, las vistas, el espacio de trabajo y el historial. Abajo, la
  **tarjeta de usuario**: nombre, estado de Morgan y el engranaje de Ajustes. Desde la 5.0.2,
  desde Windows, encima de la tarjeta, **«Descargar Morgan para Windows»**, que no sale en un
  móvil ni dentro del propio programa.
- **Ajustes es un panel, no una vista** (2.0.29), como en la captura de referencia:
  secciones a la izquierda con buscador (General, Personalización, Memoria, Datos y
  privacidad, Cuenta, Acceso por API, Acerca de) y filas con nombre, una línea de
  ayuda y el control.
  Se abre desde el engranaje o desde el menú de la cuenta y se cierra con Escape o
  pulsando fuera, sin perder dónde estabas. En 640 px o menos ocupa la pantalla y
  las secciones pasan a pastillas que se desplazan de lado. Del diseño se quedaron
  fuera el color de acento, la voz y Discord: aquí no harían nada. El idioma se
  elige de una lista y se guarda al momento **sin pisar lo que se esté escribiendo
  en el perfil**; el perfil se guarda con su botón, que solo se activa si cambió algo.
- **Tu equipo, paso a paso** (4.17): el programa para descargar (5.0) y, como alternativa, la
  línea de instalación con su botón **Copiar**, cómo
  abrir PowerShell y pegarla, y **aviso solo cuando el PC se conecta** (mira cada 3 s
  mientras hay un código; el que ya estaba no cuenta como nuevo). En cada equipo
  conectado, **«Abrir los ajustes en mi PC»**: abre allí «Morgan en tu PC» y no cambia nada
  desde aquí. Y en «Acceso por API», al crear un token, la dirección de la API y ejemplos
  que funcionan tal cual (PowerShell, curl, Python) con su botón Copiar (`ParaCopiar.tsx`).
- **Tu equipo** (3.0-C): emparejar el PC con la cuenta para el
  agente local (`PanelEquipos.tsx`). Pide un código de un solo uso y lo
  enseña grande, con su cuenta atrás, porque se teclea a mano en el PC; uno caducado no
  se enseña como si valiera. Advierte de lo que corta la amenaza H: *«si la cuenta que
  ves no es la tuya, di que no»*. Lista los equipos con su sistema, versión y última
  conexión, y revocar pide confirmación. Dice que al instalarlo el agente **no puede
  hacer nada** hasta que la persona, en su PC, le da carpetas o enciende capacidades.
  Desde la 3.8, con el código enseña **la línea de PowerShell que instala el agente** en
  un PC nuevo (y cómo emparejar uno que ya lo tiene); desde la 3.7, de cada equipo, si
  está conectado, qué ofrece y su historial (abajo).
- **Acceso por API** (2.0.41, fase 2 del plan de la API):
  crear, ver y revocar los [tokens personales](autenticacion.es.md#tokens-personales-de-api-v2040)
  (`PanelTokens.tsx`). Está escrito para quien no ha visto nunca un token: explica
  para qué sirve y dice «si no sabes qué es, no lo necesitas». Lo que lo hace seguro
  de usar:
  - **Por defecto, chatear y leer, 90 días**: nada que pueda borrar si no se marca.
  - **El valor se enseña una vez**, en un área de texto donde se ve entero (en el
    móvil, un campo de una línea lo cortaba), con «Copiar» y «Ya lo he guardado». La
    caja **no se cierra sola**: perderlo por un cambio de sección obligaría a
    revocarlo. Si el navegador no deja usar el portapapeles, lo deja seleccionado.
  - **Revocar pide confirmación** («Sí, revocar»), uno a uno o todos.
  - Avisa en color de los que **caducan en 7 días o menos**.
  - En el Morgan de tu equipo explica que no hace falta token: sin cuenta, la API
    ya es tuya.

  **Comprobado en el móvil** con la web compilada y un backend aislado, a 360 y
  390 px: crear, ver, guardar y revocar sin que nada se salga de la pantalla, y el
  token creado desde el móvil **funciona desde un script** (200) y deja de hacerlo
  al revocarlo desde la web (401). La primera captura enseñó dos cosas, corregidas:
  el título salía repetido y el valor se cortaba.
- **Qué modelo contesta no se enseña** (2.0.29, decisión mía): ni en cada
  mensaje ni debajo del compositor. Queda una sola pista, sin nombres: si contestó
  el de reserva, el tiempo de la respuesta lo explica al pasar por encima. Debajo
  del compositor, un aviso para cualquiera («Morgan puede equivocarse…»).
- **La vista de estado habla en palabras de quien usa Morgan** (2.0.29): «Conversar
  y razonar», «Internet», «Tus datos», «Correo», con «Funciona / Con problemas / No
  disponible», y qué puede hacer Morgan donde corre. Nada de proveedores de modelos,
  nombres de bases de datos, APIs de correo ni subsistemas. Las dos bases se enseñan
  como «tus datos» con el **peor** de sus estados. El detalle sigue en la respuesta
  de `/status` para diagnosticar: esto cambia lo que se pinta, no lo que el servidor
  sabe.
- **Barra de arriba**: dónde estás (Morgan · espacio), el estado real con el número de
  herramientas, y el avatar con el menú de la cuenta. El botón del menú del móvil va
  aquí, **dentro del flujo**: antes flotaba y se solapaba con lo primero de cada vista.
- **Qué puede hacer Morgan, según dónde corre** (2.0.33): el texto de la portada
  sale del entorno que dice `/status`, no de cómo se compiló la web. Y mientras
  Morgan contesta, el campo dice «Morgan está respondiendo…»; antes decía «Morgan
  API desconectada…», porque ocupado y caído compartían la misma variable.
- **Portada**: insignia (dónde corre Morgan, o «Chat temporal»), el saludo «¿Qué
  *hacemos* hoy?» con la palabra destacada, qué puede hacer Morgan aquí y **tres
  tarjetas que dependen del entorno**: en la nube, buscar, leer una página y recordar;
  en tu equipo, revisar un proyecto, planear y buscar. Ofrecer revisar un proyecto en
  la nube sería prometer algo que ahí no puede hacer.
- **Compositor**: el campo arriba y una fila debajo con adjuntar, dictar y enviar (solo la
  flecha). Las tarjetas son solo icono, título y descripción: pulsarlas escribe.
  Con mensajes se ancla abajo con 760 px de ancho máximo.

- **Espacios**: un `<select>` nativo arriba de la barra lateral. Cambiar de espacio
  empieza una conversación nueva ([datos.md](datos.es.md#5-espacios-de-trabajo-v2010)).
- **La cuenta, arriba a la derecha**: con sesión, avatar y menú (cerrar sesión); sin
  sesión (solo en local), «Iniciar sesión» y «Registrarse», que abren el acceso **por
  encima** de Morgan para no tirar una conversación a medias.
- **Qué está haciendo Morgan**: bajo el indicador de escritura, una frase sacada de
  los eventos del turno («Buscando en internet…»). Sin evento no hay frase, y el
  latido no la cambia: sería inventarse progreso ([agente.md](agente.es.md#el-streaming-post-chatstream)).
- **Automatizaciones** (4.14): una vista con la **bandeja** arriba (lo que contó cada
  ejecución, con lo que usó) y las **programadas** debajo, con pausar, reanudar y borrar.
  Crearlas no: se piden en el chat. **Verla es leerla**: sin botón de «marcar como
  leído». En la navegación, el número de avisos sin leer, que se mira cada minuto con
  una ruta que no trae los textos. Cada petición manda la zona horaria del navegador
  (`X-Morgan-Zona`), para que «a las 9» sean las 9 de quien la crea.
- **Planes pendientes arriba del chat**, no en una pestaña: bloquean el trabajo.
  **«Aprobar y ejecutar» ejecuta** (4.0): el chat manda solo el turno «✅ Plan aprobado:
  …» con `ejecutar_plan`, sin que haya que escribir «adelante».

## 3. Control sobre el turno

| Acción | Cómo, y por qué así |
|---|---|
| **Detener** | Aborta la petición con `AbortController` y una excepción propia, para no confundirla con un tiempo agotado. El texto vuelve al compositor. El turno termina y se guarda en el servidor. **Desde la 3.4, además**, avisa a la nube (`POST /chat/parar` con el `turno` del evento `inicio`) para que **pare lo que se está haciendo en el PC**. Solo el botón: cambiar de aplicación en el móvil no cancela nada |
| **Reintentar** | Conserva texto y archivos del último envío. **Desde la 4.5, no aparece si la conexión se cortó con el turno ya empezado**: ese turno sigue en el servidor, así que la web mira la conversación cada 4 s (hasta 200 s) y pinta la respuesta cuando está guardada. Medido en mi prueba desde el móvil: el reintento procesaba la misma pregunta otra vez con la primera aún en marcha. Si el servidor dice `TURNO_EN_CURSO`, el mensaje vuelve al compositor y se espera el anterior |
| **Regenerar** | Corta el historial **antes** de la respuesta descartada: si Morgan la viera, tendería a repetirla |
| **Editar y continuar** | En línea, solo el último mensaje propio; lo posterior se descarta |
| **Arrastrar y soltar** | Por la misma ruta que el clip, de uno en uno, con el nombre del archivo en cada error |
| **Voz** | `POST /uploads/{id}/transcripcion` devuelve el texto al compositor **para revisarlo antes de enviarlo**. Un intento de inyección por audio deja de ser invisible. Gasta cupo igual |
| **Descargar una copia del PC** (3.1-E) | Cuando Morgan trae un archivo del PC, el turno emite `archivo_listo` y el chat pinta **un botón** bajo la respuesta, con nombre y tamaño. **No se lee el texto del modelo**: la primera versión dependía de que escribiera el enlace tal cual, y escribía la dirección a su manera, así que me salía «una especie de enlace» y ningún botón. Al recargar una conversación vieja el botón no está (el historial guarda el texto, no los eventos); el archivo sigue en Ajustes → Tus archivos |

**Tus equipos** (Ajustes, 3.7): de cada PC, si está **conectado ahora** y qué ofrece
(leer, escribir, terminal, procesos), y su historial de órdenes de 7 días, que se carga
al abrirlo. Sin argumentos ni contenido: qué capacidad, cuándo, cómo acabó y cuánto tardó.
Desde la 3.8, **si tiene una versión vieja** («Hay una versión nueva: …; el PC te
preguntará si instalarla») y cuándo cambió su credencial. Al pedir un código, la web da
**la línea de PowerShell que instala el agente** con él en un PC nuevo.

**Lo que se hace en el PC, contado.** El progreso del turno dice qué herramienta del PC
corre («Creando el archivo en tu PC…», «Esperando a que lo confirmes en tu PC…») y,
desde la 3.4, cómo va la orden allí (evento `equipo`): «En cola en tu PC», «Cancelando
en tu PC…». Y un plan enseña los argumentos de cada paso con sus saltos de línea: lo que
Morgan va a escribir se aprueba viéndolo tal cual (3.3).

**Los enlaces del chat.** El renderizador de Markdown no dibuja enlaces, a propósito: el
texto lo escribe el modelo, que puede haber leído una página con instrucciones
escondidas, y convertir cualquier dirección en botón sería regalar un sitio donde poner
un enlace falso con la cara de Morgan. **La única excepción** es la ruta de descarga de
este mismo servidor (`/api/uploads/{id}/contenido`, también escrita como dirección
completa de la propia web). Cualquier otra dirección se sigue viendo como texto.

Las cuatro primeras comparten una sola función de envío. Los botones aparecen al
pasar por encima y solo si aplican; el de reintentar tras un error se ve siempre.

**Eliminar la cuenta** pide la contraseña aunque haya sesión, borra primero los
datos y después la cuenta (un fallo a medias deja una cuenta vacía, no filas
huérfanas), no acepta ningún identificador y no borra la auditoría.

## 4. El móvil

El 2026-09-10, abriendo la web en teléfonos simulados con la sesión abierta,
**no había navegación**: una regla móvil puesta antes de la base ocultaba el botón
del menú a todos los anchos. En un escritorio estrecho no se reproducía. El mismo
recorrido (once vistas en tres teléfonos) encontró el compositor fuera de pantalla,
iOS ampliando la página en campos de menos de 16 px, sugerencias desbordadas en
320 px y `100vh` mintiendo sobre la altura. En producción, el aviso de confirmar el
correo empujaba el chat entero fuera.

Reglas que quedaron:
- **Todo lo móvil vive al final del CSS**; antes, cualquier regla posterior lo tapa
  sin avisar. Lo fija `tests/test_movil_web.py`.
- Nada flota encima del contenido: el botón del menú y la cuenta van en
  `.barra-superior`, en el flujo (antes se reservaba un hueco de 52 px). En 620 px o
  menos las tarjetas pasan a una columna y enviar se queda en el icono; en 480 px la
  pastilla del espacio desaparece (está en la barra lateral).
- Objetivos táctiles de 44 px, `100dvh` y `env(safe-area-inset-*)`.

> **Para medir el móvil, no vale una captura con una ventana estrecha**: Edge sin
> ventana no baja de 492 px y recorta la imagen. Hay que medir `innerWidth` o meter
> la web en un iframe. Así se comprobó en la auditoría 2.3 que a 360 y 400 px no se
> sale nada.

## 5. Lo que rompía la web en producción

El 2026-09-07, el chat de la web **no había funcionado nunca en la nube**. Diez
defectos que convivían y se tapaban entre sí; ninguno se veía en local ni lo
detectaba la suite. Se encontraron reproduciendo contra el despliegue real lo que
hace el navegador.

| # | Defecto | Arreglo |
|---|---|---|
| 1 | El token CSRF no se podía leer desde otro dominio: toda la web en solo lectura | El token viaja también en el JSON de `/auth/*` |
| 2 | **Supabase no filtraba por usuario en casi ninguna consulta**: 500 al crear conversaciones y, donde no fallaba, cada usuario veía lo de los demás | `_mio()` en las 32 operaciones de los 6 repositorios; el usuario nunca se pasa por parámetro |
| 3 | Si `/auth/yo` fallaba, la interfaz suponía «este Morgan no pide cuentas» | Se reintenta, y si sigue fallando se enseña el acceso |
| 4 | Botones que fallaban en silencio | El error se muestra |
| 5 | La cookie caducada no se borraba entre dominios | Se borra con los mismos atributos con que se puso |
| 6 | La función de cupo estaba fuera del alcance de PostgREST: 500 **solo para quien no era propietario** | Envoltorio en `public` solo para `service_role` |
| 7 | Y esa función nunca había funcionado (`text` contra `date`) | Conversión dentro de la función |
| 8 | **La memoria no persistía en la nube**: respondía 200 y la tabla estaba vacía | La memoria usa el repositorio del contenedor |
| 9 | **En el iPhone la sesión se caía siempre**: Safari descarta las cookies de terceros | Proxy `/api/*` en Vercel: la API pasa a ser del mismo origen |
| 10 | Subir un archivo llegaba sin sesión: tenía su propio `fetch`, copiado y desactualizado | **Un solo `fetch` en todo el frontend**, fijado por prueba |

Lo que enseñaron:

- **Una auditoría por lectura solo encuentra lo que se le ocurre buscar.** La de la
  V1.8 buscó SQL sin filtrar; las consultas de Supabase son PostgREST y no aparecieron.
- **Reproducir lo que hace el navegador no es usar un navegador.** `httpx` guarda
  cualquier cookie; el 9 solo existía en Safari.
- **Un fichero de configuración que no se aplica no avisa.** El proxy se escribió
  primero en un `web/vercel.json` que Vercel no lee. Ahora una prueba exige un solo
  `vercel.json`.
- **Contar filas, no fiarse del 200.** El 8 cumplía en apariencia.

### Los 120 segundos del proxy

El borde de Vercel corta una respuesta **callada** a los 120,1 s (medido dos veces
seguidas). Por eso `/chat` en la nube se rinde a los 85 s y **la petición contesta a
los 100 s** aunque el turno siga: el turno no se mata, termina y se guarda, y el
aviso dice «sigo trabajando en ello», no «error», para que nadie lo repita y pague
dos veces. Desde la 2.0.14 la web usa `/chat/stream`, con latidos, y el corte deja
de afectarle: el techo es de silencio, no de duración.

## 6. Pruebas del frontend

Llegaron por un fallo que solo pasaba **cuando todo funcionaba**: un `return` dentro
del primer `try` saltaba el `finally` del segundo, y la web se quedaba para siempre
en «el servidor está arrancando» con el servidor respondiendo en 0,3 s. Ni las
pruebas de Python, ni `tsc`, ni las verificaciones por HTTP podían verlo.

| Capa | Qué comprueba |
|---|---|
| **Vitest** (`npm test` en `web/`) | Comportamiento: `Cuenta.test.tsx` (el arranque siempre acaba), `api.test.ts` (CSRF, cancelar, sesión perdida, subidas, páginas HTML de error), `progreso.test.ts`, `Tokens.test.tsx` (el valor se ve una vez, revocar confirma, lo que se pide por defecto; 12 de 12 mutaciones), `DescargaWindows.test.tsx` (solo desde Windows y no dentro del programa) |
| **Python**: `tests/test_frontend_estabilidad.py` | Invariantes de todo el código: **toda bandera de «ocupado» se apaga en un `finally`** y **un solo `fetch`** |
| **Python**: `test_contrato_web.py`, `test_movil_web.py`, `test_tema_web.py`, `test_proxy_mismo_origen.py`, `test_cabeceras_web.py` | Que cada ruta que llama la web existe, las reglas móviles, el contraste, el proxy y las cabeceras de seguridad |

```bash
cd web
npm run build   # lo que ejecuta Vercel; empieza por tsc -b, que NO es tsc --noEmit
npm test
npm run lint
```

La configuración de Vitest vive en `vitest.config.ts` y no en `vite.config.ts`:
puesta ahí, `tsc -b` fallaba y **Vercel dejaba de desplegar** sin avisar.

**Falta cubrir** la lógica del chat (detener, regenerar, editar) con pruebas de
comportamiento.
