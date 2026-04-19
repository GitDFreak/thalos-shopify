"""Reporting — Rich console + HTML export."""
from datetime import datetime
from pathlib import Path
from typing import Dict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console(record=True)


def section_header(num: int, name: str, emoji: str = ""):
    console.print(Panel(f"{emoji} {num}. {name}", style="bold cyan", width=60))


def section_done(name: str, result_dict: Dict[str, int]):
    parts = []
    for k, v in result_dict.items():
        if v > 0:
            color = "green" if k in ("created", "updated", "skipped", "ok") else "yellow" if k == "deleted" else "red"
            parts.append(f"[{color}]{v}[/{color}] {k}")
    console.print(f"  ✓ {' │ '.join(parts)}\n" if parts else "  ✓ done\n")


def print_summary(stats: Dict, timings: Dict, config):
    console.print()

    # Entity table
    t = Table(title="Sync Terminé", box=box.ROUNDED, style="bold")
    t.add_column("Section", style="cyan")
    t.add_column("Created", justify="right", style="green")
    t.add_column("Updated", justify="right", style="blue")
    t.add_column("Deleted", justify="right", style="yellow")
    t.add_column("Errors", justify="right", style="red")
    for section, s in stats.items():
        t.add_row(
            section,
            str(s.get("created", 0)),
            str(s.get("updated", 0)),
            str(s.get("deleted", 0)),
            str(s.get("errors", 0)) if s.get("errors") else "[dim]0[/dim]",
        )
    console.print(t)

    # Timings
    if timings:
        t2 = Table(title="Durées", box=box.SIMPLE)
        t2.add_column("Section", style="yellow")
        t2.add_column("Durée", justify="right")
        total = 0
        for name, dur in timings.items():
            m, s = divmod(int(dur), 60)
            t2.add_row(name, f"{m}m {s}s")
            total += dur
        t2.add_row("[bold]TOTAL[/bold]", f"[bold green]{int(total // 60)}m {int(total % 60)}s[/bold green]")
        console.print(t2)

    console.print(f"\n🔗 https://admin.shopify.com/store/{config.dest_store}")

    if config.dry_run:
        console.print("[yellow bold]⚠ Mode DRY RUN — aucune modification effectuée[/yellow bold]")


def export_html(filepath="sync_report.html"):
    html = console.export_html(inline_styles=True)
    Path(filepath).write_text(html)
    console.print(f"\n📄 Rapport: {filepath}")
