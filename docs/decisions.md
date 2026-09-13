# Architecture decision records

Each record states the context, the decision, and the consequences that follow
from it. The purpose is to prevent silent reversal. A decision reversed
deliberately is fine; a decision reversed because the reasoning was forgotten
is not.

Status values: **Accepted**, **Superseded**, **Reversed**.

---

## ADR-001: Three-plane separation, and the LLM writes only the spec

**Status:** Accepted

**Context.** The obvious implementation is to hand an agent a description of an
application and let it create repositories, provision infrastructure and
configure secrets directly. This works on the first several attempts.

**Decision.** Split the system into an account plane (manual, one-off,
credential creation), a spec plane (a schema-validated `loftline.yml`), and an
execution plane (deterministic tooling: Copier, Terraform, `gh`, EAS). An LLM
may author the spec. It may not execute the provisioning.

**Consequences.**

- The spec schema is the primary interface and must be versioned.
- Every failure mode is reproducible, because generation is a pure function of
  the spec.
- Agent involvement is confined to elicitation, which is what models are good
  at, and excluded from side-effectful vendor calls, which is where silent
  misconfiguration accumulates.
- A failed generation can be diffed against a previous one. A failed agent run
  cannot.

---

## ADR-002: One GitHub organisation, one repository per product

**Status:** Accepted

**Context.** The original conception was a new GitHub organisation per
application, isolating each product.

**Decision.** One organisation. One repository per product. Organisation-level
Actions secrets. Reusable workflows in a `.github` repository, called rather
than copied by generated projects.

**Consequences.**

- Organisation creation via API requires GitHub Enterprise Cloud with
  enterprise-managed accounts. `POST /admin/organizations` is GHES only. The
  original design was not implementable.
- Organisation-level secrets, reusable workflows, shared runners, a single
  billing surface and a shared fastlane certificates repository are all
  retained, and all would have been lost under fragmentation.
- Reusable workflows called rather than copied means CI fixes propagate without
  requiring a template update in every downstream repository.

---

## ADR-003: Trunk plus environments, not long-lived branches

**Status:** Reversed by ADR-013. Retained for the reasoning.

**Context.** The original conception was long-lived `dev`, `staging` and `prod`
branches with hosting linked per branch.

**Decision.** A single trunk. GitHub Environments carry environment-scoped
secrets and required reviewers. Preview environments are created per pull
request by the hosting provider. An environment is a deployment target, not a
branch.

**Consequences.**

- No merge drift and no cherry-pick debt between long-lived branches.
- Production gating moves from branch permissions to environment protection
  rules, which is where it belongs and where it is auditable.
- Generated projects ship a hosting blueprint file rather than per-branch
  hosting configuration.

---

## ADR-004: Base template plus orthogonal overlays, not a stack matrix

**Status:** Accepted

**Context.** Offering choices across database, notifications, authentication
and mobile produces a combinatorial space. Sixteen permutations means sixteen
deployments to verify on every template change.

**Decision.** One opinionated base template. Optional features are additive
overlays, each contributing files, a Terraform module and a CI job, and each
independently testable. Overlays are orthogonal by construction. The database
slot is the single genuinely exclusive parameter and both branches are tested.

**Consequences.**

- Verification cost grows linearly with features rather than exponentially.
- A feature that cannot be expressed as an orthogonal overlay is a signal that
  it belongs in the base or does not belong at all.
- The failure mode this prevents is silent rot: an untestable matrix is not
  tested, so the template degrades without anyone noticing until a generated
  project fails in production.

---

## ADR-005: Copier, not Cookiecutter

**Status:** Accepted

**Context.** Both render Jinja templates into project directories.

**Decision.** Copier.

**Consequences.**

- `copier update` propagates upstream template changes into
  already-generated projects via three-way merge. Without it, every improvement
  benefits only future projects and existing ones diverge permanently. This
  single capability is the difference between a template and a platform.
- `.copier-answers.yml` must be committed inside every generated project and
  must not be gitignored, or `copier update` has no state to reconcile against.
- Renaming this repository breaks `copier update` in every downstream project
  until each answers file is hand-edited, because `_src_path` is recorded at
  generation time. The repository name is therefore effectively immutable.

---

## ADR-006: Local, dependency-free demo mode is a first-class variant

**Status:** Accepted

**Context.** One of the primary uses is rapid prototype construction for
enterprise sales conversations, principally in financial services. What buys
credibility in that setting is the collapse of a prospect's sense of distance
between concept and reality, which a working application in a stakeholder's
hands achieves and a deck does not.

**Decision.** A `docker compose up` mode producing a complete running system
with no external SaaS dependency is a first-class variant of the template, not
a degraded fallback.

**Consequences.**

- Financial services prospects will not accept scenario data sitting on a
  third-party hosting account, and procurement will not entertain it. A
  cloud-only template is unusable for the highest-value case.
- This constraint conflicts with the vendor-integrated design and both must be
  supported. The base template must therefore not assume the presence of any
  hosted service.
- The scarce component in this mode is plausible synthetic data at realistic
  scale, not the repository scaffold. A data generator is harder, more reusable
  and more differentiating than the template itself.

---

## ADR-007: Name and provenance

**Status:** Accepted

**Context.** The name becomes the CLI verb, the package name, and the
`_src_path` string recorded in every generated project. An earlier candidate,
`slipway`, was discarded after discovering an existing deployment platform of
that name occupying an adjacent product category.

**Decision.** Loftline. In boatbuilding, lofting scales the designer's drawings
to full size, and every pattern and mould is taken from the resulting lines.
Repository name, package name and CLI binary are identical. Typing cost is
handled by a local shell alias, never by divergence between the three.

**Consequences.**

- Name clearance is a search problem, not a recall problem. Any future rename
  candidate must be checked against npm, PyPI, GitHub repository search sorted
  by stars, a plain web search for the bare word alongside "deploy" or "CLI",
  and the `.sh` and `.dev` domains. Appearance in first-page results in a
  developer-tools context is a reject, not a caveat.
- The repository is private, under a personal account, with the first commit
  dated before any employment that could create ownership ambiguity. The commit
  timestamp is the provenance evidence.

---

## ADR-008: The audit is dropped; the cost is custody, not enrolment

**Status:** Accepted. Supersedes the planned setup audit.

**Context.** A two-hour audit was planned to establish whether setup cost was
automatable or account-plane, on the basis that account-plane cost is one-off
and already paid for. The audit's question was answered directly instead: the
recurring cost is finding and re-wiring credentials that already exist, plus
manual repository and permission setup, plus account boilerplate. Enrolment is
not the recurring cost.

**Decision.** Do not conduct the audit. Split the account plane into
*enrolment* (manual, one-off, out of scope) and *custody* (recurring,
automatable, the core of the product). Build the credential resolver.

**Consequences.**

- The credential resolver becomes the primary mechanism, and templating becomes
  secondary. This inverts the original build order.
- The credential inventory is not written as a document. It accumulates as
  descriptors in `credentials.yml`, populated the first time each credential is
  requested, and is therefore complete by construction rather than by
  recollection.
- The value of the tool no longer depends on generating many projects. It
  delivers on the first project, because the first project is where the
  descriptors and the vault get populated.

---

## ADR-009: Four credential states, and a pure resolver

**Status:** Accepted

**Context.** A credential a project needs may already be held, may be
derivable from something held, may require manual acquisition, or may not be
able to exist until provisioning creates it.

**Decision.** Four states: `held`, `derivable`, `manual`, `produced`. The
resolver is a pure function of the spec, the descriptor file and an index of
vault paths. It performs no network calls and never reads a secret value.

**Consequences.**

- The resolver is fully testable with no vendor account, no credentials and no
  network. It is therefore the first component written and the one with real
  test coverage.
- Acquisition instructions live in the descriptor rather than in a person's
  memory, so a credential is researched once per lifetime rather than once per
  project.
- `produced` credentials are written to environment secrets and never to the
  vault, preserving the distinction between what belongs to the account and
  what belongs to a project.
- Expiry handling falls out for free: an elapsed credential moves from `inject`
  to `request` with no special case.

