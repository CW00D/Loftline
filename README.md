# Loftline

A spec file becomes a running product: repository, CI, database, API, website,
mobile app, secrets, payments, domain. Loftline generates the project from an
opinionated template and then does the part scaffolders leave to you: it
creates the GitHub repository, writes every credential where CI and hosting
read them, registers vendor webhooks, points your domain at it and deploys.

```yaml
# loftline.yml
project_name: shop
package_name: shop
database: postgres
web: true
payments: [subscriptions]
domain: shop.example
environments: [staging]
```

```
loftline plan shop.yml        # what it needs, what you already hold, how to get the rest
loftline new shop.yml ./shop  # generate it
loftline provision shop.yml --repo you/shop   # secrets, webhooks, DNS, deploy
```

The centre of the tool is not the templating but the credential resolver.
From a spec it works out exactly which credentials the product needs, which
you already hold in your vault, which it can generate, which only exist once
something is provisioned, and which you must go and get, with the steps. It
never asks you for a value it could answer itself, and no credential value
ever passes through a file it tracks, a screen, or a log.

## What a generated project contains

- A FastAPI backend with email and password auth, on Postgres with Alembic
  migrations or on Neo4j, with a complete local system under Docker Compose
  and tests against a real database.
- Optional overlays, each its own set of files: a Vite and React website, an
  Expo app for iOS and Android, push notifications, one-off checkout and
  rolling subscriptions through Stripe.
- Three long-lived branches promoted by pull request, one CI workflow, a
  Render blueprint that creates the services and databases, and a thin
  Terraform module for the repository.

## Install

See [docs/setup.md](docs/setup.md), or the install page on the dashboard.
In short: `uv tool install git+https://github.com/CW00D/Loftline`, the tools
it drives (sops, age, gh, terraform, docker), and `loftline vault init`.

## The dashboard

[loftline.org](https://staging.loftline.org) shows a team its projects: what
each was generated from, where it is deployed and whether it is up, and who
should be on its repository. It holds intent and never a credential; the
administrator's machine, where the vault is, is what acts. `loftline sync`
and `loftline realise` are the bridge.

## Reading the repository

- `docs/decisions.md` records why things are the way they are. Read it before
  changing anything; it is the argument, not the code, that keeps the tool
  coherent.
- `docs/credentials.md` describes the credential model.
- `docs/roadmap.md` is the plan and its gates.
- `template/` is the product. `cli/` is the resolver, generator and
  provisioner. `infra/` holds the Terraform module generated projects use.

## The name

In boatbuilding, lofting scales the designer's drawings to full size; every
pattern and mould is taken from the resulting lines. The template is the lines
plan. Generated projects are hulls.

MIT licensed.
