"""
Identidad y emparejamiento de los agentes locales, en la nube (3.0-C).

Contrato en docs/agente-local.md (§4-§6). En resumen:

- **Emparejar es cambiar un código por una credencial.** La web, con sesión, pide un
  código de un solo uso que dura diez minutos. El agente lo **consulta** (la nube le
  dice con qué cuenta se va a emparejar, sin gastarlo), la persona **confirma en el
  PC** y el agente lo **canjea** por su `agent_id` y su credencial. El código no se
  convierte nunca en la credencial.
- **Por qué dos pasos y no uno.** Sin la consulta, alguien que generara un código en
  *su* cuenta y convenciera a otra persona de teclearlo («soy de soporte técnico»)
  se quedaría con un agente en el PC de la víctima gobernado desde su cuenta
  (amenaza H del contrato). Enseñar la cuenta antes de canjear lo corta.
- **La credencial (`mga_…`)**: solo su hash en la nube; no vale en la API REST, y un
  token personal no vale donde vale ella.
- **Cambiar o restablecer la contraseña revoca los agentes**, como los tokens: quien
  la cambia porque sospecha algo espera que nada de lo anterior siga valiendo, y un
  agente emparejado por un intruso podría servir a la cuenta archivos inventados.
"""

import hashlib
import logging
import secrets
import time

from src.agente.protocolo import (
    ALFABETO_CODIGO,
    LARGO_CODIGO,
    PREFIJO_CREDENCIAL,
    PROTOCOLO_ACTUAL,
    PROTOCOLO_MINIMO,
    formatear_codigo,
    normalizar_codigo,
)
from src.identidad.repositorio import RepositorioDeCuentas

logger = logging.getLogger(__name__)

MINUTOS_DE_CODIGO = 10

#: Agentes activos por cuenta. Un PC por agente: diez es más de lo que tiene nadie,
#: y sin tope un bucle roto podría llenar la tabla.
MAX_AGENTES = 10

#: Cada cuánto rota sola la credencial de un agente (decisión mía, 3.8).
ROTAR_CADA = 90 * 24 * 3600

LARGO_MAXIMO_NOMBRE = 60
LARGO_MAXIMO_SISTEMA = 80

#: Freno a adivinar códigos. **Por origen y en total**, no por código: un código
#: equivocado no coincide con ninguno, así que no hay código que invalidar (el
#: contrato decía «5 fallos lo invalidan», y eso no se puede aplicar). Mismo
#: mecanismo que el freno de altas, en la tabla de intentos.
VENTANA_MINUTOS = 15
MAX_FALLOS_POR_ORIGEN = 10
MAX_FALLOS_EN_TOTAL = 50

#: Con qué identificador se apuntan los fallos. Empieza por `#` por lo mismo que
#: `CLAVE_REGISTRO`: no puede ser el nombre de ninguna cuenta.
CLAVE_EMPAREJAR = "#emparejar"


