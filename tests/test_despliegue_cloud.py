"""
Pruebas del arranque en un servicio alojado (paso 3 del plan de producción).

Un backend en `cloud` responde en una URL pública. Dos cosas tienen que cumplirse
antes de que eso sea aceptable: que no arranque sin autenticación, y que las
herramientas capaces de tocar un ordenador no existan siquiera en el catálogo.
"""

from dataclasses import replace

import pytest
import yaml

from pathlib import Path

from src.api.server import ArranqueInseguro, check_startup_safety
from src.config import load_settings, reset_settings

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def entorno_render(monkeypatch):
    """Reproduce las variables que define render.yaml."""
    for clave in ("MORGAN_API_PORT", "MORGAN_DATA_DIR", "MORGAN_LOG_DIR"):
        monkeypatch.delenv(clave, raising=False)

    monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
    monkeypatch.setenv("MORGAN_CLOUD_ENABLED", "true")
    monkeypatch.setenv("MORGAN_API_HOST", "0.0.0.0")
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_API_TOKEN", "token-de-prueba")
    monkeypatch.setenv("PORT", "10000")

    reset_settings()
    yield load_settings()
    reset_settings()


class TestArranqueSeguroEnLaNube:
    """Un backend en la nube responde en una URL pública. Tiene que haber algo
    que lo cierre, y hay **dos formas válidas**: el token compartido, para un
    Morgan privado, o las cuentas, para uno abierto a más gente."""

    def test_sin_token_ni_cuentas_no_arranca(self, entorno_render):
        """La comprobación que impide publicar Morgan abierto a cualquiera."""
        with pytest.raises(ArranqueInseguro) as fallo:
            check_startup_safety(
                replace(entorno_render, api_token=None, require_auth=False)
            )

        texto = str(fallo.value)
        assert "MORGAN_API_TOKEN" in texto
        # El mensaje tiene que ofrecer las dos salidas: quien lo lea porque quiere
        # abrir Morgan a otros no debe acabar poniendo un token compartido, que es
        # justo lo que se lo impediria.
        assert "MORGAN_REQUIRE_AUTH" in texto

    def test_con_token_arranca(self, entorno_render):
        check_startup_safety(replace(entorno_render, require_auth=False))

    def test_con_cuentas_y_sin_token_arranca(self, entorno_render):
        """Las cuentas no son una protección más floja que el token: son más
        fuerte, porque además separan los datos de cada persona.

        Exigir el token aquí hacía **imposible** abrir Morgan a otra gente: para
        invitar a alguien había que darle el token, con lo que esa persona
        obtenía acceso a la API entera al margen de su cuenta.
        """
        check_startup_safety(
            replace(entorno_render, api_token=None, require_auth=True)
        )

    def test_en_la_nube_las_cuentas_se_exigen_por_defecto(self, entorno_render):
        """Sin esto, quien despliegue en la nube sin leer la documentación se
        encontraría un Morgan con cuentas apagadas."""
        assert entorno_render.require_auth is True

    def test_en_local_no_se_exige_token(self, monkeypatch):
        """En local la API solo escucha en 127.0.0.1: exigirlo estorbaría."""
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
        monkeypatch.setenv("MORGAN_API_TOKEN", "")
        reset_settings()
        try:
            check_startup_safety(load_settings())  # no debe lanzar
        finally:
            reset_settings()


class TestElPuertoLoImponeLaPlataforma:
    def test_se_usa_port_cuando_no_hay_variable_propia(self, entorno_render):
        """Render, Railway y Fly asignan el puerto con PORT."""
        assert entorno_render.api_port == 10000

    def test_morgan_api_port_tiene_prioridad(self, monkeypatch):
        """Para no cambiar el arranque en local, donde PORT no existe."""
        monkeypatch.setenv("PORT", "10000")
        monkeypatch.setenv("MORGAN_API_PORT", "8000")
        reset_settings()
        try:
            assert load_settings().api_port == 8000
        finally:
            reset_settings()


