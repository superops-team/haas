"""Container-private delegated worker command and durable receipt replay."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TextIO

from haas.runtime.broker import (
    BrokerUnavailable,
    FakeWorkerBroker,
    UnavailableWorkerBroker,
    WorkerBroker,
)

WorkerExecutor = Callable[[dict[str, object]], list[dict[str, object]]]
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class PrivateWorkerError(RuntimeError):
    """Safe private-worker protocol failure."""


class PrivateWorker:
    def __init__(
        self,
        *,
        data_root: Path,
        generation: int,
        execute: WorkerExecutor | None = None,
        broker: WorkerBroker | None = None,
    ) -> None:
        self.data_root = data_root
        self.generation = generation
        self.broker = broker or UnavailableWorkerBroker()
        self.execute = execute or self._execute

    def run(self, envelope: dict[str, object]) -> Iterator[dict[str, object]]:
        if envelope.get("version") != 1:
            raise PrivateWorkerError("private_worker_version_unsupported")
        if envelope.get("generation") != self.generation:
            raise PrivateWorkerError("private_worker_generation_mismatch")
        execution_id = envelope.get("executionId")
        if not isinstance(execution_id, str) or not (
            1 <= len(execution_id) <= 128 and all(char in _ID_CHARS for char in execution_id)
        ):
            raise PrivateWorkerError("private_worker_execution_id_invalid")
        request = envelope.get("request")
        if not isinstance(request, dict):
            raise PrivateWorkerError("private_worker_request_invalid")

        token = self._read_generation_token()
        receipt_dir = self.data_root / "receipts" / str(self.generation)
        receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        receipt = receipt_dir / f"{execution_id}.ndjson"
        signature = receipt.with_suffix(".hmac")
        running = receipt.with_suffix(".running")
        request_digest = hashlib.sha256(self._canonical(envelope)).hexdigest()

        if receipt.exists() or signature.exists():
            yield from self._replay(receipt, signature, token, request_digest)
            return
        try:
            descriptor = os.open(running, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise PrivateWorkerError("private_worker_execution_ambiguous") from exc
        try:
            os.write(descriptor, request_digest.encode("ascii"))
        finally:
            os.close(descriptor)

        try:
            events = self.execute(request)
            encoded = b"".join(self._canonical(event) + b"\n" for event in events)
            self._atomic_write(receipt, encoded)
            digest = hmac.new(
                token, request_digest.encode("ascii") + b"\0" + encoded, hashlib.sha256
            ).hexdigest()
            self._atomic_write(signature, f"{request_digest} {digest}\n".encode("ascii"))
            running.unlink()
        except Exception:
            # A surviving marker makes an uncertain native start explicit.
            raise
        yield from events

    def _read_generation_token(self) -> bytes:
        path = self.data_root / "private" / f"generation-{self.generation}.token"
        try:
            info = path.stat()
            if stat.S_IMODE(info.st_mode) != 0o600 or not stat.S_ISREG(info.st_mode):
                raise PrivateWorkerError("private_worker_token_permissions_invalid")
            token = path.read_bytes()
        except OSError as exc:
            raise PrivateWorkerError("private_worker_token_unavailable") from exc
        if len(token) < 32:
            raise PrivateWorkerError("private_worker_token_invalid")
        return token

    def _replay(
        self, receipt: Path, signature: Path, token: bytes, request_digest: str
    ) -> Iterator[dict[str, object]]:
        try:
            encoded = receipt.read_bytes()
            stored_request, stored_signature = signature.read_text(encoding="ascii").strip().split()
        except (OSError, ValueError) as exc:
            raise PrivateWorkerError("private_worker_receipt_incomplete") from exc
        expected = hmac.new(
            token, stored_request.encode("ascii") + b"\0" + encoded, hashlib.sha256
        ).hexdigest()
        if stored_request != request_digest:
            raise PrivateWorkerError("private_worker_execution_conflict")
        if not hmac.compare_digest(stored_signature, expected):
            raise PrivateWorkerError("private_worker_receipt_invalid")
        for line in encoded.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PrivateWorkerError("private_worker_receipt_invalid") from exc
            if not isinstance(event, dict):
                raise PrivateWorkerError("private_worker_receipt_invalid")
            yield event

    def _execute(self, request: dict[str, object]) -> list[dict[str, object]]:
        # The fake harness is the only P0 offline worker. Codex requires the
        # not-yet-implemented private model/MCP broker and therefore fails closed.
        result = self.broker.model({"text": "offline"})
        text = str(result.get("text") or "offline")
        return [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "actions": {"stateDelta": {"last_text": text}},
            },
            {
                "content": {"role": "model", "parts": []},
                "actions": {"stateDelta": {"status": "completed"}},
            },
        ]

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    def _atomic_write(path: Path, value: bytes) -> None:
        temporary = path.with_suffix(path.suffix + f".{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, value)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)


def main(
    *, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr
) -> int:
    try:
        envelope = json.loads(stdin.read())
        if not isinstance(envelope, dict):
            raise PrivateWorkerError("private_worker_request_invalid")
        generation = int(os.environ.get("HAAS_WORKER_GENERATION", "0"))
        harness_base = envelope.get("harnessBase")
        broker: WorkerBroker = (
            FakeWorkerBroker() if harness_base == "fake" else UnavailableWorkerBroker()
        )
        worker = PrivateWorker(
            data_root=Path(os.environ.get("HAAS_DATA_ROOT", "/data/haas")),
            generation=generation,
            broker=broker,
        )
        for event in worker.run(envelope):
            stdout.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            stdout.flush()
        return 0
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, (PrivateWorkerError, BrokerUnavailable))
            else "private_worker_failed"
        )
        stderr.write(code + "\n")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
