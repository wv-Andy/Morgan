"""
El agente local desde la consola (3.0-C y 3.0-D).

    .\\venv\\Scripts\\python.exe -m src.agente emparejar --nube https://morgan-ia-2-0.onrender.com
    .\\venv\\Scripts\\python.exe -m src.agente conectar
    .\\venv\\Scripts\\python.exe -m src.agente estado
    .\\venv\\Scripts\\python.exe -m src.agente desemparejar
    .\\venv\\Scripts\\python.exe -m src.agente carpetas
    .\\venv\\Scripts\\python.exe -m src.agente carpetas añadir "C:\\Users\\ana\\Documentos\\Proyecto"
    .\\venv\\Scripts\\python.exe -m src.agente carpetas quitar "C:\\Users\\ana\\Documentos\\Proyecto"
    .\\venv\\Scripts\\python.exe -m src.agente escritura añadir "C:\\Users\\ana\\Documentos\\Notas"
    .\\venv\\Scripts\\python.exe -m src.agente capacidad activar create_file
    .\\venv\\Scripts\\python.exe -m src.agente confirmar borrar si
    .\\venv\\Scripts\\python.exe -m src.agente programa activar git
    .\\venv\\Scripts\\python.exe -m src.agente arranque activar
    .\\venv\\Scripts\\python.exe -m src.agente arranque desactivar
    .\\venv\\Scripts\\python.exe -m src.agente parar
    python -m src.agente actualizar
    python -m src.agente volver
    python -m src.agente version

Las carpetas son la política local: qué puede leer Morgan en este PC. Solo se cambian
aquí; la nube no puede tocarlas.

Escribir (3.3) va aparte: sus propias carpetas (`escritura`), vacías al empezar, y cada
capacidad de escritura nace apagada (`capacidad activar create_file`, `edit_file`,
`append_file`, `create_folder`, `move_file`, `delete_file`). `confirmar` dice qué operaciones piden
permiso con una notificación en este PC; por defecto, borrar, ejecutar y terminar.

La terminal (3.5) es un catálogo cerrado de programas, sin PowerShell: se encienden
las capacidades (`run_command`, `run_change_command`, `get_processes`, `kill_process`) y,
aparte, cada programa (`programa activar git`). Todo nace apagado.

`conectar` mantiene el canal con la nube (3.0-D) hasta Ctrl+C o `parar`.

`actualizar` (3.8) instala la última versión publicada, **si la firmé** y es más
nueva: la prueba, cambia a ella y, si no arranca sana en 2 minutos, vuelve sola a la
anterior. `volver` vuelve a la anterior a mano. Solo en un agente instalado; el que corre
desde una copia del código se actualiza con git.

`arranque activar` hace que el agente se abra solo, sin ventana, cada vez que se inicia
sesión en Windows, y lo arranca ya: con el PC encendido y con internet, Morgan tiene
acceso. Solo corre un agente a la vez; el que va sin ventana escribe en `agente.log`,
en la carpeta del agente.
"""

import argparse
import os
import socket
import sys
from pathlib import Path

