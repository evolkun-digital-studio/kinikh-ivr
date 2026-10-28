import asyncio
from loguru import logger

from app.core.config import get_settings
from app.rag.index import KnowledgeIndex, get_knowledge_index

class Retriever:
    _instance: "Retriever | None" = None

    def __init__(self, index: KnowledgeIndex):
        self.index = index

    async def retrieve_context(self, text: str) -> str:
        if not text:
            return ""
            
        settings = get_settings()
        if settings.rag_mode == "off":
            return ""

        # "auto" will set index.mode to "full" or "retrieval" when build() is called
        if self.index.mode == "full" or self.index.mode == "auto":
            return self.index.full_context()

        # index.mode is "retrieval"
        try:
            result = await asyncio.wait_for(
                self.index.search(text, top_k=settings.rag_top_k),
                timeout=settings.rag_query_timeout_ms / 1000.0,
            )
            if not result.chunks:
                return ""
            
            blocks = []
            for r_chunk in result.chunks:
                c = r_chunk.chunk
                content = c.text
                if c.facts:
                    fact_lines = "\n".join(f"- **{k.title()}:** {v}" for k, v in c.facts.items())
                    content += f"\n{fact_lines}"
                blocks.append(f"--- {c.document_name} ---\n{content}")
            return "\n\n".join(blocks)
            
        except asyncio.TimeoutError:
            logger.warning("[RAG] Retrieval timed out, returning empty context")
            return ""
        except Exception as e:
            logger.error(f"[RAG] Retrieval failed: {e}")
            return ""

def get_retriever() -> Retriever:
    if Retriever._instance is None:
        Retriever._instance = Retriever(get_knowledge_index())
    return Retriever._instance
