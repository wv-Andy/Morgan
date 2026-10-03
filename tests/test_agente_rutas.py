"""
Los trucos de rutas de Windows contra la política local (3.1-D).

Medido con una sonda en mi PC antes de tocar nada: de doce trucos, **dos se
colaban**. Un **enlace duro** con nombre inocente dentro de la carpeta permitida servía
el `.env` de al lado entero, y un **flujo alternativo** (`notas.txt:oculto`) dejaba leer
datos escondidos dentro de un archivo normal. Los demás ya estaban cubiertos, y aquí
quedan fijados para que sigan estándolo.
"""

import os
import subprocess
import sys

import pytest

from src.agente import capacidades
from src.agente.politica import Denegado, Politica, nombres_del_archivo

en_windows = pytest.mark.skipif(sys.platform != "win32", reason="son trucos de Windows")


def _enlace_duro(destino, nombre) -> bool:
    r = subprocess.run(["cmd", "/c", "mklink", "/H", str(nombre), str(destino)],
                       capture_output=True, text=True)
    return r.returncode == 0 and nombre.exists()


def _corto(ruta) -> str | None:
    import ctypes

    buf = ctypes.create_unicode_buffer(600)
    return buf.value if ctypes.windll.kernel32.GetShortPathNameW(str(ruta), buf, 600) else None


@pytest.fixture
def pc(tmp_path):
    permitida = tmp_path / "Permitida"
    fuera = tmp_path / "Fuera"
    permitida.mkdir()
    fuera.mkdir()
    (permitida / "notas.txt").write_text("contenido normal", encoding="utf-8")
    (permitida / ".env").write_text("CLAVE=secreta-de-verdad", encoding="utf-8")
    (permitida / "Local State").write_text('{"encrypted_key": "secreto"}', encoding="utf-8")
    (fuera / "privado.txt").write_text("NO DEBE SALIR", encoding="utf-8")
    politica = Politica()
    politica.anadir(str(permitida))
    politica.guardar()
    return {"permitida": permitida, "fuera": fuera, "politica": politica}


class TestEnlacesDuros:
    """Un enlace duro no es un enlace que se siga: es **otro nombre del mismo archivo**,
    así que `resolve()` no lo deshace."""

    @en_windows
    def test_un_nombre_inocente_del_env_no_se_lee(self, pc):
        if not _enlace_duro(pc["permitida"] / ".env", pc["permitida"] / "inocente.txt"):
            pytest.skip("este volumen no admite enlaces duros")
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(str(pc["permitida"] / "inocente.txt"), archivo=True)
        assert fallo.value.motivo == "sensible"
        leido = capacidades.read_file(str(pc["permitida"] / "inocente.txt"))
        assert not leido["success"] and "secreta-de-verdad" not in str(leido)

    @en_windows
    def test_un_nombre_dentro_de_algo_de_fuera_tampoco(self, pc):
        """El enlace duro salta la frontera de carpetas: el archivo vive en las dos."""
        if not _enlace_duro(pc["fuera"] / "privado.txt", pc["permitida"] / "copia.txt"):
            pytest.skip("este volumen no admite enlaces duros")
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(str(pc["permitida"] / "copia.txt"), archivo=True)
        assert fallo.value.motivo == "enlace_duro"

    @en_windows
    def test_dos_nombres_los_dos_permitidos_si_se_leen(self, pc):
        """No se prohíben los enlaces duros: se prohíbe que uno de sus nombres sea
        sensible o esté fuera. Dos nombres normales dentro de lo permitido se leen."""
        if not _enlace_duro(pc["permitida"] / "notas.txt", pc["permitida"] / "otras-notas.txt"):
            pytest.skip("este volumen no admite enlaces duros")
        leido = capacidades.read_file(str(pc["permitida"] / "otras-notas.txt"))
        assert leido["success"] and "contenido normal" in leido["data"]["content"]

    def test_un_archivo_normal_tiene_un_solo_nombre(self, pc):
        assert nombres_del_archivo(pc["permitida"] / "notas.txt") == [pc["permitida"] / "notas.txt"]

    @en_windows
    def test_los_nombres_se_enumeran_con_su_unidad(self, pc):
        if not _enlace_duro(pc["permitida"] / "notas.txt", pc["permitida"] / "otras-notas.txt"):
            pytest.skip("este volumen no admite enlaces duros")
        nombres = {p.name for p in nombres_del_archivo(pc["permitida"] / "notas.txt")}
        assert nombres == {"notas.txt", "otras-notas.txt"}
        assert all(p.is_absolute() and p.exists() for p in nombres_del_archivo(pc["permitida"] / "notas.txt"))


