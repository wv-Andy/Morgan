"""
La copia de un archivo del PC para descargarla (3.1-E), por el canal.

Lo pedí al empezar la 3.1: «que Morgan copie un documento y me deje descargarlo en
el móvil». Lo que fija esto:

- **Es una copia, no una lectura**: puede ser binaria y llega a 20 MB (una lectura son
  256 KB y sin binarios), y **su contenido no pasa por el modelo** ni por el filtro de
  secretos: es el archivo del dueño, tal cual, para él.
- **Pasa por la misma política**: carpetas permitidas, nombres sensibles, enlaces duros.
- **Viaja en trozos** (`fragmento`), porque un mensaje del canal no puede con 20 MB, y la
  nube **comprueba** tamaño y huella antes de dar la copia por buena.
"""

import base64
import hashlib
import json
import threading

import pytest
from fastapi.testclient import TestClient

from src.agente import capacidades
from src.agente.ejecutor import Ejecutor
from src.agente.politica import Politica
from src.agente.protocolo import MAX_COPIA, PROTOCOLO_ACTUAL, TROZO_COPIA
from src.canal import despacho
from src.canal.registro import REGISTRO, Conexion
from src.config import reset_settings
from src.identidad import como_usuario

CLAVE = "contrasena-larga"
SALUDO = {"tipo": "saludo", "protocol_version": PROTOCOLO_ACTUAL, "agent_version": "3.1.0-dev",
          "sistema": "Windows 11", "capacidades": ["copy_file"]}


@pytest.fixture
def pc(tmp_path):
    permitida = tmp_path / "Documentos"
    permitida.mkdir()
    (permitida / "contrato.pdf").write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 900)   # binario
    (permitida / "notas.txt").write_text("hola", encoding="utf-8")
    (permitida / ".env").write_text("CLAVE=secreta", encoding="utf-8")
    (tmp_path / "fuera.txt").write_text("no", encoding="utf-8")
    politica = Politica()
    politica.anadir(str(permitida))
    politica.guardar()
    return {"permitida": permitida, "fuera": tmp_path / "fuera.txt"}


class TestLaCapacidadDelPc:
    def test_un_binario_se_copia_y_da_su_huella(self, pc):
        ruta = pc["permitida"] / "contrato.pdf"
        r = capacidades.copy_file(str(ruta))
        assert r["success"]
        assert r["data"]["name"] == "contrato.pdf"
        assert r["data"]["bytes"] == ruta.stat().st_size
        assert r["data"]["sha256"] == hashlib.sha256(ruta.read_bytes()).hexdigest()
        assert r["data"]["tipo"] == "application/pdf"
        assert r["_archivo"] == str(ruta.resolve())

    def test_lo_sensible_no_se_copia(self, pc):
        r = capacidades.copy_file(str(pc["permitida"] / ".env"))
        assert not r["success"] and r["motivo"] == "sensible"

    def test_lo_de_fuera_tampoco(self, pc):
        r = capacidades.copy_file(str(pc["fuera"]))
        assert not r["success"] and r["motivo"] == "fuera"

    def test_lo_que_pasa_del_tope(self, pc):
        """El tope sale de la política del PC (3.2): la persona puede bajarlo, y el
        archivo que antes cabía deja de caber."""
        gordo = pc["permitida"] / "video.bin"
        gordo.write_bytes(b"x" * 2 * 1024 * 1024)
        assert capacidades.copy_file(str(gordo))["success"]

        politica = Politica.cargar()
        politica.copia_bytes = 1024 * 1024
        politica.guardar()
        r = capacidades.copy_file(str(gordo))
        assert not r["success"] and r["motivo"] == "demasiado_grande"

    def test_una_lectura_normal_sigue_sin_binarios(self, pc):
        """La copia levanta el límite; la lectura no, porque esa sí va al modelo."""
        r = capacidades.read_file(str(pc["permitida"] / "contrato.pdf"))
        assert not r["success"] and r["motivo"] == "binario"


