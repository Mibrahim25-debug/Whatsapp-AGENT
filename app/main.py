"""FastAPI app: HTTP routes.

Run:  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

Routes:
  GET  /health   -> is the server up, which settings are missing (no secret values)
  POST /webhook  -> direct webhook (needs a public HTTPS tunnel)

Two ways to receive WhatsApp events:
  A) Tunnel mode: 360dialog -> your public URL -> POST /webhook
  B) webhook.site mode: 360dialog -> https://webhook.site/<uuid>, and this app
     polls webhook.site (set WEBHOOKSITE_URL in .env). No tunnel needed.
"""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from app.agent import CricketAgent
from app.config import Settings
from app.llm_client import OpenRouterClient
from app.poller import WebhookSitePoller, extract_token
from app.webhook import IncomingText, message_types, parse_webhook
from app.whatsapp_client import WhatsAppClient

logger = logging.getLogger("app")


def build_agent(settings: Settings) -> CricketAgent:
    llm = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        timeout=settings.openrouter_timeout,
        max_tokens=settings.openrouter_max_tokens,
    )
    messenger = WhatsAppClient(
        api_key=settings.d360_api_key,
        base_url=settings.d360_base_url,
        recipient_field=settings.d360_recipient_field,
    )
    return CricketAgent(settings, llm, messenger)


def ingest(payload, agent: CricketAgent, submit: Callable[[IncomingText], None]) -> int:
    """Shared by both modes: parse one webhook body, skip junk/duplicates, submit the rest.

    Returns how many messages were submitted for a reply.
    """
    result = parse_webhook(payload)
    if result.status_count:
        logger.info("Received %d status update(s); no reply needed", result.status_count)
    if result.ignored_count:
        types = [t for t in message_types(payload) if t != "text"]
        logger.info("Ignored %d unsupported message(s) %s", result.ignored_count, types or "")

    queued = 0
    for message in result.texts:
        if not agent.seen.check_and_add(message.message_id):
            logger.info("Duplicate message %s ignored", message.message_id[-8:])
            continue
        submit(message)
        queued += 1
    return queued


def create_app(settings: Settings | None = None, agent: CricketAgent | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO (one line per 3 s poll); keep only warnings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    agent = agent or build_agent(settings)

    poller: WebhookSitePoller | None = None
    if settings.webhooksite_url:
        token = extract_token(settings.webhooksite_url)
        if token:
            poller = WebhookSitePoller(
                token=token,
                handler=lambda payload: ingest(payload, agent, agent.handle),
                interval=settings.webhooksite_interval,
                replay_existing=settings.webhooksite_replay_existing,
            )
        else:
            logger.error("WEBHOOKSITE_URL is set but has no valid uuid; poller disabled")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if poller:
            poller.start()
        yield
        if poller:
            poller.stop()

    app = FastAPI(title="WhatsApp Cricket Agent", lifespan=lifespan)

    missing = settings.missing_required()
    if missing:
        logger.warning("Missing settings in .env: %s", ", ".join(missing))
    logger.info(
        "Config: model=%s recipient_field=%s greeting_mode=%s webhook_token=%s mode=%s",
        settings.openrouter_model,
        settings.d360_recipient_field,
        settings.greeting_mode_enabled,
        "on" if settings.webhook_verify_token else "off",
        "webhook.site polling" if poller else "direct /webhook",
    )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "missing_settings": settings.missing_required(),
            "model": settings.openrouter_model,
            "recipient_field": settings.d360_recipient_field,
            "greeting_mode_enabled": settings.greeting_mode_enabled,
            "webhook_token_required": bool(settings.webhook_verify_token),
            "webhooksite_polling": poller is not None,
        }

    @app.post("/webhook")
    async def webhook(request: Request, background: BackgroundTasks):
        # Optional shared secret in the URL (?token=...). 360dialog just calls the
        # URL we registered, so a query-string token works with the sandbox.
        if settings.webhook_verify_token:
            token = request.query_params.get("token", "")
            if not hmac.compare_digest(token, settings.webhook_verify_token):
                logger.warning("Rejected webhook call with missing/wrong token")
                return JSONResponse({"error": "forbidden"}, status_code=403)

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - bad JSON must not crash the server
            logger.warning("Webhook body was not valid JSON")
            return JSONResponse({"error": "invalid json"}, status_code=400)

        # Reply in the background so 360dialog gets a fast 200 and does not retry.
        queued = ingest(payload, agent, lambda m: background.add_task(agent.handle, m))

        # 200 = "webhook received". It does NOT mean the AI reply was sent or delivered.
        return {"received": True, "queued": queued}

    return app


app = create_app()
