variable "environment" {
  description = "LiteLLM control-plane environment (e.g. dev, staging, prod)"
  type        = string

  validation {
    condition     = length(var.environment) > 0
    error_message = "Environment must be a non-empty string."
  }
}

locals {
  yaml_files = fileset("${path.root}/environments/${var.environment}", "*.yaml")

  yaml_config = {
    for filename in local.yaml_files :
    trimsuffix(basename(filename), ".yaml") => yamldecode(
      file("${path.root}/environments/${var.environment}/${filename}")
    )
  }
}
