"""
El canal seguro y el despacho del agente local (3.0-D).

Lo que fija, del contrato (docs/agente-local.md §7-§9, §14-§16) y de las mediciones de
la 3.0-B:

- **El canal se autentica él mismo**: los middlewares no llegan a un WebSocket. Sin
  credencial de agente, con un token personal o con un agente revocado, se cierra
  antes de aceptar.
- **A quién va una orden sale del contexto del turno.** A→A llega; A→B y B→A no.
- **Cola, plazo y cortes**: una en vuelo y cuatro esperando; lo que no cabe se rechaza
  al momento; una orden sin respuesta vence; un corte falla lo pendiente al instante.
- **Latido**: sin respuesta a tres latidos, la nube cierra.
- **El agente no confía en la nube**: rechaza lo que no es para él, lo vencido y lo
  que no sabe hacer, y no ejecuta dos veces la misma orden.
- Y de extremo a extremo, con un servidor y un agente de verdad: órdenes, reconexión
  tras un reinicio de la nube (1012) y parada al ser revocado.
"""

import asyncio
import json
import re
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.agente import auditoria
from src.agente.ejecutor import Ejecutor
from src.agente.protocolo import PROTOCOLO_ACTUAL
from src.api.app import create_app
from src.canal import despacho
from src.canal.registro import REGISTRO
from src.config import reset_settings
from src.identidad import como_usuario

CLAVE = "contrasena-larga"
SALUDO = {"tipo": "saludo", "protocol_version": PROTOCOLO_ACTUAL, "agent_version": "3.0.0-dev",
          "sistema": "Windows 11", "capacidades": ["estado"]}


@pytest.fixture
def nube(monkeypatch):
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    REGISTRO.reiniciar()
    monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 0.5)
    yield create_app()
    REGISTRO.reiniciar()
    reset_settings()


def _cuenta(app, nombre="ana"):
    """Una cuenta con un agente emparejado. Devuelve (web, csrf, user_id, agent_id, credencial)."""
    web = TestClient(app)
    r = web.post("/auth/registro", json={
        "username": nombre, "email": f"{nombre}@ejemplo.co", "password": CLAVE,
    }, headers={"X-Forwarded-For": f"10.3.0.{len(nombre)}"})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf"]
    user_id = web.get("/auth/yo").json()["usuario"]["id"]
    return web, csrf, user_id, *_emparejar(app, web, csrf)


def _emparejar(app, web, csrf, nombre="Portátil"):
    codigo = web.post("/auth/agentes/codigo", headers={"x-morgan-csrf": csrf}).json()["codigo"]
    r = TestClient(app).post("/agente/emparejar/confirmar", json={
        "codigo": codigo, "nombre": nombre, "protocol_version": PROTOCOLO_ACTUAL,
    })
    assert r.status_code == 200, r.text
    return r.json()["agent_id"], r.json()["credencial"]


def _socket(app, credencial):
    return TestClient(app, headers={"Authorization": f"Bearer {credencial}"}).websocket_connect("/agente/canal")


def _enviar_en_hilo(user_id, capability="estado", arguments=None, plazo=5.0):
    """El despacho corre en el hilo del turno, no en el del servidor. Así se prueba."""
    resultado: dict = {}

    def correr():
        with como_usuario(user_id):
            try:
                resultado["valor"] = despacho.enviar(capability, arguments or {}, plazo=plazo)
            except Exception as exc:
                resultado["error"] = exc

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    return hilo, resultado


def _cierre(fallo) -> int:
    return fallo.value.code


def _hasta_el_cierre(ws) -> tuple[int | None, int]:
    """Lee hasta que la nube cierra. Devuelve (código del mensaje `cierre`, código del cierre).

    Tras aceptar, la nube manda primero un mensaje con el código y después cierra: el
    proxy de Render no reenvía los códigos de cierre (medido). Las dos cosas se miran.
    """
    del_mensaje = None
    try:
        while True:
            mensaje = ws.receive_json()
            if mensaje.get("tipo") == "cierre":
                del_mensaje = mensaje["codigo"]
    except WebSocketDisconnect as exc:
        return del_mensaje, exc.code


# --- Entrar al canal -----------------------------------------------------------


class TestEntrar:
    def test_sin_credencial_no_entra(self, nube):
        with pytest.raises(WebSocketDisconnect) as fallo:
            with TestClient(nube).websocket_connect("/agente/canal") as ws:
                ws.receive_json()
        assert _cierre(fallo) == 4401

    def test_con_un_token_personal_no_entra(self, nube):
        web, csrf, *_ = _cuenta(nube)
        token = web.post("/auth/tokens", json={"nombre": "x", "alcances": ["chat", "lectura", "escritura"]},
                         headers={"x-morgan-csrf": csrf}).json()["valor"]

        with pytest.raises(WebSocketDisconnect) as fallo:
            with _socket(nube, token) as ws:
                ws.receive_json()
        assert _cierre(fallo) == 4401

    def test_un_agente_revocado_no_entra(self, nube):
        web, csrf, _, agent_id, credencial = _cuenta(nube)
        web.delete(f"/auth/agentes/{agent_id}", headers={"x-morgan-csrf": csrf})

        with pytest.raises(WebSocketDisconnect) as fallo:
            with _socket(nube, credencial) as ws:
                ws.receive_json()
        assert _cierre(fallo) == 4401

    def test_el_saludo_se_exige(self, nube):
        *_, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json({"tipo": "hola"})
            assert _hasta_el_cierre(ws) == (4400, 4400)

    def test_protocolo_incompatible(self, nube):
        *_, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json({**SALUDO, "protocol_version": PROTOCOLO_ACTUAL + 1})
            assert _hasta_el_cierre(ws) == (4426, 4426)

    def test_bienvenida_y_la_web_ve_la_version(self, nube):
        web, _, _, agent_id, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            bienvenida = ws.receive_json()
            assert bienvenida["tipo"] == "bienvenida" and bienvenida["agent_id"] == agent_id
            assert bienvenida["compatibilidad"] == "CURRENT"
            agente = web.get("/auth/agentes").json()["agentes"][0]
        assert agente["agent_version"] == "3.0.0-dev" and agente["sistema"] == "Windows 11"

    def test_otro_equipo_de_la_misma_cuenta_convive(self, nube):
        """Hasta la 3.7, uno por persona (P1: el segundo se rechazaba con 4409). Desde
        entonces conviven, cada uno con su conexión."""
        web, csrf, user_id, _, credencial = _cuenta(nube)
        _, otra = _emparejar(nube, web, csrf, "Sobremesa")

        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            with _socket(nube, otra) as ws2:
                ws2.send_json(SALUDO)
                assert ws2.receive_json()["tipo"] == "bienvenida"
                assert {c.nombre for c in REGISTRO.todas(user_id)} == {"Portátil", "Sobremesa"}

    def test_el_mismo_agente_sustituye_su_conexion_vieja(self, nube):
        """Lo normal tras una suspensión: la vieja sigue medio abierta."""
        *_, credencial = _cuenta(nube)
        with _socket(nube, credencial) as vieja:
            vieja.send_json(SALUDO)
            vieja.receive_json()
            with _socket(nube, credencial) as nueva:
                nueva.send_json(SALUDO)
                assert nueva.receive_json()["tipo"] == "bienvenida"
                assert _hasta_el_cierre(vieja) == (4410, 4410)


