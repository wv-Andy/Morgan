"""
Tokens personales de API (plan de la API, fase 1).

Lo que usa un cliente que no es el navegador —un script, una extensión de editor,
el agente local de la 3.0— para hablar con Morgan **sin fingir que es uno**. Hasta
aquí, la única forma era iniciar sesión con la contraseña, guardar la cookie y
copiar el CSRF a una cabecera en cada petición (medido: docs/autenticacion.md).

Cómo es un token:

- **`mgn_` + 32 bytes aleatorios** en base64url. El prefijo lo hace reconocible en
  un registro o en un escáner de secretos, y lo distingue del token compartido
  `MORGAN_API_TOKEN`, que es de la instalación y no de una persona.
- **Se guarda el SHA-256**, nunca el valor, igual que las sesiones. El valor se
  enseña una sola vez, al crearlo. Perdido, se revoca y se crea otro.
- **Caduca siempre**: 90 días si no se dice otra cosa, un año como mucho. Un token
  olvidado en un portátil viejo no puede valer para siempre.
- **Tiene alcances** (`chat`, `lectura`, `escritura`): un cliente que solo lee
  conversaciones no debería poder borrar la memoria.
- **Hay cosas que nunca puede hacer**, tenga los alcances que tenga: todo lo que
  cuelga de `/auth` salvo `GET /auth/yo` (crear o revocar tokens, cambiar la
  contraseña, borrar la cuenta, descargar todos los datos), la administración,
  conectar servicios externos y los diagnósticos. Robar un token no puede
  convertirse en robar la cuenta.
- **Caen todos** al cambiar o restablecer la contraseña, que es lo que espera quien
  la cambia porque sospecha algo.
"""

import logging
import secrets
import time
from contextvars import ContextVar

from src.identidad.cuentas import FRENOS, MAXIMOS_TOQUES_RECORDADOS, MINUTOS_ENTRE_TOQUES, _hash_token
from src.identidad.repositorio import RepositorioDeCuentas

logger = logging.getLogger(__name__)

PREFIJO = "mgn_"

CHAT = "chat"
LECTURA = "lectura"
ESCRITURA = "escritura"
ALCANCES = (CHAT, LECTURA, ESCRITURA)

DIAS_POR_DEFECTO = 90
DIAS_MAXIMOS = 365

#: Tokens vivos por cuenta. Un cliente por token; veinte clientes a la vez es
#: mucho más de lo que tiene nadie, y sin tope un bucle roto podría llenar la tabla.
MAX_TOKENS_VIVOS = 20

LARGO_MAXIMO_NOMBRE = 60

#: Métodos que no cambian nada: les basta el alcance `lectura`.
METODOS_DE_LECTURA = frozenset({"GET", "HEAD"})

#: Rutas que un token no puede usar nunca, con los alcances que tenga. Por prefijo.
PROHIBIDAS_POR_PREFIJO = ("/auth", "/admin", "/integraciones", "/diagnostico")

#: La excepción: saber de quién es el token es lo primero que hace un cliente.
PERMITIDAS_AUNQUE_PROHIBIDAS = frozenset({("GET", "/auth/yo")})

#: Las dos rutas del chat. Van aparte de `escritura` porque son lo que pide casi
#: cualquier cliente, y dar `escritura` para chatear sería dar de más.
RUTAS_DE_CHAT = frozenset({"/chat", "/chat/stream"})


# El token con el que se hace esta petición, si se hace con uno. Lo lee la
# auditoría para anotar qué cliente hizo qué. Por defecto ninguno: la web, la
# línea de comandos y las tareas programadas no van con token.
_token_actual: ContextVar[str | None] = ContextVar("morgan_api_token", default=None)


def token_actual() -> str | None:
    """El id (nunca el valor) del token de esta petición, o None."""
    return _token_actual.get()


def fijar_token(token_id: str | None) -> object:
    return _token_actual.set(token_id)


class TokenNoValido(ValueError):
    """Los datos para crear un token no valen (nombre, alcances, caducidad)."""


class DemasiadosTokens(ValueError):
    """La cuenta ya tiene el máximo de tokens vivos."""


def parece_token(valor: str | None) -> bool:
    """Si un valor tiene forma de token personal (no si es válido)."""
    return bool(valor) and valor.startswith(PREFIJO)


def token_de_la_cabecera(authorization: str | None) -> str | None:
    """El token personal de una cabecera `Authorization: Bearer mgn_...`, o None.

    Solo esa cabecera. `X-Morgan-Token` es del token compartido y no se mezcla:
    que haya una sola forma de presentar un token personal es una cosa menos que
    auditar.
    """
    cabecera = (authorization or "").strip()
    if cabecera[:7].lower() != "bearer ":
        return None
    valor = cabecera[7:].strip()
    return valor if parece_token(valor) else None


