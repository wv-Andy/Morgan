"""
Dónde viven los bytes de un archivo subido.

El índice (qué archivos hay, cómo se llaman) va a la base de datos por el patrón
repositorio, como todo lo demás. Los **bytes** son otra cosa: no caben cómodamente
en una fila, así que tienen su propia abstracción con dos implementaciones.

- **Disco**, para el Morgan local: el directorio persiste entre reinicios.
- **Supabase Storage**, para el Morgan de la nube: allí el disco es efímero y el
  servicio se duerme, de modo que un archivo guardado en disco desaparece al primer
  rato de inactividad.

La interfaz es deliberadamente mínima —guardar, leer, borrar— porque es todo lo
que hace falta. Ampliarla invitaría a acoplar el resto del sistema a un backend
concreto.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# Nombre del bucket en Supabase. Es privado: público dejaría los documentos del
# usuario accesibles con solo adivinar la URL.
BUCKET = "morgan-uploads"


class AlmacenamientoError(RuntimeError):
    """No se pudo guardar o recuperar el contenido de un archivo."""


class AlmacenBytes(ABC):
    """Guarda y recupera el contenido de los archivos subidos."""

    @property
    @abstractmethod
    def nombre(self) -> str:
        """Identificador que se persiste junto al archivo, para saber dónde está."""

    @abstractmethod
    def guardar(self, identificador: str, contenido: bytes) -> None: ...

    @abstractmethod
    def leer(self, identificador: str) -> bytes | None: ...

    @abstractmethod
    def eliminar(self, identificador: str) -> None: ...


class AlmacenEnDisco(AlmacenBytes):
    """El de siempre: un directorio local."""

    def __init__(self, directorio: Path):
        self.directorio = Path(directorio)

    @property
    def nombre(self) -> str:
        return "disco"

    def guardar(self, identificador: str, contenido: bytes) -> None:
        self.directorio.mkdir(parents=True, exist_ok=True)
        try:
            (self.directorio / identificador).write_bytes(contenido)
        except OSError as exc:
            raise AlmacenamientoError(f"No se pudo escribir el archivo: {exc}") from exc

    def leer(self, identificador: str) -> bytes | None:
        ruta = self.directorio / identificador
        if not ruta.is_file():
            return None
        try:
            return ruta.read_bytes()
        except OSError as exc:
            logger.warning("No se pudo leer %s: %s", identificador, exc)
            return None

    def eliminar(self, identificador: str) -> None:
        (self.directorio / identificador).unlink(missing_ok=True)


class AlmacenSupabase(AlmacenBytes):
    """Supabase Storage, para cuando el disco no sobrevive al reinicio."""

    def __init__(self, url: str, clave: str, bucket: str = BUCKET, timeout: float = 30.0):
        self.base = f"{url.rstrip('/')}/storage/v1/object"
        self.bucket = bucket
        self.timeout = timeout
        self._cabeceras = {
            "Authorization": f"Bearer {clave}",
            "apikey": clave,
        }

    @property
    def nombre(self) -> str:
        return "supabase"

    def _ruta(self, identificador: str) -> str:
        # El identificador lo genera Morgan y es urlsafe, pero se codifica igual:
        # depender de eso desde otro modulo seria una suposicion frágil.
        return f"{self.base}/{self.bucket}/{quote(identificador, safe='')}"

    def guardar(self, identificador: str, contenido: bytes) -> None:
        try:
            r = httpx.post(
                self._ruta(identificador),
                headers={
                    **self._cabeceras,
                    "Content-Type": "application/octet-stream",
                    # Sin esto, subir dos veces el mismo id da 409 en lugar de
                    # sustituir. No deberia pasar con ids generados, pero un 409
                    # silencioso seria peor que un reemplazo.
                    "x-upsert": "true",
                },
                content=contenido,
                timeout=self.timeout,
            )
        except httpx.RequestError as exc:
            raise AlmacenamientoError(f"No se pudo conectar con el almacenamiento: {exc}") from exc

        if r.status_code not in (200, 201):
            raise AlmacenamientoError(
                f"El almacenamiento rechazó el archivo ({r.status_code}): {r.text[:200]}"
            )

    def leer(self, identificador: str) -> bytes | None:
        try:
            r = httpx.get(self._ruta(identificador), headers=self._cabeceras, timeout=self.timeout)
        except httpx.RequestError as exc:
            logger.warning("No se pudo descargar %s: %s", identificador, exc)
            return None

        if r.status_code == 200:
            return r.content

        # Supabase Storage devuelve 400, no 404, cuando el objeto no existe.
        # Comprobado contra el servicio real. Registrarlo como aviso llenaba el
        # log de ruido en un caso perfectamente normal.
        if r.status_code not in (400, 404):
            logger.warning("El almacenamiento devolvió %s al leer %s", r.status_code, identificador)
        return None

    def eliminar(self, identificador: str) -> None:
        try:
            httpx.delete(self._ruta(identificador), headers=self._cabeceras, timeout=self.timeout)
        except httpx.RequestError as exc:
            # Un borrado que falla deja basura, no un fallo funcional: el indice
            # ya no lo referencia. Se registra y se sigue.
            logger.warning("No se pudo borrar %s del almacenamiento: %s", identificador, exc)
