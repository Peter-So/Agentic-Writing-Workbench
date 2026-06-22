"""Material Assembly Chain — 材料装配链.

Assembles structured context for chapter writing by combining:
  1. Character profiles (人物档案)
  2. Outline sections (大纲)
  3. Five-dimension retrieval results (五维资料库语义检索)
  4. Timeline constraints (时间线)

Usage:
    from langchain_engine.material_chain import MaterialAssemblyChain
    chain = MaterialAssemblyChain()
    context = chain.assemble(chapter=3, query_hints=["封云的沉默", "课堂上的紧张"])
"""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict

from langchain_engine.retrieval_chain import DegradationRetrievalChain, RetrievalResult

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import DEFAULT_NOVEL_DIR


@dataclass
class MaterialBundle:
    """Assembled material package for chapter generation."""
    chapter_num: int
    outline_section: str
    characters: str
    retrieved_references: List[Dict]
    timeline_context: str
    constraints: str
    total_tokens_est: int = 0

    def to_prompt_context(self, max_chars: int = 12000) -> str:
        """Format as structured context block for LLM prompt."""
        sections = []

        sections.append("## 本章大纲\n" + self.outline_section)
        sections.append("## 相关人物档案\n" + self.characters)

        if self.timeline_context:
            sections.append("## 时间线约束\n" + self.timeline_context)

        # Add retrieval results (truncate if needed)
        if self.retrieved_references:
            ref_lines = ["## 五维资料库参考"]
            for ref in self.retrieved_references:
                ref_lines.append(
                    f"- [{ref['dimension']}] {ref['book']}·{ref['anchor_label']} "
                    f"(score={ref['score']:.2f})\n  {ref['text'][:200]}"
                )
            sections.append("\n".join(ref_lines))

        if self.constraints:
            sections.append("## 写作约束\n" + self.constraints)

        full = "\n\n".join(sections)
        if len(full) > max_chars:
            full = full[:max_chars] + "\n\n[...材料截断...]"
        self.total_tokens_est = len(full) // 2  # rough CJK estimate
        return full


