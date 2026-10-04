"""The multi-step commands, as functions the CLI and the MCP both call.

`realise`, `provision` and `vault copy` each string several modules together
and choose a vault. Writing that once here means the terminal and Claude run
the same code, and the MCP surface (ADR-019) stays a thin layer. Nothing here
prints; callers render the reports. Nothing here returns a value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .adopt import load_project, project_descriptors, resolve_project
from .doctor import Capability, VaultConfig, require
from .errors import LoftlineError
from .generate import generate
from .models import DescriptorSet, Spec, load_descriptors
from .providers.aura import AuraClient
from .providers.cloudflare import CloudflareClient
from .providers.render import RenderClient
from .providers.stripe import StripeClient
from .provision import ProvisionReport, provision
from .realise import RealiseReport, spec_from_dashboard
from .realise import realise as run_realise
from .resolve import Derive, Inject, Resolution, resolve
from .secrets import GitHubSink, write_secrets
from .site import DEFAULT_SITE, SiteClient, credentials_plan, sync_project
from .vault import VaultIndex
from .vault_sops import SopsAgeVault
from .vaults import (
    Choice,
    choose,
    copy_credentials,
    parse_target,
    require_project_vault,
)


class OrchestrationError(LoftlineError):
    pass


def descriptors_for(credentials: Path, project_dir: Path | None) -> DescriptorSet:
    """Loftline's descriptors, plus the project's own file if it has one
    (adopted projects, ADR-036)."""
    return project_descriptors(load_descriptors(credentials), project_dir)


def site_client(credentials: Path, site: str = DEFAULT_SITE) -> SiteClient:
    """The dashboard, signed in with the token in your personal vault."""
    personal = VaultConfig.from_env()
    require(personal, Capability.DECRYPT)
    assert personal.vault_path is not None
    descriptors = load_descriptors(credentials)
    if "loftline_site_token" not in descriptors:
        raise OrchestrationError(
            "credentials.yml has no loftline_site_token descriptor."
        )
    descriptor = descriptors["loftline_site_token"]
    assert descriptor.vault_path is not None
    token = SopsAgeVault(personal.vault_path).get(descriptor.vault_path)
    return SiteClient(token, site=site)


# --- realise -------------------------------------------------------------------


def realise_from_dashboard(
    name: str,
    into: Path,
    *,
    org: str | None = None,
    terraform: str | None = None,
    vault: Path | None = None,
    credentials: Path,
    site: str = DEFAULT_SITE,
) -> RealiseReport:
    """Pull a project defined on the dashboard and make it real (ADR-033)."""
    client = site_client(credentials, site)
    pulled = client.pull_project(name, org_slug=org)
    if not pulled.get("spec"):
        raise OrchestrationError(
            f"{name} has no spec on the dashboard; define it there first."
        )
    if pulled["spec"].get("adopted"):
        raise OrchestrationError(
            f"{name} is an adopted project; there is nothing to realise. "
            "Its hosting is already in place."
        )
    if pulled.get("vault") == "project" and not org:
        # The dashboard says this project has a vault of its own; the machine
        # must have it, or credentials would land in the wrong one.
        require_project_vault(name)
    chosen = choose(vault, org, name)
    config = chosen.config
    require(config, Capability.DECRYPT)
    assert config.vault_path is not None
    descriptors = load_descriptors(credentials)
    store = SopsAgeVault(config.vault_path)
    project_spec = spec_from_dashboard(dict(pulled["spec"]))
    resolution = resolve(project_spec, descriptors, store.index())
    if resolution.request:
        names = ", ".join(r.name for r in resolution.request)
        flag = (
            f" --org {org}"
            if org
            else (f" --project-vault {name}" if chosen.kind == "project" else "")
        )
        raise OrchestrationError(
            f"{name} still needs {names} in {chosen.label}. The dashboard's "
            "project page lists how to get each; store them with "
            f"`loftline vault set <name>{flag}` and re-run."
        )

    def do_generate(s: Spec, directory: Path) -> Path:
        return generate(s, directory)

    def do_secrets(s: Spec, repository: str) -> None:
        sink = GitHubSink(repository)
        sink.preflight()
        for environment_name in s.environments:
            sink.ensure_environment(environment_name)
        write_secrets(resolve(s, descriptors, store.index()), store, sink)

    report = run_realise(
        project_spec,
        into,
        generate=do_generate,
        write_secrets=do_secrets,
        terraform=terraform,
    )
    sync_project(
        project_spec,
        client,
        repository=report.repository,
        project_dir=report.directory,
        org_slug=org,
        resolution=resolution,
        index=store.index(),
        vault_kind=chosen.kind,
    )
    return report


@dataclass(frozen=True)
class DashboardPlan:
    spec: Spec
    resolution: Resolution
    index: VaultIndex
    choice: Choice


def plan_from_dashboard(
    name: str,
    *,
    org: str | None = None,
    vault: Path | None = None,
    credentials: Path,
    site: str = DEFAULT_SITE,
) -> DashboardPlan:
    """Resolve a project defined on the dashboard against the vault it will
    use, and push the credential plan up so the project page can show what to
    get before anything is realised. Marks nothing as generated."""
    client = site_client(credentials, site)
    pulled = client.pull_project(name, org_slug=org)
    if not pulled.get("spec"):
        raise OrchestrationError(
            f"{name} has no spec on the dashboard; define it there first."
        )
    if pulled["spec"].get("adopted"):
        raise OrchestrationError(
            f"{name} is an adopted project; plan it from its checkout with "
            "`loftline plan loftline.yml`."
        )
    if pulled.get("vault") == "project" and not org:
        require_project_vault(name)
    choice = choose(vault, org, name)
    require(choice.config, Capability.READ_INDEX)
    assert choice.config.vault_path is not None
    descriptors = load_descriptors(credentials)
    spec = spec_from_dashboard(dict(pulled["spec"]))
    index = SopsAgeVault(choice.config.vault_path).index()
    resolution = resolve(spec, descriptors, index)
    client.push_plan(
        name,
        credentials_plan(
            resolution,
            index,
            store_flag=choice.set_flag,
            link_query=choice.link_query,
            refresh=name,
        ),
        vault_kind=choice.kind,
        org_slug=org,
    )
    return DashboardPlan(spec, resolution, index, choice)


# --- provision -----------------------------------------------------------------


def provision_from_vault(
    spec_path: Path,
    repo: str,
    *,
    org: str | None = None,
    vault: Path | None = None,
    credentials: Path,
    environments: Sequence[str] | None = None,
    aura_type: str = "free-db",
    region: str = "europe-west1",
    deploy: bool = True,
) -> ProvisionReport:
    """Create each environment's database, write every credential, deploy."""
    project = load_project(spec_path)
    if not isinstance(project, Spec):
        raise OrchestrationError(
            f"{project.project_name} is an adopted project; its hosting is already "
            "someone's and Loftline does not provision it."
        )
    config = choose(vault, org, project.project_name).config
    require(config, Capability.DECRYPT)
    assert config.vault_path is not None
    github = GitHubSink(repo)
    github.preflight()
    descriptors = descriptors_for(credentials, spec_path.parent)
    store = SopsAgeVault(config.vault_path)
    resolution = resolve(project, descriptors, store.index())
    targets: list[Inject | Derive] = [*resolution.inject, *resolution.derive]
    for environment_name in sorted({e for t in targets for e in t.environments}):
        github.ensure_environment(environment_name)

    def secret(name: str) -> str:
        return store.get(descriptors[name].vault_path or "")

    render = RenderClient(secret("render_api_key"))
    # Vendor clients only for what the spec uses: an Aura key is not asked of
    # a Postgres project, nor a Stripe key of one without payments.
    aura = (
        AuraClient(secret("aura_client_id"), secret("aura_client_secret"))
        if project.database == "aura"
        else None
    )
    stripe = StripeClient(secret("stripe_secret_key")) if project.payments else None
    dns = CloudflareClient(secret("cloudflare_api_token")) if project.domain else None
    return provision(
        project,
        resolution,
        store,
        github,
        render,
        aura,
        stripe=stripe,
        dns=dns,
        environments=list(environments) if environments else None,
        instance_type=aura_type,
        region=region,
        deploy=deploy,
    )


