locals {
  organizations = try(local.yaml_config.organizations.organizations, {})
}

resource "litellm_organization" "this" {
  for_each = local.organizations

  organization_alias = each.value.organization_alias
  metadata           = try(each.value.metadata, null)
  models             = try(each.value.models, ["all-proxy-models"])
  max_budget         = try(each.value.max_budget, null)
  budget_duration    = try(each.value.budget_duration, null)
  tpm_limit          = try(each.value.tpm_limit, null)
  rpm_limit          = try(each.value.rpm_limit, null)
  blocked            = try(each.value.blocked, null)
}
