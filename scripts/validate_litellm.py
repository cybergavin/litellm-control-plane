#!/usr/bin/env python3
"""Run read-only post-promotion checks against a LiteLLM gateway."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 30


class ValidationError(RuntimeError):
    """A safe-to-log LiteLLM validation failure."""


def _request(
    api_base: str,
    path: str,
    api_key: str,
    *,
    expect_json: bool,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Any:
    url = f"{api_base.rstrip('/')}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="GET",
    )

    try:
        with opener(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        raise ValidationError(f"{path} returned HTTP {error.code}") from None
    except urllib.error.URLError:
        raise ValidationError(f"{path} could not be reached") from None
    except TimeoutError:
        raise ValidationError(f"{path} timed out") from None

    if not expect_json:
        return None

    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValidationError(f"{path} returned invalid JSON") from None


def validate_litellm(
    api_base: str,
    api_key: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[str]:
    """Validate health, dependencies, authentication, and model availability."""

    if not api_base.strip():
        raise ValidationError("LITELLM_API_BASE is required")
    if not api_key.strip():
        raise ValidationError("LITELLM_API_KEY is required")

    messages: list[str] = []

    _request(
        api_base,
        "/health/liveliness",
        api_key,
        expect_json=False,
        opener=opener,
    )
    messages.append("PASS: LiteLLM liveliness endpoint is reachable.")

    readiness = _request(
        api_base,
        "/health/readiness",
        api_key,
        expect_json=True,
        opener=opener,
    )
    if not isinstance(readiness, dict):
        raise ValidationError("health/readiness must return a JSON object")
    if str(readiness.get("status", "")).lower() != "healthy":
        raise ValidationError("health/readiness did not report healthy status")
    if str(readiness.get("db", "")).lower() != "connected":
        raise ValidationError("health/readiness did not report a connected database")
    messages.append(
        "PASS: LiteLLM reports healthy readiness and database connectivity."
    )

    readiness_details = _request(
        api_base,
        "/health/readiness/details",
        api_key,
        expect_json=True,
        opener=opener,
    )
    if not isinstance(readiness_details, dict):
        raise ValidationError("health/readiness/details must return a JSON object")
    if str(readiness_details.get("status", "")).lower() != "healthy":
        raise ValidationError("health/readiness/details did not report healthy status")
    if str(readiness_details.get("db", "")).lower() != "connected":
        raise ValidationError(
            "health/readiness/details did not report a connected database"
        )
    cache_status = str(readiness_details.get("cache", "")).lower()
    if cache_status not in {"redis", "connected"}:
        raise ValidationError(
            "health/readiness/details did not report a connected Redis cache"
        )
    messages.append("PASS: LiteLLM authenticated readiness reports a Redis cache.")

    redis_info = _request(
        api_base,
        "/cache/redis/info",
        api_key,
        expect_json=True,
        opener=opener,
    )
    if not isinstance(redis_info, dict) or not isinstance(redis_info.get("info"), dict):
        raise ValidationError("cache/redis/info must return Redis server information")
    messages.append("PASS: LiteLLM can query the Redis cache.")

    key_info = _request(
        api_base,
        "/key/info",
        api_key,
        expect_json=True,
        opener=opener,
    )
    if not isinstance(key_info, dict):
        raise ValidationError("key/info must return a JSON object")
    messages.append("PASS: LiteLLM control-plane authentication succeeded.")

    models = _request(
        api_base,
        "/v1/models",
        api_key,
        expect_json=True,
        opener=opener,
    )
    model_entries = models.get("data") if isinstance(models, dict) else None
    if not isinstance(model_entries, list) or not model_entries:
        raise ValidationError("v1/models did not return any available models")
    if any(
        not isinstance(model, dict) or not str(model.get("id", "")).strip()
        for model in model_entries
    ):
        raise ValidationError("v1/models returned an entry without a model id")
    messages.append(f"PASS: LiteLLM exposes {len(model_entries)} model(s).")

    return messages


def main() -> int:
    try:
        messages = validate_litellm(
            os.environ.get("LITELLM_API_BASE", ""),
            os.environ.get("LITELLM_API_KEY", ""),
        )
    except ValidationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
