"""OpenRouter client: sends chat messages to an LLM and returns the reply text.

Docs: https://openrouter.ai/docs/api-reference/overview
Endpoint: POST https://openrouter.ai/api/v1/chat/completions
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Status codes that are usually temporary. We retry these ONCE at most,
# so a broken model cannot burn through the free quota.
_TRANSIENT = {408, 500, 502, 503, 504}


class LLMError(Exception):
    """The LLM could not produce an answer. `reason` is safe to log."""

    def __init__(self, reason: str, status_code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 45.0,
        max_tokens: int = 500,
        max_retries: int = 1,
        transport: httpx.BaseTransport | None = None,  # tests pass a mock here
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

    def chat(self, messages: list[dict]) -> str:
        if not self._api_key:
            raise LLMError("OPENROUTER_API_KEY is not set")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # Optional attribution headers documented by OpenRouter.
            "HTTP-Referer": "http://localhost",
            "X-Title": "WhatsApp Cricket Agent",
        }
        body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.post("/chat/completions", json=body, headers=headers)
            except httpx.TimeoutException:
                error = LLMError("OpenRouter request timed out")
            except httpx.HTTPError as exc:
                error = LLMError(f"Network error calling OpenRouter: {type(exc).__name__}")
            else:
                if response.status_code == 200:
                    return self._parse(response)
                error = self._http_error(response)
                if response.status_code not in _TRANSIENT:
                    raise error

            if attempt > self.max_retries:
                raise error
            logger.warning("OpenRouter transient failure (%s); retrying once", error.reason)
            time.sleep(1.5)

    @staticmethod
    def _http_error(response: httpx.Response) -> LLMError:
        code = response.status_code
        reasons = {
            400: "bad request (check OPENROUTER_MODEL and the message format)",
            401: "invalid or missing OpenRouter API key",
            402: "OpenRouter account has insufficient credits for this model",
            403: "request blocked by OpenRouter moderation or permissions",
            404: "model not found (check OPENROUTER_MODEL)",
            408: "OpenRouter request timed out",
            429: "OpenRouter rate limit or free quota reached",
        }
        reason = reasons.get(code, "OpenRouter or the model provider is unavailable")
        return LLMError(f"HTTP {code}: {reason}", status_code=code)

    def _parse(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            raise LLMError("OpenRouter returned a non-JSON response")
        if not isinstance(data, dict):
            raise LLMError("OpenRouter returned an unexpected response shape")
        # OpenRouter can return HTTP 200 with an error object (e.g. provider failure).
        if data.get("error"):
            err = data["error"] if isinstance(data["error"], dict) else {}
            raise LLMError(f"OpenRouter error: {err.get('message', 'unknown error')}"[:200])
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("OpenRouter response had no choices/message/content")
        if not isinstance(content, str) or not content.strip():
            raise LLMError("OpenRouter returned an empty answer")
        used_model = data.get("model")
        if used_model:
            # openrouter/free picks a model for us; log which one answered.
            logger.info("OpenRouter answered with model %s", used_model)
        return content.strip()

    def close(self) -> None:
        self._client.close()
