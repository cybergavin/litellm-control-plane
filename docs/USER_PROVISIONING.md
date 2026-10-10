# User Provisioning via SSO

Human users are **not managed by this control plane's IaC**. Managing users in YAML
and applying them through OpenTofu creates unnecessary operational overhead,
scales poorly, and — critically — duplicates a job that your Identity Provider
(IdP) already does better.

Our philosophy for user provisioning is based on three mechanisms, in order of preference:

1. **Option 1 — SCIM Provisioning (Enterprise)**: The ideal solution. IdP continuously syncs user/groups.
2. **Option 2 — SSO Just-in-Time (JIT) Provisioning**: If SCIM presents challenges. Users are created automatically upon first login.
3. **Option 3 — Manual Workflow**: For more control or in the absence of an IdP. Use the provided GitHub Actions workflow.

---

## Why Not IaC for Users?

| Concern | IaC approach | IdP approach |
|---|---|---|
| Onboarding a new user | PR → review → merge → apply | Automatic on first SSO login |
| Offboarding a user | PR → review → merge → apply | SCIM deprovision or IdP group removal |
| Source of truth | `users.yaml` (duplicate) | Your IdP directory (authoritative) |
| Scale (200 users) | 200 YAML entries, 200 PR lines | Zero entries |
| Audit trail | Git history | IdP audit log (built-in) |

---

## Option 1 — SCIM Provisioning (Enterprise)

**Requires a LiteLLM Enterprise license.** SCIM provides continuous,
bidirectional lifecycle management — users and teams are created, updated, and
deprovisioned in LiteLLM whenever your IdP directory changes. No login is
required to trigger provisioning.

> **Reference:** [LiteLLM Enterprise — SSO & SCIM](https://docs.litellm.ai/docs/proxy/enterprise#sso--scim)

### Supported Identity Providers

- Microsoft Entra ID (Azure AD)
- Okta
- Google Workspace
- Keycloak
- OneLogin
- Auth0

### Setup steps

1. **Generate SCIM credentials** in the LiteLLM Admin UI:
   `Settings → Admin Settings → SCIM`

   Note the **SCIM Tenant URL** and the generated **Bearer Token**.

2. **Configure provisioning in your IdP.** Example for Entra ID:
   - Open your LiteLLM Enterprise Application in the Azure Portal.
   - Navigate to **Provisioning → Get started**.
   - Set **Provisioning Mode** to `Automatic`.
   - Paste the SCIM Tenant URL into **Tenant URL** and the Bearer Token into
     **Secret Token**.
   - Click **Test Connection**, then **Save**.

3. **Assign users and groups** to the LiteLLM Enterprise Application in your
   IdP. SCIM will begin syncing immediately.

### What SCIM does and does not manage

| Resource | SCIM manages? |
|---|---|
| User create / update / deprovision | ✅ Yes |
| Group (team) create / update / delete | ✅ Yes |
| Group membership | ✅ Yes |
| Virtual API keys | ❌ No — keys are managed by this IaC control plane |
| Model configuration | ❌ No — managed by this IaC control plane |
| Team budgets and spend limits | ❌ No — managed by this IaC control plane |

This is the correct split of responsibilities: **SCIM owns identity**,
**IaC owns governance configuration**.

---

## Option 2 — SSO Just-in-Time (JIT) Provisioning

**Available to all LiteLLM tiers.** Users are automatically created in LiteLLM
the first time they sign in through your SSO provider. No pre-provisioning
is required.

> **⚠️ Disadvantage:** SSO JIT cannot automatically offboard or remove users. While SCIM or the manual workflow can handle deletions, removing a user provisioned via SSO JIT requires manual intervention.

> **Reference:** [LiteLLM SSO / UI docs](https://docs.litellm.ai/docs/proxy/ui)

### How it works

1. A user authenticates against your OIDC provider (Entra ID, Okta, Google, etc.).
2. LiteLLM reads the JWT, creates a user record on first login, and assigns the
   default role and parameters you configured.
3. Optionally, LiteLLM reads a `groups` (or custom) claim from the JWT and adds
   the user to matching LiteLLM teams automatically.

### Required LiteLLM `config.yaml` settings

```yaml
general_settings:
  # Default role and parameters applied to every new SSO user on first login.
  default_internal_user_params:
    user_role: "internal_user_viewer"   # safest production default
    # max_budget: 0                     # uncomment to prevent personal key usage

  # Map a JWT claim to LiteLLM team IDs for automatic group-based team assignment.
  # The claim must contain an array of strings that match the team_id values
  # created by this control plane (the YAML map keys in teams.yaml).
  litellm_jwtauth:
    team_ids_jwt_field: "groups"        # the JWT claim carrying group membership
```

### Team mapping requirement

LiteLLM does **not** create teams from JWT claims — it only adds the user to
teams that already exist in its database. Our IaC creates the teams; the
`team_id` value LiteLLM uses is the YAML map key from `teams.yaml`
(e.g. `engineering-platform`).

Ensure your IdP group identifiers match those map keys exactly, or use your
IdP's group-claim mapping to emit the correct values.

### OIDC provider configuration snippets

**Microsoft Entra ID** — add an optional claim `groups` (security groups) to
your app registration token configuration, or use a `groupMembershipClaims`
manifest setting. Emit the group object IDs or display names and map them in
your IdP to the LiteLLM team IDs.

**Okta** — add a Groups claim to your OIDC application and filter by the groups
that correspond to LiteLLM teams.

**Generic OIDC** — configure the `groups` scope or a custom claim in your
authorization server and ensure the values match LiteLLM team IDs.

---

## Option 3 — Manual Workflow

If you do not have an SSO provider, do not have JIT capability, or simply require explicit review and approval for user provisioning (e.g., for staging environments), use the automated workflow included in this repository.

1. **Trigger the Workflow**
   Navigate to the **Actions** tab in GitHub and select the `Human Users - Manage LiteLLM Access` workflow.
2. **Provide Details**
   Click **Run workflow**, set the **environment** (e.g., `staging`, `prod`), your **operation** (`create`, `update`, `delete`), and the user details (`username`, `display_alias`, `models`, etc.).
3. **Execution**
   The workflow securely delegates to the underlying Python scripts (which leverage the LiteLLM Admin API) to orchestrate the lifecycle of the user and dispatch a customizable welcome email if applicable.

---

## Role Assignment via OIDC Claims

Regardless of which provisioning method you use, you can centrally manage
LiteLLM roles by defining **App Roles** in your IdP and including them in
the OIDC JWT.

| LiteLLM role | Description |
|---|---|
| `proxy_admin` | Full administrative access |
| `proxy_admin_viewer` | Read-only access to all keys and spend |
| `internal_user` | Can create personal keys and view own spend |
| `internal_user_viewer` | Read-only access to own keys and spend (recommended production default) |

Configure your `config.yaml` to read the role from the JWT:

```yaml
general_settings:
  litellm_jwtauth:
    user_role_jwt_field: "roles"   # JWT claim containing the LiteLLM role string
```

---

## Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│  This IaC control plane manages:                                     │
│  Models · Organizations · Teams · Service Accounts · Workload Keys   │
├─────────────────────────────────────────────────────────────────────┤
│  Your IdP manages (via SSO JIT or SCIM):                             │
│  Human Users · Human Team Membership · Role Assignment               │
└─────────────────────────────────────────────────────────────────────┘
```

The teams created by this control plane are the anchor point. Your IdP maps
employees to those teams. The control plane enforces budget limits, model
restrictions, and workload key lifecycle on each team independently of how
many humans are in it.
