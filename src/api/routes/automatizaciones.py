"""
Rutas de las automatizaciones y de la bandeja de avisos (4.14).

- Las de cada persona (`/automatizaciones`, `/avisos`): cada una ve y toca solo las suyas.
  **No hay ruta para crearlas**: se crean hablando con Morgan, con un plan rojo que la
  persona aprueba (decisión mía). Aquí se pausan, se reanudan y se borran.
- La del reloj (`POST /automatizaciones/reloj`): la llama `pg_cron` desde Supabase cada
  minuto, solo si toca algo. Sin sesión: se autentica con su propio secreto
  (`MORGAN_RELOJ_SECRETO`, comparado en tiempo constante). Sin secreto configurado, no
  existe (404). Contesta enseguida y ejecuta en otro hilo.
"""

import hmac
import time

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from src.api.dependencies import CoreContainer, get_container
from src.automatizacion import horario
from src.automatizacion.repositorio import repositorio_de_automatizaciones
from src.config import get_settings
from src.identidad import usuario_actual
from src.tools.automatizaciones import MAX_ACTIVAS, _en_palabras

router = APIRouter(tags=["Automatizaciones"])


def _repo(container: CoreContainer):
    return repositorio_de_automatizaciones(container.repositories)


def _publica(a: dict) -> dict:
    """Lo que ve la web de una automatización."""
    return {
        "id": a["id"], "nombre": a["nombre"], "instruccion": a["instruccion"],
        "cuando": horario.describir(a["horario"]), "zona": a["zona"], "necesita_pc": a["necesita_pc"],
        "activa": a["activa"], "proxima": a["proxima"] if a["activa"] else None,
        "proxima_texto": _en_palabras(a["proxima"], a["zona"]) if a["activa"] else None,
        "esperando_pc": bool(a.get("esperando_pc_desde")), "ultima": a.get("ultima"),
        "ultimo_estado": a.get("ultimo_estado"), "fallos_seguidos": a.get("fallos_seguidos", 0),
        # Los pasos fijos (4.15): lo que hace exactamente, con {fecha} y {hora} sin sustituir.
        "pasos": a.get("pasos") or None,
    }


def _la_suya(container: CoreContainer, automatizacion_id: str) -> dict:
    auto = _repo(container).obtener(usuario_actual(), automatizacion_id)
    if auto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail={"code": "NO_EXISTE", "message": "Esa automatización no existe."})
    return auto


@router.get("/automatizaciones", summary="Mis automatizaciones")
def listar(container: CoreContainer = Depends(get_container)) -> dict:
    return {"automatizaciones": [_publica(a) for a in _repo(container).listar(usuario_actual())],
            "max_activas": MAX_ACTIVAS}


@router.post("/automatizaciones/{automatizacion_id}/pausar", summary="Pausar una automatización")
def pausar(automatizacion_id: str, container: CoreContainer = Depends(get_container)) -> dict:
    _la_suya(container, automatizacion_id)
    _repo(container).actualizar(usuario_actual(), automatizacion_id,
                                {"activa": False, "esperando_pc_desde": None})
    return _publica(_la_suya(container, automatizacion_id))


@router.post("/automatizaciones/{automatizacion_id}/reanudar", summary="Reanudar una automatización")
def reanudar(automatizacion_id: str, container: CoreContainer = Depends(get_container)) -> dict:
    auto = _la_suya(container, automatizacion_id)
    repo = _repo(container)
    if not auto["activa"] and repo.contar_activas(usuario_actual()) >= MAX_ACTIVAS:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "code": "DEMASIADAS", "message": f"Ya tienes {MAX_ACTIVAS} activas: pausa o borra otra antes."})
    # Desde ahora: lo que tocó mientras estaba pausada no se recupera de golpe.
    repo.actualizar(usuario_actual(), automatizacion_id, {
        "activa": True, "fallos_seguidos": 0, "esperando_pc_desde": None,
        "proxima": horario.siguiente(auto["horario"], auto["zona"], time.time())})
    return _publica(_la_suya(container, automatizacion_id))


@router.delete("/automatizaciones/{automatizacion_id}", summary="Borrar una automatización")
def borrar(automatizacion_id: str, container: CoreContainer = Depends(get_container)) -> dict:
    _la_suya(container, automatizacion_id)
    _repo(container).borrar(usuario_actual(), automatizacion_id)
    return {"borrada": automatizacion_id}


@router.get("/avisos", summary="La bandeja de avisos")
def avisos(container: CoreContainer = Depends(get_container)) -> dict:
    repo = _repo(container)
    return {"avisos": repo.avisos(usuario_actual()), "sin_leer": repo.sin_leer(usuario_actual())}


@router.get("/avisos/sin-leer", summary="Cuántos avisos sin leer")
def sin_leer(container: CoreContainer = Depends(get_container)) -> dict:
    """Para el número de la navegación: la web lo mira cada minuto, sin traer los textos."""
    return {"sin_leer": _repo(container).sin_leer(usuario_actual())}


class Leidos(BaseModel):
    ids: list[str] | None = Field(None, max_length=200, description="Sin ids, todos.")


@router.post("/avisos/leidos", summary="Marcar avisos como leídos")
def leidos(peticion: Leidos, container: CoreContainer = Depends(get_container)) -> dict:
    repo = _repo(container)
    repo.marcar_leidos(usuario_actual(), peticion.ids)
    return {"sin_leer": repo.sin_leer(usuario_actual())}


@router.post("/automatizaciones/reloj", summary="El reloj de Supabase (pg_cron)")
def reloj(x_morgan_reloj: str | None = Header(None), container: CoreContainer = Depends(get_container)) -> dict:
    secreto = get_settings().reloj_secreto
    # Sin secreto configurado, la ruta no existe; con uno, solo con él.
    if not secreto:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "NOT_FOUND", "message": "Not Found"})
    if not x_morgan_reloj or not hmac.compare_digest(x_morgan_reloj.encode(), secreto.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail={"code": "UNAUTHORIZED", "message": "Reloj no válido."})
    return {"lanzado": container.reloj.tic_en_segundo_plano()}
