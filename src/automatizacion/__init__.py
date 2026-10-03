"""
Las automatizaciones (4.14): órdenes guardadas con un horario que Morgan ejecuta sola, sin
nadie delante, y cuyo resultado deja en la bandeja de la web. Diseño y mis decisiones en
docs/plan-4.x.md («La propuesta» y «Mis decisiones»).

- `horario.py`: cuándo toca (puro: sin base, sin red).
- `contexto.py`: la zona horaria de quien pide y si este turno es de una automatización.
- `repositorio.py`: dónde se guardan (SQLite en local, Supabase en la nube).
- `ejecutor.py`: el reloj y la ejecución.
"""
