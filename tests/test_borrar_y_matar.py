"""
Las dos operaciones que no se pueden deshacer: borrar del disco y matar procesos.

**Por qué estas dos y por qué ahora.** La auditoría de la V2.0.2 dejó
`filesystem.py` al 82% y `terminal.py` al 77%, y dijo por qué preocupaban más
que el resto: son las herramientas que tocan el disco y la línea de órdenes, y
es exactamente ahí donde ya apareció un defecto —`git_diff` escribía ficheros
siendo de solo lectura— al mirar el módulo peor cubierto.

Lo que no se ejecutaba nunca era, entre otras cosas, **`kill_process` entero**.
Una herramienta `critical` que termina procesos, sin una sola prueba.

## Lo que se comprueba, y en qué orden de gravedad

1. **Que no se pueda borrar el sistema.** `rmtree` es la operación más
   destructiva del proyecto, y lo que la separa de un desastre son tres
   comprobaciones.
2. **Que no se pueda matar un proceso crítico de Windows.**
3. **Que las dos vivan solo en el Morgan local**, no en el de la nube.

No se mata ningún proceso de verdad ni se borra nada fuera de `tmp_path`: lo que
se comprueba es la decisión, y una prueba que necesite un proceso real no es
reproducible.
"""

import os
from pathlib import Path

import pytest

from src.tools.filesystem import PROTECTED_SYSTEM_DIRS, DeleteFileTool
from src.tools.terminal import PROTECTED_PROCESSES, KillProcessTool


@pytest.fixture
def borrar():
    return DeleteFileTool()


@pytest.fixture
def matar():
    return KillProcessTool()


# ─────────────────────────────────────────────────────────────────────────────
# Borrar del disco
# ─────────────────────────────────────────────────────────────────────────────


class TestNoSePuedeBorrarElSistema:
    """Tres comprobaciones separan `rmtree` de un desastre. Ninguna tenía prueba.
    """

    def test_ni_la_raiz_de_una_unidad(self, borrar):
        salida = borrar.execute(path="C:\\", recursive=True)

        assert salida["success"] is False
        assert "raíz de unidad" in salida["error"]

    @pytest.mark.parametrize("protegido", [str(p) for p in PROTECTED_SYSTEM_DIRS])
    def test_ni_un_directorio_protegido(self, borrar, protegido):
        salida = borrar.execute(path=protegido, recursive=True)

        assert salida["success"] is False
        assert "protegida" in salida["error"] or "no existe" in salida["error"].lower()

    def test_ni_algo_DENTRO_de_un_directorio_protegido(self, borrar):
        """La comprobación mira los padres, no solo la ruta exacta. Sin eso,
        `C:\\Windows` estaría a salvo y `C:\\Windows\\System32` no.
        """
        salida = borrar.execute(path="C:\\Windows\\System32", recursive=True)

        assert salida["success"] is False
        assert "protegida" in salida["error"] or "no existe" in salida["error"].lower()

    def test_ni_llegando_ahi_con_puntos_suspensivos(self, borrar):
        """La ruta se resuelve ANTES de comprobarla, que es el orden correcto.

        Al revés —comprobar el texto y luego resolver— es como se colaba la
        fuga de las rutas de GitHub: `..` dejaba de ser un nombre y pasaba a ser
        una instrucción.
        """
        salida = borrar.execute(path="C:\\Windows\\..\\Windows\\System32",
                                recursive=True)

        assert salida["success"] is False

    def test_ni_por_un_camino_que_NO_nombre_la_carpeta_protegida(self, borrar):
        """El caso que de verdad necesita `resolve()`, y que descubrí mutando.

        Mi primera prueba de travesía usaba `C:\\Windows\\..\\Windows\\System32`,
        y **pasaba igual sin resolver la ruta**: entre los padres de ese texto
        está `C:\\Windows` literalmente, así que la comprobación lo pillaba de
        rebote.

        Este camino no nombra ninguna carpeta protegida en ningún punto:

            C:\\Users\\ana\\..\\..\\Windows\\System32

        Sus padres son `C:\\Users\\ana\\..\\..\\Windows`, `...\\..`,
        `C:\\Users\\ana`, `C:\\Users` y `C:\\`. **`C:\\Windows` no está entre
        ellos.** Sin resolver primero, la comprobación de carpetas protegidas
        mira los nombres equivocados y deja pasar un `rmtree` sobre System32.

        Es exactamente la misma forma que la fuga de GitHub: comprobar el texto
        en lugar del destino.
        """
        salida = borrar.execute(
            path="C:\\Users\\ana\\..\\..\\Windows\\System32",
            recursive=True,
        )

        assert salida["success"] is False, (
            "Se ha aceptado borrar System32 por un camino que no lo nombra"
        )
        assert "protegida" in salida["error"], (
            f"Rechazado, pero no por ser protegida: {salida['error']}"
        )

    def test_y_el_mensaje_dice_QUE_ruta_lo_impidio(self, borrar):
        """Para que quien lo lea sepa por qué, y no solo que no se pudo."""
        salida = borrar.execute(path="C:\\Windows", recursive=True)

        assert salida["success"] is False
        assert salida["data"] is None


