"""
Subida de archivos (V1.4).

El archivo se valida y se guarda; **no se procesa aquí**. Extraer texto de un PDF
o describir una imagen es trabajo del agente, y hacerlo en la subida obligaría a
esperar por algo que quizá no se pida nunca.

Nada de lo que llega se ejecuta ni se interpreta por el hecho de haberse subido.
"""

import logging

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from src.api.dependencies import CoreContainer, get_container
from src.api.schemas import UploadItem, UploadListResponse
from src.config import get_settings
from src.uploads.store import ArchivoRechazado

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["Uploads"])


@router.get("", response_model=UploadListResponse, summary="Archivos subidos")
def list_uploads(container: CoreContainer = Depends(get_container)) -> UploadListResponse:
    archivos = container.uploads.listar()
    return UploadListResponse(
        success=True,
        count=len(archivos),
        uploads=[UploadItem(**a.to_dict()) for a in archivos],
        max_mb=get_settings().upload_max_bytes // (1024 * 1024),
        ttl_hours=get_settings().upload_ttl_hours,
    )


@router.get("/{upload_id}/contenido", summary="Descargar un archivo")
def descargar(upload_id: str, container: CoreContainer = Depends(get_container)) -> Response:
    """El archivo tal cual, para guardarlo en el móvil o en el PC (3.1-E).

    **Solo lo propio**: el repositorio filtra por la cuenta de la petición, así que un
    identificador de otra persona no se encuentra. Se sirve **siempre como descarga**
    (`attachment`) y con `nosniff`: un HTML o un SVG subido no se abre como página en el
    dominio de Morgan, que sería una forma de ejecutar código con la sesión de su dueño.
    """
    archivo = container.uploads.obtener(upload_id)
    contenido = container.uploads.leer(upload_id) if archivo else None
    if archivo is None or contenido is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NO_ENCONTRADO",
                    "message": "Ese archivo no existe o ya caducó (se borran solos)."},
        )
    # El nombre va codificado en las dos formas de la cabecera: `quote` escapa comillas
    # y saltos de línea, así que un nombre no puede partir la cabecera ni añadir otra.
    nombre = quote(archivo.nombre_original)
    return Response(
        content=contenido,
        media_type=archivo.mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"; '
                                   f"filename*=UTF-8''{nombre}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("", response_model=UploadItem, summary="Subir un archivo")
async def upload(
    file: UploadFile = File(..., description="Imagen, PDF, texto, código o audio"),
    container: CoreContainer = Depends(get_container),
) -> UploadItem:
    contenido = await file.read()

    try:
        archivo = container.uploads.guardar(file.filename or "archivo", contenido)
    except ArchivoRechazado as exc:
        # Un rechazo tambien se audita: saber que alguien intento subir un
        # ejecutable disfrazado importa mas que saber que subio un PDF.
        container.audit_logger.log(
            tool_name="upload_file",
            risk_level="moderate",
            authorized=True,
            args={"nombre": (file.filename or "")[:120], "bytes": len(contenido)},
            success=False,
            error=str(exc),
        )
        # 422 y no 500: el usuario ha hecho algo que puede corregir, y el mensaje
        # esta escrito para leerse.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "UPLOAD_REJECTED", "message": str(exc)},
        )
    except OSError as exc:
        logger.error("No se pudo guardar el archivo subido: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "STORAGE_ERROR", "message": "No se pudo guardar el archivo."},
        )

    # Se registra el nombre y el tipo, nunca el contenido: un archivo del usuario
    # puede llevar datos personales y el log de auditoria no es sitio para eso.
    container.audit_logger.log(
        tool_name="upload_file",
        risk_level="moderate",
        authorized=True,
        args={"id": archivo.id, "nombre": archivo.nombre_original,
              "mime": archivo.mime, "bytes": archivo.tamano},
        success=True,
    )

    return UploadItem(**archivo.to_dict())


@router.delete("/{upload_id}", summary="Eliminar un archivo subido")
def delete_upload(
    upload_id: str,
    container: CoreContainer = Depends(get_container),
) -> dict:
    borrado = container.uploads.eliminar(upload_id)

    container.audit_logger.log(
        tool_name="delete_upload",
        risk_level="moderate",
        authorized=True,
        args={"id": upload_id},
        success=borrado,
        error=None if borrado else "no existe",
    )

    if not borrado:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UPLOAD_NOT_FOUND", "message": f"No existe el archivo '{upload_id}'."},
        )
    return {"success": True, "deleted": upload_id}


