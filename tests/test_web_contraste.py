"""
El contraste del texto de la web (4.23, lo básico de accesibilidad), en los tres temas.

Medido con axe-core (`scripts/auditar_accesibilidad.py`) en las 8 vistas y las 9 secciones de
Ajustes: el único fallo era el contraste. El texto tenue (`--text-muted`) se quedaba en 3,3-4,6:1
según el tema y el fondo, y en el claro el verde de «disponible» en 2,9:1 y el azul del lema en
3,8:1. WCAG AA pide **4,5:1** para el texto normal. Arreglado en los colores de cada tema
(`web/src/index.css`), y aquí se fija sin navegador: cada color de texto contra cada fondo de
su tema, para que un retoque de diseño no lo vuelva a bajar.
"""

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "web" / "src" / "index.css"
TEXTOS = ("--text-primary", "--text-secondary", "--text-muted", "--accent-text",
          "--status-success", "--status-error", "--status-degraded", "--status-info")
FONDOS = ("--bg-app", "--bg-sidebar", "--bg-raised", "--bg-active", "--bg-input", "--bg-input-hover", "--bg-card")
MINIMO = 4.5


def _temas() -> dict[str, dict[str, str]]:
    texto = CSS.read_text(encoding="utf-8")
    temas = {}
    for selector, nombre in ((r":root\s*\{", "azul noche"), (r':root\[data-tema="negro"\]\s*\{', "oscuro"),
                             (r':root\[data-tema="claro"\]\s*\{', "claro")):
        inicio = re.search(selector, texto).end()
        bloque = texto[inicio:texto.index("\n}", inicio)]
        temas[nombre] = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b", bloque))
    return temas


def _luminancia(color: str) -> float:
    canales = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a: str, b: str) -> float:
    claro, oscuro = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (claro + 0.05) / (oscuro + 0.05)


def test_la_formula_es_la_de_wcag():
    assert round(contraste("#000000", "#ffffff"), 1) == 21.0
    assert round(contraste("#6f7f98", "#111a2c"), 2) == 4.28       # lo que midió axe: 4,27


@pytest.mark.parametrize("tema", ["azul noche", "oscuro", "claro"])
def test_cada_texto_se_lee_sobre_cada_fondo(tema):
    colores = _temas()[tema]
    assert set(TEXTOS) <= set(colores) and set(FONDOS) <= set(colores), "falta un color del tema"
    flojos = [f"{t} {colores[t]} sobre {f} {colores[f]}: {contraste(colores[t], colores[f]):.2f}"
              for t in TEXTOS for f in FONDOS if contraste(colores[t], colores[f]) < MINIMO]
    assert not flojos, "\n".join(flojos)
