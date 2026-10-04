"""
La entrada del agente congelado con PyInstaller (5.0, decisión W2).

`morgan-agente.exe` es el mismo agente de siempre (`python -m src.agente`), sin un Python
aparte: lo lleva dentro. El programa de Windows (Tauri) lo arranca y le pasa las órdenes
(`emparejar`, `conectar`, `ajustes`…). Ver docs/plan-5.0.md.
"""

import sys

from src.agente.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
