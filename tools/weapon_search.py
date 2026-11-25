"""Scaling sweep: every base-game weapon, affinity and upgrade level in one pass.

Answers questions like "which weapons reach S in Dexterity at any level?"
without one lookup per weapon. For each weapon row (affinity variants are
separate rows) and each stat it computes the scaling value at every upgrade
level, correct<Stat> * correct<Stat>Rate, the same numbers lookup_weapon
shows, and keeps weapons whose best level reaches the requested grade.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from tools.attack_rating import (
    AFFINITY_OFFSETS,
    MAX_UPGRADE_LEVEL,
    SCALING_COL,
    SCALING_RATE_COL,
    STATS,
    ParamTables,
    infer_affinity,
    is_dlc_weapon,
    load_tables,
    normalize_affinity,
    reinforce_family,
    resolve_weapon_id,
)
from tools.weapon_lookup import GRADE_THRESHOLDS, scaling_grade

GRADES = tuple(letter for _, letter in reversed(GRADE_THRESHOLDS))  # E, D, C, B, A, S
GRADE_MIN = {letter: value for value, letter in GRADE_THRESHOLDS}
DEFAULT_LIMIT = 30


class ScalingMatch(BaseModel):
    weapon: str
    affinity: str
    stat: str
    max_upgrade_level: int
    best_scaling: float
    best_grade: str
    # First upgrade level at which each grade is reached, e.g. {"C": 0, "B": 8, "A": 15}.
    grade_from_level: dict[str, int]


class ScalingSearchResult(BaseModel):
    stat: str
    min_grade: str
    affinity: str
    weapon: str | None
    total_matches: int
    matches: list[ScalingMatch]
    note: str | None = None


@lru_cache(maxsize=4)
def _family_rates(directory: str) -> dict[int, list[dict[str, float]]]:
    """reinforceTypeId -> per-level scaling rates, cut where the next family starts."""
    reinforce = load_tables(directory).reinforce
    families: dict[int, list[dict[str, float]]] = {}
    for start in {int(i) for i in load_tables(directory).weapons["reinforceTypeId"]}:
        if start not in reinforce.index:
            continue
        family = reinforce_family(reinforce.loc[start]["Name"])
        levels = []
        for level in range(MAX_UPGRADE_LEVEL + 1):
            rid = start + level
            if rid not in reinforce.index or reinforce_family(reinforce.loc[rid]["Name"]) != family:
                break
            row = reinforce.loc[rid]
            levels.append({s: float(row[SCALING_RATE_COL[s]]) for s in STATS})
        families[start] = levels
    return families


def is_player_weapon(tables: ParamTables, wid: int) -> bool:
    """Real, upgradeable base-game weapons. Pots, spritestones and ammo also live in
    EquipParamWeapon, but have no originEquipWep."""
    row = tables.weapons.loc[wid]
    return int(row["originEquipWep"]) != -1 and not is_dlc_weapon(wid)


def _weapon_ids(tables: ParamTables, weapon: str | None) -> list[int]:
    """Every named base-game player weapon row, or the affinity variants of one weapon."""
    if weapon:
        wid, _ = resolve_weapon_id(weapon, None, tables)
        base = wid - wid % 10000
        ids = [
            base + off for off in AFFINITY_OFFSETS.values() if base + off in tables.weapons.index
        ]
    else:
        ids = sorted({i for ids in tables.name_index.values() for i in ids})
    return [i for i in ids if is_player_weapon(tables, i)]


def search_weapon_scaling(
    stat: str,
    min_grade: str,
    affinity: str | None = None,
    weapon: str | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
    directory: str | Path | None = None,
) -> ScalingSearchResult:
    """Weapons whose scaling in stat (or any stat) reaches min_grade at some upgrade level.

    Sorted by best scaling value, highest first. affinity limits the search to one
    affinity; weapon limits it to that weapon's affinity variants.
    """
    stats = STATS if stat == "any" else (stat,)
    if stat != "any" and stat not in STATS:
        raise ValueError(f"stat must be one of {', '.join(STATS)} or 'any'")
    grade = min_grade.upper()
    if grade not in GRADE_MIN:
        raise ValueError(f"min_grade must be one of {', '.join(GRADES)}")
    wanted_affinity = normalize_affinity(affinity) if affinity else None

    tables = load_tables(directory)
    families = _family_rates(str(Path(directory) if directory else _data_dir()))
    weapons = tables.weapons
    exists = weapons.index.__contains__

    matches: list[ScalingMatch] = []
    for wid in _weapon_ids(tables, weapon):
        row = weapons.loc[wid]
        aff = infer_affinity(wid, exists)
        if wanted_affinity and aff != wanted_affinity:
            continue
        levels = families.get(int(row["reinforceTypeId"]))
        if not levels:
            continue
        for s in stats:
            correct = float(row[SCALING_COL[s]])
            if correct <= 0:
                continue
            values = [correct * rates[s] for rates in levels]
            best = max(values)
            if best < GRADE_MIN[grade]:
                continue
            reached: dict[str, int] = {}
            for level, value in enumerate(values):
                letter = scaling_grade(value)
                if letter != "-" and letter not in reached:
                    reached[letter] = level
            matches.append(
                ScalingMatch(
                    weapon=str(row["Name"]),
                    affinity=aff,
                    stat=s,
                    max_upgrade_level=len(levels) - 1,
                    best_scaling=round(best, 2),
                    best_grade=scaling_grade(best),
                    grade_from_level=reached,
                )
            )

    matches.sort(key=lambda m: (-m.best_scaling, m.weapon))
    note = None
    if weapon and not _weapon_ids(tables, weapon):
        note = f"{weapon} is not a base-game player weapon (DLC weapons aren't covered)."
    elif len(matches) > limit:
        note = f"Showing the top {limit} of {len(matches)} by scaling; narrow by stat or affinity."
    return ScalingSearchResult(
        stat=stat,
        min_grade=grade,
        affinity=wanted_affinity or "any",
        weapon=weapon or None,
        total_matches=len(matches),
        matches=matches[:limit],
        note=note,
    )


def _data_dir() -> Path:
    from tools.attack_rating import data_dir

    return data_dir()


def clear_cache() -> None:
    _family_rates.cache_clear()
