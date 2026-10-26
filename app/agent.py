"""The cricket agent: decides what to reply and remembers recent conversation.

Memory here is IN-MEMORY ONLY. Restarting the server erases conversation
history and the list of already-processed message ids.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict, deque
from typing import Protocol

from app.config import Settings
from app.llm_client import LLMError
from app.webhook import IncomingText
from app.whatsapp_client import SendResult

logger = logging.getLogger(__name__)

GREETINGS = {"hi", "hello", "hey", "hii", "salam", "assalamualaikum", "assalam o alaikum"}


class ChatModel(Protocol):
    def chat(self, messages: list[dict]) -> str: ...


class Messenger(Protocol):
    def send_text(self, recipient: str, text: str) -> SendResult: ...


def mask_id(sender_id: str) -> str:
    """Hide most of a phone number / BSUID in logs (privacy)."""
    return sender_id if len(sender_id) <= 4 else "***" + sender_id[-4:]


class SeenMessages:
    """Remembers recently processed message ids, with a fixed maximum size."""

    def __init__(self, max_size: int = 1000):
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._max = max_size
        self._lock = threading.Lock()

    def check_and_add(self, message_id: str) -> bool:
        """Return True if new (and record it), False if already seen."""
        with self._lock:
            if message_id in self._ids:
                return False
            self._ids[message_id] = None
            if len(self._ids) > self._max:
                self._ids.popitem(last=False)  # drop the oldest
            return True

    def __len__(self) -> int:
        return len(self._ids)


class ConversationHistory:
    """Last N chat messages per sender, so follow-up questions make sense."""

    def __init__(self, max_messages: int = 10, max_senders: int = 500):
        self._max_messages = max(2, max_messages)
        self._max_senders = max_senders
        self._data: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, sender_id: str) -> list[dict]:
        with self._lock:
            return list(self._data.get(sender_id, []))

    def add(self, sender_id: str, role: str, content: str) -> None:
        with self._lock:
            if sender_id not in self._data:
                self._data[sender_id] = deque(maxlen=self._max_messages)
                if len(self._data) > self._max_senders:
                    self._data.popitem(last=False)
            self._data.move_to_end(sender_id)
            self._data[sender_id].append({"role": role, "content": content})

    def clear(self, sender_id: str) -> None:
        with self._lock:
            self._data.pop(sender_id, None)


class CricketAgent:
    def __init__(
        self,
        settings: Settings,
        llm: ChatModel,
        messenger: Messenger,
        history: ConversationHistory | None = None,
        seen: SeenMessages | None = None,
    ):
        self.settings = settings
        self.llm = llm
        self.messenger = messenger
        self.history = history or ConversationHistory(settings.history_max_messages)
        self.seen = seen or SeenMessages()

    # --- pure decision logic -------------------------------------------------

    def is_greeting(self, text: str) -> bool:
        cleaned = text.strip().lower().strip("!.?, ")
        return cleaned in GREETINGS

    def generate_reply(self, sender_id: str, text: str) -> str:
        """Return the reply text for one user message (uses LLM unless greeting mode hits)."""
        if self.settings.greeting_mode_enabled and self.is_greeting(text):
            reply = self.settings.greeting_intro_text
            self.history.add(sender_id, "user", text)
            self.history.add(sender_id, "assistant", reply)
            return reply

        messages = [{"role": "system", "content": self.settings.system_prompt}]
        messages += self.history.get(sender_id)
        messages.append({"role": "user", "content": text})
        try:
            reply = self.llm.chat(messages)
        except LLMError as exc:
            logger.error("LLM failed for %s: %s", mask_id(sender_id), exc.reason)
            # Fallback is not stored in history, so the next question starts clean.
            return self.settings.fallback_message

        self.history.add(sender_id, "user", text)
        self.history.add(sender_id, "assistant", reply)
        return reply

    # --- full pipeline for one incoming message --------------------------------

    def handle(self, message: IncomingText) -> SendResult | None:
        """Process one incoming text. Returns the send result, or None if skipped.

        Never raises: one bad message must not crash the server.
        """
        try:
            who = mask_id(message.sender_id)
            if self.settings.allowed_senders and message.sender_id not in self.settings.allowed_senders:
                logger.warning("Ignoring message from non-allowed sender %s", who)
                return None

            logger.info("Processing message %s from %s (%d chars)", message.message_id[-8:], who, len(message.text))
            reply = self.generate_reply(message.sender_id, message.text)

            recipient = self.settings.d360_recipient_id or message.sender_id
            result = self.messenger.send_text(recipient, reply)
            if result.accepted:
                logger.info(
                    "360dialog ACCEPTED reply (HTTP %s, id=%s). Delivery not yet confirmed.",
                    result.status_code,
                    result.message_id or "n/a",
                )
            else:
                logger.error(
                    "360dialog did NOT accept reply (HTTP %s): %s%s",
                    result.status_code,
                    result.error,
                    " [transient]" if result.transient else "",
                )
            return result
        except Exception:  # noqa: BLE001 - last-resort guard for the background task
            logger.exception("Unexpected error while handling message %s", message.message_id[-8:])
            return None
