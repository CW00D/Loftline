variable "repository" {
  type = string
}

variable "description" {
  type    = string
  default = ""
}

variable "environments" {
  type = list(string)
}

variable "required_checks" {
  type    = list(string)
  default = ["api", "secrets"]
}

variable "collaborators" {
  type    = map(string)
  default = {}
}
