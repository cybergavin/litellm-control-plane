# Human user provisioning is intentionally absent from this control plane's IaC.
#
# Our philosophy for user provisioning in LiteLLM is:
#
#   1. Ideal solution: SCIM with IdP — your IdP (Entra ID, Okta, etc.) continuously
#      syncs user and group lifecycle events to LiteLLM (Requires Enterprise).
#
#   2. If there are challenges with SCIM: SSO Just-in-Time (JIT) provisioning — 
#      users are created in LiteLLM on first login. Configure `default_internal_user_params` 
#      and `litellm_jwtauth` in your LiteLLM config.yaml.
#
#   3. For more control or in the absence of SSO: Use the manual GitHub Actions workflow
#      provided in this repo (.github/workflows/litellm-human-user.yml) and its
#      underlying scripts to manage users directly.
#
# See docs/USER_PROVISIONING.md for the full implementation guide.
