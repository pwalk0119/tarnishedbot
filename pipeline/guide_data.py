"""Game facts for writing guide entries, read from the param CSVs in data/processed.

Row names are Smithbox Paramdex labels, often prefixed with a group in brackets,
e.g. "[Stormhill] Castleward Tunnel" or "[Merchant Kale] Crafting Kit".
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pandas as pd

from pipeline.load_params import PROCESSED_DIR

LABEL_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")

# Item tables by kind. ShopLineupParam.equipType and ItemLotParam.lotItemCategory
# use different numbering for the same kinds.
ITEM_TABLES = {
    "weapon": "EquipParamWeapon",
    "protector": "EquipParamProtector",
    "accessory": "EquipParamAccessory",
    "goods": "EquipParamGoods",
    "gem": "EquipParamGem",
}
SHOP_EQUIP_TYPE = {0: "weapon", 1: "protector", 2: "accessory", 3: "goods", 4: "gem"}
LOT_CATEGORY = {1: "goods", 2: "weapon", 3: "protector", 4: "accessory", 5: "gem"}
# How many follow-on rows (ID+1, ID+2, ...) an enemy's item lot can chain into.
MAX_LOT_CHAIN = 10


def split_label(name: object) -> tuple[str, str]:
    """'[Stormhill] Castleward Tunnel' -> ('Stormhill', 'Castleward Tunnel')."""
    text = "" if pd.isna(name) else str(name).strip()
    m = LABEL_RE.match(text)
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", text)


@cache
def table(param: str, data_dir: Path = PROCESSED_DIR) -> pd.DataFrame:
    df = pd.read_csv(data_dir / f"{param}.csv", low_memory=False)
    return df.set_index("ID", drop=False)


def clear_cache() -> None:
    table.cache_clear()
    _item_index.cache_clear()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower().replace("é", "e")).strip()


# --------------------------------------------------------------------- items


def item_name(kind: str, item_id: int) -> str:
    df = table(ITEM_TABLES[kind])
    if item_id in df.index:
        return split_label(df.at[item_id, "Name"])[1] or f"{kind} {item_id}"
    return f"{kind} {item_id}"


@cache
def _item_index() -> dict[str, list[tuple[str, int, str]]]:
    index: dict[str, list[tuple[str, int, str]]] = {}
    for kind, param in ITEM_TABLES.items():
        for item_id, name in table(param)["Name"].items():
            label = split_label(name)[1]
            if label:
                index.setdefault(_norm(label), []).append((kind, int(item_id), label))
    for item_id, name in table("Magic")["Name"].items():
        label = split_label(name)[1]
        if label:
            index.setdefault(_norm(label), []).append(("magic", int(item_id), label))
    return index


def find_item(name: str) -> list[tuple[str, int, str]]:
    """Exact (case/punctuation-insensitive) matches as (kind, id, name)."""
    return _item_index().get(_norm(name), [])


def search_items(text: str, limit: int = 15) -> list[tuple[str, int, str]]:
    """Items whose name contains text."""
    needle = _norm(text)
    hits = [hit for key, hits in _item_index().items() if needle in key for hit in hits]
    return sorted(hits, key=lambda h: (len(h[2]), h[2]))[:limit]


# -------------------------------------------------------------------- graces


@dataclass
class Grace:
    id: int
    name: str
    group: str


# Paramdex labels that differ from the in-game grace name, confirmed in game.
GRACE_NAME_FIXES = {
    "Gatefront Ruins": "Gatefront",  # by Stormgate, west of the ruins
}


def graces(group: str | None = None) -> list[Grace]:
    """Sites of grace, optionally only those in a grace group such as 'Stormhill'."""
    warps = table("BonfireWarpParam")
    groups = table("BonfireWarpSubCategoryParam")["Name"].to_dict()
    out = []
    for _, row in warps.iterrows():
        label_group, name = split_label(row["Name"])
        name = GRACE_NAME_FIXES.get(name, name)
        if not name:
            continue
        group_name = split_label(groups.get(int(row["bonfireSubCategoryId"]), ""))[1]
        group_name = group_name or label_group
        if group is None or _norm(group_name) == _norm(group):
            out.append(Grace(int(row["ID"]), name, group_name))
    return out


def grace_names() -> set[str]:
    return {_norm(g.name) for g in graces()}


def grace_groups() -> list[str]:
    return sorted({g.group for g in graces() if g.group})


# --------------------------------------------------------------------- shops


@dataclass
class ShopItem:
    id: int
    name: str
    price: int
    stock: int | None  # None = unlimited
    unlocked_later: bool


def merchants() -> list[str]:
    labels = (split_label(n)[0] for n in table("ShopLineupParam")["Name"])
    return sorted({label for label in labels if label})


def shop(merchant: str) -> list[ShopItem]:
    """Items sold by a merchant, using the bracketed label on each row."""
    out = []
    for _, row in table("ShopLineupParam").iterrows():
        label, rest = split_label(row["Name"])
        if _norm(label) != _norm(merchant):
            continue
        kind = SHOP_EQUIP_TYPE.get(int(row["equipType"]))
        name = item_name(kind, int(row["equipId"])) if kind else rest
        qty = int(row["sellQuantity"])
        out.append(
            ShopItem(
                id=int(row["ID"]),
                name=name,
                price=int(row["value"]),
                stock=None if qty < 0 else qty,
                unlocked_later=int(row["eventFlag_forRelease"]) != 0,
            )
        )
    return out


# ------------------------------------------------------------------- enemies


@dataclass
class Drop:
    name: str
    chance: float  # percent
    quantity: int

    def bullet(self) -> str:
        """'- **Bolt** (x5) — 10% drop chance': quantity goes with the item name."""
        qty = f" (x{self.quantity})" if self.quantity > 1 else ""
        return f"- **{self.name}**{qty} — {self.chance:g}% drop chance"


@dataclass
class EnemyVariant:
    id: int
    name: str
    base_hp: int
    hp: int
    area_scaling: str | None
    runes: int
    stance: int
    drops: list[Drop] = field(default_factory=list)


def drops(lot_id: int) -> list[Drop]:
    """Items from an enemy item lot and the rows chained after it."""
    lots = table("ItemLotParam_enemy")
    out: list[Drop] = []
    for lid in range(lot_id, lot_id + MAX_LOT_CHAIN):
        if lid not in lots.index:
            break
        row = lots.loc[lid]
        points = [int(row[f"lotItemBasePoint{i:02d}"]) for i in range(1, 9)]
        total = sum(points) or 1
        for i in range(1, 9):
            kind = LOT_CATEGORY.get(int(row[f"lotItemCategory{i:02d}"]))
            item_id = int(row[f"lotItemId{i:02d}"])
            if kind and item_id > 0 and points[i - 1] > 0:
                out.append(
                    Drop(
                        name=item_name(kind, item_id),
                        chance=round(100 * points[i - 1] / total, 1),
                        quantity=max(int(row[f"lotItemNum{i:02d}"]), 1),
                    )
                )
    return out


def _scaling(npc_row: pd.Series) -> tuple[float, str | None]:
    effects = table("SpEffectParam")
    rate, label = 1.0, None
    for col in (c for c in npc_row.index if c.startswith("spEffectID")):
        eff = int(npc_row[col])
        if eff <= 0 or eff not in effects.index:
            continue
        name = str(effects.at[eff, "Name"])
        if "Area Scaling" in name:
            rate *= float(effects.at[eff, "maxHpRate"])
            label = name
    return rate, label


def enemy(name: str, *, include_unscaled: bool = False) -> list[EnemyVariant]:
    """NpcParam rows whose name contains `name`, with area-scaled HP and drops."""
    npcs = table("NpcParam")
    needle = _norm(name)
    out = []
    for npc_id, row in npcs.iterrows():
        label = str(row["Name"]) if not pd.isna(row["Name"]) else ""
        if needle not in _norm(label):
            continue
        if not include_unscaled and "unscaled" in label.lower():
            continue
        rate, scaling = _scaling(row)
        lot = int(row["itemLotId_enemy"])
        out.append(
            EnemyVariant(
                id=int(npc_id),
                name=label,
                base_hp=int(row["hp"]),
                hp=math.floor(int(row["hp"]) * rate + 1e-9),
                area_scaling=scaling,
                runes=int(row["getSoul"]),
                stance=int(row["superArmorDurability"]),
                drops=drops(lot) if lot > 0 else [],
            )
        )
    return out


def boss_runes(name: str) -> list[tuple[str, int]]:
    """(GameAreaParam label, runes) for boss areas whose label contains name."""
    needle = _norm(name)
    out = []
    for _, row in table("GameAreaParam").iterrows():
        label = "" if pd.isna(row["Name"]) else str(row["Name"])
        if needle in _norm(label):
            out.append((label, int(row["bonusSoul_single"])))
    return out


# ----------------------------------------------------------------- criticals

# ThrowParam rows for the player (AtkChrId 0) attacking a character model (DefChrId) are
# numbered GG + model (4 digits) + 2 digits. The group GG says which kind of critical hit
# it is. This mapping is inferred, not documented: group 10 is the only one that uses the
# "_start" backstab range fields, and it is missing for enemies that can't be backstabbed
# (Margit 2130, Erdtree Burial Watchdog 4260, Mad Pumpkin Head 4340) while present for
# ones that can (Godrick Soldier 4311, Imp 3080). Bosses like Mohg (4800) only have 14.
CRITICAL_GROUPS = {
    10: "backstab",
    13: "riposte after a parry",
    14: "critical after a stance break",
}


def model_id(npc_id: int) -> int:
    """NpcParam IDs start with the character model: 43110010 -> c4311."""
    return npc_id // 10000


def criticals(npc_id: int) -> list[str]:
    """Critical hits the player can land on this NPC, from ThrowParam (see above).

    An empty list means ThrowParam has no player throws against this model at all.
    """
    throws = table("ThrowParam")
    rows = throws[(throws["AtkChrId"] == 0) & (throws["DefChrId"] == model_id(npc_id))]
    groups = {int(i) // 1_000_000 for i in rows["ID"]}
    return [label for g, label in CRITICAL_GROUPS.items() if g in groups]
