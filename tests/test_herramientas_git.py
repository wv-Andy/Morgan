"""
Las tres herramientas de git local, ejecutadas de verdad.

**El hueco que cierran.** `src/tools/git.py` estaba al 64%: lo que no se
ejecutaba era justo el cuerpo de las tres, o sea todo lo que hacen. Se
comprobaba que existieran y qué esquema declaran, no qué pasa al usarlas.

Y ahí había un defecto. `git_diff` metía la «ruta» que compone el modelo en la
línea de órdenes **sin el separador `--`**, así que git la interpretaba como
opción:

    file_path='--output=/donde/sea'  ->  git diff --output=/donde/sea

`git diff --output` **escribe un fichero**. Una herramienta declarada `safe`
—que se ejecuta sin pedir confirmación— podía escribir en cualquier sitio donde
el proceso tuviera permiso, y respondía «no hay cambios sin confirmar»: nada
parecía raro.

Es la misma familia que la fuga de las rutas de GitHub, y en el mismo día: un
argumento que compone el modelo dejando de ser dato y pasando a ser instrucción.

**Se prueban contra repositorios de verdad**, creados en un directorio temporal.
Simular `subprocess` comprobaría que se llama a git con ciertos argumentos, no
que git haga lo que se espera — y lo que falló aquí fue precisamente lo segundo.
"""

import subprocess
from pathlib import Path

import pytest

from src.tools.git import GitCommitTool, GitDiffTool, GitStatusTool


