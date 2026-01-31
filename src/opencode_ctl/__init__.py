"""OpenCode session lifecycle manager."""

__version__ = "0.4.0"

from .client import (
    Message,
    OpenCodeClient,
    OpenCodeClientError,
    Permission,
    SendResult,
    SessionInfo,
    ToolCall,
)
from .runner import OpenCodeRunner, SessionNotFoundError, SessionNotRunningError
from .store import Session, Store, TransactionalStore

__all__ = [
    "Message",
    "OpenCodeClient",
    "OpenCodeClientError",
    "OpenCodeRunner",
    "Permission",
    "SendResult",
    "Session",
    "SessionInfo",
    "SessionNotFoundError",
    "SessionNotRunningError",
    "Store",
    "ToolCall",
    "TransactionalStore",
]
