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

Build the local SQLite database from the committed CSVs (re-run after updating `data/processed/`):

```bash
uv run python -m pipeline.load_params
```

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

Game data is extracted **offline** from a local copy of the game (version pinned above). Raw game files, the Smithbox project, and modding tool binaries stay on the developer's machine, never in this repo. Only param tables exported as CSV (numbers plus community row names) are committed to `data/processed/`. The game folder is only ever read: work happens on a copy of `regulation.bin`.

| CSVs | Source |
|---|---|
| EquipParamWeapon, ReinforceParamWeapon, CalcCorrectGraph, AttackElementCorrectParam | Smithbox Param Editor CSV export |
| BonfireWarpParam (sites of grace), BonfireWarpSubCategoryParam, ShopLineupParam, ShopLineupParam_Recipe, NpcParam, SpEffectParam, ItemLotParam_enemy, ItemLotParam_map, GameAreaParam, WorldMapPointParam, WorldMapPlaceNameParam, EquipParamGoods, EquipParamProtector, EquipParamAccessory, EquipParamGem, Magic, SwordArtsParam, EquipMtrlSetParam | WitchyBND + `pipeline/witchy_to_csv.py` |

To re-export with WitchyBND: copy `regulation.bin` out of the game folder, run `WitchyBND.exe -s regulation.bin`, then `WitchyBND.exe -s <Param>.param` for each param in the unpacked `regulation-bin` folder, and convert:

```
uv run python -m pipeline.witchy_to_csv <path>/regulation-bin BonfireWarpParam ShopLineupParam ...
```

Both routes give identical values; the four weapon tables were cross-checked value for value.
