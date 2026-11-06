from pathlib import Path

import pytest

from pipeline.load_params import TABLES

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"


def csvs_present() -> bool:
    return all((PROCESSED_DIR / name).is_file() for name in TABLES)


@pytest.fixture(scope="session")
def param_db_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A throwaway SQLite database loaded from data/processed/ (once per test run)."""
    if not csvs_present():
        pytest.skip("param CSVs not found in data/processed/")
    from pipeline.load_params import load_params

    db = tmp_path_factory.mktemp("db") / "params.db"
    url = f"sqlite:///{db.as_posix()}"
    load_params(PROCESSED_DIR, url)
    return url
