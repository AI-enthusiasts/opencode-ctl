"""Tests for runner.py — session lifecycle management."""

from __future__ import annotations

import os
import signal
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from opencode_ctl.runner import (
    OpenCodeRunner,
    SessionNotFoundError,
    SessionNotRunningError,
)
from opencode_ctl.store import Session, TransactionalStore
from opencode_ctl.client import SendResult, Message, Permission, SessionInfo


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OCCTL_DATA_DIR", str(tmp_path))
    return tmp_path


def _make_session(
    id: str = "oc-test1234",
    port: int = 9100,
    pid: int = 99999,
    status: str = "running",
    config_path: str | None = "/tmp/test",
    agent: str | None = None,
) -> Session:
    return Session(
        id=id,
        port=port,
        pid=pid,
        created_at=datetime.now().isoformat(),
        last_activity=datetime.now().isoformat(),
        config_path=config_path,
        status=status,
        agent=agent,
    )


def _store_session(session: Session, tmp_store) -> None:
    with TransactionalStore() as store:
        store.add_session(session)


class TestStop:
    def test_stop_existing_session(self, tmp_store):
        session = _make_session(pid=os.getpid())
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        with patch("os.kill") as mock_kill:
            result = runner.stop(session.id)
            assert result is True
            mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)

        with TransactionalStore() as store:
            assert store.get_session(session.id) is None

    def test_stop_nonexistent_returns_false(self, tmp_store):
        runner = OpenCodeRunner()
        result = runner.stop("oc-nonexistent")
        assert result is False

    def test_force_stop_sends_sigkill(self, tmp_store):
        session = _make_session(pid=os.getpid())
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        with patch("os.kill") as mock_kill:
            runner.stop(session.id, force=True)
            mock_kill.assert_called_once_with(os.getpid(), signal.SIGKILL)

    def test_stop_handles_dead_process(self, tmp_store):
        session = _make_session(pid=99999999)
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        with patch("os.kill", side_effect=ProcessLookupError):
            result = runner.stop(session.id)
            assert result is True

        with TransactionalStore() as store:
            assert store.get_session(session.id) is None


class TestStatus:
    def test_returns_none_for_missing(self, tmp_store):
        runner = OpenCodeRunner()
        assert runner.status("oc-nonexistent") is None

    def test_dead_session_removed_from_store(self, tmp_store):
        session = _make_session()
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        with patch.object(runner, "_is_process_alive", return_value=False):
            result = runner.status(session.id)
            assert result is not None
            assert result.status == "dead"

        with TransactionalStore() as store:
            assert store.get_session(session.id) is None

    def test_running_session_with_permissions(self, tmp_store):
        session = _make_session()
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        mock_client = MagicMock()
        mock_client.list_permissions.return_value = [
            Permission(id="p1", permission="bash", patterns=["ls"])
        ]

        with (
            patch.object(runner, "_is_process_alive", return_value=True),
            patch("opencode_ctl.runner.OpenCodeClient", return_value=mock_client),
        ):
            result = runner.status(session.id)
            assert result is not None
            assert result.status == "waiting_permission"


class TestListSessions:
    def test_empty_store(self, tmp_store):
        runner = OpenCodeRunner()
        assert runner.list_sessions() == []

    def test_filters_dead_sessions(self, tmp_store):
        alive = _make_session("oc-alive", port=9100, pid=1)
        dead = _make_session("oc-dead", port=9101, pid=2)
        _store_session(alive, tmp_store)
        _store_session(dead, tmp_store)

        runner = OpenCodeRunner()

        def fake_alive(pid):
            return pid == 1

        mock_client = MagicMock()
        mock_client.list_permissions.return_value = []
        mock_client.list_oc_sessions.return_value = []

        with (
            patch.object(runner, "_is_process_alive", side_effect=fake_alive),
            patch("opencode_ctl.runner.OpenCodeClient", return_value=mock_client),
            patch.object(runner, "_check_git_changes", return_value=(False, [])),
        ):
            sessions = runner.list_sessions()
            assert len(sessions) == 1
            assert sessions[0].id == "oc-alive"

        with TransactionalStore() as store:
            assert store.get_session("oc-dead") is None


class TestCleanupIdle:
    def test_kills_idle_sessions(self, tmp_store):
        old_time = (datetime.now() - timedelta(seconds=120)).isoformat()
        session = _make_session()
        session.last_activity = old_time
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        with patch("os.kill") as mock_kill:
            stopped = runner.cleanup_idle(max_idle_seconds=60)
            assert session.id in stopped
            mock_kill.assert_called_once()

    def test_keeps_active_sessions(self, tmp_store):
        session = _make_session()
        session.last_activity = datetime.now().isoformat()
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        stopped = runner.cleanup_idle(max_idle_seconds=60)
        assert stopped == []


class TestTouch:
    def test_updates_activity(self, tmp_store):
        session = _make_session()
        session.last_activity = "2020-01-01T00:00:00"
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        assert runner.touch(session.id) is True

        with TransactionalStore() as store:
            s = store.get_session(session.id)
            assert s.last_activity != "2020-01-01T00:00:00"

    def test_returns_false_for_missing(self, tmp_store):
        runner = OpenCodeRunner()
        assert runner.touch("oc-nonexistent") is False


