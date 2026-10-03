"""
Backlog multimodal: formatos, límites y auditoría.

Corresponde a los puntos §2, §3, §4, §5, §20, §23 y §24 del backlog, que son los
que se decidió implementar tras evaluarlos uno a uno (`docs/backlog-multimodal.md`).
"""

import time

import pytest
from fastapi.testclient import TestClient

import src.config as config
from src.uploads.extraccion import MAX_PAGINAS, SEGUNDOS_MAXIMOS, extraer
from src.uploads.store import ArchivoRechazado, UploadStore

TOKEN = "secreto-de-prueba"
CABECERA = {"Authorization": f"Bearer {TOKEN}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"contenido" * 10
EJECUTABLE = b"MZ\x90\x00" + b"\x00" * 60


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    config.reset_settings()
    from src.memory.db import Database
    from src.memory.sqlite_repositories import SQLiteRepositoryFactory
    from src.uploads.almacenamiento import AlmacenEnDisco

    fabrica = SQLiteRepositoryFactory(Database(tmp_path / "uploads.db"))
    yield UploadStore(
        repositorio=fabrica.uploads,
        almacen=AlmacenEnDisco(tmp_path / "bytes"),
    )
    config.reset_settings()


@pytest.fixture
def api(tmp_path, monkeypatch, modelo_simulado):
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_API_TOKEN", TOKEN)
    config.reset_settings()
    from src.api.app import create_app

    yield TestClient(create_app())
    config.reset_settings()


# --- §2, §3, §4, §5: formatos nuevos -----------------------------------------


class TestLosFormatosNuevos:
    """Todos se leen decodificando bytes: no hacen falta extractores nuevos, así
    que ampliar la lista es barato. Ese fue el criterio para aceptarlos."""

    @pytest.mark.parametrize("nombre, contenido", [
        ("config.yaml", b"servidor:\n  puerto: 8000\n"),
        ("pyproject.toml", b"[tool]\nx = 1\n"),
        ("app.log", b"2026-09-06 ERROR algo\n"),
        ("setup.ini", b"[seccion]\nclave=valor\n"),
        ("main.rs", b"fn main() {}"),
        ("Query.sql", b"SELECT 1;"),
        ("App.java", b"class App {}"),
        ("prog.c", b"int main(void){return 0;}"),
        ("prog.cpp", b"#include <iostream>"),
        ("Program.cs", b"class P {}"),
        ("index.php", b"<?php echo 1;"),
        ("main.go", b"package main"),
        ("app.kt", b"fun main() {}"),
        ("script.sh", b"#!/bin/bash\necho hola\n"),
        ("datos.tsv", b"a\tb\n1\t2\n"),
    ])
    def test_se_aceptan_y_se_leen(self, store, nombre, contenido):
        archivo = store.guardar(nombre, contenido)

        assert archivo.familia == "texto"
        assert "untrusted_file_data" in extraer(contenido, archivo.mime, nombre)

    def test_un_svg_entra_como_texto_y_no_como_imagen(self, store):
        """Un SVG es XML y puede llevar `<script>`. Mandarlo a un modelo de visión
        no aporta nada, y renderizarlo en una previsualización sería un agujero.
        Leído, Morgan lo explica igual."""
        archivo = store.guardar("icono.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")

        assert archivo.familia == "texto"
        assert archivo.familia != "imagen"

    @pytest.mark.parametrize("nombre", ["voz.flac", "voz.opus", "voz.aac", "voz.oga"])
    def test_los_formatos_de_audio_nuevos(self, store, nombre):
        assert store.guardar(nombre, b"datos de audio" * 20).familia == "audio"

    def test_la_extraccion_no_depende_del_prefijo_del_mime(self, store):
        """Fiarse de que el MIME empiece por `text/` dejaba fuera YAML
        (`application/x-yaml`), TOML y SVG (`image/svg+xml`)."""
        for nombre, contenido in [("a.yaml", b"k: v"), ("b.toml", b"k = 1"),
                                  ("c.svg", b"<svg/>"), ("d.json", b"{}")]:
            archivo = store.guardar(nombre, contenido)
            extraer(contenido, archivo.mime, nombre)  # no debe lanzar

    def test_un_formato_sin_extractor_sigue_rechazandose(self, store):
        """Aceptar un formato por poder subirlo es prometer algo que falla luego."""
        with pytest.raises(ArchivoRechazado):
            store.guardar("hoja.xlsx", b"PK\x03\x04datos")


# --- §20: límites de cantidad -------------------------------------------------


class TestLosLimitesDeCantidad:
    """Hasta ahora solo había límite **por archivo**: nada impedía subir mil de
    19 MB y llenar el disco sin pasarse en ninguno."""

    def test_hay_un_limite_de_numero_de_archivos(self, store):
        store.max_archivos = 3

        for i in range(3):
            store.guardar(f"n{i}.txt", b"contenido")

        with pytest.raises(ArchivoRechazado, match="límite son 3"):
            store.guardar("uno-mas.txt", b"contenido")

    def test_hay_un_limite_de_espacio_total(self, store):
        store.max_total_bytes = 2000

        store.guardar("uno.txt", b"x" * 1500)

        with pytest.raises(ArchivoRechazado, match="No cabe"):
            store.guardar("dos.txt", b"x" * 1500)

    def test_el_mensaje_dice_cuanto_queda_libre(self, store):
        store.max_total_bytes = 3 * 1024 * 1024
        store.guardar("uno.txt", b"x" * (1024 * 1024))

        with pytest.raises(ArchivoRechazado) as fallo:
            store.guardar("dos.txt", b"x" * (3 * 1024 * 1024))

        assert "MB libres" in str(fallo.value)

    def test_lo_caducado_no_cuenta_para_el_cupo(self, store):
        """Se limpia antes de comprobar: lo que ya no vale no debería impedir
        subir algo nuevo."""
        store.max_archivos = 1
        viejo = store.guardar("viejo.txt", b"x")

        # Se envejece solo ese, en vez de bajar el TTL a cero: con TTL cero
        # caducaria tambien el que se acaba de subir y la prueba no probaria nada.
        registro = store.repositorio.get(viejo.id)
        registro.creado_en = time.time() - 999_999
        store.repositorio.add(registro)

        store.guardar("nuevo.txt", b"x")  # no debe lanzar: el viejo ya no cuenta

        quedan = store.listar()
        assert [a.nombre_original for a in quedan] == ["nuevo.txt"]

    def test_el_limite_por_archivo_sigue_vigente(self, store):
        store.max_bytes = 1024

        with pytest.raises(ArchivoRechazado, match="límite"):
            store.guardar("gordo.txt", b"x" * 5000)


# --- §23: archivos costosos de procesar ---------------------------------------


class TestLaExtraccionNoSeCuelga:
    """Un archivo puede ser costoso de analizar sin ser grande: un PDF con miles
    de objetos anidados, por ejemplo."""

    def test_hay_un_limite_de_tiempo_de_extraccion(self):
        assert 0 < SEGUNDOS_MAXIMOS <= 60

    def test_hay_un_limite_de_paginas(self):
        """Leer un libro entero agota el contexto del modelo."""
        assert 0 < MAX_PAGINAS <= 200

    def test_un_texto_enorme_se_recorta_y_se_dice(self, store):
        salida = extraer(b"x" * 500_000, "text/plain", "enorme.log")

        assert "recortado" in salida
        assert len(salida) < 40_000


# --- §24: auditoría -----------------------------------------------------------


class TestLaAuditoriaDeArchivos:
    """Subir y borrar son operaciones sobre datos del usuario: deben quedar
    registradas. Era un hueco real."""

    @staticmethod
    def _eventos():
        from src.api.dependencies import get_container

        return get_container().audit_logger.get_recent(limit=10)

    def test_una_subida_queda_registrada(self, api):
        api.post("/uploads", files={"file": ("foto.png", PNG)}, headers=CABECERA)

        assert any(e["tool"] == "upload_file" and e["success"] for e in self._eventos())

    def test_un_rechazo_tambien(self, api):
        """Saber que alguien intentó subir un ejecutable disfrazado importa más
        que saber que subió un PDF."""
        api.post("/uploads", files={"file": ("v.exe", EJECUTABLE)}, headers=CABECERA)

        assert any(
            e["tool"] == "upload_file" and not e["success"] for e in self._eventos()
        )

    def test_un_borrado_queda_registrado(self, api):
        subida = api.post("/uploads", files={"file": ("foto.png", PNG)}, headers=CABECERA)

        api.delete(f"/uploads/{subida.json()['id']}", headers=CABECERA)

        assert any(e["tool"] == "delete_upload" for e in self._eventos())

    def test_el_contenido_del_archivo_nunca_se_registra(self, api):
        """Un archivo del usuario puede llevar datos personales; el log de
        auditoría no es sitio para eso."""
        secreto = b"CONTRASENA_SUPER_SECRETA_12345"

        api.post("/uploads", files={"file": ("notas.txt", secreto)}, headers=CABECERA)

        assert "CONTRASENA_SUPER_SECRETA" not in str(self._eventos())

    def test_se_registra_el_nombre_y_el_tipo(self, api):
        """Lo justo para poder auditar qué entró, sin exponer el contenido."""
        api.post("/uploads", files={"file": ("informe.png", PNG)}, headers=CABECERA)

        texto = str(self._eventos())
        assert "informe.png" in texto
        assert "image/png" in texto


# --- §15: el archivo va ligado al mensaje -------------------------------------


class TestElVinculoArchivoMensaje:
    """Antes se pegaba el identificador en el texto del mensaje. Funcionaba, pero
    bastaba con que el usuario borrara esa línea para que Morgan perdiera el
    vínculo y tuviera que adivinar cuál de los archivos disponibles era el suyo."""

    def _subir(self, api, nombre="informe.md", contenido=b"# Ventas"):
        return api.post("/uploads", files={"file": (nombre, contenido)}, headers=CABECERA).json()

    def test_un_adjunto_inexistente_se_rechaza_antes_de_gastar_un_turno(self, api):
        """Mejor decirlo que gastar un turno entero para que Morgan acabe
        respondiendo que no lo encuentra."""
        respuesta = api.post(
            "/chat",
            json={"message": "lee esto", "attachments": ["no-existe"]},
            headers=CABECERA,
        )

        assert respuesta.status_code == 422
        assert respuesta.json()["error"]["code"] == "ATTACHMENT_NOT_FOUND"

    def test_el_vinculo_se_persiste_en_los_metadatos(self, api):
        subido = self._subir(api)

        api.post(
            "/chat",
            json={"message": "Resume el adjunto", "session_id": "s-adj",
                  "attachments": [subido["id"]]},
            headers=CABECERA,
        )

        mensajes = api.get("/sessions/s-adj/messages", headers=CABECERA).json()["messages"]
        del_usuario = [m for m in mensajes if m["role"] == "user"]
        assert del_usuario, "no se guardó el mensaje del usuario"

        adjuntos = del_usuario[0].get("metadata", {}).get("attachments") or []
        assert [a["id"] for a in adjuntos] == [subido["id"]]
        assert adjuntos[0]["nombre"] == "informe.md"

    def test_sin_adjuntos_los_metadatos_quedan_vacios(self, api):
        api.post("/chat", json={"message": "hola", "session_id": "s-simple"}, headers=CABECERA)

        mensajes = api.get("/sessions/s-simple/messages", headers=CABECERA).json()["messages"]
        del_usuario = [m for m in mensajes if m["role"] == "user"]
        if del_usuario:
            assert not (del_usuario[0].get("metadata") or {}).get("attachments")

    def test_se_admiten_varios_adjuntos(self, api):
        uno = self._subir(api, "uno.md", b"# Uno")
        dos = self._subir(api, "dos.md", b"# Dos")

        respuesta = api.post(
            "/chat",
            json={"message": "compara", "session_id": "s-varios",
                  "attachments": [uno["id"], dos["id"]]},
            headers=CABECERA,
        )

        assert respuesta.status_code in (200, 503)

    def test_hay_un_limite_de_adjuntos_por_mensaje(self, api):
        respuesta = api.post(
            "/chat",
            json={"message": "x", "attachments": [f"id-{i}" for i in range(20)]},
            headers=CABECERA,
        )

        assert respuesta.status_code == 422


# --- §19: el idioma del archivo no manda --------------------------------------


class TestElIdiomaDeLaRespuesta:
    def test_el_prompt_dice_que_el_idioma_de_un_audio_no_manda(self):
        """Transcribir una nota de voz en inglés no significa contestar en inglés."""
        from src.agent.prompt import SYSTEM_PROMPT

        assert "idioma" in SYSTEM_PROMPT.lower()
        assert "no manda" in SYSTEM_PROMPT

    def test_el_prompt_aisla_tambien_los_archivos(self):
        from src.agent.prompt import SYSTEM_PROMPT

        assert "untrusted_file_data" in SYSTEM_PROMPT
