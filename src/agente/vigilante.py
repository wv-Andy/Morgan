"""
El vigilante del agente (3.6): lo levanta si se cae y lo reinicia si se atasca.

Antes, si el proceso del agente moría, nadie lo levantaba hasta el siguiente inicio de
sesión: Morgan se quedaba horas sin el PC. Y si se colgaba (vivo, pero sin atender), la
nube lo daba por muerto y él no se enteraba.

**Decisión mía (2026-09-25): siempre.** El acceso directo de Inicio lanza el
vigilante, y el vigilante lanza el agente (`conectar`) y lo mira:

- **Si se cae**, lo vuelve a lanzar, con una espera que crece (2, 4, 8… hasta 30 s).
- **Si se atasca** (más de 2 minutos sin pulso en `salud.json`), lo termina y lo lanza.
- **Nunca** si acabó bien: la persona lo paró (`parar`, código 0), no está emparejado
  (2), la nube lo revocó o está desactualizado (3), o ya había otro en marcha (4).
- **Freno**: más de 5 caídas en 10 minutos y se rinde, y lo deja dicho en la salud y en
  el registro. Un agente que se cae en bucle no se arregla relanzándolo.
"""

import subprocess
import sys
import time
from collections import deque
from typing import Callable

from src.agente import arranque, salud

#: Si una vuelta del vigilante dura esto de más, el PC estuvo suspendido (ver `vigilar`).
SUSPENDIDO = 30.0
CAIDAS_MAXIMAS = 5
VENTANA_CAIDAS = 600.0
CADA = 5.0
#: Lo que tiene una versión recién instalada, y aún por confirmar, para llegar a sana
#: antes de que su propio vigilante vuelva a la anterior (3.8.5). Más que el plazo de
#: `actualizar` (120 s): si ese proceso sigue vivo, juzga él primero.
PLAZO_ESTRENO = 180.0
#: Código del vigilante que volvió a la versión anterior (y la dejó lanzada).
VUELTA_ATRAS = 6
#: Código del vigilante que no era la versión activa y le pasó el relevo.
RELEVO = 7

#: Códigos del agente que significan «no me levantes»: ver `__main__._conectar`.
FINALES = {0: "parado", 2: "sin emparejar", 3: "revocado o desactualizado", 4: "ya había otro"}


def _anotar(texto: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] vigilante: {texto}", flush=True)


def _terminar_arbol(hijo) -> None:
    """El lanzador y lo que lanzó: si solo se mata al lanzador, el agente de verdad podría
    seguir vivo con el candado, y el siguiente no arrancaría."""
    try:
        import psutil

        for nieto in psutil.Process(hijo.pid).children(recursive=True):
            try:
                nieto.kill()
            except psutil.Error:
                pass
    except Exception:
        pass
    hijo.kill()


def lanzar_agente() -> subprocess.Popen:
    banderas = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen([str(arranque.pythonw()), "-m", "src.agente", "conectar"],
                            cwd=str(arranque.raiz_del_proyecto()), creationflags=banderas,
                            stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr)


def _por_confirmar() -> bool:
    """Si esta versión es una instalada que acaba de estrenarse y aún no llegó a sana."""
    try:
        from src import __version__
        from src.agente import instalacion

        if not instalacion.instalada():
            return False
        activa = instalacion.leer_activa()
        return activa.get("version") == __version__ and bool(activa.get("pendiente"))
    except Exception:
        return False


def _volver_atras(motivo: str) -> int | None:
    """Vuelve a la versión anterior y deja lanzado su vigilante. None si no hay anterior."""
    from src.agente import instalacion

    anterior = instalacion.volver_atras(motivo)
    if anterior is None:
        return None
    _anotar(f"{motivo}: se vuelve a la {anterior}")
    arranque.lanzar_en_segundo_plano(instalacion.python_de(anterior), instalacion.carpeta_de(anterior))
    return VUELTA_ATRAS


def _relevo_a_la_activa() -> int | None:
    """Si este no es el código de la versión activa, le pasa el relevo: repara el acceso
    directo de Inicio y lanza el vigilante de la activa. `activa.json` manda.

    Medido en la evaluación de la 3.8.5: un apagón entre escribir `activa.json` y
    reescribir el acceso directo (PowerShell, un segundo) dejaba arrancando la versión de
    antes mientras la activa era otra, por confirmar, y nadie lo arreglaba."""
    try:
        from src import __version__
        from src.agente import instalacion

        if not instalacion.instalada():
            return None
        activa = instalacion.leer_activa().get("version")
        if not activa or activa == __version__ or not instalacion.python_de(activa).exists():
            return None
    except Exception:
        return None
    _anotar(f"la versión activa es la {activa}, no esta ({__version__}): se le pasa el relevo")
    if arranque.activado():
        arranque.activar(instalacion.python_de(activa), instalacion.carpeta_de(activa))
    arranque.lanzar_en_segundo_plano(instalacion.python_de(activa), instalacion.carpeta_de(activa))
    return RELEVO


