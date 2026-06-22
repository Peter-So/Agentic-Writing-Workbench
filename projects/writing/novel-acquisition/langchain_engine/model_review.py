"""Model Review Chain — DeepSeek LLM五维度审查 + 自动修复循环.

Five dimensions:
  1. 去AI痕迹 (de-AI)
  2. 文学质量 (literary quality)
  3. 结构完整性 (structure)
  4. 人物一致性 (character consistency)
  5. 大纲符合度 (outline adherence)

Usage:
    from langchain_engine.model_review import ModelReviewChain
    reviewer = ModelReviewChain()
    result = reviewer.review(chapter_text, outline, characters)
"""

import json
import urllib.request
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import env_file

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"


def _get_api_key() -> str:
    """Get DeepSeek API key from environment or .env file."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        env_path = env_file()
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.strip().split("=", 1)[1]
                    break
    return key


@dataclass
class DimensionScore:
    name: str
    score: int  # 0-100
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


@dataclass
class ModelReviewResult:
    overall_score: int
    passed: bool  # >= 90
    dimensions: List[DimensionScore] = field(default_factory=list)
    raw_response: str = ""
    iteration: int = 0

    def summary(self) -> str:
        lines = [f"Overall: {self.overall_score}/100 ({'PASS' if self.passed else 'FAIL'})"]
        for d in self.dimensions:
            lines.append(f"  {d.name}: {d.score}")
            for issue in d.issues[:3]:
                lines.append(f"    - {issue}")
        return '\n'.join(lines)


class ModelReviewChain:
    """DeepSeek-powered model review with auto-fix loop."""

    REVIEW_PROMPT_TEMPLATE = """你是一位资深中文小说编辑，请对以下章节进行五维度审查。

## 审查维度（每项0-100分）

1. **去AI痕迹** (de_ai): 检查是否存在AI写作的典型痕迹
   - 解释性否定结构(不是A是B/并非A而是B)
   - AI身体怪癖(磨牙/抠指甲/瞳孔微缩)
   - 过度使用"微微""缓缓""不禁"等AI高频词
   - 叙事者翻译潜台词(把角色心理直接说出来)

2. **文学质量** (literary): 语言是否生动有力
   - 具体性(五感细节 vs 抽象概括)
   - 节奏感(长短句交替、留白)
   - 意象密度(每段至少一个具象画面)

3. **结构完整性** (structure): 章节是否有起承转合
   - 开头是否有钩子
   - 是否有推进力(每个场景推动下一个)
   - 结尾是否留悬念或情感余韵

4. **人物一致性** (character): 角色行为是否符合设定
   - 封云开关模型(默认开朗好奇，特定触发才沉默)
   - 对话风格是否区分(不同角色语气不同)
   - 行为动机是否可信

5. **大纲符合度** (outline): 是否覆盖大纲要求的事件
   - 关键事件是否都出现
   - 顺序是否合理
   - 是否有大纲外的多余情节

## 章节正文
{chapter_text}

## 本章大纲
{outline}

## 相关人物设定
{characters}

## 输出格式(严格JSON)
```json
{{
  "overall_score": 85,
  "de_ai": {{"score": 80, "issues": ["第3段使用了'不是A而是B'结构"], "suggestions": ["直接写出结果"]}},
  "literary": {{"score": 88, "issues": [], "suggestions": []}},
  "structure": {{"score": 85, "issues": ["结尾略显仓促"], "suggestions": ["增加余韵段落"]}},
  "character": {{"score": 90, "issues": [], "suggestions": []}},
  "outline": {{"score": 82, "issues": ["大纲第3点未体现"], "suggestions": ["补充对应场景"]}}
}}
```

只输出JSON，不要其他内容。"""

    def __init__(self, api_key: Optional[str] = None,
                 model: str = DEEPSEEK_MODEL,
                 pass_threshold: int = 90):
        self.api_key = api_key or _get_api_key()
        self.model = model
        self.pass_threshold = pass_threshold

    def _call_llm(self, prompt: str, max_tokens: int = 2000) -> str:
        """Call DeepSeek API."""
        payload = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "messages": [{"role": "user", "content": prompt}]
        }).encode("utf-8")

        req = urllib.request.Request(
            DEEPSEEK_URL, data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # Handle reasoning model (content might be in different field)
        choice = result["choices"][0]["message"]
        return choice.get("content", "") or ""

    def review(self, chapter_text: str, outline: str = "",
               characters: str = "") -> ModelReviewResult:
        """Run five-dimension model review.

        Args:
            chapter_text: Full chapter text
            outline: Chapter outline section
            characters: Relevant character profiles
        """
        # Truncate inputs to fit context
        chapter_text = chapter_text[:8000]
        outline = outline[:2000]
        characters = characters[:2000]

        prompt = self.REVIEW_PROMPT_TEMPLATE.format(
            chapter_text=chapter_text,
            outline=outline,
            characters=characters
        )

        raw = self._call_llm(prompt)

        # Parse JSON response
        try:
            # Extract JSON from response (may have markdown fences)
            json_str = raw
            if "```json" in raw:
                json_str = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                json_str = raw.split("```")[1].split("```")[0]

            data = json.loads(json_str.strip())
        except (json.JSONDecodeError, IndexError):
            # Fallback: try to find any JSON object
            import re
            match = re.search(r'\{[^{}]*"overall_score"[^{}]*\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                return ModelReviewResult(
                    overall_score=0, passed=False,
                    raw_response=raw[:500]
                )

        # Build result
        dimensions = []
        for dim_key, dim_name in [
            ("de_ai", "去AI痕迹"),
            ("literary", "文学质量"),
            ("structure", "结构完整性"),
            ("character", "人物一致性"),
            ("outline", "大纲符合度"),
        ]:
            dim_data = data.get(dim_key, {})
            dimensions.append(DimensionScore(
                name=dim_name,
                score=dim_data.get("score", 0),
                issues=dim_data.get("issues", []),
                suggestions=dim_data.get("suggestions", [])
            ))

        overall = data.get("overall_score", 0)
        return ModelReviewResult(
            overall_score=overall,
            passed=overall >= self.pass_threshold,
            dimensions=dimensions,
            raw_response=raw[:1000]
        )

    def review_and_fix_loop(self, chapter_text: str, outline: str = "",
                            characters: str = "",
                            max_iterations: int = 3,
                            fix_callback=None) -> List[ModelReviewResult]:
        """Review → fix → re-review loop until pass or max iterations.

        Args:
            chapter_text: Initial chapter text
            outline: Chapter outline
            characters: Character profiles
            max_iterations: Max fix attempts
            fix_callback: Optional callable(text, issues) -> fixed_text
                         If None, returns after first review.

        Returns:
            List of review results (one per iteration)
        """
        results = []
        current_text = chapter_text

        for i in range(max_iterations):
            result = self.review(current_text, outline, characters)
            result.iteration = i + 1
            results.append(result)

            if result.passed:
                break

            if fix_callback is None:
                break

            # Collect all issues for fixing
            all_issues = []
            for dim in result.dimensions:
                all_issues.extend(dim.issues)

            if not all_issues:
                break

            # Apply fix
            current_text = fix_callback(current_text, all_issues)

        return results
