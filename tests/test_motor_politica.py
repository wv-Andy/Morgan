"""
El motor de política del agente (3.2).

Lo que exige el plan (§15-§17) y lo que decidí el 2026-09-23:

- **Un solo sitio decide**: toda operación local pasa por `motor.evaluar()`.
- **Lo bloqueado gana sobre lo permitido**: con `C:\\` permitido se puede seguir
  prohibiendo una carpeta concreta.
- **Capacidades una a una**: lo que la persona apaga en su PC ni se anuncia a la nube.
- **Los límites solo se bajan**: nadie amplía lo que sale del PC editando un fichero.
- **La nube no puede cambiar nada de esto** (§16): no hay mensaje que lo toque.
- Y el hueco de la confirmación, listo para la 3.3: el motor ya sabe decir qué
  operaciones exigirán que la persona diga que sí **en su PC**.
"""

import json

import pytest

from src.agente import capacidades, motor
from src.agente import estado as almacen
from src.agente.politica import DE_ESCRITURA, DE_LECTURA, MAX_BYTES, VERSION, Politica
from src.agente.protocolo import MAX_COPIA


@pytest.fixture
def pc(tmp_path):
    permitida = tmp_path / "Trabajo"
    privada = permitida / "Privado"
    fuera = tmp_path / "Fuera"
    for carpeta in (permitida, privada, fuera):
        carpeta.mkdir(parents=True)
    (permitida / "notas.txt").write_text("hola", encoding="utf-8")
    (privada / "diario.txt").write_text("cosas mías", encoding="utf-8")
    (fuera / "ajeno.txt").write_text("no", encoding="utf-8")
    politica = Politica()
    politica.anadir(str(permitida))
    politica.guardar()
    return {"permitida": permitida, "privada": privada, "fuera": fuera}


class TestLoBloqueadoGana:
    def test_una_carpeta_dentro_de_una_permitida_se_puede_prohibir(self, pc):
        politica = Politica.cargar()
        politica.bloquear(str(pc["privada"]))
        politica.guardar()

        assert motor.evaluar("leer", str(pc["permitida"] / "notas.txt"), archivo=True)
        negada = motor.evaluar("leer", str(pc["privada"] / "diario.txt"), archivo=True)
        assert not negada and negada.motivo == "bloqueada"

    def test_y_la_lectura_de_verdad_tambien(self, pc):
        politica = Politica.cargar()
        politica.bloquear(str(pc["privada"]))
        politica.guardar()

        r = capacidades.read_file(str(pc["privada"] / "diario.txt"))
        assert not r["success"] and r["motivo"] == "bloqueada"
        assert "cosas mías" not in str(r)

    def test_se_puede_bloquear_algo_que_aun_no_existe(self, pc):
        """«Nunca leas mi carpeta Privado», antes de crearla."""
        politica = Politica.cargar()
        futura = pc["permitida"] / "Secretos"
        politica.bloquear(str(futura))
        politica.guardar()
        futura.mkdir()
        (futura / "x.txt").write_text("x", encoding="utf-8")

        assert motor.evaluar("leer", str(futura / "x.txt"), archivo=True).motivo == "bloqueada"

    def test_desbloquear_lo_devuelve(self, pc):
        politica = Politica.cargar()
        politica.bloquear(str(pc["privada"]))
        politica.guardar()
        politica = Politica.cargar()
        assert politica.desbloquear(str(pc["privada"]))
        politica.guardar()

        assert motor.evaluar("leer", str(pc["privada"] / "diario.txt"), archivo=True)

    def test_la_busqueda_no_lo_enseña(self, pc):
        politica = Politica.cargar()
        politica.bloquear(str(pc["privada"]))
        politica.guardar()

        rutas = [m["path"] for m in capacidades.search_files("diario")["data"]["matches"]]
        assert rutas == []


