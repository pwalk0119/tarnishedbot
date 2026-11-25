"""Weapon progression: one weapon's numbers at every upgrade level, in one query.

The weapon_levels table holds a row per base-game weapon, affinity and upgrade
level: base damage per type (floored like the status screen) and scaling per
stat with its letter grade. pipeline.load_params builds it from the param CSVs
with build_weapon_levels, so answering "how does it change per level" is a
single SELECT instead of one lookup per level.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tools.attack_rating import (
    ATK_RATE_COL,
    ATTACK_BASE_COL,
    DAMAGE_TYPES,
    MAX_UPGRADE_LEVEL,
    SCALING_COL,
    SCALING_RATE_COL,
    STATS,
    ParamTables,
    display_floor,
    infer_affinity,
    reinforce_family,
    resolve_in_index,
)
from tools.weapon_lookup import _default_url, _engine, _name_index, scaling_grade
from tools.weapon_search import _weapon_ids

LEVELS_TABLE = "weapon_levels"


class LevelRow(BaseModel):
    level: int
    base_damage: dict[str, int]  # zero types left out
    scaling: dict[str, float]  # stats with no scaling left out
    scaling_grades: dict[str, str]


class WeaponProgression(BaseModel):
    weapon_id: int
    name: str
    affinity: str
    max_upgrade_level: int
    levels: list[LevelRow]


def build_weapon_levels(tables: ParamTables) -> pd.DataFrame:
    """One row per base-game player weapon x affinity x upgrade level."""
    weapons, reinforce = tables.weapons, tables.reinforce
    exists = weapons.index.__contains__
    rows = []
    for wid in _weapon_ids(tables, None):
        w = weapons.loc[wid]
        start = int(w["reinforceTypeId"])
        if start not in reinforce.index:
            continue
        family = reinforce_family(reinforce.loc[start]["Name"])
        levels = []
        for level in range(MAX_UPGRADE_LEVEL + 1):
            rid = start + level
            if rid not in reinforce.index or reinforce_family(reinforce.loc[rid]["Name"]) != family:
                break
            levels.append(reinforce.loc[rid])
        affinity = infer_affinity(wid, exists)
        for level, rates in enumerate(levels):
            row = {
                "weapon_id": wid,
                "name": str(w["Name"]),
                "affinity": affinity,
                "level": level,
                "max_level": len(levels) - 1,
            }
            for dmg in DAMAGE_TYPES:
                base = float(w[ATTACK_BASE_COL[dmg]]) * float(rates[ATK_RATE_COL[dmg]])
                row[dmg] = display_floor(base)
            for s in STATS:
                value = round(float(w[SCALING_COL[s]]) * float(rates[SCALING_RATE_COL[s]]), 2)
                row[s] = value
                row[f"grade_{s}"] = scaling_grade(value)
            rows.append(row)
    return pd.DataFrame(rows)


def weapon_progression(
    weapon: str | int, affinity: str | None = None, *, database_url: str | None = None
) -> WeaponProgression:
    """Base damage and scaling of one weapon at every upgrade level."""
    url = database_url or _default_url()
    names, ids = _name_index(url)
    wid, affinity_name = resolve_in_index(weapon, affinity, names, ids.__contains__)
    try:
        with _engine(url).connect() as conn:
            rows = (
                conn.execute(
                    text(f"SELECT * FROM {LEVELS_TABLE} WHERE weapon_id = :id ORDER BY level"),
                    {"id": wid},
                )
                .mappings()
                .all()
            )
    except OperationalError as exc:
        raise ValueError(
            f"The {LEVELS_TABLE} table is missing. Run: uv run python -m pipeline.load_params"
        ) from exc
    if not rows:
        raise ValueError(f"No upgrade data for weapon {wid}")

    levels = [
        LevelRow(
            level=int(r["level"]),
            base_damage={d: int(r[d]) for d in DAMAGE_TYPES if int(r[d])},
            scaling={s: float(r[s]) for s in STATS if float(r[s]) > 0},
            scaling_grades={s: str(r[f"grade_{s}"]) for s in STATS if float(r[s]) > 0},
        )
        for r in rows
    ]
    return WeaponProgression(
        weapon_id=wid,
        name=str(rows[0]["name"]),
        affinity=affinity_name,
        max_upgrade_level=int(rows[0]["max_level"]),
        levels=levels,
    )
