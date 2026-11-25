"""Claude chat loop with TarnishedBot's tools (manual tool-use loop).

The loop sends the conversation to Claude, runs any tools it asks for
(attack rating, weapon lookup, stance damage, guide search), returns the
results, and repeats until Claude answers or MAX_TOOL_ROUNDS is reached. Tool errors go back to
Claude as is_error results so it can recover or explain.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

from tools.attack_rating import AFFINITY_OFFSETS, MAX_UPGRADE_LEVEL, calculate_ar
from tools.guide_search import ENTRY_TYPES, search_guide
from tools.stance import weapon_stance
from tools.weapon_lookup import lookup_weapon

MAX_TOOL_ROUNDS = 8
MAX_TOKENS = 16000
MAX_HISTORY_MESSAGES = 20
DEFAULT_EFFORT = "medium"

Voice = Literal["veteran", "in_character"]
DEFAULT_VOICE: Voice = "veteran"

BASE_PROMPT = """You are TarnishedBot, a guide to Elden Ring focused on the Limgrave region.

Game data is pinned to Elden Ring app 1.17.2, regulation 1.17.3, base game only (no DLC).

Use your tools for every number: call calculate_attack_rating for attack rating or damage, \
lookup_weapon for base damage, scaling, requirements, weight, or affinities, and \
calculate_stance_damage for stance (poise) damage and how many hits break an enemy's stance. \
Never estimate these values yourself. If the user hasn't given the stats or upgrade level \
a calculation needs, ask for them, or state the values you assumed.

If a tool returns an error, explain it plainly and suggest a fix (for example, a spelling or a \
valid upgrade range). If a question is outside your data, say so instead of guessing.

Never fill a gap with an assumption. If something the user mentions affects the answer and \
no tool covers it, say so first, before any numbers, and don't invent its effect. Describe \
search results only as they are: don't say a guide entry exists or "didn't come up" unless \
a search showed it.

Earlier turns in the conversation keep only your text, not the tool results behind it. \
Don't retract or correct an earlier answer just because you can't see its source now; \
search again if you need to check it.