class TestCapacidadesUnaAUna:
    def test_lo_apagado_ni_se_anuncia(self, pc):
        politica = Politica.cargar()
        politica.capacidades["copy_file"] = False
        politica.guardar()

        assert "copy_file" not in capacidades.disponibles()
        assert "read_file" in capacidades.disponibles()

    def test_y_si_la_nube_la_pide_igual_se_niega(self, pc):
        """El catálogo del turno se arma con lo anunciado, pero un agente no se fía."""
        politica = Politica.cargar()
        politica.capacidades["read_file"] = False
        politica.guardar()

        r = capacidades.read_file(str(pc["permitida"] / "notas.txt"))
        assert not r["success"] and r["motivo"] == "capacidad_apagada"

    def test_apagar_una_no_toca_las_demas(self, pc):
        politica = Politica.cargar()
        politica.capacidades["search_files"] = False
        politica.guardar()

        assert not capacidades.search_files("notas")["success"]
        assert capacidades.list_files(str(pc["permitida"]))["success"]

    def test_se_encienden_otra_vez(self, pc):
        politica = Politica.cargar()
        politica.capacidades["read_file"] = False
        politica.guardar()
        politica = Politica.cargar()
        politica.capacidades["read_file"] = True
        politica.guardar()

        assert capacidades.read_file(str(pc["permitida"] / "notas.txt"))["success"]


class TestLosLimitesSoloBajan:
    def test_subir_el_de_lectura_no_sirve(self, pc):
        politica = Politica.cargar()
        politica.lectura_bytes = 10 * MAX_BYTES
        politica.guardar()

        assert Politica.cargar().lectura_bytes == MAX_BYTES

    def test_subir_el_de_copia_tampoco(self, pc):
        politica = Politica.cargar()
        politica.copia_bytes = 10 * MAX_COPIA
        politica.guardar()

        assert Politica.cargar().copia_bytes == MAX_COPIA

    def test_bajarlo_si(self, pc):
        grande = pc["permitida"] / "grande.txt"
        grande.write_text("x" * 100_000, encoding="utf-8")
        politica = Politica.cargar()
        politica.lectura_bytes = 2 * 1024
        politica.guardar()

        r = capacidades.read_file(str(grande))
        assert r["success"] and len(r["data"]["content"]) <= 2 * 1024
        assert r["data"]["is_truncated"] and r["data"]["bytes_max"] == 2 * 1024

    def test_un_valor_absurdo_no_rompe_nada(self, pc):
        (almacen.carpeta() / "politica.json").write_text(json.dumps({
            "carpetas": [str(pc["permitida"])],
            "limites": {"lectura_kb": "muchos", "copia_mb": None},
        }), encoding="utf-8")
        politica = Politica.cargar()
        assert politica.lectura_bytes == MAX_BYTES and politica.copia_bytes == MAX_COPIA


class TestElFormatoDelFichero:
    def test_el_de_la_30_se_sigue_leyendo(self, pc):
        """Quien ya tenía el agente solo tiene carpetas: lo demás toma su valor normal."""
        (almacen.carpeta() / "politica.json").write_text(
            json.dumps({"carpetas": [str(pc["permitida"])]}), encoding="utf-8")

        politica = Politica.cargar()
        assert politica.carpetas and politica.bloqueadas == []
        assert all(politica.capacidades[c] for c in DE_LECTURA)
        assert not any(politica.capacidades[c] for c in DE_ESCRITURA)   # nacen apagadas (3.3)
        assert politica.lectura_bytes == MAX_BYTES

    def test_un_fichero_roto_no_deja_el_pc_abierto(self):
        (almacen.carpeta()).mkdir(parents=True, exist_ok=True)
        (almacen.carpeta() / "politica.json").write_text("{esto no es json", encoding="utf-8")

        politica = Politica.cargar()
        assert politica.carpetas == []          # sin carpetas no se lee nada

    def test_se_guarda_con_su_version(self, pc):
        Politica.cargar().guardar()
        datos = json.loads((almacen.carpeta() / "politica.json").read_text(encoding="utf-8"))
        assert datos["version"] == VERSION == 3
        assert set(datos) == {"version", "carpetas", "bloqueadas", "escritura", "programas",
                              "capacidades", "limites", "confirmar"}


