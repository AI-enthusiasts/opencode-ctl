from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


@dataclass
class SendResult:
    text: str
    raw: dict[str, Any]
    session_id: str = ""


@dataclass
class Permission:
    id: str
    permission: str
    patterns: list[str]
    tool_call_id: str = ""
    tool_message_id: str = ""


@dataclass
class ToolCall:
    name: str
    state: str
    args: dict[str, Any] = field(default_factory=dict)
    result: str = ""


@dataclass
class Message:
    id: str
    role: str
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    timestamp: int = 0


@dataclass
class SessionInfo:
    id: str
    title: str
    created: int
    updated: int
    parent_id: str | None = None


class OpenCodeClientError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


class OpenCodeClient:
    def __init__(self, base_url: str, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def create_session(self) -> str:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.base_url}/session", json={})
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)
            return resp.json().get("id")

    def send_message(
        self,
        session_id: str,
        text: str,
        agent: Optional[str] = None,
    ) -> SendResult:
        """Send message synchronously (blocking, waits for response)."""
        body: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if agent:
            body["agent"] = agent

        with httpx.Client(timeout=self.timeout) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/session/{session_id}/message",
                json=body,
                timeout=self.timeout,
            ) as resp:
                if resp.status_code != 200:
                    raise OpenCodeClientError(
                        resp.status_code, "Failed to send message"
                    )

                full_response = ""
                for chunk in resp.iter_text():
                    full_response += chunk

        if not full_response:
            return SendResult(text="", raw={}, session_id=session_id)

        try:
            data = json.loads(full_response)
        except json.JSONDecodeError:
            return SendResult(text=full_response, raw={}, session_id=session_id)

        text_parts = []
        for part in data.get("parts", []):
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))

        return SendResult(text="\n".join(text_parts), raw=data, session_id=session_id)

    def send_message_async(
        self,
        session_id: str,
        text: str,
        agent: Optional[str] = None,
    ) -> str:
        """Send message asynchronously (non-blocking, returns immediately)."""
        body: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if agent:
            body["agent"] = agent

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{self.base_url}/session/{session_id}/prompt_async",
                json=body,
            )
            if resp.status_code not in (200, 204):
                raise OpenCodeClientError(
                    resp.status_code, "Failed to send async message"
                )

        return session_id

    def get_session_status(self) -> dict[str, dict]:
        """Get status of all sessions via /session/status endpoint.

        Returns dict mapping session_id to status info like {"type": "idle"|"busy"|"retry"}.
        """
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/session/status")
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)
            return resp.json()

    def is_session_busy(self, session_id: str) -> bool:
        """Check if session is currently processing via /session/status endpoint."""
        statuses = self.get_session_status()
        status = statuses.get(session_id, {})
        return status.get("type") in ("busy", "retry")

    def get_last_assistant_message(self, session_id: str) -> Optional[Message]:
        """Get the last assistant message from session."""
        messages = self.get_messages(session_id, limit=20)
        for msg in reversed(messages):
            if msg.role == "assistant":
                return msg
        return None

    def wait_for_completion(
        self,
        session_id: str,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
    ) -> Optional[Message]:
        """Wait for session to complete processing and return last assistant message."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_session_busy(session_id):
                return self.get_last_assistant_message(session_id)
            time.sleep(poll_interval)
        return None

    def list_permissions(self) -> list[Permission]:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/permission")
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)

            return [
                Permission(
                    id=p.get("id", ""),
                    permission=p.get("permission", ""),
                    patterns=p.get("patterns", []),
                    tool_call_id=p.get("tool", {}).get("callID", ""),
                    tool_message_id=p.get("tool", {}).get("messageID", ""),
                )
                for p in resp.json()
            ]

    def reply_permission(
        self,
        permission_id: str,
        reply: str,
        message: Optional[str] = None,
    ) -> None:
        body: dict[str, Any] = {"reply": reply}
        if message:
            body["message"] = message

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{self.base_url}/permission/{permission_id}/reply",
                json=body,
            )
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)

    def list_oc_sessions(self) -> list[SessionInfo]:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/session")
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)

            return [
                SessionInfo(
                    id=s.get("id", ""),
                    title=s.get("title", ""),
                    created=s.get("time", {}).get("created", 0),
                    updated=s.get("time", {}).get("updated", 0),
                    parent_id=s.get("parentID"),
                )
                for s in resp.json()
            ]

    def get_session(self, session_id: str) -> SessionInfo | None:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/session/{session_id}")
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)

            s = resp.json()
            return SessionInfo(
                id=s.get("id", ""),
                title=s.get("title", ""),
                created=s.get("time", {}).get("created", 0),
                updated=s.get("time", {}).get("updated", 0),
                parent_id=s.get("parentID"),
            )

    def fork_session(
        self,
        session_id: str,
        message_id: Optional[str] = None,
    ) -> SessionInfo:
        """Fork a session, copying all messages up to (but not including) message_id."""
        body: dict[str, Any] = {}
        if message_id:
            body["messageID"] = message_id

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/session/{session_id}/fork",
                json=body,
            )
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)

            s = resp.json()
            return SessionInfo(
                id=s.get("id", ""),
                title=s.get("title", ""),
                created=s.get("time", {}).get("created", 0),
                updated=s.get("time", {}).get("updated", 0),
                parent_id=s.get("parentID"),
            )

    def get_messages(self, session_id: str, limit: int = 10) -> list[Message]:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/session/{session_id}/message")
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)

            messages = []
            for m in resp.json()[-limit:]:
                info = m.get("info", {})
                text_parts = []
                tool_calls = []
                for part in m.get("parts", []):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "tool":
                        state_info = part.get("state", {})
                        tool_calls.append(
                            ToolCall(
                                name=part.get("tool", ""),
                                state=state_info.get("status", ""),
                                args=state_info.get("input", {}),
                                result=str(state_info.get("output", "")),
                            )
                        )

                time_info = info.get("time", {})
                messages.append(
                    Message(
                        id=info.get("id", ""),
                        role=info.get("role", "unknown"),
                        text="\n".join(text_parts),
                        tool_calls=tool_calls,
                        timestamp=time_info.get("created", 0),
                    )
                )

            return messages

    def get_config(self) -> dict[str, Any]:
        """Get the resolved OpenCode configuration."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/config")
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)
            return resp.json()
