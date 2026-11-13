from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text

from pipeline.load_params import (
    GAME_VERSION,
    META_TABLE,
    REGULATION_VERSION,
    TABLES,
    load_params,
)
from tests.conftest import PROCESSED_DIR


def test_tables_match_csv_row_counts(param_db_url: str) -> None:
    engine = create_engine(param_db_url)
    with engine.connect() as conn:
        for csv_name, table in TABLES.items():
            expected = len(pd.read_csv(PROCESSED_DIR / csv_name, low_memory=False))
            actual = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
            assert actual == expected, table
    engine.dispose()


def test_every_table_has_unique_id_index(param_db_url: str) -> None:
    engine = create_engine(param_db_url)
    insp = inspect(engine)
    for table in TABLES.values():
        indexes = insp.get_indexes(table)
        assert any(ix["unique"] and ix["column_names"] == ["ID"] for ix in indexes), table
    engine.dispose()


def test_meta_records_versions_and_hashes(param_db_url: str) -> None:
    engine = create_engine(param_db_url)
    with engine.connect() as conn:
        meta = dict(conn.execute(text(f'SELECT key, value FROM "{META_TABLE}"')).all())
    engine.dispose()
    assert meta["game_version"] == GAME_VERSION
    assert meta["regulation_version"] == REGULATION_VERSION
    for csv_name in TABLES:
        assert len(meta[f"sha256:{csv_name}"]) == 64


def test_reload_replaces_tables(tmp_path: Path) -> None:
    if not all((PROCESSED_DIR / n).is_file() for n in TABLES):
        pytest.skip("param CSVs not found in data/processed/")
    url = f"sqlite:///{(tmp_path / 'twice.db').as_posix()}"
    first = load_params(PROCESSED_DIR, url)
    second = load_params(PROCESSED_DIR, url)
    assert first == second


def test_missing_csv_raises(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'none.db').as_posix()}"
    with pytest.raises(FileNotFoundError):
        load_params(tmp_path, url)


def test_creates_database_folder(tmp_path: Path) -> None:
    if not all((PROCESSED_DIR / n).is_file() for n in TABLES):
        pytest.skip("param CSVs not found in data/processed/")
    db = tmp_path / "nested" / "dir" / "p.db"
    load_params(PROCESSED_DIR, f"sqlite:///{db.as_posix()}")
    assert db.is_file()