class TestLaNubeNoLaCambia:
    """§16 del plan: `Cloud → request`, nunca `Cloud → modifica policy → ejecuta`."""

    def test_ninguna_capacidad_escribe_la_politica(self, pc):
        import inspect

        fuente = inspect.getsource(capacidades)
        assert ".guardar()" not in fuente, "una capacidad está escribiendo la política"
        assert "bloquear" not in fuente and "capacidades[" not in fuente

    def test_una_orden_no_cambia_el_fichero(self, pc):
        import asyncio

        from src.agente.ejecutor import Ejecutor
        from src.agente.protocolo import PROTOCOLO_ACTUAL

        fichero = almacen.carpeta() / "politica.json"
        antes = fichero.read_bytes()
        ejecutor = Ejecutor("agt-1", capacidades.disponibles())
        for capacidad, argumentos in (
            ("read_file", {"path": str(pc["permitida"] / "notas.txt")}),
            ("list_files", {"path": str(pc["permitida"])}),
            ("search_files", {"query": "notas"}),
            ("system_info", {}),
        ):
            asyncio.run(ejecutor.procesar({
                "tipo": "orden", "protocol_version": PROTOCOLO_ACTUAL, "request_id": "r",
                "command_id": f"c-{capacidad}", "agent_id": "agt-1", "capability": capacidad,
                "arguments": argumentos, "vence_en_ms": 5000,
            }))
        assert fichero.read_bytes() == antes

    def test_el_protocolo_no_tiene_ningun_mensaje_de_politica(self):
        import inspect

        from src.agente import canal, ejecutor

        for modulo in (canal, ejecutor):
            fuente = inspect.getsource(modulo)
            for palabra in ("politica.guardar", "anadir(", "bloquear(", "capacidades["):
                assert palabra not in fuente, (modulo.__name__, palabra)


class TestLaConfirmacion:
    """Qué pide permiso en el PC. Desde la 3.3, **solo borrar** (decisión mía): lo
    demás pasa por un plan aprobado en el móvil. Las pruebas de la escritura de verdad
    están en test_agente_escritura.py."""

    @pytest.mark.parametrize("operacion", ["listar", "leer", "buscar", "copiar", "estado"])
    def test_leer_no_pide_permiso(self, pc, operacion):
        decision = motor.evaluar(operacion, str(pc["permitida"]), archivo=None)
        assert not decision.exige_confirmacion

    @pytest.mark.parametrize("capacidad, pide", [("create_file", False), ("edit_file", False),
                                                  ("move_file", False), ("delete_file", True)])
    def test_solo_borrar(self, pc, capacidad, pide, tmp_path, monkeypatch):
        # Las carpetas de las pruebas están dentro de %LOCALAPPDATA%, donde no se
        # escribe nunca: se lleva a otro sitio (como en test_agente_escritura.py).
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "_local"))
        politica = Politica.cargar()
        politica.permitir_escritura(str(pc["permitida"]))
        politica.capacidades[capacidad] = True
        decision = motor.evaluar(motor.OPERACION_DE[capacidad], str(pc["permitida"] / "notas.txt"),
                                 capacidad=capacidad, politica=politica)
        assert decision and decision.exige_confirmacion is pide

    def test_ejecutar_nace_apagado(self, pc):
        """Existe desde la 3.5, pero como todo lo que cambia cosas, apagado por defecto."""
        ruta = str(pc["permitida"])
        assert motor.evaluar("ejecutar", ruta).motivo == "capacidad_desconocida"
        assert motor.evaluar("ejecutar", ruta, capacidad="run_change_command").motivo == "capacidad_apagada"
        assert motor.evaluar("terminar", capacidad="kill_process").motivo == "capacidad_apagada"

    def test_leer_una_carpeta_no_deja_escribirla(self, pc):
        politica = Politica.cargar()
        politica.capacidades["create_file"] = True
        decision = motor.evaluar("escribir", str(pc["permitida"] / "x.txt"), capacidad="create_file",
                                 nueva=True, politica=politica)
        assert not decision and decision.motivo == "sin_carpetas_escritura"


