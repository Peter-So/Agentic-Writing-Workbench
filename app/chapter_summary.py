from __future__ import annotations

import json
import re
from typing import Any

from app.config import load_runtime_config
from app.llm_client import create_llm, resolve_text_model
from app.project_paths import project_dir

# 跨章节进展记忆：每章过审后压成结构化摘要，落盘并供后续章节按关联性检索。
# 补上项目今天的缺口——大纲是静态设计，无"已完成章节实际发生了什么"的累积。
def summary_file(novel_id: str | None = None):
    try:
        from app.project_structure import resolve_structure_target

        _role, path = resolve_structure_target(novel_id, "chapter_summary", create_missing=True)
        if path:
            return path
    except Exception:
        pass
    return project_dir(novel_id, "memory", "已完成章节摘要.md")

SUMMARY_PROMPT = """你是小说连续性记录员。请把下面这一章的正文压成结构化摘要，供后续章节保持连贯。
只输出 JSON，不要其他内容。字段：
- characters: 本章出场人物名（数组）
- events: 本章已发生的关键事件（数组，每条简短）
- resolved: 本章已解决/兑现的钩子或悬念（数组）
- open_threads: 本章新埋下或仍未解决的伏笔/悬念（数组）
- character_changes: 人物状态/关系的变化（数组）
- facts: 本章确立的关键设定增量（数组）

## 第{chapter}章正文
{text}

## 输出格式
```json
{{"characters":[],"events":[],"resolved":[],"open_threads":[],"character_changes":[],"facts":[]}}
```"""


def _parse_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL) or re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(1) if m.re.groups else m.group(0)
    try:
        return json.loads(text)
    except Exception:
        return None


def summarize_chapter(chapter: int, text: str, model_key: str | None = None) -> dict[str, Any]:
    """用便宜模型把整章正文压成结构化摘要。失败返回 ok=False，不阻断主流程。"""
    if not (text or "").strip():
        return {"ok": False, "chapter": chapter, "summary": {}, "error": "空正文"}
    cfg = load_runtime_config()
    try:
        model_key = resolve_text_model(cfg, "review", model_key)
        llm = create_llm(cfg, model_key, temperature=0.1, max_tokens=1500)
        raw = ""
        for chunk in llm.stream(SUMMARY_PROMPT.format(chapter=chapter, text=text[:12000])):
            raw += getattr(chunk, "content", "") or ""
        parsed = _parse_json(raw)
        if not parsed:
            return {"ok": False, "chapter": chapter, "summary": {}, "error": "摘要无法解析"}
        return {"ok": True, "chapter": chapter, "summary": parsed}
    except Exception as exc:
        return {"ok": False, "chapter": chapter, "summary": {}, "error": f"{type(exc).__name__}: {exc}"}


def _load_all(novel_id: str | None = None) -> dict[str, dict[str, Any]]:
    """从摘要文件读出 {chapter: summary}（JSON 块形式存储，逐章一段）。"""
    path = summary_file(novel_id)
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for m in re.finditer(r"<!--CH(\d+)-->\s*```json\s*(\{.*?\})\s*```", path.read_text(encoding="utf-8"), re.DOTALL):
        try:
            out[m.group(1)] = json.loads(m.group(2))
        except Exception:
            continue
    return out


def save_chapter_summary(chapter: int, summary: dict[str, Any], novel_id: str | None = None) -> None:
    """把某章摘要写入摘要文件（按章去重覆盖）。原子写。"""
    import os
    path = summary_file(novel_id)
    all_sum = _load_all(novel_id)
    all_sum[str(chapter)] = summary
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# 已完成章节摘要（自动生成，供跨章节连续性记忆）\n"]
    for ch in sorted(all_sum, key=lambda x: int(x)):
        lines.append(f"\n## 第{ch}章\n<!--CH{ch}-->\n```json\n{json.dumps(all_sum[ch], ensure_ascii=False, indent=2)}\n```\n")
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def relevant_summaries(chapter: int, characters: list[str] | None = None, hints: str = "",
                       novel_id: str | None = None) -> list[dict[str, Any]]:
    """按关联性返回写第 chapter 章时该参考的既往章节摘要。

    强关联：相邻前一章（必带）。
    中关联：更早章节中，与本章出场人物 / 提示词命中的 open_threads/facts 相关的。
    无关：与人物、伏笔、提示都无交集的远章 → 不返回。
    """
    all_sum = _load_all(novel_id)
    if not all_sum:
        return []
    chars = set(characters or [])
    hint_text = (hints or "").lower()
    picked: list[dict[str, Any]] = []
    prev = str(chapter - 1)
    # 强关联：相邻前一章
    if prev in all_sum:
        picked.append({"chapter": chapter - 1, "relation": "adjacent", **all_sum[prev]})
    # 中关联：更早章节按交集判定
    for ch in sorted((c for c in all_sum if int(c) < chapter - 1), key=lambda x: int(x)):
        s = all_sum[ch]
        s_chars = set(s.get("characters") or [])
        blob = " ".join(s.get("open_threads") or []) + " " + " ".join(s.get("facts") or [])
        char_overlap = bool(chars & s_chars)
        hint_overlap = bool(hint_text) and any(w and w in blob.lower() for w in hint_text.split())
        if char_overlap or hint_overlap:
            picked.append({"chapter": int(ch), "relation": "related", **s})
    return picked
