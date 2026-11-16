"""Guide loader and search tests.

A fake embedder (hashed bag of words) stands in for the real model, so these
tests never download anything. Words in common => similar vectors, which is
enough to check that loading, filtering and ranking work.
"""

import hashlib
import json
import math
import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from backend.chat import run_tool
from pipeline.load_guide import collect_chunks, load_guide, parse_entry, split_long
from tools import guide_search
from tools.guide_search import credit_for, search_guide

DIMS = 256


def fake_embed(texts: Sequence[str]) -> list[list[float]]:
    vectors = []
    for text in texts:
        vec = [0.0] * DIMS
        for word in re.findall(r"[a-z]+", text.lower()):
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMS] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vectors.append([v / norm for v in vec])
    return vectors


def entry(title: str, etype: str, status: str, sections: dict[str, str], **extra: str) -> str:
    front = "\n".join(
        [
            f"title: {title}",
            f"type: {etype}",
            "region: Limgrave",
            f"status: {status}",
            f"source: {extra.get('source', 'original')}",
            f"aliases: [{extra.get('aliases', '')}]",
            "tags: []",
        ]
    )
    body = "\n\n".join(f"## {name}\n\n{text}" for name, text in sections.items())
    return f"---\n{front}\n---\n\n{body}\n"


@pytest.fixture
def entries(tmp_path: Path) -> Path:
    root = tmp_path / "entries"
    (root / "boss").mkdir(parents=True)
    (root / "npc").mkdir()
    (root / "dungeon").mkdir()
    (root / "boss" / "margit.md").write_text(
        entry(
            "Margit, the Fell Omen",
            "boss",
            "verified",
            {
                "Overview": "Margit guards the gate of Stormveil Castle.",
                "Strategy": "Dodge his delayed hammer slams, then punish. Margit's Shackle "
                "pins him briefly.",
            },
            aliases="Margit",
        ),
        encoding="utf-8",
    )
    (root / "npc" / "boc.md").write_text(
        entry(
            "Boc the Seamster",
            "npc",
            "verified",
            {"Overview": "Boc alters garments once you give him the Sewing Needle."},
            source="fandom:https://eldenring.fandom.com/wiki/Boc",
        ),
        encoding="utf-8",
    )
    (root / "dungeon" / "coastal-cave.md").write_text(
        entry(
            "Coastal Cave",
            "dungeon",
            "draft",
            {"Boss": "Two Demi-Human Chiefs drop the Sewing Needle and Tailoring Tools."},
        ),
        encoding="utf-8",
    )
    (root / "npc" / "broken.md").write_text("no front matter here", encoding="utf-8")
    return root


@pytest.fixture
def index(entries: Path, tmp_path: Path) -> Path:
    path = tmp_path / "chroma"
    load_guide(entries, path, include_drafts=True, embedder=fake_embed)
    return path


@pytest.fixture(autouse=True)
def _fresh_cache():
    guide_search.clear_cache()
    yield
    guide_search.clear_cache()


# ------------------------------------------------------------------ parsing


def test_parse_entry_splits_sections_and_drops_comments() -> None:
    text = "---\ntitle: X\nstatus: draft\n---\n## A\none <!-- hidden -->\n## B\n\n## C\nthree\n"
    meta, sections = parse_entry(text)
    assert meta["title"] == "X"
    assert sections == {"A": "one", "C": "three"}  # empty section B is dropped


def test_parse_entry_requires_front_matter() -> None:
    with pytest.raises(ValueError):
        parse_entry("## Overview\ntext")


def test_split_long_keeps_short_text_and_splits_long() -> None:
    assert split_long("short") == ["short"]
    steps = "\n".join(f"{i}. " + "word " * 40 for i in range(1, 11))
    pieces = split_long(steps, limit=500)
    assert len(pieces) > 1
    assert all(len(p) <= 500 for p in pieces)
    assert sum(p.count("word") for p in pieces) == 400  # nothing lost
    assert all(p.startswith(tuple("123456789")) for p in pieces)  # splits fall between steps


def test_chunks_start_with_title_and_section(entries: Path) -> None:
    chunks, report = collect_chunks(entries, include_drafts=True)
    margit = [c for c in chunks if c.metadata["title"] == "Margit, the Fell Omen"]
    assert [c.metadata["section"] for c in margit] == ["Overview", "Strategy"]
    assert margit[0].text.startswith("Margit, the Fell Omen - Overview (Margit)")
    assert margit[1].text.startswith("Margit, the Fell Omen - Strategy\n")
    assert report.entries == 3
    assert len(report.errors) == 1 and "broken.md" in report.errors[0]


def test_drafts_are_skipped_by_default(entries: Path) -> None:
    chunks, report = collect_chunks(entries)
    assert {c.metadata["title"] for c in chunks} == {"Margit, the Fell Omen", "Boc the Seamster"}
    assert report.skipped_drafts == 1


# ------------------------------------------------------------------ search


def test_search_ranks_the_matching_entry_first(index: Path) -> None:
    result = search_guide("how do I beat Margit", path=index, embedder=fake_embed)
    assert result.hits[0].title == "Margit, the Fell Omen"
    assert result.hits[0].score >= result.hits[-1].score


def test_search_filters_by_type(index: Path) -> None:
    result = search_guide("Sewing Needle", "dungeon", path=index, embedder=fake_embed)
    assert {h.title for h in result.hits} == {"Coastal Cave"}
    assert result.hits[0].status == "draft"


def test_search_adds_fandom_credit(index: Path) -> None:
    result = search_guide("Boc garments", "npc", path=index, embedder=fake_embed)
    assert result.hits[0].credit and "CC BY-SA" in result.hits[0].credit
    assert credit_for("original") is None


def test_reload_replaces_the_index(entries: Path, index: Path) -> None:
    load_guide(entries, index, embedder=fake_embed)  # verified only this time
    result = search_guide("Sewing Needle", limit=8, path=index, embedder=fake_embed)
    assert "Coastal Cave" not in {h.title for h in result.hits}
    assert len(result.hits) == 3  # Margit x2 + Boc, no duplicates


def test_empty_index_says_so(tmp_path: Path) -> None:
    empty = tmp_path / "empty_entries"
    empty.mkdir()
    path = tmp_path / "chroma"
    load_guide(empty, path, embedder=fake_embed)
    result = search_guide("Margit", path=path, embedder=fake_embed)
    assert result.hits == [] and "empty" in (result.note or "")


def test_missing_index_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="load_guide"):
        search_guide("Margit", path=tmp_path / "nothing", embedder=fake_embed)


def test_empty_query_raises(index: Path) -> None:
    with pytest.raises(ValueError):
        search_guide("   ", path=index, embedder=fake_embed)


# ------------------------------------------------------------------ chat tool


def test_run_tool_search_guide(index: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARNISHEDBOT_CHROMA_PATH", str(index))
    monkeypatch.setattr(guide_search, "get_embedder", lambda: fake_embed)
    out, is_error = run_tool("search_guide", {"query": "beat Margit", "entry_type": "any"})
    assert not is_error
    assert json.loads(out)["hits"][0]["title"] == "Margit, the Fell Omen"


def test_run_tool_search_guide_without_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TARNISHEDBOT_CHROMA_PATH", str(tmp_path / "nothing"))
    out, is_error = run_tool("search_guide", {"query": "Margit", "entry_type": "boss"})
    assert is_error and "load_guide" in out
