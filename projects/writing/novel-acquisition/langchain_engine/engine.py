"""LangChain Writing Engine — 统一入口.

Provides a single entry point for all writing workflow operations:
  - semantic_search: 五维语义检索
  - material_assemble: 材料装配
  - pre_review: 预审查(硬规则)
  - model_review: 模型审查(DeepSeek五维度)
  - full_pipeline: 完整流水线(检索→装配→写作→审查→修复)

Usage (CLI):
    python3 -m langchain_engine.engine search "少年的沉默"
    python3 -m langchain_engine.engine assemble --chapter 1
    python3 -m langchain_engine.engine pre-review --file chapters/ch01.md
    python3 -m langchain_engine.engine model-review --file chapters/ch01.md

Usage (Python):
    from langchain_engine.engine import WritingEngine
    engine = WritingEngine()
    results = engine.search("少年的沉默与隐忍")
    bundle = engine.assemble(chapter=1)
    pre = engine.pre_review("chapter text here")
    review = engine.model_review("chapter text here", bundle)
"""

import sys
import json
import argparse
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import DEFAULT_NOVEL_DIR

from langchain_engine.retriever import FiveDimRetriever, SearchResult
from langchain_engine.retrieval_chain import DegradationRetrievalChain, RetrievalResult
from langchain_engine.material_chain import MaterialAssemblyChain, MaterialBundle
from langchain_engine.review_chain import PreReviewChain, PreReviewResult
from langchain_engine.model_review import ModelReviewChain, ModelReviewResult


class WritingEngine:
    """Unified writing engine combining all chains."""

    def __init__(self, novel_dir: str | Path | None = None):
        self.novel_dir = Path(novel_dir) if novel_dir else DEFAULT_NOVEL_DIR
        self.retrieval = DegradationRetrievalChain()
        self.material = MaterialAssemblyChain(novel_dir=self.novel_dir)
        self.pre_review_chain = PreReviewChain()
        self.model_review_chain = ModelReviewChain()

    def search(self, query: str, top_k: int = 8,
               dimension: Optional[str] = None) -> RetrievalResult:
        """Semantic search in 五维资料库."""
        return self.retrieval.invoke(query, dimension=dimension)

    def multi_search(self, queries: List[str], top_k: int = 10,
                     dimension: Optional[str] = None) -> List[SearchResult]:
        """Multi-query search with deduplication."""
        return self.retrieval.retriever.multi_query_search(
            queries, top_k=top_k, dimension=dimension
        )

    def assemble(self, chapter: int,
                 query_hints: Optional[List[str]] = None,
                 **kwargs) -> MaterialBundle:
        """Assemble material bundle for chapter writing."""
        return self.material.assemble(chapter=chapter,
                                      query_hints=query_hints, **kwargs)

    def pre_review(self, text: str, outline: str = "",
                   character_names: Optional[List[str]] = None) -> PreReviewResult:
        """Run hard-rule pre-review."""
        return self.pre_review_chain.check(text, outline, character_names)

    def model_review(self, text: str, bundle: Optional[MaterialBundle] = None,
                     outline: str = "", characters: str = "") -> ModelReviewResult:
        """Run DeepSeek model review."""
        if bundle:
            outline = outline or bundle.outline_section
            characters = characters or bundle.characters
        return self.model_review_chain.review(text, outline, characters)

    def full_review(self, text: str, chapter: int = 0,
                    outline: str = "", characters: str = "") -> dict:
        """Run complete review pipeline (pre + model).

        Returns dict with both results and overall pass/fail.
        """
        # Pre-review (instant)
        pre = self.pre_review(text, outline)

        # If pre-review has blocking issues, don't waste LLM tokens
        if not pre.passed:
            return {
                "passed": False,
                "stage": "pre_review",
                "pre_review": pre,
                "model_review": None,
                "blocking_issues": pre.blocking_count,
                "message": f"预审查未通过: {pre.blocking_count}个blocking问题需先修复"
            }

        # Model review (LLM call)
        model_result = self.model_review_chain.review(text, outline, characters)

        return {
            "passed": model_result.passed,
            "stage": "model_review",
            "pre_review": pre,
            "model_review": model_result,
            "overall_score": model_result.overall_score,
            "message": f"审查{'通过' if model_result.passed else '未通过'}: {model_result.overall_score}/100"
        }


def main():
    parser = argparse.ArgumentParser(description="LangChain Writing Engine")
    sub = parser.add_subparsers(dest="command")

    # search
    s = sub.add_parser("search", help="Semantic search")
    s.add_argument("query", type=str)
    s.add_argument("--top-k", type=int, default=8)
    s.add_argument("--dimension", type=str, default=None)

    # assemble
    a = sub.add_parser("assemble", help="Material assembly")
    a.add_argument("--chapter", type=int, required=True)
    a.add_argument("--hints", nargs="*", type=str, default=None)

    # pre-review
    p = sub.add_parser("pre-review", help="Pre-review (rules)")
    p.add_argument("--file", type=str, required=True)

    # model-review
    m = sub.add_parser("model-review", help="Model review (DeepSeek)")
    m.add_argument("--file", type=str, required=True)
    m.add_argument("--chapter", type=int, default=0)

    args = parser.parse_args()
    engine = WritingEngine()

    if args.command == "search":
        result = engine.search(args.query, top_k=args.top_k,
                               dimension=args.dimension)
        print(f"Level: {result.level_used} | Results: {len(result.results)}")
        for r in result.results:
            print(f"  [{r.dimension}] {r.book}/{r.anchor_label} "
                  f"score={r.score:.3f}")
            print(f"    {r.text[:100]}")

    elif args.command == "assemble":
        bundle = engine.assemble(chapter=args.chapter,
                                 query_hints=args.hints)
        ctx = bundle.to_prompt_context()
        print(f"Chapter {bundle.chapter_num} | Refs: {len(bundle.retrieved_references)}")
        print(f"Context: {len(ctx)} chars (~{bundle.total_tokens_est} tokens)")
        print(ctx[:1000])

    elif args.command == "pre-review":
        text = Path(args.file).read_text(encoding="utf-8")
        result = engine.pre_review(text)
        print(f"Passed: {result.passed} | Blocking: {result.blocking_count}")
        for issue in result.issues:
            print(f"  [{issue.severity}] L{issue.line_num}: {issue.rule}")
            print(f"    {issue.text_excerpt}")

    elif args.command == "model-review":
        text = Path(args.file).read_text(encoding="utf-8")
        result = engine.model_review(text)
        print(result.summary())

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
