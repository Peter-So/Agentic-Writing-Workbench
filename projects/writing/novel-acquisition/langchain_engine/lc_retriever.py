"""LangChain-native retriever implementing BaseRetriever with six-level degradation.

Uses LangChain's retriever protocol so it plugs into any LangChain chain.
"""

from typing import List, Optional, Any
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field

from langchain_engine.retriever import FiveDimRetriever


class FiveDimLangChainRetriever(BaseRetriever):
    """LangChain-compatible retriever wrapping FiveDimRetriever with degradation.

    Implements BaseRetriever so it can be used in:
      - RetrievalQA chains
      - create_retrieval_chain
      - Any LCEL pipe expecting a Retriever
    """

    top_k: int = Field(default=8, description="Max results to return")
    min_score_l0: float = Field(default=0.45, description="L0 semantic threshold")
    min_results: int = Field(default=3, description="Min results before degrading")
    dimension: Optional[str] = Field(default=None, description="Filter by dimension")
    book: Optional[str] = Field(default=None, description="Filter by book")

    _retriever: FiveDimRetriever = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._retriever = FiveDimRetriever()

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        """Six-level degradation retrieval returning LangChain Documents.

        L0: Semantic (score >= 0.45)
        L1: Lower threshold (score >= 0.35)
        L2: Auto-split multi-query
        L3: Role-label search
        L4: Grep fallback
        """
        # L0: Semantic search
        results = self._retriever.search(
            query, top_k=self.top_k,
            dimension=self.dimension, book=self.book,
            min_score=self.min_score_l0
        )

        if len(results) >= self.min_results:
            return self._to_documents(results, level="L0")

        # L1: Lower threshold
        results_l1 = self._retriever.search(
            query, top_k=self.top_k,
            dimension=self.dimension, book=self.book,
            min_score=0.35
        )
        if len(results_l1) >= self.min_results:
            return self._to_documents(results_l1, level="L1")

        # L2: Split query into sub-terms
        import re
        clean = re.sub(r'[的地得了过着]', ' ', query)
        parts = [p.strip() for p in re.split(r'[，。、；\s]+', clean) if len(p.strip()) >= 2]
        if not parts:
            parts = [query[:4]]

        results_l2 = self._retriever.multi_query_search(
            parts[:6], top_k=self.top_k,
            dimension=self.dimension, book=self.book
        )
        results_l2 = [r for r in results_l2 if r.score >= 0.35]
        if len(results_l2) >= self.min_results:
            return self._to_documents(results_l2, level="L2")

        # L3: Role-label keywords
        role_kws = ["老师", "班主任", "父亲", "母亲", "同学", "朋友",
                    "少年", "少女", "学生", "对手"]
        found_roles = [kw for kw in role_kws if kw in query]
        if found_roles:
            results_l3 = self._retriever.multi_query_search(
                found_roles, top_k=self.top_k, dimension="characters"
            )
            results_l3 = [r for r in results_l3 if r.score >= 0.30]
            if results_l3:
                return self._to_documents(results_l3, level="L3")

        # L4: Combine all partial results
        all_results = results + results_l1 + results_l2
        seen = set()
        deduped = []
        for r in sorted(all_results, key=lambda x: x.score, reverse=True):
            key = f"{r.book}_{r.text[:50]}"
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        return self._to_documents(deduped[:self.top_k], level="L4_partial")

    def _to_documents(self, results, level: str) -> List[Document]:
        """Convert SearchResult to LangChain Document with rich metadata."""
        docs = []
        for r in results:
            docs.append(Document(
                page_content=r.text,
                metadata={
                    "book": r.book,
                    "dimension": r.dimension,
                    "anchor_label": r.anchor_label,
                    "anchor_category": r.anchor_category,
                    "score": round(r.score, 4),
                    "retrieval_level": level,
                    "source": f"{r.book}·{r.anchor_label}",
                }
            ))
        return docs
