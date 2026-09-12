"""The Loftline dashboard, from the command line.

The dashboard holds intent, never a credential: which projects exist, what
the tooling last reported about them, and who should be a collaborator on
each repository. `loftline sync` runs on an administrator's machine, where
the vault and the GitHub token are, and reconciles the two directions:

- up: the project's spec (names and choices, never values) and its live
  status, one health check per environment;
- down: the collaborators the team asked for, written into the project's
  Terraform variables for the administrator to apply, and confirmed against
  the repository's actual collaborator list.

Everything here speaks HTTP through the same injectable transport as the
vendor clients, so tests exercise the exact requests without a network.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .errors import LoftlineError
from .models import Spec
from .providers import Transport, decode, urllib_transport
from .provision import hostnames, service_name, web_service_name

# Where the dashboard's API lives. Production, when it exists, is
# https://api.loftline.org; until then the staging site is the site.
DEFAULT_SITE = "https://api.staging.loftline.org"


class SiteError(LoftlineError):
    pass


class SiteClient:
    def __init__(
        self,
        token: str,
        *,
        site: str = DEFAULT_SITE,
        transport: Transport | None = None,
    ) -> None:
        self._token = token
        self._base = site.rstrip("/")
        self._transport = transport or urllib_transport

    def _call(self, method: str, path: str, payload: Any = None) -> Any:  # noqa: ANN401
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
        if status == 401:
            raise SiteError(
                "The dashboard refused the token. Create a new one on its Settings "
                "page and run `loftline login` again."
            )
        if status >= 400:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            raise SiteError(
                f"Dashboard {method} {path} failed (HTTP {status}): {detail}"
            )
        return data

    def me(self) -> dict[str, Any]:
        return dict(self._call("GET", "/me"))

    def push_project(
        self,
        name: str,
        *,
        repository: str | None,
        spec: dict[str, Any],
        status: dict[str, Any],
        org_slug: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            self._call(
                "PUT",
                f"/sync/projects/{name}",
                {
                    "repository": repository,
                    "spec": spec,
                    "status": status,
                    "org_slug": org_slug,
                },
            )
        )

    def pull_project(self, name: str, *, org_slug: str | None = None) -> dict[str, Any]:
        query = f"?org_slug={org_slug}" if org_slug else ""
        return dict(self._call("GET", f"/sync/projects/{name}{query}"))

    def mark_applied(
        self, name: str, logins: list[str], *, org_slug: str | None = None
    ) -> None:
        query = f"?org_slug={org_slug}" if org_slug else ""
        self._call(
            "POST", f"/sync/projects/{name}/applied{query}", {"github_logins": logins}
        )


class SiteLike(Protocol):
    def push_project(
        self,
        name: str,
        *,
        repository: str | None,
        spec: dict[str, Any],
        status: dict[str, Any],
        org_slug: str | None = None,
    ) -> dict[str, Any]: ...
    def pull_project(
        self, name: str, *, org_slug: str | None = None
    ) -> dict[str, Any]: ...
    def mark_applied(
        self, name: str, logins: list[str], *, org_slug: str | None = None
    ) -> None: ...


# --- what goes up: the spec and the live status -------------------------------


def spec_summary(spec: Spec) -> dict[str, Any]:
    """The spec as the dashboard shows it. Names and choices only."""
    return {
        "database": spec.database,
        "mobile": spec.mobile,
        "web": spec.web,
        "notifications": spec.notifications,
        "payments": list(spec.payments),
        "hosting": {
            "api": spec.hosting.api,
            "web": spec.hosting.web,
            "dns": spec.hosting.dns,
        },
        "domain": spec.domain,
        "environments": list(spec.environments),
    }


def environment_urls(spec: Spec, environment: str) -> dict[str, str | None]:
    """Where each component answers, by domain if there is one, else on Render."""
    names = hostnames(spec, environment)
    api = (
        f"https://{names['api']}"
        if "api" in names
        else (f"https://{service_name(spec.project_name, environment)}.onrender.com")
    )
    web: str | None = None
    if spec.web and spec.hosting.web == "render":
        web = (
            f"https://{names['web']}"
            if "web" in names
            else f"https://{web_service_name(spec.project_name, environment)}"
            ".onrender.com"
        )
    return {"api_url": api, "web_url": web}


def http_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return bool(
                response.status == 200
                and b'"db":true' in response.read().replace(b" ", b"")
            )
    except (urllib.error.URLError, OSError):
        return False


def live_status(
    spec: Spec,
    *,
    health: Callable[[str], bool] = http_health,
    now: datetime | None = None,
) -> dict[str, Any]:
    checked = (now or datetime.now(UTC)).isoformat()
    environments = []
    for environment in spec.environments:
        urls = environment_urls(spec, environment)
        environments.append(
            {
                "name": environment,
                **urls,
                "healthy": health(f"{urls['api_url']}/health"),
                "checked_at": checked,
            }
        )
    return {"environments": environments}


# --- what comes down: collaborators, into Terraform ----------------------------

COLLABORATORS_BLOCK = re.compile(r"^collaborators\s*=\s*\{.*?^\}\n?", re.M | re.S)


def write_collaborators(tfvars: Path, collaborators: dict[str, str]) -> None:
    """Set the `collaborators` map in a project's terraform.tfvars, replacing
    the previous one. The Terraform module already takes this input."""
    text = tfvars.read_text(encoding="utf-8") if tfvars.exists() else ""
    text = COLLABORATORS_BLOCK.sub("", text).rstrip("\n") + "\n"
    if collaborators:
        lines = "\n".join(
            f'  "{login}" = "{permission}"'
            for login, permission in sorted(collaborators.items())
        )
        text += (
            "\n# Collaborators the team asked for on the Loftline dashboard.\n"
            "# Written by `loftline sync`; `terraform apply` makes them real.\n"
            f"collaborators = {{\n{lines}\n}}\n"
        )
    tfvars.write_text(text, encoding="utf-8")


def github_collaborators(repository: str) -> set[str]:
    """Logins currently on the repository, through `gh`. Empty if it cannot be
    asked, which reads as 'pending' rather than as a claim."""
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/collaborators",
                "--paginate",
                "-q",
                ".[].login",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    if result.returncode != 0:
        return set()
    return {line.strip().lower() for line in result.stdout.splitlines() if line.strip()}


@dataclass(frozen=True)
class SyncReport:
    project: str
    repository: str | None
    environments: tuple[tuple[str, bool], ...]
    wanted: tuple[str, ...] = ()
    applied: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    tfvars: Path | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def sync_project(
    spec: Spec,
    site: SiteLike,
    *,
    repository: str | None,
    project_dir: Path | None,
    org_slug: str | None = None,
    health: Callable[[str], bool] = http_health,
    on_github: Callable[[str], set[str]] = github_collaborators,
) -> SyncReport:
    """Push the project up, pull its collaborators down, and say what is left
    for the administrator to apply."""
    status = live_status(spec, health=health)
    site.push_project(
        spec.project_name,
        repository=repository,
        spec=spec_summary(spec),
        status=status,
        org_slug=org_slug,
    )
    pulled = site.pull_project(spec.project_name, org_slug=org_slug)
    wanted = {
        str(c["github_login"]).lower(): str(c.get("permission", "push"))
        for c in pulled.get("collaborators", [])
    }
    repo = repository or pulled.get("repository")

    tfvars: Path | None = None
    notes: list[str] = []
    if project_dir is not None:
        tfvars = project_dir / "infra" / "terraform.tfvars"
        if tfvars.parent.is_dir():
            write_collaborators(tfvars, wanted)
        else:
            tfvars = None
            notes.append(
                f"{project_dir} has no infra/ directory; collaborators not written"
            )

    present = on_github(repo) if repo and wanted else set()
    applied = sorted(login for login in wanted if login in present)
    pending = sorted(login for login in wanted if login not in present)
    site.mark_applied(spec.project_name, applied, org_slug=org_slug)
    if pending and tfvars is not None:
        notes.append(
            f"run `terraform apply` in {tfvars.parent} to add: {', '.join(pending)}"
        )

    return SyncReport(
        project=spec.project_name,
        repository=repo,
        environments=tuple(
            (e["name"], bool(e["healthy"])) for e in status["environments"]
        ),
        wanted=tuple(sorted(wanted)),
        applied=tuple(applied),
        pending=tuple(pending),
        tfvars=tfvars,
        notes=tuple(notes),
    )
