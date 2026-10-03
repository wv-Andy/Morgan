"""
Las automatizaciones (4.14): el horario, dónde se guardan, el reloj, la ejecución sin nadie
delante, crearlas desde el chat y la bandeja.

Lo que haría daño si fallase: que una automatización **cambie algo** (en la 4.14 solo
consulta, decisión mía), que se ejecute **dos veces** porque dos relojes se cruzan,
que corra **sin gastar cupo** (con el rol de propietario), que una persona vea o toque las
de otra, que el reloj se pueda llamar **sin su secreto**, que se cree **sin aprobar** un
plan rojo, o que con el PC apagado se pierda sin decirlo (decisión 4: esperar 1 hora).
"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from src.automatizacion import horario
from src.automatizacion.contexto import PERMITIDAS, en_automatizacion, fijar_zona, permitida
from src.automatizacion.ejecutor import ESPERA_PC, MAX_FALLOS, REINTENTO_PC
from src.automatizacion.repositorio import AutomatizacionesSQLite, nuevo_id
from src.memory.db import Database

MADRID = "Europe/Madrid"


def _epoch(texto: str, zona: str = MADRID) -> float:
    return datetime.fromisoformat(texto).replace(tzinfo=ZoneInfo(zona)).timestamp()


def _local(momento: float, zona: str = MADRID) -> str:
    return datetime.fromtimestamp(momento, tz=ZoneInfo(zona)).strftime("%Y-%m-%d %H:%M %a")


# --- El horario ---------------------------------------------------------------------------

class TestHorario:
    @pytest.mark.parametrize("entrada, salida", [
        ({"tipo": "diaria", "hora": "9:05"}, {"tipo": "diaria", "hora": "09:05"}),
        ({"tipo": "semanal", "dias": [2, 0, 2], "hora": "08:30"}, {"tipo": "semanal", "dias": [0, 2], "hora": "08:30"}),
        ({"tipo": "cada_horas", "cada": "3"}, {"tipo": "cada_horas", "cada": 3}),
    ])
    def test_valen(self, entrada, salida):
        assert horario.validar(entrada) == (salida, None)

    @pytest.mark.parametrize("entrada", [
        None, {}, {"tipo": "cada_minutos", "cada": 5}, {"tipo": "diaria"}, {"tipo": "diaria", "hora": "25:00"},
        {"tipo": "semanal", "hora": "08:00"}, {"tipo": "semanal", "dias": [7], "hora": "08:00"},
        {"tipo": "cada_horas", "cada": 0}, {"tipo": "cada_horas", "cada": 169},
    ])
    def test_no_valen(self, entrada):
        normal, motivo = horario.validar(entrada)
        assert normal is None and motivo

    def test_diaria_hoy_si_no_ha_pasado_y_si_no_manana(self):
        h = {"tipo": "diaria", "hora": "09:00"}
        assert _local(horario.siguiente(h, MADRID, _epoch("2026-10-01T08:00"))) == "2026-10-01 09:00 Thu"
        assert _local(horario.siguiente(h, MADRID, _epoch("2026-10-01T09:00"))) == "2026-10-02 09:00 Fri"

    def test_a_su_hora_tambien_tras_el_cambio_de_hora(self):
        """El 25 de octubre de 2026 Madrid pasa de UTC+2 a UTC+1: las 9 siguen siendo las 9."""
        h = {"tipo": "diaria", "hora": "09:00"}
        antes = horario.siguiente(h, MADRID, _epoch("2026-10-24T10:00"))
        assert _local(antes) == "2026-10-25 09:00 Sun"
        assert datetime.fromtimestamp(antes, tz=ZoneInfo("UTC")).hour == 8      # ya en invierno
        verano = horario.siguiente(h, MADRID, _epoch("2026-10-23T10:00"))
        assert datetime.fromtimestamp(verano, tz=ZoneInfo("UTC")).hour == 7

    def test_semanal_el_siguiente_dia_de_la_lista(self):
        h = {"tipo": "semanal", "dias": [0, 2], "hora": "08:30"}               # lunes y miércoles
        assert _local(horario.siguiente(h, MADRID, _epoch("2026-10-01T12:00"))) == "2026-10-05 08:30 Mon"
        assert _local(horario.siguiente(h, MADRID, _epoch("2026-10-05T08:30"))) == "2026-10-07 08:30 Wed"

    def test_cada_horas_al_minuto(self):
        assert horario.siguiente({"tipo": "cada_horas", "cada": 2}, MADRID, 1000.5) == 960 + 7200

    def test_una_zona_que_no_existe_es_utc(self):
        assert horario.zona_valida("Marte/Olympus") is None and horario.zona("Marte/Olympus").key == "UTC"

    @pytest.mark.parametrize("h, texto", [
        ({"tipo": "diaria", "hora": "09:00"}, "cada día a las 09:00"),
        ({"tipo": "semanal", "dias": [0, 2], "hora": "08:30"}, "los lunes y miércoles a las 08:30"),
        ({"tipo": "semanal", "dias": [5, 6], "hora": "10:00"}, "los sábados y domingos a las 10:00"),
        ({"tipo": "cada_horas", "cada": 1}, "cada hora"),
    ])
    def test_en_palabras(self, h, texto):
        assert horario.describir(h) == texto


# --- Dónde se guardan ---------------------------------------------------------------------

def _auto(user_id="ana", **cambios) -> dict:
    datos = {"id": nuevo_id("auto"), "user_id": user_id, "nombre": "Noticias", "instruccion": "Busca noticias",
             "horario": {"tipo": "diaria", "hora": "09:00"}, "zona": MADRID, "necesita_pc": False,
             "activa": True, "proxima": 1000.0, "reclamo": 0, "fallos_seguidos": 0, "creado_en": 1.0}
    datos.update(cambios)
    return datos


@pytest.fixture
def repo(tmp_path):
    return AutomatizacionesSQLite(Database(tmp_path / "a.db"))


class TestRepositorio:
    def test_cada_persona_las_suyas(self, repo):
        a = _auto("ana")
        repo.crear(a)
        repo.crear(_auto("luis"))
        assert [x["id"] for x in repo.listar("ana")] == [a["id"]]
        assert repo.obtener("luis", a["id"]) is None
        assert not repo.borrar("luis", a["id"]) and repo.obtener("ana", a["id"])
        assert repo.listar("ana")[0]["horario"] == {"tipo": "diaria", "hora": "09:00"}

    def test_pendientes_solo_activas_y_llegadas(self, repo):
        llegada, futura, pausada = _auto(proxima=500), _auto(proxima=5000), _auto(proxima=10, activa=False)
        for a in (llegada, futura, pausada):
            repo.crear(a)
        assert [a["id"] for a in repo.pendientes(1000)] == [llegada["id"]]

    def test_reclamar_solo_una_vez(self, repo):
        a = _auto()
        repo.crear(a)
        assert repo.reclamar(a["id"], 0, {"proxima": 2000}) is True
        assert repo.reclamar(a["id"], 0, {"proxima": 3000}) is False, "dos relojes cruzados"
        assert repo.obtener("ana", a["id"])["proxima"] == 2000

    def test_la_bandeja(self, repo):
        for i in range(3):
            repo.crear_aviso({"id": f"av{i}", "user_id": "ana", "titulo": "t", "texto": "x", "estado": "hecha",
                              "herramientas": ["search_web"], "leido": False, "creado_en": float(i)})
        repo.crear_aviso({"id": "otro", "user_id": "luis", "titulo": "t", "texto": "x", "estado": "hecha",
                          "leido": False, "creado_en": 1.0})
        assert [a["id"] for a in repo.avisos("ana")] == ["av2", "av1", "av0"]
        assert repo.avisos("ana")[0]["herramientas"] == ["search_web"]
        assert repo.sin_leer("ana") == 3
        repo.marcar_leidos("ana", ["av0", "otro"])
        assert repo.sin_leer("ana") == 2 and repo.sin_leer("luis") == 1
        repo.marcar_leidos("ana")
        assert repo.sin_leer("ana") == 0

    def test_los_avisos_viejos_se_van(self, repo):
        repo.crear_aviso({"id": "viejo", "user_id": "ana", "titulo": "t", "texto": "x", "estado": "hecha",
                          "leido": True, "creado_en": 0.0})
        repo.limpiar(time.time())
        assert repo.avisos("ana") == []

    def test_al_borrar_la_cuenta_se_van(self, tmp_path):
        from src.identidad.repositorio import CuentasSQLite

        db = Database(tmp_path / "c.db")
        repo = AutomatizacionesSQLite(db)
        cuentas = CuentasSQLite(db)
        cuentas.crear({"id": "ana", "username": "ana", "email": "a@x.co", "password_hash": "h",
                       "status": "activo", "creado_en": 1.0, "actualizado_en": 1.0})
        repo.crear(_auto("ana"))
        repo.crear_aviso({"id": "a1", "user_id": "ana", "titulo": "t", "texto": "x", "estado": "hecha",
                          "leido": False, "creado_en": 1.0})
        cuentas.eliminar("ana")
        assert repo.listar("ana") == [] and repo.avisos("ana") == []


# --- Solo consultas -----------------------------------------------------------------------

class TestSoloConsulta:
    def test_fuera_de_una_automatizacion_todo(self):
        assert permitida("create_file") and permitida("remember_fact")

    @pytest.mark.parametrize("nombre", ["create_file", "delete_file", "open_app", "clipboard", "screenshot",
                                        "notify", "windows", "ui_control", "create_plan", "remember_fact",
                                        "create_automation", "run_change_command", "kill_process", "copy_file"])
    def test_dentro_nada_que_actue_ni_pida_permitir(self, nombre):
        with en_automatizacion({"id": "x"}):
            assert not permitida(nombre)

    def test_la_lista_blanca_solo_tiene_verdes(self):
        """Ninguna de PERMITIDAS cambia nada ni exige plan en el catálogo real."""
        from src.canal.herramientas import herramientas_del_equipo
        from src.tools.base import NIVELES_QUE_CAMBIAN

        for h in herramientas_del_equipo(store=object()):
            if h.name in PERMITIDAS:
                assert h.permission_level not in NIVELES_QUE_CAMBIAN and not getattr(h, "exige_plan", False), h.name


# --- El contenedor de verdad, con el modelo simulado ----------------------------------------

@pytest.fixture
def web(monkeypatch, modelo_simulado):
    from src.api import dependencies
    from src.api.app import create_app
    from src.api.sesion_web import CABECERA_CSRF
    from src.config import reset_settings

    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")
    monkeypatch.setenv("MORGAN_RELOJ_SECRETO", "secreto-del-reloj-de-prueba")
    reset_settings()
    dependencies.reset_container()
    app = create_app()

    def cuenta(nombre):
        cliente = TestClient(app)
        r = cliente.post("/auth/registro", json={"username": nombre, "email": f"{nombre}@ejemplo.co",
                                                 "password": "contrasena-larga"})
        assert r.status_code == 200, r.text
        cliente.headers[CABECERA_CSRF] = r.json()["csrf"]
        cliente.headers["X-Morgan-Zona"] = MADRID
        return cliente, r.json()["usuario"]["id"]

    container = dependencies.get_container()
    yield {"cuenta": cuenta, "container": container, "modelo": modelo_simulado, "app": app}
    dependencies.reset_container()
    reset_settings()


def _crear(container, user_id, **cambios) -> dict:
    from src.automatizacion.repositorio import repositorio_de_automatizaciones

    a = _auto(user_id, **cambios)
    repositorio_de_automatizaciones(container.repositories).crear(a)
    return repositorio_de_automatizaciones(container.repositories).obtener(user_id, a["id"])


def _repo(container):
    from src.automatizacion.repositorio import repositorio_de_automatizaciones

    return repositorio_de_automatizaciones(container.repositories)


class TestEjecucion:
    def test_hace_la_consulta_y_lo_deja_en_la_bandeja(self, web):
        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        auto = _crear(c, ana, proxima=time.time() - 5)
        modelo.queue_tool_call("recall_memory", {"query": "noticias"})
        modelo.queue_text("Hoy no hay noticias nuevas.")
        assert c.reloj.tic() == 1
        aviso = _repo(c).avisos(ana)[0]
        assert aviso["estado"] == "hecha" and aviso["texto"] == "Hoy no hay noticias nuevas."
        assert aviso["herramientas"] == ["recall_memory"] and aviso["titulo"] == "Noticias"
        guardada = _repo(c).obtener(ana, auto["id"])
        assert guardada["ultimo_estado"] == "hecha" and guardada["proxima"] > time.time()

    def test_lo_que_actua_ni_se_ofrece_ni_se_ejecuta(self, web):
        from src.identidad import como_usuario

        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        _crear(c, ana, proxima=time.time() - 5)
        modelo.queue_tool_call("remember_fact", {"key": "x", "value": "inyectado"})
        modelo.queue_text("Listo.")
        c.reloj.tic()
        ofrecidas = set(modelo.herramientas[0])
        assert ofrecidas and ofrecidas <= PERMITIDAS, ofrecidas - PERMITIDAS
        with como_usuario(ana):
            assert not c.memory_manager.recall("x"), "una automatización no recuerda nada"

    def test_se_cuenta_en_su_cupo_con_su_rol(self, web, monkeypatch):
        from src.identidad.roles import Rol

        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        _crear(c, ana, proxima=time.time() - 5)
        apuntes = []
        monkeypatch.setattr(c.uso, "apuntar", lambda u, concepto, rol=None: apuntes.append((u, concepto, rol)))
        modelo.queue_text("ok")
        c.reloj.tic()
        assert apuntes == [(ana, "mensajes", Rol.USER)], "con el rol de propietario no gastaría cupo"

    def test_sin_cupo_se_salta_y_se_dice(self, web, monkeypatch):
        from src.identidad.cuotas import CuotaAgotada

        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        _crear(c, ana, proxima=time.time() - 5)

        def sin_cupo(*a, **k):
            raise CuotaAgotada("mensajes", 50)

        monkeypatch.setattr(c.uso, "apuntar", sin_cupo)
        c.reloj.tic()
        aviso = _repo(c).avisos(ana)[0]
        assert aviso["estado"] == "saltada" and "cupo" in aviso["texto"] and not modelo.call_count

    def test_dos_relojes_a_la_vez_una_sola_ejecucion(self, web):
        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        auto = _crear(c, ana, proxima=time.time() - 5)
        modelo.queue_text("una")
        modelo.queue_text("dos")
        assert c.reloj.ejecutar(dict(auto), time.time()) is True
        assert c.reloj.ejecutar(dict(auto), time.time()) is False, "la segunda llega con el reclamo viejo"
        assert len(_repo(c).avisos(ana)) == 1

    def test_no_queda_en_el_historial(self, web):
        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        _crear(c, ana, proxima=time.time() - 5)
        modelo.queue_text("ok")
        c.reloj.tic()
        from src.identidad import como_usuario

        with como_usuario(ana):
            assert c.repositories.sessions.list() == []
        assert not any(s.startswith("auto-") for s in c.agent.sessions._sessions)

    def test_lo_atrasado_no_se_repite_de_golpe(self, web):
        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        auto = _crear(c, ana, proxima=time.time() - 5 * 86400, horario={"tipo": "cada_horas", "cada": 1})
        modelo.queue_text("ok")
        c.reloj.tic()
        assert c.reloj.tic() == 0 and len(_repo(c).avisos(ana)) == 1
        assert _repo(c).obtener(ana, auto["id"])["proxima"] > time.time()

    def test_tres_fallos_la_pausan(self, web, monkeypatch):
        _, ana = web["cuenta"]("ana")
        c = web["container"]
        auto = _crear(c, ana, proxima=time.time() - 5)
        monkeypatch.setattr(c.agent, "chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("roto")))
        for _ in range(MAX_FALLOS):
            fila = _repo(c).obtener(ana, auto["id"])
            c.reloj.ejecutar(fila, fila["proxima"] + 1)
        fila = _repo(c).obtener(ana, auto["id"])
        assert fila["activa"] is False and fila["fallos_seguidos"] == MAX_FALLOS
        assert "pausado" in _repo(c).avisos(ana)[0]["texto"]

    def test_un_fallo_del_modelo_cuenta_como_fallo(self, web, monkeypatch):
        from src.observabilidad import anotar

        _, ana = web["cuenta"]("ana")
        c = web["container"]
        _crear(c, ana, proxima=time.time() - 5)
        monkeypatch.setattr(c.agent, "chat", lambda *a, **k: anotar("fallo", "modelo") or "El modelo no respondió")
        c.reloj.tic()
        assert _repo(c).avisos(ana)[0]["estado"] == "fallo"

    def test_un_error_tras_reclamarla_tambien_llega_a_la_bandeja(self, web, monkeypatch):
        """Medido en estas pruebas: un error inesperado tras reclamarla la perdía sin aviso."""
        from src.automatizacion.ejecutor import Reloj

        _, ana = web["cuenta"]("ana")
        c = web["container"]
        _crear(c, ana, proxima=time.time() - 5)
        monkeypatch.setattr(Reloj, "_cuenta", lambda self, u: (_ for _ in ()).throw(RuntimeError("base caída")))
        assert c.reloj.tic() == 1
        assert _repo(c).avisos(ana)[0]["estado"] == "fallo"

    def test_el_modelo_caido_de_verdad_es_un_fallo(self, web, monkeypatch):
        """Sin simular la anotación: el núcleo la deja cuando el proveedor falla."""
        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        _crear(c, ana, proxima=time.time() - 5)
        monkeypatch.setattr(modelo, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("503")))
        c.reloj.tic()
        assert _repo(c).avisos(ana)[0]["estado"] == "fallo"

    def test_todo_el_turno_corre_con_su_rol(self, web, monkeypatch):
        """No solo el cupo: lo que mire el rol dentro del turno (permisos, otros cupos)."""
        from src.identidad import rol_actual, usuario_actual
        from src.identidad.roles import Rol

        _, ana = web["cuenta"]("ana")
        c = web["container"]
        _crear(c, ana, proxima=time.time() - 5)
        visto = []
        monkeypatch.setattr(c.agent, "chat", lambda *a, **k: visto.append((usuario_actual(), rol_actual())) or "ok")
        c.reloj.tic()
        assert visto == [(ana, Rol.USER)]

    def test_y_un_administrador_con_el_suyo(self, web, monkeypatch):
        from src.identidad import rol_actual
        from src.identidad.repositorio import repositorio_de_cuentas
        from src.identidad.roles import Rol

        _, ana = web["cuenta"]("ana")
        c = web["container"]
        repositorio_de_cuentas(c.repositories).actualizar(ana, {"role": "admin"})
        _crear(c, ana, proxima=time.time() - 5)
        visto = []
        monkeypatch.setattr(c.agent, "chat", lambda *a, **k: visto.append(rol_actual()) or "ok")
        c.reloj.tic()
        assert visto == [Rol.ADMIN]

    def test_una_cuenta_bloqueada_no_ejecuta(self, web):
        from src.identidad.repositorio import repositorio_de_cuentas

        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        auto = _crear(c, ana, proxima=time.time() - 5)
        repositorio_de_cuentas(c.repositories).actualizar(ana, {"status": "bloqueado"})
        c.reloj.tic()
        assert not modelo.call_count and _repo(c).obtener(ana, auto["id"])["activa"] is False

    def test_una_cuenta_que_ya_no_esta_no_ejecuta(self, web):
        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        auto = _crear(c, "fantasma", proxima=time.time() - 5)
        c.reloj.tic()
        assert not modelo.call_count and _repo(c).obtener("fantasma", auto["id"])["activa"] is False


class TestPcApagado:
    """Mi decisión 4: esperar hasta una hora y, si no, saltarla y avisar."""

    def test_espera_y_si_vuelve_se_hace(self, web, monkeypatch):
        from src.automatizacion.ejecutor import Reloj

        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        auto = _crear(c, ana, proxima=1_000_000.0, necesita_pc=True)
        monkeypatch.setattr(Reloj, "_pc_conectado", lambda self, u: False)
        assert c.reloj.ejecutar(dict(auto), 1_000_100.0) is False
        fila = _repo(c).obtener(ana, auto["id"])
        assert fila["proxima"] == 1_000_100.0 + REINTENTO_PC and fila["esperando_pc_desde"] == 1_000_000.0
        assert not modelo.call_count and _repo(c).avisos(ana) == []

        monkeypatch.setattr(Reloj, "_pc_conectado", lambda self, u: True)
        modelo.queue_text("hecho con el PC")
        assert c.reloj.ejecutar(fila, fila["proxima"]) is True
        assert _repo(c).avisos(ana)[0]["estado"] == "hecha"
        assert _repo(c).obtener(ana, auto["id"])["esperando_pc_desde"] is None

    def test_pasada_la_hora_se_salta_y_se_avisa(self, web, monkeypatch):
        from src.automatizacion.ejecutor import Reloj

        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        auto = _crear(c, ana, proxima=1_000_000.0, necesita_pc=True)
        monkeypatch.setattr(Reloj, "_pc_conectado", lambda self, u: False)
        c.reloj.ejecutar(dict(auto), 1_000_100.0)
        fila = _repo(c).obtener(ana, auto["id"])
        assert c.reloj.ejecutar(fila, 1_000_000.0 + ESPERA_PC + 1) is True
        aviso = _repo(c).avisos(ana)[0]
        assert aviso["estado"] == "saltada" and "PC no se conectó" in aviso["texto"] and not modelo.call_count
        fila = _repo(c).obtener(ana, auto["id"])
        assert fila["activa"] and fila["fallos_seguidos"] == 0, "saltarla no es un fallo"

    def test_nunca_espera_mas_de_la_hora(self, web, monkeypatch):
        from src.automatizacion.ejecutor import Reloj

        _, ana = web["cuenta"]("ana")
        c = web["container"]
        auto = _crear(c, ana, proxima=1_000_000.0, necesita_pc=True, esperando_pc_desde=1_000_000.0)
        monkeypatch.setattr(Reloj, "_pc_conectado", lambda self, u: False)
        c.reloj.ejecutar(dict(auto), 1_000_000.0 + ESPERA_PC - 60)
        assert _repo(c).obtener(ana, auto["id"])["proxima"] == 1_000_000.0 + ESPERA_PC


class TestCrearDesdeElChat:
    def test_es_roja_y_exige_plan(self, web):
        h = web["container"].tool_registry.get("create_automation")
        assert h.permission_level == "high_risk" and h.exige_plan
        from src.identidad.permiso_automatico import entra

        assert not entra(h.permission_level), "con el permiso automático tampoco se aprueba sola"

    def test_sin_plan_no_se_crea(self, web):
        cliente, ana = web["cuenta"]("ana")
        modelo = web["modelo"]
        modelo.queue_tool_call("create_automation", {"nombre": "x", "instruccion": "busca",
                                                     "horario": {"tipo": "diaria", "hora": "09:00"}})
        modelo.queue_text("Te lo propongo.")
        cliente.post("/chat", json={"message": "programa algo", "session_id": "s1"})
        assert _repo(web["container"]).listar(ana) == []

    def test_con_el_plan_aprobado_se_crea_en_su_zona(self, web):
        cliente, ana = web["cuenta"]("ana")
        modelo = web["modelo"]
        paso = {"descripcion": "programar", "herramienta": "create_automation", "argumentos": {
            "nombre": "Noticias", "instruccion": "Resume las noticias de IA",
            "horario": {"tipo": "diaria", "hora": "09:00"}}}
        modelo.queue_tool_call("create_plan", {"objetivo": "noticias cada mañana", "pasos": [paso]})
        modelo.queue_text("¿Lo apruebas?")
        cliente.post("/chat", json={"message": "cada día a las 9 resúmeme las noticias de IA", "session_id": "s1"})
        planes = cliente.get("/planes", params={"session_id": "s1"}).json()["planes"]
        assert planes and planes[0]["estado"] == "pendiente"
        assert _repo(web["container"]).listar(ana) == [], "pendiente de aprobar: nada todavía"
        assert cliente.post(f"/planes/{planes[0]['id']}/aprobar").status_code == 200
        modelo.queue_text("Programada.")
        r = cliente.post("/chat", json={"message": "aprobado", "session_id": "s1", "ejecutar_plan": planes[0]["id"]})
        (auto,) = _repo(web["container"]).listar(ana)
        assert auto["zona"] == MADRID and _local(auto["proxima"]).endswith("09:00 " + _local(auto["proxima"])[-3:])
        # Medido con el modelo real: el «Listo» no decía cuándo era la primera ni dónde verla.
        respuesta = r.json()["response"]
        assert "cada día a las 09:00 (Europe/Madrid)" in respuesta and "Automatizaciones" in respuesta

    @pytest.mark.parametrize("instruccion", ["Elimina los archivos .tmp de Descargas", "Borra lo viejo",
                                             "Mueve las fotos a Imágenes", "Instala las actualizaciones",
                                             "Cierra Chrome", "Crea un archivo con el resumen"])
    def test_lo_que_cambia_algo_ni_se_propone(self, web, instruccion):
        """Medido con el modelo real: «cada noche borra los temporales» se propuso y se creó."""
        h = web["container"].tool_registry.get("create_automation")
        motivo = h.prevalidar({"instruccion": instruccion, "horario": {"tipo": "diaria", "hora": "23:00"}})
        assert motivo and "solo consulta" in motivo

    @pytest.mark.parametrize("instruccion", ["Busca noticias de IA y envía el resumen como mensaje",
                                             "Dime qué programas están instalados", "Avísame si el borrador está listo",
                                             "Mira si el puerto 8000 está cerrado", "Crea un resumen de 5 puntos",
                                             "Dime si el servidor está apagado", "Busca ofertas de vacaciones",
                                             "Avísame si hay algo que eliminaría espacio",
                                             "Mira si el banco ya me acredita la nómina"])
    def test_lo_que_solo_mira_pasa(self, web, instruccion):
        from src.identidad import como_usuario

        _, ana = web["cuenta"]("ana")
        h = web["container"].tool_registry.get("create_automation")
        with como_usuario(ana):
            assert h.prevalidar({"instruccion": instruccion, "horario": {"tipo": "diaria", "hora": "09:00"}}) is None

    def test_lo_imposible_ni_se_propone(self, web):
        from src.identidad import como_usuario

        _, ana = web["cuenta"]("ana")
        h = web["container"].tool_registry.get("create_automation")
        with como_usuario(ana):
            assert "hora" in h.prevalidar({"instruccion": "x", "horario": {"tipo": "cada_horas", "cada": 0}})
            for _ in range(10):
                _crear(web["container"], ana)
            assert "10" in h.prevalidar({"instruccion": "x", "horario": {"tipo": "diaria", "hora": "09:00"}})

    def test_sin_zona_es_utc_y_lo_dice(self, web):
        from src.identidad import como_usuario

        _, ana = web["cuenta"]("ana")
        h = web["container"].tool_registry.get("create_automation")
        fijar_zona(None)
        with como_usuario(ana):
            r = h.execute(nombre="x", instruccion="busca", horario={"tipo": "diaria", "hora": "09:00"})
        assert r["success"] and "UTC" in r["data"]["zona"] and "no sé la tuya" in r["data"]["zona"]


class TestRutas:
    def test_cada_uno_las_suyas(self, web):
        ana_c, ana = web["cuenta"]("ana")
        luis_c, _ = web["cuenta"]("luis")
        auto = _crear(web["container"], ana, proxima=time.time() + 100)
        assert [a["id"] for a in ana_c.get("/automatizaciones").json()["automatizaciones"]] == [auto["id"]]
        assert luis_c.get("/automatizaciones").json()["automatizaciones"] == []
        assert luis_c.post(f"/automatizaciones/{auto['id']}/pausar").status_code == 404
        assert luis_c.delete(f"/automatizaciones/{auto['id']}").status_code == 404
        assert _repo(web["container"]).obtener(ana, auto["id"])["activa"]

    def test_pausar_reanudar_y_borrar(self, web):
        ana_c, ana = web["cuenta"]("ana")
        auto = _crear(web["container"], ana, proxima=10.0, fallos_seguidos=2)
        assert ana_c.post(f"/automatizaciones/{auto['id']}/pausar").json()["activa"] is False
        r = ana_c.post(f"/automatizaciones/{auto['id']}/reanudar").json()
        assert r["activa"] and r["fallos_seguidos"] == 0 and r["proxima"] > time.time(), "desde ahora, no lo atrasado"
        assert ana_c.delete(f"/automatizaciones/{auto['id']}").status_code == 200
        assert ana_c.get("/automatizaciones").json()["automatizaciones"] == []

    def test_reanudar_respeta_el_maximo(self, web):
        ana_c, ana = web["cuenta"]("ana")
        pausada = _crear(web["container"], ana, activa=False)
        for _ in range(10):
            _crear(web["container"], ana)
        assert ana_c.post(f"/automatizaciones/{pausada['id']}/reanudar").status_code == 409

    def test_la_bandeja(self, web):
        ana_c, ana = web["cuenta"]("ana")
        luis_c, _ = web["cuenta"]("luis")
        _repo(web["container"]).crear_aviso({"id": "a1", "user_id": ana, "titulo": "t", "texto": "x",
                                             "estado": "hecha", "leido": False, "creado_en": 1.0})
        assert ana_c.get("/avisos").json()["sin_leer"] == 1
        assert luis_c.get("/avisos").json() == {"avisos": [], "sin_leer": 0}
        assert ana_c.post("/avisos/leidos", json={}).json()["sin_leer"] == 0

    def test_sin_sesion_nada(self, web):
        anonimo = TestClient(web["app"])
        assert anonimo.get("/automatizaciones").status_code == 401
        assert anonimo.get("/avisos").status_code == 401


class TestElReloj:
    def test_sin_secreto_no_existe(self, web, monkeypatch):
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_RELOJ_SECRETO", "")
        reset_settings()
        assert TestClient(web["app"]).post("/automatizaciones/reloj").status_code == 404

    def test_con_otro_secreto_no(self, web, monkeypatch):
        lanzados = []
        monkeypatch.setattr(web["container"].reloj, "tic_en_segundo_plano", lambda: lanzados.append(1) or True)
        cliente = TestClient(web["app"])
        assert cliente.post("/automatizaciones/reloj").status_code == 401
        assert cliente.post("/automatizaciones/reloj", headers={"X-Morgan-Reloj": "otro"}).status_code == 401
        assert not lanzados

    def test_con_el_suyo_lanza_sin_sesion(self, web, monkeypatch):
        lanzados = []
        monkeypatch.setattr(web["container"].reloj, "tic_en_segundo_plano", lambda: lanzados.append(1) or True)
        r = TestClient(web["app"]).post("/automatizaciones/reloj",
                                        headers={"X-Morgan-Reloj": "secreto-del-reloj-de-prueba"})
        assert r.status_code == 200 and r.json() == {"lanzado": True} and lanzados == [1]

    def test_un_tic_a_la_vez(self, web):
        reloj = web["container"].reloj
        with reloj._cerrojo:
            assert reloj.tic() == 0 and reloj.tic_en_segundo_plano() is False


def test_en_una_automatizacion_no_hay_permiso_automatico(web):
    agente = web["container"].agent
    agente.permiso_automatico = lambda: True
    assert agente._permiso_automatico_del_turno() is True
    with en_automatizacion({"id": "x"}):
        assert agente._permiso_automatico_del_turno() is False


def test_el_contenedor_es_uno_aunque_lo_pidan_dos_hilos_a_la_vez(monkeypatch):
    """Medido en producción (2026-10-02): el reloj interno (4.14) lo pedía al arrancar a la vez
    que la primera petición; se construían dos, chocaban al crear la base y una petición
    recibía 503. Con un constructor lento, cinco hilos a la vez: uno solo."""
    import threading
    import time as _time

    from src.api import dependencies

    construidos = []

    class Lento:
        def __init__(self):
            _time.sleep(0.2)
            construidos.append(self)

    monkeypatch.setattr(dependencies, "CoreContainer", Lento)
    dependencies.reset_container()
    hilos = [threading.Thread(target=dependencies.get_container) for _ in range(5)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    dependencies.reset_container()
    assert len(construidos) == 1


# --- La implementación de Supabase (la de producción) -----------------------------------------
#
# Hasta la 4.19 solo se probaba la de SQLite; la cobertura lo enseñó (78 % del fichero, con
# Supabase entera sin tocar). Lo que haría daño: que una consulta no filtrara por dueño, que
# reclamar dejara de ser atómico, o que un identificador se colara en el filtro de PostgREST.

class ClienteFalso:
    def __init__(self, respuesta=None):
        self.peticiones: list[tuple] = []
        self.respuesta = respuesta if respuesta is not None else []

    def select(self, tabla, consulta=""):
        self.peticiones.append(("GET", tabla, consulta))
        return [dict(f) for f in self.respuesta]

    def update(self, tabla, consulta, valores):
        self.peticiones.append(("PATCH", tabla, consulta, valores))
        return self.respuesta

    def insert(self, tabla, filas):
        self.peticiones.append(("POST", tabla, filas))
        return self.respuesta

    def delete(self, tabla, consulta):
        self.peticiones.append(("DELETE", tabla, consulta))
        return self.respuesta


class TestEnSupabase:
    def _repo(self, respuesta=None):
        from src.automatizacion.repositorio import AutomatizacionesSupabase

        cliente = ClienteFalso(respuesta)
        return AutomatizacionesSupabase(cliente), cliente

    def test_todo_lo_de_una_persona_filtra_por_ella(self):
        repo, cliente = self._repo([{"id": "a1"}])
        repo.listar("ana")
        repo.obtener("ana", "a1")
        repo.actualizar("ana", "a1", {"nombre": "Otra"})
        repo.borrar("ana", "a1")
        repo.avisos("ana")
        repo.sin_leer("ana")
        repo.marcar_leidos("ana")
        for peticion in cliente.peticiones:
            assert "user_id=eq.ana" in peticion[2], peticion
        assert {p[0] for p in cliente.peticiones} == {"GET", "PATCH", "DELETE"}

    def test_un_identificador_no_se_cuela_en_el_filtro(self):
        repo, cliente = self._repo()
        repo.obtener("ana&user_id=neq.x", "a1,b2")
        consulta = cliente.peticiones[-1][2]
        assert "user_id=eq.ana%26user_id%3Dneq.x" in consulta and "id=eq.a1%2Cb2" in consulta
        assert consulta.count("&") == 2, "solo los & de Morgan: user_id, id y limit"

    def test_reclamar_solo_si_nadie_subio_el_contador(self):
        repo, cliente = self._repo([])        # PostgREST no devuelve filas: otro se adelantó
        assert repo.reclamar("a1", 3, {"proxima": 5.0, "user_id": "otra"}) is False
        _, tabla, consulta, valores = cliente.peticiones[-1]
        assert tabla == "automatizaciones" and "id=eq.a1" in consulta and "reclamo=eq.3" in consulta
        assert valores == {"proxima": 5.0, "reclamo": 4}, "sube el contador y no cambia de dueño"

        repo, _ = self._repo([{"id": "a1"}])
        assert repo.reclamar("a1", 3, {}) is True

    def test_pendientes_solo_activas_y_llegadas(self):
        repo, cliente = self._repo()
        repo.pendientes(1000.0, limite=7)
        consulta = cliente.peticiones[-1][2]
        assert "activa=is.true" in consulta and "proxima=lte.1000.0" in consulta
        assert "order=proxima.asc" in consulta and "limit=7" in consulta

    def test_lo_que_vuelve_de_postgres_queda_igual_que_de_sqlite(self):
        fila = {"id": "a1", "horario": '{"tipo": "diaria", "hora": "09:00"}', "pasos": '[{"tool": "x"}]',
                "necesita_pc": 0, "activa": 1}
        repo, _ = self._repo([fila])
        a = repo.obtener("ana", "a1")
        assert a["horario"] == {"tipo": "diaria", "hora": "09:00"} and a["pasos"] == [{"tool": "x"}]
        assert a["necesita_pc"] is False and a["activa"] is True

        # Y si ya viene como JSON de verdad (jsonb), se respeta.
        repo, _ = self._repo([{"id": "a1", "horario": {"tipo": "cada_horas", "horas": 2}, "pasos": None}])
        assert repo.obtener("ana", "a1")["horario"] == {"tipo": "cada_horas", "horas": 2}

    def test_un_json_roto_no_rompe_la_lista(self):
        repo, _ = self._repo([{"id": "a1", "horario": "{roto", "pasos": "[roto", "herramientas": "roto"}])
        a = repo.listar("ana")[0]
        assert a["horario"] == {} and a["pasos"] is None and a["herramientas"] == []

    def test_se_guarda_solo_lo_que_es_columna_y_el_json_como_texto(self):
        from src.automatizacion.repositorio import COLUMNAS

        repo, cliente = self._repo()
        repo.crear({**_auto("ana", id="a1"), "pasos": [{"tool": "x"}], "inventada": 1})
        fila = cliente.peticiones[-1][2][0]
        assert "inventada" not in fila and set(fila) <= set(COLUMNAS)
        assert fila["horario"] == '{"tipo": "diaria", "hora": "09:00"}' and fila["pasos"] == '[{"tool": "x"}]'

    def test_actualizar_no_cambia_ni_el_id_ni_el_dueno(self):
        repo, cliente = self._repo([{"id": "a1"}])
        assert repo.actualizar("ana", "a1", {"id": "b", "user_id": "otra"}) is False
        assert cliente.peticiones == [], "sin nada que cambiar, ni se pregunta"
        repo.anotar("a1", {"user_id": "otra"})
        assert cliente.peticiones == []

    def test_marcar_leidos(self):
        repo, cliente = self._repo([{"id": "v1"}, {"id": "v2"}])
        assert repo.marcar_leidos("ana", []) == 0 and cliente.peticiones == []
        assert repo.marcar_leidos("ana", ["v1", "v2"]) == 2
        consulta = cliente.peticiones[-1][2]
        assert "user_id=eq.ana" in consulta and "leido=is.false" in consulta and 'id=in.("v1","v2")' in consulta

    def test_los_avisos_viejos_se_van(self):
        from src.automatizacion.repositorio import DURAN_LOS_AVISOS

        repo, cliente = self._repo([{"id": "v1"}])
        assert repo.limpiar(DURAN_LOS_AVISOS + 100.0) == 1
        assert cliente.peticiones[-1] == ("DELETE", "avisos", "creado_en=lt.100.0")

    def test_se_elige_segun_los_repositorios(self):
        from types import SimpleNamespace

        from src.automatizacion.repositorio import AutomatizacionesSupabase, repositorio_de_automatizaciones

        assert isinstance(repositorio_de_automatizaciones(SimpleNamespace(client=ClienteFalso())),
                          AutomatizacionesSupabase)
        with pytest.raises(RuntimeError):
            repositorio_de_automatizaciones(SimpleNamespace())


class TestElReloj:
    def test_una_que_falla_no_para_a_las_demas(self, web, monkeypatch):
        _, ana = web["cuenta"]("ana")
        c = web["container"]
        a1 = _crear(c, ana, proxima=time.time() - 10)
        a2 = _crear(c, ana, proxima=time.time() - 5)
        original = c.reloj.ejecutar

        def ejecutar(auto, ahora):
            if auto["id"] == a1["id"]:
                raise RuntimeError("se rompió")
            return original(auto, ahora)

        monkeypatch.setattr(c.reloj, "ejecutar", ejecutar)
        web["modelo"].queue_text("hecha")
        assert c.reloj.tic() == 1
        assert [a["automatizacion_id"] for a in _repo(c).avisos(ana)] == [a2["id"]]

    def test_dos_tics_a_la_vez_no_se_pisan(self, web):
        reloj = web["container"].reloj
        with reloj._cerrojo:
            assert reloj.tic() == 0
            assert reloj.tic_en_segundo_plano() is False

    def test_arrancar_dos_veces_es_un_solo_hilo(self, web):
        reloj = web["container"].reloj
        reloj.parar()
        if reloj._hilo is not None:
            reloj._hilo.join(timeout=2)
        reloj._hilo = None
        reloj._parar.clear()
        reloj.arrancar()
        primero = reloj._hilo
        reloj.arrancar()
        assert reloj._hilo is primero
        reloj.parar()
        primero.join(timeout=2)
        assert not primero.is_alive()
