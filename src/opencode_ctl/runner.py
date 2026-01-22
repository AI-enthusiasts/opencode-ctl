from __future__ import annotations

import os
import signal
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from .store import Session, TransactionalStore


class OpenCodeRunner:
    def __init__(self, opencode_bin: str = "opencode"):
        self.opencode_bin = opencode_bin

    def start(self, workdir: Optional[str] = None, timeout: float = 30.0) -> Session:
        with TransactionalStore() as store:
            port = store.allocate_port()
            session_id = f"oc-{uuid.uuid4().hex[:8]}"

            cmd = [self.opencode_bin, "serve", "--port", str(port)]

            env = os.environ.copy()
            env["OPENCODE_SESSION_ID"] = session_id

            cwd = workdir or os.getcwd()

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
                cwd=cwd,
            )

            if not self._wait_for_ready(port, timeout):
                proc.terminate()
                raise RuntimeError(f"OpenCode failed to start on port {port}")

            now = datetime.now().isoformat()
            session = Session(
                id=session_id,
                port=port,
                pid=proc.pid,
                created_at=now,
                last_activity=now,
                config_path=workdir,
                status="running",
            )

            store.add_session(session)
            return session

    def stop(self, session_id: str, force: bool = False) -> bool:
        with TransactionalStore() as store:
            session = store.get_session(session_id)
            if not session:
                return False

            try:
                sig = signal.SIGKILL if force else signal.SIGTERM
                os.kill(session.pid, sig)
                time.sleep(0.5)
            except ProcessLookupError:
                pass

            store.remove_session(session_id)
            return True

    def status(self, session_id: str) -> Optional[Session]:
        with TransactionalStore() as store:
            session = store.get_session(session_id)
            if not session:
                return None

            if not self._is_process_alive(session.pid):
                session.status = "dead"
                store.remove_session(session_id)
            elif not self._is_responsive(session.port):
                session.status = "unresponsive"
            else:
                session.status = "running"

            return session

    def list_sessions(self) -> list[Session]:
        with TransactionalStore() as store:
            sessions = []
            dead_ids = []

            for session_id, session in store.sessions.items():
                if not self._is_process_alive(session.pid):
                    dead_ids.append(session_id)
                else:
                    sessions.append(session)

            for dead_id in dead_ids:
                store.remove_session(dead_id)

            return sessions

    def cleanup_idle(self, max_idle_seconds: int = 60) -> list[str]:
        stopped = []
        with TransactionalStore() as store:
            now = datetime.now()

            for session_id, session in list(store.sessions.items()):
                last = datetime.fromisoformat(session.last_activity)
                idle = (now - last).total_seconds()

                if idle > max_idle_seconds:
                    try:
                        os.kill(session.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    store.remove_session(session_id)
                    stopped.append(session_id)

        return stopped

    def touch(self, session_id: str) -> bool:
        with TransactionalStore() as store:
            if store.get_session(session_id):
                store.update_activity(session_id)
                return True
            return False

    def _wait_for_ready(self, port: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_responsive(port):
                return True
            time.sleep(0.2)
        return False

    def _is_responsive(self, port: int) -> bool:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(f"http://localhost:{port}/health")
                return resp.status_code == 200
        except Exception:
            return False

    def _is_process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
