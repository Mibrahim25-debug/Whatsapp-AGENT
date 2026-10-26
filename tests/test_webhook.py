from fastapi.testclient import TestClient

from app.agent import CricketAgent
from app.config import Settings
from app.main import create_app
from app.webhook import parse_webhook
from app.whatsapp_client import SendResult


def cloud_payload(messages=None, statuses=None, own="551146733492"):
    value = {"messaging_product": "whatsapp", "metadata": {"display_phone_number": own}}
    if messages is not None:
        value["messages"] = messages
        value["contacts"] = [{"profile": {"name": "Ali"}, "wa_id": "923001234567"}]
    if statuses is not None:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}]}


def text_msg(mid="wamid.1", sender="923001234567", body="What is a hat-trick?"):
    return {"from": sender, "id": mid, "timestamp": "1", "type": "text", "text": {"body": body}}


# --- parser -----------------------------------------------------------------


def test_parses_cloud_api_text():
    result = parse_webhook(cloud_payload([text_msg()]))
    assert len(result.texts) == 1
    msg = result.texts[0]
    assert msg.sender_id == "923001234567"
    assert msg.text == "What is a hat-trick?"
    assert msg.message_id == "wamid.1"
    assert msg.sender_name == "Ali"


def test_parses_flat_on_premise_text():
    payload = {"contacts": [{"wa_id": "923001234567"}], "messages": [text_msg()]}
    assert parse_webhook(payload).texts[0].text == "What is a hat-trick?"


def test_bsuid_sender_field():
    msg = {"from_user_id": "BSUID123", "id": "wamid.2", "type": "text", "text": {"body": "hi"}}
    assert parse_webhook({"messages": [msg]}).texts[0].sender_id == "BSUID123"


def test_malformed_payloads_do_not_crash():
    for payload in [None, [], "x", 5, {}, {"entry": "bad"}, {"entry": [{"changes": [None]}]},
                    {"messages": [None, {"id": "x"}, {"from": "1", "type": "text"}]},
                    {"messages": [{"from": "1", "id": "a", "type": "text", "text": {"body": "  "}}]}]:
        assert parse_webhook(payload).texts == []


def test_status_only_event_ignored():
    result = parse_webhook(cloud_payload(statuses=[{"id": "wamid.9", "status": "delivered"}]))
    assert result.texts == []
    assert result.status_count == 1


def test_media_message_ignored():
    image = {"from": "923001234567", "id": "wamid.3", "type": "image", "image": {"id": "x"}}
    result = parse_webhook(cloud_payload([image]))
    assert result.texts == []
    assert result.ignored_count == 1


def test_own_echo_ignored():
    result = parse_webhook(cloud_payload([text_msg(sender="551146733492")]))
    assert result.texts == []


# --- FastAPI route ------------------------------------------------------------


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return "A hat-trick is three wickets in three consecutive deliveries."


class FakeMessenger:
    def __init__(self):
        self.sent = []

    def send_text(self, recipient, text):
        self.sent.append((recipient, text))
        return SendResult(True, 201, message_id="wamid.out")


def make_client(**overrides):
    settings = Settings(d360_api_key="k", openrouter_api_key="k", **overrides)
    llm, messenger = FakeLLM(), FakeMessenger()
    agent = CricketAgent(settings, llm, messenger)
    return TestClient(create_app(settings, agent)), llm, messenger


def test_webhook_replies_to_text():
    client, llm, messenger = make_client()
    response = client.post("/webhook", json=cloud_payload([text_msg()]))
    assert response.status_code == 200
    assert response.json()["queued"] == 1
    assert llm.calls == 1
    assert messenger.sent == [("923001234567", "A hat-trick is three wickets in three consecutive deliveries.")]


def test_duplicate_message_id_processed_once():
    client, llm, messenger = make_client()
    client.post("/webhook", json=cloud_payload([text_msg()]))
    second = client.post("/webhook", json=cloud_payload([text_msg()]))
    assert second.json()["queued"] == 0
    assert llm.calls == 1
    assert len(messenger.sent) == 1


def test_status_webhook_does_not_reply():
    client, llm, messenger = make_client()
    response = client.post("/webhook", json=cloud_payload(statuses=[{"id": "x", "status": "read"}]))
    assert response.status_code == 200
    assert llm.calls == 0 and messenger.sent == []


def test_invalid_json_returns_400():
    client, _, _ = make_client()
    response = client.post("/webhook", content=b"not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400


def test_webhook_token_required_when_configured():
    client, llm, _ = make_client(webhook_verify_token="secret123")
    assert client.post("/webhook", json=cloud_payload([text_msg()])).status_code == 403
    assert client.post("/webhook?token=wrong", json=cloud_payload([text_msg()])).status_code == 403
    assert llm.calls == 0
    ok = client.post("/webhook?token=secret123", json=cloud_payload([text_msg()]))
    assert ok.status_code == 200 and llm.calls == 1


def test_recipient_override_used():
    client, _, messenger = make_client(d360_recipient_id="BSUID-XYZ")
    client.post("/webhook", json=cloud_payload([text_msg()]))
    assert messenger.sent[0][0] == "BSUID-XYZ"


def test_health_shows_no_secrets():
    client, _, _ = make_client()
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["missing_settings"] == []
    assert "k" not in body.values()
