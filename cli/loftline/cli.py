"""The `loftline` command line.

Five commands: `doctor`, `vault list`, `vault set`, `plan` and `new`. None of
them prints a credential value, and none provisions anything. `vault set` is
the only one that writes to the vault, and it takes the value from a hidden
prompt or stdin, never from an argument, so it stays out of shell history.
`new` writes a rendered project and nothing else. There is deliberately no
`vault get`: invariant 1 forbids a value reaching stdout, so the only consumer
of `get` is the secret writer in step 5, which passes values to `gh` and never
to a terminal.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from .adopt import (
    AdoptError,
    adopt,
    load_project,
    project_descriptors,
    resolve_project,
)
from .bootstrap import init_vault, mcp_config
from .doctor import Capability, Status, VaultConfig, require, run_checks
from .errors import LoftlineError
from .generate import generate
from .models import DescriptorSet, load_descriptors, load_spec
from .orchestrate import (
    copy_between,
    provision_from_vault,
    realise_from_dashboard,
    render_provision,
    render_realise,
    site_client,
)
from .paths import credentials_file
from .realise import RealiseError
from .report import render_plan
from .resolve import resolve
from .secrets import GitHubSink, write_secrets
from .setup import DEFAULT_SITE_WEB, run_setup
from .site import DEFAULT_SITE, SiteClient, sync_project
from .urlhandler import open_terminal, parse, register
from .vault_sops import SopsAgeVault
from .vaults import (
    choose,
    register_vault,
    require_project_vault,
)

app = typer.Typer(
    help="Scaffold and provision applications, credentials first.",
    no_args_is_help=True,
    add_completion=False,
)
vault_app = typer.Typer(
    help="Inspect the credential vault, or store a value in it.",
    no_args_is_help=True,
)
app.add_typer(vault_app, name="vault")
secrets_app = typer.Typer(
    help="Write resolved credentials to where CI and hosting read them.",
    no_args_is_help=True,
)
app.add_typer(secrets_app, name="secrets")
mcp_app = typer.Typer(help="Loftline as tools inside Claude.", no_args_is_help=True)
app.add_typer(mcp_app, name="mcp")

VaultOption = Annotated[
    Path | None,
    typer.Option(
        "--vault", help="Path to the SOPS-encrypted vault file.", show_default=False
    ),
]
OrgOption = Annotated[
    str | None,
    typer.Option(
        "--org",
        help="Dashboard organisation slug. Its project uses the organisation's "
        "vault, registered on this machine, instead of your personal one.",
        show_default=False,
    ),
]
ProjectVaultOption = Annotated[
    str | None,
    typer.Option(
        "--project-vault",
        help="A personal project's name, to use the vault of its own registered "
        "on this machine instead of your personal one.",
        show_default=False,
    ),
]
CredentialsOption = Annotated[
    Path,
    typer.Option(
        "--credentials",
        help="Path to credentials.yml. Defaults to the one shipped with Loftline.",
        show_default=False,
    ),
]
DEFAULT_CREDENTIALS = credentials_file()


def _descriptors(credentials: Path, project_dir: Path | None) -> DescriptorSet:
    """Loftline's descriptors, plus the project's own file if it has one
    (adopted projects, ADR-036)."""
    return project_descriptors(load_descriptors(credentials), project_dir)


_SYMBOL = {Status.PASS: "ok  ", Status.FAIL: "FAIL", Status.UNKNOWN: "?   "}


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


@app.command()
def doctor(vault: VaultOption = None) -> None:
    """Check the operational preconditions for using the vault."""
    config = VaultConfig.from_env(vault)
    results = run_checks(config)

    typer.echo("")
    for check in results:
        typer.echo(f"  {_SYMBOL[check.status]}  {check.name}")
        typer.echo(f"        {check.detail}")
    typer.echo("")

    failed = [r for r in results if r.status is Status.FAIL]
    unknown = [r for r in results if r.status is Status.UNKNOWN]
    if unknown:
        typer.echo(
            f"{len(unknown)} precondition(s) could not be determined. Check by hand."
        )
    if failed:
        _fail(f"{len(failed)} precondition(s) not met.")
    typer.echo("All preconditions met.")


@vault_app.command("list")
def vault_list(
    vault: VaultOption = None,
    org: OrgOption = None,
    project_vault: ProjectVaultOption = None,
) -> None:
    """Print the paths held in the vault. Values are never read."""
    try:
        chosen = choose(vault, org, project_vault)
        config = chosen.config
    except LoftlineError as exc:
        _fail(str(exc))
    try:
        require(config, Capability.READ_INDEX)
        assert config.vault_path is not None
        paths = SopsAgeVault(config.vault_path).list_paths()
    except LoftlineError as exc:
        _fail(str(exc))

    index = SopsAgeVault(config.vault_path).index()
    for path in paths:
        copied_from = index.rotate_from(path)
        typer.echo(
            f"{path}  (copied from {copied_from}; reissue)" if copied_from else path
        )
    typer.echo(
        f"\n{len(paths)} path(s) in {config.vault_path} ({chosen.label}). "
        "No value was decrypted."
    )


@vault_app.command("init")
def vault_init(
    directory: Annotated[Path, typer.Argument(help="Where to create the vault.")],
    recipient: Annotated[
        list[str],
        typer.Option(
            "--recipient",
            help="An age public key (age1...). Pass twice: this machine and a backup.",
        ),
    ],
    org: Annotated[
        str | None,
        typer.Option(
            "--org",
            help="Make this an organisation's vault and register it here by slug.",
            show_default=False,
        ),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option(
            "--project",
            help="Make this a personal project's own vault and register it by name.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Create an encrypted, empty vault and initialise git in it.

    Writes the SOPS configuration, an empty vault encrypted to the given
    recipients, and a .gitignore that keeps key material out. With --org the
    vault is an organisation's, found by `--org <slug>` on every other
    command; with --project it is one personal project's own, found by that
    project's name whenever a spec names it.
    """
    if org and project:
        _fail("A vault is an organisation's or a project's, not both.")
    try:
        vault = init_vault(directory, recipient)
        if org:
            registry = register_vault("org", org, vault)
        elif project:
            registry = register_vault("project", project, vault)
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(f"Created {vault}, encrypted to {len(recipient)} recipients.")
    if org:
        typer.echo(f"Registered as the vault for the organisation {org} in {registry}.")
    elif project:
        typer.echo(f"Registered as the vault for the project {project} in {registry}.")
    typer.echo("Next:")
    if org:
        typer.echo(f"  use --org {org} on plan, sync, realise and vault set")
    elif project:
        typer.echo(
            f"  commands given a spec for {project} use it; vault set takes "
            f"--project-vault {project}"
        )
    else:
        typer.echo(f"  set LOFTLINE_VAULT={vault.resolve()} permanently")
    typer.echo(
        "  create an empty private GitHub repository and push this directory to it"
    )
    typer.echo("  loftline doctor")