# --- El despacho ---------------------------------------------------------------


class TestElDespacho:
    def test_una_orden_va_y_vuelve(self, nube):
        _, _, user_id, agent_id, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            hilo, resultado = _enviar_en_hilo(user_id)

            orden = ws.receive_json()
            assert orden["tipo"] == "orden" and orden["agent_id"] == agent_id
            assert orden["capability"] == "estado" and orden["vence_en_ms"] > 0
            assert orden["command_id"] and orden["request_id"]
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True, "data": {"ok": 1}, "error": None},
                          "tiempos": {"cola_ms": 1, "ejecucion_ms": 2}})
            hilo.join(5)

        assert resultado["valor"]["resultado"]["data"] == {"ok": 1}
        assert resultado["valor"]["total_ms"] >= 0

    def test_la_orden_queda_auditada_con_sus_identificadores(self, nube):
        from src.api.dependencies import get_container

        _, _, user_id, agent_id, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            hilo, _ = _enviar_en_hilo(user_id)
            orden = ws.receive_json()
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True}, "tiempos": {"cola_ms": 0, "ejecucion_ms": 0}})
            hilo.join(5)

        eventos = [e for e in get_container().audit_logger.get_recent(50) if e.get("accion") == "orden_agente"]
        assert eventos and eventos[-1]["objetivo"] == agent_id
        assert orden["command_id"] in eventos[-1]["detalle"] and orden["request_id"] in eventos[-1]["detalle"]

    def test_a_no_llega_al_agente_de_b(self, nube):
        """A→A OK, A→B DENIED, B→A DENIED (gate V3.0)."""
        _, _, ana, agente_ana, cred_ana = _cuenta(nube, "ana")
        _, _, bruno, _, _ = _cuenta(nube, "bruno")

        with _socket(nube, cred_ana) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()

            # B, sin agente conectado: no se le manda al de A.
            hilo_b, resultado_b = _enviar_en_hilo(bruno)
            hilo_b.join(5)
            assert isinstance(resultado_b["error"], despacho.AgenteNoConectado)

            # A sí llega al suyo, y lo primero que recibe su socket es SU orden.
            hilo_a, resultado_a = _enviar_en_hilo(ana)
            orden = ws.receive_json()
            assert orden["agent_id"] == agente_ana
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True}, "tiempos": {}})
            hilo_a.join(5)
            assert "valor" in resultado_a

    def test_sin_agente_conectado_lo_dice(self, nube):
        _, _, user_id, *_ = _cuenta(nube)
        hilo, resultado = _enviar_en_hilo(user_id)
        hilo.join(5)
        assert isinstance(resultado["error"], despacho.AgenteNoConectado)
        assert "no está conectado" in str(resultado["error"])

    def test_la_cola_tiene_tope(self, nube):
        _, _, user_id, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            hilos = [_enviar_en_hilo(user_id, plazo=5)[0] for _ in range(5)]
            limite = time.monotonic() + 5
            while REGISTRO.de(user_id).pendientes < 5 and time.monotonic() < limite:
                time.sleep(0.05)

            hilo, resultado = _enviar_en_hilo(user_id, plazo=5)
            hilo.join(3)
            assert isinstance(resultado["error"], despacho.AgenteOcupado)
            ws.close()
            for h in hilos:
                h.join(6)

    def test_una_orden_sin_respuesta_vence(self, nube):
        _, _, user_id, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            hilo, resultado = _enviar_en_hilo(user_id, plazo=0.5)
            ws.receive_json()  # la recibe y no contesta
            hilo.join(5)
        assert isinstance(resultado["error"], despacho.AgenteSinRespuesta)

    def test_un_corte_falla_lo_pendiente_al_momento(self, nube):
        """Con un agente del protocolo 2. Desde el 3, la nube espera a que vuelva y le
        pregunta (`TestRecuperarTrasUnCorte`)."""
        _, _, user_id, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json({**SALUDO, "protocol_version": 2})
            ws.receive_json()
            hilo, resultado = _enviar_en_hilo(user_id, plazo=30)
            ws.receive_json()
            inicio = time.monotonic()
            ws.close()
        hilo.join(10)
        assert isinstance(resultado["error"], despacho.AgenteDesconectado)
        assert time.monotonic() - inicio < 5  # no esperó los 30 s del plazo

    def test_una_orden_que_espera_al_agente_no_se_cuela_antes_de_la_bienvenida(self, nube, monkeypatch):
        """Encontrado en la 3.0.5: la conexión se registraba antes de la bienvenida, con
        la escritura en la base (~60 ms en Supabase) en medio. La orden que esperaba a que
        el agente volviera (tras un despliegue) llegaba primero, y el agente, que espera
        la bienvenida, daba la conexión por mala. Aquí la base tarda 0,5 s a propósito."""
        from src.identidad import repositorio

        monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 5.0)
        _, _, user_id, _, credencial = _cuenta(nube)
        for clase in (repositorio.CuentasSQLite,):
            original = clase.tocar_agente
            monkeypatch.setattr(clase, "tocar_agente",
                                lambda self, *a, **k: (time.sleep(0.5), original(self, *a, **k))[1])
        hilo, resultado = _enviar_en_hilo(user_id)       # ya esperando al agente
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            assert ws.receive_json()["tipo"] == "bienvenida"
            orden = ws.receive_json()
            assert orden["tipo"] == "orden"
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True, "data": {}}, "tiempos": {"cola_ms": 0, "ejecucion_ms": 0}})
            hilo.join(5)
        assert "valor" in resultado, resultado

    def test_si_falla_anotar_la_conexion_en_la_base_el_canal_sigue(self, nube, monkeypatch):
        """Antes, un fallo de Supabase ahí rompía la conexión recién registrada sin darla
        de baja: quedaba una que nadie escuchaba, con sus órdenes colgadas."""
        from src.identidad import repositorio

        def roto(self, *a, **k):
            raise RuntimeError("Supabase caído")

        monkeypatch.setattr(repositorio.CuentasSQLite, "tocar_agente", roto)
        _, _, user_id, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            assert ws.receive_json()["tipo"] == "bienvenida"
            hilo, resultado = _enviar_en_hilo(user_id)
            orden = ws.receive_json()
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True, "data": {}}, "tiempos": {"cola_ms": 0, "ejecucion_ms": 0}})
            hilo.join(5)
        assert "valor" in resultado, resultado

    def test_revocar_desde_la_web_cierra_la_conexion(self, nube):
        web, csrf, _, agent_id, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            web.delete(f"/auth/agentes/{agent_id}", headers={"x-morgan-csrf": csrf})
            assert _hasta_el_cierre(ws) == (4401, 4401)

    def test_cambiar_la_contrasena_cierra_la_conexion(self, nube):
        web, csrf, _, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            r = web.post("/auth/password", json={"actual": CLAVE, "nueva": "otra-clave-larga"},
                         headers={"x-morgan-csrf": csrf})
            assert r.status_code == 200
            assert _hasta_el_cierre(ws) == (4401, 4401)

    @pytest.mark.parametrize("mensaje", ["{no es json", "[1]", "5", '"texto"'])
    def test_un_mensaje_mal_formado_cierra_con_4400_y_no_en_silencio(self, nube, mensaje):
        """Medido en la 3.0.5 contra Render: la escucha se rompía y la conexión caía sin
        `cierre`; el agente solo se enteraba por la falta de latidos."""
        *_, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            ws.send_text(mensaje)
            assert _hasta_el_cierre(ws) == (4400, 4400)

    def test_el_servidor_limita_el_tamano_de_los_mensajes(self):
        """uvicorn aceptaba hasta 16 MB; un agente sano manda como mucho ~300 KB. El
        TestClient no pasa por uvicorn: se comprueba aquí que se le pasa, y en Render
        con el guion de la 3.0.5 que de verdad corta."""
        from src.api import server

        assert server.WS_MAX_SIZE == 2 * 2**20
        codigo = open(server.__file__, encoding="utf-8").read()
        assert "ws_max_size=WS_MAX_SIZE" in codigo

    def test_restablecer_la_contrasena_por_correo_cierra_la_conexion_ya(self, nube, monkeypatch):
        """Encontrado en la 3.0.5: se revocaba en la base, pero la conexión seguía abierta
        hasta la revalidación. Es el camino de «me robaron la cuenta»."""
        from src.api.routes import agentes

        monkeypatch.setattr(agentes, "REVALIDAR_CADA", 3600)   # que no la salve la revalidación
        web, _, _, _, credencial = _cuenta(nube)
        from src.api.routes.cuentas import get_servicio

        from src.api.dependencies import get_container

        token = get_servicio(get_container()).solicitar_recuperacion("ana@ejemplo.co")
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            inicio = time.monotonic()
            r = TestClient(nube).post("/auth/restablecer", json={"token": token, "password": "otra-clave-larga"})
            assert r.status_code == 200, r.text
            assert _hasta_el_cierre(ws) == (4401, 4401)
        assert time.monotonic() - inicio < 3

    def test_borrar_la_cuenta_cierra_la_conexion_ya(self, nube, monkeypatch):
        from src.api.routes import agentes

        monkeypatch.setattr(agentes, "REVALIDAR_CADA", 3600)
        web, csrf, _, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            inicio = time.monotonic()
            r = web.request("DELETE", "/auth/cuenta", json={"password": CLAVE}, headers={"x-morgan-csrf": csrf})
            assert r.status_code == 200, r.text
            assert _hasta_el_cierre(ws) == (4401, 4401)
        assert time.monotonic() - inicio < 3

    def test_un_resultado_huerfano_se_ignora_y_se_anota(self, nube):
        from src.api.dependencies import get_container

        _, _, user_id, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            ws.send_json({"tipo": "resultado", "command_id": "inventado", "estado": "COMPLETED"})
            # La conexión sigue: una orden de verdad va y vuelve después.
            hilo, resultado = _enviar_en_hilo(user_id)
            orden = ws.receive_json()
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True}, "tiempos": {}})
            hilo.join(5)
        assert "valor" in resultado
        acciones = [e.get("accion") for e in get_container().audit_logger.get_recent(50)]
        assert "resultado_huerfano" in acciones

    def test_desde_el_hilo_del_servidor_no_se_puede(self, nube):
        REGISTRO.loop = asyncio.new_event_loop()
        REGISTRO.hilo = threading.current_thread()
        try:
            with pytest.raises(RuntimeError):
                despacho.enviar("estado")
        finally:
            REGISTRO.loop.close()


