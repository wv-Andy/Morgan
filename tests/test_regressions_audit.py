"""
Pruebas de regresión de los defectos encontrados en la auditoría técnica.

Cada prueba de este módulo corresponde a un hallazgo concreto de docs/auditoria-v1.md
y falla si el defecto vuelve a introducirse.
"""

import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agent.core import Agent
from src.agent.sessions import SessionStore
from src.memory.db import MemoryStorageError
from src.memory.manager import MEMORY_SUMMARY_HEADER, MemoryManager
from src.memory.memoria_sobre_repositorio import MemoriaSobreRepositorio, sobre_sqlite
from src.models.base import ChatMessage, ToolCallRequest
from src.models.mock import MockLLMProvider
from src.tools.coding import PatchFileTool, RunTestsTool
from src.tools.filesystem import CreateFileTool, MoveFileTool
from src.tools.registry import ToolRegistry
from src.tools.web import ReadWebpageTool, check_url_is_public


def _agent(permissions, tools=None, responses=None):
    model = MockLLMProvider()
    for r in responses or []:
        model.queue_text(r)
    return Agent(
        model=model,
        tool_registry=tools or ToolRegistry(),
        permission_manager=permissions,
        sessions=SessionStore(),
    )


# --- C-1: run_tests lanzaba NameError fuera del try -------------------------


class TestRunTestsNoCrash:
    def test_run_tests_fuera_del_repositorio_devuelve_error_controlado(self, tmp_path):
        """Antes lanzaba NameError: BASE_DIR no estaba definido."""
        result = RunTestsTool().execute(test_command="pytest", cwd=str(tmp_path), timeout=30)

        assert set(result.keys()) == {"success", "data", "error"}
        assert result["success"] is False

    def test_run_tests_directorio_inexistente(self):
        result = RunTestsTool().execute(cwd="Z:/ruta/que/no/existe")

        assert result["success"] is False
        assert "inexistente" in result["error"].lower()


# --- C-2: los permisos no deben bloquear sin consola ------------------------


class TestPermisosNoInteractivos:
    def test_herramienta_moderada_se_deniega_sin_consola(self, non_interactive_permissions):
        """Sin TTY debe denegar de inmediato, nunca quedarse esperando entrada."""
        authorized = non_interactive_permissions.check_permission(
            CreateFileTool(), {"path": "salida.txt", "content": "x"}
        )
        assert authorized is False

    def test_no_bloquea_el_hilo(self, non_interactive_permissions):
        """La comprobación debe retornar; si bloquea, el hilo sigue vivo al expirar."""
        resultado = {}

        def target():
            resultado["ok"] = non_interactive_permissions.check_permission(
                CreateFileTool(), {"path": "x.txt", "content": "y"}
            )

        hilo = threading.Thread(target=target, daemon=True)
        hilo.start()
        hilo.join(timeout=5)

        assert not hilo.is_alive(), "check_permission se quedó bloqueado esperando stdin"
        assert resultado["ok"] is False

    def test_herramienta_segura_sigue_permitida(self, non_interactive_permissions):
        from src.tools.system import SystemInfoTool

        assert non_interactive_permissions.check_permission(SystemInfoTool(), {}) is True


# --- C-3: correlación del tool_call_id con Groq -----------------------------


class TestToolCallIdGroq:
    def test_el_id_del_resultado_coincide_con_el_de_la_llamada(self):
        with patch("src.models.groq.Groq"):
            from src.models.groq import GroqProvider

            provider = GroqProvider(api_key="clave-de-prueba")
            provider.client = MagicMock()
            provider.client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(tool_calls=None, content="ok"))]
            )

            provider.generate([
                ChatMessage(role="user", content="hola"),
                ChatMessage(
                    role="model",
                    tool_calls=[ToolCallRequest(id="call_ABC", name="list_files", arguments={})],
                ),
                ChatMessage(
                    role="tool",
                    tool_name="list_files",
                    tool_call_id="call_ABC",
                    tool_result={"success": True},
                ),
            ])

            enviados = provider.client.chat.completions.create.call_args.kwargs["messages"]
            asistente = next(m for m in enviados if m["role"] == "assistant")
            herramienta = next(m for m in enviados if m["role"] == "tool")

            assert asistente["tool_calls"][0]["id"] == herramienta["tool_call_id"]

    def test_chat_message_expone_el_campo(self):
        assert "tool_call_id" in ChatMessage.__dataclass_fields__


