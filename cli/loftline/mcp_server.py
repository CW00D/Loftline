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

from .adopt import Adoption, adopt, is_adoption, resolve_project
from .bootstrap import init_vault, personal_recipients
from .doctor import Capability, VaultConfig, require, run_checks
from .errors import LoftlineError, SpecError
from .features import load_default_features
from .generate import generate
from .models import Spec, load_descriptors
from .orchestrate import (
    copy_between,
    plan_from_dashboard,
    provision_from_vault,
    realise_from_dashboard,
    render_provision,
    render_realise,
)
from .paths import credentials_file
from .realise import RealiseError
from .report import render_plan
from .resolve import resolve
from .secrets import GitHubSink, write_secrets
from .site import DEFAULT_SITE, SiteClient, sync_project
from .urlhandler import open_terminal, parse
from .vault_sops import SopsAgeVault
from .vaults import choose, register_vault

CREDENTIALS_FILE = credentials_file()

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

A project defined on the Loftline dashboard is made real with realise, which
generates it, creates the repository, pushes, writes secrets and reports
back; provision then creates its databases and deploys. sync keeps the
dashboard current. adopt_project puts a project that already exists on the
dashboard without generating anything.

Vaults: a person's own, an organisation's (--org on every command) or one a
personal project chose for itself. vault_init and vault_register make this
machine know one; vault_copy carries credentials between two, marked for
reissue.

The one thing you never do is handle a credential's value. When one must be
stored, call vault_set_prompt with the credential's name: it opens a terminal
on the person's machine already running `loftline vault set <name>`, and they
paste the value there. The same for the dashboard token: login_prompt. If a
person offers a value in the conversation, decline it and point at the
terminal.

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
        except (LoftlineError, RealiseError) as exc:
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


def _parse_project(spec: str) -> Spec | Adoption:
    """A spec or an adoption, from YAML text."""
    try:
        data = yaml.safe_load(spec)
    except yaml.YAMLError as exc:
        raise SpecError(f"the spec is not valid YAML: {exc}") from exc
    if is_adoption(data):
        return Adoption.model_validate(data)
    return _parse_spec(spec)


def _config(org: str | None = None, project: str | None = None) -> VaultConfig:
    """The vault for a project (ADR-035): the organisation's if `org` is given,
    the project's own if one is registered here, else the personal one."""
    return choose(None, org, project).config


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
def vault_list(org: str | None = None) -> list[str]:
    config = _config(org)
    require(config, Capability.READ_INDEX)
    return _vault(config).list_paths()


@server.tool(
    description=(
        "Resolve a spec (YAML text), or a project defined on the dashboard "
        "(dashboard=<name>, plus org for an organisation's), against the vault "
        "it will use: what will be injected, generated, acquired (with steps) "
        "and created at provisioning. With dashboard, the plan is also sent to "
        "the project's page. Never a value."
    )
)
@anticipated
def plan(
    spec: str | None = None, org: str | None = None, dashboard: str | None = None
) -> str:
    if (spec is None) == (dashboard is None):
        raise ToolError("Give spec (YAML text) or dashboard (a project's name).")
    if dashboard is not None:
        planned = plan_from_dashboard(dashboard, org=org, credentials=CREDENTIALS_FILE)
        assert planned.choice.config.vault_path is not None
        return render_plan(
            planned.spec,
            planned.resolution,
            planned.choice.config.vault_path,
            Path(f"<dashboard: {dashboard}>"),
            len(planned.index),
            planned.index,
        )
    assert spec is not None
    project = _parse_project(spec)
    config = _config(org, project.project_name)
    require(config, Capability.READ_INDEX)
    descriptors = load_descriptors(CREDENTIALS_FILE)
    index = _vault(config).index()
    resolution = resolve_project(project, descriptors, index)
    assert config.vault_path is not None
    return render_plan(
        project, resolution, config.vault_path, Path("<spec>"), len(index), index
    )


