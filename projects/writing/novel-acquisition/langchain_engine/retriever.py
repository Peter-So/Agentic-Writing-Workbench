"""ChromaDB vector retriever for 五维资料库.

Usage:
    from langchain_engine.retriever import FiveDimRetriever
    retriever = FiveDimRetriever()
    results = retriever.search("少年的沉默与隐忍", top_k=5)
    results = retriever.search("校园欺凌", top_k=5, dimension="psychology")
"""

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

CHROMA_URL = os.environ.get("CHROMA_URL", "http://chroma:8000")
EMBEDDING_URL = os.environ.get("EMBEDDING_URL", "http://embedding:8001/embed")
COLLECTION_NAME = "five_dimensions"


@dataclass
class SearchResult:
    text: str
    score: float
    book: str
    dimension: str
    anchor_label: str
    anchor_category: str
    metadata: dict = field(default_factory=dict)


class FiveDimRetriever:
    """Semantic retriever over 五维资料库 via ChromaDB + Embedding sidecar."""

    def __init__(self, chroma_url=CHROMA_URL, embedding_url=EMBEDDING_URL,
                 collection_name=COLLECTION_NAME):
        self.chroma_url = chroma_url
        self.embedding_url = embedding_url
        self.collection_name = collection_name
        self._collection_id = None

    @property
    def collection_id(self) -> str:
        if self._collection_id is None:
            url = f"{self.chroma_url}/api/v2/tenants/default_tenant/databases/default_database/collections"
            with urllib.request.urlopen(url, timeout=10) as resp:
                collections = json.loads(resp.read().decode("utf-8"))
            for c in collections:
                if c["name"] == self.collection_name:
                    self._collection_id = c["id"]
                    break
            if not self._collection_id:
                raise RuntimeError(f"Collection '{self.collection_name}' not found")
        return self._collection_id

    def _embed(self, text: str) -> List[float]:
        payload = json.dumps({"texts": [text]}).encode("utf-8")
        req = urllib.request.Request(
            self.embedding_url, data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))["embeddings"][0]

    def search(self, query: str, top_k: int = 5,
               dimension: Optional[str] = None,
               book: Optional[str] = None,
               min_score: float = 0.0) -> List[SearchResult]:
        """Semantic search over 五维资料库.

        Args:
            query: Natural language query
            top_k: Max results to return
            dimension: Filter by dimension (scenes/psychology/characters/twists)
            book: Filter by book name
            min_score: Minimum cosine similarity (0-1)

        Returns:
            List of SearchResult sorted by score descending
        """
        embedding = self._embed(query)

        body = {
            "query_embeddings": [embedding],
            "n_results": top_k * 2,  # over-fetch for filtering
            "include": ["documents", "metadatas", "distances"]
        }

        # Build where filter
        where_clauses = []
        if dimension:
            where_clauses.append({"dimension": {"$eq": dimension}})
        if book:
            where_clauses.append({"book": {"$eq": book}})

        if len(where_clauses) == 1:
            body["where"] = where_clauses[0]
        elif len(where_clauses) > 1:
            body["where"] = {"$and": where_clauses}

        url = (f"{self.chroma_url}/api/v2/tenants/default_tenant/"
               f"databases/default_database/collections/{self.collection_id}/query")
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        docs = result["documents"][0]
        metas = result["metadatas"][0]
        dists = result["distances"][0]

        results = []
        for doc, meta, dist in zip(docs, metas, dists):
            score = 1 - dist  # cosine distance → similarity
            if score < min_score:
                continue
            results.append(SearchResult(
                text=doc,
                score=score,
                book=meta.get("book", ""),
                dimension=meta.get("dimension", ""),
                anchor_label=meta.get("anchor_label", ""),
                anchor_category=meta.get("anchor_category", ""),
                metadata=meta
            ))

        return results[:top_k]

    def multi_query_search(self, queries: List[str], top_k: int = 5,
                           dedupe: bool = True, **kwargs) -> List[SearchResult]:
        """Search with multiple queries (L2拆词 equivalent), dedupe by doc."""
        seen_ids = set()
        all_results = []
        for q in queries:
            hits = self.search(q, top_k=top_k, **kwargs)
            for h in hits:
                doc_key = f"{h.book}_{h.anchor_label}_{h.text[:50]}"
                if dedupe and doc_key in seen_ids:
                    continue
                seen_ids.add(doc_key)
                all_results.append(h)

        # Sort by score and return top_k
        all_results.sort(key=lambda x: x.score, reverse=True)
        return all_results[:top_k]
