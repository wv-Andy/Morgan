"""
Ningún enlace roto en la documentación (V2.0.22).

**Por qué existe.** El 2026-09-16 la documentación pasó de 61 documentos a 21:
fusionar, resumir y borrar deja enlaces apuntando a ficheros que ya no están, o a
secciones que cambiaron de nombre. Un enlace roto en el portafolio del proyecto es
lo primero que encuentra quien lo lee, y nadie lo nota escribiendo.

Comprueba, en `docs/`, el README, `pendientes.md` y el CHANGELOG:

1. Que cada enlace relativo apunta a un fichero o carpeta que existe.
2. Que cada ancla (`fichero.md#seccion`) corresponde a un título de ese fichero,
   con la misma regla con la que GitHub genera los identificadores.

No sigue enlaces externos (`http…`): eso necesitaría red.
"""

import re
import unicodedata
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# Solo los que existen: en el espejo público (4.19) no están pendientes.md, el CHANGELOG ni
# los documentos internos, y sus enlaces a ellos ya se quitaron al publicarlo.
DOCUMENTOS = sorted(
    d for d in [*(RAIZ / "docs").glob("*.md"), RAIZ / "README.md", RAIZ / "README.es.md", RAIZ / "pendientes.md",
                RAIZ / "CHANGELOG.md", *(RAIZ / "web").glob("README*.md"),
                *(RAIZ / "migraciones" / "supabase").glob("README*.md")]
    if d.exists()
)

#: `[texto](destino)`, sin imágenes de otros sitios ni enlaces con espacios raros.
ENLACE = re.compile(r"\]\(([^)\s]+)\)")
TITULO = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _slug(titulo: str) -> str:
    """El identificador que GitHub da a un título.

    Minúsculas; fuera todo lo que no sea letra, número, espacio, guion o guion
    bajo (emoji, puntuación, comillas, acentos gráficos como `«»`); los espacios
    pasan a guiones. Las letras con tilde se conservan.
    """
    texto = re.sub(r"`([^`]*)`", r"\1", titulo)
    texto = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", texto)
    texto = texto.strip().lower()
    limpio = []
    for c in texto:
        categoria = unicodedata.category(c)
        if c in " -_" or categoria[0] in "LN":
            limpio.append(c)
    return "".join(limpio).replace(" ", "-")


def _anclas(fichero: Path) -> set[str]:
    anclas: set[str] = set()
    vistas: dict[str, int] = {}
    en_codigo = False
    for linea in fichero.read_text(encoding="utf-8").splitlines():
        if linea.lstrip().startswith("```"):
            en_codigo = not en_codigo
            continue
        if en_codigo:
            continue
        m = TITULO.match(linea)
        if not m:
            continue
        base = _slug(m.group(2))
        n = vistas.get(base, 0)
        anclas.add(base if n == 0 else f"{base}-{n}")
        vistas[base] = n + 1
    return anclas


def _enlaces(fichero: Path) -> list[str]:
    texto = fichero.read_text(encoding="utf-8")
    texto = re.sub(r"```.*?```", "", texto, flags=re.S)
    texto = re.sub(r"`[^`\n]*`", "", texto)
    return ENLACE.findall(texto)


def _roto(origen: Path, destino: str) -> str | None:
    if re.match(r"^[a-z]+:", destino) or destino.startswith("//"):
        return None  # externo: http, https, mailto
    ruta, _, ancla = destino.partition("#")
    objetivo = origen if not ruta else (origen.parent / ruta).resolve()
    if not objetivo.exists():
        return f"{destino}: no existe {ruta}"
    if ancla and objetivo.suffix == ".md":
        if ancla.lower() not in _anclas(objetivo):
            return f"{destino}: {objetivo.name} no tiene la sección #{ancla}"
    return None


@pytest.mark.parametrize("documento", DOCUMENTOS, ids=lambda p: str(p.relative_to(RAIZ)))
def test_ningun_enlace_roto(documento):
    rotos = [r for d in _enlaces(documento) if (r := _roto(documento, d))]

    assert not rotos, "Enlaces rotos en " + documento.name + ":\n  " + "\n  ".join(rotos)


class TestLaComprobacionSirve:
    """Una comprobación de enlaces que nunca falla no comprueba nada."""

    def test_detecta_un_fichero_que_no_existe(self, tmp_path):
        doc = tmp_path / "a.md"
        doc.write_text("[x](no-existe.md)", encoding="utf-8")
        assert _roto(doc, "no-existe.md")

    def test_detecta_una_seccion_que_no_existe(self, tmp_path):
        (tmp_path / "b.md").write_text("# Título\n\n## Otra sección\n", encoding="utf-8")
        doc = tmp_path / "a.md"
        assert _roto(doc, "b.md#seccion-inventada")
        assert _roto(doc, "b.md#otra-sección") is None

    def test_la_regla_de_github(self):
        assert _slug("3. Planes: decir qué se va a hacer antes de hacerlo") == "3-planes-decir-qué-se-va-a-hacer-antes-de-hacerlo"
        assert _slug("El streaming: `POST /chat/stream`") == "el-streaming-post-chatstream"
        assert _slug("6. El router de modelos (V2.X del roadmap maestro)") == "6-el-router-de-modelos-v2x-del-roadmap-maestro"
        assert _slug("✅ 7. Quién puede registrarse — decidido") == "-7-quién-puede-registrarse--decidido"

    def test_lee_bastantes_enlaces_como_para_significar_algo(self):
        # Proporcional: en el espejo público hay menos documentos (y menos enlaces).
        total = sum(len(_enlaces(d)) for d in DOCUMENTOS)
        assert total > 5 * len(DOCUMENTOS), f"Solo se leen {total} enlaces: el patrón dejó de funcionar"
