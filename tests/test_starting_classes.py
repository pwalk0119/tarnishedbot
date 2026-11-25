"""Starting classes and keepsakes from CharaInitParam."""

import json

import pytest

from backend.chat import CLASS_NAMES, run_tool
from tools.attack_rating import data_dir
from tools.starting_classes import class_names, keepsakes, starting_classes

pytestmark = pytest.mark.skipif(
    not (data_dir() / "CharaInitParam.csv").is_file(), reason="CharaInitParam.csv not found"
)


def test_ten_classes_match_the_tool_enum() -> None:
    assert class_names() == CLASS_NAMES


def test_samurai() -> None:
    (samurai,) = starting_classes("samurai")
    assert samurai.level == 9
    assert samurai.stats == {
        "vigor": 12,
        "mind": 11,
        "endurance": 13,
        "strength": 12,
        "dexterity": 15,
        "intelligence": 9,
        "faith": 8,
        "arcane": 8,
    }
    assert samurai.weapons == ["Uchigatana", "Longbow", "Red Thorn Roundshield"]
    assert [(a.name, a.count) for a in samurai.ammo] == [("Arrow", 20), ("Fire Arrow", 10)]


def test_spell_names_drop_the_category_tag() -> None:
    (astrologer,) = starting_classes("Astrologer")
    assert astrologer.spells == ["Glintstone Pebble", "Glintstone Arc"]


def test_wretch_stats_are_all_ten() -> None:
    (wretch,) = starting_classes("Wretch")
    assert wretch.level == 1
    assert set(wretch.stats.values()) == {10}


def test_unknown_class_lists_options() -> None:
    with pytest.raises(KeyError, match="Samurai"):
        starting_classes("Knight")


def test_keepsakes() -> None:
    gifts = {k.name: [(i.name, i.count) for i in k.items] for k in keepsakes()}
    assert len(gifts) == 10
    assert gifts["None"] == []
    assert gifts["Stonesword Key"] == [("Stonesword Key", 2)]
    assert gifts["Crimson Amber Medallion"] == [("Crimson Amber Medallion", 1)]
    assert gifts["Boiled Prawn"] == [("Boiled Prawn", 5)]
    assert all(k.effect for k in keepsakes())


def test_run_tool_returns_classes_and_keepsakes() -> None:
    out, is_error = run_tool("get_starting_options", {"class_name": "all"})
    assert not is_error
    data = json.loads(out)
    assert len(data["classes"]) == 10
    assert len(data["keepsakes"]) == 10