class TestTodoPasaPorElMotor:
    """El gate de la 3.2: ninguna capacidad decide por su cuenta."""

    def test_ninguna_capacidad_resuelve_rutas_por_su_cuenta(self):
        import inspect

        fuente = inspect.getsource(capacidades)
        # `carpetas_personales` es la excepción: pregunta por rutas que el motor no
        # conoce (las de Windows) y solo para NO nombrar las que están fuera.
        usos = fuente.count("politica.resolver(")
        assert usos == 1, f"{usos} capacidades resuelven rutas sin pasar por el motor"

    @pytest.mark.parametrize("operacion", ["listar", "leer", "buscar", "copiar"])
    def test_lo_de_fuera_se_niega_igual_en_todas(self, pc, operacion):
        decision = motor.evaluar(operacion, str(pc["fuera"] / "ajeno.txt"), archivo=True)
        assert not decision and decision.motivo == "fuera"

    def test_sin_ruta_solo_valen_las_que_no_la_necesitan(self, pc):
        assert motor.evaluar("estado")
        assert motor.evaluar("listar")
        assert motor.evaluar("buscar")
        negada = motor.evaluar("copiar")
        assert not negada and negada.motivo == "ruta_invalida"


class TestLoQueEncontroElAtaqueDeLa325:
    """24 intentos contra la política en el Windows de verdad. Dos hallazgos, los dos
    del mismo tipo: el motor **decía que sí cuando no entendía la pregunta**."""

    def test_una_operacion_que_el_motor_no_conoce_se_deniega(self, pc):
        """Si mañana una capacidad nueva se olvida de registrarse aquí, tiene que fallar
        cerrando. Antes pasaba: no tenía capacidad que mirar ni estaba entre las
        mutaciones, así que caía en el «sí» del final."""
        for operacion in ("hackear", "", "LEER", "leer_todo", None):
            decision = motor.evaluar(operacion, str(pc["permitida"] / "notas.txt"), archivo=True)
            assert not decision, operacion
            assert decision.motivo == "operacion_desconocida", operacion

    def test_las_que_sí_conoce_siguen_funcionando(self, pc):
        from src.agente.politica import ENCENDIDA_POR_DEFECTO

        for operacion in motor.LECTURA:
            ruta = None if operacion in motor.SIN_RUTA else str(pc["permitida"] / "notas.txt")
            decision = motor.evaluar(operacion, ruta)
            if ENCENDIDA_POR_DEFECTO.get(motor.CAPACIDAD_DE[operacion], True):
                assert decision, operacion
            else:
                # La terminal y los procesos (3.5) nacen apagados: se conocen, y dicen no.
                assert decision.motivo == "capacidad_apagada", operacion

    @pytest.mark.parametrize("roto", [
        {"carpetas": "no es una lista"},
        {"bloqueadas": None},
        {"capacidades": "todas"},
        {"limites": []},
        {"confirmar": 7},
        {"version": "dos"},
    ])
    def test_un_campo_del_tipo_que_no_toca_deja_el_pc_cerrado(self, pc, roto):
        """Ignorar un `bloqueadas: null` borraría los bloqueos y dejaría el PC **más
        abierto**. Un fichero que no se entiende no se interpreta a medias."""
        entero = {"carpetas": [str(pc["permitida"])], "bloqueadas": [str(pc["privada"])], **roto}
        (almacen.carpeta() / "politica.json").write_text(json.dumps(entero), encoding="utf-8")

        politica = Politica.cargar()
        assert politica.carpetas == [] and politica.bloqueadas == []
        assert not capacidades.read_file(str(pc["permitida"] / "notas.txt"))["success"]

    def test_un_campo_que_falta_es_normal(self, pc):
        """Faltar está bien: es el fichero de la 3.0, que solo tenía carpetas."""
        (almacen.carpeta() / "politica.json").write_text(
            json.dumps({"carpetas": [str(pc["permitida"])]}), encoding="utf-8")
        assert capacidades.read_file(str(pc["permitida"] / "notas.txt"))["success"]

    @pytest.mark.parametrize("truco", ["mayusculas", "punto", "barras", "subir_y_bajar"])
    def test_no_se_rodea_un_bloqueo_escribiendo_la_ruta_de_otra_forma(self, pc, truco):
        politica = Politica.cargar()
        politica.bloquear(str(pc["privada"]))
        politica.guardar()

        diario = pc["privada"] / "diario.txt"
        rutas = {
            "mayusculas": str(diario).upper(),
            "punto": str(pc["privada"]) + "\.\diario.txt",
            "barras": str(diario).replace("\\", "/"),
            "subir_y_bajar": str(pc["privada"] / ".." / "Privado" / "diario.txt"),
        }
        r = capacidades.read_file(rutas[truco])
        assert not r["success"] and "cosas mías" not in str(r)
