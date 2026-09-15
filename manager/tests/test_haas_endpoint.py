from __future__ import annotations

import pytest
from coworker.haas import (
    EndpointMode,
    EndpointRecordStore,
    EndpointValidationError,
    HaasEndpoint,
)


def test_local_endpoint_is_canonical_and_has_stable_non_secret_fingerprint() -> None:
    endpoint = HaasEndpoint.create(
        endpoint_id="hep_local",
        mode=EndpointMode.LOCAL_MANAGED,
        base_url="http://localhost:8092/",
        token_ref="secret://manager/haas/local",
        server_identity="install-a",
    )
    same_endpoint = HaasEndpoint.create(
        endpoint_id="hep_other_record",
        mode="local_managed",
        base_url="http://localhost:8092",
        token_ref="secret://manager/haas/rotated",
        server_identity="install-a",
    )

    assert endpoint.base_url == "http://localhost:8092"
    assert endpoint.url_fingerprint == same_endpoint.url_fingerprint
    assert endpoint.token_ref not in endpoint.url_fingerprint


@pytest.mark.parametrize(
    ("mode", "url"),
    [
        ("local_managed", "http://192.168.1.5:8092"),
        ("local_managed", "https://haas.example.com"),
        ("remote", "http://haas.example.com"),
        ("remote", "https://user:password@haas.example.com"),
    ],
)
def test_endpoint_rejects_unsafe_mode_url_combinations(mode: str, url: str) -> None:
    with pytest.raises(EndpointValidationError):
        HaasEndpoint.create(
            endpoint_id="hep_bad",
            mode=mode,
            base_url=url,
            token_ref="secret://manager/haas/default",
            server_identity="server-a",
        )


def test_remote_http_requires_explicit_visible_development_override() -> None:
    endpoint = HaasEndpoint.create(
        endpoint_id="hep_dev",
        mode="remote",
        base_url="http://dev-haas.example.test:8092",
        token_ref="secret://manager/haas/dev",
        server_identity="dev-server",
        allow_insecure_development=True,
    )

    assert endpoint.tls_verify is False
    assert endpoint.allow_insecure_development is True


def test_remote_tls_verification_can_only_be_disabled_with_development_override() -> None:
    with pytest.raises(EndpointValidationError):
        HaasEndpoint.create(
            endpoint_id="hep_bad_tls",
            mode="remote",
            base_url="https://haas.example.com",
            token_ref="secret://manager/haas/dev",
            server_identity="dev-server",
            tls_verify=False,
        )

    endpoint = HaasEndpoint.create(
        endpoint_id="hep_dev_tls",
        mode="remote",
        base_url="https://haas.example.com",
        token_ref="secret://manager/haas/dev",
        server_identity="dev-server",
        tls_verify=False,
        allow_insecure_development=True,
    )
    assert endpoint.tls_verify is False


@pytest.mark.parametrize("token_ref", ["", "raw-token", "Bearer secret"])
def test_endpoint_requires_an_opaque_secret_reference(token_ref: str) -> None:
    with pytest.raises(EndpointValidationError):
        HaasEndpoint.create(
            endpoint_id="hep_bad_token",
            mode="remote",
            base_url="https://haas.example.com",
            token_ref=token_ref,
            server_identity="server-a",
        )


def test_endpoint_record_round_trips_without_resolving_credential() -> None:
    endpoint = HaasEndpoint.create(
        endpoint_id="hep_remote",
        mode="remote",
        base_url="https://HAAS.example.com:443/",
        token_ref="secret://manager/haas/remote",
        server_identity="server-a",
    )

    restored = HaasEndpoint.from_dict(endpoint.to_dict())

    assert restored == endpoint
    assert restored.to_dict()["tokenRef"] == "secret://manager/haas/remote"
    assert "token" not in restored.to_dict()


def test_endpoint_store_persists_records_atomically_and_retains_distinct_bindings(tmp_path) -> None:
    store = EndpointRecordStore(tmp_path / "haas-endpoints.json")
    first = HaasEndpoint.create(
        endpoint_id="hep_first",
        mode="local_managed",
        base_url="http://127.0.0.1:8092",
        token_ref="secret://manager/haas/first",
        server_identity="install-a",
    )
    second = HaasEndpoint.create(
        endpoint_id="hep_second",
        mode="remote",
        base_url="https://haas.example.com",
        token_ref="secret://manager/haas/second",
        server_identity="server-b",
    )

    store.put(first)
    store.put(second)
    reopened = EndpointRecordStore(store.path)

    assert reopened.get("hep_first") == first
    assert reopened.get("hep_second") == second
    assert [item.endpoint_id for item in reopened.list()] == ["hep_first", "hep_second"]
    assert store.path.stat().st_mode & 0o777 == 0o600