class TestFlujosAlternativos:
    def test_no_se_leen(self, pc):
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(str(pc["permitida"] / "notas.txt") + ":oculto", archivo=True)
        assert fallo.value.motivo == "flujo"

    def test_ni_el_flujo_por_defecto(self, pc):
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(str(pc["permitida"] / "notas.txt") + "::$DATA", archivo=True)
        assert fallo.value.motivo == "flujo"

    def test_la_unidad_sigue_valiendo(self, pc):
        assert pc["politica"].resolver(str(pc["permitida"] / "notas.txt"), archivo=True).exists()


class TestLoQueYaEstabaCubierto:
    """Fijado por la sonda de la 3.1-D: estos doce casos se comprueban juntos."""

    @en_windows
    @pytest.mark.parametrize("sufijo", [".", " ", "::$DATA"])
    def test_el_env_disfrazado_no_se_lee(self, pc, sufijo):
        with pytest.raises(Denegado):
            pc["politica"].resolver(str(pc["permitida"] / ".env") + sufijo, archivo=True)

    def test_en_mayusculas_tampoco(self, pc):
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(str(pc["permitida"] / ".ENV"), archivo=True)
        assert fallo.value.motivo == "sensible"

    @en_windows
    def test_el_nombre_corto_83_no_esconde_un_sensible(self, pc):
        """`Local State` en 8.3 es `LOCALS~1`, que no casa con la lista de nombres. Lo
        salva resolver la ruta: el objeto real se llama «Local State»."""
        corto = _corto(pc["permitida"] / "Local State")
        if not corto or "~" not in corto:
            pytest.skip("este volumen no genera nombres 8.3")
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(corto, archivo=True)
        assert fallo.value.motivo in ("sensible", "fuera")

    @en_windows
    @pytest.mark.parametrize("ruta", ["\\\\?\\C:\\Windows\\win.ini", "\\\\.\\PhysicalDrive0",
                                      "\\\\servidor\\compartida\\x.txt"])
    def test_dispositivos_y_red(self, pc, ruta):
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(ruta, archivo=True)
        assert fallo.value.motivo == "ruta_de_red"

    @en_windows
    def test_un_nombre_de_dispositivo_dentro_de_lo_permitido(self, pc):
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(str(pc["permitida"] / "CON"), archivo=True)
        assert fallo.value.motivo == "no_existe"

    def test_subir_con_dos_puntos(self, pc):
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(str(pc["permitida"] / ".." / "Fuera" / "privado.txt"), archivo=True)
        assert fallo.value.motivo == "fuera"

    def test_un_byte_cero_en_la_ruta(self, pc):
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(str(pc["permitida"] / "notas.txt") + "\x00.png", archivo=True)
        assert fallo.value.motivo == "ruta_invalida"


@en_windows
def test_enumerar_nombres_no_se_cae_con_algo_raro(tmp_path):
    """Si Windows no puede enumerar (permisos, un volumen que no lo admite), se sigue con
    la ruta tal cual y deciden las demás comprobaciones."""
    assert nombres_del_archivo(tmp_path / "no-existe.txt") == [tmp_path / "no-existe.txt"]
    assert nombres_del_archivo(tmp_path) == [tmp_path]
    os.environ.setdefault("TEMP", str(tmp_path))