def alcance_necesario(metodo: str, ruta: str) -> str | None:
    """Qué alcance hace falta para esta petición con un token. None: ninguno vale."""
    metodo = metodo.upper()
    ruta = ruta.rstrip("/") or "/"

    if (metodo, ruta) not in PERMITIDAS_AUNQUE_PROHIBIDAS and any(
        ruta == prefijo or ruta.startswith(prefijo + "/")
        for prefijo in PROHIBIDAS_POR_PREFIJO
    ):
        return None

    if metodo == "POST" and ruta in RUTAS_DE_CHAT:
        return CHAT
    if metodo in METODOS_DE_LECTURA:
        return LECTURA
    return ESCRITURA


def _alcances_de(texto: str | None) -> frozenset[str]:
    return frozenset(a for a in (texto or "").split(",") if a in ALCANCES)


class ServicioDeTokens:
    """Crear, listar, revocar y resolver tokens personales."""

    def __init__(self, repo: RepositorioDeCuentas):
        self.repo = repo

    def crear(
        self,
        user_id: str,
        nombre: str,
        alcances: list[str] | tuple[str, ...],
        dias: int = DIAS_POR_DEFECTO,
    ) -> tuple[str, dict]:
        """Crea un token. Devuelve `(valor, datos)`: el valor no se vuelve a ver."""
        nombre = (nombre or "").strip()
        if not nombre or len(nombre) > LARGO_MAXIMO_NOMBRE:
            raise TokenNoValido(
                f"El nombre es obligatorio y de {LARGO_MAXIMO_NOMBRE} caracteres como mucho."
            )

        pedidos = [a for a in dict.fromkeys(alcances or [])]
        desconocidos = [a for a in pedidos if a not in ALCANCES]
        if not pedidos or desconocidos:
            raise TokenNoValido(
                "Los alcances tienen que ser uno o varios de: " + ", ".join(ALCANCES) + "."
            )

        if not isinstance(dias, int) or not 1 <= dias <= DIAS_MAXIMOS:
            raise TokenNoValido(f"La caducidad va de 1 a {DIAS_MAXIMOS} días.")

        ahora = time.time()
        if len(self.repo.listar_tokens(user_id, ahora)) >= MAX_TOKENS_VIVOS:
            raise DemasiadosTokens(
                f"Ya tienes {MAX_TOKENS_VIVOS} tokens. Revoca alguno que no uses."
            )

        valor = PREFIJO + secrets.token_urlsafe(32)
        datos = {
            "id": "tok-" + secrets.token_hex(6),
            "user_id": user_id,
            "token_hash": _hash_token(valor),
            "nombre": nombre,
            "alcances": ",".join(a for a in ALCANCES if a in pedidos),
            "creado_en": ahora,
            "caduca_en": ahora + dias * 86400,
            "ultimo_uso": None,
            "revocado": 0,
        }
        self.repo.crear_token(datos)
        return valor, self._publico(datos)

    def listar(self, user_id: str) -> list[dict]:
        return [self._publico(f) for f in self.repo.listar_tokens(user_id, time.time())]

    def revocar(self, user_id: str, token_id: str) -> bool:
        return self.repo.revocar_token(user_id, token_id)

    def revocar_todos(self, user_id: str) -> int:
        return self.repo.revocar_tokens(user_id)

    def resolver(self, valor: str) -> dict | None:
        """El token vivo y su usuario, o None si no vale por lo que sea.

        Devuelve `{"token": {..., "alcances": frozenset}, "usuario": {...}}`.
        """
        if not parece_token(valor):
            return None

        ahora = time.time()
        encontrado = self.repo.token_por_hash(_hash_token(valor), ahora)
        if encontrado is None:
            return None

        token = dict(encontrado["token"])
        token["alcances"] = _alcances_de(token.get("alcances"))

        # El «último uso», con el mismo freno que las sesiones: como mucho una
        # escritura cada pocos minutos, que es informativo y no puede costar un
        # viaje a la base en cada petición.
        clave = "tok:" + token["id"]
        ultimo = FRENOS.ultimos_toques.get(clave)
        if ultimo is None or ahora - ultimo >= MINUTOS_ENTRE_TOQUES * 60:
            if len(FRENOS.ultimos_toques) >= MAXIMOS_TOQUES_RECORDADOS:
                FRENOS.ultimos_toques.clear()
            FRENOS.ultimos_toques[clave] = ahora
            try:
                self.repo.tocar_token(token["id"], ahora)
            except Exception:
                logger.warning("No se pudo anotar el uso del token", exc_info=True)

        return {"token": token, "usuario": encontrado["usuario"]}

    @staticmethod
    def _publico(fila: dict) -> dict:
        """Lo que se enseña de un token: nunca el hash."""
        return {
            "id": fila["id"],
            "nombre": fila["nombre"],
            "alcances": [a for a in ALCANCES if a in _alcances_de(fila.get("alcances"))],
            "creado_en": fila["creado_en"],
            "caduca_en": fila["caduca_en"],
            "ultimo_uso": fila.get("ultimo_uso"),
        }