class TestSend:
    def test_creates_new_oc_session_and_sends(self, tmp_store):
        session = _make_session()
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        mock_client = MagicMock()
        mock_client.create_session.return_value = "ses_new"
        mock_client.send_message.return_value = SendResult(
            text="response", raw={}, session_id="ses_new"
        )
        mock_client.list_permissions.return_value = []
        mock_client.list_oc_sessions.return_value = []

        with (
            patch.object(runner, "_is_process_alive", return_value=True),
            patch("opencode_ctl.runner.OpenCodeClient", return_value=mock_client),
            patch.object(runner, "_check_git_changes", return_value=(False, [])),
        ):
            result = runner.send(session.id, "hello", wait=True)
            assert result.text == "response"
            mock_client.create_session.assert_called_once()
            mock_client.send_message.assert_called_once_with("ses_new", "hello", None)

    def test_async_send_returns_session_id(self, tmp_store):
        session = _make_session()
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        mock_client = MagicMock()
        mock_client.create_session.return_value = "ses_new"
        mock_client.send_message_async.return_value = "ses_new"
        mock_client.list_permissions.return_value = []
        mock_client.list_oc_sessions.return_value = []

        with (
            patch.object(runner, "_is_process_alive", return_value=True),
            patch("opencode_ctl.runner.OpenCodeClient", return_value=mock_client),
            patch.object(runner, "_check_git_changes", return_value=(False, [])),
        ):
            result = runner.send(session.id, "hello", wait=False)
            assert result.session_id == "ses_new"
            assert result.text == ""

    def test_raises_for_nonexistent_session(self, tmp_store):
        runner = OpenCodeRunner()
        with pytest.raises(SessionNotFoundError):
            runner.send("oc-nonexistent", "hello")

    def test_raises_for_dead_session(self, tmp_store):
        session = _make_session()
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        with patch.object(runner, "_is_process_alive", return_value=False):
            with pytest.raises(SessionNotRunningError):
                runner.send(session.id, "hello")


class TestGetSessionChain:
    def test_builds_parent_chain_and_children(self, tmp_store):
        session = _make_session()
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        mock_client = MagicMock()
        mock_client.list_permissions.return_value = []
        mock_client.list_oc_sessions.return_value = [
            SessionInfo(
                id="ses_root", title="Root", created=1000, updated=1000, parent_id=None
            ),
            SessionInfo(
                id="ses_mid",
                title="Mid",
                created=2000,
                updated=2000,
                parent_id="ses_root",
            ),
            SessionInfo(
                id="ses_leaf",
                title="Leaf",
                created=3000,
                updated=3000,
                parent_id="ses_mid",
            ),
            SessionInfo(
                id="ses_child",
                title="Child",
                created=4000,
                updated=4000,
                parent_id="ses_mid",
            ),
        ]

        with (
            patch.object(runner, "_is_process_alive", return_value=True),
            patch("opencode_ctl.runner.OpenCodeClient", return_value=mock_client),
            patch.object(runner, "_check_git_changes", return_value=(False, [])),
        ):
            chain = runner.get_session_chain(session.id, "ses_mid")

        assert chain[0].id == "ses_root"
        assert chain[1].id == "ses_mid"
        # children sorted by created: ses_leaf(3000) before ses_child(4000)
        assert chain[2].id == "ses_leaf"
        assert chain[3].id == "ses_child"


class TestCheckGitChanges:
    def test_no_config_path(self, tmp_store):
        runner = OpenCodeRunner()
        session = _make_session(config_path=None)
        assert runner._check_git_changes(session) == (False, [])

    def test_nonexistent_directory(self, tmp_store):
        runner = OpenCodeRunner()
        session = _make_session(config_path="/nonexistent/path")
        assert runner._check_git_changes(session) == (False, [])

    def test_not_git_repo(self, tmp_path):
        runner = OpenCodeRunner()
        session = _make_session(config_path=str(tmp_path))
        assert runner._check_git_changes(session) == (False, [])

    def test_clean_repo(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        runner = OpenCodeRunner()
        session = _make_session(config_path=str(tmp_path))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            has_changes, files = runner._check_git_changes(session)
            assert has_changes is False
            assert files == []

    def test_dirty_repo(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        runner = OpenCodeRunner()
        session = _make_session(config_path=str(tmp_path))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=" M src/main.py\n?? new_file.txt\n",
            )
            has_changes, files = runner._check_git_changes(session)
            assert has_changes is True
            assert "src/main.py" in files
            assert "new_file.txt" in files


class TestHasUncommittedChanges:
    def test_releases_lock_before_git(self, tmp_store, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        session = _make_session(config_path=str(tmp_path))
        _store_session(session, tmp_store)

        runner = OpenCodeRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=" M file.py\n")
            has_changes, files = runner.has_uncommitted_changes(session.id)
            assert has_changes is True


class TestDetermineStatus:
    def test_dead_process(self, tmp_store):
        runner = OpenCodeRunner()
        session = _make_session()
        with patch.object(runner, "_is_process_alive", return_value=False):
            assert runner._determine_status(session) == "dead"

    def test_error_on_connection_failure(self, tmp_store):
        runner = OpenCodeRunner()
        session = _make_session()
        with (
            patch.object(runner, "_is_process_alive", return_value=True),
            patch(
                "opencode_ctl.runner.OpenCodeClient",
                side_effect=Exception("connection refused"),
            ),
        ):
            assert runner._determine_status(session) == "error"

    def test_idle_when_no_sessions(self, tmp_store):
        runner = OpenCodeRunner()
        session = _make_session()
        mock_client = MagicMock()
        mock_client.list_permissions.return_value = []
        mock_client.list_oc_sessions.return_value = []

        with (
            patch.object(runner, "_is_process_alive", return_value=True),
            patch("opencode_ctl.runner.OpenCodeClient", return_value=mock_client),
        ):
            assert runner._determine_status(session) == "idle"
