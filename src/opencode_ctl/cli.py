from typing import Optional
import json
import subprocess

import typer
from rich.console import Console
from rich.table import Table

from .client import OpenCodeClientError
from .runner import OpenCodeRunner, SessionNotFoundError, SessionNotRunningError

app = typer.Typer(name="occtl", help="OpenCode session lifecycle manager")
console = Console()
runner = OpenCodeRunner()


def _handle_session_error(e: Exception) -> None:
    if isinstance(e, SessionNotFoundError):
        console.print(f"[yellow]Not found:[/yellow] {e}")
        raise typer.Exit(1)
    if isinstance(e, SessionNotRunningError):
        console.print(f"[red]Session not running:[/red] {e.status}")
        raise typer.Exit(1)
    if isinstance(e, OpenCodeClientError):
        console.print(f"[red]Error:[/red] {e.status_code} {e.message}")
        raise typer.Exit(1)
    console.print(f"[red]Failed:[/red] {e}")
    raise typer.Exit(1)


@app.command()
def start(
    workdir: Optional[str] = typer.Option(
        None, "--workdir", "-w", help="Working directory for OpenCode"
    ),
    timeout: float = typer.Option(
        30.0, "--timeout", "-t", help="Startup timeout in seconds"
    ),
):
    try:
        session = runner.start(workdir=workdir, timeout=timeout)
        console.print(f"[green]Started session:[/green] {session.id}")
        console.print(f"  Port: {session.port}")
        console.print(f"  PID: {session.pid}")
    except FileNotFoundError as e:
        console.print(f"[red]Directory not found:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed to start:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def stop(
    session_id: str = typer.Argument(..., help="Session ID to stop"),
    force: bool = typer.Option(False, "--force", "-f", help="Force kill"),
):
    if runner.stop(session_id, force=force):
        console.print(f"[green]Stopped:[/green] {session_id}")
    else:
        console.print(f"[yellow]Not found:[/yellow] {session_id}")
        raise typer.Exit(1)


@app.command()
def status(session_id: str = typer.Argument(..., help="Session ID")):
    session = runner.status(session_id)
    if not session:
        console.print(f"[yellow]Not found:[/yellow] {session_id}")
        raise typer.Exit(1)

    color = {"running": "green", "dead": "red", "unresponsive": "yellow"}.get(
        session.status, "white"
    )
    console.print(f"[{color}]{session.status}[/{color}] {session.id}")
    console.print(f"  Port: {session.port}")
    console.print(f"  PID: {session.pid}")
    console.print(f"  Last activity: {session.last_activity}")


@app.command(name="list")
def list_sessions():
    sessions = runner.list_sessions()
    if not sessions:
        console.print("[dim]No active sessions[/dim]")
        return

    table = Table()
    table.add_column("ID")
    table.add_column("Port")
    table.add_column("PID")
    table.add_column("Status")
    table.add_column("Last Activity")

    for s in sessions:
        table.add_row(s.id, str(s.port), str(s.pid), s.status, s.last_activity)

    console.print(table)


@app.command()
def cleanup(
    max_idle: int = typer.Option(
        60, "--max-idle", "-m", help="Max idle seconds before cleanup"
    ),
):
    stopped = runner.cleanup_idle(max_idle_seconds=max_idle)
    if stopped:
        console.print(f"[green]Cleaned up {len(stopped)} session(s):[/green]")
        for sid in stopped:
            console.print(f"  - {sid}")
    else:
        console.print("[dim]No idle sessions to clean[/dim]")


@app.command()
def touch(session_id: str = typer.Argument(..., help="Session ID to touch")):
    if runner.touch(session_id):
        console.print(f"[green]Updated activity:[/green] {session_id}")
    else:
        console.print(f"[yellow]Not found:[/yellow] {session_id}")
        raise typer.Exit(1)


@app.command()
def send(
    session_id: str = typer.Argument(..., help="Session ID (occtl session)"),
    message: str = typer.Argument(..., help="Message to send"),
    agent: Optional[str] = typer.Option(
        None, "--agent", "-a", help="Agent to use (e.g., docs-retriever)"
    ),
    timeout: float = typer.Option(
        300.0, "--timeout", "-t", help="Request timeout in seconds"
    ),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON response"),
):
    try:
        result = runner.send(session_id, message, agent=agent, timeout=timeout)
        if raw:
            console.print(json.dumps(result.raw, indent=2))
        else:
            console.print(result.text)
    except Exception as e:
        _handle_session_error(e)


@app.command()
def attach(session_id: str = typer.Argument(..., help="Session ID to attach")):
    try:
        url = runner.get_attach_url(session_id)
        console.print(f"[dim]Attaching to {url}...[/dim]")
        subprocess.run(["opencode", "attach", url])
    except Exception as e:
        _handle_session_error(e)


@app.command()
def permissions(session_id: str = typer.Argument(..., help="Session ID")):
    try:
        perms = runner.list_permissions(session_id)
        if not perms:
            console.print("[dim]No pending permissions[/dim]")
            return

        table = Table()
        table.add_column("ID")
        table.add_column("Permission")
        table.add_column("Pattern")
        table.add_column("Tool")

        for p in perms:
            table.add_row(p.id, p.permission, p.pattern, p.tool_name)

        console.print(table)
    except Exception as e:
        _handle_session_error(e)


@app.command()
def approve(
    session_id: str = typer.Argument(..., help="Session ID"),
    permission_id: str = typer.Argument(..., help="Permission ID to approve"),
    always: bool = typer.Option(
        False, "--always", "-a", help="Always allow this pattern"
    ),
):
    try:
        runner.approve_permission(session_id, permission_id, always=always)
        reply = "always" if always else "once"
        console.print(f"[green]Approved ({reply}):[/green] {permission_id}")
    except Exception as e:
        _handle_session_error(e)


@app.command()
def reject(
    session_id: str = typer.Argument(..., help="Session ID"),
    permission_id: str = typer.Argument(..., help="Permission ID to reject"),
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="Rejection message"
    ),
):
    try:
        runner.reject_permission(session_id, permission_id, message=message)
        console.print(f"[yellow]Rejected:[/yellow] {permission_id}")
    except Exception as e:
        _handle_session_error(e)


@app.command()
def version():
    """Print occtl version"""
    console.print("0.2.0")


if __name__ == "__main__":
    app()
