"""The vault adapter boundary.

Four operations, per ADR-010 and the adapter section of credentials.md. Any
backend implementing them is acceptable; `vault_sops.py` is the one chosen in
ADR-011.

`VaultIndex` is the only part of the vault the resolver ever sees: paths and
acquisition timestamps, never values. Both are available from the encrypted
file without decrypting anything, which is what makes the resolver pure by
construction rather than by discipline.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class VaultAdapter(Protocol):
    """A credential store. Values cross this boundary in only one direction."""

    def list_paths(self) -> list[str]:
        """Every path holding a value. Never decrypts."""

    def get(self, path: str) -> str:
        """The value at one path. The only operation that decrypts."""

    def set(self, path: str, value: str) -> None:
        """Store a value and stamp its acquisition time."""

    def acquired_at(self, path: str) -> datetime | None:
        """When the value at one path was stored. Never decrypts."""


@dataclass(frozen=True)
class VaultIndex:
    """Paths present in the vault, each with its acquisition time if known."""

    entries: Mapping[str, datetime | None] = field(default_factory=dict)

    @classmethod
    def from_paths(cls, paths: Iterable[str]) -> VaultIndex:
        """An index with no timestamps, as in the documented list-of-paths form."""
        return cls({path: None for path in paths})

    @classmethod
    def from_mapping(cls, entries: Mapping[str, datetime | None]) -> VaultIndex:
        return cls(dict(entries))

    @classmethod
    def from_adapter(cls, adapter: VaultAdapter) -> VaultIndex:
        """Build an index using only the two operations that never decrypt."""
        return cls({path: adapter.acquired_at(path) for path in adapter.list_paths()})

    def acquired_at(self, path: str) -> datetime | None:
        return self.entries.get(path)

    def __contains__(self, path: object) -> bool:
        return path in self.entries

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self.entries))

    def __len__(self) -> int:
        return len(self.entries)


def as_index(
    source: VaultIndex | Mapping[str, datetime | None] | Iterable[str],
) -> VaultIndex:
    """Accept an index, a path-to-timestamp mapping, or a plain list of paths."""
    if isinstance(source, VaultIndex):
        return source
    if isinstance(source, Mapping):
        return VaultIndex.from_mapping(source)
    return VaultIndex.from_paths(source)
