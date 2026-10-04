"""
Lo que la bandeja de Morgan para Windows (5.1) le pide al agente: pausar y reanudar, lo
último que hizo en el PC y si hay versión nueva del programa.

El estado del icono **no pasa por aquí**: el programa lo lee él mismo de `salud.json` (el
pulso de cada 5 s, 3.6) y de la marca de pausa, cada pocos segundos, sin lanzar un proceso
(lanzar el agente congelado cada 3 s costaría CPU para nada).

## Pausar (decisiones mías, 2026-10-04)

- **Pausar corta lo que esté en marcha, en menos de 2 s** (el criterio de la 5.1): se pide
  parar (la señal de siempre, que el agente mira ahora cada 0,25 s en vez de cada 2), el
  agente **cancela sus órdenes** con el motivo «pausa» y se va. Si en `CORTE` no se ha ido
  (una capacidad sin puntos seguros, un programa de la terminal que no acaba), **se corta**
  su proceso, con lo que haya lanzado. Una escritura cortada no deja el archivo a medias: se
  escribe en un temporal junto al destino y se sustituye de una vez (3.3); como mucho queda
  ese temporal (`.morgan-….tmp`). La orden queda en el diario como no terminada, y la nube
  lo pregunta al volver.
- **La pausa dura hasta que se reanuda**, también tras reiniciar el PC: el vigilante y el
  agente la miran al arrancar y no se levantan. Quien pausa para que Morgan no toque nada no
  espera que un reinicio lo vuelva a encender sin decir nada.
- **Emparejar de nuevo la quita**: quien empareja quiere el agente en marcha.
- **Solo se corta lo que es del agente**: el pid apuntado *y* su hora de arranque. Un pid
  viejo que Windows ya ha dado a otro programa no se toca.
"""

import json
import time
from pathlib import Path

from src.agente import estado as almacen

#: La marca de pausa, en la carpeta del estado. La lee también el programa (Rust).
MARCA = "pausa.json"
#: Lo que se espera a que el agente se vaya solo antes de cortarlo.
CORTE = 1.0
#: Dónde apunta el vigilante quién es (pid y hora de arranque), para poder cortarlo.
VIGILANTE = "vigilante.pid"
#: Holgura al comparar la hora de arranque apuntada con la del proceso.
HOLGURA = 10.0

#: La página de versiones del programa (la misma que enlaza la web).
VERSIONES = "https://api.github.com/repos/wv-Andy/Morgan/releases/latest"
#: Lo único que se abre desde el aviso de versión nueva: una página de versiones de Morgan.
PAGINA_DE_VERSIONES = "https://github.com/wv-Andy/Morgan/releases/"


def marca() -> Path:
    return almacen.carpeta() / MARCA


def en_pausa() -> bool:
    return marca().exists()


def quitar_pausa() -> None:
    marca().unlink(missing_ok=True)


# --- Quién es quién ------------------------------------------------------------------------


def apuntar_vigilante() -> None:
    """El vigilante, al nacer: su pid y su hora de arranque. Nunca lanza."""
    try:
        import psutil

        yo = psutil.Process()
        almacen.carpeta().mkdir(parents=True, exist_ok=True)
        (almacen.carpeta() / VIGILANTE).write_text(
            json.dumps({"pid": yo.pid, "arrancado": yo.create_time()}), encoding="utf-8")
    except Exception:
        pass


def _proceso_si_es(pid, arrancado):
    """El proceso, solo si sigue siendo el apuntado (mismo pid y misma hora de arranque)."""
    import psutil

    if not isinstance(pid, int) or not isinstance(arrancado, (int, float)):
        return None
    try:
        proceso = psutil.Process(pid)
        if abs(proceso.create_time() - arrancado) > HOLGURA:
            return None             # ese pid ya es de otro programa
        return proceso
    except psutil.Error:
        return None


def _el_vigilante():
    try:
        datos = json.loads((almacen.carpeta() / VIGILANTE).read_text(encoding="utf-8"))
        return _proceso_si_es(datos.get("pid"), datos.get("arrancado"))
    except (OSError, ValueError, AttributeError):
        return None


def _el_agente():
    from src.agente import salud

    datos = salud.leer()
    return _proceso_si_es(datos.get("pid"), datos.get("arrancado"))


def _cortar(procesos: list) -> int:
    """Corta cada proceso con lo que haya lanzado. Devuelve cuántos cortó."""
    import psutil

    cortados = 0
    for proceso in procesos:
        try:
            familia = proceso.children(recursive=True) + [proceso]
        except psutil.Error:
            continue
        for p in familia:
            try:
                p.kill()
                cortados += 1
            except psutil.Error:
                pass
    return cortados


# --- Pausar y reanudar ---------------------------------------------------------------------


