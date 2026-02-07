from importlib.metadata import version as get_version
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


def _resolve_oc_session(session_id: str, oc_session: str | None) -> str:
    """Resolve OpenCode session ID: use provided or auto-detect latest."""
    if oc_session:
        return oc_session
    latest = runner.get_latest_oc_session(session_id)
    return latest.id


@app.command()
def start(
    workdir: Optional[str] = typer.Option(
        None, "--workdir", "-w", help="Working directory for OpenCode"
    ),
    timeout: float = typer.Option(
        30.0, "--timeout", "-t", help="Startup timeout in seconds"
    ),
    allow_occtl_commands: bool = typer.Option(
        False, "--allow-occtl-commands", help="Allow occtl commands inside session"
    ),
    agent: Optional[str] = typer.Option(
        None, "--agent", "-a", help="Default agent for this session"
    ),
):
    try:
        session = runner.start(
            workdir=workdir,
            timeout=timeout,
            allow_occtl_commands=allow_occtl_commands,
            agent=agent,
        )
        console.print(f"[green]Started session:[/green] {session.id}")
        console.print(f"  Port: {session.port}")
        console.print(f"  PID: {session.pid}")
        if session.agent:
            console.print(f"  Agent: {session.agent}")
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

    color = {
        "running": "green",
        "idle": "cyan",
        "waiting_permission": "yellow",
        "dead": "red",
        "error": "red",
    }.get(session.status, "white")
    console.print(f"[{color}]{session.status}[/{color}] {session.id}")
    console.print(f"  Port: {session.port}")
    console.print(f"  PID: {session.pid}")
    if session.agent:
        console.print(f"  Agent: {session.agent}")
    console.print(f"  Last activity: {session.last_activity}")

    has_changes, changed_files = runner.has_uncommitted_changes(session_id)
    if has_changes:
        console.print(
            f"\n  [yellow]Uncommitted changes ({len(changed_files)}):[/yellow]"
        )
        for file in changed_files:
            console.print(f"    {file}")
    else:
        console.print("\n  [green]No uncommitted changes[/green]")


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
    table.add_column("Agent")
    table.add_column("Dirty")
    table.add_column("Last Activity")

    for s in sessions:
        dirty_marker = (
            "[yellow]✓[/yellow]" if s.has_uncommitted_changes else "[green]✗[/green]"
        )
        table.add_row(
            s.id,
            str(s.port),
            str(s.pid),
            s.status,
            s.agent or "[dim]—[/dim]",
            dirty_marker,
            s.last_activity,
        )

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
    wait: bool = typer.Option(
        False, "--wait", "-w", help="Wait for response (sync mode)"
    ),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON response"),
):
    try:
        result = runner.send(
            session_id, message, agent=agent, timeout=timeout, wait=wait
        )
        if wait:
            if raw:
                console.print(json.dumps(result.raw, indent=2))
            else:
                console.print(result.text)
        else:
            console.print(result.session_id)
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
def permissions(
    session_id: Optional[str] = typer.Argument(
        None, help="Session ID (omit to show all sessions)"
    ),
):
    try:
        if session_id:
            # Single session mode
            perms = runner.list_permissions(session_id)
            if not perms:
                console.print("[dim]No pending permissions[/dim]")
                return

            table = Table(show_lines=True)
            table.add_column("ID", style="dim")
            table.add_column("Type")
            table.add_column("Commands", style="cyan")

            for p in perms:
                commands = "\n".join(p.patterns) if p.patterns else "[dim]—[/dim]"
                table.add_row(p.id, p.permission, commands)

            console.print(table)
        else:
            # All sessions mode
            sessions = runner.list_sessions()
            if not sessions:
                console.print("[dim]No active sessions[/dim]")
                return

            total_perms = 0
            for s in sessions:
                if s.status == "dead":
                    continue
                try:
                    perms = runner.list_permissions(s.id)
                    if perms:
                        total_perms += len(perms)
                        console.print(f"\n[bold cyan]{s.id}[/bold cyan]")

                        table = Table(show_lines=True)
                        table.add_column("ID", style="dim")
                        table.add_column("Type")
                        table.add_column("Commands", style="cyan")

                        for p in perms:
                            commands = (
                                "\n".join(p.patterns) if p.patterns else "[dim]—[/dim]"
                            )
                            table.add_row(p.id, p.permission, commands)

                        console.print(table)
                except Exception:
                    # Session might have died between list and permissions check
                    pass

            if total_perms == 0:
                console.print("[dim]No pending permissions in any session[/dim]")
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
def sessions(session_id: str = typer.Argument(..., help="occtl session ID")):
    """List OpenCode sessions inside an occtl session"""
    try:
        oc_sessions = runner.list_oc_sessions(session_id)
        if not oc_sessions:
            console.print("[dim]No sessions[/dim]")
            return

        table = Table()
        table.add_column("Session ID", style="cyan")
        table.add_column("Title")
        table.add_column("Updated")

        from datetime import datetime

        for s in oc_sessions:
            updated = (
                datetime.fromtimestamp(s.updated / 1000).strftime("%H:%M:%S")
                if s.updated
                else "—"
            )
            title = s.title[:50] + "..." if len(s.title) > 50 else s.title
            table.add_row(s.id, title, updated)

        console.print(table)
    except Exception as e:
        _handle_session_error(e)


