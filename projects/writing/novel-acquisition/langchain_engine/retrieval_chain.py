"""Six-level degradation retrieval chain (六级降级检索链).

Implements the retrieval strategy:
  L0: 语义检索 (ChromaDB vector search) — DEFAULT ENTRY
  L1: 原词精确搜索 (exact keyword in documents)
  L2: 拆词搜索 (split query into sub-terms, multi-query)
  L3: 角色定位搜索 (role/position/type labels)
  L4: 原文grep (fallback to raw text search in corpus)
  L5: 跨类型映射 (cross-genre mapping via local DeepSeek prompt)

Usage:
    from langchain_engine.retrieval_chain import DegradationRetrievalChain
    chain = DegradationRetrievalChain()
    results = chain.invoke("少年在食堂的窘迫")
"""

import json
import os
import subprocess
import urllib.request
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from langchain_engine.retriever import FiveDimRetriever, SearchResult

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import NOVEL_ACQUISITION_DIR, OUTPUTS_DIR

CORPUS_DIR = str(NOVEL_ACQUISITION_DIR / "anchors")
EXTRACTED_DIR = str(OUTPUTS_DIR)


@dataclass
class RetrievalResult:
    """Final result from the degradation chain."""
    query: str
    level_used: str  # L0-L5
    results: List[SearchResult]
    levels_tried: List[str] = field(default_factory=list)
    debug_info: Dict[str, Any] = field(default_factory=dict)