class MaterialAssemblyChain:
    """Assemble material bundles for novel chapter writing."""

    def __init__(self, novel_dir: str | Path | None = None):
        self.novel_dir = Path(novel_dir) if novel_dir else DEFAULT_NOVEL_DIR
        self.retrieval_chain = DegradationRetrievalChain()
        self._outline_cache = None
        self._characters_cache = None

    @property
    def outline(self) -> str:
        if self._outline_cache is None:
            for name in ["高中卷大纲.md"]:
                path = self.novel_dir / name
                if path.exists():
                    self._outline_cache = path.read_text(encoding="utf-8")
                    break
            if self._outline_cache is None:
                self._outline_cache = ""
        return self._outline_cache

    @property
    def characters(self) -> str:
        if self._characters_cache is None:
            for name in ["人物档案-完整版.md", "人物档案.md"]:
                path = self.novel_dir / name
                if path.exists():
                    self._characters_cache = path.read_text(encoding="utf-8")
                    break
            if self._characters_cache is None:
                self._characters_cache = ""
        return self._characters_cache

    def _extract_chapter_outline(self, chapter: int) -> str:
        """Extract outline section for a specific chapter."""
        lines = self.outline.split('\n')
        chapter_markers = [
            f"第{chapter}章", f"第{self._cn_num(chapter)}章",
            f"Chapter {chapter}", f"## 第{chapter}章",
            f"### 第{chapter}章", f"**第{chapter}章"
        ]

        start_idx = None
        end_idx = None

        for i, line in enumerate(lines):
            if start_idx is None:
                if any(m in line for m in chapter_markers):
                    start_idx = i
            elif start_idx is not None:
                # Next chapter marker = end
                next_markers = [
                    f"第{chapter+1}章", f"第{self._cn_num(chapter+1)}章",
                    f"Chapter {chapter+1}", f"## 第{chapter+1}章"
                ]
                if any(m in line for m in next_markers):
                    end_idx = i
                    break

        if start_idx is not None:
            section = '\n'.join(lines[start_idx:end_idx])
            return section[:3000]  # cap at 3000 chars
        return f"[第{chapter}章大纲未找到]"

    def _extract_relevant_characters(self, chapter_outline: str) -> str:
        """Extract character profiles mentioned in chapter outline."""
        char_lines = self.characters.split('\n')

        # Find character names in outline
        mentioned = []
        current_char = None
        current_block = []

        for line in char_lines:
            if line.startswith('## ') or line.startswith('### '):
                if current_char and current_block:
                    # Check if this character is mentioned in outline
                    name = current_char.replace('#', '').strip()
                    short_name = name[:2]  # First 2 chars
                    if short_name in chapter_outline or name in chapter_outline:
                        mentioned.append('\n'.join(current_block))
                current_char = line
                current_block = [line]
            else:
                current_block.append(line)

        # Don't forget last character
        if current_char and current_block:
            name = current_char.replace('#', '').strip()
            short_name = name[:2]
            if short_name in chapter_outline or name in chapter_outline:
                mentioned.append('\n'.join(current_block))

        result = '\n\n'.join(mentioned)
        return result[:4000] if result else "[相关人物未匹配]"

    def assemble(self, chapter: int,
                 query_hints: Optional[List[str]] = None,
                 dimension: Optional[str] = None,
                 extra_constraints: str = "") -> MaterialBundle:
        """Assemble full material bundle for a chapter.

        Args:
            chapter: Chapter number
            query_hints: Semantic queries for retrieval (auto-generated from outline if None)
            dimension: Filter retrieval by dimension
            extra_constraints: Additional writing constraints
        """
        # 1. Extract chapter outline
        chapter_outline = self._extract_chapter_outline(chapter)

        # 2. Extract relevant character profiles
        relevant_chars = self._extract_relevant_characters(chapter_outline)

        # 3. Five-dimension retrieval
        if query_hints is None:
            # Auto-generate from outline keywords
            query_hints = self._outline_to_queries(chapter_outline)

        all_refs = []
        for query in query_hints[:5]:  # max 5 queries
            result = self.retrieval_chain.invoke(
                query, dimension=dimension
            )
            for r in result.results[:3]:  # top 3 per query
                all_refs.append({
                    "text": r.text,
                    "score": r.score,
                    "book": r.book,
                    "dimension": r.dimension,
                    "anchor_label": r.anchor_label,
                    "level": result.level_used
                })

        # Dedupe references
        seen = set()
        deduped_refs = []
        for ref in all_refs:
            key = f"{ref['book']}_{ref['text'][:50]}"
            if key not in seen:
                seen.add(key)
                deduped_refs.append(ref)
        deduped_refs.sort(key=lambda x: x["score"], reverse=True)
        deduped_refs = deduped_refs[:10]  # max 10 references

        # 4. Timeline context
        timeline = self._get_timeline_context(chapter)

        # 5. Standard constraints
        constraints = self._build_constraints(extra_constraints)

        return MaterialBundle(
            chapter_num=chapter,
            outline_section=chapter_outline,
            characters=relevant_chars,
            retrieved_references=deduped_refs,
            timeline_context=timeline,
            constraints=constraints
        )

    def _outline_to_queries(self, outline: str) -> List[str]:
        """Auto-generate retrieval queries from outline text."""
        import re
        # Extract key phrases (sentences with emotional/action content)
        sentences = re.split(r'[。！？\n]', outline)
        queries = []
        for s in sentences:
            s = s.strip()
            if len(s) >= 8 and len(s) <= 60:
                # Prefer sentences with character/emotion content
                if any(kw in s for kw in ['感', '心', '看', '想', '说', '笑',
                                           '怒', '哭', '走', '站', '坐']):
                    queries.append(s)
        return queries[:5] if queries else [outline[:50]]

    def _get_timeline_context(self, chapter: int) -> str:
        """Get timeline constraints for chapter."""
        timeline_path = self.novel_dir / "时间线.md"
        if timeline_path.exists():
            return timeline_path.read_text(encoding="utf-8")[:1500]
        return ""

    def _build_constraints(self, extra: str) -> str:
        """Build standard writing constraints."""
        base = [
            "禁止AI否定结构(不是A是B)",
            "禁止AI身体怪癖(磨牙/抠指甲/转笔)",
            "封云开关模型：默认开朗好奇，仅特定触发时沉默",
            "叙述中用母亲/父亲，对话中可用口语称谓",
            "源文档优先，不可凭空生成情节",
        ]
        if extra:
            base.append(extra)
        return '\n'.join(f"- {c}" for c in base)

    @staticmethod
    def _cn_num(n: int) -> str:
        """Convert number to Chinese."""
        cn = "零一二三四五六七八九十"
        if n <= 10:
            return cn[n]
        elif n < 20:
            return f"十{cn[n-10]}" if n > 10 else "十"
        elif n < 100:
            tens = n // 10
            ones = n % 10
            return f"{cn[tens]}十{cn[ones]}" if ones else f"{cn[tens]}十"
        return str(n)
