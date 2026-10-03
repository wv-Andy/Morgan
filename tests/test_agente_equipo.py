"""
El PC por dentro (4.7): `system_info` ampliada y `pc_diagnostics` (rendimiento, red,
puertos, servicios). Solo lectura y sin administrador.

Lo que haría daño si fallase: que `pc_diagnostics` respondiera sin haberla encendido
(nace apagada), que saliera algo que identifica el equipo o a la persona (direcciones
físicas, etiquetas, usuario), que una parte rota se llevara a las demás, o que un dato
dijera otra cosa de la que es (Windows 11 que el registro llama «Windows 10»).
"""

import json
import re
from collections import namedtuple

import psutil
import pytest

from src.agente import equipo
from src.agente.politica import DE_DIAGNOSTICO, Politica


@pytest.fixture
def encendida():
    politica = Politica()
    politica.capacidades["pc_diagnostics"] = True
    politica.guardar()


class TestNaceApagada:
    def test_apagada_no_responde(self):
        Politica().guardar()
        r = equipo.pc_diagnostics("rendimiento")
        assert not r["success"] and r["motivo"] == "capacidad_apagada"

    def test_esta_entre_las_capacidades_y_apagada(self):
        assert DE_DIAGNOSTICO == ("pc_diagnostics",)
        assert Politica().capacidades["pc_diagnostics"] is False

    def test_un_aspecto_raro_se_dice(self, encendida):
        r = equipo.pc_diagnostics("temperaturas")
        assert not r["success"] and "rendimiento" in r["error"]


class TestLosAspectos:
    @pytest.mark.parametrize("aspecto", ["rendimiento", "red", "puertos", "servicios"])
    def test_cada_uno_contesta(self, encendida, aspecto):
        r = equipo.pc_diagnostics(aspecto)
        assert r["success"], r

    def test_rendimiento_dice_que_consume(self, encendida):
        d = equipo.pc_diagnostics("rendimiento")["data"]
        assert {"cpu_pct", "memoria_usada_pct", "mas_cpu", "mas_memoria", "discos"} <= set(d)
        assert 0 < len(d["mas_memoria"]) <= equipo.MAX_PROCESOS
        assert all(set(p) == {"nombre", "pid", "cpu_pct", "memoria"} for p in d["mas_cpu"] + d["mas_memoria"])

    def test_con_la_memoria_llena_lo_dice(self, encendida, monkeypatch):
        real = psutil.virtual_memory()
        monkeypatch.setattr(psutil, "virtual_memory", lambda: real._replace(percent=97.0))
        assert any("memoria" in p for p in equipo.pc_diagnostics("rendimiento")["data"]["pistas"])

    def test_la_red_sin_direcciones_fisicas(self, encendida):
        texto = json.dumps(equipo.pc_diagnostics("red")["data"])
        assert not re.search(r"([0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}", texto)


Conexion = namedtuple("Conexion", "type status laddr raddr pid")
Dir = namedtuple("Dir", "ip port")


class TestPuertos:
    def _conexiones(self, monkeypatch):
        conexiones = [
            Conexion(1, psutil.CONN_LISTEN, Dir("0.0.0.0", 8000), None, 111),
            Conexion(1, psutil.CONN_LISTEN, Dir("127.0.0.1", 5432), None, 222),
            Conexion(1, psutil.CONN_ESTABLISHED, Dir("192.168.1.2", 50000), Dir("1.1.1.1", 443), 111),
            Conexion(2, psutil.CONN_NONE, Dir("0.0.0.0", 5353), None, 333),
        ]
        monkeypatch.setattr(psutil, "net_connections", lambda kind="inet": conexiones)

        class Proceso:
            def __init__(self, pid):
                self.pid = pid

            def name(self):
                return {111: "python.exe", 222: "postgres.exe", 333: "mdns.exe"}[self.pid]

        monkeypatch.setattr(psutil, "Process", Proceso)

    def test_sin_filtro_solo_tcp_que_escucha(self, encendida, monkeypatch):
        self._conexiones(monkeypatch)
        d = equipo.pc_diagnostics("puertos")["data"]
        assert [(e["puerto"], e["programa"], e["abierto_a"]) for e in d["escuchando"]] == [
            (5432, "postgres.exe", "solo este PC"), (8000, "python.exe", "la red")]
        assert "1 puertos UDP" in d["udp"]

    def test_con_filtro_tambien_udp(self, encendida, monkeypatch):
        self._conexiones(monkeypatch)
        d = equipo.pc_diagnostics("puertos", filtro="mdns")["data"]
        assert [(e["puerto"], e["protocolo"]) for e in d["escuchando"]] == [(5353, "UDP")]


