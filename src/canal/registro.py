"""
Qué agentes están conectados ahora mismo (3.0-D).

**En la memoria del proceso**, a propósito: Render gratuito tiene una sola instancia y
un registro compartido (Redis, la base) sería construir antes de necesitarlo (§37 del
plan). El límite está medido y escrito: durante un despliegue conviven dos instancias
unos segundos, y lo cubre el despacho esperando a que el agente reaparezca
(docs/mediciones.md §6).

Reglas que viven aquí:

- **Varios agentes por persona** desde la 3.7 (antes, uno: P1 del contrato). Cada
  conexión es de **un PC** (`agent_id`), y todo lo que dirige una orden la busca por
  su PC, nunca por la persona: con dos, una consulta tras un corte que fuera «al
  agente de la persona» podría ir al otro. Si vuelve a conectar **el mismo** PC —lo
  normal tras despertar de una suspensión, cuando la conexión vieja sigue medio
  abierta—, la nueva sustituye a la vieja.
- **Un PC que se cae por un corte sigue contando** unos segundos (`recientes`): para
  elegir equipo, quien tiene dos los sigue teniendo aunque uno parpadee (3.7.5). Si no,
  una orden sin decir cuál iría al otro sin preguntar. Uno revocado deja de contar al
  momento.
- Cada conexión lleva su cola: **una operación en vuelo y cuatro esperando**; con más,
  se rechaza al momento en vez de acumular (§12-§13 del plan).
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Códigos de cierre del canal. Del rango de la aplicación (4000-4999), salvo 1012.
CERRADO_CREDENCIAL = 4401     # sin credencial, credencial mala o agente revocado
CERRADO_SALUDO = 4400         # el saludo no es válido
CERRADO_OTRO_AGENTE = 4409    # hasta la 3.7: la cuenta ya tenía otro agente conectado
CERRADO_SUSTITUIDA = 4410     # el mismo agente se volvió a conectar
CERRADO_INCOMPATIBLE = 4426   # protocolo fuera del rango que acepta la nube
CERRADO_SIN_LATIDOS = 4408    # tres latidos sin respuesta

#: Operaciones a la vez por agente, y en espera detrás (§15 del contrato).
EN_VUELO = 1
EN_ESPERA = 4


@dataclass
class Conexion:
    """Un agente conectado, visto desde la nube."""

    user_id: str
    agent_id: str
    nombre: str
    capacidades: frozenset[str]
    enviar: "callable"   # corrutina: enviar(texto)
    cerrar: "callable"   # corrutina: cerrar(codigo, motivo)
    loop: asyncio.AbstractEventLoop
    conectado_en: float = field(default_factory=time.time)
    #: command_id → futuro con el resultado.
    esperando: dict = field(default_factory=dict)
    pendientes: int = 0
    #: La versión del protocolo que habla ESTE agente. La nube le manda órdenes con
    #: ella, no con la suya: un agente más viejo sigue funcionando (3.1-E).
    protocolo: int = 1
    #: command_id → trozos de un archivo que está llegando (3.1-E).
    fragmentos: dict = field(default_factory=dict)
    #: command_id → a quién contarle los cambios de estado de esa orden (3.4).
    estados: dict = field(default_factory=dict)
    #: command_id → futuro con la respuesta a «¿qué pasó con esta orden?» (3.4).
    consultas: dict = field(default_factory=dict)
    turno: asyncio.Lock = field(default_factory=asyncio.Lock)
    viva: bool = True


class RegistroDeConexiones:
    def __init__(self) -> None:
        #: persona → PC (`agent_id`) → su conexión.
        self._por_usuario: dict[str, dict[str, Conexion]] = {}
        #: persona → PC → (su nombre, cuándo se cayó): los que se fueron por un corte.
        self._se_fueron: dict[str, dict[str, tuple[str, float]]] = {}
        #: El bucle del servidor, donde viven los sockets. El despacho, que corre en
        #: el hilo del turno, le pasa el trabajo a él.
        self.loop: asyncio.AbstractEventLoop | None = None
        #: El hilo de ese bucle. Esperar un resultado desde él lo bloquearía entero.
        self.hilo = None

    # --- Consulta ---

    def de(self, user_id: str, agent_id: str | None = None) -> Conexion | None:
        """La conexión de **ese** PC; sin decirlo, la **única** de la persona. Con varios
        conectados y sin decir cuál, `None`: elegir es cosa de quien pregunta, y el
        despacho no adivina (decisión mía, 3.7)."""
        if agent_id is not None:
            conexion = self._por_usuario.get(user_id, {}).get(agent_id)
            return conexion if conexion is not None and conexion.viva else None
        vivas = self.todas(user_id)
        return vivas[0] if len(vivas) == 1 else None

    def todas(self, user_id: str) -> list[Conexion]:
        """Los PC conectados de la persona, del más antiguo al más nuevo."""
        return sorted((c for c in self._por_usuario.get(user_id, {}).values() if c.viva),
                      key=lambda c: c.conectado_en)

    def recientes(self, user_id: str, ventana: float) -> list[str]:
        """Los nombres de los PC que se cayeron por un corte hace menos de `ventana`
        segundos y no han vuelto: probablemente vuelven (3.7.5)."""
        limite = time.monotonic() - ventana
        vivos = {c.agent_id for c in self.todas(user_id)}
        return [nombre for agent_id, (nombre, cuando) in self._se_fueron.get(user_id, {}).items()
                if cuando > limite and agent_id not in vivos]

    def todas_las_conexiones(self) -> list[Conexion]:
        return [c for por_pc in self._por_usuario.values() for c in por_pc.values() if c.viva]

    def conectados(self) -> int:
        return len(self.todas_las_conexiones())

    # --- Altas y bajas ---

    async def registrar(self, conexion: Conexion) -> None:
        import threading

        self.loop = conexion.loop
        self.hilo = threading.current_thread()
        de_la_persona = self._por_usuario.setdefault(conexion.user_id, {})
        anterior = de_la_persona.get(conexion.agent_id)
        de_la_persona[conexion.agent_id] = conexion
        if anterior is not None and anterior is not conexion:
            # El mismo PC, reconectado: la conexión vieja está medio muerta.
            await self._dar_de_baja(anterior, CERRADO_SUSTITUIDA, "sustituida por una conexión nueva")

    async def quitar(self, conexion: Conexion, codigo: int | None = None, motivo: str = "") -> None:
        de_la_persona = self._por_usuario.get(conexion.user_id, {})
        if de_la_persona.get(conexion.agent_id) is conexion:
            del de_la_persona[conexion.agent_id]
            if not de_la_persona:
                self._por_usuario.pop(conexion.user_id, None)
            idos = self._se_fueron.setdefault(conexion.user_id, {})
            if codigo == CERRADO_CREDENCIAL:
                idos.pop(conexion.agent_id, None)      # revocado: no va a volver
            else:
                idos[conexion.agent_id] = (conexion.nombre, time.monotonic())
        await self._dar_de_baja(conexion, codigo, motivo)

    async def cerrar_usuario(self, user_id: str, codigo: int, motivo: str,
                             agent_id: str | None = None) -> None:
        """Cierra los PC conectados de alguien (contraseña cambiada: todos), o solo uno
        (ese equipo revocado)."""
        for conexion in self.todas(user_id):
            if agent_id is None or conexion.agent_id == agent_id:
                await self.quitar(conexion, codigo, motivo)

    async def _dar_de_baja(self, conexion: Conexion, codigo: int | None, motivo: str) -> None:
        if not conexion.viva:
            return
        conexion.viva = False
        # Lo que esperaba respuesta falla YA, con su motivo: no se queda colgado
        # hasta que venza el plazo (§15 del contrato).
        from src.canal.despacho import AgenteDesconectado

        for futuro in [*conexion.esperando.values(), *conexion.consultas.values()]:
            if not futuro.done():
                futuro.set_exception(AgenteDesconectado())
        conexion.esperando.clear()
        conexion.consultas.clear()
        if codigo is not None:
            try:
                await conexion.cerrar(codigo, motivo)
            except Exception:
                pass

    def cerrar_desde_hilo(self, user_id: str, codigo: int, motivo: str,
                          agent_id: str | None = None) -> None:
        """Para las rutas normales, que corren en otro hilo: cierra los PC conectados de
        alguien (o solo ese `agent_id`). Sin ninguno conectado, no hace nada."""
        if not self.todas(user_id) or self.loop is None:
            return
        futuro = asyncio.run_coroutine_threadsafe(
            self.cerrar_usuario(user_id, codigo, motivo, agent_id), self.loop)
        try:
            futuro.result(timeout=5)
        except Exception:
            logger.warning("No se pudo cerrar la conexión de un agente", exc_info=True)

    def reiniciar(self) -> None:
        """Para las pruebas."""
        self._por_usuario.clear()
        self._se_fueron.clear()
        self.loop = None
        self.hilo = None


#: Uno por proceso: es el punto.
REGISTRO = RegistroDeConexiones()
