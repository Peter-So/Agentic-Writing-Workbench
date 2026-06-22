from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT, load_runtime_config
from app.llm_client import create_llm, resolve_text_model
from app.novel_context import DEFAULT_NOVEL_ID, NOVELS_ROOT, novel_dir, novel_id_from_path, normalize_novel_id
from app.project_paths import project_dir

PENDING_FILE = ROOT / "data" / "writing_updates" / "pending_file_updates.json"

@dataclass
class FileUpdateContext:
    path: Path
    rel_path: str
    old_content: str
    new_content: str
    track: str = "normal"
    novel_id: str = DEFAULT_NOVEL_ID


def after_file_save(ctx: FileUpdateContext) -> dict[str, Any]:
    """Analyze a saved writing file and perform safe downstream updates.

    Chapter prose updates are low-risk continuity memory, so summaries/indexes are
    refreshed automatically. Setting-level changes produce pending proposals only.
    """
    if ctx.old_content == ctx.new_content:
        return {"ok": True, "kind": "unchanged", "message": "文件内容未变化。", "actions": []}
    ctx.novel_id = normalize_novel_id(ctx.novel_id or novel_id_from_path(ctx.path))
    if _is_chapter_file(ctx.path):
        return _handle_chapter_save(ctx)
    if _is_structural_material_file(ctx.path, ctx.novel_id):
        return _handle_setting_save(ctx)
    return {"ok": True, "kind": "file", "message": "文件已保存；当前类型无需联动更新。", "actions": []}


def after_prose_confirm(chapter: int | None, content: str, track: str = "create",
                        novel_id: str | None = None) -> dict[str, Any]:
    """Run the same downstream checks for accepted creative prose."""
    if not chapter or not (content or "").strip():
        return {"ok": True, "kind": "prose", "message": "无正文回流任务。", "actions": []}
    nid = normalize_novel_id(novel_id)
    chapters_dir = project_dir(nid, "chapters")
    ctx = FileUpdateContext(
        path=chapters_dir / f"chapter-{chapter:02d}-accepted.md",
        rel_path=f"novels/{nid}/正文/chapter-{chapter:02d}-accepted.md",
        old_content="",
        new_content=content,
        track=track,
        novel_id=nid,
    )
    return _handle_chapter_save(ctx, chapter=chapter, source="confirm")


def apply_pending_update(update_id: str) -> dict[str, Any]:
    pending = _load_pending()
    item = pending.get(update_id)
    if not item:
        return {"ok": False, "error": "建议不存在或已处理"}
    target = (ROOT / item["target"]).resolve()
    root = ROOT.resolve()
    novels_root = NOVELS_ROOT.resolve()
    if not str(target).startswith(str(root)) or not str(target).startswith(str(novels_root)):
        return {"ok": False, "error": "目标路径非法"}
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    block = _format_proposal_block(item)
    if block in text:
        applied = False
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
        _atomic_write(target, text)
        applied = True
    item["status"] = "applied"
    item["applied_at"] = datetime.now().isoformat(timespec="seconds")
    pending[update_id] = item
    _save_pending(pending)
    return {"ok": True, "applied": applied, "target": item["target"], "proposal": item}


def reject_pending_update(update_id: str) -> dict[str, Any]:
    pending = _load_pending()
    item = pending.get(update_id)
    if not item:
        return {"ok": False, "error": "建议不存在或已处理"}
    item["status"] = "rejected"
    item["rejected_at"] = datetime.now().isoformat(timespec="seconds")
    pending[update_id] = item
    _save_pending(pending)
    return {"ok": True, "proposal": item}


