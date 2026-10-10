#!/bin/bash
##################################################################################
## Validate input YAML files against their corresponding JSON schemas.
## This script assumes that the schemas are named according to the pattern
## <base_name>_schema.yaml, where <base_name> is derived from the input YAML
## file name. Schema files live in the schemas/ directory at the repo root.
##################################################################################
set -eu

if ! command -v check-jsonschema > /dev/null 2>&1; then
    echo "Error: check-jsonschema is required for schema validation." >&2
    exit 127
fi

if [[ $# -eq 0 ]]; then
    echo "No environment YAML files supplied for validation." >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"

for yaml_file in "$@"
do
    base_name="$(basename "$yaml_file")"
    base_name="${base_name%.*}"
    schema_path="${REPO_ROOT}/schemas/${base_name}_schema.yaml"

    if [[ -f "$schema_path" ]]; then
        echo "Validating $yaml_file against $schema_path"
        check-jsonschema "$yaml_file" --schemafile "$schema_path" || {
            echo "Validation failed for $yaml_file" >&2
            exit 1
        }
    else
        echo "Schema file not found for $yaml_file: $schema_path" >&2
        exit 1
    fi
done
