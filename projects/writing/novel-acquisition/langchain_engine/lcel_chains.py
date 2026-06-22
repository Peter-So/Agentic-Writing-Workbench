"""LCEL-native review and fix pipeline.

Uses RunnableSequence, RunnableParallel, JsonOutputParser for:
  1. Structured review with few-shot calibration
  2. Parallel multi-dimension assessment
  3. Auto-fix loop with typed outputs

Usage:
    from langchain_engine.lcel_chains import review_chain, fix_chain, full_pipeline
    result = review_chain.invoke({"chapter_text": ..., "outline": ..., "characters": ...})
    fixed = fix_chain.invoke({"original_text": ..., "issues": ...})
"""

from typing import Dict, Any, List
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.runnables import (
    RunnableSequence,
    RunnableParallel,
    RunnableLambda,
    RunnablePassthrough,
)
from langchain_core.documents import Document

from langchain_engine.llm import get_review_llm, get_generation_llm
from langchain_engine.prompts import (
    REVIEW_PROMPT,
    REVIEW_FEW_SHOT,
    FIX_PROMPT,
    MATERIAL_PROMPT,
    CHAPTER_WRITING_PROMPT,
)
from langchain_engine.lc_retriever import FiveDimLangChainRetriever


# ============================================================
# OUTPUT PARSERS
# ============================================================

review_parser = JsonOutputParser()


# ============================================================
# REVIEW CHAIN (ChatPromptTemplate → LLM → JsonOutputParser)
# ============================================================