class TestElLatido:
    def test_revocado_por_detras_pierde_la_conexion_sin_esperar_a_una_orden(self, nube, monkeypatch):
        from src.api.dependencies import get_container
        from src.api.routes import agentes
        from src.identidad.repositorio import repositorio_de_cuentas

        monkeypatch.setattr(agentes, "LATIDO", 0.2)
        monkeypatch.setattr(agentes, "REVALIDAR_CADA", 0.3)
        _, _, user_id, agent_id, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            repositorio_de_cuentas(get_container().repositories).revocar_agente(user_id, agent_id)
            with pytest.raises(WebSocketDisconnect) as fallo:
                while True:
                    ws.receive_json()
                    ws.send_json({"tipo": "pong"})
        assert _cierre(fallo) == 4401

    def test_la_revalidacion_es_cada_30_s_como_mucho(self):
        """Decisión mía en la 3.0.5: sin comprobación en cada orden, a cambio de
        revalidar cada 30 s (antes 60). Subirla ensancharía el hueco aceptado."""
        from src.api.routes import agentes

        assert agentes.REVALIDAR_CADA <= 30

    def test_una_orden_no_espera_a_la_base(self, nube, monkeypatch):
        """Los ~60 ms que se quitaron en la 3.0.5: una orden no espera a la base. Desde la
        3.7 se apunta en el historial, pero **en otro hilo y después**: con la base
        lentísima, la orden acaba igual de rápido."""
        import threading as hilos

        from src.identidad import repositorio

        _, _, user_id, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            consultas = []
            original = repositorio.repositorio_de_cuentas

            def lenta(*a, **k):
                consultas.append(hilos.current_thread().name)
                time.sleep(2)
                return original(*a, **k)

            monkeypatch.setattr(repositorio, "repositorio_de_cuentas", lenta)
            inicio = time.monotonic()
            hilo, resultado = _enviar_en_hilo(user_id)
            orden = ws.receive_json()
            while orden["tipo"] != "orden":
                orden = ws.receive_json()
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True, "data": {}}, "tiempos": {"cola_ms": 0, "ejecucion_ms": 0}})
            hilo.join(5)
            tardo = time.monotonic() - inicio
        assert "valor" in resultado, resultado
        assert tardo < 1.5, f"la orden esperó a la base: {tardo:.1f} s"
        assert set(consultas) <= {"historial-orden"}

    def test_sin_respuesta_a_los_latidos_se_cierra(self, nube, monkeypatch):
        from src.api.routes import agentes

        monkeypatch.setattr(agentes, "LATIDO", 0.2)
        *_, credencial = _cuenta(nube)
        with pytest.raises(WebSocketDisconnect) as fallo:
            with _socket(nube, credencial) as ws:
                ws.send_json(SALUDO)
                while True:
                    ws.receive_json()  # escucha, pero no contesta
        assert _cierre(fallo) == 4408

    def test_el_que_contesta_sigue(self, nube, monkeypatch):
        from src.api.routes import agentes

        monkeypatch.setattr(agentes, "LATIDO", 0.2)
        *_, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            for _ in range(8):
                assert ws.receive_json()["tipo"] == "latido"
                ws.send_json({"tipo": "pong"})


