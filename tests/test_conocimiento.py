"""
Conocimiento: lo que Morgan consulta, frente a lo que recuerda (V1.8).

La V1.8 separa dos cosas que estaban en la misma caja:

- **Memoria**: cuatro hechos sobre ti que viajan en CADA prompt.
- **Conocimiento**: material largo que se busca cuando viene a cuento.

Mezclarlos engorda el prompt con documentos que casi nunca se usan y diluye lo
que sí importa. Estas pruebas fijan la separación y, sobre todo, que la búsqueda
encuentre lo que está ahí — que es lo único que hace útil a una biblioteca.
"""

import pytest

from src.conocimiento import AlmacenDeConocimiento, Documento
from src.conocimiento.almacen import raiz_de, terminos_de
from src.memory.db import Database

MANUAL = """Configuración del correo en Morgan.

Para que la recuperación de contraseña funcione hace falta un proveedor SMTP o
una API HTTP de envío.

En la nube, Render bloquea los puertos SMTP salientes, así que hay que usar una
API HTTP como Brevo. Con SMTP el envío muere con «Network is unreachable».

Brevo permite verificar una dirección concreta en lugar de un dominio entero, lo
que evita tener que comprar uno."""


@pytest.fixture
def almacen(tmp_path):
    return AlmacenDeConocimiento(Database(tmp_path / "conocimiento.db"))


@pytest.fixture
def con_documentos(almacen):
    almacen.añadir("Configurar el correo", MANUAL, fuente="documentación", coleccion="morgan")
    almacen.añadir(
        "Receta de tortilla",
        "Necesitas huevos, patatas y cebolla si eres de los que la pone.",
        coleccion="personal",
    )
    return almacen


class TestLaBusquedaEncuentraLoQueEstaAhi:
    """Lo único que hace útil a una biblioteca."""

    def test_por_una_palabra_del_texto(self, con_documentos):
        assert con_documentos.buscar("brevo")

    def test_por_una_palabra_solo_del_titulo(self, con_documentos):
        """El fallo que apareció al ejercitarlo: el filtro miraba solo el texto
        del fragmento, pero la puntuación premiaba el título. Un documento
        titulado «Configurar el correo» no salía al buscar «correo» si la palabra
        no estaba también en el cuerpo — el filtro lo descartaba antes de que el
        ranking llegara a verlo."""
        assert con_documentos.buscar("correo")

    def test_aunque_la_palabra_este_conjugada(self, con_documentos):
        """El español conjuga y declina mucho, y quien busca casi nunca escribe
        la forma exacta que hay en el texto."""
        assert con_documentos.buscar("como configuro el correo")

    def test_aunque_falten_las_tildes(self, con_documentos):
        """Quien escribe deprisa en una caja de búsqueda no las pone."""
        assert con_documentos.buscar("configuracion")

    def test_lo_que_no_esta_no_se_inventa(self, con_documentos):
        assert con_documentos.buscar("dinosaurios") == []

    def test_las_palabras_vacias_no_lo_devuelven_todo(self, con_documentos):
        """Sin quitarlas, buscar «cómo se configura el correo» devolvería todo lo
        que contenga «se» o «el»."""
        terminos = terminos_de("como se configura el correo de la que es")

        assert "se" not in terminos
        assert "configura" in terminos

    def test_una_busqueda_solo_de_palabras_vacias_no_revienta(self, con_documentos):
        """Mejor un resultado malo que una excepción cuando alguien busca
        literalmente «por que»."""
        con_documentos.buscar("por que")


class TestElOrdenDeLosResultados:
    def test_el_titulo_pesa_mas_que_el_cuerpo(self, almacen):
        """Si alguien tituló un documento «Configurar el correo», ese documento
        es sobre eso."""
        almacen.añadir("Configurar el correo", "Instrucciones detalladas aquí.")
        almacen.añadir("Otras cosas", "Una vez mencioné el correo de pasada.")

        resultados = almacen.buscar("correo")

        assert resultados[0]["titulo"] == "Configurar el correo"

    def test_traer_mas_terminos_gana(self, almacen):
        almacen.añadir("Uno", "Aquí se habla de Brevo y nada más.")
        almacen.añadir("Dos", "Aquí se habla de Brevo, de SMTP y de Render.")

        resultados = almacen.buscar("brevo smtp render")

        assert resultados[0]["titulo"] == "Dos"

    def test_repetir_una_palabra_no_multiplica_la_relevancia(self, almacen):
        """Un fragmento que repite la palabra veinte veces no es veinte veces
        mejor: sería la forma más fácil de envenenar el ranking."""
        almacen.añadir("Repetitivo", "brevo " * 50)
        almacen.añadir("Completo", "Brevo se configura con SMTP en Render.")

        resultados = almacen.buscar("brevo smtp render")

        assert resultados[0]["titulo"] == "Completo"

    def test_se_respeta_el_limite(self, almacen):
        for i in range(10):
            almacen.añadir(f"Doc {i}", "todos hablan de brevo")

        assert len(almacen.buscar("brevo", limite=3)) == 3


