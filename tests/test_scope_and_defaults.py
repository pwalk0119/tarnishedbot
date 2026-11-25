"""DLC weapons are refused, and unspecified stats get the documented defaults."""

import json

import pytest

from backend.chat import run_tool
from tools.attack_rating import calculate_ar, data_available, lowest_starting_stats
from tools.stance import weapon_stance
from tools.weapon_lookup import lookup_weapon

pytestmark = pytest.mark.skipif(not data_available(), reason="param CSVs not found")

NONE = {"str": None, "dex": None, "int": None, "fai": None, "arc": None}


# ------------------------------------------------------------------ DLC


def test_calculator_refuses_dlc_weapon() -> None:
    with pytest.raises(ValueError, match="DLC"):
        calculate_ar("Milady", 0, None, {"str": 20, "dex": 20, "int": 10, "fai": 10, "arc": 10})


def test_lookup_refuses_dlc_weapon(param_db_url: str) -> None:
    with pytest.raises(ValueError, match="DLC"):
        lookup_weapon("Star-Lined Sword", None, 0, database_url=param_db_url)


def test_stance_refuses_dlc_weapon() -> None:
    with pytest.raises(ValueError, match="DLC"):
        weapon_stance("Milady", None)


def test_base_game_weapon_still_works() -> None:
    assert calculate_ar("Uchigatana", 0, None, {**NONE, "str": 11, "dex": 15}).total > 0


# ------------------------------------------------------------- stat defaults


def test_stats_that_cant_matter_default_to_10() -> None:
    # Estoc scales only with Str/Dex: the chat log's "all requirements met" case.
    r = calculate_ar("Estoc", 21, None, {**NONE, "str": 11, "dex": 13})
    assert r.assumed_stats == {"int": 10, "fai": 10, "arc": 10}
    full = calculate_ar("Estoc", 21, None, {"str": 11, "dex": 13, "int": 1, "fai": 1, "arc": 1})
    assert r.total == full.total == 276


def test_unrequired_scaling_stat_uses_lowest_starting_value() -> None:
    # Magic affinity adds Int scaling with no Int requirement.
    r = calculate_ar("Magic Longsword", 10, None, {**NONE, "str": 10, "dex": 10})
    assert lowest_starting_stats()["int"] == 7
    assert r.assumed_stats == {"int": 7, "fai": 10, "arc": 10}
    assert r.effective_stats["int"] == 7


def test_lowest_starting_stats() -> None:
    assert lowest_starting_stats() == {"str": 8, "dex": 9, "int": 7, "fai": 6, "arc": 7}


def test_missing_required_scaling_stat_is_an_error() -> None:
    with pytest.raises(ValueError, match="Strength wasn't given"):
        calculate_ar("Uchigatana", 0, None, {**NONE, "dex": 15})


def test_given_stats_are_not_assumed() -> None:
    r = calculate_ar("Longsword", 0, None, {"str": 12, "dex": 12, "int": 9, "fai": 9, "arc": 9})
    assert r.assumed_stats == {}


def test_run_tool_accepts_null_stats() -> None:
    args = {
        "weapon": "Estoc",
        "affinity": "Standard",
        "upgrade_level": 21,
        "strength": 11,
        "dexterity": 13,
        "intelligence": None,
        "faith": None,
        "arcane": None,
        "two_handed": False,
        "buffs": [],
    }
    out, is_error = run_tool("calculate_attack_rating", args)
    assert not is_error
    assert json.loads(out)["assumed_stats"] == {"int": 10, "fai": 10, "arc": 10}
