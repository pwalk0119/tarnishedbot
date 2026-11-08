"""Tests for tools.attack_rating.

Data tests need the four param CSVs (TARNISHEDBOT_DATA_DIR or data/processed/)
and skip when they're missing. In-game checks live in tests/fixtures/ar_checks.csv
(same columns as ar_checks.example.csv); every row must match exactly.
"""

import csv
import math
from pathlib import Path

import pytest

from tools.attack_rating import (
    DAMAGE_TYPES,
    Stats,
    calculate_ar,
    curve_value,
    data_available,
    load_tables,
    normalize_affinity,
    resolve_weapon_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ar_checks.csv"

needs_data = pytest.mark.skipif(not data_available(), reason="param CSVs not found")

# ----------------------------------------------------------------- curve (no data)

# Hand-picked curve: breakpoints 1/20/60/80/150, grow 0/35/75/90/110,
# exponent 1.2 on stage 0, -1.2 on stage 1, linear after.
BP = [1.0, 20.0, 60.0, 80.0, 150.0]
GROW = [0.0, 35.0, 75.0, 90.0, 110.0]
ADJ = [1.2, -1.2, 1.0, 1.0, 1.0]


@pytest.mark.parametrize(
    ("stat", "expected"),
    [
        (1, 0.0),
        (20, 0.35),
        (60, 0.75),
        (80, 0.90),
        (150, 1.10),
    ],
)
def test_curve_hits_breakpoints(stat: float, expected: float) -> None:
    assert curve_value(stat, BP, GROW, ADJ) == pytest.approx(expected)


def test_curve_clamps_outside_range() -> None:
    assert curve_value(0, BP, GROW, ADJ) == pytest.approx(0.0)
    assert curve_value(200, BP, GROW, ADJ) == pytest.approx(1.10)


def test_curve_positive_exponent() -> None:
    # Stage 0, halfway: growth = 0.5 ** 1.2
    expected = (0 + 35 * 0.5**1.2) / 100
    assert curve_value(10.5, BP, GROW, ADJ) == pytest.approx(expected)


def test_curve_negative_exponent() -> None:
    # Stage 1, halfway: growth = 1 - (1 - 0.5) ** 1.2
    expected = (35 + 40 * (1 - 0.5**1.2)) / 100
    assert curve_value(40, BP, GROW, ADJ) == pytest.approx(expected)


def test_curve_linear_stage() -> None:
    assert curve_value(70, BP, GROW, ADJ) == pytest.approx(0.825)


def test_curve_rejects_wrong_length() -> None:
    with pytest.raises(ValueError):
        curve_value(10, BP[:4], GROW, ADJ)


# ------------------------------------------------------------ inputs (no data)


def test_affinity_aliases() -> None:
    assert normalize_affinity("bloody") == "Blood"
    assert normalize_affinity("flame art") == "Flame Art"
    with pytest.raises(ValueError):
        normalize_affinity("Shiny")


def test_stats_accept_short_keys_and_validate() -> None:
    stats = Stats(**{"str": 10, "dex": 12, "int": 9, "fai": 8, "arc": 7})
    assert stats.short() == {"str": 10, "dex": 12, "int": 9, "fai": 8, "arc": 7}
    with pytest.raises(ValueError):
        Stats(**{"str": 100, "dex": 1, "int": 1, "fai": 1, "arc": 1})


# ------------------------------------------------------------------ data tests

STATS_10 = {"str": 10, "dex": 10, "int": 10, "fai": 10, "arc": 10}


@needs_data
@pytest.mark.parametrize(
    ("weapon", "affinity", "expected_id", "expected_affinity"),
    [
        ("Longsword", None, 2000000, "Standard"),
        ("Longsword", "Heavy", 2000100, "Heavy"),
        ("Heavy Longsword", None, 2000100, "Heavy"),
        ("heavy longsword", "heavy", 2000100, "Heavy"),
        ("Bloody Longsword", None, 2001100, "Blood"),
        ("Longsword", "Blood", 2001100, "Blood"),
        ("Longsword", "Bloody", 2001100, "Blood"),
        (2000800, None, 2000800, "Magic"),
        ("2000000", "Keen", 2000200, "Keen"),
    ],
)
def test_resolve_weapon_id(
    weapon: str | int, affinity: str | None, expected_id: int, expected_affinity: str
) -> None:
    assert resolve_weapon_id(weapon, affinity, load_tables()) == (expected_id, expected_affinity)


@needs_data
def test_resolve_rejects_conflicting_affinity() -> None:
    with pytest.raises(ValueError):
        resolve_weapon_id("Heavy Longsword", "Keen", load_tables())


@needs_data
def test_resolve_unknown_weapon() -> None:
    with pytest.raises(KeyError):
        resolve_weapon_id("Definitely Not A Sword", None, load_tables())


@needs_data
def test_upgrade_level_past_family_max_is_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_ar("Longsword", 26, None, STATS_10)


@needs_data
def test_unmet_requirement_applies_penalty() -> None:
    # Longsword needs 10 Str; physical scales with Str, so 9 Str => bonus = base * -0.4.
    result = calculate_ar("Longsword", 0, None, {**STATS_10, "str": 9})
    assert result.physical.bonus_exact == pytest.approx(result.physical.base_exact * -0.4)


@needs_data
def test_unmet_requirement_is_reported() -> None:
    # Blasphemous Blade needs 15 Dex. Dex scales physical only, so fire keeps its scaling.
    stats = {"str": 33, "dex": 10, "int": 10, "fai": 65, "arc": 10}
    result = calculate_ar("Blasphemous Blade", 5, None, stats, two_handed=True)
    assert result.requirements["dex"] == 15
    assert result.unmet_requirements == ["dex"]
    assert result.physical.penalized_by == ["dex"]
    assert result.physical.bonus < 0
    assert result.fire.penalized_by == []
    assert result.fire.bonus > 0
    assert any("Dexterity 10" in w and "requirement of 15" in w for w in result.warnings)


@needs_data
def test_met_requirements_report_nothing() -> None:
    result = calculate_ar("Longsword", 0, None, STATS_10)
    assert result.unmet_requirements == []
    assert all(not result.line(d).penalized_by for d in DAMAGE_TYPES)
    assert not any("requirement" in w for w in result.warnings)


@needs_data
def test_two_handing_counts_toward_strength_requirement() -> None:
    # 9 Str one-handed misses the Longsword's 10; two-handed it becomes 13.
    one = calculate_ar("Longsword", 0, None, {**STATS_10, "str": 9})
    two = calculate_ar("Longsword", 0, None, {**STATS_10, "str": 9}, two_handed=True)
    assert one.unmet_requirements == ["str"]
    assert two.unmet_requirements == []
    assert two.physical.bonus > 0


@needs_data
def test_two_handing_raises_effective_strength() -> None:
    result = calculate_ar("Longsword", 0, None, {**STATS_10, "str": 20}, two_handed=True)
    assert result.effective_stats["str"] == 30
    capped = calculate_ar("Longsword", 0, None, {**STATS_10, "str": 99}, two_handed=True)
    assert capped.effective_stats["str"] == 148


@needs_data
def test_displayed_values_are_floored() -> None:
    result = calculate_ar("Longsword", 10, None, {**STATS_10, "str": 18, "dex": 14})
    for dmg in DAMAGE_TYPES:
        line = result.line(dmg)
        assert line.base == math.floor(line.base_exact + 1e-9)
        assert line.bonus == math.floor(line.bonus_exact + 1e-9)


# ------------------------------------------------------- in-game fixture checks


def _fixture_rows() -> list[dict[str, str]]:
    if not FIXTURE.is_file():
        return []
    with FIXTURE.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


ROWS = _fixture_rows()


@pytest.mark.skipif(not ROWS, reason=f"no in-game check values in {FIXTURE.name} yet")
@needs_data
@pytest.mark.parametrize(
    "row",
    ROWS,
    ids=[f"{r['weapon']}+{r['upgrade']}-{r['affinity'] or 'Standard'}" for r in ROWS],
)
def test_matches_in_game_values(row: dict[str, str]) -> None:
    result = calculate_ar(
        row["weapon"],
        int(row["upgrade"]),
        row["affinity"] or None,
        {k: int(row[k]) for k in ("str", "dex", "int", "fai", "arc")},
        two_handed=_truthy(row["two_handed"]),
    )
    actual = {}
    expected = {}
    for dmg in DAMAGE_TYPES:
        for part in ("base", "bonus"):
            actual[f"{dmg}_{part}"] = getattr(result.line(dmg), part)
            expected[f"{dmg}_{part}"] = int(row[f"{dmg}_{part}"])
    assert actual == expected