from src.agente import estado as almacen
from src.agente.emparejar import EmparejamientoFallido, desemparejar, emparejar


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="python -m src.agente", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    ordenes = parser.add_subparsers(dest="orden", required=True)
    p = ordenes.add_parser("emparejar", help="emparejar este PC con tu cuenta")
    # Directo a Render, no a través de Vercel: el canal del agente dura horas y el
    # proxy de Vercel no está hecho para eso (contrato §7).
    # `MORGAN_NUBE` (5.0): otra nube, para probar el programa contra morgan-carga sin tocar
    # producción, o para quien tenga su propio Morgan.
    p.add_argument("--nube", default=os.environ.get("MORGAN_NUBE") or "https://morgan-ia-2-0.onrender.com")
    p.add_argument("--nombre", default=socket.gethostname(), help="cómo se verá en la web")
    p.add_argument("--codigo", help="el código de la web (si no, se pregunta)")
    # La ventana del programa de Windows (5.0) no tiene consola: primero pregunta de qué
    # cuenta es el código (`--consultar`, sin usarlo), lo enseña, y si la persona dice que sí,
    # empareja con `--si`. La confirmación sigue: la hace la ventana.
    p.add_argument("--consultar", action="store_true", help="solo decir de qué cuenta es el código")
    p.add_argument("--si", action="store_true", help="sin preguntar (ya se confirmó la cuenta)")
    ordenes.add_parser("conectar", help="conectar con la nube y atender peticiones (Ctrl+C para parar)")
    ordenes.add_parser("estado", help="si este PC está emparejado, y con quién")
    c = ordenes.add_parser("carpetas", help="ver, añadir o quitar las carpetas que Morgan puede leer")
    c.add_argument("accion", nargs="?", choices=["añadir", "anadir", "quitar"])
    c.add_argument("ruta", nargs="?")
    e = ordenes.add_parser("escritura", help="ver, añadir o quitar las carpetas donde Morgan puede escribir")
    e.add_argument("accion", nargs="?", choices=["añadir", "anadir", "quitar"])
    e.add_argument("ruta", nargs="?")
    conf = ordenes.add_parser("confirmar", help="qué operaciones se confirman con una notificación en este PC")
    conf.add_argument("operacion", nargs="?", choices=["escribir", "borrar", "ejecutar", "terminar", "abrir"])
    prog = ordenes.add_parser("programa", help="encender o apagar un programa del catálogo de la terminal")
    prog.add_argument("accion", nargs="?", choices=["activar", "desactivar"])
    prog.add_argument("nombre", nargs="?")
    conf.add_argument("valor", nargs="?", choices=["si", "sí", "no"])
    b = ordenes.add_parser("bloquear", help="prohibir una carpeta aunque esté dentro de una permitida")
    b.add_argument("ruta")
    d = ordenes.add_parser("desbloquear", help="quitar una carpeta de las prohibidas")
    d.add_argument("ruta")
    cap = ordenes.add_parser("capacidad", help="encender o apagar lo que Morgan puede hacer aquí")
    cap.add_argument("accion", nargs="?", choices=["activar", "desactivar"])
    cap.add_argument("nombre", nargs="?")
    lim = ordenes.add_parser("limite", help="bajar lo que puede salir de este PC")
    lim.add_argument("cual", nargs="?", choices=["lectura", "copia"])
    lim.add_argument("valor", nargs="?", help="lectura en KB, copia en MB")
    ordenes.add_parser("politica", help="ver entera la política de este PC")
    ordenes.add_parser("desemparejar", help="olvidar la credencial y revocarla en la nube")
    a = ordenes.add_parser("arranque", help="que el agente se abra solo al iniciar sesión en Windows")
    a.add_argument("accion", nargs="?", choices=["activar", "desactivar", "estado"], default="estado")
    ordenes.add_parser("parar", help="parar el agente que está en marcha (también el automático)")
    ordenes.add_parser("vigilar", help="lanzar el agente y levantarlo si se cae (lo usa el arranque automático)")
    ordenes.add_parser("cruzar", help="comparar lo que la nube apuntó de este PC con su diario")
    act = ordenes.add_parser("actualizar", help="instalar la última versión del agente, si está firmada por el autor de Morgan")
    act.add_argument("--si", action="store_true", help="sin preguntar (lo usa la notificación)")
    ordenes.add_parser("volver", help="volver a la versión anterior del agente")
    ordenes.add_parser("version", help="la versión de este agente")
    ordenes.add_parser("ajustes", help="abrir la ventana «Morgan en tu PC»: carpetas y lo que puede hacer")
    ordenes.add_parser("rotar", help="cambiar ya la credencial de este PC (sola, cada 90 días)")
    ins = ordenes.add_parser("instalar", help="instalar el agente en este PC (lo usa el instalador de la web)")
    ins.add_argument("--nube")
    ins.add_argument("--codigo", help="el código de la web; no hace falta si este PC ya está emparejado")
    ins.add_argument("--paquete", help="el paquete que bajó el instalador")
    ins.add_argument("--nombre", default=socket.gethostname(), help="cómo se verá en la web")
    des = ordenes.add_parser("desinstalar", help="quitar el agente de este PC (quedan los respaldos)")
    des.add_argument("--si", action="store_true", help="sin preguntar")
    args = parser.parse_args(argv)

    try:
        if args.orden == "emparejar":
            codigo = args.codigo or input("Código de la web (Ajustes → Tu equipo): ")
            if args.consultar:
                from src.agente.emparejar import consultar

                print(f"CUENTA: {consultar(args.nube, codigo)}")
                return 0
            preguntar = (lambda _texto: "s") if args.si else input
            return 0 if emparejar(args.nube, codigo, args.nombre, preguntar=preguntar) else 1
        if args.orden == "desemparejar":
            desemparejar()
            return 0
        if args.orden == "conectar":
            return _conectar()
        if args.orden == "carpetas":
            return _carpetas(args.accion, args.ruta)
        if args.orden == "escritura":
            return _escritura(args.accion, args.ruta)
        if args.orden == "confirmar":
            return _confirmar(args.operacion, args.valor)
        if args.orden == "programa":
            return _programa(args.accion, args.nombre)
        if args.orden in ("bloquear", "desbloquear"):
            return _bloquear(args.orden, args.ruta)
        if args.orden == "capacidad":
            return _capacidad(args.accion, args.nombre)
        if args.orden == "limite":
            return _limite(args.cual, args.valor)
        if args.orden == "politica":
            return _politica()
        if args.orden == "arranque":
            return _arranque(args.accion)
        if args.orden == "parar":
            return _parar()
        if args.orden == "vigilar":
            return _vigilar()
        if args.orden == "cruzar":
            from src.agente.cruzar import cruzar

            return cruzar()
        if args.orden == "ajustes":
            from src.agente import ajustes

            return ajustes.abrir()
        if args.orden == "version":
            from src import __version__

            print(__version__)
            return 0
        if args.orden == "actualizar":
            from src.agente import instalacion

            if sys.stdout is None:
                from src.agente import arranque

                sys.stdout = sys.stderr = arranque.abrir_registro()
            preguntar = None if args.si else (
                lambda texto: input(f"{texto} [s/N] ").strip().lower() in ("s", "si", "sí"))
            return instalacion.actualizar(preguntar=preguntar)
        if args.orden in ("volver", "instalar"):
            from src.agente import arranque

            if arranque.congelado():
                from src.agente.instalacion import PROGRAMA_DE_WINDOWS

                print(PROGRAMA_DE_WINDOWS)
                return 2
        if args.orden == "volver":
            return _volver()
        if args.orden == "rotar":
            return _rotar()
        if args.orden == "instalar":
            from src.agente import instalacion

            return instalacion.instalar(nube=args.nube, codigo=args.codigo, nombre=args.nombre,
                                        paquete=Path(args.paquete) if args.paquete else None)
        if args.orden == "desinstalar":
            from src.agente import instalacion

            if not args.si and input(
                    "Se desempareja este PC, se quita del inicio de Windows y se borra el agente "
                    "(menos los respaldos de tus archivos). ¿Seguir? [s/N] ").strip().lower() not in ("s", "si", "sí"):
                print("No se desinstala.")
                return 0
            return instalacion.desinstalar()
        datos = almacen.cargar()
        print(f"Estado: {almacen.estado().value}")
        if datos:
            print(f"  {datos.nombre} ({datos.agent_id}) · cuenta {datos.cuenta} · {datos.nube}")
        _contar_marcha()
        return 0
    except EmparejamientoFallido as exc:
        print(f"No se pudo: {exc}")
        return 2


