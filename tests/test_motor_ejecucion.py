"""
El motor de ejecución del agente (3.4).

Lo que exige el plan (§V3.4): estados `PENDING, RUNNING, COMPLETED, FAILED,
CANCEL_REQUESTED, CANCELLED`, ejecución secuencial, y que las operaciones sean
**predecibles aunque se repitan, se pierda la respuesta, el agente se desconecte, haya
timeout o se llene la cola**. Y lo que decidí el 2026-09-24:

- «Parar» cancela lo del PC, en su siguiente punto seguro.
- Lo que ya pasó el punto sin vuelta **se deja y se dice**.
- El diario del PC guarda 24 h, y **nunca lo leído**.

Reproducido antes de construir (3.4-A): al vencer el plazo, la nube recibía «no terminó
a tiempo» y el archivo se escribía igual; y con un corte a mitad, la orden repetida se
ejecutaba otra vez.
"""

import asyncio
import json
import threading
import time

import pytest

from src.agente import aviso, capacidades, control, escritura
from src.agente import estado as almacen
from src.agente.diario import DURA, Diario
from src.agente.ejecutor import Ejecutor
from src.agente.politica import DE_ESCRITURA, Politica
from src.agente.protocolo import PROTOCOLO_ACTUAL


def _orden(capability="estado", command_id="c1", vence_en_ms=10_000, **arguments):
    return {"tipo": "orden", "protocol_version": PROTOCOLO_ACTUAL, "request_id": "r1",
            "command_id": command_id, "agent_id": "ag", "capability": capability,
            "arguments": arguments, "vence_en_ms": vence_en_ms}


@pytest.fixture(autouse=True)
def sin_appdata(tmp_path, monkeypatch):
    """Las carpetas de las pruebas están dentro de %LOCALAPPDATA%, zona prohibida."""
    for variable in ("APPDATA", "LOCALAPPDATA", "ProgramData"):
        (tmp_path / "_sistema" / variable).mkdir(parents=True)
        monkeypatch.setenv(variable, str(tmp_path / "_sistema" / variable))


@pytest.fixture
def pc(tmp_path, monkeypatch):
    notas = tmp_path / "Notas"
    notas.mkdir()
    (notas / "lista.txt").write_text("pan\n", encoding="utf-8")
    politica = Politica()
    politica.anadir(str(notas))
    politica.permitir_escritura(str(notas))
    for c in DE_ESCRITURA:
        politica.capacidades[c] = True
    politica.guardar()
    monkeypatch.setattr(escritura, "a_la_papelera", lambda ruta: ruta.unlink())
    return notas


async def _durante(ejecutor, orden, accion, espera=0.2):
    """Lanza la orden y, mientras corre, hace `accion(ejecutor)`."""
    tarea = asyncio.create_task(ejecutor.procesar(orden))
    await asyncio.sleep(espera)
    accion(ejecutor)
    return await tarea


class TestElControl:
    def test_antes_del_punto_sin_vuelta_para(self):
        c = control.Control()
        assert c.pedir("la_persona") is True
        with pytest.raises(control.Cancelada):
            c.comprobar()
        with pytest.raises(control.Cancelada):
            c.marcar_sin_vuelta()

    def test_despues_ya_no(self):
        c = control.Control()
        c.marcar_sin_vuelta()
        assert c.pedir("la_persona") is False
        c.comprobar()           # no lanza: sigue hasta el final

    def test_sin_control_los_puntos_no_hacen_nada(self):
        control.punto_seguro()
        control.sin_vuelta()
        assert not control.pedida()

    def test_nunca_se_cree_parada_una_que_siguio(self):
        """Mil carreras entre pedir y marcar: o para, o se sabe que no paró."""
        for _ in range(1000):
            c, paro = control.Control(), {}

            def capacidad():
                try:
                    c.marcar_sin_vuelta()
                    paro["hizo"] = True
                except control.Cancelada:
                    paro["hizo"] = False

            hilo = threading.Thread(target=capacidad)
            hilo.start()
            va_a_parar = c.pedir("x")
            hilo.join()
            assert va_a_parar is (not paro["hizo"])


