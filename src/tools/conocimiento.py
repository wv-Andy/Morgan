"""
Herramientas de conocimiento (V1.8).

Le dan a Morgan una biblioteca que consultar, distinta de lo que recuerda de ti.

La diferencia importa para el modelo tanto como para el diseño, y por eso las
descripciones insisten en ella: `remember_fact` es para «prefiero que me hables de
usted»; `add_knowledge` es para el manual de 40 páginas. Confundirlas llena el
system prompt de documentos o entierra tus preferencias entre notas.

Todas son `requires_local = False`: trabajan sobre la base, no sobre el disco.
"""

import logging
from typing import Any

from src.tools.base import RiskLevel, Tool, ToolCategory

logger = logging.getLogger(__name__)


#: Si cada persona tiene documentos en este espacio (4.2). Lo olvidan las herramientas que
#: los crean o los borran, que son el único sitio donde eso pasa.
CON_DOCUMENTOS = None


def _con_documentos():
    global CON_DOCUMENTOS
    if CON_DOCUMENTOS is None:
        from src.tools.recordado import Recordado

        CON_DOCUMENTOS = Recordado()
    return CON_DOCUMENTOS


def _titulos(almacen) -> list[str] | None:
    """Los títulos de quien pregunta (hasta `TITULOS_EN_EL_TURNO`), recordados un rato, o
    `None` si no se pudieron leer (4.3). Una sola consulta sirve para el índice del turno
    y para saber si sus herramientas se ofrecen: antes eran dos, y la del índice, en
    **cada** turno. Guardar, indexar o borrar un documento lo olvida."""
    from src.tools.recordado import de_quien

    return _con_documentos().dato((almacen, *de_quien()),
                                  lambda: list(almacen.titulos(TITULOS_EN_EL_TURNO)), None)


def tiene_documentos(almacen) -> bool:
    """Si quien pregunta tiene algún documento (4.2). Si no se sabe, sí: ver `Recordado`."""
    titulos = _titulos(almacen)
    return True if titulos is None else bool(titulos)


class _HerramientaDeConocimiento(Tool):
    def __init__(self, almacen):
        self.almacen = almacen

    @property
    def category(self) -> str:
        return ToolCategory.MEMORY.value

    @property
    def requires_local(self) -> bool:
        return False

    @staticmethod
    def _ok(datos: Any) -> dict:
        return {"success": True, "data": datos, "error": None}

    @staticmethod
    def _error(mensaje: str) -> dict:
        return {"success": False, "data": None, "error": mensaje}


class SearchKnowledgeTool(_HerramientaDeConocimiento):
    motivo_no_disponible = "la persona no tiene ningún documento guardado en su conocimiento."

    def disponible(self) -> bool:
        """Solo con algún documento guardado (4.2): en un conocimiento vacío no hay nada
        que buscar, listar ni borrar, y su esquema viajaba en cada llamada al modelo."""
        return tiene_documentos(self.almacen)

    """Busca en lo que se ha guardado. Es la que más se usa, con diferencia."""

    @property
    def name(self) -> str:
        return "search_knowledge"

    @property
    def description(self) -> str:
        return (
            "Busca en los documentos que la persona guardó (notas, manuales, «mi "
            "procedimiento»). Si es sobre sus cosas, antes que en internet o en su PC. "
            "Devuelve fragmentos con su documento."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": "Qué buscar, en lenguaje natural",
                },
                "coleccion": {
                    "type": "string",
                    "description": "Buscar solo dentro de una colección concreta",
                },
                "limite": {
                    "type": "integer",
                    "description": "Cuántos fragmentos devolver (por defecto 5)",
                },
            },
            "required": ["consulta"],
        }

    def execute(self, consulta: str = "", **kwargs) -> dict:
        if not (consulta or "").strip():
            return self._error("Hace falta algo que buscar.")

        limite = max(1, min(int(kwargs.get("limite") or 5), 10))
        resultados = self.almacen.buscar(
            consulta, coleccion=kwargs.get("coleccion"), limite=limite
        )

        if not resultados:
            # Se dice explicitamente que no hay nada, y que eso no significa que
            # la respuesta no exista: solo que no esta guardada aqui. Sin esa
            # aclaracion, el modelo tiende a responder "no tienes informacion
            # sobre eso" como si fuera un hecho del mundo.
            return self._ok({
                "encontrados": 0,
                "resultados": [],
                "nota": (
                    "No hay nada guardado sobre eso. No significa que la "
                    "respuesta no exista: solo que no está en los documentos del "
                    "usuario. Puedes buscar en internet o preguntarle."
                ),
            })

        return self._ok({
            "encontrados": len(resultados),
            "resultados": resultados,
            "nota": (
                "Esto sale de los documentos del usuario. Cita de qué documento "
                "es lo que uses, para que pueda comprobarlo."
            ),
        })


