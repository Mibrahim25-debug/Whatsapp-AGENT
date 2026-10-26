"""Webhook parsing: turns a raw 360dialog webhook JSON body into simple events.

The sandbox may send either of two shapes, so we support both:

1. Cloud API style (Meta WhatsApp Cloud API):
   {"object": "whatsapp_business_account",
    "entry": [{"changes": [{"value": {"metadata": {...},
                                      "contacts": [...],
                                      "messages": [...],
                                      "statuses": [...]}}]}]}

2. Flat / On-Premise style (older 360dialog sandbox):
   {"contacts": [...], "messages": [...], "statuses": [...]}

Every field is treated as optional. Anything we cannot understand is skipped,
never raised as an exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class IncomingText:
    """A text message a user sent to the agent."""

    message_id: str
    sender_id: str  # phone number (wa_id) or BSUID, whatever the payload gives
    text: str
    sender_name: str = ""


@dataclass
class ParseResult:
    texts: list[IncomingText]
    status_count: int = 0  # delivery/read status updates (ignored)
    ignored_count: int = 0  # media, unsupported or malformed messages


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _value_blocks(payload: dict) -> list[dict]:
    """Return every block that may contain `messages` / `statuses`."""
    blocks: list[dict] = []
    for entry in _as_list(payload.get("entry")):
        for change in _as_list(_as_dict(entry).get("changes")):
            value = _as_dict(_as_dict(change).get("value"))
            if value:
                blocks.append(value)
    if "messages" in payload or "statuses" in payload:
        blocks.append(payload)
    return blocks


def _sender_of(message: dict) -> str:
    # `from` is the normal field. Newer payloads may carry a BSUID instead.
    for key in ("from", "from_user_id", "user_id"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _names_by_id(contacts: list) -> dict[str, str]:
    names: dict[str, str] = {}
    for contact in contacts:
        contact = _as_dict(contact)
        wa_id = contact.get("wa_id") or contact.get("user_id")
        name = _as_dict(contact.get("profile")).get("name")
        if isinstance(wa_id, str) and isinstance(name, str):
            names[wa_id] = name
    return names


def parse_webhook(payload: Any) -> ParseResult:
    result = ParseResult(texts=[])
    if not isinstance(payload, dict):
        return result

    for block in _value_blocks(payload):
        result.status_count += len(_as_list(block.get("statuses")))

        # Our own business number; messages "from" it are echoes, not users.
        metadata = _as_dict(block.get("metadata"))
        own_number = _digits(str(metadata.get("display_phone_number") or ""))
        names = _names_by_id(_as_list(block.get("contacts")))

        for raw in _as_list(block.get("messages")):
            message = _as_dict(raw)
            sender = _sender_of(message)
            message_id = message.get("id")
            if not sender or not isinstance(message_id, str) or not message_id:
                result.ignored_count += 1
                continue
            if own_number and _digits(sender) == own_number:
                result.ignored_count += 1
                continue
            if message.get("type") != "text":
                result.ignored_count += 1
                continue
            body = _as_dict(message.get("text")).get("body")
            if not isinstance(body, str) or not body.strip():
                result.ignored_count += 1
                continue
            result.texts.append(
                IncomingText(
                    message_id=message_id,
                    sender_id=sender,
                    text=body.strip(),
                    sender_name=names.get(sender, ""),
                )
            )
    return result


def message_types(payload: Any) -> list[str]:
    """Types of all messages in a payload (for logging ignored media types)."""
    if not isinstance(payload, dict):
        return []
    types = []
    for block in _value_blocks(payload):
        for raw in _as_list(block.get("messages")):
            types.append(str(_as_dict(raw).get("type") or "unknown"))
    return types
