"""OpenCode session lifecycle manager."""

__version__ = "0.1.0"

from .client import OpenCodeClient, OpenCodeClientError, Permission, SendResult
from .runner import OpenCodeRunner, SessionNotFoundError, SessionNotRunningError
from .store import Session, Store, TransactionalStore

__all__ = [
    "OpenCodeClient",
    "OpenCodeClientError",
    "OpenCodeRunner",
    "Permission",
    "SendResult",
    "Session",
    "SessionNotFoundError",
    "SessionNotRunningError",
    "Store",
    "TransactionalStore",
]
