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

# Reusable import scaffold for UI-created models whose API configuration the
# pinned provider cannot fully represent. Add the stable model key and LiteLLM
# ID under only the target environment, run an import-only plan, apply it, then
# remove the entry after state adoption. Keep the empty map and import block in
# place for future imports.
locals {
  preserved_model_imports = try(
    local.yaml_config.global.preserved_model_imports,
    {}
  )
}

import {
  for_each = local.preserved_model_imports

  to = litellm_model.preserved[each.key]
  id = each.value
}
