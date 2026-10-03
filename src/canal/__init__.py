"""
El canal entre la nube y los agentes locales (3.0-D), del lado de la nube.

- `registro.py`: qué agentes están conectados ahora mismo, en este proceso.
- `despacho.py`: mandar una petición al agente **de la persona del turno** y esperar
  el resultado, con cola, plazo y auditoría.

El endpoint WebSocket vive en `src/api/routes/agentes.py`. El contrato, en
docs/agente-local.md (§7-§9, §14-§15).
"""
