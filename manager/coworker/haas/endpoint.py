from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class EndpointValidationError(ValueError):
    """The endpoint record violates the Manager-to-HaaS trust boundary."""


class EndpointMode(str, Enum):
    LOCAL_MANAGED = "local_managed"
    REMOTE = "remote"


@dataclass(frozen=True, slots=True)
class HaasEndpoint:
    endpoint_id: str
    mode: EndpointMode
    base_url: str
    token_ref: str
    server_identity: str
    tls_verify: bool
    allow_insecure_development: bool
    url_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "endpointId": self.endpoint_id,
            "mode": self.mode.value,
            "baseUrl": self.base_url,
            "tokenRef": self.token_ref,
            "serverIdentity": self.server_identity,
            "tlsVerify": self.tls_verify,
            "allowInsecureDevelopment": self.allow_insecure_development,
            "urlFingerprint": self.url_fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: object) -> HaasEndpoint:
        if not isinstance(raw, dict):
            raise EndpointValidationError("endpoint record must be an object")
        endpoint = cls.create(
            endpoint_id=_required_string(raw, "endpointId"),
            mode=_required_string(raw, "mode"),
            base_url=_required_string(raw, "baseUrl"),
            token_ref=_required_string(raw, "tokenRef"),
            server_identity=_required_string(raw, "serverIdentity"),
            tls_verify=raw.get("tlsVerify") is not False,
            allow_insecure_development=raw.get("allowInsecureDevelopment") is True,
        )
        stored_fingerprint = raw.get("urlFingerprint")
        if stored_fingerprint is not None and stored_fingerprint != endpoint.url_fingerprint:
            raise EndpointValidationError("endpoint URL fingerprint does not match")
        return endpoint

    @classmethod
    def create(
        cls,
        *,
        endpoint_id: str,
        mode: EndpointMode | str,
        base_url: str,
        token_ref: str,
        server_identity: str,
        tls_verify: bool = True,
        allow_insecure_development: bool = False,
    ) -> HaasEndpoint:
        try:
            parsed_mode = EndpointMode(mode)
        except ValueError as exc:
            raise EndpointValidationError("unsupported endpoint mode") from exc
        if not endpoint_id.strip() or not server_identity.strip():
            raise EndpointValidationError("endpoint identity is required")
        if not token_ref.startswith("secret://") or len(token_ref) <= len("secret://"):
            raise EndpointValidationError("token_ref must be an opaque secret reference")

        parsed = urlsplit(base_url.strip())
        if not parsed.scheme or not parsed.hostname or parsed.query or parsed.fragment:
            raise EndpointValidationError("invalid endpoint URL")
        if parsed.username is not None or parsed.password is not None:
            raise EndpointValidationError("endpoint URL must not contain credentials")
        if parsed.path not in {"", "/"}:
            raise EndpointValidationError("endpoint URL must not contain a path")

        if parsed_mode is EndpointMode.LOCAL_MANAGED:
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
                raise EndpointValidationError("local_managed requires a loopback HTTP URL")
            effective_tls_verify = False
        else:
            if parsed.scheme != "https" and not allow_insecure_development:
                raise EndpointValidationError("remote endpoints require HTTPS")
            if parsed.scheme not in {"http", "https"}:
                raise EndpointValidationError("unsupported endpoint URL scheme")
            if not tls_verify and not allow_insecure_development:
                raise EndpointValidationError(
                    "disabling TLS verification requires a development override"
                )
            effective_tls_verify = tls_verify if parsed.scheme == "https" else False

        canonical = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", ""))
        fingerprint = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(
            endpoint_id=endpoint_id.strip(),
            mode=parsed_mode,
            base_url=canonical,
            token_ref=token_ref,
            server_identity=server_identity.strip(),
            tls_verify=effective_tls_verify,
            allow_insecure_development=allow_insecure_development,
            url_fingerprint=fingerprint,
        )


class EndpointRecordStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def get(self, endpoint_id: str) -> HaasEndpoint | None:
        return {record.endpoint_id: record for record in self.list()}.get(endpoint_id)

    def list(self) -> list[HaasEndpoint]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise EndpointValidationError("endpoint record store is invalid") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("version") != 1
            or not isinstance(raw.get("endpoints"), list)
        ):
            raise EndpointValidationError("endpoint record store is invalid")
        records = [HaasEndpoint.from_dict(item) for item in raw["endpoints"]]
        if len({record.endpoint_id for record in records}) != len(records):
            raise EndpointValidationError("endpoint record ids must be unique")
        return sorted(records, key=lambda item: item.endpoint_id)

    def put(self, endpoint: HaasEndpoint) -> None:
        records = {record.endpoint_id: record for record in self.list()}
        records[endpoint.endpoint_id] = endpoint
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "version": 1,
                        "endpoints": [
                            record.to_dict()
                            for record in sorted(
                                records.values(), key=lambda item: item.endpoint_id
                            )
                        ],
                    },
                    handle,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temp_name)


def _required_string(raw: dict[object, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise EndpointValidationError(f"endpoint record is missing {key}")
    return value
