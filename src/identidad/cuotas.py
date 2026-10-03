"""
Cupo de uso por usuario (identidad, V2.0 adelantada).

Morgan usa las claves de su dueño. Con una sola persona eso da igual; con varias,
**una sola podría agotar la cuota de todas** en una tarde. El cupo es lo que hace
viable abrir Morgan a otros sin quedarse sin modelo.

Decisiones que conviene tener presentes:

- **Se cuenta por día natural**, no con una ventana deslizante. Es más fácil de
  explicar —«te quedan 12 mensajes hoy»— y una fila por usuario y día se limpia
  sola con un borrado por fecha, sin trabajo de mantenimiento.
- **El usuario local no tiene cupo.** Es tu ordenador y tus claves: limitarte a ti
  mismo en tu propia máquina no protege de nada.
- **Se cuenta lo que cuesta dinero**, no las peticiones. Listar conversaciones no
  gasta cuota del modelo; un turno de chat, una transcripción y un análisis de
  imagen, sí.
- **Se apunta al empezar, no al terminar.** Si se contara al final, un turno que
  falla a mitad saldría gratis y bastaría con provocar fallos para saltarse el
  cupo.

## El cupo global (V2.0.24)

El registro está abierto a cualquiera con el enlace (decisión mía), y el cupo
por persona **no acota el total**: diez cuentas son diez cupos. La auditoría de la
2.3 midió que un script creaba 30 cuentas en 4 segundos; los frenos de altas lo
convirtieron en horas, no en imposible.

Así que además hay un tope **para la suma de todas las cuentas sujetas a cupo**. El
propietario y el usuario local no cuentan ni se frenan: son quienes pagan. Los
números, y de dónde salen, están en `docs/autenticacion.md`.

La comprobación global **no es atómica entre personas**: dos turnos simultáneos de
dos cuentas pueden pasar los dos con el contador en el último hueco. Se acepta: el
exceso posible son unas pocas llamadas, no una ventana abierta, y hacerlo atómico
exigiría un bloqueo compartido por todos los turnos del servidor.
"""

import logging
from dataclasses import dataclass
from datetime import date

from src.identidad.modelos import USUARIO_LOCAL
from src.identidad.roles import Rol, sin_cupo

logger = logging.getLogger(__name__)


class CuotaAgotada(RuntimeError):
    """El usuario ha llegado a su límite del día.

    El mensaje está escrito para enseñarse: no es un fallo del sistema, es una
    condición normal que la persona debe entender.
    """

    def __init__(self, concepto: str, limite: int):
        self.concepto = concepto
        self.limite = limite
        super().__init__(
            f"Has llegado al límite de {limite} {concepto} por día. "
            "Se renueva mañana."
        )


class CupoGlobalAgotado(CuotaAgotada):
    """Entre todas las cuentas se ha llegado al tope del día (V2.0.24).

    Hereda de `CuotaAgotada` a propósito: las rutas y la web ya saben responder a
    un cupo agotado (429 `CUOTA_AGOTADA`), y este es el mismo caso visto desde
    otro lado. Lo que cambia es el mensaje: **no es culpa de quien lo lee**.
    """

    def __init__(self, concepto: str, limite: int):
        RuntimeError.__init__(
            self,
            f"Morgan ha llegado hoy a su límite de {concepto} para todas las cuentas. "
            "No es por tu uso: se renueva mañana.",
        )
        self.concepto = concepto
        self.limite = limite