class TestCancelar:
    def test_una_en_marcha_para_en_su_punto_seguro(self):
        def larga():
            for _ in range(200):
                control.punto_seguro()
                time.sleep(0.01)
            return {"success": True, "data": {}}

        ej = Ejecutor("ag", {"larga": larga})
        r = asyncio.run(_durante(ej, _orden("larga"), lambda e: e.cancelar("c1")))
        assert r["estado"] == "CANCELLED" and "la persona" in r["resultado"]["error"]
        assert ej.diario.de("c1")["estado"] == "CANCELLED"

    def test_un_borrado_esperando_confirmacion_no_borra(self, pc, monkeypatch):
        def preguntar(titulo, detalle, espera=aviso.ESPERA):
            while not control.pedida():
                time.sleep(0.01)
            return "cancelada"

        monkeypatch.setattr(aviso, "preguntar", preguntar)
        ej = Ejecutor("ag", capacidades.disponibles())
        r = asyncio.run(_durante(ej, _orden("delete_file", path=str(pc / "lista.txt")),
                                 lambda e: e.cancelar("c1")))
        assert r["estado"] == "CANCELLED" and (pc / "lista.txt").exists()

    def test_la_notificacion_se_retira_si_se_para(self, monkeypatch):
        from tests.test_agente_escritura import _WindowsDeMentira

        _WindowsDeMentira(lambda n: None).instalar(monkeypatch)
        c = control.Control()
        token = control.ACTUAL.set(c)
        try:
            threading.Timer(0.2, lambda: c.pedir("la_persona")).start()
            inicio = time.monotonic()
            assert aviso.preguntar("t", "d", espera=10) == "cancelada"
            assert time.monotonic() - inicio < 2
        finally:
            control.ACTUAL.reset(token)

    def test_pasado_el_punto_sin_vuelta_se_hace_y_se_dice(self, pc, monkeypatch):
        """Decisión mía: «se deja y se dice»."""
        lento = escritura._escribir_temporal

        def despacio(destino, datos):
            time.sleep(0.5)
            return lento(destino, datos)

        monkeypatch.setattr(escritura, "_escribir_temporal", despacio)
        ej = Ejecutor("ag", capacidades.disponibles())
        vistos = {}
        r = asyncio.run(_durante(ej, _orden("create_file", path=str(pc / "nuevo.txt"), content="x"),
                                 lambda e: vistos.update(e.cancelar("c1"))))
        assert vistos["sin_vuelta"] is True
        assert r["estado"] == "COMPLETED" and (pc / "nuevo.txt").exists()
        assert "ya estaba hecho" in r["resultado"]["data"]["cancelacion"]

    def test_una_en_cola_ni_empieza(self):
        llamadas = []
        ej = Ejecutor("ag", {"algo": lambda: llamadas.append(1) or {"success": True}})

        async def todo():
            await ej.en_cola(_orden("algo"), 1)
            ej.cancelar("c1")
            return await ej.procesar(_orden("algo"))

        r = asyncio.run(todo())
        assert r["estado"] == "CANCELLED" and llamadas == []

    def test_cancelar_lo_ya_acabado_o_desconocido_no_hace_nada(self):
        ej = Ejecutor("ag")
        asyncio.run(ej.procesar(_orden()))
        assert ej.cancelar("c1")["estado"] == "COMPLETED"
        assert ej.cancelar("nunca")["estado"] == "DESCONOCIDA"

    def test_una_busqueda_se_puede_parar(self, pc):
        for i in range(3000):
            (pc / f"f{i}.txt").write_text("x", encoding="utf-8")
        ej = Ejecutor("ag", capacidades.disponibles())

        def buscar_despacio(*a, **k):
            control.ACTUAL.get().pedir("la_persona")   # como si llegara a mitad
            return capacidades.search_files(*a, **k)

        ej.capacidades["search_files"] = buscar_despacio
        r = asyncio.run(ej.procesar(_orden("search_files", query="nada-de-esto")))
        assert r["estado"] == "CANCELLED"