class TestElEjecutorNoDejaSalirLaRuta:
    def _orden(self, ruta, **cambios):
        return {"tipo": "orden", "protocol_version": PROTOCOLO_ACTUAL, "request_id": "r1",
                "command_id": "c1", "agent_id": "agt-1", "capability": "copy_file",
                "arguments": {"path": str(ruta)}, "vence_en_ms": 5000, **cambios}

    def test_el_archivo_va_fuera_del_resultado(self, pc):
        import asyncio

        ejecutor = Ejecutor("agt-1", capacidades.disponibles())
        respuesta = asyncio.run(ejecutor.procesar(self._orden(pc["permitida"] / "contrato.pdf")))
        assert respuesta["estado"] == "COMPLETED"
        assert respuesta["_archivo"].endswith("contrato.pdf")
        assert "_archivo" not in respuesta["resultado"]

    def test_si_la_frontera_de_salida_tumba_el_resultado_tampoco(self, pc, monkeypatch):
        """El resultado puede fallar DESPUÉS de que la capacidad deje su archivo: la
        frontera de salida (3.1-A) puede cortarlo. Entonces no se manda nada."""
        import asyncio

        from src.agente import salida

        monkeypatch.setattr(salida, "MAX_RESPUESTA", 10)
        ejecutor = Ejecutor("agt-1", capacidades.disponibles())
        respuesta = asyncio.run(ejecutor.procesar(self._orden(pc["permitida"] / "contrato.pdf")))
        assert respuesta["estado"] == "FAILED" and "_archivo" not in respuesta

    def test_si_falla_no_hay_archivo(self, pc):
        import asyncio

        ejecutor = Ejecutor("agt-1", capacidades.disponibles())
        respuesta = asyncio.run(ejecutor.procesar(self._orden(pc["permitida"] / ".env")))
        assert respuesta["estado"] == "FAILED" and "_archivo" not in respuesta


