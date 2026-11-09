"""Load exported param CSVs from data/processed/ into SQLite.

One table per CSV (all columns kept, unique index on ID), plus a load_meta
table recording the pinned game version and each source file's SHA-256.
Re-running replaces the tables.

Run from the project root:
    uv run python -m pipeline.load_params
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

PROCESSED_DIR = Path("data/processed")
GAME_VERSION = "1.17.2"
REGULATION_VERSION = "1.17.3"

# CSV file -> SQLite table name.
TABLES = {
    "EquipParamWeapon.csv": "equip_param_weapon",
    "ReinforceParamWeapon.csv": "reinforce_param_weapon",
    "CalcCorrectGraph.csv": "calc_correct_graph",
    "AttackElementCorrectParam.csv": "attack_element_correct_param",
    # Guide data, exported with pipeline.witchy_to_csv.
    "BonfireWarpParam.csv": "bonfire_warp_param",
    "ShopLineupParam.csv": "shop_lineup_param",
    "NpcParam.csv": "npc_param",
    "ItemLotParam_enemy.csv": "item_lot_param_enemy",
    "ItemLotParam_map.csv": "item_lot_param_map",
    "GameAreaParam.csv": "game_area_param",
    "WorldMapPointParam.csv": "world_map_point_param",
    "WorldMapPlaceNameParam.csv": "world_map_place_name_param",
    "BonfireWarpSubCategoryParam.csv": "bonfire_warp_sub_category_param",
    "EquipParamGoods.csv": "equip_param_goods",
    "EquipParamProtector.csv": "equip_param_protector",
    "EquipParamAccessory.csv": "equip_param_accessory",
    "EquipParamGem.csv": "equip_param_gem",
    "Magic.csv": "magic",
    "SwordArtsParam.csv": "sword_arts_param",
    "SpEffectParam.csv": "sp_effect_param",
    "ShopLineupParam_Recipe.csv": "shop_lineup_param_recipe",
    "EquipMtrlSetParam.csv": "equip_mtrl_set_param",
}
META_TABLE = "load_meta"


def find_csvs(processed_dir: Path = PROCESSED_DIR) -> list[Path]:
    """Return the CSV files in processed_dir."""
    return sorted(processed_dir.glob("*.csv"))


def make_engine(database_url: str) -> Engine:
    """Create an engine, making the folder for a file-based SQLite database."""
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_params(
    processed_dir: Path = PROCESSED_DIR, database_url: str | None = None
) -> dict[str, int]:
    """Load each param CSV into its own table. Returns {table: row count}."""
    if database_url is None:
        from backend.config import get_settings

        database_url = get_settings().database_url

    missing = [name for name in TABLES if not (processed_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing CSVs in {processed_dir}: {', '.join(missing)}")

    engine = make_engine(database_url)
    counts: dict[str, int] = {}
    meta_rows = [
        {"key": "game_version", "value": GAME_VERSION},
        {"key": "regulation_version", "value": REGULATION_VERSION},
        {"key": "loaded_at", "value": datetime.now(UTC).isoformat(timespec="seconds")},
    ]
    try:
        # One transaction: either every table loads or none change.
        with engine.begin() as conn:
            for csv_name, table in TABLES.items():
                path = processed_dir / csv_name
                df = pd.read_csv(path, low_memory=False)
                if "ID" not in df.columns:
                    raise ValueError(f"{csv_name} has no ID column")
                if df["ID"].duplicated().any():
                    raise ValueError(f"{csv_name} has duplicate IDs")
                df.to_sql(table, conn, if_exists="replace", index=False)
                conn.execute(text(f'CREATE UNIQUE INDEX "ix_{table}_ID" ON "{table}" ("ID")'))
                counts[table] = len(df)
                meta_rows.append({"key": f"sha256:{csv_name}", "value": _sha256(path)})
            pd.DataFrame(meta_rows).to_sql(META_TABLE, conn, if_exists="replace", index=False)
    finally:
        engine.dispose()
    return counts


if __name__ == "__main__":
    for table, count in load_params().items():
        print(f"{table}: {count} rows")