# --- vault copy ----------------------------------------------------------------


@dataclass(frozen=True)
class CopyReport:
    copied: tuple[str, ...]
    source: Choice
    target: Choice
    # The flag `vault set` needs to reach the target vault, for the reissue hint.
    target_flag: str


def copy_between(
    to: str,
    *,
    names: Sequence[str] = (),
    spec_path: Path | None = None,
    source: str = "personal",
    credentials: Path,
    replace: bool = False,
) -> CopyReport:
    """Copy credentials from one vault into another, marked for rotation
    (ADR-035). Targets are an organisation slug, `project:<name>` or
    `personal`."""
    if not names and spec_path is None:
        raise OrchestrationError(
            "Name the credentials to copy, or pass --spec to copy what a spec holds."
        )
    if source == to:
        raise OrchestrationError("the source and the target are the same vault.")
    from_org, from_project = parse_target(source)
    to_org, to_project = parse_target(to)
    if to_project:
        require_project_vault(to_project)
    if from_project:
        require_project_vault(from_project)
    source_choice = choose(None, from_org, from_project)
    target_choice = choose(None, to_org, to_project)
    require(source_choice.config, Capability.DECRYPT)
    require(target_choice.config, Capability.WRITE)
    assert source_choice.config.vault_path is not None
    assert target_choice.config.vault_path is not None
    descriptors = descriptors_for(
        credentials, spec_path.parent if spec_path is not None else None
    )
    source_store = SopsAgeVault(source_choice.config.vault_path)
    target_store = SopsAgeVault(target_choice.config.vault_path)
    held = set(source_store.list_paths())

    paths: list[str] = []
    if spec_path is not None:
        resolution = resolve_project(
            load_project(spec_path), descriptors, source_store.index()
        )
        paths += [entry.vault_path for entry in resolution.inject]
    for name in names:
        descriptor = descriptors.get(name)
        if descriptor is None or descriptor.vault_path is None:
            raise OrchestrationError(
                f"{name} is not a credential that lives in a vault."
            )
        if descriptor.vault_path not in held:
            raise OrchestrationError(
                f"{name} is not in {source_choice.label}; nothing to copy."
            )
        paths.append(descriptor.vault_path)
    if not paths:
        raise OrchestrationError(
            f"Nothing to copy: {source_choice.label} holds none of these."
        )

    copied = copy_credentials(
        source_store,
        target_store,
        dict.fromkeys(paths),
        mark_from=source_choice.mark,
        replace=replace,
    )
    flag = (
        f" --org {to_org}"
        if to_org
        else (f" --project-vault {to_project}" if to_project else "")
    )
    return CopyReport(tuple(copied), source_choice, target_choice, flag)


