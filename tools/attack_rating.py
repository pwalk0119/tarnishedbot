"""Attack-rating (AR) calculator built on Smithbox CSV exports.

Game data: Elden Ring 1.17.2 / regulation 1.17.3, base game only.

CSV files (read from TARNISHEDBOT_DATA_DIR if set, else data/processed/):
    EquipParamWeapon.csv, ReinforceParamWeapon.csv,
    CalcCorrectGraph.csv, AttackElementCorrectParam.csv

Formula, per damage type (physical, magic, fire, lightning, holy):
    base  = attackBase<Type> * <type>AtkRate[reinforceTypeId + level]
    bonus = base * sum(scaling[stat] * curve(stat))  for stats flagged in
            AttackElementCorrectParam (is<Stat>Correct_by<Type>)
            where scaling[stat] = correct<Stat> * correct<Stat>Rate / 100
    If a flagged stat is below its requirement, bonus = base * -0.4.
    Two-handing: Str = min(floor(Str * 1.5), 148) unless the weapon has isDualBlade = 1.
    Displayed base and bonus are floored.

Data names differ from in-game names: holy = "Dark", lightning = "Thunder",
Int = "Magic", Dex = "Agility"/"Dexterity", Arcane = "Luck".

TODO:
    - Status buildup (bleed, frost, poison, sleep, madness, scarlet rot).
    - Somber (unique) weapon edge cases, including +0..+10 level validation.
    - AttackElementCorrectParam overwrite*/Influence* columns (used by some
      Smithscript / Golem Fist affinities). Currently ignored; a warning is returned.
    - Bows/greatbows/ballistae, which the game may always treat as two-handed.
    - Sorcery/incantation scaling for staves and seals.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

DATA_DIR_ENV = "TARNISHEDBOT_DATA_DIR"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"
CSV_FILES = {
    "weapons": "EquipParamWeapon.csv",
    "reinforce": "ReinforceParamWeapon.csv",
    "curves": "CalcCorrectGraph.csv",
    "element_correct": "AttackElementCorrectParam.csv",
}

DAMAGE_TYPES = ("physical", "magic", "fire", "lightning", "holy")
STATS = ("str", "dex", "int", "fai", "arc")

# Column names, verified against the CSV headers.
ATTACK_BASE_COL = {
    "physical": "attackBasePhysics",
    "magic": "attackBaseMagic",
    "fire": "attackBaseFire",
    "lightning": "attackBaseThunder",
    "holy": "attackBaseDark",
}
ATK_RATE_COL = {
    "physical": "physicsAtkRate",
    "magic": "magicAtkRate",
    "fire": "fireAtkRate",
    "lightning": "thunderAtkRate",
    "holy": "darkAtkRate",
}
CURVE_COL = {
    "physical": "correctType_Physics",
    "magic": "correctType_Magic",
    "fire": "correctType_Fire",
    "lightning": "correctType_Thunder",
    "holy": "correctType_Dark",
}
ELEMENT_SUFFIX = {
    "physical": "Physics",
    "magic": "Magic",
    "fire": "Fire",
    "lightning": "Thunder",
    "holy": "Dark",
}
SCALING_COL = {
    "str": "correctStrength",
    "dex": "correctAgility",
    "int": "correctMagic",
    "fai": "correctFaith",
    "arc": "correctLuck",
}
SCALING_RATE_COL = {
    "str": "correctStrengthRate",
    "dex": "correctAgilityRate",
    "int": "correctMagicRate",
    "fai": "correctFaithRate",
    "arc": "correctLuckRate",
}
REQUIREMENT_COL = {
    "str": "properStrength",
    "dex": "properAgility",
    "int": "properMagic",
    "fai": "properFaith",
    "arc": "properLuck",
}
ELEMENT_STAT_NAME = {
    "str": "Strength",
    "dex": "Dexterity",
    "int": "Magic",
    "fai": "Faith",
    "arc": "Luck",
}

AFFINITY_OFFSETS = {
    "Standard": 0,
    "Heavy": 100,
    "Keen": 200,
    "Quality": 300,
    "Fire": 400,
    "Flame Art": 500,
    "Lightning": 600,
    "Sacred": 700,
    "Magic": 800,
    "Cold": 900,
    "Poison": 1000,
    "Blood": 1100,
    "Occult": 1200,
}
# Extra spellings seen in weapon names or likely from users.
AFFINITY_ALIASES = {
    "bloody": "Blood",
    "none": "Standard",
    "holy": "Sacred",
    "flameart": "Flame Art",
}

MAX_STAT = 99
MAX_UPGRADE_LEVEL = 25
TWO_HAND_STR_MULTIPLIER = 1.5
TWO_HAND_STR_CAP = 148
REQUIREMENT_PENALTY = -0.4
CURVE_STAGES = 5
# Guards floor() against float error such as 268.99999999999997.
_FLOOR_EPSILON = 1e-9


# --------------------------------------------------------------------------- data


@dataclass(frozen=True)
class ParamTables:
    weapons: pd.DataFrame  # indexed by ID
    reinforce: pd.DataFrame
    curves: pd.DataFrame
    element_correct: pd.DataFrame
    name_index: dict[str, list[int]]  # lowercase name -> IDs (NPC rows excluded)


def data_dir() -> Path:
    env = os.environ.get(DATA_DIR_ENV)
    return Path(env) if env else DEFAULT_DATA_DIR


def data_available(directory: Path | None = None) -> bool:
    d = directory or data_dir()
    return all((d / name).is_file() for name in CSV_FILES.values())


@lru_cache(maxsize=4)
def _load_tables(directory: str) -> ParamTables:
    d = Path(directory)
    missing = [name for name in CSV_FILES.values() if not (d / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing param CSVs in {d}: {', '.join(missing)}")

    frames = {key: pd.read_csv(d / name).set_index("ID") for key, name in CSV_FILES.items()}
    weapons = frames["weapons"]

    name_index: dict[str, list[int]] = {}
    for wid, name in weapons["Name"].items():
        if isinstance(name, str) and name and not name.startswith("["):
            name_index.setdefault(name.strip().lower(), []).append(int(wid))

    return ParamTables(
        weapons=weapons,
        reinforce=frames["reinforce"],
        curves=frames["curves"],
        element_correct=frames["element_correct"],
        name_index=name_index,
    )


def load_tables(directory: str | Path | None = None) -> ParamTables:
    """Read the four CSVs once and cache them (per directory)."""
    return _load_tables(str(Path(directory) if directory else data_dir()))


# ------------------------------------------------------------------------ models


class Stats(BaseModel):
    """Character stats. Accepts short keys: str, dex, int, fai, arc."""

    model_config = ConfigDict(populate_by_name=True)

    strength: int = Field(ge=1, le=MAX_STAT, alias="str")
    dexterity: int = Field(ge=1, le=MAX_STAT, alias="dex")
    intelligence: int = Field(ge=1, le=MAX_STAT, alias="int")
    faith: int = Field(ge=1, le=MAX_STAT, alias="fai")
    arcane: int = Field(ge=1, le=MAX_STAT, alias="arc")

    def short(self) -> dict[str, int]:
        return self.model_dump(by_alias=True)


class DamageLine(BaseModel):
    base: int
    bonus: int
    total: int
    base_exact: float
    bonus_exact: float


class AttackRating(BaseModel):
    weapon_id: int
    weapon_name: str
    affinity: str
    upgrade_level: int
    two_handed: bool
    # Stats after two-handing (Str can reach 148), keyed str/dex/int/fai/arc.
    effective_stats: dict[str, int]
    physical: DamageLine
    magic: DamageLine
    fire: DamageLine
    lightning: DamageLine
    holy: DamageLine
    total: int
    warnings: list[str] = []

    def line(self, damage_type: str) -> DamageLine:
        return getattr(self, damage_type)


# ------------------------------------------------------------------------ curve


def curve_value(
    stat: float,
    stage_max: list[float],
    stage_grow: list[float],
    adj_pt: list[float],
) -> float:
    """CalcCorrectGraph curve value for a stat, as a fraction (e.g. 0.75 for 75%).

    stage_max: stageMaxVal0..4 breakpoints; stage_grow: stageMaxGrowVal0..4;
    adj_pt: adjPt_maxGrowVal0..4 exponents (stage i uses adj_pt[i]).
    """
    if not (len(stage_max) == len(stage_grow) == len(adj_pt) == CURVE_STAGES):
        raise ValueError("curve needs exactly 5 breakpoints, grow values and exponents")
    if stat <= stage_max[0]:
        return stage_grow[0] / 100
    if stat >= stage_max[-1]:
        return stage_grow[-1] / 100

    i = next(k for k in range(CURVE_STAGES - 1) if stat <= stage_max[k + 1])
    ratio = (stat - stage_max[i]) / (stage_max[i + 1] - stage_max[i])
    exponent = adj_pt[i]
    growth = ratio**exponent if exponent > 0 else 1 - (1 - ratio) ** (-exponent)
    return (stage_grow[i] + (stage_grow[i + 1] - stage_grow[i]) * growth) / 100


def curve_from_row(row: pd.Series) -> tuple[list[float], list[float], list[float]]:
    return (
        [float(row[f"stageMaxVal{i}"]) for i in range(CURVE_STAGES)],
        [float(row[f"stageMaxGrowVal{i}"]) for i in range(CURVE_STAGES)],
        [float(row[f"adjPt_maxGrowVal{i}"]) for i in range(CURVE_STAGES)],
    )


# ----------------------------------------------------------------------- lookup


def normalize_affinity(affinity: str) -> str:
    key = affinity.strip().lower()
    if key in AFFINITY_ALIASES:
        return AFFINITY_ALIASES[key]
    for name in AFFINITY_OFFSETS:
        if name.lower() == key:
            return name
    raise ValueError(f"Unknown affinity {affinity!r}. Options: {', '.join(AFFINITY_OFFSETS)}")


def _prefixes() -> list[tuple[str, str]]:
    """(lowercase name prefix, canonical affinity), longest first."""
    pairs = [(name.lower(), name) for name in AFFINITY_OFFSETS if name != "Standard"]
    pairs.append(("bloody", "Blood"))
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def _unique_id(ids: list[int], label: str) -> int:
    if len(ids) > 1:
        raise ValueError(f"{label!r} matches several weapon IDs {ids}; pass the ID instead")
    return ids[0]


def resolve_weapon_id(
    weapon: str | int, affinity: str | None, tables: ParamTables
) -> tuple[int, str]:
    """Return (weapon row ID, canonical affinity name).

    Accepts a base name plus affinity ("Longsword", "Heavy"), an affinity name
    ("Heavy Longsword", "Bloody Longsword"), or a numeric ID.
    """
    wanted = normalize_affinity(affinity) if affinity is not None else None
    parsed: str | None = None

    if isinstance(weapon, int) or str(weapon).strip().isdigit():
        wid = int(weapon)
        if wid not in tables.weapons.index:
            raise KeyError(f"No weapon with ID {wid}")
    else:
        label = str(weapon).strip()
        key = label.lower()
        if key in tables.name_index:
            wid = _unique_id(tables.name_index[key], label)
        else:
            for prefix, aff in _prefixes():
                rest = key.removeprefix(prefix + " ")
                if rest != key and rest in tables.name_index:
                    base = _unique_id(tables.name_index[rest], rest)
                    wid, parsed = base + AFFINITY_OFFSETS[aff], aff
                    break
            else:
                raise KeyError(f"Unknown weapon {label!r}")

    if wanted is None:
        if parsed is not None:
            _require_row(wid, parsed, tables)
            return wid, parsed
        return wid, _infer_affinity(wid, tables)

    offset = wid % 10000
    base = wid - offset
    current = _infer_affinity(wid, tables)
    if offset != 0 and current != wanted:
        raise ValueError(f"Weapon {wid} is already {current}; it can't also be {wanted}")
    target = base + AFFINITY_OFFSETS[wanted]
    _require_row(target, wanted, tables)
    return target, wanted


def _require_row(wid: int, affinity: str, tables: ParamTables) -> None:
    if wid not in tables.weapons.index:
        raise ValueError(f"This weapon can't take the {affinity} affinity (no row {wid})")


def _infer_affinity(wid: int, tables: ParamTables) -> str:
    offset = wid % 10000
    if offset == 0:
        return "Standard"
    for name, off in AFFINITY_OFFSETS.items():
        if off == offset and (wid - offset) in tables.weapons.index:
            return name
    return "Standard"


# -------------------------------------------------------------------- calculate


def _floor(x: float) -> int:
    return math.floor(x + _FLOOR_EPSILON)


def calculate_ar(
    weapon_name_or_id: str | int,
    upgrade_level: int,
    affinity: str | None,
    stats: Stats | Mapping[str, int],
    two_handed: bool = False,
    *,
    tables: ParamTables | None = None,
) -> AttackRating:
    """Calculate displayed attack rating for one weapon, upgrade level, and stat line."""
    tables = tables or load_tables()
    stats = stats if isinstance(stats, Stats) else Stats(**stats)
    wid, affinity_name = resolve_weapon_id(weapon_name_or_id, affinity, tables)
    weapon = tables.weapons.loc[wid]
    warnings: list[str] = []

    if not 0 <= upgrade_level <= MAX_UPGRADE_LEVEL:
        raise ValueError(f"upgrade_level must be 0..{MAX_UPGRADE_LEVEL}")
    reinforce_type = int(weapon["reinforceTypeId"])
    reinforce_id = reinforce_type + upgrade_level
    if reinforce_type not in tables.reinforce.index or reinforce_id not in tables.reinforce.index:
        raise ValueError(f"No upgrade row {reinforce_id} (+{upgrade_level}) for weapon {wid}")
    reinforce = tables.reinforce.loc[reinforce_id]
    # Rows are named like "Standard +10". A level past the family's max lands in
    # the next family (e.g. Standard +100 would be Heavy +0), so compare names.
    if _family(tables.reinforce.loc[reinforce_type]["Name"]) != _family(reinforce["Name"]):
        raise ValueError(f"+{upgrade_level} is above this weapon's max upgrade level")

    element_id = int(weapon["attackElementCorrectId"])
    if element_id not in tables.element_correct.index:
        raise ValueError(f"Missing AttackElementCorrectParam row {element_id}")
    element = tables.element_correct.loc[element_id]
    if _has_overrides(element):
        warnings.append(
            "This weapon's AttackElementCorrectParam row uses overwrite/Influence values, "
            "which this calculator doesn't apply yet; scaling may be slightly off."
        )

    effective = stats.short()
    gets_two_hand_bonus = int(weapon["isDualBlade"]) != 1
    if two_handed:
        if gets_two_hand_bonus:
            effective["str"] = min(
                math.floor(stats.strength * TWO_HAND_STR_MULTIPLIER), TWO_HAND_STR_CAP
            )
        else:
            warnings.append("No two-handing Strength bonus for this weapon (isDualBlade = 1).")

    lines: dict[str, DamageLine] = {}
    for dmg in DAMAGE_TYPES:
        base = float(weapon[ATTACK_BASE_COL[dmg]]) * float(reinforce[ATK_RATE_COL[dmg]])
        bonus = 0.0
        if base > 0:
            suffix = ELEMENT_SUFFIX[dmg]
            flagged = [
                s for s in STATS if int(element[f"is{ELEMENT_STAT_NAME[s]}Correct_by{suffix}"]) == 1
            ]
            unmet = [s for s in flagged if effective[s] < int(weapon[REQUIREMENT_COL[s]])]
            if unmet:
                bonus = base * REQUIREMENT_PENALTY
            else:
                curve = curve_from_row(tables.curves.loc[int(weapon[CURVE_COL[dmg]])])
                bonus = base * sum(
                    float(weapon[SCALING_COL[s]])
                    * float(reinforce[SCALING_RATE_COL[s]])
                    / 100
                    * curve_value(effective[s], *curve)
                    for s in flagged
                )
        lines[dmg] = DamageLine(
            base=_floor(base),
            bonus=_floor(bonus),
            total=_floor(base + bonus),
            base_exact=base,
            bonus_exact=bonus,
        )

    total = _floor(sum(line.base_exact + line.bonus_exact for line in lines.values()))
    return AttackRating(
        weapon_id=wid,
        weapon_name=str(weapon["Name"]),
        affinity=affinity_name,
        upgrade_level=upgrade_level,
        two_handed=two_handed,
        effective_stats=effective,
        total=total,
        warnings=warnings,
        **lines,
    )


def _family(reinforce_name: object) -> str:
    """'Standard +10' -> 'Standard'."""
    return str(reinforce_name).rsplit(" +", 1)[0]


def _has_overrides(element: pd.Series) -> bool:
    for col, value in element.items():
        if str(col).startswith("overwrite") and int(value) != -1:
            return True
        if str(col).startswith("Influence") and int(value) != 100:
            return True
    return False
