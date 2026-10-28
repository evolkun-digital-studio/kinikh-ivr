import pytest
import asyncio
import re
from unittest.mock import AsyncMock, patch

from app.rag.index import KnowledgeIndex
from app.rag.models import Chunk
from app.core.config import Settings, get_settings
from app.rag.chunker import chunk_documents
from app.rag.loader import load_documents


@pytest.fixture
def mock_chunks():
    chunks = []
    for i in range(16):
        chunks.append(
            Chunk(
                chunk_id=f"doc#{i}",
                source="doc.md",
                document_name="Doc",
                section=f"Section {i}",
                text=f"This is section {i} about something.",
                index_text=f"doc doc section {i} about something",
                facts={},
                is_demo=False,
                content_hash=f"hash_{i}",
            )
        )
    # Add a specific chunk to test retrieval
    chunks[-1] = Chunk(
        chunk_id="doc#15",
        source="doc.md",
        document_name="Doc",
        section="Unique Section",
        text="This is a unique chunk about banana pancakes.",
        index_text="unique chunk banana pancakes",
        facts={},
        is_demo=False,
        content_hash="hash_15",
    )
    return chunks


def test_training_manual_contains_exactly_five_clean_documents():
    settings = get_settings()
    documents = load_documents(settings.rag_knowledge_dir)
    assert [document.name for document in documents] == [
        "company.md",
        "contact.md",
        "faqs.md",
        "routing.md",
        "services.md",
    ]

    chunks = chunk_documents(documents)
    assert chunks
    for chunk in chunks:
        assert "Architecture" not in chunk.text
        assert "Interiors" not in chunk.text
        assert re.search(r"(?<!\d)\d{10}(?!\d)", chunk.text) is None


@pytest.fixture
def mock_settings(monkeypatch):
    settings = get_settings().model_copy(update={
        "rag_mode": "retrieval",
        "rag_full_context_max_chars": 40000,
        "rag_hybrid_lexical_weight": 0.5,
        "rag_query_timeout_ms": 400
    })
    monkeypatch.setattr("app.rag.index.get_settings", lambda: settings)
    return settings


@pytest.mark.asyncio
async def test_hybrid_search(mock_chunks, mock_settings, monkeypatch):
    index = KnowledgeIndex()
    index.chunks = mock_chunks
    index.mode = "retrieval"
    
    # Mock BM25 build
    from app.rag.index import BM25, _tokenize
    corpus = [_tokenize(c.index_text) for c in mock_chunks]
    index.bm25 = BM25(corpus)
    
    # Mock embeddings: vectors of length 3
    index.embeddings = [[0.1, 0.1, 0.1] for _ in range(15)]
    index.embeddings.append([0.9, 0.9, 0.9])  # the banana pancake chunk
    
    # Mock OpenAIClient
    mock_client = AsyncMock()
    mock_client.embed.return_value = [[0.9, 0.9, 0.9]]  # Query embedding
    monkeypatch.setattr("app.rag.index.get_openai_client", lambda: mock_client)
    
    result = await index.search("banana pancakes", top_k=2)
    assert result.mode == "hybrid"
    assert len(result.chunks) == 2
    assert result.chunks[0].chunk.section == "Unique Section"
    assert result.chunks[0].vector_score > 0.9  # high cosine similarity


@pytest.mark.asyncio
async def test_lexical_fallback_on_embedding_failure(mock_chunks, mock_settings, monkeypatch):
    index = KnowledgeIndex()
    index.chunks = mock_chunks
    index.mode = "retrieval"
    
    from app.rag.index import BM25, _tokenize
    corpus = [_tokenize(c.index_text) for c in mock_chunks]
    index.bm25 = BM25(corpus)
    index.embeddings = [[0.1, 0.1, 0.1] for _ in range(16)]
    
    # Mock OpenAIClient to raise exception (timeout/failure)
    mock_client = AsyncMock()
    mock_client.embed.side_effect = Exception("OpenAI API timeout")
    monkeypatch.setattr("app.rag.index.get_openai_client", lambda: mock_client)
    
    result = await index.search("banana pancakes", top_k=2)
    assert result.mode == "lexical"
    assert len(result.chunks) == 2
    assert result.chunks[0].chunk.section == "Unique Section"

@pytest.mark.asyncio
async def test_retrieve_context_timeout(monkeypatch):
    from app.rag.index import KnowledgeIndex
    from app.rag.retriever import Retriever
    index = KnowledgeIndex()
    index.mode = "retrieval"
    
    # Make search sleep longer than timeout
    async def slow_search(*args, **kwargs):
        await asyncio.sleep(0.5)
        return []
        
    index.search = slow_search
    retriever = Retriever(index)
    
    from app.core.config import get_settings
    settings = get_settings().model_copy(update={
        "rag_mode": "auto",
        "rag_query_timeout_ms": 100
    })
    monkeypatch.setattr("app.rag.retriever.get_settings", lambda: settings)
    
    context = await retriever.retrieve_context("test query")
    assert context == ""



from fastapi.testclient import TestClient
from app.main import app

def test_auth_protected_reindexing():
    client = TestClient(app, raise_server_exceptions=False)
    # Without auth header should fail with 422 (FastAPI validation)
    response = client.post("/api/v1/system/rag/reindex")
    assert response.status_code == 422
    
    # With mocked auth should pass (or attempt to run)
    from app.core.dependencies import get_current_user_id
    app.dependency_overrides[get_current_user_id] = lambda: "admin"
    
    # We also need to mock index.build() so we don't actually build
    with patch("app.rag.index.KnowledgeIndex.build", new_callable=AsyncMock) as mock_build:
        response = client.post("/api/v1/system/rag/reindex")
        assert response.status_code == 200
        assert "mode" in response.json()
        mock_build.assert_called_once()
        
    # Clean up
    app.dependency_overrides.clear()
