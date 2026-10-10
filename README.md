# LiteLLM Control Plane

**GitOps-driven configuration management for [LiteLLM](https://github.com/BerriAI/litellm) using [OpenTofu](https://opentofu.org/).**

Manage LiteLLM models, organizations, teams, and service-account keys as code — reviewed in pull requests, applied through a human-approved CI/CD pipeline, and stored in versioned YAML files. Human users are provisioned automatically by your Identity Provider via SSO or SCIM.

---

## Documentation

| Document | Description |
|---|---|
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | Mermaid sequence diagrams for all three CI/CD workflows and the end-to-end promotion path |
| [docs/USER_PROVISIONING.md](docs/USER_PROVISIONING.md) | How to provision human users via SSO JIT and SCIM — and why they are not managed in IaC |

## Overview

This repository implements a declarative, YAML-driven control plane for a LiteLLM AI Gateway. It is designed to be:

- **Provider-agnostic** — works with any LiteLLM deployment (AWS, GCP, Azure, on-prem).
- **Environment-aware** — supports any number of named environments (e.g. `staging`, `prod`).
- **Secure by default** — credentials never appear in YAML or HCL; the API key is fetched from AWS Secrets Manager at runtime.
- **Auditable** — every change is a reviewed pull request; destructive plans require explicit approval.

> **Note:** This repository serves as a starter template for a LiteLLM control plane. While it covers the structural core (models, teams, organizations, and keys), it can easily be expanded to support any other functionality provided by the LiteLLM Terraform provider (e.g., `access_groups`, `mcp_servers`). Please refer to the [official LiteLLM Terraform Provider documentation](https://registry.terraform.io/providers/BerriAI/litellm/latest/docs) for a full list of resources.

### Environments

The control plane is natively environment-aware, typically starting with two:
- **`staging`**: A non-production environment for safe model testing and integration. Generally operates with looser governance, allowing more ad-hoc testing.
- **`prod`**: The production environment with strict governance. Here, team budgets are enforced, applications authenticate via explicit workload keys (see `service_accounts` and `team_members`), and human users are limited to read-only views of their team's spend.

## Repository Structure

```
.
├── backend.tf                  # Remote state backend (configure for your cloud)
├── provider.tf                 # LiteLLM Terraform provider
├── global.tf                   # Environment variable + YAML loader
├── models.tf                   # litellm_model resources
├── organizations.tf            # litellm_organization resources
├── teams.tf                    # litellm_team resources (budget enforcement)
├── users.tf                    # No resources — explains SSO provisioning approach
├── service_accounts.tf         # litellm_user + litellm_key for workload identities
├── team_members.tf             # litellm_team_member_add for service accounts only
├── outputs.tf                  # Useful outputs (team IDs, org IDs, key aliases)
├── docs/
│   ├── WORKFLOWS.md            # CI/CD flow diagrams (Mermaid)
│   └── USER_PROVISIONING.md    # SSO JIT and SCIM provisioning guide
├── environments/
│   ├── staging/                # Non-production environment
│   │   ├── global.yaml         # Environment settings (prod_environment, user_email_domain)
│   │   ├── models.yaml         # Models available in staging
│   │   └── organizations.yaml  # (typically empty for non-prod)
│   └── prod/                   # Production environment
│       ├── global.yaml
│       ├── models.yaml
│       ├── organizations.yaml
│       ├── teams.yaml
│       ├── team_members.yaml   # Service accounts only
│       └── service_accounts.yaml
├── schemas/                    # JSON Schema validators for all YAML files
├── scripts/
│   ├── check_tofu_plan.py      # Rejects replacement/mixed destroy actions in CI
│   ├── validate_litellm.py     # Post-promotion health and auth checks
│   ├── pc_lint.sh              # pre-commit: yamllint
│   ├── pc_validate.sh          # pre-commit: JSON Schema validation
│   └── pc_scan.sh              # pre-commit: Checkov security scan
└── .github/workflows/
    ├── litellm-control-plane-plan.yml      # Plan workflow (PR / manual)
    ├── litellm-control-plane-apply.yml     # Apply workflow (human approval gate)
    └── litellm-control-plane-validate.yml  # Post-promotion health validation
```

## Quick Start

### Prerequisites

| Tool | Version |
|---|---|
| OpenTofu | `>= 1.12.6, < 1.13.0` |
| LiteLLM Terraform provider | `1.101.0` |
| Python | `>= 3.10` |
| check-jsonschema | latest |
| yamllint | latest |
| Checkov | latest |
| pre-commit | latest |

### 1. Configure the backend

Edit `backend.tf` to point at your remote state store. The file ships with an S3-compatible scaffold and detailed comments. Example for S3:

```hcl
terraform {
  backend "s3" {
    bucket       = "my-tfstate-bucket"
    key          = "litellm-control-plane/prod/tofu.tfstate"
    encrypt      = true
    region       = "us-east-1"
    use_lockfile = true
  }
}
```

You can also pass backend config at init time:

```bash
tofu init \
  -backend-config="bucket=my-tfstate-bucket" \
  -backend-config="key=litellm-control-plane/prod/tofu.tfstate" \
  -backend-config="region=us-east-1"
```

### 2. Configure your environment YAML

Copy the `environments/prod` example and customize it for your organization:

```bash
cp -r environments/prod environments/my-env
```

Edit `environments/my-env/global.yaml`:

```yaml
# The environment name that triggers production governance.
prod_environment: "my-env"

# Domain used to derive alert email addresses for soft-budget notifications.
# e.g. "example.com" → team owner emails default to "<owner>@example.com"
user_email_domain: "example.com"
```

> **Human users** are not managed here. See [docs/USER_PROVISIONING.md](docs/USER_PROVISIONING.md) for how to configure SSO JIT provisioning or SCIM sync so that users are automatically provisioned by your Identity Provider.

### 3. Set required environment variables

```bash
# LiteLLM connection
export LITELLM_API_BASE="https://your-litellm-gateway.example.com"
export LITELLM_API_KEY="sk-..."

# OpenTofu environment
export TF_VAR_environment="my-env"
```

### 4. Plan and apply

```bash
tofu init
tofu validate
tofu plan -out=plan.tfplan
tofu apply plan.tfplan
```

## Environment Configuration Reference

### `global.yaml`

| Field | Required | Default | Description |
|---|---|---|---|
| `prod_environment` | No | `"prod"` | Environment name that activates production governance (team budgets, tags, strict key permissions) |
| `user_email_domain` | No | _(none)_ | Domain used to derive alert email addresses for soft-budget notifications |
| `preserved_model_imports` | No | `{}` | Ephemeral import map for UI-created models (see [Importing UI Models](#importing-ui-models)) |

### `models.yaml`

Declares LiteLLM model deployments. Each entry is a stable map key:

```yaml
models:
  openai-gpt-4o:
    model_name: "openai/gpt-4o"        # LiteLLM-facing model name
    custom_llm_provider: "openai"       # LiteLLM provider string
    base_model: "gpt-4o"               # Upstream model identifier
    credential_name: "openai_cred"     # Optional: credential alias in LiteLLM config
    mode: "chat"                        # chat | embedding | rerank | image_generation | ...
    tier: "paid"                        # free | paid (default: paid)
    rpm: 60                             # Optional: requests-per-minute limit
    input_cost_per_million_tokens: 2.50
    output_cost_per_million_tokens: 10.00
```

### `organizations.yaml`

Production governance boundary. Teams belong to organizations:

```yaml
organizations:
  engineering:
    organization_alias: "Engineering"
    models: ["all-proxy-models"]    # Optional parent allowance (default: all-proxy-models)
    max_budget: 1000
    budget_duration: "1mo"
```

### `teams.yaml`

Teams are the primary inference unit in production. Each team gets a workload key:

```yaml
teams:
  engineering-platform:
    team_alias: "Platform Engineering"
    organization: "engineering"
    models:
      - "openai/gpt-4o"
      - "openai/gpt-4o-mini"
    max_budget: 200           # USD per month
    budget_duration: "1mo"
    soft_budget: 150          # Optional: alert threshold (default: 75% of max_budget)
    soft_budget_alerting_emails:    # Optional: overrides auto-derived emails
      - "alerts@example.com"
    tags:
      - "cost_center:CC-1234"
```

### Human Users

Human users are **not managed in YAML or IaC**. Our philosophy for user provisioning is:

1. **Ideal solution**: **SCIM sync** — continuous lifecycle management driven by your IdP (Enterprise). 
2. **If there are challenges with SCIM**: **SSO JIT provisioning** — users appear in LiteLLM on first login (all tiers).
3. **For more control or in the absence of SSO**: **Manual GitHub Actions workflow** — manage users explicitly via the provided `.github/workflows/litellm-human-user.yml`.

See **[docs/USER_PROVISIONING.md](docs/USER_PROVISIONING.md)** for the full
implementation guide including `config.yaml` snippets, IdP setup steps, and manual workflow details.

### `service_accounts.yaml` _(production only)_

Non-interactive workload identities. Each gets one LiteLLM virtual key in production:

```yaml
service_accounts:
  svc-my-app:
    user_alias: "My Application"
    team: "engineering-platform"    # Must reference a key in teams.yaml
```

### `team_members.yaml` _(production only)_

Associates **service-account** workload identities with their owning teams.
Human team membership is handled by your IdP group-to-team mapping:

```yaml
team_members:
  engineering-platform:
    - principal: "svc-my-app"   # Must match a key in service_accounts.yaml
```

## GitHub Actions Workflows

Three reusable workflows are provided:

| Workflow | Trigger | Purpose |
|---|---|---|
| `litellm-control-plane-plan.yml` | PR / `workflow_dispatch` | `tofu plan` with destructive change detection |
| `litellm-control-plane-apply.yml` | `workflow_dispatch` after plan | Human-approved `tofu apply` |
| `litellm-control-plane-validate.yml` | Post-apply / `workflow_dispatch` | Health + auth checks against the live gateway |

### Required GitHub Variables (per environment)

| Variable | Description |
|---|---|
| `LITELLM_API_BASE` | Base URL of the LiteLLM gateway |
| `LITELLM_API_KEY_SECRET_ID` | AWS Secrets Manager secret ID holding the master API key |
| `AWS_ROLE_TFPLAN` | IAM role ARN assumed for read-only plan operations |
| `AWS_ROLE_TFAPPLY` | IAM role ARN assumed for apply operations |
| `AWS_DEFAULT_REGION` | AWS region (default: `us-east-1`) |

### Approval Gate

The apply workflow pauses at the `approve` job and waits for a review from the GitHub Environment named `<env>-approval`. Create this environment in your repository **Settings → Environments** and add required reviewers.

## Importing UI Models

If a model was created via the LiteLLM UI and its API configuration cannot be fully represented by the pinned provider version, use the `preserve_api_configuration` flag:

```yaml
# models.yaml
models:
  my-special-model:
    model_name: "my/special-model"
    custom_llm_provider: "openai"
    base_model: "special-model"
    preserve_api_configuration: true
```

Then add a temporary import entry to `global.yaml`:

```yaml
preserved_model_imports:
  my-special-model: "<litellm-model-uuid>"
```

Run `tofu plan` and `tofu apply` once to import the state, then remove the entry from `preserved_model_imports`.

## Pre-commit Hooks

Install hooks to validate locally before pushing:

```bash
pip install pre-commit yamllint check-jsonschema checkov
pre-commit install
```

## Security

- **No credentials in code.** API keys and secrets are never stored in YAML or HCL.
- **Replacement protection.** `check_tofu_plan.py` rejects `create_before_destroy` replacements; only explicit deletes are allowed through the approval gate.
- **No human identity in IaC.** Human users are provisioned by your IdP — they never appear in state, eliminating PII exposure and stale-user drift.

## License

[MIT](LICENSE)