---

## ADR-010: Vault backend is pluggable, chosen once

**Status:** Accepted. Amended by ADR-011, which selects the backend, and by
ADR-012, which adds `acquired_at` to the adapter surface.

**Context.** The vault holds account-level secret values. Candidates are a
hosted manager with a UI and sharing, or a local store with no vendor.

**Decision.** The CLI shells out to a vault adapter exposing three operations:
`list_paths`, `get(path)`, `set(path, value)`. Any backend implementing those
is acceptable. Choose one now and do not revisit.

**Consequences.**

- The resolver depends only on `list_paths`, so backend choice cannot affect
  the component with the most logic in it.
- A hosted manager is required if the tool is ever shared with anyone else; a
  local store is sufficient while it is single-user. The adapter boundary
  defers that decision without cost.

---

## ADR-011: Vault backend is SOPS with age, in a separate private repository

**Status:** Accepted. Resolves the choice deferred by ADR-010.

**Context.** The vault holds account-level credential values. It is read on a
developer machine at generation time by a human. CI never reads it, because
resolved values are pushed into GitHub environment secrets and the hosting
provider's environment groups, and everything downstream reads from there.
That removes service accounts, machine identities, seat counts and CI
integration from the requirement set entirely. What is required is a local
store with a scriptable read.

**Decision.** SOPS with age. No vendor, no account, no subscription, no network
dependency. The encrypted file lives in a **separate private repository**, not
in this one.

**Consequences.**

- SOPS encrypts leaf values and leaves keys and document structure in
  plaintext. `list_paths` therefore reads the *encrypted* file and needs no
  decryption at all, which keeps the resolver pure by construction rather than
  by discipline.
- A SOPS creation rule of `encrypted_regex: '^value$'` leaves `acquired_at`
  timestamps in plaintext, so expiry is evaluated without decrypting anything.
  Only `get` ever decrypts, and only a single extracted value at a time.
- The vault is **not** committed to the Loftline repository. Loftline is
  intended to be shared. A public repository containing an encrypted vault
  publishes the credential inventory and hands anyone a permanent offline
  attack target against the age key.
- The age private key sits at `~/.config/sops/age/keys.txt` in plaintext,
  protected only by filesystem permissions. This is the real gap against a
  hosted manager and it is accepted on four conditions: full-disk encryption
  enabled, `chmod 600` on the key file, the key backed up out of band because
  loss destroys the vault irrecoverably, and encryption to two recipients so a
  backup identity exists.
- Blast radius is bounded: almost everything stored is vendor-revocable in
  seconds from a console. This is not a signing key store.
- If this ever becomes multi-user, migrate to a hosted manager. The adapter
  boundary from ADR-010 makes that roughly thirty lines.
- `age-plugin-yubikey` is the available upgrade path, binding the identity to
  hardware so the key never exists on disk.

---

## ADR-012: Resolver decisions the descriptor schema left open

**Status:** Accepted

**Context.** Implementing the resolver surfaced five questions that
`credentials.md` does not answer. Each was resolved in code, and each would
otherwise be re-decided differently by whoever next touches it.

**Decision.**

1. **`derivable` descriptors carry a `derivation` field**, required by the
   schema. The resolver contract emits `(name, derivation)` but the required
   fields table names no source for it.
2. **A credential a feature `produces` needs a descriptor too**, not just the
   ones it `requires`. The `defer` tuple carries `produced_by`, `github_secret`
   and `environments`, all of which come from a descriptor, so a missing one is
   the same fatal error as a missing requirement.
3. **A `held` credential absent from the vault becomes a `request`.** The
   states table treats `manual` as the state that prompts, but a credential
   recorded as held that is not in fact held has to be acquired, and the vault
   is the authority on that rather than the descriptor. The converse also
   holds: a `manual` credential present in the vault is injected, which is what
   makes the second-run request list empty.
4. **An expiring credential with no `acquired_at` is requested, not injected.**
   Freshness that cannot be proved is not assumed. The alternative is injecting
   a credential that elapsed at an unknown point and discovering it during a
   deployment.
5. **`loftline doctor` gates per capability, not per command.** Three levels:
   `read-index` needs only the vault file, `decrypt` adds `sops`, `age`, the
   key file and full-disk encryption, `write` adds a second recipient. This is
   what lets `plan` be a hard-gated command and still run on a machine with no
   age key, which is the point of encrypting only `value` fields.

**Consequences.**

- A check that cannot reach a verdict reports UNKNOWN, which is printed loudly
  and does not block. Full-disk encryption on Windows usually cannot be queried
  without elevation, and treating that as a failure would train the operator to
  bypass the gate, which is worse than reporting it honestly.
- The `expires`, `acquired_at` and vault-presence rules mean the descriptor
  states are a declaration of *kind*, and the vault index decides *action*.
  Keeping that split explicit is what stops the resolver growing special cases.
- Hosting has no spec question, so `hosting.render` is an unconditional feature
  in the mapping. A second hosting provider is a spec schema change made
  deliberately, not a flag that accumulates.
- `docs/credentials.md` is now behind the implementation in three places and
  must be reconciled: the required-fields list omits `derivation`, the
  descriptor states table implies the state alone decides the action, and the
  operational preconditions section describes `doctor` as a single hard gate on
  every command rather than three capability levels.

---

## ADR-013: MCP server is the second interface; the exposed tool set is bounded

**Status:** Accepted in principle, not yet implemented. Build after Step 5.

**Context.** An MCP server would let an agent asked to start a project resolve
requirements and generate the base itself, which is the natural extension of
ADR-001: the model authors the spec, deterministic tooling consumes it. The
question is not whether to build it but which tools it exposes.

**Decision.** Build it after Step 5, as a thin wrapper over an already working
CLI. Exposed to a model: `list_features`, `validate_spec`, `plan(spec)`, and
`generate(spec)` writing to a scratch directory. Not exposed: `provision`,
`write_secrets`, `acquire`.

**Consequences.**

- The withheld tools create repositories, write environment secrets and touch
  vendor accounts. Exposing them recreates precisely the failure mode ADR-001
  exists to prevent, and an MCP call is indistinguishable to a model from any
  other function call, so the protocol offers no meaningful confirmation step.
- The resolver's purity (ADR-009) becomes a security property rather than only
  a testability one. Because `plan` consumes a path index and timestamps and
  never a value, it cannot leak a credential into a transcript even if
  instructed to.
- Acquisition never happens through a chat interface. A secret pasted into a
  chat window is a secret in a retained, synced transcript. The model prints
  the `acquire` instructions and stops; the value is supplied via `loftline
  acquire` in a terminal. The wrong path must be unavailable rather than
  discouraged.
- `plan` runs at the `read-index` capability level from ADR-012, so an MCP
  session needs no age key present. The security boundary and the capability
  gating agree, which is the reason both were designed that way.
- Building this before Step 5 means designing a tool surface for an engine
  whose shape is still being discovered, and doing the interface work twice.

---

## ADR-014: If a hosted vault is ever built, the server holds ciphertext only

**Status:** Accepted in principle. Out of scope until the CLI has a user who is
not the author.

**Context.** A hosted product with accounts would give sync across devices,
team sharing, recovery from a lost laptop, and onboarding for someone with no
existing vault. These are real and a purely client-side design cannot provide
them. The tempting implementation is server-side custody of values, by analogy
with GitHub environment secrets.

**Decision.** Accounts are acceptable. Server-side custody of plaintext, or of
decryption keys, is not. The server holds ciphertext; the client holds a key
derived from a passphrase that never leaves the device.

**Consequences.**

- The GitHub analogy does not transfer. GitHub holds secrets as a side effect
  of a product users already trust with source code, backed by HSMs and a large
  security organisation. Loftline would be asking for production credentials as
  the first thing it does, before it has done anything for the user.
- A store of hosting, DNS, database and app store credentials across many small
  companies is a higher-value target than most of the accounts it protects.
  Tooling vendors are attacked for exactly this reason.
