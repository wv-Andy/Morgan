"""
Lo que la nube y el agente local tienen que tener igual (3.0).

Vive aparte y sin dependencias para que lo importen los dos lados: la nube
(`src/identidad/agentes.py`, `src/api/routes/agentes.py`) y el programa del PC
(`src/agente/`). Ver docs/agente-local.md.
"""

#: Versión del protocolo que habla esta versión de Morgan (§10 del contrato).
#:
#: **2 (V3.1.0-dev)**: añade los mensajes `fragmento` (agente → nube), con los que un
#: archivo viaja en trozos para que la persona lo descargue (`copy_file`). Un agente de
#: la 1 sigue funcionando: no anuncia esa capacidad y la nube le habla en su versión.
#:
#: **3 (V3.4.0-dev)**: el motor de ejecución. El agente cuenta los cambios de estado de
#: cada orden (`estado`: en cola, en marcha, cancelándose) y la nube puede **cancelar**
#: una orden (`cancelar`) y **preguntar** por ella tras un corte (`consultar` →
#: `consulta`). Con un agente de la 2, la nube no manda nada de eso.
PROTOCOLO_ACTUAL = 3

#: La más antigua que la nube acepta. Por debajo: INCOMPATIBLE, y el saludo se
#: rechaza con el motivo. Entre esta y la actual: OUTDATED, funciona con aviso.
PROTOCOLO_MINIMO = 1

#: Prefijo de la credencial de un agente. Distinto del de los tokens personales
#: (`mgn_`): un escáner de secretos los distingue, y ninguna de las dos vale en el
#: sitio de la otra.
PREFIJO_CREDENCIAL = "mga_"

#: Caracteres de los códigos de emparejamiento: sin los que se confunden al
#: teclearlos (0/O, 1/I/L). 31 símbolos × 8 posiciones: 31⁸ ≈ 8,5 · 10¹¹, unos
#: 40 bits. Con el freno de intentos, adivinar uno vivo es cuestión de siglos.
ALFABETO_CODIGO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
LARGO_CODIGO = 8


#: Lo que puede ocupar un archivo copiado para descargar (decisión mía: 20 MB, el
#: mismo tope que las subidas de la web).
MAX_COPIA = 20 * 1024 * 1024

#: Lo que se lee y se manda de una vez. En base64 crece un tercio (683 KB), muy por
#: debajo del tope de 2 MB por mensaje del canal.
TROZO_COPIA = 512 * 1024


def normalizar_codigo(texto: str | None) -> str:
    """El código como lo guarda la nube: sin guion ni espacios, en mayúsculas."""
    return "".join(c for c in (texto or "").upper() if c.isalnum())


def formatear_codigo(codigo: str) -> str:
    """`K7QF2M9D` → `K7QF-2M9D`, que es como se enseña y se teclea."""
    return f"{codigo[:4]}-{codigo[4:]}"
