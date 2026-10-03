"""
Configuración compartida de las pruebas de Morgan.

Antes no existía aislamiento: `test_api.py` escribía en `data/morgan_memory.db`
y en `logs/audit.log` reales a través del contenedor global, de modo que las
pruebas tenían efectos secundarios sobre los datos locales del desarrollador y su
resultado dependía del entorno.

Aquí se redirigen la base de datos, el log de auditoría y la configuración a
directorios temporales, y se garantiza que los permisos se evalúan siempre en modo
no interactivo (una prueba jamás debe quedarse esperando una confirmación).
"""

import os

import pytest

from src.config import MAXIMO_CLAVES_POR_PROVEEDOR, reset_settings

#: Todo lo que abre una puerta a un servicio real: bases de datos, modelos (uno de
#: pago), correo y OAuth. Ver `_isolated_environment`.
SECRETOS_EXTERNOS = (
    "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SECRET_KEY",
    *(f"GROQ_API_KEY_{n}" for n in range(2, MAXIMO_CLAVES_POR_PROVEEDOR + 1)),
    "GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY",
    "MORGAN_EMAIL_API", "MORGAN_EMAIL_API_KEY", "MORGAN_SMTP_HOST", "MORGAN_SMTP_PASSWORD",
    "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    "MORGAN_RELOJ_SECRETO",
)


@pytest.fixture
def _test_workspace(tmp_path):
    """Directorio temporal propio de CADA prueba.

    Antes era de ámbito de sesión y todas las pruebas compartían la misma base:
    dos que usaran el mismo identificador de sesión se contaminaban entre sí.
    """
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_environment(_test_workspace, monkeypatch):
    """Aísla configuración, base de datos y logs de cada prueba.

    Sin esto, las pruebas de la API escribían en data/morgan_memory.db y en
    logs/audit.log reales a través del contenedor global.
    """
    monkeypatch.setenv("MODERATE_PERMISSION_MODE", "ask")
    monkeypatch.setenv("MORGAN_LOG_LEVEL", "WARNING")
    # El .env del desarrollador no debe cambiar el resultado de las pruebas. Si
    # hay un token real definido (por ejemplo para el acceso remoto), la API lo
    # exigiría y toda la suite respondería 401. Las pruebas de autenticación lo
    # definen ellas mismas cuando lo necesitan.
    # Se define vacío en lugar de borrarlo: load_dotenv() volvería a leerlo del
    # .env, porque solo respeta las variables que YA existen en el entorno.
    monkeypatch.setenv("MORGAN_API_TOKEN", "")
    # **Ningún secreto externo del .env llega a una prueba** (V2.0.25).
    #
    # Medido en la auditoría de la 2.3: con `MORGAN_ENVIRONMENT=cloud`, el
    # contenedor de una prueba montaba los repositorios de Supabase con las
    # credenciales REALES y hacía peticiones a producción al arrancar
    # (`GET /memories`, `GET /morgan_users`). Nueve ficheros de pruebas ponen ese
    # entorno. Por el mismo camino, las claves de modelos —OpenAI incluida, que es
    # de pago— y las de correo llegaban a la suite.
    #
    # Se vacían en lugar de borrarse por lo mismo que el token: load_dotenv()
    # volvería a leerlas. Las minúsculas también, porque las de Supabase se pegan
    # así del panel y en Linux son otra variable. La prueba que necesite una, la
    # pone ella. `tests/test_suite_aislada.py` lo vigila.
    for secreto in SECRETOS_EXTERNOS:
        monkeypatch.setenv(secreto, "")
        monkeypatch.setenv(secreto.lower(), "")
    monkeypatch.setenv("MORGAN_CLOUD_ENABLED", "false")
    # El reloj de las automatizaciones (4.14) no se pone a ejecutar nada solo en una prueba.
    monkeypatch.setenv("MORGAN_RELOJ_INTERNO", "false")
    monkeypatch.setenv("MORGAN_DATA_DIR", str(_test_workspace / "data"))
    monkeypatch.setenv("MORGAN_LOG_DIR", str(_test_workspace / "logs"))
    # El agente local (3.0) guarda su credencial en %LOCALAPPDATA%/Morgan/agente.
    # Una prueba que emparejara sin esto escribiría en la carpeta real del PC de
    # quien corre la suite, y podría pisar su emparejamiento de verdad.
    monkeypatch.setenv("MORGAN_AGENTE_DIR", str(_test_workspace / "agente"))
    # Y el arranque automático (3.0.5): sin esto, una prueba de `arranque activar` dejaría
    # un acceso directo en la carpeta de Inicio real, y el agente se abriría solo.
    monkeypatch.setenv("MORGAN_CARPETA_INICIO", str(_test_workspace / "inicio"))
    # Ni el menú Inicio de quien pasa las pruebas, ni ventanas de verdad (4.17).
    monkeypatch.setenv("MORGAN_CARPETA_MENU", str(_test_workspace / "menu"))
    monkeypatch.setenv("MORGAN_SIN_VENTANAS", "1")
    # Y la clave con la que firmo el agente (3.8): una prueba de `claves` no puede
    # tocar la de verdad, que está en su carpeta de usuario.
    monkeypatch.setenv("MORGAN_FIRMA_DIR", str(_test_workspace / "firma"))
    reset_settings()

    # El contenedor de la API es un singleton: si ya se construyó con las rutas
    # reales, hay que descartarlo para que se rehaga con las temporales.
    from src.api import dependencies

    dependencies.reset_container()
    yield
    dependencies.reset_container()
    reset_settings()


