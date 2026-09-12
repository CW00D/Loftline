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

from .bootstrap import init_vault, mcp_config
from .doctor import Capability, Status, VaultConfig, require, run_checks
from .errors import LoftlineError
from .generate import generate
from .models import load_descriptors, load_spec
from .providers.aura import AuraClient
from .providers.render import RenderClient
from .providers.stripe import StripeClient
from .provision import provision
from .report import render_plan
from .resolve import Derive, Inject, resolve
from .secrets import GitHubSink, write_secrets
from .vault_sops import SopsAgeVault

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
CredentialsOption = Annotated[
    Path,
    typer.Option("--credentials", help="Path to credentials.yml."),
]

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
def vault_list(vault: VaultOption = None) -> None:
    """Print the paths held in the vault. Values are never read."""
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.READ_INDEX)
        assert config.vault_path is not None
        paths = SopsAgeVault(config.vault_path).list_paths()
    except LoftlineError as exc:
        _fail(str(exc))

    for path in paths:
        typer.echo(path)
    typer.echo(
        f"\n{len(paths)} path(s) in {config.vault_path}. No value was decrypted."
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
) -> None:
    """Create an encrypted, empty vault and initialise git in it.

    Writes the SOPS configuration, an empty vault encrypted to the given
    recipients, and a .gitignore that keeps key material out.
    """
    try:
        vault = init_vault(directory, recipient)
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(f"Created {vault}, encrypted to {len(recipient)} recipients.")
    typer.echo("Next:")
    typer.echo(f"  set LOFTLINE_VAULT={vault.resolve()} permanently")
    typer.echo(
        "  create an empty private GitHub repository and push this directory to it"
    )
    typer.echo("  loftline doctor")


@vault_app.command("set")
def vault_set(
    name: Annotated[
        str, typer.Argument(help="The credential's name in credentials.yml.")
    ],
    vault: VaultOption = None,
    credentials: CredentialsOption = Path("credentials.yml"),
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
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.WRITE)
        assert config.vault_path is not None
        descriptors = load_descriptors(credentials)

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

    typer.echo(f"Stored {name} at {descriptor.vault_path}.")
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
        value = str(typer.prompt(f"Value for {name}", hide_input=True, default=""))
    if not value:
        _fail(f"An empty value was given for {name}. Nothing stored.")
    return value


@app.command()
def plan(
    spec: Annotated[Path, typer.Argument(help="Path to a loftline.yml project spec.")],
    vault: VaultOption = None,
    credentials: CredentialsOption = Path("credentials.yml"),
) -> None:
    """Report how every credential this spec needs will be resolved.

    Reads the encrypted vault for its path index only, so it needs no age key
    and no `sops` binary. It performs no side effects whatever.
    """
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.READ_INDEX)
        assert config.vault_path is not None
        project = load_spec(spec)
        descriptors = load_descriptors(credentials)
        index = SopsAgeVault(config.vault_path).index()
        resolution = resolve(project, descriptors, index)
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(render_plan(project, resolution, config.vault_path, spec, len(index)))


@app.command()
def new(
    spec: Annotated[Path, typer.Argument(help="Path to a loftline.yml project spec.")],
    destination: Annotated[
        Path, typer.Argument(help="Directory to render the project into.")
    ],
    vault: VaultOption = None,
    credentials: CredentialsOption = Path("credentials.yml"),
    force: Annotated[
        bool, typer.Option("--force", help="Render into a directory that is not empty.")
    ] = False,
) -> None:
    """Generate a project from a spec.

    Resolves the spec's credentials first, so a feature with no descriptor
    fails here rather than at provisioning, then renders the template. Nothing
    is provisioned and no credential value is read.
    """
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.READ_INDEX)
        assert config.vault_path is not None
        project = load_spec(spec)
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
    credentials: CredentialsOption = Path("credentials.yml"),
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
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.DECRYPT)
        assert config.vault_path is not None
        sink = GitHubSink(repo)
        sink.preflight()
        project = load_spec(spec)
        descriptors = load_descriptors(credentials)
        store = SopsAgeVault(config.vault_path)
        resolution = resolve(project, descriptors, store.index())
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
    credentials: CredentialsOption = Path("credentials.yml"),
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
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.DECRYPT)
        assert config.vault_path is not None
        github = GitHubSink(repo)
        github.preflight()
        project = load_spec(spec)
        descriptors = load_descriptors(credentials)
        store = SopsAgeVault(config.vault_path)
        resolution = resolve(project, descriptors, store.index())
        targets: list[Inject | Derive] = [*resolution.inject, *resolution.derive]
        for environment_name in sorted({e for t in targets for e in t.environments}):
            github.ensure_environment(environment_name)
        render = RenderClient(store.get(descriptors["render_api_key"].vault_path or ""))
        # Vendor clients only for what the spec uses: an Aura key is not asked
        # of a Postgres project, nor a Stripe key of one without payments.
        aura = (
            AuraClient(
                store.get(descriptors["aura_client_id"].vault_path or ""),
                store.get(descriptors["aura_client_secret"].vault_path or ""),
            )
            if project.database == "aura"
            else None
        )
        stripe = (
            StripeClient(store.get(descriptors["stripe_secret_key"].vault_path or ""))
            if project.payments
            else None
        )
        report = provision(
            project,
            resolution,
            store,
            github,
            render,
            aura,
            stripe=stripe,
            environments=environment,
            instance_type=aura_type,
            region=region,
            deploy=not no_deploy,
        )
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(f"Provisioned {project.project_name} in {repo}")
    for entry in report.environments:
        health = {True: "healthy", False: "UNHEALTHY", None: "not checked"}[
            entry.healthy
        ]
        typer.echo(
            f"  {entry.environment:11} {entry.service}  database {entry.instance} "
            f"({entry.instance_status})  deploy {entry.deploy_status or 'skipped'}  "
            f"{health}"
        )
        if entry.url:
            typer.echo(f"  {'':11} {entry.url}")
        if entry.webhooks:
            typer.echo(
                f"  {'':11} Stripe webhooks registered: {', '.join(entry.webhooks)}"
            )
        if entry.web_service:
            web_deploy = entry.web_deploy_status or "skipped"
            typer.echo(f"  {'':11} {entry.web_service}  deploy {web_deploy}")
    typer.echo("No value was printed.")


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
