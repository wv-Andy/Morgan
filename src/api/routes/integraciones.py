"""
Rutas de servicios externos: conectar, consultar y desconectar (V1.9).

**Nada de esto es el login de Morgan.** Ver `src/integraciones/__init__.py`: son
dos sistemas distintos, y una integración cuelga siempre de una cuenta de Morgan
ya autenticada.

Una regla gobierna el diseño de estas rutas: **si algo no puede funcionar, se
dice, no se ofrece**. Cuando faltan las credenciales del servidor o la clave de
cifrado, `GET /integraciones` lo marca y la interfaz no pinta el botón de
conectar. Un botón que siempre da error es peor que su ausencia: hace perder el
tiempo y parece una avería de Morgan cuando es configuración que falta.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from src.api.dependencies import CoreContainer, get_container
from src.config import get_settings
from src.identidad import USUARIO_LOCAL, usuario_actual
from src.identidad.contexto import como_usuario
from src.integraciones.modelos import CATALOGO, ServicioExterno
from src.integraciones.oauth import ErrorDelServicio, ServicioNoConfigurado, cliente_de
from src.integraciones.repositorio import repositorio_de_integraciones
from src.integraciones.secretos import hay_clave

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integraciones", tags=["Integraciones"])


def _repo(container: CoreContainer):
    return repositorio_de_integraciones(container.repositories)


def _exigir_cuenta() -> str:
    """El usuario de esta petición, o 401.

    Una integración se guarda a nombre de alguien. Con el usuario implícito no
    habría a quién atribuirla, y en un Morgan compartido la conectaría una
    persona y la usarían todas.
    """
    user_id = usuario_actual()
    if user_id == USUARIO_LOCAL:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "SIN_SESION",
                "message": "Necesitas una cuenta para conectar servicios externos.",
            },
        )
    return user_id


def _url_publica_de_la_api() -> str:
    """Dónde está este backend visto desde fuera.

    `MORGAN_API_URL` manda. Si no está, se usa `RENDER_EXTERNAL_URL`, que Render
    define solo — así el despliegue habitual funciona sin una variable más que
    recordar. En local, el puerto de siempre.
    """
    import os

    for variable in ("MORGAN_API_URL", "RENDER_EXTERNAL_URL"):
        valor = (os.getenv(variable) or "").strip()
        if valor:
            return valor.rstrip("/")

    return f"http://127.0.0.1:{get_settings().api_port}"


def _url_de_la_web() -> str:
    """A dónde devolver al navegador. La misma variable que usan los correos."""
    import os

    return (os.getenv("MORGAN_WEB_URL") or "http://localhost:5173").rstrip("/")


def _callback_de(servicio: str) -> str:
    """La URL a la que el servicio devuelve al navegador.

    Apunta al **backend**, no a la web. El secreto de la aplicación vive aquí, y
    es aquí donde se canjea el código. Poner la de Vercel da un
    `redirect_uri_mismatch` que no explica gran cosa.

    Tiene que coincidir EXACTAMENTE con la que se registró en GitHub, incluido
    el esquema y sin barra final.
    """
    return f"{_url_publica_de_la_api()}/integraciones/{servicio}/callback"


@router.get("", summary="Servicios que puedes conectar, y su estado")
def listar(container: CoreContainer = Depends(get_container)) -> dict:
    """El estado real de cada servicio, sin adornos.

    Distingue cuatro situaciones que la interfaz necesita separar:

    - **No disponible**: falta algo para que esto pueda funcionar. No hay nada
      que pulsar, y se dice qué falta.
    - **Disponible y sin conectar**: se puede conectar.
    - **Conectado**: con el nombre de la cuenta y los permisos concedidos.
    - **Conectado y fallando**: hay integración, pero la última operación dio
      error. Es muy distinto de «no conectado» y hay que poder verlo.

    **Esta ruta no exige cuenta, y las de conectar y desconectar sí.** La
    diferencia es deliberada: sin cuenta no hay a quién atribuir una
    autorización —eso no cambia—, pero contestar 401 a la simple pregunta «¿qué
    servicios hay?» dejaba al panel sin poder explicarse. En el Morgan local,
    que no pide cuentas, la vista entera se pintaba como una avería en rojo.

    Es el mismo criterio que ya se aplicaba a las credenciales que le faltan al
    servidor, y que esta vista tiene escrito como norma: lo que no puede
    funcionar se enseña apagado y con su motivo, no como un error. Un fallo
    parece culpa de Morgan; un motivo se puede leer y resolver.
    """
    user_id = usuario_actual()
    sin_cuenta = user_id == USUARIO_LOCAL
    conectadas = (
        {} if sin_cuenta else {i.servicio: i for i in _repo(container).listar()}
    )

    servicios = []
    for definicion in CATALOGO.values():
        nombre = definicion.servicio.value
        faltan = [v for v in definicion.variables if not _hay_variable(v)]

        # Sin clave no se pueden guardar credenciales cifradas, y guardarlas en
        # claro no es una alternativa: seria un token de acceso a repositorios
        # privados legible por cualquiera con acceso a la base.
        if not hay_clave():
            faltan.append("MORGAN_SECRET_KEY")

        # Se listan TODAS las que faltan, no la primera.
        #
        # Informar de una en una obliga a quien administra a arreglar, redesplegar,
        # volver a mirar y descubrir que falta otra. Con tres variables eso son
        # tres vueltas de un ciclo que en Render tarda minutos.
        motivo = None
        if faltan:
            motivo = (
                f"Este Morgan no puede conectar {definicion.nombre} todavía. "
                "Quien lo administra debe añadir en el servidor: "
                + ", ".join(faltan)
                + "."
            )

        # Sin cuenta manda este motivo y no el de las variables: es el que la
        # persona que mira puede resolver. Decirle que el servidor necesita tres
        # variables, cuando el problema es que no hay a quién atribuir la
        # autorizacion, la manda a arreglar lo que no toca.
        if sin_cuenta:
            motivo = (
                f"Para conectar {definicion.nombre} hace falta una cuenta de "
                "Morgan. Este Morgan no pide cuentas, así que no habría a quién "
                "atribuir la autorización: una la conectaría una persona y la "
                "usarían todas."
            )

        integracion = conectadas.get(nombre)
        servicios.append({
            "servicio": nombre,
            "nombre": definicion.nombre,
            "descripcion": definicion.descripcion,
            "permite": list(definicion.permite),
            "disponible": motivo is None,
            "motivo_no_disponible": motivo,
            **(integracion.to_dict() if integracion else {"conectado": False}),
        })

    return {"success": True, "usuario": user_id, "servicios": servicios}


def _hay_variable(nombre: str) -> bool:
    import os

    return bool((os.getenv(nombre) or "").strip())


@router.post("/{servicio}/conectar", summary="Empezar a conectar un servicio")
def conectar(servicio: str, container: CoreContainer = Depends(get_container)) -> dict:
    """Emite el estado y devuelve a dónde ir.

    **Devuelve la URL en lugar de redirigir.** Esta petición la hace `fetch`
    desde la web, y una redirección la seguiría el propio `fetch`: el navegador
    nunca llegaría a la pantalla de GitHub. Redirigir es cosa del cliente.
    """
    _exigir_cuenta()
    definicion = _definicion(servicio)

    if not hay_clave():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SIN_CIFRADO",
                "message": "Falta MORGAN_SECRET_KEY: no se pueden guardar "
                           "credenciales de forma segura.",
            },
        )

    try:
        estado = _repo(container).crear_estado(definicion.servicio.value)
        # Un cliente por servicio desde la 2.0.17: antes esto llamaba al de GitHub
        # por su nombre, y añadir Google Calendar habría sido copiar la ruta.
        url = cliente_de(definicion.servicio.value).url_de_autorizacion(
            estado, definicion.scopes, _callback_de(definicion.servicio.value)
        )
    except ServicioNoConfigurado as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "NO_CONFIGURADO", "message": str(exc)},
        ) from exc

    return {"success": True, "url": url}


@router.get("/{servicio}/callback", summary="Vuelta del servicio externo")
def callback(
    servicio: str,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    container: CoreContainer = Depends(get_container),
) -> RedirectResponse:
    """Donde GitHub devuelve al navegador tras autorizar.

    **De quién es esta autorización lo dice el `state`, no la cookie.** La vuelta
    es una navegación del navegador; fiarse de la cookie sería preguntarle al
    mismo canal que se está intentando verificar. El `state` se emitió con una
    sesión válida, se guardó con su `user_id`, y se consume aquí.

    Sin esa comprobación, una web ajena podría completar el flujo con SU código
    y dejar su cuenta de GitHub conectada a la sesión de otra persona: Morgan
    actuaría después sobre repositorios ajenos creyendo que son los tuyos.

    Siempre se acaba **redirigiendo a la web**, con o sin éxito. Dejar al usuario
    mirando un JSON en el dominio de la API es abandonarlo a mitad de camino.
    """
    definicion = _definicion(servicio)
    nombre = definicion.servicio.value
    repo = _repo(container)

    if error:
        # La persona pulsó «cancelar» en GitHub. No es un fallo.
        return _volver_a_la_web(nombre, "cancelado")

    if not code or not state:
        return _volver_a_la_web(nombre, "invalido")

    user_id = repo.consumir_estado(state, nombre)
    if user_id is None:
        logger.warning("Callback de %s con un estado que no vale", nombre)
        return _volver_a_la_web(nombre, "estado_invalido")

    # A partir de aqui se actua COMO EL USUARIO QUE PIDIO CONECTAR, que puede no
    # ser el de la cookie de esta peticion. Es lo que hace que la integracion se
    # guarde a nombre de quien corresponde.
    with como_usuario(user_id):
        cliente = cliente_de(nombre)
        try:
            credenciales = cliente.canjear(code, _callback_de(nombre))
            cuenta = cliente.cuenta_de(credenciales.token)
        except (ServicioNoConfigurado, ErrorDelServicio) as exc:
            logger.warning("No se pudo completar la conexión con %s: %s", nombre, exc)
            return _volver_a_la_web(nombre, "fallo")

        from src.tools.github import olvidar_conexiones

        olvidar_conexiones()        # sus herramientas pasan a ofrecerse ya (4.1.5)
        repo.guardar(
            nombre, credenciales.token,
            cuenta=cuenta.get("login"),
            scopes=credenciales.scopes or definicion.scopes,
            # El de renovación y la caducidad, en los servicios cuyo token caduca
            # (Google, a la hora). En GitHub los dos son `None`.
            refresco=credenciales.refresco,
            expira_en=credenciales.expira_en,
            metadatos={"nombre": cuenta.get("nombre"), "avatar": cuenta.get("avatar")},
        )

        container.audit_logger.log(
            tool_name="conectar_integracion",
            risk_level="moderate",
            authorized=True,
            # El token NO se registra, ni enmascarado: un log de auditoria se
            # comparte para investigar, y ahi no pinta nada una credencial.
            args={"servicio": nombre, "cuenta": cuenta.get("login")},
            success=True,
            error=None,
        )

    return _volver_a_la_web(nombre, "conectado")


@router.delete("/{servicio}", summary="Desconectar un servicio")
def desconectar(servicio: str, container: CoreContainer = Depends(get_container)) -> dict:
    """Desconecta, y le pide al servicio que invalide el token.

    **El borrado local ocurre pase lo que pase.** Avisar a GitHub es lo que hace
    que el token deje de valer de verdad, pero si esa llamada falla, quedarse
    conectado sería el peor resultado: la persona ha dicho que no quiere seguir
    conectada.

    La respuesta dice si la revocación remota se confirmó, para poder avisar de
    que conviene revisarlo en GitHub.
    """
    _exigir_cuenta()
    definicion = _definicion(servicio)
    nombre = definicion.servicio.value
    repo = _repo(container)

    revocado = False
    try:
        credenciales = repo.credenciales_de(nombre)
        if credenciales:
            token, refresco, _ = credenciales
            # Con el de renovación, cuando lo hay: revocarlo invalida la
            # autorización entera, no solo el token de la hora en curso.
            revocado = cliente_de(nombre).revocar(refresco or token)
    except Exception:
        # Incluye el caso de que la clave de cifrado haya cambiado y el token no
        # se pueda descifrar. Aun asi hay que poder desconectar: si no, la fila
        # quedaria inmortal.
        logger.warning("No se pudo revocar el token de %s", nombre, exc_info=True)

    borrado = repo.eliminar(nombre)
    from src.tools.github import olvidar_conexiones

    olvidar_conexiones()

    container.audit_logger.log(
        tool_name="desconectar_integracion",
        risk_level="moderate",
        authorized=True,
        args={"servicio": nombre, "revocado_en_origen": revocado},
        success=borrado,
        error=None,
    )

    return {"success": True, "desconectado": borrado, "revocado_en_origen": revocado}


def _definicion(servicio: str):
    try:
        return CATALOGO[ServicioExterno(servicio)]
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SERVICIO_DESCONOCIDO",
                "message": f"Morgan no sabe conectar con '{servicio}'.",
            },
        ) from exc


def _volver_a_la_web(servicio: str, resultado: str) -> RedirectResponse:
    """Devuelve el navegador a la web, diciendo cómo fue.

    El resultado viaja en la URL porque es lo único que sobrevive a una
    redirección entre dominios. La web lo lee, enseña el aviso y lo limpia de la
    barra de direcciones.
    """
    base = _url_de_la_web()
    return RedirectResponse(
        url=f"{base}/?integracion={servicio}&resultado={resultado}",
        # 302 y no 307: la vuelta de OAuth es un GET y debe seguir siéndolo.
        status_code=status.HTTP_302_FOUND,
    )
