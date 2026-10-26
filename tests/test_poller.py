import json

import httpx
import pytest

from app.agent import CricketAgent
from app.config import Settings
from app.main import ingest
from app.poller import WebhookSitePoller, extract_token
from app.whatsapp_client import SendResult

TOKEN = "123e4567-e89b-12d3-a456-426614174000"


def wa_payload(mid="wamid.1", body="What is an over?"):
    msg = {"from": "923001234567", "id": mid, "type": "text", "text": {"body": body}}
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def item(uuid, payload=None, raw=None, method="POST"):
    content = raw if raw is not None else json.dumps(payload)
    return {"uuid": uuid, "method": method, "content": content}


class Server:
    """Pretends to be the webhook.site API; `items` is newest-first like the real API."""

    def __init__(self, items):
        self.items = items
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(200, json={"data": self.items})


def make_poller(server, handler, **kw):
    return WebhookSitePoller(TOKEN, handler, transport=httpx.MockTransport(server), **kw)


def test_extract_token():
    assert extract_token(f"https://webhook.site/{TOKEN}") == TOKEN
    assert extract_token(f"https://webhook.site/#!/view/{TOKEN.upper()}/x") == TOKEN
    assert extract_token(TOKEN) == TOKEN
    assert extract_token("https://webhook.site/") == ""
    assert extract_token("") == ""


def test_first_poll_skips_old_requests_then_handles_new():
    old = item("old-1", wa_payload("wamid.old"))
    server = Server([old])
    got = []
    poller = make_poller(server, got.append)
    assert poller.poll_once() == 0 and got == []  # startup baseline
    server.items = [item("new-1", wa_payload("wamid.new")), old]
    assert poller.poll_once() == 1
    assert len(got) == 1 and got[0]["entry"][0]["changes"][0]["value"]["messages"][0]["id"] == "wamid.new"
    assert poller.poll_once() == 0  # not handled twice


def test_replay_existing_option():
    server = Server([item("a", wa_payload("wamid.a"))])
    got = []
    assert make_poller(server, got.append, replay_existing=True).poll_once() == 1


def test_handles_oldest_first_and_calls_correct_url():
    server = Server([])
    got = []
    poller = make_poller(server, lambda p: got.append(p["tag"]))
    poller.poll_once()
    server.items = [item("c", {"tag": 3}), item("b", {"tag": 2}), item("a", {"tag": 1})]
    poller.poll_once()
    assert got == [1, 2, 3]
    assert server.requests[0].url.path == f"/token/{TOKEN}/requests"


def test_non_json_and_non_post_ignored_and_handler_errors_isolated():
    server = Server([])
    got = []

    def handler(payload):
        if payload.get("boom"):
            raise RuntimeError("bad event")
        got.append(payload)

    poller = make_poller(server, handler)
    poller.poll_once()
    server.items = [
        item("4", {"ok": 1}),
        item("3", {"boom": True}),
        item("2", raw="not json"),
        item("1", {"ok": 0}, method="GET"),
    ]
    assert poller.poll_once() == 1
    assert got == [{"ok": 1}]


def test_http_errors_raise():
    poller = WebhookSitePoller(TOKEN, lambda p: None, transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(RuntimeError):
        poller.poll_once()
    poller = WebhookSitePoller(TOKEN, lambda p: None, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        poller.poll_once()


def test_end_to_end_poll_to_reply():
    class LLM:
        def chat(self, messages):
            return "Six legal balls."

    class Messenger:
        def __init__(self):
            self.sent = []

        def send_text(self, recipient, text):
            self.sent.append((recipient, text))
            return SendResult(True, 201)

    messenger = Messenger()
    agent = CricketAgent(Settings(d360_api_key="k", openrouter_api_key="k"), LLM(), messenger)
    server = Server([])
    poller = make_poller(server, lambda p: ingest(p, agent, agent.handle))
    poller.poll_once()
    server.items = [item("n1", wa_payload("wamid.1"))]
    poller.poll_once()
    # Same WhatsApp message delivered again as a new webhook.site request: still one reply.
    server.items = [item("n2", wa_payload("wamid.1")), item("n1", wa_payload("wamid.1"))]
    poller.poll_once()
    assert messenger.sent == [("923001234567", "Six legal balls.")]