def _carpetas(accion: str | None, ruta: str | None) -> int:
    from src.agente.politica import Politica

    politica = Politica.cargar()
    if accion in ("añadir", "anadir", "quitar") and not ruta:
        print("Falta la carpeta.")
        return 2
    if accion in ("añadir", "anadir"):
        try:
            print(f"Permitida: {politica.anadir(ruta)}")
        except ValueError as exc:
            print(f"No se permite: {exc}")
            return 2
        politica.guardar()
    elif accion == "quitar":
        print("Quitada." if politica.quitar(ruta) else "No estaba permitida.")
        politica.guardar()
    return _politica()


def _escritura(accion: str | None, ruta: str | None) -> int:
    """Dónde puede escribir Morgan (3.3): una lista aparte de la de leer."""
    from src.agente.politica import Politica

    politica = Politica.cargar()
    if accion and not ruta:
        print("Falta la carpeta.")
        return 2
    if accion in ("añadir", "anadir"):
        try:
            print(f"Morgan puede escribir en: {politica.permitir_escritura(ruta)}")
        except ValueError as exc:
            print(f"No se permite: {exc}")
            return 2
        politica.guardar()
    elif accion == "quitar":
        print("Quitada." if politica.quitar_escritura(ruta) else "No estaba permitida.")
        politica.guardar()
    return _politica()


