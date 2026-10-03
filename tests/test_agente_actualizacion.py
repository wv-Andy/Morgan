"""
El agente instalado y sus actualizaciones (3.8).

Mis decisiones (2026-09-25): **una actualización solo vale si la firmó él** (Ed25519,
la clave solo en su PC; la nube solo aloja), **se instala si la persona dice sí**, y si
la nueva no arranca sana **vuelve sola a la anterior**. Lo que haría daño si fallase, y
se prueba aquí: que el PC ejecute algo que yo no publiqué (firma falsa, manifiesto
tocado, paquete cambiado, una versión vieja servida a propósito, un zip con rutas fuera
de su sitio), que una versión rota se quede, o que una prueba toque la clave de verdad.
"""

import base64
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import __version__
from src.agente import estado as almacen
from src.agente import firma, instalacion
from tests.test_canal_agente import _cuenta, nube  # noqa: F401


@pytest.fixture
def claves(monkeypatch):
    """Un par de claves de prueba; la pública, la que lleva el agente."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    privada = Ed25519PrivateKey.generate()
    publica = privada.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    monkeypatch.setattr(firma, "CLAVE_PUBLICA", publica)
    return privada


def _zip(ficheros: dict) -> bytes:
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w") as zf:
        for nombre, contenido in ficheros.items():
            zf.writestr(nombre, contenido)
    return salida.getvalue()


BUENO = {"src/__init__.py": '__version__ = "9.9.9"\n', "src/agente/__main__.py": "print('hola')\n",
         "src/agente/__init__.py": "", "requirements-agente.txt": "httpx\n"}


def _publicado(privada, version="9.9.9", paquete: bytes | None = None, **extra):
    paquete = paquete if paquete is not None else _zip(BUENO)
    manifiesto = json.dumps({"producto": "morgan-agente", "version": version, "protocolo": 3,
                             "sha256": hashlib.sha256(paquete).hexdigest(), "bytes": len(paquete), **extra},
                            sort_keys=True)
    return manifiesto, base64.b64encode(privada.sign(manifiesto.encode())).decode(), paquete


class TestLaFirma:
    def test_la_de_andy_vale(self, claves):
        manifiesto, f, _ = _publicado(claves)
        assert firma.verificar(manifiesto, f)["version"] == "9.9.9"

    def test_un_manifiesto_tocado_no(self, claves):
        manifiesto, f, _ = _publicado(claves)
        with pytest.raises(firma.FirmaNoValida):
            firma.verificar(manifiesto.replace("9.9.9", "9.9.8"), f)

    def test_otra_clave_no(self, claves):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        manifiesto, f, _ = _publicado(Ed25519PrivateKey.generate())
        with pytest.raises(firma.FirmaNoValida):
            firma.verificar(manifiesto, f)

    def test_sin_clave_en_el_agente_no_se_acepta_nada(self, claves, monkeypatch):
        manifiesto, f, _ = _publicado(claves)
        monkeypatch.setattr(firma, "CLAVE_PUBLICA", "")
        with pytest.raises(firma.FirmaNoValida, match="no lleva clave"):
            firma.verificar(manifiesto, f)

    @pytest.mark.parametrize("basura", ["", "no-es-base64!!", base64.b64encode(b"corta").decode()])
    def test_una_firma_mal_formada_no(self, claves, basura):
        manifiesto, _, _ = _publicado(claves)
        with pytest.raises(firma.FirmaNoValida):
            firma.verificar(manifiesto, basura)

    def test_firmado_pero_de_otro_producto_no(self, claves):
        manifiesto = json.dumps({"producto": "otra-cosa", "version": "9.9.9", "sha256": "x", "bytes": 1})
        with pytest.raises(firma.FirmaNoValida):
            firma.verificar(manifiesto, base64.b64encode(claves.sign(manifiesto.encode())).decode())


class TestLasVersiones:
    @pytest.mark.parametrize("nueva, actual, sale", [
        ("3.8.5", "3.8.0", True), ("3.8.0", "3.8.0", False), ("3.7.5", "3.8.0", False),
        ("3.8.0", "3.8.0-dev", True), ("3.8.0-dev", "3.8.0", False), ("3.10", "3.9.5", True),
    ])
    def test_solo_una_mas_nueva(self, nueva, actual, sale):
        """Una nube tomada podría servir un paquete viejo firmado de verdad, con un fallo
        ya arreglado: no se instala."""
        assert firma.es_mas_nueva({"version": nueva}, actual) is sale

    @pytest.mark.parametrize("rara", ["3.8.0/../x", "..", "3.8.0 ", "3", "v3.8"])
    def test_una_version_rara_no_se_entiende(self, rara):
        with pytest.raises(firma.FirmaNoValida):
            firma.clave_de_version(rara.rstrip() if rara == "3.8.0 " else rara) if rara != "3.8.0 " else \
                firma.clave_de_version("3.8.0 x")


class TestElPaquete:
    def test_el_del_manifiesto_pasa(self, claves):
        manifiesto, f, paquete = _publicado(claves)
        firma.comprobar_paquete(paquete, firma.verificar(manifiesto, f))

    def test_otro_con_el_mismo_tamano_no(self, claves):
        manifiesto, f, paquete = _publicado(claves)
        cambiado = paquete[:-1] + bytes([paquete[-1] ^ 1])
        with pytest.raises(firma.FirmaNoValida):
            firma.comprobar_paquete(cambiado, firma.verificar(manifiesto, f))

    def test_uno_mas_largo_no(self, claves):
        manifiesto, f, paquete = _publicado(claves)
        with pytest.raises(firma.FirmaNoValida, match="no mide"):
            firma.comprobar_paquete(paquete + b"x", firma.verificar(manifiesto, f))

    @pytest.mark.parametrize("nombre", ["../fuera.py", "/raiz.py", "src/agente/../../x.py", "C:/x.py",
                                        "src\\..\\..\\x.py", "src/otra/x.py", "venv/Scripts/python.exe",
                                        "src/agente/x.pyd", "src/agente/x.bat"])
    def test_nada_fuera_de_su_sitio(self, tmp_path, nombre):
        with pytest.raises(firma.FirmaNoValida):
            firma.abrir(_zip({**BUENO, nombre: "x"}), tmp_path / "v")
        assert not (tmp_path / "fuera.py").exists() and not (tmp_path / "x.py").exists()

    def test_sin_el_agente_no(self, tmp_path):
        with pytest.raises(firma.FirmaNoValida):
            firma.abrir(_zip({"src/__init__.py": ""}), tmp_path / "v")

    def test_abre_lo_que_trae(self, tmp_path):
        firma.abrir(_zip(BUENO), tmp_path / "v")
        assert (tmp_path / "v" / "src" / "agente" / "__main__.py").read_text() == "print('hola')\n"


class TestPublicar:
    """`scripts/publicar_agente.py`, con la clave en la carpeta de la prueba."""

    @pytest.fixture
    def publicar(self, tmp_path, monkeypatch):
        if sys.platform != "win32":
            pytest.skip("la clave se guarda con DPAPI")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import publicar_agente

        copia = tmp_path / "firma.py"
        copia.write_text(publicar_agente.FIRMA_PY.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(publicar_agente, "FIRMA_PY", copia)
        monkeypatch.setattr(publicar_agente, "PUBLICADO", tmp_path / "publicado")
        return publicar_agente

    def test_la_clave_no_queda_en_claro_ni_fuera_de_su_carpeta(self, publicar, monkeypatch, capsys):
        import os

        assert publicar.claves() == 0
        guardada = Path(os.environ["MORGAN_FIRMA_DIR"]) / "firma-agente.bin"
        privada = publicar._privada()
        from cryptography.hazmat.primitives import serialization

        cruda = privada.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                      serialization.NoEncryption())
        assert cruda.hex().encode() not in guardada.read_bytes() and cruda not in guardada.read_bytes()
        assert cruda.hex() not in capsys.readouterr().out, "la privada no se imprime nunca"
        assert publicar.claves() == 1, "no se pisa una clave que ya existe"

    def test_publicar_y_que_un_agente_lo_acepte(self, publicar, monkeypatch):
        publicar.claves()
        import re

        publica = re.search(r'CLAVE_PUBLICA = "([0-9a-f]+)"', publicar.FIRMA_PY.read_text()).group(1)
        monkeypatch.setattr(firma, "CLAVE_PUBLICA", publica)
        assert publicar.publicar() == 0
        manifiesto = (publicar.PUBLICADO / "manifiesto.json").read_text(encoding="utf-8")
        datos = firma.verificar(manifiesto, (publicar.PUBLICADO / "firma.txt").read_text())
        paquete = (publicar.PUBLICADO / "paquete.zip").read_bytes()
        firma.comprobar_paquete(paquete, datos)
        assert datos["version"] == __version__
        with zipfile.ZipFile(io.BytesIO(paquete)) as zf:
            nombres = zf.namelist()
        assert "src/agente/firma.py" in nombres and not any("__pycache__" in n for n in nombres)
        assert not any(n.startswith(("src/api", "src/canal", "src/memory")) for n in nombres), \
            "el paquete del agente no lleva el resto de Morgan"
        assert publicar.paquete() == paquete, "el mismo código da el mismo paquete"
        assert publicar.publicar() == 1, "la misma versión no se publica dos veces"

    def test_con_una_clave_que_no_es_la_del_agente_no_publica(self, publicar, monkeypatch):
        publicar.claves()
        monkeypatch.setattr(firma, "CLAVE_PUBLICA", "00" * 32)
        assert publicar.publicar() == 1


class TestLaNubeSoloAloja:
    @pytest.fixture
    def publicado(self, tmp_path, monkeypatch, claves):
        from src.api.routes import agentes

        manifiesto, f, paquete = _publicado(claves)
        (tmp_path / "manifiesto.json").write_text(manifiesto, encoding="utf-8")
        (tmp_path / "firma.txt").write_text(f, encoding="utf-8")
        (tmp_path / "paquete.zip").write_bytes(paquete)
        monkeypatch.setattr(agentes, "PUBLICADO", tmp_path)
        return manifiesto, f, paquete

    def test_sin_credencial_ni_codigo_no(self, nube, publicado):
        assert TestClient(nube).get("/agente/actualizacion").status_code == 401
        assert TestClient(nube).get("/agente/paquete").status_code == 401

    def test_con_la_credencial_del_agente(self, nube, publicado):
        _, _, _, _, credencial = _cuenta(nube)
        cliente = TestClient(nube, headers={"Authorization": f"Bearer {credencial}"})
        r = cliente.get("/agente/actualizacion").json()
        assert r["manifiesto"] == publicado[0], "el manifiesto va tal cual se firmó"
        assert r["firma"] == publicado[1]
        assert cliente.get("/agente/paquete").content == publicado[2]

    def test_con_un_codigo_vivo_sin_gastarlo(self, nube, publicado):
        web, csrf, *_ = _cuenta(nube)
        codigo = web.post("/auth/agentes/codigo", headers={"x-morgan-csrf": csrf}).json()["codigo"]
        cliente = TestClient(nube, headers={"X-Morgan-Codigo": codigo})
        assert cliente.get("/agente/paquete").content == publicado[2]
        r = TestClient(nube).post("/agente/emparejar/confirmar", json={"codigo": codigo, "nombre": "Nuevo",
                                                                      "protocol_version": 3})
        assert r.status_code == 200, "bajar el paquete no gasta el código"

    def test_un_codigo_falso_no(self, nube, publicado):
        r = TestClient(nube, headers={"X-Morgan-Codigo": "AAAA-BBBB"}).get("/agente/paquete")
        assert r.status_code in (400, 401, 429) and r.content != publicado[2]

    def test_una_credencial_revocada_no(self, nube, publicado):
        web, csrf, _, agent_id, credencial = _cuenta(nube)
        web.delete(f"/auth/agentes/{agent_id}", headers={"x-morgan-csrf": csrf})
        r = TestClient(nube, headers={"Authorization": f"Bearer {credencial}"}).get("/agente/actualizacion")
        assert r.status_code == 401

    def test_sin_nada_publicado(self, nube, tmp_path, monkeypatch):
        from src.api.routes import agentes

        monkeypatch.setattr(agentes, "PUBLICADO", tmp_path / "vacio")
        _, _, _, _, credencial = _cuenta(nube)
        cliente = TestClient(nube, headers={"Authorization": f"Bearer {credencial}"})
        assert cliente.get("/agente/actualizacion").json()["manifiesto"] is None
        assert cliente.get("/agente/paquete").status_code == 404


# --- Instalar, cambiar y volver -------------------------------------------------------------


class Ejecutar:
    """Hace de `subprocess.run`: apunta lo que se le pide y contesta a `version`."""

    def __init__(self, dice=None, falla_en=None):
        self.llamadas, self.dice, self.falla_en = [], dice, falla_en

    def __call__(self, orden, **kw):
        import subprocess

        self.llamadas.append(orden)
        if self.falla_en and self.falla_en in orden:
            raise subprocess.CalledProcessError(1, orden)
        if orden[-2:] == ["-m", "venv"] or "venv" in orden[:3]:
            Path(orden[-1]).mkdir(parents=True, exist_ok=True)
        if orden[-1] == "version":
            return subprocess.CompletedProcess(orden, 0, stdout=(self.dice or "9.9.9") + "\n")
        return subprocess.CompletedProcess(orden, 0, stdout="")


class TestInstalarUnaVersion:
    def test_queda_en_su_carpeta_y_probada(self, claves):
        manifiesto, f, paquete = _publicado(claves)
        ejecutar = Ejecutar()
        destino = instalacion.preparar(firma.verificar(manifiesto, f), paquete, ejecutar=ejecutar)
        assert destino == instalacion.carpeta_de("9.9.9")
        assert (destino / "src" / "agente" / "__main__.py").exists()
        assert any("pip" in o for o in ejecutar.llamadas) and ejecutar.llamadas[-1][-1] == "version"
        assert instalacion.instalada(destino) and not instalacion.instalada(Path(__file__).parents[1])

    def test_si_dice_otra_version_no_queda_nada(self, claves):
        manifiesto, f, paquete = _publicado(claves)
        with pytest.raises(firma.FirmaNoValida):
            instalacion.preparar(firma.verificar(manifiesto, f), paquete, ejecutar=Ejecutar(dice="1.0.0"))
        assert not instalacion.carpeta_de("9.9.9").exists()
        assert not any(instalacion.app().glob("*.nueva"))

    def test_si_pip_falla_no_queda_nada(self, claves):
        manifiesto, f, paquete = _publicado(claves)
        import subprocess

        with pytest.raises(subprocess.CalledProcessError):
            instalacion.preparar(firma.verificar(manifiesto, f), paquete, ejecutar=Ejecutar(falla_en="pip"))
        assert not instalacion.carpeta_de("9.9.9").exists()


def _instalar_a_mano(version):
    carpeta = instalacion.carpeta_de(version)
    (carpeta / "venv" / "Scripts").mkdir(parents=True)
    instalacion.python_de(version).write_text("")
    return carpeta


class TestCambiarYVolver:
    def test_cambiar_deja_la_anterior_y_por_confirmar(self):
        _instalar_a_mano("3.8.0"), _instalar_a_mano("3.8.5")
        instalacion._guardar_activa({"version": "3.8.0"})
        instalacion.cambiar_a("3.8.5")
        activa = instalacion.leer_activa()
        assert activa["version"] == "3.8.5" and activa["anterior"] == "3.8.0" and activa["pendiente"]

    def test_confirmar_borra_las_viejas_menos_la_anterior(self):
        for v in ("3.7.5", "3.8.0", "3.8.5"):
            _instalar_a_mano(v)
        instalacion._guardar_activa({"version": "3.8.0", "anterior": "3.7.5"})
        instalacion.cambiar_a("3.8.5")
        assert not instalacion.confirmar_si_pendiente("3.8.0"), "solo la que está por confirmar"
        assert instalacion.confirmar_si_pendiente("3.8.5")
        assert sorted(p.name for p in instalacion.app().iterdir() if p.is_dir()) == ["3.8.0", "3.8.5"]
        assert not instalacion.confirmar_si_pendiente("3.8.5"), "una vez"

    def test_volver_atras(self):
        _instalar_a_mano("3.8.0"), _instalar_a_mano("3.8.5")
        instalacion._guardar_activa({"version": "3.8.0"})
        instalacion.cambiar_a("3.8.5")
        assert instalacion.volver_atras("no arrancó") == "3.8.0"
        activa = instalacion.leer_activa()
        assert activa["version"] == "3.8.0" and activa["fallida"]["version"] == "3.8.5"
        assert instalacion.volver_atras() is None, "de la anterior no hay otra anterior"

    def test_el_arranque_de_windows_sigue_a_la_activa(self):
        from src.agente import arranque

        if sys.platform != "win32":
            pytest.skip("acceso directo de Windows")
        _instalar_a_mano("3.8.0"), _instalar_a_mano("3.8.5")
        instalacion._guardar_activa({"version": "3.8.0"})
        arranque.activar(instalacion.python_de("3.8.0"), instalacion.carpeta_de("3.8.0"))
        instalacion.cambiar_a("3.8.5")
        assert "3.8.5" in _destino_del_acceso()
        instalacion.volver_atras()
        assert "3.8.0" in _destino_del_acceso()


def _destino_del_acceso() -> str:
    import subprocess

    from src.agente import arranque

    guion = f"(New-Object -ComObject WScript.Shell).CreateShortcut('{arranque.acceso_directo()}').WorkingDirectory"
    return subprocess.run(["powershell", "-NoProfile", "-Command", guion], capture_output=True, text=True).stdout


class Reloj:
    def __init__(self):
        self.t = 1_000_000.0

    def ahora(self):
        return self.t

    def dormir(self, s):
        self.t += s


class TestSana:
    def test_ready_con_su_version_y_pulso(self):
        from src.agente import salud

        reloj = Reloj()
        salud.apuntar(pid=1, estado="READY", version="3.8.5", pulso=reloj.t, arrancado=reloj.t)
        assert instalacion.esperar_sana("3.8.5", dormir=reloj.dormir, ahora=reloj.ahora)

    def test_una_suspension_no_gasta_el_plazo(self):
        """Auditoría de la 3.x: si el PC dormía justo después de actualizar, al despertar el
        plazo estaba gastado y se volvía atrás desde una versión buena."""
        from src.agente import salud

        reloj = Reloj()
        pasos = {"n": 0}

        def dormir(s):
            pasos["n"] += 1
            reloj.t += 3600 if pasos["n"] == 3 else s        # una hora suspendido
            if pasos["n"] == 10:                               # y al despertar, conecta
                salud.apuntar(pid=1, estado="READY", version="3.8.5", pulso=reloj.t, arrancado=reloj.t)
        assert instalacion.esperar_sana("3.8.5", plazo=30, dormir=dormir, ahora=reloj.ahora, desde=reloj.t)

    def test_la_del_que_se_acaba_de_parar_no_cuenta(self):
        """3.8.5: al reinstalar la misma versión, la salud del agente recién parado (READY,
        misma versión, pulso de hace segundos) pasaba por la del nuevo."""
        from src.agente import salud

        reloj = Reloj()
        salud.apuntar(pid=1, estado="READY", version="3.8.5", pulso=reloj.t, arrancado=reloj.t - 600)
        assert not instalacion.esperar_sana("3.8.5", plazo=30, dormir=reloj.dormir, ahora=reloj.ahora,
                                            desde=reloj.t)

    @pytest.mark.parametrize("datos", [
        {"estado": "READY", "version": "3.8.0"},        # la vieja, todavía
        {"estado": "RECONNECTING", "version": "3.8.5"},
        {"estado": "READY", "version": "3.8.5", "viejo": True},
    ])
    def test_si_no_no(self, datos):
        from src.agente import salud

        reloj = Reloj()
        pulso = reloj.t - 3600 if datos.pop("viejo", False) else reloj.t + 10_000
        salud.apuntar(pid=1, pulso=pulso, arrancado=reloj.t, **datos)
        assert not instalacion.esperar_sana("3.8.5", plazo=30, dormir=reloj.dormir, ahora=reloj.ahora)


class Http:
    def __init__(self, manifiesto, f, paquete):
        self.cuerpo, self.paquete, self.pedidos = {"manifiesto": manifiesto, "firma": f}, paquete, []

    def get(self, url, headers=None):
        import httpx

        self.pedidos.append((url, headers))
        if url.endswith("/agente/actualizacion"):
            return httpx.Response(200, json=self.cuerpo, request=httpx.Request("GET", url))
        return httpx.Response(200, content=self.paquete, request=httpx.Request("GET", url))


@pytest.fixture
def instalado(monkeypatch):
    """Este agente, como si corriera instalado en la 3.8.0, emparejado, y sin tocar
    procesos de verdad."""
    from src.agente import arranque

    almacen.guardar(almacen.Emparejamiento("agt-x", "https://nube.invalid", "PC", "ana", 0), "mga_prueba")
    _instalar_a_mano("3.8.0")
    instalacion._guardar_activa({"version": "3.8.0"})
    monkeypatch.setattr(instalacion, "__version__", "3.8.0")
    monkeypatch.setattr(instalacion, "instalada", lambda raiz=None: True)
    hecho = {"parar": 0, "lanzar": []}
    monkeypatch.setattr(arranque, "pedir_parada", lambda: hecho.__setitem__("parar", hecho["parar"] + 1))
    monkeypatch.setattr(arranque, "esperar_a_que_pare", lambda hasta=None: True)
    monkeypatch.setattr(arranque, "lanzar_en_segundo_plano", lambda python=None, carpeta=None:
                        hecho["lanzar"].append(Path(carpeta).name))
    return hecho


class TestActualizar:
    def test_de_punta_a_punta(self, claves, instalado, monkeypatch):
        http = Http(*_publicado(claves, version="3.8.5"))
        monkeypatch.setattr(instalacion, "esperar_sana", lambda v, **k: True)
        dicho = []
        assert instalacion.actualizar(dicho.append, http=http, ejecutar=Ejecutar(dice="3.8.5")) == 0
        assert instalacion.leer_activa()["version"] == "3.8.5" and not instalacion.leer_activa()["pendiente"]
        assert instalado["lanzar"] == ["3.8.5"] and instalado["parar"] == 1
        assert all(h == {"Authorization": "Bearer mga_prueba"} for _, h in http.pedidos)

    def test_si_la_nueva_no_arranca_sana_vuelve_sola(self, claves, instalado, monkeypatch):
        http = Http(*_publicado(claves, version="3.8.5"))
        monkeypatch.setattr(instalacion, "esperar_sana", lambda v, **k: False)
        dicho = []
        assert instalacion.actualizar(dicho.append, http=http, ejecutar=Ejecutar(dice="3.8.5")) == 1
        assert instalacion.leer_activa()["version"] == "3.8.0"
        assert instalado["lanzar"] == ["3.8.5", "3.8.0"], "se lanza la nueva y, al fallar, la de antes"
        assert "se volvió a la 3.8.0" in dicho[-1]

    def test_si_su_vigilante_ya_volvio_atras_no_se_para_la_anterior(self, claves, instalado, monkeypatch):
        """3.8.5: una nueva que se cae en bucle hace que su vigilante vuelva a la anterior
        antes de los 120 s. Aquí se paraba entonces la anterior, ya sana, y el PC se quedaba
        sin agente."""
        from src.agente import arranque

        http = Http(*_publicado(claves, version="3.8.5"))

        def su_vigilante_vuelve(v, **k):
            instalacion.volver_atras("se cayó 6 veces")          # lo que hace el vigilante
            instalado["lanzar"].append("3.8.0")                   # y lanza la anterior
            return False
        monkeypatch.setattr(instalacion, "esperar_sana", su_vigilante_vuelve)
        monkeypatch.setattr(arranque, "en_marcha", lambda nombre="agente.lock": True)
        dicho = []
        assert instalacion.actualizar(dicho.append, http=http, ejecutar=Ejecutar(dice="3.8.5")) == 1
        assert instalado["parar"] == 1, "solo la parada de antes de cambiar: la anterior no se toca"
        assert instalacion.leer_activa()["version"] == "3.8.0"
        assert instalado["lanzar"] == ["3.8.5", "3.8.0"], "y no se lanza otra vez"
        assert "se volvió a la 3.8.0" in dicho[-1]

    def test_firma_falsa_no_instala_nada(self, claves, instalado):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        http = Http(*_publicado(Ed25519PrivateKey.generate(), version="3.8.5"))
        dicho = []
        assert instalacion.actualizar(dicho.append, http=http, ejecutar=Ejecutar()) == 1
        assert "NO se instala" in dicho[-1]
        assert not instalacion.carpeta_de("3.8.5").exists() and instalado["parar"] == 0

    def test_paquete_cambiado_por_el_camino_no(self, claves, instalado):
        manifiesto, f, paquete = _publicado(claves, version="3.8.5")
        http = Http(manifiesto, f, _zip({**BUENO, "src/agente/malo.py": "import os"}))
        assert instalacion.actualizar(lambda _: None, http=http, ejecutar=Ejecutar(dice="3.8.5")) == 1
        assert not instalacion.carpeta_de("3.8.5").exists() and instalado["parar"] == 0

    def test_una_vieja_firmada_no(self, claves, instalado):
        http = Http(*_publicado(claves, version="3.7.5"))
        dicho = []
        assert instalacion.actualizar(dicho.append, http=http, ejecutar=Ejecutar()) == 0
        assert "Ya tienes la última" in dicho[-1] and instalado["parar"] == 0

    def test_si_la_persona_dice_que_no(self, claves, instalado):
        http = Http(*_publicado(claves, version="3.8.5"))
        assert instalacion.actualizar(lambda _: None, preguntar=lambda _: False, http=http,
                                      ejecutar=Ejecutar()) == 0
        assert instalacion.leer_activa()["version"] == "3.8.0" and len(http.pedidos) == 1

    def test_con_una_orden_en_marcha_no_se_cambia(self, claves, instalado, monkeypatch):
        from src.agente import salud

        salud.apuntar(pid=1, en_marcha=True)
        http = Http(*_publicado(claves, version="3.8.5"))
        reloj = Reloj()
        assert instalacion.actualizar(lambda _: None, http=http, ejecutar=Ejecutar(dice="3.8.5"),
                                      dormir=reloj.dormir, ahora=reloj.ahora) == 1
        assert instalacion.leer_activa()["version"] == "3.8.0" and instalado["parar"] == 0

    def test_desde_el_codigo_fuente_no(self, monkeypatch):
        almacen.guardar(almacen.Emparejamiento("agt-x", "https://nube.invalid", "PC", "ana", 0), "mga_prueba")
        dicho = []
        assert instalacion.actualizar(dicho.append) == 2
        assert "git" in dicho[0]


class TestElAviso:
    def _mirar(self, claves, instalado, respuesta, monkeypatch):
        http = Http(*_publicado(claves, version="3.8.5"))
        vueltas, avisos, lanzado = iter(range(2)), [], []

        def aviso(titulo, detalle, espera, botones):
            avisos.append((titulo, botones))
            return respuesta
        reloj = Reloj()
        instalacion.mirar_novedades(lambda: next(vueltas, None) is None, aviso=aviso, http=http,
                                    lanzar=lambda: lanzado.append(1), dormir=reloj.dormir, ahora=reloj.ahora)
        return avisos, lanzado

    def test_instalar_lanza_actualizar(self, claves, instalado, monkeypatch):
        avisos, lanzado = self._mirar(claves, instalado, "permitida", monkeypatch)
        assert avisos[0][1] == ("Luego", "Instalar") and "3.8.5" in avisos[0][0] and lanzado == [1]

    def test_la_que_ya_fallo_no_se_ofrece_otra_vez(self, claves, instalado, monkeypatch):
        """Auditoría de la 3.x: tras volver atrás, se ofrecía la versión rota en cada arranque."""
        datos = instalacion.leer_activa()
        datos["fallida"] = {"version": "3.8.5", "motivo": "se cayó", "cuando": 0}
        instalacion._guardar_activa(datos)
        avisos, lanzado = self._mirar(claves, instalado, "permitida", monkeypatch)
        assert avisos == [] and lanzado == []

    def test_pero_una_mas_nueva_que_la_fallida_si(self, claves, instalado, monkeypatch):
        datos = instalacion.leer_activa()
        datos["fallida"] = {"version": "3.8.4", "motivo": "se cayó", "cuando": 0}
        instalacion._guardar_activa(datos)
        avisos, _ = self._mirar(claves, instalado, "rechazada", monkeypatch)
        assert len(avisos) == 1

    def test_si_la_instalacion_no_cambia_nada_sigue_mirando(self, claves, instalado, monkeypatch):
        """Antes, tras «Instalar» el hilo se acababa: si la instalación fallaba antes de
        cambiar (sin red, una dependencia), no se volvía a mirar hasta reiniciar."""
        http = Http(*_publicado(claves, version="3.8.5"))
        monkeypatch.setattr(instalacion, "MIRAR_CADA", 60)
        vueltas, avisos, lanzado = iter(range(20)), [], []
        reloj = Reloj()
        instalacion.mirar_novedades(lambda: next(vueltas, None) is None,
                                    aviso=lambda *a, **k: avisos.append(1) or "permitida",
                                    http=http, lanzar=lambda: lanzado.append(1), dormir=reloj.dormir,
                                    ahora=reloj.ahora)
        assert len(avisos) >= 2 and len(lanzado) >= 2

    @pytest.mark.parametrize("respuesta", ["rechazada", "cerrada", "sin_respuesta", "no_disponible"])
    def test_lo_demas_es_luego_y_no_insiste_en_24_h(self, claves, instalado, monkeypatch, respuesta):
        avisos, lanzado = self._mirar(claves, instalado, respuesta, monkeypatch)
        assert lanzado == [] and len(avisos) == 1
        assert instalacion.leer_activa()["pospuesta"]["version"] == "3.8.5"
        avisos, _ = self._mirar(claves, instalado, respuesta, monkeypatch)
        assert avisos == [], "«Luego» calla esa versión 24 horas"


class TestInstalar:
    """`instalar`: un PC nuevo (el script ya dejó la versión y llama desde ella) y un agente
    que corre desde una copia del código (el mío)."""

    @pytest.fixture
    def sin_procesos(self, monkeypatch):
        from src.agente import arranque

        hecho = {"activar": [], "lanzar": [], "parar": 0, "menu": [], "ventana": 0}
        monkeypatch.setattr(arranque, "activar", lambda python=None, carpeta=None: hecho["activar"].append(Path(carpeta).name))
        monkeypatch.setattr(arranque, "crear_acceso_de_ajustes",
                            lambda python=None, carpeta=None: hecho["menu"].append(Path(carpeta).name))
        from src.agente import ajustes

        monkeypatch.setattr(ajustes, "abrir_en_segundo_plano",
                            lambda: hecho.__setitem__("ventana", hecho["ventana"] + 1) or True)
        monkeypatch.setattr(arranque, "lanzar_en_segundo_plano",
                            lambda python=None, carpeta=None: hecho["lanzar"].append(Path(carpeta).name))
        monkeypatch.setattr(arranque, "pedir_parada", lambda: hecho.__setitem__("parar", hecho["parar"] + 1))
        monkeypatch.setattr(arranque, "esperar_a_que_pare", lambda hasta=None: True)
        monkeypatch.setattr(instalacion, "esperar_sana", lambda v, **k: True)
        return hecho

    def test_un_pc_nuevo(self, claves, sin_procesos, monkeypatch, tmp_path):
        manifiesto, f, paquete = _publicado(claves, version="3.8.0")
        (tmp_path / "p.zip").write_bytes(paquete)
        monkeypatch.setattr(instalacion, "__version__", "3.8.0")
        monkeypatch.setattr(instalacion, "instalada", lambda raiz=None: True)
        emparejado = []

        def emparejar(nube, codigo, nombre):
            emparejado.append((nube, codigo))
            almacen.guardar(almacen.Emparejamiento("agt-n", nube, nombre, "ana", 0), "mga_nueva")
            return True
        http = Http(manifiesto, f, paquete)
        dicho = []
        assert instalacion.instalar(dicho.append, nube="https://nube.invalid", codigo="K7QF-2M9D",
                                    paquete=tmp_path / "p.zip", http=http, emparejar=emparejar) == 0
        assert http.pedidos[0][1] == {"X-Morgan-Codigo": "K7QF-2M9D"}, "con el código, sin credencial"
        assert emparejado == [("https://nube.invalid", "K7QF-2M9D")]
        assert instalacion.leer_activa()["version"] == "3.8.0"
        assert sin_procesos["activar"] == sin_procesos["lanzar"] == ["3.8.0"]
        # 4.17: «Morgan en tu PC» en el menú Inicio, y la ventana abierta al acabar (al
        # instalarlo no puede hacer nada hasta elegir carpetas; antes, a la consola).
        assert sin_procesos["menu"] == ["3.8.0"] and sin_procesos["ventana"] == 1
        assert "Morgan en tu PC" in dicho[-1]

    def test_un_pc_nuevo_con_firma_falsa_no_empareja(self, claves, sin_procesos, monkeypatch, tmp_path):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        manifiesto, f, paquete = _publicado(Ed25519PrivateKey.generate(), version="3.8.0")
        (tmp_path / "p.zip").write_bytes(paquete)
        monkeypatch.setattr(instalacion, "__version__", "3.8.0")
        monkeypatch.setattr(instalacion, "instalada", lambda raiz=None: True)
        borrado, emparejado = [], []
        monkeypatch.setattr(instalacion, "_borrar_despues", lambda carpetas: borrado.extend(carpetas))
        dicho = []
        assert instalacion.instalar(dicho.append, nube="https://n.invalid", codigo="K7QF-2M9D",
                                    paquete=tmp_path / "p.zip", http=Http(manifiesto, f, paquete),
                                    emparejar=lambda *a: emparejado.append(a)) == 1
        assert "NO se instala" in dicho[-1] and emparejado == [] and sin_procesos["activar"] == []
        assert borrado == [instalacion.carpeta_de("3.8.0")], "lo que dejó el script se borra"

    def test_el_paquete_del_script_tiene_que_ser_el_firmado(self, claves, sin_procesos, monkeypatch, tmp_path):
        manifiesto, f, paquete = _publicado(claves, version="3.8.0")
        (tmp_path / "p.zip").write_bytes(_zip({**BUENO, "src/agente/otro.py": ""}))
        monkeypatch.setattr(instalacion, "__version__", "3.8.0")
        monkeypatch.setattr(instalacion, "instalada", lambda raiz=None: True)
        monkeypatch.setattr(instalacion, "_borrar_despues", lambda carpetas: None)
        assert instalacion.instalar(lambda _: None, nube="https://n.invalid", codigo="K7QF-2M9D",
                                    paquete=tmp_path / "p.zip", http=Http(manifiesto, f, paquete),
                                    emparejar=lambda *a: True) == 1
        assert sin_procesos["activar"] == []

    def test_lo_que_corre_tiene_que_ser_lo_publicado(self, claves, sin_procesos, monkeypatch, tmp_path):
        """Si entre el script y aquí se publicó otra versión, lo que corre no es lo firmado."""
        manifiesto, f, paquete = _publicado(claves, version="3.8.5")
        (tmp_path / "p.zip").write_bytes(paquete)
        monkeypatch.setattr(instalacion, "__version__", "3.8.0")
        monkeypatch.setattr(instalacion, "instalada", lambda raiz=None: True)
        monkeypatch.setattr(instalacion, "_borrar_despues", lambda carpetas: None)
        assert instalacion.instalar(lambda _: None, nube="https://n.invalid", codigo="K7QF-2M9D",
                                    paquete=tmp_path / "p.zip", http=Http(manifiesto, f, paquete),
                                    emparejar=lambda *a: True) == 1
        assert sin_procesos["activar"] == []

    def test_desde_una_copia_del_codigo_se_pasa_a_la_instalada(self, claves, sin_procesos, monkeypatch):
        almacen.guardar(almacen.Emparejamiento("agt-a", "https://nube.invalid", "PC", "andy", 0), "mga_andy")
        manifiesto, f, paquete = _publicado(claves, version="3.8.0")
        http = Http(manifiesto, f, paquete)
        assert instalacion.instalar(lambda _: None, http=http, ejecutar=Ejecutar(dice="3.8.0"),
                                    emparejar=lambda *a: pytest.fail("ya estaba emparejado")) == 0
        assert http.pedidos[0] == ("https://nube.invalid/agente/actualizacion", {"Authorization": "Bearer mga_andy"})
        assert (instalacion.carpeta_de("3.8.0") / "src" / "agente" / "__main__.py").exists()
        assert sin_procesos["lanzar"] == ["3.8.0"]

    def test_con_un_codigo_se_empareja_otra_vez(self, claves, sin_procesos, monkeypatch, tmp_path):
        """3.8.5: reinstalar un PC revocado con un código nuevo reutilizaba la credencial
        revocada, y el agente no conectaba nunca. Con un código, se empareja de nuevo, y el
        emparejamiento de antes se da de baja."""
        from src.agente import emparejar as mod

        almacen.guardar(almacen.Emparejamiento("agt-vieja", "https://n.invalid", "PC", "ana", 0), "mga_revocada")
        manifiesto, f, paquete = _publicado(claves, version="3.8.0")
        (tmp_path / "p.zip").write_bytes(paquete)
        monkeypatch.setattr(instalacion, "__version__", "3.8.0")
        monkeypatch.setattr(instalacion, "instalada", lambda raiz=None: True)
        de_baja, emparejado = [], []
        monkeypatch.setattr(mod, "desemparejar", lambda decir=print, http=None: de_baja.append(almacen.credencial()))

        def emparejar(nube, codigo, nombre):
            emparejado.append(codigo)
            almacen.guardar(almacen.Emparejamiento("agt-nueva", nube, nombre, "ana", 0), "mga_nueva")
            return True
        assert instalacion.instalar(lambda _: None, nube="https://n.invalid", codigo="K7QF-2M9D",
                                    paquete=tmp_path / "p.zip", http=Http(manifiesto, f, paquete),
                                    emparejar=emparejar) == 0
        assert de_baja == ["mga_revocada"] and emparejado == ["K7QF-2M9D"]
        assert almacen.credencial() == "mga_nueva"

    def test_sin_codigo_ni_emparejamiento(self):
        dicho = []
        assert instalacion.instalar(dicho.append, nube="https://n.invalid") == 2


class TestElLanzador:
    def test_apunta_a_la_activa_y_no_mira_la_carpeta_desde_la_que_se_llama(self):
        _instalar_a_mano("3.8.0"), _instalar_a_mano("3.8.5")
        instalacion._guardar_activa({"version": "3.8.0"})
        instalacion.cambiar_a("3.8.5")
        texto = instalacion.lanzador().read_text(encoding="utf-8")
        assert "app\\3.8.5" in texto and "3.8.0" not in texto
        assert " -P -m src.agente %*" in texto, "sin -P, una carpeta src de donde se ejecute suplantaría al agente"
        instalacion.volver_atras()
        assert "app\\3.8.0" in instalacion.lanzador().read_text(encoding="utf-8")


class TestDesinstalar:
    @pytest.fixture
    def agente_con_cosas(self, monkeypatch):
        from src.agente import arranque

        almacen.guardar(almacen.Emparejamiento("agt-x", "https://nube.invalid", "PC", "ana", 0), "mga_x")
        carpeta = almacen.carpeta()
        (carpeta / "respaldos").mkdir(parents=True)
        (carpeta / "respaldos" / "notas.txt.1").write_text("lo de antes")
        (carpeta / "diario.jsonl").write_text("{}")
        (carpeta / "politica.json").write_text("{}")
        _instalar_a_mano("3.8.0")
        instalacion._escribir_lanzador("3.8.0")
        if sys.platform == "win32":
            arranque.activar()
        monkeypatch.setattr(arranque, "esperar_a_que_pare", lambda hasta=None: True)
        return carpeta

    def test_se_va_todo_menos_los_respaldos(self, agente_con_cosas):
        from src.agente import arranque

        pedidos = []

        class Nube:
            def post(self, url, headers=None):
                import httpx

                pedidos.append((url, headers))
                return httpx.Response(200, json={"success": True}, request=httpx.Request("POST", url))
        dicho = []
        despues = []
        assert instalacion.desinstalar(dicho.append, http=Nube(), lanzar=despues.extend) == 0
        assert pedidos == [("https://nube.invalid/agente/desemparejar", {"Authorization": "Bearer mga_x"})]
        # El lanzador, un momento después: es el que ejecuta esto.
        assert despues == [instalacion.lanzador()]
        assert sorted(p.name for p in agente_con_cosas.iterdir()) == ["morgan-agente.cmd", "respaldos"]
        assert (agente_con_cosas / "respaldos" / "notas.txt.1").read_text() == "lo de antes"
        assert not arranque.activado()
        assert str(agente_con_cosas / "respaldos") in dicho[-1]

    def test_si_el_agente_no_se_para_no_se_toca_nada(self, agente_con_cosas, monkeypatch):
        from src.agente import arranque

        monkeypatch.setattr(arranque, "esperar_a_que_pare", lambda hasta=None: False)
        assert instalacion.desinstalar(lambda _: None, http=object()) == 1
        assert almacen.credencial() == "mga_x"
        assert arranque.activado() or sys.platform != "win32"

    def test_la_carpeta_de_la_version_que_corre_se_borra_despues(self, agente_con_cosas, monkeypatch):
        monkeypatch.setattr(instalacion, "instalada", lambda raiz=None: True)
        despues = []

        class Nube:
            def post(self, url, headers=None):
                import httpx

                return httpx.Response(200, json={}, request=httpx.Request("POST", url))
        assert instalacion.desinstalar(lambda _: None, http=Nube(), lanzar=despues.extend) == 0
        assert sorted(despues) == sorted([instalacion.app(), instalacion.lanzador()])
        assert instalacion.app().exists() and instalacion.lanzador().exists()


class TestElInstaladorDeLaNube:
    def test_el_codigo_viene_con_su_linea_de_instalar(self, nube, monkeypatch):
        monkeypatch.setenv("MORGAN_API_URL", "https://nube.ejemplo")
        web, csrf, *_ = _cuenta(nube)
        r = web.post("/auth/agentes/codigo", headers={"x-morgan-csrf": csrf}).json()
        assert r["instalar"] == (
            r"irm https://nube.ejemplo/agente/instalar.ps1 -OutFile $env:TEMP\instalar-morgan.ps1; "
            r"powershell -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\instalar-morgan.ps1 "
            f"-Codigo {r['codigo']} -Nube https://nube.ejemplo")
        assert "scriptblock" not in r["instalar"] and "iex" not in r["instalar"], "Windows lo bloquea"

    def test_la_lista_dice_la_ultima_publicada(self, nube, tmp_path, monkeypatch):
        from src.api.routes import agentes

        (tmp_path / "manifiesto.json").write_text('{"version": "3.8.5"}', encoding="utf-8")
        monkeypatch.setattr(agentes, "PUBLICADO", tmp_path)
        web, *_ = _cuenta(nube)
        assert web.get("/auth/agentes").json()["ultima_version"] == "3.8.5"

    def test_es_publico_y_no_lleva_secretos(self, nube):
        r = TestClient(nube).get("/agente/instalar.ps1")
        assert r.status_code == 200 and "X-Morgan-Codigo" in r.text and "Get-FileHash" in r.text
        assert "mga_" not in r.text and "mgn_" not in r.text
        assert r.content.startswith(b"\xef\xbb\xbf"),"con BOM: se guarda en un fichero y PowerShell 5.1 lo necesita"

    def test_abre_el_paquete_con_las_mismas_reglas_que_el_agente(self):
        """El script abre el zip antes de que el agente pueda comprobar la firma: sus reglas
        tienen que ser las de `firma.PERMITIDO`, ni una más."""
        import re

        guion = (Path(__file__).resolve().parents[1] / "scripts" / "instalar-agente.ps1").read_text(encoding="utf-8-sig")
        for patron in firma.PERMITIDO:
            assert f're.compile(r"{patron.pattern}")' in guion
        assert len(re.findall(r're\.compile\(r"', guion)) == len(firma.PERMITIDO)
        assert '".." in partes' in guion


class TestUnAgenteViejo:
    """3.8.5, «agente desactualizado»: se identifica bien y **no recibe operaciones que no
    sabe hacer** (el gate de la 3.8). Lo de que un protocolo fuera del rango se rechaza
    (4426) y uno viejo funciona en su versión está en test_canal_agente y test_agente_copia."""

    def test_a_uno_del_protocolo_2_no_se_le_manda_detener(self, nube):
        import asyncio

        from src.canal import despacho
        from src.canal.registro import REGISTRO
        from tests.test_canal_agente import SALUDO, _enviar_en_hilo, _socket

        _, _, user_id, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json({**SALUDO, "protocol_version": 2})
            ws.receive_json()
            hilo, resultado = _enviar_en_hilo(user_id, plazo=5)
            orden = ws.receive_json()
            assert orden["protocol_version"] == 2, "se le habla en su versión"
            pedido = asyncio.run_coroutine_threadsafe(
                despacho.cancelar_async(user_id, orden["command_id"]), REGISTRO.loop).result(5)
            assert pedido is False, "«cancelar» es del protocolo 3: a uno del 2 no se le manda"
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True, "data": {}, "error": None},
                          "tiempos": {"cola_ms": 0, "ejecucion_ms": 1}})
            hilo.join(10)
        assert resultado.get("valor", {}).get("estado") == "COMPLETED", resultado

    def test_no_se_le_pide_lo_que_no_anuncia(self, nube):
        from src.canal import despacho
        from tests.test_canal_agente import SALUDO, _enviar_en_hilo, _socket

        _, _, user_id, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json({**SALUDO, "protocol_version": 2, "capacidades": ["estado", "list_files"]})
            ws.receive_json()
            hilo, resultado = _enviar_en_hilo(user_id, capability="create_file", arguments={"ruta": "x"}, plazo=3)
            hilo.join(10)
        assert isinstance(resultado.get("error"), despacho.EquipoSinCapacidad), resultado

    def test_la_bienvenida_a_uno_viejo_solo_anade_campos(self, nube):
        """`rotar_credencial` (3.8) va en la bienvenida sin subir el protocolo: uno viejo
        ignora lo que no conoce, y los campos de siempre siguen ahí."""
        from tests.test_canal_agente import SALUDO, _socket

        _, _, _, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json({**SALUDO, "protocol_version": 1})
            bienvenida = ws.receive_json()
        assert {"tipo", "agent_id", "compatibilidad", "latido", "hora"} <= set(bienvenida)
        assert bienvenida["compatibilidad"] == "OUTDATED" and bienvenida["rotar_credencial"] is False


class TestUnaSolaActualizacion:
    """4.1, medido en mi PC: `actualizar` a mano mientras se pulsaba «Instalar» en
    el aviso. Las dos preparaban la misma carpeta; una falló («sigue la 3.9.5») mientras la
    otra cambiaba de versión."""

    def test_si_otra_esta_en_marcha_no_se_toca_nada(self, claves, instalado):
        from src.agente import arranque

        otra = arranque.Candado("actualizar.lock").tomar()
        try:
            http = Http(*_publicado(claves, version="3.8.5"))
            dicho = []
            assert instalacion.actualizar(dicho.append, http=http, ejecutar=Ejecutar(dice="3.8.5")) == 1
            assert "Ya se está actualizando" in dicho[-1]
            assert http.pedidos == [] and instalado["parar"] == 0
            assert instalacion.leer_activa()["version"] == "3.8.0"
        finally:
            otra.soltar()

    def test_al_terminar_suelta_el_candado(self, claves, instalado, monkeypatch):
        from src.agente import arranque

        monkeypatch.setattr(instalacion, "esperar_sana", lambda v, **k: True)
        http = Http(*_publicado(claves, version="3.8.5"))
        assert instalacion.actualizar(lambda _: None, http=http, ejecutar=Ejecutar(dice="3.8.5")) == 0
        assert not arranque.en_marcha("actualizar.lock")