class AddKnowledgeTool(_HerramientaDeConocimiento):
    @property
    def name(self) -> str:
        return "add_knowledge"

    @property
    def description(self) -> str:
        return (
            "Guarda un texto largo para consultarlo después (notas, documentación). Los "
            "hechos sueltos de la persona van en remember_fact."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "titulo": {
                    "type": "string",
                    "description": "Para encontrarlo después",
                },
                "contenido": {"type": "string", "description": "El texto entero"},
                "fuente": {
                    "type": "string",
                    "description": "URL, archivo o 'el usuario'",
                },
                "coleccion": {
                    "type": "string",
                    "description": "Para agrupar documentos del mismo tema",
                },
            },
            "required": ["titulo", "contenido"],
        }

    def execute(self, titulo: str = "", contenido: str = "", **kwargs) -> dict:
        _con_documentos().olvidar()
        try:
            documento = self.almacen.añadir(
                titulo=titulo,
                contenido=contenido,
                fuente=kwargs.get("fuente") or "el usuario",
                coleccion=kwargs.get("coleccion") or "general",
            )
        except ValueError as exc:
            return self._error(str(exc))

        datos = documento.to_dict()
        datos["fragmentos"] = len(documento.fragmentar())
        return self._ok(datos)


class ListKnowledgeTool(_HerramientaDeConocimiento):
    motivo_no_disponible = "la persona no tiene ningún documento guardado en su conocimiento."

    def disponible(self) -> bool:
        """Solo con algún documento guardado (4.2): en un conocimiento vacío no hay nada
        que buscar, listar ni borrar, y su esquema viajaba en cada llamada al modelo."""
        return tiene_documentos(self.almacen)

    @property
    def name(self) -> str:
        return "list_knowledge_sources"

    @property
    def description(self) -> str:
        return (
            "Lista qué documentos hay guardados y en qué colecciones. Útil para "
            "saber sobre qué puedes consultar antes de buscar."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "coleccion": {"type": "string", "description": "Filtrar por colección"},
            },
        }

    def execute(self, **kwargs) -> dict:
        documentos = self.almacen.listar(coleccion=kwargs.get("coleccion"), limite=50)

        return self._ok({
            "total": len(documentos),
            "colecciones": self.almacen.colecciones(),
            "documentos": [
                {
                    "id": d.id,
                    "titulo": d.titulo,
                    "coleccion": d.coleccion,
                    "fuente": d.fuente,
                    "resumen": d.resumen,
                }
                for d in documentos
            ],
        })


class RemoveKnowledgeTool(_HerramientaDeConocimiento):
    motivo_no_disponible = "la persona no tiene ningún documento guardado en su conocimiento."

    def disponible(self) -> bool:
        """Solo con algún documento guardado (4.2): en un conocimiento vacío no hay nada
        que buscar, listar ni borrar, y su esquema viajaba en cada llamada al modelo."""
        return tiene_documentos(self.almacen)

    @property
    def name(self) -> str:
        return "remove_knowledge"

    @property
    def description(self) -> str:
        return "Elimina un documento guardado, por su identificador."

    @property
    def permission_level(self) -> str:
        # Borrar algo que el usuario guardo a proposito no es una operacion
        # inocente: se pierde el contenido, no solo el indice.
        return RiskLevel.MODERATE.value

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "documento_id": {"type": "string", "description": "Identificador"},
            },
            "required": ["documento_id"],
        }

    def execute(self, documento_id: str = "", **kwargs) -> dict:
        if self.almacen.eliminar(documento_id):
            _con_documentos().olvidar()
            return self._ok({"eliminado": documento_id})

        return self._error(f"No hay ningún documento con el identificador '{documento_id}'.")


