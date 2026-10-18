"""Keyword ranking over real workspace chunks; no model or embedding call."""
import re
import unicodedata

from app.ports import Chunk


def words(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text).casefold()
    return set(re.findall(r"\w+", "".join(c for c in text if not unicodedata.combining(c))))


def rank(chunks: list[Chunk], query: str, limit: int) -> list[tuple[Chunk, float]]:
    terms = words(query)
    if not terms:
        return []
    scored = [(chunk, len(terms & words(chunk.text)) / len(terms)) for chunk in chunks]
    return sorted((hit for hit in scored if hit[1] > 0), key=lambda hit: (-hit[1], hit[0].id))[:limit]