class TestElAgenteManda:
    def test_en_trozos_y_en_orden(self, pc, monkeypatch):
        import asyncio

        from src.agente.canal import Canal

        monkeypatch.setattr("src.agente.canal.TROZO_COPIA", 1024)
        enviados = []

        class WsFalso:
            async def send(self, texto):
                enviados.append(json.loads(texto))

        canal = Canal("http://x", "mga_x", Ejecutor("agt-1"))
        ruta = pc["permitida"] / "contrato.pdf"
        asyncio.run(canal._enviar_archivo(WsFalso(), "c1", str(ruta)))

        assert len(enviados) == -(-ruta.stat().st_size // 1024)
        assert [m["indice"] for m in enviados] == list(range(len(enviados)))
        assert all(m["tipo"] == "fragmento" and m["command_id"] == "c1" for m in enviados)
        junto = b"".join(base64.b64decode(m["datos"]) for m in enviados)
        assert junto == ruta.read_bytes()

    def test_un_trozo_cabe_en_un_mensaje_del_canal(self):
        """En base64 crece un tercio: tiene que quedar debajo del tope de 2 MB."""
        from src.api.server import WS_MAX_SIZE

        assert TROZO_COPIA * 4 / 3 + 200 < WS_MAX_SIZE


class TestLaNubeJuntaYComprueba:
    def _conexion(self):
        return Conexion(user_id="u", agent_id="a", nombre="PC", capacidades=frozenset(),
                        enviar=None, cerrar=None, loop=None, esperando={"c1": object()})

    def test_solo_de_una_orden_que_existe(self):
        conexion = self._conexion()
        despacho.recibir_fragmento(conexion, {"command_id": "inventado", "datos": "aGk="})
        assert conexion.fragmentos == {}

    def test_los_trozos_se_juntan(self):
        conexion = self._conexion()
        for trozo in (b"hola ", b"mundo"):
            despacho.recibir_fragmento(conexion, {"command_id": "c1", "datos": base64.b64encode(trozo).decode()})
        assert b"".join(conexion.fragmentos["c1"]["trozos"]) == b"hola mundo"

    def test_un_trozo_mal_codificado_invalida_la_copia(self):
        conexion = self._conexion()
        despacho.recibir_fragmento(conexion, {"command_id": "c1", "datos": "no es base64!!"})
        assert conexion.fragmentos["c1"]["error"] and conexion.fragmentos["c1"]["trozos"] == []

    def test_pasarse_del_tope_invalida_la_copia(self, monkeypatch):
        monkeypatch.setattr(despacho, "MAX_COPIA", 10)
        conexion = self._conexion()
        despacho.recibir_fragmento(conexion, {"command_id": "c1", "datos": base64.b64encode(b"x" * 50).decode()})
        assert "tope" in conexion.fragmentos["c1"]["error"]
        assert conexion.fragmentos["c1"]["trozos"] == []

    def _resultado(self, contenido):
        return {"success": True, "data": {"name": "x.bin", "bytes": len(contenido),
                                          "sha256": hashlib.sha256(contenido).hexdigest()}}

    def test_la_huella_se_comprueba_en_la_nube(self):
        contenido = b"unos datos"
        trozos = {"trozos": [b"otros dato"], "bytes": 10, "error": None}   # mismo tamaño
        with pytest.raises(despacho.CopiaNoValida) as fallo:
            despacho._archivo_recibido(trozos, self._resultado(contenido))
        assert "huella" in str(fallo.value)

    def test_el_tamano_tambien(self):
        """Con su propio mensaje: «faltan bytes» se entiende mejor que «la huella no
        coincide», que es lo que diría la comprobación siguiente."""
        contenido = b"unos datos"
        trozos = {"trozos": [contenido[:4]], "bytes": 4, "error": None}
        with pytest.raises(despacho.CopiaNoValida) as fallo:
            despacho._archivo_recibido(trozos, self._resultado(contenido))
        assert "llegaron 4 bytes" in str(fallo.value)

    def test_si_no_llego_nada(self):
        with pytest.raises(despacho.CopiaNoValida) as fallo:
            despacho._archivo_recibido(None, self._resultado(b"x"))
        assert "no llegó nada" in str(fallo.value)

    def test_lo_bueno_pasa(self):
        contenido = b"unos datos"
        trozos = {"trozos": [contenido[:4], contenido[4:]], "bytes": len(contenido), "error": None}
        assert despacho._archivo_recibido(trozos, self._resultado(contenido)) == contenido

    def test_un_fallo_del_pc_no_pide_contenido(self):
        assert despacho._archivo_recibido(None, {"success": False, "data": None}) == b""


class TestDeExtremoAExtremo:
    """Por el canal de verdad de la nube, con un agente simulado que manda los trozos."""

    @pytest.fixture
    def nube(self, monkeypatch):
        from src.api import dependencies
        from src.api.app import create_app

        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        dependencies.reset_container()
        REGISTRO.reiniciar()
        monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 0.5)
        yield create_app()
        REGISTRO.reiniciar()
        reset_settings()

    def _cuenta(self, app):
        web = TestClient(app)
        r = web.post("/auth/registro", json={"username": "ana", "email": "ana@ejemplo.co",
                                             "password": CLAVE}, headers={"X-Forwarded-For": "10.4.0.1"})
        assert r.status_code == 200, r.text
        csrf = r.json()["csrf"]
        user_id = web.get("/auth/yo").json()["usuario"]["id"]
        codigo = web.post("/auth/agentes/codigo", headers={"x-morgan-csrf": csrf}).json()["codigo"]
        r = TestClient(app).post("/agente/emparejar/confirmar", json={
            "codigo": codigo, "nombre": "PC", "protocol_version": PROTOCOLO_ACTUAL})
        return user_id, r.json()["credencial"]

    def test_el_archivo_llega_entero_y_comprobado(self, nube, pc):
        user_id, credencial = self._cuenta(nube)
        contenido = (pc["permitida"] / "contrato.pdf").read_bytes()
        resultado: dict = {}

        def turno():
            with como_usuario(user_id):
                try:
                    resultado["valor"] = despacho.enviar(
                        "copy_file", {"path": str(pc["permitida"] / "contrato.pdf")}, plazo=10)
                except Exception as exc:      # pragma: no cover - se comprueba abajo
                    resultado["error"] = exc

        cliente = TestClient(nube, headers={"Authorization": f"Bearer {credencial}"})
        with cliente.websocket_connect("/agente/canal") as ws:
            ws.send_json(SALUDO)
            assert ws.receive_json()["tipo"] == "bienvenida"
            hilo = threading.Thread(target=turno, daemon=True)
            hilo.start()
            orden = ws.receive_json()
            while orden["tipo"] != "orden":
                orden = ws.receive_json()
            assert orden["capability"] == "copy_file"
            # El agente: primero los trozos, después el resultado.
            for i in range(0, len(contenido), 4096):
                ws.send_json({"tipo": "fragmento", "command_id": orden["command_id"],
                              "indice": i // 4096,
                              "datos": base64.b64encode(contenido[i:i + 4096]).decode()})
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True, "data": {
                              "name": "contrato.pdf", "bytes": len(contenido),
                              "sha256": hashlib.sha256(contenido).hexdigest(),
                              "tipo": "application/pdf"}},
                          "tiempos": {"cola_ms": 0, "ejecucion_ms": 1}})
            hilo.join(10)

        assert "valor" in resultado, resultado
        assert resultado["valor"]["_contenido"] == contenido
        assert resultado["valor"]["resultado"]["data"]["name"] == "contrato.pdf"

    def test_si_los_trozos_no_cuadran_no_hay_copia(self, nube, pc):
        user_id, credencial = self._cuenta(nube)
        resultado: dict = {}

        def turno():
            with como_usuario(user_id):
                try:
                    resultado["valor"] = despacho.enviar("copy_file", {"path": "x"}, plazo=10)
                except Exception as exc:
                    resultado["error"] = exc

        cliente = TestClient(nube, headers={"Authorization": f"Bearer {credencial}"})
        with cliente.websocket_connect("/agente/canal") as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            hilo = threading.Thread(target=turno, daemon=True)
            hilo.start()
            orden = ws.receive_json()
            while orden["tipo"] != "orden":
                orden = ws.receive_json()
            ws.send_json({"tipo": "fragmento", "command_id": orden["command_id"], "indice": 0,
                          "datos": base64.b64encode(b"otra cosa").decode()})
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True, "data": {
                              "name": "x", "bytes": 5, "sha256": hashlib.sha256(b"hola!").hexdigest()}},
                          "tiempos": {"cola_ms": 0, "ejecucion_ms": 1}})
            hilo.join(10)

        assert isinstance(resultado.get("error"), despacho.CopiaNoValida), resultado

    def test_a_un_agente_viejo_la_nube_le_habla_en_su_version(self, nube):
        """El protocolo subió a 2 con los trozos (3.1-E). Un agente de la 1 sigue
        funcionando: la nube le manda las órdenes con la versión que negoció."""
        user_id, credencial = self._cuenta(nube)
        resultado: dict = {}

        def turno():
            with como_usuario(user_id):
                try:
                    resultado["valor"] = despacho.enviar("estado", {}, plazo=10)
                except Exception as exc:
                    resultado["error"] = exc

        cliente = TestClient(nube, headers={"Authorization": f"Bearer {credencial}"})
        with cliente.websocket_connect("/agente/canal") as ws:
            ws.send_json({**SALUDO, "protocol_version": 1, "capacidades": ["estado"]})
            bienvenida = ws.receive_json()
            assert bienvenida["compatibilidad"] == "OUTDATED"
            hilo = threading.Thread(target=turno, daemon=True)
            hilo.start()
            orden = ws.receive_json()
            while orden["tipo"] != "orden":
                orden = ws.receive_json()
            assert orden["protocol_version"] == 1
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True, "data": {}},
                          "tiempos": {"cola_ms": 0, "ejecucion_ms": 0}})
            hilo.join(10)
        assert "valor" in resultado, resultado

    def test_los_trozos_de_un_agente_no_llenan_la_memoria_de_la_nube(self, nube):
        """Sin una orden esperando, los trozos se tiran."""
        _, credencial = self._cuenta(nube)
        cliente = TestClient(nube, headers={"Authorization": f"Bearer {credencial}"})
        with cliente.websocket_connect("/agente/canal") as ws:
            ws.send_json(SALUDO)
            ws.receive_json()
            for _ in range(5):
                ws.send_json({"tipo": "fragmento", "command_id": "inventado", "indice": 0,
                              "datos": base64.b64encode(b"x" * 1000).decode()})
            ws.send_json({"tipo": "pong"})
            assert ws.receive_json()["tipo"] == "latido"
        conexiones = [c for c in [REGISTRO.de("u")] if c]
        assert all(not c.fragmentos for c in conexiones)


