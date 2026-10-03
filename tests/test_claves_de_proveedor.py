"""
Que una clave imposible se detecte al arrancar, y no turno a turno.

**El fallo que da origen a esto, con sus números.** Al recrear el servicio de
producción en Virginia hubo que teclear 18 secretos a mano. Tres salieron
tocados, y dos eran claves de modelo:

```
Groq   → 401 Invalid API Key
Gemini → 'ascii' codec can't encode character '\\xa3' in position 19
```

El síntoma fue el peor posible: **todo funcionaba**. Los turnos se contestaban,
la web iba bien, `/status` decía que el modelo estaba disponible y listaba la
cadena entera. Lo único distinto era que **el proveedor de pago contestaba
todos los turnos** mientras los dos gratuitos fallaban en silencio. Se descubrió
porque una medición miró quién había respondido, no porque nada avisara.

La clave de Gemini es el caso que justifica este módulo entero: un símbolo de
libra dentro de la clave **no puede funcionar nunca**, porque una cabecera HTTP
solo admite ASCII. No es un fallo intermitente ni un problema del proveedor: es
una clave que revienta antes de salir a la red, y eso se sabe sin preguntarle a
nadie.
"""

import pytest

from src.models.claves import (
    MAXIMO_RAZONABLE,
    MINIMO_RAZONABLE,
    ClaveInutilizable,
    revisar,
)

#: Una clave con la pinta de las de verdad, para lo que no se está probando.
BUENA = "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"


class TestElCasoQueOcurrioDeVerdad:
    """La clave de Gemini de producción, reproducida."""

    #: `\xa3` es el símbolo de la libra, y estaba en la posición 19.
    COMO_LA_DE_PRODUCCION = "AIzaSyABCDEFGHIJKL" + "\xa3" + "MNOPQRSTUVWXYZ01234"

    def test_se_rechaza(self):
        with pytest.raises(ClaveInutilizable):
            revisar("GEMINI_API_KEY", self.COMO_LA_DE_PRODUCCION)

    def test_el_mensaje_dice_la_variable(self):
        """Quien lo lea va a ir al panel de Render, y allí ve variables."""
        with pytest.raises(ClaveInutilizable, match="GEMINI_API_KEY"):
            revisar("GEMINI_API_KEY", self.COMO_LA_DE_PRODUCCION)

    def test_y_dice_DONDE_esta_el_caracter(self):
        """Sin la posición, «tiene un carácter raro» obliga a mirar 39
        caracteres uno por uno para encontrarlo.
        """
        with pytest.raises(ClaveInutilizable) as fallo:
            revisar("GEMINI_API_KEY", self.COMO_LA_DE_PRODUCCION)

        assert "posición 19" in str(fallo.value)

    def test_y_explica_por_que_es_imposible_y_no_solo_raro(self):
        """La diferencia importa: «raro» invita a probar otra vez, «imposible»
        manda a copiar la clave de nuevo, que es lo que hay que hacer.
        """
        with pytest.raises(ClaveInutilizable) as fallo:
            revisar("GEMINI_API_KEY", self.COMO_LA_DE_PRODUCCION)

        mensaje = str(fallo.value)
        assert "NO PUEDE funcionar" in mensaje
        assert "ASCII" in mensaje
        assert "copiarla" in mensaje


class TestLoQueNoEsUnError:
    """Una comprobación que rechaza de más es peor que no tenerla: deja a
    Morgan sin proveedor por algo que funcionaba.
    """

    def test_una_clave_normal_pasa(self):
        assert revisar("GROQ_API_KEY", BUENA) == BUENA

    def test_sin_clave_no_es_un_error(self):
        """Significa «este proveedor no está configurado», que es normal y ya lo
        trata la cadena.
        """
        assert revisar("GROQ_API_KEY", None) is None
        assert revisar("GROQ_API_KEY", "") is None
        assert revisar("GROQ_API_KEY", "   ") is None

    @pytest.mark.parametrize("real", [
        "gsk_" + "a" * 52,                       # Groq, 56 caracteres
        "AIza" + "b" * 35,                       # Gemini, 39
        "nvapi-" + "c" * 64,                     # NVIDIA
        "sk-proj-" + "d" * 156,                  # OpenAI, de los largos
    ])
    def test_las_formas_de_los_cuatro_proveedores_pasan(self, real):
        """Las longitudes salen de las claves de verdad: 56 de Groq, 53 de
        Gemini, y las de OpenAI pasan de 160 caracteres.
        """
        assert revisar("X_API_KEY", real) == real

    def test_los_espacios_de_los_bordes_se_perdonan(self, caplog):
        """Es el accidente de copiado más común y no impide nada. Se recorta y
        se avisa, porque si el proveedor luego la rechaza conviene saber que
        venía con espacios.
        """
        with caplog.at_level("WARNING"):
            assert revisar("GROQ_API_KEY", f"  {BUENA}  ") == BUENA

        assert "espacios" in caplog.text


