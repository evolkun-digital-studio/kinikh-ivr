"""Loads company knowledge documents (Markdown) from a directory."""

from __future__ import annotations

from pathlib import Path
import re
import unicodedata

from loguru import logger

from app.rag.models import KnowledgeDocument

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_BLANK_RUNS = re.compile(r"\n{3,}")


def normalize_text(raw: str) -> str:
    """Unicode NFC, LF newlines, no trailing whitespace, no blank-line runs."""
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    text = text.lstrip("﻿")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_RUNS.sub("\n\n", text).strip() + "\n"


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).split("\n"):
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()
    return meta, text[match.end() :]


def load_documents(directory: str | Path) -> list[KnowledgeDocument]:
    """Every *.md file in `directory`, sorted by name for a stable index."""
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"Knowledge directory not found: {root}")

    documents: list[KnowledgeDocument] = []
    for path in sorted(root.glob("*.md")):
        meta, body = _split_front_matter(normalize_text(path.read_text(encoding="utf-8")))
        documents.append(
            KnowledgeDocument(
                name=path.name,
                title=meta.get("title") or path.stem.replace("_", " ").title(),
                text=body.strip() + "\n",
                is_demo=meta.get("status", "").lower() == "demo",
                path=str(path),
            )
        )
    logger.info(f"[RAG] loaded {len(documents)} knowledge documents from {root}")
    return documents
