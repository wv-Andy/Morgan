"""
Rotar la credencial del agente (3.8).

Decisión mía (2026-09-25): **sola cada 90 días, y a mano con `rotar`**, en dos pasos:
la nueva queda pendiente y la vieja vale hasta que el agente se conecta con la nueva.
Lo que haría daño si fallase: que un corte a mitad deje el PC sin credencial que valga,
que la vieja siga valiendo después de estrenar la nueva, o que rotar resucite un equipo
revocado.
"""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.agente import estado as almacen
from src.agente.ejecutor import Ejecutor
from src.canal.registro import REGISTRO
from src.identidad.agentes import ROTAR_CADA, ServicioDeAgentes
from src.identidad.repositorio import repositorio_de_cuentas
from tests.test_canal_agente import _cuenta, _esperar, nube, servidor  # noqa: F401


def _servicio():
    from src.api.dependencies import get_container

    return ServicioDeAgentes(repositorio_de_cuentas(get_container().repositories))


def _vale(nube, credencial) -> bool:
    r = TestClient(nube, headers={"Authorization": f"Bearer {credencial}"}).get("/agente/historial")
    return r.status_code == 200


class TestEnDosPasos:
    def test_la_vieja_vale_hasta_que_se_estrena_la_nueva(self, nube):
        _, _, _, _, vieja = _cuenta(nube)
        nueva = _servicio().rotar(vieja)
        assert nueva and nueva != vieja and nueva.startswith("mga_")
        assert _vale(nube, vieja), "un corte antes de estrenarla no deja el PC sin credencial"
        assert _vale(nube, nueva)                 # estrenarla…
        assert not _vale(nube, vieja), "…mata la vieja"
        assert _vale(nube, nueva)

    def test_pedir_otra_antes_de_estrenarla_sustituye_la_pendiente(self, nube):
        _, _, _, _, vieja = _cuenta(nube)
        primera = _servicio().rotar(vieja)
        segunda = _servicio().rotar(vieja)
        assert not _vale(nube, primera)
        assert _vale(nube, segunda) and not _vale(nube, vieja)

    def test_revocar_mata_las_dos(self, nube):
        web, csrf, _, agent_id, vieja = _cuenta(nube)
        nueva = _servicio().rotar(vieja)
        web.delete(f"/auth/agentes/{agent_id}", headers={"x-morgan-csrf": csrf})
        assert not _vale(nube, vieja) and not _vale(nube, nueva)
        assert _servicio().rotar(vieja) is None, "rotar no resucita un equipo revocado"

    def test_estrenada_una_vez_no_se_vuelve_a_estrenar(self, nube):
        """Si la pendiente no se vaciara, cada conexión la «estrenaría» otra vez y la fecha
        de la credencial se reiniciaría: nunca tocaría rotar."""
        web, _, _, _, vieja = _cuenta(nube)
        nueva = _servicio().rotar(vieja)
        assert _vale(nube, nueva)
        primera = web.get("/auth/agentes").json()["agentes"][0]["credencial_rotada_en"]
        time.sleep(0.05)
        assert _vale(nube, nueva)
        assert web.get("/auth/agentes").json()["agentes"][0]["credencial_rotada_en"] == primera

    def test_solo_desde_la_actual(self, nube):
        """Dejar una pendiente exige presentar la actual: una carrera con otra rotación no
        deja dos a la vez."""
        _, _, _, agent_id, vieja = _cuenta(nube)
        repo = _servicio().repo
        assert not repo.preparar_rotacion(agent_id, "no-es-la-actual", "x" * 64)
        assert repo.preparar_rotacion(agent_id, __import__("hashlib").sha256(vieja.encode()).hexdigest(), "y" * 64)

    def test_si_no_se_pudo_dejar_pendiente_no_da_credencial(self, nube, monkeypatch):
        _, _, _, _, vieja = _cuenta(nube)
        servicio = _servicio()
        monkeypatch.setattr(servicio.repo, "preparar_rotacion", lambda *a: False)
        assert servicio.rotar(vieja) is None

    def test_la_nueva_de_uno_no_vale_para_otro(self, nube):
        _, _, _, _, de_ana = _cuenta(nube, "ana")
        _, _, _, id_bea, de_bea = _cuenta(nube, "bea")
        nueva = _servicio().rotar(de_ana)
        resuelto = _servicio().resolver(nueva)
        assert resuelto["agente"]["id"] != id_bea


