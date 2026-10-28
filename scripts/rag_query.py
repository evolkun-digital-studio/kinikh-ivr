"""CLI utility to query the RAG knowledge index."""

import argparse
import asyncio
import sys

from app.rag.index import get_knowledge_index

async def main():
    parser = argparse.ArgumentParser(description="Query the Knowledge Index directly")
    parser.add_argument("query", help="The search query")
    parser.add_argument("--top-k", type=int, default=4, help="Number of results")
    args = parser.parse_args()
    
    index = get_knowledge_index()
    await index.build()
    print(f"Index mode: {index.mode}")
    print(f"Query: {args.query}")
    
    if index.mode == "full":
        print("\nIndex is in 'full' mode (too small for retrieval).")
        return
        
    result = await index.search(args.query, top_k=args.top_k)
    print(f"\nFound {len(result.chunks)} chunks in {result.timing.retrieval_total_ms:.1f}ms")
    print(f"Mode used: {result.mode}")
    
    for rank, rc in enumerate(result.chunks, 1):
        print(f"\n--- Rank {rank} | Score: {rc.score:.3f} (V:{rc.vector_score or 0:.3f}, L:{rc.lexical_score or 0:.3f}) ---")
        print(f"Section: {rc.chunk.section}")
        print(f"Source: {rc.chunk.source}")
        print(f"Text Preview:\n{rc.chunk.text[:200]}...")


if __name__ == "__main__":
    asyncio.run(main())