def _hash(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def correo_parcial(email: str | None) -> str | None:
    """`ana.garcia@ejemplo.co` → `an•••@ejemplo.co`: basta para reconocerlo, no para leerlo."""
    if not email or "@" not in email:
        return None
    nombre, dominio = email.split("@", 1)
    return f"{nombre[:2]}•••@{dominio}"


class CodigoNoValido(ValueError):
    """El código no existe, caducó o ya se usó. Un solo motivo para los tres."""

    def __init__(self) -> None:
        super().__init__(
            "Ese código no vale: no existe, ha caducado o ya se usó. "
            "Pide otro en la web (Ajustes → Tu equipo)."
        )


class DemasiadosFallos(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"Demasiados códigos equivocados. Prueba de nuevo en {VENTANA_MINUTOS} minutos."
        )


class AgenteNoValido(ValueError):
    """Los datos del agente no valen (nombre, protocolo) o no caben más."""


class ServicioDeAgentes:
    """Códigos, emparejamiento, credenciales y revocación de los agentes."""

    def __init__(self, repo: RepositorioDeCuentas):
        self.repo = repo

    # --- En la web, con sesión ------------------------------------------------

    def crear_codigo(self, user_id: str) -> dict:
        """Un código nuevo. Anula los que tuviera pendientes: solo vale el último."""
        ahora = time.time()
        codigo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(LARGO_CODIGO))
        self.repo.invalidar_codigos_agente(user_id, ahora)
        self.repo.crear_codigo_agente({
            "codigo_hash": _hash(codigo),
            "user_id": user_id,
            "creado_en": ahora,
            "caduca_en": ahora + MINUTOS_DE_CODIGO * 60,
            "usado_en": None,
        })
        return {"codigo": formatear_codigo(codigo), "caduca_en": ahora + MINUTOS_DE_CODIGO * 60}

    def listar(self, user_id: str) -> list[dict]:
        return self.repo.listar_agentes(user_id)

    def revocar(self, user_id: str, agent_id: str) -> bool:
        return self.repo.revocar_agente(user_id, agent_id)

    def revocar_todos(self, user_id: str) -> int:
        return self.repo.revocar_agentes(user_id)

    # --- Desde el agente, sin sesión -----------------------------------------

    def consultar(self, codigo: str, origen: str) -> dict:
        """Con qué cuenta se emparejaría este código. **No lo gasta.**"""
        encontrado = self._codigo_vivo(codigo, origen)
        usuario = encontrado["usuario"]
        return {
            "nombre": usuario.get("display_name") or usuario.get("username"),
            "correo": correo_parcial(usuario.get("email")),
            "caduca_en": encontrado["codigo"]["caduca_en"],
        }

    def confirmar(
        self,
        codigo: str,
        origen: str,
        nombre: str,
        sistema: str | None,
        agent_version: str | None,
        protocol_version: int,
    ) -> dict:
        """Canjea el código por la identidad del agente. La credencial sale **una vez**."""
        nombre = (nombre or "").strip()
        if not nombre or len(nombre) > LARGO_MAXIMO_NOMBRE:
            raise AgenteNoValido(
                f"El nombre del equipo es obligatorio y de {LARGO_MAXIMO_NOMBRE} caracteres como mucho."
            )
        if not isinstance(protocol_version, int) or not (
            PROTOCOLO_MINIMO <= protocol_version <= PROTOCOLO_ACTUAL
        ):
            raise AgenteNoValido(
                f"Este agente habla el protocolo {protocol_version} y la nube acepta del "
                f"{PROTOCOLO_MINIMO} al {PROTOCOLO_ACTUAL}. Actualiza el agente."
            )

        encontrado = self._codigo_vivo(codigo, origen)
        user_id = encontrado["usuario"]["id"]

        if len(self.repo.listar_agentes(user_id)) >= MAX_AGENTES:
            raise AgenteNoValido(
                f"Esta cuenta ya tiene {MAX_AGENTES} equipos. Revoca alguno que no uses."
            )

        ahora = time.time()
        # Se gasta ANTES de crear el agente, y solo si seguía vivo: con dos agentes
        # canjeando el mismo código a la vez, el segundo se encuentra sin él.
        if not self.repo.consumir_codigo_agente(_hash(normalizar_codigo(codigo)), ahora):
            raise CodigoNoValido()

        credencial = PREFIJO_CREDENCIAL + secrets.token_urlsafe(32)
        agent_id = "agt-" + secrets.token_hex(6)
        self.repo.crear_agente({
            "id": agent_id,
            "user_id": user_id,
            "nombre": nombre,
            "sistema": (sistema or "")[:LARGO_MAXIMO_SISTEMA] or None,
            "agent_version": (agent_version or "")[:30] or None,
            "protocol_version": protocol_version,
            "credencial_hash": _hash(credencial),
            "estado": "activo",
            "creado_en": ahora,
            "last_seen": ahora,
        })
        return {"agent_id": agent_id, "credencial": credencial, "user_id": user_id}

    def resolver(self, credencial: str | None) -> dict | None:
        """El agente activo de esta credencial y su usuario, o None. Si es la nueva de una
        rotación, **aquí se estrena**: pasa a ser la actual y la vieja deja de valer (3.8)."""
        if not credencial or not credencial.startswith(PREFIJO_CREDENCIAL):
            return None
        resuelto = self.repo.agente_por_credencial(_hash(credencial))
        if resuelto is not None and resuelto["agente"].pop("por_nueva", False):
            ahora = time.time()
            if self.repo.promover_credencial(resuelto["agente"]["id"], _hash(credencial), ahora):
                resuelto["agente"]["credencial_desde"] = ahora
        return resuelto

    def toca_rotar(self, resuelto: dict, ahora: float | None = None) -> bool:
        """Si la credencial de ese agente tiene más de 90 días (decisión mía, 3.8)."""
        desde = resuelto["agente"].get("credencial_desde")
        return isinstance(desde, (int, float)) and (ahora or time.time()) - desde > ROTAR_CADA

    def rotar(self, credencial: str | None) -> str | None:
        """Una credencial nueva para el agente de `credencial`, **pendiente**: la vieja sigue
        valiendo hasta que el agente se conecte con la nueva. Pedir otra antes sustituye la
        pendiente. None si `credencial` no vale."""
        resuelto = self.resolver(credencial)
        if resuelto is None:
            return None
        nueva = PREFIJO_CREDENCIAL + secrets.token_urlsafe(32)
        if not self.repo.preparar_rotacion(resuelto["agente"]["id"], _hash(credencial), _hash(nueva)):
            return None
        return nueva

    def desemparejar(self, credencial: str | None) -> bool:
        """Lo pide el propio agente, con su credencial: se revoca a sí mismo."""
        resuelto = self.resolver(credencial)
        if resuelto is None:
            return False
        return self.repo.revocar_agente(resuelto["usuario"]["id"], resuelto["agente"]["id"])

    # --- Por dentro -----------------------------------------------------------

    def _codigo_vivo(self, codigo: str, origen: str) -> dict:
        ahora = time.time()
        desde = ahora - VENTANA_MINUTOS * 60
        origenes = self.repo.origenes_de_intentos(CLAVE_EMPAREJAR, desde)
        if len(origenes) >= MAX_FALLOS_EN_TOTAL or origenes.count(origen) >= MAX_FALLOS_POR_ORIGEN:
            raise DemasiadosFallos()

        normalizado = normalizar_codigo(codigo)
        encontrado = (
            self.repo.codigo_agente(_hash(normalizado), ahora)
            if len(normalizado) == LARGO_CODIGO else None
        )
        if encontrado is None:
            self.repo.apuntar_intento(CLAVE_EMPAREJAR, origen, ahora)
            raise CodigoNoValido()
        return encontrado


