"""Check guide entries and update the statuses in guide/coverage.md.

    uv run python -m pipeline.guide_check                    # check only
    uv run python -m pipeline.guide_check --update-coverage  # also rewrite coverage statuses

Errors (exit code 1): bad or missing front matter, missing required sections,
duplicate titles, or a verified entry that still has [check] markers.
Warnings: grace or item names that aren't in the game data, entries not in coverage.md.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pipeline import guide_data as gd

GUIDE_DIR = Path("guide")
ENTRY_DIR = GUIDE_DIR / "entries"
COVERAGE = GUIDE_DIR / "coverage.md"

TYPES = {"region", "dungeon", "landmark", "npc", "field_boss", "boss", "enemy"}
REGIONS = {"Limgrave", "Stormhill", "Weeping Peninsula", "Stormveil Castle", "Stranded Graveyard"}
STATUSES = ("todo", "draft", "verified")
BOSS_SECTIONS = ["Overview", "Location", "Stats", "Strategy", "Rewards", "Tips"]
REQUIRED_SECTIONS = {
    "region": [
        "Overview",
        "Getting There",
        "Sites of Grace",
        "Points of Interest",
        "Bosses",
        "NPCs",
        "Items",
        "Tips",
    ],
    "dungeon": [
        "Overview",
        "Location",
        "Sites of Grace",
        "Walkthrough",
        "Enemies",
        "Boss",
        "Items",
        "Tips",
    ],
    "landmark": ["Overview", "Location", "Sites of Grace", "What's Here", "Items", "Tips"],
    "npc": ["Overview", "Location", "Services", "Questline", "Rewards", "Tips"],
    "field_boss": BOSS_SECTIONS,
    "boss": BOSS_SECTIONS,
    "enemy": ["Overview", "Where Found", "Stats", "Drops", "How to Fight"],
}
ITEM_SECTIONS = {"Items", "Rewards", "Drops"}
SOURCE_RE = re.compile(r"^(original|fandom:https://\S+)$")
FRONT_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.S)
CHECK_RE = re.compile(r"\[check\]", re.I)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
GRACE_BULLET_RE = re.compile(r"^\s*[-*]\s+(?!\*\*)([^—–:\n]+?)\s*(?:[—–:].*)?$")
ITEM_BULLET_RE = re.compile(r"^\s*[-*]\s+\*\*(.+?)\*\*")
COVERAGE_ROW_RE = re.compile(r"^\|\s*(?P<title>[^|]+?)\s*\|\s*(?P<status>[^|]*?)\s*\|\s*$")
NONE_RE = re.compile(r"^\s*none\.?\s*$", re.I)


@dataclass
class Entry:
    path: Path
    meta: dict
    sections: dict[str, str]
    body: str

    @property
    def title(self) -> str:
        return str(self.meta.get("title", "")).strip()

    @property
    def status(self) -> str:
        return str(self.meta.get("status", "")).strip()

    def names(self) -> list[str]:
        aliases = self.meta.get("aliases") or []
        return [self.title, *(str(a) for a in aliases)]


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)

    def error(self, path: Path, msg: str) -> None:
        self.errors.append(f"{path.as_posix()}: {msg}")

    def warn(self, path: Path, msg: str) -> None:
        self.warnings.append(f"{path.as_posix()}: {msg}")


def split_sections(body: str) -> dict[str, str]:
    """Map '## Heading' -> text under it (until the next ## heading)."""
    sections: dict[str, str] = {}
    current = None
    lines: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current, lines = m.group(1), []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def parse_entry(path: Path, report: Report) -> Entry | None:
    text = path.read_text(encoding="utf-8")
    m = FRONT_RE.match(text)
    if not m:
        report.error(path, "missing front matter (--- block at the top)")
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        report.error(path, f"front matter isn't valid YAML: {exc}")
        return None
    if not isinstance(meta, dict):
        report.error(path, "front matter must be key: value pairs")
        return None
    body = m.group(2)
    return Entry(path, meta, split_sections(body), body)


def check_entry(entry: Entry, report: Report, graces: set[str], check_items: bool) -> None:
    p, meta = entry.path, entry.meta
    if not entry.title:
        report.error(p, "title is missing")
    etype = meta.get("type")
    if etype not in TYPES:
        report.error(p, f"type must be one of {sorted(TYPES)}, got {etype!r}")
    if meta.get("region") not in REGIONS:
        report.error(p, f"region must be one of {sorted(REGIONS)}, got {meta.get('region')!r}")
    if entry.status not in ("draft", "verified"):
        report.error(p, f"status must be draft or verified, got {entry.status!r}")
    if not SOURCE_RE.match(str(meta.get("source", ""))):
        report.error(p, "source must be 'original' or 'fandom:https://...'")
    for key in ("aliases", "tags", "related"):
        if key in meta and meta[key] is not None and not isinstance(meta[key], list):
            report.error(p, f"{key} must be a list, e.g. [a, b]")

    for name in REQUIRED_SECTIONS.get(etype, []):
        if name not in entry.sections:
            report.error(p, f"missing section '## {name}'")
        elif not COMMENT_RE.sub("", entry.sections[name]).strip():
            report.warn(p, f"section '## {name}' is empty (write 'None.' if it doesn't apply)")

    checks = len(CHECK_RE.findall(entry.body))
    if entry.status == "verified" and checks:
        report.error(p, f"verified but still has {checks} [check] marker(s)")

    grace_text = COMMENT_RE.sub("", entry.sections.get("Sites of Grace", ""))
    for line in grace_text.splitlines():
        m = GRACE_BULLET_RE.match(line)
        if m and not NONE_RE.match(m.group(1)) and gd._norm(m.group(1)) not in graces:
            report.warn(p, f"grace '{m.group(1).strip()}' isn't in the game data")

    if check_items:
        for section in ITEM_SECTIONS & entry.sections.keys():
            for line in COMMENT_RE.sub("", entry.sections[section]).splitlines():
                m = ITEM_BULLET_RE.match(line)
                if m and not gd.find_item(m.group(1)):
                    report.warn(p, f"item '{m.group(1)}' isn't in the game data")


def load_entries(report: Report, entry_dir: Path = ENTRY_DIR) -> list[Entry]:
    entries = []
    for path in sorted(entry_dir.rglob("*.md")):
        entry = parse_entry(path, report)
        if entry:
            entries.append(entry)
    return entries


def coverage_titles(text: str) -> list[str]:
    titles = []
    for line in text.splitlines():
        m = COVERAGE_ROW_RE.match(line)
        if m and m.group("title") not in ("Entry",) and not set(m.group("title")) <= set("-: "):
            titles.append(m.group("title"))
    return titles


def update_coverage(text: str, status_by_name: dict[str, str]) -> str:
    """Rewrite each coverage row's status from the matching entry (or 'todo')."""
    out = []
    for line in text.splitlines():
        m = COVERAGE_ROW_RE.match(line)
        if m and m.group("title") != "Entry" and not set(m.group("title")) <= set("-: "):
            status = status_by_name.get(gd._norm(m.group("title")), "todo")
            line = f"| {m.group('title')} | {status} |"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def run(
    entry_dir: Path = ENTRY_DIR,
    coverage: Path = COVERAGE,
    *,
    write_coverage: bool = False,
    check_items: bool = True,
) -> Report:
    report = Report()
    entries = load_entries(report, entry_dir)
    report.entries = entries
    graces = gd.grace_names()

    seen: dict[str, Path] = {}
    for entry in entries:
        check_entry(entry, report, graces, check_items)
        key = gd._norm(entry.title)
        if key and key in seen:
            report.error(entry.path, f"duplicate title, also in {seen[key].as_posix()}")
        seen.setdefault(key, entry.path)

    if coverage.is_file():
        text = coverage.read_text(encoding="utf-8")
        planned = {gd._norm(t) for t in coverage_titles(text)}
        status_by_name: dict[str, str] = {}
        for entry in entries:
            names = {gd._norm(n) for n in entry.names()}
            if not names & planned:
                report.warn(entry.path, f"'{entry.title}' isn't listed in coverage.md")
            for n in names:
                status_by_name[n] = entry.status
        if write_coverage:
            coverage.write_text(
                update_coverage(text, status_by_name), encoding="utf-8", newline="\n"
            )
    return report


def summarize(report: Report, coverage: Path = COVERAGE) -> str:
    lines = []
    if coverage.is_file():
        titles = coverage_titles(coverage.read_text(encoding="utf-8"))
        statuses = {gd._norm(n): e.status for e in report.entries for n in e.names()}
        counts = {s: 0 for s in STATUSES}
        for t in titles:
            counts[statuses.get(gd._norm(t), "todo")] += 1
        lines.append(
            f"Coverage: {counts['verified']} verified, {counts['draft']} draft, "
            f"{counts['todo']} todo (of {len(titles)})"
        )
    checks = sum(len(CHECK_RE.findall(e.body)) for e in report.entries)
    lines.append(f"Entries: {len(report.entries)}, open [check] markers: {checks}")
    lines += [f"ERROR   {e}" for e in report.errors]
    lines += [f"warning {w}" for w in report.warnings]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check guide entries.")
    parser.add_argument("--update-coverage", action="store_true", help="Rewrite coverage.md")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    report = run(write_coverage=args.update_coverage)
    print(summarize(report))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
