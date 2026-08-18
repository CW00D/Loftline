"""The SOPS with age vault adapter (ADR-011).

SOPS encrypts leaf values and leaves keys, document structure and anything the
creation rule excludes in plaintext. With `encrypted_regex: '^value$'`, both
`list_paths` and `acquired_at` read the *encrypted* file directly: no key, no
`sops` binary, no secret in memory. Only `get` decrypts, and only one extracted
value at a time.

Plaintext never touches disk. `set` passes the value in the argument vector to
`sops set`, so there is no decrypt-edit-encrypt round trip through a temporary
file. The trade is that the value is briefly visible in the process table of
the local machine, which is the lesser exposure of the two.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .errors import VaultError
from .vault import VaultIndex

VALUE_KEY = "value"
ACQUIRED_KEY = "acquired_at"
METADATA_KEY = "sops"


class SopsAgeVault:
    """A vault held in one SOPS-encrypted YAML file."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = Path(vault_path)

    # --- operations that never decrypt --------------------------------------

    def list_paths(self) -> list[str]:
        """Every path holding a value, read from the ciphertext."""
        return sorted(self._walk(self._document()))

    def acquired_at(self, path: str) -> datetime | None:
        """When the value at `path` was stored, read from the ciphertext."""
        node = self._node(self._document(), path)
        return _parse_timestamp(node.get(ACQUIRED_KEY), path)

    def index(self) -> VaultIndex:
        """Paths and timestamps in one read, with nothing decrypted."""
        document = self._document()
        return VaultIndex.from_mapping(
            {
                path: _parse_timestamp(
                    self._node(document, path).get(ACQUIRED_KEY), path
                )
                for path in sorted(self._walk(document))
            }
        )

    # --- the only operation that decrypts ------------------------------------

    def get(self, path: str) -> str:
        """The value at one path. Decrypts exactly one leaf."""
        _check_path(path)
        self._node(
            self._document(), path
        )  # fail on an unknown path before shelling out
        extract = "".join(f'["{segment}"]' for segment in path.split("/")) + '["value"]'
        completed = self._run(["-d", "--extract", extract, str(self.vault_path)])
        if completed.returncode != 0:
            raise VaultError(
                f"sops could not decrypt {path} from {self.vault_path}: "
                f"{completed.stderr.strip()}"
            )
        return completed.stdout.rstrip("\n")

    # --- writing -------------------------------------------------------------

    def set(self, path: str, value: str, *, now: datetime | None = None) -> None:
        """Store a value and stamp its acquisition time, in place."""
        _check_path(path)
        if not value:
            raise VaultError(f"refusing to store an empty value at {path}")

        stamped = json.dumps(
            {
                VALUE_KEY: value,
                ACQUIRED_KEY: (now or datetime.now(UTC))
                .astimezone(UTC)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        index = "".join(f'["{segment}"]' for segment in path.split("/"))
        completed = self._run(["set", str(self.vault_path), index, stamped])
        if completed.returncode != 0:
            # The stderr of a failed `sops set` can echo the argument vector,
            # which carries the value. It is never reproduced here.
            raise VaultError(
                f"sops could not write {path} to {self.vault_path} "
                f"(exit status {completed.returncode}). Run `loftline doctor`."
            )

    # --- internals -----------------------------------------------------------

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        sops = shutil.which("sops")
        if sops is None:
            raise VaultError(
                "sops is not on PATH, so no value can be read or written. "
                "Run `loftline doctor`."
            )
        return subprocess.run(
            [sops, *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def _document(self) -> dict[str, Any]:
        try:
            text = self.vault_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise VaultError(
                f"the vault file {self.vault_path} does not exist"
            ) from exc
        except OSError as exc:
            raise VaultError(
                f"the vault file {self.vault_path} could not be read: {exc}"
            ) from exc
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise VaultError(
                f"the vault file {self.vault_path} is not valid YAML: {exc}"
            ) from exc
        if document is None:
            return {}
        if not isinstance(document, dict):
            raise VaultError(f"the vault file {self.vault_path} must contain a mapping")
        return document

    def _walk(self, document: dict[str, Any], prefix: str = "") -> list[str]:
        paths: list[str] = []
        for key, node in document.items():
            if not prefix and key == METADATA_KEY:
                continue  # SOPS's own metadata block, not a credential
            if not isinstance(node, dict):
                continue
            path = f"{prefix}/{key}" if prefix else str(key)
            if VALUE_KEY in node:
                paths.append(path)
            else:
                paths.extend(self._walk(node, path))
        return paths

    def _node(self, document: dict[str, Any], path: str) -> dict[str, Any]:
        _check_path(path)
        node: Any = document
        for segment in path.split("/"):
            if not isinstance(node, dict) or segment not in node:
                raise VaultError(f"{path} is not in the vault at {self.vault_path}")
            node = node[segment]
        if not isinstance(node, dict) or VALUE_KEY not in node:
            raise VaultError(f"{path} is not in the vault at {self.vault_path}")
        return node


def _check_path(path: str) -> None:
    segments = path.split("/")
    if len(segments) < 2 or not all(segments):
        raise VaultError(
            f"{path!r} is not a vault path. Paths look like loftline/render/api_key"
        )


def _parse_timestamp(raw: Any, path: str) -> datetime | None:  # noqa: ANN401 - parsed YAML
    if raw is None:
        return None
    if isinstance(raw, datetime):
        stamped = raw
    elif isinstance(raw, str):
        try:
            stamped = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise VaultError(
                f"acquired_at for {path} is not an ISO 8601 timestamp: {raw!r}"
            ) from exc
    else:
        raise VaultError(f"acquired_at for {path} is not a timestamp: {raw!r}")
    return stamped if stamped.tzinfo else stamped.replace(tzinfo=UTC)
