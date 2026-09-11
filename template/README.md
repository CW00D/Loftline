# Skeleton

A FastAPI backend on Neo4j with self-hosted email and password auth, a
healthcheck, a complete local system under Docker Compose, a Render blueprint
for two hosted environments, and one CI workflow. No application logic. This
is the substrate a product sits on.

## Running it locally

The whole system, with no external dependency:

```
docker compose up -d
python api/seed_dev.py
```

The API is at http://localhost:8000 and the Neo4j browser at
http://localhost:7474 (login `neo4j` / `skeleton`). The seed creates one user,
`dev@skeleton.test` with password `skeleton`. `GET /health` should answer
`{"ok": true, "db": true}`.

To work on the API with reloading, run only the database in Docker:

```
docker compose up -d neo4j
cd api
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt     # macOS and Linux: .venv/bin/pip
copy .env.example .env                                # macOS and Linux: cp
.venv\Scripts\uvicorn main:app --reload
```

## Tests

```
cd api
pytest
```

They run against the Neo4j that Docker Compose starts and they wipe it. They
refuse to run against a hosted instance.

## What is here

| Path | Purpose |
| --- | --- |
| `api/main.py` | The app, the lifespan that applies the schema, `/health` |
| `api/auth.py` | Signup, login, `/me`, password change, forgot and reset by emailed code, delete |
| `api/schema.py` | Constraints, applied on every boot. This is the migration mechanism. |
| `api/db.py` | The driver singleton and the production guard |
| `api/security.py` | Argon2 hashing, JWT encode and decode, reset codes |
| `api/emails.py` | SMTP delivery on a daemon thread; logs the code when unconfigured |
| `api/seed_dev.py` | Wipes the local database and creates one user |
| `api/tests/` | Every endpoint, against a real graph |
| `docker-compose.yml` | Neo4j and the API, for local development |
| `render.yaml` | Two Render services, one per deploying branch |
| `.github/workflows/ci.yml` | Lint, test against a Neo4j service container, build the image |

## Branches

Three long-lived branches. Feature branches come off `dev`. Promotion is a
merge of the whole branch, never a cherry-pick; a hotfix goes to `prod` and
is merged straight back down through `staging` to `dev`.

| Branch | What happens on push |
| --- | --- |
| `dev` | CI. Nothing is deployed; development is local. |
| `staging` | CI, then Render deploys `skeleton-api-staging`. |
| `prod` | CI, then Render deploys `skeleton-api-prod`. |

## Configuration

Every value comes from the environment. Locally, `api/.env` (copied from
`api/.env.example`, gitignored). Hosted, from the platform's environment,
written there by Loftline. Nothing secret is ever in this repository.

| Variable | Purpose |
| --- | --- |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | The graph |
| `JWT_SECRET` | Signs session tokens. Generated per project and per environment. |
| `SMTP_USER`, `SMTP_PASSWORD` | Sender for password reset codes. Optional locally. |
| `SMTP_HOST`, `SMTP_PORT` | Default to Gmail on 587 |

## Deploying

Connect the repository to Render once by hand: New, Blueprint, pick this
repository. Render reads `render.yaml` and creates both services. Set the
environment variables on each, or let Loftline write them. From then on a push
to `staging` or `prod` deploys.