# --- El ejecutor del PC --------------------------------------------------------


def _orden(**cambios):
    return {"tipo": "orden", "protocol_version": PROTOCOLO_ACTUAL, "request_id": "r1",
            "command_id": "c1", "agent_id": "agt-1", "capability": "estado",
            "arguments": {}, "vence_en_ms": 5000, **cambios}


class TestElEjecutor:
    def test_estado(self):
        respuesta = asyncio.run(Ejecutor("agt-1").procesar(_orden()))
        assert respuesta["estado"] == "COMPLETED"
        assert respuesta["resultado"]["data"]["agent_id"] == "agt-1"
        assert set(respuesta["tiempos"]) == {"cola_ms", "ejecucion_ms"}

    @pytest.mark.parametrize("cambio, motivo", [
        ({"agent_id": "agt-otro"}, "otro_agente"),
        ({"protocol_version": 99}, "protocolo"),
        ({"vence_en_ms": 0}, "vencida"),
        ({"vence_en_ms": None}, "vencida"),
        ({"capability": "borrar_todo"}, "capacidad_no_disponible"),
        ({"arguments": ["no", "dict"]}, "argumentos"),
    ])
    def test_rechaza_lo_que_no_vale(self, cambio, motivo):
        respuesta = asyncio.run(Ejecutor("agt-1").procesar(_orden(**cambio)))
        assert respuesta["estado"] == "REJECTED" and respuesta["motivo"] == motivo

    def test_no_ejecuta_dos_veces_la_misma_orden(self):
        veces = []
        ejecutor = Ejecutor("agt-1", {"contar": lambda: veces.append(1) or {"success": True}})

        async def dos():
            a = await ejecutor.procesar(_orden(capability="contar"))
            b = await ejecutor.procesar(_orden(capability="contar"))
            return a, b

        primera, segunda = asyncio.run(dos())
        assert len(veces) == 1
        assert segunda["repetida"] is True and segunda["estado"] == primera["estado"]

    def test_un_fallo_no_filtra_detalles(self):
        def rota():
            raise ValueError(r"C:\Users\ana\secreto.txt no se pudo")

        respuesta = asyncio.run(Ejecutor("agt-1", {"rota": rota}).procesar(_orden(capability="rota")))
        assert respuesta["estado"] == "FAILED"
        assert "secreto" not in json.dumps(respuesta) and "ValueError" in respuesta["resultado"]["error"]

    def test_al_vencer_se_cancela_en_un_punto_seguro(self):
        """3.4: el plazo ya no abandona la operación, la cancela."""
        from src.agente import control

        def con_puntos():
            for _ in range(100):
                control.punto_seguro()
                time.sleep(0.05)
            return {"success": True}

        respuesta = asyncio.run(Ejecutor("agt-1", {"lenta": con_puntos}).procesar(
            _orden(capability="lenta", vence_en_ms=100)))
        assert respuesta["estado"] == "CANCELLED" and "vencida" in respuesta["resultado"]["error"]

    def test_lo_que_acaba_en_la_gracia_dice_que_acabo(self):
        """Reproducido en la 3.4-A: la nube recibía «no terminó a tiempo» y el archivo se
        escribía igual. Ahora recibe lo que de verdad pasó."""
        respuesta = asyncio.run(Ejecutor("agt-1", {"lenta": lambda: time.sleep(0.5) or {"success": True}})
                                .procesar(_orden(capability="lenta", vence_en_ms=100)))
        assert respuesta["estado"] == "COMPLETED"

    def test_lo_que_no_para_dice_que_sigue_y_el_diario_apunta_como_acabo(self, monkeypatch):
        from src.agente import ejecutor as modulo

        monkeypatch.setattr(modulo, "GRACIA", 0.1)
        ejecutor = Ejecutor("agt-1", {"lenta": lambda: time.sleep(0.6) or {"success": True}})
        respuesta = asyncio.run(ejecutor.procesar(_orden(capability="lenta", vence_en_ms=100)))
        assert respuesta["estado"] == "RUNNING" and respuesta["resultado"]["motivo"] == "sigue_en_marcha"
        time.sleep(1)
        assert ejecutor.diario.de("c1")["estado"] == "COMPLETED"

    def test_queda_auditado_en_el_pc(self):
        asyncio.run(Ejecutor("agt-1").procesar(_orden()))
        asyncio.run(Ejecutor("agt-1").procesar(_orden(command_id="c2", agent_id="agt-otro")))

        fases = [(e["fase"], e["command_id"]) for e in auditoria.leer()]
        assert ("recibida", "c1") in fases and ("ejecutada", "c1") in fases
        assert ("rechazada", "c2") in fases


# --- De extremo a extremo: servidor y agente de verdad --------------------------


@pytest.fixture
def servidor(nube):
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(nube, host="127.0.0.1", port=puerto, log_level="warning",
                                           ws="websockets-sansio"))
    hilo = threading.Thread(target=server.run, daemon=True)
    hilo.start()
    limite = time.monotonic() + 10
    while not server.started and time.monotonic() < limite:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{puerto}"
    server.should_exit = True
    hilo.join(10)


def _agente_en_hilo(base, agent_id, credencial, espera_minima=0.2):
    from src.agente.canal import Canal

    estados: list[str] = []
    canal = Canal(base, credencial, Ejecutor(agent_id),
                  al_cambiar=lambda e, d: estados.append(e.value), espera_minima=espera_minima)
    final: dict = {}
    hilo = threading.Thread(target=lambda: final.update(estado=asyncio.run(canal.correr())), daemon=True)
    hilo.start()
    return canal, hilo, estados, final


def _esperar(condicion, segundos=10):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.05)
    return False


