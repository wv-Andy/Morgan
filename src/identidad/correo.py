"""
Envío de correo para la recuperación de contraseña (identidad, V2.0 adelantada).

**Esto no es un sistema de correo simulado.** O el correo sale de verdad, o no
sale y se dice claramente. Lo que no hace es fingir: un `enviar()` que devuelve
`True` sin mandar nada convierte la recuperación en una función rota que parece
funcionar, y el fallo solo se descubre el día que alguien pierde su contraseña.

Hay **dos transportes**, y cuál usar no es cuestión de gustos:

- **API HTTP** (Brevo, Resend). Va por el puerto 443, como cualquier petición web.
- **SMTP** (`smtplib`, biblioteca estándar). Va por el 587 o el 465.

**En un hosting como Render, SMTP no funciona.** Los puertos SMTP salientes están
bloqueados —medida antispam habitual en planes gratuitos— y el intento muere con
`Network is unreachable`. La configuración puede ser perfecta y no salir nada. Se
descubrió así, en producción, con las credenciales de Gmail bien puestas.

Por eso, si hay una API HTTP configurada, se prefiere siempre. En local SMTP va
bien y sigue soportado: es lo más cómodo para probar sin dar de alta nada.

**Sin nada configurado no se envía, y el enlace tampoco queda en el log.** El
filtro de secretos lo enmascara —hace bien— así que dejarlo escrito solo producía
un `?token=***` inservible. Para probar en desarrollo, el token se saca del
servicio directamente: `solicitar_recuperacion()` lo devuelve.

## Elegir proveedor

Lo que decide no es el precio, es **a quién puedes escribir**:

| | Sin dominio propio | Cuota gratuita |
|---|---|---|
| **Brevo** | ✅ A cualquiera, verificando tu propia dirección | 300/día |
| **Resend** | ❌ Solo a tu dirección de registro | 3.000/mes |

Para Morgan, donde el correo tiene que llegarle a **otras personas**, Brevo
funciona sin comprar un dominio y Resend no.

## Variables

    # API HTTP — lo que funciona en la nube. Se prefiere si está.
    MORGAN_EMAIL_API       'brevo' o 'resend'
    MORGAN_EMAIL_API_KEY   la clave del proveedor
    MORGAN_EMAIL_FROM      remitente; en Brevo, una dirección verificada

    # SMTP — para local, o un servidor que no bloquee la salida
    MORGAN_SMTP_HOST / PORT / USER / PASSWORD / FROM

    MORGAN_WEB_URL         base de la web, para construir el enlace
"""

import logging
import os
import smtplib
import ssl
import time
from email.message import EmailMessage

from src.config import get_settings

logger = logging.getLogger(__name__)

ASUNTO = "Recuperar tu contraseña de Morgan"
ASUNTO_VERIFICACION = "Confirma tu correo en Morgan"

PROVEEDORES = ("brevo", "resend")

URL_BREVO = "https://api.brevo.com/v3/smtp/email"
URL_RESEND = "https://api.resend.com/emails"


# --- Qué pasó en el último envío ---------------------------------------------
#
# El fallo de un envío es invisible desde fuera: la respuesta de /auth/recuperar
# es la misma pase lo que pase, para no delatar si la cuenta existe. Sin esto, la
# única forma de saber por qué no llega un correo es entrar en el panel del
# servidor y leer los logs, que es justo lo que no siempre se puede hacer.
#
# Se guarda en memoria: no interesa el historial, solo lo último. Se pierde al
# reiniciar, y está bien que así sea.
_ultimo: dict = {"momento": None, "ok": None, "detalle": None}


def olvidar_ultimo_intento() -> None:
    """Borra lo anotado. Existe para las pruebas: es estado de módulo, y sin esto
    una prueba que provoca un fallo de envío se lo deja puesto a la siguiente."""
    _ultimo.update({"momento": None, "ok": None, "detalle": None})


def ultimo_intento() -> dict:
    """Qué pasó la última vez que se intentó enviar. Lo usa `/status`."""
    return dict(_ultimo)


def _anotar(ok: bool, detalle: str) -> None:
    _ultimo.update({"momento": time.time(), "ok": ok, "detalle": _sanear(detalle)})


def _sanear(texto: str) -> str:
    """Quita de un mensaje cualquier cosa que no deba publicarse.

    `/status` es público. Los errores traen el texto que devuelve el proveedor, y
    ese texto puede incluir la dirección o parte de la clave con la que se intentó
    autenticar.
    """
    limpio = " ".join(str(texto).split())[:200]

    for secreto in _secretos():
        if secreto and len(secreto) > 3:
            limpio = limpio.replace(secreto, "···")

    return limpio


