"""Stat buffs that change attack rating: physick tears, talismans, helms, consumables.

Every buff here is a SpEffectParam row whose add<Stat>Status columns raise a
stat, e.g. "[Tear] Strength-knot Crystal Tear" adds 10 Strength for 180 s. The
row names come from Smithbox's community names and are matched without their
"[Tear]"-style prefix, so "Strength-knot Crystal Tear" finds it.

Buffs that multiply damage instead (Flame-Shrouding Cracked Tear, Golden Vow)
don't change the stats or the AR shown on the status screen, so they're
reported as "no stat change" rather than applied.

Godrick's Great Rune isn't linked to its effect by any param (the game applies
it from a script), so its effect row is mapped by hand in EXTRA_BUFFS.
"""

from __future__ import annotations

import difflib
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from tools.attack_rating import data_dir

STAT_COLUMNS = {
    "str": "addStrengthStatus",
    "dex": "addDexterityStatus",
    "int": "addMagicStatus",
    "fai": "addFaithStatus",
    "arc": "addLuckStatus",
}
# Prefixes on SpEffectParam names that mark something a player can use or wear.
KINDS = {"Tear": "physick tear", "Talisman": "talisman", "Armor": "armor", "Item": "item"}
# Effects with no params link. 600 is the unnamed +5-to-all row Godrick's Great Rune uses.
EXTRA_BUFFS = {"Godrick's Great Rune": (600, "great rune")}

NAME_RE = re.compile(r"^\[(?P<kind>[^\]]+)\]\s*(?P<name>.+)$")


class Buff(BaseModel):
    name: str
    kind: str
    effect_id: int
    # Stat increases, keyed str/dex/int/fai/arc. Empty for damage-only buffs.
    stats: dict[str, int]
    duration_s: float | None = None


def _norm(text: str) -> str:
    # Drop apostrophes first so "Radagon's" and "radagons" match.
    text = re.sub(r"['’]", "", text.lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


@lru_cache(maxsize=4)
def _buffs(directory: str) -> dict[str, Buff]:
    cols = ["ID", "Name", "effectEndurance", *STAT_COLUMNS.values()]
    effects = pd.read_csv(Path(directory) / "SpEffectParam.csv", usecols=cols, low_memory=False)
    by_id = effects.set_index("ID")

    def make(effect_id: int, name: str, kind: str) -> Buff:
        row = by_id.loc[effect_id]
        stats = {s: int(row[c]) for s, c in STAT_COLUMNS.items() if int(row[c]) != 0}
        duration = float(row["effectEndurance"])
        return Buff(
            name=name,
            kind=kind,
            effect_id=effect_id,
            stats=stats,
            duration_s=duration if duration > 0 else None,
        )

    index: dict[str, Buff] = {}
    for effect_id, raw in zip(effects["ID"], effects["Name"].fillna(""), strict=True):
        m = NAME_RE.match(str(raw))
        if not m or m.group("kind") not in KINDS:
            continue
        buff = make(int(effect_id), m.group("name").strip(), KINDS[m.group("kind")])
        key = _norm(buff.name)
        # Some items have several effect rows; keep the one that changes stats.
        if key not in index or (buff.stats and not index[key].stats):
            index[key] = buff
    for name, (effect_id, kind) in EXTRA_BUFFS.items():
        index[_norm(name)] = make(effect_id, name, kind)
    return index


def clear_cache() -> None:
    _buffs.cache_clear()


def find_buff(name: str, directory: str | Path | None = None) -> Buff:
    """Look up one buff by name. Raises KeyError with close matches if not found."""
    index = _buffs(str(directory or data_dir()))
    key = _norm(name)
    if key in index:
        return index[key]
    close = difflib.get_close_matches(key, list(index), n=3, cutoff=0.6)
    hint = f" Did you mean: {', '.join(index[c].name for c in close)}?" if close else ""
    raise KeyError(f"Unknown buff {name!r}.{hint}")
