from importlib.metadata import version as get_version
from typing import Optional
import fnmatch
import json
import os
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
def config(
    session_id: str = typer.Argument(..., help="occtl session ID"),
    section: Optional[str] = typer.Argument(
        None, help="Section to show: permission, agent, tools, all (default: all)"
    ),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """Show resolved OpenCode configuration (permissions, agents, tools)."""
    try:
        cfg = runner.get_config(session_id)
        section_name = section or "all"

        if as_json:
            if section_name == "all":
                data = {
                    "permission": cfg.get("permission", {}),
                    "agent": cfg.get("agent", {}),
                    "tools": cfg.get("tools", {}),
                }
            else:
                data = cfg.get(section_name, {})
            console.print(json.dumps(data, indent=2))
            return

        if section_name in ("all", "permission"):
            permission = cfg.get("permission", {})
            if permission:
                table = Table(title="Permission Rules")
                table.add_column("Permission", style="cyan")
                table.add_column("Pattern")
                table.add_column("Action")

                for key, value in permission.items():
                    if key.startswith("__"):
                        continue
                    if isinstance(value, dict):
                        for pattern, action in value.items():
                            color = {
                                "allow": "green",
                                "deny": "red",
                                "ask": "yellow",
                            }.get(str(action), "white")
                            table.add_row(key, pattern, f"[{color}]{action}[/{color}]")
                    else:
                        color = {"allow": "green", "deny": "red", "ask": "yellow"}.get(
                            str(value), "white"
                        )
                        table.add_row("*", key, f"[{color}]{value}[/{color}]")

                console.print(table)
            else:
                console.print("[dim]No permission rules[/dim]")

        if section_name in ("all", "agent"):
            agents = cfg.get("agent", {})
            if agents:
                console.print()
                table = Table(title="Agent Configuration")
                table.add_column("Agent", style="cyan")
                table.add_column("Model")
                table.add_column("Permission overrides")

                for name, agent_cfg in sorted(agents.items()):
                    if not isinstance(agent_cfg, dict):
                        continue
                    model = agent_cfg.get("model", "—")
                    perms = agent_cfg.get("permission", {})
                    perm_str = (
                        ", ".join(f"{k}={v}" for k, v in perms.items())
                        if perms
                        else "—"
                    )
                    table.add_row(name, str(model), perm_str)

                console.print(table)

        if section_name in ("all", "tools"):
            tools = cfg.get("tools", {})
            if tools:
                console.print()
                table = Table(title="Tool Overrides")
                table.add_column("Tool", style="cyan")
                table.add_column("Enabled")

                for name, enabled in sorted(tools.items()):
                    color = "green" if enabled else "red"
                    table.add_row(name, f"[{color}]{enabled}[/{color}]")

                console.print(table)

    except Exception as e:
        _handle_session_error(e)


@app.command(name="test-permission")
def test_permission(
    session_id: str = typer.Argument(..., help="occtl session ID"),
    command: str = typer.Argument(..., help="Bash command to test"),
    agent: Optional[str] = typer.Option(
        None, "--agent", "-a", help="Agent name (uses agent-specific rules)"
    ),
):
    """Test if a bash command would be allowed by permission rules."""
    try:
        cfg = runner.get_config(session_id)
        permission = cfg.get("permission", {})

        # Build flat rule list from permission config (same as OpenCode's fromConfig)
        rules: list[dict] = []
        for perm_name, value in permission.items():
            if perm_name.startswith("__"):
                continue
            if isinstance(value, dict):
                for pattern, action in value.items():
                    rules.append(
                        {"permission": perm_name, "pattern": pattern, "action": action}
                    )
            else:
                rules.append({"permission": perm_name, "pattern": "*", "action": value})

        # If agent specified, merge agent-specific rules
        if agent:
            agents = cfg.get("agent", {})
            agent_cfg = agents.get(agent, {})
            if isinstance(agent_cfg, dict):
                agent_perms = agent_cfg.get("permission", {})
                for perm_name, value in agent_perms.items():
                    if isinstance(value, dict):
                        for pattern, action in value.items():
                            rules.append(
                                {
                                    "permission": perm_name,
                                    "pattern": pattern,
                                    "action": action,
                                }
                            )
                    else:
                        rules.append(
                            {"permission": perm_name, "pattern": "*", "action": value}
                        )

        # findLast: find last matching rule (same as OpenCode's evaluate)
        matched_rule = None
        for rule in reversed(rules):
            if _wildcard_match("bash", rule["permission"]) and _wildcard_match(
                command, rule["pattern"]
            ):
                matched_rule = rule
                break

        if matched_rule is None:
            console.print(f"[yellow]⚠ No matching rule[/yellow] for: {command}")
            console.print("[dim]Default: ask[/dim]")
        elif matched_rule["action"] == "allow":
            console.print(f"[green]✅ allow[/green] — {command}")
            console.print(
                f"[dim]Matched: {matched_rule['permission']}:{matched_rule['pattern']} → {matched_rule['action']}[/dim]"
            )
        elif matched_rule["action"] == "deny":
            console.print(f"[red]🚫 deny[/red] — {command}")
            console.print(
                f"[dim]Matched: {matched_rule['permission']}:{matched_rule['pattern']} → {matched_rule['action']}[/dim]"
            )
        else:
            console.print(f"[yellow]❓ {matched_rule['action']}[/yellow] — {command}")
            console.print(
                f"[dim]Matched: {matched_rule['permission']}:{matched_rule['pattern']} → {matched_rule['action']}[/dim]"
            )

    except Exception as e:
        _handle_session_error(e)


def _wildcard_match(text: str, pattern: str) -> bool:
    """Match text against a wildcard pattern (supports * and ?).

    Implements the same logic as OpenCode's Wildcard.match.
    """
    if pattern == "*":
        return True

    # Convert wildcard pattern to a simple matcher
    return fnmatch.fnmatch(text, pattern)


@app.command()
def logs(
    pattern: Optional[str] = typer.Argument(None, help="Search pattern (grep)"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow latest log file"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    level: Optional[str] = typer.Option(
        None, "--level", "-l", help="Filter by level: error, warn, info, debug"
    ),
    all_files: bool = typer.Option(
        False, "--all", "-a", help="Search all log files (not just latest)"
    ),
):
    """Search OpenCode logs."""
    log_dir = os.path.expanduser("~/.local/share/opencode/log")
    if not os.path.isdir(log_dir):
        console.print(f"[red]Log directory not found:[/red] {log_dir}")
        raise typer.Exit(1)

    log_files = sorted(
        [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")]
    )

    if not log_files:
        console.print("[yellow]No log files found[/yellow]")
        raise typer.Exit(1)

    if follow:
        latest = log_files[-1]
        console.print(f"[dim]Following: {latest}[/dim]")
        try:
            subprocess.run(["tail", "-f", latest])
        except KeyboardInterrupt:
            pass
        return

    target_files = log_files if all_files else [log_files[-1]]

    if pattern or level:
        grep_patterns = []
        if pattern:
            grep_patterns.append(pattern)
        if level:
            grep_patterns.append(f"level={level.upper()}")

        for log_file in target_files:
            try:
                cmd = ["grep", "-i"]
                if len(grep_patterns) == 1:
                    cmd.extend([grep_patterns[0], log_file])
                else:
                    # Multiple patterns: use grep pipeline
                    cmd.extend([grep_patterns[0], log_file])

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                output = result.stdout

                # Apply second pattern filter if needed
                if len(grep_patterns) > 1 and output:
                    result2 = subprocess.run(
                        ["grep", "-i", grep_patterns[1]],
                        input=output,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    output = result2.stdout

                if output:
                    if all_files:
                        console.print(
                            f"\n[dim]── {os.path.basename(log_file)} ──[/dim]"
                        )
                    lines_list = output.strip().split("\n")
                    for line in lines_list[-lines:]:
                        # Colorize log levels
                        if "level=ERROR" in line or "level=error" in line:
                            console.print(f"[red]{line}[/red]")
                        elif "level=WARN" in line or "level=warn" in line:
                            console.print(f"[yellow]{line}[/yellow]")
                        else:
                            console.print(line)
            except subprocess.TimeoutExpired:
                console.print(f"[yellow]Timeout searching {log_file}[/yellow]")
    else:
        # No pattern: show tail of latest log
        latest = target_files[-1]
        console.print(f"[dim]{os.path.basename(latest)}[/dim]\n")
        try:
            result = subprocess.run(
                ["tail", f"-{lines}", latest],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    if "level=ERROR" in line or "level=error" in line:
                        console.print(f"[red]{line}[/red]")
                    elif "level=WARN" in line or "level=warn" in line:
                        console.print(f"[yellow]{line}[/yellow]")
                    else:
                        console.print(line)
        except subprocess.TimeoutExpired:
            console.print("[yellow]Timeout reading log[/yellow]")


@app.command()
def version():
    console.print(get_version("opencode-ctl"))


if __name__ == "__main__":
    app()