class TestLoQueEncontroElAtaqueDeLa315:
    """La 3.1.5 ataca a propósito lo que construyó la 3.1. De 17 intentos, ninguno cruzó
    la frontera de carpetas; sí salieron dos cosas que llegaban al modelo mal."""

    @en_windows
    def test_un_binario_que_empieza_con_texto_no_se_lee(self, pc):
        """Antes se miraban solo los primeros 8 KB: un archivo con texto al principio y
        un byte nulo en el 20.000 entraba y devolvía basura."""
        raro = pc["permitida"] / "parece-texto.dat"
        raro.write_bytes(b"a" * 20000 + b"\x00" + b"b" * 1000)
        r = capacidades.read_file(str(raro))
        assert not r["success"] and r["motivo"] == "binario"

    def test_un_texto_de_verdad_se_sigue_leyendo(self, pc):
        r = capacidades.read_file(str(pc["permitida"] / "notas.txt"))
        assert r["success"] and "contenido normal" in r["data"]["content"]

    @pytest.mark.parametrize("marca", ["\u202e", "\u202d", "\u2066", "\u200f"])
    def test_un_nombre_no_puede_cambiar_como_se_lee(self, marca):
        """`foto\u202egpj.exe` se enseña como «fotoexe.jpg»: es el truco clásico para
        disfrazar un ejecutable de imagen."""
        from src.agente.salida import limpiar_nombre

        limpio = limpiar_nombre(f"foto{marca}gpj.exe")
        assert marca not in limpio
        assert limpio == "foto gpj.exe"

    @en_windows
    def test_una_ruta_con_barras_mezcladas_no_se_sale(self, pc):
        truco = str(pc["permitida"]).replace("\\", "/") + "/../Fuera/privado.txt"
        with pytest.raises(Denegado) as fallo:
            pc["politica"].resolver(truco, archivo=True)
        assert fallo.value.motivo == "fuera"

    @en_windows
    def test_ni_con_puntos_codificados(self, pc):
        with pytest.raises(Denegado):
            pc["politica"].resolver(str(pc["permitida"]) + "\%2e%2e\Fuera\privado.txt", archivo=True)

    def test_un_archivo_enorme_solo_sale_su_principio(self, pc):
        grande = pc["permitida"] / "enorme.txt"
        grande.write_text("linea de relleno\n" * 200_000, encoding="utf-8")   # ~3,4 MB
        r = capacidades.read_file(str(grande))
        assert r["success"] and r["data"]["is_truncated"]
        assert len(r["data"]["content"]) < 300_000

    def test_una_sola_linea_gigante_tambien(self, pc):
        """Sin cortar por bytes, una línea de 5 MB sería una respuesta de 5 MB."""
        from src.agente.politica import MAX_BYTES

        una = pc["permitida"] / "una-linea.txt"
        una.write_text("x" * 3_000_000, encoding="utf-8")
        r = capacidades.read_file(str(una))
        assert r["success"] and len(r["data"]["content"]) <= MAX_BYTES


class TestArgumentosAbusivos:
    """3.1.5: la nube puede estar comprometida (§3 del plan). Sus argumentos pasan por el
    esquema en la nube, pero el agente **no se fía**: aquí se le mandan los que nunca
    mandaría un Morgan sano."""

    def _pc(self, tmp_path):
        permitida = tmp_path / "Permitida"
        permitida.mkdir()
        (permitida / "notas.txt").write_text("hola", encoding="utf-8")
        politica = Politica()
        politica.anadir(str(permitida))
        politica.guardar()
        return permitida

    @pytest.mark.parametrize("valor", [None, 123, ["lista"], {"a": 1}, True])
    def test_una_ruta_que_no_es_texto(self, tmp_path, valor):
        self._pc(tmp_path)
        for capacidad in (capacidades.read_file, capacidades.copy_file):
            r = capacidad(valor)
            assert not r["success"], (capacidad.__name__, valor)
        # `list_files` sin ruta es lo normal: enseña las carpetas permitidas.
        listado = capacidades.list_files(valor)
        assert listado["success"] == (valor is None), valor

    def test_una_ruta_de_un_mega(self, tmp_path):
        self._pc(tmp_path)
        r = capacidades.read_file("C:\\" + "a" * 1_000_000)
        assert not r["success"]

    @pytest.mark.parametrize("max_lines, offset", [(-5, 1), (0, 0), (10**9, 10**9), (None, None)])
    def test_numeros_absurdos_al_leer(self, tmp_path, max_lines, offset):
        permitida = self._pc(tmp_path)
        r = capacidades.read_file(str(permitida / "notas.txt"), max_lines=max_lines, offset=offset)
        assert r["success"], (max_lines, offset)
        assert len(r["data"]["content"]) <= 5000 * 200

    def test_una_busqueda_de_diez_mil_caracteres(self, tmp_path):
        permitida = self._pc(tmp_path)
        r = capacidades.search_files("x" * 10_000, path=str(permitida))
        assert r["success"] and r["data"]["count"] == 0

    def test_un_tope_de_resultados_absurdo(self, tmp_path):
        permitida = self._pc(tmp_path)
        r = capacidades.search_files("*", path=str(permitida), max_results=10**9)
        assert r["success"] and r["data"]["count"] <= capacidades.MAX_RESULTADOS

    def test_una_profundidad_absurda_no_cuelga(self, tmp_path):
        """Una carpeta muy honda: se corta por profundidad, no se recorre entera."""
        permitida = self._pc(tmp_path)
        honda = permitida
        for i in range(40):
            honda = honda / f"n{i}"
        honda.mkdir(parents=True)
        (honda / "fondo.txt").write_text("x", encoding="utf-8")
        r = capacidades.search_files("fondo", path=str(permitida))
        assert r["success"] and r["data"]["count"] == 0     # más honda que MAX_PROFUNDIDAD
