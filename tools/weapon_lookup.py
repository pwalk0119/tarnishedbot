"""Weapon lookup tool: one weapon's numbers from the SQLite param tables.

Needs the database built by `uv run python -m pipeline.load_params`.
Names work the same way as the AR calculator ("Heavy Longsword", or
"Longsword" + affinity "Heavy", or a numeric ID).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel
from sqlalchemy import Engine, create_engine, text

from tools.attack_rating import (
    AFFINITY_OFFSETS,
    ATK_RATE_COL,
    ATTACK_BASE_COL,
    DAMAGE_TYPES,
    MAX_UPGRADE_LEVEL,
    REQUIREMENT_COL,
    SCALING_COL,
    SCALING_RATE_COL,
    STATS,
    build_name_index,
    display_floor,
    reinforce_family,
    resolve_in_index,
)

WEAPON_TABLE = "equip_param_weapon"
REINFORCE_TABLE = "reinforce_param_weapon"

# In-game letter grades for a scaling value (community-documented thresholds).
GRADE_THRESHOLDS = ((175, "S"), (140, "A"), (90, "B"), (60, "C"), (25, "D"), (1, "E"))


class WeaponInfo(BaseModel):
    weapon_id: int
    name: str
    affinity: str
    upgrade_level: int
    max_upgrade_level: int
    weight: float
    # At this upgrade level, floored like the in-game display. Zero types left out.
    base_damage: dict[str, int]
    # Scaling value per stat at this level (correct<Stat> * rate), and its letter.
    scaling: dict[str, float]
    scaling_grades: dict[str, str]
    requirements: dict[str, int]
    available_affinities: list[str]


def scaling_grade(value: float) -> str:
    """Letter grade for a scaling value; '-' for no scaling."""
    for threshold, letter in GRADE_THRESHOLDS:
        if value >= threshold:
            return letter
    return "-"


@lru_cache(maxsize=4)
def _engine(database_url: str) -> Engine:
    return create_engine(database_url)


@lru_cache(maxsize=4)
def _name_index(database_url: str) -> tuple[dict[str, list[int]], frozenset[int]]:
    with _engine(database_url).connect() as conn:
        rows = conn.execute(text(f'SELECT "ID", "Name" FROM {WEAPON_TABLE}')).all()
    return build_name_index(rows), frozenset(int(r[0]) for r in rows)


def clear_cache() -> None:
    """Forget cached engines and name indexes (call after reloading the database)."""
    for fn in (_name_index, _engine):
        fn.cache_clear()


def _default_url() -> str:
    from backend.config import get_settings

    return get_settings().database_url


def lookup_weapon(
    weapon: str | int,
    affinity: str | None = None,
    upgrade_level: int = 0,
    *,
    database_url: str | None = None,
) -> WeaponInfo:
    """Return base damage, scaling, requirements, and affinities for one weapon."""
    url = database_url or _default_url()
    names, ids = _name_index(url)
    wid, affinity_name = resolve_in_index(weapon, affinity, names, ids.__contains__)

    with _engine(url).connect() as conn:
        w = (
            conn.execute(text(f'SELECT * FROM {WEAPON_TABLE} WHERE "ID" = :id'), {"id": wid})
            .mappings()
            .one()
        )
        reinforce_type = int(w["reinforceTypeId"])
        family_rows = (
            conn.execute(
                text(
                    f'SELECT * FROM {REINFORCE_TABLE} WHERE "ID" BETWEEN :lo AND :hi ORDER BY "ID"'
                ),
                {"lo": reinforce_type, "hi": reinforce_type + MAX_UPGRADE_LEVEL},
            )
            .mappings()
            .all()
        )

    if not family_rows or int(family_rows[0]["ID"]) != reinforce_type:
        raise ValueError(f"No upgrade rows for reinforceTypeId {reinforce_type}")
    family = reinforce_family(family_rows[0]["Name"])
    levels = {
        int(r["ID"]) - reinforce_type: r
        for r in family_rows
        if reinforce_family(r["Name"]) == family
    }
    max_level = max(levels)
    if upgrade_level not in levels:
        raise ValueError(f"upgrade_level must be 0..{max_level} for this weapon")
    rates = levels[upgrade_level]

    base_damage = {}
    for dmg in DAMAGE_TYPES:
        value = display_floor(float(w[ATTACK_BASE_COL[dmg]]) * float(rates[ATK_RATE_COL[dmg]]))
        if value:
            base_damage[dmg] = value
    scaling = {
        s: round(float(w[SCALING_COL[s]]) * float(rates[SCALING_RATE_COL[s]]), 2) for s in STATS
    }
    base_id = wid - (wid % 10000) if affinity_name != "Standard" else wid
    available = [name for name, off in AFFINITY_OFFSETS.items() if (base_id + off) in ids]

    return WeaponInfo(
        weapon_id=wid,
        name=str(w["Name"]),
        affinity=affinity_name,
        upgrade_level=upgrade_level,
        max_upgrade_level=max_level,
        weight=float(w["weight"]),
        base_damage=base_damage,
        scaling=scaling,
        scaling_grades={s: scaling_grade(v) for s, v in scaling.items()},
        requirements={s: int(w[REQUIREMENT_COL[s]]) for s in STATS},
        available_affinities=available,
    )