@vault_app.command("register")
def vault_register(
    vault: Annotated[
        Path, typer.Argument(help="A vault.yml, cloned from its vault repository.")
    ],
    org: Annotated[
        str | None,
        typer.Option(
            "--org", help="The organisation it belongs to.", show_default=False
        ),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option(
            "--project", help="The project it belongs to.", show_default=False
        ),
    ] = None,
) -> None:
    """Tell this machine where an existing organisation or project vault is.

    For a second administrator who has cloned the vault repository, or a
    second machine. Records a location, nothing more.
    """
    if bool(org) == bool(project):
        _fail("Say whose vault it is: --org <slug> or --project <name>.")
    if not vault.is_file():
        _fail(f"{vault} is not a file. Clone the vault repository first.")
    kind, name = ("org", org) if org else ("project", project)
    try:
        registry = register_vault(kind, str(name), vault)
    except LoftlineError as exc:
        _fail(str(exc))
    typer.echo(f"Registered {vault.resolve()} as the vault for {name} in {registry}.")


@vault_app.command("copy")
def vault_copy(
    to: Annotated[
        str,
        typer.Option(
            "--to",
            help="The receiving vault: an organisation's slug, project:<name> for a "
            "project's own vault, or personal.",
        ),
    ],
    names: Annotated[
        list[str] | None,
        typer.Argument(help="Credential names to copy. Default: what the spec holds."),
    ] = None,
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec", help="A loftline.yml: copy every held credential it needs."
        ),
    ] = None,
    source: Annotated[
        str,
        typer.Option(
            "--from",
            help="The vault to copy from, in the same forms. Default: personal.",
        ),
    ] = "personal",
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
    replace: Annotated[
        bool,
        typer.Option("--replace", help="Overwrite values the target already has."),
    ] = False,
) -> None:
    """Copy credentials from one vault into another, marked for rotation.

    For a project that has moved, or been given a vault of its own. Each value
    is decrypted from the source vault and stored straight into the target;
    nothing is printed. Every copy is marked as copied, and the plan and the
    dashboard say so until a fresh value replaces it with `vault set
    --replace`: a credential two vaults hold should become one vault's alone.
    """
    try:
        report = copy_between(
            to,
            names=names or (),
            spec_path=spec,
            source=source,
            credentials=credentials,
            replace=replace,
        )
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(f"Copied {len(report.copied)} credential(s) into {report.target.label}:")
    for path in report.copied:
        typer.echo(f"  {path}")
    typer.echo(
        "Each is marked as copied. Reissue it with the vendor and store the new value "
        f"with `loftline vault set <name>{report.target_flag} --replace`; the mark "
        "clears."
    )
    typer.echo("The target vault has changed. Commit and push its vault repository.")


