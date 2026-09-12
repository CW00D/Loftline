"""Loftline as an MCP server.

A thin layer: every tool is one of the existing commands, or a read-only view
of the data those commands consume. The LLM on the other end elicits a
project's requirements, writes the spec, and calls these tools. It performs
no provisioning itself (ADR-001); the tools are deterministic and the
person approves each call.

Two rules shape the surface:

1. No tool accepts or returns a credential value. Storing one is a terminal
   command (`loftline vault set`), because anything that passes through a
   conversation is in a log. Tools return names, paths, reports and
   instructions.
2. Specs travel as YAML text, not file paths. The LLM authors the spec; the
   tool validates it and, when a project is generated, writes it into the
   project as `loftline.yml` so the project records what it was made from.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .doctor import Capability, VaultConfig, require, run_checks
from .errors import LoftlineError, SpecError
from .features import load_default_features
from .generate import generate
from .models import Spec, load_descriptors
from .report import render_plan
from .resolve import resolve
from .secrets import GitHubSink, write_secrets
from .site import DEFAULT_SITE, SiteClient, sync_project
from .vault_sops import SopsAgeVault

CREDENTIALS_FILE = Path(__file__).resolve().parents[2] / "credentials.yml"

INSTRUCTIONS = """\
Loftline scaffolds and provisions applications, credentials first. The
workflow you drive:

1. Elicit the project's requirements from the person: name, whether it needs
   a mobile app, whether it sends push notifications, which database. Use
   spec_schema for the exact questions and features for what each implies.
2. Write the spec as YAML and call plan with it. The report says which
   credentials are already held, which will be generated, which the person
   must go and get (with the steps), and which are created at provisioning.
3. For anything to acquire, give the person the printed steps and the exact
   terminal command to store it, which the plan report prints beside each
   item: `loftline vault set <credential name>`, for example
   `loftline vault set apple_team_id`. The argument is the credential's name,
   never its vault path (`loftline/apple/team_id` is a path, not a name).
   Never ask for the value in the conversation and never accept one if
   offered.
4. Call new to render the project, then secrets_write to put its credentials
   where CI reads them. Neither returns a value.

