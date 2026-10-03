"""
Cómo se establece el propietario de Morgan (identidad, V2.0 adelantada).

El Owner no se crea registrándose ni se decide con una condición en el código: se
**promociona** una cuenta ya existente, una sola vez, en un arranque controlado.

## Cómo funciona

Se define `MORGAN_OWNER_EMAIL` con la dirección de la cuenta que debe ser
propietaria. Al arrancar, Morgan mira:

1. ¿Ya hay un Owner? → no hace nada. **Nunca hay dos.**
2. ¿Existe una cuenta con ese correo? → la promociona y lo deja en la auditoría.
3. ¿No existe todavía? → no la crea. Espera a que se registre.

Ese último punto es deliberado. Crear la cuenta aquí obligaría a inventar una
contraseña, escribirla en algún sitio o dejarla vacía, y las tres opciones son
peores que pedirle al dueño que se registre como cualquiera y se promocione al
reiniciar.

## Lo que este módulo NO hace

- **No hay contraseña maestra.** El propietario recupera su acceso por el mismo
  camino que los demás. Una vía de emergencia paralela es una puerta trasera con
  otro nombre.
- **No se degrada solo.** Quitar `MORGAN_OWNER_EMAIL` no deja a Morgan sin dueño:
  el rol está en la base, no en el entorno. La variable solo sirve para el
  arranque inicial, y una vez hecho su trabajo puede borrarse.
- **No promociona a nadie más.** Si ya hay un Owner y la variable señala a otra
  persona, se registra un aviso y no se toca nada. Cambiar de propietario es una
  operación administrativa explícita, no un efecto de reiniciar el servidor.
"""

import logging
import os

from src.identidad.roles import Rol

logger = logging.getLogger(__name__)


def correo_configurado() -> str:
    return (os.getenv("MORGAN_OWNER_EMAIL") or "").strip().lower()


def asegurar_propietario(repo, auditoria=None) -> str | None:
    """Promociona la cuenta configurada, si procede. Devuelve su id, o None.

    Se llama al arrancar. Es idempotente: puede ejecutarse en cada reinicio sin
    consecuencias, que es justo lo que hará en la práctica.
    """
    correo = correo_configurado()
    existente = repo.propietario()

    if existente is not None:
        if correo and (existente.get("email") or "").lower() != correo:
            # Se avisa pero no se toca nada. Reiniciar el servidor no puede ser
            # una forma de traspasar la propiedad de la instalacion.
            logger.warning(
                "MORGAN_OWNER_EMAIL señala a una cuenta distinta de la que ya es "
                "propietaria. No se cambia nada: el traspaso de propiedad es una "
                "operación administrativa explícita, no un efecto de reiniciar."
            )
        return existente["id"]

    if not correo:
        # Sin variable y sin propietario, Morgan funciona igual: es lo normal en
        # una instalacion local, donde no hay a quien distinguir.
        return None

    fila = repo.buscar(correo)
    if fila is None or (fila.get("email") or "").lower() != correo:
        logger.info(
            "MORGAN_OWNER_EMAIL está configurada pero esa cuenta aún no existe. "
            "Regístrate con esa dirección y reinicia: entonces se promocionará."
        )
        return None

    repo.actualizar(fila["id"], {"role": Rol.OWNER.value})
    logger.info("Cuenta promocionada a propietaria: %s", fila["id"])

    if auditoria is not None:
        # Queda registrado porque es la operacion mas privilegiada que ocurre en
        # toda la vida de una instalacion.
        try:
            auditoria.registrar_evento(
                accion="owner.bootstrap",
                actor="sistema",
                objetivo=fila["id"],
                resultado="ok",
                detalle="Promoción inicial desde MORGAN_OWNER_EMAIL",
            )
        except Exception:
            logger.warning("No se pudo auditar la promoción", exc_info=True)

    return fila["id"]
