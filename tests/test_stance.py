"""Stance damage tool. Expected values come from regulation 1.17.3 params; the guard counter
vs Imp case was also checked in game (one Longsword guard counter breaks an Imp)."""

from __future__ import annotations

import json

import pytest

from backend.chat import run_tool
from tools.attack_rating import data_available
from tools.stance import find_enemy_stance, hits_to_break, weapon_stance

pytestmark = pytest.mark.skipif(not data_available(), reason="param CSVs not exported")


def _attack(attacks, name):
    return next(a for a in attacks if a.attack == name)


def test_hits_to_break() -> None:
    assert hits_to_break(30, 30) == 1
    assert hits_to_break(30, 29.9) == 2
    assert hits_to_break(35, 5) == 7
    assert hits_to_break(30, 0) is None


def test_longsword_guard_counter_breaks_an_imp() -> None:
    r = weapon_stance("Longsword", enemy="Imp")
    assert r.base_stance_damage == 5.0
    gc = _attack(r.one_handed, "Guard counter")
    assert gc.stance_damage == 30.0  # 5 x 600%
    assert gc.hits_to_break[30] == 1
    assert _attack(r.one_handed, "R1 1").stance_damage == 5.0
    assert _attack(r.one_handed, "R2").stance_damage == 10.0
    assert _attack(r.two_handed, "Guard counter").stance_damage == 33.0


def test_class_default_fills_missing_attacks() -> None:
    # Short Sword (209) and Golden Halberd (1813) have few or no rows of their own.
    short = weapon_stance("Short Sword")
    assert _attack(short.one_handed, "R1 1").stance_damage == 5.0
    halberd = weapon_stance("Golden Halberd")
    assert {"R1 1", "Charged R2", "Guard counter"} <= {a.attack for a in halberd.one_handed}


def test_enemy_stance_prefers_guide_regions() -> None:
    (sentinel,) = find_enemy_stance("Tree Sentinel")
    assert sentinel.name == "Tree Sentinel (Limgrave)"
    assert sentinel.stance == 80
    assert [e.stance for e in find_enemy_stance("Imp")] == [30, 35]
    (margit,) = find_enemy_stance("Margit, the Fell Omen")
    assert margit.stance == 80
    with pytest.raises(ValueError):
        find_enemy_stance("Not An Enemy")


def test_run_tool_stance() -> None:
    out, is_error = run_tool(
        "calculate_stance_damage", {"weapon": "Longsword", "affinity": "Standard", "enemy": "Imp"}
    )
    assert not is_error
    data = json.loads(out)
    assert [e["stance"] for e in data["enemy"]] == [30, 35]
    out, is_error = run_tool(
        "calculate_stance_damage", {"weapon": "Longsword", "affinity": "Standard", "enemy": ""}
    )
    assert not is_error and json.loads(out)["enemy"] == []
