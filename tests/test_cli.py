"""Tests for cli.py — CLI interface."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from opencode_ctl.cli import app
from opencode_ctl.runner import SessionNotFoundError, SessionNotRunningError
from opencode_ctl.client import (
    OpenCodeClientError,
    SendResult,
    Permission,
    SessionInfo,
    Message,
)
from tests.conftest import make_session

cli = CliRunner()


class TestStartCommand:
    def test_prints_session_info(self):
        session = make_session(agent="oracle")
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.start.return_value = session
            result = cli.invoke(app, ["start"])
            assert result.exit_code == 0
            assert "oc-test1234" in result.output
            assert "9100" in result.output
            assert "oracle" in result.output

    def test_passes_workdir(self):
        session = make_session()
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.start.return_value = session
            cli.invoke(app, ["start", "-w", "/tmp/myproject"])
            mock_runner.start.assert_called_once_with(
                workdir="/tmp/myproject",
                timeout=30.0,
                allow_occtl_commands=False,
                agent=None,
            )

    def test_failure_exits_1(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.start.side_effect = RuntimeError("boom")
            result = cli.invoke(app, ["start"])
            assert result.exit_code == 1
            assert "Failed to start" in result.output


class TestStopCommand:
    def test_stop_success(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.stop.return_value = True
            result = cli.invoke(app, ["stop", "oc-abc"])
            assert result.exit_code == 0
            assert "Stopped" in result.output

    def test_stop_not_found(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.stop.return_value = False
            result = cli.invoke(app, ["stop", "oc-abc"])
            assert result.exit_code == 1
            assert "Not found" in result.output


class TestStatusCommand:
    def test_shows_status_info(self):
        session = make_session(status="idle", agent="explore")
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.status.return_value = session
            mock_runner.has_uncommitted_changes.return_value = (False, [])
            result = cli.invoke(app, ["status", "oc-test1234"])
            assert result.exit_code == 0
            assert "idle" in result.output
            assert "explore" in result.output
            assert "No uncommitted changes" in result.output

    def test_shows_dirty_files(self):
        session = make_session()
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.status.return_value = session
            mock_runner.has_uncommitted_changes.return_value = (
                True,
                ["src/main.py", "README.md"],
            )
            result = cli.invoke(app, ["status", "oc-test1234"])
            assert "Uncommitted changes (2)" in result.output
            assert "src/main.py" in result.output

    def test_not_found(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.status.return_value = None
            result = cli.invoke(app, ["status", "oc-nonexistent"])
            assert result.exit_code == 1


class TestListCommand:
    def test_empty_list(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.list_sessions.return_value = []
            result = cli.invoke(app, ["list"])
            assert "No active sessions" in result.output

    def test_shows_sessions_table(self):
        s1 = make_session("oc-aaa", port=9100, status="running", agent="build")
        s1.has_uncommitted_changes = True
        s2 = make_session("oc-bbb", port=9101, status="idle")
        s2.has_uncommitted_changes = False

        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.list_sessions.return_value = [s1, s2]
            result = cli.invoke(app, ["list"])
            assert "oc-aaa" in result.output
            assert "oc-bbb" in result.output
            assert "build" in result.output


class TestSendCommand:
    def test_sync_send_prints_text(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.send.return_value = SendResult(
                text="Hello world", raw={"parts": []}, session_id="ses_abc"
            )
            result = cli.invoke(app, ["send", "oc-abc", "test message", "--wait"])
            assert result.exit_code == 0
            assert "Hello world" in result.output

    def test_async_send_prints_session_id(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.send.return_value = SendResult(
                text="", raw={}, session_id="ses_new123"
            )
            result = cli.invoke(app, ["send", "oc-abc", "test message"])
            assert "ses_new123" in result.output

    def test_raw_mode_prints_json(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.send.return_value = SendResult(
                text="text", raw={"key": "value"}, session_id="ses_abc"
            )
            result = cli.invoke(app, ["send", "oc-abc", "test", "--wait", "--raw"])
            assert '"key"' in result.output
            assert '"value"' in result.output

    def test_error_handling(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.send.side_effect = SessionNotFoundError("oc-abc")
            result = cli.invoke(app, ["send", "oc-abc", "test"])
            assert result.exit_code == 1
            assert "Not found" in result.output


class TestPermissionsCommand:
    def test_single_session_no_perms(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.list_permissions.return_value = []
            result = cli.invoke(app, ["permissions", "oc-abc"])
            assert "No pending permissions" in result.output

    def test_single_session_with_perms(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.list_permissions.return_value = [
                Permission(id="p1", permission="bash", patterns=["rm -rf *"]),
            ]
            result = cli.invoke(app, ["permissions", "oc-abc"])
            assert "p1" in result.output
            assert "bash" in result.output
            assert "rm -rf *" in result.output


class TestApproveCommand:
    def test_approve_once(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.approve_permission.return_value = None
            result = cli.invoke(app, ["approve", "oc-abc", "perm_1"])
            assert "Approved (once)" in result.output

    def test_approve_always(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.approve_permission.return_value = None
            result = cli.invoke(app, ["approve", "oc-abc", "perm_1", "--always"])
            assert "Approved (always)" in result.output


class TestRejectCommand:
    def test_reject(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.reject_permission.return_value = None
            result = cli.invoke(app, ["reject", "oc-abc", "perm_1"])
            assert "Rejected" in result.output


class TestSessionsCommand:
    def test_lists_oc_sessions(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.list_oc_sessions.return_value = [
                SessionInfo(
                    id="ses_abc",
                    title="My session",
                    created=1706745600000,
                    updated=1706745601000,
                ),
            ]
            result = cli.invoke(app, ["sessions", "oc-abc"])
            assert "ses_abc" in result.output
            assert "My session" in result.output


class TestTailCommand:
    def test_shows_messages(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.get_messages.return_value = [
                Message(id="m1", role="user", text="Hello"),
                Message(id="m2", role="assistant", text="Hi there"),
            ]
            result = cli.invoke(app, ["tail", "oc-abc", "-s", "ses_abc"])
            assert "Hello" in result.output
            assert "Hi there" in result.output

    def test_raw_mode_prints_all_messages(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.get_messages.return_value = [
                Message(id="m1", role="user", text="User message"),
                Message(id="m2", role="assistant", text="Assistant message"),
            ]
            result = cli.invoke(app, ["tail", "oc-abc", "-s", "ses_abc", "--raw"])
            assert "User message" in result.output
            assert "Assistant message" in result.output

    def test_role_filter(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.get_messages.return_value = [
                Message(id="m1", role="user", text="User message"),
                Message(id="m2", role="assistant", text="Assistant message"),
            ]
            result = cli.invoke(
                app, ["tail", "oc-abc", "-s", "ses_abc", "--role", "user", "--raw"]
            )
            assert "User message" in result.output
            assert "Assistant message" not in result.output

    def test_last_flag_shows_last_assistant(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.get_messages.return_value = [
                Message(id="m1", role="user", text="Question"),
                Message(id="m2", role="assistant", text="Answer"),
            ]
            result = cli.invoke(
                app, ["tail", "oc-abc", "-s", "ses_abc", "--last", "--raw"]
            )
            assert "Answer" in result.output
            assert "Question" not in result.output

    def test_chain_mode(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.get_chain_messages.return_value = [
                Message(id="m1", role="assistant", text="From parent"),
                Message(id="m2", role="assistant", text="From current"),
            ]
            result = cli.invoke(app, ["tail", "oc-abc", "-s", "ses_abc", "--chain"])
            assert "From parent" in result.output
            assert "From current" in result.output

    def test_no_messages(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.get_messages.return_value = []
            result = cli.invoke(app, ["tail", "oc-abc", "-s", "ses_abc"])
            assert "No messages" in result.output


class TestForkCommand:
    def test_fork_success(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.fork_session.return_value = SessionInfo(
                id="ses_forked",
                title="",
                created=1000,
                updated=1000,
                parent_id="ses_abc",
            )
            result = cli.invoke(app, ["fork", "oc-abc", "-s", "ses_abc"])
            assert "Forked" in result.output
            assert "ses_forked" in result.output


class TestErrorHandling:
    def test_session_not_found_error(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.send.side_effect = SessionNotFoundError("oc-abc")
            result = cli.invoke(app, ["send", "oc-abc", "test"])
            assert result.exit_code == 1
            assert "Not found" in result.output

    def test_session_not_running_error(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.send.side_effect = SessionNotRunningError("dead")
            result = cli.invoke(app, ["send", "oc-abc", "test"])
            assert result.exit_code == 1
            assert "not running" in result.output

    def test_opencode_client_error(self):
        with patch("opencode_ctl.cli.runner") as mock_runner:
            mock_runner.send.side_effect = OpenCodeClientError(500, "Internal error")
            result = cli.invoke(app, ["send", "oc-abc", "test"])
            assert result.exit_code == 1
            assert "500" in result.output


class TestVersionCommand:
    def test_prints_version(self):
        with patch("opencode_ctl.cli.get_version", return_value="0.4.0"):
            result = cli.invoke(app, ["version"])
            assert "0.4.0" in result.output
