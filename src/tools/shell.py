"""
Utilidades compartidas para la ejecución de comandos en PowerShell.

Centraliza los detalles que, si se olvidan, producen salidas ilegibles o comandos
que no llegan a ejecutarse:

- PowerShell escribe su salida en la página de códigos de la consola (cp1252 en
  español), mientras que Morgan la decodifica como UTF-8. Sin forzar la
  codificación, los acentos llegan al modelo como caracteres de reemplazo.
- Una ruta entrecomillada es una cadena literal para PowerShell: necesita el
  operador de llamada '&' para ejecutarse.
"""

# Prefijo que fuerza a PowerShell a emitir UTF-8 antes de ejecutar el comando.
_UTF8_PREAMBLE = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "


def ps_command(command: str) -> str:
    """Prepara un comando de PowerShell forzando la salida en UTF-8."""
    return f"{_UTF8_PREAMBLE}{command}"


def ps_args(command: str) -> list[str]:
    """Devuelve la lista de argumentos completa para invocar PowerShell de forma no interactiva."""
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        ps_command(command),
    ]