Every tool is deterministic. You choose whether to call it; you never
provision anything yourself.
"""

server = MCPServer(
    name="loftline",
    instructions=INSTRUCTIONS,
    version="0.1.0",
)


# --- helpers -----------------------------------------------------------------


def anticipated[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Loftline's own errors are anticipated failures, not crashes.

    The SDK shows the model only "Error executing tool" for an unexpected
    exception. Every LoftlineError carries a message written to be acted on,
    so it is passed through as a ToolError.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except LoftlineError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _parse_spec(spec: str) -> Spec:
    """A spec from YAML text. Invalid YAML or fields are a clear error."""
    try:
        data = yaml.safe_load(spec)
    except yaml.YAMLError as exc:
        raise SpecError(f"the spec is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError("the spec must be a YAML mapping of the spec fields")
    try:
        return Spec.model_validate(data)
    except ValueError as exc:
        raise SpecError(f"the spec is not valid:\n{exc}") from exc


def _config() -> VaultConfig:
    return VaultConfig.from_env()


def _vault(config: VaultConfig) -> SopsAgeVault:
    assert config.vault_path is not None
    return SopsAgeVault(config.vault_path)


# --- read-only: what the system is -------------------------------------------


@server.tool(
    description="The project spec's JSON schema: the questions a project answers."
)
@anticipated
def spec_schema() -> dict[str, Any]:
    return Spec.model_json_schema()


@server.tool(
    description=(
        "The feature map: for each feature, when the spec enables it and which "
        "credentials it requires or produces. Data, not code."
    )
)
@anticipated
def features() -> dict[str, Any]:
    feature_set = load_default_features()
    return {
        name: {
            "enabled_when": feature.enabled_when.model_dump(
                by_alias=True, exclude_none=True
            ),
            "requires": list(feature.requires),
            "produces": list(feature.produces),
        }
        for name, feature in sorted(feature_set.features.items())
    }


@server.tool(
    description=(
        "Every credential descriptor: vendor, scope, state, where it lives and how "
        "to acquire it. Never a value."
    )
)
@anticipated
def credentials() -> list[dict[str, Any]]:
    descriptors = load_descriptors(CREDENTIALS_FILE)
    return [
        descriptor.model_dump(exclude_none=True)
        for _, descriptor in sorted(descriptors.entries.items())
    ]


# --- the commands ------------------------------------------------------------


@server.tool(description="Check the machine's preconditions for using the vault.")
@anticipated
def doctor() -> list[dict[str, str]]:
    return [
        {"check": r.name, "status": r.status.value, "detail": r.detail}
        for r in run_checks(_config())
    ]


@server.tool(
    description="The credential paths held in the vault. Values are never read."
)
@anticipated
def vault_list() -> list[str]:
    config = _config()
    require(config, Capability.READ_INDEX)
    return _vault(config).list_paths()


@server.tool(
    description=(
        "Resolve a spec (YAML text) against the vault: what will be injected, "
        "generated, acquired (with steps) and created at provisioning. Read-only."
    )
)
@anticipated
def plan(spec: str) -> str:
    config = _config()
    require(config, Capability.READ_INDEX)
    project = _parse_spec(spec)
    descriptors = load_descriptors(CREDENTIALS_FILE)
    index = _vault(config).index()
    resolution = resolve(project, descriptors, index)
    assert config.vault_path is not None
    return render_plan(
        project, resolution, config.vault_path, Path("<spec>"), len(index)
    )


@server.tool(
    description=(
        "Generate a project from a spec (YAML text) into a directory. Resolves "
        "credentials first and fails on any without a descriptor. Provisions nothing."
    )
)
@anticipated
def new(spec: str, destination: str, force: bool = False) -> str:
    config = _config()
    require(config, Capability.READ_INDEX)
    project = _parse_spec(spec)
    descriptors = load_descriptors(CREDENTIALS_FILE)
    resolution = resolve(project, descriptors, _vault(config).index())
    target = generate(project, Path(destination), overwrite=force)
    # The project records what it was generated from.
    (target / "loftline.yml").write_text(
        yaml.safe_dump(project.model_dump(), sort_keys=False), encoding="utf-8"
    )
    held, made, get, later = (
        len(resolution.inject),
        len(resolution.derive),
        len(resolution.request),
        len(resolution.defer),
    )
    return (
        f"Generated {project.project_name} at {target}\n"
        f"features: {', '.join(resolution.features)}\n"
        f"credentials: {held} held, {made} generated, {get} to acquire, "
        f"{later} created at provisioning\n"
        "Nothing has been provisioned."
    )


@server.tool(
    description=(
        "Write a spec's credentials to a GitHub repository's environments as "
        "secrets (OWNER/NAME). Held values are decrypted one at a time and "
        "handed to gh on stdin; derived values are generated once and kept. "
        "Refuses if any credential is still to acquire unless partial is true. "
        "Returns names and places, never values."
    )
)
@anticipated
def secrets_write(
    spec: str, repo: str, partial: bool = False, rotate: bool = False
) -> str:
    config = _config()
    require(config, Capability.DECRYPT)
    sink = GitHubSink(repo)
    sink.preflight()
    project = _parse_spec(spec)
    descriptors = load_descriptors(CREDENTIALS_FILE)
    store = _vault(config)
    resolution = resolve(project, descriptors, store.index())
    report = write_secrets(resolution, store, sink, partial=partial, rotate=rotate)
    lines = [f"Secrets for {project.project_name} in {repo}"]
    lines += [
        f"  {w.source:9}  {w.github_secret:24} {w.environment}" for w in report.written
    ]
    if report.deferred:
        lines.append(f"  deferred to provisioning: {', '.join(report.deferred)}")
    if report.outstanding:
        lines.append(f"  still to acquire: {', '.join(report.outstanding)}")
    lines.append("No value was returned.")
    return "\n".join(lines)


@server.tool(
    description=(
        "Push a project's spec and live health to the Loftline dashboard and pull "
        "the collaborators the team asked for into its Terraform variables. Needs "
        "loftline_site_token in the vault (run `loftline login`). Writes nothing to "
        "GitHub itself; says what `terraform apply` will add."
    )
)
@anticipated
def sync(
    spec: str,
    repo: str | None = None,
    project_dir: str | None = None,
    org: str | None = None,
) -> str:
    config = _config()
    require(config, Capability.DECRYPT)
    descriptors = load_descriptors(CREDENTIALS_FILE)
    descriptor = descriptors["loftline_site_token"]
    assert descriptor.vault_path is not None
    store = _vault(config)
    client = SiteClient(store.get(descriptor.vault_path), site=DEFAULT_SITE)
    project = _parse_spec(spec)
    report = sync_project(
        project,
        client,
        repository=repo,
        project_dir=Path(project_dir) if project_dir else None,
        org_slug=org,
        resolution=resolve(project, descriptors, store.index()),
    )
    lines = [f"Synced {report.project}"]
    lines += [
        f"  {n}: {'healthy' if h else 'unhealthy'}" for n, h in report.environments
    ]
    if report.wanted:
        lines.append(f"  wanted: {', '.join(report.wanted)}")
        lines.append(f"  applied: {', '.join(report.applied) or 'none'}")
        lines.append(f"  pending: {', '.join(report.pending) or 'none'}")
    lines += [f"  {note}" for note in report.notes]
    return "\n".join(lines)


def main() -> None:
    """Serve over stdio. Claude Code starts this from .mcp.json."""
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
