"""Webhook.site poller: fetches incoming webhook calls by asking webhook.site for them.

Why: a tunnel (ngrok / cloudflared) is not needed. 360dialog posts incoming
WhatsApp events to your https://webhook.site/<uuid> address. This module then
asks the webhook.site API for the newest requests every few seconds and hands
each JSON body to the same code the /webhook route uses.

API used (anonymous, the token uuid is the only "key"):
    GET https://webhook.site/token/<uuid>/requests?sorting=newest&per_page=50
Each item has a `uuid` and the raw request body as a string in `content`.

Privacy: webhook.site stores your WhatsApp messages on their server. Use it for
testing only.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import OrderedDict
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def extract_token(url_or_uuid: str) -> str:
    """Pull the uuid out of 'https://webhook.site/<uuid>' (or a bare uuid)."""
    match = _UUID.search(url_or_uuid or "")
    return match.group(0).lower() if match else ""


class WebhookSitePoller:
    def __init__(
        self,
        token: str,
        handler: Callable[[dict], None],
        base_url: str = "https://webhook.site",
        interval: float = 3.0,
        replay_existing: bool = False,
        per_page: int = 50,
        transport: httpx.BaseTransport | None = None,  # tests pass a mock here
    ):
        self.token = token
        self.handler = handler
        self.interval = interval
        self.replay_existing = replay_existing
        self.per_page = per_page
        self._seen: OrderedDict[str, None] = OrderedDict()  # bounded, see _remember
        self._first_poll_done = False
        self._failures = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client = httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(15.0, connect=10.0), transport=transport
        )

    # --- one polling round ---------------------------------------------------

    def _remember(self, request_uuid: str) -> None:
        self._seen[request_uuid] = None
        if len(self._seen) > 2000:
            self._seen.popitem(last=False)

    def poll_once(self) -> int:
        """Fetch new requests and pass each JSON body to the handler. Returns how many were handled."""
        response = self._client.get(
            f"/token/{self.token}/requests",
            params={"sorting": "newest", "per_page": self.per_page},
        )
        if response.status_code == 404:
            raise RuntimeError("webhook.site token not found (check WEBHOOKSITE_URL)")
        response.raise_for_status()
        data = response.json().get("data") if isinstance(response.json(), dict) else None
        items = [i for i in (data or []) if isinstance(i, dict) and i.get("uuid")]

        new_items = [i for i in items if i["uuid"] not in self._seen]
        for item in items:
            self._remember(item["uuid"])

        if not self._first_poll_done:
            self._first_poll_done = True
            if not self.replay_existing:
                logger.info("webhook.site: skipped %d old request(s) already there at startup", len(new_items))
                return 0

        handled = 0
        for item in reversed(new_items):  # oldest first
            if (item.get("method") or "POST").upper() != "POST":
                continue
            try:
                payload = json.loads(item.get("content") or "")
            except (TypeError, ValueError):
                logger.info("webhook.site: request %s had no JSON body; ignored", str(item["uuid"])[:8])
                continue
            try:
                self.handler(payload)
                handled += 1
            except Exception:  # noqa: BLE001 - one bad event must not stop polling
                logger.exception("webhook.site: handler failed for request %s", str(item["uuid"])[:8])
        return handled

    # --- background loop -------------------------------------------------------

    def _loop(self) -> None:
        logger.info("webhook.site poller started (every %.1fs)", self.interval)
        while not self._stop.is_set():
            try:
                self.poll_once()
                self._failures = 0
            except Exception as exc:  # noqa: BLE001
                self._failures += 1
                # Log the first failure and then every 10th, to avoid flooding the log.
                if self._failures == 1 or self._failures % 10 == 0:
                    logger.error("webhook.site poll failed (%d in a row): %s", self._failures, type(exc).__name__ + ": " + str(exc)[:150])
            wait = self.interval if self._failures == 0 else min(30.0, self.interval * (1 + self._failures))
            self._stop.wait(wait)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="webhooksite-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._client.close()
