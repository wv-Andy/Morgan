"""
Rutas de cuentas: registro, inicio de sesión, recuperación y perfil (identidad, V2.0 adelantada).

Todas viven bajo `/auth`. Las de entrada —registrarse, iniciar sesión, pedir un
enlace de recuperación— son públicas por necesidad: exigir sesión para poder
iniciar sesión no tendría sentido. Las demás requieren estar dentro.

El cuidado aquí está en **qué se responde cuando algo va mal**. Un formulario de
acceso que distingue «esa cuenta no existe» de «la contraseña es incorrecta» es
una herramienta para averiguar quién está registrado, y lo mismo vale para el de
recuperación. Por eso varias respuestas de este módulo son deliberadamente vagas,
y esa vaguedad es la funcionalidad, no un descuido.
"""

import logging
import time

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from src.api.dependencies import CoreContainer, get_container
from src.config import get_settings
from src.api.sesion_web import (
    COOKIE_CSRF,
    COOKIE_SESION,
    origen_de,
    poner_cookies,
    quitar_cookies,
    renovar_csrf,
)
from src.identidad import USUARIO_LOCAL, usuario_actual
from src.identidad.cuentas import (
    CuentaDuplicada,
    CuentaProtegida,
    DemasiadosIntentos,
    DemasiadosRegistros,
    ErrorDeAutenticacion,
    ServicioDeCuentas,
)
from src.identidad.password import IdentificadorInvalido, PasswordInvalida
from src.identidad.repositorio import repositorio_de_cuentas
from src.identidad.tokens import token_actual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Cuentas"])


def get_servicio(container: CoreContainer = Depends(get_container)) -> ServicioDeCuentas:
    return ServicioDeCuentas(repositorio_de_cuentas(container.repositories))


# --- Cuerpos de petición ------------------------------------------------------


class RegistroRequest(BaseModel):
    username: str = Field(..., description="Nombre de usuario, 3-32 caracteres")
    email: str = Field(..., description="Correo electrónico")
    password: str = Field(..., description="Contraseña, mínimo 8 caracteres")
    display_name: str | None = Field(None, description="Nombre visible, opcional")


class LoginRequest(BaseModel):
    identificador: str = Field(..., description="Nombre de usuario o correo")
    password: str


class RecuperarRequest(BaseModel):
    email: str


class RestablecerRequest(BaseModel):
    token: str
    password: str


class CambiarPasswordRequest(BaseModel):
    actual: str
    nueva: str


class UsuarioResponse(BaseModel):
    success: bool = True
    usuario: dict
    csrf: str | None = None


# --- Traducción de errores ----------------------------------------------------