- Holding plaintext or keys would require envelope encryption with a managed
  KMS, per-user data keys, rotation, immutable per-read audit logs, a
  pre-planned breach disclosure process, a UK GDPR processor agreement with
  every customer carrying a 72-hour notification duty, and professional
  indemnity plus cyber insurance. It also creates an obligation that outlives
  enthusiasm for the project: a vault users depend on cannot be abandoned.
- Holding ciphertext removes nearly all of that. A breach yields useless blobs.
- The inverted split, server holds the key and client holds the ciphertext, is
  rejected explicitly. The key must reach the client at generation time
  regardless, so the property is not preserved; it centralises the one item
  that unlocks everything; it still requires the full account system; and it
  delivers no sync, since the vault remains on a single machine.
- Cost of the correct design: no server-side processing of secret values. No
  validating a key against a vendor, no inspecting a token for expiry. The
  resolver already operates on path indexes and timestamps rather than values,
  so nothing currently designed is lost.
- Optional recovery escrow is the one legitimate exception. A recovery key,
  itself encrypted under a user-held passphrase, can be returned but not used.
  This addresses the sharpest edge in ADR-011, which is that losing the age key
  destroys a vault irrecoverably.
- Bring-your-own-vault remains the default regardless. The ADR-010 adapter
  boundary makes it nearly free: `op` for 1Password users, their own tooling
  for Infisical or Doppler users, SOPS for everyone else.
---

## ADR-013: Three long-lived branches, promoted by merge

**Status:** Accepted. Reverses ADR-003.

**Context.** ADR-003 chose a single trunk with GitHub Environments as the
deployment targets, on the grounds that long-lived branches accumulate merge
drift and that production gating belongs in environment protection rules. On
reflection the tool is for individuals and small teams, and for that audience
the branch *is* the mental model: "what is on staging" is answered by looking
at the staging branch, not by reading a deployment log. The auditability that
ADR-003 valued is real but is not the cost being paid by that audience.

**Decision.** Three long-lived branches, `dev`, `staging` and `prod`, in every
generated repository. `dev` is the default branch and the base for feature
branches. Promotion is a merge of the whole branch, `dev` into `staging` and
`staging` into `prod`, never a cherry-pick.

The branches mean different things for a web project and a mobile project,
selected by the `mobile` question in the spec:

| Branch | Web project | Mobile project |
| --- | --- | --- |
| `dev` | Local only, `docker compose up`. No hosted deployment. | Same. |
| `staging` | Deploys automatically to the `staging` environment. | Produces the release candidate build. This is what is submitted to the stores. |
| `prod` | Deploys automatically to the `production` environment. | Housekeeping: records what was submitted. Store submission is manual, in App Store Connect and the Play Console, and is not automated. |

GitHub Environments are retained for what they are good at, holding
environment-scoped secrets, and each deploying branch maps to one: `staging`
to `staging`, `prod` to `production`. The secret writer in Step 5 is unaffected.

**Consequences.**

- Merge drift is the known failure mode of this model and the mitigation is
  procedural: promotion is always a merge of the entire source branch. A
  hotfix branches from `prod`, merges to `prod`, and is then merged back down
  through `staging` to `dev` immediately. The moment someone cherry-picks, the
  three branches stop being a sequence and become three products.
- Branch protection replaces environment protection as the production gate.
  `prod` requires a pull request from `staging`; direct pushes are refused.
  This is configured by the Terraform module in Step 7.
- Preview environments per pull request are dropped. A feature branch is
  tried locally, then on `staging`.
- The hosting blueprint declares two services, one tracking `staging` and one
  tracking `prod`, rather than one service with previews.
- A web project's `staging` environment is optional in practice and is kept
  anyway, so that the two project types share one branch model rather than
  two.
- Mobile store submission stays manual by design. Automating it is Step 9 and
  remains the worst return in the plan.

---

## ADR-014: What the base template is

**Status:** Accepted

**Context.** Step 3 extracted the skeleton in `template/` from BeerReel, the
most recent deployed project. Stripping a real application forces decisions
about where the base ends and overlays begin, and about what may change during
extraction. Those are recorded here so the next extraction, or the first
overlay, does not re-decide them.

**Decision.**

1. **The base is a FastAPI backend on Neo4j** with self-hosted email and
   password auth, a healthcheck, the schema applied at boot, one CI workflow,
   a Render blueprint and a complete local system under Docker Compose. It
   deploys and runs with nothing but a graph database and a JWT secret.
2. **Object storage and push notifications are overlays, not base.** Each was
   used by one project. The rule that a template feature must be needed twice
   applies, and each degrades gracefully in its absence anyway. Their
   descriptors already exist for when the overlay lands.
3. **`database: postgres` has no template branch yet.** The base was
   extracted from a graph-backed project; the relational branch is Step 4
   work and will be a second extraction, not an edit of this one.
4. **The placeholder literal is `skeleton`.** Everywhere the source said the
   product's name, the skeleton says `skeleton` or `Skeleton`, consistently,
   so Step 4's parameterisation is a mechanical substitution and nothing else.
5. **Three changes were made beyond stripping**, each because the extracted
   form contradicted an accepted decision or a hard invariant. The API was
   added to Docker Compose, because ADR-006 requires the whole system to run
   locally and the source ran only the database there. The boot waits for the
   database with a bounded retry, because a compose start can win the race
   against the database's own health check. A `JWT_SECRET` shorter than 32
   bytes is refused at first use rather than warned about, because RFC 7518
   requires the length and a warning at boot is read by nobody.
6. **The Render blueprint declares two services**, one per deploying branch,
   as ADR-013 requires. Every environment variable is `sync: false`; values
   are written by Loftline and never appear in the repository.

**Consequences.**

- The mobile app is a separate extraction, gated by Step 9's pipeline rather
  than Step 3's deploy. Its auth screens map one to one onto the endpoints
  the skeleton keeps.
- `credentials.yml` gained `aura_client_secret` and `features.yml` gained a
  `base` feature requiring `jwt_secret`, `smtp_user` and `smtp_password`,
  because the skeleton needs them and the resolver had no way to say so.
- The tests in `template/api/tests` run against a real Neo4j and wipe it, and
  refuse to run against a hosted instance. They are the skeleton's contract;
  an overlay that breaks them is not an overlay.
- The skeleton's own tooling (`ruff`, `pytest`) is configured inside
  `template/api`, and `template/` is excluded from Loftline's. The two are
  different projects with different rules.

---

## ADR-015: Product direction, and what it changes

**Status:** Accepted

**Context.** Loftline began as a personal scaffolder. The intended product is
an MCP server with accounts: Claude elicits a project's requirements, writes
the spec, and Loftline generates a repository whose optional features are
already wired, so that later work adds a payment method rather than a payment
integration. Where credentials are needed, Loftline reuses what is held or
guides the acquisition. ADR-001 already places the LLM at the spec and
nowhere else; this record captures what the product goal changes.

**Decision.**

1. **The MCP is a thin layer over the CLI.** Each tool is a command. The
   elicitation is a prompt plus the spec schema. It is built after the CLI
   works end to end for one user, because the deterministic parts are what a
   hosted product gets wrong and they are cheapest to fix while the only user
   is the author.
2. **"Needed twice" is replaced.** An overlay is built when a real project
   first needs it, through that project, so it is validated by a deployment.
   Every overlay ships with its own tests and a deploy check, as the base
   does. The old rule was written for a personal tool, where a speculative
   feature is pure maintenance; for a product whose value is preloaded
   building blocks, the overlays are the point.
3. **Accounts come last.** They reverse two recorded positions: the README's
   non-goal of hosting anything, and ADR-011's local vault. A hosted service
   that holds other people's vendor credentials is a custodian, and a breach
   is everyone's keys at once. The security posture is written down before
   the first stranger's key is stored, and nothing before that step depends
   on it happening.

**Consequences.**

- The spec grows one question per overlay. ADR-001's requirement that the
  schema be versioned becomes load-bearing.
- The order of work is roadmap Steps 4, 5, 7 and 8, then the MCP wrapper,
  then overlays as projects demand them, then accounts.

---

## ADR-016: How the template is parameterised and how overlays attach

**Status:** Accepted