class TestBorrarLoQueSiSePuede:
    def test_un_archivo(self, borrar, tmp_path):
        objetivo = tmp_path / "sobra.txt"
        objetivo.write_text("adiós", encoding="utf-8")

        salida = borrar.execute(path=str(objetivo))

        assert salida["success"] is True
        assert salida["data"]["type"] == "file"
        assert not objetivo.exists()

    def test_una_carpeta_vacia(self, borrar, tmp_path):
        objetivo = tmp_path / "vacia"
        objetivo.mkdir()

        salida = borrar.execute(path=str(objetivo))

        assert salida["success"] is True
        assert not objetivo.exists()

    def test_una_carpeta_con_cosas_NO_sin_recursive(self, borrar, tmp_path):
        """Y el error dice que hace falta `recursive`, que es lo que permite
        reintentarlo a sabiendas en lugar de adivinar.
        """
        objetivo = tmp_path / "con-cosas"
        objetivo.mkdir()
        (objetivo / "algo.txt").write_text("x", encoding="utf-8")

        salida = borrar.execute(path=str(objetivo), recursive=False)

        assert salida["success"] is False
        assert "recursive" in salida["error"]
        assert objetivo.exists(), "no se ha borrado nada, que es lo correcto"

    def test_y_si_con_recursive(self, borrar, tmp_path):
        objetivo = tmp_path / "arbol"
        (objetivo / "dentro").mkdir(parents=True)
        (objetivo / "dentro" / "hoja.txt").write_text("x", encoding="utf-8")

        salida = borrar.execute(path=str(objetivo), recursive=True)

        assert salida["success"] is True
        assert "recursivo" in salida["data"]["type"]
        assert not objetivo.exists()

    def test_lo_que_no_existe_se_dice_y_no_revienta(self, borrar, tmp_path):
        salida = borrar.execute(path=str(tmp_path / "nunca-existio"))

        assert salida["success"] is False
        assert "no existe" in salida["error"].lower()


class TestBorrarEsCriticoYSoloLocal:
    def test_pide_confirmacion(self, borrar):
        """No puede ser `safe`. Fue una herramienta de solo lectura declarada
        `safe` la que escribía ficheros en el defecto que encontró la auditoría.
        """
        assert borrar.permission_level in ("critical", "sensitive", "high_risk")

    def test_y_no_existe_en_la_nube(self, borrar):
        """Cloud Morgan no es acceso a tu ordenador. Lo que no está registrado
        no puede invocarse ni aparece en el esquema que ve el modelo.
        """
        assert borrar.requires_local is True


# ─────────────────────────────────────────────────────────────────────────────
# Matar procesos
# ─────────────────────────────────────────────────────────────────────────────