def _secretos() -> list[str]:
    return [
        (os.getenv("MORGAN_EMAIL_API_KEY") or "").strip(),
        os.getenv("MORGAN_SMTP_PASSWORD") or "",
        (os.getenv("MORGAN_SMTP_USER") or "").strip(),
        (os.getenv("MORGAN_EMAIL_FROM") or "").strip(),
    ]


# --- Configuración ------------------------------------------------------------


def _config_api() -> dict | None:
    """La configuración de la API HTTP, o None si no está completa."""
    proveedor = (os.getenv("MORGAN_EMAIL_API") or "").strip().lower()
    clave = (os.getenv("MORGAN_EMAIL_API_KEY") or "").strip()

    if proveedor not in PROVEEDORES or not clave:
        return None

    remitente = (
        os.getenv("MORGAN_EMAIL_FROM") or os.getenv("MORGAN_SMTP_FROM") or ""
    ).strip()
    if not remitente:
        return None

    return {"proveedor": proveedor, "clave": clave, "remitente": remitente}


def _config_smtp() -> dict | None:
    host = (os.getenv("MORGAN_SMTP_HOST") or "").strip()
    usuario = (os.getenv("MORGAN_SMTP_USER") or "").strip()
    password = os.getenv("MORGAN_SMTP_PASSWORD") or ""

    if not (host and usuario and password):
        return None

    return {
        "host": host,
        "puerto": int(os.getenv("MORGAN_SMTP_PORT") or 587),
        "usuario": usuario,
        "password": password,
        "remitente": (os.getenv("MORGAN_SMTP_FROM") or usuario).strip(),
    }


def _base_web() -> str:
    return (os.getenv("MORGAN_WEB_URL") or "http://localhost:5173").rstrip("/")


def enlace_de_recuperacion(token: str) -> str:
    return f"{_base_web()}/restablecer?token={token}"


def _cuerpo(enlace: str) -> str:
    return (
        "Has pedido recuperar tu contraseña de Morgan.\n\n"
        f"Abre este enlace para elegir una nueva:\n\n{enlace}\n\n"
        "El enlace caduca en 30 minutos y solo sirve una vez.\n\n"
        "Si no has sido tú, ignora este correo: tu contraseña no ha cambiado y "
        "nadie ha entrado en tu cuenta.\n"
    )


# --- Transportes --------------------------------------------------------------


def _enviar_por_api(
    config: dict, destinatario: str, cuerpo: str, asunto: str = ASUNTO
) -> None:
    """Envía por la API HTTP del proveedor.

    Es el transporte que funciona en la nube: va por el 443, como cualquier
    petición web, y ningún hosting lo bloquea.
    """
    import httpx

    if config["proveedor"] == "brevo":
        url = URL_BREVO
        cabeceras = {"api-key": config["clave"], "accept": "application/json"}
        json = {
            "sender": {"email": config["remitente"], "name": "Morgan"},
            "to": [{"email": destinatario}],
            "subject": asunto,
            "textContent": cuerpo,
        }
    else:  # resend
        url = URL_RESEND
        cabeceras = {"authorization": f"Bearer {config['clave']}"}
        json = {
            "from": config["remitente"],
            "to": [destinatario],
            "subject": asunto,
            "text": cuerpo,
        }

    respuesta = httpx.post(url, headers=cabeceras, json=json, timeout=20)

    if respuesta.status_code >= 400:
        # El cuerpo trae el motivo real —remitente sin verificar, clave
        # invalida, cuota agotada— y es lo unico que permite arreglarlo. Se
        # sanea antes de publicarlo en ningun sitio.
        raise RuntimeError(
            f"{config['proveedor']} respondió {respuesta.status_code}: "
            f"{respuesta.text[:200]}"
        )


def _enviar_por_smtp(
    config: dict, destinatario: str, cuerpo: str, asunto: str = ASUNTO
) -> None:
    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = config["remitente"]
    mensaje["To"] = destinatario
    mensaje.set_content(cuerpo)

    contexto = ssl.create_default_context()

    if config["puerto"] == 465:
        with smtplib.SMTP_SSL(config["host"], 465, context=contexto, timeout=20) as smtp:
            smtp.login(config["usuario"], config["password"])
            smtp.send_message(mensaje)
    else:
        with smtplib.SMTP(config["host"], config["puerto"], timeout=20) as smtp:
            smtp.starttls(context=contexto)
            smtp.login(config["usuario"], config["password"])
            smtp.send_message(mensaje)


# --- Punto de entrada ---------------------------------------------------------


def enlace_de_verificacion(token: str) -> str:
    return f"{_base_web()}/?verificar={token}"