**Context.** Step 4 turned the skeleton into a Copier template with the five
questions and two overlays, `mobile` and `notifications`. Doing so under the
hard invariants forced several structural decisions.

**Decision.**

1. **Rendering is opt-in per file.** Only files that carry the project name
   have the `.jinja` suffix: the README, the compose file, the Render
   blueprint, the API title and the email sender name, plus the app's
   identifiers. Everything else is copied byte for byte. Development
   fixtures that used to carry the placeholder, the local database password
   and the seeded user, are now fixed values (`localdev`, `dev@example.test`)
   rather than per-project ones, so those files need no rendering at all.
2. **Backend overlays attach by file discovery.** `main.py` includes every
   module in `api/routers/` that defines `router`. An overlay adds a router
   by adding a file; the shared `main.py` never changes. This is what makes
   invariant 6 hold for endpoints.
3. **The app does not know which overlays exist.** Push registration is part
   of the base app and is best-effort: a project without the notifications
   overlay answers 404 to `POST /me/push-token`, which the app swallows like
   any other failure. The alternative, conditional imports and dependencies
   in the app, is a conditional block in a shared file and is what invariant 6
   forbids. Feature detection at runtime keeps the app one artefact.
4. **Overlay configuration is not declared in shared files.** The Render
   blueprint lists only the base's variables; an overlay's are written by the
   secret writer to the service directly. An overlay documents its own
   variables in the docstring of the module it adds.
5. **`database: postgres` is refused at generation** with a message naming
   ADR-014, rather than rendered from the graph branch. The Spec model still
   accepts it, because the resolver can plan a project the template cannot
   yet render.
6. **`package_name` reaches only the mobile identifiers** (`com.<package>`).
   The API is a flat directory of modules and has no package to name. The
   question is kept because the spec, the roadmap and the mobile overlay all
   use it.
7. **The mobile overlay builds but does not submit.** The staging workflow
   runs `eas build` for iOS and stops. Store submission, and the Apple
   credentials it needs, are Step 9.

**Consequences.**

- Generation is tested by rendering the working tree, which Copier does
  including uncommitted changes. `loftline new` renders from the same place.
- The mobile app's dependency on `expo-notifications` is unconditional. The
  cost is one native module in every build; the benefit is that enabling
  notifications changes nothing in the app.
- A second hosting provider or database is a new question, a new conditional
  path, and a schema version, never a conditional block.

---

## ADR-017: The secret writer

**Status:** Accepted

**Context.** Step 5 takes the resolver's output and puts values where CI and
hosting read them. It is the only step that handles credential values, so
its shape is mostly about what it refuses to do.

**Decision.**

1. **GitHub environment secrets first, through `gh`.** The value goes to
   `gh secret set` on stdin, which encrypts it locally before sending. It is
   never an argument, never printed, never on disk. Environments are created
   with an idempotent PUT before anything is written.
2. **Render's sink waits for Step 8.** Render environment variables attach
   to a service, and the service does not exist until provisioning creates
   it. The writer's sink interface is the seam; the Render sink arrives with
   the provisioner.
3. **Derived values are generated once per environment and kept.** A second
   run finds the secret present and leaves it, so re-running the writer
   never rotates every session token. `--rotate` regenerates deliberately.
   Staging and production get different values.
4. **Derivations are named, not described.** A derivable descriptor's
   `derivation` is a key into a registry in `secrets.py`, and an unknown key
   is a loud error checked before anything is decrypted. Prose belongs in a
   comment beside it.
5. **Outstanding credentials block the whole write.** If the plan still has
   things to acquire, nothing is written unless `--partial` is passed, which
   writes what is held and generated and lists the rest. A half-configured
   environment should be a choice, not a surprise.
6. **The gate is a CI job.** On a push to `staging` or `prod`, a job runs in
   the matching GitHub environment and fails if the base's secrets are
   absent, printing names only. A deploying branch without its secrets is
   broken, and this is where that surfaces.

**Consequences.**

- `gh` must be installed and logged in on the machine running the writer.
  The writer checks both before decrypting anything.
- The writer never stores into the vault, and the vault adapter's `get` is
  called from nowhere else. That is checked by tests, not by review.
- The CI secrets job means a freshly generated project's `staging` and
  `prod` branches are red until `loftline secrets write` has run for it. The
  `dev` branch is unaffected.

---

## ADR-018: GitHub provisioning

**Status:** Accepted

**Context.** Step 7 makes a project's repository, branches, environments and
protection reproducible from one Terraform apply. The roadmap fixes the
shape: a module in `infra/`, consumed by a thin root module in generated
projects, applied by hand. The decisions below are the ones it left open.

**Decision.**

1. **The owner is the token's owner.** The spec has no owner question and
   gains none. The GitHub provider creates the repository under whoever
   `GITHUB_TOKEN` belongs to; an organisation sets `owner` in the root
   module's provider block. `gh auth token` supplies the token, so Step 7
   introduces no credential.
2. **Terraform makes the first commit; the project is committed on top of
   it.** GitHub cannot create a branch in an empty repository, so the module
   initialises the repository, renames the initial branch to `dev`, and
   branches `staging` and `prod` from it. The generated code is then
   committed on `dev` with that initial commit as its parent, never
   force-pushed over it: `staging` and `prod` hold the same commit, and
   GitHub refuses a pull request between branches with no history in common.
   That was found the hard way on the gate repository. From then on `staging`
   and `prod` are reached by pull request only.
3. **The `prod` gate is branch protection, not environment protection.**
   `prod` requires a pull request and the named CI checks green on the exact
   commit, and refuses force pushes and deletion for everyone including
   admins. Zero approvals are required, because a solo developer cannot
   approve their own pull request and the requirement that matters is the
   pull request itself (ADR-013).
4. **The required check names are the CI job names.** `api` and `secrets`,
   as `ci.yml` defines them. A test in Loftline holds the two in step.
5. **State is local for this module, until the R2 bucket exists.** This
   state holds repository ids and protection rules and nothing secret;
   invariant 5 still treats it as sensitive, so it is gitignored and the
   R2 backend is one file copy away (`backend.tf.example`, with locking via
   `use_lockfile`). Other modules, Step 8's in particular, may hold produced
   credentials in state and must not start life local.
6. **The module is referenced at `ref=main`.** Tagging begins the first time
   the module changes after a downstream project exists; until then a tag
   would pin nothing anyone depends on.

**Consequences.**

- The gate, a repository and its environments existing entirely from
  `terraform apply`, is exercised on a fresh repository, not by importing
  `loftline-skeleton`, which was made by hand before this module existed.
- `terraform apply` is run by hand from the generated project's `infra/`
  directory, as the roadmap says. Wrapping it is a later convenience.
- `prevent_destroy` on the repository means `terraform destroy` refuses;
  deleting a product's repository is a deliberate act in GitHub.

---

## ADR-019: The MCP server, and building it before hosting

**Status:** Accepted. Amends the order in ADR-015.

**Context.** ADR-015 placed the MCP wrapper after Step 8. It was brought
forward for a reason the original order did not weigh: the wrapper is the
best way to *see* what exists, and understanding the system before extending
it is worth more than one more step of extension. Everything the MCP wraps
already works; Step 8 adds one tool later.

**Decision.**

1. **A thin layer over the existing commands.** `doctor`, `vault_list`,
   `plan`, `new` and `secrets_write` are tools. Three read-only views exist
   so the model can explain the system: `spec_schema`, `features` and
   `credentials`.
2. **No tool takes or returns a credential value.** There is no `vault_set`
   tool and there will not be one. Storing a value is a terminal command,
   because anything that passes through a conversation is in a log. The
   server's instructions tell the model to refuse a value if one is offered.
3. **Specs travel as YAML text.** The model authors the spec; `plan` and
   `new` validate it. `new` writes it into the generated project as
   `loftline.yml`, so a project records what it was made from.
4. **The model chooses; the tool acts.** Each tool is deterministic and the
   person approves each call through the client. This is ADR-001's boundary
   as it applies to a tool-calling model: the model is confined to
   elicitation and to choosing which deterministic step runs next.
