# Privacidad y condiciones de uso de Morgan

> Vigente desde el 2026-10-03.

Morgan es un proyecto personal que mantengo yo, una sola persona, y que cualquiera puede
usar gratis creándose una cuenta. Aquí explico qué datos guarda, dónde, con quién se
comparten para que Morgan funcione, cuánto tiempo, y qué puedes hacer con ellos. Si algo de
esto cambia, lo cambio aquí antes.

## Qué guarda Morgan

| Qué | Para qué |
|---|---|
| Tu cuenta: nombre de usuario, correo y la contraseña **cifrada con un hash** (nunca en claro) | Que puedas entrar, y recuperar la contraseña por correo |
| Tus conversaciones, lo que Morgan recuerda de ti (memoria), tus planes, tareas, automatizaciones y los avisos de la bandeja | Que Morgan las tenga la próxima vez que entres |
| Los archivos que subes | Que Morgan pueda leerlos en la conversación |
| Cuánto usas Morgan cada día (número de mensajes) | Repartir el cupo gratuito entre todos |
| Tus sesiones abiertas (el navegador desde el que entras) e intentos de entrar | Poder cerrarlas, y frenar a quien intente adivinar contraseñas |
| Si conectas tu PC: el nombre del equipo, su versión y el historial de órdenes que Morgan le mandó (30 días) | Que puedas ver qué se hizo en tu PC y revocarlo |

**Lo que no guarda**: el contenido de tu PC. Morgan solo ve en tu PC lo que tú permites en
«Morgan en tu PC» (las carpetas y capacidades que enciendes), en el momento en que lo pide, y
lo que lee pasa por la conversación como cualquier otro mensaje. Las claves de acceso al PC
quedan cifradas en tu equipo.

## Con quién se comparte

No vendo ni cedo tus datos a nadie. Para funcionar, Morgan usa estos servicios, que los
procesan en mi nombre:

| Servicio | Qué recibe | Dónde |
|---|---|---|
| **Supabase** | La base de datos y los archivos subidos | Estados Unidos (us-east-1) |
| **Render** | El servidor de Morgan (lo que pasa por él, no lo guarda) | Estados Unidos (Virginia) |
| **Vercel** | La página web | Global |
| **Groq**, **Google (Gemini)** y, como último recurso, **OpenAI** | El texto de cada conversación, para que el modelo conteste | Estados Unidos |
| **Serper (Google)** | Lo que Morgan busca en internet por ti | Estados Unidos |
| **Brevo** | Tu correo, solo para mandarte la verificación o la recuperación de contraseña | Unión Europea |
| **GitHub** | Si usas Morgan para Windows: una consulta al día a su página de versiones, para avisarte de una nueva (como cualquier visita, ve la IP de tu PC; no lleva nada tuyo) | Estados Unidos |

Cada proveedor de modelos trata lo que recibe según sus propias condiciones. **Ojo con una**:
el plan gratuito de la API de Gemini puede usar lo que se le envía para mejorar los productos
de Google. Morgan lo usa solo como respaldo cuando Groq no contesta, pero si una conversación
tuya pasa por Gemini, le aplican esas condiciones. No escribas en Morgan nada que no quieras
que lea un proveedor de modelos.

## Cuánto tiempo

Tus datos se guardan mientras tengas la cuenta. Lo que caduca solo: los avisos de la bandeja
y el historial de órdenes al PC (30 días), las sesiones y los enlaces de recuperación
(cuando vencen). Hago copias de seguridad de la base, que guardo yo, en mi equipo.

## Lo que puedes hacer

- **Descargar todos tus datos** (Ajustes → Exportar mis datos).
- **Borrar tu cuenta** (Ajustes → Eliminar la cuenta): se borra todo lo tuyo de la base y se
  desconectan tus PC. Lo que pudiera quedar en una copia de seguridad desaparece cuando esa
  copia se sustituye.
- Revocar el acceso de un PC o de un token de la API en cualquier momento.
- Preguntarme lo que quieras sobre tus datos, o pedirme que borre algo: abre una consulta en
  [el repositorio de Morgan](https://github.com/wv-Andy/Morgan/issues) (sin poner ahí nada
  privado; te contesto y seguimos por donde prefieras).

## Condiciones de uso

- Morgan es gratuito, se ofrece **tal cual** y sin garantías: puede fallar, estar caído o
  cambiar. No lo uses para nada en lo que un error te cause un daño serio.
- Morgan puede equivocarse. Lo que hace en tu PC lo propone antes y lo apruebas tú; revisa
  lo que apruebas.
- Hay un cupo de uso diario por persona, para que alcance para todos.
- No lo uses para nada ilegal, para atacar a otros ni para sobrecargar el servicio. Si lo
  haces, puedo cerrar la cuenta.
- El código de Morgan es público ([wv-Andy/Morgan](https://github.com/wv-Andy/Morgan)).