class TestCadaNoventaDias:
    def test_toca_rotar_por_la_edad_de_la_credencial(self, nube):
        _, _, _, agent_id, vieja = _cuenta(nube)
        servicio = _servicio()
        assert not servicio.toca_rotar(servicio.resolver(vieja))
        servicio.repo.tocar_agente(agent_id, {"creado_en": time.time() - ROTAR_CADA - 60})
        assert servicio.toca_rotar(servicio.resolver(vieja))
        nueva = servicio.rotar(vieja)
        assert not servicio.toca_rotar(servicio.resolver(nueva)), "estrenada, cuenta desde ahora"

    def test_la_web_ve_cuando_se_roto(self, nube):
        web, _, _, _, vieja = _cuenta(nube)
        assert web.get("/auth/agentes").json()["agentes"][0]["credencial_rotada_en"] is None
        _vale(nube, _servicio().rotar(vieja))
        assert web.get("/auth/agentes").json()["agentes"][0]["credencial_rotada_en"] > time.time() - 60


class TestLaRuta:
    def test_sin_credencial_no(self, nube):
        assert TestClient(nube).post("/agente/rotar").status_code == 401
        r = TestClient(nube, headers={"Authorization": "Bearer mga_inventada"}).post("/agente/rotar")
        assert r.status_code == 401

    def test_con_la_suya(self, nube):
        _, _, _, _, vieja = _cuenta(nube)
        r = TestClient(nube, headers={"Authorization": f"Bearer {vieja}"}).post("/agente/rotar")
        assert r.status_code == 200 and _vale(nube, r.json()["credencial"])

    def test_un_token_personal_no(self, nube):
        web, csrf, *_ = _cuenta(nube)
        r = web.post("/auth/tokens", json={"nombre": "t", "alcances": ["lectura", "escritura"]},
                     headers={"x-morgan-csrf": csrf})
        assert r.status_code == 200, r.text
        token = r.json()["valor"]
        assert token.startswith("mgn_")
        r = TestClient(nube, headers={"Authorization": f"Bearer {token}"}).post("/agente/rotar")
        assert r.status_code == 401


class TestElAgenteRotaSolo:
    """Con un agente de verdad: la nube dice en la bienvenida que toca, el agente pide una
    nueva, la guarda cifrada y se reconecta con ella."""

    def _agente(self, servidor, agent_id, credencial, rotar):
        from src.agente.canal import Canal

        almacen.guardar(almacen.Emparejamiento(agent_id, servidor, "PC", "ana", time.time()), credencial)
        canal = Canal(servidor, credencial, Ejecutor(agent_id), espera_minima=0.1, rotar_credencial=rotar)
        hilo = threading.Thread(target=lambda: asyncio.run(canal.correr()), daemon=True)
        hilo.start()
        return canal, hilo

    def test_de_punta_a_punta(self, nube, servidor):
        from src.agente.emparejar import rotar_credencial

        _, _, ana, agent_id, vieja = _cuenta(nube)
        _servicio().repo.tocar_agente(agent_id, {"creado_en": time.time() - ROTAR_CADA - 60})
        canal, hilo = self._agente(servidor, agent_id, vieja, rotar_credencial)
        try:
            assert _esperar(lambda: canal.credencial != vieja, 15), "no rotó"
            assert almacen.credencial() == canal.credencial, "la nueva, guardada en el PC"
            assert _esperar(lambda: not _vale(nube, vieja), 15), "la vieja sigue valiendo"
            assert _esperar(lambda: REGISTRO.de(ana, agent_id) is not None, 10)
            time.sleep(1)
            assert canal.estado.value == "READY", "reconectó con la nueva"
            assert not _servicio().toca_rotar(_servicio().resolver(canal.credencial))
        finally:
            canal.parar()
            hilo.join(10)

    def test_si_falla_sigue_con_la_que_tiene(self, nube, servidor):
        _, _, ana, agent_id, vieja = _cuenta(nube)
        _servicio().repo.tocar_agente(agent_id, {"creado_en": time.time() - ROTAR_CADA - 60})
        intentos = []

        def rota_mal(credencial):
            intentos.append(credencial)
            raise OSError("sin red")
        canal, hilo = self._agente(servidor, agent_id, vieja, rota_mal)
        try:
            assert _esperar(lambda: intentos, 10)
            time.sleep(1)
            assert canal.credencial == vieja and canal.estado.value == "READY" and _vale(nube, vieja)
            assert almacen.credencial() == vieja
        finally:
            canal.parar()
            hilo.join(10)

    def test_con_la_credencial_joven_no_rota(self, nube, servidor):
        _, _, _, agent_id, vieja = _cuenta(nube)
        intentos = []
        canal, hilo = self._agente(servidor, agent_id, vieja, lambda c: intentos.append(c) or c)
        try:
            assert _esperar(lambda: canal.estado.value == "READY", 10)
            time.sleep(1)
            assert intentos == []
        finally:
            canal.parar()
            hilo.join(10)
