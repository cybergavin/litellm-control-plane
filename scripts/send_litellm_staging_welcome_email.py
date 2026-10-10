#!/usr/bin/env python3
"""Send the post-provisioning welcome email for a STAGING LiteLLM human user."""

from __future__ import annotations

import argparse
import html
import json
import re
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Callable

SMTP_HOST = "smtp.example.com"
SMTP_PORT = 25
SMTP_TIMEOUT_SECONDS = 15
SENDER_NAME = "Example Cloud Platform Services"
SENDER_EMAIL = "donotreply@example.com"
SUBJECT = "AI Gateway: Your STAGING Access Is Ready"
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "templates/litellm_staging_welcome.html"
)


class NotificationError(RuntimeError):
    """A safe-to-log welcome-email failure."""


def _clean(value: str, field: str, *, max_length: int = 256) -> str:
    normalized = value.strip()
    if not normalized:
        raise NotificationError(f"{field} must not be empty")
    if len(normalized) > max_length:
        raise NotificationError(f"{field} exceeds {max_length} characters")
    if any(
        ord(character) < 0x20 or ord(character) == 0x7F
        for character in normalized
    ):
        raise NotificationError(f"{field} must not contain control characters")
    return normalized


def resolve_recipient(username: str, email: str) -> tuple[str, str]:
    normalized_username = _clean(username, "username", max_length=64).lower()
    if USERNAME_PATTERN.fullmatch(normalized_username) is None:
        raise NotificationError("username contains unsupported characters")
    recipient = email.strip().lower() or f"{normalized_username}@example.com"
    if EMAIL_PATTERN.fullmatch(recipient) is None:
        raise NotificationError("email must be a valid address")
    return normalized_username, recipient


def parse_cc_addresses(value: str) -> tuple[str, ...]:
    if not value.strip():
        raise NotificationError("CC address configuration must not be empty")

    addresses: list[str] = []
    seen: set[str] = set()
    for raw_address in value.split(","):
        address = raw_address.strip().lower()
        if not address:
            raise NotificationError(
                "CC address configuration contains an empty entry"
            )
        if EMAIL_PATTERN.fullmatch(address) is None:
            raise NotificationError(f"invalid CC email address: {address!r}")
        if address not in seen:
            seen.add(address)
            addresses.append(address)
    return tuple(addresses)


def render_html(display_name: str, template_path: Path = TEMPLATE_PATH) -> str:
    greeting_name = _clean(display_name, "display name", max_length=128)
    components = greeting_name.split()
    if len(components) < 2:
        raise NotificationError(
            "display name must contain at least a first and last name"
        )
    greeting_name = " ".join(components)
    try:
        template = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise NotificationError("welcome-email template could not be read") from None
    if template.count("[Display Name]") != 1:
        raise NotificationError(
            "welcome-email template must contain exactly one display-name placeholder"
        )
    return template.replace("[Display Name]", html.escape(greeting_name))


def build_message(
    recipient: str, body: str, cc_addresses: tuple[str, ...]
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = SUBJECT
    message["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
    message["To"] = recipient
    message["Cc"] = ", ".join(cc_addresses)
    message.set_content(body, subtype="html")
    return message


def send_message(
    message: EmailMessage,
    recipient: str,
    cc_addresses: tuple[str, ...],
    *,
    smtp_factory: Callable[..., Any] = smtplib.SMTP,
) -> None:
    try:
        with smtp_factory(
            SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS
        ) as smtp:
            refused = smtp.send_message(
                message,
                from_addr=SENDER_EMAIL,
                to_addrs=list(dict.fromkeys((recipient, *cc_addresses))),
            )
    except (OSError, smtplib.SMTPException):
        raise NotificationError("welcome email could not be sent") from None
    if refused:
        raise NotificationError("SMTP relay refused one or more recipients")


def audit_record(
    args: argparse.Namespace,
    recipient: str,
    cc_addresses: tuple[str, ...],
    outcome: str,
    error: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": args.request_id,
        "operation": "create",
        "environment": "staging",
        "change_reference": args.change_reference,
        "actor": args.actor,
        "outcome": outcome,
        "recipient": recipient,
        "sender": SENDER_EMAIL,
        "cc": list(cc_addresses),
    }
    if error is not None:
        record["error"] = error
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", default="")
    parser.add_argument("--display-name", default="")
    parser.add_argument("--cc-addresses", required=True)
    parser.add_argument("--change-reference", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--request-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    recipient = "unavailable"
    cc_addresses: tuple[str, ...] = ()
    try:
        _username, recipient = resolve_recipient(args.username, args.email)
        cc_addresses = parse_cc_addresses(args.cc_addresses)
        body = render_html(args.display_name)
        send_message(
            build_message(recipient, body, cc_addresses),
            recipient,
            cc_addresses,
        )
        audit = audit_record(args, recipient, cc_addresses, "sent")
        print("LITELLM_USER_NOTIFICATION_AUDIT=" + json.dumps(audit, sort_keys=True))
        return 0
    except NotificationError as error:
        audit = audit_record(
            args, recipient, cc_addresses, "failed", str(error)
        )
        print("LITELLM_USER_NOTIFICATION_AUDIT=" + json.dumps(audit, sort_keys=True))
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
