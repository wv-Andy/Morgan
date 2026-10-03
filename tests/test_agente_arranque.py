"""
El agente siempre en marcha: arranque automático, uno solo a la vez y parar (3.0).

Lo pedí al cerrar la 3.0: con el PC encendido y con internet, Morgan tiene acceso
sin abrir el agente a mano. Lo que se comprueba aquí es lo que haría daño si fallase:
dos agentes a la vez (se echarían uno al otro de la nube sin parar), un agente sin
ventana que no se pueda parar, o una señal vieja que pare al nuevo nada más nacer.
"""

import asyncio
import os
import subprocess
import sys
import threading
import time

import pytest

from src.agente import __main__ as consola
from src.agente import arranque
from src.agente import estado as almacen

en_windows = pytest.mark.skipif(sys.platform != "win32", reason="el arranque es de Windows")


@pytest.fixture
def emparejado():
    almacen.guardar(almacen.Emparejamiento("agt-prueba", "https://nube.invalid", "PC de prueba",
                                           "ana", time.time()), "mga_prueba")


class CanalFalso:
    """Corre hasta que se le para, como el de verdad, sin tocar la red."""

    creados: list["CanalFalso"] = []

    def __init__(self, *args, **kwargs):
        self.parado = threading.Event()
        CanalFalso.creados.append(self)

    def parar(self):
        self.parado.set()

    async def correr(self):
        while not self.parado.is_set():
            await asyncio.sleep(0.02)
        return almacen.EstadoAgente.DISCONNECTED


@pytest.fixture
def canal_falso(monkeypatch):
    import src.agente.canal as canal

    CanalFalso.creados = []
    monkeypatch.setattr(canal, "Canal", CanalFalso)
    return CanalFalso


class TestUnoSoloALaVez:
    def test_con_el_candado_tomado_no_se_toma_otra_vez(self):
        primero = arranque.Candado().tomar()
        try:
            with pytest.raises(arranque.YaEnMarcha):
                arranque.Candado().tomar()
            assert arranque.en_marcha()
        finally:
            primero.soltar()
        assert not arranque.en_marcha()
        arranque.Candado().tomar().soltar()

    def test_tambien_entre_procesos(self):
        """El caso real: el automático ya corre y alguien abre otro a mano."""
        guion = ("from src.agente import arranque\n"
                 "try:\n    arranque.Candado().tomar(); print('tomado')\n"
                 "except arranque.YaEnMarcha:\n    print('ocupado')\n")
        entorno = {**os.environ, "MORGAN_AGENTE_DIR": str(almacen.carpeta())}

        def otro():
            return subprocess.run([sys.executable, "-c", guion], capture_output=True, text=True,
                                  env=entorno, check=True).stdout.strip()

        candado = arranque.Candado().tomar()
        try:
            assert otro() == "ocupado"
        finally:
            candado.soltar()
        assert otro() == "tomado"

    def test_un_agente_que_muere_de_golpe_no_deja_el_candado_tomado(self):
        """Si se cuelga o se mata el proceso, el siguiente inicio de sesión tiene que poder
        arrancar: el bloqueo lo suelta el sistema, no depende de que el agente lo suelte."""
        guion = ("import time\nfrom src.agente import arranque\n"
                 "c = arranque.Candado().tomar(); print('listo', flush=True); time.sleep(60)\n")
        entorno = {**os.environ, "MORGAN_AGENTE_DIR": str(almacen.carpeta())}
        otro = subprocess.Popen([sys.executable, "-c", guion], stdout=subprocess.PIPE, text=True, env=entorno)
        try:
            assert otro.stdout.readline().strip() == "listo"
            assert arranque.en_marcha()
        finally:
            otro.kill()
            otro.wait(timeout=10)
        # Windows suelta el bloqueo de un proceso muerto poco después, no en el acto
        # (medido: a veces aún estaba tomado justo tras el wait; LockFileEx lo documenta).
        limite = time.monotonic() + 5
        while arranque.en_marcha() and time.monotonic() < limite:
            time.sleep(0.05)
        assert not arranque.en_marcha()

    def test_conectar_no_arranca_un_segundo_agente(self, emparejado, canal_falso, capsys):
        candado = arranque.Candado().tomar()
        try:
            assert consola.main(["conectar"]) == 4
        finally:
            candado.soltar()
        assert canal_falso.creados == []
        assert "Ya hay un agente en marcha" in capsys.readouterr().out