class TestLosFragmentos:
    """Un documento entero no sirve como unidad de búsqueda: si la respuesta está
    en el párrafo 40 de 200, dar el documento completo llena el contexto de lo que
    no hacía falta."""

    def test_un_texto_corto_es_un_solo_fragmento(self):
        documento = Documento(id="d1", titulo="Corto", contenido="Dos frases. Nada más.")

        assert len(documento.fragmentar()) == 1

    def test_uno_largo_se_parte(self):
        parrafo = "Un párrafo con bastante texto dentro. " * 20
        documento = Documento(id="d1", titulo="Largo", contenido="\n\n".join([parrafo] * 5))

        assert len(documento.fragmentar()) > 1

    def test_los_fragmentos_se_solapan(self):
        """Sin solape, una frase que cae en la frontera queda partida entre dos
        fragmentos y no se encuentra en ninguno: media respuesta a cada lado."""
        parrafo = "Texto de relleno para llegar al límite del fragmento. " * 15
        documento = Documento(id="d1", titulo="X", contenido="\n\n".join([parrafo] * 4))

        fragmentos = documento.fragmentar()
        assert len(fragmentos) >= 2

        # El principio del segundo tiene algo del final del primero.
        final_del_primero = fragmentos[0].texto[-50:]
        assert any(
            trozo in fragmentos[1].texto
            for trozo in (final_del_primero[:20], final_del_primero[-20:])
        )

    def test_un_documento_vacio_no_da_fragmentos(self):
        assert Documento(id="d1", titulo="Vacío", contenido="   ").fragmentar() == []

    def test_cada_fragmento_sabe_de_que_documento_es(self):
        """Sin eso, una respuesta basada en conocimiento no se puede comprobar."""
        documento = Documento(id="d1", titulo="Mi manual", contenido="algo de texto")

        assert documento.fragmentar()[0].titulo_documento == "Mi manual"


class TestGuardarYReemplazar:
    def test_se_guarda_y_se_recupera(self, almacen):
        creado = almacen.añadir("Notas", "El contenido entero.")

        recuperado = almacen.obtener(creado.id)

        assert recuperado.contenido == "El contenido entero."

    def test_el_mismo_titulo_reemplaza_en_vez_de_duplicar(self, almacen):
        """Volver a añadir el manual actualizado es lo que la gente hace, y
        acumular tres copias con distinto contenido convierte la búsqueda en una
        ruleta."""
        almacen.añadir("Manual", "Versión vieja.", coleccion="x")
        almacen.añadir("Manual", "Versión nueva.", coleccion="x")

        documentos = almacen.listar(coleccion="x")

        assert len(documentos) == 1
        assert documentos[0].contenido == "Versión nueva."

    def test_reemplazar_no_deja_fragmentos_huerfanos(self, almacen):
        """Si no se borran, la búsqueda seguiría encontrando la versión vieja."""
        almacen.añadir("Manual", "Aquí hablaba de zanahorias.", coleccion="x")
        almacen.añadir("Manual", "Ahora hablo de tomates.", coleccion="x")

        assert almacen.buscar("zanahorias") == []
        assert almacen.buscar("tomates")

    def test_el_mismo_titulo_en_otra_coleccion_es_otro_documento(self, almacen):
        almacen.añadir("Notas", "Las del trabajo.", coleccion="trabajo")
        almacen.añadir("Notas", "Las de casa.", coleccion="casa")

        assert len(almacen.listar()) == 2

    def test_un_documento_sin_titulo_se_rechaza(self, almacen):
        with pytest.raises(ValueError):
            almacen.añadir("  ", "contenido")

    def test_un_documento_vacio_se_rechaza(self, almacen):
        """No aporta nada y ensucia los listados."""
        with pytest.raises(ValueError):
            almacen.añadir("Título", "   ")

    def test_eliminar_lo_quita_de_la_busqueda(self, con_documentos):
        documento = con_documentos.listar(coleccion="personal")[0]

        assert con_documentos.eliminar(documento.id) is True
        assert con_documentos.buscar("tortilla") == []

    def test_eliminar_algo_que_no_existe_devuelve_falso(self, almacen):
        assert almacen.eliminar("no-existe") is False