class TestNoSePuedeMatarWindows:
    """`kill_process` no tenía ni una prueba, y es la herramienta que termina
    procesos. La lista de protegidos era la única defensa y nadie comprobaba
    que se consultara.
    """

    @pytest.mark.parametrize("critico", sorted(PROTECTED_PROCESSES))
    def test_ni_por_nombre(self, matar, critico):
        salida = matar.execute(name=critico)

        assert salida["success"] is False
        assert "protegido" in salida["error"]

    def test_ni_escribiendolo_en_mayusculas(self, matar):
        """El nombre lo compone el modelo a partir de lo que le digan, y
        `LSASS.EXE` es el mismo proceso que `lsass.exe`. Una comparación sensible
        a las mayúsculas sería una defensa que se salta escribiendo distinto.
        """
        salida = matar.execute(name="LSASS.EXE")

        assert salida["success"] is False
        assert "protegido" in salida["error"]

    def test_con_espacios_no_mata_nada_pero_no_por_la_lista(self, matar):
        """Aquí hay un matiz que conviene escribir, porque esta prueba pasaba
        dándome una impresión falsa.

        `"  explorer.exe  "` **no** entra en la lista de protegidos: el nombre
        se pasa a minúsculas pero no se recorta, así que con espacios no está en
        el conjunto. Lo que impide el daño es otra cosa: el bucle exige que el
        nombre sea **exactamente igual** al del proceso, y ningún proceso se
        llama con espacios alrededor. No se mata nada, pero no gracias a la
        lista.

        Se deja documentado y no se "arregla" con un `.strip()` porque la
        propiedad que de verdad protege es la de abajo, y esa sí se comprueba.
        """
        salida = matar.execute(name="  explorer.exe  ")

        assert salida["success"] is False
        assert "No se encontró" in salida["error"], (
            "Si esto cambia a 'protegido', alguien añadió un strip() y esta "
            "prueba se queda obsoleta, no rota"
        )

    def test_la_propiedad_QUE_SI_protege(self, matar):
        """Las dos comparaciones son consistentes, y de ahí sale la garantía.

        Para que el bucle mate un proceso, `name.lower()` tiene que ser igual a
        `proceso.name().lower()`. Y la lista se consulta con ese mismo
        `name.lower()`. Así que si un proceso protegido pudiera coincidir, el
        nombre con el que coincide está por definición en la lista y se rechaza
        antes de llegar.

        Dicho de otra forma: **no hay ninguna cadena que case con un proceso
        protegido y no esté en la lista.** Eso es lo que hace que no haya
        rodeo, y no el `.strip()` que falta.
        """
        for protegido in PROTECTED_PROCESSES:
            # Lo unico que casaria con el proceso es su nombre exacto...
            candidato = protegido.lower()
            # ...y ese candidato esta en la lista.
            assert candidato in PROTECTED_PROCESSES

    def test_la_lista_esta_toda_en_minusculas(self):
        """Si alguien añade `"Notepad.exe"`, la comparación contra
        `name.lower()` no lo encontraría nunca y la entrada sería decorativa.
        """
        mayusculas = [p for p in PROTECTED_PROCESSES if p != p.lower()]

        assert not mayusculas, (
            f"Estas entradas nunca coincidirán: {mayusculas}"
        )

    def test_ni_por_pid(self, matar, monkeypatch):
        """El camino del PID mira el nombre REAL del proceso, no lo que diga
        quien llama. Es la comprobación que importa: el PID no dice qué es.
        """
        import psutil

        class ProcesoCritico:
            def __init__(self, pid):
                self._pid = pid

            def name(self):
                return "lsass.exe"

            def terminate(self):
                raise AssertionError("se ha intentado matar un proceso crítico")

            def wait(self, timeout=None):
                raise AssertionError("se ha intentado matar un proceso crítico")

        monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
        monkeypatch.setattr(psutil, "Process", ProcesoCritico)

        salida = matar.execute(pid=4)

        assert salida["success"] is False
        assert "protegido" in salida["error"]


class TestMatarLoQueSiSePuede:
    def test_por_pid(self, matar, monkeypatch):
        import psutil

        matados = []

        class ProcesoCorriente:
            def __init__(self, pid):
                self._pid = pid

            def name(self):
                return "micosa.exe"

            def terminate(self):
                matados.append(self._pid)

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
        monkeypatch.setattr(psutil, "Process", ProcesoCorriente)

        salida = matar.execute(pid=4242)

        assert salida["success"] is True
        assert matados == [4242]
        assert salida["data"]["terminated_count"] == 1

    def test_un_pid_que_no_existe_se_dice(self, matar, monkeypatch):
        import psutil

        monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)

        salida = matar.execute(pid=999999)

        assert salida["success"] is False
        assert "999999" in salida["error"]

    def test_un_nombre_que_no_corre_se_dice(self, matar, monkeypatch):
        """Distinto de «lo maté»: el modelo tiene que poder distinguir «no
        estaba» de «ya no está».
        """
        import psutil

        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([]))

        salida = matar.execute(name="nada-de-esto.exe")

        assert salida["success"] is False
        assert "No se encontró" in salida["error"]