class TestServicios:
    def _servicios(self, monkeypatch):
        class Servicio:
            def __init__(self, nombre, estado, inicio, roto=False):
                self._n, self._e, self._i, self._roto = nombre, estado, inicio, roto

            def name(self):
                return self._n

            def display_name(self):
                return f"Servicio {self._n}"

            def status(self):
                if self._roto:
                    raise OSError("QueryServiceConfig2W")     # medido en mi PC
                return self._e

            def start_type(self):
                return self._i

        lista = [Servicio("Spooler", "stopped", "automatic"), Servicio("WinDefend", "running", "automatic"),
                 Servicio("Fax", "stopped", "manual"), Servicio("Raro", "running", "automatic", roto=True)]
        monkeypatch.setattr(psutil, "win_service_iter", lambda: iter(lista), raising=False)

    def test_los_automaticos_parados(self, encendida, monkeypatch):
        self._servicios(monkeypatch)
        d = equipo.pc_diagnostics("servicios")["data"]
        assert d["total"] == 3 and d["en_marcha"] == 1, "el que no se deja leer se salta"
        assert [s["nombre"] for s in d["automaticos_parados"]] == ["Spooler"]

    def test_por_nombre(self, encendida, monkeypatch):
        self._servicios(monkeypatch)
        d = equipo.pc_diagnostics("servicios", filtro="defend")["data"]
        assert [s["nombre"] for s in d["coinciden"]] == ["WinDefend"]


class TestSystemInfo:
    @pytest.mark.parametrize("compilacion, esperado", [("26200", "Windows 11 Pro"), ("19045", "Windows 10 Pro")])
    def test_windows_11_aunque_el_registro_diga_10(self, monkeypatch, compilacion, esperado):
        monkeypatch.setattr(equipo, "_registro", lambda ruta, *n: ["Windows 10 Pro", "25H2", compilacion])
        assert equipo.windows().startswith(esperado)

    def test_una_parte_rota_no_se_lleva_las_demas(self, monkeypatch):
        def rota():
            raise RuntimeError("el registro no contesta")

        monkeypatch.setattr(equipo, "graficas", rota)
        d = equipo.resumen()
        assert "graficas" not in d and "discos" in d and d["temperaturas"] == equipo.SIN_TEMPERATURAS

    def test_el_tamano_en_palabras(self):
        assert all(re.search(r"\d+(,\d)? (bytes|KB|MB|GB|TB)$", d["libre"]) for d in equipo.discos())


class TestEnLaNube:
    def test_una_sola_herramienta_verde_y_sin_plan(self):
        from src.canal.herramientas import DiagnosticoDelEquipo, herramientas_del_equipo

        # Una sola para el PC por dentro, con `aspecto`; `pc_context` (4.13) va en la misma familia.
        (h,) = [h for h in herramientas_del_equipo()
                if isinstance(h, DiagnosticoDelEquipo) and h.name != "pc_context"]
        assert h.name == "pc_diagnostics" and h.permission_level == "safe" and not h.exige_plan
        assert h.parameters["properties"]["aspecto"]["enum"] == list(equipo.ASPECTOS)

    def test_lo_que_devuelve_cuenta_como_leido_del_pc(self):
        from src.agent import fuga

        assert "pc_diagnostics" in fuga.DEL_PC

    def test_system_info_dice_lo_que_trae(self):
        """Medido con el modelo real (4.7): con la descripción de antes, a «¿cuánta batería
        me queda?» contestó que no tenía herramienta."""
        from src.canal.herramientas import herramientas_del_equipo

        (h,) = [h for h in herramientas_del_equipo() if h.name == "system_info"]
        assert all(p in h.description for p in ("batería", "discos", "gráfica"))