def vigilar(lanzar: Callable[[], subprocess.Popen] = lanzar_agente,
            dormir: Callable[[float], None] = time.sleep,
            ahora: Callable[[], float] = time.time,
            por_confirmar: Callable[[], bool] = _por_confirmar,
            volver_atras: Callable[[str], int | None] = _volver_atras,
            relevo: Callable[[], int | None] = _relevo_a_la_activa) -> int:
    """Hasta que el agente acabe bien o se rinda. Devuelve el código con que termina.

    **Una versión por confirmar se juzga aquí también** (3.8.5): normalmente la juzga
    `actualizar`, desde la versión anterior; pero si el PC se apaga a mitad, nadie lo haría
    y una versión que no conecta se quedaría. Si en `PLAZO_ESTRENO` no llega a sana (el
    agente la confirma al llegar a READY), o se rinde por caídas, se vuelve a la anterior."""
    codigo = relevo()
    if codigo is not None:
        return codigo
    caidas: deque = deque()
    estreno = ahora() if por_confirmar() else None
    while True:
        if arranque.senal_de_parada().exists():
            # La persona pidió parar mientras el agente estaba caído: no se levanta.
            arranque.senal_de_parada().unlink(missing_ok=True)
            _anotar("parado por la persona")
            return 0
        hijo = lanzar()
        lanzado = ahora()
        _anotar(f"agente lanzado (pid {hijo.pid})")
        atascado = False
        # Desde cuándo se le da margen al agente: su lanzamiento, o el último despertar.
        margen_desde = lanzado
        ultima_buena: dict = {}
        while hijo.poll() is None:
            antes = ahora()
            dormir(CADA)
            if ahora() - antes > CADA + SUSPENDIDO:
                # El vigilante también estuvo parado: el PC se suspendió. Medido en mi
                # PC el 2026-09-25 (3.6.5): dos suspensiones de 5 y 80 minutos, y al
                # despertar el vigilante reiniciaba un agente que no estaba atascado, solo
                # congelado con todo lo demás (su último pulso tenía 276 y 4.770 s). Se le
                # da un margen nuevo para latir antes de juzgarlo.
                _anotar(f"el PC estuvo suspendido {int(ahora() - antes)} s: se le da margen al agente")
                margen_desde = ahora()
                if estreno is not None:
                    # Tampoco cuenta para el plazo de una versión por confirmar (auditoría de la
                    # 3.x): si no, al despertar se volvería atrás desde una versión buena.
                    estreno += ahora() - antes
                continue
            # Ilegible un instante (se estaba sustituyendo) no es «sin pulso»: se juzga con la
            # última lectura buena de este lanzamiento. Medido en mi PC el 2026-09-25:
            # una lectura vacía, 24 minutos después de lanzarlo, y el vigilante reinició un
            # agente sano («1470 s sin pulso», que era el tiempo desde que lo lanzó). Si nunca
            # hubo ninguna buena, cuentan los 120 s desde el lanzamiento, como siempre.
            datos = salud.leer() or ultima_buena
            ultima_buena = datos
            # Del agente que se lanzó: él, o el hijo de su lanzador. Medido en producción el
            # 2026-09-25, a los minutos de estrenarlo: con el `pythonw.exe` del entorno
            # virtual, que es un lanzador, el pulso lo escribía el nieto, el vigilante no lo
            # reconocía nunca, y reiniciaba un agente sano cada 120 s.
            suyo = hijo.pid in (datos.get("pid"), datos.get("ppid"))
            pulso = datos.get("pulso") if suyo else None
            referencia = max(pulso if isinstance(pulso, (int, float)) else lanzado, margen_desde)
            if estreno is not None and ahora() - estreno > PLAZO_ESTRENO and por_confirmar():
                _terminar_arbol(hijo)
                hijo.wait()
                codigo = volver_atras(f"la versión nueva no llegó a sana en {int(PLAZO_ESTRENO)} s")
                if codigo is not None:
                    return codigo
                estreno = None
            if ahora() - referencia > salud.ATASCADO:
                _anotar(f"atascado: {int(ahora() - referencia)} s sin pulso; se reinicia")
                atascado = True
                _terminar_arbol(hijo)
                break
        codigo = hijo.wait()
        if not atascado and codigo in FINALES:
            _anotar(f"el agente acabó ({FINALES[codigo]}): no se levanta")
            return codigo
        caidas.append(ahora())
        while caidas and ahora() - caidas[0] > VENTANA_CAIDAS:
            caidas.popleft()
        if len(caidas) > CAIDAS_MAXIMAS:
            if estreno is not None and por_confirmar():
                codigo = volver_atras(f"la versión nueva se cayó {len(caidas)} veces")
                if codigo is not None:
                    return codigo
            _anotar(f"{len(caidas)} caídas en {int(VENTANA_CAIDAS / 60)} minutos: se rinde")
            salud.apuntar(estado="RENDIDO", desde=ahora())
            return 5
        espera = min(2 ** len(caidas), 30)
        _anotar(f"el agente {'se atascó' if atascado else f'se cayó (código {codigo})'}; "
                f"se levanta en {espera} s")
        dormir(espera)
