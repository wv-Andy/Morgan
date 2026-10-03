"""
Dónde vive el conocimiento y cómo se busca (V1.8).

## Cómo se busca

Por **términos**, puntuando cada fragmento por cuántos aparecen y dónde. No es
búsqueda semántica: «coche» no encuentra «automóvil».

Es una decisión, no una carencia por descuido. La alternativa —*embeddings*—
exigiría un modelo de vectores, una llamada por fragmento al indexar, y un almacén
vectorial. Traería mejores resultados y también una dependencia que hay que pagar,
mantener y que falla cuando el proveedor no responde. Para consultar tus propias
notas, buscar por palabras funciona; cuando se quede corto, se sabrá porque las
búsquedas empezarán a no encontrar lo que está ahí.

Lo que sí se hace bien es **puntuar**: un término en el título vale más que en el
cuerpo, y un fragmento con tres de los cuatro términos va antes que uno con uno.
Sin puntuación, «buscar» sería devolver todo lo que contenga cualquier palabra, y
eso es peor que no buscar.

## Por qué el almacén está aquí y no en `src/memory/`

Porque el conocimiento **no es memoria**, y ponerlos juntos volvería a mezclar lo
que la V1.8 vino a separar. Usa la misma base y el mismo patrón de repositorio,
pero es otra cosa: la memoria viaja en cada prompt, el conocimiento se consulta.
"""

import json
import logging
import re
import secrets
import time
import unicodedata

from src.conocimiento.modelos import Documento
from src.espacios.contexto import espacio_actual
from src.identidad import usuario_actual

logger = logging.getLogger(__name__)

# Palabras que aparecen en todo y no distinguen nada. Sin quitarlas, buscar
# «cómo se configura el correo» devuelve todo lo que contenga «se» o «el».
VACIAS = {
    "a", "al", "algo", "ante", "con", "como", "cual", "cuando", "de", "del",
    "desde", "donde", "el", "en", "es", "esa", "ese", "esta", "este", "esto",
    "hay", "la", "las", "lo", "los", "mas", "me", "mi", "muy", "no", "o",
    "para", "pero", "por", "que", "se", "si", "sin", "sobre", "su", "sus",
    "te", "tu", "un", "una", "uno", "y", "ya",
}

MIN_LONGITUD_TERMINO = 2
MAX_RESULTADOS = 20

# A partir de esta longitud se busca por la raiz en lugar de por la palabra
# entera. El espanol conjuga y declina mucho: sin esto, "configuro" no encuentra
# "configurar" ni "configuracion", y quien busca casi nunca escribe la forma
# exacta que hay en el texto.
#
# Es un stemming tosco —cortar dos letras— y a veces junta palabras que no
# comparten raiz. Se acepta: el coste de un falso positivo en una busqueda es
# leerlo y descartarlo; el de un falso negativo es no encontrar lo que si estaba.
LONGITUD_PARA_RAIZ = 6
LETRAS_A_RECORTAR = 2


