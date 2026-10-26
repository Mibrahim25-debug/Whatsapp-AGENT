import json

import httpx
import pytest

from app.agent import ConversationHistory, CricketAgent, SeenMessages
from app.config import Settings
from app.llm_client import LLMError, OpenRouterClient
from app.webhook import IncomingText
from app.whatsapp_client import SendResult


class RecordingLLM:
    def __init__(self, replies=None, error=None):
        self.replies = list(replies or ["answer"])
        self.error = error
        self.seen_messages = []

    def chat(self, messages):
        self.seen_messages.append(messages)
        if self.error:
            raise self.error
        return self.replies.pop(0) if self.replies else "answer"


class FakeMessenger:
    def __init__(self, result=None):
        self.sent = []
        self.result = result or SendResult(True, 201)

    def send_text(self, recipient, text):
        self.sent.append((recipient, text))
        return self.result


def agent_with(llm, **overrides):
    return CricketAgent(Settings(**overrides), llm, FakeMessenger())


# --- history / follow-ups -------------------------------------------------------


def test_follow_up_includes_history():
    llm = RecordingLLM(["Test cricket lasts up to 5 days.", "T20 is 20 overs per side."])
    agent = agent_with(llm)
    agent.generate_reply("u1", "How long is Test cricket?")
    agent.generate_reply("u1", "And T20?")
    second_call = llm.seen_messages[1]
    assert second_call[0]["role"] == "system"
    assert [m["content"] for m in second_call[1:]] == [
        "How long is Test cricket?",
        "Test cricket lasts up to 5 days.",
        "And T20?",
    ]


def test_history_is_per_sender():
    llm = RecordingLLM()
    agent = agent_with(llm)
    agent.generate_reply("u1", "q1")
    agent.generate_reply("u2", "q2")
    assert len(llm.seen_messages[1]) == 2  # system + q2 only


def test_history_is_bounded():
    history = ConversationHistory(max_messages=4)
    for i in range(10):
        history.add("u", "user", str(i))
    assert [m["content"] for m in history.get("u")] == ["6", "7", "8", "9"]


def test_seen_messages_bounded():
    seen = SeenMessages(max_size=3)
    for i in range(5):
        assert seen.check_and_add(str(i))
    assert len(seen) == 3
    assert not seen.check_and_add("4")
    assert seen.check_and_add("0")  # oldest was evicted


# --- greeting mode ----------------------------------------------------------------


def test_greeting_goes_to_llm_by_default():
    llm = RecordingLLM(["Hello! Ask me about cricket."])
    agent = agent_with(llm)
    assert agent.generate_reply("u", "Hi") == "Hello! Ask me about cricket."
    assert len(llm.seen_messages) == 1


def test_greeting_mode_returns_fixed_intro():
    llm = RecordingLLM()
    agent = agent_with(llm, greeting_mode_enabled=True, greeting_intro_text="I am Ibrahim's bot.")
    assert agent.generate_reply("u", "Hello!") == "I am Ibrahim's bot."
    assert llm.seen_messages == []
    agent.generate_reply("u", "What is an over?")  # non-greeting still uses LLM
    assert len(llm.seen_messages) == 1


# --- failures ----------------------------------------------------------------------


def test_llm_failure_sends_fallback():
    llm = RecordingLLM(error=LLMError("HTTP 429: rate limit"))
    agent = agent_with(llm, fallback_message="Busy, try later.")
    result = agent.handle(IncomingText("m1", "u1", "q"))
    assert result.accepted
    assert agent.messenger.sent == [("u1", "Busy, try later.")]
    assert agent.history.get("u1") == []  # fallback not stored


def test_handle_never_raises():
    class Boom:
        def chat(self, messages):
            raise RuntimeError("unexpected")

    agent = agent_with(Boom())
    assert agent.handle(IncomingText("m1", "u1", "q")) is None


def test_allowed_senders_filter():
    agent = agent_with(RecordingLLM(), allowed_senders=["111"])
    assert agent.handle(IncomingText("m1", "222", "q")) is None
    assert agent.messenger.sent == []


# --- OpenRouter client (mocked HTTP, no real quota used) ----------------------------


def openrouter(handler, **kw):
    return OpenRouterClient("test-key", "openrouter/free", transport=httpx.MockTransport(handler), **kw)


def test_openrouter_success_parsing():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "some/free-model", "choices": [{"message": {"content": " Six balls. "}}]})

    client = openrouter(handler)
    assert client.chat([{"role": "user", "content": "How many balls in an over?"}]) == "Six balls."
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["model"] == "openrouter/free"
    assert captured["body"]["messages"][0]["content"] == "How many balls in an over?"


@pytest.mark.parametrize("code,text", [(401, "invalid"), (402, "credits"), (404, "not found"), (429, "rate limit")])
def test_openrouter_http_errors_no_retry(code, text):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(code, json={"error": {"message": "x"}})

    with pytest.raises(LLMError) as exc:
        openrouter(handler).chat([])
    assert text in exc.value.reason
    assert exc.value.status_code == code
    assert len(calls) == 1


def test_openrouter_transient_retries_once_only(monkeypatch):
    monkeypatch.setattr("app.llm_client.time.sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(LLMError):
        openrouter(handler).chat([])
    assert len(calls) == 2


def test_openrouter_network_error(monkeypatch):
    monkeypatch.setattr("app.llm_client.time.sleep", lambda s: None)

    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(LLMError) as exc:
        openrouter(handler).chat([])
    assert "Network error" in exc.value.reason


@pytest.mark.parametrize("body", [
    {"choices": []},
    {"choices": [{"message": {"content": None}}]},
    {"error": {"message": "provider failed"}},
    ["not", "a", "dict"],
])
def test_openrouter_malformed_responses(body):
    client = openrouter(lambda request: httpx.Response(200, json=body))
    with pytest.raises(LLMError):
        client.chat([])


def test_openrouter_missing_key():
    client = OpenRouterClient("", "openrouter/free")
    with pytest.raises(LLMError):
        client.chat([])


def test_error_reason_never_contains_key():
    client = openrouter(lambda request: httpx.Response(401))
    with pytest.raises(LLMError) as exc:
        client.chat([])
    assert "test-key" not in str(exc.value)