def test_el_tope_de_una_copia_es_el_que_decidio_andy():
    assert MAX_COPIA == 20 * 1024 * 1024


# --- La mitad de la nube: guardar la copia y descargarla --------------------------


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    from src import config
    from src.memory.db import Database
    from src.memory.sqlite_repositories import SQLiteRepositoryFactory
    from src.uploads import UploadStore
    from src.uploads.almacenamiento import AlmacenEnDisco

    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    config.reset_settings()
    fabrica = SQLiteRepositoryFactory(Database(tmp_path / "copias.db"))
    yield UploadStore(repositorio=fabrica.uploads, almacen=AlmacenEnDisco(tmp_path / "bytes"))
    config.reset_settings()


class TestGuardarLaCopia:
    def test_un_word_se_guarda_aunque_morgan_no_lo_sepa_leer(self, almacen):
        """Lo subido por la web se limita a lo que Morgan lee; una copia del PC sirve
        para descargarla, y la mitad de los documentos de una persona son Word o Excel."""
        from src.uploads.store import ArchivoRechazado, FAMILIA_DESCARGA

        datos = b"PK\x03\x04" + b"contenido de un docx" * 10
        with pytest.raises(ArchivoRechazado):
            almacen.guardar("informe.docx", datos)

        copia = almacen.guardar_copia_del_equipo(
            "informe.docx", datos,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert copia.familia == FAMILIA_DESCARGA
        assert almacen.leer(copia.id) == datos

    def test_lo_que_morgan_sí_lee_conserva_su_familia(self, almacen):
        copia = almacen.guardar_copia_del_equipo("contrato.pdf", b"%PDF-1.7\nhola", "application/pdf")
        assert copia.familia == "documento" and copia.mime == "application/pdf"

    def test_el_tope_de_tamano_sigue_valiendo(self, almacen, monkeypatch):
        from src.uploads.store import ArchivoRechazado

        monkeypatch.setattr(almacen, "max_bytes", 10)
        with pytest.raises(ArchivoRechazado):
            almacen.guardar_copia_del_equipo("x.bin", b"y" * 100, "application/octet-stream")

    def test_se_borra_sola_a_las_24_h(self, almacen):
        """La decisión mía: la copia caduca. Los archivos subidos ya lo hacían."""
        from src.config import get_settings

        assert get_settings().upload_ttl_hours == 24
        copia = almacen.guardar_copia_del_equipo("x.txt", b"hola", "text/plain")
        almacen.ttl_segundos = -1
        assert almacen.limpiar_caducados() == 1
        assert almacen.obtener(copia.id) is None


class TestLaHerramientaDeLaNube:
    def _herramienta(self, almacen):
        from src.canal.herramientas import CopiarDelEquipo

        return CopiarDelEquipo(almacen)

    def test_devuelve_el_enlace_y_no_el_contenido(self, almacen, monkeypatch):
        from src.canal import herramientas

        contenido = b"%PDF-1.7\n" + b"x" * 500
        monkeypatch.setattr(herramientas, "enviar", lambda *a, **k: {
            "estado": "COMPLETED",
            "resultado": {"success": True, "data": {"name": "contrato.pdf", "bytes": len(contenido),
                                                    "tipo": "application/pdf"}},
            "_contenido": contenido,
        })
        r = self._herramienta(almacen).execute(path="C:/Docs/contrato.pdf")
        assert r["success"]
        assert r["data"]["nombre"] == "contrato.pdf" and r["data"]["bytes"] == len(contenido)
        assert r["data"]["enlace"] == f"/api/uploads/{r['data']['upload_id']}/contenido"
        assert "contenido" not in json.dumps(r["data"])[:0] or b"%PDF" not in json.dumps(r["data"]).encode()
        assert almacen.leer(r["data"]["upload_id"]) == contenido

    def test_si_el_pc_dice_que_no_se_cuenta_tal_cual(self, almacen, monkeypatch):
        from src.canal import herramientas

        monkeypatch.setattr(herramientas, "enviar", lambda *a, **k: {
            "estado": "COMPLETED",
            "resultado": {"success": False, "data": None, "motivo": "sensible",
                          "error": "Ese archivo puede tener credenciales o secretos: no se lee."},
        })
        r = self._herramienta(almacen).execute(path="C:/Docs/.env")
        assert not r["success"] and "credenciales" in r["error"]

    def test_si_el_equipo_no_esta_conectado(self, almacen, monkeypatch):
        from src.canal import despacho, herramientas

        def sin_equipo(*a, **k):
            raise despacho.AgenteNoConectado()

        monkeypatch.setattr(herramientas, "enviar", sin_equipo)
        r = self._herramienta(almacen).execute(path="C:/x.txt")
        assert not r["success"] and "no está conectado" in r["error"]

    def test_tiene_su_propio_plazo_largo(self, almacen, monkeypatch):
        from src.canal import herramientas

        plazos = []
        monkeypatch.setattr(herramientas, "enviar", lambda *a, **k: plazos.append(k.get("plazo")) or {
            "estado": "COMPLETED",
            "resultado": {"success": True, "data": {"name": "x.txt", "bytes": 4, "tipo": "text/plain"}},
            "_contenido": b"hola"})
        self._herramienta(almacen).execute(path="C:/x.txt")
        assert plazos == [self._herramienta(almacen).PLAZO] and plazos[0] >= 120

    def test_solo_existe_con_el_equipo_conectado(self, almacen):
        assert self._herramienta(almacen).disponible() is False


def container_nombre(web, upload_id):
    """El nombre tal como quedó guardado, para saber si la prueba prueba algo."""
    return [a["nombre"] for a in web.get("/uploads").json()["uploads"] if a["id"] == upload_id][0]


class TestLaDescarga:
    """La ruta que convierte una copia en un archivo en el móvil."""

    @pytest.fixture
    def nube(self, monkeypatch):
        from src.api import dependencies
        from src.api.app import create_app

        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        dependencies.reset_container()
        yield create_app()
        reset_settings()

    def _cuenta(self, app, nombre, ip):
        web = TestClient(app)
        r = web.post("/auth/registro", json={"username": nombre, "email": f"{nombre}@ejemplo.co",
                                             "password": CLAVE}, headers={"X-Forwarded-For": ip})
        assert r.status_code == 200, r.text
        return web, r.json()["csrf"]

    def _subir(self, web, csrf, nombre, contenido, tipo="text/plain"):
        r = web.post("/uploads", files={"file": (nombre, contenido, tipo)},
                     headers={"x-morgan-csrf": csrf})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def test_se_descarga_tal_cual_y_como_adjunto(self, nube):
        web, csrf = self._cuenta(nube, "ana", "10.5.0.1")
        contenido = "informe con acentos: ñ á\n".encode("utf-8")
        upload_id = self._subir(web, csrf, "informe.txt", contenido)

        r = web.get(f"/uploads/{upload_id}/contenido")
        assert r.status_code == 200
        assert r.content == contenido
        assert r.headers["content-disposition"].startswith("attachment;")
        assert "informe.txt" in r.headers["content-disposition"]
        assert r.headers["x-content-type-options"] == "nosniff"

    def test_el_archivo_de_otra_persona_no_se_descarga(self, nube):
        web, csrf = self._cuenta(nube, "ana", "10.5.0.2")
        upload_id = self._subir(web, csrf, "mio.txt", b"privado")
        otra, _ = self._cuenta(nube, "bruno", "10.5.0.3")

        assert otra.get(f"/uploads/{upload_id}/contenido").status_code == 404
        assert web.get(f"/uploads/{upload_id}/contenido").status_code == 200

    def test_uno_que_no_existe(self, nube):
        web, _ = self._cuenta(nube, "ana", "10.5.0.4")
        r = web.get("/uploads/inventado/contenido")
        assert r.status_code == 404
        assert "NO_ENCONTRADO" in r.text and "caduc" in r.text

    def test_sin_sesion_no_se_descarga(self, nube):
        web, csrf = self._cuenta(nube, "ana", "10.5.0.5")
        upload_id = self._subir(web, csrf, "mio.txt", b"privado")
        assert TestClient(nube).get(f"/uploads/{upload_id}/contenido").status_code == 401

    def test_un_html_no_se_abre_como_pagina(self, nube):
        """Servirlo como página en el dominio de Morgan sería ejecutar su script con la
        sesión de su dueño. Va siempre como descarga y con nosniff."""
        web, csrf = self._cuenta(nube, "ana", "10.5.0.6")
        upload_id = self._subir(web, csrf, "pagina.html", b"<script>alert(1)</script>", "text/html")
        r = web.get(f"/uploads/{upload_id}/contenido")
        assert r.headers["content-disposition"].startswith("attachment;")
        assert r.headers["x-content-type-options"] == "nosniff"

    def test_un_nombre_con_comillas_o_saltos_no_parte_la_cabecera(self):
        """El nombre viene del PC de la persona, y un archivo puede llamarse casi
        cualquier cosa. Se prueba la ruta directamente: al subir por HTTP, el cliente ya
        codifica el nombre, así que por ahí nunca llegaría hostil."""
        from types import SimpleNamespace

        from src.api.routes.uploads import descargar

        malo = 'raro"; x=1' + chr(13) + chr(10) + "Set-Cookie: robada=1.txt"
        archivo = SimpleNamespace(nombre_original=malo, mime="text/plain")
        fingido = SimpleNamespace(uploads=SimpleNamespace(
            obtener=lambda _id: archivo, leer=lambda _id: b"hola"))

        r = descargar("up-1", container=fingido)
        cabecera = r.headers["content-disposition"]
        assert cabecera.count(chr(34)) == 2                # solo las dos del nombre
        assert not set(cabecera) & {chr(13), chr(10)}   # nada que parta la cabecera
        assert "robada" not in {k.lower() for k in r.headers}
        assert r.body == b"hola"


class TestLoQueOcupaEnElPrompt:
    """Medido en mi PC (2026-09-19): con el catálogo del equipo, tres búsquedas y
    varios listados en el historial, una petición llegó a **8.459 tokens** y Groq la
    rechazó (su tope son 8.000 por minuto). Gemini estaba saturado y no quedó respaldo:
    el turno murió con «fallo durante la ejecución del agente». Cada palabra del catálogo
    se paga en **todas** las llamadas del turno."""

    def _catalogo(self, de_escritura: bool = False):
        from src.canal.herramientas import EscribirEnElEquipo, herramientas_del_equipo

        return sum(len(json.dumps({"name": h.name, "description": h.description,
                                   "parameters": h.parameters}, ensure_ascii=False))
                   for h in herramientas_del_equipo(store=object())
                   if (type(h) is EscribirEnElEquipo) == de_escritura
                   and type(h).__name__ not in ("TerminalDelEquipo", "ArchivoDelEquipo",
                                                "DiagnosticoDelEquipo", "AplicacionDelEquipo",
                                                "ServicioDelEquipo", "AvisoDelEquipo",
                                                "VentanaDelEquipo", "CapturaDelEquipo",
                                                "InterfazDelEquipo"))

    def test_las_de_interfaz_tampoco(self):
        """Leer controles y manejar ratón y teclado (4.12), solo si están encendidas."""
        from src.canal.herramientas import InterfazDelEquipo, herramientas_del_equipo

        total = sum(len(json.dumps({"name": h.name, "description": h.description,
                                    "parameters": h.parameters}, ensure_ascii=False))
                    for h in herramientas_del_equipo(store=object()) if isinstance(h, InterfazDelEquipo))
        assert 0 < total < 1000, "mira el tope de Groq"

    def test_las_de_pantalla_tampoco(self):
        """Ventanas y capturas (4.11), solo si están encendidas en el PC."""
        from src.canal.herramientas import CapturaDelEquipo, VentanaDelEquipo, herramientas_del_equipo

        total = sum(len(json.dumps({"name": h.name, "description": h.description,
                                    "parameters": h.parameters}, ensure_ascii=False))
                    for h in herramientas_del_equipo(store=object())
                    if isinstance(h, (VentanaDelEquipo, CapturaDelEquipo)))
        assert 0 < total < 1400, "mira el tope de Groq"

    def test_las_de_avisos_tampoco(self):
        """El portapapeles y los avisos (4.10), solo si están encendidas en el PC."""
        from src.canal.herramientas import AvisoDelEquipo, herramientas_del_equipo

        total = sum(len(json.dumps({"name": h.name, "description": h.description,
                                    "parameters": h.parameters}, ensure_ascii=False))
                    for h in herramientas_del_equipo(store=object()) if isinstance(h, AvisoDelEquipo))
        assert 0 < total < 550, "mira el tope de Groq"

    def test_las_de_aplicaciones_tampoco(self):
        """Abrir y cerrar (4.8), dos herramientas, solo si están encendidas en el PC."""
        from src.canal.herramientas import AplicacionDelEquipo, herramientas_del_equipo

        total = sum(len(json.dumps({"name": h.name, "description": h.description,
                                    "parameters": h.parameters}, ensure_ascii=False))
                    for h in herramientas_del_equipo(store=object()) if isinstance(h, AplicacionDelEquipo))
        assert 0 < total < 1000, "mira el tope de Groq"

    def test_la_del_pc_por_dentro_tampoco(self):
        """`pc_diagnostics` (4.7), una sola con `aspecto` en vez de cuatro: 471 caracteres
        (~120 tokens), solo si está encendida en el PC. `pc_context` (4.13), 326 más: va en
        cuanto hay carpetas, así que se mide aparte."""
        from src.canal.herramientas import DiagnosticoDelEquipo, herramientas_del_equipo

        medida = {h.name: len(json.dumps({"name": h.name, "description": h.description,
                                          "parameters": h.parameters}, ensure_ascii=False))
                  for h in herramientas_del_equipo(store=object()) if isinstance(h, DiagnosticoDelEquipo)}
        assert 0 < medida["pc_diagnostics"] < 550, "mira el tope de Groq"
        assert 0 < medida["pc_context"] < 400, "mira el tope de Groq"

    def test_las_de_archivos_tampoco(self):
        """Copiar, comprimir y los metadatos (4.6): cada una solo si está encendida en el
        PC. Medido al crearlas: 885 caracteres (~220 tokens) las tres."""
        from src.canal.herramientas import ArchivoDelEquipo, herramientas_del_equipo

        total = sum(len(json.dumps({"name": h.name, "description": h.description,
                                    "parameters": h.parameters}, ensure_ascii=False))
                    for h in herramientas_del_equipo(store=object()) if isinstance(h, ArchivoDelEquipo))
        assert total < 1000, "mira el tope de Groq"

    def test_las_de_la_terminal_tampoco(self):
        """Solo están si la persona encendió la terminal en su PC (3.5)."""
        from src.canal.herramientas import TerminalDelEquipo, herramientas_del_equipo

        total = sum(len(json.dumps({"name": h.name, "description": h.description,
                                    "parameters": h.parameters}, ensure_ascii=False))
                    for h in herramientas_del_equipo(store=object()) if isinstance(h, TerminalDelEquipo))
        # 1.285 en la 3.5; con npm, pip, winget y los scripts propios (4.9), ~1.480: hay que
        # nombrarlos para que el modelo sepa que existen. El tope sube a 1.600 a sabiendas.
        assert total < 1600, "mira el tope de Groq"

    def test_la_de_servicios_tampoco(self):
        """Arrancar y parar servicios (4.9), aparte y solo si está encendida en el PC."""
        from src.canal.herramientas import ServicioDelEquipo, herramientas_del_equipo

        total = sum(len(json.dumps({"name": h.name, "description": h.description,
                                    "parameters": h.parameters}, ensure_ascii=False))
                    for h in herramientas_del_equipo(store=object()) if isinstance(h, ServicioDelEquipo))
        assert 0 < total < 450, "mira el tope de Groq"

    def test_las_herramientas_del_equipo_no_engordan(self):
        assert self._catalogo() < 3000, "el catálogo del equipo creció: mira el tope de Groq"

    def test_las_de_escritura_tampoco(self):
        """Solo están si la persona encendió la escritura en su PC (3.3), pero entonces
        van en todas las llamadas del turno.

        1.141 en la 3.3; 1.412 con `append_file` (4.1, decisión mía) y 1.351 tras
        recortar las descripciones («en el PC» ya lo dice la sección del prompt). El tope
        sube a 1.400 a sabiendas, para que siga avisando si crece más."""
        assert self._catalogo(de_escritura=True) < 1400, "mira el tope de Groq"

    def test_el_prompt_dice_como_mandar_un_archivo_en_dos_pasos(self):
        """Lo que gastó mi turno: tres búsquedas y seis listados, carpeta a
        carpeta, sin llegar nunca a copiar."""
        from src.agent.prompt import CON_EQUIPO_REMOTO

        assert "search_files SIN ruta" in CON_EQUIPO_REMOTO
        assert "No vayas abriendo carpetas una a una" in CON_EQUIPO_REMOTO
        assert "copy_file" in CON_EQUIPO_REMOTO


class TestElAvisoParaLaWeb:
    """probé la primera versión (2026-09-19): «me da como un tipo de enlace pero no
    hay un botón físico de descargar». El modelo escribía la dirección a su manera, y el
    chat solo convierte en botón la ruta que sirve este servidor. Ahora la herramienta
    **avisa** cuando la copia está lista y el chat pinta el botón sin leer el texto."""

    def test_al_guardar_la_copia_se_avisa_a_la_web(self, almacen, monkeypatch):
        from src.canal import herramientas
        from src.canal.herramientas import CopiarDelEquipo
        from src.eventos_turno import CanalDelTurno, escuchando

        contenido = b"%PDF-1.7\nbitacora"
        monkeypatch.setattr(herramientas, "enviar", lambda *a, **k: {
            "estado": "COMPLETED",
            "resultado": {"success": True, "data": {"name": "bitacora.pdf",
                                                    "bytes": len(contenido), "tipo": "application/pdf"}},
            "_contenido": contenido,
        })
        canal = CanalDelTurno()
        with escuchando(canal):
            r = CopiarDelEquipo(almacen).execute(path="C:/Docs/bitacora.pdf")

        avisos = [e for e in list(canal.cola.queue) if isinstance(e, dict) and e.get("tipo") == "archivo_listo"]
        assert len(avisos) == 1
        assert avisos[0]["upload_id"] == r["data"]["upload_id"]
        assert avisos[0]["nombre"] == "bitacora.pdf"
        assert avisos[0]["bytes"] == len(contenido)

    def test_si_falla_no_se_avisa(self, almacen, monkeypatch):
        from src.canal import herramientas
        from src.canal.herramientas import CopiarDelEquipo
        from src.eventos_turno import CanalDelTurno, escuchando

        monkeypatch.setattr(herramientas, "enviar", lambda *a, **k: {
            "estado": "COMPLETED", "resultado": {"success": False, "error": "no se puede"}})
        canal = CanalDelTurno()
        with escuchando(canal):
            CopiarDelEquipo(almacen).execute(path="C:/Docs/.env")
        assert not [e for e in list(canal.cola.queue)
                    if isinstance(e, dict) and e.get("tipo") == "archivo_listo"]
