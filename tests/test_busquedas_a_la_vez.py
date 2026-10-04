"""
Varias búsquedas en una sola llamada (4.21): `search_web(consultas=[...])`.

Medido con el modelo real: «la altura del Everest, el río más largo de Sudamérica y el año de
la llegada a la Luna» eran seis vueltas al modelo, una búsqueda en cada una (16 s), y cada
vuelta reenviaba todo lo anterior. Lo que haría daño si fallase: que fueran en fila y no a la
vez, que una búsqueda caída tirara las demás, o que las direcciones encontradas dejaran de
poder abrirse tras leer el PC (la protección de la 3.1-B lee el resultado de la búsqueda).
"""

import threading
import time

import pytest

from src.tools import web
from src.tools.web import MAX_CONSULTAS, SearchWebTool


@pytest.fixture
def herramienta(monkeypatch):
    """Un Google de mentira: tarda 0,3 s y apunta qué le pidieron."""
    pedidas, activas, pico = [], [0], [0]
    cerrojo = threading.Lock()

    def google(self, query, max_results):
        with cerrojo:
            pedidas.append((query, max_results))
            activas[0] += 1
            pico[0] = max(pico[0], activas[0])
        time.sleep(0.3)
        with cerrojo:
            activas[0] -= 1
        if query == "rota":
            raise ValueError("google rechazó la consulta (400)")
        return {"results": [{"title": query, "url": f"https://ejemplo.co/{query.replace(' ', '-')}",
                             "snippet": "..."}][:max_results]}

    monkeypatch.setattr(SearchWebTool, "_google", google)
    h = SearchWebTool(serper_api_key="clave-falsa", orden=("google",))
    h.pedidas, h.pico = pedidas, pico
    return h


class TestVariasALaVez:
    def test_van_en_paralelo(self, herramienta):
        inicio = time.monotonic()
        r = herramienta.execute(consultas=["everest altura", "rio mas largo sudamerica", "llegada a la luna"])
        assert r["success"]
        assert time.monotonic() - inicio < 0.6, "tres de 0,3 s en fila serían 0,9"
        assert herramienta.pico[0] == 3
        assert [c["query"] for c in r["data"]["consultas"]] == [
            "everest altura", "rio mas largo sudamerica", "llegada a la luna"]

    def test_como_mucho_las_maximas_y_sin_repetir(self, herramienta):
        r = herramienta.execute(consultas=["a", "b", "a", "c", "d", "e", "f"])
        assert [c["query"] for c in r["data"]["consultas"]] == ["a", "b", "c", "d"][:MAX_CONSULTAS]

    def test_la_query_cuenta_como_la_primera(self, herramienta):
        r = herramienta.execute(query="a", consultas=["b"])
        assert [c["query"] for c in r["data"]["consultas"]] == ["a", "b"]

    def test_menos_resultados_por_consulta_si_no_se_dice(self, herramienta):
        herramienta.execute(consultas=["a", "b"])
        assert {n for _, n in herramienta.pedidas} == {web.RESULTADOS_POR_CONSULTA}

    @pytest.mark.parametrize("pedidos, salen", [(1, 3), (4, 4), (10, 5), ("x", 3)])
    def test_entre_tres_y_cinco_por_consulta(self, herramienta, pedidos, salen):
        """Medido: el modelo pedía 1 por consulta, se quedaba corto y volvía a buscar cada
        dato por separado (7 llamadas al modelo en vez de 2)."""
        herramienta.execute(consultas=["a", "b"], max_results=pedidos)
        assert {n for _, n in herramienta.pedidas} == {salen}

    def test_una_caida_no_tira_las_demas(self, herramienta):
        r = herramienta.execute(consultas=["buena", "rota"])
        assert r["success"]
        por_query = {c["query"]: c for c in r["data"]["consultas"]}
        assert por_query["buena"]["results"] and "error" in por_query["rota"]

    def test_si_fallan_todas_es_un_fallo(self, herramienta):
        assert herramienta.execute(consultas=["rota"])["success"] is False

    def test_una_sola_sigue_como_siempre(self, herramienta):
        r = herramienta.execute(query="capital de australia")
        assert r["success"] and "results" in r["data"] and "consultas" not in r["data"]

    def test_sin_nada_que_buscar(self, herramienta):
        assert herramienta.execute()["success"] is False
        assert herramienta.execute(consultas=["  "])["success"] is False


class TestLaProteccionDeFugaSigue:
    def test_lo_encontrado_en_varias_se_puede_abrir(self, herramienta):
        import json

        from src.agent import fuga

        r = herramienta.execute(consultas=["everest altura", "luna"])
        conocidas = fuga.conocidas(json.dumps(r, default=str))
        assert fuga.se_puede_abrir("https://ejemplo.co/everest-altura", conocidas)
        assert fuga.se_puede_abrir("https://ejemplo.co/luna", conocidas)
        assert not fuga.se_puede_abrir("https://otro.co/x", conocidas)