@vault_app.command("set")
def vault_set(
    name: Annotated[
        str, typer.Argument(help="The credential's name in credentials.yml.")
    ],
    vault: VaultOption = None,
    org: OrgOption = None,
    project_vault: ProjectVaultOption = None,
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
    replace: Annotated[
        bool,
        typer.Option(
            "--replace", help="Overwrite a value already in the vault, as on rotation."
        ),
    ] = False,
    stdin: Annotated[
        bool,
        typer.Option(
            "--stdin", help="Read the value from standard input instead of prompting."
        ),
    ] = False,
) -> None:
    """Store a credential's value in the vault.

    The value is taken from a hidden prompt, or from stdin with --stdin. It is
    never accepted as an argument, so it cannot land in shell history, and it
    is never echoed back.
    """
    try:
        if project_vault:
            require_project_vault(project_vault)
        chosen = choose(vault, org, project_vault)
        config = chosen.config
        require(config, Capability.WRITE)
        assert config.vault_path is not None
        descriptors = _descriptors(credentials, Path.cwd())

        descriptor = descriptors.get(name)
        if descriptor is None:
            known = ", ".join(sorted(descriptors.entries)) or "none"
            _fail(
                f"No descriptor named {name} in {credentials}. Known credentials: "
                f"{known}.\nAdd a descriptor first; the vault path comes from it."
            )
        if descriptor.vault_path is None:
            _fail(
                f"{name} is {descriptor.state} and never lives in the vault. "
                "A produced credential is written to environment secrets at "
                "provisioning time; a derivable one is generated on demand."
            )

        store = SopsAgeVault(config.vault_path)
        if descriptor.vault_path in store.list_paths() and not replace:
            _fail(
                f"{name} is already in the vault at {descriptor.vault_path}. "
                "Pass --replace to overwrite it, for example after rotating it."
            )

        value = _read_value(name, stdin)
        store.set(descriptor.vault_path, value)
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(f"Stored {name} at {descriptor.vault_path} in {chosen.label}.")
    typer.echo(
        f"{config.vault_path.name} has changed. Commit and push the vault repository "
        "so the value survives this machine."
    )


def _read_value(name: str, from_stdin: bool) -> str:
    """The value, from stdin or a hidden prompt. Only a trailing newline is dropped."""
    if from_stdin:
        raw = sys.stdin.read()
        value = raw[:-1] if raw.endswith("\n") else raw
        value = value[:-1] if value.endswith("\r") else value
    else:
        typer.echo(
            f"Paste the value for {name} and press Enter. Nothing is echoed while "
            "you type, not even asterisks."
        )
        value = str(typer.prompt(f"Value for {name}", hide_input=True, default=""))
    if not value:
        _fail(f"An empty value was given for {name}. Nothing stored.")
    # Enough to know the paste landed; no part of the value itself (invariant 1).
    typer.echo(f"Received {len(value)} characters.")
    return value


