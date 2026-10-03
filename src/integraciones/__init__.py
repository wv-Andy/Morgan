"""
Servicios externos conectados a Morgan (V1.9).

**Esto no es el login de Morgan.** Son dos sistemas distintos y confundirlos es
el error más caro que se puede cometer aquí:

| | Qué significa |
|---|---|
| Login de Morgan | Cuentas propias: usuario, contraseña, sesión. Quién eres |
| Integración | *Tú* autorizas a Morgan a usar tu cuenta de otro servicio |

Se descartó OAuth para *iniciar sesión* por complejidad, y aquí reaparece para
*conectar servicios*. No es una contradicción: en el primer caso GitHub diría
quién eres —y quedarías atado a que su servicio esté en pie para poder entrar—;
en el segundo, ya se sabe quién eres y lo único que se obtiene es permiso para
actuar en tu nombre en un sitio concreto.

La consecuencia práctica: **una integración cuelga siempre de una cuenta de
Morgan**. Sin sesión no hay a quién atribuir la autorización.
"""

from src.integraciones.modelos import Integracion, ServicioExterno
from src.integraciones.secretos import SinClaveDeCifrado, hay_clave

__all__ = ["Integracion", "ServicioExterno", "SinClaveDeCifrado", "hay_clave"]
