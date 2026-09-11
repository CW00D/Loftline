# github-project

A Terraform module that turns a project name into its GitHub repository: the
private repo, `dev` as the default branch, `staging` and `prod` branched from
it, the deployment environments, and the branch protection that makes
promotion a pull request from one branch to the next (ADR-013).

Generated projects consume it from `infra/main.tf`; they never carry a copy.

## Inputs

| Name | Purpose | Default |
| --- | --- | --- |
| `repository` | The repository name | required |
| `description` | Shown on GitHub | `""` |
| `environments` | Deployment environments to create | `["staging", "production"]` |
| `required_checks` | CI job names `prod` waits for | `["api", "secrets"]` |
| `collaborators` | `username => permission` | `{}` |

The owner is whoever the provider's token belongs to.

## Authentication

The GitHub provider reads `GITHUB_TOKEN`. The GitHub CLI already holds one:

```
$env:GITHUB_TOKEN = gh auth token     # PowerShell
export GITHUB_TOKEN=$(gh auth token)  # bash
```

No new credential, nothing stored.

## What it deliberately does not do

- Rename an existing repository. `prevent_destroy` is set; a rename is done
  in GitHub first, then here.
- Set environment protection rules. Under ADR-013 the gate is the `prod`
  branch, which requires a pull request and green CI.
- Push the project's code. Terraform creates the repository with one initial
  commit; the generated project is committed on top of that commit on `dev`
  and reaches `staging` and `prod` by pull request. Never force-push over the
  initial commit: `staging` and `prod` hold it, and a pull request between
  branches needs a commit in common.
