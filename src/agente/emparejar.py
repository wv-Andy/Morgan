"""
Emparejar este PC con una cuenta de Morgan, y desemparejarlo (3.0-C).

El flujo del contrato (docs/agente-local.md §5):

1. La persona pide un código en la web (Ajustes → Tu equipo).
2. Lo teclea aquí.
3. El agente **consulta** el código: la nube dice con qué cuenta se emparejaría,
   sin gastarlo.
4. **El agente enseña esa cuenta y pregunta en el PC.** Sin un «s» explícito, no se
   empareja. Es lo que corta a quien genera un código en *su* cuenta y convence a
   otra persona de teclearlo (amenaza H).
5. Si se confirma, canjea el código por el `agent_id` y la credencial, que se guarda
   cifrada.

Las preguntas y los mensajes pasan por funciones que se pueden sustituir, para
probar el flujo sin consola.
"""

import platform
from typing import Callable

from src import __version__
from src.agente import estado as almacen
from src.agente.protocolo import PROTOCOLO_ACTUAL, normalizar_codigo


class EmparejamientoFallido(RuntimeError):
    pass


def _sistema() -> str:
    return f"{platform.system()} {platform.release()}".strip()


def _error(respuesta) -> str:
    try:
        return respuesta.json()["error"]["message"]
    except Exception:
        return f"la nube respondió {respuesta.status_code}"


def consultar(nube: str, codigo: str, http=None) -> str:
    """De qué cuenta es este código, **sin usarlo** (sigue valiendo hasta que caduque). Lo que
    hay que enseñar a la persona antes de emparejar: alguien podría darle un código de SU
    cuenta. La ventana del programa de Windows (5.0) lo pregunta con esto, y después empareja
    con `--si`."""
    import httpx

    cliente = http or httpx.Client(timeout=90)
    r = cliente.post(f"{nube.rstrip('/')}/agente/emparejar/consultar", json={"codigo": normalizar_codigo(codigo)})
    if r.status_code != 200:
        raise EmparejamientoFallido(_error(r))
    cuenta = r.json()["cuenta"]
    return cuenta["nombre"] + (f" ({cuenta['correo']})" if cuenta.get("correo") else "")


def emparejar(
    nube: str,
    codigo: str,
    nombre: str,
    preguntar: Callable[[str], str] = input,
    decir: Callable[[str], None] = print,
    http=None,
) -> almacen.Emparejamiento | None:
    """Empareja este PC. Devuelve el emparejamiento, o None si la persona dijo que no."""
    import httpx

    if almacen.estado() is not almacen.EstadoAgente.UNPAIRED:
        raise EmparejamientoFallido(
            "Este PC ya está emparejado. Desemparéjalo primero: python -m src.agente desemparejar"
        )

    nube = nube.rstrip("/")
    cliente = http or httpx.Client(timeout=90)
    codigo = normalizar_codigo(codigo)
    etiqueta = consultar(nube, codigo, http=cliente)

    decir(f"Este código empareja este PC con la cuenta de Morgan: {etiqueta}")
    decir("Desde esa cuenta se podrá pedir a este PC que haga cosas, siempre con sus propias reglas.")
    respuesta = preguntar("¿Es tu cuenta y quieres emparejarlo? Escribe «s» para sí: ")
    if respuesta.strip().lower() not in ("s", "si", "sí"):
        decir("No se ha emparejado. El código sigue sin usar hasta que caduque.")
        return None

    r = cliente.post(f"{nube}/agente/emparejar/confirmar", json={
        "codigo": codigo,
        "nombre": nombre,
        "sistema": _sistema(),
        "agent_version": __version__,
        "protocol_version": PROTOCOLO_ACTUAL,
    })
    if r.status_code != 200:
        raise EmparejamientoFallido(_error(r))
    datos = r.json()

    emparejamiento = almacen.Emparejamiento(
        agent_id=datos["agent_id"], nube=nube, nombre=nombre, cuenta=etiqueta,
        emparejado_en=almacen.ahora(),
    )
    almacen.guardar(emparejamiento, datos["credencial"])
    decir(f"Emparejado como «{nombre}» ({datos['agent_id']}).")
    return emparejamiento


def rotar_credencial(credencial: str, http=None) -> str:
    """Pide a la nube una credencial nueva con la actual y la guarda (3.8). La nube la deja
    pendiente: la vieja vale hasta que el agente se conecte con la nueva. Lanza si no se
    pudo; entonces no se ha tocado nada en el PC."""
    import httpx

    datos = almacen.cargar()
    cliente = http or httpx.Client(timeout=30)
    r = cliente.post(f"{datos.nube}/agente/rotar", headers={"Authorization": f"Bearer {credencial}"})
    r.raise_for_status()
    nueva = r.json().get("credencial")
    if not isinstance(nueva, str) or not nueva.startswith("mga_"):
        raise ValueError("La nube no devolvió una credencial.")
    almacen.guardar_credencial(nueva)
    return nueva


def desemparejar(decir: Callable[[str], None] = print, http=None) -> bool:
    """Revoca este agente en la nube y borra la credencial del PC.

    **Se borra aunque la nube no conteste**: el PC deja de poder entrar pase lo que
    pase. Si la nube no se enteró, el equipo sigue en la lista de la web, desde donde
    se revoca igual.
    """
    import httpx

    emparejamiento = almacen.cargar()
    credencial = almacen.credencial()
    if emparejamiento is None or credencial is None:
        almacen.borrar()
        decir("Este PC no estaba emparejado.")
        return False

    avisada = False
    try:
        cliente = http or httpx.Client(timeout=30)
        r = cliente.post(
            f"{emparejamiento.nube}/agente/desemparejar",
            headers={"Authorization": f"Bearer {credencial}"},
        )
        avisada = r.status_code == 200
    except Exception:
        avisada = False
    finally:
        almacen.borrar()

    decir(
        "Desemparejado: la nube ya no lo reconoce y este PC ha olvidado su credencial."
        if avisada else
        "Este PC ha olvidado su credencial, pero la nube no contestó: revócalo también "
        "en la web (Ajustes → Tu equipo)."
    )
    return avisada