class IndexUploadTool(_HerramientaDeConocimiento):
    """Convierte un archivo que subió el usuario en conocimiento consultable."""

    def __init__(self, almacen, uploads):
        super().__init__(almacen)
        self.uploads = uploads

    motivo_no_disponible = "la persona no tiene ningún archivo subido que convertir en documento."

    def disponible(self) -> bool:
        """Solo con algún archivo subido (4.2): es lo que convierte en documento."""
        return self.uploads.tiene_alguno()

    @property
    def name(self) -> str:
        return "index_document"

    @property
    def description(self) -> str:
        return (
            "Guarda un archivo subido (PDF, notas) como documento consultable, para "
            "buscarlo después sin leerlo entero."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "upload_id": {
                    "type": "string",
                    "description": "Identificador del archivo, de 'list_uploads'",
                },
                "coleccion": {"type": "string", "description": "Dónde agruparlo"},
            },
            "required": ["upload_id"],
        }

    def execute(self, upload_id: str = "", **kwargs) -> dict:
        archivo = self.uploads.obtener(upload_id)

        if archivo is None:
            return self._error(
                f"No hay ningún archivo con el identificador '{upload_id}'. "
                "Puede que haya caducado."
            )

        contenido = self.uploads.leer(upload_id)
        if contenido is None:
            return self._error("El archivo ya no está disponible.")

        try:
            from src.uploads.extraccion import extraer

            texto = extraer(contenido, archivo.mime, archivo.nombre_original)
        except Exception as exc:
            return self._error(f"No se pudo leer el archivo: {exc}")

        if not (texto or "").strip():
            return self._error(
                f"De '{archivo.nombre_original}' no se pudo sacar texto. "
                "Si es una imagen o un audio, usa 'analyze_image' o "
                "'transcribe_audio' en su lugar."
            )

        _con_documentos().olvidar()
        documento = self.almacen.añadir(
            titulo=archivo.nombre_original,
            contenido=texto,
            fuente=f"archivo subido ({archivo.mime})",
            coleccion=kwargs.get("coleccion") or "general",
            metadatos={"upload_id": upload_id},
        )

        datos = documento.to_dict()
        datos["fragmentos"] = len(documento.fragmentar())
        return self._ok(datos)


#: Cuántos títulos van en el turno, y cuánto de cada uno.
TITULOS_EN_EL_TURNO = 12
LARGO_DEL_TITULO = 60


def indice_para_el_turno(almacen) -> str:
    """Los títulos de los documentos de quien pregunta, para su turno (4.1), o "".

    Medido en la 4.0-D y la 4.0.5: con «anótalo como dice mi procedimiento», el modelo
    buscaba entre los archivos del PC, a veces hasta el tope de vueltas, porque no sabía
    que ese documento estaba en su conocimiento. Solo los títulos: unas decenas de tokens,
    y solo a quien tiene documentos. Son datos de la persona: entre comillas, en una línea,
    recortados."""
    titulos = _titulos(almacen)
    if titulos is None:
        logger.warning("No se pudieron leer los títulos del conocimiento")
        return ""
    limpios = [" ".join(str(t).split())[:LARGO_DEL_TITULO] for t in titulos if str(t or "").strip()]
    if not limpios:
        return ""
    return ("Documentos guardados en su conocimiento (search_knowledge los busca; si habla de "
            "«mi procedimiento», «mi guía»… empieza por ahí): "
            + ", ".join(f"«{t}»" for t in limpios) + ".")


def knowledge_tools(almacen, uploads=None) -> list[Tool]:
    """Las herramientas de conocimiento, listas para registrar."""
    herramientas: list[Tool] = [
        SearchKnowledgeTool(almacen),
        AddKnowledgeTool(almacen),
        ListKnowledgeTool(almacen),
        RemoveKnowledgeTool(almacen),
    ]

    if uploads is not None:
        herramientas.append(IndexUploadTool(almacen, uploads))

    return herramientas
