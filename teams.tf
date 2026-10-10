locals {
  teams = try(local.yaml_config.teams.teams, {})

  # Production default: read-only spend/activity routes for human team members.
  prod_default_team_member_permissions = tolist([
    "/team/daily/activity",
    "/team/daily/activity/aggregated",
    "/spend/logs",
    "/spend/logs/v2",
  ])

  # Email domain for automatic soft-budget alert recipients derived from human
  # team members. Set `user_email_domain` in global.yaml (e.g. "example.com")
  # to enable automatic email derivation; omit it to disable the default.
  user_email_domain = try(local.yaml_config.global.user_email_domain, "")

  # Production environment name — set `prod_environment` in global.yaml to
  # control which environment activates production governance (budgets, tags,
  # permissions). Defaults to "prod".
  prod_environment = try(local.yaml_config.global.prod_environment, "prod")

  prod_default_soft_budget_alerting_emails = local.user_email_domain != "" ? {
    for team_key, members in local.team_members :
    team_key => [
      for member in members : "${member.principal}@${local.user_email_domain}"
      if member.principal_type == "user"
    ]
  } : {}
}

resource "litellm_team" "this" {
  for_each = local.teams

  team_alias      = each.value.team_alias
  organization_id = litellm_organization.this[each.value.organization].id
  models          = each.value.models
  max_budget      = try(each.value.max_budget, null)
  soft_budget = var.environment == local.prod_environment ? try(
    each.value.soft_budget,
    each.value.max_budget * 0.75,
    null
  ) : null
  soft_budget_alerting_emails = var.environment == local.prod_environment ? tolist(
    try(
      each.value.soft_budget_alerting_emails,
      try(each.value.max_budget, null) != null && local.user_email_domain != ""
      ? try(local.prod_default_soft_budget_alerting_emails[each.key], [])
      : [],
      []
    )
  ) : tolist([])
  budget_duration = try(each.value.budget_duration, null)
  tags = var.environment == local.prod_environment ? distinct(concat(
    [
      "environment:${var.environment}",
      "organization:${each.value.organization}",
      "team:${each.key}",
    ],
    try(each.value.tags, [])
  )) : tolist([])
  team_member_permissions = try(
    each.value.team_member_permissions,
    var.environment == local.prod_environment ? local.prod_default_team_member_permissions : tolist([])
  )
}
