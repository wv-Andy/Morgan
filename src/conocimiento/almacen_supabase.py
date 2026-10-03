"""
El conocimiento en la nube (V1.9).

Era lo único de la V1.8 que quedó a medias: `AlmacenDeConocimiento` solo hablaba
con SQLite, así que en la nube las herramientas de conocimiento **ni se
registraban** y la biblioteca solo existía en el Morgan de escritorio.

## Lo que se comparte, y por qué importa

**La puntuación NO se reimplementa aquí.** Se reutiliza `_puntuar` de la versión
local, tal cual. Tener dos implementaciones de la relevancia significaría que la
misma búsqueda ordena distinto según dónde corra Morgan, y eso es peor que no
buscar: quien se acostumbra a que un documento salga el primero deja de mirar
los siguientes.

Lo que sí cambia es el **filtro grueso** —qué fragmentos son candidatos—, porque
esa parte es SQL y hay que decirla en cada dialecto. En Supabase vive en una
función de Postgres, `buscar_conocimiento`: lo que hace falta —un JOIN entre
fragmentos y documentos con un OR de LIKE sobre dos columnas por cada término—
no se puede expresar en la sintaxis de filtros de PostgREST sin retorcerla hasta
hacerla ilegible.

## El aislamiento

Como en el resto de la capa de datos: el usuario se lee del contexto de la
petición, **nunca se recibe por parámetro**. Si fuera un argumento, un método
nuevo podría olvidarlo y seguir compilando.
"""

import json
import logging
import secrets
import time

from src.conocimiento.modelos import Documento
from src.espacios.contexto import espacio_actual
from src.identidad import usuario_actual
from src.memory.db import MemoryStorageError
# Los mismos filtros que el resto de la capa de Supabase, no una copia: `_esc`
# es lo que impide que un identificador cuele un filtro ajeno, y dos copias de
# eso pueden divergir.
from src.memory.supabase_repositories import _de_este_espacio, _esc, _mio

logger = logging.getLogger(__name__)

#: Cuántos candidatos pide el filtro antes de puntuar. El mismo múltiplo que la
#: versión local: pedir justo los que se van a devolver haría que el ranking no
#: tuviera entre qué elegir.
CANDIDATOS_POR_RESULTADO = 5


