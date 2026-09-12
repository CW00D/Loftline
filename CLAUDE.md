# Working context for Claude Code

Read `README.md`, `docs/decisions.md` and `docs/credentials.md` before making
changes. `docs/decisions.md` records why things are the way they are; if a
change contradicts an ADR, say so explicitly rather than silently reversing it.

## What this repository is

A scaffolder and provisioner. It takes a project spec, resolves the credentials
that spec requires against a vault of credentials already held, prompts only for
the genuinely missing ones, renders a project template, and wires everything
into GitHub environment secrets and a hosting provider.

The central mechanism is the credential resolver, not the templating. See
`docs/credentials.md`.

## Stack

- Python 3.12, `uv` for dependency management
- `typer` for the CLI, `pydantic` for the spec and descriptor schemas
- `copier` for rendering
- `pytest` for tests
- Terraform for GitHub provisioning, invoked by hand rather than wrapped
- Vault backend behind an adapter with `list_paths`, `get`, `set`

## Hard invariants

Violating any of these is a defect regardless of what was asked for.

1. **No credential value is ever written to a tracked file, printed to stdout,
   or included in a log line.** `credentials.yml` holds descriptors and vault
   paths, never values.
2. **The resolver is pure.** No network calls, no vendor SDK imports, no vault
   value reads. It takes a spec, a descriptor set and a list of vault paths,
   and returns a partition. This is what makes it testable and it must stay
   that way.
3. **Nothing under `template/` may be ignored by a gitignore pattern.** The
   template is the product. A pattern that swallows a template file produces
   generated projects missing that file, and the failure surfaces only at
   deploy time downstream.
4. **`.copier-answers.yml` is emitted into every generated project and must not
   be gitignored there.** Without it in the downstream repository, `copier
   update` has no prior state and the entire update mechanism does not exist.
5. **GitHub Actions workflow files are not Jinja-rendered.** `${{ secrets.X }}`
   collides with Jinja delimiters. Workflow files carry no `.jinja` suffix.
   Per-project variation goes into repository variables, not into the template.
6. **Optional features are conditional *paths*, not conditional blocks inside
   shared files.** A rendered path evaluating to empty is skipped by Copier.
   Conditional blocks inside shared files produce the untestable matrix that
   ADR-004 exists to prevent.

## Conventions

- British English in all prose, comments and documentation.
- No em dashes anywhere.
- Type hints on everything. `ruff` and `mypy` clean.
- Errors are explicit exceptions with actionable messages naming the credential
  or file involved. Never swallow a vendor error into a generic failure.
- Tests do not require credentials, network access or a vendor account. If a
  component cannot be tested without those, the boundary is in the wrong place.

## Directory meanings

- `template/` is what gets rendered. Files ending `.jinja` are rendered;
  everything else is copied byte-for-byte.
- `infra/` holds versioned Terraform modules living in this repository.
  `template/infra/` emits a thin root module in generated projects that
  references them by source and version. Generated projects consume
  infrastructure; they never carry copies of it.
- `cli/` is the resolver, generator and provisioner.
- `_scratch/` is gitignored generation output. Always generate test projects
  there.

## Working style for this repository

- Small commits with one concern each.
- Do not add a template feature that has not been needed twice.
- Do not add a spec question until a real project demands it. The current
  set is `project_name`, `package_name`, `database`, `mobile`, `web`,
  `notifications`, `payments` (a list; each member is its own overlay) and
  `hosting` (one provider per component). Each addition is an ADR (ADR-022).
- When a step's gate in `docs/roadmap.md` is not yet met, do not start the next
  step. The gates exist because later work is otherwise built on assumptions
  earlier work would have falsified.
- Update `docs/decisions.md` when a design decision is made, not afterwards.