5. **Stdio, started by the client.** `.mcp.json` at the repository root
   registers the server for Claude Code; the entry point is `loftline-mcp`.

**Consequences.**

- The remaining order is Step 8, then Step 6, then overlays, then accounts.
- Tests drive the server over an in-memory transport with no network, no
  `sops` and no `gh`, and assert the negative space: no parameter named for
  a secret, no value in any result.
- When Step 8 lands it adds a `provision` tool with the same shape.

---

## ADR-020: Distribution and the product model

**Status:** Accepted. Settles the questions ADR-015 deferred.

**Context.** Three questions arrived together: how friends get Loftline, how
the source is protected, and how the work is paid for. They were considered
against hosting the MCP server, which had been the assumed product shape.

**Decision.**

1. **Loftline is a local tool, distributed as a download.** Every operation
   that touches a credential value runs on the user's machine with the
   user's own credentials: their age key, their `gh` login, their vendor
   keys from their own vault. Nothing Loftline runs holds anyone else's
   credentials. The hosted MCP is dropped: once the value-touching parts are
   local, a server would hold nothing but the source, and would cost money
   and an authentication layer to do it.
2. **The source is not protected technically, and is not the asset.** A
   downloaded Python program is readable, and compiling it only makes it
   inconvenient to read. What is hard to copy is what accumulates: the
   descriptors, the overlays proven on deployments, the decision records,
   and the continued work. A copy is a snapshot that stops improving. A
   source-available licence permitting use and forbidding redistribution
   and commercial use states the terms; it is a legal instrument, not a
   lock, and it closes the README's open question at the point of first
   sharing.
3. **Membership sells the stream of updates.** Sign-up on a website issues
   a signed licence key with an expiry. The local tool verifies it offline
   and phones home for exactly one thing, `loftline update`, which serves a
   newer version only to an active key. A lapsed member keeps what they
   have; they stop receiving improvements. A pirated copy is a frozen copy.
   The check in the tool can be patched out; the server that serves updates
   cannot, and it is the only thing that matters.
4. **The website is a Loftline project.** It needs auth, payments and a
   hosted backend, so it is generated from the template and deployed with
   Loftline, and it is the first real project to need a payments overlay,
   which is therefore built through it rather than speculatively.
5. **Nothing on the server holds a user's credentials, ever.** The server
   knows who paid and until when. That is the entire custodial surface.

**Consequences.**

- The order of work is: Step 8 (deploy, because the website needs hosting),
  the website with the payments overlay, `loftline update` and a release
  pipeline, then the licence text.
- Step 8 stays a local operation using the user's own Render and Aura keys.
  It does not become a server-side tool.
- Friends are onboarded now by collaborator access to the repository and
  `docs/setup.md`; the download and membership replace that when they exist.
- The Terraform module referenced by generated projects lives in a private
  repository. Before any non-collaborator uses Loftline it must be published
  somewhere readable, since there is nothing secret in it.

---

## ADR-021: Hosting provisioning

**Status:** Accepted. Verification deferred.

**Context.** Step 8 makes the deferred credentials real and puts an
environment on a live URL. The roadmap's cheap route holds: Render creates
the services from the repository's blueprint, connected once by hand, and
Loftline does the rest through Render's and Aura's APIs. Both APIs were
probed read-only with the stored keys before anything was designed.

**Decision.**

1. **Per environment, in order:** find the Render service by the blueprint's
   name; create or find the Aura instance named `<project>-<environment>`;
   write every credential the environment needs, held, derived and produced,
   to the Render service's environment and to the GitHub environment;
   trigger a deploy; check `/health`. All services are looked up before
   anything is created, so a missing blueprint connection fails with nothing
   half done.
2. **The Render service is the readable record.** GitHub secrets cannot be
   read back; Render environment variables can. A derived value already on
   the service is reused and re-written to GitHub so the two agree; an Aura
   instance that already exists is reused only if the service still holds
   its password, since Aura shows it exactly once, and is otherwise refused
   with the reason.
3. **Vendor refusals pass through verbatim.** Aura's free tier permits one
   instance per account. Loftline does not work around it; it surfaces the
   refusal and lets the person choose environments, a paid type, or another
   account. `--environment` and `--aura-type` exist for that choice.
4. **The provisioner depends on protocols, not clients.** `RenderLike` and
   `AuraLike` name the handful of operations used, so the orchestration is
   tested against fakes and either vendor can be swapped without touching it.
5. **Live verification is deferred.** The one Aura account available holds
   its single free instance for another product. The provisioner is
   verified against fakes and both clients against recorded API shapes;
   the first real run waits for a relational template branch and a Render
   Postgres, which is the next template work.

**Consequences.**

- The Step 8 gate, a live URL from no console interaction beyond the
  blueprint connection, is not yet met. It is met by the Postgres branch.
- Postgres on Render becomes the default database path for new projects:
  created from the blueprint, no second vendor, no per-account instance
  limit. Aura remains the graph option.
- A project with two hosted environments needs two databases. On any free
  tier that is one too many, and the provisioner says so rather than
  sharing one.

---

## ADR-022: Feature families and hosting per component

**Status:** Accepted. Extends the spec schema; supersedes the "five questions"
limit in CLAUDE.md, which two real projects have now demanded.

**Context.** The second extraction, from UniSoc, brings a relational backend,
a web front-end, Android, and Stripe payments. Two constraints were set
before extracting: optional features that come in families must be
independently selectable, because each module is a separately priced thing;
and each hosted component must be able to live on a provider of the user's
choosing, because the providers used so far were chosen for being free.

**Decision.**

1. **The spec gains three fields.** `web: bool`, `payments: list[str]` and
   `hosting: {api, web}`. The question set is no longer fixed at five; it
   is fixed at what real projects have demanded, and each addition is a
   recorded schema change.
2. **Families are lists; members are overlays.** `payments: [subscriptions,
   checkout]` enables `payments.subscriptions` and `payments.checkout`, each
   a conditional path with its own credentials, tests and configuration.
   Nothing shared changes when one is enabled (invariant 6). A selector may
   test membership: `{field: payments, contains: subscriptions}`.
3. **Hosting is a choice per component.** `hosting.api` and `hosting.web`
   each name a provider. Provider files are conditional paths keyed on those
   answers: the Render blueprint exists only if a component is on Render.
   The provisioner selects a client per slot. Selectors may read nested
   fields: `{field: hosting.api, equals: render}`.
4. **One provider per slot ships first.** Render for the API and the web
   front-end, because both extractions target it. A second provider is a
   new conditional path, a new client behind the same protocol, and a
   descriptor set; the schema needs no change. Vercel for the web slot is
   the expected first addition, since the site being extracted lives there.
5. **The database's host follows the database.** `database: postgres` is a
   Render Postgres created by the blueprint; `database: aura` is Aura. A
   separate `hosting.database` slot is added when a second host for either
   exists.

**Consequences.**

- `hosting.render` in the feature map becomes `hosting.api.render` and
  `hosting.web.render`, each conditional on its slot. Selectors gain three
  forms: a nested `field` (`hosting.api`), `contains` for lists, and
  `all_of` for a conjunction.
- Copier questions are scalars and lists, so the nested `hosting` field
  becomes one flat question per slot: `hosting_api`, `hosting_web`.
- The Spec accepts `hosting.web: vercel` so a plan can be made for it; the
  generator refuses it until the files exist, as it does for postgres.
- Stripe is one account per operator: `stripe_secret_key` and
  `stripe_publishable_key` are global and held; `stripe_webhook_secret` is
  produced when the endpoint is registered at provisioning.
- Every overlay in a family documents its own variables in the module it
  adds; the Render blueprint never lists them (ADR-016).
- Pricing follows the module boundary. What a member pays for is a list of
  overlays, which is also what the spec says.

---

## ADR-023: The database branch is a directory

**Status:** Accepted. Delivers what ADR-014 point 3 and ADR-016 point 5
deferred; supersedes both.

