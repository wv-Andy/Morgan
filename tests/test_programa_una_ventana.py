"""
Morgan para Windows en una sola ventana (5.3): lo que la web puede pedirle al programa.

Desde la 5.3 la ventana del programa carga la web de Morgan, y la web le pide cosas por el
puente de Tauri. Lo decidí el 2026-10-04: **solo lo inofensivo** (el estado, pausar y
reanudar, lo último que hizo, abrir «Qué puede hacer y qué carpetas ve», la versión, ir a la
pantalla de emparejar). **Emparejar no**: es lo único que da acceso al PC, y una web
comprometida podría conectarlo a otra cuenta.

Aquí se fija sin compilar nada, leyendo los ficheros del programa:

- cada orden está declarada en `build.rs` (con la lista, Tauri exige un permiso para cada una;
  sin ella, las deja todas a cualquier página local), registrada y con permiso local;
- la capacidad de la web es solo para `https://morgan-ia.vercel.app`, y sus permisos son
  exactamente los inofensivos: ni emparejar, ni consultar códigos, ni nada de Tauri;
- la web pide al programa lo mismo que se le deja, ni más ni menos (`web/src/lib/programa.ts`);
- no queda una ventana fija en `tauri.conf.json` (la ventana se crea en `main.rs`, con su
  regla de navegación).

Lo que pasa de verdad en la ventana se mide en GitHub (`empaquetado/probar_ventana.py`).
"""

import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TAURI = RAIZ / "escritorio" / "src-tauri"

INOFENSIVAS = {"resumen", "pausar", "reanudar", "ultimas", "abrir_ajustes", "pantalla_emparejar",
               "programa", "actualizar_ahora"}
PELIGROSAS = {"emparejar", "consultar", "desemparejar", "estado", "abrir_web"}


def _json(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _ordenes_de_main() -> set[str]:
    texto = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    return set(re.findall(r"#\[tauri::command\]\s*(?:async\s+)?fn\s+(\w+)", texto))


def _registradas() -> set[str]:
    texto = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    bloque = re.search(r"generate_handler!\[(.*?)\]", texto, re.S).group(1)
    return {o.strip() for o in bloque.split(",") if o.strip()}


def _declaradas() -> set[str]:
    texto = (TAURI / "build.rs").read_text(encoding="utf-8")
    bloque = re.search(r"const ORDENES: &\[&str\] = &\[(.*?)\];", texto, re.S).group(1)
    return set(re.findall(r'"(\w+)"', bloque))


def _permiso(orden: str) -> str:
    return "allow-" + orden.replace("_", "-")


def test_todas_las_ordenes_declaradas_registradas_y_con_permiso_local():
    ordenes = _ordenes_de_main()
    assert ordenes == INOFENSIVAS | PELIGROSAS
    assert _registradas() == ordenes
    assert _declaradas() == ordenes, "sin la lista de build.rs, Tauri deja todas a cualquier página local"
    texto = (TAURI / "build.rs").read_text(encoding="utf-8")
    assert ".app_manifest(" in texto and ".commands(ORDENES)" in texto
    local = _json(TAURI / "capabilities" / "default.json")
    assert "remote" not in local, "la capacidad con todo es solo para la pantalla del programa"
    assert {_permiso(o) for o in ordenes} <= set(local["permissions"])


def test_la_web_solo_lo_inofensivo():
    web = _json(TAURI / "capabilities" / "web.json")
    assert web["remote"]["urls"] == ["https://morgan-ia.vercel.app/*"]
    assert web["windows"] == ["main"]
    assert set(web["permissions"]) == {_permiso(o) for o in INOFENSIVAS}
    for orden in PELIGROSAS:
        assert _permiso(orden) not in web["permissions"]
    # Nada de Tauri (eventos, ventanas, el sistema…): solo las órdenes del programa.
    assert not [p for p in web["permissions"] if ":" in p]


def test_la_web_pide_lo_que_se_le_deja():
    texto = (RAIZ / "web" / "src" / "lib" / "programa.ts").read_text(encoding="utf-8")
    pedidas = set(re.findall(r"pedir\('(\w+)'\)", texto))
    assert pedidas == INOFENSIVAS


def test_una_sola_ventana_creada_en_el_programa():
    conf = _json(TAURI / "tauri.conf.json")
    assert "windows" not in conf["app"], "la ventana se crea en main.rs, con su regla de navegación"
    texto = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    assert texto.count("WebviewWindowBuilder::new(") == 1
    assert "bandeja::navegacion(url.scheme(), url.host_str())" in texto
    # Lo que la bandeja manda a la web lleva el nombre que escucha la web.
    assert "CustomEvent('morgan-programa'" in texto
    assert "EVENTO_DEL_PROGRAMA = 'morgan-programa'" in (RAIZ / "web" / "src" / "lib" / "programa.ts").read_text(encoding="utf-8")


def test_las_que_lanzan_el_agente_no_congelan_la_ventana():
    """Una orden síncrona corre en el hilo de la ventana: pausar (hasta ~2 s) la congelaba."""
    texto = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    for orden in ("estado", "pausar", "reanudar", "ultimas", "consultar", "emparejar", "desemparejar"):
        assert re.search(rf"#\[tauri::command\]\s*async fn {orden}\(", texto), orden


def test_la_depuracion_solo_en_la_copia_de_prueba():
    """La prueba de la ventana abre el protocolo de depuración de WebView2 (con él, cualquier
    programa del PC podría pedirle a la página lo que quisiera). Solo la copia de prueba lo
    lleva: la característica `depurar` no es por defecto, el código está tras ella, y el
    instalador que se publica se compila sin ella."""
    cargo = (TAURI / "Cargo.toml").read_text(encoding="utf-8")
    assert re.search(r"^depurar = \[\]$", cargo, re.M)
    assert "default" not in cargo.split("[features]")[1].split("[")[0]
    texto = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    assert texto.count("remote-debugging-port") == 1
    i = texto.index("remote-debugging-port")
    assert '#[cfg(feature = "depurar")]' in texto[i - 600:i]
    flujo = (RAIZ / ".github" / "workflows" / "escritorio.yml").read_text(encoding="utf-8")
    compilaciones = re.findall(r"npx tauri build.*", flujo)
    # La que se publica (5.4: el programa con --no-bundle y luego `tauri bundle`, para firmar lo de
    # dentro entre medias), una y sin nada más; la 9.9.9 de la prueba de actualizar solo cambia el
    # número; y la de depurar no da instalador (--no-bundle).
    assert compilaciones.count("npx tauri build --no-bundle") == 1, compilaciones
    assert re.findall(r"run: npx tauri bundle.*", flujo) == ["run: npx tauri bundle"]
    con_depurar = [c for c in compilaciones if "depurar" in c]
    assert con_depurar and all("--no-bundle" in c for c in con_depurar), compilaciones
    assert not [c for c in compilaciones if "--features" in c and "depurar" not in c]