def _confirmar(operacion: str | None, valor: str | None) -> int:
    from src.agente.politica import Politica

    politica = Politica.cargar()
    if operacion and valor:
        politica.confirmar[operacion] = valor != "no"
        politica.guardar()
    elif operacion or valor:
        print("Hace falta la operación y sí o no: confirmar borrar si")
        return 2
    return _politica()


def _programa(accion: str | None, nombre: str | None) -> int:
    """Los programas del catálogo de la terminal (3.5): se encienden uno a uno."""
    from src.agente.politica import Politica
    from src.agente.terminal import CATALOGO

    politica = Politica.cargar()
    if accion and nombre:
        nombre = nombre.lower()
        if nombre not in CATALOGO:
            print(f"«{nombre}» no está en el catálogo. Los hay: {', '.join(sorted(CATALOGO))}.")
            return 2
        if accion == "activar" and nombre not in politica.programas:
            politica.programas.append(nombre)
        elif accion == "desactivar":
            politica.programas = [p for p in politica.programas if p != nombre]
        politica.guardar()
        print(f"{nombre}: {'encendido' if accion == 'activar' else 'apagado'}.")
    elif accion or nombre:
        print("Hace falta la acción y el nombre: programa activar git")
        return 2
    return _politica()


def _bloquear(orden: str, ruta: str) -> int:
    """Lo bloqueado gana sobre lo permitido (3.2, decisión mía)."""
    from src.agente.politica import Politica

    politica = Politica.cargar()
    if orden == "bloquear":
        try:
            print(f"Bloqueada: {politica.bloquear(ruta)}")
        except ValueError as exc:
            print(f"No se pudo: {exc}")
            return 2
    else:
        print("Desbloqueada." if politica.desbloquear(ruta) else "No estaba bloqueada.")
    politica.guardar()
    return _politica()


def _capacidad(accion: str | None, nombre: str | None) -> int:
    from src.agente.politica import CAPACIDADES, Politica

    politica = Politica.cargar()
    if accion and nombre:
        if nombre not in CAPACIDADES:
            print(f"No existe «{nombre}». Las hay: {', '.join(CAPACIDADES)}.")
            return 2
        politica.capacidades[nombre] = accion == "activar"
        politica.guardar()
        print(f"{nombre}: {'encendida' if accion == 'activar' else 'apagada'}.")
    elif accion or nombre:
        print("Hace falta la acción y el nombre: capacidad activar read_file")
        return 2
    return _politica()


def _limite(cual: str | None, valor: str | None) -> int:
    from src.agente.politica import Politica

    politica = Politica.cargar()
    if cual and valor:
        try:
            pedido = int(valor)
        except ValueError:
            print("El valor es un número: lectura en KB, copia en MB.")
            return 2
        if cual == "lectura":
            politica.lectura_bytes = pedido * 1024
        else:
            politica.copia_bytes = pedido * 1024 * 1024
        politica.guardar()
        # Se vuelve a cargar para enseñar lo que de verdad quedó: los topes del código
        # mandan, y un valor por encima se recorta al guardarlo.
        politica = Politica.cargar()
        quedó = politica.lectura_bytes // 1024 if cual == "lectura" else politica.copia_bytes // (1024 * 1024)
        if quedó != pedido:
            print(f"Pedido {pedido}; queda en {quedó}: solo se puede bajar del tope del programa.")
    elif cual or valor:
        print("Hace falta cuál y cuánto: limite lectura 64")
        return 2
    return _politica()


