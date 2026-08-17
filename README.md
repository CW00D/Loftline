# Loftline

A scaffolder and provisioner for new applications. Loftline takes a small
declarative project specification and produces a working repository, deployed
to a hosting provider, with CI, secrets and environments already wired.

The name comes from boatbuilding. The lines plan is the full-size drawing from
which every mould and pattern is taken; one authoritative set of lines, many
hulls, all faithful to it. The template is the lines plan. Generated projects
are hulls.

## Why this exists

Setting up a new application from scratch costs two to three days. The cost is
not the difficulty of the work. It is that the work arrives at the moment an
idea has maximum energy, consumes that energy on undifferentiated
infrastructure, and blocks everything else while it runs. The loss is not the
days. It is the projects that never get past that wall.

Loftline moves that cost to once, up front, and amortises it.

## Architecture: three planes

The system is split into three planes. They must not merge.

**Account plane.** One-off, largely manual, per developer account rather than
per application: Apple Developer Program enrolment, Google Play console,
GitHub organisation, hosting provider account, domain registrar, secrets
manager, error tracking, App Store Connect API key, Play service account.
Created once, injected into every subsequent project. Loftline consumes these
credentials; it does not create them.

**Spec plane.** A single schema-validated `loftline.yml`: name, stack
selections, feature flags, environments. This is the only artefact an LLM ever
authors.

**Execution plane.** Fully deterministic. Copier renders the repository,
Terraform provisions infrastructure, the GitHub CLI configures secrets and
environments, EAS handles mobile signing.

The load-bearing constraint: **an LLM may write the spec and must never perform
the provisioning.** A model asked to provision directly will succeed several
times and then silently misconfigure something. Constraining it to authoring a
validated spec, consumed by deterministic tooling, is the difference between a
tool that is trusted and a tool that is debugged on every run.

## Non-goals

Recorded explicitly, because scope creep on a project of this class happens
through omission rather than decision.

Loftline does **not**:

- Create GitHub organisations. This is not possible via API without GitHub
  Enterprise Cloud with enterprise-managed accounts, and one organisation with
  one repository per product is structurally better regardless.
- Manage Apple Developer Program or Google Play enrolment, D-U-N-S
  acquisition, payment methods, or App Store review. These are account-plane
  and permanently manual.
- Purchase domains or wait on DNS propagation.
- Host anything. It configures third-party hosting; it is not a platform.
- Target users who cannot operate a command line. For a non-technical user the
  account plane is the entire wall, and serving them would require a hosted
  product where the operator holds the vendor relationships. That is a
  different product and is out of scope here.
- Support a matrix of stack permutations. See `docs/decisions.md`, ADR-004.
- Generate application logic. It generates the substrate the logic sits on.

## Repository layout

```
loftline/
  copier.yml          # question set; contains _subdirectory: template
  template/           # the lines plan: what gets rendered
  infra/              # Terraform modules consumed by generated projects
  cli/                # generator wrapper
  docs/
    decisions.md      # architecture decision records
    audit.md          # evidence base for what is worth automating
    roadmap.md        # build sequence and gates
```

## Status

Pre-audit. No code written. The next action is `docs/audit.md`, which is
capable of concluding that the provisioner should not be built. That outcome
is a success of the audit, not a failure of the project.

## Operating rules

Two rules exist to stop this becoming permanent maintenance work with no
consuming project.

1. Never add a template feature that has not been needed twice.
2. Build the next real application *through* the template, not beside it.

A scaffolder maintained without a consuming project is a hobby with a
Terraform directory.

## Licence

None. All rights reserved by default. The licence decision is deferred to the
point of first sharing, and is two decisions rather than one: the licence on
the template, and the licence on generated output. Generated projects must be
unencumbered or nobody will use them.