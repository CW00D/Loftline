"""Human readable rendering of a resolution.

Plain text, deterministic, and free of anything that could carry a value: the
resolution holds paths, secret names and instructions, and nothing else.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import Spec
from .resolve import Resolution

INDENT = "  "


def render_plan(
    spec: Spec,
    resolution: Resolution,
    vault_path: Path,
    spec_path: Path,
    path_count: int,
) -> str:
    lines: list[str] = ["", f"Loftline plan for {spec.project_name}", ""]
    lines += _summary(spec, vault_path, spec_path, path_count, resolution)
    lines += _inject(resolution)
    lines += _derive(resolution)
    lines += _request(resolution)
    lines += _defer(resolution)
    lines += [
        "Nothing has been created, written or fetched. `plan` has no side effects.",
        "",
    ]
    return "\n".join(lines)


def _summary(
    spec: Spec,
    vault_path: Path,
    spec_path: Path,
    path_count: int,
    resolution: Resolution,
) -> list[str]:
    return [
        f"{INDENT}spec          {spec_path}",
        f"{INDENT}package       {spec.package_name}",
        f"{INDENT}database      {spec.database}",
        f"{INDENT}environments  {', '.join(spec.environments)}",
        f"{INDENT}features      {', '.join(resolution.features) or 'none'}",
        f"{INDENT}vault         {vault_path}",
        f"{INDENT}              {path_count} path(s) read from the encrypted file, "
        "no decryption performed",
        "",
    ]


def _heading(title: str, count: int, subtitle: str) -> list[str]:
    return [f"{title} ({count}) {subtitle}", ""]


def _inject(resolution: Resolution) -> list[str]:
    lines = _heading(
        "Inject",
        len(resolution.inject),
        "held already, read from the vault at provisioning",
    )
    if not resolution.inject:
        lines += [f"{INDENT}nothing", ""]
        return lines
    for entry in resolution.inject:
        lines.append(
            f"{INDENT}{entry.name}  ->  {entry.github_secret}  "
            f"[{', '.join(entry.environments) or 'no environment'}]"
        )
        lines.append(f"{INDENT}{INDENT}vault path  {entry.vault_path}")
        if entry.expires_at is not None:
            lines.append(f"{INDENT}{INDENT}expires     {_stamp(entry.expires_at)}")
    lines.append("")
    return lines


def _derive(resolution: Resolution) -> list[str]:
    lines = _heading(
        "Generate", len(resolution.derive), "derived automatically from something held"
    )
    if not resolution.derive:
        lines += [f"{INDENT}nothing", ""]
        return lines
    for entry in resolution.derive:
        lines.append(
            f"{INDENT}{entry.name}  ->  {entry.github_secret}  "
            f"[{', '.join(entry.environments) or 'no environment'}]"
        )
        lines.append(f"{INDENT}{INDENT}derivation  {entry.derivation}")
    lines.append("")
    return lines


def _request(resolution: Resolution) -> list[str]:
    lines = _heading(
        "Acquire",
        len(resolution.request),
        "you must go and get these, once per lifetime",
    )
    if not resolution.request:
        lines += [f"{INDENT}nothing, everything needed is already held", ""]
        return lines
    for entry in resolution.request:
        lines.append(f"{INDENT}{entry.name}  ({entry.vendor}, {entry.reason})")
        lines.append(f"{INDENT}{INDENT}store at  {entry.vault_path}")
        if entry.acquire:
            lines.append(f"{INDENT}{INDENT}how to acquire:")
            lines += [
                f"{INDENT}{INDENT}{INDENT}{step}"
                for step in entry.acquire.rstrip().splitlines()
            ]
        else:
            lines.append(
                f"{INDENT}{INDENT}no acquire instructions recorded. Add them to "
                "credentials.yml once you have worked them out."
            )
        lines.append("")
    return lines


def _defer(resolution: Resolution) -> list[str]:
    lines = _heading(
        "Create during provisioning",
        len(resolution.defer),
        "these cannot exist until provisioning creates them",
    )
    if not resolution.defer:
        lines += [f"{INDENT}nothing", ""]
        return lines
    for entry in resolution.defer:
        lines.append(
            f"{INDENT}{entry.name}  ->  {entry.github_secret}  "
            f"[{', '.join(entry.environments) or 'no environment'}]"
        )
        lines.append(f"{INDENT}{INDENT}produced by  {entry.produced_by}")
    lines.append("")
    return lines


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")