class TestLasColecciones:
    def test_se_puede_buscar_dentro_de_una(self, con_documentos):
        """Buscar «receta» en la colección de Morgan no debe traer la tortilla."""
        assert con_documentos.buscar("tortilla", coleccion="morgan") == []
        assert con_documentos.buscar("tortilla", coleccion="personal")

    def test_se_listan_con_su_recuento(self, con_documentos):
        colecciones = {c["coleccion"]: c["documentos"] for c in con_documentos.colecciones()}

        assert colecciones == {"morgan": 1, "personal": 1}

    def test_las_fuentes_tambien(self, con_documentos):
        fuentes = {f["fuente"] for f in con_documentos.fuentes()}

        assert "documentación" in fuentes


class TestCadaUnoConsultaLoSuyo:
    def test_el_conocimiento_de_otro_no_se_busca(self, almacen):
        from src.identidad import como_usuario

        with como_usuario("ana"):
            almacen.añadir("Privado de Ana", "Sus contraseñas y sus cosas.")

        with como_usuario("bruno"):
            assert almacen.buscar("contraseñas") == []
            assert almacen.listar() == []

    def test_ni_se_borra(self, almacen):
        from src.identidad import como_usuario

        with como_usuario("ana"):
            documento = almacen.añadir("De Ana", "contenido")

        with como_usuario("bruno"):
            assert almacen.eliminar(documento.id) is False

        with como_usuario("ana"):
            assert almacen.obtener(documento.id) is not None


class TestLasHerramientas:
    @pytest.fixture
    def herramientas(self, con_documentos):
        from src.tools.conocimiento import knowledge_tools

        return {h.name: h for h in knowledge_tools(con_documentos)}

    def test_buscar_devuelve_los_fragmentos_con_su_origen(self, herramientas):
        datos = herramientas["search_knowledge"].execute(consulta="brevo")["data"]

        assert datos["encontrados"] > 0
        assert datos["resultados"][0]["titulo"]
        assert "Cita de qué documento" in datos["nota"]

    def test_sin_resultados_se_aclara_que_no_es_un_hecho_del_mundo(self, herramientas):
        """Sin esa aclaración, el modelo tiende a responder «no tienes
        información sobre eso» como si fuera una verdad universal."""
        datos = herramientas["search_knowledge"].execute(consulta="dinosaurios")["data"]

        assert datos["encontrados"] == 0
        assert "no significa que la respuesta no exista" in datos["nota"].lower()

    def test_buscar_sin_consulta_se_rechaza(self, herramientas):
        assert herramientas["search_knowledge"].execute(consulta="  ")["success"] is False

    def test_anadir_dice_en_cuantos_trozos_quedo(self, herramientas):
        datos = herramientas["add_knowledge"].execute(
            titulo="Nuevo", contenido="Un texto cualquiera."
        )["data"]

        assert datos["fragmentos"] >= 1

    def test_la_descripcion_distingue_conocimiento_de_memoria(self, herramientas):
        """Es lo único que impide que el modelo guarde «prefiero español» como
        documento, o un manual de 40 páginas como hecho."""
        descripcion = herramientas["add_knowledge"].description.lower()

        assert "remember_fact" in descripcion

    def test_borrar_pide_confirmacion(self, herramientas):
        """Se pierde el contenido, no solo el índice."""
        assert herramientas["remove_knowledge"].permission_level == "moderate"

    def test_buscar_no_pide_confirmacion(self, herramientas):
        assert herramientas["search_knowledge"].permission_level == "safe"

    def test_todas_existen_en_la_nube(self, herramientas):
        """Trabajan sobre la base, no sobre el disco."""
        assert all(h.requires_local is False for h in herramientas.values())


class TestLaRaizDeLasPalabras:
    @pytest.mark.parametrize(
        "palabra, raiz",
        [
            ("configuro", "configu"),
            ("configuracion", "configuraci"),
            ("correo", "corr"),
            ("smtp", "smtp"),
            ("api", "api"),
        ],
    )
    def test_solo_se_recorta_lo_largo(self, palabra, raiz):
        """Recortar palabras cortas las convertiría en cualquier cosa: «api» →
        «a» casaría con medio diccionario."""
        assert raiz_de(palabra) == raiz
