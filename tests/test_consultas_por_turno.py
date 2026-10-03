"""
Cuántas veces habla Morgan con la base de datos al guardar un turno.

**Por qué se cuenta.** Con Render en Frankfurt y Supabase en `us-east-1`, cada
consulta cuesta ~220 ms de ida y vuelta. Un turno hacía **once**, y eso son 2,4
segundos que no dependen de lo que se le pida a Morgan: se pagan igual con «di
hola» que con un encargo de investigación.

La instrumentación las contó por tabla y operación, y salieron tres repetidas:

```
bd.sessions.get: 2      bd.sessions.post: 2      bd.messages.get: 2
```

`save_turn` llamaba a `sessions.create()` y justo después a `messages.add_many`,
que **ya se asegura de que la sesión exista** —lo hacen las dos
implementaciones—. Así que la primera dejaba la base como la iba a dejar la
segunda. Y no era una consulta: `create()` empieza con un `get()`, que en
Supabase son dos viajes —la fila y, aparte, el recuento de mensajes que ahí no
le importa a nadie— más el upsert. **Tres viajes por turno, para nada.**

Estas pruebas cuentan las llamadas al repositorio en lugar de medir tiempos: un
tiempo depende de la red y de la máquina, y un recuento no. Si alguien vuelve a
añadir una consulta al camino del turno, aquí se ve.
"""

import pytest

from src.agent.history import ConversationHistory
from src.models.base import ChatMessage


class RepoContado:
    """Cuenta las llamadas de cada repositorio, sin tocar ninguna base."""

    def __init__(self):
        self.llamadas: list[str] = []
        padre = self

        class Sesiones:
            def create(self, session_id, title=None, metadata=None):
                padre.llamadas.append("sessions.create")
                return None

            def get(self, session_id):
                padre.llamadas.append("sessions.get")
                return None

            def touch(self, session_id, title=None):
                padre.llamadas.append("sessions.touch")

        class Mensajes:
            def add_many(self, mensajes):
                # Las dos implementaciones se aseguran aquí de que la sesión
                # exista, y por eso `save_turn` no tiene que hacerlo.
                padre.llamadas.append("messages.add_many(+ensure)")
                return len(mensajes)

            def list_for_session(self, session_id, limit=None, roles=None):
                padre.llamadas.append("messages.list_for_session")
                return []

        self.sessions = Sesiones()
        self.messages = Mensajes()


@pytest.fixture
def historial():
    repos = RepoContado()
    return ConversationHistory(repos), repos


class TestGuardarUnTurno:
    def test_no_asegura_la_sesion_dos_veces(self, historial):
        hist, repos = historial

        hist.save_turn("s1", [ChatMessage(role="user", content="hola")])

        assert "sessions.create" not in repos.llamadas, (
            "`save_turn` vuelve a llamar a `sessions.create()`. `add_many` ya "
            "asegura la sesión, y `create()` cuesta tres viajes de red en "
            "Supabase: la fila, el recuento de mensajes y el upsert"
        )

    def test_habla_con_la_base_dos_veces_y_no_mas(self, historial):
        """Guardar y actualizar la marca de tiempo. Nada más.

        El número está escrito a propósito: si alguien añade una consulta al
        camino del turno, esta prueba lo dice. Cada una cuesta ~220 ms en
        producción.
        """
        hist, repos = historial

        hist.save_turn("s1", [ChatMessage(role="user", content="hola")])

        assert repos.llamadas == [
            "messages.add_many(+ensure)",
            "sessions.touch",
        ], f"El camino del turno cambió: {repos.llamadas}"

    def test_un_turno_sin_nada_que_guardar_no_toca_la_base(self, historial):
        hist, repos = historial

        assert hist.save_turn("s1", []) == 0
        assert repos.llamadas == []

    def test_sigue_guardando_los_mensajes(self, historial):
        """Lo que se quitó era repetido, no necesario."""
        hist, repos = historial

        guardados = hist.save_turn("s1", [
            ChatMessage(role="user", content="hola"),
            ChatMessage(role="model", content="qué tal"),
        ])

        assert guardados == 2

    def test_y_sigue_poniendo_el_titulo(self, historial):
        hist, repos = historial

        hist.save_turn(
            "s1",
            [ChatMessage(role="user", content="hola")],
            title_hint="Una conversación",
        )

        assert "sessions.touch" in repos.llamadas


class TestCargarElHistorial:
    def test_una_sola_consulta(self, historial):
        """Hidratar la conversación es una lectura, no dos: el recuento de
        mensajes que trae `sessions.get()` no hace falta para el contexto.
        """
        hist, repos = historial

        hist.load("s1")

        assert repos.llamadas == ["messages.list_for_session"]
