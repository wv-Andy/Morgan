"""
Almacén de archivos subidos por el usuario (V1.4).

Un archivo que llega por la web es **dato no confiable**, igual que una página
descargada por `read_webpage`: lo ha escrito un tercero y puede contener
instrucciones dirigidas al modelo. Esta capa se ocupa de lo aburrido y crítico —
validar, acotar, nombrar y limpiar — para que las herramientas que lo usen puedan
concentrarse en su trabajo.

Decisiones que conviene tener presentes:

- **El nombre original nunca toca el disco.** Se guarda como metadato y el archivo
  se nombra con un identificador generado. Así `../../.env` o `C:\\Windows\\x` no
  son ni siquiera un problema que haya que validar: no hay ruta que construir con
  lo que el usuario escribió.
- **El tipo se deduce del contenido, no de la extensión.** Un `.png` puede ser
  cualquier cosa; la firma de los primeros bytes, no.
- **Los archivos caducan.** Sin caducidad, el disco crece sin límite y quedan
  copias de documentos que el usuario cree haber usado y olvidado.
"""

import logging
import mimetypes
import secrets
import time
from pathlib import Path

from src.config import get_settings
from src.memory.models import Upload
from src.memory.repositories import UploadRepository
from src.uploads.almacenamiento import AlmacenBytes, AlmacenEnDisco

logger = logging.getLogger(__name__)


class ArchivoRechazado(ValueError):
    """El archivo no cumple las condiciones para aceptarse.

    Lleva un mensaje pensado para enseñarse: aquí el usuario ha hecho algo que
    puede corregir, así que no es un error interno que ocultar.
    """


# Formatos admitidos: extensión canónica y firma de los primeros bytes cuando el
# formato la tiene. `None` significa "no hay firma fiable"; ahí manda el texto.
FIRMAS: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
    "application/pdf": (b"%PDF-",),
}

# Lo que Morgan admite hoy. Ampliar esto es barato; hacerlo sin extractor que lo
# entienda, no: el archivo se aceptaría y luego no se podría leer.
TIPOS_ADMITIDOS: dict[str, set[str]] = {
    "imagen": {"image/png", "image/jpeg", "image/gif", "image/webp"},
    "documento": {"application/pdf"},
    # Todo lo que se lee decodificando bytes cae aqui: no hace falta extractor,
    # asi que ampliar la lista es barato. La familia se llama "texto" porque eso
    # es lo que Morgan hace con ello, no por el uso que le da la persona.
    "texto": {
        "text/plain", "text/markdown", "text/csv", "text/tab-separated-values",
        "application/json", "application/xml", "text/xml",
        "application/x-yaml", "application/toml", "text/x-ini", "text/x-log",
        # Codigo. El objetivo es leerlo, explicarlo y revisarlo; ejecutarlo sigue
        # siendo cosa de las herramientas de terminal, con sus permisos.
        "text/x-python", "text/javascript", "text/x-typescript", "text/html",
        "text/css", "text/x-c", "text/x-c++", "text/x-java", "text/x-csharp",
        "text/x-sql", "text/x-php", "text/x-go", "text/x-rust", "text/x-kotlin",
        "text/x-swift", "text/x-ruby", "text/x-shellscript", "text/x-lua",
        "text/x-r", "text/x-perl", "text/x-scala", "text/x-dart",
        # SVG entra como TEXTO, no como imagen: es XML y puede llevar <script>.
        # Mandarlo a un modelo de vision no aporta nada, y renderizarlo en una
        # previsualizacion seria abrir un agujero. Leido, Morgan lo explica igual.
        "image/svg+xml",
    },
    "audio": {
        "audio/mpeg", "audio/wav", "audio/webm", "audio/mp4", "audio/ogg",
        "audio/flac", "audio/x-flac", "audio/opus", "audio/aac",
    },
}