class TestDeExtremoAExtremo:
    def test_el_agente_de_verdad_atiende_una_orden(self, nube, servidor):
        _, _, user_id, agent_id, credencial = _cuenta(nube)
        canal, hilo, estados, _ = _agente_en_hilo(servidor, agent_id, credencial)
        try:
            assert _esperar(lambda: REGISTRO.de(user_id) is not None)
            hilo_orden, resultado = _enviar_en_hilo(user_id)
            hilo_orden.join(10)
            assert resultado["valor"]["estado"] == "COMPLETED"
            assert resultado["valor"]["resultado"]["data"]["agent_id"] == agent_id
            assert "READY" in estados
        finally:
            canal.parar()
            hilo.join(10)

    def test_tras_un_reinicio_de_la_nube_vuelve_solo(self, nube, servidor):
        """El 1012 que manda la nube al apagarse: el agente se muda en el acto."""
        _, _, user_id, agent_id, credencial = _cuenta(nube)
        # Espera mínima de 3 s: si tratara el 1012 como un corte cualquiera, tardaría
        # eso o más. En el acto, bastante menos.
        canal, hilo, estados, _ = _agente_en_hilo(servidor, agent_id, credencial, espera_minima=3)
        try:
            assert _esperar(lambda: REGISTRO.de(user_id) is not None)
            primera = REGISTRO.de(user_id)
            asyncio.run_coroutine_threadsafe(
                REGISTRO.quitar(primera, 1012, "reinicio"), REGISTRO.loop
            ).result(5)

            inicio = time.monotonic()
            assert _esperar(lambda: REGISTRO.de(user_id) not in (None, primera), 5)
            # En el acto: < 2,5 s. Con la espera creciente serían 3 s o más (espera_minima=3).
            # No 1,5: con la suite entera en marcha fallaba de vez en cuando por lentitud.
            assert time.monotonic() - inicio < 2.5
            hilo_orden, resultado = _enviar_en_hilo(user_id)
            hilo_orden.join(10)
            assert "valor" in resultado, (repr(resultado.get("error")), estados)
            assert resultado["valor"]["estado"] == "COMPLETED"
        finally:
            canal.parar()
            hilo.join(10)

    def test_con_el_aviso_de_reinicio_se_muda_en_el_acto(self, nube, servidor):
        """El proxy de Render no reenvía el 1012 (medido): el aviso es un mensaje normal."""
        from src.canal.reinicio import avisar_reinicio

        _, _, user_id, agent_id, credencial = _cuenta(nube)
        canal, hilo, estados, _ = _agente_en_hilo(servidor, agent_id, credencial, espera_minima=3)
        try:
            assert _esperar(lambda: REGISTRO.de(user_id) is not None)
            primera = REGISTRO.de(user_id)

            avisados = asyncio.run_coroutine_threadsafe(avisar_reinicio(), REGISTRO.loop).result(5)

            assert avisados == 1
            inicio = time.monotonic()
            assert _esperar(lambda: REGISTRO.de(user_id) not in (None, primera), 5)
            # En el acto: < 2,5 s. Con la espera creciente serían 3 s o más (espera_minima=3).
            # No 1,5: con la suite entera en marcha fallaba de vez en cuando por lentitud.
            assert time.monotonic() - inicio < 2.5
        finally:
            canal.parar()
            hilo.join(10)

    def test_el_agente_contesta_los_latidos(self, nube, servidor, monkeypatch):
        from src.api.routes import agentes

        monkeypatch.setattr(agentes, "LATIDO", 0.3)
        _, _, user_id, agent_id, credencial = _cuenta(nube)
        canal, hilo, estados, _ = _agente_en_hilo(servidor, agent_id, credencial)
        try:
            assert _esperar(lambda: REGISTRO.de(user_id) is not None)
            primera = REGISTRO.de(user_id)
            time.sleep(2.5)  # más de ocho latidos: sin contestar, la nube lo habría echado
            assert REGISTRO.de(user_id) is primera
        finally:
            canal.parar()
            hilo.join(10)

    def test_revocado_se_para_y_no_insiste(self, nube, servidor):
        web, csrf, user_id, agent_id, credencial = _cuenta(nube)
        canal, hilo, estados, final = _agente_en_hilo(servidor, agent_id, credencial)
        assert _esperar(lambda: REGISTRO.de(user_id) is not None)

        web.delete(f"/auth/agentes/{agent_id}", headers={"x-morgan-csrf": csrf})

        hilo.join(10)
        assert final["estado"].value == "REVOKED"
        assert estados.count("CONNECTING") == 1  # no volvió a intentarlo


class TestElAvisoAlApagarse:
    def test_avisa_y_despues_deja_seguir_el_apagado(self, monkeypatch):
        """El manejador de SIGTERM avisa a los agentes y luego llama al de uvicorn."""
        import signal

        from src.canal import reinicio

        orden: list[str] = []

        async def avisar():
            orden.append("aviso")
            return 1

        monkeypatch.setattr(reinicio, "avisar_reinicio", avisar)
        monkeypatch.setattr(reinicio, "MARGEN", 0.05)
        anterior = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda sig, frame: orden.append("uvicorn"))
        try:
            async def probar():
                assert reinicio.instalar() is True
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                await asyncio.sleep(0.5)

            asyncio.run(probar())
        finally:
            signal.signal(signal.SIGTERM, anterior)

        assert orden == ["aviso", "uvicorn"]

    def test_fuera_del_hilo_principal_no_toca_las_senales(self):
        from src.canal import reinicio

        resultado = {}
        hilo = threading.Thread(target=lambda: resultado.update(v=reinicio.instalar(asyncio.new_event_loop())))
        hilo.start()
        hilo.join(5)
        assert resultado["v"] is False


class TestConUnProxyQueSeComeLosCodigos:
    """Medido en Render: su proxy no reenvía los códigos de cierre de un WebSocket. Aquí
    un servidor que manda el mensaje `cierre` y luego corta con 1000, como si el código
    de verdad se hubiera perdido por el camino."""

    @pytest.mark.parametrize("codigo, final", [(4401, "REVOKED"), (4426, "INCOMPATIBLE")])
    def test_el_agente_para_con_el_codigo_del_mensaje(self, codigo, final):
        from websockets.asyncio.server import serve

        from src.agente.canal import Canal

        conexiones = []

        async def atender(ws):
            conexiones.append(1)
            await ws.recv()  # el saludo
            await ws.send(json.dumps({"tipo": "cierre", "codigo": codigo, "motivo": "x"}))
            await ws.close(code=1000)  # el código de verdad, perdido

        async def probar():
            async with serve(atender, "127.0.0.1", 0) as servidor:
                puerto = servidor.sockets[0].getsockname()[1]
                canal = Canal(f"http://127.0.0.1:{puerto}", "mga_x", Ejecutor("agt-1"), espera_minima=0.1)
                # La URL del canal añade /agente/canal; al servidor de prueba le da igual.
                return await asyncio.wait_for(canal.correr(), timeout=10)

        assert asyncio.run(probar()).value == final
        assert len(conexiones) == 1  # paró a la primera: no reintentó

    def test_si_la_nube_calla_el_agente_reconecta_solo(self):
        """El caso medido en Render: el proxy deja la conexión colgada, sin cerrarla y
        sin tráfico. El agente lo nota por la falta de latidos y reconecta."""
        from websockets.asyncio.server import serve

        from src.agente.canal import Canal

        conexiones = []

        async def atender(ws):
            conexiones.append(time.monotonic())
            await ws.recv()  # el saludo
            await ws.send(json.dumps({"tipo": "bienvenida", "agent_id": "agt-1", "latido": 0.2}))
            await ws.wait_closed()  # y a callar, sin cerrar: el que cuelga es el agente

        async def probar():
            async with serve(atender, "127.0.0.1", 0) as servidor:
                puerto = servidor.sockets[0].getsockname()[1]
                canal = Canal(f"http://127.0.0.1:{puerto}", "mga_x", Ejecutor("agt-1"), espera_minima=0.1)
                tarea = asyncio.create_task(canal.correr())
                limite = time.monotonic() + 5
                while len(conexiones) < 2 and time.monotonic() < limite:
                    await asyncio.sleep(0.05)
                canal.parar()
                await asyncio.wait_for(tarea, 5)

        asyncio.run(probar())
        assert len(conexiones) >= 2
        # Dos latidos de 0,2 s sin noticias y la espera mínima: bastante menos de 2 s.
        assert conexiones[1] - conexiones[0] < 2


