#!/usr/bin/env python3
"""Manage policy-constrained human users through the LiteLLM API.

The command accepts either a versioned JSON request or the flat arguments used
by the GitHub workflow. It emits a sanitized JSON audit record and never logs
the LiteLLM API key or raw API response bodies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

DEFAULT_TIMEOUT_SECONDS = 30
SCHEMA_VERSION = "1.0"
SUPPORTED_ENVIRONMENTS = {"staging", "prod"}
SUPPORTED_OPERATIONS = {"create", "update", "delete"}
NETID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STAGING_ROLE = "internal_user"
PROD_ROLE = "internal_user_viewer"
PROD_PLATFORM_ADMIN_ROLE = "proxy_admin_viewer"
PROD_PLATFORM_ADMIN_USERNAMES = frozenset(set())
ALL_PROXY_MODELS = "all-proxy-models"
SPECIAL_MODEL_GROUPS = frozenset(
    {ALL_PROXY_MODELS, "all-team-models", "no-default-models"}
)
MODEL_CREATOR_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MODEL_NAME_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9_-]*/[^/@\s]+(?:@[a-z0-9][a-z0-9_-]*)?$"
)
MODEL_NAME_DECLARATION_PATTERN = re.compile(
    r'''^\s+model_name:\s*(?:"([^"]+)"|'([^']+)'|([^#\s]+))\s*(?:#.*)?$'''
)
STAGING_MODELS_PATH = (
    Path(__file__).resolve().parents[1]
    / "litellm-control-plane/environments/staging/models.yaml"
)


def role_for_user(environment: str, username: str) -> str:
    """Return the repository-owned role for a human identity."""

    if environment == "staging":
        return STAGING_ROLE
    if username in PROD_PLATFORM_ADMIN_USERNAMES:
        return PROD_PLATFORM_ADMIN_ROLE
    return PROD_ROLE


class UserManagementError(RuntimeError):
    """A safe-to-log user-management failure."""


class ApiClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class ManagementRequest:
    request_id: str
    operation: str
    environment: str
    change_reference: str
    actor: str
    username: str
    email: str | None
    current_email: str | None
    display_alias: str
    models: tuple[str, ...] | None
    team_aliases: tuple[str, ...] | None
    confirm_delete: bool

    @property
    def user_id(self) -> str:
        """Stable LiteLLM identity; email is a separately mutable attribute."""

        return f"{self.username}@example.com"


@dataclass(frozen=True)
class UserSnapshot:
    user_id: str
    email: str
    display_alias: str | None
    role: str | None
    models: tuple[str, ...]
    teams: tuple[tuple[str, str], ...]
    key_count: int

    @property
    def team_ids(self) -> set[str]:
        return {team_id for team_id, _alias in self.teams}

    @property
    def team_aliases(self) -> tuple[str, ...]:
        return tuple(sorted(alias for _team_id, alias in self.teams))


class LiteLLMClient:
    """Small stdlib-only client that keeps failures safe for CI logs."""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        actor: str,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        opener: Any = urllib.request.urlopen,
    ) -> None:
        if not api_base.strip():
            raise UserManagementError("LITELLM_API_BASE is required")
        if not api_key.strip():
            raise UserManagementError("LITELLM_API_KEY is required")
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.actor = actor
        self.timeout = timeout
        self.opener = opener

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.api_base}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "litellm-changed-by": self.actor,
            },
            method=method,
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                response_payload = response.read()
        except urllib.error.HTTPError as error:
            raise UserManagementError(
                f"{method} {path} returned HTTP {error.code}"
            ) from None
        except urllib.error.URLError:
            raise UserManagementError(f"{method} {path} could not be reached") from None
        except TimeoutError:
            raise UserManagementError(f"{method} {path} timed out") from None

        if not response_payload:
            return None
        try:
            return json.loads(response_payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise UserManagementError(
                f"{method} {path} returned invalid JSON"
            ) from None


def _required_string(value: Any, field: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UserManagementError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise UserManagementError(f"{field} exceeds {max_length} characters")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized):
        raise UserManagementError(f"{field} must not contain control characters")
    return normalized


def _optional_string(value: Any, field: str, *, max_length: int = 256) -> str | None:
    if value is None or value == "":
        return None
    return _required_string(value, field, max_length=max_length)


def _display_name(value: Any) -> str:
    normalized = _required_string(value, "user.display_alias", max_length=128)
    components = normalized.split()
    if len(components) < 2:
        raise UserManagementError(
            "user.display_alias must contain at least a first and last name"
        )
    return " ".join(components)


def _string_list(value: Any, field: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise UserManagementError(f"{field} must be a non-empty array when provided")
    normalized = tuple(_required_string(item, field) for item in value)
    if len({item.casefold() for item in normalized}) != len(normalized):
        raise UserManagementError(f"{field} must not contain duplicates")
    return normalized


def parse_request(payload: Any) -> ManagementRequest:
    """Validate and normalize the canonical versioned request contract."""

    if not isinstance(payload, dict):
        raise UserManagementError("request must be a JSON object")
    allowed = {
        "schema_version",
        "request_id",
        "operation",
        "environment",
        "change_reference",
        "actor",
        "user",
        "confirm_delete",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise UserManagementError(
            "request contains unsupported field(s): " + ", ".join(unexpected)
        )
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise UserManagementError(f"schema_version must be {SCHEMA_VERSION}")

    operation = _required_string(payload.get("operation"), "operation").lower()
    if operation not in SUPPORTED_OPERATIONS:
        raise UserManagementError(
            "operation must be one of: " + ", ".join(sorted(SUPPORTED_OPERATIONS))
        )
    environment = _required_string(payload.get("environment"), "environment").lower()
    if environment not in SUPPORTED_ENVIRONMENTS:
        raise UserManagementError("environment must be staging or prod")

    user = payload.get("user")
    if not isinstance(user, dict):
        raise UserManagementError("user must be a JSON object")
    allowed_user = {
        "username",
        "email",
        "current_email",
        "display_alias",
        "models",
        "team_aliases",
    }
    unexpected_user = sorted(set(user) - allowed_user)
    if unexpected_user:
        raise UserManagementError(
            "user contains unsupported field(s): " + ", ".join(unexpected_user)
        )

    username = _required_string(user.get("username"), "user.username", max_length=64).lower()
    if NETID_PATTERN.fullmatch(username) is None:
        raise UserManagementError(
            "user.username must start with a lowercase letter or digit and contain only lowercase letters, digits, underscores, or hyphens"
        )
    email = _optional_string(user.get("email"), "user.email", max_length=254)
    if email is not None:
        email = email.lower()
        if EMAIL_PATTERN.fullmatch(email) is None:
            raise UserManagementError("user.email must be a valid email address")
    if operation == "create" and email is None:
        email = f"{username}@example.com"

    current_email = _optional_string(
        user.get("current_email"), "user.current_email", max_length=254
    )
    if current_email is not None:
        current_email = current_email.lower()
        if EMAIL_PATTERN.fullmatch(current_email) is None:
            raise UserManagementError(
                "user.current_email must be a valid email address"
            )
        if operation == "create":
            raise UserManagementError(
                "user.current_email is valid only for update or delete"
            )

    models = _string_list(user.get("models"), "user.models")
    team_aliases = _string_list(user.get("team_aliases"), "user.team_aliases")
    display_alias = _display_name(user.get("display_alias"))
    change_reference = _optional_string(
        payload.get("change_reference"), "change_reference", max_length=256
    )
    if change_reference is None:
        raise UserManagementError("change_reference is required")

    confirm_delete = payload.get("confirm_delete", False)
    if not isinstance(confirm_delete, bool):
        raise UserManagementError("confirm_delete must be a boolean")
    if operation == "delete" and not confirm_delete:
        raise UserManagementError("delete requires confirm_delete=true")
    if operation != "delete" and confirm_delete:
        raise UserManagementError("confirm_delete is valid only for delete")

    if environment == "staging" and team_aliases is not None:
        raise UserManagementError("TST human users must remain teamless")
    if environment == "prod" and models is not None:
        raise UserManagementError("PRD human users must not receive direct models")
    if environment == "prod" and operation == "create" and team_aliases is None:
        raise UserManagementError(
            "PRD user creation requires at least one team_aliases entry"
        )

    return ManagementRequest(
        request_id=_required_string(payload.get("request_id"), "request_id"),
        operation=operation,
        environment=environment,
        change_reference=change_reference,
        actor=_required_string(payload.get("actor"), "actor"),
        username=username,
        email=email,
        current_email=current_email,
        display_alias=display_alias,
        models=models,
        team_aliases=team_aliases,
        confirm_delete=confirm_delete,
    )


def _as_object(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UserManagementError(f"{description} must return a JSON object")
    return value


def _as_object_list(value: Any, description: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise UserManagementError(f"{description} must return a JSON array of objects")
    return value


def load_staging_model_names(path: Path = STAGING_MODELS_PATH) -> tuple[str, ...]:
    """Read the reviewed model names without adding a runtime YAML dependency."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise UserManagementError("TST model inventory could not be read") from None

    names: list[str] = []
    for line in lines:
        if "model_name:" not in line:
            continue
        match = MODEL_NAME_DECLARATION_PATTERN.fullmatch(line)
        if match is None:
            raise UserManagementError("TST model inventory has an invalid model_name")
        name = next(value for value in match.groups() if value is not None)
        if MODEL_NAME_PATTERN.fullmatch(name) is None:
            raise UserManagementError(
                f"TST model inventory has an invalid model name: {name!r}"
            )
        names.append(name)

    if not names:
        raise UserManagementError("TST model inventory contains no models")
    if len(set(names)) != len(names):
        raise UserManagementError("TST model inventory contains duplicate model names")
    return tuple(sorted(names))