**Context.** The relational branch was extracted from UniSoc, a FastAPI
backend on SQLAlchemy 2, Alembic and Postgres. Invariant 6 says optional
features are conditional paths, never conditional blocks in shared files.
The database is not an optional feature but the one exclusive choice
(ADR-004), and it decides the shape of the whole data layer: the driver, the
schema mechanism, the user store queries, the seed, the test fixtures, the
compose file and the CI service container. Two questions had to be settled:
where the branch lives in the template, and how much of the API is allowed
to differ between branches.

**Decision.**

1. **Each database choice is a directory of the template.** The template
   holds `api/` for what is shared and one sibling per choice,
   `{% if database == 'postgres' %}api{% endif %}/` and
   `{% if database == 'aura' %}api{% endif %}/`, which Copier renders into
   the same `api/`. The compose file and the CI workflow follow the same
   rule. What is in the choice's directory is the branch; what is in `api/`
   is shared and identical whichever database is chosen, which a test
   asserts byte for byte.
2. **`main.py` is shared, and talks to the database through five calls.**
   Each branch's `db.py` provides `get_session`, `wait_until_reachable`,
   `migrate`, `ping` and `close`. The app, the router discovery, the boot
   sequence and `/health` are the same file for both. The graph branch's
   boot logic moved from `main.py` into its `db.py` to make this so.
3. **The API contract is the same across branches.** `routers/auth.py`
   exists per branch because it is the user store, but its endpoints,
   bodies and answers are identical, and `tests/test_api.py` is shared:
   it is the contract, and it runs unchanged against either database. The
   mobile app and the web front-end are written against that contract, not
   against a database.
4. **Migrations are Alembic, applied on every boot.** `tables.py` is the
   single source of truth and `migrations/versions/` is derived from it by
   `alembic revision --autogenerate`; the first migration is written by
   hand to match. `db.migrate()` runs `upgrade head` in the lifespan, the
   same place the graph branch applies its constraints, so development and
   production cannot drift. Alembic's logging configuration is not loaded,
   because it would replace the API's.
