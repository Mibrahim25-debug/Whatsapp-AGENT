"""Configuration: reads settings from environment variables (and the .env file).

Secrets (API keys) live only in .env, never in source code.
Call `Settings.from_env()` to get a settings object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load .env from the current working directory into os.environ.
# Existing environment variables are NOT overwritten.
load_dotenv()

DEFAULT_SYSTEM_PROMPT = (
    "You are a friendly, helpful cricket assistant being tested through WhatsApp. "
    "Answer cricket questions clearly, accurately, and naturally. "
    "Keep replies reasonably concise for WhatsApp (usually under 120 words). "
    "Use plain text; WhatsApp supports *bold* and _italic_ but not Markdown headings or tables. "
    "If the user asks for current scores, live matches, or recent results and you cannot "
    "verify them, say so honestly and suggest checking a reliable source such as the ICC "
    "website or ESPNcricinfo. Never invent statistics or claim you checked live data. "
    "If the user asks about something unrelated to cricket, answer briefly and politely "
    "steer the conversation back to cricket."
)

DEFAULT_INTRO = "Hello! I'm your cricket assistant. Ask me anything about cricket."


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass
class Settings:
    # 360dialog sandbox
    d360_api_key: str = ""
    d360_base_url: str = "https://waba-sandbox.360dialog.io"
    # "to" for a phone number, "recipient" for a Business-Scoped User ID (BSUID)
    d360_recipient_field: str = "to"
    # Optional: always reply to this phone number / BSUID instead of the sender id
    # found in the webhook. Useful when the sandbox gives you a BSUID.
    d360_recipient_id: str = ""
    # Optional comma-separated allow-list of sender ids. Empty = allow everyone.
    allowed_senders: list[str] = field(default_factory=list)

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout: float = 45.0
    openrouter_max_tokens: int = 500

    # Webhook protection: if set, /webhook requires ?token=<value> in the URL.
    webhook_verify_token: str = ""
    public_webhook_url: str = ""

    # webhook.site mode (no tunnel needed): poll https://webhook.site/<uuid> for events.
    # Set WEBHOOKSITE_URL to enable. Old requests present at startup are skipped
    # unless WEBHOOKSITE_REPLAY_EXISTING=true.
    webhooksite_url: str = ""
    webhooksite_interval: float = 3.0
    webhooksite_replay_existing: bool = False

    # Agent behaviour
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    history_max_messages: int = 10  # user + assistant messages kept per sender
    greeting_mode_enabled: bool = False
    greeting_intro_text: str = DEFAULT_INTRO
    fallback_message: str = (
        "Sorry, I can't answer right now because the AI service is busy. "
        "Please try again in a minute."
    )

    # Server
    port: int = 8000
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        field_name = (env("D360_RECIPIENT_FIELD") or "to").strip().lower()
        if field_name not in {"to", "recipient"}:
            field_name = "to"
        allowed = [s.strip() for s in (env("ALLOWED_SENDERS") or "").split(",") if s.strip()]
        return cls(
            d360_api_key=(env("D360_API_KEY") or "").strip(),
            d360_base_url=(env("D360_BASE_URL") or cls.d360_base_url).rstrip("/"),
            d360_recipient_field=field_name,
            d360_recipient_id=(env("D360_RECIPIENT_ID") or "").strip(),
            allowed_senders=allowed,
            openrouter_api_key=(env("OPENROUTER_API_KEY") or "").strip(),
            openrouter_model=(env("OPENROUTER_MODEL") or cls.openrouter_model).strip(),
            openrouter_base_url=(env("OPENROUTER_BASE_URL") or cls.openrouter_base_url).rstrip("/"),
            openrouter_timeout=_float(env("OPENROUTER_TIMEOUT"), cls.openrouter_timeout),
            openrouter_max_tokens=_int(env("OPENROUTER_MAX_TOKENS"), cls.openrouter_max_tokens),
            webhook_verify_token=(env("WEBHOOK_VERIFY_TOKEN") or "").strip(),
            public_webhook_url=(env("PUBLIC_WEBHOOK_URL") or "").strip(),
            webhooksite_url=(env("WEBHOOKSITE_URL") or "").strip(),
            webhooksite_interval=max(1.0, _float(env("WEBHOOKSITE_INTERVAL"), cls.webhooksite_interval)),
            webhooksite_replay_existing=_bool(env("WEBHOOKSITE_REPLAY_EXISTING"), False),
            system_prompt=env("SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT,
            history_max_messages=_int(env("HISTORY_MAX_MESSAGES"), cls.history_max_messages),
            greeting_mode_enabled=_bool(env("GREETING_MODE_ENABLED"), False),
            greeting_intro_text=env("GREETING_INTRO_TEXT") or DEFAULT_INTRO,
            fallback_message=env("FALLBACK_MESSAGE") or cls.fallback_message,
            port=_int(env("PORT"), cls.port),
            log_level=(env("LOG_LEVEL") or "INFO").upper(),
        )

    def missing_required(self) -> list[str]:
        """Names of required settings that are empty (values are never returned)."""
        missing = []
        if not self.d360_api_key:
            missing.append("D360_API_KEY")
        if not self.openrouter_api_key:
            missing.append("OPENROUTER_API_KEY")
        return missing
