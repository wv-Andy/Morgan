"""
Cupo de uso por usuario (identidad, V2.0 adelantada).

Morgan usa las claves de su dueño. Con una sola persona da igual; con varias,
**una sola podría agotar la cuota de todas** en una tarde. Estas pruebas
comprueban que el cupo se aplica de verdad — que no es lo mismo que estar escrito.
"""

import pytest

from src.identidad import USUARIO_LOCAL, como_usuario
from src.identidad.cuotas import CONCEPTOS, ControlDeUso, Cuotas, CuotaAgotada
from src.memory.db import Database


@pytest.fixture
def control(tmp_path):
    return ControlDeUso(
        Database(tmp_path / "uso.db"),
        Cuotas(mensajes=3, transcripciones=2, imagenes=1),
    )


class TestElCupoSeAplica:
    def test_se_agota_al_llegar_al_limite(self, control):
        """El defecto que apareció al ejercitarlo: la primera versión contaba
        pero **no rechazaba nada**, porque el UPDATE condicional no incrementaba
        y tampoco avisaba."""
        for _ in range(3):
            control.apuntar("ana", "mensajes")

        with pytest.raises(CuotaAgotada):
            control.apuntar("ana", "mensajes")

    def test_el_mensaje_dice_que_hacer(self, control):
        """No es un fallo del sistema: es una condición normal que la persona
        debe entender."""
        control.apuntar("ana", "imagenes")

        with pytest.raises(CuotaAgotada) as fallo:
            control.apuntar("ana", "imagenes")

        texto = str(fallo.value)
        assert "límite" in texto
        assert "mañana" in texto or "clave" in texto

    def test_los_conceptos_son_independientes(self, control):
        """Agotar los mensajes no debe impedir transcribir un audio."""
        for _ in range(3):
            control.apuntar("ana", "mensajes")

        control.apuntar("ana", "transcripciones")  # no debe lanzar

    def test_cada_usuario_tiene_el_suyo(self, control):
        for _ in range(3):
            control.apuntar("ana", "mensajes")

        control.apuntar("bruno", "mensajes")  # no debe lanzar

        assert control.consumo("bruno")["mensajes"] == 1

    def test_un_concepto_inventado_se_rechaza(self, control):
        with pytest.raises(ValueError):
            control.apuntar("ana", "cosas_raras")


class TestElUsuarioLocalNoTieneCupo:
    """Es tu ordenador y tus claves: limitarte a ti mismo en tu propia máquina no
    protege de nada. Y el Morgan de escritorio depende de esto."""

    def test_no_se_le_limita(self, control):
        for _ in range(50):
            control.apuntar(USUARIO_LOCAL, "mensajes")

    def test_su_restante_es_ilimitado(self, control):
        assert all(v == -1 for v in control.restante(USUARIO_LOCAL).values())

    def test_no_se_le_apunta_consumo(self, control):
        control.apuntar(USUARIO_LOCAL, "mensajes")

        assert control.consumo(USUARIO_LOCAL)["mensajes"] == 0


class TestElRestante:
    def test_arranca_completo(self, control):
        assert control.restante("ana")["mensajes"] == 3

    def test_baja_al_consumir(self, control):
        control.apuntar("ana", "mensajes")

        assert control.restante("ana")["mensajes"] == 2

    def test_nunca_es_negativo(self, control):
        for _ in range(3):
            control.apuntar("ana", "mensajes")

        assert control.restante("ana")["mensajes"] == 0

    def test_devuelve_todos_los_conceptos(self, control):
        assert set(control.restante("ana")) == set(CONCEPTOS)


class TestTolerancia:
    def test_un_fallo_al_leer_el_consumo_no_bloquea(self, control, monkeypatch):
        """Contar es accesorio: que falle no puede impedir usar Morgan. El riesgo
        de no contar un turno es menor que el de dejar a alguien fuera por un
        problema de la base."""
        def romper(*a, **k):
            raise RuntimeError("la base no responde")

        monkeypatch.setattr(control.db, "connect", romper)

        assert control.consumo("ana") == {c: 0 for c in CONCEPTOS}

    def test_el_consumo_antiguo_se_limpia(self, control):
        control.apuntar("ana", "mensajes")

        # Se envejece la fila a mano, como si fuera de hace meses.
        with control.db.connect() as conn:
            conn.execute("UPDATE uso_diario SET dia = '2020-01-01'")

        assert control.limpiar_antiguos(dias=30) == 1


