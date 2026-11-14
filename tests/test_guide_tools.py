"""Guide facts helpers and the entry checker."""

from pathlib import Path

import pytest

from pipeline import guide_check as gc
from pipeline import guide_data as gd
from tools.attack_rating import data_available

needs_data = pytest.mark.skipif(not data_available(), reason="param CSVs not exported")

# ------------------------------------------------------------- guide_data


def test_split_label() -> None:
    assert gd.split_label("[Stormhill] Castleward Tunnel") == ("Stormhill", "Castleward Tunnel")
    assert gd.split_label("Crafting Kit") == ("", "Crafting Kit")
    assert gd.split_label(float("nan")) == ("", "")


@needs_data
def test_graces_by_group() -> None:
    names = {g.name for g in gd.graces("Stormhill")}
    assert {"Stormhill Shack", "Castleward Tunnel"} <= names
    assert "Church of Elleh" in {g.name for g in gd.graces("Limgrave")}
    # Paramdex calls it "Gatefront Ruins"; in game it's "Gatefront".
    assert "Gatefront" in {g.name for g in gd.graces("Limgrave")}
    assert "Gatefront Ruins" not in {g.name for g in gd.graces()}


@needs_data
def test_shop_names_items_and_stock() -> None:
    items = {i.name: i for i in gd.shop("Merchant Kale")}
    assert items["Crafting Kit"].price == 300
    assert items["Throwing Dagger"].stock is None  # unlimited


@needs_data
def test_enemy_hp_includes_area_scaling() -> None:
    # Known in-game values: Tree Sentinel 2,889 HP; Margit 4,174 HP.
    assert gd.enemy("Tree Sentinel (Limgrave)")[0].hp == 2889
    assert 4174 in {v.hp for v in gd.enemy("Margit, the Fell Omen")}


@needs_data
def test_enemy_drops_and_boss_runes() -> None:
    soldier = next(v for v in gd.enemy("Godrick Soldier") if v.runes == 64)
    assert "Lordsworn's Straight Sword" in {d.name for d in soldier.drops}
    assert ("[Stormveil Castle] Margit the Fell Omen", 12000) in gd.boss_runes("Margit")


def test_drop_bullet_puts_quantity_after_name() -> None:
    assert gd.Drop("Bolt", 10.0, 5).bullet() == "- **Bolt** (x5) — 10% drop chance"
    assert gd.Drop("Brass Shield", 2.0, 1).bullet() == "- **Brass Shield** — 2% drop chance"


@needs_data
def test_criticals_from_throw_param() -> None:
    # Soldiers can be backstabbed; Margit and the Burial Watchdog can't (stance break only).
    assert "backstab" in gd.criticals(43110010)  # Godrick Soldier, Limgrave
    margit = gd.criticals(21300014)
    assert "backstab" not in margit
    assert "critical after a stance break" in margit
    assert "backstab" not in gd.criticals(42600100)  # Erdtree Burial Watchdog


@needs_data
def test_find_item_is_forgiving() -> None:
    assert gd.find_item("crafting kit") == [("goods", 8500, "Crafting Kit")]
    assert gd.find_item("Nope Item") == []


# ------------------------------------------------------------ guide_check

GOOD = """---
title: Church of Elleh
type: landmark
region: Limgrave
aliases: [Elleh]
status: draft
source: original
---

## Overview
A ruined church.

## Location
West of the First Step.

## Sites of Grace
- Church of Elleh — inside the church

## What's Here
Kalé the merchant.

## Items
- **Crafting Kit** — bought from Kalé [check]

## Tips
Buy the Crafting Kit.
"""

COVERAGE = """## Landmarks

| Entry | Status |
|---|---|
| Church of Elleh | todo |
| Gatefront Ruins | todo |
"""


def setup_guide(tmp_path: Path, entry: str = GOOD) -> tuple[Path, Path]:
    entries = tmp_path / "entries" / "landmark"
    entries.mkdir(parents=True)
    (entries / "church-of-elleh.md").write_text(entry, encoding="utf-8")
    coverage = tmp_path / "coverage.md"
    coverage.write_text(COVERAGE, encoding="utf-8")
    return tmp_path / "entries", coverage


@needs_data
def test_good_draft_passes_and_updates_coverage(tmp_path: Path) -> None:
    entries, coverage = setup_guide(tmp_path)
    report = gc.run(entries, coverage, write_coverage=True)
    assert report.errors == []
    assert report.warnings == []
    text = coverage.read_text(encoding="utf-8")
    assert "| Church of Elleh | draft |" in text
    assert "| Gatefront Ruins | todo |" in text


@needs_data
def test_verified_with_check_marker_is_an_error(tmp_path: Path) -> None:
    entries, coverage = setup_guide(tmp_path, GOOD.replace("status: draft", "status: verified"))
    report = gc.run(entries, coverage)
    assert any("[check]" in e for e in report.errors)


