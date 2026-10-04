"""
Lo que la bandeja de Morgan para Windows (5.1) le pide al agente (`src/agente/bandeja.py`).

El criterio de la 5.1: **pausar corta lo que esté en marcha en menos de 2 s**, medido. Aquí,
con procesos de verdad que hacen de agente: uno que obedece la señal de parada y otro que no
(con un hijo, como un programa de la terminal que no acaba), que hay que cortar. Y que una
pausa **no se salta** al iniciar sesión, al relanzar el vigilante ni al activar el arranque;
que solo se corta lo que es del agente (un pid reutilizado por otro programa, no); lo último
que hizo, sin tocar el diario; y el aviso de versión nueva.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from src.agente import arranque, bandeja, control, vigilante
from src.agente import estado as almacen
from src.agente.ejecutor import Ejecutor
from src.agente.estado import Emparejamiento
from src.agente.protocolo import PROTOCOLO_ACTUAL

RAIZ = Path(__file__).resolve().parents[1]

OBEDIENTE = """
import os, sys, threading, time
sys.path.insert(0, {raiz!r})
from src.agente import arranque, salud
candado = arranque.Candado().tomar()
salud.apuntar(pid=os.getpid(), arrancado=time.time())
print("listo", flush=True)
arranque.vigilar_parada(lambda: None)
candado.soltar()
"""

VIGILANTE = """
import sys, time
sys.path.insert(0, {raiz!r})
from src.agente import arranque, bandeja
candado = arranque.Candado("vigilante.lock").tomar()
bandeja.apuntar_vigilante()
print("listo", flush=True)
time.sleep(120)       # como el de verdad: mira a su agente cada 5 s, no la señal
"""

TERCO = """
import os, subprocess, sys, time
sys.path.insert(0, {raiz!r})
from src.agente import arranque, salud
import psutil
candado = arranque.Candado().tomar()
salud.apuntar(pid=os.getpid(), arrancado=psutil.Process().create_time())
hijo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print(hijo.pid, flush=True)
time.sleep(120)       # no mira la señal: hay que cortarlo
"""


def _lanzar(codigo: str, candado: str = "agente.lock") -> tuple[subprocess.Popen, str]:
    proceso = subprocess.Popen([sys.executable, "-c", codigo.format(raiz=str(RAIZ))],
                               stdout=subprocess.PIPE, text=True)
    primera = proceso.stdout.readline().strip()
    limite = time.monotonic() + 10
    while not arranque.en_marcha(candado) and time.monotonic() < limite:
        time.sleep(0.05)
    assert arranque.en_marcha(candado), "el proceso de prueba no cogió el candado"
    return proceso, primera


def _emparejado():
    almacen.guardar(Emparejamiento("agt-1", "http://nube", "PC", "ana@x.com", time.time()), "mga_prueba")


class TestPausar:
    def test_sin_nada_en_marcha_deja_la_marca(self):
        assert bandeja.pausar() == "En pausa."
        assert bandeja.en_pausa()

    def test_un_agente_que_obedece_se_va_solo_y_rapido(self):
        proceso, _ = _lanzar(OBEDIENTE)
        cortes = []
        try:
            empieza = time.monotonic()
            texto = bandeja.pausar(cortar=lambda procesos: cortes.extend(procesos) or 0)
            tardo = time.monotonic() - empieza
            assert proceso.wait(timeout=5) == 0
        finally:
            proceso.kill()
        assert texto == "En pausa." and cortes == []
        assert tardo < 2.0, f"tardó {tardo:.2f} s"
        assert not arranque.senal_de_parada().exists()

    def test_el_vigilante_se_corta_aunque_el_agente_obedezca(self):
        """El vigilante de verdad mira a su agente cada 5 s: tardaría eso en irse."""
        vigilante_, _ = _lanzar(VIGILANTE, "vigilante.lock")
        agente_, _ = _lanzar(OBEDIENTE)
        try:
            empieza = time.monotonic()
            texto = bandeja.pausar()
            tardo = time.monotonic() - empieza
            assert agente_.wait(timeout=5) == 0          # el agente se fue solo
            assert vigilante_.wait(timeout=5) is not None
        finally:
            agente_.kill()
            vigilante_.kill()
        assert texto == "En pausa."
        assert tardo < 2.0, f"tardó {tardo:.2f} s"
        assert not arranque.en_marcha("vigilante.lock")

    def test_uno_que_no_obedece_se_corta_con_su_hijo_en_menos_de_2_s(self):
        proceso, hijo = _lanzar(TERCO)
        try:
            empieza = time.monotonic()
            texto = bandeja.pausar()
            tardo = time.monotonic() - empieza
            assert proceso.wait(timeout=5) is not None
            limite = time.monotonic() + 5
            while psutil.pid_exists(int(hijo)) and time.monotonic() < limite:
                time.sleep(0.05)
            hijo_vivo = psutil.pid_exists(int(hijo)) and psutil.Process(int(hijo)).status() != psutil.STATUS_ZOMBIE
        finally:
            proceso.kill()
            try:
                psutil.Process(int(hijo)).kill()
            except psutil.Error:
                pass
        assert texto.startswith("En pausa (cortado")
        assert not hijo_vivo, "el hijo del agente siguió vivo"
        assert tardo < 2.0, f"tardó {tardo:.2f} s"
        assert bandeja.en_pausa() and not arranque.senal_de_parada().exists()

    def test_un_pid_que_ya_es_de_otro_programa_no_se_toca(self):
        """El pid apuntado sigue vivo, pero arrancó en otro momento: es de otro programa."""
        yo = psutil.Process()
        assert bandeja._proceso_si_es(yo.pid, yo.create_time()) is not None
        assert bandeja._proceso_si_es(yo.pid, yo.create_time() - 3600) is None
        assert bandeja._proceso_si_es(None, time.time()) is None

    def test_el_agente_mira_la_senal_a_menudo(self):
        """Mirarla cada 2 s, como antes, ya se comía el plazo entero de la pausa."""
        assert arranque.MIRAR_PARADA <= 0.5


class TestLaPausaNoSeSalta:
    def test_reanudar_quita_la_marca_y_lanza(self, monkeypatch):
        _emparejado()
        bandeja.pausar()
        lanzados = []
        monkeypatch.setattr(arranque, "lanzar_en_segundo_plano", lambda *a, **k: lanzados.append(1))
        assert bandeja.reanudar() == "Reanudado."
        assert not bandeja.en_pausa() and lanzados == [1]

    def test_reanudar_sin_emparejar_no_lanza(self, monkeypatch):
        bandeja.pausar()
        lanzados = []
        monkeypatch.setattr(arranque, "lanzar_en_segundo_plano", lambda *a, **k: lanzados.append(1))
        assert bandeja.reanudar() == "Este PC no está emparejado."
        assert lanzados == []

    def test_el_vigilante_no_lanza_en_pausa(self):
        bandeja.pausar()
        lanzados = []
        codigo = vigilante.vigilar(lanzar=lambda: lanzados.append(1), relevo=lambda: None,
                                   por_confirmar=lambda: False)
        assert codigo == 0 and lanzados == []

    def test_el_agente_no_conecta_en_pausa(self, monkeypatch):
        from src.agente import __main__ as consola

        from src.agente import canal

        _emparejado()
        bandeja.pausar()
        conectados = []

        class SinRed:
            """Si se saltara la pausa, conectaría de verdad y la prueba no acabaría nunca."""

            def __init__(self, *a, **k):
                conectados.append(1)

            def parar(self):
                pass

            async def correr(self):
                from src.agente.estado import EstadoAgente

                return EstadoAgente.DISCONNECTED

        monkeypatch.setattr(canal, "Canal", SinRed)
        assert consola.main(["conectar"]) == 0
        assert conectados == []

    def test_activar_el_arranque_no_lo_lanza_en_pausa(self, monkeypatch, capsys):
        from src.agente import __main__ as consola

        _emparejado()
        bandeja.pausar()
        lanzados = []
        monkeypatch.setattr(arranque, "activar", lambda *a, **k: Path("acceso.lnk"))
        monkeypatch.setattr(arranque, "lanzar_en_segundo_plano", lambda *a, **k: lanzados.append(1))
        assert consola.main(["arranque", "activar"]) == 0
        assert lanzados == [] and "En pausa" in capsys.readouterr().out

    def test_emparejar_de_nuevo_la_quita(self, monkeypatch):
        from src.agente import __main__ as consola

        bandeja.pausar()
        monkeypatch.setattr(consola, "emparejar", lambda *a, **k: True)
        consola.main(["emparejar", "--codigo", "ABCD-EFGH", "--si"])
        assert not bandeja.en_pausa()

    def test_un_emparejado_fallido_no_la_quita(self, monkeypatch):
        from src.agente import __main__ as consola

        bandeja.pausar()
        monkeypatch.setattr(consola, "emparejar", lambda *a, **k: False)
        consola.main(["emparejar", "--codigo", "ABCD-EFGH", "--si"])
        assert bandeja.en_pausa()


def _orden(capability, command_id):
    return {"tipo": "orden", "protocol_version": PROTOCOLO_ACTUAL, "request_id": "r1",
            "command_id": command_id, "agent_id": "ag", "capability": capability,
            "arguments": {}, "vence_en_ms": 10_000}


class TestAlPararSeCancelaLoQueHay:
    def test_cancelar_todas_para_la_que_esta_en_marcha_con_su_motivo(self):
        def larga():
            for _ in range(500):
                control.punto_seguro()
                time.sleep(0.01)
            return {"success": True, "data": {}}

        ej = Ejecutor("ag", {"larga": larga})

        async def todo():
            tarea = asyncio.create_task(ej.procesar(_orden("larga", "c1")))
            await asyncio.sleep(0.2)
            empieza = time.monotonic()
            pedidas = ej.cancelar_todas("pausa")
            r = await tarea
            return pedidas, r, time.monotonic() - empieza

        pedidas, r, tardo = asyncio.run(todo())
        assert pedidas == 1
        assert r["estado"] == "CANCELLED" and "pausa" in r["resultado"]["error"]
        assert tardo < 1.0


class TestParaAMitadDeConectar:
    """Un intento de conexión esperaba hasta 30 s a la nube sin mirar si le pedían parar
    (medido con el congelado en GitHub: la pausa salía «cortada»)."""

    def test_una_nube_que_no_contesta_no_retiene_la_parada(self):
        from src.agente.canal import Canal

        async def todo():
            mudos = []

            async def no_contesta(lector, escritor):
                mudos.append(escritor)          # acepta y no dice nada: ni HTTP ni websocket

            servidor = await asyncio.start_server(no_contesta, "127.0.0.1", 0)
            puerto = servidor.sockets[0].getsockname()[1]
            canal = Canal(f"http://127.0.0.1:{puerto}", "mga_prueba", Ejecutor("ag"))
            corriendo = asyncio.create_task(canal.correr())
            limite = time.monotonic() + 10
            while not mudos and time.monotonic() < limite:
                await asyncio.sleep(0.05)
            assert mudos, "el canal no llegó a conectar"
            empieza = time.monotonic()
            canal.parar()
            final = await asyncio.wait_for(corriendo, timeout=10)
            tardo = time.monotonic() - empieza
            servidor.close()
            return final, tardo

        final, tardo = asyncio.run(todo())
        assert final.value == "DISCONNECTED"
        assert tardo < 1.0, f"tardó {tardo:.2f} s en parar"


def _diario(*registros):
    almacen.carpeta().mkdir(parents=True, exist_ok=True)
    (almacen.carpeta() / "diario.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros) + '{"command_id": "a medi',
        encoding="utf-8")


class TestLoUltimoQueHizo:
    def test_lo_mas_reciente_primero_en_palabras_y_con_su_ultimo_estado(self):
        ahora = time.time()
        _diario(
            {"command_id": "c1", "capability": "read_file", "argumentos": '{"path": "C:/Notas/lista.txt"}',
             "estado": "PENDING", "recibida": ahora - 60},
            {"command_id": "c2", "capability": "create_file", "argumentos": '{"path": "C:/Notas/nuevo.txt", "con',
             "estado": "FAILED", "recibida": ahora - 30},
            {"command_id": "c1", "capability": "read_file", "argumentos": '{"path": "C:/Notas/lista.txt"}',
             "estado": "COMPLETED", "recibida": ahora - 60},
            {"command_id": "c3", "capability": "estado", "argumentos": "{}", "estado": "COMPLETED",
             "recibida": ahora - 10},
            {"command_id": "c4", "capability": "read_file", "argumentos": "{}", "estado": "COMPLETED",
             "recibida": ahora - 25 * 3600},
        )
        vistas = bandeja.ultimas()
        assert [v["que"] for v in vistas] == ["Crear archivos de texto", "Leer archivos de texto"]
        assert [v["estado"] for v in vistas] == ["Falló", "Hecho"]
        # Los argumentos recortados no son JSON entero: sin detalle, pero sin romperse.
        assert vistas[0]["detalle"] == "" and vistas[1]["detalle"] == "C:/Notas/lista.txt"

    def test_solo_lee_el_diario(self):
        ahora = time.time()
        _diario({"command_id": "c1", "capability": "read_file", "argumentos": "{}",
                 "estado": "RUNNING", "recibida": ahora})
        antes = (almacen.carpeta() / "diario.jsonl").read_bytes()
        assert bandeja.ultimas()[0]["estado"] == "En marcha"
        assert (almacen.carpeta() / "diario.jsonl").read_bytes() == antes

    def test_sin_diario_nada(self):
        assert bandeja.ultimas() == []

    def test_cuantas(self):
        ahora = time.time()
        _diario(*[{"command_id": f"c{i}", "capability": "list_files", "argumentos": "{}",
                   "estado": "COMPLETED", "recibida": ahora - i} for i in range(30)])
        assert len(bandeja.ultimas(5)) == 5


class TestVersionNueva:
    PAGINA = "https://github.com/wv-Andy/Morgan/releases/tag/v5.1.0"

    def _con(self, tag, pagina=None):
        return lambda url: {"tag_name": tag, "html_url": pagina or self.PAGINA}

    def test_una_mas_nueva(self):
        assert bandeja.novedades("5.0.2", self._con("v5.1.0")) == {
            "hay": True, "version": "5.1.0", "pagina": self.PAGINA}

    def test_la_misma_o_una_mas_vieja_no(self):
        assert bandeja.novedades("5.1.0", self._con("v5.1.0")) == {"hay": False}
        assert bandeja.novedades("5.1.0", self._con("v5.0.2")) == {"hay": False}

    def test_compara_numeros_no_texto(self):
        assert bandeja.novedades("5.9.0", self._con("v5.10.0"))["hay"] is True

    def test_solo_abre_la_pagina_de_versiones_de_morgan(self):
        assert bandeja.novedades("5.0.0", self._con("v5.1.0", "https://otro.sitio/morgan")) == {"hay": False}

    def test_sin_red_no_dice_nada(self):
        def falla(url):
            raise OSError("sin red")

        assert bandeja.novedades("5.0.0", falla) == {"hay": False}
        assert bandeja.novedades("5.0.0", self._con("ultima")) == {"hay": False}

    def test_la_orden_lo_imprime_en_json(self, monkeypatch, capsys):
        from src.agente import __main__ as consola

        monkeypatch.setattr(bandeja, "_pedir", lambda url: {"tag_name": "v99.0.0", "html_url": self.PAGINA})
        assert consola.main(["novedades"]) == 0
        assert json.loads(capsys.readouterr().out)["version"] == "99.0.0"
