from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from app.config import ROOT, load_runtime_config
from app.llm_client import create_llm, resolve_text_model
from app.novel_context import novel_dir, normalize_novel_id
from app.project_kinds import SHORT_FILM_KIND, project_kind
from app.project_paths import outputs_dir


def load_skill_text(novel_id: str | None, task: str) -> str:
    from app.short_film_skill_store import load_short_film_skill_text

    return load_short_film_skill_text(novel_id, task)


def refine_short_film_prompt(
    *,
    novel_id: str | None,
    task: str,
    chapter: int | None,
    user_message: str,
    bundle: dict[str, Any],
    model_key: str | None = None,
) -> dict[str, Any]:
    """Rewrite a plain request into a professional short-film provider prompt.

    This is a local project skill layer. It runs before provider fanout and
    persists both the original and refined prompt for auditability.
    """
    nid = normalize_novel_id(novel_id)
    if project_kind(nid) != SHORT_FILM_KIND:
        return {"ok": False, "skipped": True, "reason": "not_short_film"}
    skills = load_skill_text(nid, task)
    if not skills.strip():
        return {"ok": False, "skipped": True, "reason": "no_skill_files"}
    docs = ((bundle.get("materials") or {}).get("project_docs") or {})
    brief = "\n\n".join(f"### {name}\n{text[:2500]}" for name, text in docs.items())[:9000]
    system = (
        "你是电影短片创作制片编辑，负责把普通用户需求改写成可交给多个在线 AI provider "
        "并行作答的专业提问包。你只改写问题，不创作最终答案。"
        "禁止输出寒暄、身份说明、'好的'、'以下是'这类开场白，直接从专业问题或提问包标题开始。"
    )
    user = f"""# 任务
把用户原始需求改写成专业电影短片创作提问包，供千问、DeepSeek、豆包分别作答。

# 项目类型
电影短片脚本项目

# 当前任务
task={task}
场次/段落={chapter or "未指定"}

# 本地技能范式
{skills}

# 项目材料
{brief or "暂无项目材料。"}

# 用户原始需求
{user_message or "（无额外要求）"}

# 改写要求
1. 先明确本次要解决的专业问题，不要泛泛而谈。
2. 按当前任务加入对应检查点：
   - 概念：输出可作为正式剧本前置材料的概念开发稿，包含片名方向、logline、核心命题、主角欲望、阻碍、选择、代价、结尾余味、可拍摄展开要点；应要求多项结构化材料。
   - 剧本：输出场景标题、动作、角色、对白、声音线索和场景节奏。
   - 角色：外观锚点、服装锚点、性格动作、关系弧光、可表演细节。
   - 分镜：镜号、景别、机位/运动、画面、声音、情绪、可拍摄性。
3. 要求 provider 输出可直接融合的结构化结果。
4. 保留用户意图，不替用户扩写最终正文。
5. 不要自行加入用户没有要求的字数限制、篇幅上限或单句极短版本限制。
6. 输出中文，直接给出改写后的完整提问包。
"""
    text = ""
    try:
        cfg = load_runtime_config()
        selected_model = resolve_text_model(cfg, "writing", model_key)
        llm = create_llm(cfg, selected_model, temperature=0.15, max_tokens=2200)
        resp = llm.invoke([{"role": "system", "content": system}, {"role": "human", "content": user}])
        text = _clean_refined_prompt((getattr(resp, "content", "") or "").strip())
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "fallback": user_message}
    if not text:
        return {"ok": False, "error": "empty_refined_prompt", "fallback": user_message}
    path = _save_refined_prompt(nid, task, user_message, text)
    return {
        "ok": True,
        "task": task,
        "path": path,
        "original": user_message,
        "text": text,
        "chars": len(text),
        "model": selected_model,
    }


def _clean_refined_prompt(text: str) -> str:
    """Remove assistant prefaces that should not be sent to providers."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    drop_prefixes = (
        "好的，作为电影短片创作制片编辑",
        "好的，作为一名电影短片创作制片编辑",
        "好的，",
    )
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and any(lines[0].strip().startswith(prefix) for prefix in drop_prefixes):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def _save_refined_prompt(novel_id: str, task: str, original: str, refined: str) -> str:
    out_dir = outputs_dir(novel_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{task}_refined_prompt_{stamp}.md"
    content = "\n\n".join([
        "# Provider 专业提问改写",
        f"- project: {novel_id}",
        f"- task: {task}",
        f"- created_at: {datetime.now().isoformat(timespec='seconds')}",
        "## 原始需求",
        original or "（空）",
        "## 改写后提问包",
        refined,
    ])
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
