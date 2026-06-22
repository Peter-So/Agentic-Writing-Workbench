"""Pre-LLM Review Chain — 预审查链 (硬规则 + DeepSeek软判断).

Two-layer review:
  Layer 1: Rule Engine (regex/pattern hard gates) — instant, zero cost
  Layer 2: DeepSeek LLM soft judgment — for ambiguous cases

Usage:
    from langchain_engine.review_chain import PreReviewChain, ModelReviewChain
    pre = PreReviewChain()
    result = pre.check(chapter_text)
    if not result.passed:
        print(result.issues)
"""

import re
import json
import urllib.request
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


@dataclass
class ReviewIssue:
    rule: str
    severity: str  # "blocking" or "warning"
    line_num: int
    text_excerpt: str
    suggestion: str = ""


@dataclass
class PreReviewResult:
    passed: bool
    issues: List[ReviewIssue] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)

    @property
    def blocking_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "blocking")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")


class PreReviewChain:
    """Hard-rule pre-review gate. Zero LLM cost, instant."""

    # AI否定结构模式 (6种子模式)
    NEGATION_PATTERNS = [
        (r'不是[^，。！？\n]{2,15}[，,]\s*而是', "不是A，而是B"),
        (r'并非[^，。！？\n]{2,15}[，,]\s*而是', "并非A，而是B"),
        (r'不是[^，。！？\n]{2,15}[，,]\s*是', "不是A，是B"),
        (r'与其说[^，。！？\n]{2,15}[，,]\s*不如说', "与其说A，不如说B"),
        (r'并不是[^，。！？\n]{2,15}[，,]\s*只是', "并不是A，只是B"),
        (r'不[^，。！？\n]{1,8}[，,]\s*反而', "不A，反而B"),
    ]

    # AI身体怪癖黑名单
    BODY_QUIRKS = [
        "磨牙", "抠指甲", "转笔", "咬嘴唇", "攥紧拳头",
        "不自觉地", "下意识地攥", "指尖微微颤抖",
        "喉结上下滚动", "瞳孔微缩", "牙关紧咬",
    ]

    # 叙事称谓问题 (叙述中不应出现口语称谓)
    NARRATIVE_INFORMAL = [
        (r'(?<![「\u201c\u201d])他妈(?![」\u201c\u201d])', "叙述中'他妈'→母亲"),
        (r'(?<![「\u201c\u201d])他爸(?![」\u201c\u201d])', "叙述中'他爸'→父亲"),
        (r'(?<![「\u201c\u201d])她妈(?![」\u201c\u201d])', "叙述中'她妈'→母亲"),
        (r'(?<![「\u201c\u201d])她爸(?![」\u201c\u201d])', "叙述中'她爸'→父亲"),
    ]

    def check(self, text: str, outline: str = "",
              character_names: Optional[List[str]] = None) -> PreReviewResult:
        """Run all hard-rule checks.

        Args:
            text: Chapter text to review
            outline: Chapter outline for consistency check
            character_names: Known character names for unknown-name detection
        """
        issues = []
        lines = text.split('\n')

        # === Gate 1: AI否定结构 ===
        for line_num, line in enumerate(lines, 1):
            for pattern, name in self.NEGATION_PATTERNS:
                matches = re.finditer(pattern, line)
                for m in matches:
                    # Exclude if inside dialogue quotes
                    pos = m.start()
                    before = line[:pos]
                    if self._in_dialogue(before, line, pos):
                        continue
                    issues.append(ReviewIssue(
                        rule=f"AI否定结构({name})",
                        severity="blocking",
                        line_num=line_num,
                        text_excerpt=m.group()[:60],
                        suggestion="删除整个否定结构，直接写出结果"
                    ))

        # === Gate 2: AI身体怪癖 ===
        for line_num, line in enumerate(lines, 1):
            for quirk in self.BODY_QUIRKS:
                if quirk in line:
                    if not self._in_dialogue(line[:line.index(quirk)], line, line.index(quirk)):
                        issues.append(ReviewIssue(
                            rule="AI身体怪癖",
                            severity="blocking",
                            line_num=line_num,
                            text_excerpt=f"...{quirk}...",
                            suggestion=f"删除'{quirk}'，用具体动作/环境替代"
                        ))

        # === Gate 3: 叙事称谓 ===
        for line_num, line in enumerate(lines, 1):
            for pattern, suggestion in self.NARRATIVE_INFORMAL:
                if re.search(pattern, line):
                    issues.append(ReviewIssue(
                        rule="叙事称谓",
                        severity="blocking",
                        line_num=line_num,
                        text_excerpt=line[:60],
                        suggestion=suggestion
                    ))

        # === Gate 4: 封云沉默过度检测 ===
        silence_keywords = ["沉默", "不说话", "没有开口", "低着头不语", "一言不发"]
        silence_count = sum(
            1 for line in lines
            for kw in silence_keywords if kw in line
        )
        total_lines = len([l for l in lines if l.strip()])
        if total_lines > 0 and silence_count / max(total_lines, 1) > 0.15:
            issues.append(ReviewIssue(
                rule="封云沉默过度(开关模型违规)",
                severity="warning",
                line_num=0,
                text_excerpt=f"沉默描写{silence_count}处/{total_lines}行",
                suggestion="封云默认态=开朗好奇，仅特定触发时沉默"
            ))

        # === Gate 5: 重复用词检测 ===
        word_freq = self._check_repetition(text)
        for word, count in word_freq:
            issues.append(ReviewIssue(
                rule="高频重复词",
                severity="warning",
                line_num=0,
                text_excerpt=f"'{word}' 出现{count}次",
                suggestion="替换为同义词或删除"
            ))

        # === Stats ===
        stats = {
            "total_chars": len(text),
            "total_lines": len(lines),
            "negation_count": sum(1 for i in issues if "否定" in i.rule),
            "quirk_count": sum(1 for i in issues if "怪癖" in i.rule),
            "silence_ratio": silence_count / max(total_lines, 1),
        }

        passed = all(i.severity != "blocking" for i in issues)
        return PreReviewResult(passed=passed, issues=issues, stats=stats)

    def _in_dialogue(self, before: str, line: str, pos: int) -> bool:
        """Check if position is inside dialogue quotes."""
        # Chinese dialogue pairs
        pairs = [('\u201c', '\u201d'), ('\u300c', '\u300d'),
                 ('\u300e', '\u300f'), ('"', '"')]
        for oq, cq in pairs:
            opens = before.count(oq)
            closes = before.count(cq)
            if opens > closes:
                return True
        # Also check if line starts with quote before position
        stripped = line.lstrip()
        if stripped and stripped[0] in '\u201c\u300c\u300e"':
            # Find closing quote
            close_map = {'\u201c': '\u201d', '\u300c': '\u300d',
                         '\u300e': '\u300f', '"': '"'}
            cq = close_map.get(stripped[0], '"')
            close_pos = line.find(cq, 1)
            if close_pos == -1 or pos < close_pos:
                return True
        return False

    def _check_repetition(self, text: str, threshold: int = 8) -> List[Tuple[str, int]]:
        """Detect overused words (2-4 char phrases)."""
        # Common filler words to check
        check_words = [
            "微微", "缓缓", "轻轻", "默默", "静静",
            "忍不住", "不禁", "终于", "似乎", "仿佛",
            "目光", "眼神", "嘴角", "心中", "脑海",
        ]
        results = []
        for word in check_words:
            count = text.count(word)
            if count >= threshold:
                results.append((word, count))
        results.sort(key=lambda x: -x[1])
        return results[:5]