def _400(exc: Exception) -> HTTPException:
    """Un dato que el usuario puede corregir. El mensaje se le enseña tal cual."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "DATOS_INVALIDOS", "message": str(exc)},
    )


def _401(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "CREDENCIALES", "message": str(exc)},
    )


# --- Registro -----------------------------------------------------------------


@router.post("/registro", summary="Crear una cuenta")
def registrar(
    datos: RegistroRequest,
    request: Request,
    response: Response,
    tareas: BackgroundTasks,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> UsuarioResponse:
    """Crea la cuenta y deja la sesión iniciada.

    Se entra directamente porque el correo aún no está verificado y pedirlo antes
    de dejar probar nada es la forma más rápida de perder a alguien. La
    verificación llegará como un aviso dentro de la aplicación, no como una puerta.
    """
    if not get_settings().registro_abierto:
        # 403 y no 404: la ruta existe, y decir "aqui no hay nada" mandaria a
        # quien la use a buscar un fallo que no existe.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "REGISTRO_CERRADO",
                "message": "Este Morgan no admite cuentas nuevas. "
                           "Pídele acceso a quien lo administra.",
            },
        )

    try:
        usuario = servicio.registrar(
            datos.username, datos.email, datos.password, datos.display_name,
            origen=origen_de(request),
        )
    except DemasiadosRegistros as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "DEMASIADOS_REGISTROS", "message": str(exc)},
        ) from exc
    except (IdentificadorInvalido, PasswordInvalida, CuentaDuplicada) as exc:
        raise _400(exc) from exc

    # Sin volver a comprobar la contraseña que se acaba de guardar: ver
    # `abrir_sesion_tras_el_alta` (un hash y cinco consultas menos por alta).
    token = servicio.abrir_sesion_tras_el_alta(usuario, request.headers.get("user-agent"))
    csrf = poner_cookies(response, token)

    # En segundo plano, por lo mismo que el de recuperación: hablar con el
    # proveedor de correo puede pasar de quince segundos, y nadie debería
    # esperar a eso para entrar en su cuenta recién creada.
    #
    # Que el correo falle NO impide registrarse. Verificar no es una puerta:
    # la cuenta funciona desde el primer momento y el aviso aparece dentro.
    _mandar_verificacion(tareas, servicio, usuario.id)

    return UsuarioResponse(usuario=usuario.to_dict(), csrf=csrf)


# --- Sesión -------------------------------------------------------------------


@router.post("/login", summary="Iniciar sesión")
def login(
    datos: LoginRequest,
    request: Request,
    response: Response,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> UsuarioResponse:
    try:
        usuario, token = servicio.iniciar_sesion(
            datos.identificador,
            datos.password,
            origen=origen_de(request),
            user_agent=request.headers.get("user-agent"),
        )
    except DemasiadosIntentos as exc:
        # 429 y no 401: el cliente debe poder distinguir "espera" de "prueba otra
        # contrasena", que es justo lo contrario de lo que se hace con el 401.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "DEMASIADOS_INTENTOS", "message": str(exc)},
        ) from exc
    except ErrorDeAutenticacion as exc:
        raise _401(exc) from exc

    csrf = poner_cookies(response, token)
    return UsuarioResponse(usuario=usuario.to_dict(), csrf=csrf)


@router.post("/logout", summary="Cerrar sesión")
def logout(
    request: Request,
    response: Response,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> dict:
    """Cierra la sesión y borra las cookies.

    Se invalida en el servidor, no solo en el navegador: borrar la cookie sin más
    dejaría la sesión viva treinta días para quien tuviera copia del token.
    """
    servicio.cerrar_sesion(request.cookies.get(COOKIE_SESION) or "")
    quitar_cookies(response)
    return {"success": True}


def _csrf_para_el_cliente(
    request: Request, response: Response, hay_sesion: bool
) -> str:
    """El token CSRF que la interfaz debe repetir en sus peticiones.

    **Por qué viaja aquí y no basta la cookie.** La cookie CSRF la pone el
    dominio de la API y la interfaz vive en otro —Render y Vercel—. El
    navegador la *envía* en cada petición, pero `document.cookie` **no puede
    leerla** desde otro dominio. El cliente no tenía con qué rellenar la
    cabecera, así que todos los POST se rechazaban con 403 y la web quedaba
    en solo lectura: ni enviar un mensaje, ni crear una conversación, ni
    subir un archivo. Se veía como «Morgan cree que no he iniciado sesión».

    **Por qué esto no debilita la defensa.** El doble envío protege porque
    una web ajena no puede *conseguir* el token. Sigue sin poder: esta ruta
    solo responde a los orígenes de la lista de CORS, y sin estar en ella el
    navegador le impide leer la respuesta. Es la misma política de mismo
    origen que hacía ilegible la cookie, aplicada un escalón más arriba.
    """
    actual = request.cookies.get(COOKIE_CSRF) or ""
    if actual:
        return actual

    # Sin sesión no hay nada que proteger todavía: `csrf_valido` deja pasar
    # las peticiones anónimas, y emitir un token aquí solo serviría para
    # dejar cookies a quien únicamente ha abierto la página.
    return renovar_csrf(response) if hay_sesion else ""


@router.get("/yo", summary="Quién soy")
def yo(
    request: Request,
    response: Response,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> dict:
    """El usuario de esta petición, para que la interfaz sepa qué enseñar.

    Nunca da 401: sin sesión responde `autenticado: false`, que es una respuesta
    normal. Un 401 aquí obligaría a la interfaz a tratar como error el caso más
    corriente de todos —abrir la web sin haber entrado— y llenaría la consola de
    fallos que no lo son.

    **`local` describe el DESPLIEGUE, no a quien pregunta.** Significa «este
    Morgan no pide cuentas a nadie», y eso lo decide `MORGAN_REQUIRE_AUTH`.

    Deducirlo del usuario de la peticion era un error, y uno que dejaba la web
    SIN FORMA DE ENTRAR: esta ruta es publica, asi que el middleware la deja pasar
    sin sesion con el usuario implicito puesto, y en la nube respondia
    `local: true` a quien todavia no habia entrado. La interfaz lo leia como «aqui
    no hacen falta cuentas» y enseñaba Morgan en lugar del formulario de acceso.
    """
    local = not get_settings().require_auth
    user_id = usuario_actual()
    usuario = None if user_id == USUARIO_LOCAL else servicio.obtener(user_id)

    return {
        "success": True,
        "autenticado": usuario is not None,
        "local": local,
        "usuario": usuario.to_dict() if usuario else None,
        # Con un token de API no hay CSRF que dar: no hay cookie que proteger.
        "csrf": _csrf_para_el_cliente(
            request, response, usuario is not None and token_actual() is None
        ),
    }


@router.get("/sesiones", summary="Mis sesiones abiertas")
def sesiones(servicio: ServicioDeCuentas = Depends(get_servicio)) -> dict:
    """Dónde hay sesión abierta, para poder revisarlo y cerrarlo."""
    user_id = _exigir_cuenta()
    return {"success": True, "sesiones": servicio.sesiones_activas(user_id)}


@router.post("/sesiones/cerrar-otras", summary="Cerrar el resto de sesiones")
def cerrar_otras(
    request: Request,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> dict:
    _exigir_cuenta()
    cerradas = servicio.cerrar_todas(
        usuario_actual(), excepto=request.cookies.get(COOKIE_SESION)
    )
    return {"success": True, "cerradas": cerradas}


# --- Contraseña ---------------------------------------------------------------


@router.post("/password", summary="Cambiar la contraseña")
def cambiar_password(
    datos: CambiarPasswordRequest,
    request: Request,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> dict:
    user_id = _exigir_cuenta()

    try:
        servicio.cambiar_password(user_id, datos.actual, datos.nueva)
    except PasswordInvalida as exc:
        raise _400(exc) from exc
    except ErrorDeAutenticacion as exc:
        raise _401(exc) from exc

    # Las demas sesiones caen: si alguien te habia robado la contrasena,
    # cambiarla no sirve de nada mientras su sesion siga viva. La actual se
    # respeta para no echar de la aplicacion a quien acaba de cambiarla.
    otras = servicio.cerrar_todas(user_id, excepto=request.cookies.get(COOKIE_SESION))
    # Los agentes quedaron revocados: si hay uno conectado, se le cierra ya (3.0-D).
    from src.canal.registro import CERRADO_CREDENCIAL, REGISTRO

    REGISTRO.cerrar_desde_hilo(user_id, CERRADO_CREDENCIAL, "La contraseña de la cuenta cambió.")

    return {"success": True, "sesiones_cerradas": otras}


@router.post("/recuperar", summary="Pedir un enlace de recuperación")
def recuperar(
    datos: RecuperarRequest,
    tareas: BackgroundTasks,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> dict:
    """Envía el enlace, si esa dirección tiene cuenta.

    **La respuesta es idéntica exista o no la cuenta**, y eso es a propósito: si
    cambiara, cualquiera podría comprobar aquí quién está registrado. El token
    solo llega al correo; no aparece en esta respuesta.

    **El correo se manda en segundo plano.** Hablar con un servidor SMTP —
    conectar, negociar TLS, autenticar, enviar— puede pasar de quince segundos, y
    con el servicio recién despertado, mas. Esperandolo, la peticion se alargaba
    tanto que el navegador se cansaba antes y la persona veia un error habiendose
    enviado el correo.

    Y no hay nada que esperar: esta respuesta ya es la misma pase lo que pase con
    el envio, precisamente para no delatar si la cuenta existe.
    """
    token = servicio.solicitar_recuperacion(datos.email)

    if token:
        tareas.add_task(_enviar_en_segundo_plano, datos.email, token)

    return {
        "success": True,
        "message": "Si esa dirección tiene una cuenta, recibirás un correo con "
                   "las instrucciones en unos minutos.",
    }


def _enviar_en_segundo_plano(email: str, token: str) -> None:
    """Manda el correo cuando la respuesta ya ha salido.

    Se traga cualquier fallo: aqui ya no hay nadie esperando, y una excepcion en
    una tarea de fondo solo serviria para ensuciar el log con una traza sin
    contexto. Lo que importa —que no se pudo enviar— se registra explicitamente.
    """
    from src.identidad.correo import enviar_recuperacion

    try:
        if not enviar_recuperacion(email, token):
            logger.warning(
                "No se envió el correo de recuperación: falta configuración SMTP. "
                "Quien lo pidió no recibirá nada."
            )
    except Exception:
        # Nunca se registra el token ni el enlace: el log no debe contener nada
        # que sirva para entrar en una cuenta.
        logger.exception("Falló el envío del correo de recuperación")


@router.post("/restablecer", summary="Restablecer con el enlace recibido")
def restablecer(
    datos: RestablecerRequest,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> dict:
    try:
        user_id = servicio.restablecer_password(datos.token, datos.password)
    except PasswordInvalida as exc:
        raise _400(exc) from exc
    except ErrorDeAutenticacion as exc:
        raise _401(exc) from exc

    # Sus agentes quedaron revocados: el que esté conectado cae ya. Encontrado en la
    # 3.0.5: se revocaba en la base pero la conexión seguía abierta hasta la siguiente
    # orden o la revalidación (60 s), y es justo el camino de «me robaron la cuenta».
    from src.canal.registro import CERRADO_CREDENCIAL, REGISTRO

    REGISTRO.cerrar_desde_hilo(user_id, CERRADO_CREDENCIAL, "La contraseña de la cuenta se restableció.")

    # No se inicia sesion automaticamente: quien restablece puede estar en un
    # equipo prestado, y la contrasena nueva deberia probarse una vez.
    return {"success": True, "message": "Contraseña cambiada. Ya puedes entrar."}


# --- Utilidad -----------------------------------------------------------------


def _exigir_cuenta() -> str:
    """El identificador del usuario, o 401 si no hay cuenta detrás.

    El usuario local no cuenta como cuenta: no tiene contraseña ni correo, así
    que cambiar «su» contraseña o listar «sus» sesiones no significa nada.
    """
    user_id = usuario_actual()

    if user_id == USUARIO_LOCAL:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "SIN_CUENTA",
                "message": "Esta acción necesita una cuenta de Morgan.",
            },
        )

    return user_id


class EliminarCuentaRequest(BaseModel):
    password: str = Field(..., min_length=1, description="Tu contraseña, para confirmar")


@router.delete("/cuenta", summary="Eliminar mi cuenta y todos mis datos")
def eliminar_cuenta(
    datos: EliminarCuentaRequest,
    response: Response,
    servicio: ServicioDeCuentas = Depends(get_servicio),
    container: CoreContainer = Depends(get_container),
) -> dict:
    """Borra la cuenta de quien la pide, con sus conversaciones y sus archivos.

    **El orden importa y es este: primero los datos, después la cuenta.**

    Al revés —borrar la cuenta y luego los datos— deja una ventana en la que
    existen conversaciones, recuerdos y archivos cuyo `user_id` ya no
    corresponde a nadie. Si esa segunda mitad falla, esas filas se quedan ahí
    para siempre, invisibles pero presentes, y un identificador reutilizado las
    haría reaparecer en la cuenta de otra persona. Con este orden, un fallo a
    medias deja una cuenta viva y vacía: recuperable, y de nadie más.

    Los repositorios ya filtran por el usuario de la petición, así que esto solo
    puede borrar lo propio. No se le pasa ningún identificador a propósito: si
    lo aceptara, sería una ruta para borrar la cuenta de otro.

    **Lo que no se borra:** la auditoría. Registra qué hizo Morgan, no quién era
    esa persona, y es lo que permite investigar un incidente después. Borrarla a
    petición del titular convertiría el borrado de cuenta en una forma de tapar
    un rastro.
    """
    user_id = _exigir_cuenta()

    try:
        # Se comprueba la contraseña ANTES de tocar un solo dato. Si se
        # comprobara al final, una contraseña equivocada dejaría al usuario con
        # todo borrado y la cuenta intacta.
        servicio.comprobar_para_eliminar(user_id, datos.password)
    except CuentaProtegida as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CUENTA_PROTEGIDA", "message": str(exc)},
        ) from exc
    except ErrorDeAutenticacion as exc:
        raise _401(exc) from exc

    borrados = _borrar_mis_datos(container)

    servicio.eliminar_cuenta(user_id, datos.password)
    quitar_cookies(response)
    # Sin cuenta no hay agente: el conectado cae ya, no en la próxima revalidación.
    from src.canal.registro import CERRADO_CREDENCIAL, REGISTRO

    REGISTRO.cerrar_desde_hilo(user_id, CERRADO_CREDENCIAL, "La cuenta se eliminó.")

    container.audit_logger.log(
        tool_name="delete_account",
        risk_level="critical",
        authorized=True,
        args={"datos_borrados": borrados},
        success=True,
        error=None,
    )

    return {"success": True, "borrado": borrados}


def _borrar_mis_datos(container: CoreContainer) -> dict:
    """Borra lo que la cuenta ha generado. Solo lo suyo: los repositorios filtran.

    Cada bloque va en su propio `try`. Un fallo al borrar los archivos no puede
    impedir que se borren las conversaciones: dejar la mitad de los datos por un
    problema en la otra mitad es el peor resultado posible de esta operación.
    """
    repos = container.repositories
    cuenta: dict[str, int] = {}

    try:
        sesiones = repos.sessions.list(limit=1000, archived=None)
        for sesion in sesiones:
            repos.sessions.delete(sesion.id)
        cuenta["conversaciones"] = len(sesiones)
    except Exception:
        logger.warning("No se pudieron borrar las conversaciones", exc_info=True)

    try:
        cuenta["recuerdos"] = repos.memories.clear()
    except Exception:
        logger.warning("No se pudieron borrar los recuerdos", exc_info=True)

    try:
        archivos = repos.uploads.list(limit=1000)
        for archivo in archivos:
            # Por el store y no por el repositorio: hay que borrar tambien los
            # bytes, no solo la fila del indice.
            container.uploads.eliminar(archivo.id)
        cuenta["archivos"] = len(archivos)
    except Exception:
        logger.warning("No se pudieron borrar los archivos", exc_info=True)

    try:
        tareas = repos.tasks.list(limit=1000)
        for tarea in tareas:
            repos.tasks.delete(tarea.id)
        cuenta["tareas"] = len(tareas)
    except Exception:
        logger.warning("No se pudieron borrar las tareas", exc_info=True)

    try:
        planes = repos.planes.list(limit=1000)
        for plan in planes:
            repos.planes.delete(plan.id)
        cuenta["planes"] = len(planes)
    except Exception:
        logger.warning("No se pudieron borrar los planes", exc_info=True)

    return cuenta


def _mandar_verificacion(
    tareas: BackgroundTasks, servicio: ServicioDeCuentas, user_id: str
) -> bool:
    """Emite el token y encola el correo. Devuelve si había algo que enviar.

    `False` cuando la cuenta ya está verificada o no tiene correo: no es un
    fallo, es que no hay nada que hacer.
    """
    from src.identidad.correo import enviar_verificacion

    emitido = servicio.solicitar_verificacion(user_id)
    if emitido is None:
        return False

    token, destinatario = emitido
    tareas.add_task(_enviar_sin_romper, enviar_verificacion, destinatario, token)
    return True


def _enviar_sin_romper(funcion, destinatario: str, token: str) -> None:
    """Envuelve el envío para que un fallo no reviente la tarea de fondo.

    Sin esto, un proveedor caído deja una excepción sin capturar en el hilo de
    fondo: no rompe la respuesta —ya salió— pero llena el log de rastros que
    parecen un error de Morgan cuando es un servicio ajeno que no responde.
    """
    try:
        funcion(destinatario, token)
    except Exception:
        logger.warning("No se pudo enviar el correo", exc_info=True)


class VerificarRequest(BaseModel):
    token: str = Field(..., min_length=1)


@router.post("/verificar", summary="Confirmar el correo con el enlace recibido")
def verificar(
    datos: VerificarRequest,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> dict:
    """Marca el correo como verificado.

    **Pública a propósito**, como el resto de rutas de entrada: quien abre el
    enlace puede estar en otro navegador, o sin sesión. Exigirla convertiría un
    clic en un correo en «primero inicia sesión», que es justo la fricción que
    esta función existe para evitar.

    No se dice de quién es la cuenta ni si el token pertenecía a alguien: solo
    si valía. Un enlace caducado y uno inventado responden lo mismo.
    """
    if not servicio.verificar_email(datos.token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "TOKEN_INVALIDO",
                "message": "Ese enlace no vale o ya caducó. "
                           "Pide uno nuevo desde Ajustes.",
            },
        )

    return {"success": True, "message": "Correo confirmado."}


@router.post("/verificar/reenviar", summary="Reenviar el correo de confirmación")
def reenviar_verificacion(
    tareas: BackgroundTasks,
    servicio: ServicioDeCuentas = Depends(get_servicio),
) -> dict:
    """Vuelve a mandar el enlace a quien lo pide.

    Esta **sí** exige sesión, al revés que la de confirmar: aquí se manda un
    correo, y una ruta abierta que dispara correos a partir de una dirección es
    una herramienta para molestar a terceros. Con sesión, solo puedes pedirlo
    para tu propia cuenta.
    """
    user_id = _exigir_cuenta()

    if not _mandar_verificacion(tareas, servicio, user_id):
        return {"success": True, "enviado": False, "message": "Tu correo ya está confirmado."}

    return {
        "success": True,
        "enviado": True,
        "message": "Te hemos enviado un correo. Revisa también la carpeta de spam.",
    }


@router.get("/datos", summary="Descargar todo lo que Morgan guarda de ti")
def exportar_datos(
    servicio: ServicioDeCuentas = Depends(get_servicio),
    container: CoreContainer = Depends(get_container),
) -> dict:
    """Todo lo que esta cuenta ha generado, en un solo JSON.

    **Es el complemento de poder borrar la cuenta**, y las dos juntas son lo que
    convierte «tus datos» en algo real: si solo se puede borrar, la única forma
    de conservar una conversación es copiarla a mano; si solo se puede exportar,
    irse significa dejarlo todo ahí.

    **Se exporta lo propio y nada más.** No se acepta ningún identificador: los
    repositorios ya filtran por el usuario de la petición, así que esta ruta no
    puede devolver lo de otro aunque alguien lo intente.

    **Lo que NO va dentro**, y por qué:

    - *El hash de la contraseña.* No es un dato tuyo que sirva de algo; es una
      credencial. Publicarlo en un fichero que acaba en la carpeta de descargas
      es regalar material para atacarla sin prisa.
    - *Los tokens de servicios conectados.* Lo mismo, y peor: abren cuentas
      ajenas a Morgan. Sí se dice **qué** tienes conectado y con qué cuenta.
    - *Los identificadores de sesión.* Cada uno es una llave viva.

    El formato es JSON y no algo más bonito a propósito: lo que hace útil una
    exportación es que otro programa pueda leerla.
    """
    user_id = _exigir_cuenta()
    usuario = servicio.obtener(user_id)
    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SIN_CUENTA", "message": "No se encontró la cuenta."},
        )

    repos = container.repositories
    datos: dict = {
        "exportado_en": time.time(),
        "formato": 1,
        "cuenta": usuario.to_dict(),
    }

    # Cada bloque va aparte: un fallo al leer una cosa no puede dejar sin
    # exportar el resto. Se marca lo que no se pudo leer en lugar de callarlo,
    # porque una exportación incompleta que parece completa es peor que ninguna.
    fallos: list[str] = []

    def _bloque(nombre: str, leer):
        try:
            datos[nombre] = leer()
        except Exception:
            logger.warning("No se pudo exportar %s", nombre, exc_info=True)
            fallos.append(nombre)

    def _conversaciones():
        salida = []
        for sesion in repos.sessions.list(limit=1000, archived=None):
            salida.append({
                **sesion.to_dict(),
                "mensajes": [
                    m.to_dict()
                    for m in repos.messages.list_for_session(sesion.id, limit=10_000)
                ],
            })
        return salida

    _bloque("conversaciones", _conversaciones)
    _bloque("recuerdos", lambda: [r.to_dict() for r in repos.memories.search(limit=10_000)])
    _bloque("archivos", lambda: [a.to_dict() for a in repos.uploads.list(limit=1000)])
    _bloque("tareas", lambda: [t.to_dict() for t in repos.tasks.list(limit=1000)])
    _bloque("planes", lambda: [p.to_dict() for p in repos.planes.list(limit=1000)])
    _bloque("ajustes", _ajustes(container))
    _bloque("servicios_conectados", _servicios_conectados(container))

    if fallos:
        datos["incompleto"] = fallos

    container.audit_logger.log(
        tool_name="export_account_data",
        risk_level="moderate",
        authorized=True,
        args={"bloques": [k for k in datos if isinstance(datos.get(k), list)]},
        success=not fallos,
        error=None if not fallos else f"no se pudo leer: {', '.join(fallos)}",
    )

    return {"success": True, "datos": datos}


def _ajustes(container: CoreContainer):
    """Las preferencias: nombre, ocupacion, idioma, estilo de respuesta.

    Viven en la memoria persistente, no en una tabla propia, asi que se leen por
    donde las lee la vista de Ajustes. Son datos que la persona escribio sobre
    si misma: de lo mas suyo que hay en Morgan.
    """
    def leer():
        from src.memory.settings import SettingsStore

        return SettingsStore(container.memory_manager).load().to_dict()

    return leer


def _servicios_conectados(container: CoreContainer):
    """Qué tienes conectado, SIN los tokens.

    Se incluye porque saber que tu Morgan tiene acceso a tu GitHub es un dato
    tuyo y de los que importan. El token no: abre una cuenta ajena a Morgan, y
    en un fichero de descargas no pinta nada.
    """
    def leer():
        from src.integraciones.repositorio import repositorio_de_integraciones

        repo = repositorio_de_integraciones(container.repositories)
        return [i.to_dict() for i in repo.listar()]

    return leer


# --- Tokens personales de API (plan de la API, fase 1) -------------------------
#
# Se gestionan SOLO con sesión. Viven bajo `/auth`, y todo `/auth` salvo
# `GET /auth/yo` está fuera del alcance de cualquier token (src/identidad/tokens.py):
# con un token robado no se crean más tokens ni se revoca el de su dueño.


class CrearTokenRequest(BaseModel):
    nombre: str = Field(..., description="Para qué es: «script de copias», «VS Code»")
    alcances: list[str] = Field(..., description="Uno o varios de: chat, lectura, escritura")
    dias: int = Field(90, description="Días hasta que caduca, de 1 a 365")


def _servicio_de_tokens(container: CoreContainer):
    from src.identidad.tokens import ServicioDeTokens

    return ServicioDeTokens(repositorio_de_cuentas(container.repositories))


def _exigir_tokens_activos() -> None:
    if not get_settings().tokens_api:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "TOKENS_DESACTIVADOS",
                "message": "Este Morgan no tiene activados los tokens de API.",
            },
        )


@router.get("/tokens", summary="Mis tokens de API")
def listar_tokens(container: CoreContainer = Depends(get_container)) -> dict:
    """Los tokens vivos, sin su valor: ese se enseña una sola vez, al crearlo."""
    user_id = _exigir_cuenta()
    return {"success": True, "tokens": _servicio_de_tokens(container).listar(user_id)}


@router.post("/tokens", summary="Crear un token de API")
def crear_token(
    datos: CrearTokenRequest,
    container: CoreContainer = Depends(get_container),
) -> dict:
    """Crea un token y devuelve su valor **esta única vez**.

    Guárdalo al recibirlo: Morgan solo conserva su huella y no puede volver a
    enseñarlo. Si se pierde, se revoca y se crea otro.
    """
    from src.identidad.tokens import DemasiadosTokens, TokenNoValido

    user_id = _exigir_cuenta()
    _exigir_tokens_activos()

    try:
        valor, token = _servicio_de_tokens(container).crear(
            user_id, datos.nombre, datos.alcances, datos.dias
        )
    except TokenNoValido as exc:
        raise _400(exc) from exc
    except DemasiadosTokens as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DEMASIADOS_TOKENS", "message": str(exc)},
        ) from exc

    container.audit_logger.registrar_evento(
        "crear_token", actor=user_id, objetivo=token["id"],
        detalle=",".join(token["alcances"]),
    )
    # La dirección de la API (4.17), para que la web enseñe ejemplos que funcionan tal cual.
    from src.api.routes.integraciones import _url_publica_de_la_api

    return {"success": True, "token": token, "valor": valor, "api_url": _url_publica_de_la_api()}


@router.delete("/tokens/{token_id}", summary="Revocar un token de API")
def revocar_token(token_id: str, container: CoreContainer = Depends(get_container)) -> dict:
    """Deja de valer en la petición siguiente. Uno de otra persona no se encuentra."""
    user_id = _exigir_cuenta()

    if not _servicio_de_tokens(container).revocar(user_id, token_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TOKEN_NO_ENCONTRADO", "message": "Ese token no existe o ya no vale."},
        )

    container.audit_logger.registrar_evento("revocar_token", actor=user_id, objetivo=token_id)
    return {"success": True}


@router.post("/tokens/revocar-todos", summary="Revocar todos mis tokens de API")
def revocar_todos_los_tokens(container: CoreContainer = Depends(get_container)) -> dict:
    user_id = _exigir_cuenta()
    revocados = _servicio_de_tokens(container).revocar_todos(user_id)
    container.audit_logger.registrar_evento(
        "revocar_tokens", actor=user_id, detalle=f"{revocados} revocados"
    )
    return {"success": True, "revocados": revocados}


# --- El permiso automático (4.6) -----------------------------------------------------


class PermisoAutomaticoRequest(BaseModel):
    encendido: bool


@router.get("/permiso-automatico", summary="Si lo verde y amarillo se aprueba solo")
def ver_permiso_automatico(container: CoreContainer = Depends(get_container)) -> dict:
    from src.identidad import permiso_automatico

    repo = repositorio_de_cuentas(container.repositories)
    return {"success": True, "encendido": permiso_automatico.lo_tiene(repo, usuario_actual())}


@router.put("/permiso-automatico", summary="Encender o apagar el permiso automático")
def cambiar_permiso_automatico(
    datos: PermisoAutomaticoRequest,
    container: CoreContainer = Depends(get_container),
) -> dict:
    """Lo verde y amarillo se aprueba y se ejecuta solo; lo rojo sigue esperando (4.6).

    Solo con la sesión de la web: `/auth` está vedado a los tokens de API. Queda en la
    auditoría, encendido y apagado."""
    from src.identidad import permiso_automatico

    user_id = usuario_actual()
    repo = repositorio_de_cuentas(container.repositories)
    encendido = permiso_automatico.cambiar(repo, user_id, datos.encendido)
    container.audit_logger.registrar_evento(
        "permiso_automatico", actor=user_id, detalle="encendido" if encendido else "apagado"
    )
    return {"success": True, "encendido": encendido}
