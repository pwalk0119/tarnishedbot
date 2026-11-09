"""Convert WitchyBND param XML into CSVs shaped like Smithbox's exports.

WitchyBND serializes a .param file to XML with one <field> per column and one
<row> per param row. Rows leave out cells that equal the field's default value,
so this fills them back in. Padding fields (dummy8) are dropped. Row names come
from the Paramdex names Witchy embeds (paramdexName) and go in the Name column.

Typical use, with a copy of regulation.bin unpacked by WitchyBND outside the
game folder:
    uv run python -m pipeline.witchy_to_csv C:/EldenTools/regulation/1.17.3/regulation-bin \
        BonfireWarpParam ShopLineupParam NpcParam
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

from pipeline.load_params import PROCESSED_DIR

SKIP_TYPES = {"dummy8"}


def read_param_xml(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return (columns, rows) from one WitchyBND param XML file.

    Columns are ID, Name, then every non-padding field in file order.
    """
    root = ET.parse(path).getroot()
    fields = [
        (f.get("name", ""), f.get("defaultValue", ""))
        for f in root.iter("field")
        if f.get("type") not in SKIP_TYPES
    ]
    if not fields:
        raise ValueError(f"{path} has no <field> definitions; is it a WitchyBND param XML?")
    columns = ["ID", "Name", *(name for name, _ in fields)]

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in root.iter("row"):
        row_id = row.get("id")
        if row_id is None:
            raise ValueError(f"{path}: a <row> has no id")
        if row_id in seen:
            raise ValueError(f"{path}: duplicate row id {row_id}")
        seen.add(row_id)
        out = {"ID": row_id, "Name": row.get("paramdexName", "")}
        for name, default in fields:
            out[name] = row.get(name, default)
        rows.append(out)
    return columns, rows


def write_csv(columns: list[str], rows: Iterable[dict[str, str]], out_path: Path) -> str:
    """Write rows to out_path and return the file's SHA-256."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(out_path.read_bytes()).hexdigest()


def convert(xml_dir: Path, params: Iterable[str], out_dir: Path = PROCESSED_DIR) -> dict[str, int]:
    """Convert <xml_dir>/<Param>.param.xml to <out_dir>/<Param>.csv. Returns row counts."""
    counts: dict[str, int] = {}
    for param in params:
        xml_path = xml_dir / f"{param}.param.xml"
        columns, rows = read_param_xml(xml_path)
        sha = write_csv(columns, rows, out_dir / f"{param}.csv")
        counts[param] = len(rows)
        print(f"{param}: {len(rows)} rows, {len(columns)} columns, sha256 {sha[:16]}…")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("xml_dir", type=Path, help="Folder with <Param>.param.xml files")
    parser.add_argument("params", nargs="+", help="Param names, e.g. BonfireWarpParam")
    parser.add_argument("--out", type=Path, default=PROCESSED_DIR, help="Output folder")
    args = parser.parse_args()
    convert(args.xml_dir, args.params, args.out)


if __name__ == "__main__":
    main()