5. **An overlay that needs a column owns a migration and a `tables_<name>.py`
   module, not a line in `tables.py`.** The notifications overlay adds
   `migrations/versions/{% if notifications %}0002_push_token.py{% endif %}`,
   which creates the column, and `{% if notifications %}tables_push.py{% endif %}`,
   which appends it to the shared table's metadata so that `alembic
   revision --autogenerate` sees it rather than proposing to drop it.
   `migrations/env.py` imports every `tables_*.py` module, the same
   discovery `main.py` does for routers. The overlay's router writes the
   column with SQL rather than an attribute on the model. `tables.py` stays
   shared; enabling notifications changes nothing shared, which is
   invariant 6 applied inside a branch. `alembic check` passes with the
   overlay on and with it off.
6. **The tests run against a real Postgres**, the one Docker Compose and the
   CI service container start, wipe it, and refuse anything that is not a
   local host. UniSoc tested on in-memory SQLite; that was not carried over,
   because SQLite hides the constraint names, timezone handling and
   `server_default`s that the branch relies on.
7. **Two shared files carry conditional blocks, deliberately.** The Render
   blueprint is one file by Render's design and gains a `databases:` block
   and a `fromDatabase` variable when the database is Postgres, so the
   database is created alongside the services and its connection string
   never passes through Loftline. The README is prose. Both are keyed on a
   single answer, each block is complete in itself, and generation tests
   render every combination. Nothing executable has a conditional block.
8. **What was left behind.** UniSoc's email verification step, its
   asyncio email worker, rate limiting with `slowapi`, Azure blob storage,
   Sentry and the hard-coded signing key. Verification and rate limiting
   have each been needed once and are candidates for overlays; the worker
   duplicates the base's daemon-thread sender; the key was never a
   candidate. The Stripe customer created at registration belongs to the
   payments family, not to auth.

**Consequences.**

- `database: postgres` is the default choice in `copier.yml` and renders.
  The generator's refusal remains for a choice with no directory.
- Staging's Render Postgres is on the free plan, which Render deletes after
  thirty days; prod's is the smallest paid plan. The blueprint says so.
- A machine with its own Postgres on 5432 answers instead of the container.
  The compose file, `.env.example` and the test refusal all say so, because
  the failure looks like a wrong password rather than a wrong database.
- A third database is a third directory and a `db.py` with the same five
  calls. The shared-files test is what keeps it honest.
- `hosting.database` (ADR-022 point 5) is still not a question: the
  database's host follows the database, Render for Postgres and Aura for
  Aura.

---

## ADR-024: The web overlay

**Status:** Accepted

**Context.** UniSoc's website is a Vite and React single-page app on Vercel:
a marketing site, an auth flow against the API, and a large logged-in
product area. The `web` question (ADR-022) needed files behind it.

**Decision.**

1. **The overlay is the site's skeleton, extracted the way ADR-014
   extracted the backend.** What remains: Vite, React, React Router, the
   auth context, a protected route, and one page per auth endpoint the
   base API has (log in, sign up, forgot and reset password, account with
   password change, log out and delete), plus a landing page that says to
   replace it. Every product page, PostHog, Framer Motion, Recharts,
   Helmet, Stripe and the Vercel headers file were left behind; Stripe
   returns with the payments family.
2. **The site's API client is the mobile app's, ported.** Same timeout,
   same error wording, same 401 handling, same field-level 422 and 409
   mapping, with the token in localStorage instead of the secure store.
   The two front-ends are one contract seen from two places.
3. **The base API gains `CORS_ORIGINS`.** A browser cannot call the API
   from another origin without it, and invariant 6 forbids the overlay
   adding middleware to the shared `main.py`. So the base always installs
   the middleware, reading a comma-separated list that is empty by
   default. The compose file and `.env.example` allow the Vite dev
   server; the blueprint sets the hosted site's URL when the site is on
   Render.
4. **Hosted, the site is a Render static site per deploying branch,** in
   the blueprint under the ADR-023 exception for that file. Render builds
   it from `web/` on push; `VITE_API_URL` is baked in at that build and
   names the API service beside it; every path is rewritten to
   `index.html` so the client-side router owns the URL space.
5. **The site has its own workflow,** `web.yml`, which lints and builds on
   changes under `web/`. It is a separate conditional path, so the API's
   workflow is untouched and the `prod` protection rule still names only
   the API's jobs.

**Consequences.**

- `hosting.web: vercel` is still refused at generation; the files for it
  are a `vercel.json` and a workflow, when a project needs it.
- The site has no tests of its own beyond lint and build, as the mobile
  app has none beyond `expo-doctor`. The contract they both consume is
  tested in `api/tests/test_api.py`.
- Render names services after the blueprint entry; if a name is taken
  across Render, the generated URLs in the blueprint need editing by hand.

---

## ADR-025: Android in the mobile overlay

**Status:** Accepted

**Context.** The mobile overlay built for iOS only. UniSoc ships to the Play
Store as well, with an EAS submit profile driven by a Google Play service
account key and Firebase Cloud Messaging for push.

**Decision.**

1. **The staging workflow builds both platforms.** Two `eas build` steps,
   one per platform, both `--no-wait`. Submission stays manual (ADR-013);
   `eas.json` carries the submit profiles so that `eas submit` from a
   laptop is one command per platform.
2. **The Play service account key is a credential.** `google_play_service_account`,
   account scope, manual, the JSON file's contents. The `mobile` feature
   requires it alongside the Apple credentials. It lives at
   `app/google-play-service-account.json` only on the machine that submits,
   and is gitignored.
3. **Android push is a file, detected at build time.** `app.config.js`
   sets `googleServicesFile` only if `google-services.json` is present.
   The file is per Firebase project and is downloaded by hand; the FCM
   service account key is uploaded once with `eas credentials`, as the
   Apple credentials are. Without the file the app builds and cannot
   receive push, which is the notifications overlay degrading rather than
   the mobile overlay failing.

**Consequences.**

- The Android keystore is generated and held by EAS on the first build,
  like Apple signing. Nothing about it is in the repository or the vault.
- Firebase is not a descriptor. It is per project, not per account, and is
  a file rather than a value; the descriptor model does not fit it and it
  has been needed once.

---

## ADR-026: The payments family

**Status:** Accepted

**Context.** UniSoc's payments are a strategy pattern over a unified
one-off checkout: reserve, price on the server, pay through a Stripe
PaymentIntent, fulfil by webhook, with Stripe Connect and a cron that
expires abandoned reservations. The spec's `payments` list (ADR-022) says
each module is a separately priced overlay, and rolling subscriptions were
asked for first. Neither existed in the source as a module; subscriptions
had to be built from Stripe Billing's documented integration.

**Decision.**

1. **Two modules, each a complete overlay:** `payments.checkout` and
   `payments.subscriptions`. Each is a router, a table, a migration, a
   product-configuration file, a test file, and its own Stripe webhook
   endpoint with its own signing secret (`stripe_checkout_webhook_secret`,
   `stripe_subscriptions_webhook_secret`). A module can be enabled,
   rotated or removed without touching the other. One thing is shared by
   the family and keyed on it being non-empty: `stripe_gateway.py`, the
   API key and webhook verification.
2. **The product edits one file per module, not the router.**
   `checkout_items.py` holds the catalogue and `fulfil()`;
   `subscription_plans.py` maps plan names to Stripe Price ids read from
   the environment. Prices are on the server; a client names an item or a
   plan and gets back a `client_secret`.
3. **Both modules hand the client a PaymentIntent `client_secret`.** A
   one-off purchase and a subscription's first invoice look identical to
   the client, so one web component, `PayForm.jsx`, pays either. Nothing
   is granted on the client's word; the module's webhook does it.
4. **The modules exist for the Postgres branch.** Their tables and routers
   live in that branch's directory; the generator refuses `payments` on
   `database: aura` until a graph project needs them.
5. **Overlay migrations sit in a fixed order and chain to the nearest
   earlier one present.** `0002` push token, `0003` orders, `0004`
   subscriptions. Each migration file is rendered and computes its own
   `down_revision` from the other overlays' answers, so every combination
   is a linear chain and Alembic has one head. The expression lives in the
   overlay's own file, which is the one place an overlay may know that
   others exist.
6. **Overlay dependencies live in `requirements.d/`.** The Dockerfile and
   CI install `requirements.txt` and then every file in that directory.
   A shared requirements file cannot carry a conditional line; a directory
   can carry a conditional file.
7. **What was left behind.** Stripe Connect and the platform fee, the
   reservation and expiry cron, the Stripe customer created at
   registration, and the strategy classes. Each is UniSoc's product, seen
   once. The generic module keeps the shape: server pricing, an order per
   attempt, idempotent webhook fulfilment.

**Consequences.**

- `stripe_publishable_key` reaches the web front-end under its own name:
  the static site's `STRIPE_PUBLISHABLE_KEY`, declared `sync: false` in the
  blueprint and listed as its own Vite prefix. The provisioner writes every
  held credential consumed by `web` to the static site as well as the API,
  and rebuilds the site so the value is baked in (ADR-027).
- The mobile app has no payment screen yet. Both modules return what
  Stripe's payment sheet consumes, and `@stripe/stripe-react-native` is a
  native module that ADR-016's "one artefact" rule would put in every
  build; that trade is deferred until a mobile project pays for it.
- The package manifest of the web overlay carries a conditional block for
  the Stripe packages, under the same reasoning as the blueprint (ADR-023
  point 7): a manifest is one file by its tool's design.
- Enabling a payment module on an existing project by `copier update`
  inserts a migration into the chain; a database already past that point
  needs `alembic stamp` by hand. Recorded, not solved.

---

## ADR-027: The provisioner follows the spec

**Status:** Accepted. Extends ADR-021.

**Context.** The provisioner was written for one shape of project: Aura
behind a Render API service. The spec now chooses the database, adds a web
front-end and lists payment modules, each of which produces or consumes a
credential at provisioning time.

**Decision.**

1. **The database step is per database.** On Aura, as before: create or
   reuse an instance, write its three values. On Postgres, nothing: the
   blueprint created the database beside the service and Render sets
   `DATABASE_URL` on the service itself. The report says `blueprint`.
   The Aura client is built only for an Aura project, so a Postgres project
   is never asked for Aura credentials.
2. **Each payment module's webhook endpoint is registered by the
   provisioner.** For every module in `payments`, an endpoint at the
   service's URL plus the module's path is created in Stripe with the
   module's events, and the signing secret Stripe returns once is written
   to the service and to GitHub under the module's secret name. The service
   is the record: a re-run finds the secret there and registers nothing. An
   endpoint Stripe has but the service does not is refused, as an Aura
   instance with a lost password is.
3. **A held credential consumed by `web` is written to the static site as
   well as the API,** under its descriptor name, and the site is rebuilt
   so the value reaches the bundle. Injected credentials now carry their
   descriptor's `consumed_by` for this purpose.
4. **Every service the spec implies must exist before anything is
   written,** the static sites included, so a blueprint connected before
   `web` was enabled fails cleanly rather than half-provisioning.

**Consequences.**

- The Stripe client speaks the form-encoded REST API through the same
  injectable transport as the others; Loftline gains no SDK.
- Connecting the blueprint remains the one manual step. Render's API can
  create services directly, which would remove it, but Render's GitHub
  access is itself granted by hand per repository, so the step would move
  rather than vanish.
- The mobile app's consumption of the publishable key is not provisioned:
  EAS builds read `eas.json`, and the value is not a secret. It is a
  documented edit when a mobile project takes payments.

---

## ADR-028: Free plans by default

**Status:** Accepted. Amends ADR-014 point 6 and ADR-023.

**Context.** The blueprint declared Render's starter plan for services and
a paid plan for the production database, on the grounds that always-on and
durable are what production means. Every project so far has been a gate
run or a hobby product, and the person paying said they would rather not
unless it is necessary.

**Decision.** Every service and database in the blueprint is on Render's
free plan. The blueprint's comment names each limit and the one-word change
that lifts it. Upgrading is a decision made in the generated project when
it starts to matter, not one the template makes for it.

**Consequences.**

- A free service sleeps after fifteen idle minutes; the first request
  after that takes up to a minute. The provisioner's health check runs
  straight after a deploy, when the service is awake, so it is unaffected.
- A free Postgres is deleted by Render at the end of its free period. Prod
  must be upgraded before it holds real data; the blueprint says so.

---

## ADR-029: What the second gate run found

**Status:** Accepted. Amends ADR-018 and ADR-026.

**Context.** The first Postgres project deployed through the full flow
found two things the local checks could not: git cannot track an empty
directory, so the Dockerfile's copy of `requirements.d/` failed in CI for a
project with no overlay packages; and a pull request whose CI was red merged
into `staging` regardless, because only `prod` required checks, and staging
deploys on push.

**Decision.** `requirements.d/` carries a README, so the directory always
exists in the repository. The Terraform module requires the `api` job on
`staging` as well as `prod`, through a `staging_required_checks` variable.
The `secrets` job is not required on staging because it runs only on the
push that follows the merge; on `prod` it is required and reports as
skipped on the pull request, which GitHub counts as passing.

**Consequences.**

- An already generated project picks up the protection change on its next
  `terraform init -upgrade` and `apply`, once Loftline's `main` is pushed:
  generated root modules reference the module by that branch.
- The gate did its job. Both failures were invisible to the generation
  tests, which render and lint but do not commit or deploy.

Amended the same day: Render allows one free Postgres per account, and
refuses a second outright, so a project with two environments cannot have
two free databases. Staging takes the free one; prod is on the smallest
paid plan. A gate run that must cost nothing provisions staging only.

Amended 2026-09-12 (ADR-024): the site's workflow and Render build use
`npm install`, not `npm ci`. The template ships no lock file, because the
dependency list varies with the overlays; a generated project commits its
own after the first install, and both commands honour it from then on.

---

## ADR-030: The spec's environments drive the blueprint

**Status:** Accepted. Extends ADR-013 and ADR-028.

**Context.** The blueprint declared both environments regardless of the
spec, and the second database is paid. A project that wants to exist for
free, or simply to start small, had to hand-edit its blueprint.

**Decision.** `environments` reaches the template as a Copier answer. The
blueprint creates one service, one static site and one database per
environment listed, and the Terraform variables carry the same list. A
project written as `environments: [staging]` gets a free staging API,
site and database; adding `production` to the spec and re-rendering adds
the rest.

**Consequences.**

- The branches themselves are still all three (ADR-013): Terraform creates
  `prod` whether or not anything deploys from it, so promotion works the
  day production is added.
- The GitHub `secrets` job in CI runs for whichever deploying branch is
  pushed, unchanged.

---

## ADR-031: The product's domain is a spec value

**Status:** Accepted

**Context.** A product lives at its own domain, not at a hosting
provider's URL. Pointing names at services and proving ownership to the
host is the kind of once-per-project chore Loftline exists to remove, and
it was being done by hand.

**Decision.**

1. **The spec gains `domain`,** the apex only, and a `hosting.dns` slot
   naming who holds the zone, Cloudflare first. The names are fixed by
   convention: staging lives at `staging.<domain>` with its API at
   `api.staging.<domain>`; production at `<domain>` with its API at
   `api.<domain>`. The blueprint, the provisioner and the mobile app's
   hosted API URL all derive from that one rule.
2. **The blueprint declares the names on the services,** and points the
   site and the API's CORS at each other by domain name rather than by
   Render URL, so the Render URLs stop mattering the moment DNS resolves.
3. **The provisioner creates the records and asks the host to verify.**
   A DNS-only CNAME per name, to the service's own hostname, created or
   corrected through Cloudflare's API; then Render is asked to look each
   name up, which is when it issues the certificate. Nothing is deployed
   if the zone is not on Cloudflare.
4. **The zone being on Cloudflare is the one manual step,** per domain,
   like connecting the blueprint per project: register the domain there
   or point its nameservers at it. `cloudflare_api_token` gains DNS edit
   permission in its acquire steps.

**Consequences.**

- Records are never proxied. Render terminates TLS and verifies ownership
  by lookup; Cloudflare's proxy in front of it breaks both.
- The apex is a CNAME too, which Cloudflare flattens; on a DNS host that
  cannot, production's site would need an A record, which is that host's
  client's problem when one exists.
- A `www.` name is not declared. Render redirects it to the apex on its
  own for verified apex domains; if a project wants it declared, it is a
  line in the blueprint.

---

## ADR-032: The dashboard holds intent, never a credential

**Status:** Accepted. Extends ADR-014 (the hosted-vault position) and ADR-020.

**Context.** A team wants to see its projects in one place and to add
colleagues to them without handing out the vault. ADR-014 forbids the
server holding any credential; ADR-001 forbids anything but deterministic
tooling acting on the world.

**Decision.**

1. **The Loftline site is the dashboard, built on Loftline's own base.**
   People, organisations with owners and members, projects owned by a
   person or an org, and collaborators per project addressed by GitHub
   login. Product code beside the template's, with its own migrations
   numbered from 0100.
2. **Sign in with GitHub, alongside email and password.** A user's GitHub
   login is the identity a collaborator invitation needs. The one GitHub
   token the site ever holds is used for a single profile read and
   dropped. The site never calls GitHub for anything else.
3. **The site stores intent; the administrator's machine acts.**
   `loftline sync` pushes a project's spec (names and choices, refused if
   a key looks like a value) and one health check per environment, and
   pulls the collaborators the team asked for into the project's Terraform
   variables. `terraform apply` on the administrator's machine, with the
   administrator's token, makes them real; the next sync confirms them
   against the repository through `gh` and marks them applied.
4. **The command line signs in with a personal token,** created on the
   dashboard and shown once, stored in the vault as `loftline_site_token`
   by `loftline login`, hashed on the server, revocable there.

**Consequences.**

- Team members need no credential at all: CI holds the secrets, Render
  holds the rest, and their repository access comes from being a
  collaborator. A second administrator is a person given the vault, as a
  password manager would be shared.
- The site can show that an environment is unhealthy; it cannot fix it,
  and that is the design.
- A product's own credentials, such as the GitHub OAuth secret, sit
  outside the feature map, so the provisioner does not write them; today
  they are set on the service by hand. A per-project credentials file is
  the obvious next step and is recorded, not built.
- Production of the site needs a second redirect URI on the OAuth App;
  no new app.

---

## ADR-033: Defined on the dashboard, realised on the administrator's machine

**Status:** Accepted. Extends ADR-032.

**Context.** A team wants to define a project on the dashboard and have it
appear, and to see which credentials it needs and how to get them. ADR-032
keeps every credential and every side effect on the administrator's
machine, so "create it here" cannot mean the server creating anything.

**Decision.**

1. **The dashboard authors the spec.** The same questions the CLI asks, the
   same shape check; the project is recorded as defined and not yet
   generated. A spec with an unknown field, or a field whose name looks
   like a value, is refused.
2. **`loftline realise <name>` makes it real.** On the administrator's
   machine it pulls the spec, refuses if any credential is still missing,
   then generates, creates the repository with Terraform, commits on top of
   the first commit and pushes, opens the promotion pull request with
   auto-merge, writes the secrets, and syncs back. It stops before the one
   step only a person can do, connecting the blueprint, and prints it. Every
   external command goes through an injectable runner, so the sequence is
   tested without Terraform, git or GitHub.
3. **The credential plan is on the project page, names only.** `sync`
   reports each credential's state (held, generated, missing, produced),
   the acquire steps for a missing one, and the exact `loftline vault set`
   command. The page offers to copy the command or to open it through a
   `loftline://vault/set/<name>` link, which the CLI registers as a URL
   handler and answers by opening a terminal already running the command.
   The link carries a name; the value goes into the terminal's hidden
   prompt and nowhere else.
