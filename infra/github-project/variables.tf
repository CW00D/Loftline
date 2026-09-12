variable "repository" {
  description = "Repository name. The project's name; the owner comes from the token."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,38}$", var.repository))
    error_message = "A lowercase slug of letters, digits and hyphens, starting with a letter."
  }
}

variable "description" {
  description = "Repository description shown on GitHub."
  type        = string
  default     = ""
}

variable "environments" {
  description = "GitHub deployment environments to create. Secrets are written per environment."
  type        = list(string)
  default     = ["staging", "production"]
}

variable "required_checks" {
  description = "CI job names that must pass before prod accepts a merge. Names, not workflow files."
  type        = list(string)
  default     = ["api", "secrets"]
}

variable "staging_required_checks" {
  description = "CI job names that must pass before staging accepts a merge."
  type        = list(string)
  default     = ["api"]
}

variable "collaborators" {
  description = "GitHub usernames to add as collaborators, with their permission."
  type        = map(string) # username => pull | triage | push | maintain | admin
  default     = {}
}