class AlmacenDeConocimientoSupabase:
    """La misma interfaz que `AlmacenDeConocimiento`, sobre PostgREST."""

    def __init__(self, client):
        self.client = client

    # --- Escritura -----------------------------------------------------------

    def añadir(
        self,
        titulo: str,
        contenido: str,
        fuente: str = "manual",
        coleccion: str = "general",
        metadatos: dict | None = None,
    ) -> Documento:
        """Guarda un documento y lo indexa.

        Si ya existe uno con el mismo título en la misma colección se
        **reemplaza**, igual que en local: volver a añadir el mismo manual
        actualizado es lo que la gente hace, y acumular tres copias con distinto
        contenido convierte la búsqueda en una ruleta.
        """
        if not (titulo or "").strip():
            raise ValueError("Un documento necesita un título.")
        if not (contenido or "").strip():
            raise ValueError("Un documento vacío no aporta nada.")

        from src.conocimiento.almacen import normalizar

        ahora = time.time()
        existente = self._id_por_titulo(titulo.strip(), coleccion)

        documento = Documento(
            id=existente or f"doc-{secrets.token_urlsafe(8)}",
            titulo=titulo.strip()[:200],
            contenido=contenido,
            fuente=(fuente or "manual").strip()[:200],
            coleccion=(coleccion or "general").strip()[:100],
            creado_en=ahora,
            actualizado_en=ahora,
            metadatos=metadatos or {},
        )

        fila = {
            "id": documento.id,
            "titulo": documento.titulo,
            "titulo_norm": normalizar(documento.titulo),
            "contenido": documento.contenido,
            "fuente": documento.fuente,
            "coleccion": documento.coleccion,
            "creado_en": ahora,
            "actualizado_en": ahora,
            "huella": documento.huella,
            "metadatos": documento.metadatos,
            "user_id": usuario_actual(),
            "espacio_id": espacio_actual(),
        }

        # La clave primaria es (user_id, id): nombrar solo una columna da «no
        # unique or exclusion constraint matching», que llega al navegador como
        # un 500 sin explicación. Ya pasó con `sessions`.
        self.client.upsert("conocimiento", [fila], on_conflict="user_id,id")

        # Los fragmentos viejos se borran ANTES de escribir los nuevos. Al
        # revés quedarían mezclados los de las dos versiones del documento, y
        # la búsqueda devolvería párrafos que ya no existen.
        self.client.delete(
            "conocimiento_fragmentos",
            f"documento_id=eq.{_esc(documento.id)}&{_mio()}",
        )

        fragmentos = [
            {
                "documento_id": documento.id,
                "orden": f.orden,
                "texto": f.texto,
                "texto_norm": normalizar(f.texto),
                "user_id": usuario_actual(),
            }
            for f in documento.fragmentar()
        ]
        if fragmentos:
            self.client.insert("conocimiento_fragmentos", fragmentos)

        logger.info("Documento indexado: %s (%s)", documento.titulo, documento.id)
        return documento

    def _id_por_titulo(self, titulo: str, coleccion: str) -> str | None:
        from src.conocimiento.almacen import normalizar

        filas = self.client.select(
            "conocimiento",
            f"titulo_norm=eq.{_esc(normalizar(titulo))}"
            f"&coleccion=eq.{_esc(coleccion or 'general')}"
            f"&{_mio()}&{_de_este_espacio()}&select=id&limit=1",
        )
        return filas[0]["id"] if filas else None

    def eliminar(self, documento_id: str) -> bool:
        """Borra el documento. Sus fragmentos caen con él por la clave foránea."""
        borrados = self.client.delete(
            "conocimiento", f"id=eq.{_esc(documento_id)}&{_mio()}&{_de_este_espacio()}",
        )
        return len(borrados) > 0

    # --- Lectura -------------------------------------------------------------

    def obtener(self, documento_id: str) -> Documento | None:
        filas = self.client.select(
            "conocimiento",
            f"id=eq.{_esc(documento_id)}&{_mio()}&{_de_este_espacio()}&select=*&limit=1",
        )
        return self._a_documento(filas[0]) if filas else None

    def listar(self, coleccion: str | None = None, limite: int = 50) -> list[Documento]:
        filtros = [_mio(), _de_este_espacio(), "select=*", "order=actualizado_en.desc",
                   f"limit={int(limite)}"]
        if coleccion:
            filtros.insert(1, f"coleccion=eq.{_esc(coleccion)}")

        return [
            self._a_documento(f)
            for f in self.client.select("conocimiento", "&".join(filtros))
        ]

    def titulos(self, limite: int = 12) -> list[str]:
        """Los títulos de sus documentos, los más recientes primero, sin el texto (4.1)."""
        filas = self.client.select(
            "conocimiento",
            f"{_mio()}&{_de_este_espacio()}&select=titulo&order=actualizado_en.desc&limit={int(limite)}",
        )
        return [f.get("titulo") or "" for f in filas]

    def colecciones(self) -> list[dict]:
        """Qué colecciones hay y cuántos documentos tiene cada una.

        Se agrupa en Python. PostgREST no da agregados por grupo sin crear una
        vista, y crear una vista para contar unas pocas filas es más pieza de la
        que el problema merece.
        """
        filas = self.client.select(
            "conocimiento", f"{_mio()}&{_de_este_espacio()}&select=coleccion",
        )
        cuenta: dict[str, int] = {}
        for f in filas:
            nombre = f.get("coleccion") or "general"
            cuenta[nombre] = cuenta.get(nombre, 0) + 1

        return [
            {"coleccion": nombre, "documentos": n}
            for nombre, n in sorted(cuenta.items())
        ]

    def fuentes(self) -> list[dict]:
        filas = self.client.select(
            "conocimiento", f"{_mio()}&{_de_este_espacio()}&select=fuente"
        )
        cuenta: dict[str, int] = {}
        for f in filas:
            nombre = f.get("fuente") or "manual"
            cuenta[nombre] = cuenta.get(nombre, 0) + 1

        return [
            {"fuente": nombre, "documentos": n}
            for nombre, n in sorted(cuenta.items())
        ]

    def buscar(
        self,
        consulta: str,
        coleccion: str | None = None,
        limite: int = 5,
    ) -> list[dict]:
        """Los fragmentos que mejor casan, de más a menos.

        El filtro lo hace Postgres; **la puntuación es la misma función que en
        local**. Ver la nota del módulo: dos implementaciones del ranking
        significan que la misma búsqueda ordena distinto según dónde corra
        Morgan.
        """
        from src.conocimiento.almacen import (
            MAX_RESULTADOS,
            AlmacenDeConocimiento,
            raiz_de,
            terminos_de,
        )

        terminos = terminos_de(consulta)
        if not terminos:
            return []

        try:
            filas = self.client.rpc("buscar_conocimiento", {
                "p_user_id": usuario_actual(),
                "p_raices": [raiz_de(t) for t in terminos],
                "p_coleccion": coleccion,
                "p_limite": MAX_RESULTADOS * CANDIDATOS_POR_RESULTADO,
                # None es «General». La función compara con IS NOT DISTINCT FROM
                # para que eso encuentre lo que no tiene espacio.
                "p_espacio_id": espacio_actual(),
            }) or []
        except MemoryStorageError:
            # Buscar en el conocimiento es un extra: si el almacén no responde,
            # Morgan sigue pudiendo contestar con lo que sabe. Devolver nada es
            # mejor que romper el turno entero.
            logger.warning("No se pudo buscar en el conocimiento", exc_info=True)
            return []

        puntuados = [
            (AlmacenDeConocimiento._puntuar(f, terminos), f) for f in filas
        ]
        puntuados.sort(key=lambda p: p[0], reverse=True)

        return [
            {
                "documento_id": f["documento_id"],
                "titulo": f["titulo"],
                "fuente": f["fuente"],
                "coleccion": f["coleccion"],
                "fragmento": f["orden"],
                "texto": f["texto"],
                "relevancia": round(puntos, 2),
            }
            for puntos, f in puntuados[:limite]
            if puntos > 0
        ]

    @staticmethod
    def _a_documento(fila: dict) -> Documento:
        crudos = fila.get("metadatos") or {}
        if isinstance(crudos, str):
            try:
                crudos = json.loads(crudos)
            except (ValueError, TypeError):
                crudos = {}

        return Documento(
            id=fila["id"],
            titulo=fila.get("titulo", ""),
            contenido=fila.get("contenido", ""),
            fuente=fila.get("fuente", "manual"),
            coleccion=fila.get("coleccion", "general"),
            creado_en=fila.get("creado_en", 0.0) or 0.0,
            actualizado_en=fila.get("actualizado_en", 0.0) or 0.0,
            metadatos=crudos,
        )
