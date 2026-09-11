"""The secret writer: a resolution becomes environment secrets.

This is the one place a credential value is read from the vault, and it goes
straight into a sink that stores it encrypted. Values cross this module as
function arguments and stdin. They are never logged, never returned to the
caller, never written to disk.

Sinks are pluggable. GitHub environment secrets are the first. Render's
environment variables attach to a service, which does not exist until Step 8
provisions it, so that sink arrives with the provisioner.
"""

from __future__ import annotations

import secrets as stdlib_secrets
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .errors import SecretsError
from .resolve import Derive, Inject, Resolution
from .vault import VaultAdapter

# --- derivations -------------------------------------------------------------
# A derivable descriptor names one of these. The name is a contract: it says
# exactly how the value is made, and the registry is the only place that
# knowledge lives. An unknown name is a loud error, not a guess.

DERIVATIONS: Mapping[str, Callable[[], str]] = {
    # 32 random bytes, base64url encoded: 43 characters, no padding. Meets
    # RFC 7518's minimum for an HMAC-SHA256 key with room to spare.
    "random_bytes_32_base64": lambda: stdlib_secrets.token_urlsafe(32),
}


def derive(
    derivation: str, registry: Mapping[str, Callable[[], str]] = DERIVATIONS
) -> str:
    try:
        return registry[derivation]()
    except KeyError:
        known = ", ".join(sorted(registry))
        raise SecretsError(
            f"no derivation named {derivation!r}. Known: {known}. "
            "Add it to DERIVATIONS in secrets.py, or correct the descriptor."
        ) from None


# --- sinks -------------------------------------------------------------------


class SecretSink(Protocol):
    """Somewhere secrets are stored, keyed by name within an environment."""

    def preflight(self) -> None:
        """Raise SecretsError if the sink cannot be used at all."""

    def ensure_environment(self, environment: str) -> None:
        """Create the environment if it does not exist. Idempotent."""

    def exists(self, name: str, environment: str) -> bool:
        """Whether a secret of this name is already set in the environment."""

    def write(self, name: str, environment: str, value: str) -> None:
        """Store the value. Overwrites."""


class GitHubSink:
    """GitHub environment secrets, through the `gh` CLI.

    `gh secret set` reads the value from stdin and encrypts it locally before
    sending, so the value is never an argument and never on the wire in clear.
    """

    def __init__(
        self,
        repo: str,
        run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        if repo.count("/") != 1:
            raise SecretsError(f"--repo must be OWNER/NAME, not {repo!r}")
        self.repo = repo
        self._run = run or subprocess.run

    def _gh(
        self, *args: str, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        gh = shutil.which("gh")
        if gh is None:
            raise SecretsError(
                "gh is not on PATH. Install the GitHub CLI and run `gh auth login`."
            )
        return self._run(
            [gh, *args], input=stdin, capture_output=True, text=True, check=False
        )

    def preflight(self) -> None:
        status = self._gh("auth", "status")
        if status.returncode != 0:
            raise SecretsError(
                "gh is not logged in. Run `gh auth login` and try again.\n"
                + status.stderr.strip()
            )
        view = self._gh("repo", "view", self.repo, "--json", "name")
        if view.returncode != 0:
            raise SecretsError(
                f"gh cannot see {self.repo}: {view.stderr.strip()}\n"
                "Check the name, and that the logged-in account has access."
            )

    def ensure_environment(self, environment: str) -> None:
        result = self._gh(
            "api", "--method", "PUT", f"repos/{self.repo}/environments/{environment}"
        )
        if result.returncode != 0:
            raise SecretsError(
                f"could not create environment {environment} in {self.repo}: "
                f"{result.stderr.strip()}"
            )

    def exists(self, name: str, environment: str) -> bool:
        result = self._gh(
            "secret",
            "list",
            "--repo",
            self.repo,
            "--env",
            environment,
            "--json",
            "name",
        )
        if result.returncode != 0:
            raise SecretsError(
                f"could not list secrets for {environment} in {self.repo}: "
                f"{result.stderr.strip()}"
            )
        return f'"name":"{name}"' in result.stdout.replace(" ", "")

    def write(self, name: str, environment: str, value: str) -> None:
        result = self._gh(
            "secret",
            "set",
            name,
            "--repo",
            self.repo,
            "--env",
            environment,
            stdin=value,
        )
        if result.returncode != 0:
            detail = result.stderr.strip().replace(value, "[value redacted]")
            raise SecretsError(
                f"could not write {name} to {environment} in {self.repo}: {detail}"
            )


# --- the writer --------------------------------------------------------------


@dataclass(frozen=True)
class Written:
    name: str
    github_secret: str
    environment: str
    source: str  # "vault" | "generated" | "kept"


@dataclass(frozen=True)
class WriteReport:
    """What happened. Carries names and places, never values."""

    written: tuple[Written, ...] = ()
    outstanding: tuple[str, ...] = ()  # requested credentials, not yet acquired
    deferred: tuple[str, ...] = ()  # produced at provisioning, not this step
    environments: tuple[str, ...] = ()
    partial: bool = False


def write_secrets(
    resolution: Resolution,
    vault: VaultAdapter,
    sink: SecretSink,
    *,
    partial: bool = False,
    rotate: bool = False,
    derivations: Mapping[str, Callable[[], str]] = DERIVATIONS,
) -> WriteReport:
    """Write every injectable and derivable credential in the resolution.

    Refuses when the resolution still has credentials to acquire, unless
    `partial` is set, in which case it writes what it can and reports the rest.
    Derived values are generated once per environment and kept on later runs
    unless `rotate` is set.
    """
    outstanding = tuple(entry.name for entry in resolution.request)
    if outstanding and not partial:
        names = ", ".join(outstanding)
        raise SecretsError(
            f"{len(outstanding)} credential(s) still to acquire: {names}.\n"
            "Run `loftline plan` for the steps, `loftline vault set <name>` to "
            "store each, or pass --partial to write what is held and leave "
            "these for later."
        )

    # Every derivation must be known before anything is decrypted or written,
    # so a typo in a descriptor cannot leave the environments half done.
    for derived in resolution.derive:
        if derived.derivation not in derivations:
            derive(derived.derivation, derivations)

    targets: list[Inject | Derive] = [*resolution.inject, *resolution.derive]
    environments = sorted({env for target in targets for env in target.environments})
    for environment in environments:
        sink.ensure_environment(environment)

    written: list[Written] = []

    for held in resolution.inject:
        value = vault.get(held.vault_path)
        for environment in held.environments:
            sink.write(held.github_secret, environment, value)
            written.append(Written(held.name, held.github_secret, environment, "vault"))

    for derived in resolution.derive:
        for environment in derived.environments:
            if not rotate and sink.exists(derived.github_secret, environment):
                written.append(
                    Written(derived.name, derived.github_secret, environment, "kept")
                )
                continue
            sink.write(
                derived.github_secret,
                environment,
                derive(derived.derivation, derivations),
            )
            written.append(
                Written(derived.name, derived.github_secret, environment, "generated")
            )

    return WriteReport(
        written=tuple(written),
        outstanding=outstanding,
        deferred=tuple(entry.name for entry in resolution.defer),
        environments=tuple(environments),
        partial=partial,
    )