class TestElDiario:
    def test_sobrevive_a_reiniciar_el_agente(self, pc):
        asyncio.run(Ejecutor("ag", capacidades.disponibles()).procesar(
            _orden("create_file", path=str(pc / "a.txt"), content="hola")))
        otro = Ejecutor("ag", capacidades.disponibles())
        consulta = otro.consultar("c1")
        assert consulta["estado"] == "COMPLETED" and consulta["respuesta"]["resultado"]["success"]

    def test_la_misma_orden_no_se_hace_dos_veces_tras_reiniciar(self, pc):
        orden = _orden("edit_file", path=str(pc / "lista.txt"), old_text="pan", new_text="pan integral")
        asyncio.run(Ejecutor("ag", capacidades.disponibles()).procesar(orden))
        r = asyncio.run(Ejecutor("ag", capacidades.disponibles()).procesar(orden))
        assert r["repetida"] and (pc / "lista.txt").read_text(encoding="utf-8") == "pan integral\n"
        # Y con el resultado de verdad, no un «ya se atendió» genérico.
        assert r["estado"] == "COMPLETED" and r["resultado"]["success"] is True
        assert r["resultado"]["data"]["path"].endswith("lista.txt")

    def test_nunca_guarda_lo_leido(self, pc):
        (pc / "secreto.txt").write_text("CONTENIDO-PRIVADO-123", encoding="utf-8")
        ej = Ejecutor("ag", capacidades.disponibles())
        r = asyncio.run(ej.procesar(_orden("read_file", path=str(pc / "secreto.txt"))))
        assert "CONTENIDO-PRIVADO-123" in json.dumps(r)
        en_disco = (almacen.carpeta() / "diario.jsonl").read_text(encoding="utf-8")
        assert "CONTENIDO-PRIVADO-123" not in en_disco
        assert json.loads(en_disco.splitlines()[-1])["estado"] == "COMPLETED"

    def test_una_lectura_sin_su_resultado_se_puede_repetir(self, pc):
        asyncio.run(Ejecutor("ag", capacidades.disponibles()).procesar(
            _orden("read_file", path=str(pc / "lista.txt"))))
        consulta = Ejecutor("ag", capacidades.disponibles()).consultar("c1")
        assert consulta["repetible"] and consulta["respuesta"] is None

    def test_lo_que_quedo_a_medias_al_reiniciar_se_dice(self):
        diario = Diario()
        diario.nueva("c9", _orden("create_file", "c9"))
        diario.pasar("c9", "RUNNING")
        assert Diario().de("c9") == {**Diario().de("c9"), "estado": "FAILED", "motivo": "agente_reiniciado"}

    def test_dura_24_horas(self, monkeypatch):
        diario = Diario()
        diario.nueva("vieja", _orden())
        ahora = time.time()
        monkeypatch.setattr(time, "time", lambda: ahora + DURA + 1)
        diario.nueva("nueva", _orden(command_id="nueva"))
        assert diario.de("vieja") is None and diario.de("nueva")

    def test_lo_viejo_se_olvida_al_cargar(self):
        almacen.carpeta().mkdir(parents=True, exist_ok=True)
        (almacen.carpeta() / "diario.json").write_text(json.dumps({
            "vieja": {"estado": "COMPLETED", "recibida": time.time() - DURA - 10, "capability": "x"},
            "nueva": {"estado": "COMPLETED", "recibida": time.time(), "capability": "x"},
        }), encoding="utf-8")
        diario = Diario()
        assert diario.de("vieja") is None and diario.de("nueva")
        # Y no se arrastra en el fichero: el diario no crece con lo caducado.
        assert "vieja" not in (almacen.carpeta() / "diario.jsonl").read_text(encoding="utf-8")

    def test_con_mucho_uso_se_compacta_solo(self):
        diario = Diario()
        diario.nueva("a", _orden(command_id="a"))
        for _ in range(1100):
            diario.pasar("a", "RUNNING")
        lineas = (almacen.carpeta() / "diario.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lineas) < 1010

    def test_solo_se_anade_y_al_arrancar_se_compacta(self):
        """3.4.5: reescribir el diario entero en cada cambio crecía con él (39 ms por orden
        con 5.000). Ahora cada cambio es una línea, y al arrancar queda una por orden."""
        diario = Diario()
        diario.nueva("a", _orden(command_id="a"))
        diario.pasar("a", "RUNNING")
        diario.pasar("a", "COMPLETED", resultado={"success": True})
        fichero = almacen.carpeta() / "diario.jsonl"
        assert len(fichero.read_text(encoding="utf-8").splitlines()) == 3
        assert Diario().de("a")["estado"] == "COMPLETED"
        assert len(fichero.read_text(encoding="utf-8").splitlines()) == 1

    def test_una_linea_a_medias_no_rompe_nada(self):
        """Un corte de luz a mitad de escribir una línea."""
        diario = Diario()
        diario.nueva("a", _orden(command_id="a"))
        with open(almacen.carpeta() / "diario.jsonl", "a", encoding="utf-8") as f:
            f.write('{"command_id": "b", "estado": "RUNN')
        assert Diario().de("a") and Diario().de("b") is None

    def test_el_diario_de_la_340_se_migra(self):
        almacen.carpeta().mkdir(parents=True, exist_ok=True)
        (almacen.carpeta() / "diario.json").write_text(json.dumps({
            "vieja": {"estado": "COMPLETED", "recibida": time.time(), "capability": "x"}}), encoding="utf-8")
        assert Diario().de("vieja")["estado"] == "COMPLETED"
        assert not (almacen.carpeta() / "diario.json").exists()

    def test_el_coste_no_crece_con_el_diario(self):
        diario = Diario()
        for i in range(3000):
            diario._entradas[f"r{i}"] = {"capability": "read_file", "argumentos": "x" * 200,
                                         "estado": "COMPLETED", "recibida": time.time()}
        diario._compactar(diario._entradas)
        inicio = time.perf_counter()
        for i in range(50):
            diario.nueva(f"n{i}", _orden(command_id=f"n{i}"))
        por_cambio = (time.perf_counter() - inicio) / 50 * 1000
        assert por_cambio < 5, f"{por_cambio:.1f} ms por cambio con 3.000 órdenes"

    def test_una_desconocida_se_dice_asi(self):
        assert Ejecutor("ag").consultar("nunca")["estado"] == "DESCONOCIDA"

    def test_mientras_corre_se_dice_como_va(self):
        ej = Ejecutor("ag", {"lenta": lambda: time.sleep(0.4) or {"success": True}})
        vistos = {}
        asyncio.run(_durante(ej, _orden("lenta"), lambda e: vistos.update(e.conocida("c1"))))
        assert vistos == {"tipo": "estado", "command_id": "c1", "estado": "RUNNING"}


class TestNuncaDosVeces:
    """Reproducido en la 3.4-A: con un corte a mitad, la orden repetida se ejecutaba
    otra vez."""

    def _lenta(self, veces, segundos=0.4):
        def lenta():
            time.sleep(segundos)
            veces.append(1)
            return {"success": True, "data": {"n": len(veces)}}
        return lenta

    def test_la_misma_orden_mientras_corre_no_se_lanza_otra_vez(self):
        veces = []
        ej = Ejecutor("ag", {"lenta": self._lenta(veces)})

        async def todo():
            primera = asyncio.create_task(ej.procesar(_orden("lenta")))
            await asyncio.sleep(0.1)
            segunda = await ej.procesar(_orden("lenta"))
            return await primera, segunda

        primera, segunda = asyncio.run(todo())
        time.sleep(0.2)
        assert veces == [1]
        assert primera["estado"] == "COMPLETED"
        assert segunda == {"tipo": "estado", "command_id": "c1", "estado": "RUNNING"}

    def test_si_se_deja_de_esperar_a_mitad_no_se_repite(self):
        """Lo que hacía el canal al cortarse: cancelar la tarea que espera."""
        veces = []
        ej = Ejecutor("ag", {"lenta": self._lenta(veces, 0.5)})

        async def todo():
            tarea = asyncio.create_task(ej.procesar(_orden("lenta")))
            await asyncio.sleep(0.1)
            tarea.cancel()
            try:
                await tarea
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.7)
            return await ej.procesar(_orden("lenta"))

        otra = asyncio.run(todo())
        assert veces == [1] and otra["repetida"]
        assert ej.diario.de("c1")["estado"] == "COMPLETED"