def _pausas(detalles: list[str]) -> list[float]:
    """Las pausas que el agente decidió antes de cada reconexión («…; vuelve en 0.3 s»).

    Se mide la decisión y no el tiempo entre conexiones (4.19): en la integración continua,
    con la máquina cargada, conectar llegó a sumar 0,8 s a cada hueco y las pruebas fallaban
    sin que la espera hubiera crecido."""
    return [float(m.group(1)) for d in detalles if (m := re.search(r"vuelve en ([\d.]+) s", d))]


class TestReconexionMedidaEnLa305:
    """Lo que enseñó el registro de mi agente en producción (3.0.5)."""

    def _correr(self, atender, hasta, espera_minima=0.3, **kw):
        from websockets.asyncio.server import serve

        from src.agente.canal import Canal

        detalles = []

        async def probar():
            async with serve(atender, "127.0.0.1", 0) as servidor:
                puerto = servidor.sockets[0].getsockname()[1]
                canal = Canal(f"http://127.0.0.1:{puerto}", "mga_x", Ejecutor("agt-1"),
                              espera_minima=espera_minima, al_cambiar=lambda e, d: detalles.append(d), **kw)
                tarea = asyncio.create_task(canal.correr())
                limite = time.monotonic() + 15
                while not hasta() and time.monotonic() < limite:
                    await asyncio.sleep(0.02)
                canal.parar()
                await asyncio.wait_for(tarea, 5)

        asyncio.run(probar())
        return detalles

    def test_tras_una_conexion_buena_la_espera_vuelve_a_empezar(self):
        """En mi PC la espera crecía entre cortes separados por horas: 1,1 → 2,3 →
        4,2 → 10,3 → 18,9 → 44,6 s. Cada corte tras una conexión buena es el primero."""
        conexiones = []

        async def atender(ws):
            conexiones.append(time.monotonic())
            await ws.recv()
            await ws.send(json.dumps({"tipo": "bienvenida", "agent_id": "agt-1", "latido": 5}))
            ws.transport.abort()          # un corte sin código, como los de Render

        detalles = self._correr(atender, lambda: len(conexiones) >= 5)
        pausas = _pausas(detalles)
        # Espera mínima 0,3 s (+ hasta la mitad de jitter). Creciendo: 0,3, 0,6, 1,2, 2,4…
        assert len(pausas) >= 4 and max(pausas) <= 0.5, pausas

    def test_si_nunca_llega_a_conectar_la_espera_si_crece(self):
        conexiones = []

        async def atender(ws):
            conexiones.append(time.monotonic())
            ws.transport.abort()          # ni saludo: no llega a READY

        detalles = self._correr(atender, lambda: len(conexiones) >= 4, espera_minima=0.2)
        pausas = _pausas(detalles)
        # 0,2-0,3 → 0,4-0,6 → 0,8-1,2: la tercera, siempre más del doble que la primera.
        assert len(pausas) >= 3 and pausas[2] > 2 * pausas[0], pausas

    def test_el_registro_dice_por_que_se_corto(self):
        """«cierre None» sin más no dejaba saber si fue la red, la nube o un despliegue."""
        conexiones = []

        async def atender(ws):
            conexiones.append(1)
            await ws.recv()
            await ws.send(json.dumps({"tipo": "bienvenida", "agent_id": "agt-1", "latido": 0.2}))
            await ws.wait_closed()

        detalles = self._correr(atender, lambda: len(conexiones) >= 2)
        assert any("sin latidos de la nube" in d for d in detalles), detalles

    def test_una_conexion_medio_muerta_no_retrasa_la_reconexion(self):
        """Tras dos latidos perdidos el agente cierra, y con la conexión medio muerta la
        respuesta al cierre no llega: la librería esperaba 10 s más por defecto. Aquí un
        intermediario que, como el proxy de Render, deja de pasar tráfico sin cerrar."""
        from websockets.asyncio.server import serve

        from src.agente.canal import CIERRE_MAXIMO, Canal

        conexiones = []

        async def atender(ws):
            conexiones.append(time.monotonic())
            await ws.recv()
            await ws.send(json.dumps({"tipo": "bienvenida", "agent_id": "agt-1", "latido": 0.2}))
            await ws.wait_closed()

        async def probar():
            async with serve(atender, "127.0.0.1", 0) as servidor:
                destino = servidor.sockets[0].getsockname()[1]
                primera = {"hecha": False}

                async def intermediario(lector, escritor):
                    callar = not primera["hecha"]
                    primera["hecha"] = True
                    r2, w2 = await asyncio.open_connection("127.0.0.1", destino)

                    async def pasar(de, a, subida):
                        inicio = time.monotonic()
                        while datos := await de.read(65536):
                            # La primera conexión enmudece a los 0,3 s en los dos sentidos.
                            if callar and time.monotonic() - inicio > 0.3:
                                continue
                            a.write(datos)
                            await a.drain()

                    await asyncio.gather(pasar(lector, w2, True), pasar(r2, escritor, False),
                                         return_exceptions=True)

                proxy = await asyncio.start_server(intermediario, "127.0.0.1", 0)
                puerto = proxy.sockets[0].getsockname()[1]
                canal = Canal(f"http://127.0.0.1:{puerto}", "mga_x", Ejecutor("agt-1"), espera_minima=0.1)
                tarea = asyncio.create_task(canal.correr())
                limite = time.monotonic() + 20
                while len(conexiones) < 2 and time.monotonic() < limite:
                    await asyncio.sleep(0.05)
                canal.parar()
                await asyncio.wait_for(tarea, 10)
                proxy.close()

        asyncio.run(probar())
        assert len(conexiones) >= 2
        # 0,3 s hablando + dos latidos de 0,2 + el cierre que no llega + la espera mínima.
        assert conexiones[1] - conexiones[0] < 0.3 + 0.4 + CIERRE_MAXIMO + 1.5


