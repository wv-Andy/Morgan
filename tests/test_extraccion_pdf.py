"""
Sacar el texto de un PDF, y decir la verdad cuando no se puede.

**El hueco que cierran.** `src/uploads/extraccion.py` estaba al 75%, y lo que
faltaba era el cuerpo entero de la lectura de PDF: el recorte a cien páginas, el
corte por tiempo, y la distinción entre «este PDF no tiene texto» y «este PDF
está roto».

Esa distinción es lo que más importa de este módulo. Un PDF escaneado no tiene
texto que extraer, solo imágenes, y devolver una cadena vacía haría que Morgan
dijera que no encuentra nada en un documento que la persona está viendo con sus
propios ojos. Decir «parece escaneado, haría falta reconocimiento óptico» es una
respuesta que se entiende y que se puede actuar.

Los PDF se construyen aquí, en el propio fichero de pruebas, y por dos razones.
Un binario guardado en el repositorio no se puede leer en una revisión: nadie
sabe qué contiene sin abrirlo con una herramienta. Y escribirlos a mano evita
añadir una dependencia —`reportlab`— solo para las pruebas: son treinta líneas
de sintaxis PDF, y así el caso que se prueba está a la vista.
"""

import io

import pytest

from src.uploads.extraccion import (
    MAX_PAGINAS,
    ExtraccionFallida,
    extraer,
    extraer_texto_pdf,
    extraer_texto_plano,
)


