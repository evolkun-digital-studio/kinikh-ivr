"""Section-aware chunking: one chunk per Markdown section.

Company knowledge is small and already organized under headings, so a
section is the natural unit — a heading is never separated from its content
and each chunk can answer a question on its own. Only a section longer than
`max_chars` is split further, on paragraph boundaries, with the heading
repeated on every part. There is no sliding-window overlap: the repeated
heading carries the context overlap would otherwise provide.
"""

from __future__ import annotations

import hashlib
import re

from app.rag.models import Chunk, KnowledgeDocument

DEFAULT_MAX_CHARS = 1200

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SEARCH_TERMS = re.compile(r"<!--\s*search-terms:\s*(.*?)\s*-->", re.DOTALL | re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FACT = re.compile(r"^\s*[-*]\s+\*\*(.+?):\*\*\s*(.+?)\s*$")
_BLANK_RUNS = re.compile(r"\n{3,}")
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _NON_SLUG.sub("-", text.lower()).strip("-") or "section"


def _split_long(body: str, max_chars: int) -> list[str]:
    if len(body) <= max_chars:
        return [body]
    parts: list[str] = []
    current = ""
    for paragraph in body.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if current and len(candidate) > max_chars:
            parts.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _sections(text: str) -> list[tuple[list[str], str]]:
    """(heading path, raw body) for every stretch of text under a heading."""
    sections: list[tuple[list[str], str]] = []
    stack: list[tuple[int, str]] = []
    lines: list[str] = []

    def flush() -> None:
        if lines:
            sections.append(([title for _, title in stack], "\n".join(lines)))
            lines.clear()

    for line in text.split("\n"):
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
        else:
            lines.append(line)
    flush()
    return sections


def chunk_document(
    document: KnowledgeDocument,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_search_terms: bool = True,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    used_ids: set[str] = set()

    for path, raw in _sections(document.text):
        search_terms = ", ".join(term.strip() for term in _SEARCH_TERMS.findall(raw))
        body = _BLANK_RUNS.sub("\n\n", _COMMENT.sub("", raw)).strip()
        if not body:
            continue

        # The document's H1 is its title, not a section of its own.
        below_title = path[1:] if len(path) > 1 else path
        section = " › ".join(below_title) if below_title else document.title
        facts = {
            match.group(1).strip().lower(): match.group(2).strip()
            for match in map(_FACT.match, body.split("\n"))
            if match
        }

        parts = _split_long(body, max_chars)
        for number, part in enumerate(parts, start=1):
            slug = slugify(section)
            if len(parts) > 1:
                slug = f"{slug}-part-{number}"
            chunk_id = f"{document.name}#{slug}"
            while chunk_id in used_ids:
                chunk_id += "-dup"
            used_ids.add(chunk_id)

            index_text = f"{document.title} › {section}\n{part}"
            if include_search_terms and search_terms:
                index_text += f"\nRelated terms: {search_terms}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    source=document.name,
                    document_name=document.title,
                    section=section,
                    text=f"{section}\n{part}",
                    index_text=index_text,
                    facts=facts,
                    is_demo=document.is_demo,
                    content_hash=hashlib.sha256(index_text.encode("utf-8")).hexdigest()[:16],
                )
            )
    return chunks


def chunk_documents(
    documents: list[KnowledgeDocument],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_search_terms: bool = True,
) -> list[Chunk]:
    return [
        chunk
        for document in documents
        for chunk in chunk_document(
            document, max_chars=max_chars, include_search_terms=include_search_terms
        )
    ]
