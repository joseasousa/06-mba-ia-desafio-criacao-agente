from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path


TOKEN_RE = re.compile(r"[a-z0-9]+")
ARTICLE_RE = re.compile(r"(?=\*\*Art\.\s*\d+[^*]*\*\*)")


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return [token for token in TOKEN_RE.findall(normalized) if len(token) > 2]


class RegulationIndex:
    """Índice lexical local que nunca entrega o regulamento inteiro ao modelo."""

    def __init__(self, path: Path):
        self.path = path
        self._chunks = self._load_chunks()

    def _load_chunks(self) -> list[str]:
        text = self.path.read_text(encoding="utf-8")
        chunks: list[str] = []
        current_heading = ""
        for chapter in re.split(r"(?=^## )", text, flags=re.MULTILINE):
            if not chapter.strip() or chapter.startswith("# "):
                continue
            lines = chapter.splitlines()
            current_heading = lines[0].strip()
            body = "\n".join(lines[1:]).strip()
            articles = [part.strip() for part in ARTICLE_RE.split(body) if part.strip()]
            for article in articles:
                chunks.append(f"{current_heading}\n{article}"[:4000])
        return chunks

    def search(self, query: str, limit: int = 3) -> list[str]:
        query_counts = Counter(_tokens(query))
        ranked: list[tuple[float, str]] = []
        for chunk in self._chunks:
            chunk_counts = Counter(_tokens(chunk))
            score = sum(
                (3.0 if token in {"domingo", "domingos", "piscina", "visitante", "visitantes"} else 1.0)
                * min(count, chunk_counts[token])
                for token, count in query_counts.items()
            )
            if score:
                ranked.append((score, chunk))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [chunk for _, chunk in ranked[: max(1, min(limit, 3))]]

