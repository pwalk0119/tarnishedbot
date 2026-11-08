"""Claude chat loop with TarnishedBot's tools (manual tool-use loop).

The loop sends the conversation to Claude, runs any tools it asks for
(attack rating, weapon lookup), returns the results, and repeats until
Claude answers or MAX_TOOL_ROUNDS is reached. Tool errors go back to
Claude as is_error results so it can recover or explain.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

from tools.attack_rating import AFFINITY_OFFSETS, MAX_UPGRADE_LEVEL, calculate_ar
from tools.weapon_lookup import lookup_weapon

MAX_TOOL_ROUNDS = 8
MAX_TOKENS = 16000
MAX_HISTORY_MESSAGES = 20
DEFAULT_EFFORT = "medium"

SYSTEM_PROMPT = """You are TarnishedBot, an assistant for Elden Ring focused on the Limgrave region.

Game data is pinned to Elden Ring app 1.17.2, regulation 1.17.3, base game only (no DLC).

Use your tools for every number: call calculate_attack_rating for attack rating or damage, \
and lookup_weapon for base damage, scaling, requirements, weight, or affinities. Never estimate \
these values yourself. If the user hasn't given the stats or upgrade level a calculation needs, \
ask for them, or state the values you assumed.

If a tool returns an error, explain it plainly and suggest a fix (for example, a spelling or a \
valid upgrade range). If a question is outside your data, say so instead of guessing.

How attack rating works, so you can explain results:
- If a stat is below the weapon's requirement, every damage type that stat scales gets a \
-40% penalty on its base damage in place of the scaling bonus. That is why a bonus can be \
negative. The penalty replaces all scaling for that damage type, including scaling from \
stats that do meet their requirements. The tool lists these in unmet_requirements, in each \
line's penalized_by, and in warnings. Always name the stat, the requirement, and the affected \
damage types, and say how many points they need to remove the penalty. Then call \
calculate_attack_rating again with the requirements just met and report that attack rating, \
so the user sees the real gain. Never estimate it.
- Two-handing multiplies Strength by 1.5 (capped at 148) for scaling and for meeting \
requirements. effective_stats shows the values used; mention it when two_handed is true.
- Always relay the tool's warnings to the user in plain language.

