"""
El cupo global: un techo para la suma de todas las cuentas (V2.0.24).

El registro está abierto a cualquiera con el enlace (decisión mía) y el cupo
por persona no acota el total: diez cuentas son diez cupos, y con OpenAI de pago
al final de la cadena eso es dinero. La auditoría de la 2.3 lo dejó como decisión;
Delegué el número. Por qué 150 / 60 / 15, en el docstring de `Cuotas`.

Lo que fijan estas pruebas:

1. El tope se aplica a la **suma** de todas las cuentas sujetas a cupo.
2. **El propietario y el usuario local ni cuentan ni se frenan**: pagan ellos.
3. Un rechazo global **no gasta** el cupo de la persona.
4. 0 es sin tope, y se configura por variable de entorno.
5. El mensaje dice que **no es culpa de quien lo lee**.
6. La rama de Supabase suma sin gastar una llamada a la función.
7. Y un defecto que apareció al construirlo: las herramientas de imagen y audio
   apuntaban el uso **sin el rol**, así que el propietario en la nube gastaba cupo.
"""

import pytest

from src.config import load_settings
from src.identidad import USUARIO_LOCAL, como_usuario
from src.identidad.cuotas import ControlDeUso, CuotaAgotada, Cuotas, CupoGlobalAgotado
from src.identidad.roles import Rol
from src.memory.db import Database


@pytest.fixture
def control(tmp_path):
    return ControlDeUso(
        Database(tmp_path / "uso.db"),
        Cuotas(mensajes=50, imagenes=20, transcripciones=20,
               global_mensajes=5, global_imagenes=2, global_transcripciones=0),
    )


class TestElTopeEsDeTodos:
    def test_la_suma_de_varias_cuentas_llega_al_tope(self, control):
        for persona in ("ana", "bruno", "carla", "ana", "bruno"):
            control.apuntar(persona, "mensajes", Rol.USER)

        with pytest.raises(CupoGlobalAgotado):
            control.apuntar("dani", "mensajes", Rol.USER)

    def test_por_debajo_del_tope_se_pasa(self, control):
        for persona in ("ana", "bruno", "carla", "dani"):
            control.apuntar(persona, "mensajes", Rol.USER)

        control.apuntar("eva", "mensajes", Rol.USER)  # el quinto cabe

    def test_un_rechazo_global_no_gasta_el_cupo_de_la_persona(self, control):
        for persona in ("ana", "bruno", "carla", "dani", "eva"):
            control.apuntar(persona, "mensajes", Rol.USER)

        with pytest.raises(CupoGlobalAgotado):
            control.apuntar("fede", "mensajes", Rol.USER)

        assert control.consumo("fede")["mensajes"] == 0

    def test_cada_concepto_tiene_el_suyo(self, control):
        control.apuntar("ana", "imagenes", Rol.USER)
        control.apuntar("bruno", "imagenes", Rol.USER)
        with pytest.raises(CupoGlobalAgotado):
            control.apuntar("carla", "imagenes", Rol.USER)

        control.apuntar("carla", "mensajes", Rol.USER)  # los mensajes siguen

    def test_cero_es_sin_tope(self, control):
        for i in range(30):
            control.apuntar(f"persona{i}", "transcripciones", Rol.USER)

    def test_al_dia_siguiente_se_renueva(self, control, monkeypatch):
        for persona in ("ana", "bruno", "carla", "dani", "eva"):
            control.apuntar(persona, "mensajes", Rol.USER)

        monkeypatch.setattr(ControlDeUso, "_hoy", staticmethod(lambda: "2999-01-01"))
        control.apuntar("fede", "mensajes", Rol.USER)

    def test_el_cupo_por_persona_se_sigue_aplicando(self, tmp_path):
        control = ControlDeUso(Database(tmp_path / "u.db"), Cuotas(mensajes=2, global_mensajes=100))
        control.apuntar("ana", "mensajes", Rol.USER)
        control.apuntar("ana", "mensajes", Rol.USER)

        with pytest.raises(CuotaAgotada) as fallo:
            control.apuntar("ana", "mensajes", Rol.USER)
        assert not isinstance(fallo.value, CupoGlobalAgotado)


class TestQuienPagaNoCuentaNiSeFrena:
    def test_el_propietario_pasa_con_el_tope_lleno(self, control):
        for persona in ("ana", "bruno", "carla", "dani", "eva"):
            control.apuntar(persona, "mensajes", Rol.USER)

        control.apuntar("andy", "mensajes", Rol.OWNER)
        control.apuntar(USUARIO_LOCAL, "mensajes")

    def test_el_propietario_no_suma(self, control):
        for _ in range(10):
            control.apuntar("andy", "mensajes", Rol.OWNER)

        for persona in ("ana", "bruno", "carla", "dani", "eva"):
            control.apuntar(persona, "mensajes", Rol.USER)

    def test_un_administrador_si_cuenta(self, control):
        """Administrar Morgan no es pagarlo: ya era así con el cupo por persona."""
        for _ in range(5):
            control.apuntar("moderadora", "mensajes", Rol.ADMIN)

        with pytest.raises(CupoGlobalAgotado):
            control.apuntar("ana", "mensajes", Rol.USER)


class TestLoQueVeLaPersona:
    def test_dice_que_no_es_por_su_uso(self, control):
        for persona in ("ana", "bruno", "carla", "dani", "eva"):
            control.apuntar(persona, "mensajes", Rol.USER)

        with pytest.raises(CupoGlobalAgotado) as fallo:
            control.apuntar("fede", "mensajes", Rol.USER)

        texto = str(fallo.value)
        assert "todas las cuentas" in texto
        assert "No es por tu uso" in texto
        assert "mañana" in texto

    def test_las_rutas_ya_lo_entienden(self):
        """Es un `CuotaAgotada`: el chat y la transcripción responden 429 sin tocarlos."""
        assert issubclass(CupoGlobalAgotado, CuotaAgotada)


