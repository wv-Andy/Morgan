"""
Herramientas sobre archivos subidos por el usuario (V1.4).

**Los nombres llevan sufijo `_upload` a propósito.** El plan original las llamaba
`list_files`, `read_file` y `create_file`, pero esos nombres ya existen y operan
sobre el disco del usuario. Serían dos herramientas distintas con el mismo nombre:
el modelo las confundiría, y en el entorno `cloud` las locales ni siquiera se
registran, así que llamaría a algo inexistente.

Todas son `requires_local = False`: trabajan sobre archivos que llegaron por la
web, no sobre el disco, así que tienen sentido también en la nube. Es justo lo
contrario que las de `filesystem`.

Y todas tratan el contenido como **dato no confiable**: lo escribió un tercero.
"""

import base64
import logging
from typing import Any

from src.tools.base import RiskLevel, Tool, ToolCategory
from src.uploads.extraccion import ExtraccionFallida, extraer
from src.uploads.store import UploadStore

logger = logging.getLogger(__name__)


class _HerramientaDeSubidas(Tool):
    """Base común: todas necesitan el almacén y ninguna toca el disco del usuario."""

    def __init__(self, store: UploadStore):
        self.store = store

    motivo_no_disponible = ("la persona no tiene ningún archivo subido aquí; si quiere que "
                            "mires uno, que lo adjunte.")

    def disponible(self) -> bool:
        """Solo si la persona tiene algún archivo subido (4.2): si no, no hay nada que
        listar, leer, mirar ni transcribir, y sus esquemas viajaban en cada llamada."""
        return self.store.tiene_alguno()

    @property
    def category(self) -> str:
        return ToolCategory.WEB.value

    @property
    def requires_local(self) -> bool:
        return False

    def _archivo(self, upload_id: str):
        archivo = self.store.obtener(upload_id)
        if archivo is None:
            return None, {
                "success": False,
                "data": None,
                "error": (
                    f"No encuentro el archivo '{upload_id}'. Puede haber caducado: "
                    "los archivos subidos se borran solos pasado su tiempo de vida."
                ),
            }
        return archivo, None


class ListUploadsTool(_HerramientaDeSubidas):
    """Qué archivos ha subido el usuario en esta sesión."""

    @property
    def name(self) -> str:
        return "list_uploads"

    @property
    def description(self) -> str:
        return (
            "Lista los archivos que el usuario ha subido por la interfaz web "
            "(imágenes, PDF, texto, código, audio). NO lista archivos del disco: "
            "para eso está 'list_files'."
        )

    @property
    def permission_level(self) -> str:
        return RiskLevel.SAFE.value

    def execute(self, **kwargs: Any) -> dict:
        archivos = [a.to_dict() for a in self.store.listar()]
        return {
            "success": True,
            "data": {"count": len(archivos), "uploads": archivos},
            "error": None,
        }


class ReadUploadTool(_HerramientaDeSubidas):
    """Extrae el texto de un archivo subido."""

    @property
    def name(self) -> str:
        return "read_upload"

    @property
    def description(self) -> str:
        return (
            "Extrae y devuelve el texto de un archivo que el usuario subió por la "
            "web: PDF, texto, Markdown, CSV, JSON o código. Para imágenes usa "
            "'analyze_image'. NO lee archivos del disco: para eso está 'read_file'."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "upload_id": {
                    "type": "string",
                    "description": "Identificador del archivo, obtenido de 'list_uploads'",
                },
            },
            "required": ["upload_id"],
        }

    @property
    def permission_level(self) -> str:
        return RiskLevel.SAFE.value

    def execute(self, upload_id: str = "", **kwargs: Any) -> dict:
        archivo, error = self._archivo(upload_id)
        if error:
            return error

        try:
            contenido = self.store.leer(upload_id)
            if contenido is None:
                raise OSError("el contenido ya no está disponible")
            texto = extraer(contenido, archivo.mime, archivo.nombre_original)
        except ExtraccionFallida as exc:
            return {"success": False, "data": None, "error": str(exc)}
        except OSError as exc:
            logger.warning("No se pudo leer el archivo subido %s: %s", upload_id, exc)
            return {
                "success": False,
                "data": None,
                "error": "No he podido acceder al archivo. Puede haberse borrado.",
            }

        return {
            "success": True,
            "data": {
                "nombre": archivo.nombre_original,
                "mime": archivo.mime,
                "contenido": texto,
            },
            "error": None,
        }