def pausar(corte: float = CORTE, cortar=None) -> str:
    """Deja el agente en pausa: parado, y sin levantarse hasta `reanudar`."""
    from src.agente import arranque

    cortar = cortar or _cortar
    almacen.carpeta().mkdir(parents=True, exist_ok=True)
    marca().write_text(json.dumps({"desde": time.time()}), encoding="utf-8")
    if not arranque.en_marcha() and not arranque.en_marcha("vigilante.lock"):
        return "En pausa."
    arranque.pedir_parada()
    limite = time.monotonic() + corte
    while arranque.en_marcha() and time.monotonic() < limite:
        time.sleep(0.05)
    se_fue = not arranque.en_marcha()
    # El vigilante solo mira, y mira cada 5 s: tardaría hasta eso en ver que su agente se fue, y
    # mientras, `reanudar` lo daría por en marcha y no lanzaría nada. Medido en GitHub con el
    # congelado (5.1): la pausa salía «cortada» aunque el agente obedeciera. Se corta siempre.
    procesos = [_el_vigilante()] + ([] if se_fue else [_el_agente()])
    cortados = cortar([p for p in procesos if p is not None])
    # Al irse los dos, la espera borra la señal: el próximo vigilante no se iría al nacer.
    if arranque.esperar_a_que_pare(hasta=2.0):
        if se_fue:
            return "En pausa."
        return f"En pausa (cortado: no se paró solo en {corte:g} s; {cortados} procesos)."
    # Sigue vivo: la señal se queda, para que pare en cuanto pueda (y `reanudar` la quita).
    return "Pedida la pausa, pero el agente sigue en marcha. Mira agente.log."


def reanudar() -> str:
    from src.agente import arranque

    quitar_pausa()
    arranque.senal_de_parada().unlink(missing_ok=True)
    if almacen.cargar() is None or almacen.credencial() is None:
        return "Este PC no está emparejado."
    if arranque.en_marcha() or arranque.en_marcha("vigilante.lock"):
        return "Ya estaba en marcha."
    arranque.lanzar_en_segundo_plano()
    return "Reanudado."


# --- Lo último que hizo --------------------------------------------------------------------

ESTADOS = {"COMPLETED": "Hecho", "FAILED": "Falló", "CANCELLED": "Cancelado", "REJECTED": "No permitido",
           "PENDING": "En cola", "RUNNING": "En marcha", "CANCEL_REQUESTED": "Cancelándose"}
#: Lo que la nube pregunta para saber quién es el agente: no es algo que «hizo» en tu PC.
NO_CUENTAN = frozenset({"estado"})


def _que_es(capacidad: str) -> str:
    from src.agente.ajustes import GRUPOS

    for _, capacidades in GRUPOS:
        for nombre, texto, _ in capacidades:
            if nombre == capacidad:
                return texto
    return capacidad


def _detalle(argumentos) -> str:
    """La ruta, el programa o la aplicación de la orden, si se puede leer (los argumentos
    del diario van recortados a 250 caracteres: pueden no ser JSON entero)."""
    try:
        datos = json.loads(argumentos) if isinstance(argumentos, str) else {}
    except ValueError:
        return ""
    if not isinstance(datos, dict):
        return ""
    for clave in ("path", "source", "ruta", "program", "name", "app", "query"):
        valor = datos.get(clave)
        if isinstance(valor, str) and valor:
            return valor[:120]
    return ""


def ultimas(cuantas: int = 15) -> list[dict]:
    """Lo último que pidió la nube en este PC (el diario, 24 h), lo más reciente primero.
    **Solo lee**: abrir el `Diario` lo compactaría, y el agente lo está usando."""
    try:
        lineas = (almacen.carpeta() / "diario.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    ordenes: dict = {}
    for linea in lineas:
        try:
            registro = json.loads(linea)
        except ValueError:
            continue                    # una línea a medias: se salta
        if isinstance(registro, dict) and isinstance(registro.get("command_id"), str):
            ordenes[registro["command_id"]] = registro
    limite = time.time() - 24 * 3600
    vistas = [o for o in ordenes.values()
              if o.get("capability") not in NO_CUENTAN and isinstance(o.get("recibida"), (int, float))
              and o["recibida"] > limite]
    vistas.sort(key=lambda o: o["recibida"], reverse=True)
    return [{"cuando": o["recibida"], "que": _que_es(str(o.get("capability"))),
             "detalle": _detalle(o.get("argumentos")),
             "estado": ESTADOS.get(o.get("estado"), str(o.get("estado")))}
            for o in vistas[:cuantas]]


# --- Versión nueva -------------------------------------------------------------------------


def _como_tupla(version: str) -> tuple:
    try:
        return tuple(int(p) for p in version.strip().lstrip("vV").split("."))
    except ValueError:
        return ()


def _pedir(url: str) -> dict:
    import urllib.request

    peticion = urllib.request.Request(url, headers={"User-Agent": "Morgan-para-Windows",
                                                    "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(peticion, timeout=8) as respuesta:
        return json.loads(respuesta.read(200_000).decode("utf-8"))


def novedades(actual: str, pedir=None) -> dict:
    """Si en la página de versiones hay una más nueva que `actual`. Nunca lanza: sin red o
    con una respuesta rara, `{"hay": False}` y la bandeja no dice nada."""
    try:
        datos = (pedir or _pedir)(VERSIONES)
        version = str(datos.get("tag_name") or "")
        pagina = str(datos.get("html_url") or "")
    except Exception:
        return {"hay": False}
    nueva, ahora = _como_tupla(version), _como_tupla(actual)
    if not nueva or not ahora or nueva <= ahora or not pagina.startswith(PAGINA_DE_VERSIONES):
        return {"hay": False}
    return {"hay": True, "version": version.lstrip("vV"), "pagina": pagina}


def imprimir(datos) -> None:
    print(json.dumps(datos, ensure_ascii=False))