@dataclass(frozen=True)
class Cuotas:
    """Cuántas operaciones de cada tipo puede hacer un usuario al día.

    Y cuántas, como mucho, entre todos (`global_*`). Un global a 0 es sin tope.
    Los valores de serie salen de lo medido:

    - **150 mensajes**: el cupo gratuito de modelos ronda los 210 turnos al día
      (tres cuentas de Groq y el relevo de modelo, estimado). Deja unos 60 para
      el propietario, que no cuenta en la suma pero gasta del mismo cupo.
    - **15 imágenes**: solo Gemini ve imágenes, y su cuenta gratuita da 20
      peticiones al día. Deja margen para el propietario.
    - **60 transcripciones**: holgado para un uso normal, y acota un bucle.
    """

    mensajes: int = 50
    transcripciones: int = 20
    imagenes: int = 20
    global_mensajes: int = 150
    global_transcripciones: int = 60
    global_imagenes: int = 15

    def limite_de(self, concepto: str) -> int:
        return int(getattr(self, concepto, 0))

    def limite_global_de(self, concepto: str) -> int:
        return int(getattr(self, f"global_{concepto}", 0))

    @classmethod
    def desde_configuracion(cls) -> "Cuotas":
        """Los topes globales se pueden cambiar sin tocar código."""
        from src.config import get_settings

        s = get_settings()
        return cls(
            global_mensajes=s.cupo_global_mensajes,
            global_transcripciones=s.cupo_global_transcripciones,
            global_imagenes=s.cupo_global_imagenes,
        )


# Conceptos que se cuentan, con el nombre que se le enseña a la persona.
CONCEPTOS = {
    "mensajes": "mensajes",
    "transcripciones": "transcripciones de audio",
    "imagenes": "análisis de imágenes",
}


