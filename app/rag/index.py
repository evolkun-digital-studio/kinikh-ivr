"""Knowledge index for RAG — lexical (BM25) and vector (OpenAI) hybrid search."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
from collections import Counter
from pathlib import Path

from loguru import logger

from app.ai.openai_client import get_openai_client
from app.core.config import get_settings
from app.rag.chunker import chunk_documents
from app.rag.loader import load_documents
from app.rag.models import Chunk, RetrievedChunk, RetrievalResult, RetrievalTiming


def _tokenize(text: str) -> list[str]:
    # Keep Devanagari and alphanumeric, lowercase.
    # [\u0900-\u097F] covers Devanagari.
    return re.findall(r'[a-z0-9\u0900-\u097F]+', text.lower())


class BM25:
    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.avgdl = sum(len(doc) for doc in corpus) / max(self.corpus_size, 1)
        self.doc_freqs = []
        self.doc_lens = []
        self.idf = {}
        
        df = Counter()
        for doc in corpus:
            self.doc_lens.append(len(doc))
            frequencies = Counter(doc)
            self.doc_freqs.append(frequencies)
            for term in frequencies:
                df[term] += 1
                
        for term, freq in df.items():
            self.idf[term] = math.log(1 + (self.corpus_size - freq + 0.5) / (freq + 0.5))

    def get_scores(self, query: list[str]) -> list[float]:
        scores = [0.0] * self.corpus_size
        for i in range(self.corpus_size):
            score = 0.0
            doc_len = self.doc_lens[i]
            frequencies = self.doc_freqs[i]
            for term in query:
                if term not in frequencies:
                    continue
                freq = frequencies[term]
                num = freq * (self.k1 + 1)
                den = freq + self.k1 * (1 - self.b + self.b * doc_len / max(self.avgdl, 1))
                score += self.idf.get(term, 0.0) * (num / den)
            scores[i] = score
        return scores


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(a * a for a in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


class KnowledgeIndex:
    def __init__(self):
        self.chunks: list[Chunk] = []
        self.embeddings: list[list[float]] = []
        self.bm25: BM25 | None = None
        self.mode: str = "off"
        self._build_lock = asyncio.Lock()

    async def build(self) -> None:
        async with self._build_lock:
            settings = get_settings()
            docs = load_documents(settings.rag_knowledge_dir)
            self.chunks = chunk_documents(docs)
            
            # Combined hash
            import hashlib
            combined_hash = hashlib.sha256("".join(c.content_hash for c in self.chunks).encode()).hexdigest()[:8]
            
            index_dir = Path(settings.rag_index_dir)
            index_dir.mkdir(parents=True, exist_ok=True)
            meta_file = index_dir / "meta.json"
            
            rebuild_needed = True
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        if json.load(f).get("hash") == combined_hash:
                            rebuild_needed = False
                except Exception:
                    pass
                    
            if rebuild_needed:
                for cache_file in index_dir.glob("*.json"):
                    try:
                        cache_file.unlink()
                    except Exception:
                        pass
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump({"hash": combined_hash}, f)
            
            total_chars = sum(len(c.text) for c in self.chunks)
            if settings.rag_mode == "auto":
                self.mode = "full" if total_chars <= settings.rag_full_context_max_chars else "retrieval"
            else:
                self.mode = settings.rag_mode
                
            if self.mode == "retrieval":
                await self._build_retrieval_index()
                
            logger.info(f"[RAG] loaded {len(docs)} documents, {len(self.chunks)} chunks, hash={combined_hash} from {settings.rag_knowledge_dir}")

    async def _build_retrieval_index(self) -> None:
        settings = get_settings()
        index_dir = Path(settings.rag_index_dir)
        
        # BM25
        corpus = [_tokenize(c.index_text) for c in self.chunks]
        self.bm25 = BM25(corpus)
        
        # Embeddings
        self.embeddings = []
        missing_indices = []
        texts_to_embed = []
        
        for i, chunk in enumerate(self.chunks):
            cache_file = index_dir / f"{chunk.content_hash}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if "embedding" in data:
                            self.embeddings.append(data["embedding"])
                            continue
                except Exception:
                    pass
            self.embeddings.append([])
            missing_indices.append(i)
            texts_to_embed.append(chunk.index_text)
            
        if missing_indices:
            client = get_openai_client()
            batch_size = 100
            for i in range(0, len(texts_to_embed), batch_size):
                batch_texts = texts_to_embed[i:i+batch_size]
                batch_indices = missing_indices[i:i+batch_size]
                
                logger.info(f"[RAG] Embedding batch {i//batch_size + 1}, size {len(batch_texts)}")
                vectors = await client.embed(batch_texts, model=settings.rag_embedding_model)
                
                for idx, vector in zip(batch_indices, vectors):
                    self.embeddings[idx] = vector
                    chunk = self.chunks[idx]
                    cache_file = index_dir / f"{chunk.content_hash}.json"
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump({"embedding": vector}, f)
        
    def full_context(self) -> str:
        if not self.chunks:
            return ""
            
        by_doc: dict[str, list[str]] = {}
        for c in self.chunks:
            text = c.text
            if c.facts:
                fact_lines = "\n".join(f"- **{k.title()}:** {v}" for k, v in c.facts.items())
                text += f"\n{fact_lines}"
            by_doc.setdefault(c.document_name, []).append(text)
            
        blocks = []
        for doc_title, texts in by_doc.items():
            blocks.append(f"--- {doc_title} ---\n" + "\n\n".join(texts))
        return "\n\n".join(blocks)
        
    async def search(self, query: str, top_k: int) -> RetrievalResult:
        settings = get_settings()
        timing = RetrievalTiming()
        start_time = asyncio.get_running_loop().time()
        
        if not self.chunks:
            return RetrievalResult(
                query=query, context_query=None, mode="vector", 
                chunks=[], timing=timing
            )
            
        client = get_openai_client()
        query_vector = None
        
        # Try embed
        embed_start = asyncio.get_running_loop().time()
        try:
            vectors = await client.embed(
                [query], 
                model=settings.rag_embedding_model, 
                timeout=settings.rag_query_timeout_ms / 1000.0
            )
            query_vector = vectors[0]
            timing.query_embedding_ms = (asyncio.get_running_loop().time() - embed_start) * 1000
        except Exception as exc:
            logger.warning(f"[RAG] Query embedding failed/timeout: {exc}")
            
        lexical_start = asyncio.get_running_loop().time()
        bm25_scores = self.bm25.get_scores(_tokenize(query)) if self.bm25 else [0.0] * len(self.chunks)
        timing.lexical_search_ms = (asyncio.get_running_loop().time() - lexical_start) * 1000
        
        max_bm25 = max(bm25_scores) if bm25_scores else 0.0
        normalized_bm25 = [s / max_bm25 if max_bm25 > 0 else 0.0 for s in bm25_scores]
        
        vector_start = asyncio.get_running_loop().time()
        cosine_scores = [0.0] * len(self.chunks)
        if query_vector:
            for i, emb in enumerate(self.embeddings):
                if emb:
                    cosine_scores[i] = _cosine_similarity(query_vector, emb)
        timing.vector_search_ms = (asyncio.get_running_loop().time() - vector_start) * 1000
        
        scored_chunks = []
        mode = "hybrid" if query_vector else "lexical"
        weight = settings.rag_hybrid_lexical_weight
        
        for i, chunk in enumerate(self.chunks):
            if query_vector:
                score = (1 - weight) * cosine_scores[i] + weight * normalized_bm25[i]
            else:
                score = normalized_bm25[i]
            scored_chunks.append((score, cosine_scores[i], normalized_bm25[i], chunk))
            
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        top_scored = scored_chunks[:top_k]
        
        retrieved = []
        for rank, (score, v_score, l_score, chunk) in enumerate(top_scored, 1):
            retrieved.append(RetrievedChunk(
                chunk=chunk,
                rank=rank,
                score=score,
                vector_score=v_score,
                lexical_score=l_score
            ))
            
        timing.retrieval_total_ms = (asyncio.get_running_loop().time() - start_time) * 1000
        return RetrievalResult(
            query=query,
            context_query=None,
            mode=mode,
            chunks=retrieved,
            timing=timing
        )


_index_singleton: KnowledgeIndex | None = None

def get_knowledge_index() -> KnowledgeIndex:
    global _index_singleton
    if _index_singleton is None:
        _index_singleton = KnowledgeIndex()
    return _index_singleton