@app.command()
def plan(
    spec: Annotated[Path, typer.Argument(help="Path to a loftline.yml project spec.")],
    vault: VaultOption = None,
    org: OrgOption = None,
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
) -> None:
    """Report how every credential this spec needs will be resolved.

    Reads the encrypted vault for its path index only, so it needs no age key
    and no `sops` binary. It performs no side effects whatever.
    """
    try:
        project = load_project(spec)
        config = choose(vault, org, project.project_name).config
        require(config, Capability.READ_INDEX)
        assert config.vault_path is not None
        descriptors = _descriptors(credentials, spec.parent)
        index = SopsAgeVault(config.vault_path).index()
        resolution = resolve_project(project, descriptors, index)
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(
        render_plan(project, resolution, config.vault_path, spec, len(index), index)
    )


@app.command()
def new(
    spec: Annotated[Path, typer.Argument(help="Path to a loftline.yml project spec.")],
    destination: Annotated[
        Path, typer.Argument(help="Directory to render the project into.")
    ],
    vault: VaultOption = None,
    org: OrgOption = None,
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
    force: Annotated[
        bool, typer.Option("--force", help="Render into a directory that is not empty.")
    ] = False,
) -> None:
    """Generate a project from a spec.

    Resolves the spec's credentials first, so a feature with no descriptor
    fails here rather than at provisioning, then renders the template. Nothing
    is provisioned and no credential value is read.
    """
    try:
        project = load_spec(spec)
        config = choose(vault, org, project.project_name).config
        require(config, Capability.READ_INDEX)
        assert config.vault_path is not None
        descriptors = load_descriptors(credentials)
        index = SopsAgeVault(config.vault_path).index()
        resolution = resolve(project, descriptors, index)
        generate(project, destination, overwrite=force)
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(f"Generated {project.project_name} at {destination}")
    typer.echo(f"  features     {', '.join(resolution.features)}")
    typer.echo(
        f"  credentials  {len(resolution.inject)} held, "
        f"{len(resolution.derive)} generated, "
        f"{len(resolution.request)} to acquire, "
        f"{len(resolution.defer)} created at provisioning"
    )
    if resolution.request:
        typer.echo(f"  run `loftline plan {spec}` for the acquire steps")
    typer.echo("Nothing has been provisioned. Next: git init, push, connect hosting.")


@secrets_app.command("write")
def secrets_write(
    spec: Annotated[Path, typer.Argument(help="Path to a loftline.yml project spec.")],
    repo: Annotated[
        str, typer.Option("--repo", help="GitHub repository as OWNER/NAME.")
    ],
    vault: VaultOption = None,
    org: OrgOption = None,
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
    partial: Annotated[
        bool,
        typer.Option(
            "--partial",
            help="Write what is held and generated; list what is still to acquire.",
        ),
    ] = False,
    rotate: Annotated[
        bool,
        typer.Option("--rotate", help="Regenerate derived secrets that already exist."),
    ] = False,
) -> None:
    """Write the spec's credentials to the repository's GitHub environments.

    Held values are decrypted from the vault one at a time and handed to `gh`
    on stdin. Derived values are generated once per environment and kept on
    later runs. Nothing is printed but names and places.
    """
    try:
        project = load_project(spec)
        config = choose(vault, org, project.project_name).config
        require(config, Capability.DECRYPT)
        assert config.vault_path is not None
        sink = GitHubSink(repo)
        sink.preflight()
        descriptors = _descriptors(credentials, spec.parent)
        store = SopsAgeVault(config.vault_path)
        resolution = resolve_project(project, descriptors, store.index())
        report = write_secrets(resolution, store, sink, partial=partial, rotate=rotate)
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(f"Secrets for {project.project_name} in {repo}")
    typer.echo(f"  environments  {', '.join(report.environments) or 'none'}")
    for entry in report.written:
        typer.echo(f"  {entry.source:9}  {entry.github_secret:24} {entry.environment}")
    if report.deferred:
        typer.echo(f"  deferred to provisioning: {', '.join(report.deferred)}")
    if report.outstanding:
        typer.echo(
            f"  still to acquire: {', '.join(report.outstanding)}  "
            f"(run `loftline plan {spec}` for the steps)"
        )
    typer.echo(
        "No value was printed. Push the deploying branch to see CI consume them."
    )


