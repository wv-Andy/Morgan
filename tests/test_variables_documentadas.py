"""
Toda variable de entorno que el código lee aparece en `.env.example`.

**Por qué existe.** El fichero se quedó con 12 variables sin mencionar: los
límites de las subidas, dónde escribe Morgan en disco, las credenciales de
GitHub, la clave que cifra los tokens, el tope de la respuesta HTTP. Quien
despliega Morgan no tiene forma de descubrirlas leyendo el ejemplo, y algunas
—`MORGAN_SECRET_KEY`— deciden si una funcionalidad entera se ofrece o no.

Es el tipo de desajuste que no falla nunca y se nota tarde: todo arranca, y una
parte simplemente no está.

La lista se saca del código, no de una copia escrita a mano, para que una
variable nueva obligue a documentarla el mismo día.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

#: Las que no son configuración de Morgan sino de quien lo aloja: las define la
#: plataforma, no la persona, y ponerlas en el ejemplo invita a tocarlas.
DE_LA_PLATAFORMA = {
    "RENDER_EXTERNAL_URL",
    "VERCEL_GIT_COMMIT_SHA",
    "PORT",
    # La del sistema operativo: la terminal del agente (3.5) busca ahí los programas.
    "PATH",
}


def variables_que_lee_el_codigo() -> set[str]:
    encontradas: set[str] = set()
    for fichero in (RAIZ / "src").rglob("*.py"):
        texto = fichero.read_text(encoding="utf-8")
        for patron in (
            r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
            r'_env_(?:int|bool|str)\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
            r'os\.environ\[["\']([A-Z][A-Z0-9_]+)["\']\]',
            r'os\.environ\.get\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
        ):
            encontradas |= set(re.findall(patron, texto))
    return encontradas - DE_LA_PLATAFORMA


class TestElEjemploEstaCompleto:
    def test_ninguna_variable_se_queda_sin_mencionar(self):
        ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
        faltan = sorted(v for v in variables_que_lee_el_codigo() if v not in ejemplo)

        assert not faltan, (
            f"El código lee estas variables y .env.example no las menciona: "
            f"{faltan}. Quien despliegue Morgan no puede descubrirlas"
        )

    def test_el_ejemplo_no_fija_el_tope_del_turno(self):
        """`MORGAN_TURN_TIMEOUT=120` estuvo recomendado aquí, y 120 es el peor
        número posible: coincide exactamente con el corte del proxy del borde,
        así que el turno muere en una página de error de Vercel en lugar de en
        el aviso de Morgan. Sin definir, el valor sigue al entorno."""
        ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")

        activa = re.search(r"^MORGAN_TURN_TIMEOUT\s*=\s*\S", ejemplo, re.MULTILINE)
        assert not activa, (
            "`.env.example` fija MORGAN_TURN_TIMEOUT. Tiene que ir comentada: "
            "el valor por defecto depende del entorno y fijarlo lo rompe"
        )
