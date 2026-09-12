"""Stripe: registering a webhook endpoint per payment module.

A payment module's webhook secret is produced when its endpoint is
registered. Stripe returns the signing secret exactly once, in the create
response, and never again; the provisioner stores it the moment it arrives.
An endpoint whose secret was lost can only be deleted and re-created.

Stripe's API is form-encoded, not JSON, and is authenticated with the
account's secret key as a bearer token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ..errors import ProvisionError
from . import Transport, decode, urllib_transport

BASE_URL = "https://api.stripe.com"


@dataclass(frozen=True)
class WebhookEndpoint:
    id: str
    url: str
    secret: str | None  # only on the create response, never again


class StripeClient:
    def __init__(
        self,
        secret_key: str,
        *,
        transport: Transport | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._key = secret_key
        self._transport = transport or urllib_transport
        self._base = base_url.rstrip("/")

    def _call(
        self, method: str, path: str, fields: list[tuple[str, str]] | None = None
    ) -> Any:  # noqa: ANN401 - JSON out
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        body = None
        if fields is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(fields).encode()
        status, raw = self._transport(method, f"{self._base}{path}", headers, body)
        data = decode(raw)
        if status >= 400:
            message = (
                data.get("error", {}).get("message", data)
                if isinstance(data, dict)
                else data
            )
            raise ProvisionError(
                f"Stripe {method} {path} failed (HTTP {status}): {message}"
            )
        return data

    def find_webhook_endpoint(self, url: str) -> WebhookEndpoint | None:
        data = self._call("GET", "/v1/webhook_endpoints?limit=100")
        for entry in data.get("data", []):
            if entry.get("url") == url:
                return WebhookEndpoint(id=str(entry["id"]), url=url, secret=None)
        return None

    def create_webhook_endpoint(self, url: str, events: list[str]) -> WebhookEndpoint:
        fields = [("url", url), ("description", "Created by Loftline")]
        fields += [(f"enabled_events[{i}]", event) for i, event in enumerate(events)]
        data = self._call("POST", "/v1/webhook_endpoints", fields)
        return WebhookEndpoint(id=str(data["id"]), url=url, secret=data.get("secret"))
