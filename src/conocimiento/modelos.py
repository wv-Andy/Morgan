"""
Qué es un documento de conocimiento y en qué se parte (V1.8).

Un documento entero no sirve como unidad de búsqueda. Si alguien pregunta «¿cómo se
configura el correo?» y la respuesta está en el párrafo 40 de un manual de 200, dar
el manual entero es tan inútil como no dar nada: el modelo tiene que leerlo todo y
el contexto se llena de lo que no hacía falta.

Por eso el documento se parte en **fragmentos**, y lo que se busca son fragmentos.
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

# Cuánto ocupa un fragmento. Ni tan corto que pierda el contexto de su propia
# frase, ni tan largo que traiga media página de ruido con la respuesta.
TAMANO_FRAGMENTO = 1200
SOLAPE = 150


@dataclass
class Fragmento:
    """Un trozo buscable de un documento."""

    documento_id: str
    orden: int
    texto: str
    # De dónde salió dentro del documento, para poder citarlo. Sin esto, una
    # respuesta basada en conocimiento no se puede comprobar.
    titulo_documento: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "documento_id": self.documento_id,
            "orden": self.orden,
            "texto": self.texto,
            "titulo_documento": self.titulo_documento,
        }


@dataclass
class Documento:
    """Algo que Morgan puede consultar: unas notas, un manual, una página."""

    id: str
    titulo: str
    contenido: str
    # De dónde viene: una URL, un archivo subido, «escrito a mano». Importa para
    # poder decir «esto lo sé porque está en tal sitio».
    fuente: str = "manual"
    # Agrupa documentos relacionados, para poder buscar solo dentro de un tema.
    coleccion: str = "general"
    creado_en: float = field(default_factory=time.time)
    actualizado_en: float = field(default_factory=time.time)
    metadatos: dict[str, Any] = field(default_factory=dict)

    @property
    def huella(self) -> str:
        """Identifica el contenido, no el documento.

        Sirve para no reindexar lo que no ha cambiado: reindexar es partir y
        volver a escribir todos los fragmentos, y hacerlo cada vez que alguien
        vuelve a añadir el mismo manual es trabajo tirado.
        """
        return hashlib.sha256(self.contenido.encode("utf-8")).hexdigest()[:16]

    @property
    def resumen(self) -> str:
        """Las primeras líneas, para listar sin traerse el documento entero."""
        limpio = " ".join(self.contenido.split())
        return limpio[:200] + ("..." if len(limpio) > 200 else "")

    def fragmentar(self) -> list[Fragmento]:
        """Parte el documento en trozos que se solapan.

        **El solape no es un descuido.** Sin él, una frase que cae justo en la
        frontera queda partida entre dos fragmentos y no se encuentra en ninguno:
        la mitad de la respuesta en cada lado, y ninguno de los dos casa con la
        búsqueda.

        Se parte por párrafos siempre que se pueda. Cortar por número de
        caracteres a secas rompe frases por la mitad, y un fragmento que empieza
        en «...ción del correo hay que» no le dice nada a nadie.
        """
        texto = self.contenido.strip()
        if not texto:
            return []

        if len(texto) <= TAMANO_FRAGMENTO:
            return [Fragmento(self.id, 0, texto, self.titulo)]

        fragmentos: list[Fragmento] = []
        parrafos = [p for p in texto.split("\n\n") if p.strip()]
        actual = ""

        for parrafo in parrafos:
            if len(actual) + len(parrafo) + 2 <= TAMANO_FRAGMENTO:
                actual = f"{actual}\n\n{parrafo}" if actual else parrafo
                continue

            if actual:
                fragmentos.append(
                    Fragmento(self.id, len(fragmentos), actual.strip(), self.titulo)
                )
                # El solape arrastra el final del fragmento anterior.
                actual = actual[-SOLAPE:] + "\n\n" + parrafo
            else:
                actual = parrafo

            # Un parrafo mas largo que el tamano maximo se parte a lo bruto: no
            # hay frontera natural donde cortarlo.
            while len(actual) > TAMANO_FRAGMENTO:
                fragmentos.append(
                    Fragmento(
                        self.id, len(fragmentos), actual[:TAMANO_FRAGMENTO], self.titulo
                    )
                )
                actual = actual[TAMANO_FRAGMENTO - SOLAPE:]

        if actual.strip():
            fragmentos.append(
                Fragmento(self.id, len(fragmentos), actual.strip(), self.titulo)
            )

        return fragmentos

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "titulo": self.titulo,
            "fuente": self.fuente,
            "coleccion": self.coleccion,
            "resumen": self.resumen,
            "tamano": len(self.contenido),
            "creado_en": self.creado_en,
            "actualizado_en": self.actualizado_en,
            "metadatos": self.metadatos,
        }

    @classmethod
    def from_dict(cls, datos: dict[str, Any]) -> "Documento":
        return cls(
            id=datos["id"],
            titulo=datos.get("titulo", ""),
            contenido=datos.get("contenido", ""),
            fuente=datos.get("fuente", "manual"),
            coleccion=datos.get("coleccion", "general"),
            creado_en=datos.get("creado_en") or 0.0,
            actualizado_en=datos.get("actualizado_en") or 0.0,
            metadatos=datos.get("metadatos") or {},
        )