def _cuerpo_verificacion(enlace: str) -> str:
    return (
        "Bienvenido a Morgan.\n\n"
        "Confirma que esta dirección es tuya abriendo este enlace:\n\n"
        f"{enlace}\n\n"
        "El enlace caduca en 24 horas y solo sirve una vez.\n\n"
        "Puedes usar Morgan sin confirmar el correo. Lo que no podrás hacer "
        "hasta entonces es recuperar tu contraseña si la olvidas, porque el "
        "enlace de recuperación va justo a esta dirección.\n\n"
        "Si no has creado ninguna cuenta, ignora este correo.\n"
    )


def enviar_verificacion(destinatario: str, token: str) -> bool:
    """Envía el correo de confirmación. Devuelve si salió de verdad."""
    return _enviar(
        destinatario,
        _cuerpo_verificacion(enlace_de_verificacion(token)),
        ASUNTO_VERIFICACION,
        "verificación",
    )


def enviar_recuperacion(destinatario: str, token: str) -> bool:
    """Envía el correo de recuperación. Devuelve si salió de verdad."""
    return _enviar(
        destinatario,
        _cuerpo(enlace_de_recuperacion(token)),
        ASUNTO,
        "recuperación",
    )


def _enviar(destinatario: str, cuerpo: str, asunto: str, clase: str) -> bool:
    """El transporte, compartido por los dos tipos de correo.

    Se extrajo al añadir la verificación. Duplicarlo habría duplicado también
    la elección de transporte, el diagnóstico de por qué falla y el cuidado de
    no escribir el enlace en el log — y esa última es la que más caro sale
    olvidar en una copia.
    """

    # La API HTTP manda sobre SMTP cuando estan las dos: es la que funciona en
    # todas partes, y tener ambas configuradas casi siempre significa que se dejo
    # la vieja puesta al migrar.
    api = _config_api()
    smtp = _config_smtp() if api is None else None

    if api is None and smtp is None:
        _anotar(False, "No hay correo configurado")

        if get_settings().is_cloud:
            logger.error(
                "Sin correo configurado (MORGAN_EMAIL_API/_API_KEY o "
                "MORGAN_SMTP_*): no se pudo enviar el correo de %s. La "
                "recuperación de contraseña NO funciona en este despliegue.",
                clase,
            )
            return False

        # En local tampoco se escribe el enlace, y no por olvido: el filtro de
        # secretos del log enmascara todo lo que parece "token=", asi que lo que
        # quedaba escrito era ".../restablecer?token=***" — inservible. Burlarlo
        # seria publicar un secreto en el log a proposito.
        #
        # Para probar el flujo en desarrollo sin dar de alta ningun proveedor, el
        # token se saca directamente del servicio:
        #
        #     ServicioDeCuentas(Database(...)).solicitar_recuperacion("tu@correo")
        logger.warning(
            "Sin correo configurado: no se ha enviado nada a %s. Define "
            "MORGAN_EMAIL_API y MORGAN_EMAIL_API_KEY para que salga de verdad.",
            destinatario,
        )
        return False

    try:
        if api is not None:
            _enviar_por_api(api, destinatario, cuerpo, asunto)
        else:
            _enviar_por_smtp(smtp, destinatario, cuerpo, asunto)

    except smtplib.SMTPAuthenticationError as exc:
        _anotar(False, f"El servidor rechazó las credenciales: {exc}")
        raise

    except (OSError, smtplib.SMTPException) as exc:
        # 'Network is unreachable' aqui no es una configuracion mal puesta: es el
        # hosting bloqueando la salida SMTP, cosa habitual en planes gratuitos.
        # El remedio no es revisar la contrasena, es dejar de usar SMTP.
        pista = ""
        if smtp is not None and "unreachable" in str(exc).lower():
            pista = (
                " — el hosting bloquea la salida SMTP; usa una API HTTP "
                "(MORGAN_EMAIL_API=brevo)"
            )
        _anotar(False, f"No se pudo contactar con el servidor de correo: {exc}{pista}")
        raise

    except Exception as exc:
        _anotar(False, str(exc))
        raise

    # Se registra QUE se envio, nunca el enlace ni el token: el log no debe
    # contener nada que sirva para entrar en una cuenta.
    _anotar(True, "Enviado correctamente")
    logger.info("Correo de %s enviado", clase)
    return True


def transporte() -> str | None:
    """Cómo se enviaría un correo ahora mismo: 'api:<proveedor>', 'smtp' o None."""
    api = _config_api()
    if api is not None:
        return f"api:{api['proveedor']}"

    return "smtp" if _config_smtp() is not None else None


def smtp_configurado() -> bool:
    """Si la recuperación por correo puede funcionar. Lo usa `/status`."""
    return transporte() is not None
