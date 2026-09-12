"""Render: finding services, setting their environment, triggering deploys.

Services are created by Render from the repository's blueprint, connected
once by hand (roadmap Step 8, the cheap route). Loftline finds them by name
and does three things: writes environment variables one at a time, triggers a
deploy, and waits for it.

Unlike GitHub secrets, Render environment variables can be read back. That
makes a service the readable record of a project's produced and derived
values, which is what lets a re-run recover an instance password or reuse a
JWT secret rather than regenerating it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ..errors import ProvisionError
from . import Transport, decode, urllib_transport

BASE_URL = "https://api.render.com/v1"


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    url: str | None
    branch: str | None

    @property
    def host(self) -> str | None:
        """The service's own hostname, which a CNAME points at."""
        if not self.url:
            return None
        return self.url.split("://", 1)[-1].rstrip("/")


@dataclass(frozen=True)
class CustomDomain:
    id: str
    name: str
    status: str  # Render's verificationStatus: "verified" | "unverified"


class RenderClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: Transport | None = None,
        base_url: str = BASE_URL,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key
        self._transport = transport or urllib_transport
        self._base = base_url.rstrip("/")
        self._sleep = sleep

    def _call(
        self,
        method: str,
        path: str,
        payload: Any = None,  # noqa: ANN401 - JSON in
        *,
        redact: str | None = None,
    ) -> Any:  # noqa: ANN401 - JSON out
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        status, raw = self._transport(method, f"{self._base}{path}", headers, body)
        data = decode(raw)
        if status >= 400:
            detail = str(data)
            if redact:
                detail = detail.replace(redact, "[value redacted]")
            raise ProvisionError(
                f"Render {method} {path} failed (HTTP {status}): {detail}"
            )
        return data

    def find_service(self, name: str) -> Service | None:
        for entry in self._call("GET", f"/services?name={quote(name)}&limit=5"):
            service = entry.get("service", entry)
            if service.get("name") == name:
                details = service.get("serviceDetails", {})
                return Service(
                    id=str(service["id"]),
                    name=name,
                    url=details.get("url"),
                    branch=service.get("branch"),
                )
        return None

    def env_vars(self, service_id: str) -> dict[str, str]:
        """Every variable on the service, with its value. Never log the result."""
        out: dict[str, str] = {}
        for entry in self._call("GET", f"/services/{service_id}/env-vars?limit=100"):
            var = entry.get("envVar", entry)
            out[str(var["key"])] = str(var.get("value", ""))
        return out

    def set_env_var(self, service_id: str, key: str, value: str) -> None:
        self._call(
            "PUT",
            f"/services/{service_id}/env-vars/{quote(key)}",
            {"value": value},
            redact=value,
        )

    def custom_domains(self, service_id: str) -> list[CustomDomain]:
        """The names the blueprint declared on the service, with Render's view
        of whether DNS proves them."""
        out: list[CustomDomain] = []
        for entry in self._call(
            "GET", f"/services/{service_id}/custom-domains?limit=50"
        ):
            domain = entry.get("customDomain", entry)
            out.append(
                CustomDomain(
                    id=str(domain["id"]),
                    name=str(domain["name"]),
                    status=str(domain.get("verificationStatus", "unverified")),
                )
            )
        return out

    def verify_custom_domain(self, service_id: str, domain_id: str) -> str:
        """Ask Render to look the name up now. Returns the new status."""
        data = self._call(
            "POST",
            f"/services/{service_id}/custom-domains/{quote(domain_id)}/verify",
            {},
        )
        domain = data.get("customDomain", data) if isinstance(data, dict) else {}
        return str(domain.get("verificationStatus", "unverified"))

    def trigger_deploy(self, service_id: str) -> str:
        data = self._call(
            "POST", f"/services/{service_id}/deploys", {"clearCache": "do_not_clear"}
        )
        return str(data.get("id", ""))

    def wait_for_deploy(
        self, service_id: str, deploy_id: str, *, timeout: float = 900
    ) -> str:
        """Block until the deploy settles. Returns Render's final status."""
        deadline = time.monotonic() + timeout
        while True:
            data = self._call("GET", f"/services/{service_id}/deploys/{deploy_id}")
            status = str(data.get("status", "unknown"))
            if status in {
                "live",
                "build_failed",
                "update_failed",
                "canceled",
                "deactivated",
            }:
                return status
            if time.monotonic() >= deadline:
                raise ProvisionError(
                    f"Render deploy {deploy_id} is still {status} after {int(timeout)}s"
                )
            self._sleep(10)