class TestLaRamaDeLaNube:
    """El cupo en Supabase: la mitad que corre en producción y que **no tenía
    ninguna prueba**.

    Lo que costó: la función `apuntar_uso` se creó en un esquema privado,
    `morgan_priv`, siguiendo la decisión de no dejar funciones en `public`. Para
    la función de las políticas RLS es correcto; para esta no, porque el backend
    la llama **a través de PostgREST**, que solo expone `public`. Respondía 404,
    y ese 404 llegaba al navegador convertido en un 500 al enviar cualquier
    mensaje.

    Debajo había un segundo fallo que el primero tapaba: la columna `dia` es
    `date` y la función recibía `text`, así que ni siquiera habría funcionado
    estando al alcance. Nunca se había ejecutado con éxito.

    **El propietario está exento de cupo**, así que nada de esto se veía
    probando con la cuenta de siempre: aparecía justo con los usuarios nuevos.

    Estas pruebas fijan el contrato de la llamada, que es lo comprobable sin red.
    """

    class ClienteFalso:
        def __init__(self, respuesta=True):
            self.llamadas: list[tuple[str, dict]] = []
            self.respuesta = respuesta

        def rpc(self, funcion, args):
            self.llamadas.append((funcion, args))
            return self.respuesta

    @pytest.fixture
    def cliente(self):
        return self.ClienteFalso()

    @pytest.fixture
    def control_nube(self, cliente):
        class FabricaFalsa:
            def __init__(self, c):
                self.client = c

        return ControlDeUso(FabricaFalsa(cliente), Cuotas(mensajes=3))

    def test_se_apunta_llamando_a_la_funcion_de_postgres(self, control_nube, cliente):
        """Y no con dos consultas: comprobar y sumar por separado dejaría una
        ventana por la que dos peticiones simultáneas pasarían las dos."""
        with como_usuario("usr-ana"):
            control_nube.apuntar("usr-ana", "mensajes")

        assert len(cliente.llamadas) == 1
        funcion, argumentos = cliente.llamadas[0]
        assert funcion == "apuntar_uso"

    def test_la_llamada_manda_los_cuatro_argumentos_con_su_nombre(self, control_nube, cliente):
        """PostgREST casa los parámetros por nombre. Uno mal escrito da un 404
        que parece «la función no existe»."""
        with como_usuario("usr-ana"):
            control_nube.apuntar("usr-ana", "mensajes")

        _, argumentos = cliente.llamadas[0]

        assert set(argumentos) == {"p_user_id", "p_dia", "p_concepto", "p_limite"}
        assert argumentos["p_user_id"] == "usr-ana"
        assert argumentos["p_concepto"] == "mensajes"
        assert argumentos["p_limite"] == 3

    def test_el_dia_viaja_como_fecha_iso(self, control_nube, cliente):
        """La columna es `date`. Cualquier otro formato revienta con
        «column "dia" is of type date but expression is of type text»."""
        import re

        with como_usuario("usr-ana"):
            control_nube.apuntar("usr-ana", "mensajes")

        _, argumentos = cliente.llamadas[0]

        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", argumentos["p_dia"]), argumentos["p_dia"]

    def test_un_false_de_la_funcion_es_cupo_agotado(self, cliente):
        """La función devuelve si cabía. Ignorar ese `false` dejaría el cupo
        escrito pero sin aplicar."""
        cliente.respuesta = False

        class FabricaFalsa:
            client = cliente

        control = ControlDeUso(FabricaFalsa(), Cuotas(mensajes=3))

        with pytest.raises(CuotaAgotada):
            with como_usuario("usr-ana"):
                control.apuntar("usr-ana", "mensajes")

    def test_el_propietario_no_gasta_una_llamada(self, control_nube, cliente):
        """Está exento, así que ni siquiera se pregunta. Es también la razón de
        que el fallo no se viera nunca con la cuenta de siempre."""
        from src.identidad.roles import Rol

        control_nube.apuntar("usr-andy", "mensajes", Rol.OWNER)

        assert cliente.llamadas == []

    def test_un_concepto_inventado_no_llega_a_la_base(self, control_nube, cliente):
        """La función tiene su propia lista blanca porque el concepto acaba en
        SQL dinámico, pero rechazarlo aquí ahorra el viaje."""
        with pytest.raises(ValueError):
            with como_usuario("usr-ana"):
                control_nube.apuntar("usr-ana", "inventado")

        assert cliente.llamadas == []


