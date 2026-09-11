"""Thin clients for the vendors Loftline provisions against.

Each client speaks one vendor's HTTP API through an injectable transport, so
tests exercise the exact requests without a network. No client logs a value
and every error names the vendor, the operation and what the vendor said.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

# (method, url, headers, body) -> (status, body)
Transport = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]]


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, data=body, headers=dict(headers), method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def decode(body: bytes) -> Any:  # noqa: ANN401 - parsed JSON
    """JSON if it parses, else the text. Vendors err in both."""
    if not body:
        return {}
    try:
        return json.loads(body)
    except ValueError:
        return body.decode(errors="replace")
