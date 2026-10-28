"""Data types shared across the RAG layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SupportLevel = Literal["strong", "partial", "insufficient"]
RetrievalMode = Literal["vector", "lexical", "hybrid"]


@dataclass(frozen=True)
class KnowledgeDocument:
    """One knowledge file after normalization (front matter removed)."""

    name: str  # file name, e.g. "services.md"
    title: str  # human title from front matter, e.g. "Services"
    text: str
    is_demo: bool
    path: str


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit of company knowledge — a whole document section."""

    chunk_id: str  # "services.md#interior-design"
    source: str  # "services.md"
    document_name: str  # "Services"
    section: str  # "Interior Design"
    # What the LLM sees: the section heading followed by its body.
    text: str
    # What is embedded and keyword-indexed: breadcrumb + text + search terms.
    # Search terms help retrieval but are never shown to the LLM, so an alias
    # can't be mistaken for an additional company capability.
    index_text: str
    # Exact values declared as "- **Key:** value" lines in the section.
    facts: dict[str, str] = field(default_factory=dict)
    is_demo: bool = False
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    rank: int  # 1-based
    score: float  # the score chunks were ranked by in this mode
    vector_score: float | None = None  # cosine similarity
    lexical_score: float | None = None  # normalized BM25, 0..1


@dataclass(frozen=True)
class FactConflict:
    """The same fact key declared with different values in different chunks."""

    key: str
    values: list[dict[str, str]]  # [{"value": ..., "chunk_id": ...}, ...]

    @property
    def chunk_ids(self) -> list[str]:
        return [entry["chunk_id"] for entry in self.values]


@dataclass
class RetrievalTiming:
    query_embedding_ms: float = 0.0
    vector_search_ms: float = 0.0
    lexical_search_ms: float = 0.0
    retrieval_total_ms: float = 0.0


@dataclass
class RetrievalResult:
    query: str
    # Previous user turn + the question, searched alongside `query` so a
    # follow-up like "What about interiors?" still finds the right section.
    context_query: str | None
    mode: RetrievalMode
    chunks: list[RetrievedChunk]
    timing: RetrievalTiming
    embedding_cache_hit: bool = False
    conflicts: list[FactConflict] = field(default_factory=list)

    def top(self, k: int) -> list[RetrievedChunk]:
        return self.chunks[:k]


@dataclass
class RAGTiming:
    query_embedding_ms: float = 0.0
    vector_search_ms: float = 0.0
    lexical_search_ms: float = 0.0
    retrieval_total_ms: float = 0.0
    llm_ttft_ms: float | None = None
    llm_total_ms: float = 0.0
    total_response_ms: float = 0.0


@dataclass
class RAGResponse:
    question: str
    answer: str
    # Chunk ids the model said it used (e.g. "services.md#interior-design").
    sources: list[str]
    # Chunks actually placed in the prompt, in prompt order.
    retrieved_chunks: list[RetrievedChunk]
    scores: list[float]
    # Evidence signal, not a calibrated probability. See RAGService.
    support_level: SupportLevel
    model_support: SupportLevel | None
    retrieval_support: SupportLevel
    conflicting_sources: bool
    conflicts: list[FactConflict]
    # Numbers / times / URLs / emails in the answer that appear in neither the
    # supplied context nor the conversation — a cheap deterministic
    # hallucination signal.
    ungrounded_values: list[str]
    timing: RAGTiming
    retrieval_mode: RetrievalMode
    # Debug: exactly what was sent to the LLM (no credentials are ever in it).
    context: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    history_used: list[dict[str, str]] = field(default_factory=list)

    @property
    def retrieval_ms(self) -> float:
        return self.timing.retrieval_total_ms

    @property
    def llm_ms(self) -> float:
        return self.timing.llm_total_ms

    @property
    def total_ms(self) -> float:
        return self.timing.total_response_ms


@dataclass(frozen=True)
class RAGStreamEvent:
    """One step of a streamed answer: retrieval done, a text delta, or the
    final response."""

    type: Literal["retrieval", "token", "final"]
    text: str = ""
    retrieval: RetrievalResult | None = None
    response: RAGResponse | None = None
