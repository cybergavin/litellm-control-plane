locals {
  team_members = try(local.yaml_config.team_members.team_members, {})
}

# Adds service-account workload identities to their owning teams.
# Human users are NOT managed here — they are provisioned automatically by
# your Identity Provider via SSO JIT provisioning or SCIM sync.
# See docs/USER_PROVISIONING.md for the recommended approach.
resource "litellm_team_member_add" "this" {
  for_each = local.team_members

  team_id = litellm_team.this[each.key].id

  dynamic "member" {
    for_each = each.value

    content {
      user_id = litellm_user.service_account[member.value.principal].id
      role    = "user"
    }
  }
}