class HumanUserManager:
    def __init__(
        self,
        api: ApiClient,
        *,
        configured_models: tuple[str, ...] = (),
    ) -> None:
        self.api = api
        self.configured_models = configured_models
        self.completed_steps: list[str] = []

    def find_user(
        self,
        user_id: str,
        current_email: str | None = None,
        *,
        allow_legacy_email_match: bool = False,
    ) -> dict[str, Any] | None:
        response = _as_object(
            self.api.request(
                "GET",
                "/user/list",
                query={"user_ids": user_id, "page_size": "100"},
            ),
            "GET /user/list",
        )
        users = _as_object_list(response.get("users"), "GET /user/list users")
        exact = [
            user
            for user in users
            if str(user.get("user_id", "")).casefold() == user_id.casefold()
        ]
        if len(exact) > 1:
            raise UserManagementError(
                "multiple LiteLLM users have the requested user_id; manual remediation is required"
            )
        if exact:
            return exact[0]

        expected_email = current_email or user_id
        response = _as_object(
            self.api.request(
                "GET",
                "/user/list",
                query={"user_email": expected_email, "page_size": "100"},
            ),
            "GET /user/list",
        )
        users = _as_object_list(response.get("users"), "GET /user/list users")
        email_matches = [
            user
            for user in users
            if str(user.get("user_email", "")).casefold() == expected_email.casefold()
        ]
        if len(email_matches) > 1:
            raise UserManagementError(
                "multiple LiteLLM users have the requested email; manual remediation is required"
            )
        if email_matches:
            if allow_legacy_email_match:
                return email_matches[0]
            legacy_id = str(email_matches[0].get("user_id", "unknown"))
            raise UserManagementError(
                f"email belongs to noncanonical LiteLLM user_id {legacy_id!r}; "
                "use update with current_email to manage that legacy user"
            )
        return None

    def mutate(self, path: str, body: dict[str, Any], step: str) -> Any:
        result = self.api.request("POST", path, body=body)
        self.completed_steps.append(step)
        return result

    def snapshot(self, user: dict[str, Any]) -> UserSnapshot:
        user_id = _required_string(user.get("user_id"), "LiteLLM user_id")
        response = _as_object(
            self.api.request("GET", "/user/info", query={"user_id": user_id}),
            "GET /user/info",
        )
        info = _as_object(response.get("user_info"), "GET /user/info user_info")
        teams = _as_object_list(response.get("teams", []), "GET /user/info teams")
        keys = _as_object_list(response.get("keys", []), "GET /user/info keys")

        team_pairs: list[tuple[str, str]] = []
        for team in teams:
            team_id = _required_string(team.get("team_id"), "LiteLLM team_id")
            team_alias = _required_string(
                team.get("team_alias") or team_id, "LiteLLM team_alias"
            )
            team_pairs.append((team_id, team_alias))
        raw_models = info.get("models") or []
        if not isinstance(raw_models, list) or not all(
            isinstance(model, str) for model in raw_models
        ):
            raise UserManagementError("GET /user/info returned invalid models")

        return UserSnapshot(
            user_id=user_id,
            email=_required_string(
                info.get("user_email") or user.get("user_email"),
                "LiteLLM user_email",
                max_length=254,
            ),
            display_alias=(
                str(info["user_alias"]) if info.get("user_alias") is not None else None
            ),
            role=str(info["user_role"]) if info.get("user_role") is not None else None,
            models=tuple(sorted(raw_models)),
            teams=tuple(sorted(team_pairs, key=lambda item: item[1].casefold())),
            key_count=len(keys),
        )

    def list_teams(self) -> list[dict[str, Any]]:
        return _as_object_list(self.api.request("GET", "/team/list"), "GET /team/list")

    def resolve_teams(self, aliases: tuple[str, ...]) -> dict[str, str]:
        teams = self.list_teams()
        by_alias: dict[str, list[tuple[str, str]]] = {}
        for team in teams:
            team_id = _required_string(team.get("team_id"), "LiteLLM team_id")
            team_alias = _required_string(
                team.get("team_alias") or team_id, "LiteLLM team_alias"
            )
            by_alias.setdefault(team_alias.casefold(), []).append((team_id, team_alias))

        resolved: dict[str, str] = {}
        for requested_alias in aliases:
            matches = by_alias.get(requested_alias.casefold(), [])
            if not matches:
                raise UserManagementError(
                    f"team alias {requested_alias!r} does not exist in LiteLLM"
                )
            if len(matches) > 1:
                raise UserManagementError(
                    f"team alias {requested_alias!r} is ambiguous in LiteLLM"
                )
            team_id, canonical_alias = matches[0]
            resolved[team_id] = canonical_alias
        return resolved

    def resolve_models(self, selectors: tuple[str, ...]) -> tuple[str, ...]:
        if selectors == (ALL_PROXY_MODELS,):
            return selectors
        if SPECIAL_MODEL_GROUPS.intersection(selectors):
            raise UserManagementError(
                "special model access groups cannot be combined with explicit models"
            )

        resolved: set[str] = set()
        for selector in selectors:
            if "/" in selector:
                resolved.add(selector)
                continue
            if MODEL_CREATOR_PATTERN.fullmatch(selector) is None:
                raise UserManagementError(
                    f"invalid model creator selector: {selector!r}"
                )
            matches = {
                model
                for model in self.configured_models
                if model.startswith(f"{selector}/")
            }
            if not matches:
                raise UserManagementError(
                    f"model creator {selector!r} has no configured TST models"
                )
            resolved.update(matches)

        response = _as_object(self.api.request("GET", "/v1/models"), "GET /v1/models")
        entries = _as_object_list(response.get("data"), "GET /v1/models data")
        available = {
            str(entry.get("id")) for entry in entries if str(entry.get("id", ""))
        }
        models = tuple(sorted(resolved))
        missing = sorted(resolved - available)
        if missing:
            raise UserManagementError(
                "requested model(s) are not available: " + ", ".join(missing)
            )
        return models

    def reconcile_teams(
        self,
        snapshot: UserSnapshot,
        desired: dict[str, str],
    ) -> None:
        current_ids = snapshot.team_ids
        desired_ids = set(desired)
        current = dict(snapshot.teams)
        # Add first so a PRD user is not left teamless if a later removal fails.
        for team_id in sorted(desired_ids - current_ids):
            self.mutate(
                "/team/member_add",
                {
                    "team_id": team_id,
                    "member": {"role": "user", "user_id": snapshot.user_id},
                },
                f"team_added:{desired[team_id]}",
            )
        for team_id in sorted(current_ids - desired_ids):
            self.mutate(
                "/team/member_delete",
                {"team_id": team_id, "user_id": snapshot.user_id},
                f"team_removed:{current[team_id]}",
            )

    @staticmethod
    def assert_policy(request: ManagementRequest, snapshot: UserSnapshot) -> None:
        if request.environment == "staging":
            if snapshot.role != STAGING_ROLE:
                raise UserManagementError(
                    f"TST verification expected role {STAGING_ROLE!r}, found {snapshot.role!r}"
                )
            if snapshot.teams:
                raise UserManagementError("TST verification found team membership")
            return
        expected_role = role_for_user(request.environment, request.username)
        if snapshot.role != expected_role:
            raise UserManagementError(
                f"PRD verification expected role {expected_role!r}, found {snapshot.role!r}"
            )
        if snapshot.models:
            raise UserManagementError("PRD verification found direct model access")
        if snapshot.key_count:
            raise UserManagementError("PRD verification found one or more user keys")
        if not snapshot.teams:
            raise UserManagementError("PRD verification found no team membership")

    def execute(self, request: ManagementRequest) -> dict[str, Any]:
        self.completed_steps = []
        lookup_email = (
            request.email if request.operation == "create" else request.current_email
        )
        existing = self.find_user(
            request.user_id,
            lookup_email,
            allow_legacy_email_match=request.operation in {"update", "delete"},
        )
        before = self.snapshot(existing) if existing is not None else None

        if request.operation == "create":
            if before is not None:
                raise UserManagementError("user already exists")
            desired_teams: dict[str, str] = {}
            if request.environment == "prod":
                desired_teams = self.resolve_teams(request.team_aliases or ())
            models = request.models or (ALL_PROXY_MODELS,)
            if request.environment == "staging":
                models = self.resolve_models(models)
            payload: dict[str, Any] = {
                "user_id": request.user_id,
                "user_email": request.email or request.user_id,
                "user_role": role_for_user(request.environment, request.username),
                "auto_create_key": False,
                "send_invite_email": False,
            }
            if request.display_alias is not None:
                payload["user_alias"] = request.display_alias
            if request.environment == "staging":
                payload["models"] = list(models)
                payload["teams"] = []
            self.mutate("/user/new", payload, "user_created")
            created = self.find_user(request.user_id, request.email)
            if created is None:
                raise UserManagementError("created user could not be verified")
            after = self.snapshot(created)
            if request.environment == "prod":
                self.reconcile_teams(after, desired_teams)
                after = self.snapshot(created)
            self.assert_policy(request, after)
            changes = ["user"] + (["teams"] if desired_teams else [])
            return self._audit(request, "created", after, changes, True)

        if before is None:
            raise UserManagementError("user does not exist")

        if request.operation == "delete":
            self.mutate("/user/delete", {"user_ids": [before.user_id]}, "user_deleted")
            if self.find_user(before.user_id) is not None:
                raise UserManagementError(
                    "deleted user still exists during verification"
                )
            return self._audit(request, "deleted", before, ["user"], True, exists=False)
        if request.environment == "prod":
            if before.models:
                raise UserManagementError(
                    "PRD user has direct models; remove them before API-managed updates"
                )
            if before.key_count:
                raise UserManagementError(
                    "PRD user has one or more keys; remove them before API-managed updates"
                )

        desired_role = role_for_user(request.environment, request.username)
        patch: dict[str, Any] = {"user_id": before.user_id}
        if (
            request.email is not None
            and before.email.casefold() != request.email.casefold()
        ):
            patch["user_email"] = request.email
        if before.role != desired_role:
            patch["user_role"] = desired_role
        if (
            request.display_alias is not None
            and request.display_alias != before.display_alias
        ):
            patch["user_alias"] = request.display_alias
        if request.environment == "staging" and request.models is not None:
            models = self.resolve_models(request.models)
            if models != before.models:
                patch["models"] = list(models)

        desired_teams: dict[str, str]
        if request.environment == "staging":
            desired_teams = {}
        elif request.team_aliases is None:
            desired_teams = dict(before.teams)
            if not desired_teams:
                raise UserManagementError(
                    "PRD update requires team_aliases because the user has no current teams"
                )
        else:
            desired_teams = self.resolve_teams(request.team_aliases)

        if len(patch) > 1:
            self.mutate("/user/update", patch, "user_updated")
        self.reconcile_teams(before, desired_teams)
        current = self.find_user(before.user_id)
        if current is None:
            raise UserManagementError("updated user could not be verified")
        after = self.snapshot(current)
        self.assert_policy(request, after)
        changed_fields = self._changed_fields(before, after)
        outcome = "updated" if changed_fields else "no_change"
        return self._audit(request, outcome, after, changed_fields, True)

    @staticmethod
    def _changed_fields(before: UserSnapshot, after: UserSnapshot) -> list[str]:
        changed: list[str] = []
        for name in ("email", "display_alias", "role", "models", "team_aliases"):
            if getattr(before, name) != getattr(after, name):
                changed.append(name)
        return changed

    def _audit(
        self,
        request: ManagementRequest,
        outcome: str,
        snapshot: UserSnapshot,
        changed_fields: list[str],
        compliant: bool,
        *,
        exists: bool = True,
    ) -> dict[str, Any]:
        target = {
            "username": request.username,
            "email": snapshot.email,
            "user_id": snapshot.user_id,
        }
        if snapshot.user_id.casefold() != request.user_id.casefold():
            target["requested_user_id"] = request.user_id
            if request.current_email is not None:
                target["lookup_email"] = request.current_email

        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request.request_id,
            "operation": request.operation,
            "environment": request.environment,
            "change_reference": request.change_reference,
            "actor": request.actor,
            "outcome": outcome,
            "changed_fields": changed_fields,
            "completed_steps": list(self.completed_steps),
            "target": target,
            "verification": {
                "exists": exists,
                "policy_compliant": compliant,
                "role": snapshot.role if exists else None,
                "models": list(snapshot.models) if exists else [],
                "team_aliases": list(snapshot.team_aliases) if exists else [],
                "key_count": snapshot.key_count if exists else 0,
            },
        }


