"""Stat buffs: lookup from SpEffectParam and their effect on attack rating."""

import json

import pytest

from backend.chat import run_tool
from tools.attack_rating import calculate_ar, data_dir
from tools.buffs import find_buff

needs_data = pytest.mark.skipif(
    not (data_dir() / "SpEffectParam.csv").is_file(), reason="SpEffectParam.csv not found"
)
pytestmark = needs_data

WRETCH = {"str": 10, "dex": 10, "int": 10, "fai": 10, "arc": 10}
KNOTS = ["Strength-knot Crystal Tear", "Dexterity-knot Crystal Tear"]


# ------------------------------------------------------------------ lookup


@pytest.mark.parametrize(
    ("name", "stats"),
    [
        ("Strength-knot Crystal Tear", {"str": 10}),
        ("Dexterity-knot Crystal Tear", {"dex": 10}),
        ("Radagon's Soreseal", {"str": 5, "dex": 5}),
        ("Godrick's Great Rune", {"str": 5, "dex": 5, "int": 5, "fai": 5, "arc": 5}),
        ("Imp Head (Fanged)", {"str": 2}),
    ],
)
def test_find_buff_reads_stat_bonus(name: str, stats: dict[str, int]) -> None:
    assert find_buff(name).stats == stats


def test_find_buff_ignores_case_and_apostrophes() -> None:
    assert find_buff("radagons soreseal").name == "Radagon's Soreseal"


def test_damage_only_buff_has_no_stats() -> None:
    buff = find_buff("Flame-Shrouding Cracked Tear")
    assert buff.stats == {}
    assert buff.duration_s == 180


def test_unknown_buff_suggests_close_names() -> None:
    with pytest.raises(KeyError, match="Strength-knot Crystal Tear"):
        find_buff("Strenght knot tear")


# ------------------------------------------------------------------ AR


def test_knot_tears_alone_dont_meet_nagakiba_dex() -> None:
    # The case from the first live test: 10 + 10 Dex is still 2 short of 22,
    # so the penalty stays even with both tears and two-handing.
    r = calculate_ar("Nagakiba", 17, None, WRETCH, two_handed=True, buffs=KNOTS)
    assert r.effective_stats["str"] == 30  # (10 + 10) x 1.5
    assert r.effective_stats["dex"] == 20
    assert r.unmet_requirements == ["dex"]
    assert r.physical.bonus < 0
    assert [b.name for b in r.buffs] == KNOTS


def test_buff_counts_toward_requirements() -> None:
    plain = calculate_ar("Nagakiba", 17, None, {**WRETCH, "dex": 12}, two_handed=True)
    buffed = calculate_ar("Nagakiba", 17, None, {**WRETCH, "dex": 12}, two_handed=True, buffs=KNOTS)
    # Without the tears, Str is 15 two-handed (needs 18) and Dex 12 (needs 22).
    assert plain.unmet_requirements == ["str", "dex"]
    assert buffed.unmet_requirements == []
    assert buffed.total > plain.total


def test_buffs_match_raising_the_stats_directly() -> None:
    buffed = calculate_ar("Longsword", 10, None, WRETCH, buffs=["Radagon's Soreseal"])
    raised = calculate_ar("Longsword", 10, None, {**WRETCH, "str": 15, "dex": 15})
    assert buffed.total == raised.total


def test_two_handing_multiplies_buffed_strength() -> None:
    r = calculate_ar("Longsword", 0, None, WRETCH, two_handed=True, buffs=[KNOTS[0]])
    assert r.effective_stats["str"] == 30


def test_same_buff_twice_counts_once() -> None:
    r = calculate_ar("Longsword", 0, None, WRETCH, buffs=[KNOTS[0], KNOTS[0]])
    assert r.effective_stats["str"] == 20
    assert len(r.buffs) == 1


def test_buffed_stats_cap_at_99() -> None:
    r = calculate_ar("Longsword", 0, None, {**WRETCH, "str": 97}, buffs=["Radagon's Soreseal"])
    assert r.effective_stats["str"] == 99
    assert any("capped at 99" in w for w in r.warnings)


def test_damage_only_buff_warns_and_changes_nothing() -> None:
    plain = calculate_ar("Longsword", 0, None, WRETCH)
    r = calculate_ar("Longsword", 0, None, WRETCH, buffs=["Flame-Shrouding Cracked Tear"])
    assert r.total == plain.total
    assert any("doesn't raise any stats" in w for w in r.warnings)


# ------------------------------------------------------------------ chat tool


def test_run_tool_passes_buffs() -> None:
    args = {
        "weapon": "Nagakiba",
        "affinity": "Standard",
        "upgrade_level": 17,
        "strength": 10,
        "dexterity": 10,
        "intelligence": 10,
        "faith": 10,
        "arcane": 10,
        "two_handed": True,
        "buffs": KNOTS,
    }
    out, is_error = run_tool("calculate_attack_rating", args)
    assert not is_error
    assert json.loads(out)["unmet_requirements"] == ["dex"]


def test_run_tool_unknown_buff_is_an_error() -> None:
    args = {
        "weapon": "Longsword",
        "affinity": "Standard",
        "upgrade_level": 0,
        "strength": 10,
        "dexterity": 10,
        "intelligence": 10,
        "faith": 10,
        "arcane": 10,
        "two_handed": False,
        "buffs": ["Mystery Tear"],
    }
    out, is_error = run_tool("calculate_attack_rating", args)
    assert is_error and "Unknown buff" in out
