"""
La suite no toca servicios reales (V2.0.25, auditoría de la 2.3).

**Lo que se midió.** El `conftest` vaciaba el token de la API y redirigía base y
registros, pero no las demás credenciales, y `load_dotenv` las leía del `.env`
del desarrollador. Resultado, medido con un espía:

- Con `MORGAN_ENVIRONMENT=cloud` —nueve ficheros de pruebas lo ponen—, el
  contenedor montaba los repositorios de **Supabase de producción** y hacía
  peticiones al arrancar (`GET /memories`, `GET /morgan_users`). No quedó ningún
  dato de prueba en producción (comprobado: tres cuentas, las tres reales), pero
  la puerta estaba abierta.
- **Tres pruebas llamaban a los modelos reales** en cada ejecución de la suite,
  mandando mensajes a `/chat` con la cadena de verdad: gastaban cupo de Groq y, con
  Groq agotado, habrían llegado a OpenAI, que cobra.
- Dos pruebas se **saltaban solas** cuando faltaban las claves.

Estas pruebas fijan el arreglo y, sobre todo, que no se deshaga en silencio: si el
código empieza a leer un secreto nuevo, tiene que entrar en la lista.
"""

import importlib.util
import re
from pathlib import Path

from src.config import get_settings, reset_settings

RAIZ = Path(__file__).resolve().parent.parent


def _conftest():
    especificacion = importlib.util.spec_from_file_location("_conftest_morgan", RAIZ / "tests" / "conftest.py")
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


SECRETOS_EXTERNOS = _conftest().SECRETOS_EXTERNOS

#: Variables que parecen secretas y NO abren un servicio externo, con el motivo.
NO_EXTERNAS = {
    "MORGAN_API_TOKEN": "el token de la propia API; el conftest lo vacía aparte",
    "MORGAN_SECRET_KEY": "cifra los tokens en la base local; no sale a ningún sitio",
    "MORGAN_WEB_URL": "una dirección pública, no una credencial",
    "MORGAN_API_URL": "ídem: dónde se ve la API desde fuera",
    "NVIDIA_BASE_URL": "un punto de acceso sin clave no sirve de nada",
    "OPENAI_BASE_URL": "ídem",
    "MORGAN_OPENAI_TOPE_TOKENS": "un número, no una credencial",
    "MORGAN_TOKENS_API": "un interruptor sí/no de los tokens personales, no una credencial",
}

PARECE_SECRETO = re.compile(
    r'"((?:MORGAN_|GROQ_|GEMINI_|OPENAI_|NVIDIA_|SUPABASE_|GITHUB_|GOOGLE_)'
    r'[A-Z_0-9]*(?:KEY|SECRET|PASSWORD|CLIENT_ID|TOKEN|_URL|EMAIL_API|SMTP_HOST)[A-Z_0-9]*)"'
)


class TestDentroDeUnaPruebaNoHayCredencialesReales:
    def test_ni_supabase_ni_modelos(self):
        s = get_settings()
        assert not s.has_supabase
        assert not s.groq_api_keys
        assert not s.openai_api_key
        assert not s.gemini_api_key
        assert not s.nvidia_api_key

    def test_en_modo_nube_el_contenedor_no_monta_supabase(self, monkeypatch):
        """El caso medido: nueve ficheros ponen este entorno."""
        from src.api import dependencies

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        dependencies.reset_container()
        contenedor = dependencies.get_container()

        assert "Supabase" not in type(contenedor.repositories).__name__
        assert contenedor.remote_repositories is None
        assert contenedor.agent is None, "sin claves no hay cadena real de modelos"

    def test_las_minusculas_del_env_tampoco_pasan(self, monkeypatch):
        """Las claves de Supabase se pegan del panel en minúsculas."""
        import os

        assert not os.environ.get("supabase_url")
        assert not os.environ.get("supabase_secret_key")


class TestLaListaCubreTodoLoQueLeeElCodigo:
    def test_todo_secreto_del_codigo_esta_en_la_lista_o_justificado(self):
        leidos: set[str] = set()
        for fichero in (RAIZ / "src").rglob("*.py"):
            leidos |= set(PARECE_SECRETO.findall(fichero.read_text(encoding="utf-8")))

        sin_cubrir = sorted(leidos - set(SECRETOS_EXTERNOS) - set(NO_EXTERNAS))

        assert not sin_cubrir, (
            "El código lee estas variables con pinta de credencial y la suite no las "
            f"vacía: {sin_cubrir}. Añádelas a SECRETOS_EXTERNOS en tests/conftest.py, "
            "o a NO_EXTERNAS aquí con el motivo."
        )

    def test_la_comprobacion_encuentra_bastantes_como_para_significar_algo(self):
        leidos: set[str] = set()
        for fichero in (RAIZ / "src").rglob("*.py"):
            leidos |= set(PARECE_SECRETO.findall(fichero.read_text(encoding="utf-8")))
        assert len(leidos) >= 15, f"solo encuentra {len(leidos)}: el patrón dejó de funcionar"
