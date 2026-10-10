# LiteLLM Control Plane

**GitOps-driven configuration management for [LiteLLM](https://github.com/BerriAI/litellm) using [OpenTofu](https://opentofu.org/).**

Manage LiteLLM models, organizations, teams, and service-account keys as code — reviewed in pull requests, applied through a human-approved CI/CD pipeline, and stored in versioned YAML files. Human user access is decoupled from IaC, managed either via Identity Provider integration (SSO JIT or SCIM) or through a dedicated, policy-enforcing GitHub Actions workflow with automated onboarding.

---

## Documentation

| Document | Description |
|---|---|
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | Mermaid sequence diagrams for all three CI/CD workflows and the end-to-end promotion path |
| [docs/USER_PROVISIONING.md](docs/USER_PROVISIONING.md) | Human user provisioning philosophy (SSO JIT, SCIM, automated workflow) and IdP configuration guide |

## Overview

This repository implements a declarative, YAML-driven control plane for a LiteLLM AI Gateway. It is designed to be:

- **Provider-agnostic** — works with any LiteLLM deployment (AWS, GCP, Azure, on-prem).
- **Environment-aware** — supports any number of named environments (e.g. `staging`, `prod`).
- **Secure by default** — credentials never appear in YAML or HCL; the API key is fetched from AWS Secrets Manager at runtime via OIDC.
- **Auditable** — every infrastructure change is a reviewed pull request; destructive plans require explicit approval. Every user management operation is logged with a structured JSON audit trail.
- **Policy-constrained identity** — human user access is strictly segregated by environment (self-service sandbox in staging vs. read-only team observer in production).

> **Note:** This repository serves as a starter template for a LiteLLM control plane. While it covers the structural core (models, teams, organizations, keys, and user lifecycle), it can easily be expanded to support any other functionality provided by the LiteLLM Terraform provider (e.g., `access_groups`, `mcp_servers`). Please refer to the [official LiteLLM Terraform Provider documentation](https://registry.terraform.io/providers/BerriAI/litellm/latest/docs) for a full list of resources.

### Environments

The control plane is natively environment-aware, typically starting with two:
- **`staging`**: A non-production environment for safe model testing and integration. Operates with loose governance: human users are granted `internal_user` roles to generate personal virtual keys, experiment with sandbox models, and receive automated onboarding welcome emails.
- **`prod`**: The production environment with strict governance. Team budgets and member permissions are enforced, applications authenticate via explicit workload keys (see `service_accounts` and `team_members`), and human users are strictly limited to read-only observer roles (`internal_user_viewer`) with zero personal keys.

## Repository Structure

```
.
├── backend.tf                  # Remote state backend (configure for your cloud)
├── provider.tf                 # LiteLLM Terraform provider
├── global.tf                   # Environment variable + YAML loader
├── models.tf                   # litellm_model resources + UI model import scaffold
├── organizations.tf            # litellm_organization resources
├── teams.tf                    # litellm_team resources (budget & spend enforcement)
├── users.tf                    # Explains human user provisioning architecture (no IaC resources)
├── service_accounts.tf         # litellm_user + litellm_key for workload identities
├── team_members.tf             # litellm_team_member_add for service accounts only
├── outputs.tf                  # Useful outputs (team IDs, org IDs, key aliases)
├── docs/
│   ├── WORKFLOWS.md            # CI/CD flow diagrams (Mermaid)
│   └── USER_PROVISIONING.md    # SSO JIT, SCIM, and automated workflow guide
├── environments/
│   ├── staging/                # Non-production environment
│   │   ├── global.yaml         # Environment settings (prod_environment, user_email_domain)
│   │   ├── models.yaml         # Models available in staging
│   │   ├── organizations.yaml  # Staging organization boundary
│   │   └── users.yaml          # Informational placeholder (users managed outside IaC)
│   └── prod/                   # Production environment
│       ├── global.yaml
│       ├── models.yaml
│       ├── organizations.yaml
│       ├── teams.yaml
│       ├── team_members.yaml   # Service accounts only
│       ├── service_accounts.yaml
│       └── users.yaml          # Informational placeholder (users managed outside IaC)
├── schemas/                    # JSON Schema validators for all YAML files
├── scripts/
│   ├── check_tofu_plan.py      # Rejects replacement/mixed destroy actions in CI
│   ├── manage_litellm_user.py  # Policy-constrained human user management via LiteLLM API
│   ├── send_litellm_staging_welcome_email.py # Staging user onboarding email dispatcher
│   ├── templates/
│   │   └── litellm_staging_welcome.html      # Welcome email HTML template
│   ├── validate_litellm.py     # Post-promotion health and auth checks
│   ├── pc_lint.sh              # pre-commit: yamllint
│   ├── pc_validate.sh          # pre-commit: JSON Schema validation
│   └── pc_scan.sh              # pre-commit: Checkov security scan
└── .github/workflows/
    ├── litellm-control-plane-plan.yml      # Plan workflow (PR / manual dispatch)
    ├── litellm-control-plane-apply.yml     # Apply workflow (human approval gate)
    ├── litellm-control-plane-validate.yml  # Post-promotion health validation
    └── litellm-human-user.yml              # Human user access management & onboarding
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

> **Human users** are not managed in IaC files. See [Human User Management](#human-user-management) and [docs/USER_PROVISIONING.md](docs/USER_PROVISIONING.md) for how users are provisioned via IdP or the automated GitHub Actions workflow.

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

```yaml
team_members:
  engineering-platform:
    - principal: "svc-my-app"   # Must match a key in service_accounts.yaml
```

### `users.yaml`

This file is an **informational placeholder**. Human users are **not managed in YAML or OpenTofu state**. Any entries in this file are ignored by OpenTofu. See [Human User Management](#human-user-management) below for details.

---

## Human User Management

### Provisioning Philosophy

Managing individual human users in declarative IaC creates operational friction: it exposes PII (usernames, email addresses) in git history, causes state churn, and risks locking Terraform state during routine onboarding or offboarding.

Our user management philosophy operates across three tiers:

1. **Option 1 — SCIM Provisioning (Enterprise)**: Ideal solution. Continuous, bidirectional identity lifecycle sync driven by your Identity Provider (Entra ID, Okta, Google Workspace, etc.).
2. **Option 2 — SSO Just-in-Time (JIT) Provisioning (All tiers)**: Users are automatically provisioned in LiteLLM on first login via OIDC/SAML claims.
3. **Option 3 — Automated Workflow (`litellm-human-user.yml`)**: Policy-enforced user management via GitHub Actions and the LiteLLM Admin API. Ideal when SCIM/SSO is unavailable, or when explicit approval, auditing, and automated onboarding notifications are required.

See **[docs/USER_PROVISIONING.md](docs/USER_PROVISIONING.md)** for detailed IdP configuration guidance (Entra ID, Okta, OIDC claims, and LiteLLM `config.yaml` settings).

### The Human User Management Workflow

For environments requiring explicit access control, the repository provides a dedicated, audited GitHub Actions workflow: [`.github/workflows/litellm-human-user.yml`](.github/workflows/litellm-human-user.yml), powered by [`scripts/manage_litellm_user.py`](scripts/manage_litellm_user.py) and [`scripts/send_litellm_staging_welcome_email.py`](scripts/send_litellm_staging_welcome_email.py).

#### Key Workflow Operations

- **`create`**: Provisions a new user in LiteLLM (`POST /user/new`), assigns the environment-appropriate role, binds models (staging) or teams (prod), verifies policy compliance, and automatically dispatches a branded welcome email for staging users.
- **`update`**: Updates display name, email, or role (`POST /user/update`), and reconciles team memberships (`POST /team/member_add`, `POST /team/member_delete`). Supports legacy users via the `current_email` lookup parameter.
- **`delete`**: Permanently removes a user (`POST /user/delete`). Requires explicit confirmation (`confirm_delete: true`).

#### Environment Governance & Policy Matrix

The workflow enforces strict, environment-specific governance rules via `manage_litellm_user.py`:

| Policy Dimension | Staging (`staging`) | Production (`prod`) |
|---|---|---|
| **LiteLLM Role** | `internal_user` (self-service sandbox) | `internal_user_viewer` (read-only observer) or `proxy_admin_viewer` |
| **Team Membership** | **Teamless** (users must not be assigned to teams) | **Team-bound** (must belong to $\ge 1$ existing LiteLLM teams) |
| **Direct Model Access** | Allowed. Resolves exact models, model creator prefixes (`openai`, `anthropic`), or `all-proxy-models` against active models | **Strictly prohibited** (must omit models; model access is governed by team assignment) |
| **Virtual Keys** | Allowed (users generate self-service test keys with 30-day expiry) | **Zero keys permitted** (enforced $\text{key\_count} = 0$; production inference uses service-account keys only) |
| **Onboarding Notification** | Automated HTML welcome email dispatched upon creation | None (observers access team spend dashboards) |

#### Workflow Inputs Reference

The workflow is triggered via **Actions → Human Users - Manage LiteLLM Access → Run workflow** (restricted to `main`):

| Input | Required | Description |
|---|---|---|
| `environment` | Yes | Target LiteLLM environment (`staging` or `prod`) |
| `operation` | Yes | Lifecycle operation (`create`, `update`, `delete`) |
| `username` | Yes | Human user identifier (lowercase alphanumeric, e.g. `jdoe`) |
| `display_alias` | Yes | Display name containing at least first and last name (e.g. `Jane Doe`) |
| `email` | No | Optional email override (defaults to `<username>@example.com` on create) |
| `current_email` | No | Legacy users only: current email used for exact lookup on update/delete |
| `models` | No | **STAGING only**: comma-separated creators (`openai`, `anthropic`), exact models, or `all-proxy-models`. Blank defaults to `all-proxy-models` on create |
| `teams` | No | **PROD only**: comma-separated exact LiteLLM team aliases (e.g. `engineering-platform`). Required on create |
| `change_reference` | Yes | Mandatory change, ticket, or incident reference (e.g. `CHG-1042`, `REQ-5512`) |
| `confirm_delete` | No | Safety confirmation flag; must be `true` when `operation` is `delete` (default: `false`) |

#### Automated Staging Onboarding Notification

When a user is created in `staging`, the workflow automatically triggers the `notify-staging-create` job:
1. Runs [`scripts/send_litellm_staging_welcome_email.py`](scripts/send_litellm_staging_welcome_email.py).
2. Renders [`scripts/templates/litellm_staging_welcome.html`](scripts/templates/litellm_staging_welcome.html) with personalized recipient details.
3. Connects to internal SMTP (`smtp.example.com:25`) and dispatches the welcome email, CC'ing the platform team configured in `LITELLM_STAGING_WELCOME_CC_ADDRESSES`.
4. Informs the user of the staging dashboard URL, self-service personal key management (30-day expiry), $10 monthly sandbox allowance, and guidelines for requesting production activation.

#### Auditability & Policy Verification

Every execution of the user management workflow:
- Enforces concurrency locking (`litellm-human-user-${environment}-${username}`) to avoid race conditions.
- Validates the requested change against live LiteLLM state (`GET /user/list`, `GET /user/info`, `GET /team/list`, `GET /v1/models`).
- Re-verifies policy compliance after mutation via `assert_policy`.
- Emits a structured JSON audit record to workflow logs (`LITELLM_USER_AUDIT={...}`), recording timestamp, actor, change reference, target identity, completed mutation steps, and post-verification policy compliance. API keys and secrets are never logged.

#### CLI & Script Execution

The management scripts can also be run locally or in automation:

```bash
# Export credentials
export LITELLM_API_BASE="https://aigateway-staging.example.com"
export LITELLM_API_KEY="sk-..."

# Create a staging user with model creator expansion
python3 scripts/manage_litellm_user.py \
  --operation create \
  --environment staging \
  --username jdoe \
  --display-alias "Jane Doe" \
  --models "openai,anthropic" \
  --change-reference "REQ-1001" \
  --actor "admin" \
  --request-id "req-12345"

# Create a production user bound to teams
python3 scripts/manage_litellm_user.py \
  --operation create \
  --environment prod \
  --username jdoe \
  --display-alias "Jane Doe" \
  --teams "engineering-platform" \
  --change-reference "CHG-2002" \
  --actor "admin" \
  --request-id "req-12346"

# Or execute from a canonical JSON request file:
python3 scripts/manage_litellm_user.py --request-file request.json
```

---

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

---

## GitHub Actions Workflows

Four workflows automate control-plane operations and user lifecycle management:

| Workflow | Type / Trigger | Purpose |
|---|---|---|
| `litellm-control-plane-plan.yml` | Reusable / PR / `workflow_dispatch` | Runs `tofu plan` with replacement protection and destructive change detection |
| `litellm-control-plane-apply.yml` | Reusable / `workflow_dispatch` | Human-approved `tofu apply` gated by GitHub Environment review |
| `litellm-control-plane-validate.yml` | Reusable / Post-apply / `workflow_dispatch` | Read-only health, readiness, redis cache, and model auth checks |
| `litellm-human-user.yml` | Operational / `workflow_dispatch` | Policy-constrained human user lifecycle management & onboarding |

### Required GitHub Variables (per environment)

| Variable | Scope | Description |
|---|---|---|
| `LITELLM_API_BASE` | All workflows | Base URL of the LiteLLM gateway |
| `LITELLM_API_KEY_SECRET_ID` | All workflows | AWS Secrets Manager secret ID holding the master API key |
| `AWS_ROLE_TFPLAN` | Plan, Validate | IAM role ARN assumed via OIDC for read-only plan and validation operations |
| `AWS_ROLE_TFAPPLY` | Apply, Human Users | IAM role ARN assumed via OIDC for write/apply operations |
| `AWS_DEFAULT_REGION` | All workflows | AWS region (e.g. `us-east-1` or `us-west-2`) |
| `LITELLM_STAGING_WELCOME_CC_ADDRESSES` | Human Users | Comma-separated email addresses CC'd on staging welcome emails |

### Approval Gate

The apply workflow pauses at the `approve` job and waits for a review from the GitHub Environment named `<env>-approval`. Create this environment in your repository **Settings → Environments** and configure required reviewers.

---

## Pre-commit Hooks

Install hooks to validate configurations locally before pushing:

```bash
pip install pre-commit yamllint check-jsonschema checkov
pre-commit install
```

The hooks execute:
- `scripts/pc_lint.sh` — checks YAML formatting and syntax via `yamllint`.
- `scripts/pc_validate.sh` — validates environment YAML configurations against `schemas/*_schema.yaml` via `check-jsonschema`.
- `scripts/pc_scan.sh` — scans Infrastructure as Code configurations via `checkov`.

---

## Security

- **No credentials in code.** API keys and secrets are never stored in YAML, HCL, or workflow files. The master key is retrieved from AWS Secrets Manager at runtime via GitHub OIDC roles.
- **Replacement protection.** `check_tofu_plan.py` rejects `create_before_destroy` replacements; only explicit deletes are allowed through the approval gate.
- **Decoupled human identity.** Human users do not exist in OpenTofu state, eliminating PII exposure in git and avoiding state lock contention.
- **Strict environment policy boundaries.** The user management workflow strictly isolates staging (self-service sandbox, teamless) from production (viewer-only observer, team-bound, zero personal keys).
- **Comprehensive audit trail.** All control-plane changes are pinned to immutable 40-character commit SHAs. User management operations require change references and emit structured JSON audit logs (`LITELLM_USER_AUDIT`).

---

## License

[MIT](LICENSE)