class TestRecuperarTrasUnCorte:
    """3.4: tras un corte con la orden en vuelo, la nube **pregunta** en vez de decir «no
    se sabe si llegó a hacerse». Contra un servidor de verdad (su bucle sigue vivo tras
    el corte, como en Render), con un agente simulado que se corta y vuelve."""

    @pytest.fixture(autouse=True)
    def _latido_largo(self, monkeypatch):
        """El agente simulado no contesta a los latidos: la nube lo echaría a los 15 s
        (tres latidos de 5). En tu PC estas pruebas tardan 0,5 s; en la integración continua,
        con la máquina atascada, una tardó 25 s y la nube la echó (4.19). Aquí se prueba qué
        pasa con la orden tras un corte; lo de los latidos tiene sus propias pruebas."""
        from src.api.routes import agentes

        monkeypatch.setattr(agentes, "LATIDO", 60.0)

    def _agente(self, servidor, credencial, protocolo=PROTOCOLO_ACTUAL):
        from websockets.sync.client import connect

        ws = connect(servidor.replace("http", "ws") + "/agente/canal",
                     additional_headers={"Authorization": f"Bearer {credencial}"})
        ws.send(json.dumps({**SALUDO, "protocol_version": protocolo}))
        assert json.loads(ws.recv())["tipo"] == "bienvenida"
        return ws

    @staticmethod
    def _siguiente(ws, tipo):
        while True:
            m = json.loads(ws.recv(timeout=10))
            if m.get("tipo") == tipo:
                return m

    @staticmethod
    def _resultado(cid, **datos):
        return {"tipo": "resultado", "command_id": cid, "estado": "COMPLETED",
                "resultado": {"success": True, "data": datos, "error": None}}

    def _cortar_y_volver(self, servidor, credencial, user_id):
        primero = self._agente(servidor, credencial)
        hilo, resultado = _enviar_en_hilo(user_id, plazo=20)
        orden = self._siguiente(primero, "orden")
        primero.close()
        segundo = self._agente(servidor, credencial)
        consulta = self._siguiente(segundo, "consultar")
        assert consulta["command_id"] == orden["command_id"]
        return segundo, orden["command_id"], hilo, resultado

    def test_si_termino_durante_el_corte_se_recoge_su_resultado(self, nube, servidor):
        _, _, user_id, _, credencial = _cuenta(nube)
        ws, cid, hilo, resultado = self._cortar_y_volver(servidor, credencial, user_id)
        ws.send(json.dumps({"tipo": "consulta", "command_id": cid, "estado": "COMPLETED",
                            "respuesta": self._resultado(cid, hecho=1)}))
        hilo.join(15)
        ws.close()
        assert resultado["valor"]["resultado"]["data"] == {"hecho": 1}

    def test_si_nunca_llego_se_reenvia_con_el_mismo_id(self, nube, servidor):
        _, _, user_id, _, credencial = _cuenta(nube)
        ws, cid, hilo, resultado = self._cortar_y_volver(servidor, credencial, user_id)
        ws.send(json.dumps({"tipo": "consulta", "command_id": cid, "estado": "DESCONOCIDA"}))
        otra = self._siguiente(ws, "orden")
        assert otra["command_id"] == cid
        ws.send(json.dumps(self._resultado(cid, segunda=True)))
        hilo.join(15)
        ws.close()
        assert resultado["valor"]["resultado"]["data"] == {"segunda": True}

    def test_si_sigue_en_marcha_se_espera(self, nube, servidor):
        _, _, user_id, _, credencial = _cuenta(nube)
        ws, cid, hilo, resultado = self._cortar_y_volver(servidor, credencial, user_id)
        ws.send(json.dumps({"tipo": "consulta", "command_id": cid, "estado": "RUNNING"}))
        time.sleep(0.3)
        ws.send(json.dumps(self._resultado(cid, al_final=True)))
        hilo.join(15)
        ws.close()
        assert resultado["valor"]["resultado"]["data"] == {"al_final": True}

    def test_una_lectura_sin_resultado_guardado_se_repite(self, nube, servidor):
        _, _, user_id, _, credencial = _cuenta(nube)
        ws, cid, hilo, resultado = self._cortar_y_volver(servidor, credencial, user_id)
        ws.send(json.dumps({"tipo": "consulta", "command_id": cid, "estado": "COMPLETED",
                            "respuesta": None, "repetible": True}))
        otra = self._siguiente(ws, "orden")
        assert otra["command_id"] == f"{cid}-r"
        ws.send(json.dumps(self._resultado(otra["command_id"], repetida=True)))
        hilo.join(15)
        ws.close()
        assert resultado["valor"]["resultado"]["data"] == {"repetida": True}

    def test_un_corte_en_plena_consulta_se_vuelve_a_preguntar(self, nube, servidor):
        _, _, user_id, _, credencial = _cuenta(nube)
        ws, cid, hilo, resultado = self._cortar_y_volver(servidor, credencial, user_id)
        ws.close()                                  # se corta otra vez sin contestar
        inicio = time.monotonic()
        tercero = self._agente(servidor, credencial)
        assert self._siguiente(tercero, "consultar")["command_id"] == cid
        tercero.send(json.dumps({"tipo": "consulta", "command_id": cid, "estado": "COMPLETED",
                                 "respuesta": self._resultado(cid, a_la_tercera=True)}))
        hilo.join(15)
        tercero.close()
        assert resultado["valor"]["resultado"]["data"] == {"a_la_tercera": True}
        assert time.monotonic() - inicio < 5        # no esperó los 10 s de la consulta

    def test_si_no_vuelve_se_dice_que_se_desconecto(self, nube, servidor):
        _, _, user_id, _, credencial = _cuenta(nube)
        primero = self._agente(servidor, credencial)
        hilo, resultado = _enviar_en_hilo(user_id, plazo=20)
        self._siguiente(primero, "orden")
        primero.close()
        hilo.join(15)
        assert isinstance(resultado["error"], despacho.AgenteDesconectado)

    def test_un_estado_o_consulta_de_otra_orden_no_cuenta(self, nube, servidor):
        """Solo los de órdenes en vuelo: un agente no inventa progreso de nada."""
        _, _, user_id, _, credencial = _cuenta(nube)
        ws = self._agente(servidor, credencial)
        hilo, resultado = _enviar_en_hilo(user_id, plazo=20)
        orden = self._siguiente(ws, "orden")
        ws.send(json.dumps({"tipo": "estado", "command_id": "inventada", "estado": "RUNNING"}))
        ws.send(json.dumps({"tipo": "consulta", "command_id": "inventada", "estado": "COMPLETED"}))
        ws.send(json.dumps(self._resultado(orden["command_id"], ok=1)))
        hilo.join(15)
        ws.close()
        assert resultado["valor"]["resultado"]["data"] == {"ok": 1}