def _politica() -> int:
    """Todo lo que este PC deja hacer, de un vistazo."""
    from src.agente.politica import CAPACIDADES, Politica

    politica = Politica.cargar()
    print("Carpetas que Morgan puede leer en este PC:" if politica.carpetas else
          "Ninguna carpeta permitida: Morgan no puede leer nada de este PC.")
    for carpeta in politica.carpetas:
        print(f"  {carpeta}")
    if politica.bloqueadas:
        print("Bloqueadas (ganan sobre lo permitido):")
        for carpeta in politica.bloqueadas:
            print(f"  {carpeta}")
    print("Carpetas donde Morgan puede escribir:" if politica.escritura else
          "Ninguna carpeta de escritura: Morgan no puede cambiar nada de este PC.")
    for carpeta in politica.escritura:
        print(f"  {carpeta}")
    print("Capacidades: " + ", ".join(
        f"{c}{'' if politica.capacidades.get(c) else ' (apagada)'}" for c in CAPACIDADES))
    from src.agente.terminal import CATALOGO

    print("Programas de la terminal: " + ", ".join(
        f"{p}{'' if p in politica.programas else ' (apagado)'}" for p in sorted(CATALOGO)))
    pide = [op for op, si in politica.confirmar.items() if si]
    print("Se confirma con una notificación en este PC: " + (", ".join(pide) or "nada"))
    print(f"Límites: lectura {politica.lectura_bytes // 1024} KB · "
          f"copia {(politica.copia_bytes or 0) // (1024 * 1024)} MB")
    print("La nube no puede cambiar nada de esto: solo se cambia aquí.")
    return 0


def _contar_marcha() -> None:
    from src.agente import arranque, salud

    marcha = arranque.en_marcha()
    print("En marcha ahora: " + ("sí" if marcha else "no")
          + (" (con su vigilante)" if arranque.en_marcha("vigilante.lock") else ""))
    print("Salud: " + salud.diagnostico(en_marcha=marcha))
    print("Se abre solo al iniciar sesión: " + ("sí" if arranque.activado() else "no"))


def _arranque(accion: str) -> int:
    from src.agente import arranque

    if accion == "activar":
        if almacen.cargar() is None or almacen.credencial() is None:
            print("Este PC no está emparejado. Primero: python -m src.agente emparejar")
            return 2
        print(f"Activado: {arranque.activar()}")
        if not arranque.en_marcha():
            arranque.lanzar_en_segundo_plano()
            # Tarda un instante en coger el candado: sin esperar, diría «en marcha: no».
            import time

            limite = time.monotonic() + 10
            while not arranque.en_marcha() and time.monotonic() < limite:
                time.sleep(0.2)
            print("Arrancado en segundo plano. Su registro: " + str(almacen.carpeta() / "agente.log"))
    elif accion == "desactivar":
        print("Desactivado." if arranque.desactivar() else "No estaba activado.")
        if arranque.en_marcha():
            print("El agente sigue en marcha hasta que lo pares: python -m src.agente parar")
    _contar_marcha()
    return 0


def _parar() -> int:
    from src.agente import arranque

    if not arranque.en_marcha() and not arranque.en_marcha("vigilante.lock"):
        print("No hay ningún agente en marcha.")
        return 0
    # La señal la ve el agente (que acaba con 0 y el vigilante no lo levanta) y, si el
    # agente estaba caído, el vigilante antes de levantarlo (3.6).
    arranque.pedir_parada()
    # Se espera a que se haya ido (3.8): `parar` y enseguida `arranque activar` no
    # relanzaba nada, porque `activar` aún veía al viejo cerrándose (3.7.5).
    if arranque.esperar_a_que_pare():
        print("Parado.")
        return 0
    print(f"Pedido, pero sigue en marcha tras {int(arranque.ESPERA_PARADA)} s. "
          f"Mira su registro: {almacen.carpeta() / 'agente.log'}")
    return 1


def _rotar() -> int:
    """Rotar a mano (3.8). Con el agente en marcha, se para, se rota y se relanza: el que
    corre tiene la credencial vieja en memoria."""
    from src.agente import arranque
    from src.agente.emparejar import rotar_credencial

    if almacen.cargar() is None or almacen.credencial() is None:
        print("Este PC no está emparejado.")
        return 2
    corria = arranque.en_marcha() or arranque.en_marcha("vigilante.lock")
    if corria:
        arranque.pedir_parada()
        arranque.esperar_a_que_pare()
    try:
        rotar_credencial(almacen.credencial())
        print("Credencial nueva guardada. Se estrena al conectar: entonces la vieja deja de valer.")
        codigo = 0
    except Exception as exc:
        print(f"No se pudo rotar ({type(exc).__name__}): sigue la de antes.")
        codigo = 1
    if corria:
        arranque.lanzar_en_segundo_plano()
    return codigo


