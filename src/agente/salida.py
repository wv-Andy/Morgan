"""
La frontera de salida del agente (3.1-A): lo último que ve un resultado antes de salir
del PC.

Leer un archivo en remoto es **sacarlo del PC** (§19 del plan): `PC → nube → modelo`.
Hasta la 3.0 los controles estaban repartidos por cada capacidad (carpetas, nombres
sensibles, 256 KB, binarios). Esto no los sustituye: se añade **un solo sitio por el
que pasa todo** resultado, para lo que no depende de la capacidad:

1. **Los secretos escritos dentro de un archivo se tapan** (decisión mía,
   2026-09-19). Un `.env` ya no se lee por su nombre, pero una clave de API pegada en
   unas notas, una clave privada dentro de un `.txt` o una contraseña en un archivo de
   configuración con otro nombre sí salían. Ahora salen como «[clave oculta]»: el modelo
   sabe que había una, no cuál. **Solo en lo que se lee** (`read_file`): la copia para
   descargar (3.1-E) es el archivo tal cual, para su dueño, y no pasa por el modelo.
2. **Un tope al tamaño total de la respuesta**, sea cual sea la capacidad: si alguna se
   equivocara en sus propios límites, esto no deja salir más.

## Qué se reconoce, y qué no

Por **forma**: claves con un formato conocido (OpenAI, Anthropic, GitHub, AWS, Google,
Groq, Slack, Stripe, JWT y las de Morgan), bloques de clave privada, contraseñas dentro
de una URL. Y por **contexto**: una asignación a un nombre que suena a secreto
(`password`, `contraseña`, `api_key`, `token`, `secret`…) con un valor que parece un
literal que **tiene algún dígito** (las claves de verdad casi siempre lo tienen).

Lo segundo es heurístico, a propósito prudente con el **código**, que es lo que más
leo: `token = obtener_token()`, `password = request.form["password"]` o
`api_key = settings.api_key` **no** se tapan (son código, no secretos). Un valor sin
comillas solo se tapa si tiene algún dígito o símbolo, como las claves de verdad.

**No es infalible**, y no pretende serlo: una contraseña escrita en prosa sin etiqueta
(«la del banco es perro1234») no se reconoce. Es una capa más, no la única: las carpetas
las elige la persona y los archivos de credenciales no se leen por su nombre.
"""

import json
import re

#: Lo que ocupa como mucho una respuesta del agente, serializada. `read_file` ya corta a
#: 256 KB; con el sobre y los escapes de JSON cabe de sobra. Por debajo de los 2 MB del
#: canal (3.0.5).
MAX_RESPUESTA = 768 * 1024

#: Las que devuelven rutas que se enseñan (3.3): se limpian como las de un listado.
ESCRITURAS = ("create_file", "edit_file", "append_file", "create_folder", "move_file", "delete_file")

OCULTO = "[clave oculta]"

#: Por forma: si aparece, es un secreto sin mirar el contexto.
_POR_FORMA = [
    # Bloques de clave privada (RSA, EC, OpenSSH, PGP…), enteros.
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----", re.S),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),                 # Anthropic
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),   # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                # AWS (id de clave)
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),                     # Google
    re.compile(r"\bgsk_[A-Za-z0-9]{30,}"),                       # Groq
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),             # Slack
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),   # Stripe
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}"),                  # NVIDIA
    re.compile(r"\bmg[na]_[A-Za-z0-9_\-]{30,}"),                 # Morgan: tokens y agentes
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
]

#: Contraseña dentro de una URL: `esquema://usuario:CONTRASEÑA@servidor`.
_EN_URL = re.compile(r"(\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:)([^\s@/]{3,})(@)", re.I)

#: Por contexto: `nombre_que_suena_a_secreto = valor`, `: valor`, `"nombre": "valor"`.
_NOMBRE = (r"(?:[A-Za-z0-9_\-]*?(?:pass(?:word|wd)?|pwd|contrase(?:ñ|n)a|clave|secret|"
           r"api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key|token|auth)[A-Za-z0-9_\-]*)")
_ASIGNACION = re.compile(
    # Espacios y tabuladores, nunca un salto de línea: `API_KEY=` vacío se comía la
    # línea siguiente como si fuera su valor (medido en el .env.example de Morgan).
    r"(?P<pre>\b" + _NOMBRE + r"[\"']?[ \t]*[:=][ \t]*)"
    r"(?:(?P<q>[\"'])(?P<citado>[^\"'\s]{6,})(?P=q)"            # entre comillas
    r"|(?P<suelto>[^\s\"'(\[{,;]{8,})(?![\w(\[.]))",            # sin comillas: ver abajo
    re.I,
)


def _parece_secreto(valor: str) -> bool:
    """Un valor que parece una clave y no código ni configuración: **tiene algún dígito**,
    no es una dirección web y no es solo un número o una versión.

    Medido pasando el filtro por todo Morgan (código, web y documentación, sin ninguna
    clave real): la primera versión, que tapaba cualquier valor entre comillas, dio 18
    falsos positivos en 15 archivos (`"configurada"`, `"Bearer"`, `"#emparejar"`, una
    URL de Google, `86_400.0`…). Lo que se pierde, a sabiendas: una contraseña solo de
    letras (`password = "caballocorrecto"`) ya no se tapa.
    """
    if "://" in valor or not re.search(r"\d", valor):
        return False
    return not re.fullmatch(r"[\d_.,:+\-]+", valor)


