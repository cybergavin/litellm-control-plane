terraform {
  required_version = ">= 1.12.6, < 1.13.0"

  required_providers {
    litellm = {
      source  = "registry.terraform.io/BerriAI/litellm"
      version = "1.101.0"
    }
  }
}

# Authentication is supplied at runtime through LITELLM_API_BASE and
# LITELLM_API_KEY. Credentials must not be stored in YAML or OpenTofu code.
provider "litellm" {}
