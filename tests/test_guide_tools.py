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
