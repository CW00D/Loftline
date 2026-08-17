# Roadmap

Sequenced by gate rather than by date. Each step has an exit condition that
must hold before the next step begins. The gates exist because the failure mode
of this project is building the later steps against assumptions the earlier
steps would have falsified.

## Step 0: Audit

**Effort:** 2 hours.
**Deliverable:** `docs/audit.md` completed, with a stated conclusion.
**Gate:** automatable hours exceed account-plane hours.

The only step that can terminate the project. See `docs/audit.md` for the
decision rule.

## Step 1: Extraction

**Effort:** one weekend.
**Deliverable:** a stripped skeleton in `template/`, with no templating.

Clone the most recent working project into `template/` and delete everything
domain-specific. What remains: backend, database layer, one migration, auth
stubs, healthcheck, one CI workflow, a hosting blueprint, and a
`docker-compose.yml`. No Jinja, no variables, no conditionals.

**Gate:** the stripped skeleton deploys from a clean clone.

Deployment, not compilation. A skeleton that builds but has never been deployed
encodes precisely the assumptions that break later in generated projects. If
this cannot be achieved in a weekend, templating is not the problem and
everything downstream is premature.

## Step 2: Parameterisation

**Effort:** 2 to 3 days.
**Deliverable:** `copier.yml` and a rendering template.

Minimum viable question set: `project_name`, `package_name`, `database`,
`mobile`, `notifications`. Jinja-ise filenames and contents. Nothing beyond
this set until a real project demands it.

**Gate:** two distinct projects generated and both deployed.

The second generation is the entire test. Templates fail on the paths nobody
exercised, so a template validated by one generation is validated by nothing.

Steps 0 to 2 total 20 to 25 hours and capture most of the available value.
Everything after this point has diminishing returns.

## Step 3: Local demo mode

**Effort:** 2 to 3 days.
**Deliverable:** `docker compose up` yielding a complete running system with
zero external dependencies, plus a seed loader and a synthetic data generator.

Sequenced ahead of cloud provisioning deliberately. This is the variant with
the highest work value, it is unblocked by vendor accounts, and it is the one
usable in settings where third-party hosting is not acceptable. See ADR-006.

**Gate:** a stranger can clone, run one command, and reach a working system
with realistic data at a scale that survives follow-up questions.

## Step 4: GitHub provisioning

**Effort:** 1 to 2 days.
**Deliverable:** a Terraform module in `infra/`.

Narrow scope: repository creation, branch protection, environments,
environment-scoped Actions secrets, collaborators. State in object storage with
locking. The generator writes `terraform.tfvars` from the Copier answers;
`terraform apply` is run by hand rather than wrapped. Wrapping is a later
convenience and a present source of opaque failures.

**Gate:** a generated project's repository and environments exist entirely from
`terraform apply`, with no console interaction.

## Step 5: Hosting provisioning

**Effort:** 3 to 5 days. Highest uncertainty in the plan.

Try the cheap route first: commit the hosting blueprint file, connect the
repository once by hand, and let the provider manage services, environment
groups and preview environments. That is an hour of work and may remove the
need for infrastructure-as-code on the hosting side entirely. Reach for a
Terraform provider only if the blueprint route is demonstrably insufficient.

Managed database provisioning goes in a small wrapper over the vendor API,
invoked by the CLI, not forced through Terraform.

**Gate:** a generated project reaches a live URL without console interaction
beyond the initial one-time repository connection.

## Step 6: Mobile pipeline

**Effort:** ~1 week. Worst return in the plan.

Build profiles, credentials, store submission. Deferred until there is a mobile
application that actually needs shipping. This component has the highest rot
rate in the system, because the platform vendors change underneath it on their
schedule rather than yours.

## Step 7: Update propagation

**Effort:** 1 to 2 days. Not exercisable in isolation.

Requires two or more real downstream repositories. Test by making a deliberate
template change and propagating it into both, then confirming neither is
broken by the merge.

## Totals

| Scope | Hours | Calendar, part-time |
| --- | --- | --- |
| Steps 0 to 2 | 20 to 25 | one weekend plus two evenings |
| Steps 0 to 3 | 35 to 45 | two to three weekends |
| Steps 0 to 5 | 55 to 70 | three to four weekends |
| Everything | 100 to 140 | several months |

Beyond step 5 the system carries permanent maintenance load proportional to the
number of vendors in its dependency graph. That load does not appear in the
table and is the real cost of the later steps.

## Sequencing constraint

Steps 0 to 2 are self-contained and can be done in a single weekend without
displacing other commitments. Steps 3 onward cannot, and starting them while
another deadline is live is the failure mode this project is most susceptible
to: unbounded scope, self-defined success criteria, no external deadline, and
immediate sensations of progress.