def _split_csv(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def request_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": args.request_id,
        "operation": args.operation,
        "environment": args.environment,
        "change_reference": args.change_reference,
        "actor": args.actor,
        "confirm_delete": args.confirm_delete,
        "user": {
            "username": args.username,
            "email": args.email or None,
            "current_email": args.current_email or None,
            "display_alias": args.display_alias or None,
            "models": _split_csv(args.models),
            "team_aliases": _split_csv(args.teams),
        },
    }


def _load_request(args: argparse.Namespace) -> Any:
    if args.request_file is None:
        return request_from_args(args)
    try:
        if args.request_file == "-":
            return json.load(sys.stdin)
        return json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise UserManagementError("request file could not be read as JSON") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request-file", help="Canonical JSON request path, or - for stdin"
    )
    parser.add_argument("--operation", choices=sorted(SUPPORTED_OPERATIONS))
    parser.add_argument("--environment", choices=sorted(SUPPORTED_ENVIRONMENTS))
    parser.add_argument("--username")
    parser.add_argument("--email", default="")
    parser.add_argument("--current-email", default="")
    parser.add_argument("--display-alias", default="")
    parser.add_argument("--models", default="")
    parser.add_argument("--teams", default="")
    parser.add_argument("--change-reference", default="")
    parser.add_argument("--actor")
    parser.add_argument("--request-id")
    parser.add_argument("--confirm-delete", action="store_true")
    return parser


