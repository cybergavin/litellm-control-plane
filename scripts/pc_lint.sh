#!/bin/bash
##################################################################################
## Lints staged files using:
## - yamllint for YAML
##################################################################################
set -eu

if ! command -v yamllint > /dev/null 2>&1; then
  echo "FAIL: yamllint is required for YAML linting." >&2
  exit 127
fi

if [[ $# -eq 0 ]]; then
  echo "PASS: No YAML files to lint."
  exit 0
fi

yamllint "$@" && echo "PASS: No YAML linting errors." || {
  echo "FAIL: YAML linting errors."
  exit 1
}