EXTENSIONES: dict[str, str] = {
    # --- Imagenes ---
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml",  # se trata como texto, ver TIPOS_ADMITIDOS

    # --- Documentos ---
    ".pdf": "application/pdf",

    # --- Texto y datos ---
    ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".json": "application/json", ".jsonl": "application/json",
    ".xml": "application/xml",
    ".yml": "application/x-yaml", ".yaml": "application/x-yaml",
    ".toml": "application/toml", ".ini": "text/x-ini", ".cfg": "text/x-ini",
    ".conf": "text/x-ini", ".env": "text/x-ini", ".log": "text/x-log",

    # --- Codigo ---
    ".py": "text/x-python", ".pyi": "text/x-python",
    ".js": "text/javascript", ".mjs": "text/javascript", ".cjs": "text/javascript",
    ".ts": "text/x-typescript", ".tsx": "text/x-typescript", ".jsx": "text/javascript",
    ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".scss": "text/css", ".sass": "text/css",
    ".c": "text/x-c", ".h": "text/x-c",
    ".cpp": "text/x-c++", ".cc": "text/x-c++", ".cxx": "text/x-c++",
    ".hpp": "text/x-c++", ".hh": "text/x-c++",
    ".java": "text/x-java", ".kt": "text/x-kotlin", ".kts": "text/x-kotlin",
    ".cs": "text/x-csharp", ".sql": "text/x-sql", ".php": "text/x-php",
    ".go": "text/x-go", ".rs": "text/x-rust", ".swift": "text/x-swift",
    ".rb": "text/x-ruby", ".lua": "text/x-lua", ".r": "text/x-r",
    ".pl": "text/x-perl", ".scala": "text/x-scala", ".dart": "text/x-dart",
    ".sh": "text/x-shellscript", ".bash": "text/x-shellscript",
    ".zsh": "text/x-shellscript", ".ps1": "text/x-shellscript",
    ".bat": "text/x-shellscript", ".cmd": "text/x-shellscript",

    # --- Audio ---
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".webm": "audio/webm",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".opus": "audio/opus", ".flac": "audio/flac", ".aac": "audio/aac",
}


# El modelo del archivo es `Upload`, en src/memory/models.py: se persiste, asi
# que pertenece a la capa de datos y no a esta.
ArchivoSubido = Upload


#: La familia de una copia traída del PC cuyo formato Morgan no sabe leer (3.1-E): está
#: para descargarla, no para leerla.
FAMILIA_DESCARGA = "descarga"


def familia_de(mime: str) -> str | None:
    for familia, tipos in TIPOS_ADMITIDOS.items():
        if mime in tipos:
            return familia
    return None


def _detectar_mime(nombre: str, contenido: bytes) -> str | None:
    """Deduce el tipo mirando primero el contenido y solo después el nombre.

    Una extensión la elige quien sube el archivo; la firma de los primeros bytes,
    no. Cuando el formato tiene firma conocida, manda ella.
    """
    for mime, firmas in FIRMAS.items():
        if any(contenido.startswith(f) for f in firmas):
            # WebP comparte cabecera RIFF con otros contenedores.
            if mime == "image/webp" and contenido[8:12] != b"WEBP":
                continue
            return mime

    extension = Path(nombre).suffix.lower()
    por_extension = EXTENSIONES.get(extension)

    # Si la extension dice ser un formato que TIENE firma conocida y esa firma no
    # aparecio arriba, el archivo no es lo que dice. Sin esta comprobacion, un
    # ejecutable renombrado a .png se aceptaba como imagen.
    if por_extension in FIRMAS:
        return None

    if por_extension:
        return por_extension

    adivinado, _ = mimetypes.guess_type(nombre)
    # Igual para lo que adivine la libreria por el nombre.
    return None if adivinado in FIRMAS else adivinado


