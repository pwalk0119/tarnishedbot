"""Load walkthrough entries (guide/entries/**/*.md) into ChromaDB for guide search.

Each entry is split into chunks, one per "## Section" (long sections are split
further). Every chunk starts with "Title - Section" so it still makes sense on
its own. Chunks are embedded with a small local model and stored in one
collection, which is rebuilt from scratch on every run.

Only `status: verified` entries are loaded unless include_drafts is set.

Run from the project root:
    uv run python -m pipeline.load_guide                   # verified entries only
    uv run python -m pipeline.load_guide --include-drafts  # drafts too (development)
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tools.guide_search import (
    COLLECTION,
    EMBEDDING_MODEL,
    chroma_path,
    clear_cache,
    get_embedder,
    open_client,
)

ENTRY_DIR = Path("guide/entries")
MAX_CHUNK_CHARS = 1200

Embedder = Callable[[Sequence[str]], list[list[float]]]

FRONT_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.S)
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict[str, str | int]


@dataclass
class LoadReport:
    entries: int = 0
    chunks: int = 0
    skipped_drafts: int = 0
    errors: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ parsing


def parse_entry(text: str) -> tuple[dict, dict[str, str]]:
    """Split an entry into (front matter dict, {section heading: section text})."""
    m = FRONT_RE.match(text)
    if not m:
        raise ValueError("missing front matter (--- block at the top)")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("front matter must be key: value pairs")

    sections: dict[str, str] = {}
    current, lines = None, []
    for line in COMMENT_RE.sub("", m.group(2)).splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current, lines = heading.group(1), []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return meta, {name: body for name, body in sections.items() if body}


def split_long(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into pieces of at most ~limit chars, on blank lines or list items."""
    if len(text) <= limit:
        return [text]
    # A new block starts at a blank line, a "- " bullet or a "1. " step.
    blocks = re.split(r"\n\s*\n|\n(?=\s*(?:[-*]|\d+\.)\s)", text)
    pieces, current = [], ""
    for block in (b.strip() for b in blocks if b.strip()):
        if current and len(current) + len(block) + 1 > limit:
            pieces.append(current)
            current = block
        else:
            current = f"{current}\n{block}" if current else block
    if current:
        pieces.append(current)
    return pieces


def entry_chunks(path: Path, meta: dict, sections: dict[str, str], root: Path) -> list[Chunk]:
    """Turn one parsed entry into chunks with search metadata."""
    title = str(meta.get("title", "")).strip() or path.stem
    rel = path.relative_to(root).as_posix()
    base_meta: dict[str, str | int] = {
        "title": title,
        "type": str(meta.get("type", "")),
        "region": str(meta.get("region", "")),
        "status": str(meta.get("status", "")),
        "source": str(meta.get("source", "original")),
        "path": rel,
    }
    # Aliases and tags go in the first chunk so searches by nickname still find it.
    extra = [str(a) for a in meta.get("aliases") or []] + [str(t) for t in meta.get("tags") or []]

    chunks: list[Chunk] = []
    for section, body in sections.items():
        for part_no, part in enumerate(split_long(body)):
            header = f"{title} - {section}"
            if not chunks and extra:
                header += f" ({', '.join(extra)})"
            chunks.append(
                Chunk(
                    id=f"{rel}#{section}#{part_no}",
                    text=f"{header}\n{part}",
                    metadata={**base_meta, "section": section, "part": part_no},
                )
            )
    return chunks


def collect_chunks(
    entry_dir: Path = ENTRY_DIR, *, include_drafts: bool = False
) -> tuple[list[Chunk], LoadReport]:
    report = LoadReport()
    chunks: list[Chunk] = []
    for path in sorted(entry_dir.rglob("*.md")):
        try:
            meta, sections = parse_entry(path.read_text(encoding="utf-8"))
        except (ValueError, yaml.YAMLError) as exc:
            report.errors.append(f"{path.as_posix()}: {exc}")
            continue
        status = str(meta.get("status", ""))
        if status == "todo" or (status != "verified" and not include_drafts):
            report.skipped_drafts += status == "draft"
            continue
        new = entry_chunks(path, meta, sections, entry_dir)
        chunks.extend(new)
        report.entries += 1
        report.chunks += len(new)
    return chunks, report


# ------------------------------------------------------------------ loading


def load_guide(
    entry_dir: Path = ENTRY_DIR,
    path: str | Path | None = None,
    *,
    include_drafts: bool = False,
    embedder: Embedder | None = None,
) -> LoadReport:
    """Rebuild the guide collection from entry_dir. Returns what was loaded."""
    chunks, report = collect_chunks(entry_dir, include_drafts=include_drafts)
    client = open_client(path or chroma_path())

    # Rebuild from scratch so deleted or renamed entries don't linger.
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    collection = client.create_collection(
        COLLECTION,
        embedding_function=None,
        metadata={
            "hnsw:space": "cosine",
            "embedding_model": EMBEDDING_MODEL,
            "include_drafts": include_drafts,
        },
    )
    if chunks:
        embed = embedder or get_embedder()
        collection.add(
            ids=[c.id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[c.metadata for c in chunks],
            embeddings=embed([c.text for c in chunks]),
        )
    clear_cache()  # a running search shouldn't keep the old collection
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--include-drafts", action="store_true", help="also load draft entries")
    parser.add_argument("--entries", type=Path, default=ENTRY_DIR, help="entry folder")
    parser.add_argument("--chroma", type=Path, default=None, help="ChromaDB folder")
    args = parser.parse_args(argv)

    target = args.chroma or chroma_path()
    report = load_guide(args.entries, target, include_drafts=args.include_drafts)
    print(f"Loaded {report.entries} entries as {report.chunks} chunks into {target}")
    if report.skipped_drafts:
        print(f"Skipped {report.skipped_drafts} draft entries (use --include-drafts to load them)")
    for err in report.errors:
        print(f"Skipped {err}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