def create_pending_update(
    *,
    novel_id: str,
    target_name: str,
    reason: str,
    patch: str,
    source_path: str,
    source: str = "impact_analysis",
) -> dict[str, Any] | None:
    """Create a user-confirmable setting update proposal."""
    from app.project_structure import resolve_structure_target

    raw_target = str(target_name or "").strip()
    requested_name = raw_target or Path(raw_target).name
    _role, resolved = resolve_structure_target(novel_id, requested_name, create_missing=True)
    if not resolved and raw_target:
        _role, resolved = resolve_structure_target(novel_id, Path(raw_target).name, create_missing=True)
    if not resolved:
        return None
    patch = str(patch or "").strip()
    reason = str(reason or "").strip()
    if not patch or not reason:
        return None
    nid = normalize_novel_id(novel_id)
    target = resolved
    target_name = target.name
    proposal = {
        "id": _pending_id(source_path, target_name, patch),
        "target": str(target.relative_to(ROOT)).replace("\\", "/"),
        "target_name": target_name,
        "reason": reason[:800],
        "patch": patch[:2000],
        "status": "pending",
        "source": source,
        "source_path": source_path,
        "novel_id": nid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    return _store_pending(proposal)


def _handle_chapter_save(
    ctx: FileUpdateContext,
    chapter: int | None = None,
    source: str = "file_save",
) -> dict[str, Any]:
    chapter = chapter or _chapter_from_path(ctx.path)
    actions: list[str] = []
    summary_result: dict[str, Any] | None = None
    if chapter:
        try:
            from app.chapter_summary import save_chapter_summary, summarize_chapter

            res = summarize_chapter(chapter, ctx.new_content)
            summary_result = res
            if res.get("ok"):
                save_chapter_summary(chapter, res["summary"], novel_id=ctx.novel_id)
                actions.append(f"summary_updated(ch{chapter:02d})")
                try:
                    from app.output_index import index_confirmed

                    index_confirmed(ctx.track, "summary", chapter, summary=res["summary"], novel_id=ctx.novel_id)
                    index_confirmed(ctx.track, "prose", chapter, text=ctx.new_content, novel_id=ctx.novel_id)
                    actions.append("rag_index_updated")
                except Exception as exc:
                    actions.append(f"rag_index_failed:{type(exc).__name__}")
            else:
                actions.append(f"summary_failed:{res.get('error', 'unknown')}")
        except Exception as exc:
            summary_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            actions.append(f"summary_failed:{type(exc).__name__}")

    proposals = _propose_related_updates(ctx, chapter=chapter, source=source)
    if proposals:
        actions.append(f"pending_setting_updates({len(proposals)})")
    return {
        "ok": True,
        "kind": "chapter",
        "chapter": chapter,
        "message": _chapter_message(chapter, summary_result, proposals),
        "actions": actions,
        "summary": summary_result,
        "proposals": proposals,
    }


def _handle_setting_save(ctx: FileUpdateContext) -> dict[str, Any]:
    proposals = _propose_setting_propagation(ctx)
    return {
        "ok": True,
        "kind": "setting",
        "message": "设定文件已保存；相关联动建议已生成，需确认后写入其他文件。" if proposals else "设定文件已保存；未发现需要联动的建议。",
        "actions": [f"pending_setting_updates({len(proposals)})"] if proposals else [],
        "proposals": proposals,
    }


def _propose_related_updates(ctx: FileUpdateContext, chapter: int | None, source: str) -> list[dict[str, Any]]:
    outline_chunk = _extract_chapter_outline(chapter, ctx.novel_id) if chapter else ""
    prompt = f"""你是小说项目的连续性编辑。用户刚保存了第{chapter or "未知"}章正文，请判断是否需要更新设定层文档。

硬规则：
- 只输出 JSON。
- 摘要文件已经由系统自动更新，不要建议更新“已完成章节摘要.md”。
- 只有正文新增/改动与现有大纲、人物档案、设定明显不一致，或产生新伏笔/新人物侧面，才给建议。
- 不要直接改文档，只提出待人工确认的建议。
- target 使用项目结构文件名或语义名称，例如：大纲、人物档案、基础设定、世界观设定、情节设定；系统会通过项目结构 Wiki 路由到实际文件。
- patch 必须是可追加到目标文件末尾的短文本块，80-500字。

## 现有本章大纲
{outline_chunk[:4000]}

## 旧正文节选
{ctx.old_content[:5000]}

## 新正文节选
{ctx.new_content[:8000]}

## 输出
{{"updates":[{{"target":"大纲","reason":"为什么需要更新","patch":"建议追加文本"}}]}}
"""
    return _llm_proposals(prompt, source_path=ctx.rel_path, source=source, novel_id=ctx.novel_id)


def _propose_setting_propagation(ctx: FileUpdateContext) -> list[dict[str, Any]]:
    prompt = f"""你是小说项目的设定一致性编辑。用户刚修改了设定文件 `{ctx.path.name}`，请判断是否需要同步提示其他设定文档。

硬规则：
- 只输出 JSON。
- 不要建议更新当前文件本身。
- target 使用项目结构文件名或语义名称，例如：大纲、人物档案、基础设定、世界观设定、情节设定；系统会通过项目结构 Wiki 路由到实际文件。
- 如果没有必要同步，updates 输出空数组。
- patch 必须是可追加到目标文件末尾的短文本块，80-500字。

## 修改前节选
{ctx.old_content[:6000]}

## 修改后节选
{ctx.new_content[:8000]}

## 输出
{{"updates":[{{"target":"人物档案.md","reason":"为什么需要同步","patch":"建议追加文本"}}]}}
"""
    return _llm_proposals(prompt, source_path=ctx.rel_path, source="setting_save",
                          exclude_name=ctx.path.name, novel_id=ctx.novel_id)


def _llm_proposals(
    prompt: str,
    source_path: str,
    source: str,
    novel_id: str,
    exclude_name: str | None = None,
) -> list[dict[str, Any]]:
    try:
        cfg = load_runtime_config()
        model_key = resolve_text_model(cfg, "review")
        llm = create_llm(cfg, model_key, temperature=0.1, max_tokens=1500)
        raw = ""
        for chunk in llm.stream(prompt):
            raw += getattr(chunk, "content", "") or ""
        parsed = _parse_json(raw) or {"updates": []}
    except Exception as exc:
        return [{
            "id": _pending_id(source_path, "analysis-failed", str(exc)),
            "target": "",
            "target_name": "",
            "reason": f"联动分析失败：{type(exc).__name__}: {exc}",
            "patch": "",
            "status": "failed",
            "source": source,
            "source_path": source_path,
            "novel_id": normalize_novel_id(novel_id),
        }]
    updates = []
    for item in parsed.get("updates") or []:
        requested_name = str(item.get("target") or "").strip()
        try:
            from app.project_structure import resolve_structure_target

            _role, resolved = resolve_structure_target(novel_id, requested_name, create_missing=True)
        except Exception:
            resolved = None
        if not resolved:
            continue
        target_name = resolved.name
        if target_name == exclude_name:
            continue
        patch = str(item.get("patch") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not patch or not reason:
            continue
        nid = normalize_novel_id(novel_id)
        target = resolved
        proposal = {
            "id": _pending_id(source_path, target_name, patch),
            "target": str(target.relative_to(ROOT)).replace("\\", "/"),
            "target_name": target_name,
            "reason": reason[:800],
            "patch": patch[:2000],
            "status": "pending",
            "source": source,
            "source_path": source_path,
            "novel_id": nid,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        updates.append(_store_pending(proposal))
    return updates


def _store_pending(proposal: dict[str, Any]) -> dict[str, Any]:
    pending = _load_pending()
    existing = pending.get(proposal["id"])
    if existing and existing.get("status") in {"pending", "applied"}:
        return existing
    pending[proposal["id"]] = proposal
    _save_pending(pending)
    return proposal


def _load_pending() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pending(data: dict[str, dict[str, Any]]) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, PENDING_FILE)


def _pending_id(source_path: str, target_name: str, patch: str) -> str:
    import hashlib

    seed = f"{source_path}\n{target_name}\n{patch}".encode("utf-8", errors="ignore")
    return hashlib.sha1(seed).hexdigest()[:16]


def _format_proposal_block(item: dict[str, Any]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"<!--AUTO_UPDATE:{item['id']}-->\n"
        f"## 待确认联动更新（已采纳） {now}\n\n"
        f"> 来源：`{item.get('source_path', '')}`\n"
        f"> 原因：{item.get('reason', '')}\n\n"
        f"{item.get('patch', '').strip()}\n"
        f"<!--/AUTO_UPDATE:{item['id']}-->"
    )


def _chapter_message(chapter: int | None, summary: dict[str, Any] | None, proposals: list[dict[str, Any]]) -> str:
    bits = []
    if chapter and summary and summary.get("ok"):
        bits.append(f"第{chapter}章摘要已自动更新")
    elif chapter:
        bits.append(f"第{chapter}章摘要更新失败")
    if proposals:
        bits.append(f"{len(proposals)}条设定联动建议待确认")
    if not bits:
        bits.append("文件已保存，无需联动更新")
    return "；".join(bits) + "。"


def _is_chapter_file(path: Path) -> bool:
    return path.suffix.lower() == ".md" and path.parent.name in {"正文", "chapters"} and _chapter_from_path(path) is not None


def _is_structural_material_file(path: Path, novel_id: str | None) -> bool:
    if path.suffix.lower() != ".md":
        return False
    try:
        from app.project_structure import find_related_structure_file, structural_target_names

        if path.name in structural_target_names(novel_id):
            return True
        matched = find_related_structure_file(novel_id, path.name)
        return bool(matched and matched[1].resolve() == path.resolve())
    except Exception:
        return False


def _chapter_from_path(path: Path) -> int | None:
    m = re.search(r"chapter[-_ ]?(\d+)", path.name, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _extract_chapter_outline(chapter: int | None, novel_id: str | None = None) -> str:
    try:
        from app.project_structure import find_related_structure_file, resolve_structure_target

        _role, outline_file = resolve_structure_target(novel_id, "outline", create_missing=False)
        if (not outline_file or not outline_file.exists()):
            matched = find_related_structure_file(novel_id, "outline")
            outline_file = matched[1] if matched else outline_file
    except Exception:
        outline_file = None
    if not chapter or not outline_file or not outline_file.exists():
        return ""
    text = outline_file.read_text(encoding="utf-8", errors="replace")
    cn = _cn_num(chapter)
    pattern = re.compile(
        rf"(^##+\s*(?:第\s*)?(?:{chapter}|{cn})\s*章[^\n]*\n.*?)(?=^##+\s*(?:第\s*)?(?:\d+|[一二三四五六七八九十]+)\s*章|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _cn_num(n: int) -> str:
    nums = "零一二三四五六七八九十"
    if 0 <= n <= 10:
        return nums[n]
    if n < 20:
        return "十" + nums[n - 10]
    if n < 100:
        ten, one = divmod(n, 10)
        return nums[ten] + "十" + (nums[one] if one else "")
    return str(n)


def _parse_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL) or re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(1) if m.re.groups else m.group(0)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
