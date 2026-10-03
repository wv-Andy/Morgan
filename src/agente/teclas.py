"""
Las teclas que el control de la interfaz puede pulsar (4.12). Aparte de `interfaz.py` y sin
nada de Windows: la nube (Linux) también la usa, para rechazar un plan imposible antes de
proponerlo (`HerramientaDelEquipo.prevalidar`). Ninguna con la tecla Windows (Win+R abre
«Ejecutar»).
"""

TECLAS = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "retroceso": 0x08, "backspace": 0x08,
          "suprimir": 0x2E, "delete": 0x2E, "arriba": 0x26, "abajo": 0x28, "izquierda": 0x25,
          "derecha": 0x27, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "inicio": 0x24,
          "home": 0x24, "fin": 0x23, "end": 0x23, "repag": 0x21, "pageup": 0x21, "avpag": 0x22,
          "pagedown": 0x22, "espacio": 0x20, "space": 0x20, "f2": 0x71, "f5": 0x74, "f11": 0x7A}
COMBINACIONES = {"ctrl+" + c for c in "acvxzysfntwpl"} | {"ctrl+enter", "shift+tab", "alt+izquierda",
                                                          "alt+derecha", "alt+left", "alt+right"}
PERMITIDAS = ("Intro, Tab, Esc, flechas, Inicio, Fin, Retroceso, Suprimir y "
              "Ctrl+A/C/V/X/Z/Y/S/F/N/T/W/P/L")
#: Cómo se llama un campo de contraseña, por si se nombra: «Contraseña», «Password», «PIN»…
NOMBRES_DE_CLAVE = ("contrase", "password", "passwd", "clave", "pin")


def normalizar(teclas: str) -> str:
    return str(teclas or "").lower().replace(" ", "")


def se_puede(teclas: str) -> bool:
    combinacion = normalizar(teclas)
    return combinacion in TECLAS or combinacion in COMBINACIONES


def parece_clave(nombre: str) -> bool:
    nombre = str(nombre or "").lower()
    return any(p in nombre for p in NOMBRES_DE_CLAVE)
