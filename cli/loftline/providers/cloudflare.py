"""Cloudflare: the DNS records that point a product's domain at its hosting.

The zone must already be on Cloudflare, which is a one-off per domain and a
person's job: register it there, or point its nameservers at Cloudflare.
From then on every record is created or corrected here, never by hand.

Records are created DNS-only, not proxied. The host terminates TLS itself
and verifies ownership by looking the name up; Cloudflare's proxy in front
of that breaks both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ..errors import ProvisionError
from . import Transport, decode, urllib_transport

BASE_URL = "https://api.cloudflare.com/client/v4"


@dataclass(frozen=True)
class Record:
    id: str
    name: str
    type: str
    content: str


class CloudflareClient:
    def __init__(
        self,
        api_token: str,
        *,
        transport: Transport | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._token = api_token
        self._transport = transport or urllib_transport
        self._base = base_url.rstrip("/")

    def _call(self, method: str, path: str, payload: Any = None) -> Any:  # noqa: ANN401
        import json

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        status, raw = self._transport(method, f"{self._base}{path}", headers, body)
        data = decode(raw)
        errors = data.get("errors") if isinstance(data, dict) else None
        if status >= 400 or (isinstance(data, dict) and data.get("success") is False):
            detail = (
                "; ".join(str(e.get("message", e)) for e in errors) if errors else data
            )
            raise ProvisionError(
                f"Cloudflare {method} {path} failed (HTTP {status}): {detail}"
            )
        return data.get("result") if isinstance(data, dict) else data

    def find_zone(self, domain: str) -> str | None:
        """The zone id for an apex domain, or None if Cloudflare does not hold it."""
        for zone in self._call("GET", f"/zones?name={quote(domain)}") or []:
            if zone.get("name") == domain:
                return str(zone["id"])
        return None

    def find_record(self, zone_id: str, name: str, type_: str) -> Record | None:
        query = f"/zones/{zone_id}/dns_records?name={quote(name)}&type={type_}"
        for entry in self._call("GET", query) or []:
            if entry.get("name") == name:
                return Record(
                    id=str(entry["id"]),
                    name=name,
                    type=str(entry.get("type", type_)),
                    content=str(entry.get("content", "")),
                )
        return None

    def upsert_cname(self, zone_id: str, name: str, target: str) -> Record:
        """Make `name` a DNS-only CNAME to `target`, creating or correcting it."""
        payload = {
            "type": "CNAME",
            "name": name,
            "content": target,
            "ttl": 1,  # automatic
            "proxied": False,
            "comment": "Created by Loftline",
        }
        existing = self.find_record(zone_id, name, "CNAME")
        if existing is not None:
            if existing.content == target:
                return existing
            self._call("PATCH", f"/zones/{zone_id}/dns_records/{existing.id}", payload)
            return Record(existing.id, name, "CNAME", target)
        created = self._call("POST", f"/zones/{zone_id}/dns_records", payload)
        return Record(str(created["id"]), name, "CNAME", target)
