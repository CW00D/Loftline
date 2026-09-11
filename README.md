# Loftline

A scaffolder and provisioner for new applications. Loftline takes a small
declarative project specification and produces a working repository, deployed
to a hosting provider, with CI, environments and every required credential
already wired.

The name comes from boatbuilding. The lines plan is the full-size drawing from
which every pattern and mould is taken; one authoritative set of lines, many
hulls, all faithful to it. The template is the lines plan. Generated projects
are hulls.

## Why this exists

Setting up a new application costs two to three days. The cost is not the
difficulty of the work. It is that the work arrives when an idea has maximum
energy, consumes that energy on undifferentiated infrastructure, and blocks
everything else while it runs. The loss is not the days. It is the projects
that never get past that wall.

Three distinct costs are being paid, and only one of them is unavoidable:

1. **Enrolment.** Apple Developer Program, Play console, registrar, payment
   methods. Genuinely manual, genuinely one-off, and already paid for. Not
   addressed here.
2. **Custody.** Finding the push token again. Working out which service account
   file belongs where. Re-deriving what the last project called an environment
   variable. This recurs on every project only because the knowledge lives in
   whichever repository it was last used in. **This is the largest cost and it
   is entirely automatable.**
3. **Wiring.** Repository creation, branch protection, environment secrets,
   hosting linkage, account boilerplate. Mechanical and automatable.

Loftline addresses custody and wiring. It moves enrolment to a documented
manual step that is performed once per credential per lifetime.

## What it does

```
loftline new beerreel
```

reads a spec, unions the credential requirements of every enabled feature,
diffs them against a vault of credentials already held, and then:

- **held** credentials are injected silently
- **derivable** credentials are generated from something already held
- **manual** credentials are requested, with step-by-step acquisition
  instructions printed inline, and stored permanently on first supply
- **produced** credentials are deferred to provisioning time and written
  straight into environment secrets

Everything is then written to GitHub environment secrets and the hosting
provider's environment groups. On the second project the manual list is empty
unless the spec enables a feature never used before, or a credential has
expired.

## Architecture: three planes

**Account plane.** Credentials that belong to you rather than to any project.
Enrolment is manual. Custody is the vault plus `docs/credentials.md`.

**Spec plane.** A schema-validated `loftline.yml`: name, stack selections,
feature flags, environments. This is the only artefact an LLM ever authors.

**Execution plane.** Fully deterministic. The resolver partitions credentials,
Copier renders the repository, Terraform provisions, the GitHub CLI writes
secrets, EAS handles mobile signing.

The load-bearing constraint: **an LLM may write the spec and must never perform
the provisioning.** A model asked to provision directly will succeed several
times and then silently misconfigure something.

## Non-goals

Recorded explicitly, because scope creep on a project of this class happens
through omission rather than decision.

Loftline does **not**:

- Create GitHub organisations. Not possible via API without Enterprise Cloud
  with enterprise-managed accounts, and one organisation with one repository
  per product is structurally better regardless.
- Perform enrolment: Apple or Play sign-up, D-U-N-S, payment methods, store
  review. It documents these and prompts for their outputs.
- Purchase domains or wait on DNS propagation.
- Host anything. It configures third-party hosting; it is not a platform.
- Target users who cannot operate a command line. For a non-technical user the
  enrolment step is the entire wall, and serving them requires a hosted product
  where the operator holds the vendor relationships. Different product.
- Support a matrix of stack permutations. See `docs/decisions.md`, ADR-004.
- Generate application logic. It generates the substrate the logic sits on.

## Setting it up

See `docs/setup.md`: tools, an encryption key and its backup, a vault,
your first credentials, and Loftline as tools inside Claude. About half an
hour on a new machine.

## Repository layout

```
loftline/
  copier.yml          # question set; contains _subdirectory: template
  pyproject.toml      # packages the CLI as `loftline`
  CLAUDE.md           # working context for Claude Code
  template/           # the lines plan: what gets rendered
  infra/              # Terraform modules consumed by generated projects
  cli/                # resolver, generator, provisioner
  docs/
    decisions.md      # architecture decision records
    credentials.md    # credential model and descriptor schema
    roadmap.md        # build sequence and gates
```

## Operating rules

1. Never add a template feature that has not been needed twice.
2. Build the next real application *through* the template, not beside it.
3. No credential value is ever committed, printed to logs, or written to
   Terraform state where avoidable.

A scaffolder maintained without a consuming project is a hobby with a Terraform
directory.

## Licence

None. All rights reserved by default. Deferred to the point of first sharing,
and it is two decisions: the licence on the template, and the licence on
generated output. Generated projects must be unencumbered or nobody will use
them.
