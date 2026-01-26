from __future__ import annotations

import os
import re
import signal
import subprocess
import time
import uuid
from datetime import datetime
from typing import Optional

from .client import (
    OpenCodeClient,
    OpenCodeClientError,
    Message,
    Permission,
    SendResult,
    SessionInfo,
)
from .store import Session, TransactionalStore


class SessionNotFoundError(Exception):
    pass


class SessionNotRunningError(Exception):
    def __init__(self, status: str):
        self.status = status
        super().__init__(f"Session not running: {status}")


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
            if not os.path.isdir(cwd):
                os.makedirs(cwd, exist_ok=True)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
                cwd=cwd,
                text=True,
            )

            url = self._wait_for_server_url(proc, port, timeout)
            if not url:
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

            session.status = self._determine_status(session)

            if session.status == "dead":
                store.remove_session(session_id)

            return session

    def list_sessions(self) -> list[Session]:
        with TransactionalStore() as store:
            sessions = []
            dead_ids = []

            for sid, session in store.sessions.items():
                status = self._determine_status(session)
                if status == "dead":
                    dead_ids.append(sid)
                else:
                    session.status = status
                    sessions.append(session)

            for dead_id in dead_ids:
                store.remove_session(dead_id)

            return sessions

    def cleanup_idle(self, max_idle_seconds: int = 60) -> list[str]:
        stopped = []
        with TransactionalStore() as store:
            now = datetime.now()

            for sid, session in list(store.sessions.items()):
                last = datetime.fromisoformat(session.last_activity)
                idle = (now - last).total_seconds()

                if idle > max_idle_seconds:
                    try:
                        os.kill(session.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    store.remove_session(sid)
                    stopped.append(sid)

        return stopped

    def touch(self, session_id: str) -> bool:
        with TransactionalStore() as store:
            if store.get_session(session_id):
                store.update_activity(session_id)
                return True
            return False

    def send(
        self,
        session_id: str,
        message: str,
        agent: Optional[str] = None,
        timeout: float = 300.0,
        wait: bool = False,
    ) -> SendResult:
        session = self._get_running_session(session_id)
        self.touch(session_id)

        client = OpenCodeClient(f"http://localhost:{session.port}", timeout=timeout)
        oc_session_id = client.create_session()

        if wait:
            return client.send_message(oc_session_id, message, agent)

        client.send_message_async(oc_session_id, message, agent)
        return SendResult(text="", raw={}, session_id=oc_session_id)

    def wait_for_response(
        self,
        session_id: str,
        oc_session_id: str,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
    ) -> Optional[Message]:
        session = self._get_running_session(session_id)
        client = OpenCodeClient(f"http://localhost:{session.port}")
        return client.wait_for_completion(oc_session_id, timeout, poll_interval)

    def list_permissions(self, session_id: str) -> list[Permission]:
        session = self._get_running_session(session_id)
        client = OpenCodeClient(f"http://localhost:{session.port}")
        return client.list_permissions()

    def approve_permission(
        self,
        session_id: str,
        permission_id: str,
        always: bool = False,
    ) -> None:
        session = self._get_running_session(session_id)
        client = OpenCodeClient(f"http://localhost:{session.port}")
        reply = "always" if always else "once"
        client.reply_permission(permission_id, reply)

    def reject_permission(
        self,
        session_id: str,
        permission_id: str,
        message: Optional[str] = None,
    ) -> None:
        session = self._get_running_session(session_id)
        client = OpenCodeClient(f"http://localhost:{session.port}")
        client.reply_permission(permission_id, "reject", message)

    def get_attach_url(self, session_id: str) -> str:
        session = self._get_running_session(session_id)
        return f"http://localhost:{session.port}"

    def list_oc_sessions(self, session_id: str) -> list[SessionInfo]:
        session = self._get_running_session(session_id)
        client = OpenCodeClient(f"http://localhost:{session.port}")
        return client.list_oc_sessions()

    def get_oc_session(self, session_id: str, oc_session_id: str) -> SessionInfo | None:
        session = self._get_running_session(session_id)
        client = OpenCodeClient(f"http://localhost:{session.port}")
        return client.get_session(oc_session_id)

    def get_session_chain(
        self, session_id: str, oc_session_id: str
    ) -> list[SessionInfo]:
        session = self._get_running_session(session_id)
        client = OpenCodeClient(f"http://localhost:{session.port}")
        all_sessions = client.list_oc_sessions()
        sessions_by_id = {s.id: s for s in all_sessions}

        chain = []
        current_id: str | None = oc_session_id

        while current_id and current_id in sessions_by_id:
            sess = sessions_by_id[current_id]
            chain.append(sess)
            current_id = sess.parent_id

        chain.reverse()

        children = [s for s in all_sessions if s.parent_id == oc_session_id]
        chain.extend(sorted(children, key=lambda s: s.created))

        return chain

    def get_messages(
        self, session_id: str, oc_session_id: str, limit: int = 10
    ) -> list[Message]:
        session = self._get_running_session(session_id)
        client = OpenCodeClient(f"http://localhost:{session.port}")
        return client.get_messages(oc_session_id, limit)

    def get_chain_messages(
        self, session_id: str, oc_session_id: str, limit: int = 100
    ) -> list[Message]:
        session = self._get_running_session(session_id)
        client = OpenCodeClient(f"http://localhost:{session.port}")

        all_sessions = client.list_oc_sessions()
        sessions_by_id = {s.id: s for s in all_sessions}

        parent_chain = []
        current_id: str | None = oc_session_id
        while current_id and current_id in sessions_by_id:
            parent_chain.append(current_id)
            current_id = sessions_by_id[current_id].parent_id
        parent_chain.reverse()

        all_messages = []
        for sess_id in parent_chain:
            messages = client.get_messages(sess_id, limit=1000)
            all_messages.extend(messages)

        all_messages.sort(key=lambda m: m.timestamp)

        return all_messages[-limit:] if len(all_messages) > limit else all_messages

    def _get_running_session(self, session_id: str) -> Session:
        session = self.status(session_id)
        if not session:
            raise SessionNotFoundError(session_id)
        if session.status not in ("running", "waiting_permission", "idle"):
            raise SessionNotRunningError(session.status)
        return session

    def _wait_for_server_url(
        self, proc: subprocess.Popen, port: int, timeout: float
    ) -> Optional[str]:
        deadline = time.time() + timeout
        pattern = re.compile(r"opencode server listening on (https?://[^\s]+)")

        while time.time() < deadline:
            if proc.poll() is not None:
                return None

            if proc.stdout:
                line = proc.stdout.readline()
                if line:
                    match = pattern.search(line)
                    if match:
                        return match.group(1)

            time.sleep(0.1)

        return None

    def _is_process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def _determine_status(self, session: Session) -> str:
        """Determine the actual status of a session.

        Returns:
            - "dead" if process is not alive
            - "waiting_permission" if has pending permissions
            - "idle" if not busy and no pending permissions
            - "running" if actively processing
            - "error" if cannot determine (e.g., server unreachable)
        """
        if not self._is_process_alive(session.pid):
            return "dead"

        try:
            client = OpenCodeClient(f"http://localhost:{session.port}")

            # Check for pending permissions first
            permissions = client.list_permissions()
            if permissions:
                return "waiting_permission"

            # Check if any OpenCode session is busy
            oc_sessions = client.list_oc_sessions()
            if not oc_sessions:
                return "idle"

            # Consider session busy only if updated recently (within last 10 seconds)
            now_ms = int(time.time() * 1000)
            for oc_sess in oc_sessions:
                # Check if session was updated recently (less than 10 seconds ago)
                if oc_sess.updated and (now_ms - oc_sess.updated) < 10_000:
                    if client.is_session_busy(oc_sess.id):
                        return "running"

            return "idle"

        except (OpenCodeClientError, Exception):
            # If we can't connect to the server, mark as error
            return "error"