class ControlDeUso:
    """Lleva la cuenta del consumo diario y decide si cabe una operación más."""

    def __init__(self, db, cuotas: Cuotas | None = None):
        # Se admite lo mismo que en el resto de la capa de identidad: una base
        # SQLite, o una factoria de repositorios de la que se saca lo que haga
        # falta. La rama de Supabase existe porque en la nube no hay fichero
        # local que sobreviva a un reinicio, y es justo alli donde el cupo
        # importa: en tu equipo no hay a quien limitar.
        self.client = getattr(db, "client", None)
        self.db = getattr(db, "db", db) if self.client is None else None
        self.cuotas = cuotas or Cuotas()

    @staticmethod
    def _hoy() -> str:
        return date.today().isoformat()

    def consumo(self, user_id: str) -> dict[str, int]:
        """Lo gastado hoy. Un fallo de la base devuelve cero, no bloquea."""
        try:
            if self.client is not None:
                filas = self.client.select(
                    "uso_diario",
                    f"user_id=eq.{user_id}&dia=eq.{self._hoy()}"
                    "&select=mensajes,transcripciones,imagenes&limit=1",
                )
                fila = filas[0] if filas else None
            else:
                with self.db.connect() as conn:
                    fila = conn.execute(
                        "SELECT mensajes, transcripciones, imagenes FROM uso_diario "
                        "WHERE user_id = ? AND dia = ?",
                        (user_id, self._hoy()),
                    ).fetchone()
        except Exception:
            # Contar es accesorio: que falle no puede impedir usar Morgan. El
            # riesgo de no contar un turno es menor que el de dejar a alguien
            # fuera por un problema de la base.
            logger.warning("No se pudo leer el consumo de %s", user_id, exc_info=True)
            return {c: 0 for c in CONCEPTOS}

        if fila is None:
            return {c: 0 for c in CONCEPTOS}
        return {c: fila[c] or 0 for c in CONCEPTOS}

    def exento(self, user_id: str, rol: Rol | None = None) -> bool:
        """Si a este usuario no se le aplican los límites.

        El usuario implícito siempre —es tu equipo y tus claves— y el propietario
        también, porque el cupo existe para repartir un recurso compartido y a
        quien lo paga no hay nada que repartirle.
        """
        return user_id == USUARIO_LOCAL or (rol is not None and sin_cupo(rol))

    def restante(self, user_id: str, rol: Rol | None = None) -> dict[str, int]:
        """Cuánto le queda hoy de cada cosa."""
        if self.exento(user_id, rol):
            return {c: -1 for c in CONCEPTOS}  # -1: sin límite

        gastado = self.consumo(user_id)
        return {
            c: max(0, self.cuotas.limite_de(c) - gastado[c]) for c in CONCEPTOS
        }

    def apuntar(self, user_id: str, concepto: str, rol: Rol | None = None) -> None:
        """Registra una operación, o lanza `CuotaAgotada` si ya no cabe.

        Comprobar y apuntar van juntos a propósito: separarlos dejaría una
        ventana entre la comprobación y el apunte por la que dos peticiones
        simultáneas podrían pasar las dos.
        """
        if self.exento(user_id, rol):
            return  # tu maquina y tus claves, o las del dueno

        if concepto not in CONCEPTOS:
            raise ValueError(f"Concepto de uso desconocido: '{concepto}'")

        limite = self.cuotas.limite_de(concepto)
        hoy = self._hoy()

        # Primero el tope entre todos: si ya está lleno, no se gasta el cupo de
        # la persona en un intento que no va a pasar.
        tope_global = self.cuotas.limite_global_de(concepto)
        if tope_global > 0 and self._gastado_hoy_por_todos(concepto, hoy) >= tope_global:
            logger.warning(
                "Cupo global de %s agotado (%d). Se rechaza a %s.",
                concepto, tope_global, user_id,
            )
            raise CupoGlobalAgotado(CONCEPTOS[concepto], tope_global)

        if self.client is not None:
            # PostgREST no sabe escribir "columna = columna + 1", asi que la
            # atomicidad la pone una funcion de Postgres. Devuelve si cabia.
            cabia = self.client.rpc(
                "apuntar_uso",
                {"p_user_id": user_id, "p_dia": hoy,
                 "p_concepto": concepto, "p_limite": limite},
            )
            if cabia is False:
                raise CuotaAgotada(CONCEPTOS[concepto], limite)
            return

        with self.db.connect() as conn:
            # La fila del dia, si no existia.
            conn.execute(
                "INSERT OR IGNORE INTO uso_diario (user_id, dia) VALUES (?, ?)",
                (user_id, hoy),
            )

            # El incremento lleva su propia condicion: si el contador ya esta en
            # el limite, la sentencia no toca ninguna fila y `rowcount` vale 0.
            # Esa es la senal, y es fiable: comprobar por separado y sumar
            # despues dejaria una ventana por la que dos peticiones simultaneas
            # pasarian las dos.
            cursor = conn.execute(
                f"UPDATE uso_diario SET {concepto} = {concepto} + 1 "
                f"WHERE user_id = ? AND dia = ? AND {concepto} < ?",
                (user_id, hoy, limite),
            )

            if cursor.rowcount == 0:
                raise CuotaAgotada(CONCEPTOS[concepto], limite)

    def _gastado_hoy_por_todos(self, concepto: str, hoy: str) -> int:
        """La suma del día entre todas las cuentas que cuentan.

        Un fallo de la base devuelve 0 y deja pasar, por el mismo motivo que
        `consumo`: contar es accesorio, y el cupo por persona sigue aplicándose.
        """
        try:
            if self.client is not None:
                # Una fila por cuenta activa hoy: son pocas, y sumar aquí evita
                # depender de agregados de PostgREST, que vienen apagados.
                filas = self.client.select("uso_diario", f"dia=eq.{hoy}&select={concepto}")
                return sum(int(f.get(concepto) or 0) for f in filas)
            with self.db.connect() as conn:
                fila = conn.execute(
                    f"SELECT COALESCE(SUM({concepto}), 0) AS n FROM uso_diario WHERE dia = ?",
                    (hoy,),
                ).fetchone()
                return int(fila["n"] if fila else 0)
        except Exception:
            logger.warning("No se pudo leer el uso global de %s", concepto, exc_info=True)
            return 0

    def limpiar_antiguos(self, dias: int = 30) -> int:
        """Borra el consumo de días pasados. No hace falta guardarlo para siempre."""
        from datetime import timedelta

        limite = (date.today() - timedelta(days=dias)).isoformat()

        if self.client is not None:
            return len(self.client.delete("uso_diario", f"dia=lt.{limite}"))

        with self.db.connect() as conn:
            return conn.execute("DELETE FROM uso_diario WHERE dia < ?", (limite,)).rowcount
