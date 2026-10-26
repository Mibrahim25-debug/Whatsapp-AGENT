import json

import httpx
import pytest

from app.whatsapp_client import WhatsAppClient


def client_with(handler, field="to", key="test-d360-key"):
    return WhatsAppClient(key, recipient_field=field, transport=httpx.MockTransport(handler))


def test_successful_send_uses_to_field():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["key"] = request.headers["D360-API-KEY"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"messages": [{"id": "wamid.ABC"}]})

    result = client_with(handler).send_text("923001234567", "Hello")
    assert result.accepted and result.status_code == 201 and result.message_id == "wamid.ABC"
    assert captured["url"].endswith("/v1/messages")
    assert captured["key"] == "test-d360-key"
    body = captured["body"]
    assert body["to"] == "923001234567" and "recipient" not in body
    assert body["messaging_product"] == "whatsapp"
    assert body["type"] == "text" and body["text"] == {"body": "Hello"}


def test_recipient_field_for_bsuid():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={})

    result = client_with(handler, field="recipient").send_text("BSUID123", "Hi")
    assert result.accepted and result.message_id == ""
    assert captured["body"]["recipient"] == "BSUID123" and "to" not in captured["body"]


@pytest.mark.parametrize("code,fragment,transient", [
    (401, "API key", False),
    (403, "recipient", False),
    (429, "limit", False),
    (502, "temporary", True),
    (504, "temporary", True),
])
def test_error_codes(code, fragment, transient):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(code, json={"error": "x"})

    result = client_with(handler).send_text("1", "Hi")
    assert not result.accepted
    assert result.status_code == code
    assert fragment in result.error
    assert result.transient is transient
    assert len(calls) == 1  # never auto-retried (avoids duplicate sends)


def test_timeout_is_not_accepted():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    result = client_with(handler).send_text("1", "Hi")
    assert not result.accepted and result.transient and "timeout" in result.error


def test_long_text_truncated():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(201)

    client_with(handler).send_text("1", "x" * 5000)
    assert len(captured["body"]["text"]["body"]) == 4096


def test_missing_key_does_not_call_api():
    calls = []
    result = client_with(lambda r: calls.append(1), key="").send_text("1", "Hi")
    assert not result.accepted and calls == []


def test_invalid_recipient_field_rejected():
    with pytest.raises(ValueError):
        WhatsAppClient("k", recipient_field="phone")