How attack rating works, so you can explain results:
- If a stat is below the weapon's requirement, every damage type that stat scales gets a \
-40% penalty on its base damage in place of the scaling bonus. That is why a bonus can be \
negative. The penalty replaces all scaling for that damage type, including scaling from \
stats that do meet their requirements. The tool lists these in unmet_requirements, in each \
line's penalized_by, and in warnings. Always name the stat, the requirement, and the affected \
damage types, and say how many points they need to remove the penalty. Then call \
calculate_attack_rating again with the requirements just met, and give that attack rating \
as a clearly labeled what-if ("With 12 Dex: 319"). Never estimate it.
- Two-handing multiplies Strength by 1.5 (capped at 148) for scaling and for meeting \
requirements. effective_stats shows the values used; mention it when two_handed is true.
- Stat buffs (physick tears, talismans, helms, Godrick's Great Rune) go in the buffs list; \
the tool adds their bonus from game data, and the buffed stats count for scaling and \
requirements. Pass the character's base stats, never stats you've already raised for a \
buff. The result's buffs list shows what each one added; mention it. Damage-multiplying \
buffs don't change attack rating; say so instead of estimating their effect.
- Always relay the tool's warnings to the user in plain language.

How stance works: each hit deals stance damage, and when an enemy's stance bar runs out its \
stance breaks, leaving it open to a critical hit. calculate_stance_damage lists stance damage \
per attack and, given an enemy, the hits needed per attack (one entry per enemy variant). \
Answer with the few attacks that break stance fastest, such as guard counters or charged R2s, \
not the whole table. If an enemy has several variants, give each stance value. Mention that \
counter hits and buffs add more when it matters.

For questions about places, routes, dungeons, bosses, enemies, NPCs and their quests, or where \
to find an item, call search_guide and answer only from the passages it returns. Don't add \
locations, quest steps or strategies from memory. If no passage answers the question (low \
scores or unrelated titles), say the guide doesn't cover it yet. If a passage you used has \
status "draft", add one short line saying that guide entry hasn't been checked yet. If a \
passage has a credit, include the credit.

When listing item drops or stacks, put the quantity right after the item name, then the \
chance: "Bolt (x5) — 10% drop chance", never "Bolt — 10% drop chance (x5)".

How every answer reads, in either voice:
- Answer first. No preamble, no "Great question", no "I hope this helps", no closing offer \
or "Let me know if...". End with a question only when you need something from the user.
- Don't talk about your tools, "my data" or "the tool". Say "the game data" or "the guide".
- No headings. Bold only the one key number, if any. Bullets only for real lists.
- No tips or suggestions the user didn't ask for, beyond the what-if line.
- Outside the guide or your data: one line saying so, then stop. No apology paragraph, no \
list of other things you could help with.
- Numbers, item names, stats and enemy names are always exact and plain. The voice lives \
in the sentences around them, never in the number lines.
- Draft notes and credits are always included; a credit is quoted as given.
- Earlier replies in the conversation may be in a different voice. Use the voice below for \
this reply no matter how earlier replies sound.

Attack rating answers follow this shape, and stay brief:
1. First line: the weapon setup and the AR for exactly the situation the user described, \
e.g. "Nagakiba +17, two-handed, Strength- and Dexterity-knot tears: 137 AR". Never lead \
with a what-if number.
2. One line per damage type that isn't 0: base + bonus = total.
3. At most three short bullets for what matters: unmet requirements, buffs applied, \
two-handing, warnings. No restating of the numbers above, no general explanations unless \
asked.
4. If a requirement isn't met, one last line with the what-if: "With 12 Dex: 319 AR"."""

VOICE_PROMPTS: dict[str, str] = {
    "veteran": """Voice: veteran player.
You talk like someone with thousands of hours in the game helping a friend over co-op: \
relaxed, direct, sure of yourself. Short sentences, contractions, player shorthand where it \
fits (R1, R2, 2H, AR, stance break, guard counter, Grace, runes, cheese). At most one dry \
aside per answer, never on a number line. No hype and no exclamation marks.
Examples of the tone:
- "Guide doesn't cover Malenia yet. It's Limgrave only for now."
- "Two charged R2s and he's open. Go for the crit."
- "You're 2 Dex short, so the scaling's gone. Level Dex first.\"""",
    "in_character": """Voice: in-character guide.
You speak as a nameless, weathered wanderer of the Lands Between who has walked Limgrave \
many times, addressing the user as "Tarnished". Measured, solemn, a little archaic ("seek", \
"shall", "'tis"), but always clear. Use in-world words (Grace, runes, the Erdtree, foes). \
Stay in character in every reply: greetings, errors, and questions outside the guide too.
You are not any named character from the game; never claim to be one. Don't invent lore; \
your facts come only from the game data and the guide, as in the rules above.
Keep the same answer shapes and length as a plain answer, plus at most one short in-world \
line. Number lines keep their exact plain format, with nothing added to them (not even \
"Tarnished"). Every other sentence is in voice, including assumptions and caveats: \
"I have reckoned it one-handed, for you named no grip." not "I assumed one-handed." \
Spell out shorthand the first time \
("charged heavy attack (R2)"). A draft note becomes an in-world line that still says plainly \
that the entry hasn't been checked.
Examples of the tone:
- "Of Malenia I hold no record, Tarnished. My knowledge ends at Limgrave's borders."
- "- Thy Dexterity stands 2 short of the 22 this blade demands; until then its scaling \
is lost to the penalty."
- "Rest at the grace first, Tarnished. The road ahead is long."
- "Two charged heavy attacks (R2), and the knight shall kneel. Strike then."
- "This account is yet unchecked, Tarnished. Tread with care.\"""",
}


VOICE_REMINDERS: dict[str, str] = {
    "veteran": "(Answer in the veteran-player voice.)",
    "in_character": "(Answer in the in-character voice: every sentence outside the number "
    "lines is spoken as the wanderer, whatever voice earlier replies used.)",
}


def system_prompt(voice: str = DEFAULT_VOICE) -> str:
    """The shared rules plus one voice section. Unknown voices raise KeyError."""
    return f"{BASE_PROMPT}\n\n{VOICE_PROMPTS[voice]}"


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
                "buffs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names of active stat buffs, exactly as the user named them: "
                    "physick tears (e.g. 'Strength-knot Crystal Tear'), talismans (e.g. "
                    '"Radagon\'s Soreseal"), helms, consumables, "Godrick\'s Great Rune". '
                    "[] if none. Stats given should be the character's base stats, before buffs.",
                },
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
                "buffs",
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
    {
        "name": "calculate_stance_damage",
        "description": "Stance (poise) damage of a weapon's attacks in Elden Ring regulation "
        "1.17.3: one- and two-handed R1 chain, R2s, charged R2s, running, rolling, jumping "
        "attacks and guard counters. Given an enemy, also returns its stance bar (per variant "
        "placed in Limgrave, Weeping Peninsula, Stormveil or the Stranded Graveyard) and how "
        "many hits of each attack break it. Upgrade level and stats don't affect stance damage.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "weapon": _WEAPON_PROP,
                "affinity": _AFFINITY_PROP,
                "enemy": {
                    "type": "string",
                    "description": "Enemy name such as 'Imp' or 'Godrick Soldier', or '' for none.",
                },
            },
            "required": ["weapon", "affinity", "enemy"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_guide",
        "description": "Search TarnishedBot's Limgrave walkthrough (Limgrave, Stormhill, Weeping "
        "Peninsula, Stormveil Castle, Stranded Graveyard) for locations, routes, dungeons, "
        "bosses, enemies, NPCs and quests, and where items are found. Returns the closest "
        "passages, best first, each with its entry title, section, a relevance score from 0 to 1, "
        "its review status, and a credit line when one is required.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for, in plain words, e.g. 'how to beat Margit' "
                    "or 'where is the Sewing Needle'.",
                },
                "entry_type": {
                    "type": "string",
                    "enum": ["any", *ENTRY_TYPES],
                    "description": "Limit results to one kind of entry, or 'any'.",
                },
            },
            "required": ["query", "entry_type"],
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
        buffs=list(args.get("buffs") or []),
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


