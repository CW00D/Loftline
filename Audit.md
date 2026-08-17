# Setup audit

**Status:** Not started.

This document precedes all code. It is the only step capable of concluding that
Loftline should not be built, which makes it the highest-leverage work in the
project. Budget two hours.

## What this establishes

The premise of Loftline is that two to three days are lost to setup on every
new application. That premise is not in doubt. What is in doubt is the
*composition* of those days.

If the hours were spent on repository structure, boilerplate, CI and
infrastructure configuration, they are automatable and the provisioner is
justified.

If the hours were spent in vendor consoles, on enrolment forms, waiting for DNS
and fighting certificates, they are account-plane, permanently manual, and
**already paid for once**. In that case the correct artefact is a checklist
plus a single shared set of accounts, and building a provisioner is theatre.

## Method

Reconstruct from evidence rather than memory. Memory of tedious work is
systematically distorted toward whatever was most frustrating, which is not
the same as whatever consumed the most time.

**Source 1: commit history.** For each prior project:

```bash
git log --reverse --date=short --pretty=format:'%ad %h %s' | head -80
```

The first three weeks are the relevant window. Infrastructure and configuration
commits are the signal; feature commits mark the point at which setup ended.

**Source 2: the mailbox.** A better record of the account plane than any
repository. Search for confirmation and verification mail from Apple, Expo,
the hosting provider, the DNS provider, the registrar, and the secrets manager.
Dated evidence of what was fought with, in what order, and how long each item
blocked.

**Source 3: browser history**, if retained, for the vendor console sessions
that left no other trace.

## Classification

Every step receives exactly one classification.

**Automatable.** Repository structure, boilerplate code, CI workflows,
migrations, environment configuration, anything expressible in Terraform or a
hosting blueprint. Loftline addresses this.

**Account plane.** Enrolment, identity verification, payment methods, DNS,
certificates, store review, anything requiring a first login with 2FA.
Loftline cannot address this, and it does not recur.

**Done forever.** Carried across all future projects regardless of tooling.
Neither a cost nor a saving from here on.

## Findings

### BeerReel

| Step | Hours | Classification | Notes |
| --- | --- | --- | --- |
|  |  |  |  |

### UniSoc

| Step | Hours | Classification | Notes |
| --- | --- | --- | --- |
|  |  |  |  |

### Uncle's CRM

Include this one. It is the only case with a user who is not the author, and
therefore the only evidence about the non-technical audience. Record separately
whether the value delivered was the tooling or the labour, because the answer
determines whether a product exists there or only a service.

| Step | Hours | Classification | Notes |
| --- | --- | --- | --- |
|  |  |  |  |

## Totals

| Classification | BeerReel | UniSoc | CRM | Total |
| --- | --- | --- | --- | --- |
| Automatable |  |  |  |  |
| Account plane |  |  |  |  |
| Done forever |  |  |  |  |

## Decision rule

Fix this before filling in the table, so the rule is not fitted to the result.

- **Automatable hours exceed account-plane hours:** proceed to extraction.
  Expected saving per project is the automatable total, less generation and
  verification time.
- **Account-plane hours dominate:** do not build the provisioner. Write a
  setup checklist, consolidate to one shared set of accounts, and stop. Revisit
  only if a future project introduces genuinely new automatable surface.
- **Neither dominates:** build the template and the local demo mode. Defer
  Terraform provisioning until a third project demands it.

## Conclusion

*To be written after the table is complete. State the decision, the number it
rests on, and the date.*