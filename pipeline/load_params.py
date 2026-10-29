"""Load exported param CSVs from data/processed/ into SQLite.

Stub for now. Run from the project root:
    uv run python -m pipeline.load_params
"""

from pathlib import Path

PROCESSED_DIR = Path("data/processed")


def find_csvs(processed_dir: Path = PROCESSED_DIR) -> list[Path]:
    """Return the CSV files that would be loaded."""
    return sorted(processed_dir.glob("*.csv"))


def load_params(processed_dir: Path = PROCESSED_DIR, database_url: str | None = None) -> None:
    """Load each CSV into a SQLite table (one table per param). Not implemented yet."""
    # TODO: read each CSV with pandas and write it with SQLAlchemy (df.to_sql).
    raise NotImplementedError("load_params is a stub")


if __name__ == "__main__":
    csvs = find_csvs()
    print(f"Found {len(csvs)} CSV file(s) in {PROCESSED_DIR}/")
    for path in csvs:
        print(f"  {path.name}")
