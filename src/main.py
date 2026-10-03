"""
Morgan — Punto de entrada principal (V0.9).

CLI interactiva con Rich para conversar con el agente, programar proyectos,
gestionar memoria, consultar auditoría y administrar herramientas por dominios.
"""

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.config import get_settings
from src.logging_config import setup_logging
from src.agent.core import Agent
from src.api.dependencies import CoreContainer, get_container
from src.memory.manager import MemoryManager
from src.security.audit import AuditLogger
from src.security.permissions import _detect_interactive
from src.tools.registry import ToolRegistry

console = Console()


def show_banner():
    banner_raw = (
        "  __  __  ___  ____   ____    _    _   _ \n"
        " |  \\/  |/ _ \\|  _ \\ / ___|  / \\  | \\ | |\n"
        " | |\\/| | | | | |_) | |  _  / _ \\ |  \\| |\n"
        " | |  | | |_| |  _ <| |_| |/ ___ \\| |\\  |\n"
        " |_|  |_|\\___/|_| \\_\\\\____//_/   \\_\\_| \\_|\n"
        " Personal AI Agent for Windows (v0.9 — Coding Agent & Security Ready)"
    )
    console.print(Panel(
        Text(banner_raw, style="bold cyan"),
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()
    console.print(
        "  Escribe un mensaje para conversar o programar. "
        "Usa [bold cyan]/help[/bold cyan] para ver comandos.",
        style="dim",
    )
    console.print()


def show_help():
    table = Table(
        title="Comandos de Morgan",
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Comando", style="bold cyan")
    table.add_column("Descripción")

    table.add_row("/help", "Muestra esta ayuda")
    table.add_row("/tools", "Lista todas las herramientas con su nivel de riesgo y dominio")
    table.add_row("/domains", "Muestra las herramientas agrupadas por categorías de dominio")
    table.add_row("/memory", "Muestra hechos y preferencias guardadas en SQLite")
    table.add_row("/audit", "Muestra los registros recientes del log de auditoría de seguridad")
    table.add_row("/clear", "Limpia el historial de conversación actual en memoria")
    table.add_row("/exit", "Salir de Morgan")

    console.print()
    console.print(table)
    console.print()


def show_tools(agent: Agent):
    tools_info = agent.get_tools_info()
    table = Table(
        title=f"Catálogo de Herramientas de Morgan ({len(tools_info)} disponibles)",
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Herramienta", style="bold")
    table.add_column("Dominio", style="magenta")
    table.add_column("Descripción")
    table.add_column("Nivel de Riesgo", justify="center")

    risk_display = {
        "safe": "[green]🟢 SAFE[/green]",
        "low_risk": "[green]🟢 LOW_RISK[/green]",
        "moderate": "[yellow]🟡 MODERATE[/yellow]",
        "high_risk": "[orange3]🟠 HIGH_RISK[/orange3]",
        "critical": "[red]🔴 CRITICAL[/red]",
        "sensitive": "[red]🔴 CRITICAL[/red]",
    }

    for t in agent.tools.list_tools():
        r = getattr(t, "risk_level", t.permission_level)
        table.add_row(
            t.name,
            getattr(t, "category", "general").upper(),
            t.description,
            risk_display.get(r, r.upper()),
        )

    console.print()
    console.print(table)
    console.print()


def show_domains(registry: ToolRegistry):
    table = Table(
        title="Dominios de Herramientas (V0.8)",
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Dominio", style="bold magenta")
    table.add_column("Herramientas Incluidas")

    for cat in registry.get_categories():
        tools = registry.get_by_category(cat)
        names = ", ".join([f"[cyan]{t.name}[/cyan]" for t in tools])
        table.add_row(cat.upper(), names)

    console.print()
    console.print(table)
    console.print()


def show_audit(audit_logger: AuditLogger):
    entries = audit_logger.get_recent(limit=12)
    table = Table(
        title=f"Registro de Auditoría de Seguridad ({len(entries)} eventos recientes)",
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Fecha/Hora", style="dim")
    table.add_column("Herramienta", style="bold cyan")
    table.add_column("Nivel", justify="center")
    table.add_column("Autorizado", justify="center")
    table.add_column("Resultado", justify="center")

    for e in entries:
        auth_str = "[green]✔ Sí[/green]" if e["authorized"] else "[red]✖ No[/red]"
        res_str = "[green]Éxito[/green]" if e["success"] else f"[red]{e.get('error') or 'Fallo'}[/red]"
        table.add_row(
            e["timestamp"],
            e["tool"],
            e["risk_level"].upper(),
            auth_str,
            res_str,
        )

    console.print()
    console.print(table)
    console.print()


def show_memory(memory_manager: MemoryManager):
    memories = memory_manager.recall()
    table = Table(
        title=f"Memoria Persistente SQLite ({len(memories)} recuerdos guardados)",
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Categoría", style="bold magenta")
    table.add_column("Clave", style="bold cyan")
    table.add_column("Valor")
    table.add_column("Actualizado", style="dim")

    if not memories:
        console.print("\n  [dim]No hay recuerdos almacenados aún. Pídele a Morgan que recuerde algo.[/dim]\n")
        return

    for m in memories:
        table.add_row(m["category"], m["key"], m["value"], m["updated_at"])

    console.print()
    console.print(table)
    console.print()


def preparar_consola() -> CoreContainer:
    """Monta Morgan para la consola sobre el mismo contenedor que la API.

    Hasta la 2.0.11 la consola armaba su propio catálogo, copiado del de la API, y
    la copia se había quedado atrás: le faltaban 12 de 49 herramientas —el
    conocimiento, los planes y GitHub— mientras un comentario aseguraba que las
    dos compartían catálogo. Con una sola construcción no pueden divergir.
    """
    settings = get_settings()
    # Antes que el contenedor: `setup_logging` es idempotente, así que la llamada
    # que hace él ya no añade la salida por pantalla, que se mezclaría con la
    # conversación.
    setup_logging(level=settings.log_level, log_dir=settings.log_dir, console=False)

    container = get_container()
    # El contenedor está pensado para la API, que no tiene a quién preguntar y
    # deniega. En la consola puede haber alguien delante: se detecta igual que
    # antes. El agente guarda este mismo objeto, así que lo ve.
    container.permission_manager.interactive = _detect_interactive()
    return container


def main():
    show_banner()

    container = preparar_consola()
    agent = container.agent
    if agent is None:
        console.print(" [red]✖[/red]")
        console.print()
        console.print(
            "  [red]Error:[/red] Morgan no tiene modelo "
            f"({container.llm_error or 'sin proveedor configurado'}). "
            "Revisa las claves en .env"
        )
        console.print()
        sys.exit(1)

    console.print(
        "  [green]✔[/green] [dim]Motor: "
        f"[bold cyan]{getattr(agent.model, 'model_name', '?')}[/bold cyan][/dim]"
    )

    audit_logger = container.audit_logger
    memory_manager = container.memory_manager
    registry = container.tool_registry

    console.print(
        f"  [dim]Herramientas activas: [bold cyan]{len(registry)}[/bold cyan] en [bold cyan]{len(registry.get_categories())}[/bold cyan] dominios | "
        f"Auditoría: [bold cyan]Activa[/bold cyan][/dim]"
    )
    console.print()

    # --- Loop de conversación ---
    while True:
        try:
            user_input = console.input("[bold cyan]  tú >[/bold cyan] ").strip()

            if not user_input:
                continue

            if user_input.startswith("/"):
                cmd = user_input.lower()

                if cmd in ("/exit", "/quit", "/q"):
                    console.print("\n  [dim]Hasta pronto 👋 Morgan en reposo.[/dim]\n")
                    break
                elif cmd == "/help":
                    show_help()
                    continue
                elif cmd == "/tools":
                    show_tools(agent)
                    continue
                elif cmd == "/domains":
                    show_domains(registry)
                    continue
                elif cmd == "/memory":
                    show_memory(memory_manager)
                    continue
                elif cmd == "/audit":
                    show_audit(audit_logger)
                    continue
                elif cmd == "/clear":
                    agent.clear_history()
                    console.print("  [dim]Historial en memoria limpiado ✔[/dim]\n")
                    continue
                else:
                    console.print(f"  [yellow]Comando desconocido:[/yellow] {user_input}. Usa [bold]/help[/bold].\n")
                    continue

            console.print()
            with console.status("[dim]Morgan está analizando y ejecutando...[/dim]", spinner="dots"):
                response = agent.process(user_input)

            console.print()
            response_panel = Panel(
                Markdown(response),
                title="[bold cyan]Morgan[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
            console.print(response_panel)
            console.print()

        except KeyboardInterrupt:
            console.print("\n\n  [dim]Sesión interrumpida. Hasta pronto 👋[/dim]\n")
            break
        except EOFError:
            console.print("\n\n  [dim]Hasta pronto 👋[/dim]\n")
            break
        except Exception as e:
            console.print(f"\n  [red]Error:[/red] {e}\n")


if __name__ == "__main__":
    main()
