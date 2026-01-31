"""Shared test fixtures for opencode-ctl."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from opencode_ctl.store import Session


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """Isolated store directory — each test gets its own store.json."""
    monkeypatch.setenv("OCCTL_DATA_DIR", str(tmp_path))
    return tmp_path


def make_session(
    id: str = "oc-test1234",
    port: int = 9100,
    pid: int = 99999,
    status: str = "running",
    config_path: str | None = "/tmp/test",
    agent: str | None = None,
    created_at: str | None = None,
    last_activity: str | None = None,
) -> Session:
    """Factory for Session objects with sensible defaults."""
    now = datetime.now().isoformat()
    return Session(
        id=id,
        port=port,
        pid=pid,
        created_at=created_at or now,
        last_activity=last_activity or now,
        config_path=config_path,
        status=status,
        agent=agent,
    )


def mock_httpx_client() -> MagicMock:
    """Create a MagicMock that behaves like httpx.Client context manager."""
    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    return mock_http


def mock_response(
    status_code: int = 200,
    json_data: Any = None,
    text: str = "",
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or (json.dumps(json_data) if json_data else text)
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def mock_stream_response(
    status_code: int = 200,
    body: str = "",
) -> MagicMock:
    """Create a mock streaming httpx.Response (for client.stream())."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.iter_text.return_value = [body] if body else []
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp
