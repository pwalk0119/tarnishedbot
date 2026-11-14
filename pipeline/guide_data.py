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
# Damage type -> NpcParam multiplier column. "Standard" is plain physical damage.
DAMAGE_RATE_COLS = {
    "standard": "neutralDamageCutRate",
    "slash": "slashDamageCutRate",
    "strike": "blowDamageCutRate",
    "pierce": "thrustDamageCutRate",
    "magic": "magicDamageCutRate",
    "fire": "fireDamageCutRate",
    "lightning": "thunderDamageCutRate",
    "holy": "darkDamageCutRate",
}
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
    map_placements.cache_clear()
    located_graces.cache_clear()


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
    map_id: str = ""  # e.g. m60_42_37_00 (open world tile) or m30_02_00_00 (catacomb)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    entity_id: int = 0  # the grace object's EntityID in the map file


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
            map_id = (
                f"m{int(row['areaNo']):02d}_{int(row['gridXNo']):02d}_{int(row['gridZNo']):02d}_00"
            )
            out.append(
                Grace(
                    int(row["ID"]),
                    name,
                    group_name,
                    map_id,
                    float(row["posX"]),
                    float(row["posY"]),
                    float(row["posZ"]),
                    int(row["bonfireEntityId"]),
                )
            )
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
    # Damage multipliers by type (1.2 = takes 20% more), from NpcParam *DamageCutRate.
    damage_rates: dict[str, float] = field(default_factory=dict)

    def weaknesses(self) -> tuple[list[str], list[str]]:
        """(weak to, resists), e.g. (["fire +20%"], ["lightning -20%"])."""
        weak = [f"{k} +{round((r - 1) * 100)}%" for k, r in self.damage_rates.items() if r > 1]
        strong = [f"{k} -{round((1 - r) * 100)}%" for k, r in self.damage_rates.items() if r < 1]
        return weak, strong


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
                damage_rates={k: round(float(row[c]), 2) for k, c in DAMAGE_RATE_COLS.items()},
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


# Human NPC rows (NpcParam IDs with 9 digits, e.g. Patches 523090010) use model c0000.
HUMAN_NPC_MIN_ID = 100_000_000


def model_id(npc_id: int) -> int:
    """NpcParam IDs start with the character model: 43110010 -> c4311."""
    return npc_id // 10000


