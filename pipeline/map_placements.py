"""Extract enemy and item placements from Elden Ring map files (MSB) into one CSV.

The map files are exported from the packed game archives with Smithbox's file browser into a
Smithbox project folder outside the game directory, e.g.
C:\\EldenTools\\smithbox-project\\map\\mapstudio. Nothing here reads or writes the game folder.

    uv run python -m pipeline.map_placements <mapstudio dir> --work C:/EldenTools/maps

Steps: copy each base-game map file into the work folder, convert it with WitchyBND
(-> <name>-msb-dcx/ with one XML per part), then read:

- Part/Enemy: NPCParamID -> enemy name (NpcParam)
- Event/Treasure: ItemLotID on a corpse or chest asset -> items (ItemLotParam_map)
- Part/Asset: assets whose model has a pickUpItemLotParamId in AssetEnvironmentGeometryParam
  (gatherable plants such as Grave Glovewort) -> items

Output: data/processed/MapPlacements.csv, one row per placed thing.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import astuple, dataclass, fields
from pathlib import Path

from pipeline import guide_data as gd
from pipeline.load_params import PROCESSED_DIR

WITCHY = Path("C:/EldenTools/WitchyBND-3.0.1.0/WitchyBND.exe")
OUTPUT = PROCESSED_DIR / "MapPlacements.csv"
# Base-game legacy dungeons, catacombs, caves, tunnels, towers, and small open-world tiles.
# m61 (Shadow of the Erdtree) and the _99 / _01 / _02 variants are skipped.
MAP_RE = re.compile(r"^(m1\d_\d\d_00_00|m3\d_\d\d_00_00|m60_\d\d_\d\d_00)\.msb\.dcx$")
ASSET_RE = re.compile(r"^AEG(\d{3})_(\d{3})_")
BATCH = 20
BATCH_TIMEOUT = 600  # seconds


@dataclass
class Placement:
    map: str
    kind: str  # enemy | treasure | gather
    part: str  # MSB part name, e.g. c3080_9000 or AEG099_920_9001
    param_id: int  # NpcParam ID (enemy) or ItemLotParam_map ID (treasure, gather)
    name: str  # enemy name or item list, e.g. "Grave Glovewort [1]" / "Root Resin (x2)"
    x: float
    y: float
    z: float
    entity_id: int


def convert_maps(src: Path, work: Path) -> list[Path]:
    """Copy base-game map files to `work` and convert them. Returns the output folders."""
    work.mkdir(parents=True, exist_ok=True)
    todo = []
    for f in sorted(src.iterdir()):
        if not MAP_RE.match(f.name):
            continue
        out = work / f.name.replace(".msb.dcx", "-msb-dcx")
        if not out.is_dir():
            shutil.copy2(f, work / f.name)
            todo.append(work / f.name)
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        # No stdin: WitchyBND can wait for a keypress after an error ("PauseOnError").
        try:
            subprocess.run(
                [str(WITCHY), "-s", *map(str, batch)],
                cwd=work,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                timeout=BATCH_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print(f"WitchyBND timed out on {batch[0].name}..{batch[-1].name}")
        print(f"converted {min(i + BATCH, len(todo))}/{len(todo)}", flush=True)
    missing = [f.name for f in todo if not (work / f.name.replace(".msb.dcx", "-msb-dcx")).is_dir()]
    if missing:
        print(f"{len(missing)} map(s) failed to convert: {', '.join(missing[:10])}")
    return sorted(p for p in work.iterdir() if p.is_dir() and p.name.endswith("-msb-dcx"))


def _text(root: ET.Element, tag: str, default: str = "0") -> str:
    el = root.find(tag)
    return el.text.strip() if el is not None and el.text else default


def _pos(root: ET.Element) -> tuple[float, float, float]:
    p = root.find("Position")
    if p is None:
        return (0.0, 0.0, 0.0)
    return tuple(round(float(_text(p, axis)), 2) for axis in ("X", "Y", "Z"))  # type: ignore[return-value]


def _items_label(lot_id: int, *, names_only: bool = False) -> str:
    """'Root Resin (x2)' for pickups. Gatherables use names_only: their lots list the same
    plant at several yields (x1, x2, x3), so only the distinct names are kept."""
    lots = gd.table("ItemLotParam_map")
    if lot_id not in lots.index:
        return f"item lot {lot_id}"
    items = gd.lot_items(lots.loc[lot_id])
    if not names_only:
        # A pickup can chain into the following lot IDs: the Gatefront cellar chest is lot
        # 1042371010 (Whetstone Knife) and also gives 1042371011 (Ash of War: Storm Stomp).
        nxt = lot_id + 1
        while nxt in lots.index and nxt % 10 != 0:
            items += gd.lot_items(lots.loc[nxt])
            nxt += 1
    if names_only:
        return ", ".join(dict.fromkeys(n for n, _ in items)) or f"item lot {lot_id}"
    return ", ".join(f"{n} (x{q})" if q > 1 else n for n, q in items) or f"item lot {lot_id}"


def gather_lots() -> dict[int, int]:
    """Asset model ID (e.g. 99920 for AEG099_920) -> pickup item lot."""
    assets = gd.table("AssetEnvironmentGeometryParam")
    col = assets["pickUpItemLotParamId"]
    return {int(i): int(v) for i, v in col[col > 0].items()}


def read_map(folder: Path, npc_names: dict[int, str], gather: dict[int, int]) -> list[Placement]:
    map_id = folder.name.removesuffix("-msb-dcx")
    out: list[Placement] = []

    for f in sorted((folder / "Part" / "Enemy").glob("*.xml")):
        root = ET.parse(f).getroot()
        npc = int(_text(root, "NPCParamID"))
        if npc <= 0:
            continue
        out.append(
            Placement(
                map_id,
                "enemy",
                _text(root, "Name", f.stem),
                npc,
                npc_names.get(npc, f"NpcParam {npc}"),
                *_pos(root),
                int(_text(root, "EntityID")),
            )
        )

    asset_dir = folder / "Part" / "Asset"
    for f in sorted((folder / "Event" / "Treasure").glob("*.xml")):
        root = ET.parse(f).getroot()
        lot = int(_text(root, "ItemLotID"))
        part = _text(root, "TreasurePartName", "")
        if lot <= 0:
            continue
        asset = asset_dir / f"{part}.xml"
        aroot = ET.parse(asset).getroot() if asset.is_file() else root
        out.append(
            Placement(
                map_id,
                "treasure",
                part,
                lot,
                _items_label(lot),
                *_pos(aroot),
                int(_text(aroot, "EntityID")),
            )
        )

    for f in sorted(asset_dir.glob("*.xml")):
        m = ASSET_RE.match(f.stem)
        if not m:
            continue
        lot = gather.get(int(m.group(1)) * 1000 + int(m.group(2)))
        if not lot:
            continue
        root = ET.parse(f).getroot()
        out.append(
            Placement(
                map_id,
                "gather",
                f.stem,
                lot,
                _items_label(lot, names_only=True),
                *_pos(root),
                int(_text(root, "EntityID")),
            )
        )
    return out


def write_csv(rows: list[Placement], path: Path = OUTPUT) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow([f.name for f in fields(Placement)])
        w.writerows(astuple(r) for r in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mapstudio", type=Path, help="Folder with exported .msb.dcx files")
    parser.add_argument("--work", type=Path, default=Path("C:/EldenTools/maps"))
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    folders = convert_maps(args.mapstudio, args.work)
    npcs = gd.table("NpcParam")["Name"]
    npc_names = {
        int(i): gd.split_label(n)[1] or str(n) for i, n in npcs.items() if isinstance(n, str)
    }
    gather = gather_lots()
    rows = [p for folder in folders for p in read_map(folder, npc_names, gather)]
    write_csv(rows, args.out)
    kinds = {k: sum(r.kind == k for r in rows) for k in ("enemy", "treasure", "gather")}
    print(f"{len(folders)} maps -> {args.out}: {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