Keep answers short and practical. Show attack rating as base + bonus per damage type, \
plus the total."""

AFFINITIES = list(AFFINITY_OFFSETS)

_WEAPON_PROP = {
    "type": "string",
    "description": "Weapon name such as 'Longsword' or 'Heavy Longsword', or a numeric param ID.",
}
_AFFINITY_PROP = {
    "type": "string",
    "enum": AFFINITIES,
    "description": "Affinity. Use 'Standard' if the user gave none or the weapon name already "
    "includes it (e.g. 'Heavy Longsword').",
}
_LEVEL_PROP = {
    "type": "integer",
    "description": f"Upgrade level: 0-{MAX_UPGRADE_LEVEL} for regular weapons, 0-10 for somber.",
}


def _stat_prop(name: str) -> dict[str, Any]:
    return {"type": "integer", "description": f"{name} stat, 1-99."}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "calculate_attack_rating",
        "description": "Calculate a weapon's displayed attack rating for a character's stats in "
        "Elden Ring 1.17.2 / regulation 1.17.3. Returns base and bonus for physical, magic, fire, "
        "lightning, and holy damage, plus the total, floored like the in-game status screen. "
        "Applies the -40% penalty when a requirement isn't met, and the two-handed Strength bonus.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "weapon": _WEAPON_PROP,
                "affinity": _AFFINITY_PROP,
                "upgrade_level": _LEVEL_PROP,
                "strength": _stat_prop("Strength"),
                "dexterity": _stat_prop("Dexterity"),
                "intelligence": _stat_prop("Intelligence"),
                "faith": _stat_prop("Faith"),
                "arcane": _stat_prop("Arcane"),
                "two_handed": {"type": "boolean", "description": "True if two-handing."},
            },
            "required": [
                "weapon",
                "affinity",
                "upgrade_level",
                "strength",
                "dexterity",
                "intelligence",
                "faith",
                "arcane",
                "two_handed",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "lookup_weapon",
        "description": "Look up one weapon in Elden Ring 1.17.2 / regulation 1.17.3: base damage "
        "at an upgrade level, scaling values with letter grades, stat requirements, weight, max "
        "upgrade level, and which affinities it can take.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "weapon": _WEAPON_PROP,
                "affinity": _AFFINITY_PROP,
                "upgrade_level": _LEVEL_PROP,
            },
            "required": ["weapon", "affinity", "upgrade_level"],
            "additionalProperties": False,
        },
    },
]


# ------------------------------------------------------------------- models


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ToolCall(BaseModel):
    name: str
    input: dict[str, Any]
    is_error: bool
    result: str


class ChatResult(BaseModel):
    reply: str
    tool_calls: list[ToolCall] = []
    stop_reason: str | None = None


# -------------------------------------------------------------------- tools


def _affinity_arg(value: Any) -> str | None:
    # "Standard" means "let the weapon name decide", so "Heavy Longsword" still works.
    return None if value in (None, "", "Standard") else str(value)


def _run_attack_rating(args: dict[str, Any], database_url: str | None) -> str:
    result = calculate_ar(
        args["weapon"],
        int(args["upgrade_level"]),
        _affinity_arg(args.get("affinity")),
        {
            "str": args["strength"],
            "dex": args["dexterity"],
            "int": args["intelligence"],
            "fai": args["faith"],
            "arc": args["arcane"],
        },
        two_handed=bool(args.get("two_handed", False)),
    )
    return result.model_dump_json()


def _run_lookup(args: dict[str, Any], database_url: str | None) -> str:
    info = lookup_weapon(
        args["weapon"],
        _affinity_arg(args.get("affinity")),
        int(args.get("upgrade_level", 0)),
        database_url=database_url,
    )
    return info.model_dump_json()


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any], str | None], str]] = {
    "calculate_attack_rating": _run_attack_rating,
    "lookup_weapon": _run_lookup,
}


def run_tool(name: str, args: dict[str, Any], database_url: str | None = None) -> tuple[str, bool]:
    """Run one tool. Returns (result text, is_error)."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool {name!r}.", True
    try:
        return handler(args, database_url), False
    except KeyError as exc:
        # KeyError messages come wrapped in quotes; unwrap for readability.
        return f"Error: {exc.args[0] if exc.args else exc}", True
    except (ValueError, ValidationError, TypeError) as exc:
        return f"Error: {exc}", True


# --------------------------------------------------------------------- loop


def _text_of(content: Sequence[Any]) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


def build_messages(message: str, history: Sequence[HistoryMessage]) -> list[dict[str, Any]]:
    """Recent text history + the new user message. The first message must be from the user."""
    recent = list(history)[-MAX_HISTORY_MESSAGES:]
    while recent and recent[0].role != "user":
        recent.pop(0)
    msgs: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in recent]
    msgs.append({"role": "user", "content": message})
    return msgs


def run_chat(
    client: anthropic.Anthropic,
    model: str,
    message: str,
    history: Sequence[HistoryMessage] = (),
    *,
    database_url: str | None = None,
    effort: str = DEFAULT_EFFORT,
) -> ChatResult:
    """Answer one user message, calling tools as Claude requests them."""
    messages = build_messages(message, history)
    calls: list[ToolCall] = []

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            output_config={"effort": effort},
            messages=messages,
        )
        stop = response.stop_reason

        if stop == "refusal":
            return ChatResult(
                reply="Sorry, I can't help with that request.", tool_calls=calls, stop_reason=stop
            )
        if stop == "max_tokens":
            text = _text_of(response.content)
            note = "(My answer was cut off. Try asking a narrower question.)"
            return ChatResult(reply=f"{text}\n\n{note}".strip(), tool_calls=calls, stop_reason=stop)
        if stop == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        if stop != "tool_use":
            return ChatResult(reply=_text_of(response.content), tool_calls=calls, stop_reason=stop)

        # Keep the full assistant content (thinking + tool_use blocks) unchanged.
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            args = block.input if isinstance(block.input, dict) else json.loads(block.input)
            text, is_error = run_tool(block.name, args, database_url)
            calls.append(ToolCall(name=block.name, input=args, is_error=is_error, result=text))
            result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": text,
            }
            if is_error:
                result["is_error"] = True
            results.append(result)
        # All results for this turn go back in one user message.
        messages.append({"role": "user", "content": results})

    return ChatResult(
        reply="I needed too many lookups to answer that. Try a more specific question.",
        tool_calls=calls,
        stop_reason="tool_round_limit",
    )