class TestLaCuentaDeEstados:
    def test_la_nube_se_entera_de_cada_paso(self):
        mensajes = []

        async def avisar(m):
            mensajes.append(m)

        ej = Ejecutor("ag", avisar=avisar)

        async def todo():
            await ej.en_cola(_orden(), 1)
            return await ej.procesar(_orden())

        asyncio.run(todo())
        assert [(m["estado"], m.get("posicion")) for m in mensajes] == [("PENDING", 1), ("RUNNING", None)]


class TestLaColaDelCanal:
    def _canal(self):
        from src.agente.canal import Canal

        canal = Canal("http://x", "mga_x", Ejecutor("ag"))
        mandados = []

        async def mandar(m):
            mandados.append(m)
            return True

        canal._mandar = mandar
        return canal, mandados

    def test_con_la_cola_llena_se_rechaza_al_momento(self):
        from src.agente.canal import TOPE_COLA

        canal, mandados = self._canal()

        async def todo():
            canal._cola = asyncio.Queue()
            for i in range(TOPE_COLA + 1):
                await canal._recibir_orden(_orden(command_id=f"c{i}"))

        asyncio.run(todo())
        assert canal._cola.qsize() == TOPE_COLA
        assert mandados[-1]["estado"] == "REJECTED" and mandados[-1]["motivo"] == "ocupado"

    def test_una_orden_ya_en_cola_no_se_encola_otra_vez(self):
        canal, mandados = self._canal()

        async def todo():
            canal._cola = asyncio.Queue()
            await canal._recibir_orden(_orden())
            await canal._recibir_orden(_orden())

        asyncio.run(todo())
        assert canal._cola.qsize() == 1 and mandados[-1]["estado"] == "PENDING"

    def test_sin_conexion_lista_no_se_manda_nada(self):
        """Tras un corte, un resultado no puede colarse antes del saludo."""
        from src.agente.canal import Canal

        class Socket:
            enviados = []

            async def send(self, texto):
                self.enviados.append(texto)

        canal = Canal("http://x", "mga_x", Ejecutor("ag"))
        canal._ws = Socket()            # abriéndose, sin bienvenida
        assert asyncio.run(canal._mandar({"tipo": "resultado"})) is False
        assert Socket.enviados == []
        canal._lista = canal._ws        # ya saludada
        assert asyncio.run(canal._mandar({"tipo": "resultado"})) is True
        assert len(Socket.enviados) == 1


def test_parar_el_canal_dos_veces_no_revienta():
    """Encontrado en el estrés de la 3.4.5: la segunda vez, con el bucle ya cerrado."""
    from src.agente.canal import Canal

    canal = Canal("http://127.0.0.1:9", "mga_x", Ejecutor("ag"), espera_minima=0.05)

    async def correr_y_parar():
        tarea = asyncio.create_task(canal.correr())
        await asyncio.sleep(0.1)
        canal.parar()
        await tarea

    asyncio.run(correr_y_parar())
    canal.parar()                       # otra vez, con el bucle cerrado