class UploadStore:
    """Guarda, recupera y caduca los archivos subidos.

    Es una fachada sobre dos piezas separadas a proposito:

    - el **indice** (que archivos hay) va al repositorio, y por tanto a SQLite en
      local y a Supabase en la nube, como el resto de entidades;
    - los **bytes** van a `AlmacenBytes`, disco o Supabase Storage.

    Antes el indice vivia en un diccionario en memoria y los bytes siempre en
    disco. En la nube eso significaba perderlo todo en cada reinicio: el disco de
    Render es efimero y el servicio se duerme a los 15 minutos.
    """

    def __init__(
        self,
        repositorio: "UploadRepository | None" = None,
        almacen: "AlmacenBytes | None" = None,
        directorio: Path | None = None,
    ):
        settings = get_settings()
        self.max_bytes = settings.upload_max_bytes
        self.ttl_segundos = settings.upload_ttl_hours * 3600
        self.max_archivos = settings.upload_max_archivos
        self.max_total_bytes = settings.upload_max_total_mb * 1024 * 1024

        if repositorio is None or almacen is None:
            # Construccion por defecto: todo local. Quien quiera la nube inyecta
            # las piezas, que es lo que hace CoreContainer segun el entorno.
            from src.memory.sqlite_repositories import SQLiteRepositoryFactory

            base = SQLiteRepositoryFactory()
            repositorio = repositorio or base.uploads
            almacen = almacen or AlmacenEnDisco(
                Path(directorio) if directorio else settings.data_dir / "uploads"
            )

        self.repositorio = repositorio
        self.almacen = almacen
        from src.tools.recordado import Recordado

        self._con_archivos = Recordado()

    # El directorio sigue expuesto porque hay pruebas y diagnosticos que lo miran.
    @property
    def directorio(self) -> Path | None:
        return getattr(self.almacen, "directorio", None)

    @staticmethod
    def _sin_limites() -> bool:
        """Si a quien sube esto no se le aplican los cupos.

        Nunca lanza: sin identidad disponible se responde que **sí** hay límites,
        que es el lado seguro del error.
        """
        try:
            from src.identidad import rol_actual, usuario_actual
            from src.identidad.roles import propietario_con_cuenta, sin_cupo

            rol = rol_actual()
            return sin_cupo(rol) and propietario_con_cuenta(usuario_actual(), rol)
        except Exception:
            return False

    def guardar_copia_del_equipo(self, nombre: str, contenido: bytes, mime: str | None) -> Upload:
        """Una copia traída del PC de la persona para que la descargue (3.1-E).

        **Admite cualquier tipo**, y esa es la diferencia con `guardar`. Lo subido por la
        web se restringe a lo que Morgan sabe leer, porque subirlo sirve para eso; una
        copia del PC sirve para **descargarla**, y la mitad de los documentos de una
        persona (Word, Excel, un zip) no son de los que Morgan lee. Lo que no sepa leer
        entra con la familia `descarga`: aparece en sus archivos, se descarga, y
        `read_upload` dirá que no puede con ese formato.

        Lo demás es igual: tope de tamaño, cupo de la cuenta y **borrado solo a las 24 h**
        (decisión mía). El contenido no pasa por el modelo.
        """
        return self.guardar(nombre, contenido, mime=mime, cualquier_tipo=True)

    def guardar(self, nombre: str, contenido: bytes, mime: str | None = None,
                cualquier_tipo: bool = False) -> Upload:
        """Valida y almacena. Lanza `ArchivoRechazado` con un motivo legible."""
        if not contenido:
            raise ArchivoRechazado("El archivo está vacío.")

        if len(contenido) > self.max_bytes and not self._sin_limites():
            limite = self.max_bytes // (1024 * 1024)
            raise ArchivoRechazado(
                f"El archivo pesa {len(contenido) // (1024 * 1024)} MB y el límite "
                f"son {limite} MB."
            )

        mime = _detectar_mime(nombre or "archivo", contenido) or (mime if cualquier_tipo else None)
        familia = familia_de(mime) if mime else None
        if familia is None and cualquier_tipo:
            familia = FAMILIA_DESCARGA
        if familia is None:
            raise ArchivoRechazado(
                f"No admito archivos de tipo '{mime or 'desconocido'}'. "
                "Puedo con imágenes, PDF, texto, código y audio."
            )

        self.limpiar_caducados()
        self._comprobar_cupo(len(contenido))

        # El nombre en disco lo genera Morgan. Lo que escribió el usuario se
        # conserva como metadato y nunca se usa para construir una ruta, así que
        # '../../.env' no llega a ser un problema que validar.
        identificador = secrets.token_urlsafe(12)
        self.almacen.guardar(identificador, contenido)

        archivo = Upload(
            id=identificador,
            nombre_original=(nombre or "archivo")[:255],
            mime=mime or "application/octet-stream",
            familia=familia,
            tamano=len(contenido),
            creado_en=time.time(),
            almacenamiento=self.almacen.nombre,
        )
        try:
            self.repositorio.add(archivo)
        except Exception:
            # Si el indice falla, los bytes quedarian huerfanos y ocupando sitio
            # sin que nada los referencie ni los caduque.
            self.almacen.eliminar(identificador)
            raise

        logger.info(
            "Archivo aceptado: %s (%s, %d bytes) en %s",
            archivo.id, archivo.mime, archivo.tamano, archivo.almacenamiento,
        )
        self._con_archivos.olvidar()
        return archivo

    def _comprobar_cupo(self, bytes_nuevos: int) -> None:
        """Comprueba que quepa uno mas, en numero y en espacio.

        Los limites son **por usuario**, no globales. El repositorio ya filtra por
        el usuario de la peticion, asi que `list()` solo devuelve lo suyo: con
        limites globales, la primera persona que llenara el cupo dejaria sin subir
        a todas las demas.

        Se llama despues de limpiar los caducados: lo que ya no vale no deberia
        contar para el cupo de lo que se quiere subir ahora.
        """
        # El cupo de archivos existe por la misma razon que el de mensajes:
        # repartir un disco que paga el dueno. A el no se le aplica.
        if self._sin_limites():
            return

        # De TODOS los espacios. Con `list()`, que ve solo el espacio actual,
        # bastaba con repartir los archivos entre espacios para saltarse el
        # límite: cada uno se veía vacío.
        existentes = self.repositorio.list_todos(limit=self.max_archivos + 1)

        if len(existentes) >= self.max_archivos:
            raise ArchivoRechazado(
                f"Ya tienes {len(existentes)} archivos subidos y el límite son "
                f"{self.max_archivos}. Elimina alguno antes de subir otro."
            )

        ocupado = sum(a.tamano for a in existentes)
        if ocupado + bytes_nuevos > self.max_total_bytes:
            libre_mb = max(0, (self.max_total_bytes - ocupado)) // (1024 * 1024)
            raise ArchivoRechazado(
                f"No cabe: quedan {libre_mb} MB libres de "
                f"{self.max_total_bytes // (1024 * 1024)} MB. Elimina algún archivo."
            )

    def obtener(self, identificador: str) -> Upload | None:
        return self.repositorio.get(identificador)

    def leer(self, identificador: str) -> bytes | None:
        if self.repositorio.get(identificador) is None:
            return None
        return self.almacen.leer(identificador)

    def listar(self) -> list[Upload]:
        self.limpiar_caducados()
        return self.repositorio.list()

    def tiene_alguno(self) -> bool:
        """Si quien pregunta tiene algún archivo en este espacio (4.2), recordado un rato.

        Para `disponible()` de las herramientas de adjuntos: sin ninguno, leer, mirar o
        transcribir uno no tiene sentido, y sus esquemas viajaban en cada llamada al
        modelo (~470 tokens). Guardar y borrar lo olvidan."""
        from src.tools.recordado import de_quien

        return self._con_archivos.valor(de_quien(), lambda: bool(self.repositorio.list()))

    def eliminar(self, identificador: str) -> bool:
        if not self.repositorio.delete(identificador):
            return False
        self.almacen.eliminar(identificador)
        self._con_archivos.olvidar()
        return True

    def limpiar_caducados(self) -> int:
        """Borra lo que ha pasado su tiempo de vida, indice y bytes.

        Sin esto el almacenamiento crece sin límite y quedan copias de documentos
        que el usuario cree haber usado y olvidado.
        """
        caducados = self.repositorio.delete_older_than(time.time() - self.ttl_segundos)
        for identificador in caducados:
            self.almacen.eliminar(identificador)

        if caducados:
            logger.info("Limpiados %d archivos subidos caducados", len(caducados))
            self._con_archivos.olvidar()
        return len(caducados)
