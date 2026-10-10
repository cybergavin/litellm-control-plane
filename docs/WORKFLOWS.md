# CI/CD Workflow Reference

This document describes the three GitHub Actions workflows that implement the
GitOps control plane for LiteLLM. All three workflows are **reusable**
(`workflow_call`) and can also be triggered **manually** (`workflow_dispatch`).

---

## Contents

1. [End-to-End Promotion Path](#1-end-to-end-promotion-path)
2. [Pull Request Flow](#2-pull-request-flow)
3. [Plan Workflow](#3-plan-workflow-litellm-control-plane-planyml)
4. [Apply Workflow](#4-apply-workflow-litellm-control-plane-applyyml)
5. [Validation Workflow](#5-validation-workflow-litellm-control-plane-validateyml)
6. [Security Controls Summary](#6-security-controls-summary)

---

## 1. End-to-End Promotion Path

This diagram shows how a configuration change flows from a developer's branch
all the way through staging and into production.

```mermaid
flowchart TD
    A([👩‍💻 Developer\nEdits YAML / .tf]) --> B[git push\nfeature branch]
    B --> C[Open Pull Request]

    subgraph PR ["Pull Request (feature → main)"]
        C --> D[🔍 Plan — staging\nlitellm-control-plane-plan.yml]
        D --> E{Plan\nsuccessful?}
        E -- No --> F([❌ Fix errors\nand re-push])
        F --> D
        E -- Yes --> G[👥 Peer code review\nof YAML diff + plan output]
        G --> H{PR\napproved?}
        H -- No --> F
        H -- Yes --> I[Merge to main]
    end

    subgraph STAGING ["Staging Promotion"]
        I --> J[🔍 Plan — staging\nautomated or manual]
        J --> K[🔐 Approval gate\nstaging-approval environment]
        K --> L[⚙️ Apply — staging\ntofu apply]
        L --> M[✅ Validate — staging\nhealth + auth checks]
        M --> N{Staging\nhealthy?}
        N -- No --> O([🚨 Investigate\nrollback if needed])
        N -- Yes --> P[staging ✅ CONVERGED]
    end

    subgraph PROD ["Production Promotion (manual dispatch)"]
        P --> Q[👤 Operator dispatches\nApply workflow\nwith reviewed SHA]
        Q --> R[🔐 Approval gate\nprod-approval environment]
        R --> S[⚙️ Apply — prod\ntofu apply]
        S --> T[✅ Validate — prod\nhealth + auth checks]
        T --> U{Production\nhealthy?}
        U -- No --> V([🚨 Investigate\nrollback if needed])
        U -- Yes --> W([🎉 prod ✅ CONVERGED])
    end

    style PR fill:#e8f4f8,stroke:#2196F3
    style STAGING fill:#e8f8e8,stroke:#4CAF50
    style PROD fill:#fff3e0,stroke:#FF9800
```

> **Key principle — immutable candidates:** Every workflow that touches
> infrastructure pins to a full 40-character commit SHA. The SHA is verified
> twice — once before checkout and once after — to prevent TOCTOU substitution.

---

## 2. Pull Request Flow

When a PR is opened or updated, the plan workflow runs automatically (if wired
via a caller workflow) or can be triggered manually. Its sole purpose is to
make the diff visible and safe to review.

```mermaid
sequenceDiagram
    actor Dev as Developer
    participant GH as GitHub
    participant Runner as GitHub Runner
    participant AWS as AWS (IAM + Secrets Manager)
    participant LiteLLM as LiteLLM Gateway

    Dev->>GH: git push (feature branch)
    Dev->>GH: Open Pull Request

    GH->>Runner: Trigger plan workflow\n(environment = staging, ref = HEAD SHA)

    Runner->>Runner: ① Checkout at immutable SHA
    Runner->>Runner: ② Verify SHA matches event commit

    Runner->>Runner: ③ Validate required GitHub vars\n(AWS_ROLE_TFPLAN, LITELLM_API_BASE,\nLITELLM_API_KEY_SECRET_ID)

    Runner->>AWS: ④ AssumeRoleWithWebIdentity\n(AWS_ROLE_TFPLAN via OIDC)
    AWS-->>Runner: Temporary credentials

    Runner->>AWS: ⑤ GetSecretValue\n(LITELLM_API_KEY_SECRET_ID)
    AWS-->>Runner: LITELLM_API_KEY

    Runner->>LiteLLM: ⑥ GET /key/info\n(verify API key is valid)
    LiteLLM-->>Runner: 200 OK

    Runner->>AWS: ⑦ tofu init\n(pull remote state from S3)
    Runner->>Runner: ⑧ tofu validate

    Runner->>LiteLLM: ⑨ tofu plan -detailed-exitcode\n(read-only LiteLLM API calls)
    LiteLLM-->>Runner: Current resource state

    Runner->>Runner: ⑩ check_tofu_plan.py\n(reject replacements / mixed destroys)

    alt Plan has destructive deletes
        Runner->>GH: ⚠️ Workflow warning + step summary\n listing resources to delete
    end

    Runner->>Runner: ⑪ Remove local plan file\n(never persisted)

    Runner-->>GH: Plan summary posted to PR

    actor Rev as Reviewer
    Rev->>GH: Review YAML diff + plan output
    Rev->>GH: Approve and merge PR
```

---

## 3. Plan Workflow (`litellm-control-plane-plan.yml`)

This reusable workflow can be called by a parent workflow or dispatched
manually. It is **read-only** — it never mutates LiteLLM state.

```mermaid
sequenceDiagram
    participant Caller as Caller Workflow\nor Manual Dispatch
    participant Runner as GitHub Runner
    participant AWS as AWS
    participant SM as Secrets Manager
    participant LiteLLM as LiteLLM Gateway
    participant S3 as S3 State Bucket

    Caller->>Runner: workflow_call / workflow_dispatch\n(environment, deployment_ref)

    Note over Runner: Concurrency group per environment\nqueues concurrent runs — never cancels

    Runner->>Runner: Checkout at deployment_ref SHA

    alt SHA is not 40 hex chars
        Runner->>Caller: ❌ Error: invalid ref format
    end
    alt Checkout SHA ≠ deployment_ref
        Runner->>Caller: ❌ Error: checkout mismatch
    end

    Runner->>Runner: Validate required GitHub vars

    Runner->>AWS: OIDC → AssumeRoleWithWebIdentity\n(AWS_ROLE_TFPLAN)
    AWS-->>Runner: Read-only credentials

    Runner->>SM: GetSecretValue(LITELLM_API_KEY_SECRET_ID)
    SM-->>Runner: API key (masked in logs)

    Runner->>LiteLLM: GET /key/info (auth probe)
    LiteLLM-->>Runner: 200 OK

    Runner->>S3: tofu init (download state)
    S3-->>Runner: terraform.tfstate

    Runner->>Runner: tofu validate

    Runner->>LiteLLM: tofu plan -detailed-exitcode\n(read API, diff against state)
    LiteLLM-->>Runner: Resource states

    alt Exit code 1 (hard error)
        Runner->>Caller: ❌ Plan failed
    else Exit code 0 (no changes)
        Runner->>Runner: check_tofu_plan.py → no-op
        Runner->>Caller: ✅ Already converged
    else Exit code 2 (changes present)
        Runner->>Runner: check_tofu_plan.py
        alt Contains replacement or mixed-destroy
            Runner->>Caller: ❌ Blocked: unsafe plan
        else Contains only explicit deletes
            Runner->>Caller: ⚠️ Warning: N delete operations
        else No destructive changes
            Runner->>Caller: ✅ Safe to apply
        end
    end

    Runner->>Runner: rm plan file (cleanup, always)
```

---

## 4. Apply Workflow (`litellm-control-plane-apply.yml`)

The apply workflow is the **only path that mutates LiteLLM state**. It is
structured as three sequential jobs — verification, approval, and apply — to
ensure no code runs against the gateway without a human sign-off.

```mermaid
sequenceDiagram
    actor Operator as Operator
    participant GH as GitHub Actions
    participant Reviewer as Approver(s)
    participant Runner as GitHub Runner
    participant AWS as AWS
    participant SM as Secrets Manager
    participant S3 as S3 State Bucket
    participant LiteLLM as LiteLLM Gateway

    Operator->>GH: workflow_dispatch\n(environment, reviewed_sha)

    Note over GH: JOB 1 — verify-candidate

    GH->>Runner: Start verify-candidate job\n(ubuntu-latest, no environment gate)

    alt SHA is not 40 hex chars
        Runner->>GH: ❌ Fail: invalid ref
    end
    alt SHA ≠ GITHUB_SHA (event commit)
        Runner->>GH: ❌ Fail: SHA mismatch
    end

    Runner->>GH: ✅ Candidate verified

    Note over GH: JOB 2 — approve\n(needs: verify-candidate)
    Note over GH: Halted at GitHub Environment gate\n"<env>-approval" — up to 60 min

    GH->>Reviewer: 🔔 Review requested

    alt Reviewer rejects
        Reviewer->>GH: ❌ Reject
        GH->>Operator: Workflow cancelled
    else Reviewer approves
        Reviewer->>GH: ✅ Approve
    end

    Note over GH: JOB 3 — apply\n(needs: approve)

    GH->>Runner: Start apply job\n(environment = <env>)

    Runner->>Runner: Checkout at deployment_ref SHA
    Runner->>Runner: Verify SHA after checkout

    Runner->>Runner: Validate required vars\n(AWS_ROLE_TFAPPLY, LITELLM_API_BASE,\nLITELLM_API_KEY_SECRET_ID)

    Runner->>Runner: Ensure AWS CLI present\n(install if missing)

    Runner->>AWS: OIDC → AssumeRoleWithWebIdentity\n(AWS_ROLE_TFAPPLY — write permissions)
    AWS-->>Runner: Write credentials

    Runner->>SM: GetSecretValue(LITELLM_API_KEY_SECRET_ID)
    SM-->>Runner: API key

    Runner->>LiteLLM: GET /key/info (auth probe)
    LiteLLM-->>Runner: 200 OK

    Runner->>S3: tofu init (download state)
    Runner->>Runner: tofu validate

    Runner->>LiteLLM: tofu plan -detailed-exitcode\n(fresh plan against live state)

    alt Plan exit 1
        Runner->>GH: ❌ Plan error — apply blocked
    else Plan exit 0 (no changes)
        Runner->>GH: ✅ Already converged — no apply needed
    else Plan exit 2 (changes)
        Runner->>Runner: check_tofu_plan.py\n(reject replacements)
        Runner->>LiteLLM: tofu apply (write operations)
        LiteLLM-->>Runner: Resources created / updated / deleted
        Runner->>S3: Push updated state
        Runner->>GH: ✅ Apply complete
    end

    Runner->>Runner: rm plan file (cleanup, always)
```

---

## 5. Validation Workflow (`litellm-control-plane-validate.yml`)

Run after every apply to confirm the live gateway is healthy. Uses
`validate_litellm.py` which issues **read-only GET requests only** — it never
mutates state.

```mermaid
sequenceDiagram
    participant Caller as Caller Workflow\nor Manual Dispatch
    participant Runner as GitHub Runner
    participant AWS as AWS
    participant SM as Secrets Manager
    participant LiteLLM as LiteLLM Gateway

    Caller->>Runner: workflow_call / workflow_dispatch\n(environment, deployment_ref)

    Runner->>Runner: Checkout at deployment_ref SHA
    Runner->>Runner: Verify SHA after checkout

    Runner->>Runner: Validate required vars\n(AWS_ROLE_TFPLAN, LITELLM_API_BASE,\nLITELLM_API_KEY_SECRET_ID)

    Runner->>AWS: OIDC → AssumeRoleWithWebIdentity\n(AWS_ROLE_TFPLAN — read-only)
    AWS-->>Runner: Read-only credentials

    Runner->>SM: GetSecretValue(LITELLM_API_KEY_SECRET_ID)
    SM-->>Runner: API key

    Runner->>Runner: python3 scripts/validate_litellm.py

    Runner->>LiteLLM: GET /health/liveliness
    LiteLLM-->>Runner: PASS: reachable

    Runner->>LiteLLM: GET /health/readiness
    LiteLLM-->>Runner: PASS: healthy + db=connected

    Runner->>LiteLLM: GET /health/readiness/details
    LiteLLM-->>Runner: PASS: healthy + cache=redis

    Runner->>LiteLLM: GET /cache/redis/info
    LiteLLM-->>Runner: PASS: Redis server info returned

    Runner->>LiteLLM: GET /key/info
    LiteLLM-->>Runner: PASS: authentication confirmed

    Runner->>LiteLLM: GET /v1/models
    LiteLLM-->>Runner: PASS: N models available

    alt Any check fails
        Runner->>Caller: ❌ Validation failed\n(error written to stderr)
    else All checks pass
        Runner->>Caller: ✅ All validation checks passed
    end

    Runner->>Runner: Write validated SHA to step summary
```

---

## 6. Security Controls Summary

| Control | Where enforced |
|---|---|
| **Immutable SHA pinning** | All three workflows verify the checkout SHA before and after `actions/checkout` |
| **Replacement blocked** | `check_tofu_plan.py` rejects any plan containing `create_before_destroy` or mixed destroy+create actions |
| **Destructive change visibility** | Explicit deletes emit a named GitHub workflow warning and are listed in the step summary |
| **Human approval gate** | Apply workflow pauses on the `<env>-approval` GitHub Environment; requires configured reviewers |
| **Least-privilege IAM** | Plan uses `AWS_ROLE_TFPLAN` (read-only); Apply uses `AWS_ROLE_TFAPPLY` (write) |
| **Credentials never in code** | API key fetched from Secrets Manager at runtime; never stored in YAML, HCL, or workflow files |
| **Concurrency lock** | All three workflows share a concurrency group per environment; concurrent runs queue, never cancel |
| **Production viewer-only users** | `users.tf` lifecycle precondition rejects any human user with a non-viewer role in the production environment |
