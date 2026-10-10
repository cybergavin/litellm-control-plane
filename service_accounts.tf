locals {
  service_accounts = try(local.yaml_config.service_accounts.service_accounts, {})
}

resource "litellm_user" "service_account" {
  for_each = local.service_accounts

  user_id    = each.key
  user_alias = each.value.user_alias
  user_role  = "internal_user"

  # Keys use a separate write-only resource so their values never enter state.
  auto_create_key   = false
  send_invite_email = false

  lifecycle {
    # Team membership is owned by litellm_team_member_add.
    ignore_changes = [teams]
  }
}

# Create one production workload key per service account. LiteLLM stores each
# generated key via its configured virtual-key backend (e.g. AWS Secrets
# Manager). Only created in the production environment.
resource "litellm_key" "service_account" {
  for_each = var.environment == local.prod_environment ? local.service_accounts : {}

  user_id   = litellm_user.service_account[each.key].id
  team_id   = litellm_team.this[each.value.team].id
  key_alias = each.key

  # Ensure the key owner is a member of the team before the key is generated.
  depends_on = [litellm_team_member_add.this]
}
