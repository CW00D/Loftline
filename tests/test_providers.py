"""Vendor client tests through a fake transport: exact requests, no network."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from loftline.errors import ProvisionError
from loftline.providers.aura import AuraClient
from loftline.providers.render import RenderClient

Call = tuple[str, str, dict[str, str], bytes | None]


class FakeTransport:
    """Answers each (method, path) with a canned (status, body); records calls."""

    def __init__(self, answers: dict[tuple[str, str], tuple[int, object]]) -> None:
        self.answers = answers
        self.calls: list[Call] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        path = url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append((method, "/" + path, dict(headers), body))
        status, answer = self.answers.get(
            (method, "/" + path), (404, {"error": "no canned answer"})
        )
        return status, json.dumps(answer).encode()


# --- Aura --------------------------------------------------------------------

TOKEN = {"access_token": "tok", "token_type": "bearer"}


def aura(
    answers: dict[tuple[str, str], tuple[int, object]],
) -> tuple[AuraClient, FakeTransport]:
    transport = FakeTransport({("POST", "/oauth/token"): (200, TOKEN), **answers})
    return AuraClient(
        "cid", "csecret", transport=transport, sleep=lambda _s: None
    ), transport


def test_aura_authenticates_with_basic_credentials_then_a_bearer_token() -> None:
    client, transport = aura(
        {("GET", "/v1/tenants"): (200, {"data": [{"id": "t1", "name": "T"}]})}
    )

    assert client.tenant_id() == "t1"
    token_call, tenants_call = transport.calls
    assert token_call[2]["Authorization"].startswith("Basic ")
    assert token_call[3] == b"grant_type=client_credentials"
    assert tenants_call[2]["Authorization"] == "Bearer tok"


def test_aura_bad_credentials_are_a_clear_error() -> None:
    transport = FakeTransport(
        {("POST", "/oauth/token"): (401, {"error": "invalid_client"})}
    )
    client = AuraClient("cid", "wrong", transport=transport)

    with pytest.raises(ProvisionError, match="aura_client_secret"):
        client.tenant_id()


def test_aura_create_instance_sends_the_documented_shape_and_keeps_the_password() -> (
    None
):
    client, transport = aura(
        {
            ("GET", "/v1/tenants"): (200, {"data": [{"id": "t1"}]}),
            ("POST", "/v1/instances"): (
                202,
                {
                    "data": {
                        "id": "i1",
                        "status": "creating",
                        "connection_url": None,
                        "username": "neo4j",
                        "password": "pw-once",
                    }
                },
            ),
        }
    )

    instance = client.create_instance("demo-staging")

    body = json.loads(
        next(c[3] for c in transport.calls if c[1] == "/v1/instances") or b"{}"
    )
    assert body == {
        "name": "demo-staging",
        "tenant_id": "t1",
        "type": "free-db",
        "cloud_provider": "gcp",
        "region": "europe-west1",
        "memory": "1GB",
        "version": "5",
    }
    assert (instance.id, instance.username, instance.password) == (
        "i1",
        "neo4j",
        "pw-once",
    )


def test_aura_refusal_is_passed_through_verbatim() -> None:
    client, _ = aura(
        {
            ("GET", "/v1/tenants"): (200, {"data": [{"id": "t1"}]}),
            ("POST", "/v1/instances"): (
                400,
                {"errors": [{"message": "free-tier instance limit reached"}]},
            ),
        }
    )

    with pytest.raises(ProvisionError, match="free-tier instance limit reached"):
        client.create_instance("demo-staging")


def test_aura_wait_until_running_polls_and_returns_the_uri() -> None:
    states = iter(["creating", "creating", "running"])
    transport = FakeTransport({("POST", "/oauth/token"): (200, TOKEN)})

    def answer(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        if url.endswith("/v1/instances/i1"):
            status = next(states)
            return 200, json.dumps(
                {
                    "data": {
                        "id": "i1",
                        "name": "n",
                        "status": status,
                        "connection_url": "neo4j+s://x"
                        if status == "running"
                        else None,
                    }
                }
            ).encode()
        return transport(method, url, headers, body)

    client = AuraClient("c", "s", transport=answer, sleep=lambda _s: None)

    instance = client.wait_until_running("i1")

    assert instance.status == "running"
    assert instance.uri == "neo4j+s://x"


def test_aura_find_instance_by_name() -> None:
    client, _ = aura(
        {
            ("GET", "/v1/instances"): (
                200,
                {
                    "data": [
                        {"id": "i1", "name": "other"},
                        {"id": "i2", "name": "demo-staging"},
                    ]
                },
            ),
            ("GET", "/v1/instances/i2"): (
                200,
                {
                    "data": {
                        "id": "i2",
                        "name": "demo-staging",
                        "status": "running",
                        "connection_url": "neo4j+s://y",
                    }
                },
            ),
        }
    )

    found = client.find_instance("demo-staging")

    assert found is not None and found.id == "i2" and found.password is None
    assert client.find_instance("nope") is None


# --- Render ------------------------------------------------------------------


def render(
    answers: dict[tuple[str, str], tuple[int, object]],
) -> tuple[RenderClient, FakeTransport]:
    transport = FakeTransport(answers)
    return RenderClient(
        "rnd-key", transport=transport, sleep=lambda _s: None
    ), transport


def test_render_find_service_by_name() -> None:
    client, transport = render(
        {
            ("GET", "/v1/services?name=demo-api-staging&limit=5"): (
                200,
                [
                    {
                        "service": {
                            "id": "srv-1",
                            "name": "demo-api-staging",
                            "branch": "staging",
                            "serviceDetails": {
                                "url": "https://demo-api-staging.onrender.com"
                            },
                        }
                    }
                ],
            )
        }
    )

    service = client.find_service("demo-api-staging")

    assert service is not None
    assert (service.id, service.url, service.branch) == (
        "srv-1",
        "https://demo-api-staging.onrender.com",
        "staging",
    )
    assert transport.calls[0][2]["Authorization"] == "Bearer rnd-key"


def test_render_env_vars_returns_key_to_value() -> None:
    client, _ = render(
        {
            ("GET", "/v1/services/srv-1/env-vars?limit=100"): (
                200,
                [
                    {"envVar": {"key": "JWT_SECRET", "value": "abc"}},
                    {"envVar": {"key": "SMTP_USER", "value": "me"}},
                ],
            )
        }
    )

    assert client.env_vars("srv-1") == {"JWT_SECRET": "abc", "SMTP_USER": "me"}


def test_render_set_env_var_puts_one_key() -> None:
    client, transport = render(
        {("PUT", "/v1/services/srv-1/env-vars/NEO4J_URI"): (200, {})}
    )

    client.set_env_var("srv-1", "NEO4J_URI", "neo4j+s://x")

    method, path, _, body = transport.calls[0]
    assert (method, path) == ("PUT", "/v1/services/srv-1/env-vars/NEO4J_URI")
    assert json.loads(body or b"{}") == {"value": "neo4j+s://x"}


def test_render_failed_set_redacts_the_value() -> None:
    client, _ = render(
        {("PUT", "/v1/services/srv-1/env-vars/K"): (500, {"message": "boom hunter2"})}
    )

    with pytest.raises(ProvisionError) as excinfo:
        client.set_env_var("srv-1", "K", "hunter2")

    assert "hunter2" not in str(excinfo.value)
    assert "[value redacted]" in str(excinfo.value)


def test_render_deploy_and_wait() -> None:
    states = iter(["build_in_progress", "live"])
    transport = FakeTransport(
        {("POST", "/v1/services/srv-1/deploys"): (201, {"id": "dep-1"})}
    )

    def answer(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        if url.endswith("/deploys/dep-1"):
            return 200, json.dumps({"id": "dep-1", "status": next(states)}).encode()
        return transport(method, url, headers, body)

    client = RenderClient("k", transport=answer, sleep=lambda _s: None)

    deploy_id = client.trigger_deploy("srv-1")

    assert deploy_id == "dep-1"
    assert client.wait_for_deploy("srv-1", deploy_id) == "live"