class TestLosLimitesDeArchivosSonPorUsuario:
    """Con límites globales, la primera persona que llenara el cupo dejaría sin
    subir a todas las demás."""

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        import src.config as config

        config.reset_settings()
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.uploads import UploadStore
        from src.uploads.almacenamiento import AlmacenEnDisco

        fabrica = SQLiteRepositoryFactory(Database(tmp_path / "u.db"))
        almacen = UploadStore(
            repositorio=fabrica.uploads, almacen=AlmacenEnDisco(tmp_path / "b")
        )
        almacen.max_archivos = 2
        yield almacen
        config.reset_settings()

    def test_agotar_el_cupo_no_afecta_a_los_demas(self, store):
        from src.uploads.store import ArchivoRechazado

        with como_usuario("ana"):
            store.guardar("a1.txt", b"contenido")
            store.guardar("a2.txt", b"contenido")
            with pytest.raises(ArchivoRechazado):
                store.guardar("a3.txt", b"contenido")

        with como_usuario("bruno"):
            store.guardar("b1.txt", b"contenido")  # no debe lanzar
            assert len(store.listar()) == 1

    def test_el_espacio_tambien_se_cuenta_por_usuario(self, store):
        from src.uploads.store import ArchivoRechazado

        store.max_total_bytes = 2000

        with como_usuario("ana"):
            store.guardar("grande.txt", b"x" * 1800)
            with pytest.raises(ArchivoRechazado):
                store.guardar("otro.txt", b"x" * 1800)

        with como_usuario("bruno"):
            store.guardar("suyo.txt", b"x" * 1800)  # no debe lanzar


