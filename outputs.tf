output "service_account_ids" {
  description = "LiteLLM user IDs for all provisioned service accounts."
  value = {
    for k, v in litellm_user.service_account : k => v.id
  }
}

output "service_account_key_aliases" {
  description = "Virtual key aliases for production service-account keys (production environment only)."
  value = {
    for k in keys(local.service_accounts) : k => k
    if var.environment == local.prod_environment
  }
}

output "team_ids" {
  description = "LiteLLM team IDs keyed by stable team identifier."
  value = {
    for k, v in litellm_team.this : k => v.id
  }
}

output "organization_ids" {
  description = "LiteLLM organization IDs keyed by stable organization identifier."
  value = {
    for k, v in litellm_organization.this : k => v.id
  }
}