class TestLaConfiguracion:
    def test_los_valores_de_serie(self, monkeypatch):
        for v in ("MENSAJES", "TRANSCRIPCIONES", "IMAGENES"):
            monkeypatch.delenv(f"MORGAN_CUPO_GLOBAL_{v}", raising=False)
        s = load_settings()
        assert (s.cupo_global_mensajes, s.cupo_global_transcripciones, s.cupo_global_imagenes) == (150, 60, 15)

    def test_se_cambia_por_variable_y_no_admite_negativos(self, monkeypatch):
        monkeypatch.setenv("MORGAN_CUPO_GLOBAL_MENSAJES", "0")
        monkeypatch.setenv("MORGAN_CUPO_GLOBAL_IMAGENES", "-3")
        s = load_settings()
        assert s.cupo_global_mensajes == 0
        assert s.cupo_global_imagenes == 0

    def test_el_contenedor_usa_la_configuracion(self, monkeypatch):
        from src.api import dependencies
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_CUPO_GLOBAL_MENSAJES", "7")
        reset_settings()
        dependencies.reset_container()
        try:
            assert dependencies.get_container().uso.cuotas.global_mensajes == 7
        finally:
            reset_settings()
            dependencies.reset_container()


class TestLaRamaDeLaNube:
    class Cliente:
        def __init__(self, filas):
            self.filas = filas
            self.rpcs = []
            self.consultas = []

        def select(self, tabla, consulta):
            self.consultas.append((tabla, consulta))
            return self.filas

        def rpc(self, funcion, args):
            self.rpcs.append(funcion)
            return True

    def _control(self, cliente, tope):
        class Fabrica:
            client = cliente

        return ControlDeUso(Fabrica(), Cuotas(global_mensajes=tope))

    def test_suma_las_filas_del_dia_y_rechaza_sin_llamar_a_la_funcion(self):
        cliente = self.Cliente([{"mensajes": 90}, {"mensajes": 60}])

        with pytest.raises(CupoGlobalAgotado):
            self._control(cliente, 150).apuntar("ana", "mensajes", Rol.USER)

        assert cliente.rpcs == []
        tabla, consulta = cliente.consultas[0]
        assert tabla == "uso_diario" and "dia=eq." in consulta and "select=mensajes" in consulta

    def test_con_hueco_apunta_como_siempre(self):
        cliente = self.Cliente([{"mensajes": 90}, {"mensajes": None}])

        self._control(cliente, 150).apuntar("ana", "mensajes", Rol.USER)

        assert cliente.rpcs == ["apuntar_uso"]

    def test_un_fallo_al_sumar_no_deja_a_nadie_fuera(self):
        class Roto(self.Cliente):
            def select(self, *a):
                raise RuntimeError("la base no contesta")

        cliente = Roto([])
        self._control(cliente, 150).apuntar("ana", "mensajes", Rol.USER)
        assert cliente.rpcs == ["apuntar_uso"]


class TestElRolEnLasHerramientasMultimodales:
    """El defecto encontrado: `_apuntar_cupo` no pasaba el rol."""

    def test_el_propietario_autenticado_no_gasta_cupo_de_imagenes(self, tmp_path):
        from src.tools.multimodal import _apuntar_cupo

        uso = ControlDeUso(Database(tmp_path / "u.db"), Cuotas(imagenes=1))
        with como_usuario("andy", Rol.OWNER):
            assert _apuntar_cupo(uso, "imagenes") is None
            assert _apuntar_cupo(uso, "imagenes") is None
            assert uso.consumo("andy")["imagenes"] == 0

    def test_una_cuenta_normal_si(self, tmp_path):
        from src.tools.multimodal import _apuntar_cupo

        uso = ControlDeUso(Database(tmp_path / "u.db"), Cuotas(imagenes=1))
        with como_usuario("ana"):
            assert _apuntar_cupo(uso, "imagenes") is None
            segundo = _apuntar_cupo(uso, "imagenes")

        assert segundo["success"] is False


class TestPorHttp:
    def test_el_chat_responde_429_con_el_mensaje_global(self, monkeypatch):
        from fastapi.testclient import TestClient

        from src.api import dependencies
        from src.api.app import create_app
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        dependencies.reset_container()
        try:
            app = create_app()
            contenedor = dependencies.get_container()
            contenedor.uso.cuotas = Cuotas(global_mensajes=1)

            class Agente:
                model = type("M", (), {"model_name": "de-prueba"})()

                def chat(self, *a, **k):
                    return "hola"

            contenedor.agent = Agente()

            def cuenta(nombre):
                c = TestClient(app)
                r = c.post("/auth/registro", json={
                    "username": nombre, "email": f"{nombre}@ejemplo.co", "password": "contrasena-larga",
                })
                return c, r.json()["csrf"]

            ana, csrf_ana = cuenta("ana")
            bruno, csrf_bruno = cuenta("bruno")

            primera = ana.post("/chat", json={"message": "hola"}, headers={"X-Morgan-CSRF": csrf_ana})
            segunda = bruno.post("/chat", json={"message": "hola"}, headers={"X-Morgan-CSRF": csrf_bruno})
        finally:
            reset_settings()
            dependencies.reset_container()

        assert primera.status_code == 200, primera.text
        assert segunda.status_code == 429
        assert segunda.json()["error"]["code"] == "CUOTA_AGOTADA"
        assert "todas las cuentas" in segunda.json()["error"]["message"]