def failure_audit(
    request: ManagementRequest | None,
    error: UserManagementError,
    completed_steps: list[str] | None = None,
) -> dict[str, Any]:
    """Build a safe structured record for rejected or failed operations."""

    completed_steps = completed_steps or []
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request.request_id if request else "unavailable",
        "operation": request.operation if request else "unavailable",
        "environment": request.environment if request else "unavailable",
        "change_reference": request.change_reference if request else "unavailable",
        "actor": request.actor if request else "unavailable",
        "outcome": "partially_applied" if completed_steps else "failed",
        "completed_steps": completed_steps,
        "error": str(error),
        "target": (
            {
                "username": request.username,
                "email": request.email,
                "lookup_email": request.current_email,
                "user_id": request.user_id,
            }
            if request
            else {
                "username": "unavailable",
                "email": "unavailable",
                "user_id": "unavailable",
            }
        ),
    }


def main() -> int:
    args = build_parser().parse_args()
    request: ManagementRequest | None = None
    manager: HumanUserManager | None = None
    if args.request_file is None:
        missing = [
            name
            for name in ("operation", "environment", "username", "actor", "request_id")
            if getattr(args, name) is None
        ]
        if missing:
            print(
                "ERROR: missing required argument(s): " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
    try:
        request = parse_request(_load_request(args))
        client = LiteLLMClient(
            os.environ.get("LITELLM_API_BASE", ""),
            os.environ.get("LITELLM_API_KEY", ""),
            request.actor,
        )
        configured_models: tuple[str, ...] = ()
        if (
            request.environment == "staging"
            and request.models is not None
            and any(
                "/" not in selector and selector not in SPECIAL_MODEL_GROUPS
                for selector in request.models
            )
        ):
            configured_models = load_staging_model_names()
        manager = HumanUserManager(client, configured_models=configured_models)
        audit = manager.execute(request)
        serialized = json.dumps(audit, sort_keys=True)
        print(f"LITELLM_USER_AUDIT={serialized}")
        return 0
    except UserManagementError as error:
        audit = failure_audit(
            request,
            error,
            manager.completed_steps if manager is not None else None,
        )
        serialized = json.dumps(audit, sort_keys=True)
        print(f"LITELLM_USER_AUDIT={serialized}")
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
