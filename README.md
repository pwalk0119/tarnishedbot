# TarnishedBot

TarnishedBot is an AI assistant for Elden Ring, scoped to the Limgrave region. It answers questions about weapons, upgrades, damage scaling, and the area itself. It uses the Claude API with tool calling, so numeric questions such as attack rating are computed from real game data instead of guessed. It's a CS-460 Software Design capstone project, due December 13, 2026.

**Architecture:** Streamlit frontend → FastAPI backend → Claude API (Anthropic SDK, tool calling), backed by SQLite + SQLAlchemy (weapon/upgrade/scaling tables) and ChromaDB (searchable guide text).

## Pinned game version

| | Version |
|---|---|
| Elden Ring app | 1.17.2 |
| Regulation (calibrations) | 1.17.3 |
| DLC | None (base game only) |

All game data and test values must come from this version.

## Project layout

```
backend/          FastAPI app (main.py, config.py)
frontend/         Streamlit chat UI (app.py)
tools/            Tools the bot can call (attack-rating calculator, etc.)
pipeline/         CSV -> SQLite loader
data/raw/         Local only, gitignored: Smithbox project and game extracts
data/processed/   Committed: exported CSV tables (numbers only)
tests/            pytest suite; tests/fixtures/ for in-game check values
docs/             Design docs
```

## Setup

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 automatically if needed.

```bash
uv sync
copy .env.example .env      # Windows (macOS/Linux: cp .env.example .env)
```

Then put your Anthropic API key in `.env` as `ANTHROPIC_API_KEY=...`.

## Running

From the project root, in two terminals:

```bash
uv run uvicorn backend.main:app --reload
```

```bash
uv run streamlit run frontend/app.py
```

The backend serves on http://localhost:8000 (health check: `/health`, API docs: `/docs`). The frontend opens at http://localhost:8501.

## Tests and linting

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

GitHub Actions runs the same commands on every push and pull request. Tests never call the Claude API and don't download embedding models.

## Game data

Game data is extracted **offline** with Smithbox from a local copy of the game (version pinned above). Raw game files, the Smithbox project, and modding tool binaries stay on the developer's machine under `data/raw/`, which is gitignored. Only numeric tables exported as CSV are committed to `data/processed/`.
