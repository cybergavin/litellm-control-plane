#!/bin/bash
##################################################################################
## Scans staged OpenTofu, YAML, and JSON files with Checkov.
## Designed to be run as a pre-commit hook.
##################################################################################
set -eu

# Get the repository root directory
REPO_ROOT=$(git rev-parse --show-toplevel)

# Run checkov scan for staged files
if [[ $# -gt 0 ]]; then
  checkov --config-file "${REPO_ROOT}/.checkov.yml" $(printf -- '-f %q ' "$@")
else
  echo "PASS: No staged files to scan."
fi
