from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from filelock import FileLock


@dataclass
class Session:
    id: str
    port: int
    pid: int
    created_at: str
    last_activity: str
    config_path: Optional[str] = None
    status: str = "running"
    has_uncommitted_changes: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("has_uncommitted_changes", None)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        data = data.copy()
        data.pop("has_uncommitted_changes", None)
        return cls(**data)


@dataclass
class Store:
    sessions: dict[str, Session] = field(default_factory=dict)
    next_port: int = 9100

    @classmethod
    def path(cls) -> Path:
        return (
            Path(
                os.environ.get(
                    "OCCTL_DATA_DIR", Path.home() / ".local" / "share" / "opencode-ctl"
                )
            )
            / "store.json"
        )

    @classmethod
    def lock_path(cls) -> Path:
        return cls.path().with_suffix(".lock")

    @classmethod
    def load(cls) -> Store:
        path = cls.path()
        if not path.exists():
            return cls()

        with open(path) as f:
            data = json.load(f)

        sessions = {
            k: Session.from_dict(v) for k, v in data.get("sessions", {}).items()
        }
        return cls(sessions=sessions, next_port=data.get("next_port", 9100))

    def save(self) -> None:
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "sessions": {k: v.to_dict() for k, v in self.sessions.items()},
            "next_port": self.next_port,
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def allocate_port(self) -> int:
        port = self.next_port
        self.next_port += 1
        return port

    def add_session(self, session: Session) -> None:
        self.sessions[session.id] = session

    def remove_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def get_session(self, session_id: str) -> Optional[Session]:
        return self.sessions.get(session_id)

    def update_activity(self, session_id: str) -> None:
        if session := self.sessions.get(session_id):
            session.last_activity = datetime.now().isoformat()


class TransactionalStore:
    def __init__(self):
        self._lock = FileLock(Store.lock_path(), timeout=10)
        self._store: Optional[Store] = None

    def __enter__(self) -> Store:
        self._lock.acquire()
        self._store = Store.load()
        return self._store

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None and self._store:
            self._store.save()
        self._lock.release()
        self._store = None