def render_provision(
    report: ProvisionReport, project_name: str, repo: str
) -> list[str]:
    """The provision report as lines. Shared by the CLI and the MCP."""
    lines = [f"Provisioned {project_name} in {repo}"]
    for entry in report.environments:
        health = {True: "healthy", False: "UNHEALTHY", None: "not checked"}[
            entry.healthy
        ]
        lines.append(
            f"  {entry.environment:11} {entry.service}  database {entry.instance} "
            f"({entry.instance_status})  deploy {entry.deploy_status or 'skipped'}  "
            f"{health}"
        )
        if entry.url:
            lines.append(f"  {'':11} {entry.url}")
        if entry.webhooks:
            lines.append(
                f"  {'':11} Stripe webhooks registered: {', '.join(entry.webhooks)}"
            )
        if entry.web_service:
            web_deploy = entry.web_deploy_status or "skipped"
            lines.append(f"  {'':11} {entry.web_service}  deploy {web_deploy}")
        for hostname, status in entry.domains:
            lines.append(f"  {'':11} https://{hostname}  ({status})")
        if entry.domains:
            lines.append(
                f"  {'':11} Render issues each name's certificate after verifying "
                "it, usually within minutes; until then browsers refuse the name."
            )
    lines.append("No value was printed.")
    return lines


def render_realise(report: RealiseReport) -> list[str]:
    lines = [f"Realised {report.project} at {report.directory}"]
    lines += [f"  {step.name:11} {step.detail}" for step in report.steps]
    lines.append(f"  next        {report.next_step}")
    return lines
