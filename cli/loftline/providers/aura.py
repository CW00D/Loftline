"""Neo4j Aura: creating and finding database instances.

The Aura API issues an OAuth token for a client id and secret, then manages
instances under a tenant. An instance's password is returned exactly once,
in the create response, and can never be read back: the provisioner must
store it the moment it arrives, and an instance whose password was lost can
only be replaced.

The free tier permits one `free-db` instance per account. A second request
is refused by Aura, and the refusal is passed through verbatim so the person
can decide whether to pay, use another account, or provision fewer
environments.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..errors import ProvisionError
from . import Transport, decode, urllib_transport

BASE_URL = "https://api.neo4j.io"


@dataclass(frozen=True)
class Instance:
    id: str
    name: str
    status: str
    uri: str | None  # connection_url; absent while the instance is still creating
    username: str | None  # only on the create response
    password: str | None  # only on the create response, never again


class AuraClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: Transport | None = None,
        base_url: str = BASE_URL,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._id = client_id
        self._secret = client_secret
        self._transport = transport or urllib_transport
        self._base = base_url.rstrip("/")
        self._sleep = sleep
        self._token: str | None = None

    # --- plumbing ------------------------------------------------------------

    def _bearer(self) -> str:
        if self._token is None:
            basic = base64.b64encode(f"{self._id}:{self._secret}".encode()).decode()
            status, body = self._transport(
                "POST",
                f"{self._base}/oauth/token",
                {
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                b"grant_type=client_credentials",
            )
            data = decode(body)
            if (
                status != 200
                or not isinstance(data, dict)
                or "access_token" not in data
            ):
                raise ProvisionError(
                    f"Aura refused the client credentials (HTTP {status}). "
                    "Check aura_client_id and aura_client_secret in the vault."
                )
            self._token = str(data["access_token"])
        return self._token

    def _call(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:  # noqa: ANN401 - parsed JSON
        headers = {
            "Authorization": f"Bearer {self._bearer()}",
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        status, raw = self._transport(method, f"{self._base}{path}", headers, body)
        data = decode(raw)
        if status >= 400:
            raise ProvisionError(
                f"Aura {method} {path} failed (HTTP {status}): {_message(data)}"
            )
        return data

    # --- operations ----------------------------------------------------------

    def tenant_id(self) -> str:
        """The account's tenant. Aura calls these projects in newer consoles."""
        tenants = self._call("GET", "/v1/tenants").get("data", [])
        if not tenants:
            raise ProvisionError(
                "the Aura account has no tenant; create a project in the console"
            )
        return str(tenants[0]["id"])

    def find_instance(self, name: str) -> Instance | None:
        for entry in self._call("GET", "/v1/instances").get("data", []):
            if entry.get("name") == name:
                return self.get_instance(str(entry["id"]))
        return None

    def get_instance(self, instance_id: str) -> Instance:
        data = self._call("GET", f"/v1/instances/{instance_id}").get("data", {})
        return Instance(
            id=str(data["id"]),
            name=str(data["name"]),
            status=str(data.get("status", "unknown")),
            uri=data.get("connection_url"),
            username=None,
            password=None,
        )

    def create_instance(
        self,
        name: str,
        *,
        tenant_id: str | None = None,
        instance_type: str = "free-db",
        cloud_provider: str = "gcp",
        region: str = "europe-west1",
        memory: str = "1GB",
        version: str = "5",
    ) -> Instance:
        """Create an instance. The returned password exists nowhere else."""
        data = self._call(
            "POST",
            "/v1/instances",
            {
                "name": name,
                "tenant_id": tenant_id or self.tenant_id(),
                "type": instance_type,
                "cloud_provider": cloud_provider,
                "region": region,
                "memory": memory,
                "version": version,
            },
        ).get("data", {})
        return Instance(
            id=str(data["id"]),
            name=name,
            status=str(data.get("status", "creating")),
            uri=data.get("connection_url"),
            username=data.get("username"),
            password=data.get("password"),
        )

    def wait_until_running(self, instance_id: str, *, timeout: float = 900) -> Instance:
        deadline = time.monotonic() + timeout
        while True:
            instance = self.get_instance(instance_id)
            if instance.status == "running" and instance.uri:
                return instance
            if time.monotonic() >= deadline:
                raise ProvisionError(
                    f"Aura instance {instance.name} is still {instance.status} after "
                    f"{int(timeout)}s. It may finish on its own; re-run to pick it up."
                )
            self._sleep(10)

    def delete_instance(self, instance_id: str) -> None:
        self._call("DELETE", f"/v1/instances/{instance_id}")


def _message(data: Any) -> str:  # noqa: ANN401 - parsed JSON
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(e.get("message", e)) for e in errors)
        return str(data.get("error") or data.get("message") or data)
    return str(data)
