# One product, one repository (ADR-002), three long-lived branches promoted
# by merge (ADR-013). Everything here is derived from the project's name and
# a handful of lists; nothing is project-specific beyond that.
#
# The owner is whoever the provider's token belongs to. Under a personal
# account that is the account; under an organisation, set `owner` in the
# provider block of the root module.

resource "github_repository" "this" {
  name        = var.repository
  description = var.description
  visibility  = "private"

  # The first commit. Without it there is no branch to protect and no branch
  # to create the others from. The generated project is committed on top of
  # it, never force-pushed over it: staging and prod hold this commit too, and
  # a pull request between branches needs a commit in common.
  auto_init = true

  has_issues      = true
  has_wiki        = false
  has_projects    = false
  has_discussions = false

  allow_merge_commit = true
  allow_squash_merge = false
  allow_rebase_merge = false
  # Promotion pull requests carry no review, so "merge when green" is the
  # whole workflow: `gh pr merge --auto --merge` on a prod PR merges the
  # moment the required checks pass.
  allow_auto_merge       = true
  delete_branch_on_merge = false

  lifecycle {
    # Renaming a repository from here would break every clone and every
    # `copier update` in flight. Do it deliberately in GitHub, then here.
    prevent_destroy = true
  }
}

resource "github_repository_vulnerability_alerts" "this" {
  repository = github_repository.this.name
}

# --- branches ----------------------------------------------------------------
# auto_init creates the repository's default branch under GitHub's own default
# name. Rename it to dev, so no stray branch is left behind, then branch
# staging and prod from it.

resource "github_branch_default" "dev" {
  repository = github_repository.this.name
  branch     = "dev"
  rename     = true
}

resource "github_branch" "staging" {
  repository    = github_repository.this.name
  branch        = "staging"
  source_branch = github_branch_default.dev.branch
}

resource "github_branch" "prod" {
  repository    = github_repository.this.name
  branch        = "prod"
  source_branch = github_branch.staging.branch
}

# --- environments ------------------------------------------------------------
# Deployment environments hold the secrets Loftline writes (Step 5). Created
# empty here; the secret writer fills them. Protection rules on production
# are deliberately not set: under ADR-013 the gate is the prod branch, not the
# environment.

resource "github_repository_environment" "this" {
  for_each    = toset(var.environments)
  repository  = github_repository.this.name
  environment = each.value
}

# --- branch protection -------------------------------------------------------
# dev is unprotected: it is where work lands, and where the generated project
# is first committed on top of the auto_init commit.
#
# staging accepts pull requests only. prod accepts pull requests only and
# refuses to merge until the named CI checks have passed on the exact commit.
# Neither branch can be force-pushed or deleted, by anyone, admins included.
# A solo developer approves nothing (GitHub will not let you approve your own
# pull request), so the review count is zero; the requirement is the PR
# itself, which is what makes promotion a merge of the whole branch rather
# than a cherry-pick.

resource "github_branch_protection" "staging" {
  repository_id = github_repository.this.node_id
  pattern       = github_branch.staging.branch

  enforce_admins          = true
  allows_force_pushes     = false
  allows_deletions        = false
  required_linear_history = false

  required_pull_request_reviews {
    required_approving_review_count = 0
  }

  # Staging deploys on push, so a red pull request must not reach it. The
  # secrets job is not listed: it runs only on the push that follows.
  required_status_checks {
    strict   = true
    contexts = var.staging_required_checks
  }
}

resource "github_branch_protection" "prod" {
  repository_id = github_repository.this.node_id
  pattern       = github_branch.prod.branch

  enforce_admins          = true
  allows_force_pushes     = false
  allows_deletions        = false
  required_linear_history = false

  required_pull_request_reviews {
    required_approving_review_count = 0
  }

  required_status_checks {
    strict   = true
    contexts = var.required_checks
  }
}

# --- collaborators -----------------------------------------------------------

resource "github_repository_collaborator" "this" {
  for_each   = var.collaborators
  repository = github_repository.this.name
  username   = each.key
  permission = each.value
}
