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

