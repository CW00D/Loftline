output "repository" {
  description = "OWNER/NAME, as `gh` and `loftline secrets write --repo` expect it."
  value       = github_repository.this.full_name
}

output "clone_url" {
  description = "HTTPS clone URL. Add it as `origin` in the generated project."
  value       = github_repository.this.http_clone_url
}

output "default_branch" {
  value = github_branch_default.dev.branch
}

output "environments" {
  value = sort([for e in github_repository_environment.this : e.environment])
}