@needs_data
def test_bad_header_missing_section_and_unknown_names(tmp_path: Path) -> None:
    bad = (
        GOOD.replace("type: landmark", "type: castle")
        .replace("source: original", "source: fextralife")
        .replace("- Church of Elleh —", "- Church of Ellehh —")
        .replace("**Crafting Kit**", "**Crafting Kitt**")
    )
    entries, coverage = setup_guide(tmp_path, bad)
    report = gc.run(entries, coverage)
    assert any("type must be" in e for e in report.errors)
    assert any("source must be" in e for e in report.errors)
    assert any("Church of Ellehh" in w for w in report.warnings)
    assert any("Crafting Kitt" in w for w in report.warnings)

    no_tips = GOOD.split("## Tips")[0]
    entries2, coverage2 = setup_guide(tmp_path / "b", no_tips)
    assert any("## Tips" in e for e in gc.run(entries2, coverage2).errors)


def test_missing_front_matter(tmp_path: Path) -> None:
    entries, coverage = setup_guide(tmp_path, "## Overview\nNo header.\n")
    report = gc.run(entries, coverage, check_items=False)
    assert any("front matter" in e for e in report.errors)


@needs_data
def test_templates_have_required_sections() -> None:
    for path in Path("guide/templates").glob("*.md"):
        report = gc.Report()
        entry = gc.parse_entry(path, report)
        assert entry is not None, report.errors
        for name in gc.REQUIRED_SECTIONS[entry.meta["type"]]:
            assert name in entry.sections, f"{path.name} lacks {name}"


@needs_data
def test_area_checklist_stormfoot() -> None:
    c = gd.area_checklist("Stormfoot Catacombs")
    assert [g.name for g in c.graces] == ["Stormfoot Catacombs"]
    assert ("Root Resin", 2) in [i for lot in c.pickups for i in lot.items]
    assert ("Wandering Noble Ashes", 1) in [i for lot in c.pickups for i in lot.items]
    assert [i for lot in c.boss_drops for i in lot.items] == [("Noble Sorcerer Ashes", 1)]
    (boss,) = c.bosses
    assert boss.runes == 1300
    assert 1200 in {v.hp for v in boss.variants}


def test_world_xz_and_relative_direction() -> None:
    assert gd.world_xz("m60_42_36_00", 10.0, 20.0) == (42 * 256 + 10.0, 36 * 256 + 20.0)
    assert gd.world_xz("m30_02_00_00", 1.0, 2.0) is None
    grace = gd.Grace(1, "Test", "Limgrave", "m30_02_00_00", 0.0, 10.0, 0.0)
    north_east_below = gd.MapPlacement("m30_02_00_00", "gather", "p", 1, "x", 30.0, 0.0, 30.0)
    assert gd.relative_to(north_east_below, grace) == "42 m NE, 10 m below"
    west = gd.MapPlacement("m30_02_00_00", "gather", "p", 1, "x", -20.0, 11.0, 0.0)
    assert gd.relative_to(west, grace) == "20 m W"


needs_placements = pytest.mark.skipif(
    not gd.placements_available(), reason="map placements not extracted"
)


@needs_placements
def test_stormfoot_map_placements() -> None:
    # Matches a full in-game route: 5 Grave Glovewort, 1 Ghost Glovewort, 4 corpses.
    anchor, rows = gd.area_placements("Stormfoot Catacombs")
    assert anchor is not None and anchor.map_id == "m30_02_00_00"
    gathered = [p.name for p in rows if p.kind == "gather"]
    assert gathered.count("Grave Glovewort [1]") == 5
    assert gathered.count("Ghost Glovewort [1]") == 1
    assert len([p for p in rows if p.kind == "treasure"]) == 4
    names = [p.name for p in rows if p.kind == "enemy"]
    assert names.count("Imp") >= 10


@needs_placements
def test_dungeon_grace_moves_into_its_map() -> None:
    # BonfireWarpParam gives Murkwater Cave's grace an open-world spot above the cave.
    assert gd.dungeon_map_of(310000) == "m31_00_00_00"
    assert gd.dungeon_map_of(61423700) is None
    cave = next(g for g in gd.located_graces() if g.name == "Murkwater Cave")
    assert cave.map_id == "m31_00_00_00"
    anchor, rows = gd.area_placements("Murkwater Cave")
    assert anchor.map_id == "m31_00_00_00"
    assert "Patches (Boss)" in {p.name for p in rows}


@needs_data
def test_human_npc_criticals() -> None:
    # Patches uses the human model c0000.
    assert gd.criticals(523090010) == ["backstab", "riposte after a parry or guard break"]


@needs_data
def test_enemy_damage_weaknesses() -> None:
    # Grave Warden Duelist takes extra slash; the cave Beastman extra fire, less lightning.
    (duelist,) = [v for v in gd.enemy("Grave Warden Duelist (Boss)") if v.id == 34001110]
    assert "slash +10%" in duelist.weaknesses()[0]
    (beastman,) = [v for v in gd.enemy("Beastman of Farum Azula") if v.id == 39701910]
    weak, strong = beastman.weaknesses()
    assert "fire +20%" in weak and "lightning -20%" in strong
