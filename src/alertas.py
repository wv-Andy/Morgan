"""
Los errores del servidor, a la bandeja del propietario (4.20).

Hasta aquí, un error en producción solo se veía si alguien leía los registros de Render. Este
manejador de `logging` recoge lo que se registra como ERROR y deja un aviso en la bandeja de
la web del propietario (la misma de las automatizaciones), sin servicios nuevos.

- **Agrupado**: el mismo error (mismo módulo y mismo mensaje, con los números quitados) llega
  una vez por `VENTANA`; los que se repiten dentro se cuentan y se dicen en el siguiente.
- **Con tope**: como mucho `MAX_AL_DIA` avisos al día. Una tormenta de errores no llena la
  bandeja.
- **Fuera del camino**: el aviso se escribe en otro hilo. Registrar un error no espera a la
  base, y si escribir el aviso falla, no se vuelve a avisar de eso (no hay bucle).
"""

import logging
import queue
import re
import threading
import time

logger = logging.getLogger(__name__)

VENTANA = 3600.0
MAX_AL_DIA = 20
MAX_TEXTO = 1500
#: Lo que no es un fallo del servidor sino de quien llama, o ya tiene su aviso propio.
IGNORADOS = ("uvicorn.access",)


def firma(registro: logging.LogRecord) -> str:
    """Qué error es, sin lo que cambia de una vez a otra (ids, números, horas)."""
    mensaje = registro.getMessage().split("\n", 1)[0]
    return f"{registro.name}:{re.sub(r'[0-9a-f]{8,}|\d+', '#', mensaje)[:200]}"


class AvisosDeErrores(logging.Handler):
    def __init__(self, avisar, reloj=time.time):
        """`avisar(titulo, texto)` deja el aviso; se llama en el hilo del manejador."""
        super().__init__(level=logging.ERROR)
        self.avisar = avisar
        self.reloj = reloj
        self._cola: queue.Queue = queue.Queue(maxsize=200)
        self._ultimo: dict[str, float] = {}
        self._repetidos: dict[str, int] = {}
        self._dia: tuple[int, int] = (0, 0)       # (día, avisos de ese día)
        self._cerrojo = threading.Lock()
        self._hilo = threading.Thread(target=self._trabajar, name="morgan-avisos-de-errores", daemon=True)
        self._hilo.start()

    def emit(self, registro: logging.LogRecord) -> None:
        if registro.name.startswith(IGNORADOS) or registro.name == __name__:
            return
        if threading.current_thread() is self._hilo:
            return                                   # un fallo al avisar no avisa de sí mismo
        ahora = self.reloj()
        clave = firma(registro)
        with self._cerrojo:
            if ahora - self._ultimo.get(clave, -VENTANA) < VENTANA:
                self._repetidos[clave] = self._repetidos.get(clave, 0) + 1
                return
            dia = int(ahora // 86400)
            if self._dia[0] != dia:
                self._dia = (dia, 0)
            if self._dia[1] >= MAX_AL_DIA:
                return
            self._dia = (dia, self._dia[1] + 1)
            self._ultimo[clave] = ahora
            repetido = self._repetidos.pop(clave, 0)
        texto = f"{registro.name}: {registro.getMessage()}"
        if registro.exc_info and registro.exc_info[1] is not None:
            texto += f"\n\n{type(registro.exc_info[1]).__name__}: {registro.exc_info[1]}"
        if repetido:
            texto += f"\n\n(Y se repitió {repetido} vez/veces en la hora anterior.)"
        try:
            self._cola.put_nowait(("Error en el servidor", texto[:MAX_TEXTO]))
        except queue.Full:
            pass

    def _trabajar(self) -> None:
        while True:
            titulo, texto = self._cola.get()
            try:
                self.avisar(titulo, texto)
            except Exception:
                # Al registro normal, nunca a la bandeja: si la base falla, no hay bucle.
                logger.warning("No se pudo dejar el aviso de un error en la bandeja", exc_info=True)


def _avisar_al_propietario(titulo: str, texto: str) -> None:
    from src.api.dependencies import get_container
    from src.automatizacion.repositorio import nuevo_id, repositorio_de_automatizaciones
    from src.identidad.repositorio import repositorio_de_cuentas

    repos = get_container().repositories
    propietario = repositorio_de_cuentas(repos).propietario()
    if not propietario:
        return
    repositorio_de_automatizaciones(repos).crear_aviso({
        "id": nuevo_id("av"), "user_id": propietario["id"], "automatizacion_id": None,
        "titulo": titulo, "texto": texto, "estado": "fallo", "herramientas": [],
        "leido": False, "creado_en": time.time()})


def instalar() -> AvisosDeErrores | None:
    """Pone el manejador en el registro raíz, una sola vez."""
    raiz = logging.getLogger()
    if any(isinstance(h, AvisosDeErrores) for h in raiz.handlers):
        return None
    manejador = AvisosDeErrores(_avisar_al_propietario)
    raiz.addHandler(manejador)
    return manejador
