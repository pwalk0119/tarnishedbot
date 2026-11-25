"""Scaling sweep across every weapon, affinity and upgrade level."""

import json

import pytest

from backend.chat import run_tool
from tools.attack_rating import data_available
from tools.weapon_lookup import lookup_weapon
from tools.weapon_search import is_dlc_weapon, search_weapon_scaling

pytestmark = pytest.mark.skipif(not data_available(), reason="param CSVs not found")


def test_urumi_reaches_s_dex_only_on_keen() -> None:
    r = search_weapon_scaling("dex", "S", weapon="Urumi")
    assert [(m.affinity, m.stat) for m in r.matches] == [("Keen", "dex")]
    # Matches the chat log: A at +15, S by +20.
    assert r.matches[0].grade_from_level["A"] <= 15 < r.matches[0].grade_from_level["S"] <= 20


def test_guardians_swordspear_never_reaches_s_dex() -> None:
    r = search_weapon_scaling("dex", "S", weapon="Guardians Swordspear")
    assert r.total_matches == 0


def test_sweep_agrees_with_lookup_weapon(param_db_url: str) -> None:
    r = search_weapon_scaling("dex", "E", weapon="Godslayer's Greatsword")
    (dex,) = r.matches
    info = lookup_weapon(
        "Godslayer's Greatsword", None, dex.max_upgrade_level, database_url=param_db_url
    )
    assert dex.best_scaling == info.scaling["dex"]
    assert dex.grade_from_level == {"D": 0, "C": 1, "B": 8}


def test_full_sweep_skips_dlc_and_consumables() -> None:
    r = search_weapon_scaling("any", "S", limit=1000)
    names = {m.weapon for m in r.matches}
    assert "Meteorite Staff" in names
    assert not names & {"Hefty Magic Pot", "Spritestone", "Milady"}
    assert r.matches == sorted(r.matches, key=lambda m: -m.best_scaling)


def test_affinity_filter() -> None:
    r = search_weapon_scaling("str", "A", affinity="Heavy", limit=1000)
    assert r.total_matches > 0
    assert {m.affinity for m in r.matches} == {"Heavy"}


def test_limit_adds_note() -> None:
    r = search_weapon_scaling("any", "E", limit=5)
    assert len(r.matches) == 5 and r.total_matches > 5 and r.note


def test_dlc_ids() -> None:
    assert is_dlc_weapon(67500000) and is_dlc_weapon(2500000)
    assert not is_dlc_weapon(2000000) and not is_dlc_weapon(20070000)


def test_bad_grade_is_an_error() -> None:
    with pytest.raises(ValueError, match="min_grade"):
        search_weapon_scaling("dex", "SS")


def test_run_tool_sweep() -> None:
    args = {"stat": "dex", "min_grade": "S", "affinity": "any", "weapon": "Urumi"}
    out, is_error = run_tool("search_weapon_scaling", args)
    assert not is_error
    assert json.loads(out)["total_matches"] == 1
