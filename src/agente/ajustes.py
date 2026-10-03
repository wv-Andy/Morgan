"""
«Morgan en tu PC»: la ventana de ajustes del agente (4.17, decisión mía: «las dos»).

Hasta ahora, elegir qué carpetas y qué capacidades tiene Morgan en el PC eran órdenes en la
consola (`capacidad activar …`, `carpetas añadir …`): bien para mí, imposible para
cualquiera. Esta ventana hace lo mismo con el ratón, **en el PC**: la regla de la 3.2 sigue
igual, la nube no puede cambiar la política. Lo único que la web puede hacer es pedir que
**se abra** (`abrir_en_segundo_plano`), y aquí decide la persona.

Con lo que ya trae Python (tkinter), sin dependencias nuevas. La lógica (atajos, carpetas,
qué es cada cosa) está separada de la ventana para poder probarla sin abrir nada.
"""

import os
import subprocess
import sys
from pathlib import Path

from src.agente.politica import CAPACIDADES, Politica

#: Cada capacidad, con lo que es en palabras y su color (verde: solo mira o no cambia
#: nada; amarillo: cambia, con un plan que se aprueba; rojo: plan y «Permitir» aquí).
GRUPOS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Mirar", [
        ("list_files", "Ver qué hay en tus carpetas", "verde"),
        ("read_file", "Leer archivos de texto", "verde"),
        ("search_files", "Buscar archivos", "verde"),
        ("file_info", "Ver el tamaño y las fechas de un archivo", "verde"),
        ("copy_file", "Mandarte una copia de un archivo, para descargarla", "verde"),
        ("pc_context", "Ver tus proyectos de código y tus editores", "verde"),
        ("system_info", "Ver el estado del PC: disco, memoria, batería", "verde"),
        ("pc_diagnostics", "Ver qué consume el PC, la red y los puertos", "verde"),
        ("get_processes", "Ver los programas abiertos", "verde"),
    ]),
    ("Cambiar archivos (con un plan que apruebas)", [
        ("create_file", "Crear archivos de texto", "amarillo"),
        ("edit_file", "Editar archivos de texto", "amarillo"),
        ("append_file", "Añadir al final de un archivo", "amarillo"),
        ("create_folder", "Crear carpetas", "amarillo"),
        ("move_file", "Mover y renombrar", "amarillo"),
        ("copy_path", "Copiar dentro del PC", "amarillo"),
        ("compress", "Comprimir y descomprimir .zip", "amarillo"),
        ("delete_file", "Borrar (a la Papelera, con «Permitir» aquí)", "rojo"),
    ]),
    ("Aplicaciones y pantalla", [
        ("open_app", "Abrir aplicaciones, archivos y páginas", "verde"),
        ("notify", "Mandarte avisos a este PC", "verde"),
        ("windows", "Ver y ordenar tus ventanas", "verde"),
        ("clipboard", "El portapapeles (leerlo pide «Permitir»)", "verde"),
        ("screenshot", "Capturas de pantalla (con «Permitir»)", "verde"),
        ("ui_read", "Ver los botones y campos de una ventana", "verde"),
        ("close_app", "Cerrar programas (con «Permitir»)", "rojo"),
        ("ui_control", "Hacer clic y escribir en una ventana (plan y «Permitir»)", "rojo"),
    ]),
    ("Terminal y sistema", [
        ("run_command", "Comandos que solo consultan (git status, ping…)", "verde"),
        ("run_change_command", "Comandos que cambian algo (plan y «Permitir»)", "rojo"),
        ("kill_process", "Terminar un programa (con «Permitir»)", "rojo"),
        ("service_control", "Servicios de Windows (plan y «Permitir»)", "rojo"),
    ]),
]
COLORES = {"verde": "#2e9e5b", "amarillo": "#d9a400", "rojo": "#d64545"}

#: Los tres atajos (decisión mía): solo cambian las capacidades, nunca las carpetas.
ATAJOS = {
    "Solo mirar": {c for c, _, _ in GRUPOS[0][1]},
    "Trabajar con archivos": {c for c, _, _ in GRUPOS[0][1]}
    | {c for c, _, color in GRUPOS[1][1] if color != "rojo"} | {"open_app", "notify"},
    "Todo": set(CAPACIDADES),
}