def raiz_de(termino: str) -> str:
    """La parte del término que se usa para buscar."""
    if len(termino) >= LONGITUD_PARA_RAIZ:
        return termino[:-LETRAS_A_RECORTAR]
    return termino


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes.

    Sin esto, «configuración» no encuentra «configuracion», y quien escribe
    deprisa en una caja de búsqueda casi nunca pone las tildes.
    """
    sin_tildes = unicodedata.normalize("NFD", texto or "")
    sin_tildes = "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")
    return sin_tildes.lower()


def terminos_de(consulta: str) -> list[str]:
    """Las palabras que de verdad discriminan."""
    palabras = re.findall(r"[a-z0-9_]+", normalizar(consulta))
    utiles = [
        p for p in palabras if len(p) >= MIN_LONGITUD_TERMINO and p not in VACIAS
    ]

    # Si todo eran palabras vacias, se buscan igualmente: mejor un resultado
    # malo que ninguno cuando alguien busca literalmente "por que".
    return utiles or palabras


class AlmacenDeConocimiento:
    """Guarda, busca y borra documentos, con sus fragmentos."""

    def __init__(self, db):
        self.db = db

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

        Si ya existe uno con el mismo título en la misma colección, se
        **reemplaza**: volver a añadir el mismo manual actualizado es lo que la
        gente hace, y acumular tres copias con distinto contenido convierte la
        búsqueda en una ruleta.
        """
        if not (titulo or "").strip():
            raise ValueError("Un documento necesita un título.")
        if not (contenido or "").strip():
            raise ValueError("Un documento vacío no aporta nada.")

        ahora = time.time()
        existente = self._buscar_por_titulo(titulo.strip(), coleccion)

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

        with self.db.connect() as conn:
            if existente:
                conn.execute(
                    "DELETE FROM conocimiento_fragmentos "
                    "WHERE documento_id = ? AND user_id = ?",
                    (documento.id, usuario_actual()),
                )
                conn.execute(
                    "UPDATE conocimiento SET contenido = ?, fuente = ?, "
                    "actualizado_en = ?, huella = ?, metadatos = ? "
                    "WHERE id = ? AND user_id = ? AND espacio_id IS ?",
                    (documento.contenido, documento.fuente, ahora, documento.huella,
                     json.dumps(documento.metadatos, ensure_ascii=False),
                     documento.id, usuario_actual(), espacio_actual()),
                )
            else:
                conn.execute(
                    "INSERT INTO conocimiento (id, titulo, titulo_norm, contenido, "
                    "fuente, coleccion, creado_en, actualizado_en, huella, metadatos, "
                    "user_id, espacio_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (documento.id, documento.titulo, normalizar(documento.titulo),
                     documento.contenido,
                     documento.fuente, documento.coleccion, ahora, ahora,
                     documento.huella,
                     json.dumps(documento.metadatos, ensure_ascii=False),
                     usuario_actual(), espacio_actual()),
                )

            for fragmento in documento.fragmentar():
                conn.execute(
                    "INSERT INTO conocimiento_fragmentos "
                    "(documento_id, orden, texto, texto_norm, user_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (documento.id, fragmento.orden, fragmento.texto,
                     normalizar(fragmento.texto), usuario_actual()),
                )

        logger.info("Documento indexado: %s (%s)", documento.titulo, documento.id)
        return documento

    def _buscar_por_titulo(self, titulo: str, coleccion: str) -> str | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                # Dentro del espacio: el mismo título en dos proyectos son dos
                # documentos, no uno que se reemplaza.
                "SELECT id FROM conocimiento "
                "WHERE titulo = ? AND coleccion = ? AND user_id = ? AND espacio_id IS ?",
                (titulo, coleccion, usuario_actual(), espacio_actual()),
            ).fetchone()
        return fila["id"] if fila else None

    def eliminar(self, documento_id: str) -> bool:
        # Primero se comprueba que el documento está en ESTE espacio. Los
        # fragmentos no llevan espacio, así que borrarlos por su documento sin
        # mirar antes habría vaciado un documento de otro proyecto dejando su
        # ficha en pie.
        if self.obtener(documento_id) is None:
            return False
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM conocimiento_fragmentos "
                "WHERE documento_id = ? AND user_id = ?",
                (documento_id, usuario_actual()),
            )
            return conn.execute(
                "DELETE FROM conocimiento WHERE id = ? AND user_id = ? AND espacio_id IS ?",
                (documento_id, usuario_actual(), espacio_actual()),
            ).rowcount > 0

    # --- Lectura -------------------------------------------------------------

    def obtener(self, documento_id: str) -> Documento | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT * FROM conocimiento WHERE id = ? AND user_id = ? AND espacio_id IS ?",
                (documento_id, usuario_actual(), espacio_actual()),
            ).fetchone()
        return self._a_documento(fila) if fila else None

    def listar(self, coleccion: str | None = None, limite: int = 50) -> list[Documento]:
        condiciones = ["user_id = ?", "espacio_id IS ?"]
        parametros: list = [usuario_actual(), espacio_actual()]

        if coleccion:
            condiciones.append("coleccion = ?")
            parametros.append(coleccion)

        parametros.append(limite)

        with self.db.connect() as conn:
            filas = conn.execute(
                f"SELECT * FROM conocimiento WHERE {' AND '.join(condiciones)} "
                "ORDER BY actualizado_en DESC LIMIT ?",
                parametros,
            ).fetchall()

        return [self._a_documento(f) for f in filas]

    def titulos(self, limite: int = 12) -> list[str]:
        """Los títulos de sus documentos, los más recientes primero, sin el texto (4.1)."""
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT titulo FROM conocimiento WHERE user_id = ? AND espacio_id IS ? "
                "ORDER BY actualizado_en DESC LIMIT ?",
                (usuario_actual(), espacio_actual(), int(limite)),
            ).fetchall()
        return [f["titulo"] for f in filas]

    def colecciones(self) -> list[dict]:
        """Las colecciones que hay, con cuántos documentos tiene cada una."""
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT coleccion, COUNT(*) AS documentos FROM conocimiento "
                "WHERE user_id = ? AND espacio_id IS ? "
                "GROUP BY coleccion ORDER BY documentos DESC",
                (usuario_actual(), espacio_actual()),
            ).fetchall()

        return [{"coleccion": f["coleccion"], "documentos": f["documentos"]} for f in filas]

    def fuentes(self) -> list[dict]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT fuente, COUNT(*) AS documentos FROM conocimiento "
                "WHERE user_id = ? AND espacio_id IS ? "
                "GROUP BY fuente ORDER BY documentos DESC",
                (usuario_actual(), espacio_actual()),
            ).fetchall()

        return [{"fuente": f["fuente"], "documentos": f["documentos"]} for f in filas]

    # --- Búsqueda ------------------------------------------------------------

    def buscar(
        self,
        consulta: str,
        coleccion: str | None = None,
        limite: int = 5,
    ) -> list[dict]:
        """Los fragmentos que mejor casan, de más a menos.

        Devuelve fragmentos y no documentos a propósito: dar el manual entero
        cuando la respuesta está en el párrafo 40 es tan inútil como no dar nada.
        """
        terminos = terminos_de(consulta)
        if not terminos:
            return []

        condiciones = ["f.user_id = ?", "d.espacio_id IS ?"]
        parametros: list = [usuario_actual(), espacio_actual()]

        # Al menos uno de los terminos: filtrar por TODOS dejaria sin resultados
        # cualquier busqueda de mas de dos palabras. El ranking se encarga de
        # poner arriba los que traen mas.
        #
        # Se mira tambien el TITULO. Sin eso, un documento titulado "Configurar
        # el correo" no aparecia al buscar "correo" si la palabra solo estaba en
        # el titulo: el filtro lo descartaba antes de que el ranking, que si
        # premia el titulo, llegara a verlo. Filtro y ranking tienen que mirar lo
        # mismo o el segundo nunca puntua lo que el primero tira.
        raices = [raiz_de(t) for t in terminos]
        condiciones.append(
            "(" + " OR ".join(
                "f.texto_norm LIKE ? OR d.titulo_norm LIKE ?" for _ in raices
            ) + ")"
        )
        for raiz in raices:
            parametros.extend([f"%{raiz}%", f"%{raiz}%"])

        if coleccion:
            condiciones.append("d.coleccion = ?")
            parametros.append(coleccion)

        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT f.documento_id, f.orden, f.texto, f.texto_norm, "
                "       d.titulo, d.titulo_norm, d.fuente, d.coleccion "
                "FROM conocimiento_fragmentos f "
                "JOIN conocimiento d ON d.id = f.documento_id AND d.user_id = f.user_id "
                f"WHERE {' AND '.join(condiciones)} "
                "LIMIT ?",
                (*parametros, MAX_RESULTADOS * 5),
            ).fetchall()

        puntuados = [
            (self._puntuar(f, terminos), f) for f in filas
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
    def _puntuar(fila, terminos: list[str]) -> float:
        """Cuánto casa un fragmento con lo que se busca.

        Sin puntuar, «buscar» sería devolver todo lo que contenga cualquier
        palabra, que es peor que no buscar.
        """
        texto = fila["texto_norm"] or ""
        titulo = fila["titulo_norm"] or ""

        puntos = 0.0
        encontrados = 0

        for termino in terminos:
            raiz = raiz_de(termino)
            apariciones = texto.count(raiz)
            en_titulo = raiz in titulo

            if apariciones:
                # Las apariciones suman, pero cada vez menos: un fragmento que
                # repite la palabra veinte veces no es veinte veces mejor.
                puntos += 1 + min(apariciones - 1, 4) * 0.2

            if en_titulo:
                # El titulo pesa mas: si alguien tituló un documento "Configurar
                # el correo", ese documento es sobre eso.
                puntos += 2

            # La cobertura cuenta el termino si aparece en CUALQUIERA de los dos.
            # Contando solo el texto, un termino que estaba unicamente en el
            # titulo daba cobertura cero, y el multiplicador castigaba justo lo
            # que la linea de arriba acababa de premiar: el documento titulado
            # "Configurar el correo" salia DEBAJO de otro que mencionaba la
            # palabra de pasada. Lo cazo una prueba.
            if apariciones or en_titulo:
                encontrados += 1

        # Traer casi todos los terminos vale mucho mas que traer uno: es la
        # diferencia entre responder la pregunta y mencionar una palabra suelta.
        cobertura = encontrados / len(terminos)
        return puntos * (0.5 + cobertura)

    @staticmethod
    def _a_documento(fila) -> Documento:
        try:
            metadatos = json.loads(fila["metadatos"] or "{}")
        except (ValueError, TypeError):
            metadatos = {}

        return Documento(
            id=fila["id"],
            titulo=fila["titulo"],
            contenido=fila["contenido"],
            fuente=fila["fuente"],
            coleccion=fila["coleccion"],
            creado_en=fila["creado_en"] or 0.0,
            actualizado_en=fila["actualizado_en"] or 0.0,
            metadatos=metadatos,
        )
