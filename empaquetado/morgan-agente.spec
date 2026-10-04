# -*- mode: python ; coding: utf-8 -*-
# El agente de Morgan congelado (5.0, decisión W2): `pyinstaller empaquetado/morgan-agente.spec`.
# Una carpeta (onedir) y no un solo .exe: arranca más rápido y los antivirus lo marcan menos.

from PyInstaller.utils.hooks import collect_submodules

ocultos = (
    collect_submodules("src.agente")
    + collect_submodules("winrt")
    + ["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox"]
)

a = Analysis(
    ["agente_congelado.py"],
    pathex=[".."],
    hiddenimports=ocultos,
    # Lo de la nube no va en el PC: ni la API, ni los modelos, ni la memoria.
    excludes=["fastapi", "uvicorn", "starlette", "groq", "google", "pypdf", "rich.console", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
# Dos ejecutables sobre los mismos ficheros: el de las órdenes (con consola, para que el
# programa lea lo que dice) y el de fondo (sin ventana: vigilar, conectar, los ajustes). Ver
# `src/agente/arranque.py::orden`.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="morgan-agente", console=True)
exe_fondo = EXE(pyz, a.scripts, [], exclude_binaries=True, name="morgan-agente-fondo", console=False)
coll = COLLECT(exe, exe_fondo, a.binaries, a.datas, name="morgan-agente")