def _apuntar_cupo(uso, concepto: str) -> dict | None:
    """Apunta una operación de pago, o devuelve el error listo para el modelo.

    Se devuelve como resultado de herramienta en vez de lanzar: el agente
    entiende un `success: False` y se lo explica a la persona, mientras que una
    excepción aquí dentro aborta el turno entero con un fallo genérico.
    """
    if uso is None:
        # Sin control de uso —Morgan de escritorio, o una prueba— no hay a quien
        # limitar. Es tu equipo y tus claves.
        return None

    from src.identidad import rol_actual, usuario_actual
    from src.identidad.cuotas import CuotaAgotada

    try:
        # Con el rol: sin él, el propietario autenticado en la nube gastaba cupo
        # de imágenes y transcripciones como cualquier cuenta (V2.0.24).
        uso.apuntar(usuario_actual(), concepto, rol_actual())
    except CuotaAgotada as exc:
        return {"success": False, "data": None, "error": str(exc)}

    return None


class AnalyzeImageTool(_HerramientaDeSubidas):
    """Describe o responde preguntas sobre una imagen subida.

    A diferencia de las demás, esta necesita un modelo con visión, y por eso
    recibe la cadena de proveedores: es la primera herramienta que depende de una
    capacidad del modelo y no solo del entorno.
    """

    def __init__(self, store: UploadStore, provider=None, uso=None):
        super().__init__(store)
        self.provider = provider
        self.uso = uso

    @property
    def name(self) -> str:
        return "analyze_image"

    @property
    def description(self) -> str:
        return (
            "Mira una imagen subida: la describe, lee su texto o responde una pregunta "
            "sobre ella."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "upload_id": {
                    "type": "string",
                    "description": "Identificador de la imagen, obtenido de 'list_uploads'",
                },
                "pregunta": {
                    "type": "string",
                    "description": "Qué quieres saber de la imagen. Si se omite, se describe.",
                },
            },
            "required": ["upload_id"],
        }

    @property
    def permission_level(self) -> str:
        return RiskLevel.SAFE.value

    def execute(self, upload_id: str = "", pregunta: str = "", **kwargs: Any) -> dict:
        from src.models.base import ChatMessage
        from src.models.capabilities import CapacidadNoDisponible, Capability

        archivo, error = self._archivo(upload_id)
        if error:
            return error

        # Se apunta despues de comprobar que el archivo existe —no tiene sentido
        # gastarle el cupo a nadie por un identificador equivocado— y antes de
        # llamar al modelo, que es lo que cuesta dinero.
        agotado = _apuntar_cupo(self.uso, "imagenes")
        if agotado:
            return agotado

        if archivo.familia != "imagen":
            return {
                "success": False,
                "data": None,
                "error": (
                    f"'{archivo.nombre_original}' no es una imagen, es "
                    f"'{archivo.mime}'. Para leer su texto usa 'read_upload'."
                ),
            }

        if self.provider is None:
            return {
                "success": False,
                "data": None,
                "error": "No hay ningún modelo configurado para analizar imágenes.",
            }

        contenido = self.store.leer(upload_id)
        if contenido is None:
            return {
                "success": False,
                "data": None,
                "error": "No he podido acceder a la imagen. Puede haberse borrado.",
            }

        instruccion = pregunta.strip() or "Describe con detalle lo que aparece en esta imagen."
        # La imagen viaja en su propio campo: cada proveedor la traduce a su
        # formato nativo, y meterla en el texto la convertiria en cientos de miles
        # de caracteres de contexto inutil para los que no la entienden.
        mensaje = ChatMessage(
            role="user",
            content=instruccion,
            image={
                "mime": archivo.mime,
                "base64": base64.b64encode(contenido).decode("ascii"),
            },
        )

        try:
            respuesta = self.provider.generate([mensaje], requires=Capability.VISION)
        except CapacidadNoDisponible as exc:
            from src.models.errors import describe_llm_error

            return {"success": False, "data": None, "error": describe_llm_error(exc)}
        except Exception as exc:
            from src.models.errors import log_llm_error

            return {"success": False, "data": None, "error": log_llm_error(exc, "analyze_image")}

        return {
            "success": True,
            "data": {
                "nombre": archivo.nombre_original,
                # Lo que describe el modelo procede de una imagen que subio un
                # tercero: es dato, no instruccion.
                "descripcion": f"<untrusted_file_data source=\"{archivo.nombre_original}\">\n"
                               f"{respuesta.content}\n</untrusted_file_data>",
            },
            "error": None,
        }