@app.command()
def chain(
    session_id: str = typer.Argument(..., help="occtl session ID"),
    oc_session: Optional[str] = typer.Option(
        None, "--session", "-s", help="OpenCode session ID (default: latest)"
    ),
):
    """Show session chain (parent sessions from compaction)"""
    try:
        oc_session = _resolve_oc_session(session_id, oc_session)
        chain_sessions = runner.get_session_chain(session_id, oc_session)
        if not chain_sessions:
            console.print("[dim]No chain found[/dim]")
            return

        from datetime import datetime

        table = Table(title="Session Chain")
        table.add_column("Session ID", style="cyan")
        table.add_column("Title")
        table.add_column("Parent")
        table.add_column("Created")

        for s in chain_sessions:
            created = (
                datetime.fromtimestamp(s.created / 1000).strftime("%Y-%m-%d %H:%M")
                if s.created
                else "—"
            )
            title = s.title[:40] + "..." if len(s.title) > 40 else s.title
            parent = (
                s.parent_id[:20] + "..."
                if s.parent_id and len(s.parent_id) > 20
                else (s.parent_id or "—")
            )
            marker = "→ " if s.id == oc_session else "  "
            table.add_row(marker + s.id, title, parent, created)

        console.print(table)
    except Exception as e:
        _handle_session_error(e)


@app.command()
def fork(
    session_id: str = typer.Argument(..., help="occtl session ID"),
    oc_session: Optional[str] = typer.Option(
        None, "--session", "-s", help="OpenCode session ID to fork (default: latest)"
    ),
    message_id: Optional[str] = typer.Option(
        None, "--message", "-m", help="Fork up to (not including) this message ID"
    ),
):
    """Fork an OpenCode session, creating a copy of its conversation history."""
    try:
        oc_session = _resolve_oc_session(session_id, oc_session)
        forked = runner.fork_session(session_id, oc_session, message_id)
        console.print(f"[green]Forked:[/green] {forked.id}")
        if forked.parent_id:
            console.print(f"  Parent: {forked.parent_id}")
    except Exception as e:
        _handle_session_error(e)


