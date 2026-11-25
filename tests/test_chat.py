"""Chat loop and /chat tests. A fake client replays scripted responses; nothing calls the API."""

import json
from typing import Any

import pytest
from anthropic.types import Message
from fastapi.testclient import TestClient

from backend.chat import (
    MAX_HISTORY_MESSAGES,
    MAX_TOOL_ROUNDS,
    TOOLS,
    VOICE_PROMPTS,
    VOICE_REMINDERS,
    HistoryMessage,
    build_messages,
    run_chat,
    run_tool,
    system_prompt,
)
from backend.config import Settings, get_settings
from backend.main import app, get_claude_client

MODEL = "claude-haiku-5-5"

AR_ARGS = {
    "weapon": "Longsword",
    "affinity": "Standard",
    "upgrade_level": 0,
    "strength": 10,
    "dexterity": 10,
    "intelligence": 10,
    "faith": 10,
    "arcane": 10,
    "two_handed": False,
    "buffs": [],
}


def make_message(content: list[dict[str, Any]], stop_reason: str) -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def tool_use(tool_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


class FakeMessages:
    def __init__(self, responses: list[Message]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Message:
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self.responses:
            raise AssertionError("fake client ran out of scripted responses")
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[Message]) -> None:
        self.messages = FakeMessages(responses)


# ------------------------------------------------------------------ schemas


@pytest.mark.parametrize("tool", TOOLS, ids=[t["name"] for t in TOOLS])
def test_tool_schemas_are_strict(tool: dict[str, Any]) -> None:
    schema = tool["input_schema"]
    assert tool["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


# --------------------------------------------------------------- run_tool


def test_run_tool_attack_rating() -> None:
    out, is_error = run_tool("calculate_attack_rating", AR_ARGS)
    assert not is_error
    assert json.loads(out)["physical"]["base"] == 110


def test_run_tool_standard_affinity_lets_name_decide() -> None:
    out, is_error = run_tool("calculate_attack_rating", {**AR_ARGS, "weapon": "Heavy Longsword"})
    assert not is_error
    assert json.loads(out)["affinity"] == "Heavy"


def test_run_tool_reports_errors() -> None:
    out, is_error = run_tool("calculate_attack_rating", {**AR_ARGS, "weapon": "Nope Sword"})
    assert is_error and "Unknown weapon" in out
    out, is_error = run_tool("calculate_attack_rating", {**AR_ARGS, "strength": 150})
    assert is_error
    out, is_error = run_tool("not_a_tool", {})
    assert is_error


def test_run_tool_lookup(param_db_url: str) -> None:
    out, is_error = run_tool(
        "lookup_weapon",
        {"weapon": "Longsword", "affinity": "Heavy", "upgrade_level": 0},
        param_db_url,
    )
    assert not is_error
    assert json.loads(out)["weapon_id"] == 2000100


# --------------------------------------------------------------- history


def test_history_is_trimmed_and_starts_with_user() -> None:
    history = [HistoryMessage(role="assistant", content="hi")] + [
        HistoryMessage(role="user" if i % 2 == 0 else "assistant", content=f"m{i}")
        for i in range(30)
    ]
    msgs = build_messages("new", history)
    assert msgs[0]["role"] == "user"
    assert len(msgs) <= MAX_HISTORY_MESSAGES + 1
    assert msgs[-1] == {"role": "user", "content": "new"}


# ------------------------------------------------------------------ loop


def test_plain_answer_needs_no_tools() -> None:
    client = FakeClient([make_message([text("Limgrave is the starting region.")], "end_turn")])
    result = run_chat(client, MODEL, "What is Limgrave?")
    assert result.reply == "Limgrave is the starting region."
    assert result.tool_calls == []
    call = client.messages.calls[0]
    assert call["model"] == MODEL
    assert call["tools"] == TOOLS
    assert call["output_config"] == {"effort": "medium"}
    assert call["system"] == system_prompt("veteran")


# ------------------------------------------------------------------ voices


def test_each_voice_keeps_the_shared_rules() -> None:
    prompts = {v: system_prompt(v) for v in VOICE_PROMPTS}
    assert set(prompts) == {"veteran", "in_character"}
    for voice, prompt in prompts.items():
        assert "Attack rating answers follow this shape" in prompt
        assert "call search_guide" in prompt
        assert prompt.endswith(VOICE_PROMPTS[voice])
    assert prompts["veteran"] != prompts["in_character"]


def test_unknown_voice_raises() -> None:
    with pytest.raises(KeyError):
        system_prompt("pirate")


def test_switching_voice_keeps_history() -> None:
    history = [
        HistoryMessage(role="user", content="AR of a Longsword?"),
        HistoryMessage(role="assistant", content="Longsword +0: 120 AR"),
    ]
    client = FakeClient([make_message([text("So it is, Tarnished.")], "end_turn")])
    run_chat(client, MODEL, "and two-handed?", history, voice="in_character")
    call = client.messages.calls[0]
    assert call["system"] == system_prompt("in_character")
    history_sent, newest = call["messages"][:-1], call["messages"][-1]
    assert [m["content"] for m in history_sent] == ["AR of a Longsword?", "Longsword +0: 120 AR"]
    assert newest["content"] == [
        {"type": "text", "text": "and two-handed?"},
        {"type": "text", "text": VOICE_REMINDERS["in_character"]},
    ]


def test_tool_call_round_trip() -> None:
    client = FakeClient(
        [
            make_message(
                [
                    text("Let me calculate."),
                    tool_use("toolu_1", "calculate_attack_rating", AR_ARGS),
                ],
                "tool_use",
            ),
            make_message([text("Your Longsword shows 110 + 10.")], "end_turn"),
        ]
    )
    result = run_chat(client, MODEL, "AR of a Longsword at 10 everything?")

    assert result.reply == "Your Longsword shows 110 + 10."
    assert [c.name for c in result.tool_calls] == ["calculate_attack_rating"]
    assert not result.tool_calls[0].is_error

    second = client.messages.calls[1]["messages"]
    assert second[1]["role"] == "assistant"  # full assistant content kept
    tool_result = second[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "toolu_1"
    assert "is_error" not in tool_result
    assert json.loads(tool_result["content"])["physical"]["base"] == 110


def test_parallel_tool_calls_share_one_user_message() -> None:
    client = FakeClient(
        [
            make_message(
                [
                    tool_use("toolu_a", "calculate_attack_rating", AR_ARGS),
                    tool_use("toolu_b", "calculate_attack_rating", {**AR_ARGS, "strength": 20}),
                ],
                "tool_use",
            ),
            make_message([text("Done.")], "end_turn"),
        ]
    )
    run_chat(client, MODEL, "compare")
    last = client.messages.calls[1]["messages"][-1]
    assert last["role"] == "user"
    assert [r["tool_use_id"] for r in last["content"]] == ["toolu_a", "toolu_b"]


def test_tool_error_is_sent_back_as_is_error() -> None:
    client = FakeClient(
        [
            make_message(
                [tool_use("toolu_1", "calculate_attack_rating", {**AR_ARGS, "weapon": "Nope"})],
                "tool_use",
            ),
            make_message([text("I couldn't find that weapon.")], "end_turn"),
        ]
    )
    result = run_chat(client, MODEL, "AR of Nope?")
    assert result.tool_calls[0].is_error
    sent = client.messages.calls[1]["messages"][-1]["content"][0]
    assert sent["is_error"] is True


def test_tool_round_limit() -> None:
    loop = [
        make_message([tool_use(f"toolu_{i}", "calculate_attack_rating", AR_ARGS)], "tool_use")
        for i in range(MAX_TOOL_ROUNDS)
    ]
    result = run_chat(FakeClient(loop), MODEL, "loop forever")
    assert result.stop_reason == "tool_round_limit"
    assert len(result.tool_calls) == MAX_TOOL_ROUNDS


def test_refusal_does_not_run_tools() -> None:
    client = FakeClient(
        [make_message([tool_use("toolu_1", "calculate_attack_rating", AR_ARGS)], "refusal")]
    )
    result = run_chat(client, MODEL, "something")
    assert result.stop_reason == "refusal"
    assert result.tool_calls == []


def test_max_tokens_does_not_run_tools() -> None:
    client = FakeClient(
        [
            make_message(
                [text("Partial"), tool_use("toolu_1", "calculate_attack_rating", AR_ARGS)],
                "max_tokens",
            )
        ]
    )
    result = run_chat(client, MODEL, "long question")
    assert result.stop_reason == "max_tokens"
    assert result.tool_calls == []
    assert result.reply.startswith("Partial")


# -------------------------------------------------------------- endpoint


@pytest.fixture
def api() -> TestClient:
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_chat_endpoint_with_fake_client(api: TestClient) -> None:
    fake = FakeClient([make_message([text("Hello, Tarnished.")], "end_turn")])
    app.dependency_overrides[get_claude_client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings(
        anthropic_api_key="test", claude_model=MODEL
    )
    resp = api.post(
        "/chat",
        json={"message": "hi", "history": [{"role": "user", "content": "earlier"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Hello, Tarnished."
    sent = fake.messages.calls[0]["messages"]
    assert sent[0]["content"] == "earlier"
    assert sent[1]["content"][0] == {"type": "text", "text": "hi"}


def test_chat_endpoint_passes_voice(api: TestClient) -> None:
    fake = FakeClient([make_message([text("Hail, Tarnished.")], "end_turn")])
    app.dependency_overrides[get_claude_client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings(
        anthropic_api_key="test", claude_model=MODEL
    )
    resp = api.post("/chat", json={"message": "hi", "voice": "in_character"})
    assert resp.status_code == 200
    assert fake.messages.calls[0]["system"] == system_prompt("in_character")


def test_chat_endpoint_rejects_unknown_voice(api: TestClient) -> None:
    app.dependency_overrides[get_claude_client] = lambda: FakeClient([])
    resp = api.post("/chat", json={"message": "hi", "voice": "pirate"})
    assert resp.status_code == 422


def test_chat_endpoint_without_api_key(api: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(anthropic_api_key="")
    resp = api.post("/chat", json={"message": "hi"})
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_chat_endpoint_rejects_empty_message(api: TestClient) -> None:
    app.dependency_overrides[get_claude_client] = lambda: FakeClient([])
    resp = api.post("/chat", json={"message": ""})
    assert resp.status_code == 422
