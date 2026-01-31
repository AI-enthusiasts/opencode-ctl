"""Tests for client.py — HTTP client for OpenCode API."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from opencode_ctl.client import (
    OpenCodeClient,
    OpenCodeClientError,
    SendResult,
    Permission,
    Message,
    ToolCall,
    SessionInfo,
)
from tests.conftest import mock_httpx_client, mock_response, mock_stream_response


@pytest.fixture
def client():
    return OpenCodeClient("http://localhost:9100")


@pytest.fixture
def http(monkeypatch):
    """Patch httpx.Client and return the mock instance.

    Usage:
        def test_something(client, http):
            http.get.return_value = mock_response(200, {"key": "value"})
            result = client.some_method()
    """
    mock = mock_httpx_client()
    with patch("httpx.Client", return_value=mock):
        yield mock


class TestCreateSession:
    def test_returns_session_id(self, client, http):
        http.post.return_value = mock_response(200, {"id": "ses_abc123"})
        result = client.create_session()
        assert result == "ses_abc123"
        http.post.assert_called_once_with("http://localhost:9100/session", json={})

    def test_raises_on_error(self, client, http):
        http.post.return_value = mock_response(500, text="Internal Server Error")
        with pytest.raises(OpenCodeClientError) as exc_info:
            client.create_session()
        assert exc_info.value.status_code == 500


class TestSendMessage:
    def test_parses_text_parts(self, client, http):
        response_body = json.dumps(
            {
                "info": {"id": "msg_1", "role": "assistant"},
                "parts": [
                    {"type": "text", "text": "Hello "},
                    {"type": "text", "text": "world"},
                ],
            }
        )
        http.stream.return_value = mock_stream_response(200, response_body)
        result = client.send_message("ses_abc", "test")
        assert result.text == "Hello \nworld"
        assert result.session_id == "ses_abc"

    def test_empty_response(self, client, http):
        http.stream.return_value = mock_stream_response(200, "")
        result = client.send_message("ses_abc", "test")
        assert result.text == ""

    def test_invalid_json_returns_raw_text(self, client, http):
        http.stream.return_value = mock_stream_response(200, "not json at all")
        result = client.send_message("ses_abc", "test")
        assert result.text == "not json at all"
        assert result.raw == {}

    def test_includes_agent_in_body(self, client, http):
        http.stream.return_value = mock_stream_response(200, json.dumps({"parts": []}))
        client.send_message("ses_abc", "test", agent="docs-retriever")
        call_args = http.stream.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["agent"] == "docs-retriever"
        assert body["parts"] == [{"type": "text", "text": "test"}]


class TestSendMessageAsync:
    def test_returns_session_id_on_204(self, client, http):
        http.post.return_value = mock_response(204)
        result = client.send_message_async("ses_abc", "test")
        assert result == "ses_abc"

    def test_returns_session_id_on_200(self, client, http):
        http.post.return_value = mock_response(200)
        result = client.send_message_async("ses_abc", "test")
        assert result == "ses_abc"

    def test_raises_on_error(self, client, http):
        http.post.return_value = mock_response(500, text="error")
        with pytest.raises(OpenCodeClientError):
            client.send_message_async("ses_abc", "test")


class TestGetSessionStatus:
    def test_parses_status_map(self, client, http):
        http.get.return_value = mock_response(
            200,
            {
                "ses_abc": {"type": "idle"},
                "ses_def": {"type": "busy"},
            },
        )
        result = client.get_session_status()
        assert result["ses_abc"]["type"] == "idle"
        assert result["ses_def"]["type"] == "busy"


class TestIsSessionBusy:
    def test_busy_when_status_is_busy(self, client):
        with patch.object(
            client,
            "get_session_status",
            return_value={"ses_abc": {"type": "busy"}},
        ):
            assert client.is_session_busy("ses_abc") is True

    def test_busy_when_status_is_retry(self, client):
        with patch.object(
            client,
            "get_session_status",
            return_value={
                "ses_abc": {"type": "retry", "attempt": 2, "message": "rate limited"},
            },
        ):
            assert client.is_session_busy("ses_abc") is True

    def test_not_busy_when_idle(self, client):
        with patch.object(
            client,
            "get_session_status",
            return_value={"ses_abc": {"type": "idle"}},
        ):
            assert client.is_session_busy("ses_abc") is False

    def test_not_busy_when_session_not_in_status(self, client):
        with patch.object(client, "get_session_status", return_value={}):
            assert client.is_session_busy("ses_abc") is False


class TestListPermissions:
    def test_parses_permission_fields(self, client, http):
        http.get.return_value = mock_response(
            200,
            [
                {
                    "id": "perm_1",
                    "sessionID": "ses_abc",
                    "permission": "bash",
                    "patterns": ["rm -rf *"],
                    "metadata": {},
                    "always": [],
                    "tool": {"callID": "call_123", "messageID": "msg_456"},
                }
            ],
        )
        perms = client.list_permissions()
        assert len(perms) == 1
        assert perms[0].id == "perm_1"
        assert perms[0].permission == "bash"
        assert perms[0].patterns == ["rm -rf *"]
        assert perms[0].tool_call_id == "call_123"
        assert perms[0].tool_message_id == "msg_456"

    def test_handles_missing_tool_field(self, client, http):
        http.get.return_value = mock_response(
            200,
            [{"id": "perm_1", "permission": "bash", "patterns": []}],
        )
        perms = client.list_permissions()
        assert perms[0].tool_call_id == ""
        assert perms[0].tool_message_id == ""


class TestGetMessages:
    def test_parses_messages_with_text_and_tools(self, client, http):
        http.get.return_value = mock_response(
            200,
            [
                {
                    "info": {
                        "id": "msg_1",
                        "role": "user",
                        "time": {"created": 1706745600000},
                    },
                    "parts": [{"type": "text", "text": "Hello"}],
                },
                {
                    "info": {
                        "id": "msg_2",
                        "role": "assistant",
                        "time": {"created": 1706745601000},
                    },
                    "parts": [
                        {"type": "text", "text": "Hi there"},
                        {
                            "type": "tool",
                            "tool": "bash",
                            "callID": "call_1",
                            "state": {
                                "status": "result",
                                "input": {"command": "ls"},
                                "output": "file.txt",
                            },
                        },
                    ],
                },
            ],
        )
        messages = client.get_messages("ses_abc", limit=10)
        assert len(messages) == 2
        assert messages[0].id == "msg_1"
        assert messages[0].role == "user"
        assert messages[0].text == "Hello"
        assert messages[0].timestamp == 1706745600000
        assert messages[1].id == "msg_2"
        assert messages[1].role == "assistant"
        assert messages[1].text == "Hi there"
        assert len(messages[1].tool_calls) == 1
        assert messages[1].tool_calls[0].name == "bash"
        assert messages[1].tool_calls[0].state == "result"
        assert messages[1].tool_calls[0].args == {"command": "ls"}

    def test_limit_truncates_from_end(self, client, http):
        all_msgs = [
            {
                "info": {"id": f"msg_{i}", "role": "user", "time": {"created": i}},
                "parts": [],
            }
            for i in range(20)
        ]
        http.get.return_value = mock_response(200, all_msgs)
        messages = client.get_messages("ses_abc", limit=5)
        assert len(messages) == 5
        assert messages[0].id == "msg_15"
        assert messages[-1].id == "msg_19"


class TestListOcSessions:
    def test_parses_session_info(self, client, http):
        http.get.return_value = mock_response(
            200,
            [
                {
                    "id": "ses_abc",
                    "title": "Test session",
                    "time": {"created": 1706745600000, "updated": 1706745601000},
                    "parentID": None,
                },
                {
                    "id": "ses_def",
                    "title": "Child",
                    "time": {"created": 1706745602000, "updated": 1706745603000},
                    "parentID": "ses_abc",
                },
            ],
        )
        sessions = client.list_oc_sessions()
        assert len(sessions) == 2
        assert sessions[0].id == "ses_abc"
        assert sessions[0].parent_id is None
        assert sessions[1].parent_id == "ses_abc"


class TestForkSession:
    def test_sends_message_id(self, client, http):
        http.post.return_value = mock_response(
            200,
            {
                "id": "ses_forked",
                "title": "Forked",
                "time": {"created": 1000, "updated": 1000},
            },
        )
        result = client.fork_session("ses_abc", message_id="msg_5")
        assert result.id == "ses_forked"
        call_args = http.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body == {"messageID": "msg_5"}

    def test_empty_body_without_message_id(self, client, http):
        http.post.return_value = mock_response(
            200,
            {
                "id": "ses_forked",
                "title": "",
                "time": {"created": 1000, "updated": 1000},
            },
        )
        client.fork_session("ses_abc")
        call_args = http.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body == {}


class TestReplyPermission:
    def test_sends_correct_body(self, client, http):
        http.post.return_value = mock_response(200)
        client.reply_permission("perm_1", "always")
        call_args = http.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body == {"reply": "always"}

    def test_includes_message_when_provided(self, client, http):
        http.post.return_value = mock_response(200)
        client.reply_permission("perm_1", "reject", message="not allowed")
        call_args = http.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body == {"reply": "reject", "message": "not allowed"}


class TestBaseUrl:
    def test_strips_trailing_slash(self):
        client = OpenCodeClient("http://localhost:9100/")
        assert client.base_url == "http://localhost:9100"

    def test_preserves_clean_url(self):
        client = OpenCodeClient("http://localhost:9100")
        assert client.base_url == "http://localhost:9100"


class TestOpenCodeClientError:
    def test_has_status_code_and_message(self):
        err = OpenCodeClientError(404, "Not found")
        assert err.status_code == 404
        assert err.message == "Not found"
        assert "404" in str(err)
        assert "Not found" in str(err)
