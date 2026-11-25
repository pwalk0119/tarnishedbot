"""weapon_levels table and get_weapon_progression."""

import json

import pytest

from backend.chat import run_tool
from tools.weapon_lookup import lookup_weapon
from tools.weapon_progression import weapon_progression


def test_progression_matches_lookup_at_every_level(param_db_url: str) -> None:
    p = weapon_progression("Godslayer's Greatsword", database_url=param_db_url)
    assert p.max_upgrade_level == 10
    assert [lv.level for lv in p.levels] == list(range(11))
    for lv in p.levels:
        info = lookup_weapon("Godslayer's Greatsword", None, lv.level, database_url=param_db_url)
        assert lv.base_damage == info.base_damage
        assert lv.scaling == {s: v for s, v in info.scaling.items() if v > 0}
    assert p.levels[8].scaling_grades["dex"] == "B"


def test_progression_by_affinity(param_db_url: str) -> None:
    p = weapon_progression("Urumi", "Keen", database_url=param_db_url)
    assert p.affinity == "Keen" and p.max_upgrade_level == 25
    assert p.levels[19].scaling_grades["dex"] == "S"
    assert p.levels[18].scaling_grades["dex"] == "A"


def test_progression_refuses_dlc(param_db_url: str) -> None:
    with pytest.raises(ValueError, match="DLC"):
        weapon_progression("Milady", database_url=param_db_url)


def test_run_tool_progression(param_db_url: str) -> None:
    args = {"weapon": "Longsword", "affinity": "Standard"}
    out, is_error = run_tool("get_weapon_progression", args, param_db_url)
    assert not is_error
    assert len(json.loads(out)["levels"]) == 26
