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

import difflib
import math
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
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
STAT_DISPLAY_NAME = {
    "str": "Strength",
    "dex": "Dexterity",
    "int": "Intelligence",
    "fai": "Faith",
    "arc": "Arcane",
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
    name_index: NameIndex  # normalized name -> IDs (NPC rows excluded)


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

    return ParamTables(
        weapons=weapons,
        reinforce=frames["reinforce"],
        curves=frames["curves"],
        element_correct=frames["element_correct"],
        name_index=build_name_index(weapons["Name"].items()),
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
    # Stats below the weapon's requirement that scale this damage type. When
    # non-empty, the bonus is the -40% penalty instead of stat scaling.
    penalized_by: list[str] = []


class AppliedBuff(BaseModel):
    name: str
    kind: str
    # Stat increases, keyed str/dex/int/fai/arc. Empty = no stat change.
    stats: dict[str, int]
    duration_s: float | None = None


class AttackRating(BaseModel):
    weapon_id: int
    weapon_name: str
    affinity: str
    upgrade_level: int
    two_handed: bool
    buffs: list[AppliedBuff] = []
    # Stats the user didn't give, filled in by fill_unspecified_stats (short keys).
    assumed_stats: dict[str, int] = {}
    # Stats after buffs and two-handing (Str can reach 148), keyed str/dex/int/fai/arc.
    effective_stats: dict[str, int]
    # Weapon's stat requirements, and the ones effective_stats doesn't meet.
    requirements: dict[str, int]
    unmet_requirements: list[str] = []
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


def normalize_name(text: str) -> str:
    """Lowercase, drop apostrophes, collapse punctuation and spaces.

    "Guardian's Swordspear", "guardians swordspear" and "Guardian Swordspear's"
    all differ only in ways players don't care about when typing a name.
    """
    text = re.sub(r"['’]", "", text.lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


class NameIndex(dict[str, list[int]]):
    """Normalized name -> IDs, plus the display name for each key (for suggestions)."""

    def __init__(self) -> None:
        super().__init__()
        self.display: dict[str, str] = {}


def build_name_index(pairs: Iterable[tuple[object, object]]) -> NameIndex:
    """(ID, Name) pairs -> normalized name -> IDs. Skips blank names and "[NPC]"-style rows."""
    index = NameIndex()
    for wid, name in pairs:
        if isinstance(name, str) and name.strip() and not name.startswith("["):
            key = normalize_name(name)
            index.setdefault(key, []).append(int(wid))
            index.display.setdefault(key, name.strip())
    return index


def _unknown_weapon(label: str, key: str, name_index: Mapping[str, list[int]]) -> KeyError:
    """KeyError naming up to three close matches, so a typo can be fixed in one retry."""
    candidates = [key]
    for prefix, _ in _prefixes():
        rest = key.removeprefix(prefix + " ")
        if rest != key:
            candidates.append(rest)
            break
    display = getattr(name_index, "display", {})
    close: list[str] = []
    for candidate in candidates:
        base_game = [k for k, ids in name_index.items() if not all(map(is_dlc_weapon, ids))]
        for match in difflib.get_close_matches(candidate, base_game, n=3, cutoff=0.6):
            if match not in close:
                close.append(match)
    names = [display.get(c, c) for c in close[:3]]
    hint = f" Did you mean: {', '.join(names)}?" if names else ""
    return KeyError(f"Unknown weapon {label!r}.{hint}")


def is_dlc_weapon(wid: int) -> bool:
    """Shadow of the Erdtree weapons use IDs like 2500000 and 67500000 (a 5 in this digit)."""
    return (wid // 100_000) % 10 == 5


def resolve_weapon_id(
    weapon: str | int, affinity: str | None, tables: ParamTables, *, allow_dlc: bool = False
) -> tuple[int, str]:
    """Return (weapon row ID, canonical affinity name) using the cached CSV tables."""
    return resolve_in_index(
        weapon,
        affinity,
        tables.name_index,
        lambda wid: wid in tables.weapons.index,
        allow_dlc=allow_dlc,
    )


def resolve_in_index(
    weapon: str | int,
    affinity: str | None,
    name_index: Mapping[str, list[int]],
    exists: Callable[[int], bool],
    *,
    allow_dlc: bool = False,
) -> tuple[int, str]:
    """Return (weapon row ID, canonical affinity name).

    Accepts a base name plus affinity ("Longsword", "Heavy"), an affinity name
    ("Heavy Longsword", "Bloody Longsword"), or a numeric ID. Works on any data
    source: pass its name index and a function that says whether an ID exists.
    DLC weapons raise ValueError unless allow_dlc is True (kept for a future
    override; nothing turns it on yet).
    """
    wanted = normalize_affinity(affinity) if affinity is not None else None
    parsed: str | None = None

    if isinstance(weapon, int) or str(weapon).strip().isdigit():
        wid = int(weapon)
        if not exists(wid):
            raise KeyError(f"No weapon with ID {wid}")
    else:
        label = str(weapon).strip()
        key = normalize_name(label)
        if key in name_index:
            wid = _unique_id(name_index[key], label)
        else:
            for prefix, aff in _prefixes():
                rest = key.removeprefix(prefix + " ")
                if rest != key and rest in name_index:
                    base = _unique_id(name_index[rest], rest)
                    wid, parsed = base + AFFINITY_OFFSETS[aff], aff
                    break
            else:
                raise _unknown_weapon(label, key, name_index)
    if not allow_dlc and is_dlc_weapon(wid):
        raise ValueError(
            f"{weapon} is a Shadow of the Erdtree (DLC) weapon. TarnishedBot covers the base "
            "game only."
        )

    if wanted is None:
        if parsed is not None:
            _require_row(wid, parsed, exists)
            return wid, parsed
        return wid, infer_affinity(wid, exists)

    offset = wid % 10000
    base = wid - offset
    current = infer_affinity(wid, exists)
    if offset != 0 and current != wanted:
        raise ValueError(f"Weapon {wid} is already {current}; it can't also be {wanted}")
    target = base + AFFINITY_OFFSETS[wanted]
    _require_row(target, wanted, exists)
    return target, wanted


def _require_row(wid: int, affinity: str, exists: Callable[[int], bool]) -> None:
    if not exists(wid):
        raise ValueError(f"This weapon can't take the {affinity} affinity (no row {wid})")


def infer_affinity(wid: int, exists: Callable[[int], bool]) -> str:
    """Affinity implied by an ID's offset (e.g. 2000100 -> Heavy), else Standard."""
    offset = wid % 10000
    if offset == 0:
        return "Standard"
    for name, off in AFFINITY_OFFSETS.items():
        if off == offset and exists(wid - offset):
            return name
    return "Standard"


# -------------------------------------------------------------------- calculate


def display_floor(x: float) -> int:
    """Floor a value the way the game displays it (with a tiny float-error guard)."""
    return math.floor(x + _FLOOR_EPSILON)


def calculate_ar(
    weapon_name_or_id: str | int,
    upgrade_level: int,
    affinity: str | None,
    stats: Stats | Mapping[str, int | None],
    two_handed: bool = False,
    *,
    buffs: Sequence[str] = (),
    tables: ParamTables | None = None,
) -> AttackRating:
    """Calculate displayed attack rating for one weapon, upgrade level, and stat line.

    A stat given as None is filled in by fill_unspecified_stats and listed in
    assumed_stats.
    """
    tables = tables or load_tables()
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
    if reinforce_family(tables.reinforce.loc[reinforce_type]["Name"]) != reinforce_family(
        reinforce["Name"]
    ):
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

    assumed: dict[str, int] = {}
    if not isinstance(stats, Stats):
        long_to_short = {n: f.alias for n, f in Stats.model_fields.items() if f.alias}
        given = {long_to_short.get(k, k): v for k, v in stats.items()}
        assumed = fill_unspecified_stats(weapon, element, reinforce, given)
        stats = Stats(**{**given, **assumed})

    effective = stats.short()
    applied = _apply_buffs(effective, buffs, warnings)

    # Two-handing multiplies the buffed Strength.
    gets_two_hand_bonus = int(weapon["isDualBlade"]) != 1
    if two_handed:
        if gets_two_hand_bonus:
            effective["str"] = min(
                math.floor(effective["str"] * TWO_HAND_STR_MULTIPLIER), TWO_HAND_STR_CAP
            )
        else:
            warnings.append("No two-handing Strength bonus for this weapon (isDualBlade = 1).")

    requirements = {s: int(weapon[REQUIREMENT_COL[s]]) for s in STATS}
    unmet_requirements = [s for s in STATS if effective[s] < requirements[s]]

    lines: dict[str, DamageLine] = {}
    for dmg in DAMAGE_TYPES:
        base = float(weapon[ATTACK_BASE_COL[dmg]]) * float(reinforce[ATK_RATE_COL[dmg]])
        bonus = 0.0
        unmet: list[str] = []
        if base > 0:
            suffix = ELEMENT_SUFFIX[dmg]
            flagged = [
                s for s in STATS if int(element[f"is{ELEMENT_STAT_NAME[s]}Correct_by{suffix}"]) == 1
            ]
            unmet = [s for s in flagged if s in unmet_requirements]
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
            base=display_floor(base),
            bonus=display_floor(bonus),
            total=display_floor(base + bonus),
            base_exact=base,
            bonus_exact=bonus,
            penalized_by=unmet,
        )

    for s in unmet_requirements:
        hit = [dmg for dmg, line in lines.items() if s in line.penalized_by]
        msg = (
            f"{STAT_DISPLAY_NAME[s]} {effective[s]} is below this weapon's requirement of "
            f"{requirements[s]}."
        )
        if hit:
            msg += (
                f" {STAT_DISPLAY_NAME[s]} scales {', '.join(hit)} damage, so that bonus is a "
                f"{round(REQUIREMENT_PENALTY * 100)}% penalty on base damage instead of scaling."
            )
        warnings.append(msg)

    total = display_floor(sum(line.base_exact + line.bonus_exact for line in lines.values()))
    return AttackRating(
        weapon_id=wid,
        weapon_name=str(weapon["Name"]),
        affinity=affinity_name,
        upgrade_level=upgrade_level,
        two_handed=two_handed,
        buffs=applied,
        assumed_stats=assumed,
        effective_stats=effective,
        requirements=requirements,
        unmet_requirements=unmet_requirements,
        total=total,
        warnings=warnings,
        **lines,
    )


# A stat that can't change this weapon's attack rating is assumed to be this.
NEUTRAL_STAT = 10


def stats_that_matter(weapon: pd.Series, element: pd.Series, reinforce: pd.Series) -> set[str]:
    """Stats that scale at least one damage type this weapon deals at this upgrade level."""
    matter: set[str] = set()
    for dmg in DAMAGE_TYPES:
        if float(weapon[ATTACK_BASE_COL[dmg]]) * float(reinforce[ATK_RATE_COL[dmg]]) <= 0:
            continue
        suffix = ELEMENT_SUFFIX[dmg]
        for s in STATS:
            flagged = int(element[f"is{ELEMENT_STAT_NAME[s]}Correct_by{suffix}"]) == 1
            if flagged and float(weapon[SCALING_COL[s]]) > 0:
                matter.add(s)
    return matter


def lowest_starting_stats() -> dict[str, int]:
    """Lowest value of each stat among the ten starting classes, short keys."""
    from tools.starting_classes import starting_classes  # imports this module

    long_names = {
        "str": "strength",
        "dex": "dexterity",
        "int": "intelligence",
        "fai": "faith",
        "arc": "arcane",
    }
    classes = starting_classes()
    return {s: min(c.stats[long] for c in classes) for s, long in long_names.items()}


def fill_unspecified_stats(
    weapon: pd.Series, element: pd.Series, reinforce: pd.Series, given: dict[str, int | None]
) -> dict[str, int]:
    """Values for the stats given as None (or left out). Returns only the filled ones.

    - A stat that can't change this weapon's attack rating: 10.
    - A stat that scales its damage but has no requirement (an Int-scaling Magic
      affinity, say): the lowest value any starting class has.
    - A stat that scales its damage and has a requirement: ValueError, since any
      guess would decide whether the -40% penalty applies.
    """
    missing = [s for s in STATS if given.get(s) is None]
    if not missing:
        return {}
    matter = stats_that_matter(weapon, element, reinforce)
    lowest = lowest_starting_stats() if any(s in matter for s in missing) else {}
    filled: dict[str, int] = {}
    for s in missing:
        if s not in matter:
            filled[s] = NEUTRAL_STAT
        elif int(weapon[REQUIREMENT_COL[s]]) > 0:
            raise ValueError(
                f"{STAT_DISPLAY_NAME[s]} wasn't given, and this weapon needs "
                f"{int(weapon[REQUIREMENT_COL[s]])} {STAT_DISPLAY_NAME[s]} and scales with it. "
                "Ask the user for it."
            )
        else:
            filled[s] = lowest[s]
    return filled


def _apply_buffs(
    effective: dict[str, int], buffs: Sequence[str], warnings: list[str]
) -> list[AppliedBuff]:
    """Add each buff's stat bonus to effective (in place), capped at MAX_STAT."""
    if not buffs:
        return []
    # Imported here because tools.buffs imports this module.
    from tools.buffs import find_buff

    applied: list[AppliedBuff] = []
    for name in buffs:
        buff = find_buff(name)
        if any(a.name == buff.name for a in applied):
            continue  # the same buff doesn't stack with itself
        applied.append(AppliedBuff(**buff.model_dump(exclude={"effect_id"})))
        if not buff.stats:
            warnings.append(
                f"{buff.name} doesn't raise any stats, so it doesn't change attack rating. "
                "Damage multipliers from buffs aren't shown on the status screen or calculated "
                "here."
            )
        for stat, amount in buff.stats.items():
            effective[stat] += amount

    capped = [s for s in STATS if effective[s] > MAX_STAT]
    for stat in capped:
        effective[stat] = MAX_STAT
    if capped:
        names = ", ".join(STAT_DISPLAY_NAME[s] for s in capped)
        warnings.append(f"Buffed {names} capped at {MAX_STAT}.")
    return applied


def reinforce_family(reinforce_name: object) -> str:
    """'Standard +10' -> 'Standard'."""
    return str(reinforce_name).rsplit(" +", 1)[0]


def _has_overrides(element: pd.Series) -> bool:
    for col, value in element.items():
        if str(col).startswith("overwrite") and int(value) != -1:
            return True
        if str(col).startswith("Influence") and int(value) != 100:
            return True
    return False