class TestDetener:
    """3.4, decisión mía: «Detener» en la web cancela lo que se hace en el PC."""

    def _enviar_con_parada(self, user_id, parada, eventos):
        from src.canal import paradas
        from src.eventos_turno import CanalDelTurno, escuchando

        resultado: dict = {}
        canal = CanalDelTurno()

        def correr():
            marca = paradas.PARADA.set(parada)
            try:
                with como_usuario(user_id), escuchando(canal):
                    resultado["valor"] = despacho.enviar("estado", {}, plazo=20)
            except Exception as exc:
                resultado["error"] = exc
            finally:
                paradas.PARADA.reset(marca)
                while not canal.cola.empty():
                    eventos.append(canal.cola.get_nowait())

        hilo = threading.Thread(target=correr, daemon=True)
        hilo.start()
        return hilo, resultado

    def test_detener_manda_cancelar_al_pc(self, nube, servidor):
        _, _, user_id, _, credencial = _cuenta(nube)
        agente = TestRecuperarTrasUnCorte()
        ws = agente._agente(servidor, credencial)
        parada, eventos = threading.Event(), []
        hilo, resultado = self._enviar_con_parada(user_id, parada, eventos)
        orden = agente._siguiente(ws, "orden")
        ws.send(json.dumps({"tipo": "estado", "command_id": orden["command_id"], "estado": "RUNNING"}))
        time.sleep(0.5)
        parada.set()
        cancelar = agente._siguiente(ws, "cancelar")
        assert cancelar["command_id"] == orden["command_id"]
        ws.send(json.dumps({"tipo": "resultado", "command_id": orden["command_id"], "estado": "CANCELLED",
                            "resultado": {"success": False, "data": None, "error": "Cancelada (la persona)."}}))
        hilo.join(15)
        ws.close()
        assert resultado["valor"]["estado"] == "CANCELLED"
        equipo = [e for e in eventos if e.get("tipo") == "equipo"]
        assert equipo and equipo[0]["estado"] == "RUNNING"

    def test_un_estado_de_otra_orden_no_se_cuenta(self, nube, servidor):
        """Un agente no puede inventarse progreso: solo cuentan los de la orden en vuelo."""
        _, _, user_id, _, credencial = _cuenta(nube)
        agente = TestRecuperarTrasUnCorte()
        ws = agente._agente(servidor, credencial)
        eventos = []
        hilo, resultado = self._enviar_con_parada(user_id, threading.Event(), eventos)
        orden = agente._siguiente(ws, "orden")
        ws.send(json.dumps({"tipo": "estado", "command_id": "inventada", "estado": "PENDING", "posicion": 9}))
        time.sleep(0.5)
        ws.send(json.dumps(agente._resultado(orden["command_id"], ok=1)))
        hilo.join(15)
        ws.close()
        assert resultado["valor"]["estado"] == "COMPLETED"
        assert [e for e in eventos if e.get("tipo") == "equipo"] == []

    def test_un_turno_parado_no_manda_nada_nuevo(self, nube):
        _, _, user_id, _, _ = _cuenta(nube)
        parada = threading.Event()
        parada.set()
        hilo, resultado = self._enviar_con_parada(user_id, parada, [])
        hilo.join(5)
        assert isinstance(resultado["error"], despacho.TurnoParado)

    def test_solo_lo_para_su_duenno(self):
        from src.canal import paradas

        turno, parada = paradas.abrir("ana")
        assert paradas.parar(turno, "bea") is False and not parada.is_set()
        assert paradas.parar("inventado", "ana") is False
        assert paradas.parar(turno, "ana") is True and parada.is_set()
        paradas.cerrar(turno)
        assert paradas.parar(turno, "ana") is False

    def test_la_ruta_parar(self, nube):
        from src.canal import paradas

        web, csrf, user_id, _, _ = _cuenta(nube)
        turno, parada = paradas.abrir(user_id)
        r = web.post("/chat/parar", json={"turno": turno}, headers={"X-Morgan-CSRF": csrf})
        assert r.status_code == 200 and r.json()["parado"] is True and parada.is_set()
        paradas.cerrar(turno)


class TestDePuntaAPuntaConElMotor:
    """3.4 con el agente de verdad (canal, ejecutor y diario) y la nube de verdad: un
    corte real a mitad de una operación, y un «Detener» real."""

    def _agente(self, servidor, agent_id, credencial, capacidades):
        from src.agente.canal import Canal

        canal = Canal(servidor, credencial, Ejecutor(agent_id, capacidades), espera_minima=0.1)
        hilo = threading.Thread(target=lambda: asyncio.run(canal.correr()), daemon=True)
        hilo.start()
        return canal, hilo

    def test_un_corte_a_mitad_se_recupera_y_no_se_hace_dos_veces(self, nube, servidor, monkeypatch):
        from src.agente import control

        monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 5.0)
        _, _, user_id, agent_id, credencial = _cuenta(nube)
        veces = []

        def lenta():
            for _ in range(15):
                control.punto_seguro()
                time.sleep(0.1)
            veces.append(1)
            return {"success": True, "data": {"hecho": len(veces)}, "error": None}

        canal, hilo = self._agente(servidor, agent_id, credencial, {"lenta": lenta})
        try:
            assert _esperar(lambda: REGISTRO.de(user_id) is not None)
            primera = REGISTRO.de(user_id)
            hilo_orden, resultado = _enviar_en_hilo(user_id, "lenta", plazo=20)
            time.sleep(0.5)
            # Un corte de verdad a mitad: la nube cierra la conexión del agente.
            asyncio.run_coroutine_threadsafe(REGISTRO.quitar(primera, 1012, "corte"), REGISTRO.loop).result(5)
            hilo_orden.join(20)
            assert resultado["valor"]["estado"] == "COMPLETED"
            assert resultado["valor"]["resultado"]["data"] == {"hecho": 1}
            time.sleep(0.5)
            assert veces == [1]          # una sola vez
        finally:
            canal.parar()
            hilo.join(10)

    def test_detener_para_la_operacion_en_el_pc(self, nube, servidor):
        from src.agente import control
        from src.canal import paradas

        _, _, user_id, agent_id, credencial = _cuenta(nube)
        vueltas = []

        def larga():
            for i in range(100):
                control.punto_seguro()
                vueltas.append(i)
                time.sleep(0.05)
            return {"success": True, "data": {}, "error": None}

        canal, hilo = self._agente(servidor, agent_id, credencial, {"larga": larga})
        try:
            assert _esperar(lambda: REGISTRO.de(user_id) is not None)
            parada = threading.Event()
            resultado = {}

            def turno():
                marca = paradas.PARADA.set(parada)
                try:
                    with como_usuario(user_id):
                        resultado["valor"] = despacho.enviar("larga", {}, plazo=20)
                finally:
                    paradas.PARADA.reset(marca)

            hilo_turno = threading.Thread(target=turno, daemon=True)
            inicio = time.monotonic()
            hilo_turno.start()
            time.sleep(0.6)
            parada.set()
            hilo_turno.join(10)
            assert resultado["valor"]["estado"] == "CANCELLED"
            assert time.monotonic() - inicio < 3            # no esperó a que acabara (5 s)
            assert len(vueltas) < 100
        finally:
            canal.parar()
            hilo.join(10)