class TestElCupoEstaConectadoDeVerdad:
    """El módulo funcionaba y estaba probado, pero **no lo llamaba nadie**: era
    una pieza muerta. Estas pruebas comprueban lo que las de arriba no pueden,
    que es que el cupo llegue a aplicarse en los caminos que cuestan dinero."""

    def test_el_chat_devuelve_429_cuando_no_queda_cupo(self, monkeypatch):
        """Un 429 y no un 500: llegar al limite es una condicion normal, y el
        cliente tiene que poder distinguirla de una averia."""
        from fastapi.testclient import TestClient

        from src.api import dependencies
        from src.api.app import create_app
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        dependencies.reset_container()

        cliente = TestClient(create_app())
        respuesta = cliente.post("/auth/registro", json={
            "username": "ana", "email": "ana@ejemplo.co", "password": "contrasena-larga",
        })
        assert respuesta.status_code == 200, respuesta.text
        csrf = respuesta.json()["csrf"]

        contenedor = dependencies.get_container()
        # Sin cupo de mensajes, ninguno cabe.
        contenedor.uso.cuotas = Cuotas(mensajes=0)

        # Un agente de mentira: sin el, la ruta responde 503 por modo degradado
        # antes de llegar a mirar el cupo, y la prueba no probaria nada.
        class AgenteFalso:
            model = type("M", (), {"model_name": "de-prueba"})()

            def chat(self, *a, **k):
                raise AssertionError("no deberia haberse llamado al modelo")

        contenedor.agent = AgenteFalso()

        respuesta = cliente.post(
            "/chat", json={"message": "hola"}, headers={"X-Morgan-CSRF": csrf}
        )

        assert respuesta.status_code == 429
        assert respuesta.json()["error"]["code"] == "CUOTA_AGOTADA"
        reset_settings()

    def test_el_usuario_local_no_encuentra_el_cupo_en_el_chat(self, monkeypatch):
        """Es tu equipo y tus claves. El Morgan de escritorio depende de esto."""
        from fastapi.testclient import TestClient

        from src.api import dependencies
        from src.api.app import create_app

        cliente = TestClient(create_app())
        contenedor = dependencies.get_container()
        contenedor.uso.cuotas = Cuotas(mensajes=0)

        class AgenteFalso:
            model = type("M", (), {"model_name": "de-prueba"})()

            def chat(self, *a, **k):
                return "hola"

        contenedor.agent = AgenteFalso()

        respuesta = cliente.post("/chat", json={"message": "hola"})

        assert respuesta.status_code == 200

    def test_analizar_una_imagen_gasta_cupo(self, tmp_path):
        from src.identidad.cuotas import ControlDeUso
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.tools.multimodal import AnalyzeImageTool
        from src.uploads import UploadStore
        from src.uploads.almacenamiento import AlmacenEnDisco

        fabrica = SQLiteRepositoryFactory(Database(tmp_path / "u.db"))
        store = UploadStore(
            repositorio=fabrica.uploads, almacen=AlmacenEnDisco(tmp_path / "b")
        )
        uso = ControlDeUso(fabrica.db, Cuotas(imagenes=1))
        herramienta = AnalyzeImageTool(store, provider=None, uso=uso)

        # Un PNG minimo, con su firma real: el store rechaza los disfrazados.
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c6300010000050001"
        ) + b"\x0d\x0a\x2d\xb4\x00\x00\x00\x00\x49\x45\x4e\x44\xae\x42\x60\x82"

        with como_usuario("ana"):
            archivo = store.guardar("foto.png", png)

            # El primero gasta el cupo y falla mas adelante, por no haber modelo
            # de vision: da igual, lo que importa es que ya lo apunto.
            herramienta.execute(upload_id=archivo.id)

            segundo = herramienta.execute(upload_id=archivo.id)

        assert segundo["success"] is False
        assert "límite" in segundo["error"]

    def test_un_identificador_equivocado_no_gasta_cupo(self, tmp_path):
        """No tiene sentido cobrarle a nadie por escribir mal un identificador."""
        from src.identidad.cuotas import ControlDeUso
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.tools.multimodal import AnalyzeImageTool
        from src.uploads import UploadStore
        from src.uploads.almacenamiento import AlmacenEnDisco

        fabrica = SQLiteRepositoryFactory(Database(tmp_path / "u.db"))
        store = UploadStore(
            repositorio=fabrica.uploads, almacen=AlmacenEnDisco(tmp_path / "b")
        )
        uso = ControlDeUso(fabrica.db, Cuotas(imagenes=5))
        herramienta = AnalyzeImageTool(store, provider=None, uso=uso)

        with como_usuario("ana"):
            herramienta.execute(upload_id="no-existe")

            assert uso.consumo("ana")["imagenes"] == 0

    def test_sin_control_de_uso_la_herramienta_sigue_funcionando(self, tmp_path):
        """El Morgan de escritorio no tiene a quien limitar, y no debe romperse
        por no tenerlo."""
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.tools.multimodal import AnalyzeImageTool
        from src.uploads import UploadStore
        from src.uploads.almacenamiento import AlmacenEnDisco

        fabrica = SQLiteRepositoryFactory(Database(tmp_path / "u.db"))
        store = UploadStore(
            repositorio=fabrica.uploads, almacen=AlmacenEnDisco(tmp_path / "b")
        )
        herramienta = AnalyzeImageTool(store, provider=None, uso=None)

        resultado = herramienta.execute(upload_id="no-existe")

        # Falla por el identificador, no por la ausencia de cupo.
        assert resultado["success"] is False
        assert "límite" not in resultado["error"]
