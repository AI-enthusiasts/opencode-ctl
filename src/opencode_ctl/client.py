from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx


@dataclass
class SendResult:
    text: str
    raw: dict[str, Any]


@dataclass
class Permission:
    id: str
    permission: str
    pattern: str
    tool_name: str


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
            return SendResult(text="", raw={})

        import json

        try:
            data = json.loads(full_response)
        except json.JSONDecodeError:
            return SendResult(text=full_response, raw={})

        text_parts = []
        for part in data.get("parts", []):
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))

        return SendResult(text="\n".join(text_parts), raw=data)

    def list_permissions(self) -> list[Permission]:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/permission")
            if resp.status_code != 200:
                raise OpenCodeClientError(resp.status_code, resp.text)

            return [
                Permission(
                    id=p.get("id", ""),
                    permission=p.get("permission", ""),
                    pattern=p.get("pattern", ""),
                    tool_name=p.get("tool", {}).get("name", ""),
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
