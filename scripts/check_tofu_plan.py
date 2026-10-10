#!/usr/bin/env python3
"""Validate destructive OpenTofu actions and make them prominent for review."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO


class PlanValidationError(ValueError):
    """Raised when an OpenTofu plan cannot be safely applied."""


def resource_changes(plan: Any) -> list[dict[str, Any]]:
    """Return validated resource-change objects from a plan."""

    if not isinstance(plan, dict):
        raise PlanValidationError("plan must be a JSON object")

    changes = plan.get("resource_changes", [])
    if not isinstance(changes, list):
        raise PlanValidationError("resource_changes must be a list")

    for resource_change in changes:
        if not isinstance(resource_change, dict):
            raise PlanValidationError("resource change must be a JSON object")

        change = resource_change.get("change")
        if not isinstance(change, dict):
            raise PlanValidationError("resource change is missing change metadata")

        actions = change.get("actions")
        if not isinstance(actions, list) or not all(
            isinstance(action, str) for action in actions
        ):
            raise PlanValidationError("resource change actions must be a string list")

    return changes


def count_destroy_actions(plan: Any) -> int:
    """Count resource changes whose action list includes a delete."""

    return sum(
        "delete" in resource_change["change"]["actions"]
        for resource_change in resource_changes(plan)
    )


def validate_plan(stream: TextIO) -> list[str]:
    """Allow explicit deletes, reject replacements, and return delete addresses."""

    try:
        plan = json.load(stream)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PlanValidationError("plan is not valid JSON") from exc

    addresses: list[str] = []
    for resource_change in resource_changes(plan):
        actions = resource_change["change"]["actions"]
        if "delete" not in actions:
            continue
        address = resource_change.get("address")
        if actions != ["delete"]:
            raise PlanValidationError(
                f"replacement or mixed destroy action is not allowed: {address!r}"
            )
        if (
            not isinstance(address, str)
            or not address
            or "\n" in address
            or "\r" in address
        ):
            raise PlanValidationError("destroying resource has an invalid address")
        addresses.append(address)

    return addresses


def report_destroy_actions(addresses: list[str]) -> None:
    """Emit a workflow warning and step summary for reviewed deletions."""

    if not addresses:
        return

    print(
        f"::warning title=Destructive OpenTofu plan::"
        f"Review {len(addresses)} delete operation(s) before approval."
    )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("## Destructive changes require review\n\n")
            summary.write(
                f"The OpenTofu plan contains {len(addresses)} delete operation(s):\n\n"
            )
            for address in addresses:
                summary.write(f"- `{address.replace('`', '')}`\n")


def main() -> int:
    try:
        addresses = validate_plan(sys.stdin)
    except PlanValidationError as exc:
        print(f"::error::{exc} Refusing to apply.", file=sys.stderr)
        return 1

    report_destroy_actions(addresses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