#: Lo que se le dice al modelo cuando la persona tiene un PC emparejado que ahora no está
#: conectado (4.3). Medido con el modelo real, con el agente sin responder: contestó «no
#: tengo acceso al sistema de archivos de tu ordenador» y le pidió que copiara la lista a
#: mano, porque el prompt no distinguía «no tiene PC» de «lo tiene, pero está apagado».
#:
#: 4.5, medido con el modelo real: si la persona insiste («Si lo está»), 3 de 3 veces el
#: modelo olvidaba el aviso y le pedía que subiera o copiara la lista (también en
#: producción, con el PC volviendo de un corte de Wi-Fi). De ahí la segunda frase.
AVISO_PC_DESCONECTADO = (
    "**Esta persona tiene su PC emparejado, pero ahora no está conectado.** Si te pide algo "
    "de su PC, díselo así: «Tu PC no está conectado ahora: enciéndelo o arranca el agente». "
    "Si insiste en que sí lo está, créetelo: puede estar volviendo a conectarse. Dile que "
    "espere unos segundos a que en Ajustes → Tu equipo salga «Conectado ahora» y te lo "
    "vuelva a pedir. Nunca digas que no tienes acceso ni le pidas que copie, pegue o suba "
    "nada de su PC: en cuanto se conecte, lo haces tú."
)

_CON_EQUIPO = None


def aviso_si_esta_desconectado(servicio) -> str:
    """`AVISO_PC_DESCONECTADO` si quien pregunta tiene algún equipo emparejado, o "". Quien
    lo usa lo pone solo cuando ninguno está conectado. Recordado 30 s por persona."""
    global _CON_EQUIPO
    from src.identidad import usuario_actual
    from src.tools.recordado import Recordado

    if _CON_EQUIPO is None:
        _CON_EQUIPO = Recordado()
    usuario = usuario_actual()
    tiene = _CON_EQUIPO.dato((servicio, usuario), lambda: bool(servicio.listar(usuario)), False)
    return AVISO_PC_DESCONECTADO if tiene else ""
