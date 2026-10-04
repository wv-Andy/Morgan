"""
La copia de seguridad de Supabase, y cómo se restaura (4.20).

El plan gratuito de Supabase no da copias descargables. Esto hace una **copia lógica** de los
datos de Morgan: cada tabla de `public`, entera, en JSON, con su huella (SHA-256) en un
manifiesto. **La estructura no va en la copia**: tablas, funciones y el reloj salen de
`migraciones/supabase/` (en orden), y los secretos del Vault, de las variables de Render.
`auth.users` tampoco: Morgan no usa la autenticación de Supabase (`auth_user_id` está vacío).

    python scripts/copia_supabase.py copiar                      # producción → ~/.morgan/copias
    python scripts/copia_supabase.py comprobar --desde DIR       # ¿la base sigue igual que la copia?
    python scripts/copia_supabase.py restaurar --desde DIR --proyecto carga

Restaurar en un proyecto vacío (las migraciones ya aplicadas): las tablas se escriben en orden
(primero de las que dependen otras) y, al final, hay que **ajustar los contadores** de las
tablas con `id` automático con el SQL que deja en `secuencias.sql`: sin eso, el primer mensaje
nuevo chocaría con uno restaurado. Restaurar sobre producción pide `--si-produccion`.

Las claves salen del `.env`: `SUPABASE_URL` y `SUPABASE_SECRET_KEY` (producción), y
`MORGAN_CARGA_SUPABASE_URL` y `MORGAN_CARGA_SUPABASE_SECRET` (morgan-carga, la de pruebas).
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
#: Fuera de OneDrive y del repositorio, junto a la clave de firma (decisión mía, 2026-10-03):
#: la copia lleva correos, hashes de contraseñas y conversaciones.
DESTINO = Path.home() / ".morgan" / "copias"

#: Todas las tablas de `public`, **primero aquellas de las que dependen otras**. Una tabla
#: nueva que no esté aquí no se copiaría: lo vigila `tests/test_copia_supabase.py` contra las
#: migraciones.
TABLAS: dict[str, str] = {
    # tabla: su clave primaria (lo que identifica una fila al restaurar)
    "morgan_users": "id",
    "sessions": "user_id,id",
    "conocimiento": "user_id,id",
    "messages": "id",
    "memories": "id",
    "uploads": "id",
    "tasks": "id",
    "uso_diario": "user_id,dia",
    "auth_sessions": "id",
    "password_reset_tokens": "token_hash",
    "login_intentos": "id",
    "planes": "user_id,id",
    "integraciones": "user_id,servicio",
    "oauth_estados": "estado",
    "conocimiento_fragmentos": "id",
    "espacios": "user_id,id",
    "api_tokens": "id",
    "agentes": "id",
    "agente_codigos": "codigo_hash",
    "ordenes_agente": "command_id",
    "automatizaciones": "id",
    "avisos": "id",
}
#: Las que numeran solas su `id`: tras restaurar, su contador tiene que seguir al mayor.
CON_CONTADOR = ("messages", "memories", "login_intentos", "conocimiento_fragmentos")
POR_PAGINA = 1000
POR_LOTE = 500


def huella(filas: list[dict]) -> str:
    """La huella de una tabla: igual para los mismos datos, venga el JSON como venga."""
    texto = json.dumps(filas, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def leer_tabla(cliente, tabla: str) -> list[dict]:
    """Una tabla entera, por páginas y en el orden de su clave (para que la huella no
    dependa del orden en que la devuelva la base)."""
    orden = ",".join(f"{c}.asc" for c in TABLAS[tabla].split(","))
    filas: list[dict] = []
    while True:
        pagina = cliente.select(tabla, f"select=*&order={orden}&limit={POR_PAGINA}&offset={len(filas)}")
        filas.extend(pagina)
        if len(pagina) < POR_PAGINA:
            return filas


def copiar(cliente, destino: Path, origen: str = "") -> dict:
    """Copia todas las tablas en `destino` (una carpeta nueva). Devuelve el manifiesto."""
    destino.mkdir(parents=True, exist_ok=False)
    manifiesto = {"formato": 1, "origen": origen, "hecha_en": time.time(), "tablas": {}}
    for tabla in TABLAS:
        filas = leer_tabla(cliente, tabla)
        (destino / f"{tabla}.json").write_text(
            json.dumps(filas, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        manifiesto["tablas"][tabla] = {"filas": len(filas), "huella": huella(filas)}
    (destino / "manifiesto.json").write_text(json.dumps(manifiesto, indent=2), encoding="utf-8")
    return manifiesto


def cargar(desde: Path) -> tuple[dict, dict[str, list[dict]]]:
    """La copia, comprobada: si un fichero no es el que se copió, no se usa."""
    manifiesto = json.loads((desde / "manifiesto.json").read_text(encoding="utf-8"))
    datos = {}
    for tabla, esperado in manifiesto["tablas"].items():
        filas = json.loads((desde / f"{tabla}.json").read_text(encoding="utf-8"))
        if len(filas) != esperado["filas"] or huella(filas) != esperado["huella"]:
            raise ValueError(f"La copia de {tabla} no es la que se hizo (fichero cambiado o roto).")
        datos[tabla] = filas
    faltan = set(TABLAS) - set(datos)
    if faltan:
        raise ValueError(f"A la copia le faltan tablas: {', '.join(sorted(faltan))}.")
    return manifiesto, datos


def sql_de_contadores() -> str:
    return "\n".join(
        f"select setval(pg_get_serial_sequence('public.{t}', 'id'), "
        f"coalesce((select max(id) from public.{t}), 0) + 1, false);"
        for t in CON_CONTADOR) + "\n"


def restaurar(cliente, desde: Path) -> dict[str, int]:
    """Escribe la copia en el proyecto del cliente, en orden y por lotes. Si una fila ya
    está (misma clave), se sobrescribe con la de la copia. Devuelve las filas por tabla."""
    _, datos = cargar(desde)
    escritas = {}
    for tabla, clave in TABLAS.items():
        filas = datos[tabla]
        for i in range(0, len(filas), POR_LOTE):
            cliente.upsert(tabla, filas[i:i + POR_LOTE], on_conflict=clave)
        escritas[tabla] = len(filas)
    (desde / "secuencias.sql").write_text(sql_de_contadores(), encoding="utf-8")
    return escritas


def comprobar(cliente, desde: Path) -> list[str]:
    """Lo que en la base no es como en la copia, tabla a tabla. Vacío si coincide."""
    manifiesto, _ = cargar(desde)
    diferencias = []
    for tabla, esperado in manifiesto["tablas"].items():
        filas = leer_tabla(cliente, tabla)
        if huella(filas) != esperado["huella"]:
            diferencias.append(f"{tabla}: {len(filas)} filas en la base, {esperado['filas']} en la copia")
    return diferencias


def _cliente(proyecto: str):
    from dotenv import load_dotenv

    from src.memory.supabase_repositories import SupabaseClient

    load_dotenv(RAIZ / ".env")
    if proyecto == "produccion":
        url, clave = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
    else:
        url, clave = os.environ.get("MORGAN_CARGA_SUPABASE_URL"), os.environ.get("MORGAN_CARGA_SUPABASE_SECRET")
    if not url or not clave:
        raise SystemExit(f"Faltan la URL o la clave de {proyecto} en el .env (ver la cabecera).")
    return SupabaseClient(url, clave, timeout=60), url


def main() -> int:
    lector = argparse.ArgumentParser(description="La copia de seguridad de Supabase de Morgan")
    lector.add_argument("orden", choices=["copiar", "comprobar", "restaurar"])
    lector.add_argument("--proyecto", choices=["produccion", "carga"], default="produccion")
    lector.add_argument("--desde", type=Path)
    lector.add_argument("--si-produccion", action="store_true")
    args = lector.parse_args()
    cliente, url = _cliente(args.proyecto)
    if args.orden == "copiar":
        destino = DESTINO / f"{args.proyecto}-{time.strftime('%Y%m%d-%H%M%S')}"
        manifiesto = copiar(cliente, destino, origen=url.split("//")[-1].split(".")[0])
        total = sum(t["filas"] for t in manifiesto["tablas"].values())
        print(f"Copia hecha: {destino} ({len(manifiesto['tablas'])} tablas, {total} filas).")
        return 0
    if args.desde is None:
        raise SystemExit("Falta --desde (la carpeta de la copia).")
    if args.orden == "comprobar":
        diferencias = comprobar(cliente, args.desde)
        print("La base coincide con la copia." if not diferencias else "\n".join(diferencias))
        return 1 if diferencias else 0
    if args.proyecto == "produccion" and not args.si_produccion:
        raise SystemExit("Restaurar sobre producción pide --si-produccion.")
    escritas = restaurar(cliente, args.desde)
    print(f"Restauradas {sum(escritas.values())} filas en {len(escritas)} tablas.")
    print(f"Falta ajustar los contadores: ejecuta {args.desde / 'secuencias.sql'} en el SQL de Supabase.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(RAIZ))
    sys.exit(main())