@app.command("provision")
def provision_command(
    spec: Annotated[Path, typer.Argument(help="Path to a loftline.yml project spec.")],
    repo: Annotated[
        str, typer.Option("--repo", help="GitHub repository as OWNER/NAME.")
    ],
    vault: VaultOption = None,
    org: OrgOption = None,
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
    environment: Annotated[
        list[str] | None,
        typer.Option(
            "--environment",
            help="Provision only these environments. Repeatable. Default: all.",
        ),
    ] = None,
    aura_type: Annotated[
        str,
        typer.Option(
            "--aura-type", help="Aura instance type: free-db or professional-db."
        ),
    ] = "free-db",
    region: Annotated[
        str, typer.Option("--region", help="Aura region.")
    ] = "europe-west1",
    no_deploy: Annotated[
        bool,
        typer.Option(
            "--no-deploy", help="Write everything but do not trigger a deploy."
        ),
    ] = False,
) -> None:
    """Create each environment's database, write every credential, and deploy.

    Needs the repository's render.yaml connected once as a Blueprint in the
    Render dashboard, and every held credential present in the vault. The
    Aura API key and the Render API key are read from the vault.
    """
    try:
        project_name = load_project(spec).project_name
        report = provision_from_vault(
            spec,
            repo,
            org=org,
            vault=vault,
            credentials=credentials,
            environments=environment,
            aura_type=aura_type,
            region=region,
            deploy=not no_deploy,
        )
    except LoftlineError as exc:
        _fail(str(exc))
    for line in render_provision(report, project_name, repo):
        typer.echo(line)


@mcp_app.command("config")
def mcp_config_command(
    desktop: Annotated[
        bool,
        typer.Option(
            "--desktop", help="For Claude Desktop's claude_desktop_config.json."
        ),
    ] = False,
    code: Annotated[
        bool, typer.Option("--code", help="For Claude Code's .mcp.json.")
    ] = False,
) -> None:
    """Print the MCP server entry for a Claude client, with this machine's paths."""
    if desktop == code:
        _fail("Pass exactly one of --desktop or --code.")
    try:
        typer.echo(mcp_config("desktop" if desktop else "code"))
    except LoftlineError as exc:
        _fail(str(exc))


if __name__ == "__main__":  # pragma: no cover
    app()


SiteOption = Annotated[
    str,
    typer.Option(
        "--site",
        envvar="LOFTLINE_SITE",
        help="The dashboard's API. Defaults to the Loftline site.",
    ),
]


@app.command()
def login(
    vault: VaultOption = None,
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
    site: SiteOption = DEFAULT_SITE,
) -> None:
    """Store a dashboard token in the vault and check it works.

    Create the token on the dashboard's Settings page; it is shown once.
    The value is taken from a hidden prompt and never echoed.
    """
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.WRITE)
        assert config.vault_path is not None
        descriptor = load_descriptors(credentials)["loftline_site_token"]
        assert descriptor.vault_path is not None
        value = _read_value("loftline_site_token", False)
        me = SiteClient(value, site=site).me()
        store = SopsAgeVault(config.vault_path)
        store.set(descriptor.vault_path, value)
    except LoftlineError as exc:
        _fail(str(exc))
    except KeyError:
        _fail("credentials.yml has no loftline_site_token descriptor.")
    typer.echo(f"Signed in to {site} as {me.get('name')} (@{me.get('handle')}).")
    typer.echo(
        f"{config.vault_path.name} has changed. Commit and push the vault repository."
    )