def tapar_secretos(texto: str) -> tuple[str, int]:
    """El texto con los secretos tapados, y cuántos se taparon."""
    cuenta = 0

    def _fuera(_m):
        nonlocal cuenta
        cuenta += 1
        return OCULTO

    for patron in _POR_FORMA:
        texto = patron.sub(_fuera, texto)

    def _url(m):
        nonlocal cuenta
        cuenta += 1
        return m.group(1) + OCULTO + m.group(3)

    texto = _EN_URL.sub(_url, texto)

    def _asignacion(m):
        nonlocal cuenta
        if m.group("citado") is not None:
            if OCULTO in m.group("citado") or not _parece_secreto(m.group("citado")):
                return m.group(0)
            cuenta += 1
            return f"{m.group('pre')}{m.group('q')}{OCULTO}{m.group('q')}"
        valor = m.group("suelto")
        if OCULTO in valor or not _parece_secreto(valor):
            return m.group(0)
        cuenta += 1
        return m.group("pre") + OCULTO

    texto = _ASIGNACION.sub(_asignacion, texto)
    return texto, cuenta


#: Marcas que cambian el orden en que se **enseña** un texto sin cambiar el texto. Es el
#: truco clásico para disfrazar un nombre: `foto‮gpj.exe` se lee «fotoexe.jpg», y
#: quien lo ve cree que descarga una imagen. Encontrado en el ataque de la 3.1.5: salían
#: intactas hasta la pantalla.
_BIDI = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f")

#: Lo que puede medir un nombre al salir del PC. Windows admite 255 caracteres por
#: nombre, y un nombre es texto que escribe cualquiera: cabe un párrafo de
#: instrucciones. Lo que se enseña se recorta; el archivo sigue llamándose igual.
MAX_NOMBRE = 120


def limpiar_nombre(nombre: str) -> str:
    """Un nombre de archivo o carpeta, como dato y no como instrucción (3.1-C).

    Los nombres los elige quien crea el archivo —y un archivo puede llegar de un correo,
    de una descarga o de un zip ajeno—, así que **son contenido no confiable**, igual
    que lo que hay dentro. Aquí se les quita lo que sirve para disfrazarse de otra cosa:

    - **Caracteres de control** (saltos de línea, tabuladores, `\\x00`), con los que un
      nombre puede fingir que es otra línea del listado o cerrar una etiqueta a medias.
    - **El cierre del envoltorio** de datos no confiables, escrito dentro del nombre.
    - **Lo que pase de 120 caracteres**: cabe un nombre real de sobra, no un párrafo de
      instrucciones.
    """
    limpio = "".join(" " if c < " " or c == "\x7f" or c in _BIDI else c for c in str(nombre))
    limpio = limpio.replace("</untrusted_file_data>", "&lt;/untrusted_file_data&gt;")
    return limpio[:MAX_NOMBRE] + ("…" if len(limpio) > MAX_NOMBRE else "")


def _limpiar_nombres(datos):
    """Los nombres y rutas de un listado o una búsqueda, ya limpios."""
    if isinstance(datos, dict):
        return {k: (limpiar_nombre(v) if k in ("name", "path", "src", "dst") and isinstance(v, str)
                    else _limpiar_nombres(v)) for k, v in datos.items()}
    if isinstance(datos, list):
        return [_limpiar_nombres(x) for x in datos]
    return datos


def filtrar(capacidad: str, resultado: dict) -> tuple[dict, dict]:
    """El resultado tal como puede salir del PC, y lo que se anota de él en la auditoría
    local (nunca el contenido)."""
    notas: dict = {}
    if capacidad in ("list_files", "search_files", *ESCRITURAS) and resultado.get("success"):
        resultado = {**resultado, "data": _limpiar_nombres(resultado.get("data"))}
    if capacidad in ("run_command", "run_change_command") and isinstance(resultado.get("data"), dict):
        # La salida de un comando es como un archivo leído (3.5): puede traer claves.
        datos = dict(resultado["data"])
        salida_tapada, tapados = tapar_secretos(str(datos.get("salida") or ""))
        if tapados:
            datos["salida"] = salida_tapada
            datos["secretos_ocultos"] = tapados
            resultado = {**resultado, "data": datos}
            notas["secretos_ocultos"] = tapados
    if capacidad == "read_file" and resultado.get("success"):
        datos = dict(resultado.get("data") or {})
        contenido, tapados = tapar_secretos(str(datos.get("content") or ""))
        if tapados:
            datos["content"] = contenido
            datos["secretos_ocultos"] = tapados
            resultado = {**resultado, "data": datos}
            notas["secretos_ocultos"] = tapados

    tamano = len(json.dumps(resultado, ensure_ascii=False).encode("utf-8"))
    if tamano > MAX_RESPUESTA:
        notas["cortada_por_tamano"] = tamano
        resultado = {
            "success": False, "data": None, "motivo": "demasiado_grande",
            "error": (f"La respuesta ocupaba {tamano // 1024} KB y el tope de salida del "
                      f"agente es {MAX_RESPUESTA // 1024} KB: no salió del PC."),
        }
    return resultado, notas