class TestRenderYamlEsCoherenteConElCodigo:
    """Un fichero de despliegue que nombre variables inexistentes es peor que
    no tenerlo: parece configurado y no lo está."""

    @staticmethod
    def _variables() -> dict[str, dict]:
        datos = yaml.safe_load((RAIZ / "render.yaml").read_text(encoding="utf-8"))
        servicio = datos["services"][0]
        return {v["key"]: v for v in servicio["envVars"]}

    def test_todas_las_variables_las_lee_alguien(self):
        """Se busca en TODO `src/`, no solo en `config.py`.

        Al principio miraba únicamente ahí, y falló en cuanto se declararon las
        del correo y la del propietario: esas las leen `correo.py` y
        `propietario.py` con `os.getenv`, porque son configuración de una pieza
        concreta y no de Morgan entero.

        Lo que importa es que la variable la lea **alguien**. Una declarada que
        nadie mira es la que hace que el fichero parezca configurado sin estarlo.
        """
        fuente = "\n".join(
            f.read_text(encoding="utf-8")
            for f in (RAIZ / "src").rglob("*.py")
        )
        desconocidas = [
            clave for clave in self._variables() if f'"{clave}"' not in fuente
        ]

        assert not desconocidas, (
            f"render.yaml define variables que nadie lee: {desconocidas}"
        )

    def test_los_secretos_no_estan_escritos_en_el_fichero(self):
        secretos = {
            "MORGAN_API_TOKEN", "GROQ_API_KEY", "GEMINI_API_KEY",
            "SUPABASE_URL", "SUPABASE_SECRET_KEY", "MORGAN_CORS_ORIGINS",
        }
        for clave, definicion in self._variables().items():
            if clave in secretos:
                assert definicion.get("sync") is False, (
                    f"{clave} debe llevar 'sync: false' para que Render lo pida "
                    "en el panel y no quede en el repositorio"
                )
                assert "value" not in definicion, f"{clave} tiene un valor escrito"

    def test_el_modo_de_permisos_es_valido(self):
        from src.config import VALID_PERMISSION_MODES

        modo = self._variables()["MODERATE_PERMISSION_MODE"]["value"]
        assert modo in VALID_PERMISSION_MODES

    def test_escucha_en_todas_las_interfaces(self):
        """En un contenedor, 127.0.0.1 hace que la plataforma nunca lo alcance."""
        assert self._variables()["MORGAN_API_HOST"]["value"] == "0.0.0.0"

    def test_el_health_check_no_exige_token(self):
        """Render reinicia el servicio si esa ruta falla; no puede llevar secreto."""
        datos = yaml.safe_load((RAIZ / "render.yaml").read_text(encoding="utf-8"))
        ruta = datos["services"][0]["healthCheckPath"]

        from src.api.auth import _requires_token

        assert not _requires_token(ruta)


class TestElCatalogoEnLaNube:
    def test_ninguna_herramienta_local_se_registra(self, entorno_render, monkeypatch):
        """La separación que el plan marca como obligatoria."""
        from src.api.dependencies import CoreContainer

        contenedor = CoreContainer()
        herramientas = contenedor.tool_registry.list_tools()

        locales = [t.name for t in herramientas if getattr(t, "requires_local", True)]
        assert locales == [], f"Herramientas locales expuestas en la nube: {locales}"

        # No se fija un numero: cada herramienta nueva no local lo cambiaria sin
        # que nada estuviera mal. Lo que no puede faltar es lo que da sentido al
        # Morgan de la nube.
        nombres = {t.name for t in herramientas}
        assert {"remember_fact", "recall_memory", "search_web", "read_webpage"} <= nombres
        assert "execute_command" not in nombres
        # `delete_file` sí existe desde la 3.3, como representante del agente del PC:
        # manda la orden a SU PC, exige plan y sin PC conectado no está disponible.
        from src.canal.herramientas import EscribirEnElEquipo

        borrar = contenedor.tool_registry.get("delete_file")
        assert isinstance(borrar, EscribirEnElEquipo) and borrar.exige_plan
        assert borrar.disponible() is False

        # `read_file` sí existe desde la 3.0-E, pero SOLO como representante del agente
        # local de cada persona: no toca el disco de este servidor, y sin agente
        # conectado ni siquiera está disponible.
        from src.canal.herramientas import HerramientaDelEquipo

        lectura = contenedor.tool_registry.get("read_file")
        assert isinstance(lectura, HerramientaDelEquipo)
        assert lectura.disponible() is False


