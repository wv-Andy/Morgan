"""
La integración con Google Calendar (V2.0.17).

**Esto autoriza a Morgan a usar TU calendario de Google. No es iniciar sesión.**
Mismo intercambio que GitHub —ver `github.py`—, con tres diferencias que importan:

## 1. El token caduca a la hora

Google devuelve un token de acceso que vale una hora y uno de **renovación**. Para
recibir el segundo hay que pedirlo (`access_type=offline`), y para recibirlo
**siempre** —también al reconectar— hay que forzar la pantalla de consentimiento
(`prompt=consent`). Sin eso, la segunda conexión vuelve sin token de renovación y
Morgan dejaría de funcionar a la hora sin explicar por qué. `oauth.token_vigente`
lo renueva antes de cada llamada.

## 2. El modo de pruebas caduca a los 7 días

Lo decidí: la app va en modo de pruebas, sin verificar. La autorización
caduca a los 7 días y renovar devuelve `invalid_grant`. Eso se convierte en
`AutorizacionCaducada` con un mensaje que dice qué hacer, y se anota en la
integración para que Servicios lo enseñe.

## 3. Los permisos: `calendar.events`

Ver y editar **eventos**, no crear, compartir ni borrar calendarios. Es el mínimo
que permite escribir, que es lo que se decidió; y la escritura solo ocurre con un
plan aprobado (`Tool.exige_plan`). Para Google es un permiso «sensible»: en modo de
pruebas no exige verificación.

## Lo que el modelo escribe no cambia a qué se llama

Ninguna URL se compone con lo que dice el modelo. El calendario es siempre
`primary` y los datos del evento viajan en el cuerpo JSON, nunca en la ruta. Es la
lección de la fuga de rutas de GitHub (ver `integraciones.md`).
"""

import logging
import os
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from src.integraciones.oauth import (
    AutorizacionCaducada,
    Credenciales,
    ErrorDelServicio,
    ServicioNoConfigurado,
)

logger = logging.getLogger(__name__)

AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
REVOCAR = "https://oauth2.googleapis.com/revoke"
USUARIO = "https://openidconnect.googleapis.com/v1/userinfo"
EVENTOS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

TIMEOUT = 20.0

#: Lo que se le dice a la persona cuando caduca la autorización. Con el porqué: sin
#: él, «vuelve a conectar» cada semana parece una avería.
CADUCADA = (
    "La autorización de Google Calendar caducó. Mientras la app de Google esté en "
    "modo de pruebas, pasa cada 7 días: vuelve a conectarlo en Servicios."
)


def configurado() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID", "").strip() and os.getenv("GOOGLE_CLIENT_SECRET", "").strip())


def _credenciales() -> tuple[str, str]:
    cliente = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    secreto = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not cliente or not secreto:
        raise ServicioNoConfigurado("Faltan GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET.")
    return cliente, secreto


# --- El intercambio OAuth ------------------------------------------------------


