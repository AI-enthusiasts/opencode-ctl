from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .runner import OpenCodeRunner

app = typer.Typer(name="occtl", help="OpenCode session lifecycle manager")
console = Console()
runner = OpenCodeRunner()


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
    session_id: str = typer.Argument(..., help="Session ID"),
    message: str = typer.Argument(..., help="Message to send"),
):
    session = runner.status(session_id)
    if not session:
        console.print(f"[yellow]Not found:[/yellow] {session_id}")
        raise typer.Exit(1)

    if session.status != "running":
        console.print(f"[red]Session not running:[/red] {session.status}")
        raise typer.Exit(1)

    runner.touch(session_id)

    import httpx

    url = f"http://localhost:{session.port}"
    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(f"{url}/message", json={"content": message})
            if resp.status_code == 200:
                data = resp.json()
                console.print(data.get("response", ""))
            else:
                console.print(f"[red]Error:[/red] {resp.status_code} {resp.text}")
                raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def attach(session_id: str = typer.Argument(..., help="Session ID to attach")):
    session = runner.status(session_id)
    if not session:
        console.print(f"[yellow]Not found:[/yellow] {session_id}")
        raise typer.Exit(1)

    if session.status != "running":
        console.print(f"[red]Session not running:[/red] {session.status}")
        raise typer.Exit(1)

    import subprocess

    url = f"http://localhost:{session.port}"
    console.print(f"[dim]Attaching to {url}...[/dim]")
    subprocess.run(["opencode", "attach", url])


@app.command()
def permissions(session_id: str = typer.Argument(..., help="Session ID")):
    """List pending permission requests for a session."""
    session = runner.status(session_id)
    if not session:
        console.print(f"[yellow]Not found:[/yellow] {session_id}")
        raise typer.Exit(1)

    if session.status != "running":
        console.print(f"[red]Session not running:[/red] {session.status}")
        raise typer.Exit(1)

    import httpx

    url = f"http://localhost:{session.port}"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{url}/permission")
            if resp.status_code == 200:
                data = resp.json()
                if not data:
                    console.print("[dim]No pending permissions[/dim]")
                    return

                table = Table()
                table.add_column("ID")
                table.add_column("Permission")
                table.add_column("Pattern")
                table.add_column("Tool")

                for perm in data:
                    table.add_row(
                        perm.get("id", ""),
                        perm.get("permission", ""),
                        perm.get("pattern", ""),
                        perm.get("tool", {}).get("name", ""),
                    )
                console.print(table)
            else:
                console.print(f"[red]Error:[/red] {resp.status_code} {resp.text}")
                raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def approve(
    session_id: str = typer.Argument(..., help="Session ID"),
    permission_id: str = typer.Argument(..., help="Permission ID to approve"),
    always: bool = typer.Option(
        False, "--always", "-a", help="Always allow this pattern"
    ),
):
    """Approve a pending permission request."""
    session = runner.status(session_id)
    if not session:
        console.print(f"[yellow]Not found:[/yellow] {session_id}")
        raise typer.Exit(1)

    if session.status != "running":
        console.print(f"[red]Session not running:[/red] {session.status}")
        raise typer.Exit(1)

    import httpx

    url = f"http://localhost:{session.port}"
    reply = "always" if always else "once"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{url}/permission/{permission_id}/reply",
                json={"reply": reply},
            )
            if resp.status_code == 200:
                console.print(f"[green]Approved ({reply}):[/green] {permission_id}")
            else:
                console.print(f"[red]Error:[/red] {resp.status_code} {resp.text}")
                raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def reject(
    session_id: str = typer.Argument(..., help="Session ID"),
    permission_id: str = typer.Argument(..., help="Permission ID to reject"),
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="Rejection message"
    ),
):
    """Reject a pending permission request."""
    session = runner.status(session_id)
    if not session:
        console.print(f"[yellow]Not found:[/yellow] {session_id}")
        raise typer.Exit(1)

    if session.status != "running":
        console.print(f"[red]Session not running:[/red] {session.status}")
        raise typer.Exit(1)

    import httpx

    url = f"http://localhost:{session.port}"
    body = {"reply": "reject"}
    if message:
        body["message"] = message
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{url}/permission/{permission_id}/reply",
                json=body,
            )
            if resp.status_code == 200:
                console.print(f"[yellow]Rejected:[/yellow] {permission_id}")
            else:
                console.print(f"[red]Error:[/red] {resp.status_code} {resp.text}")
                raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