def pdf_con(textos: list[str]) -> bytes:
    """Un PDF de verdad, con una página y un texto por cada entrada.

    Se escribe la sintaxis a mano, incluida la tabla `xref`. Sin ella `pypdf`
    responde «startxref not found» y se rechaza el fichero: es lo que separa un
    PDF de un montón de objetos sueltos.
    """
    total = len(textos)
    ids_pagina = [4 + i * 2 for i in range(total)]

    objetos: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{i} 0 R" for i in ids_pagina)
            + f"] /Count {total} >>"
        ),
        3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, texto in enumerate(textos):
        id_pagina, id_flujo = ids_pagina[i], ids_pagina[i] + 1
        objetos[id_pagina] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {id_flujo} 0 R >>"
        )
        flujo = f"BT /F1 12 Tf 20 200 Td ({texto}) Tj ET"
        objetos[id_flujo] = f"<< /Length {len(flujo)} >>\nstream\n{flujo}\nendstream"

    salida = bytearray(b"%PDF-1.4\n")
    posiciones: dict[int, int] = {}
    for numero in sorted(objetos):
        posiciones[numero] = len(salida)
        salida += f"{numero} 0 obj\n{objetos[numero]}\nendobj\n".encode("latin-1")

    inicio_xref = len(salida)
    ultimo = max(objetos) + 1
    salida += f"xref\n0 {ultimo}\n".encode("latin-1")
    salida += b"0000000000 65535 f \n"
    for numero in range(1, ultimo):
        if numero in posiciones:
            salida += f"{posiciones[numero]:010d} 00000 n \n".encode("latin-1")
        else:
            salida += b"0000000000 65535 f \n"
    salida += (
        f"trailer\n<< /Size {ultimo} /Root 1 0 R >>\n"
        f"startxref\n{inicio_xref}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(salida)


def pdf_sin_texto(paginas: int = 1) -> bytes:
    """Un PDF con páginas en blanco: es lo que parece un escaneado."""
    from pypdf import PdfWriter

    escritor = PdfWriter()
    for _ in range(paginas):
        escritor.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    escritor.write(buffer)
    return buffer.getvalue()


class TestElTextoPlano:
    def test_utf8(self):
        assert extraer_texto_plano("hola ñandú".encode("utf-8")) == "hola ñandú"

    def test_utf16(self):
        assert "hola" in extraer_texto_plano("hola".encode("utf-16"))

    def test_latin1(self):
        """Los ficheros que vienen de Windows suelen llegar así."""
        assert "ñ" in extraer_texto_plano("señor".encode("latin-1"))

    def test_bytes_que_no_son_texto_en_ninguna_codificacion(self):
        """`latin-1` acepta cualquier byte, así que en la práctica no se llega
        al último recurso. Se comprueba que no reviente de todos modos: un byte
        suelto mal codificado no puede impedir leer un fichero de diez mil
        líneas.
        """
        resultado = extraer_texto_plano(bytes([0x00, 0xFF, 0xFE, 0x41]))

        assert isinstance(resultado, str)
        assert resultado != ""


class TestUnPdfEscaneadoNoEsUnPdfRoto:
    """Es la diferencia que decide qué le dice Morgan a la persona."""

    def test_sin_texto_se_dice_que_parece_escaneado(self):
        with pytest.raises(ExtraccionFallida) as fallo:
            extraer_texto_pdf(pdf_sin_texto())

        mensaje = str(fallo.value).lower()
        assert "escaneado" in mensaje, (
            "Se ha dicho que el PDF está roto cuando lo que pasa es que no "
            "tiene texto. La persona lo está viendo en pantalla, así que ese "
            "mensaje no se sostiene"
        )
        assert "reconocimiento" in mensaje, (
            "No se dice qué haría falta, así que no hay nada que hacer con la "
            "respuesta"
        )

    def test_roto_se_dice_que_esta_roto(self):
        with pytest.raises(ExtraccionFallida) as fallo:
            extraer_texto_pdf(b"esto no es un PDF ni de lejos")

        mensaje = str(fallo.value).lower()
        assert "no he podido leer" in mensaje
        assert "escaneado" not in mensaje

    def test_un_fichero_vacio_no_se_confunde_con_uno_escaneado(self):
        with pytest.raises(ExtraccionFallida):
            extraer_texto_pdf(b"")


class TestLoQueSeLeeDeVerdad:
    def test_una_pagina_con_texto(self):
        texto = extraer_texto_pdf(pdf_con(["Hola desde el PDF"]))

        assert "Hola desde el PDF" in texto

    def test_varias_paginas_se_juntan(self):
        texto = extraer_texto_pdf(pdf_con(["Primera pagina", "Segunda pagina"]))

        assert "Primera pagina" in texto
        assert "Segunda pagina" in texto

    def test_las_paginas_en_blanco_no_dejan_huecos(self):
        """Un PDF con una página en blanco en medio no puede devolver tres
        saltos de línea seguidos: el modelo los lee como estructura.
        """
        from pypdf import PdfReader, PdfWriter

        escritor = PdfWriter()
        escritor.append(PdfReader(io.BytesIO(pdf_con(["Uno"]))))
        escritor.append(PdfReader(io.BytesIO(pdf_sin_texto())))
        escritor.append(PdfReader(io.BytesIO(pdf_con(["Dos"]))))
        buffer = io.BytesIO()
        escritor.write(buffer)

        texto = extraer_texto_pdf(buffer.getvalue())

        assert "Uno" in texto and "Dos" in texto
        assert "\n\n\n" not in texto


class TestNingunDocumentoOcupaElHiloParaSiempre:
    def test_por_encima_de_cien_paginas_se_recorta_y_se_avisa(self):
        """Sin el aviso, un documento de mil páginas parecería tener cien y
        Morgan resumiría la décima parte dándola por el todo.
        """
        total = MAX_PAGINAS + 5
        texto = extraer_texto_pdf(pdf_con([f"pagina {i}" for i in range(total)]))

        assert f"{MAX_PAGINAS} de {total}" in texto, (
            "Se han leído solo las primeras páginas sin decirlo"
        )
        assert "pagina 0" in texto

    def test_las_cien_primeras_no_llevan_aviso(self):
        texto = extraer_texto_pdf(pdf_con([f"pagina {i}" for i in range(3)]))

        assert "Se leyeron" not in texto

    def test_el_tiempo_se_comprueba_entre_paginas(self, monkeypatch):
        """`pypdf` es síncrono y no se puede interrumpir a mitad de una página,
        pero cortar entre ellas evita que un documento hostil se quede un hilo.

        Se fuerza poniendo el tope a cero: la primera comprobación ya salta.
        """
        import src.uploads.extraccion as modulo

        monkeypatch.setattr(modulo, "SEGUNDOS_MAXIMOS", -1)

        texto = extraer_texto_pdf(pdf_con(["una", "dos", "tres"]))

        assert "tiempo de extracción" in texto.lower(), (
            "Se ha leído el documento entero pese a haberse pasado el tope"
        )


class TestLaPuertaDeEntrada:
    """`extraer()` elige por el tipo del fichero. Que elija mal significa
    intentar leer un PNG como texto, o al revés.
    """

    def test_un_pdf_va_al_lector_de_pdf(self):
        texto = extraer(pdf_con(["Contenido"]), "application/pdf", "doc.pdf")

        assert "Contenido" in texto

    def test_un_texto_va_al_lector_de_texto(self):
        assert "hola" in extraer(b"hola", "text/plain", "nota.txt")

    def test_un_tipo_que_no_se_sabe_leer_lo_dice(self):
        with pytest.raises(ExtraccionFallida):
            extraer(b"\x89PNG\r\n", "image/png", "foto.png")