def hay_git() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not hay_git(), reason="git no está en el PATH")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Un repositorio recién hecho, con un commit y un cambio sin confirmar."""
    def git(*args):
        resultado = subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True
        )
        assert resultado.returncode == 0, f"git {' '.join(args)}: {resultado.stderr}"

    git("init", "-q")
    git("config", "user.email", "pruebas@ejemplo.co")
    git("config", "user.name", "Pruebas")
    git("config", "commit.gpgsign", "false")

    (tmp_path / "hola.txt").write_text("primera línea\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "el primero")

    (tmp_path / "hola.txt").write_text("primera línea\nsegunda\n", encoding="utf-8")
    return tmp_path


class TestLaRutaNoPuedeSerUnaOpcion:
    """El defecto que dio origen a este fichero."""

    def test_no_escribe_ficheros(self, repo, tmp_path):
        destino = tmp_path / "no-deberia-existir.txt"

        resultado = GitDiffTool().execute(
            file_path=f"--output={destino}", cwd=str(repo)
        )

        assert not destino.exists(), (
            "`git_diff` ha escrito un fichero. Es una herramienta de solo "
            "lectura declarada `safe`, así que se ejecuta sin confirmación: "
            "falta el `--` que separa opciones de rutas"
        )
        assert resultado["success"] is True

    @pytest.mark.parametrize("colado", ["--stat", "--name-only", "-p", "--raw"])
    def test_ninguna_opcion_cambia_lo_que_devuelve(self, repo, colado):
        """Si la «ruta» actuara como opción, la salida sería otra. Con el `--`
        es un nombre de fichero que no existe, y git no devuelve nada.
        """
        resultado = GitDiffTool().execute(file_path=colado, cwd=str(repo))

        assert resultado["success"] is True
        assert resultado["data"]["has_changes"] is False, (
            f"'{colado}' se ha interpretado como opción: la herramienta ha "
            "hecho algo distinto de mirar un fichero"
        )

    def test_una_ruta_de_verdad_sigue_funcionando(self, repo):
        """El arreglo no puede romper el uso normal, que es lo que hace."""
        resultado = GitDiffTool().execute(file_path="hola.txt", cwd=str(repo))

        assert resultado["success"] is True
        assert resultado["data"]["has_changes"] is True
        assert "segunda" in resultado["data"]["diff"]


class TestGitStatus:
    def test_ve_los_cambios_sin_confirmar(self, repo):
        resultado = GitStatusTool().execute(cwd=str(repo))

        assert resultado["success"] is True
        assert "hola.txt" in resultado["data"]["status_output"]

    def test_un_arbol_limpio_se_dice_con_palabras(self, repo):
        """`--branch` imprime siempre una cabecera («## master»), así que la
        salida nunca está vacía y el mensaje de «árbol limpio» que ya existía
        era **código muerto**: sin nada que confirmar, el modelo recibía
        «## master» a secas y tenía que deducirlo.
        """
        subprocess.run(["git", "checkout", "--", "."], cwd=repo, capture_output=True)

        resultado = GitStatusTool().execute(cwd=str(repo))

        assert resultado["success"] is True
        assert resultado["data"]["hay_cambios"] is False
        assert "limpio" in resultado["data"]["status_output"].lower()

    def test_y_con_cambios_no_se_dice_que_esta_limpio(self, repo):
        resultado = GitStatusTool().execute(cwd=str(repo))

        assert resultado["data"]["hay_cambios"] is True
        assert "limpio" not in resultado["data"]["status_output"].lower()

    def test_fuera_de_un_repositorio_lo_dice(self, tmp_path):
        fuera = tmp_path / "sin-repo"
        fuera.mkdir()

        resultado = GitStatusTool().execute(cwd=str(fuera))

        assert resultado["success"] is False
        assert resultado["error"]


class TestGitDiff:
    def test_sin_cambios_lo_dice_en_lugar_de_devolver_nada(self, repo):
        subprocess.run(["git", "checkout", "--", "."], cwd=repo, capture_output=True)

        resultado = GitDiffTool().execute(cwd=str(repo))

        assert resultado["success"] is True
        assert resultado["data"]["has_changes"] is False
        assert "No hay cambios" in resultado["data"]["diff"]

    def test_un_diff_enorme_se_recorta_y_se_avisa(self, repo):
        """Sin recortar, un diff grande llena el contexto del modelo con algo
        que no va a poder leer entero. Que se avise es lo que le permite pedir
        el resto por partes en lugar de dar por completo lo que no lo está.
        """
        # Tiene que estar ya seguido por git: `git diff` no mira los ficheros
        # nuevos sin añadir, y con uno nuevo la prueba pasaría por otra razón.
        (repo / "grande.txt").write_text("linea\n", encoding="utf-8")
        subprocess.run(["git", "add", "grande.txt"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "el grande"],
                       cwd=repo, capture_output=True)
        (repo / "grande.txt").write_text(
            "\n".join(f"linea numero {n} " + "x" * 60 for n in range(400)),
            encoding="utf-8",
        )

        resultado = GitDiffTool().execute(cwd=str(repo))

        assert resultado["success"] is True
        assert resultado["data"]["is_truncated"] is True
        assert len(resultado["data"]["diff"]) <= 5000


class TestGitCommit:
    def test_confirma_los_cambios(self, repo):
        resultado = GitCommitTool().execute(message="segundo commit", cwd=str(repo))

        assert resultado["success"] is True
        assert resultado["data"]["committed"] is True

        registro = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True
        )
        assert "segundo commit" in registro.stdout

    def test_sin_cambios_no_es_un_error(self, repo):
        """Que no hubiera nada que confirmar no es un fallo, y tratarlo como tal
        hacía que el modelo reintentara o avisara de algo que estaba bien.
        """
        GitCommitTool().execute(message="primero", cwd=str(repo))

        resultado = GitCommitTool().execute(message="otra vez", cwd=str(repo))

        assert resultado["success"] is True
        assert resultado["data"]["committed"] is False

    def test_un_mensaje_que_empieza_por_guion_es_un_mensaje(self, repo):
        """Va después de `-m`, así que git lo consume como su valor. Se fija
        porque es el mismo riesgo que tenía `git diff` con la ruta.
        """
        resultado = GitCommitTool().execute(message="--amend no soy", cwd=str(repo))

        assert resultado["success"] is True

        registro = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True
        )
        assert "--amend no soy" in registro.stdout
        assert len(registro.stdout.strip().splitlines()) == 2, (
            "Se ha reescrito el commit anterior en lugar de crear uno nuevo: "
            "el mensaje ha actuado como opción"
        )

    def test_fuera_de_un_repositorio_lo_dice(self, tmp_path):
        fuera = tmp_path / "sin-repo-2"
        fuera.mkdir()

        resultado = GitCommitTool().execute(message="nada", cwd=str(fuera))

        assert resultado["success"] is False
        assert resultado["error"]