#: Las carpetas personales que se pueden añadir con un botón.
RAPIDAS = ("documentos", "escritorio", "descargas")


def aplicar_atajo(politica: Politica, atajo: str) -> None:
    encendidas = ATAJOS[atajo]
    for capacidad in CAPACIDADES:
        politica.capacidades[capacidad] = capacidad in encendidas


def anadir_carpeta(politica: Politica, ruta: str, escribir: bool = False) -> str | None:
    """Añade una carpeta (para leer, o para leer y escribir). Devuelve el motivo si no se
    puede, con las mismas reglas que la consola. Una de escritura también se puede leer:
    sin eso, Morgan no podría ver lo que acaba de crear."""
    # Primero si vale para escribir: si no, no se añade ni para leer (antes quedaba a medias;
    # lo cazó su prueba con una carpeta de AppData).
    if escribir and (motivo := Politica._carpeta_de_escritura_valida(ruta)):
        return motivo
    try:
        politica.anadir(ruta)
        if escribir:
            politica.permitir_escritura(ruta)
    except ValueError as exc:
        return str(exc)
    return None


def quitar_carpeta(politica: Politica, ruta: str) -> None:
    """Quitarla de leer la quita también de escribir: escribir sin leer no tiene sentido."""
    politica.quitar(ruta)
    politica.quitar_escritura(ruta)


def filas_de_carpetas(politica: Politica) -> list[tuple[str, str]]:
    """Todas las carpetas, con lo que se puede hacer en cada una: también las que solo son
    de escritura (medido al verla: mi carpeta de escritura, que no estaba en las de leer,
    no salía en la lista)."""
    escritura = {os.path.normcase(c) for c in politica.escritura}
    lectura = {os.path.normcase(c) for c in politica.carpetas}
    filas = [(c, "leer y escribir" if os.path.normcase(c) in escritura else "leer") for c in politica.carpetas]
    filas += [(c, "escribir") for c in politica.escritura if os.path.normcase(c) not in lectura]
    return filas


def carpeta_rapida(nombre: str) -> str | None:
    from src.agente.capacidades import CONOCIDAS, _carpeta_conocida

    return _carpeta_conocida(CONOCIDAS[nombre])


def cuenta_emparejada() -> str:
    from src.agente import estado as almacen

    try:
        datos = almacen.cargar()
    except Exception:
        datos = None
    if not datos:
        return "Este PC todavía no está emparejado con ninguna cuenta."
    return f"Emparejado con la cuenta {datos.cuenta} como «{datos.nombre}»."


def abrir_en_segundo_plano() -> bool:
    """Abre la ventana en otro proceso, sin consola. Lo pide la web (`abrir_ajustes`) y lo
    usa el instalador al acabar. No cambia nada: decide la persona, en la ventana."""
    if os.environ.get("MORGAN_SIN_VENTANAS") == "1":
        return False     # las pruebas: nunca abrir ventanas de verdad
    raiz = Path(__file__).resolve().parents[2]
    ejecutable = Path(sys.executable)
    sin_consola = ejecutable.with_name("pythonw.exe")
    try:
        subprocess.Popen(
            [str(sin_consola if sin_consola.exists() else ejecutable), "-m", "src.agente", "ajustes"],
            cwd=str(raiz), env={**os.environ, "PYTHONPATH": str(raiz)},
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True)
    except OSError:
        return False
    return True


# --- La ventana -----------------------------------------------------------------------------

