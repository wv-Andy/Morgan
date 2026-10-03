"""
Pruebas unitarias para las herramientas web y seguridad contra prompt injection (V0.6).
"""

import pytest
from src.tools.web import (
    SearchWebTool,
    ReadWebpageTool,
    _HTMLTextExtractor,
    sanitize_web_content,
)


class TestWebToolsV06:
    def test_permission_levels(self):
        assert SearchWebTool().permission_level == "safe"
        assert ReadWebpageTool().permission_level == "safe"

    def test_html_text_extractor(self):
        html = """
        <html>
            <head><title>Test</title><style>.box { color: red; }</style></head>
            <body>
                <nav><a href="#">Inicio</a></nav>
                <h1>Título de Prueba</h1>
                <p>Este es el <b>primer</b> párrafo.</p>
                <script>console.log('malicious');</script>
                <p>Segundo párrafo con información real.</p>
            </body>
        </html>
        """
        extractor = _HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.get_text()

        assert "Título de Prueba" in text
        assert "primer párrafo" in text
        assert "Segundo párrafo" in text
        # Scripts y estilos deben haber sido eliminados
        assert "malicious" not in text
        assert "color: red" not in text

    def test_sanitize_web_content_delimiters(self):
        raw_text = "Texto obtenido de la web."
        url = "https://example.com/test"
        sanitized = sanitize_web_content(raw_text, url)

        assert '<untrusted_web_data source_url="https://example.com/test">' in sanitized
        assert "</untrusted_web_data>" in sanitized
        assert "Texto obtenido de la web." in sanitized

    def test_read_webpage_invalid_url(self):
        tool = ReadWebpageTool()
        result = tool.execute(url="https://dominio-totalmente-inexistente-12345678.org")
        assert result["success"] is False
        assert "error" in result["error"].lower()
