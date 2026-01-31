"""Tests for store.py — persistence layer."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opencode_ctl.store import Session, Store, TransactionalStore


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OCCTL_DATA_DIR", str(tmp_path))
    return tmp_path


def _make_session(
    id: str = "oc-test1234", port: int = 9100, pid: int = 12345
) -> Session:
    return Session(
        id=id,
        port=port,
        pid=pid,
        created_at="2025-01-01T00:00:00",
        last_activity="2025-01-01T00:00:00",
        config_path="/tmp/test",
        status="running",
    )


class TestSession:
    def test_to_dict_excludes_has_uncommitted_changes(self):
        s = _make_session()
        s.has_uncommitted_changes = True
        d = s.to_dict()
        assert "has_uncommitted_changes" not in d

    def test_to_dict_excludes_none_agent(self):
        s = _make_session()
        s.agent = None
        d = s.to_dict()
        assert "agent" not in d

    def test_to_dict_includes_non_none_agent(self):
        s = _make_session()
        s.agent = "oracle"
        d = s.to_dict()
        assert d["agent"] == "oracle"

    def test_from_dict_handles_missing_agent(self):
        data = {
            "id": "oc-abc",
            "port": 9100,
            "pid": 1,
            "created_at": "2025-01-01T00:00:00",
            "last_activity": "2025-01-01T00:00:00",
            "status": "running",
        }
        s = Session.from_dict(data)
        assert s.agent is None

    def test_from_dict_strips_has_uncommitted_changes(self):
        data = {
            "id": "oc-abc",
            "port": 9100,
            "pid": 1,
            "created_at": "2025-01-01T00:00:00",
            "last_activity": "2025-01-01T00:00:00",
            "status": "running",
            "has_uncommitted_changes": True,
        }
        s = Session.from_dict(data)
        assert s.has_uncommitted_changes is False

    def test_roundtrip_preserves_data(self):
        s = _make_session()
        s.agent = "explore"
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert s2.id == s.id
        assert s2.port == s.port
        assert s2.pid == s.pid
        assert s2.agent == s.agent


class TestStore:
    def test_save_and_load(self, tmp_store):
        store = Store()
        store.add_session(_make_session("oc-aaa", port=9100))
        store.add_session(_make_session("oc-bbb", port=9101))
        store.save()

        loaded = Store.load()
        assert len(loaded.sessions) == 2
        assert "oc-aaa" in loaded.sessions
        assert "oc-bbb" in loaded.sessions

    def test_load_nonexistent_returns_empty(self, tmp_store):
        store = Store.load()
        assert len(store.sessions) == 0
        assert store.next_port == 9100

    def test_allocate_port_reuses_freed_ports(self, tmp_store):
        store = Store()
        store.add_session(_make_session("oc-a", port=9100))
        store.add_session(_make_session("oc-b", port=9101))
        store.add_session(_make_session("oc-c", port=9102))

        store.remove_session("oc-b")

        port = store.allocate_port()
        assert port == 9101

    def test_allocate_port_skips_used_ports(self, tmp_store):
        store = Store()
        store.add_session(_make_session("oc-a", port=9100))
        store.add_session(_make_session("oc-b", port=9101))

        port = store.allocate_port()
        assert port == 9102

    def test_allocate_port_starts_from_9100(self, tmp_store):
        store = Store()
        port = store.allocate_port()
        assert port == 9100

    def test_remove_session_idempotent(self, tmp_store):
        store = Store()
        store.add_session(_make_session("oc-a"))
        store.remove_session("oc-a")
        store.remove_session("oc-a")
        assert len(store.sessions) == 0

    def test_get_session_returns_none_for_missing(self, tmp_store):
        store = Store()
        assert store.get_session("oc-nonexistent") is None

    def test_update_activity_changes_timestamp(self, tmp_store):
        store = Store()
        s = _make_session()
        store.add_session(s)
        old_activity = s.last_activity
        store.update_activity(s.id)
        assert s.last_activity != old_activity

    def test_update_activity_noop_for_missing(self, tmp_store):
        store = Store()
        store.update_activity("oc-nonexistent")


class TestTransactionalStore:
    def test_saves_on_clean_exit(self, tmp_store):
        with TransactionalStore() as store:
            store.add_session(_make_session("oc-tx"))

        with TransactionalStore() as store:
            assert "oc-tx" in store.sessions

    def test_does_not_save_on_exception(self, tmp_store):
        try:
            with TransactionalStore() as store:
                store.add_session(_make_session("oc-fail"))
                raise ValueError("boom")
        except ValueError:
            pass

        with TransactionalStore() as store:
            assert "oc-fail" not in store.sessions

    def test_concurrent_access_serialized(self, tmp_store):
        with TransactionalStore() as store:
            store.add_session(_make_session("oc-first"))

        with TransactionalStore() as store:
            assert "oc-first" in store.sessions
            store.add_session(_make_session("oc-second", port=9101))

        with TransactionalStore() as store:
            assert len(store.sessions) == 2

    def test_store_json_format(self, tmp_store):
        with TransactionalStore() as store:
            store.add_session(_make_session("oc-fmt"))

        raw = json.loads(Store.path().read_text())
        assert "sessions" in raw
        assert "next_port" in raw
        assert "oc-fmt" in raw["sessions"]
        assert "has_uncommitted_changes" not in raw["sessions"]["oc-fmt"]