def _inject_few_shot(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Inject few-shot examples as messages."""
    from langchain_core.messages import HumanMessage, AIMessage
    from langchain_engine.prompts import REVIEW_FEW_SHOT_EXAMPLES

    examples = []
    for ex in REVIEW_FEW_SHOT_EXAMPLES:
        examples.append(HumanMessage(content=f"审查这段文字：{ex['input']}"))
        examples.append(AIMessage(content=ex['output']))

    return {**inputs, "few_shot_examples": examples}


def _add_default_constraints(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Inject standard writing constraints if not provided."""
    if "constraints" not in inputs or not inputs["constraints"]:
        inputs["constraints"] = "\n".join([
            "- 禁止AI否定结构(不是A是B)",
            "- 禁止AI身体怪癖(磨牙/抠指甲/转笔/瞳孔微缩)",
            "- 封云开关模型：默认开朗好奇，仅特定触发时沉默",
            "- 叙述中用母亲/父亲，对话中可用口语称谓",
            "- 源文档优先，不可凭空生成情节",
        ])
    return inputs


# Full review chain: inject_few_shot → add_constraints → prompt → LLM → parse JSON
review_chain = (
    RunnableLambda(_inject_few_shot)
    | RunnableLambda(_add_default_constraints)
    | REVIEW_PROMPT
    | get_review_llm()
    | review_parser
)


# ============================================================
# FIX CHAIN (prompt → LLM → raw text output)
# ============================================================

fix_chain = (
    FIX_PROMPT
    | get_generation_llm()
    | StrOutputParser()
)


# ============================================================
# RETRIEVAL + FORMAT CHAIN
# ============================================================

retriever = FiveDimLangChainRetriever(top_k=8)


def _format_retrieval_results(docs: List[Document]) -> str:
    """Format retrieved documents into structured text for prompts."""
    lines = []
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata
        lines.append(
            f"{i}. [{meta.get('dimension','')}] "
            f"{meta.get('source','未知')} (score={meta.get('score',0):.2f})\n"
            f"   {doc.page_content[:200]}"
        )
    return "\n\n".join(lines)


def retrieve_and_format(query: str, **kwargs) -> str:
    """Retrieve from 五维资料库 and format as prompt-ready text."""
    docs = retriever.invoke(query)
    return _format_retrieval_results(docs)


# ============================================================
# MATERIAL ASSEMBLY CHAIN (retrieval → format → LLM organize)
# ============================================================

def build_material_chain():
    """Build the material assembly LCEL chain.

    Input: {"chapter_num": int, "outline": str, "characters": str, "query_hints": [str]}
    Output: organized material text
    """
    def _retrieve_multi(inputs: Dict) -> Dict:
        """Run multiple retrieval queries and combine."""
        queries = inputs.get("query_hints", [])
        if not queries:
            # Auto-extract from outline
            import re
            sentences = re.split(r'[。！？\n]', inputs.get("outline", ""))
            queries = [s.strip() for s in sentences
                       if 8 <= len(s.strip()) <= 60][:5]

        all_docs = []
        seen = set()
        for q in queries:
            docs = retriever.invoke(q)
            for doc in docs:
                key = f"{doc.metadata.get('book')}_{doc.page_content[:50]}"
                if key not in seen:
                    seen.add(key)
                    all_docs.append(doc)

        # Sort by score, take top 12
        all_docs.sort(key=lambda d: d.metadata.get("score", 0), reverse=True)
        formatted = _format_retrieval_results(all_docs[:12])
        return {**inputs, "retrieval_results": formatted}

    return (
        RunnableLambda(_retrieve_multi)
        | MATERIAL_PROMPT
        | get_review_llm()
        | StrOutputParser()
    )


material_chain = build_material_chain()


# ============================================================
# FULL WRITING PIPELINE
# ============================================================

def build_writing_pipeline():
    """Full pipeline: retrieve → assemble material → write chapter.

    Input: {"chapter_num": int, "outline": str, "characters": str,
            "query_hints": [str], "constraints": str}
    Output: chapter text
    """
    def _prepare_writing_input(inputs: Dict) -> Dict:
        """Add references from material chain."""
        # Get organized references
        material_result = material_chain.invoke(inputs)
        return {
            **inputs,
            "references": material_result,
            "material_context": [],  # No extra messages needed
        }

    return (
        RunnableLambda(_prepare_writing_input)
        | CHAPTER_WRITING_PROMPT
        | get_generation_llm()
        | StrOutputParser()
    )


writing_pipeline = build_writing_pipeline()


# ============================================================
# REVIEW-FIX LOOP (iterative until pass)
# ============================================================

def review_fix_loop(text: str, outline: str = "", characters: str = "",
                    max_iterations: int = 3, pass_threshold: int = 90) -> Dict:
    """Run review → fix → re-review loop until pass or max iterations.

    Returns:
        {"final_text": str, "iterations": [...], "passed": bool, "final_score": int}
    """
    current_text = text
    iterations = []

    for i in range(max_iterations):
        # Review
        review_input = {
            "chapter_text": current_text[:8000],
            "outline": outline[:2000],
            "characters": characters[:2000],
        }
        try:
            result = review_chain.invoke(review_input)
        except Exception as e:
            iterations.append({"iteration": i+1, "error": str(e)})
            break

        score = result.get("overall_score", 0)
        iterations.append({
            "iteration": i + 1,
            "score": score,
            "dimensions": result,
        })

        if score >= pass_threshold:
            return {
                "final_text": current_text,
                "iterations": iterations,
                "passed": True,
                "final_score": score,
            }

        # Collect issues for fixing
        all_issues = []
        for dim_key in ["de_ai", "literary", "structure", "character", "outline"]:
            dim = result.get(dim_key, {})
            all_issues.extend(dim.get("issues", []))

        if not all_issues:
            break

        # Fix
        fix_input = {
            "original_text": current_text,
            "issues": "\n".join(f"- {issue}" for issue in all_issues),
        }
        try:
            current_text = fix_chain.invoke(fix_input)
        except Exception as e:
            iterations.append({"fix_error": str(e)})
            break

    return {
        "final_text": current_text,
        "iterations": iterations,
        "passed": False,
        "final_score": iterations[-1].get("score", 0) if iterations else 0,
    }