@pytest.fixture
def temp_db(tmp_path):
    """Base de datos SQLite temporal y vacía."""
    from src.memory.db import Database

    return Database(tmp_path / "test_memory.db")


@pytest.fixture
def temp_audit(tmp_path):
    """Registro de auditoría temporal."""
    from src.security.audit import AuditLogger

    return AuditLogger(log_path=tmp_path / "audit.log")


@pytest.fixture
def non_interactive_permissions(temp_audit):
    """Gestor de permisos sin consola: nunca bloquea esperando confirmación."""
    from src.security.permissions import PermissionManager

    return PermissionManager(audit_logger=temp_audit, interactive=False)


@pytest.fixture
def isolated_container(tmp_path, monkeypatch):
    """Contenedor de la API con base de datos y auditoría temporales.

    Restaura el contenedor global al terminar para no filtrar estado entre pruebas.
    """
    from src.api import dependencies
    from src.memory.manager import MemoryManager
    from src.memory.memoria_sobre_repositorio import sobre_sqlite
    from src.security.audit import AuditLogger

    dependencies.reset_container()
    container = dependencies.get_container()

    container.memory_manager = MemoryManager(
        storage=sobre_sqlite(tmp_path / "api_memory.db")
    )
    container.audit_logger = AuditLogger(log_path=tmp_path / "api_audit.log")
    container.permission_manager.audit = container.audit_logger

    yield container

    dependencies.reset_container()


@pytest.fixture
def modelo_simulado(monkeypatch):
    """El contenedor monta el agente de verdad, pero con un modelo simulado.

    **Por qué existe** (V2.0.25). Sin claves en la suite, el contenedor arranca en
    modo degradado y no hay agente. Antes de aislar los secretos, tres pruebas de
    `test_backlog_multimodal.py` mandaban mensajes a `/chat` con la cadena REAL: cada
    ejecución de la suite llamaba a Groq, y con Groq agotado habría llegado a
    OpenAI, que cobra. Y dos pruebas se saltaban solas cuando faltaban las claves.

    Devuelve el `MockLLMProvider` para programarle respuestas.
    """
    from src.api import dependencies
    from src.models.mock import MockLLMProvider

    modelo = MockLLMProvider()
    monkeypatch.setattr(dependencies, "build_provider_chain", lambda settings: (modelo, ["simulado"]))
    dependencies.reset_container()
    return modelo


# --- Lo que necesita un escritorio de verdad (4.19) -------------------------------------------
#
# Las pruebas de la interfaz, la captura real y la ventana de Tk necesitan ventanas que se
# vean. En la integración continua (GitHub Actions) no hay nadie delante: con
# MORGAN_SIN_ESCRITORIO=1 se saltan, diciéndolo. En mi PC corren siempre.

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "escritorio: necesita un escritorio de verdad; se salta con MORGAN_SIN_ESCRITORIO=1")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("MORGAN_SIN_ESCRITORIO") != "1":
        return
    salto = pytest.mark.skip(reason="sin escritorio de verdad (MORGAN_SIN_ESCRITORIO=1)")
    for item in items:
        if "escritorio" in item.keywords:
            item.add_marker(salto)