class TestParar:
    def test_la_vigilancia_para_y_borra_la_senal(self):
        arranque.pedir_parada()
        llamado = []
        arranque.vigilar_parada(lambda: llamado.append(1), cada=0.01)
        assert llamado == [1]
        assert not arranque.senal_de_parada().exists()

    def test_sin_senal_no_para(self):
        llamado = []
        vueltas = iter(range(3))
        arranque.vigilar_parada(lambda: llamado.append(1), cada=0.01,
                                hasta=lambda: next(vueltas, None) is None)
        assert llamado == []

    def test_parar_detiene_al_que_corre_y_una_senal_vieja_no(self, emparejado, canal_falso):
        arranque.pedir_parada()   # quedó de antes: no debe parar al nuevo
        resultado = []
        hilo = threading.Thread(target=lambda: resultado.append(consola.main(["conectar"])))
        hilo.start()
        try:
            limite = time.monotonic() + 5
            while not arranque.en_marcha() and time.monotonic() < limite:
                time.sleep(0.05)
            assert arranque.en_marcha()
            time.sleep(2.5)       # más que una vuelta de la vigilancia
            assert hilo.is_alive(), "una señal vieja paró al agente nada más nacer"

            assert consola.main(["parar"]) == 0
            # Vuelve cuando ya se ha ido: un `arranque activar` justo después no lo
            # vería «en marcha» (pasó al cerrar la 3.7.5).
            assert not arranque.en_marcha()
            hilo.join(timeout=6)
            assert not hilo.is_alive()
        finally:
            for canal in canal_falso.creados:
                canal.parar()
            hilo.join(timeout=6)
        assert resultado == [0]
        assert not arranque.en_marcha(), "el candado quedó tomado"
        assert not arranque.senal_de_parada().exists()

    def test_parar_dice_si_no_se_fue(self, emparejado, monkeypatch, capsys):
        candado = arranque.Candado().tomar()          # uno que no atiende la señal
        monkeypatch.setattr(arranque, "ESPERA_PARADA", 0.5)
        try:
            assert consola.main(["parar"]) == 1
        finally:
            candado.soltar()
            arranque.senal_de_parada().unlink(missing_ok=True)
        assert "sigue en marcha" in capsys.readouterr().out

    def test_una_vez_parado_no_queda_senal_para_el_siguiente(self):
        """Pedir parar cuando ya no corría nada dejaba la señal, y el siguiente vigilante se
        iba nada más nacer (visto en la prueba de punta a punta de la 3.8)."""
        arranque.pedir_parada()
        assert arranque.esperar_a_que_pare(hasta=1)
        assert not arranque.senal_de_parada().exists()

    def test_parar_sin_agente_no_deja_senal(self, capsys):
        assert consola.main(["parar"]) == 0
        assert "No hay ningún agente" in capsys.readouterr().out
        assert not arranque.senal_de_parada().exists()


class TestSinVentana:
    def test_lo_que_imprime_va_al_registro(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        assert consola.main(["conectar"]) == 2     # sin emparejar: lo dice y sale
        sys.stdout.flush()
        registro = (almacen.carpeta() / "agente.log").read_text(encoding="utf-8")
        assert "no está emparejado" in registro

    def test_el_registro_no_crece_sin_fin(self):
        almacen.carpeta().mkdir(parents=True, exist_ok=True)
        ruta = almacen.carpeta() / "agente.log"
        ruta.write_text("x" * (arranque.TAMANO_MAXIMO_REGISTRO + 1))
        arranque.abrir_registro().close()
        assert ruta.stat().st_size == 0


@en_windows
class TestArranqueAutomatico:
    @pytest.fixture(autouse=True)
    def inicio(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MORGAN_CARPETA_INICIO", str(tmp_path / "Inicio"))
        lanzados = []

        def lanzar():
            # Como el de verdad: el agente lanzado coge el candado.
            lanzados.append(arranque.Candado().tomar())

        monkeypatch.setattr(arranque, "lanzar_en_segundo_plano", lanzar)
        yield lanzados
        for candado in lanzados:
            candado.soltar()

    def _leer(self, acceso):
        guion = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('" + str(acceso) + "');"
                 "$s.TargetPath; $s.Arguments; $s.WorkingDirectory")
        salida = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", guion],
                                capture_output=True, text=True, check=True).stdout
        return salida.strip().splitlines()

    def test_activar_crea_el_acceso_sin_ventana_y_arranca_ya(self, emparejado, inicio):
        assert consola.main(["arranque", "activar"]) == 0
        destino, argumentos, carpeta = self._leer(arranque.acceso_directo())
        assert destino.lower().endswith("pythonw.exe")
        assert argumentos == "-m src.agente vigilar"      # el vigilante lanza el agente (3.6)
        assert os.path.samefile(carpeta, arranque.raiz_del_proyecto())
        assert len(inicio) == 1

    def test_si_ya_corre_no_arranca_otro(self, emparejado, inicio):
        candado = arranque.Candado().tomar()
        try:
            assert consola.main(["arranque", "activar"]) == 0
        finally:
            candado.soltar()
        assert inicio == []

    def test_sin_emparejar_no_se_activa(self, inicio):
        assert consola.main(["arranque", "activar"]) == 2
        assert not arranque.activado()
        assert inicio == []

    def test_desactivar_lo_quita(self, emparejado):
        consola.main(["arranque", "activar"])
        assert arranque.activado()
        assert consola.main(["arranque", "desactivar"]) == 0
        assert not arranque.activado()
        assert consola.main(["arranque", "desactivar"]) == 0

    def test_rutas_con_comilla_no_rompen_el_guion(self, emparejado, tmp_path, monkeypatch):
        monkeypatch.setenv("MORGAN_CARPETA_INICIO", str(tmp_path / "Inicio de O'Brien"))
        arranque.activar()
        assert arranque.activado()