class Ventana:
    def __init__(self, raiz=None):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.politica = Politica.cargar()
        self.raiz = raiz or tk.Tk()
        self.raiz.title("Morgan en tu PC")
        self.raiz.minsize(620, 560)
        marco = ttk.Frame(self.raiz, padding=14)
        marco.pack(fill="both", expand=True)

        ttk.Label(marco, text="Morgan en tu PC", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(marco, text=cuenta_emparejada(), foreground="#555").pack(anchor="w", pady=(0, 8))
        ttk.Label(marco, wraplength=580, justify="left", text=(
            "Aquí decides qué puede hacer Morgan en este PC. Solo se cambia aquí: ni la web ni "
            "nadie desde fuera puede tocarlo. Lo amarillo y lo rojo, además, se aprueba cada vez.")
        ).pack(anchor="w")

        pestanas = ttk.Notebook(marco)
        pestanas.pack(fill="both", expand=True, pady=10)
        self._pestana_carpetas(ttk.Frame(pestanas, padding=10), pestanas)
        self._pestana_capacidades(ttk.Frame(pestanas, padding=10), pestanas)
        self._pestana_terminal(ttk.Frame(pestanas, padding=10), pestanas)

        abajo = ttk.Frame(marco)
        abajo.pack(fill="x")
        self.aviso = ttk.Label(abajo, text="", foreground="#2e7d32")
        self.aviso.pack(side="left")
        ttk.Button(abajo, text="Cerrar", command=self.cerrar).pack(side="right")
        ttk.Button(abajo, text="Guardar", command=self.guardar).pack(side="right", padx=6)
        self.sin_guardar = False
        self.raiz.protocol("WM_DELETE_WINDOW", self.cerrar)

    # Carpetas
    def _pestana_carpetas(self, hoja, pestanas):
        from tkinter import ttk

        pestanas.add(hoja, text="Carpetas")
        ttk.Label(hoja, wraplength=560, justify="left", text=(
            "Morgan solo ve las carpetas que pongas aquí. En las de «escribir» también puede "
            "crear y cambiar archivos (nunca programas ni scripts, y nunca pisa nada).")).pack(anchor="w")
        self.lista = self.tk.Listbox(hoja, height=9)
        self.lista.pack(fill="both", expand=True, pady=6)
        botones = ttk.Frame(hoja)
        botones.pack(fill="x")
        ttk.Button(botones, text="Añadir para leer…", command=lambda: self._elegir(False)).pack(side="left")
        ttk.Button(botones, text="Añadir para leer y escribir…", command=lambda: self._elegir(True)).pack(side="left", padx=6)
        ttk.Button(botones, text="Quitar", command=self._quitar).pack(side="left")
        rapidas = ttk.Frame(hoja)
        rapidas.pack(fill="x", pady=(8, 0))
        ttk.Label(rapidas, text="Rápido:").pack(side="left")
        for nombre in RAPIDAS:
            ttk.Button(rapidas, text=nombre.capitalize(),
                       command=lambda n=nombre: self._rapida(n)).pack(side="left", padx=3)
        self._pintar_carpetas()

    def _pintar_carpetas(self):
        self.lista.delete(0, "end")
        self._filas = filas_de_carpetas(self.politica)
        for carpeta, marca in self._filas:
            self.lista.insert("end", f"{carpeta}    ({marca})")

    def _elegir(self, escribir: bool):
        from tkinter import filedialog

        ruta = filedialog.askdirectory(parent=self.raiz, title="Elige una carpeta")
        if ruta:
            self._anadir(ruta, escribir)

    def _rapida(self, nombre: str):
        ruta = carpeta_rapida(nombre)
        if ruta:
            self._anadir(ruta, False)

    def _anadir(self, ruta: str, escribir: bool):
        from tkinter import messagebox

        if (motivo := anadir_carpeta(self.politica, ruta, escribir)):
            messagebox.showwarning("No se puede", motivo, parent=self.raiz)
        self._pintar_carpetas()
        self._sin_guardar()

    def _quitar(self):
        seleccion = self.lista.curselection()
        if seleccion:
            quitar_carpeta(self.politica, self._filas[seleccion[0]][0])
            self._pintar_carpetas()
            self._sin_guardar()

    # Capacidades
    def _pestana_capacidades(self, hoja, pestanas):
        from tkinter import ttk

        pestanas.add(hoja, text="Qué puede hacer")
        atajos = ttk.Frame(hoja)
        atajos.pack(fill="x", pady=(0, 6))
        ttk.Label(atajos, text="Atajos:").pack(side="left")
        for atajo in ATAJOS:
            ttk.Button(atajos, text=atajo, command=lambda a=atajo: self._atajo(a)).pack(side="left", padx=3)
        lienzo = self.tk.Canvas(hoja, highlightthickness=0, height=330)
        barra = ttk.Scrollbar(hoja, orient="vertical", command=lienzo.yview)
        dentro = ttk.Frame(lienzo)
        dentro.bind("<Configure>", lambda e: lienzo.configure(scrollregion=lienzo.bbox("all")))
        lienzo.create_window((0, 0), window=dentro, anchor="nw")
        lienzo.configure(yscrollcommand=barra.set)
        lienzo.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        # La rueda del ratón (medido al verla: solo se movía con la barra).
        lienzo.bind_all("<MouseWheel>", lambda e: lienzo.yview_scroll(int(-e.delta / 120), "units"))
        self.casillas = {}
        for titulo, capacidades in GRUPOS:
            ttk.Label(dentro, text=titulo, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 2))
            for capacidad, texto, color in capacidades:
                fila = ttk.Frame(dentro)
                fila.pack(anchor="w")
                self.tk.Label(fila, text="●", fg=COLORES[color]).pack(side="left")
                variable = self.tk.BooleanVar(value=bool(self.politica.capacidades.get(capacidad)))
                ttk.Checkbutton(fila, text=texto, variable=variable,
                                command=lambda c=capacidad, v=variable: self._cambiar(c, v)).pack(side="left")
                self.casillas[capacidad] = variable

    def _cambiar(self, capacidad, variable):
        self.politica.capacidades[capacidad] = bool(variable.get())
        self._sin_guardar()

    def _atajo(self, atajo: str):
        aplicar_atajo(self.politica, atajo)
        for capacidad, variable in self.casillas.items():
            variable.set(bool(self.politica.capacidades.get(capacidad)))
        self._sin_guardar()

    # Terminal
    def _pestana_terminal(self, hoja, pestanas):
        from tkinter import ttk

        from src.agente.terminal import CATALOGO

        pestanas.add(hoja, text="Programas")
        ttk.Label(hoja, wraplength=560, justify="left", text=(
            "Los programas que Morgan puede usar desde la terminal (nunca PowerShell ni CMD). "
            "Además hace falta encender «Comandos…» en «Qué puede hacer».")).pack(anchor="w", pady=(0, 6))
        self.programas = {}
        for nombre in sorted(CATALOGO):
            variable = self.tk.BooleanVar(value=nombre in self.politica.programas)
            ttk.Checkbutton(hoja, text=nombre, variable=variable,
                            command=lambda n=nombre, v=variable: self._programa(n, v)).pack(anchor="w")
            self.programas[nombre] = variable

    def _programa(self, nombre, variable):
        if variable.get() and nombre not in self.politica.programas:
            self.politica.programas.append(nombre)
        elif not variable.get():
            self.politica.programas = [p for p in self.politica.programas if p != nombre]
        self._sin_guardar()

    def _sin_guardar(self):
        self.sin_guardar = True
        self.aviso.configure(text="Sin guardar", foreground="#b26a00")

    def guardar(self):
        self.politica.guardar()
        self.sin_guardar = False
        self.aviso.configure(text="Guardado: Morgan ya lo sabe.", foreground="#2e7d32")

    def cerrar(self, preguntar=None):
        """Con cambios sin guardar, pregunta: guardar, tirarlos o seguir aquí."""
        if self.sin_guardar:
            from tkinter import messagebox

            respuesta = (preguntar or messagebox.askyesnocancel)(
                "Morgan en tu PC", "Hay cambios sin guardar. ¿Guardarlos antes de cerrar?")
            if respuesta is None:
                return
            if respuesta:
                self.guardar()
        self.raiz.destroy()


def abrir() -> int:
    try:
        ventana = Ventana()
    except Exception as exc:          # sin tkinter, o sin pantalla
        print(f"No se pudo abrir la ventana ({exc}). Usa las órdenes de la consola.")
        return 2
    ventana.raiz.mainloop()
    return 0