@router.post(
    "/{upload_id}/transcripcion",
    summary="Transcribir un audio para revisarlo antes de enviarlo",
    responses={
        404: {"description": "No existe el archivo, o no es tuyo"},
        409: {"description": "El archivo no es audio"},
        429: {"description": "Cupo diario de transcripciones agotado"},
        503: {"description": "No hay servicio de transcripción configurado"},
    },
)
def transcribir(
    upload_id: str,
    container: CoreContainer = Depends(get_container),
) -> dict:
    """Devuelve lo que dice un audio, sin gastar un turno de conversación.

    **Por qué existe, si Morgan ya sabe transcribir.** Sabe, pero como
    herramienta: la ejecuta *dentro* del turno, así que lo primero que ve el
    usuario de su propia grabación es la respuesta a algo que no ha podido leer.
    Si Whisper entendió mal una palabra —y con nombres propios o términos
    técnicos pasa—, Morgan responde a otra pregunta y no hay forma de saber por
    qué.

    Con esta ruta, la web transcribe, **enseña el texto y deja corregirlo**, y
    solo entonces se envía como un mensaje normal.

    Hay un efecto secundario que conviene nombrar porque es de seguridad: lo que
    se dice en un audio es contenido no confiable, igual que una página web. En
    el camino anterior entraba directo al contexto del modelo. Aquí lo lee una
    persona antes, y al enviarlo pasa a ser *su* mensaje. Un intento de
    inyección por audio deja de ser invisible.

    El cupo diario se consume igual: la transcripción cuesta lo mismo se pida
    desde donde se pida.
    """
    from src.identidad.cuotas import CuotaAgotada
    from src.tools.multimodal import TranscribeAudioTool

    # Las dos condiciones que se pueden comprobar aqui se comprueban AQUI, y no
    # leyendo el mensaje de error de la herramienta. Adivinar el codigo HTTP por
    # el texto es fragil de una forma concreta: basta con reescribir el mensaje
    # para mejorarlo —«no encuentro» en lugar de «no existe»— y la ruta empieza
    # a devolver 400 donde devolvia 404, sin que nada falle en el sitio del
    # cambio. Paso en la primera version de esta ruta.
    archivo = container.uploads.obtener(upload_id)
    if archivo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "UPLOAD_NOT_FOUND",
                "message": f"No existe el archivo '{upload_id}', o ya caducó.",
            },
        )

    if archivo.familia != "audio":
        # 409 y no 400: la peticion esta bien formada, es el archivo el que no
        # admite esta operacion.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NO_ES_AUDIO",
                "message": f"'{archivo.nombre_original}' no es un audio.",
            },
        )

    herramienta = next(
        (h for h in container.tool_registry.list_tools()
         if h.name == "transcribe_audio"),
        None,
    )
    if herramienta is None:
        # En la nube el catalogo es otro. Si no esta registrada, decirlo es mas
        # util que un 500 generico.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SIN_TRANSCRIPCION",
                "message": "Este Morgan no tiene la transcripción disponible.",
            },
        )

    assert isinstance(herramienta, TranscribeAudioTool)
    try:
        resultado = herramienta.execute(upload_id=upload_id)
    except CuotaAgotada as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "CUOTA_AGOTADA", "message": str(exc)},
        ) from exc

    if not resultado.get("success"):
        mensaje = resultado.get("error") or "No se pudo transcribir el audio."
        # Lo que queda son fallos del servicio de transcripcion: ni el archivo
        # ni el cupo, que ya se han descartado arriba.
        codigo = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if "cupo" in mensaje.lower() or "límite" in mensaje.lower()
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(
            status_code=codigo,
            detail={"code": "TRANSCRIPCION_FALLIDA", "message": mensaje},
        )

    datos = resultado["data"]
    return {
        "success": True,
        # El texto va LIMPIO, sin el envoltorio `<untrusted_file_data>`. Ese
        # marcado existe para que el modelo sepa que es dato y no instruccion;
        # aqui lo lee una persona, y pegarselo en el compositor seria ruido.
        "texto": _sin_envoltorio(datos.get("transcripcion", "")),
        "idioma": datos.get("idioma"),
        "nombre": datos.get("nombre"),
    }


def _sin_envoltorio(texto: str) -> str:
    """Quita el marcado `<untrusted_file_data>` que la herramienta añade."""
    if "<untrusted_file_data" not in texto:
        return texto.strip()

    cuerpo = texto.split(">", 1)[-1]
    return cuerpo.replace("</untrusted_file_data>", "").strip()