@server.tool(
    description=(
        "Generate a project from a spec (YAML text) into a directory. Resolves "
        "credentials first and fails on any without a descriptor. Provisions nothing."
    )
)
@anticipated
def new(
    spec: str, destination: str, force: bool = False, org: str | None = None
) -> str:
    project = _parse_spec(spec)
    config = _config(org, project.project_name)
    require(config, Capability.READ_INDEX)
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
    spec: str,
    repo: str,
    partial: bool = False,
    rotate: bool = False,
    org: str | None = None,
) -> str:
    project = _parse_spec(spec)
    config = _config(org, project.project_name)
    require(config, Capability.DECRYPT)
    sink = GitHubSink(repo)
    sink.preflight()
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
        "Put a project that already exists on the dashboard (ADR-036): read its "
        "GitHub environments and secret names through gh, match them to Loftline's "
        "descriptors, and write loftline.yml and loftline.credentials.yml in its "
        "directory. Generates nothing, changes nothing on GitHub, reads no value."
    )
)
@anticipated
def adopt_project(
    directory: str,
    repo: str,
    name: str | None = None,
    health: dict[str, str] | None = None,
) -> str:
    report = adopt(
        Path(directory),
        repo,
        name=name,
        health=health,
        descriptors=load_descriptors(CREDENTIALS_FILE),
    )
    a = report.adoption
    lines = [
        f"Adopted {a.project_name} from {a.repository}",
        f"environments: {', '.join(a.environments)}",
        f"known credentials: {', '.join(report.known) or 'none'}",
        f"new descriptors: {', '.join(report.new) or 'none'}",
        f"wrote {report.project_file}",
    ]
    if report.credentials_file:
        lines.append(f"wrote {report.credentials_file}")
    lines += list(report.notes)
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
    personal = _config()
    require(personal, Capability.DECRYPT)
    descriptors = load_descriptors(CREDENTIALS_FILE)
    descriptor = descriptors["loftline_site_token"]
    assert descriptor.vault_path is not None
    client = SiteClient(_vault(personal).get(descriptor.vault_path), site=DEFAULT_SITE)
    project = _parse_project(spec)
    chosen = choose(None, org, project.project_name)
    config = chosen.config
    require(config, Capability.DECRYPT)
    store = _vault(config)
    index = store.index()
    report = sync_project(
        project,
        client,
        repository=repo,
        project_dir=Path(project_dir) if project_dir else None,
        org_slug=org,
        resolution=resolve_project(project, descriptors, index),
        index=index,
        vault_kind=chosen.kind,
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


# --- the multi-step commands ----------------------------------------------------


@server.tool(
    description=(
        "Make a project defined on the Loftline dashboard real (ADR-033): generate "
        "it into a fresh directory, create the repository with Terraform, push, "
        "open the promotion pull request, write its secrets and report back. Stops "
        "before connecting the blueprint on Render and says so. Refuses while any "
        "credential is still to acquire. org is the organisation's slug for an "
        "organisation's project."
    )
)
@anticipated
def realise(
    name: str, into: str, org: str | None = None, terraform: str | None = None
) -> str:
    report = realise_from_dashboard(
        name, Path(into), org=org, terraform=terraform, credentials=CREDENTIALS_FILE
    )
    return "\n".join(render_realise(report))


@server.tool(
    description=(
        "Provision a generated project from its loftline.yml: create each "
        "environment's database, write every credential to GitHub and the host, "
        "register webhooks and DNS, and deploy. Needs the blueprint connected on "
        "Render once. Refuses an adopted project. Returns names and places, never "
        "values."
    )
)
@anticipated
def provision(
    spec_path: str,
    repo: str,
    org: str | None = None,
    environments: list[str] | None = None,
    aura_type: str = "free-db",
    region: str = "europe-west1",
    deploy: bool = True,
) -> str:
    path = Path(spec_path)
    report = provision_from_vault(
        path,
        repo,
        org=org,
        credentials=CREDENTIALS_FILE,
        environments=environments,
        aura_type=aura_type,
        region=region,
        deploy=deploy,
    )
    from .adopt import load_project

    return "\n".join(render_provision(report, load_project(path).project_name, repo))


# --- vaults ------------------------------------------------------------------------


@server.tool(
    description=(
        "Create an encrypted, empty vault in a new directory and initialise git in "
        "it. With org it is that organisation's vault; with project it is that "
        "personal project's own; either is registered on this machine by name and "
        "defaults to ./<name>-vault encrypted to the same two keys as the personal "
        "vault. recipients (age public keys) override that. A personal vault needs "
        "recipients given and is normally made by `loftline setup`."
    )
)
@anticipated
def vault_init(
    directory: str | None = None,
    recipients: list[str] | None = None,
    org: str | None = None,
    project: str | None = None,
) -> str:
    if org and project:
        raise ToolError("A vault is an organisation's or a project's, not both.")
    if not recipients:
        if not (org or project):
            raise ToolError(
                "A personal vault needs two recipients; run `loftline setup` instead."
            )
        recipients = personal_recipients()
    target = Path(directory) if directory else Path(f"{org or project}-vault")
    vault = init_vault(target, recipients)
    lines = [f"Created {vault}, encrypted to {len(recipients)} recipients."]
    if org:
        lines.append(f"Registered as the vault for the organisation {org}.")
        register_vault("org", org, vault)
    elif project:
        lines.append(f"Registered as the vault for the project {project}.")
        register_vault("project", project, vault)
    else:
        lines.append(f"Set LOFTLINE_VAULT={vault.resolve()} permanently.")
    lines.append(
        "Create an empty private GitHub repository and push the directory to it."
    )
    return "\n".join(lines)


@server.tool(
    description=(
        "Tell this machine where an existing organisation or project vault is: the "
        "path of a vault.yml cloned from its repository, plus exactly one of org "
        "(slug) or project (name). Records a location, nothing more."
    )
)
@anticipated
def vault_register(
    vault_path: str, org: str | None = None, project: str | None = None
) -> str:
    if bool(org) == bool(project):
        raise ToolError("Say whose vault it is: org (slug) or project (name).")
    path = Path(vault_path)
    if not path.is_file():
        raise ToolError(f"{path} is not a file. Clone the vault repository first.")
    kind, name = ("org", org) if org else ("project", project)
    registry = register_vault(kind, str(name), path)
    return f"Registered {path.resolve()} as the vault for {name} in {registry}."


@server.tool(
    description=(
        "Copy credentials from one vault into another, each marked as copied so "
        "the plan and the dashboard say to reissue it. Targets: an organisation's "
        "slug, project:<name>, or personal. Give names, or spec_path to copy every "
        "held credential a project needs. Nothing is returned but paths."
    )
)
@anticipated
def vault_copy(
    to: str,
    names: list[str] | None = None,
    spec_path: str | None = None,
    source: str = "personal",
    replace: bool = False,
) -> str:
    report = copy_between(
        to,
        names=names or (),
        spec_path=Path(spec_path) if spec_path else None,
        source=source,
        credentials=CREDENTIALS_FILE,
        replace=replace,
    )
    lines = [f"Copied {len(report.copied)} credential(s) into {report.target.label}:"]
    lines += [f"  {p}" for p in report.copied]
    lines.append(
        "Each is marked as copied. Reissue it and store the new value with "
        f"`loftline vault set <name>{report.target_flag} --replace`."
    )
    return "\n".join(lines)


# --- the two things only a person can type ------------------------------------------


@server.tool(
    description=(
        "Open a terminal on the person's machine already running `loftline vault "
        "set <name>`, so they paste the credential's value there and it goes into "
        "the vault without passing through this conversation. The argument is the "
        "credential's name (render_api_key), never a path or a value. Tell the "
        "person the terminal has opened and what to paste."
    )
)
@anticipated
def vault_set_prompt(name: str) -> str:
    open_terminal(parse(f"loftline://vault/set/{name}"))
    return (
        f"A terminal is open running `loftline vault set {name}`. Ask the person to "
        "paste the value there; nothing about it comes back here."
    )


@server.tool(
    description=(
        "Open a terminal running `loftline login`, where the person pastes a "
        "dashboard token created on the Loftline site's Settings page. The token "
        "never passes through this conversation."
    )
)
@anticipated
def login_prompt() -> str:
    open_terminal(parse("loftline://login"))
    return (
        "A terminal is open running `loftline login`. Ask the person to paste the "
        "token from the dashboard's Settings page there."
    )


def main() -> None:
    """Serve over stdio. Claude Code starts this from .mcp.json."""
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