@app.command()
def tail(
    session_id: str = typer.Argument(..., help="occtl session ID"),
    oc_session: Optional[str] = typer.Option(
        None, "--session", "-s", help="OpenCode session ID (default: latest)"
    ),
    follow: bool = typer.Option(False, "--follow", "-f", help="Wait for completion"),
    last: bool = typer.Option(
        False, "--last", "-l", help="Only show last assistant message"
    ),
    full: bool = typer.Option(False, "--full", help="Don't truncate text"),
    limit: int = typer.Option(5, "--limit", "-n", help="Number of messages to show"),
    timeout: float = typer.Option(
        300.0, "--timeout", "-t", help="Follow timeout in seconds"
    ),
    raw: bool = typer.Option(
        False, "--raw", "-r", help="Output raw text only (no formatting)"
    ),
    role: Optional[str] = typer.Option(
        None, "--role", help="Filter by role: user, assistant"
    ),
    search: Optional[str] = typer.Option(
        None,
        "--search",
        "-g",
        help="Filter messages containing pattern (case-insensitive)",
    ),
    tools: bool = typer.Option(
        False, "--tools", help="Show tool call details (args and results)"
    ),
    timestamps: bool = typer.Option(
        False, "--timestamps", "-T", help="Show message timestamps"
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Export messages to file"
    ),
    chain_mode: bool = typer.Option(
        False,
        "--chain",
        "-C",
        help="Include messages from parent sessions (full history)",
    ),
):
    try:
        oc_session = _resolve_oc_session(session_id, oc_session)

        def filter_messages(msgs: list) -> list:
            result = msgs
            if role:
                result = [m for m in result if m.role == role]
            if search:
                pattern = search.lower()
                result = [m for m in result if pattern in m.text.lower()]
            return result

        def format_message(msg, for_file: bool = False) -> str:
            lines = []

            if timestamps and msg.timestamp:
                from datetime import datetime

                ts = datetime.fromtimestamp(msg.timestamp / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                if for_file:
                    lines.append(f"[{ts}]")
                else:
                    lines.append(f"[dim][{ts}][/dim]")

            if for_file:
                lines.append(f"━━━ {msg.role} ━━━")
            else:
                role_color = {"user": "green", "assistant": "blue"}.get(
                    msg.role, "white"
                )
                lines.append(f"[{role_color}]━━━ {msg.role} ━━━[/{role_color}]")

            if msg.text:
                text = (
                    msg.text
                    if full
                    else (msg.text[:500] + "..." if len(msg.text) > 500 else msg.text)
                )
                lines.append(text)

            for tc in msg.tool_calls:
                if tools:
                    if for_file:
                        lines.append(f"  ⚡ {tc.name} ({tc.state})")
                        if tc.args:
                            import json

                            args_str = json.dumps(tc.args, indent=4, ensure_ascii=False)
                            for arg_line in args_str.split("\n"):
                                lines.append(f"    {arg_line}")
                        if tc.result and tc.state == "result":
                            result_preview = (
                                tc.result[:200] + "..."
                                if len(tc.result) > 200
                                else tc.result
                            )
                            lines.append(f"    → {result_preview}")
                    else:
                        state_color = {"result": "green", "call": "yellow"}.get(
                            tc.state, "dim"
                        )
                        lines.append(
                            f"  [{state_color}]⚡ {tc.name} ({tc.state})[/{state_color}]"
                        )
                        if tc.args:
                            import json

                            args_str = json.dumps(tc.args, indent=4, ensure_ascii=False)
                            lines.append(f"[dim]{args_str}[/dim]")
                        if tc.result and tc.state == "result":
                            result_preview = (
                                tc.result[:200] + "..."
                                if len(tc.result) > 200
                                else tc.result
                            )
                            lines.append(f"[dim]    → {result_preview}[/dim]")
                else:
                    if for_file:
                        lines.append(f"  ⚡ {tc.name} ({tc.state})")
                    else:
                        state_color = {"result": "green", "call": "yellow"}.get(
                            tc.state, "dim"
                        )
                        lines.append(
                            f"  [{state_color}]⚡ {tc.name} ({tc.state})[/{state_color}]"
                        )

            lines.append("")
            return "\n".join(lines)

        if follow:
            msg = runner.wait_for_response(session_id, oc_session, timeout=timeout)
            if not msg:
                console.print("[yellow]Timeout waiting for response[/yellow]")
                raise typer.Exit(1)
            if raw:
                console.print(msg.text)
            else:
                console.print(format_message(msg))
            return

        if last:
            if chain_mode:
                messages = runner.get_chain_messages(session_id, oc_session, limit=100)
            else:
                messages = runner.get_messages(session_id, oc_session, limit=20)
            messages = filter_messages(messages)
            for msg in reversed(messages):
                if msg.role == "assistant":
                    if raw:
                        console.print(msg.text)
                    else:
                        console.print(format_message(msg))
                    return
            console.print("[dim]No assistant messages[/dim]")
            return

        if chain_mode:
            messages = runner.get_chain_messages(session_id, oc_session, limit)
        else:
            messages = runner.get_messages(session_id, oc_session, limit)
        messages = filter_messages(messages)

        if not messages:
            console.print("[dim]No messages[/dim]")
            return

        if output:
            with open(output, "w") as f:
                for msg in messages:
                    f.write(format_message(msg, for_file=True))
            console.print(
                f"[green]Exported {len(messages)} messages to {output}[/green]"
            )
            return

        for msg in messages:
            if raw:
                console.print(msg.text)
            else:
                console.print(format_message(msg))

    except Exception as e:
        _handle_session_error(e)


@app.command()
def version():
    console.print(get_version("opencode-ctl"))


if __name__ == "__main__":
    app()