class TranscribeAudioTool(_HerramientaDeSubidas):
    """Convierte un audio subido en texto.

    Usa Whisper a través del SDK de Groq, que ya está instalado y usa la misma
    clave que el chat: no hace falta un proveedor nuevo ni otra credencial. El
    modelo detecta el idioma solo, y se le puede forzar uno cuando el audio es
    corto o ruidoso y la detección falla.
    """

    # Modelo de transcripción de Groq. Se deja aquí y no en la configuración
    # porque hoy no hay alternativa entre la que elegir; cuando la haya, se
    # traslada a Settings.
    MODELO = "whisper-large-v3-turbo"

    def __init__(self, store: UploadStore, api_key: str | None = None, uso=None):
        super().__init__(store)
        self._api_key = api_key
        self.uso = uso

    @property
    def name(self) -> str:
        return "transcribe_audio"

    @property
    def description(self) -> str:
        return (
            "Pasa a texto un audio subido o grabado; detecta el idioma."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "upload_id": {
                    "type": "string",
                    "description": "Identificador del audio, obtenido de 'list_uploads'",
                },
                "idioma": {
                    "type": "string",
                    "description": "ISO ('es', 'en') solo para forzarlo",
                },
            },
            "required": ["upload_id"],
        }

    @property
    def permission_level(self) -> str:
        return RiskLevel.SAFE.value

    def execute(self, upload_id: str = "", idioma: str = "", **kwargs: Any) -> dict:
        archivo, error = self._archivo(upload_id)
        if error:
            return error

        agotado = _apuntar_cupo(self.uso, "transcripciones")
        if agotado:
            return agotado

        if archivo.familia != "audio":
            return {
                "success": False,
                "data": None,
                "error": (
                    f"'{archivo.nombre_original}' no es un audio, es '{archivo.mime}'."
                ),
            }

        from src.config import get_settings

        clave = self._api_key or get_settings().groq_api_key
        if not clave:
            return {
                "success": False,
                "data": None,
                "error": (
                    "No hay ningún servicio de transcripción configurado. "
                    "Hace falta una clave de Groq."
                ),
            }

        contenido = self.store.leer(upload_id)
        if contenido is None:
            return {
                "success": False,
                "data": None,
                "error": "No he podido acceder al audio. Puede haberse borrado.",
            }

        try:
            from groq import Groq

            cliente = Groq(api_key=clave, timeout=float(get_settings().llm_timeout))
            parametros = {
                "file": (archivo.nombre_original, contenido),
                "model": self.MODELO,
                "response_format": "verbose_json",
            }
            # Solo se envía si se pidió: mandarlo vacío desactivaría la detección.
            if idioma.strip():
                parametros["language"] = idioma.strip()

            resultado = cliente.audio.transcriptions.create(**parametros)
        except Exception as exc:
            from src.models.errors import log_llm_error

            return {
                "success": False,
                "data": None,
                "error": log_llm_error(exc, "transcribe_audio"),
            }

        texto = (getattr(resultado, "text", "") or "").strip()
        # Whisper devuelve puntuacion suelta ('.', '...') cuando no oye voz, no
        # una cadena vacia. Comprobado con un tono puro: devolvio ".". Entregar
        # eso como transcripcion no dice nada a quien grabo.
        if not any(c.isalnum() for c in texto):
            return {
                "success": False,
                "data": None,
                "error": (
                    "No se reconoció ninguna voz en el audio. Comprueba que se "
                    "grabó con el micrófono abierto y que se oye algo."
                ),
            }

        return {
            "success": True,
            "data": {
                "nombre": archivo.nombre_original,
                "idioma": getattr(resultado, "language", None) or idioma or "detectado",
                # Lo dicho en el audio es contenido de un tercero, igual que un
                # archivo o una pagina web: dato, no instruccion.
                "transcripcion": f'<untrusted_file_data source="{archivo.nombre_original}">\n'
                                 f"{texto}\n</untrusted_file_data>",
            },
            "error": None,
        }