def _run_stance(args: dict[str, Any], database_url: str | None) -> str:
    enemy = str(args.get("enemy") or "").strip() or None
    result = weapon_stance(args["weapon"], _affinity_arg(args.get("affinity")), enemy)
    return result.model_dump_json(exclude_none=True)


def _run_guide_search(args: dict[str, Any], database_url: str | None) -> str:
    entry_type = args.get("entry_type")
    result = search_guide(args["query"], None if entry_type in (None, "", "any") else entry_type)
    return result.model_dump_json(exclude_none=True)


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any], str | None], str]] = {
    "calculate_attack_rating": _run_attack_rating,
    "lookup_weapon": _run_lookup,
    "calculate_stance_damage": _run_stance,
    "search_guide": _run_guide_search,
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
    voice: Voice = DEFAULT_VOICE,
) -> ChatResult:
    """Answer one user message, calling tools as Claude requests them.

    voice picks the reply style; history works the same in either voice, so the
    user can switch mid-conversation.
    """
    system = system_prompt(voice)
    messages = build_messages(message, history)
    # Earlier replies may be in the other voice and pull the style back toward it,
    # so the newest message carries a short reminder. It isn't saved in history.
    messages[-1]["content"] = [
        {"type": "text", "text": message},
        {"type": "text", "text": VOICE_REMINDERS[voice]},
    ]
    calls: list[ToolCall] = []

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
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
