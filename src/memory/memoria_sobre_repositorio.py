"""
La memoria persistente sobre la capa de repositorios.

**El hueco que esto tapa.** `MemoryManager` se construía siempre con su
almacenamiento por defecto, que es SQLite sobre un fichero local. En tu equipo es
lo correcto. En la nube no: el disco de Render es efímero, así que **todo lo que
Morgan recordaba se perdía en cada reinicio** — y en el plan gratuito el servicio
duerme a los quince minutos de inactividad.

Desde fuera no se veía como una avería. `POST /memory` respondía 200, la lista de
recuerdos los mostraba, y al día siguiente no había ninguno. Peor que un error:
una promesa que se incumple en silencio.

`SupabaseMemoryRepository` existía desde la V1.3 y estaba completo. Lo que
faltaba era esto: la traducción entre las dos interfaces, que se parecen tanto
que la falta pasó desapercibida.

| `MemoryStorage` (lo que usa el agente) | `MemoryRepository` (la capa de datos) |
|---|---|
| `remember` | `upsert` |
| `recall` | `search` |
| `forget` | `delete` |
| `clear` | `clear` |

El historial no pasa por aquí: va por `ConversationHistory`, que habla con los
repositorios directamente. La interfaz tuvo `save_message` y `get_messages`
por herencia de la V0.5, sin que nadie los llamara; se retiraron en la 2.0.11,
y un `get_messages` que se quedó en esta clase, en la 2.0.13.

**Desde la 2.0.13 también es la memoria del Morgan local**, sobre el
repositorio de SQLite. Había una segunda implementación, `SQLiteMemoryStorage`,
y no se comportaba igual: ver `tests/test_memoria_unificada.py`.
"""

import logging

from src.memory.base import MemoryStorage
from src.memory.repositories import MemoryRepository

logger = logging.getLogger(__name__)


class _SaludDeLaFabrica:
    """Traduce `health()` de la fábrica a `healthy()`, que es lo que se mira.

    `MemoryManager.health()` busca un atributo `db` y le llama `healthy()`. La
    fábrica de Supabase expone lo mismo con otro nombre, `health()`. Pasarla
    directamente daba un `AttributeError` en la comprobación de estado — o sea,
    el chequeo de salud rompiéndose él mismo, que es la peor forma de fallar.
    """

    def __init__(self, fabrica):
        self.fabrica = fabrica

    def healthy(self) -> tuple[bool, str | None]:
        return self.fabrica.health()


class MemoriaSobreRepositorio(MemoryStorage):
    """Adapta un `MemoryRepository` a lo que espera `MemoryManager`."""

    def __init__(self, repositorio: MemoryRepository, salud=None):
        self.repositorio = repositorio
        # En la nube no hay base local que consultar, así que la salud la
        # responde la fábrica por su propia conexión.
        self.db = _SaludDeLaFabrica(salud) if salud is not None else None

    def remember(self, key: str, value: str, category: str = "general") -> dict:
        registro = self.repositorio.upsert(key, value, category)
        # Se devuelve la misma forma que la versión SQLite: quien llama la usa
        # para construir la respuesta HTTP, y cambiarla rompería el contrato de
        # la API sin que nada avisara.
        return {
            "key": registro.key,
            "value": registro.value,
            "category": registro.category,
            "updated_at": registro.updated_at,
        }

    def recall(self, query: str | None = None, category: str | None = None, limit: int = 100) -> list[dict]:
        return [r.to_dict() for r in self.repositorio.search(query=query, category=category, limit=limit)]

    def forget(self, key: str) -> bool:
        return self.repositorio.delete(key)

    def clear(self, category: str | None = None) -> int:
        return self.repositorio.clear(category)


def sobre_sqlite(db_path=None, database=None) -> MemoriaSobreRepositorio:
    """La memoria sobre el repositorio de SQLite, en un fichero concreto o el de siempre.

    Es lo que monta `MemoryManager()` sin argumentos, y lo que usan las pruebas
    que necesitan una base aparte. Un solo sitio para construirla: si hubiera dos,
    volverían a poder divergir, que es justo lo que se acaba de arreglar.
    """
    from src.memory.db import Database
    from src.memory.sqlite_repositories import SQLiteRepositoryFactory

    fabrica = SQLiteRepositoryFactory(database or Database(db_path))
    return MemoriaSobreRepositorio(fabrica.memories, salud=fabrica)
