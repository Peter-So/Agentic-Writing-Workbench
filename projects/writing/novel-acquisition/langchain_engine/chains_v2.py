"""LCEL Chains v2 — Scene-specific pipelines using full project specs.

Each chain loads the appropriate spec document and injects project context.
Replaces the generic prompts with material-driven scene-specific templates.

Usage:
    from langchain_engine.chains_v2 import (
        prose_chain,       # 章节正文生成
        review_chain,      # 五维度审查
        fix_chain,         # 材料驱动修复
        expansion_chain,   # 扩写
        outline_chain,     # 大纲生成
        beat_chain,        # Beat Sheet
        character_chain,   # 人物档案
        review_fix_loop,   # 审查→修复循环
    )
"""

from typing import Dict, Any, List, Optional
import sys
from pathlib import Path
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_core.documents import Document

from langchain_engine.llm import get_review_llm, get_generation_llm
from langchain_engine.prompts_v2 import (
    get_prompt,
    build_project_context,
    MATERIAL_DRIVEN_PARADIGM,
)
from langchain_engine.lc_retriever import FiveDimLangChainRetriever
from langchain_engine.doc_loader import (
    extract_chapter_outline,
    extract_character_profiles,
    load_chapter_context,
    load_project_settings,
)

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import DEFAULT_NOVEL_DIR


# ============================================================
# SHARED COMPONENTS
# ============================================================

retriever = FiveDimLangChainRetriever(top_k=8)
review_parser = JsonOutputParser()
text_parser = StrOutputParser()


def _format_docs(docs: List[Document]) -> str:
    """Format retrieved Documents into prompt-ready text."""
    lines = []
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata
        lines.append(
            f"{i}. [{meta.get('dimension','')}] "
            f"{meta.get('source','未知')} (score={meta.get('score',0):.2f})\n"
            f"   {doc.page_content[:250]}"
        )
    return "\n\n".join(lines)


def _multi_retrieve(queries: List[str], dimension: Optional[str] = None,
                    top_k: int = 12) -> str:
    """Run multiple retrieval queries, dedupe, format."""
    all_docs = []
    seen = set()
    for q in queries[:5]:
        r = FiveDimLangChainRetriever(top_k=6, dimension=dimension)
        docs = r.invoke(q)
        for doc in docs:
            key = f"{doc.metadata.get('book')}_{doc.page_content[:50]}"
            if key not in seen:
                seen.add(key)
                all_docs.append(doc)
    all_docs.sort(key=lambda d: d.metadata.get("score", 0), reverse=True)
    return _format_docs(all_docs[:top_k])


# ============================================================
# CHAPTER PROSE CHAIN
# ============================================================