class TestElBlueprintDeclaraLoQueHaceFalta:
    """La prueba de arriba comprueba que toda variable declarada exista en el
    código. Faltaba la dirección contraria: que las que el despliegue **necesita**
    estén declaradas.

    Sin eso, `render.yaml` fue quedándose atrás versión a versión. El síntoma no
    es un error, es peor: quien despliega desde el blueprint no ve que le falta
    nada, y descubre a la semana que la recuperación de contraseña manda enlaces
    a `localhost` — porque `MORGAN_WEB_URL` no estaba en la lista que Render le
    pidió.
    """

    IMPRESCINDIBLES = {
        "MORGAN_ENVIRONMENT": "sin esto no se registran las herramientas correctas",
        "MORGAN_API_HOST": "Render no alcanza el servicio si no escucha en 0.0.0.0",
        "MORGAN_REQUIRE_AUTH": "es lo que cierra el despliegue cuando hay cuentas",
        "MORGAN_WEB_URL": "los enlaces del correo apuntarían al equipo de quien los recibe",
        "MORGAN_OWNER_EMAIL": "sin esto nadie es propietario de la instalación",
        "MORGAN_EMAIL_API": "SMTP no sale de Render; sin esto no hay recuperación",
        "MORGAN_EMAIL_API_KEY": "la clave del proveedor de correo",
        "SUPABASE_URL": "en la nube el disco es efímero: los datos viven ahí",
        "SUPABASE_SECRET_KEY": "sin ella, RLS bloquea al propio backend",
        "MORGAN_CORS_ORIGINS": "sin ella el navegador bloquea la web entera",
    }

    @pytest.mark.parametrize("variable, por_que", sorted(IMPRESCINDIBLES.items()))
    def test_esta_declarada(self, variable, por_que):
        declaradas = TestRenderYamlEsCoherenteConElCodigo._variables()

        assert variable in declaradas, f"Falta {variable} en render.yaml: {por_que}"

    def test_los_secretos_no_traen_valor(self):
        """Un secreto con valor escrito en el blueprint es un secreto en el
        repositorio."""
        declaradas = TestRenderYamlEsCoherenteConElCodigo._variables()

        for nombre in ("MORGAN_EMAIL_API_KEY", "SUPABASE_SECRET_KEY", "MORGAN_OWNER_EMAIL"):
            entrada = declaradas.get(nombre, {})
            assert entrada.get("sync") is False, f"{nombre} debería ir con sync: false"
            assert "value" not in entrada, f"{nombre} lleva un valor escrito"


class TestLaMemoriaNoCreceHastaQueLaMaten:
    """Medido en un despliegue igual al de producción (2.3-C, V2.0.39): sin
    `MALLOC_ARENA_MAX=2`, 25 personas a la vez llevaron la memoria a 530 MB de 537
    y Render mató el proceso. Con ella, 182 MB con 25 y 197 MB con 50."""

    def test_render_yaml_la_pone(self):
        variables = TestRenderYamlEsCoherenteConElCodigo._variables()

        assert str(variables.get("MALLOC_ARENA_MAX", {}).get("value")) == "2"

    def test_si_falta_en_la_nube_se_avisa_al_arrancar(self, entorno_render, monkeypatch, capsys):
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
        reset_settings()

        check_startup_safety(load_settings())

        assert "MALLOC_ARENA_MAX" in capsys.readouterr().err

    def test_con_ella_no_se_avisa(self, entorno_render, monkeypatch, capsys):
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        monkeypatch.setenv("MALLOC_ARENA_MAX", "2")
        reset_settings()

        check_startup_safety(load_settings())

        assert "MALLOC_ARENA_MAX" not in capsys.readouterr().err
