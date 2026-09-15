from __future__ import annotations

import contextlib
import json
import socket
import threading
import uuid
from collections.abc import Callable
from urllib.parse import urlsplit

_SCOPE_FIELDS = ("harnessId", "sessionId", "model", "baseUrl")


class CredentialChannel:
    def __init__(self, resolve: Callable[[str], str | None]) -> None:
        self._resolve = resolve
        self._grants: dict[str, tuple[str, dict[str, str]]] = {}
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def grant(self, provider: str, scope: dict[str, str]) -> str:
        if any(not scope.get(key) for key in _SCOPE_FIELDS):
            raise ValueError("credential scope incomplete")
        url = urlsplit(scope["baseUrl"])
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("provider URL invalid")
        if url.port == 0:
            raise ValueError("provider port invalid")
        scope = {key: scope[key] for key in _SCOPE_FIELDS}
        with self._lock:
            for ref, existing in self._grants.items():
                if existing == (provider, scope):
                    return ref
            ref = f"secret://manager/model/{uuid.uuid4().hex}"
            self._grants[ref] = (provider, scope)
            return ref

    @property
    def available(self) -> bool:
        thread = self._thread
        return self._socket is not None and thread is not None and thread.is_alive()

    def revoke(self, ref: str) -> None:
        with self._lock:
            self._grants.pop(ref, None)

    def start(self, sock: socket.socket) -> None:
        self._socket = sock
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        sock = self._socket
        if sock is None:
            return
        sequence = 0
        try:
            with sock.makefile("rb") as reader:
                while True:
                    line = reader.readline(16385)
                    if not line or len(line) > 16384 or not line.endswith(b"\n"):
                        return
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        return
                    number = request.get("sequence")
                    response = {"sequence": number, "error": "credential_unavailable"}
                    if (
                        isinstance(number, int)
                        and not isinstance(number, bool)
                        and number > sequence
                    ):
                        sequence = number
                        with self._lock:
                            grant = self._grants.get(str(request.get("credentialRef", "")))
                        if grant and all(
                            request.get(key) == value for key, value in grant[1].items()
                        ):
                            try:
                                value = self._resolve(grant[0])
                            except Exception:
                                value = None
                            with self._lock:
                                still_granted = self._grants.get(request["credentialRef"]) == grant
                                if (
                                    still_granted
                                    and isinstance(value, str)
                                    and value
                                    and len(value) <= 8192
                                ):
                                    response = {"sequence": number, "value": value}
                                sock.sendall(json.dumps(response).encode() + b"\n")
                            continue
                    sock.sendall(json.dumps(response).encode() + b"\n")
        except (OSError, ValueError):
            return
        finally:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        with self._lock:
            self._grants.clear()
