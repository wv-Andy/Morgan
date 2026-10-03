"""
Rutas de los agentes locales (3.0-C): emparejar, listar y revocar.

Dos grupos, y la separación es la seguridad:

- **`/auth/agentes`**: lo que hace la persona desde la web **con su sesión**: pedir
  un código, ver sus equipos y revocarlos. Todo `/auth` está fuera del alcance de los
  tokens personales (`src/identidad/tokens.py`): con un token robado no se emparejan
  equipos ni se revoca el de su dueño.
- **`/agente/…`**: lo que hace el programa del PC, que no tiene sesión. Emparejar
  (consultar y confirmar un código, sin credencial todavía: la consigue ahí) y
  desemparejarse con **su credencial** (`mga_…`), que comprueba esta ruta y nadie
  más: la credencial de un agente no vale en el resto de la API, y un token personal
  no vale aquí.

Contrato: docs/agente-local.md §4-§6.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from src.api.dependencies import CoreContainer, get_container
from src.api.sesion_web import origen_de
from src.identidad import USUARIO_LOCAL, usuario_actual
from src.identidad.agentes import (
    AgenteNoValido,
    CodigoNoValido,
    DemasiadosFallos,
    ServicioDeAgentes,
)
from src.identidad.repositorio import repositorio_de_cuentas

logger = logging.getLogger(__name__)

router_web = APIRouter(prefix="/auth/agentes", tags=["Agentes locales"])
router_agente = APIRouter(prefix="/agente", tags=["Agentes locales"])


def _servicio(container: CoreContainer) -> ServicioDeAgentes:
    return ServicioDeAgentes(repositorio_de_cuentas(container.repositories))


def _error(codigo: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=codigo, detail={"code": code, "message": message})


def _exigir_cuenta() -> str:
    user_id = usuario_actual()
    if user_id == USUARIO_LOCAL:
        raise _error(
            status.HTTP_401_UNAUTHORIZED, "SIN_CUENTA",
            "Emparejar un equipo necesita una cuenta de Morgan.",
        )
    return user_id


# --- En la web, con sesión -----------------------------------------------------


@router_web.post("/codigo", summary="Pedir un código para emparejar un equipo")
def crear_codigo(container: CoreContainer = Depends(get_container)) -> dict:
    """Un código de un solo uso que dura diez minutos. Anula el anterior."""
    from src.api.routes.integraciones import _url_publica_de_la_api

    user_id = _exigir_cuenta()
    creado = _servicio(container).crear_codigo(user_id)
    container.audit_logger.registrar_evento("codigo_agente", actor=user_id)
    # 3.8: la línea que instala el agente en un PC nuevo con este código. La nube, la de
    # verdad (Render, no el proxy de la web): el canal del agente va directo a ella.
    nube = _url_publica_de_la_api()
    #
    # **Se baja a un fichero y se ejecuta**, no `iex (irm …)`: medido en mi PC, la
    # forma «bajar y ejecutar en memoria» la bloquea Windows (Acceso denegado al crear el
    # proceso). Y con `-ExecutionPolicy Bypass` solo para ese proceso: la política de
    # Windows por defecto no deja ejecutar un .ps1 bajado.
    fichero = r"$env:TEMP\instalar-morgan.ps1"
    instalar = (f"irm {nube}/agente/instalar.ps1 -OutFile {fichero}; "
                f"powershell -NoProfile -ExecutionPolicy Bypass -File {fichero} "
                f"-Codigo {creado['codigo']} -Nube {nube}")
    return {"success": True, **creado, "instalar": instalar}


@router_web.get("", summary="Mis equipos emparejados")
def listar(container: CoreContainer = Depends(get_container)) -> dict:
    """Y de cada uno, si está conectado **ahora** y qué ofrece (3.7): antes la web solo
    sabía cuándo se conectó por última vez."""
    from src.canal.registro import REGISTRO

    user_id = _exigir_cuenta()
    agentes = _servicio(container).listar(user_id)
    for agente in agentes:
        conexion = REGISTRO.de(user_id, agente["id"])
        agente["conectado"] = conexion is not None
        agente["capacidades"] = sorted(conexion.capacidades) if conexion else []
    # 3.8: la última versión publicada, para que la web diga si un PC está desactualizado.
    return {"success": True, "agentes": agentes, "ultima_version": _version_publicada()}


def _version_publicada() -> str | None:
    try:
        return json.loads((PUBLICADO / "manifiesto.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


#: Lo más que se devuelve de una vez del historial de órdenes.
HISTORIAL_MAXIMO = 500


@router_web.get("/{agent_id}/ordenes", summary="Historial de órdenes de un equipo")
def ordenes(agent_id: str, dias: int = 7, container: CoreContainer = Depends(get_container)) -> dict:
    """Qué se pidió a ese PC, cuándo, cómo acabó y cuánto tardó (3.7). Nunca argumentos
    ni contenido. Solo de un equipo **tuyo**: uno de otra persona no se encuentra."""
    user_id = _exigir_cuenta()
    servicio = _servicio(container)
    if not any(a["id"] == agent_id for a in servicio.listar(user_id)):
        raise _error(404, "AGENTE_NO_ENCONTRADO", "Ese equipo no existe.")
    desde = time.time() - max(1, min(int(dias), 30)) * 86400
    return {"success": True,
            "ordenes": servicio.repo.ordenes_de_agente(user_id, agent_id, desde, HISTORIAL_MAXIMO)}


@router_web.delete("/{agent_id}", summary="Revocar un equipo")
def revocar(agent_id: str, container: CoreContainer = Depends(get_container)) -> dict:
    """Deja de valer al momento. Uno de otra persona no se encuentra."""
    user_id = _exigir_cuenta()
    if not _servicio(container).revocar(user_id, agent_id):
        raise _error(404, "AGENTE_NO_ENCONTRADO", "Ese equipo no existe o ya estaba revocado.")
    # Si está conectado ahora mismo, se le cierra ya: no espera a la próxima orden.
    from src.canal.registro import CERRADO_CREDENCIAL, REGISTRO

    REGISTRO.cerrar_desde_hilo(user_id, CERRADO_CREDENCIAL, "Este equipo fue revocado.", agent_id)
    container.audit_logger.registrar_evento("revocar_agente", actor=user_id, objetivo=agent_id)
    return {"success": True}


@router_web.post("/{agent_id}/abrir-ajustes", summary="Abrir la ventana de ajustes en ese PC")
def abrir_ajustes(agent_id: str) -> dict:
    """La web **no cambia** la política del PC (regla de la 3.2): solo pide que se abra la
    ventana «Morgan en tu PC» allí, y allí decide la persona (4.17, decisión mía: «las
    dos»). Un PC de otra persona, o desconectado, no se encuentra."""
    from src.canal.despacho import ErrorDelCanal, enviar
    from src.canal.registro import REGISTRO

    user_id = _exigir_cuenta()
    conexion = REGISTRO.de(user_id, agent_id)
    if conexion is None:
        raise _error(409, "NO_CONECTADO", "Ese equipo no está conectado ahora: enciéndelo y vuelve a probar.")
    if "abrir_ajustes" not in conexion.capacidades:
        raise _error(409, "AGENTE_ANTIGUO", "Ese equipo tiene un agente anterior a la 4.17: actualízalo para "
                                            "poder abrir los ajustes desde aquí.")
    try:
        respuesta = enviar("abrir_ajustes", {}, plazo=15, equipo=agent_id)
    except ErrorDelCanal as exc:
        raise _error(409, "NO_CONECTADO", str(exc)) from exc
    resultado = respuesta.get("resultado") or {}
    if not resultado.get("success"):
        raise _error(502, "NO_SE_ABRIO", resultado.get("error") or "No se pudo abrir la ventana en el PC.")
    return {"success": True}


# --- Desde el agente, sin sesión ------------------------------------------------


class ConsultarRequest(BaseModel):
    codigo: str = Field(..., max_length=20)


class ConfirmarRequest(BaseModel):
    codigo: str = Field(..., max_length=20)
    nombre: str = Field(..., description="Cómo se llama este equipo: «Portátil del trabajo»")
    sistema: str | None = Field(None, max_length=200)
    agent_version: str | None = Field(None, max_length=60)
    protocol_version: int


def _traducir(exc: Exception) -> HTTPException:
    if isinstance(exc, DemasiadosFallos):
        return _error(429, "DEMASIADOS_INTENTOS", str(exc))
    if isinstance(exc, CodigoNoValido):
        return _error(400, "CODIGO_NO_VALIDO", str(exc))
    return _error(400, "AGENTE_NO_VALIDO", str(exc))


@router_agente.post("/emparejar/consultar", summary="Con qué cuenta se emparejaría este código")
def consultar(
    datos: ConsultarRequest, request: Request, container: CoreContainer = Depends(get_container),
) -> dict:
    """No gasta el código: es para que el agente enseñe la cuenta y pida confirmación."""
    try:
        cuenta = _servicio(container).consultar(datos.codigo, origen_de(request))
    except (CodigoNoValido, DemasiadosFallos) as exc:
        raise _traducir(exc) from exc
    return {"success": True, "cuenta": cuenta}


@router_agente.post("/emparejar/confirmar", summary="Canjear el código por la identidad del agente")
def confirmar(
    datos: ConfirmarRequest, request: Request, container: CoreContainer = Depends(get_container),
) -> dict:
    """Devuelve el `agent_id` y la credencial **esta única vez**."""
    try:
        emparejado = _servicio(container).confirmar(
            datos.codigo, origen_de(request), datos.nombre, datos.sistema,
            datos.agent_version, datos.protocol_version,
        )
    except (CodigoNoValido, DemasiadosFallos, AgenteNoValido) as exc:
        raise _traducir(exc) from exc
    container.audit_logger.registrar_evento(
        "emparejar_agente", actor=emparejado["user_id"], objetivo=emparejado["agent_id"],
        detalle=datos.nombre[:60],
    )
    return {"success": True, "agent_id": emparejado["agent_id"], "credencial": emparejado["credencial"]}


@router_agente.post("/desemparejar", summary="El agente se desempareja a sí mismo")
def desemparejar(request: Request, container: CoreContainer = Depends(get_container)) -> dict:
    """Con la credencial del agente en `Authorization: Bearer mga_…`, y con nada más."""
    cabecera = (request.headers.get("authorization") or "").strip()
    credencial = cabecera[7:].strip() if cabecera[:7].lower() == "bearer " else None
    servicio = _servicio(container)
    resuelto = servicio.resolver(credencial)
    if resuelto is None or not servicio.desemparejar(credencial):
        raise _error(401, "CREDENCIAL_NO_VALIDA", "Esa credencial de agente no vale.")
    container.audit_logger.registrar_evento(
        "desemparejar_agente", actor=resuelto["usuario"]["id"], objetivo=resuelto["agente"]["id"],
    )
    return {"success": True}


@router_agente.post("/rotar", summary="El agente pide una credencial nueva")
def rotar(request: Request, container: CoreContainer = Depends(get_container)) -> dict:
    """Con su credencial actual (3.8). La nueva queda **pendiente**: la vieja sigue valiendo
    hasta que el agente se conecte con la nueva, y entonces deja de valer. Así un corte a
    mitad no deja el PC sin credencial."""
    cabecera = (request.headers.get("authorization") or "").strip()
    credencial = cabecera[7:].strip() if cabecera[:7].lower() == "bearer " else None
    servicio = _servicio(container)
    resuelto = servicio.resolver(credencial)
    nueva = servicio.rotar(credencial)
    if nueva is None:
        raise _error(401, "CREDENCIAL_NO_VALIDA", "Esa credencial de agente no vale.")
    container.audit_logger.registrar_evento(
        "rotar_credencial_agente", actor=resuelto["usuario"]["id"], objetivo=resuelto["agente"]["id"],
    )
    return {"success": True, "credencial": nueva}


@router_agente.get("/historial", summary="El agente pide su propio historial de órdenes")
def historial_del_agente(request: Request, horas: int = 24,
                         container: CoreContainer = Depends(get_container)) -> dict:
    """Para `python -m src.agente cruzar` (3.7): lo que la nube apuntó de **este** PC, para
    compararlo con su diario. Con la credencial del agente, y solo de él."""
    cabecera = (request.headers.get("authorization") or "").strip()
    credencial = cabecera[7:].strip() if cabecera[:7].lower() == "bearer " else None
    servicio = _servicio(container)
    resuelto = servicio.resolver(credencial)
    if resuelto is None:
        raise _error(401, "CREDENCIAL_NO_VALIDA", "Esa credencial de agente no vale.")
    desde = time.time() - max(1, min(int(horas), 24 * 30)) * 3600
    return {"success": True, "ordenes": servicio.repo.ordenes_de_agente(
        resuelto["usuario"]["id"], resuelto["agente"]["id"], desde, HISTORIAL_MAXIMO)}


# --- Las actualizaciones (3.8) ----------------------------------------------------
#
# **La nube solo aloja**: el paquete, su manifiesto y la firma los hago yo en mi PC
# (`scripts/publicar_agente.py`) y llegan con el repositorio. Quien comprueba es el
# agente, con la clave pública que lleva dentro (`src/agente/firma.py`). Se piden con la
# credencial de un agente o con un código de emparejamiento vivo (el de un PC que se
# está instalando): el paquete no es público aunque no tenga secretos.

PUBLICADO = Path(__file__).resolve().parents[3] / "publicado" / "agente"


def _puede_bajar(request: Request, container: CoreContainer) -> None:
    servicio = _servicio(container)
    cabecera = (request.headers.get("authorization") or "").strip()
    credencial = cabecera[7:].strip() if cabecera[:7].lower() == "bearer " else None
    if credencial and servicio.resolver(credencial) is not None:
        return
    codigo = request.headers.get("x-morgan-codigo")
    if codigo:
        try:
            servicio.consultar(codigo, origen_de(request))
            return
        except (CodigoNoValido, DemasiadosFallos) as exc:
            raise _traducir(exc) from exc
    raise _error(401, "CREDENCIAL_NO_VALIDA", "Hace falta la credencial del agente o un código de emparejamiento.")


@router_agente.get("/instalar.ps1", summary="El instalador del agente para Windows")
def instalador() -> Response:
    """Público: no lleva secretos y no hace nada sin un código de emparejamiento vivo. El
    paquete que baja lo comprueba el propio agente con mi firma."""
    ruta = Path(__file__).resolve().parents[3] / "scripts" / "instalar-agente.ps1"
    # Tal cual, con su BOM: la línea de la web lo guarda en un fichero y PowerShell 5.1, sin
    # BOM, leería los acentos como ANSI.
    return Response(ruta.read_bytes(), media_type="text/plain; charset=utf-8")


@router_agente.get("/actualizacion", summary="La última versión publicada del agente, firmada")
def actualizacion(request: Request, container: CoreContainer = Depends(get_container)) -> dict:
    """El manifiesto **tal cual se firmó** (texto, no JSON reinterpretado: la firma es de
    esos bytes) y su firma. Sin nada publicado, `manifiesto: null`."""
    _puede_bajar(request, container)
    try:
        manifiesto = (PUBLICADO / "manifiesto.json").read_text(encoding="utf-8")
        firma = (PUBLICADO / "firma.txt").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return {"success": True, "manifiesto": None, "firma": None}
    return {"success": True, "manifiesto": manifiesto, "firma": firma}


@router_agente.get("/paquete", summary="El paquete del agente publicado")
def paquete(request: Request, container: CoreContainer = Depends(get_container)) -> Response:
    _puede_bajar(request, container)
    try:
        datos = (PUBLICADO / "paquete.zip").read_bytes()
    except FileNotFoundError:
        raise _error(404, "SIN_PAQUETE", "No hay ninguna versión del agente publicada.") from None
    return Response(datos, media_type="application/zip")


# --- El canal (3.0-D) ------------------------------------------------------------
#
# El agente abre un WebSocket y lo mantiene. **Ningún middleware llega aquí** (son
# solo HTTP; encontrado en la 3.0-B): la credencial se comprueba en esta función,
# antes de aceptar la conexión. Contrato: docs/agente-local.md §7-§8.

#: Cada cuánto late el canal, y cuántos latidos sin respuesta dan al agente por
#: muerto. Medido en mi PC: una suspensión deja la conexión medio abierta y
#: solo se nota así (docs/mediciones.md §6).
#:
#: **5 s y no 15**, medido en redespliegues reales de Render (3.0-D): al activarse la
#: instancia nueva, el proxy corta la conexión con la vieja **pero no avisa al
#: agente**: le deja la suya colgada, sin tráfico, hasta 30 s. Ni el cierre 1012 ni un
#: aviso al recibir la señal de apagado llegan, porque la vieja ya no tiene al agente
#: cuando la recibe. Lo único que acorta ese hueco es que el agente note antes el
#: silencio: con 5 s, en unos 10.
LATIDO = 5.0
LATIDOS_TOLERADOS = 3

#: Cada cuánto se vuelve a mirar en la base que el agente no fue revocado. **Desde la
#: 3.0.5 es la única comprobación periódica** (decisión mía): la de antes de cada
#: orden costaba ~60 ms, un tercio de la orden, medido en Render. Revocar, cambiar o
#: restablecer la contraseña y borrar la cuenta cierran la conexión al momento; esto
#: cubre lo que llegue por otra vía (otra instancia de la nube en un despliegue). Era 60.
REVALIDAR_CADA = 30.0

SALUDO_MAXIMO = 10.0


class MensajeMalFormado(ValueError):
    """Lo que llegó por el canal no es un objeto JSON."""


def _bearer(websocket: WebSocket) -> str | None:
    cabecera = (websocket.headers.get("authorization") or "").strip()
    return cabecera[7:].strip() if cabecera[:7].lower() == "bearer " else None


@router_agente.websocket("/canal")
async def canal(websocket: WebSocket):
    """El canal de un agente: saludo, latidos, órdenes y resultados."""
    from src.agente.protocolo import PROTOCOLO_ACTUAL, PROTOCOLO_MINIMO
    from src.canal import registro as reg

    container = get_container()
    servicio = _servicio(container)
    auditar = container.audit_logger.registrar_evento

    resuelto = await asyncio.to_thread(servicio.resolver, _bearer(websocket))
    if resuelto is None:
        # Antes de aceptar: sin credencial válida, ni una trama.
        await websocket.close(code=reg.CERRADO_CREDENCIAL)
        return
    agente, usuario = resuelto["agente"], resuelto["usuario"]
    user_id, agent_id = usuario["id"], agente["id"]

    await websocket.accept()

    async def cerrar(codigo: int, motivo: str) -> None:
        # **Primero un mensaje con el código, después el cierre.** Medido en Render:
        # su proxy no reenvía los códigos de cierre (ni el 4401 al revocar ni el 1012
        # al apagarse); el agente veía un corte sin código. Un mensaje normal sí pasa.
        try:
            await websocket.send_text(json.dumps({"tipo": "cierre", "codigo": codigo, "motivo": motivo}))
        except Exception:
            pass
        # El motivo de un cierre WebSocket cabe en 123 bytes.
        recortado = motivo.encode("utf-8")[:120].decode("utf-8", "ignore")
        await websocket.close(code=codigo, reason=recortado)

    try:
        saludo = json.loads(await asyncio.wait_for(websocket.receive_text(), SALUDO_MAXIMO))
        if saludo.get("tipo") != "saludo" or not isinstance(saludo.get("protocol_version"), int):
            raise ValueError("saludo")
    except Exception:
        await cerrar(reg.CERRADO_SALUDO, "El primer mensaje tiene que ser el saludo.")
        return

    protocolo = saludo["protocol_version"]
    if not PROTOCOLO_MINIMO <= protocolo <= PROTOCOLO_ACTUAL:
        auditar("canal_agente", actor=user_id, objetivo=agent_id, resultado="incompatible",
                detalle=f"protocolo {protocolo}")
        await cerrar(
            reg.CERRADO_INCOMPATIBLE,
            f"Protocolo {protocolo}; la nube acepta del {PROTOCOLO_MINIMO} al "
            f"{PROTOCOLO_ACTUAL}. Actualiza el agente.",
        )
        return

    # Hasta la 3.7, un segundo PC de la misma persona se rechazaba aquí con 4409 («ya
    # tienes X conectado»). Desde entonces conviven: cada orden va a un PC concreto.

    capacidades = frozenset(str(c) for c in (saludo.get("capacidades") or [])[:50])
    conexion = reg.Conexion(
        user_id=user_id, agent_id=agent_id, nombre=agente.get("nombre") or agent_id,
        capacidades=capacidades, enviar=websocket.send_text, cerrar=cerrar,
        loop=asyncio.get_running_loop(), protocolo=protocolo,
    )
    # Lo que la web enseña del equipo (versión, sistema, visto por última vez). Va ANTES de
    # registrar la conexión y no la tumba si falla: entre registrar y escuchar no puede
    # haber nada que espere. Antes iba en medio, fuera del `finally` que la da de baja: un
    # corte del agente o un fallo de Supabase ahí dejaban una conexión registrada que
    # nadie escuchaba, y sus órdenes colgadas hasta el plazo (visto en la 3.0.5).
    repo = servicio.repo
    try:
        await asyncio.to_thread(repo.tocar_agente, agent_id, {
            "last_seen": time.time(),
            "protocol_version": protocolo,
            "agent_version": str(saludo.get("agent_version") or "")[:30] or None,
            "sistema": str(saludo.get("sistema") or "")[:80] or None,
        })
    except Exception:
        logger.warning("No se pudo anotar la conexión del agente %s", agent_id, exc_info=True)
    # Y la bienvenida, ANTES de registrar. Registrada, ya se le pueden mandar órdenes, y
    # el agente espera la bienvenida como primer mensaje: una orden que se colara antes
    # le haría dar la conexión por mala. Encontrado en la 3.0.5 al quitar la consulta de
    # cada orden, que con sus ~60 ms tapaba el hueco: la orden que esperaba a que el
    # agente volviera tras un despliegue caía justo ahí.
    await websocket.send_text(json.dumps({
        "tipo": "bienvenida",
        "agent_id": agent_id,
        "compatibilidad": "CURRENT" if protocolo == PROTOCOLO_ACTUAL else "OUTDATED",
        "latido": LATIDO,
        "hora": time.time(),
        # 3.8: la credencial tiene más de 90 días. El agente pide una nueva (`/agente/rotar`)
        # y se reconecta con ella. Uno más viejo ignora el campo.
        "rotar_credencial": servicio.toca_rotar(resuelto),
    }))

    ultima_noticia = time.monotonic()
    motivo = "el agente cerró"

    async def escuchar():
        nonlocal ultima_noticia
        while True:
            texto = await websocket.receive_text()
            try:
                mensaje = json.loads(texto)
                if not isinstance(mensaje, dict):
                    raise ValueError("no es un objeto")
            except ValueError:
                # Un agente sano nunca manda esto. Antes rompía la escucha y la conexión
                # caía en silencio, sin `cierre`: el agente solo se enteraba a los ~10 s
                # por la falta de latidos (medido en la 3.0.5).
                raise MensajeMalFormado() from None
            ultima_noticia = time.monotonic()
            if mensaje.get("tipo") == "fragmento":
                # Un trozo de un archivo que la persona pidió copiar (3.1-E). Se juntan
                # aquí, no en el resultado: un archivo de 20 MB no cabe en un mensaje.
                from src.canal.despacho import recibir_fragmento

                recibir_fragmento(conexion, mensaje)
                continue
            if mensaje.get("tipo") in ("estado", "consulta"):
                # Protocolo 3 (3.4): cómo va una orden, y la respuesta a «¿qué pasó con
                # ella?» tras un corte. Solo cuentan los de órdenes que esta conexión
                # tiene en vuelo o por las que preguntó.
                from src.canal.despacho import recibir_consulta, recibir_estado

                (recibir_estado if mensaje["tipo"] == "estado" else recibir_consulta)(conexion, mensaje)
                continue
            if mensaje.get("tipo") != "resultado":
                continue  # pong u otra señal de vida
            futuro = conexion.esperando.get(str(mensaje.get("command_id")))
            if futuro is None:
                # Un resultado de una orden que la nube no le mandó (o ya vencida).
                # No se usa, y se anota: es el desajuste que el contrato manda
                # tratar como posible incidente (amenaza D).
                auditar("resultado_huerfano", actor=user_id, objetivo=agent_id,
                        resultado="ignorado", detalle=str(mensaje.get("command_id"))[:60])
                continue
            if not futuro.done():
                futuro.set_result(mensaje)

    escucha = asyncio.create_task(escuchar())
    revalidado = time.monotonic()
    try:
        # Registrada DENTRO del bloque cuyo `finally` la da de baja: pase lo que pase a
        # partir de aquí, no queda una conexión registrada que nadie escucha.
        await reg.REGISTRO.registrar(conexion)
        auditar("canal_agente", actor=user_id, objetivo=agent_id, resultado="conectado",
                detalle=",".join(sorted(capacidades))[:200])
        while conexion.viva:
            hecho, _ = await asyncio.wait({escucha}, timeout=LATIDO)
            if hecho:
                escucha.result()  # relanza la desconexión del agente
            if time.monotonic() - ultima_noticia > LATIDOS_TOLERADOS * LATIDO:
                motivo = "sin latidos"
                await reg.REGISTRO.quitar(conexion, reg.CERRADO_SIN_LATIDOS,
                                          "Tres latidos sin respuesta.")
                break
            if time.monotonic() - revalidado > REVALIDAR_CADA:
                revalidado = time.monotonic()
                if await asyncio.to_thread(servicio.resolver, _bearer(websocket)) is None:
                    motivo = "revocado"
                    await reg.REGISTRO.quitar(conexion, reg.CERRADO_CREDENCIAL,
                                              "Este equipo fue revocado.")
                    break
            if conexion.viva:
                # Con la hora de la nube (3.6): el agente mide con ella cuánto tardó en
                # llegarle una orden. Ver `Ejecutor.observar_hora_nube`.
                await websocket.send_text(json.dumps({"tipo": "latido", "hora": time.time()}))
    except WebSocketDisconnect as exc:
        motivo = f"el agente cerró ({exc.code})"
    except MensajeMalFormado:
        motivo = "mensaje mal formado"
        await reg.REGISTRO.quitar(conexion, reg.CERRADO_SALUDO,
                                  "Mensaje mal formado: el canal solo acepta objetos JSON.")
    except Exception as exc:
        motivo = f"error: {type(exc).__name__}"
    finally:
        escucha.cancel()
        await reg.REGISTRO.quitar(conexion)
        try:
            await asyncio.to_thread(repo.tocar_agente, agent_id, {"last_seen": time.time()})
        except Exception:
            pass
        if not conexion.viva and motivo == "el agente cerró":
            motivo = "cerrada por la nube"
        auditar("canal_agente", actor=user_id, objetivo=agent_id, resultado="desconectado",
                detalle=motivo)
