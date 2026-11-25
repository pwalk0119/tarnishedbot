"""Starting classes and keepsakes, read from CharaInitParam.

Rows 3000-3009 are the ten classes ("Class - Samurai"); rows 2400-2409 are
the keepsakes offered at character creation ("Gift - Golden Seed"). Each row
lists equipment by item ID, so names are looked up in the weapon, armor,
talisman and goods params. Column meanings (verified against known classes):
baseVit = Vigor, baseWil = Mind, baseEnd = Endurance, baseMag = Intelligence,
baseLuc = Arcane.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from tools.attack_rating import data_dir, normalize_name

CLASS_IDS = range(3000, 3010)
KEEPSAKE_IDS = range(2400, 2410)
CLASS_PREFIX = "Class - "
KEEPSAKE_PREFIX = "Gift - "

STAT_COLUMNS = {
    "vigor": "baseVit",
    "mind": "baseWil",
    "endurance": "baseEnd",
    "strength": "baseStr",
    "dexterity": "baseDex",
    "intelligence": "baseMag",
    "faith": "baseFai",
    "arcane": "baseLuc",
}
WEAPON_SLOTS = (
    "equip_Wep_Right",
    "equip_Subwep_Right",
    "equip_Subwep_Right3",
    "equip_Wep_Left",
    "equip_Subwep_Left",
    "equip_Subwep_Left3",
)
ARMOR_SLOTS = ("equip_Helm", "equip_Armer", "equip_Gaunt", "equip_Leg")
TALISMAN_SLOTS = tuple(f"equip_Accessory0{i}" for i in range(1, 5))
SPELL_SLOTS = tuple(f"equip_Spell_0{i}" for i in range(1, 8))
AMMO_SLOTS = (
    ("equip_Arrow", "arrowNum"),
    ("equip_SubArrow", "subArrowNum"),
    ("equip_Bolt", "boltNum"),
    ("equip_SubBolt", "subBoltNum"),
)
ITEM_SLOTS = tuple((f"item_{i:02d}", f"itemNum_{i:02d}") for i in range(1, 11)) + tuple(
    (f"secondaryItem_{i:02d}", f"secondaryItemNum_{i:02d}") for i in range(1, 7)
)
NAME_FILES = {
    "weapon": "EquipParamWeapon.csv",
    "armor": "EquipParamProtector.csv",
    "talisman": "EquipParamAccessory.csv",
    "goods": "EquipParamGoods.csv",
}
# Short, hand-written summaries (the game's own item descriptions aren't extracted yet).
# Numbers checked against the params where they exist: Crimson Amber Medallion's
# SpEffect has maxHpRate 1.06, and Boiled Prawn's lasts 60 s.
KEEPSAKE_EFFECTS = {
    "None": "No keepsake.",
    "Crimson Amber Medallion": "Talisman that raises maximum HP by 6%.",
    "Lands Between Rune": "Consumable that gives 3,000 runes when used.",
    "Golden Seed": "Adds one use to your Flask of Crimson/Cerulean Tears, at a site of grace.",
    "Fanged Imp Ashes": "Spirit Ashes that summon Fanged Imp spirits to fight beside you.",
    "Cracked Pot": "Empty pots used to craft throwing pots.",
    "Stonesword Key": "Breaks an imp statue seal, opening sealed doors and areas.",
    "Bewitching Branch": "Charms an enemy so it fights for you for a short time.",
    "Boiled Prawn": "Food that raises physical damage negation for 60 seconds.",
    "Shabriri's Woe": "Talisman that draws enemies' attention to you.",
}
_TAG_RE = re.compile(r"^\[[^\]]+\]\s*")  # "[Sorcery] Glintstone Pebble" -> "Glintstone Pebble"


class ItemStack(BaseModel):
    name: str
    count: int = 1


class StartingClass(BaseModel):
    name: str
    level: int
    stats: dict[str, int]
    weapons: list[str]
    armor: list[str]
    talismans: list[str]
    spells: list[str]
    ammo: list[ItemStack]
    items: list[ItemStack]


class Keepsake(BaseModel):
    name: str
    items: list[ItemStack]
    effect: str  # hand-written summary, see KEEPSAKE_EFFECTS


class _Data(BaseModel):
    classes: dict[str, StartingClass]
    keepsakes: list[Keepsake]


def _names(directory: Path, kind: str) -> dict[int, str]:
    frame = pd.read_csv(directory / NAME_FILES[kind], usecols=["ID", "Name"])
    return {
        int(i): _TAG_RE.sub("", str(n)).strip()
        for i, n in zip(frame["ID"], frame["Name"], strict=True)
        if isinstance(n, str) and n.strip()
    }


def _ids(row: pd.Series, slots: tuple[str, ...]) -> list[int]:
    return [int(row[s]) for s in slots if int(row[s]) > 0]


@lru_cache(maxsize=4)
def _load(directory: str) -> _Data:
    d = Path(directory)
    chara = pd.read_csv(d / "CharaInitParam.csv").set_index("ID")
    names = {kind: _names(d, kind) for kind in NAME_FILES}

    def named(kind: str, item_id: int) -> str:
        return names[kind].get(item_id, f"Unknown {kind} {item_id}")

    def stacks(row: pd.Series, slots: tuple[tuple[str, str], ...], kind: str) -> list[ItemStack]:
        return [
            ItemStack(name=named(kind, int(row[i])), count=max(int(row[n]), 1))
            for i, n in slots
            if int(row[i]) > 0
        ]

    def gear(row: pd.Series) -> dict[str, list]:
        return {
            "weapons": [named("weapon", i) for i in _ids(row, WEAPON_SLOTS)],
            "armor": [named("armor", i) for i in _ids(row, ARMOR_SLOTS)],
            "talismans": [named("talisman", i) for i in _ids(row, TALISMAN_SLOTS)],
            "spells": [named("goods", i) for i in _ids(row, SPELL_SLOTS)],
            "ammo": stacks(row, AMMO_SLOTS, "weapon"),
            "items": stacks(row, ITEM_SLOTS, "goods"),
        }

    classes = {}
    for cid in CLASS_IDS:
        row = chara.loc[cid]
        name = str(row["Name"]).removeprefix(CLASS_PREFIX)
        classes[normalize_name(name)] = StartingClass(
            name=name,
            level=int(row["soulLv"]),
            stats={stat: int(row[col]) for stat, col in STAT_COLUMNS.items()},
            **gear(row),
        )

    keepsakes = []
    for kid in KEEPSAKE_IDS:
        row = chara.loc[kid]
        g = gear(row)
        items = [ItemStack(name=n) for n in g["talismans"]] + g["items"]
        name = str(row["Name"]).removeprefix(KEEPSAKE_PREFIX)
        keepsakes.append(Keepsake(name=name, items=items, effect=KEEPSAKE_EFFECTS.get(name, "")))
    return _Data(classes=classes, keepsakes=keepsakes)


def _data(directory: Path | None = None) -> _Data:
    return _load(str(directory or data_dir()))


def clear_cache() -> None:
    _load.cache_clear()


def class_names(directory: Path | None = None) -> list[str]:
    return [c.name for c in _data(directory).classes.values()]


def starting_classes(name: str | None = None, directory: Path | None = None) -> list[StartingClass]:
    """One class by name, or all ten when name is None or "all"."""
    classes = _data(directory).classes
    if name is None or normalize_name(name) == "all":
        return list(classes.values())
    key = normalize_name(name)
    if key not in classes:
        options = ", ".join(c.name for c in classes.values())
        raise KeyError(f"Unknown starting class {name!r}. Classes: {options}")
    return [classes[key]]


def keepsakes(directory: Path | None = None) -> list[Keepsake]:
    """The keepsakes offered at character creation, "None" included."""
    return list(_data(directory).keepsakes)
