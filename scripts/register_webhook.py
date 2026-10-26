"""Register your public webhook URL with the 360dialog sandbox.

Usage (from the project folder, venv active):
    python scripts/register_webhook.py https://YOUR-NGROK-DOMAIN/webhook

- Reads D360_API_KEY from .env. If it is missing, asks for it WITHOUT echoing.
- Adds ?token=WEBHOOK_VERIFY_TOKEN automatically if that setting is used.
- First checks that the URL reaches your running server (/health), so you do not
  replace a working webhook with a dead one.
- Never prints the API key.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path
from urllib.parse import urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import Settings  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    url = sys.argv[1] if len(sys.argv) > 1 else (settings.public_webhook_url or input("Public webhook URL: "))
    url = url.strip()

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        print("ERROR: URL must start with https:// (use the ngrok HTTPS URL).")
        return 1
    # webhook.site mode: the URL is just a mailbox; our agent polls it, so there is
    # no /webhook path and no backend to check through that URL.
    via_webhooksite = parsed.netloc.lower().endswith("webhook.site")

    if not via_webhooksite:
        if not parsed.path.rstrip("/").endswith("/webhook"):
            print("ERROR: URL should end with /webhook, e.g. https://abc.example.com/webhook")
            return 1

        # Step 1: make sure the public URL really reaches this backend.
        health_url = f"{parsed.scheme}://{parsed.netloc}/health"
        try:
            health = httpx.get(health_url, timeout=15, headers={"ngrok-skip-browser-warning": "1"})
            ok = health.status_code == 200 and health.json().get("status") == "ok"
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: could not reach {health_url}: {type(exc).__name__}")
            ok = False
        if not ok:
            print("Backend not reachable through this URL. Start uvicorn + the tunnel first. Webhook NOT changed.")
            return 1
        missing = health.json().get("missing_settings") or []
        if missing:
            print(f"WARNING: server is missing settings: {', '.join(missing)}")
        print(f"OK: {health_url} reached your backend.")

    final_url = url
    if settings.webhook_verify_token and not via_webhooksite:
        final_url = f"{url}?{urlencode({'token': settings.webhook_verify_token})}"

    api_key = settings.d360_api_key or getpass.getpass("D360 API key (hidden): ").strip()
    if not api_key:
        print("ERROR: no API key.")
        return 1

    # Step 2: register.
    try:
        response = httpx.post(
            f"{settings.d360_base_url}/v1/configs/webhook",
            json={"url": final_url},
            headers={"D360-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        print(f"ERROR: request failed: {type(exc).__name__}")
        return 1

    shown = url + ("?token=***" if final_url != url else "")
    print(f"360dialog answered HTTP {response.status_code} for {shown}")
    body = response.text.strip()
    if body and body != "null":
        print(f"Response body: {body[:300]}")
    else:
        print("Response body empty/null (this can be normal).")

    if response.status_code in (200, 201):
        print("Registration request ACCEPTED. Now prove it: send a WhatsApp message and watch the uvicorn log.")
        return 0
    if response.status_code == 401:
        print("401: API key missing or invalid.")
    else:
        print("Registration NOT confirmed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