def _volver() -> int:
    from src.agente import arranque, instalacion

    if not instalacion.instalada():
        print("Este agente corre desde una copia del código: no hay versiones a las que volver.")
        return 2
    arranque.pedir_parada()
    arranque.esperar_a_que_pare()
    anterior = instalacion.volver_atras("pedido a mano")
    if anterior is None:
        print("No hay versión anterior guardada.")
        return 1
    arranque.lanzar_en_segundo_plano(instalacion.python_de(anterior), instalacion.carpeta_de(anterior))
    print(f"Vuelto a la {anterior}.")
    return 0


def _vigilar() -> int:
    """El vigilante (3.6): lanza el agente y lo levanta si se cae o se atasca."""
    from src.agente import arranque, vigilante

    if sys.stdout is None:
        sys.stdout = sys.stderr = arranque.abrir_registro()
    import time

    # Unos segundos de espera por si es el relevo de uno que se va (3.8.5: al volver a la
    # versión anterior, el vigilante que se va lanza el suyo antes de soltar el candado).
    limite = time.monotonic() + 15
    while True:
        try:
            candado = arranque.Candado("vigilante.lock").tomar()
            break
        except arranque.YaEnMarcha:
            if time.monotonic() > limite:
                print("Ya hay un vigilante en marcha en este PC.")
                return 4
            time.sleep(0.5)
    try:
        return vigilante.vigilar()
    finally:
        candado.soltar()


def _conectar() -> int:
    import asyncio
    import threading

    from src.agente import arranque
    from src.agente.canal import Canal
    from src.agente.ejecutor import Ejecutor

    if sys.stdout is None:
        # Sin ventana (pythonw, el arranque automático): lo que se imprimiría va al registro.
        sys.stdout = sys.stderr = arranque.abrir_registro()

    datos = almacen.cargar()
    credencial = almacen.credencial()
    if datos is None or credencial is None:
        print("Este PC no está emparejado. Primero: python -m src.agente emparejar")
        return 2
    try:
        candado = arranque.Candado().tomar()
    except arranque.YaEnMarcha:
        # Dos agentes con la misma credencial se echarían uno al otro sin parar (4410).
        print("Ya hay un agente en marcha en este PC; este no arranca. Para pararlo: python -m src.agente parar")
        return 4
    # Una señal de parada que quedase de antes no debe parar a este nada más nacer.
    arranque.senal_de_parada().unlink(missing_ok=True)

    from src import __version__
    from src.agente import instalacion

    def al_cambiar(estado, detalle):
        print(f"[{__import__('time').strftime('%H:%M:%S')}] {estado.value} {detalle}".rstrip(), flush=True)
        if estado.value == "READY" and instalacion.confirmar_si_pendiente(__version__):
            # Una actualización que nadie llegó a comprobar (el PC se apagó a mitad): llegar
            # a READY es lo que se le pedía (3.8).
            print(f"Versión {__version__} confirmada: llegó a sana.", flush=True)

    from src.agente import capacidades
    from src.agente.politica import Politica

    from src.agente.emparejar import rotar_credencial

    canal = Canal(datos.nube, credencial, Ejecutor(datos.agent_id), al_cambiar=al_cambiar,
                  revisar_capacidades=lambda: (Politica.firma(), capacidades.disponibles()),
                  rotar_credencial=rotar_credencial)
    print(f"Conectando «{datos.nombre}» con {datos.nube} (Ctrl+C para parar)…")
    terminado = threading.Event()
    threading.Thread(target=arranque.vigilar_parada, args=(canal.parar,),
                     kwargs={"hasta": terminado.is_set}, daemon=True).start()
    # Si hay versión nueva firmada, se pregunta con una notificación (3.8).
    threading.Thread(target=instalacion.mirar_novedades, args=(terminado.is_set,), daemon=True).start()
    try:
        final = asyncio.run(canal.correr())
    except KeyboardInterrupt:
        print("Parado.")
        return 0
    finally:
        terminado.set()
        candado.soltar()
    if final.value in ("REVOKED", "INCOMPATIBLE"):
        print("La nube no acepta este agente: revocado o desactualizado. Vuelve a emparejarlo o actualízalo.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