def criticals(npc_id: int) -> list[str]:
    """Critical hits the player can land on this NPC, from ThrowParam (see above).

    An empty list means ThrowParam has no player throws against this model at all.
    """
    if npc_id >= HUMAN_NPC_MIN_ID:
        # Human NPCs (Patches, invaders such as Nerijus) share the player model c0000. Its
        # ThrowParam rows include 10/11 (riposte) and 20 (backstab, same animation as group
        # 10). Like players, they can't be stance-broken into a critical (confirmed on
        # Patches); breaking their guard opens them to a riposte instead.
        rows = table("ThrowParam")
        rows = rows[(rows["AtkChrId"] == 0) & (rows["DefChrId"] == 0)]
        ids = {int(i) for i in rows["ID"]}
        found = {
            "backstab": 20 in ids,
            "riposte after a parry or guard break": bool({10, 11} & ids),
        }
        return [label for label, ok in found.items() if ok]
    throws = table("ThrowParam")
    rows = throws[(throws["AtkChrId"] == 0) & (throws["DefChrId"] == model_id(npc_id))]
    groups = {int(i) // 1_000_000 for i in rows["ID"]}
    return [label for g, label in CRITICAL_GROUPS.items() if g in groups]


# ------------------------------------------------------------------- areas

# Label groups that don't contain the area's own name.
AREA_LABEL_ALIASES = {
    "stormveil castle": ["ld stormveil"],
    "stranded graveyard": ["ld stranded graveyard"],
}
IN_SCOPE_SCALING = ("Stranded Graveyard", "Limgrave", "Weeping Peninsula", "Stormveil Castle")


@dataclass
class AreaLot:
    lot_id: int
    label: str  # full Paramdex label, e.g. "[Limgrave - Stormfoot Catacombs] Root Resin"
    items: list[tuple[str, int]]  # (name, quantity)


@dataclass
class AreaBoss:
    label: str
    name: str
    runes: int
    variants: list[EnemyVariant] = field(default_factory=list)


@dataclass
class AreaChecklist:
    area: str
    graces: list[Grace]
    pickups: list[AreaLot]  # one-time map pickups (corpses, chests)
    boss_drops: list[AreaLot]
    bosses: list[AreaBoss]


def _matches_area(group: str, area: str) -> bool:
    g, a = _norm(group), _norm(area)
    return bool(a) and (a in g or any(alias in g for alias in AREA_LABEL_ALIASES.get(a, [])))


def lot_items(row: pd.Series) -> list[tuple[str, int]]:
    out = []
    for i in range(1, 9):
        kind = LOT_CATEGORY.get(int(row[f"lotItemCategory{i:02d}"]))
        item_id = int(row[f"lotItemId{i:02d}"])
        if kind and item_id > 0:
            out.append((item_name(kind, item_id), max(int(row[f"lotItemNum{i:02d}"]), 1)))
    return out


def area_checklist(area: str) -> AreaChecklist:
    """Everything the params know about one area, keyed off Paramdex labels.

    Pickups are one-time map item lots. Gatherable plants, enemy placements, levers and
    positions aren't in the params (they're in the map files).
    """
    lots = table("ItemLotParam_map")
    pickups, boss_drops = [], []
    for lot_id, name in lots["Name"].items():
        group, _ = split_label(name)
        if not _matches_area(group, area):
            continue
        lot = AreaLot(int(lot_id), str(name), lot_items(lots.loc[lot_id]))
        # Boss lots are labeled "[<Area> - <Boss>]"; pickups "[<Region> - <Area>]".
        (boss_drops if _norm(group).startswith(_norm(area)) else pickups).append(lot)

    bosses = []
    for _, row in table("GameAreaParam").iterrows():
        group, boss = split_label(row["Name"])
        if boss and _matches_area(group, area):
            variants = [
                v
                for v in enemy(boss)
                if v.area_scaling and any(s in v.area_scaling for s in IN_SCOPE_SCALING)
            ]
            bosses.append(AreaBoss(str(row["Name"]), boss, int(row["bonusSoul_single"]), variants))

    area_graces = [
        g for g in graces() if _norm(area) in _norm(g.name) or _norm(area) == _norm(g.group)
    ]
    return AreaChecklist(area, area_graces, pickups, boss_drops, bosses)


# -------------------------------------------------------------- map placements

# Built by pipeline.map_placements from the exported map files (MSB).
PLACEMENTS_CSV = "MapPlacements.csv"
TILE_SIZE = 256.0  # open-world tiles m60_XX_ZZ_00 are 256 m squares
DEFAULT_RADIUS = 150.0
DEFAULT_MAX_HEIGHT = 30.0
# Map "enemies" that are game system objects, not characters.
SYSTEM_NPCS = {"BuddyStone", "Talk Dummy", "Bonfire"}
GRACE_NPC_PARAM = 10000000  # the "Bonfire" object placed at each site of grace
GRACE_ENTITY_OFFSET = 1000
COMPASS = ("E", "NE", "N", "NW", "W", "SW", "S", "SE")


@dataclass
class MapPlacement:
    map: str
    kind: str  # enemy | treasure | gather
    part: str
    param_id: int
    name: str
    x: float
    y: float
    z: float
    grace: str = ""  # nearest site of grace
    grace_dist: float = 0.0  # metres, horizontal
    region: str = ""  # that grace's group, e.g. Limgrave


def placements_available(data_dir: Path = PROCESSED_DIR) -> bool:
    return (data_dir / PLACEMENTS_CSV).is_file()


def world_xz(map_id: str, x: float, z: float) -> tuple[float, float] | None:
    """Open-world tile coordinates -> world coordinates. None for dungeon maps."""
    m = re.match(r"^m60_(\d\d)_(\d\d)_00$", map_id)
    if not m:
        return None
    return int(m.group(1)) * TILE_SIZE + x, int(m.group(2)) * TILE_SIZE + z


def _grace_xz(g: Grace) -> tuple[float, float]:
    return world_xz(g.map_id, g.x, g.z) or (g.x, g.z)


def dungeon_map_of(grace_id: int) -> str | None:
    """Dungeon graces encode their map in the ID: 300200 -> m30_02_00_00 (Stormfoot)."""
    s = str(grace_id)
    if len(s) == 6 and 10 <= int(s[:2]) < 60:
        return f"m{s[:2]}_{s[2:4]}_00_00"
    return None


@cache
def located_graces(data_dir: Path = PROCESSED_DIR) -> list[Grace]:
    """graces(), with dungeon graces moved to their dungeon map.

    Some dungeon graces (e.g. Murkwater Cave, 310000) carry open-world coordinates in
    BonfireWarpParam: the spot above the cave. Their real position is the grace object in
    the dungeon's map file, found by bonfireEntityId.
    """
    df = pd.read_csv(data_dir / PLACEMENTS_CSV)
    objects = {
        int(r.entity_id): r
        for r in df[df["param_id"] == GRACE_NPC_PARAM].itertuples(index=False)
        if int(r.entity_id) > 0
    }
    out = []
    for g in graces():
        dungeon = dungeon_map_of(g.id)
        # The grace asset is <id>1950; the Bonfire character beside it is <id>0950.
        obj = objects.get(g.entity_id) or objects.get(g.entity_id - GRACE_ENTITY_OFFSET)
        if dungeon and dungeon != g.map_id and obj is not None and str(obj.map) == dungeon:
            g = Grace(
                g.id, g.name, g.group, dungeon, float(obj.x), float(obj.y), float(obj.z),
                g.entity_id,
            )  # fmt: skip
        out.append(g)
    return out


@cache
def map_placements(data_dir: Path = PROCESSED_DIR) -> list[MapPlacement]:
    """Every placed enemy, corpse/chest pickup and gatherable, tagged with its nearest grace.

    Dungeon placements use the graces inside the same map; open-world ones use every
    open-world grace, by world position.
    """
    df = pd.read_csv(data_dir / PLACEMENTS_CSV)
    all_graces = located_graces(data_dir)
    open_world = [g for g in all_graces if g.map_id.startswith("m60_")]
    by_map: dict[str, list[Grace]] = {}
    for g in all_graces:
        by_map.setdefault(g.map_id, []).append(g)

    out = []
    for r in df.itertuples(index=False):
        p = MapPlacement(
            str(r.map), str(r.kind), str(r.part), int(r.param_id), str(r.name),
            float(r.x), float(r.y), float(r.z),
        )  # fmt: skip
        pos = world_xz(p.map, p.x, p.z)
        candidates = open_world if pos else by_map.get(p.map, [])
        pos = pos or (p.x, p.z)
        best = min(
            candidates,
            key=lambda g: math.dist(pos, _grace_xz(g)),
            default=None,
        )
        if best:
            p.grace, p.region = best.name, best.group
            p.grace_dist = round(math.dist(pos, _grace_xz(best)), 1)
        out.append(p)
    return out


def relative_to(p: MapPlacement, g: Grace) -> str:
    """'40 m NE, 6 m below' from grace g to placement p (same map or both open world)."""
    gx, gz = _grace_xz(g)
    px, pz = world_xz(p.map, p.x, p.z) or (p.x, p.z)
    dx, dz, dy = px - gx, pz - gz, p.y - g.y
    dist = math.hypot(dx, dz)
    # North is +Z, east is +X: Church of Elleh sits at -X/+Z from The First Step (northwest),
    # and Gatefront's tile (gridZ 37) is north of theirs (gridZ 36).
    angle = math.degrees(math.atan2(dz, dx)) % 360
    direction = COMPASS[int((angle + 22.5) // 45) % 8]
    height = f", {abs(dy):.0f} m {'above' if dy > 0 else 'below'}" if abs(dy) >= 3 else ""
    return f"{dist:.0f} m {direction}{height}"


def tile_event_lots(map_id: str) -> list[AreaLot]:
    """Map item lots that event scripts award in an open-world tile, e.g. the Troll Carriage
    chests near Gatefront (942370060, 942370070 for m60_42_37_00). They have no position."""
    m = re.match(r"^m60_(\d\d)_(\d\d)_00$", map_id)
    if not m:
        return []
    prefix = f"9{m.group(1)}{m.group(2)}"
    lots = table("ItemLotParam_map")
    return [
        AreaLot(int(i), str(lots.at[i, "Name"]), lot_items(lots.loc[i]))
        for i in lots.index
        if len(str(i)) == 9 and str(i).startswith(prefix)
    ]


def area_placements(
    area: str,
    radius: float = DEFAULT_RADIUS,
    max_height: float = DEFAULT_MAX_HEIGHT,
    data_dir: Path = PROCESSED_DIR,
) -> tuple[Grace | None, list[MapPlacement]]:
    """Placements for an area: its whole map for dungeons, or, in the open world, within
    `radius` metres of the area's grace and `max_height` metres above or below it (so cliff
    tops like Stormhill above Gatefront stay out). Returns (anchor grace, placements)."""
    needle = _norm(area)
    candidates = [g for g in located_graces(data_dir) if g.map_id]
    anchor = next((g for g in candidates if _norm(g.name) == needle), None) or next(
        (g for g in candidates if needle in _norm(g.name) or _norm(g.name) in needle), None
    )
    if anchor is None:
        return None, []
    rows = map_placements(data_dir)
    if not anchor.map_id.startswith("m60_"):
        return anchor, [p for p in rows if p.map == anchor.map_id]
    gx, gz = _grace_xz(anchor)
    near = []
    for p in rows:
        pos = world_xz(p.map, p.x, p.z)
        if pos and math.dist(pos, (gx, gz)) <= radius and abs(p.y - anchor.y) <= max_height:
            near.append(p)
    return anchor, near