4. **Membership follows ownership.** A personal project's collaborators are
   whoever its owner names. An organisation's project takes only the
   organisation's members, and can take all of them at once
   (`auto_members`). Outsiders join the organisation first, which is an
   owner's decision, not a project's.
5. **A project describes itself.** The template emits `LOFTLINE.md`, a
   short generated summary of what the project is; `sync` sends it up and
   the project page shows it.

**Consequences.**

- The dashboard's spec form and the CLI's questions are kept in step by
  hand; the CLI's validation is the truth and runs at `realise`.
- macOS cannot register the URL scheme without an application bundle; the
  Copy button is the fallback there and everywhere.
- Product migrations in the site are numbered from 0100 so a template
  overlay added later never collides.

Amended 2026-09-13 (ADR-030): the Terraform variables always name both
GitHub environments. They hold secrets and cost nothing, and removing one
with the spec's list destroyed a real environment on the first project.
Render resources still follow the spec.

---

## ADR-034: One executable, one setup conversation

**Status:** Accepted. Extends ADR-020 and ADR-033.

**Context.** The install path was a clone, a package manager, five
commands and a JSON file to edit. It suited the author and nobody else.

**Decision.**

1. **Loftline ships as one executable per platform,** built by PyInstaller
   with the template, the descriptors and the Terraform module inside it,
   attached to a GitHub release on every tag. The Python wheel ships too,
   for people who prefer `uv tool install`. Both resolve their bundle
   through `paths.py`, so the checkout, the wheel and the executable are
   the same tool.
2. **`loftline setup` is the install page, done.** It checks each tool and
   offers to install the missing ones with the platform's package manager,
   signs in to GitHub, generates the machine's age key and a backup key,
   creates the vault and persists its location, pushes the vault to a
   private repository, opens the dashboard for a token and stores it,
   registers the URL handler, and writes the Claude Desktop entry. Every
   step checks first and asks; running it again is harmless. The console
   and the machine are injected, so the whole conversation is tested
   without a terminal.
3. **The executable serves the MCP itself** (`loftline mcp serve`), so the
   Claude entry names one file wherever it was put.

**Consequences.**

- The executable is unsigned; Windows warns once. Code signing is a
  certificate in the owner's name and a later, paid step.
- Docker stays outside setup: it is a desktop application with its own
  installer, needed only to run a generated project locally.
- The install page is three sections: download, `loftline setup`, a
  project.
