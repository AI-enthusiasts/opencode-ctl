"""Tests for client.py — HTTP client for OpenCode API."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock
from contextlib import contextmanager

import httpx
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


def _mock_response(status_code: int = 200, json_data=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(json_data) if json_data else text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def _mock_stream_response(status_code: int = 200, body: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.iter_text.return_value = [body] if body else []
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestCreateSession:
    def test_returns_session_id(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(200, {"id": "ses_abc123"})

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            MockClient.return_value = mock_http

            result = client.create_session()
            assert result == "ses_abc123"
            mock_http.post.assert_called_once_with(
                "http://localhost:9100/session", json={}
            )

    def test_raises_on_error(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(500, text="Internal Server Error")

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            MockClient.return_value = mock_http

            with pytest.raises(OpenCodeClientError) as exc_info:
                client.create_session()
            assert exc_info.value.status_code == 500


class TestSendMessage:
    def test_parses_text_parts(self):
        client = OpenCodeClient("http://localhost:9100")
        response_body = json.dumps(
            {
                "info": {"id": "msg_1", "role": "assistant"},
                "parts": [
                    {"type": "text", "text": "Hello "},
                    {"type": "text", "text": "world"},
                ],
            }
        )

        stream_resp = _mock_stream_response(200, response_body)

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.stream.return_value = stream_resp
            MockClient.return_value = mock_http

            result = client.send_message("ses_abc", "test")
            assert result.text == "Hello \nworld"
            assert result.session_id == "ses_abc"

    def test_empty_response(self):
        client = OpenCodeClient("http://localhost:9100")
        stream_resp = _mock_stream_response(200, "")

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.stream.return_value = stream_resp
            MockClient.return_value = mock_http

            result = client.send_message("ses_abc", "test")
            assert result.text == ""

    def test_invalid_json_returns_raw_text(self):
        client = OpenCodeClient("http://localhost:9100")
        stream_resp = _mock_stream_response(200, "not json at all")

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.stream.return_value = stream_resp
            MockClient.return_value = mock_http

            result = client.send_message("ses_abc", "test")
            assert result.text == "not json at all"
            assert result.raw == {}

    def test_includes_agent_in_body(self):
        client = OpenCodeClient("http://localhost:9100")
        stream_resp = _mock_stream_response(200, json.dumps({"parts": []}))

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.stream.return_value = stream_resp
            MockClient.return_value = mock_http

            client.send_message("ses_abc", "test", agent="docs-retriever")
            call_args = mock_http.stream.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert body["agent"] == "docs-retriever"
            assert body["parts"] == [{"type": "text", "text": "test"}]


class TestSendMessageAsync:
    def test_returns_session_id_on_204(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(204)

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            MockClient.return_value = mock_http

            result = client.send_message_async("ses_abc", "test")
            assert result == "ses_abc"

    def test_returns_session_id_on_200(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(200)

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            MockClient.return_value = mock_http

            result = client.send_message_async("ses_abc", "test")
            assert result == "ses_abc"

    def test_raises_on_error(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(500, text="error")

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            MockClient.return_value = mock_http

            with pytest.raises(OpenCodeClientError):
                client.send_message_async("ses_abc", "test")


class TestGetSessionStatus:
    def test_parses_status_map(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(
            200,
            {
                "ses_abc": {"type": "idle"},
                "ses_def": {"type": "busy"},
            },
        )

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = mock_resp
            MockClient.return_value = mock_http

            result = client.get_session_status()
            assert result["ses_abc"]["type"] == "idle"
            assert result["ses_def"]["type"] == "busy"


class TestIsSessionBusy:
    def test_busy_when_status_is_busy(self):
        client = OpenCodeClient("http://localhost:9100")
        with patch.object(
            client,
            "get_session_status",
            return_value={
                "ses_abc": {"type": "busy"},
            },
        ):
            assert client.is_session_busy("ses_abc") is True

    def test_busy_when_status_is_retry(self):
        client = OpenCodeClient("http://localhost:9100")
        with patch.object(
            client,
            "get_session_status",
            return_value={
                "ses_abc": {"type": "retry", "attempt": 2, "message": "rate limited"},
            },
        ):
            assert client.is_session_busy("ses_abc") is True

    def test_not_busy_when_idle(self):
        client = OpenCodeClient("http://localhost:9100")
        with patch.object(
            client,
            "get_session_status",
            return_value={
                "ses_abc": {"type": "idle"},
            },
        ):
            assert client.is_session_busy("ses_abc") is False

    def test_not_busy_when_session_not_in_status(self):
        client = OpenCodeClient("http://localhost:9100")
        with patch.object(client, "get_session_status", return_value={}):
            assert client.is_session_busy("ses_abc") is False


class TestListPermissions:
    def test_parses_permission_fields(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(
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

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = mock_resp
            MockClient.return_value = mock_http

            perms = client.list_permissions()
            assert len(perms) == 1
            assert perms[0].id == "perm_1"
            assert perms[0].permission == "bash"
            assert perms[0].patterns == ["rm -rf *"]
            assert perms[0].tool_call_id == "call_123"
            assert perms[0].tool_message_id == "msg_456"

    def test_handles_missing_tool_field(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(
            200,
            [
                {
                    "id": "perm_1",
                    "permission": "bash",
                    "patterns": [],
                }
            ],
        )

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = mock_resp
            MockClient.return_value = mock_http

            perms = client.list_permissions()
            assert perms[0].tool_call_id == ""
            assert perms[0].tool_message_id == ""


class TestGetMessages:
    def test_parses_messages_with_text_and_tools(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(
            200,
            [
                {
                    "info": {
                        "id": "msg_1",
                        "role": "user",
                        "time": {"created": 1706745600000},
                    },
                    "parts": [
                        {"type": "text", "text": "Hello"},
                    ],
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

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = mock_resp
            MockClient.return_value = mock_http

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

    def test_limit_truncates_from_end(self):
        client = OpenCodeClient("http://localhost:9100")
        all_msgs = [
            {
                "info": {"id": f"msg_{i}", "role": "user", "time": {"created": i}},
                "parts": [],
            }
            for i in range(20)
        ]
        mock_resp = _mock_response(200, all_msgs)

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = mock_resp
            MockClient.return_value = mock_http

            messages = client.get_messages("ses_abc", limit=5)
            assert len(messages) == 5
            assert messages[0].id == "msg_15"
            assert messages[-1].id == "msg_19"


class TestListOcSessions:
    def test_parses_session_info(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(
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

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = mock_resp
            MockClient.return_value = mock_http

            sessions = client.list_oc_sessions()
            assert len(sessions) == 2
            assert sessions[0].id == "ses_abc"
            assert sessions[0].parent_id is None
            assert sessions[1].parent_id == "ses_abc"


class TestForkSession:
    def test_sends_message_id(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(
            200,
            {
                "id": "ses_forked",
                "title": "Forked",
                "time": {"created": 1000, "updated": 1000},
            },
        )

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            MockClient.return_value = mock_http

            result = client.fork_session("ses_abc", message_id="msg_5")
            assert result.id == "ses_forked"

            call_args = mock_http.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert body == {"messageID": "msg_5"}

    def test_empty_body_without_message_id(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(
            200,
            {
                "id": "ses_forked",
                "title": "",
                "time": {"created": 1000, "updated": 1000},
            },
        )

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            MockClient.return_value = mock_http

            client.fork_session("ses_abc")
            call_args = mock_http.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert body == {}


class TestReplyPermission:
    def test_sends_correct_body(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(200)

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            MockClient.return_value = mock_http

            client.reply_permission("perm_1", "always")
            call_args = mock_http.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert body == {"reply": "always"}

    def test_includes_message_when_provided(self):
        client = OpenCodeClient("http://localhost:9100")
        mock_resp = _mock_response(200)

        with patch("httpx.Client") as MockClient:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            MockClient.return_value = mock_http

            client.reply_permission("perm_1", "reject", message="not allowed")
            call_args = mock_http.post.call_args
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