def _prepare_prose_input(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich prose inputs with retrieval results and project context.

    Auto-loads from project files if chapter_num is provided but outline/characters are not.
    """
    import re

    # Auto-load from project docs if chapter_num provided
    chapter_num = inputs.get("chapter_num")
    if chapter_num and not inputs.get("outline_section"):
        ctx = load_chapter_context(chapter_num, inputs.get("character_names"))
        inputs.setdefault("outline_section", ctx["outline_section"])
        inputs.setdefault("characters", ctx["characters"])
        inputs.setdefault("constraints", ctx["constraints"])
        inputs.setdefault("timeline", "")

    # Auto-retrieve if not provided
    if "retrieval_results" not in inputs or not inputs["retrieval_results"]:
        queries = inputs.get("query_hints", [])
        if not queries:
            outline = inputs.get("outline_section", "")
            sentences = re.split(r'[。！？\n]', outline)
            queries = [s.strip() for s in sentences if 8 <= len(s.strip()) <= 60][:5]
        inputs["retrieval_results"] = _multi_retrieve(queries)

    # Inject project context as messages
    inputs.setdefault("material_context", build_project_context(
        include_traversal=False,
        include_dual_narrative=False,
    ))
    inputs.setdefault("timeline", "")
    inputs.setdefault("constraints", "封云开关模型：默认开朗，仅特定触发时沉默。")
    return inputs


prose_chain = (
    RunnableLambda(_prepare_prose_input)
    | get_prompt("chapter_prose")
    | get_generation_llm()
    | text_parser
)


# ============================================================
# REVIEW CHAIN
# ============================================================

def _prepare_review_input(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Add default constraints for review."""
    inputs.setdefault("constraints", "\n".join([
        "- 禁止AI否定结构(不是A是B)",
        "- 禁止AI身体怪癖(磨牙/抠指甲/转笔/瞳孔微缩)",
        "- 禁止段尾升华/总结/点明意义",
        "- 禁止叙事者翻译潜台词",
        "- 封云开关模型：默认开朗好奇，仅特定触发时沉默",
        "- 叙述中用母亲/父亲，对话中可用口语称谓",
    ]))
    return inputs


review_chain = (
    RunnableLambda(_prepare_review_input)
    | get_prompt("review")
    | get_review_llm()
    | review_parser
)


# ============================================================
# FIX CHAIN (material-driven)
# ============================================================

def _prepare_fix_input(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-retrieve replacement materials for fix."""
    if "retrieval_results" not in inputs or not inputs["retrieval_results"]:
        # Extract keywords from issues to search for better patterns
        issues_text = inputs.get("issues", "")
        queries = [line.strip("- ") for line in issues_text.split("\n")
                   if len(line.strip()) > 5][:3]
        if queries:
            inputs["retrieval_results"] = _multi_retrieve(queries)
        else:
            inputs["retrieval_results"] = "[无检索结果]"
    inputs.setdefault("characters", "")
    inputs.setdefault("outline_section", "")
    return inputs


fix_chain = (
    RunnableLambda(_prepare_fix_input)
    | get_prompt("fix")
    | get_generation_llm()
    | text_parser
)


# ============================================================
# EXPANSION CHAIN
# ============================================================

def _prepare_expansion_input(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-retrieve expansion materials."""
    if "retrieval_results" not in inputs or not inputs["retrieval_results"]:
        weak_points = inputs.get("weak_points", "")
        queries = [line.strip("- ") for line in weak_points.split("\n")
                   if len(line.strip()) > 5][:3]
        inputs["retrieval_results"] = _multi_retrieve(queries) if queries else ""
    inputs.setdefault("characters", "")
    inputs.setdefault("outline_section", "")
    return inputs


expansion_chain = (
    RunnableLambda(_prepare_expansion_input)
    | get_prompt("expansion")
    | get_generation_llm()
    | text_parser
)


# ============================================================
# OUTLINE CHAIN
# ============================================================

def _prepare_outline_input(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve twists/scenes for outline generation."""
    if "retrieval_results" not in inputs or not inputs["retrieval_results"]:
        queries = inputs.get("query_hints", [])
        inputs["retrieval_results"] = _multi_retrieve(queries, dimension=None)
    inputs.setdefault("project_context", build_project_context(
        include_traversal=True, include_dual_narrative=True
    ))
    inputs.setdefault("timeline", "")
    return inputs


outline_chain = (
    RunnableLambda(_prepare_outline_input)
    | get_prompt("outline")
    | get_generation_llm()
    | text_parser
)


# ============================================================
# BEAT SHEET CHAIN
# ============================================================

def _prepare_beat_input(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve scenes/twists for beat sheet."""
    if "scene_refs" not in inputs:
        queries = inputs.get("query_hints", [inputs.get("outline_section", "")[:50]])
        inputs["scene_refs"] = _multi_retrieve(queries, dimension="scenes")
    if "twist_refs" not in inputs:
        inputs["twist_refs"] = _multi_retrieve(
            [inputs.get("outline_section", "")[:50]], dimension="twists"
        )
    return inputs


beat_chain = (
    RunnableLambda(_prepare_beat_input)
    | get_prompt("beat_sheet")
    | get_review_llm()
    | text_parser
)


# ============================================================
# CHARACTER CHAIN
# ============================================================

def _prepare_character_input(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve character/psychology refs."""
    name = inputs.get("character_name", "")
    if "character_refs" not in inputs:
        inputs["character_refs"] = _multi_retrieve(
            [name, inputs.get("source_character", "")[:50]],
            dimension="characters"
        )
    if "psychology_refs" not in inputs:
        inputs["psychology_refs"] = _multi_retrieve(
            [name], dimension="psychology"
        )
    inputs.setdefault("classic_refs", "")
    inputs.setdefault("project_context", [])
    return inputs


character_chain = (
    RunnableLambda(_prepare_character_input)
    | get_prompt("character")
    | get_review_llm()
    | text_parser
)


# ============================================================
# REVIEW-FIX LOOP
# ============================================================

def review_fix_loop(text: str, outline: str = "", characters: str = "",
                    chapter_num: int = None,
                    max_iterations: int = 3, pass_threshold: int = 90) -> Dict:
    """Review → fix → re-review loop using material-driven fix.

    Integrates both:
    - pre_llm_review.py (1046-line deterministic gate, 10+ rule categories)
    - Model review (DeepSeek 5-dimension scoring)

    Auto-loads outline/characters from project docs if chapter_num is provided.

    Returns:
        {"final_text": str, "iterations": list, "passed": bool, "final_score": int}
    """
    scripts_dir = DEFAULT_NOVEL_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    # Auto-load context
    if chapter_num and not outline:
        ctx = load_chapter_context(chapter_num)
        outline = ctx["outline_section"]
        characters = characters or ctx["characters"]

    current_text = text
    iterations = []

    for i in range(max_iterations):
        # ====== Gate 1: Full pre_llm_review.py (deterministic) ======
        try:
            from pre_llm_review import run as pre_llm_run, scan_patterns, \
                scan_narrator_translation_v2, scan_unknown_characters, \
                scan_protagonist_brightness_v2, scan_character_deployment, \
                scan_outline_completion, scan_structure, scan_duplicate_sentences, \
                scan_wordcount, Issue
            from dataclasses import asdict as _asdict

            # If chapter_num known, use full run() which reads from file
            # Otherwise run individual scanners on text directly
            if chapter_num:
                pre_result = pre_llm_run(chapter_num, fix=False)
                blocking_issues = [iss for iss in pre_result.get("issues", [])
                                   if iss.get("severity") == "blocking"]
            else:
                # Run scanners directly on text
                all_issues = []
                all_issues.extend(scan_patterns(current_text))
                all_issues.extend(scan_narrator_translation_v2(current_text))
                all_issues.extend(scan_unknown_characters(current_text))
                all_issues.extend(scan_protagonist_brightness_v2(current_text))
                all_issues.extend(scan_duplicate_sentences(current_text))
                all_issues.extend(scan_wordcount(current_text))
                blocking_issues = [_asdict(iss) for iss in all_issues
                                   if iss.severity == "blocking"]
        except ImportError:
            # Fallback to built-in PreReviewChain
            from langchain_engine.review_chain import PreReviewChain
            pre_reviewer = PreReviewChain()
            pre_check = pre_reviewer.check(current_text, outline)
            blocking_issues = [
                {"code": iss.rule, "line": iss.line_num,
                 "evidence": iss.text_excerpt, "problem": iss.rule}
                for iss in pre_check.issues if iss.severity == "blocking"
            ]

        if blocking_issues:
            blocking_text = "\n".join(
                f"- L{iss.get('line',0)}: [{iss.get('code','')}] "
                f"{iss.get('evidence','')[:80]}"
                for iss in blocking_issues[:15]
            )
            try:
                current_text = fix_chain.invoke({
                    "original_text": current_text,
                    "issues": blocking_text,
                    "outline_section": outline[:1500],
                    "characters": characters[:1500],
                })
            except Exception as e:
                iterations.append({"iteration": i+1, "stage": "pre_fix", "error": str(e)})
                break
            iterations.append({
                "iteration": i+1, "stage": "pre_fix",
                "blocking_fixed": len(blocking_issues),
                "issue_codes": list(set(iss.get("code","") for iss in blocking_issues)),
            })
            continue  # Re-check after pre-fix

        # ====== Gate 2: Model review (DeepSeek 5-dimension) ======
        try:
            result = review_chain.invoke({
                "chapter_text": current_text[:8000],
                "outline": outline[:2000],
                "characters": characters[:2000],
            })
        except Exception as e:
            iterations.append({"iteration": i+1, "stage": "review", "error": str(e)})
            break

        score = result.get("overall_score", 0)
        iterations.append({
            "iteration": i+1, "stage": "review",
            "score": score, "dimensions": result,
        })

        if score >= pass_threshold:
            return {
                "final_text": current_text,
                "iterations": iterations,
                "passed": True,
                "final_score": score,
            }

        # ====== Gate 3: Material-driven fix ======
        all_issues = []
        for dim_key in ["de_ai", "literary", "structure", "character", "outline"]:
            dim = result.get(dim_key, {})
            all_issues.extend(dim.get("issues", []))

        if not all_issues:
            break

        try:
            current_text = fix_chain.invoke({
                "original_text": current_text,
                "issues": "\n".join(f"- {iss}" for iss in all_issues),
                "outline_section": outline[:1500],
                "characters": characters[:1500],
            })
        except Exception as e:
            iterations.append({"iteration": i+1, "stage": "fix", "error": str(e)})
            break

    return {
        "final_text": current_text,
        "iterations": iterations,
        "passed": False,
        "final_score": iterations[-1].get("score", 0) if iterations else 0,
    }


# ============================================================
# CHARACTER CONTINUITY CHAIN
# ============================================================

CHARACTER_CONTINUITY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一位资深小说编辑，专注于角色连续性和配角刻画深度。

## 任务
分析前章正文中每个出场角色的刻画方式（语气/神态/肢体动作/心理活动/口头禅/习惯动作），
然后为当前章的大纲补充角色互动建议——在beat主线之间的间隙填充活的人物细节。

## 规则
1. 配角在当前章至少要有一个「活」的细节（非功能性出场）——一句对话/一个习惯动作/一个反应
2. 主角心理活动在beat间隙（场景过渡时）要有延续——不能只在主事件中有内心戏
3. 语气神态必须与人物档案一致，且在不同场景中有微变化（教室vs操场vs私下）
4. 建议必须具体到可直接写入大纲的程度（带具体动作/对话/反应）
5. 每条建议标注插入位置（beat N 与 beat N+1 之间）和使用的衔接技法

## 五维资料库参考素材
{reference_materials}

{material_paradigm}"""),
    ("human", """## 前章正文角色特征提取
{prev_chapter_traits}

## 当前章大纲
{current_outline}

## 人物档案（相关角色）
{character_profiles}

请输出JSON：
```json
{{
  "prev_chapter_traits": [
    {{"character": "角色名", "voice": "语气特征", "gestures": "肢体/神态", "psychology": "心理", "habits": "习惯动作/口头禅"}}
  ],
  "continuity_suggestions": [
    {{
      "insert_position": "beat N → beat N+1 之间",
      "bridging_technique": "物件传递/情绪惯性/人物桥接/感官连接/因果触发",
      "character": "角色名",
      "suggestion": "具体建议内容（可直接写入大纲的细节）",
      "rationale": "为什么这个细节能加深刻画"
    }}
  ],
  "missing_characters": ["前章出现但当前章完全缺席的角色（需要至少一个活细节）"],
  "psychological_gaps": ["主角在哪些beat过渡处缺少心理延续"]
}}
```""")
])


def character_continuity_chain(
    prev_chapter_text: str,
    current_outline: str,
    character_profiles: str,
    retrieval_queries: List[str] = None,
) -> Dict[str, Any]:
    """Analyze character traits from previous chapter and suggest continuity enrichments.

    Args:
        prev_chapter_text: Full text of previous chapter
        current_outline: Current chapter outline (beats)
        character_profiles: Relevant character profiles from 人物档案
        retrieval_queries: Optional queries to fetch reference materials from 五维库

    Returns:
        Dict with prev_chapter_traits, continuity_suggestions, missing_characters, psychological_gaps
    """
    from langchain_engine.prompts_v2 import MATERIAL_DRIVEN_PARADIGM

    # Step 1: Extract character traits from prev chapter via LLM
    trait_extraction_prompt = ChatPromptTemplate.from_messages([
        ("system", "你是角色分析专家。从小说正文中提取每个出场角色的刻画特征。"),
        ("human", """从以下正文中提取所有出场角色的：
1. 语气特征（说话方式、用词习惯）
2. 肢体动作/神态（反复出现的动作、表情）
3. 心理活动（内心独白的风格、关注点）
4. 习惯动作/口头禅

正文：
{text}

输出JSON数组：
```json
[{{"character": "角色名", "voice": "语气", "gestures": "肢体/神态", "psychology": "心理", "habits": "习惯"}}]
```""")
    ])

    # Truncate if too long (keep first 6000 chars for trait extraction)
    truncated_text = prev_chapter_text[:6000] if len(prev_chapter_text) > 6000 else prev_chapter_text

    try:
        trait_chain = trait_extraction_prompt | get_review_llm() | JsonOutputParser()
        traits = trait_chain.invoke({"text": truncated_text})
        traits_str = "\n".join(
            f"- {t['character']}: 语气={t.get('voice','?')}, 动作={t.get('gestures','?')}, "
            f"心理={t.get('psychology','?')}, 习惯={t.get('habits','?')}"
            for t in traits
        ) if isinstance(traits, list) else str(traits)
    except Exception as e:
        traits_str = f"[提取失败: {e}]"

    # Step 2: Retrieve reference materials for character interaction patterns
    ref_materials = ""
    if retrieval_queries:
        ref_materials = _multi_retrieve(retrieval_queries, dimension="characters", top_k=8)
    else:
        # Auto-generate queries from character names in outline
        import re
        char_names = re.findall(r'[\u4e00-\u9fff]{2,4}', current_outline[:500])
        auto_queries = [f"{name} 互动 细节 动作" for name in list(set(char_names))[:3]]
        if auto_queries:
            ref_materials = _multi_retrieve(auto_queries, dimension="characters", top_k=6)

    # Step 3: Run continuity analysis
    from langchain_engine.prompts_v2 import MATERIAL_DRIVEN_PARADIGM
    continuity_chain = CHARACTER_CONTINUITY_PROMPT | get_review_llm() | JsonOutputParser()

    result = continuity_chain.invoke({
        "prev_chapter_traits": traits_str,
        "current_outline": current_outline[:3000],
        "character_profiles": character_profiles[:2000],
        "reference_materials": ref_materials[:2000] if ref_materials else "（无额外参考素材）",
        "material_paradigm": MATERIAL_DRIVEN_PARADIGM,
    })

    return result