@app.command("adopt")
def adopt_command(
    directory: Annotated[Path, typer.Argument(help="The existing project's checkout.")],
    repo: Annotated[
        str, typer.Option("--repo", help="Its GitHub repository as OWNER/NAME.")
    ],
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="The project's name on the dashboard. Default: the repository's.",
            show_default=False,
        ),
    ] = None,
    health: Annotated[
        list[str] | None,
        typer.Option(
            "--health",
            help="ENV=URL: a URL whose 200 means that environment is up. Repeatable.",
            show_default=False,
        ),
    ] = None,
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
) -> None:
    """Put a project that already exists on the dashboard (ADR-036).

    Reads the repository's environments and the names of its secrets through
    `gh`, plus what render.yaml and .env.example declare, and writes
    loftline.yml and, for names Loftline has no descriptor for,
    loftline.credentials.yml. Nothing is generated and nothing is changed on
    GitHub or the host. Values are never read: GitHub does not return them.
    """
    checks: dict[str, str] = {}
    for item in health or []:
        if "=" not in item:
            _fail(f"--health takes ENV=URL, not {item!r}")
        environment, url = item.split("=", 1)
        checks[environment.strip()] = url.strip()
    try:
        report = adopt(
            directory,
            repo,
            name=name,
            health=checks,
            descriptors=load_descriptors(credentials),
        )
    except (LoftlineError, AdoptError) as exc:
        _fail(str(exc))

    a = report.adoption
    typer.echo(f"Adopted {a.project_name} from {a.repository}")
    typer.echo(f"  environments  {', '.join(a.environments)}")
    typer.echo(
        f"  credentials   {len(a.credentials)} set by the repository: "
        f"{len(report.known)} Loftline already describes, "
        f"{len(report.new)} described in {report.credentials_file.name}"
        if report.credentials_file
        else f"  credentials   {len(a.credentials)} set by the repository, "
        "all of which Loftline already describes"
    )
    for secret in report.known:
        typer.echo(f"    {secret:32} known   ({', '.join(report.sources[secret])})")
    for secret in report.new:
        typer.echo(f"    {secret:32} new     ({', '.join(report.sources[secret])})")
    for note in report.notes:
        typer.echo(f"  note: {note}")
    typer.echo(f"  wrote {report.project_file}")
    if report.credentials_file:
        typer.echo(f"  wrote {report.credentials_file}; fill in vendor and acquire")
    typer.echo("Next:")
    typer.echo(
        f"  loftline plan {report.project_file}    to see what the vault holds already"
    )
    typer.echo(
        f"  loftline sync {report.project_file} --repo {a.repository} --project "
        f"{directory}"
    )
    typer.echo(
        "  store what is missing with `loftline vault set <name>` from this "
        "directory; when nothing is, `loftline secrets write` takes over the "
        "GitHub environments. Until then the project runs exactly as it does now."
    )


def _site_client(credentials: Path, site: str) -> SiteClient:
    return site_client(credentials, site)


@app.command()
def sync(
    spec: Annotated[Path, typer.Argument(help="Path to a loftline.yml project spec.")],
    repo: Annotated[
        str | None, typer.Option("--repo", help="GitHub repository as OWNER/NAME.")
    ] = None,
    project: Annotated[
        Path | None,
        typer.Option(
            "--project",
            help="The generated project's directory, whose infra/terraform.tfvars "
            "receives the collaborators. Default: none written.",
        ),
    ] = None,
    org: OrgOption = None,
    vault: VaultOption = None,
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
    site: SiteOption = DEFAULT_SITE,
) -> None:
    """Push the project's spec and live status to the dashboard, and pull the
    collaborators the team asked for into the project's Terraform variables.

    Nothing here touches GitHub with a credential: the collaborators are
    written for `terraform apply`, and confirmed against the repository
    through `gh` afterwards. The dashboard token is yours, so it comes from
    your personal vault; the project's credentials come from its owner's.
    """
    try:
        client = _site_client(credentials, site)
        project_spec = load_project(spec)
        chosen = choose(vault, org, project_spec.project_name)
        config = chosen.config
        require(config, Capability.DECRYPT)
        assert config.vault_path is not None
        store = SopsAgeVault(config.vault_path)
        index = store.index()
        descriptors = _descriptors(credentials, project or spec.parent)
        resolution = resolve_project(project_spec, descriptors, index)
        report = sync_project(
            project_spec,
            client,
            repository=repo,
            project_dir=project,
            org_slug=org,
            resolution=resolution,
            index=index,
            vault_kind=chosen.kind,
        )
    except LoftlineError as exc:
        _fail(str(exc))
    except KeyError:
        _fail("credentials.yml has no loftline_site_token descriptor.")
    typer.echo(f"Synced {report.project} with {site} ({chosen.label})")
    for name, healthy in report.environments:
        state = (
            "not checked"
            if name in report.unchecked
            else ("healthy" if healthy else "UNHEALTHY")
        )
        typer.echo(f"  {name:11} {state}")
    if report.wanted:
        typer.echo(f"  collaborators wanted   {', '.join(report.wanted)}")
        typer.echo(f"  on the repository      {', '.join(report.applied) or 'none'}")
        typer.echo(f"  pending                {', '.join(report.pending) or 'none'}")
    for note in report.notes:
        typer.echo(f"  {note}")