def url_de_autorizacion(estado: str, scopes: tuple[str, ...], callback: str) -> str:
    cliente, _ = _credenciales()
    return f"{AUTORIZAR}?" + urlencode({
        "client_id": cliente,
        "redirect_uri": callback,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": estado,
        # Sin estos dos no llega token de renovación, o solo la primera vez.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    })


def _pedir_token(datos: dict) -> dict:
    """POST al endpoint de tokens. Traduce `invalid_grant` a `AutorizacionCaducada`."""
    try:
        respuesta = httpx.post(TOKEN, data=datos, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise ErrorDelServicio(f"No se pudo contactar con Google: {exc}") from exc

    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = {}

    if respuesta.status_code >= 400:
        if cuerpo.get("error") == "invalid_grant":
            raise AutorizacionCaducada(CADUCADA)
        # El código de error de Google no lleva secretos y dice qué pasó.
        raise ErrorDelServicio(
            f"Google respondió {respuesta.status_code}: {cuerpo.get('error') or 'sin detalle'}."
        )
    if not cuerpo.get("access_token"):
        raise ErrorDelServicio("Google no devolvió ningún token.")
    return cuerpo


def canjear(codigo: str, callback: str) -> Credenciales:
    """Cambia el código por el token de acceso y el de renovación.

    El secreto viaja desde el backend: la URL de retorno apunta a Render, no a la web.
    """
    cliente, secreto = _credenciales()
    cuerpo = _pedir_token({
        "code": codigo,
        "client_id": cliente,
        "client_secret": secreto,
        "redirect_uri": callback,
        "grant_type": "authorization_code",
    })
    if not cuerpo.get("refresh_token"):
        # Sin él, Morgan dejaría de funcionar a la hora sin decir nada. Mejor fallar
        # ahora, que es cuando se puede volver a intentar.
        raise ErrorDelServicio(
            "Google no devolvió token de renovación. Vuelve a conectar y acepta los permisos."
        )
    return Credenciales(
        token=cuerpo["access_token"],
        scopes=tuple((cuerpo.get("scope") or "").split()),
        refresco=cuerpo["refresh_token"],
        expira_en=time.time() + float(cuerpo.get("expires_in") or 3600),
    )


def refrescar(refresco: str) -> tuple[str, float]:
    """Un token de acceso nuevo a partir del de renovación. Devuelve (token, caducidad)."""
    cliente, secreto = _credenciales()
    cuerpo = _pedir_token({
        "refresh_token": refresco,
        "client_id": cliente,
        "client_secret": secreto,
        "grant_type": "refresh_token",
    })
    return cuerpo["access_token"], time.time() + float(cuerpo.get("expires_in") or 3600)


def cuenta_de(token: str) -> dict:
    """De quién es la cuenta conectada, para enseñarlo en Servicios."""
    datos = _get(USUARIO, token)
    return {"login": datos.get("email"), "nombre": datos.get("name"), "avatar": datos.get("picture")}


def revocar(token: str) -> bool:
    """Le pide a Google que invalide la autorización.

    Con el token de **renovación** se revoca la autorización entera, no solo el token
    de la hora en curso; las rutas pasan ese cuando existe. Que falle no impide
    desconectar.
    """
    try:
        respuesta = httpx.post(REVOCAR, data={"token": token}, timeout=TIMEOUT)
    except httpx.HTTPError:
        return False
    return respuesta.status_code == 200


# --- El calendario ---------------------------------------------------------------


def _get(url: str, token: str, params: dict | None = None) -> dict:
    try:
        respuesta = httpx.get(
            url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ErrorDelServicio(f"No se pudo contactar con Google Calendar: {exc}") from exc
    return _cuerpo(respuesta)


def _cuerpo(respuesta: httpx.Response) -> dict:
    if respuesta.status_code == 401:
        raise AutorizacionCaducada(CADUCADA)
    if respuesta.status_code == 403:
        raise ErrorDelServicio(
            "Google Calendar no dio permiso para esto. Si conectaste antes de que Morgan "
            "pudiera escribir, vuelve a conectarlo para conceder los permisos nuevos."
        )
    if respuesta.status_code >= 400:
        try:
            detalle = respuesta.json().get("error", {}).get("message")
        except (ValueError, AttributeError):
            detalle = None
        raise ErrorDelServicio(f"Google Calendar respondió {respuesta.status_code}: {detalle or 'sin detalle'}.")
    return respuesta.json()


def _evento(crudo: dict) -> dict:
    """Un evento de Google en la forma que ve el modelo: lo útil, nada más."""
    inicio = crudo.get("start") or {}
    fin = crudo.get("end") or {}
    return {
        "titulo": crudo.get("summary") or "(sin título)",
        # `date` en los de todo el día, `dateTime` en los demás.
        "inicio": inicio.get("dateTime") or inicio.get("date"),
        "fin": fin.get("dateTime") or fin.get("date"),
        "todo_el_dia": "date" in inicio and "dateTime" not in inicio,
        "lugar": crudo.get("location"),
        "descripcion": (crudo.get("description") or "")[:500] or None,
        "enlace": crudo.get("htmlLink"),
    }


def listar_eventos(token: str, desde: datetime, hasta: datetime, maximo: int = 50) -> dict:
    """Los eventos entre dos instantes, ya expandidos los repetidos y en orden.

    Devuelve también la zona horaria del calendario: es la de la persona, y la que
    hay que usar para entender «mañana a las 10».
    """
    datos = _get(EVENTOS, token, {
        "timeMin": desde.isoformat(),
        "timeMax": hasta.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(max(1, min(maximo, 100))),
    })
    return {
        "zona_horaria": datos.get("timeZone"),
        "eventos": [_evento(e) for e in datos.get("items") or [] if e.get("status") != "cancelled"],
    }


def zona_horaria(token: str) -> str | None:
    """La zona del calendario principal. La devuelve la lista de eventos, sin permiso extra."""
    return _get(EVENTOS, token, {"maxResults": "1"}).get("timeZone")


def crear_evento(
    token: str, titulo: str, inicio: str, fin: str, zona: str,
    descripcion: str | None = None, lugar: str | None = None,
) -> dict:
    """Crea un evento en el calendario principal. Todo en el cuerpo, nada en la URL."""
    cuerpo: dict[str, Any] = {
        "summary": titulo,
        "start": {"dateTime": inicio, "timeZone": zona},
        "end": {"dateTime": fin, "timeZone": zona},
    }
    if descripcion:
        cuerpo["description"] = descripcion
    if lugar:
        cuerpo["location"] = lugar

    try:
        respuesta = httpx.post(
            EVENTOS, json=cuerpo, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ErrorDelServicio(f"No se pudo contactar con Google Calendar: {exc}") from exc
    return _evento(_cuerpo(respuesta))
