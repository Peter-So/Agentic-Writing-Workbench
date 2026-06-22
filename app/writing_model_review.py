from __future__ import annotations

import json
import re
from typing import Any

from app.config import load_runtime_config
from app.llm_client import create_llm, resolve_text_model


# 复用 langchain_engine/model_review.py 的五维审查方法论，但通过模型注册表路由，
# 以实现「审查模型 ≠ 生成模型」的交叉评审（规范要求）。
REVIEW_PROMPT = """你是一位资深中文小说编辑，请对以下章节进行五维度审查。

## 审查维度（每项0-100分）
1. **去AI痕迹** (de_ai): 解释性否定结构(不是A是B)、AI身体怪癖、AI高频词(微微/缓缓/不禁)、叙事者翻译潜台词
2. **文学质量** (literary): 具体五感细节、节奏感、意象密度
3. **结构完整性** (structure): 开头钩子、推进力、结尾悬念/余韵
4. **人物一致性** (character): 符合人物设定与开关模型、对话风格区分、动机可信
5. **大纲符合度** (outline): 关键事件覆盖、顺序合理、无大纲外多余情节

## 章节正文
{chapter_text}

## 本章大纲
{outline}

## 相关人物设定
{characters}

## 本轮应落实的写作技巧法则
{technique_context}

## 输出格式(严格JSON，只输出JSON)
```json
{{"overall_score": 85,
  "de_ai": {{"score": 80, "issues": ["..."], "suggestions": ["..."]}},
  "literary": {{"score": 88, "issues": [], "suggestions": []}},
  "structure": {{"score": 85, "issues": [], "suggestions": []}},
  "character": {{"score": 90, "issues": [], "suggestions": []}},
  "outline": {{"score": 82, "issues": [], "suggestions": []}},
  "technique": {{"score": 85, "issues": ["未把白描/留白落实到动作和物件"], "suggestions": ["减少解释性心理总结，改用动作、停顿和物件承压"]}}}}
```
只输出JSON，不要其他内容。"""

_DIMS = ["de_ai", "literary", "structure", "character", "outline", "technique"]
_DIM_NAMES = {
    "de_ai": "去AI痕迹", "literary": "文学质量", "structure": "结构完整性",
    "character": "人物一致性", "outline": "大纲符合度", "technique": "技法落实度",
}


def _parse_review_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        return json.loads(text)
    except Exception:
        return None


def model_review_cross(
    chapter_text: str,
    outline: str = "",
    characters: str = "",
    technique_context: str = "",
    model_key: str | None = None,
    pass_threshold: int = 90,
    temperature: float = 0.05,
    max_tokens: int = 3000,
) -> dict[str, Any]:
    """用指定模型（默认 gpt，与生成用的 claude 交叉）做五维审查，返回结构化结果。"""
    cfg = load_runtime_config()
    model_key = resolve_text_model(cfg, "review", model_key)
    spec = cfg.models.get(model_key)
    llm = create_llm(cfg, model_key, temperature=temperature, max_tokens=max_tokens)
    prompt = REVIEW_PROMPT.format(
        chapter_text=chapter_text,
        outline=outline or "（无）",
        characters=characters or "（无）",
        technique_context=technique_context or "（无额外技法法则，按通用写作质量审查。）",
    )
    raw = ""
    try:
        for chunk in llm.stream(prompt):
            raw += getattr(chunk, "content", "") or ""
    except Exception:
        resp = llm.invoke(prompt)
        raw = getattr(resp, "content", "") or ""
    parsed = _parse_review_json(raw)
    if not parsed:
        return {
            "ok": False, "model": model_key, "passed": False, "overall_score": 0,
            "dimensions": [], "raw_response": raw[:2000],
            "error": "模型审查返回无法解析为 JSON",
        }
    overall = int(parsed.get("overall_score", 0) or 0)
    dimensions = []
    for key in _DIMS:
        d = parsed.get(key) or {}
        score = int(d.get("score", overall if key == "technique" else 0) or 0)
        dimensions.append({
            "name": _DIM_NAMES[key],
            "key": key,
            "score": score,
            "issues": d.get("issues") or [],
            "suggestions": d.get("suggestions") or [],
        })
    return {
        "ok": True,
        "model": model_key,
        "model_name": spec.name if spec else model_key,
        "overall_score": overall,
        "passed": overall >= pass_threshold,
        "dimensions": dimensions,
        "raw_response": raw[:2000],
    }


def review_feedback_text(review: dict[str, Any]) -> str:
    """把审查结果整理成回环时喂给生成节点的反馈文本（问题+建议）。"""
    lines = [f"总分 {review.get('overall_score', 0)}（阈值未过）。各维度问题："]
    for dim in review.get("dimensions", []):
        issues = dim.get("issues") or []
        if dim.get("score", 100) < 90 and issues:
            sug = "；".join(dim.get("suggestions") or [])
            lines.append(f"- [{dim['name']} {dim['score']}] {'；'.join(issues)}" + (f"（建议：{sug}）" if sug else ""))
    return "\n".join(lines)