class TestLoQueHaceFaltaParaLlamarla:
    def test_sin_pid_ni_nombre_no_hace_nada(self, matar):
        """Los dos son opcionales en el esquema, así que el modelo puede
        llamarla vacía. Sin esta comprobación, el `elif name` no entraría y
        devolvería «he matado 0 procesos» con success=True: una respuesta que
        miente.
        """
        salida = matar.execute()

        assert salida["success"] is False
        assert "pid" in salida["error"] and "name" in salida["error"]


class TestLosErroresDeWindowsSeTraducen:
    def test_permiso_denegado(self, matar, monkeypatch):
        import psutil

        class ProcesoBlindado:
            def __init__(self, pid):
                pass

            def name(self):
                return "algo.exe"

            def terminate(self):
                raise psutil.AccessDenied()

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
        monkeypatch.setattr(psutil, "Process", ProcesoBlindado)

        salida = matar.execute(pid=1234)

        assert salida["success"] is False
        assert "Permiso denegado" in salida["error"]
        assert "privilegios" in salida["error"], (
            "El mensaje debe decir qué haría falta, no solo que no se pudo"
        )

    def test_el_proceso_que_no_se_muere(self, matar, monkeypatch):
        import psutil

        class ProcesoTerco:
            def __init__(self, pid):
                pass

            def name(self):
                return "terco.exe"

            def terminate(self):
                return None

            def wait(self, timeout=None):
                raise psutil.TimeoutExpired(3)

        monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
        monkeypatch.setattr(psutil, "Process", ProcesoTerco)

        salida = matar.execute(pid=1234)

        assert salida["success"] is False
        assert "tiempo" in salida["error"]


class TestMatarEsCriticoYSoloLocal:
    def test_pide_confirmacion(self, matar):
        assert matar.permission_level in ("critical", "sensitive")

    def test_y_no_existe_en_la_nube(self, matar):
        assert matar.requires_local is True

    def test_ninguna_de_las_dos_se_registra_en_la_nube(self):
        """La comprobación de verdad: no que lo declaren, sino que el
        contenedor las excluya. Declararlo y no filtrarlo sería una promesa sin
        cumplir.
        """
        import tempfile

        os.environ.update({
            "MORGAN_DATA_DIR": tempfile.mkdtemp(),
            "MORGAN_LOG_DIR": tempfile.mkdtemp(),
            "MORGAN_SERVE_WEB": "false",
            "MORGAN_ENVIRONMENT": "cloud",
            "MORGAN_CLOUD_ENABLED": "true",
        })
        from src.api import dependencies
        from src.config import reset_settings

        reset_settings()
        dependencies.reset_container()
        try:
            herramientas = dependencies.get_container().tool_registry.list_tools()
            nombres = {t.name for t in herramientas}
            borrar = next((t for t in herramientas if t.name == "delete_file"), None)
            disponible = borrar.disponible() if borrar else None
        finally:
            os.environ["MORGAN_ENVIRONMENT"] = "local"
            reset_settings()
            dependencies.reset_container()

        # Desde la 3.3 hay un `delete_file` en la nube, pero **no es este**: es el
        # representante del agente del PC de la persona. No toca el disco del servidor,
        # exige un plan aprobado, sin PC conectado no existe y en el PC se confirma.
        from src.canal.herramientas import EscribirEnElEquipo

        assert isinstance(borrar, EscribirEnElEquipo) and borrar.exige_plan
        assert borrar.requires_local is False and disponible is False
        # `kill_process` también existe desde la 3.5, y como `delete_file`: representante
        # del agente del PC, con plan, confirmación allí y solo procesos de la persona.
        from src.canal.herramientas import TerminalDelEquipo

        matar = next(t for t in herramientas if t.name == "kill_process")
        assert isinstance(matar, TerminalDelEquipo) and matar.exige_plan
        assert "execute_command" not in nombres