# --- C-4: aislamiento de sesiones y concurrencia ----------------------------


class TestSesiones:
    def test_dos_sesiones_no_comparten_historial(self, non_interactive_permissions):
        agent = _agent(non_interactive_permissions, responses=["r1", "r2"])

        agent.process("mensaje de ana", session_id="ana")
        agent.process("mensaje de bob", session_id="bob")

        ana = [m.content for m in agent.sessions.get("ana").messages if m.role == "user"]
        bob = [m.content for m in agent.sessions.get("bob").messages if m.role == "user"]

        assert ana == ["mensaje de ana"]
        assert bob == ["mensaje de bob"]

    def test_turnos_concurrentes_no_corrompen_el_historial(self, non_interactive_permissions):
        model = MockLLMProvider()
        for _ in range(40):
            model.queue_text("respuesta")
        agent = Agent(
            model=model,
            tool_registry=ToolRegistry(),
            permission_manager=non_interactive_permissions,
            sessions=SessionStore(),
        )

        errores: list[Exception] = []

        def hablar(i: int):
            try:
                for _ in range(5):
                    agent.process(f"mensaje {i}", session_id="compartida")
            except Exception as exc:  # pragma: no cover
                errores.append(exc)

        hilos = [threading.Thread(target=hablar, args=(i,)) for i in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        assert errores == []
        # 20 turnos completos, uno detrás de otro.
        assert model.call_count == 20
        # Y la conversación sin mezclar: persona y Morgan alternándose. Desde la 4.1.5 en
        # memoria solo quedan los últimos 20 de texto al empezar cada turno, más el último.
        mensajes = agent.sessions.get("compartida").messages
        assert [m.role for m in mensajes] == ["user", "model"] * (len(mensajes) // 2)
        assert len(mensajes) == 22

    def test_el_historial_se_acota(self, non_interactive_permissions):
        model = MockLLMProvider()
        for _ in range(30):
            model.queue_text("ok")
        sessions = SessionStore(max_messages_per_session=10)
        agent = Agent(
            model=model,
            tool_registry=ToolRegistry(),
            permission_manager=non_interactive_permissions,
            sessions=sessions,
        )

        for i in range(20):
            agent.process(f"mensaje {i}", session_id="larga")

        assert len(agent.sessions.get("larga").messages) <= 10

    def test_el_almacen_acota_el_numero_de_sesiones(self):
        store = SessionStore(max_sessions=3)
        for i in range(10):
            store.get(f"sesion-{i}")
        assert len(store) == 3


# --- C-5: rutas protegidas en todas las escrituras --------------------------


class TestRutasProtegidasEnEscritura:
    @pytest.mark.parametrize(
        "tool, args",
        [
            (CreateFileTool(), {"path": "C:/Windows/System32/prueba.dll", "content": "x"}),
            (CreateFileTool(), {"path": "C:/Program Files/prueba.txt", "content": "x"}),
            (MoveFileTool(), {"src": "origen.txt", "dst": "C:/Windows/destino.txt"}),
            (PatchFileTool(), {
                "path": "C:/Windows/notepad.exe",
                "target_content": "a",
                "replacement_content": "b",
            }),
        ],
    )
    def test_escritura_en_ruta_del_sistema_denegada(self, non_interactive_permissions, tool, args):
        assert non_interactive_permissions.check_permission(tool, args) is False

    def test_ruta_de_usuario_normal_no_se_bloquea_por_seguridad(self, non_interactive_permissions, tmp_path):
        """Se deniega por falta de consola, no por la validación de rutas."""
        from src.security.validator import PathValidator

        assert PathValidator.is_protected(str(tmp_path / "archivo.txt")) is False


# --- Validación de argumentos (I-8) -----------------------------------------


class TestValidacionDeArgumentos:
    def test_tipo_incorrecto_se_rechaza(self, non_interactive_permissions):
        from src.tools.terminal import ExecuteCommandTool

        agent = _agent(non_interactive_permissions)
        error = agent._validate_args(ExecuteCommandTool(), {"command": "dir", "timeout": "treinta"})

        assert error is not None
        assert "timeout" in error

    def test_booleano_no_vale_como_numero(self, non_interactive_permissions):
        from src.tools.terminal import ExecuteCommandTool

        agent = _agent(non_interactive_permissions)
        error = agent._validate_args(ExecuteCommandTool(), {"command": "dir", "timeout": True})

        assert error is not None
        assert "booleano" in error.lower()

    def test_argumento_desconocido_se_rechaza(self, non_interactive_permissions):
        agent = _agent(non_interactive_permissions)
        error = agent._validate_args(CreateFileTool(), {"path": "a.txt", "content": "x", "inventado": 1})

        assert error is not None
        assert "inventado" in error

    def test_argumentos_validos_pasan(self, non_interactive_permissions):
        agent = _agent(non_interactive_permissions)
        assert agent._validate_args(CreateFileTool(), {"path": "a.txt", "content": "x"}) is None

    def test_falta_obligatorio(self, non_interactive_permissions):
        agent = _agent(non_interactive_permissions)
        error = agent._validate_args(CreateFileTool(), {"path": "a.txt"})

        assert error is not None
        assert "content" in error


# --- Base de datos (Fase D) --------------------------------------------------


class TestBaseDeDatos:
    def test_la_conexion_se_cierra(self, temp_db):
        with temp_db.connect() as conn:
            conn.execute("SELECT 1")

        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_migraciones_idempotentes(self, temp_db):
        primera = temp_db.migrate()
        segunda = temp_db.migrate()

        assert primera == segunda == temp_db.version()

    def test_base_corrupta_da_error_controlado(self, tmp_path):
        corrupta = tmp_path / "corrupta.db"
        corrupta.write_bytes(b"esto no es una base de datos" * 50)

        with pytest.raises(MemoryStorageError):
            sobre_sqlite(corrupta).recall()

    def test_directorio_inaccesible_da_error_controlado(self, tmp_path):
        # Un directorio en lugar de un fichero de base de datos.
        objetivo = tmp_path / "soy_un_directorio.db"
        objetivo.mkdir()

        with pytest.raises(MemoryStorageError):
            sobre_sqlite(objetivo).recall()

    def test_escrituras_concurrentes(self, tmp_path):
        # Con 60 s de espera y no los 10 de serie (4.19), como la de test_persistence: en la
        # integración continua, con el disco atascado, falló tras esperar los 10 s enteros.
        from src.memory.db import Database

        storage = sobre_sqlite(database=Database(tmp_path / "concurrentes.db", timeout=60))
        errores: list[Exception] = []

        def escribir(i: int):
            try:
                for j in range(20):
                    storage.remember(f"clave_{i}_{j}", f"valor_{j}", "concurrencia")
            except Exception as exc:  # pragma: no cover
                errores.append(exc)

        hilos = [threading.Thread(target=escribir, args=(i,)) for i in range(5)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        assert errores == []
        assert len(storage.recall(category="concurrencia")) == 100

    def test_clave_vacia_se_rechaza(self, temp_db):
        with pytest.raises(MemoryStorageError):
            sobre_sqlite(database=temp_db).remember("   ", "valor")

    def test_get_context_summary_tolera_fallo_de_bd(self, tmp_path):
        corrupta = tmp_path / "rota.db"
        corrupta.write_bytes(b"basura binaria" * 100)
        manager = MemoryManager(storage=MemoriaSobreRepositorio.__new__(MemoriaSobreRepositorio))

        class StorageRoto:
            def recall(self, *args, **kwargs):
                raise MemoryStorageError("base de datos no disponible")

        manager.storage = StorageRoto()

        # No debe propagar: Morgan tiene que poder arrancar sin memoria.
        assert manager.get_context_summary() == ""


# --- I-3: el system prompt no debe crecer sin límite ------------------------


class TestPromptAcotado:
    """Desde la 4.0 la memoria se añade en cada turno (nunca al prompt compartido), así
    que no puede acumularse; se sigue comprobando que el prompt crece lo justo."""

    def test_la_memoria_se_sustituye_y_no_se_acumula(self, non_interactive_permissions, tmp_path):
        agent = _agent(non_interactive_permissions)
        manager = MemoryManager(storage=sobre_sqlite(tmp_path / "prompt.db"))
        agent.memoria = manager.get_context_summary

        tamanos = []
        for i in range(8):
            manager.remember(f"dato_{i}", "x" * 30, "prueba")
            agent.chat("hola", session_id=f"s{i}")
            tamanos.append(len(agent.model.prompts[-1]))

        assert agent.model.prompts[-1].count(MEMORY_SUMMARY_HEADER) == 1
        assert MEMORY_SUMMARY_HEADER not in agent.system_prompt, "nunca en el prompt compartido"
        # El crecimiento por recuerdo debe ser constante, no acumulativo.
        incrementos = [b - a for a, b in zip(tamanos, tamanos[1:])]
        assert max(incrementos) - min(incrementos) < 10

    def test_memoria_vacia_deja_el_prompt_limpio(self, non_interactive_permissions):
        agent = _agent(non_interactive_permissions)
        agent.memoria = lambda: ""

        agent.chat("hola")

        assert MEMORY_SUMMARY_HEADER not in agent.model.prompts[-1]

    def test_una_memoria_que_falla_no_deja_sin_respuesta(self, non_interactive_permissions):
        agent = _agent(non_interactive_permissions, responses=["aquí estoy"])

        def rota():
            raise RuntimeError("base caída")

        agent.memoria = rota

        assert agent.chat("hola") == "aquí estoy"


# --- SSRF en read_webpage ----------------------------------------------------


class TestProteccionSSRF:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000/status",
            "http://127.0.0.1/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://192.168.1.1/",
            "http://10.0.0.1/",
            "file:///C:/Windows/win.ini",
            "ftp://ejemplo.org/archivo",
        ],
    )
    def test_destinos_internos_bloqueados(self, url):
        resultado = ReadWebpageTool().execute(url=url)

        assert resultado["success"] is False
        assert resultado["error"]

    def test_un_dominio_publico_no_se_bloquea_por_ssrf(self):
        es_publico, motivo = check_url_is_public("https://example.com")

        # Si no hay red, el motivo será de DNS, nunca de dirección interna.
        if not es_publico:
            assert "interna" not in (motivo or "")


# --- Auditoría ---------------------------------------------------------------


class TestAuditoria:
    def test_lee_solo_la_cola_y_devuelve_lo_ultimo(self, temp_audit):
        for i in range(500):
            temp_audit.log(f"herramienta_{i}", "safe", True, {"i": i}, True)

        recientes = temp_audit.get_recent(limit=10)

        assert len(recientes) == 10
        assert recientes[-1]["tool"] == "herramienta_499"

    def test_tolera_lineas_corruptas(self, temp_audit):
        for i in range(5):
            temp_audit.log(f"herramienta_{i}", "safe", True, {}, True)
        with open(temp_audit.log_file, "a", encoding="utf-8") as f:
            f.write("{json a medio escribir\n")

        recientes = temp_audit.get_recent(limit=10)

        assert len(recientes) == 5

    def test_log_inexistente_devuelve_vacio(self, tmp_path):
        from src.security.audit import AuditLogger

        logger = AuditLogger(log_path=tmp_path / "todavia_no_existe.log")

        assert logger.get_recent() == []

    def test_los_secretos_se_enmascaran(self, temp_audit):
        temp_audit.log("execute_command", "critical", True, {"API_KEY": "gsk_secreto_real"}, True)

        entrada = temp_audit.get_recent(limit=1)[0]

        assert "gsk_secreto_real" not in str(entrada)
        assert entrada["args"]["API_KEY"] == "********"


# --- Configuración inválida --------------------------------------------------


class TestConfiguracion:
    def test_puerto_invalido_cae_al_valor_por_defecto(self, monkeypatch):
        from src.config import load_settings

        monkeypatch.setenv("MORGAN_API_PORT", "no-es-un-numero")

        assert load_settings().api_port == 8000

    def test_modo_de_permisos_invalido_cae_a_ask(self, monkeypatch):
        from src.config import load_settings

        monkeypatch.setenv("MODERATE_PERMISSION_MODE", "loquesea")

        assert load_settings().moderate_permission_mode == "ask"

    def test_los_marcadores_del_ejemplo_no_cuentan_como_clave(self, monkeypatch):
        from src.config import load_settings

        monkeypatch.setenv("GROQ_API_KEY", "tu-groq-api-key-aqui")
        monkeypatch.setenv("GEMINI_API_KEY", "")

        settings = load_settings()

        assert settings.groq_api_key is None
        assert settings.has_llm is False

    def test_redacted_nunca_expone_la_clave(self, monkeypatch):
        from src.config import load_settings

        monkeypatch.setenv("GROQ_API_KEY", "gsk_una_clave_muy_secreta")

        redactada = load_settings().redacted()

        assert "gsk_una_clave_muy_secreta" not in str(redactada)


# --- Fallos del proveedor LLM ------------------------------------------------


class TestFallosDelModelo:
    def test_una_excepcion_del_proveedor_no_rompe_el_agente(self, non_interactive_permissions):
        class ProveedorRoto(MockLLMProvider):
            def generate(self, messages, tools=None, system_prompt=None):
                raise RuntimeError("la API del proveedor no responde")

        agent = Agent(
            model=ProveedorRoto(),
            tool_registry=ToolRegistry(),
            permission_manager=non_interactive_permissions,
            sessions=SessionStore(),
        )

        respuesta = agent.process("hola")

        assert isinstance(respuesta, str)
        assert respuesta.strip()
        assert "la API del proveedor no responde" not in respuesta

    def test_gemini_sin_candidatos_devuelve_texto_explicativo(self):
        with patch("src.models.gemini.genai"):
            from src.models.gemini import GeminiProvider

            provider = GeminiProvider(api_key="clave-de-prueba")
            provider.client = MagicMock()
            provider.client.models.generate_content.return_value = MagicMock(
                candidates=[],
                prompt_feedback=MagicMock(block_reason="SAFETY"),
                text="",
            )

            respuesta = provider.generate([])

            assert respuesta.type == "text"
            assert "SAFETY" in respuesta.content

    def test_gemini_recoge_todas_las_llamadas_a_herramienta(self):
        with patch("src.models.gemini.genai"):
            from src.models.gemini import GeminiProvider

            provider = GeminiProvider(api_key="clave-de-prueba")
            provider.client = MagicMock()

            def parte(nombre):
                p = MagicMock()
                p.function_call = MagicMock(name=nombre, args={})
                p.function_call.name = nombre
                return p

            contenido = MagicMock(parts=[parte("list_files"), parte("system_info")])
            provider.client.models.generate_content.return_value = MagicMock(
                candidates=[MagicMock(content=contenido)]
            )

            respuesta = provider.generate([])

            assert respuesta.type == "tool_call"
            assert [tc.name for tc in respuesta.tool_calls] == ["list_files", "system_info"]


# --- Herramientas ante entradas inválidas -----------------------------------


class TestHerramientasEntradasInvalidas:
    def test_leer_archivo_inexistente(self):
        from src.tools.filesystem import ReadFileTool

        resultado = ReadFileTool().execute(path="Z:/no/existe/archivo.txt")

        assert resultado["success"] is False
        assert resultado["error"]

    def test_listar_directorio_inexistente(self):
        from src.tools.filesystem import ListFilesTool

        resultado = ListFilesTool().execute(path="Z:/no/existe")

        assert resultado["success"] is False

    def test_listar_un_archivo_como_si_fuera_carpeta(self, tmp_path):
        from src.tools.filesystem import ListFilesTool

        archivo = tmp_path / "soy_un_archivo.txt"
        archivo.write_text("contenido", encoding="utf-8")

        resultado = ListFilesTool().execute(path=str(archivo))

        assert resultado["success"] is False
        assert "directorio" in resultado["error"].lower()

    def test_comando_con_timeout_expirado(self):
        from src.tools.terminal import ExecuteCommandTool

        resultado = ExecuteCommandTool().execute(command="Start-Sleep -Seconds 10", timeout=2)

        assert resultado["success"] is False
        assert "límite" in resultado["error"].lower() or "limite" in resultado["error"].lower()

    def test_matar_proceso_sin_argumentos(self):
        from src.tools.terminal import KillProcessTool

        resultado = KillProcessTool().execute()

        assert resultado["success"] is False

    def test_matar_proceso_protegido(self):
        from src.tools.terminal import KillProcessTool

        resultado = KillProcessTool().execute(name="lsass.exe")

        assert resultado["success"] is False
        assert "protegido" in resultado["error"].lower()

    def test_patch_sin_coincidencia(self, tmp_path):
        archivo = tmp_path / "codigo.py"
        archivo.write_text("print('hola')\n", encoding="utf-8")

        resultado = PatchFileTool().execute(
            path=str(archivo),
            target_content="texto que no aparece",
            replacement_content="nuevo",
        )

        assert resultado["success"] is False
        assert archivo.read_text(encoding="utf-8") == "print('hola')\n"

    def test_patch_con_coincidencia_ambigua_no_modifica(self, tmp_path):
        archivo = tmp_path / "codigo.py"
        archivo.write_text("x = 1\nx = 1\n", encoding="utf-8")
        original = archivo.read_text(encoding="utf-8")

        resultado = PatchFileTool().execute(
            path=str(archivo), target_content="x = 1", replacement_content="x = 2"
        )

        assert resultado["success"] is False
        assert archivo.read_text(encoding="utf-8") == original

    def test_borrar_ruta_inexistente(self):
        from src.tools.filesystem import DeleteFileTool

        resultado = DeleteFileTool().execute(path="Z:/no/existe.txt")

        assert resultado["success"] is False


class TestScriptsPowerShell:
    """Los .ps1 con caracteres no ASCII deben llevar BOM.

    Windows PowerShell 5.1 (el que trae Windows por defecto) asume la pagina de
    codigos ANSI cuando el fichero no lleva BOM. Leidos asi, los bytes UTF-8 de
    '—' y de '✔' producen una comilla doble en mitad de una cadena y el script
    no llega ni a analizarse: falla antes de ejecutar una sola linea.
    """

    @staticmethod
    def _scripts() -> list[Path]:
        raiz = Path(__file__).resolve().parent.parent
        return [
            p
            for p in sorted((raiz / "scripts").rglob("*.ps1"))
            if "node_modules" not in p.parts and "venv" not in p.parts
        ]

    def test_hay_scripts_que_revisar(self):
        # Si alguien mueve la carpeta, la prueba de abajo pasaria en vacio.
        assert self._scripts(), "No se encontro ningun .ps1 que comprobar"

    def test_con_caracteres_no_ascii_llevan_bom(self):
        sin_bom = []
        for script in self._scripts():
            bytes_ = script.read_bytes()
            if bytes_.startswith(b"\xef\xbb\xbf"):
                continue
            texto = bytes_.decode("utf-8")
            if any(ord(c) > 127 for c in texto):
                sin_bom.append(script.name)

        assert not sin_bom, (
            "Estos scripts tienen caracteres no ASCII y no llevan BOM; "
            f"PowerShell 5.1 no podra analizarlos: {sin_bom}"
        )

    def test_el_script_de_acceso_remoto_analiza_sin_errores(self):
        """Comprueba el sintoma, no solo la causa: que PowerShell lo acepte."""
        script = Path(__file__).resolve().parent.parent / "scripts" / "morgan-remoto.ps1"
        if not script.exists():
            pytest.skip("scripts/morgan-remoto.ps1 no existe")
        if not shutil.which("powershell"):
            pytest.skip("powershell no disponible en este equipo")

        comprobacion = (
            "$e = $null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{script}', [ref]$null, [ref]$e) | Out-Null; "
            "if ($e.Count -gt 0) { $e | ForEach-Object { $_.Message }; exit 1 }"
        )
        resultado = subprocess.run(
            ["powershell", "-NoProfile", "-Command", comprobacion],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert resultado.returncode == 0, (
            f"morgan-remoto.ps1 no analiza:\n{resultado.stdout}{resultado.stderr}"
        )


class TestGeminiThoughtSignature:
    """Gemini rechaza los functionCall que no llevan su propia firma.

    Sintoma real: al conmutar de Groq a Gemini en mitad de una conversacion con
    herramientas, la API devolvia
    400 INVALID_ARGUMENT ... 'Function call is missing a thought_signature'
    y el turno se abortaba entero.
    """

    @staticmethod
    def _proveedor():
        with patch("src.models.gemini.genai"):
            from src.models.gemini import GeminiProvider

            return GeminiProvider(api_key="clave-de-prueba")

    @staticmethod
    def _partes(contenido):
        return list(getattr(contenido, "parts", []) or [])

    def test_llamadas_sin_firma_no_se_reenvian_como_function_call(self):
        """El caso que fallaba: tool_calls producidos por Groq, sin raw_parts."""
        proveedor = self._proveedor()

        historial = [
            ChatMessage(role="user", content="busca algo"),
            ChatMessage(
                role="model",
                tool_calls=[ToolCallRequest(name="search_web", arguments={"query": "x"})],
            ),
            ChatMessage(
                role="tool",
                tool_name="search_web",
                tool_result={"success": True, "data": "resultados"},
            ),
        ]

        contenidos = proveedor._build_contents(historial)

        for contenido in contenidos:
            for parte in self._partes(contenido):
                assert getattr(parte, "function_call", None) is None, (
                    "Se reenvio un functionCall sin thought_signature: es justo lo "
                    "que Gemini rechaza con 400 INVALID_ARGUMENT"
                )
                assert getattr(parte, "function_response", None) is None, (
                    "Un functionResponse sin su functionCall delante tambien es invalido"
                )

    def test_la_informacion_de_la_llamada_no_se_pierde(self):
        """Degradar a texto no puede significar que el modelo se quede a ciegas."""
        proveedor = self._proveedor()

        historial = [
            ChatMessage(
                role="model",
                tool_calls=[ToolCallRequest(name="search_web", arguments={"query": "morgan"})],
            ),
            ChatMessage(
                role="tool",
                tool_name="search_web",
                tool_result={"success": True, "data": "el resultado concreto"},
            ),
        ]

        texto = " ".join(
            getattr(parte, "text", "") or ""
            for contenido in proveedor._build_contents(historial)
            for parte in self._partes(contenido)
        )

        assert "search_web" in texto
        assert "morgan" in texto
        assert "el resultado concreto" in texto

    def test_las_llamadas_con_firma_se_conservan_intactas(self):
        """No se puede arreglar lo anterior rompiendo el camino nativo de Gemini."""
        proveedor = self._proveedor()

        # Una Part real: types.Content valida con pydantic y rechaza un MagicMock.
        from google.genai import types

        firmada = types.Part.from_function_call(name="search_web", args={})

        historial = [
            ChatMessage(
                role="model",
                tool_calls=[ToolCallRequest(name="search_web", arguments={})],
                raw_parts=[firmada],
            ),
            ChatMessage(role="tool", tool_name="search_web", tool_result={"ok": True}),
        ]

        contenidos = proveedor._build_contents(historial)

        assert self._partes(contenidos[0]) == [firmada], (
            "Las partes originales de Gemini deben pasar tal cual, con su firma"
        )
        # El resultado si vuelve como functionResponse: su llamada era valida.
        assert any(
            getattr(parte, "function_response", None) is not None
            for parte in self._partes(contenidos[1])
        )

    def test_los_resultados_enormes_se_truncan(self):
        """read_webpage puede devolver cientos de miles de caracteres."""
        proveedor = self._proveedor()

        historial = [
            ChatMessage(
                role="model",
                tool_calls=[ToolCallRequest(name="read_webpage", arguments={})],
            ),
            ChatMessage(
                role="tool",
                tool_name="read_webpage",
                tool_result={"data": "x" * 500_000},
            ),
        ]

        texto = " ".join(
            getattr(parte, "text", "") or ""
            for contenido in proveedor._build_contents(historial)
            for parte in self._partes(contenido)
        )

        assert len(texto) < 10_000
        assert "truncado" in texto


class TestGeminiTraduceLosEsquemasCompletos:
    """Gemini exige `items` en los parametros de tipo array.

    Sin el, rechaza la peticion entera con
    `parameters.properties[x].items: missing field`, y no solo esa herramienta:
    **el catalogo completo**, asi que Gemini deja de servir de respaldo.

    El fallo estaba latente desde siempre y salio al anadir `create_task`, la
    primera herramienta con un parametro de tipo lista. Lo detecto el Stable Gate
    de la V1.5 ejecutando un encargo real.
    """

    @staticmethod
    def _proveedor():
        from unittest.mock import patch

        with patch("src.models.gemini.genai"):
            from src.models.gemini import GeminiProvider

            return GeminiProvider(api_key="clave-de-prueba")

    def test_un_array_lleva_items(self):
        esquema = self._proveedor()._a_schema(
            {"type": "array", "items": {"type": "string"}, "description": "pasos"}
        )

        assert str(esquema.type) == "Type.ARRAY"
        assert esquema.items is not None, "sin 'items', Gemini rechaza el catalogo entero"

    def test_un_array_sin_items_declarados_asume_texto(self):
        """Mejor una suposicion razonable que una peticion invalida."""
        esquema = self._proveedor()._a_schema({"type": "array"})

        assert esquema.items is not None

    def test_un_enum_se_conserva(self):
        esquema = self._proveedor()._a_schema({"type": "string", "enum": ["a", "b"]})

        assert esquema.enum == ["a", "b"]

    def test_los_tipos_simples_no_llevan_items(self):
        assert self._proveedor()._a_schema({"type": "string"}).items is None

    def test_el_catalogo_real_se_traduce_entero(self, tmp_path, monkeypatch):
        """La prueba que de verdad importa: que ninguna herramienta registrada
        produzca un esquema que Gemini vaya a rechazar."""
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        import src.config as config

        config.reset_settings()
        from src.api import dependencies

        dependencies.reset_container()
        proveedor = self._proveedor()
        try:
            # El del contenedor: el que usan la API y la consola. El de la
            # consola, que se usaba aquí, era una copia a la que le faltaban 12.
            for esquema in dependencies.get_container().tool_registry.get_schemas():
                for nombre, definicion in esquema.get("parameters", {}).get("properties", {}).items():
                    traducido = proveedor._a_schema(definicion)
                    if definicion.get("type") == "array":
                        assert traducido.items is not None, (
                            f"{esquema['name']}.{nombre} es un array sin 'items': "
                            "Gemini rechazaria el catalogo completo"
                        )
        finally:
            config.reset_settings()
