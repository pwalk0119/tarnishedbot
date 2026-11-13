"""Stance damage of a weapon's attacks, and how many hits break an enemy's stance.

Formula (regulation 1.17.3 params):

    stance damage of one hit = EquipParamWeapon.saWeaponDamage
                               x AtkParam_Pc.atkSuperArmorCorrection / 100

The weapon's attacks come from BehaviorParam_PC: row (variationId, behaviorJudgeId) points at an
AtkParam_Pc row (refType 0). Weapons whose own variationId has no rows use their class default,
variationId rounded down to the hundred (Short Sword 209 -> Straight Sword 200),
attack by attack.

An enemy's stance bar is NpcParam.superArmorDurability. Area scaling doesn't change it.

Not modeled: counter-hit bonuses (hitting an enemy during its attack), jump-attack or talisman
buffs, stance regeneration over time, and Ashes of War. Checked in game: one Straight Sword
guard counter (5 x 600% = 30) breaks an Imp's stance (30).
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from tools.attack_rating import data_dir, load_tables, resolve_weapon_id

# behaviorJudgeId -> attack name, one-handed. Two-handed versions are +200.
ONE_HANDED_ATTACKS = {
    0: "R1 1",
    10: "R1 2",
    20: "R1 3",
    30: "R1 4",
    40: "R1 5",
    100: "R2",
    105: "Charged R2",
    110: "R2 follow-up",
    115: "Charged R2 follow-up",
    120: "Running R1",
    125: "Running R2",
    130: "Rolling R1",
    140: "Backstep R1",
    150: "Jumping R1",
    160: "Jumping R2",
    180: "Guard counter",
}
TWO_HAND_OFFSET = 200
PLAYER_BEHAVIOR_BASE = 100_000_000
AREA_LABEL_RE = r"\((.*)\)\s*$"  # "Area Scaling - Tier 02 (Limgrave)" -> Limgrave
ATTACK_REF_TYPE = 0


# Area Scaling SpEffects for the guide's regions (Stranded Graveyard through Stormveil).
IN_SCOPE_AREA_EFFECTS = {7000, 7010, 7020, 7030}


class StanceAttack(BaseModel):
    attack: str
    stance_damage: float
    # Set when an enemy is given: stance value -> hits needed (one entry per enemy variant).
    hits_to_break: dict[int, int | None] | None = None


class EnemyStance(BaseModel):
    name: str
    stance: int
    area_scaling: list[str]  # Area Scaling tiers of its placements, not exact locations


class StanceResult(BaseModel):
    weapon: str
    affinity: str
    base_stance_damage: float  # saWeaponDamage
    one_handed: list[StanceAttack]
    two_handed: list[StanceAttack]
    enemy: list[EnemyStance] = []
    notes: list[str]


def _read(name: str, directory: str) -> pd.DataFrame:
    return pd.read_csv(Path(directory) / f"{name}.csv", low_memory=False)


@lru_cache(maxsize=4)
def _behaviors(directory: str) -> dict[tuple[int, int], int]:
    """(variationId, behaviorJudgeId) -> AtkParam_Pc ID, attack rows only."""
    b = _read("BehaviorParam_PC", directory)
    # Player weapon rows are numbered 100000000 + variationId * 1000 + behaviorJudgeId.
    # Other ID ranges reuse the same variation/judge pairs for other purposes.
    own_id = PLAYER_BEHAVIOR_BASE + b["variationId"] * 1000 + b["behaviorJudgeId"]
    b = b[(b["refType"] == ATTACK_REF_TYPE) & (b["ID"] == own_id)]
    return {
        (int(v), int(j)): int(r)
        for v, j, r in zip(b["variationId"], b["behaviorJudgeId"], b["refId"], strict=True)
    }


@lru_cache(maxsize=4)
def _attack_multipliers(directory: str) -> dict[int, float]:
    a = _read("AtkParam_Pc", directory)
    return dict(zip(a["ID"].astype(int), a["atkSuperArmorCorrection"].astype(float), strict=True))


@lru_cache(maxsize=4)
def _npcs(directory: str) -> pd.DataFrame:
    """NpcParam name, stance, and the Area Scaling SpEffect (0 if none) per row."""
    npcs = _read("NpcParam", directory)
    effects = _read("SpEffectParam", directory)[["ID", "Name"]]
    area = effects[effects["Name"].fillna("").str.contains("Area Scaling")]
    area_names = dict(zip(area["ID"].astype(int), area["Name"].astype(str), strict=True))
    effect_cols = [c for c in npcs.columns if c.startswith("spEffectID")]

    def area_of(row: pd.Series) -> int:
        return next((int(row[c]) for c in effect_cols if int(row[c]) in area_names), 0)

    out = npcs[["ID", "Name", "superArmorDurability"]].copy()
    out["area_effect"] = npcs.apply(area_of, axis=1)
    out["area"] = out["area_effect"].map(area_names).fillna("")
    out["area"] = out["area"].str.extract(AREA_LABEL_RE, expand=False).fillna("")
    return out


def clear_cache() -> None:
    for fn in (_behaviors, _attack_multipliers, _npcs):
        fn.cache_clear()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def hits_to_break(stance: float, per_hit: float) -> int | None:
    if per_hit <= 0:
        return None
    return math.ceil(stance / per_hit - 1e-9)


def find_enemy_stance(name: str, directory: str | Path | None = None) -> list[EnemyStance]:
    """Stance values of the enemy whose NpcParam name best matches `name`.

    Rows placed in the guide's regions win over the rest of the game; among those, exact name
    matches win over partial ones ("Tree Sentinel" finds "Tree Sentinel (Limgrave)").
    Returns one entry per distinct stance value (some enemies have variants).
    """
    npcs = _npcs(str(directory or data_dir()))
    needle = _norm(name)
    norm_names = npcs["Name"].fillna("").astype(str).map(_norm)
    placed = (npcs["area_effect"] > 0) & (npcs["superArmorDurability"] > 0)
    rows = npcs[placed & norm_names.str.contains(needle, regex=False)]
    if rows.empty:
        raise ValueError(f"No enemy named {name!r} in NpcParam.")
    in_scope = rows[rows["area_effect"].isin(IN_SCOPE_AREA_EFFECTS)]
    if not in_scope.empty:
        rows = in_scope
    exact = rows[norm_names[rows.index] == needle]
    if not exact.empty:
        rows = exact
    out = []
    for stance, group in rows.groupby("superArmorDurability"):
        out.append(
            EnemyStance(
                name=str(group["Name"].iloc[0]),
                stance=int(stance),
                area_scaling=sorted(set(group["area"])),
            )
        )
    return out


def weapon_stance(
    weapon: str | int,
    affinity: str | None = None,
    enemy: str | None = None,
    directory: str | Path | None = None,
) -> StanceResult:
    """Stance damage per attack for one weapon, and hits to break `enemy`'s stance."""
    d = str(directory or data_dir())
    tables = load_tables(d)
    wid, affinity_name = resolve_weapon_id(weapon, affinity, tables)
    row = tables.weapons.loc[wid]
    base = float(row["saWeaponDamage"])
    variation = int(row["behaviorVariationId"])
    behaviors = _behaviors(d)
    multipliers = _attack_multipliers(d)

    class_default = variation // 100 * 100
    targets = find_enemy_stance(enemy, d) if enemy else []

    def attacks(offset: int) -> list[StanceAttack]:
        out = []
        for judge, label in ONE_HANDED_ATTACKS.items():
            # Per attack, the weapon's own row wins; otherwise its class default is used.
            atk_id = behaviors.get((variation, judge + offset)) or behaviors.get(
                (class_default, judge + offset)
            )
            if atk_id is None or atk_id not in multipliers:
                continue
            dmg = round(base * multipliers[atk_id] / 100, 1)
            out.append(
                StanceAttack(
                    attack=label,
                    stance_damage=dmg,
                    hits_to_break=(
                        {e.stance: hits_to_break(e.stance, dmg) for e in targets}
                        if targets
                        else None
                    ),
                )
            )
        return out

    notes = [
        "Stance damage doesn't depend on upgrade level or stats.",
        "Not included: extra stance damage from counter hits (hitting an enemy mid-attack), "
        "buffs, Ashes of War, and the enemy's stance recovering between hits.",
    ]
    one, two = attacks(0), attacks(TWO_HAND_OFFSET)
    if not one and not two:
        notes.append("No attack data found for this weapon's moveset.")
    return StanceResult(
        weapon=str(row["Name"]),
        affinity=affinity_name,
        base_stance_damage=base,
        one_handed=one,
        two_handed=two,
        enemy=targets,
        notes=notes,
    )
