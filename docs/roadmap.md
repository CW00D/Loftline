# Roadmap

Sequenced by gate rather than by date. Each step has an exit condition that
must hold before the next begins.

The order inverts the original plan. The credential resolver comes before the
template, because custody is the dominant cost (ADR-008) and because the
resolver is the only component testable with no vendor account, no credentials
and no network.

## Step 1: Vault and descriptor schema

**Effort:** half a day.
**Deliverable:** a chosen vault backend, an adapter exposing `list_paths`,
`get` and `set`, and `credentials.yml` containing two or three real
descriptors.

Do not enumerate every credential. Add three you know by heart and stop; the
rest accumulate as the resolver requests them.

**Gate:** `loftline vault list` prints paths, and no secret value appears in
any tracked file.

## Step 2: Resolver

**Effort:** 1 day.
**Deliverable:** `cli/loftline/resolve.py` plus a full test suite.

Pure function of spec, descriptors and vault path index. Partitions into
`inject`, `derive`, `request`, `defer`. Handles expiry. Touches no network.

**Gate:** tests cover every state including expired-credential promotion and
unknown-credential failure, and pass with no credentials configured.

This is the component with the most logic and the only one that can be
verified properly. Write it carefully; everything downstream consumes its
output.

## Step 3: Extraction

**Effort:** one weekend.
**Deliverable:** a stripped skeleton in `template/`, with no templating at all.

Clone the most recent working project into `template/` and delete everything
domain-specific. What remains: backend, database layer, one migration, auth
stubs, healthcheck, one CI workflow, hosting blueprint, `docker-compose.yml`.
No Jinja, no variables, no conditionals.

**Gate:** the skeleton deploys from a clean clone. Deployment, not
compilation. A skeleton that builds but has never been deployed encodes
exactly the assumptions that break later in generated projects.

## Step 4: Parameterisation

**Effort:** 2 to 3 days.
**Deliverable:** `copier.yml` and a rendering template.

Question set: `project_name`, `package_name`, `database`, `mobile`,
`notifications`. Nothing beyond this until a real project demands it.

**Gate:** two distinct projects generated and both deployed. The second
generation is the entire test; a template validated by one generation is
validated by nothing.

## Step 5: Secret writer

**Effort:** 1 day.
**Deliverable:** resolver output written to GitHub environment secrets via the
GitHub CLI, and to the hosting provider's environment group.

**Gate:** a generated project's CI run consumes an injected credential
successfully, with no value having passed through a log or a tracked file.

Steps 1 to 5 constitute the working product. Everything after is expansion.

## Step 6: Local demo mode

**Effort:** 2 to 3 days.
**Deliverable:** `docker compose up` yielding a complete running system with
zero external dependencies, plus a seed loader and a synthetic data generator.

Highest work value of any remaining step, unblocked by vendor accounts, and
the only variant usable where third-party hosting is unacceptable. See
ADR-006.

**Gate:** clone, one command, working system with data at a scale that
survives follow-up questions.

## Step 7: GitHub provisioning

**Effort:** 1 to 2 days.
**Deliverable:** a Terraform module in `infra/`.

Repository creation, branch protection, environments, collaborators. State in
private encrypted object storage with locking. The CLI writes `terraform.tfvars`
from the answers; `terraform apply` is run by hand. Wrapping it is a later
convenience and a present source of opaque failures.

**Gate:** repository and environments exist entirely from `terraform apply`.

## Step 8: Hosting provisioning

**Effort:** 3 to 5 days. Highest uncertainty in the plan.

Cheap route first: commit the hosting blueprint, connect the repository once by
hand, let the provider manage services, environment groups and preview
environments. An hour of work, and it may remove the need for
infrastructure-as-code on the hosting side entirely. Reach for a provider only
if the blueprint route is demonstrably insufficient. Managed database creation
goes in a small wrapper over the vendor API, not through Terraform.

**Gate:** a generated project reaches a live URL with no console interaction
beyond the one-time repository connection.

## Step 9: Mobile pipeline

**Effort:** ~1 week. Worst return in the plan.

Deferred until a mobile application actually needs shipping. Highest rot rate
in the system, because the platform vendors change underneath it on their
schedule.

## Step 10: Update propagation

**Effort:** 1 to 2 days. Not exercisable in isolation; needs two downstream
repositories. Make a deliberate template change, propagate into both, confirm
neither breaks.

## Totals

| Scope | Hours | Calendar, part-time |
| --- | --- | --- |
| Steps 1 to 2 | 10 to 12 | two evenings |
| Steps 1 to 5 | 45 to 55 | three weekends |
| Steps 1 to 8 | 80 to 100 | five to six weekends |
| Everything | 120 to 160 | several months |

Beyond step 8 the system carries permanent maintenance load proportional to the
number of vendors in its dependency graph. That load is not in the table and is
the real cost of the later steps.
