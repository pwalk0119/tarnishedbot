"""Search the Limgrave walkthrough entries stored in ChromaDB.

The index is built by pipeline/load_guide.py. A search embeds the question with
the same model used for loading, then returns the closest chunks (one section
of one entry each) with their title, section, status and source.

Index location: $TARNISHEDBOT_CHROMA_PATH, else data/chroma.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path

import chromadb
from pydantic import BaseModel

COLLECTION = "guide"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_CHROMA_PATH = Path("data/chroma")
MAX_RESULTS = 8
# Same types as the entry templates in guide/templates.
ENTRY_TYPES = ("region", "dungeon", "landmark", "npc", "field_boss", "boss", "enemy")

Embedder = Callable[[Sequence[str]], list[list[float]]]


class GuideHit(BaseModel):
    title: str
    section: str
    type: str
    region: str
    text: str
    # 1.0 = same meaning as the question, around 0.3 or less = probably unrelated.
    score: float
    status: str
    source: str
    credit: str | None = None


class GuideSearchResult(BaseModel):
    query: str
    hits: list[GuideHit]
    note: str | None = None


# ------------------------------------------------------------------ setup


def chroma_path() -> Path:
    return Path(os.environ.get("TARNISHEDBOT_CHROMA_PATH", DEFAULT_CHROMA_PATH))


def open_client(path: str | Path) -> chromadb.ClientAPI:
    settings = chromadb.Settings(anonymized_telemetry=False)
    return chromadb.PersistentClient(path=str(path), settings=settings)


@lru_cache(maxsize=1)
def _model():
    # Imported here: loading torch is slow, and only real searches need it.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL)


def _embed(texts: Sequence[str]) -> list[list[float]]:
    vectors = _model().encode(list(texts), normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def get_embedder() -> Embedder:
    """The embedding function for loading and searching. Tests replace this."""
    return _embed


@lru_cache(maxsize=4)
def _collection(path: str):
    client = open_client(path)
    if COLLECTION not in [c.name for c in client.list_collections()]:
        raise ValueError(
            "The guide index hasn't been built. Run: uv run python -m pipeline.load_guide"
        )
    return client.get_collection(COLLECTION, embedding_function=None)


def clear_cache() -> None:
    _collection.cache_clear()


def credit_for(source: str) -> str | None:
    if source.startswith("fandom:"):
        url = source.removeprefix("fandom:")
        return f"Text from the Elden Ring Fandom wiki ({url}), licensed CC BY-SA."
    return None


# ------------------------------------------------------------------ search


def search_guide(
    query: str,
    entry_type: str | None = None,
    limit: int = 5,
    *,
    path: str | Path | None = None,
    embedder: Embedder | None = None,
) -> GuideSearchResult:
    """Return the guide chunks closest in meaning to query, best first."""
    query = query.strip()
    if not query:
        raise ValueError("query is empty")
    limit = max(1, min(limit, MAX_RESULTS))

    collection = _collection(str(path or chroma_path()))
    where = {"type": entry_type} if entry_type else None
    if collection.count() == 0:
        return GuideSearchResult(
            query=query,
            hits=[],
            note="The guide index is empty: no entries have been verified and loaded yet.",
        )

    embed = embedder or get_embedder()
    res = collection.query(
        query_embeddings=embed([query]),
        n_results=min(limit, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    hits = []
    for text, meta, distance in zip(
        res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True
    ):
        source = str(meta.get("source", "original"))
        hits.append(
            GuideHit(
                title=str(meta["title"]),
                section=str(meta["section"]),
                type=str(meta["type"]),
                region=str(meta["region"]),
                text=text,
                score=round(1 - float(distance), 3),
                status=str(meta["status"]),
                source=source,
                credit=credit_for(source),
            )
        )
    note = None if hits else f"No guide entries of type {entry_type!r} matched."
    return GuideSearchResult(query=query, hits=hits, note=note)