class TestLoQueSeRechaza:
    def test_un_espacio_DENTRO(self):
        """Un pegado que se partió en dos líneas."""
        mitad = len(BUENA) // 2
        with pytest.raises(ClaveInutilizable, match="dentro"):
            revisar("GROQ_API_KEY", BUENA[:mitad] + " " + BUENA[mitad:])

    def test_un_salto_de_linea_dentro(self):
        mitad = len(BUENA) // 2
        with pytest.raises(ClaveInutilizable):
            revisar("GROQ_API_KEY", BUENA[:mitad] + "\n" + BUENA[mitad:])

    def test_un_caracter_de_control_invisible(self):
        """No se ve al mirar la variable en el panel, y rompe la cabecera igual.
        """
        with pytest.raises(ClaveInutilizable, match="invisibles"):
            revisar("GROQ_API_KEY", BUENA[:10] + "\x1b" + BUENA[10:])

    def test_demasiado_corta(self):
        with pytest.raises(ClaveInutilizable, match="pegado a medias"):
            revisar("GROQ_API_KEY", "gsk_abc")

    def test_demasiado_larga(self):
        with pytest.raises(ClaveInutilizable, match="demasiados"):
            revisar("GROQ_API_KEY", "a" * (MAXIMO_RAZONABLE + 1))

    def test_los_topes_dejan_sitio_a_las_claves_de_verdad(self):
        """Un mínimo demasiado alto rechazaría la clave de un proveedor futuro
        más escueto, y eso es el fallo que no se quiere: dejar a Morgan sin
        modelo por una comprobación demasiado celosa.
        """
        assert MINIMO_RAZONABLE < 39, "la de Gemini tiene 39 y debe pasar"
        assert MAXIMO_RAZONABLE > 200, "las de OpenAI pasan de 160"


class TestLoQueNO_se_comprueba:
    """El límite del alcance, escrito para que nadie espere más de lo que hay.

    Esto **no** promete que la clave sirva: promete que si no puede servir, se
    dice antes. Saber si el proveedor la acepta cuesta una llamada de red en
    cada arranque, y un arranque que depende de tres servicios externos es un
    arranque frágil.
    """

    def test_una_clave_bien_formada_pero_falsa_pasa(self):
        """Y está bien que pase. Su `401` aparecerá en el registro al primer
        turno, que es donde corresponde.

        Es justo el caso de la clave de Groq de producción: bien formada, ASCII,
        longitud correcta, y rechazada por Groq. Esta comprobación no la habría
        cazado, y la de Gemini sí.
        """
        falsa_pero_valida = "gsk_" + "0" * 52
        assert revisar("GROQ_API_KEY", falsa_pero_valida) == falsa_pero_valida


class TestLaCadenaNoMontaUnProveedorImposible:
    """De nada sirve detectarlo si luego se monta igual."""

    def test_no_se_activa_y_se_dice_por_que(self, monkeypatch, caplog):
        from src.config import load_settings, reset_settings
        from src.models.chain import build_provider_chain

        monkeypatch.setenv("GEMINI_API_KEY", BUENA[:18] + "\xa3" + BUENA[18:])
        monkeypatch.setenv("GROQ_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("NVIDIA_API_KEY", "")
        monkeypatch.setenv("MORGAN_LLM_ORDER", "gemini")
        reset_settings()
        try:
            with caplog.at_level("ERROR"):
                modelo, activos = build_provider_chain(load_settings())
        finally:
            reset_settings()

        assert activos == [], "se ha montado un proveedor con una clave imposible"
        assert modelo is None
        assert "GEMINI_API_KEY" in caplog.text
        assert "no puede funcionar" in caplog.text

    def test_el_aviso_es_de_nivel_ERROR_y_no_warning(self, monkeypatch, caplog):
        """Es lo único que separa esto de un turno que se paga sin que nadie
        sepa por qué. Un `warning` en un registro con ruido se pierde.
        """
        from src.config import load_settings, reset_settings
        from src.models.chain import build_provider_chain

        monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "\xa3" + "a" * 51)
        monkeypatch.setenv("GEMINI_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("MORGAN_LLM_ORDER", "groq")
        reset_settings()
        try:
            caplog.clear()
            with caplog.at_level("WARNING"):
                build_provider_chain(load_settings())
        finally:
            reset_settings()

        niveles = {r.levelname for r in caplog.records if "GROQ_API_KEY" in r.message}
        assert "ERROR" in niveles, f"el aviso salió como {niveles}"

    def test_los_demas_proveedores_siguen_funcionando(self, monkeypatch):
        """Una clave rota no puede dejar a Morgan sin modelo si hay otra buena.
        Es el mismo principio que ya tenía la cadena: un proveedor que no
        arranca solo se queda fuera de la lista.
        """
        from src.config import load_settings, reset_settings
        from src.models.chain import build_provider_chain

        monkeypatch.setenv("GEMINI_API_KEY", BUENA[:18] + "\xa3" + BUENA[18:])
        monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "a" * 52)
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("NVIDIA_API_KEY", "")
        monkeypatch.setenv("MORGAN_LLM_ORDER", "groq,gemini")
        # Sin el relevo de Groq de serie (2.0.20): aqui se mira que Gemini cae y
        # Groq queda, y el relevo seria un eslabon mas que no viene a cuento.
        monkeypatch.setenv("GROQ_MODELOS_RELEVO", "")
        reset_settings()
        try:
            _modelo, activos = build_provider_chain(load_settings())
        finally:
            reset_settings()

        assert activos == ["groq"], (
            "La clave rota de Gemini se ha llevado por delante a Groq"
        )