@app.command()
def realise(
    name: Annotated[str, typer.Argument(help="The project's name on the dashboard.")],
    into: Annotated[
        Path,
        typer.Option("--into", help="A fresh directory to generate the project into."),
    ],
    org: OrgOption = None,
    terraform: Annotated[
        str | None,
        typer.Option("--terraform", help="Path to terraform, if it is not on PATH."),
    ] = None,
    vault: VaultOption = None,
    credentials: CredentialsOption = DEFAULT_CREDENTIALS,
    site: SiteOption = DEFAULT_SITE,
) -> None:
    """Make a project someone defined on the dashboard real.

    Generates it, creates its repository with Terraform, pushes the code and
    opens the promotion pull request, writes its secrets, and reports back.
    Stops before the one step only a person can do: connecting the blueprint
    on Render. Every credential stays on this machine, and an organisation's
    project takes them from the organisation's vault.
    """
    try:
        report = realise_from_dashboard(
            name,
            into,
            org=org,
            terraform=terraform,
            vault=vault,
            credentials=credentials,
            site=site,
        )
    except (LoftlineError, RealiseError) as exc:
        _fail(str(exc))
    for line in render_realise(report):
        typer.echo(line)


@app.command("url", hidden=True)
def url_command(
    link: Annotated[
        str, typer.Argument(help="A loftline:// link, as the OS hands it over.")
    ],
) -> None:
    """Open a terminal for a loftline:// link. Called by the operating system."""
    try:
        open_terminal(parse(link))
    except LoftlineError as exc:
        _fail(str(exc))


@app.command("register-url-handler")
def register_url_handler() -> None:
    """Make loftline:// links open this CLI, so the dashboard's Store buttons
    open a terminal already running `loftline vault set <name>`."""
    try:
        typer.echo(register())
    except LoftlineError as exc:
        _fail(str(exc))


class _TyperConsole:
    """The wizard's conversation, on a terminal."""

    def say(self, text: str) -> None:
        typer.echo(text)

    def ask_yes(self, question: str, default: bool = True) -> bool:
        return bool(typer.confirm(question, default=default))

    def ask(self, question: str, default: str = "") -> str:
        return str(typer.prompt(question, default=default))

    def ask_hidden(self, question: str) -> str:
        return str(typer.prompt(question, hide_input=True, default=""))

    def launch(self, url: str) -> None:
        typer.echo(f"   Opening {url}")
        typer.launch(url)


@app.command()
def setup(
    site: SiteOption = DEFAULT_SITE,
    site_web: Annotated[
        str,
        typer.Option("--site-web", help="The dashboard's address, for the browser."),
    ] = DEFAULT_SITE_WEB,
) -> None:
    """Get this machine ready, one question at a time.

    The tools Loftline drives, GitHub sign-in, an age key and a backup, an
    encrypted vault in a private repository, the dashboard token, the
    loftline:// handler and Claude Desktop. Each step checks first and asks
    before changing anything, so it is safe to run again.
    """
    try:
        run_setup(_TyperConsole(), site=site, site_web=site_web)
    except LoftlineError as exc:
        _fail(str(exc))


@mcp_app.command("serve", hidden=True)
def mcp_serve() -> None:
    """Serve the MCP over stdio. Claude starts this; people do not."""
    from .mcp_server import main as serve

    serve()