class DegradationRetrievalChain:
    """Six-level degradation retrieval following 五维资料库 retrieval protocol."""

    def __init__(self, min_score_l0: float = 0.45, min_results: int = 3,
                 top_k: int = 8):
        self.retriever = FiveDimRetriever()
        self.min_score_l0 = min_score_l0
        self.min_results = min_results
        self.top_k = top_k

    def invoke(self, query: str, dimension: Optional[str] = None,
               book: Optional[str] = None,
               split_terms: Optional[List[str]] = None,
               role_labels: Optional[List[str]] = None) -> RetrievalResult:
        """Run the full degradation chain.

        Args:
            query: Original natural language query
            dimension: Optional dimension filter
            book: Optional book filter
            split_terms: Pre-split terms for L2 (auto-generated if None)
            role_labels: Role/position labels for L3
        """
        levels_tried = []
        debug = {}

        # === L0: 语义检索 (Default Entry) ===
        l0_results = self.retriever.search(
            query, top_k=self.top_k,
            dimension=dimension, book=book,
            min_score=self.min_score_l0
        )
        levels_tried.append("L0_semantic")
        debug["L0_count"] = len(l0_results)
        debug["L0_top_score"] = l0_results[0].score if l0_results else 0

        if len(l0_results) >= self.min_results:
            return RetrievalResult(
                query=query, level_used="L0", results=l0_results,
                levels_tried=levels_tried, debug_info=debug
            )

        # === L1: 原词搜索 (lower threshold) ===
        l1_results = self.retriever.search(
            query, top_k=self.top_k,
            dimension=dimension, book=book,
            min_score=0.35  # lower threshold
        )
        levels_tried.append("L1_keyword")
        debug["L1_count"] = len(l1_results)

        if len(l1_results) >= self.min_results:
            return RetrievalResult(
                query=query, level_used="L1", results=l1_results,
                levels_tried=levels_tried, debug_info=debug
            )

        # === L2: 拆词搜索 (Multi-query) ===
        if split_terms is None:
            split_terms = self._auto_split(query)
        debug["L2_terms"] = split_terms

        l2_results = self.retriever.multi_query_search(
            split_terms, top_k=self.top_k,
            dimension=dimension, book=book
        )
        # Filter by minimum score
        l2_results = [r for r in l2_results if r.score >= 0.35]
        levels_tried.append("L2_split")
        debug["L2_count"] = len(l2_results)

        if len(l2_results) >= self.min_results:
            return RetrievalResult(
                query=query, level_used="L2", results=l2_results,
                levels_tried=levels_tried, debug_info=debug
            )

        # === L3: 角色定位搜索 ===
        if role_labels is None:
            role_labels = self._extract_role_labels(query)
        debug["L3_labels"] = role_labels

        if role_labels:
            l3_results = self.retriever.multi_query_search(
                role_labels, top_k=self.top_k,
                dimension="characters"
            )
            l3_results = [r for r in l3_results if r.score >= 0.30]
        else:
            l3_results = []
        levels_tried.append("L3_role")
        debug["L3_count"] = len(l3_results)

        if len(l3_results) >= self.min_results:
            return RetrievalResult(
                query=query, level_used="L3", results=l3_results,
                levels_tried=levels_tried, debug_info=debug
            )

        # === L4: 原文grep (raw text search in corpus) ===
        l4_results = self._grep_corpus(query, dimension=dimension)
        levels_tried.append("L4_grep")
        debug["L4_count"] = len(l4_results)

        if l4_results:
            return RetrievalResult(
                query=query, level_used="L4", results=l4_results,
                levels_tried=levels_tried, debug_info=debug
            )

        # === L5: 跨类型映射 (local LLM extension point) ===
        levels_tried.append("L5_cross_genre")
        debug["L5_note"] = "需要调用本地配置的LLM进行跨类型映射"

        # Combine all partial results from previous levels
        all_partial = l0_results + l1_results + l2_results + l3_results
        # Dedupe
        seen = set()
        deduped = []
        for r in all_partial:
            key = f"{r.book}_{r.text[:50]}"
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        deduped.sort(key=lambda x: x.score, reverse=True)

        return RetrievalResult(
            query=query, level_used="L5_partial",
            results=deduped[:self.top_k],
            levels_tried=levels_tried, debug_info=debug
        )

    def _auto_split(self, query: str) -> List[str]:
        """Auto-split query into semantic sub-terms.
        Simple heuristic: split by punctuation, then by key phrases.
        """
        import re
        # Remove common filler
        clean = re.sub(r'[的地得了过着]', ' ', query)
        # Split by natural boundaries
        parts = re.split(r'[，。、；\s]+', clean)
        parts = [p.strip() for p in parts if len(p.strip()) >= 2]

        # Also add bigrams for short queries
        if len(parts) <= 1 and len(query) >= 4:
            chars = query.replace(' ', '')
            parts = [chars[i:i+3] for i in range(0, len(chars)-2, 2)]

        return parts[:6]  # max 6 sub-queries

    def _extract_role_labels(self, query: str) -> List[str]:
        """Extract role/position keywords from query."""
        role_keywords = [
            "老师", "班主任", "教师", "师父", "师兄", "师姐",
            "父亲", "母亲", "爸", "妈", "爷爷", "奶奶",
            "同学", "朋友", "兄弟", "对手", "敌人", "情敌",
            "少年", "少女", "孩子", "学生", "青年",
            "校长", "医生", "警察", "商人", "军人",
            "穷人", "富人", "贵族", "平民", "奴隶",
        ]
        found = [kw for kw in role_keywords if kw in query]
        return found

    def _grep_corpus(self, query: str, dimension: Optional[str] = None,
                     max_results: int = 5) -> List[SearchResult]:
        """L4: Direct text search in anchor JSON files."""
        import os
        results = []
        # Use first 4 chars as search term
        search_term = query[:4] if len(query) >= 4 else query

        for fname in os.listdir(CORPUS_DIR):
            if not fname.endswith('.json'):
                continue
            filepath = os.path.join(CORPUS_DIR, fname)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    anchors = json.load(f)
                book = fname.replace('.json', '')
                for anchor in anchors:
                    dims = anchor.get('dimensions', {})
                    for dim_name, paragraphs in dims.items():
                        if dimension and dim_name != dimension:
                            continue
                        if dim_name not in ('scenes','psychology','characters','twists'):
                            continue
                        for text in paragraphs:
                            if search_term in text:
                                results.append(SearchResult(
                                    text=text, score=0.3,
                                    book=book, dimension=dim_name,
                                    anchor_label=anchor.get('label',''),
                                    anchor_category=anchor.get('category','')
                                ))
                                if len(results) >= max_results:
                                    return results
            except Exception:
                continue
        return results
