locals {
  models = try(local.yaml_config.models.models, {})

  managed_models = {
    for model_key, model in local.models : model_key => model
    if !try(model.preserve_api_configuration, false)
  }

  preserved_models = {
    for model_key, model in local.models : model_key => model
    if try(model.preserve_api_configuration, false)
  }
}

resource "litellm_model" "this" {
  for_each = local.managed_models

  model_name                = each.value.model_name
  custom_llm_provider       = each.value.custom_llm_provider
  base_model                = each.value.base_model
  litellm_credential_name   = try(each.value.credential_name, null)
  model_api_base            = try(each.value.api_base, null)
  additional_litellm_params = try(each.value.additional_litellm_params, null)
  mode                      = try(each.value.mode, null)
  tier                      = try(each.value.tier, "paid")
  rpm                       = try(each.value.rpm, null)

  input_cost_per_million_tokens  = try(each.value.input_cost_per_million_tokens, null)
  output_cost_per_million_tokens = try(each.value.output_cost_per_million_tokens, null)

}

# Preserve imported API configuration that the pinned provider cannot fully
# represent. OpenTofu owns lifecycle while LiteLLM UI/API owns attributes.
resource "litellm_model" "preserved" {
  for_each = local.preserved_models

  model_name              = each.value.model_name
  custom_llm_provider     = each.value.custom_llm_provider
  base_model              = each.value.base_model
  litellm_credential_name = try(each.value.credential_name, null)
  model_api_base          = try(each.value.api_base, null)
  tier                    = try(each.value.tier, "paid")
  rpm                     = try(each.value.rpm, null)

  input_cost_per_million_tokens  = try(each.value.input_cost_per_million_tokens, null)
  output_cost_per_million_tokens = try(each.value.output_cost_per_million_tokens, null)

  lifecycle {
    ignore_changes = all
  }
}

# Reusable import scaffold for adopting models that already exist in LiteLLM
# (brownfield rollouts, or models created through the UI/API). Add the stable
# model key (the same key used in models.yaml) and the LiteLLM model ID to
# model_imports under only the target environment's global.yaml, run an
# import-only plan, apply it, then remove the entry after state adoption.
# Models flagged preserve_api_configuration are imported into
# litellm_model.preserved; all others into litellm_model.this. Keep the empty
# map and import blocks in place for future imports.
# preserved_model_imports is retained as a backward-compatible alias.
locals {
  model_imports = merge(
    try(local.yaml_config.global.preserved_model_imports, {}),
    try(local.yaml_config.global.model_imports, {})
  )

  managed_model_imports = {
    for model_key, model_id in local.model_imports : model_key => model_id
    if contains(keys(local.managed_models), model_key)
  }

  preserved_model_imports = {
    for model_key, model_id in local.model_imports : model_key => model_id
    if contains(keys(local.preserved_models), model_key)
  }

  unknown_model_imports = [
    for model_key in keys(local.model_imports) : model_key
    if !contains(keys(local.models), model_key)
  ]
}

import {
  for_each = local.managed_model_imports

  to = litellm_model.this[each.key]
  id = each.value
}

import {
  for_each = local.preserved_model_imports

  to = litellm_model.preserved[each.key]
  id = each.value
}

# Guard against an import entry whose key is missing from models.yaml. Without
# this, the entry would be silently ignored and the next apply would create a
# duplicate model in LiteLLM instead of adopting the existing one.
output "model_imports_pending" {
  description = "Model keys currently queued for import. Should be empty outside an import run."
  value       = sort(keys(local.model_imports))

  precondition {
    condition     = length(local.unknown_model_imports) == 0
    error_message = "model_imports contains keys that are not defined in models.yaml: ${join(", ", local.unknown_model_imports)}. Add the model to models.yaml first."
  }
}
