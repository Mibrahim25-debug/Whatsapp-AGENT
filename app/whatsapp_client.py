"""360dialog Sandbox client: sends text messages.

Endpoint: POST https://waba-sandbox.360dialog.io/v1/messages
Auth header: D360-API-KEY

HTTP 201 (or 200) means the sandbox ACCEPTED the request. It does not prove the
message was delivered to the phone. Delivery shows up later as a "status" webhook
(sent / delivered / read / failed) and, of course, on the phone itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

WHATSAPP_MAX_TEXT = 4096  # WhatsApp text body limit

_ERRORS = {
    400: "bad request (check payload format)",
    401: "D360 API key missing or invalid",
    403: "recipient mismatch or missing recipient field (check D360_RECIPIENT_FIELD / D360_RECIPIENT_ID)",
    429: "sandbox message-request limit exceeded",
    502: "temporary upstream failure (bad gateway)",
    504: "temporary upstream failure (gateway timeout)",
}


@dataclass
class SendResult:
    accepted: bool  # True = API accepted the request (NOT proof of delivery)
    status_code: int | None
    message_id: str = ""
    error: str = ""
    transient: bool = False  # True = maybe worth trying again later


class WhatsAppClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://waba-sandbox.360dialog.io",
        recipient_field: str = "to",
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,  # tests pass a mock here
    ):
        if recipient_field not in {"to", "recipient"}:
            raise ValueError("recipient_field must be 'to' or 'recipient'")
        self.recipient_field = recipient_field
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

    def build_payload(self, recipient: str, text: str) -> dict:
        if len(text) > WHATSAPP_MAX_TEXT:
            text = text[: WHATSAPP_MAX_TEXT - 3] + "..."
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "type": "text",
            "text": {"body": text},
        }
        # Exactly one of "to" / "recipient", chosen by configuration.
        payload[self.recipient_field] = recipient
        return payload

    def send_text(self, recipient: str, text: str) -> SendResult:
        """Send once. No automatic retry: a retry after a timeout could send a duplicate."""
        if not self._api_key:
            return SendResult(False, None, error="D360_API_KEY is not set")
        if not recipient:
            return SendResult(False, None, error="no recipient id")

        headers = {"D360-API-KEY": self._api_key, "Content-Type": "application/json"}
        try:
            response = self._client.post(
                "/v1/messages", json=self.build_payload(recipient, text), headers=headers
            )
        except httpx.TimeoutException:
            return SendResult(False, None, error="timeout calling 360dialog", transient=True)
        except httpx.HTTPError as exc:
            return SendResult(
                False, None, error=f"network error: {type(exc).__name__}", transient=True
            )

        code = response.status_code
        if code in (200, 201):
            return SendResult(True, code, message_id=self._message_id(response))
        return SendResult(
            False,
            code,
            error=_ERRORS.get(code, f"unexpected HTTP {code}"),
            transient=code in (500, 502, 503, 504),
        )

    @staticmethod
    def _message_id(response: httpx.Response) -> str:
        try:
            data = response.json()
            return str(data["messages"][0]["id"])
        except (ValueError, KeyError, IndexError, TypeError):
            return ""

    def close(self) -> None:
        self._client.close()
