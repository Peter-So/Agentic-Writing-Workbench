from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import queue
import re
import shutil
import threading
import time
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.ai_provider_bridge import provider_status, run_provider_fanout, start_provider_job
from app.ai_provider_jobs import jobs
from app.ai_web_bridge import bridge
from app.chat_history import append_message, load_before, load_by_track, load_recent, soft_delete_project_messages
from app.config import ROOT, load_runtime_config
from app.llm_client import available_image_models, available_models, create_llm, resolve_text_model
from app.novel_context import DEFAULT_NOVEL_ID, WRITING_ROOT, list_novels, novel_dir, novel_id_from_path, normalize_novel_id
from app.writing_agent import WritingAgent
from app.writing_file_policy import editable_message, is_framework_file, path_policy
from app.writing_tools import WritingToolError, project_status
from app.workflow_status import STAGE_LABELS, STAGE_PRESETS, save_pending_workflow_snapshot, stage_preset


STATIC_DIR = ROOT / "app" / "static-writing"
UPLOAD_DIR = WRITING_ROOT / "uploads"
MAX_PREVIEW_CHARS = 200_000
IGNORED_DIRS = {
    ".git",
    "__pycache__",
    "cache",
    "outputs",
    "输出",
    "logs",
    "日志",
    "memory",
    "记忆",
    "ReferenceNovels",
}
IGNORED_REL_DIRS = {"novel-acquisition/novels"}
PREVIEW_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py"}
IGNORED_FILES = {".env", ".env.local"}

app = FastAPI(title="Writing Agent UI", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class WritingChatRequest(BaseModel):
    message: str = Field(default="")
    mode: str = Field(default="auto")
    chapter: int | None = None
    task: str = Field(default="prose")
    dimension: str | None = None
    top_k: int = Field(default=8, ge=1, le=20)
    attachments: list[str] = Field(default_factory=list)
    login_confirmed: dict[str, bool] = Field(default_factory=dict)
    use_provider_source: bool = False
    skip_material_assemble: bool = False
    track: str = Field(default="normal")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    model_preferences: dict[str, str] = Field(default_factory=dict)


class WritingChatResponse(BaseModel):
    answer: str
    intent: str
    actions: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class PlainChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    model_key: str = Field(default="")
    model_preferences: dict[str, str] = Field(default_factory=dict)


class AIProviderRunRequest(BaseModel):
    message: str = Field(..., min_length=1)
    mode: str = Field(default="search")
    chapter: int | None = None
    attachments: list[str] = Field(default_factory=list)
    login_confirmed: dict[str, bool] = Field(default_factory=dict)
    format_for_writing: bool = False
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)


class NeedAuditRequest(BaseModel):
    message: str = Field(default="")
    task: str = Field(default="prose")
    chapter: int | None = None
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    use_provider_source: bool = False


class PendingWorkflowStatusRequest(BaseModel):
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    track: str = Field(default="create")
    invocation_id: str = Field(default="")
    workflow_status: dict[str, Any] = Field(default_factory=dict)


class FileSaveRequest(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = Field(default="")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    run_update_flow: bool = Field(default=False)


class PendingUpdateRequest(BaseModel):
    id: str = Field(..., min_length=1)


class LessonAdoptRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    draft_markdown: str = Field(..., min_length=1)
    source_invocation_id: str = Field(default="")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    task: str = Field(default="")
    apply_to_skill: bool = True


class MemoryPromoteRequest(BaseModel):
    text: str = Field(..., min_length=8)
    title: str = Field(default="", max_length=120)
    source: str = Field(default="")
    task: str = Field(default="")
    target: str = Field(default="project_skill")
    track: str = Field(default="create")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)


class WikiAdoptRequest(BaseModel):
    title: str = Field(default="", max_length=120)
    content: str = Field(..., min_length=8)
    category: str = Field(default="consensus")
    source: str = Field(default="")
    task: str = Field(default="")
    authority: str = Field(default="human_confirmed")
    tags: list[str] = Field(default_factory=list)
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)


class ProjectWikiUpsertRequest(BaseModel):
    title: str = Field(default="", max_length=120)
    content: str = Field(..., min_length=2)
    category: str = Field(default="note")
    source: str = Field(default="")
    task: str = Field(default="")
    entry_id: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)


class ProjectWikiDeleteRequest(BaseModel):
    entry_id: str = Field(..., min_length=1)
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)


class InterveneRequest(BaseModel):
    """创作模式下对某轮回答的人工干预决定。"""
    decision: str = Field(..., min_length=1)  # confirm | reject | other
    original: str = Field(default="")          # 被干预的原始回答
    user_text: str = Field(default="")         # decision=other 时用户提交的答案
    chapter: int | None = None
    task: str = Field(default="prose")
    track: str = Field(default="create")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    invocation_id: str = Field(default="")
    request_analysis: dict[str, Any] = Field(default_factory=dict)
    model_preferences: dict[str, str] = Field(default_factory=dict)


CN_CHAPTER_NUMS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _infer_chapter_from_intervention(req: InterveneRequest, accepted: str) -> int | None:
    if req.chapter:
        return req.chapter
    analysis = req.request_analysis if isinstance(req.request_analysis, dict) else {}
    for key in ("target_chapter", "chapter"):
        value = analysis.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    chapters = analysis.get("context_chapters")
    if isinstance(chapters, list) and len(chapters) == 1:
        value = chapters[0]
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    text = "\n".join([
        str(analysis.get("reason") or ""),
        str(analysis.get("impact_reason") or ""),
        accepted or "",
    ])
    match = re.search(r"第\s*(\d{1,3})\s*章", text)
    if match:
        return int(match.group(1))
    match = re.search(r"第\s*([一二两三四五六七八九十])\s*章", text)
    if match:
        return CN_CHAPTER_NUMS.get(match.group(1))
    return None


def _infer_task_from_intervention(req: InterveneRequest) -> str:
    analysis = req.request_analysis if isinstance(req.request_analysis, dict) else {}
    analyzed = str(analysis.get("task") or "").strip()
    known = {
        "logline", "setting", "world", "worldview", "outline", "character",
        "beat_sheet", "plot", "prose", "expansion", "fix", "screenplay",
        "shot_list", "storyboard", "visual_prompt", "image", "materials",
    }
    if analyzed in known and req.task in {"generic", "draft", "fix", "prose"}:
        return analyzed
    return req.task


def _build_writeback_hint(
    *,
    task: str,
    chapter: int | None,
    kind: str,
    writeback: dict[str, Any] | None,
) -> dict[str, Any] | None:
    try:
        from app.project_kinds import STRONG_NOVEL_KIND
    except Exception:
        STRONG_NOVEL_KIND = "novel_strong"
    if kind != STRONG_NOVEL_KIND or task != "outline" or not chapter:
        return None
    saved = ((writeback or {}).get("novel_artifact") or {}).get("file", "")
    return {
        "type": "outline_chapter_replace",
        "chapter": chapter,
        "target": "大纲.md",
        "message": (
            f"已完成采纳、Wiki/记忆沉淀；第{chapter}章大纲仍需执行二次写回，"
            "确认覆盖后才会替换项目规范大纲文件。"
        ),
        "saved": saved,
    }


def _archive_content_for(task: str, chapter: int | None, accepted: str, kind: str) -> str:
    """Return the text that is safe to write into project files after confirmation.

    Display text may contain review notes or explanation. Archive text must be
    the structural artifact body only. Type-specific extraction belongs here or
    in the relevant artifact adapter, not in the frontend.
    """
    text = accepted or ""
    if not text.strip():
        return ""
    try:
        from app.project_kinds import STRONG_NOVEL_KIND
    except Exception:
        STRONG_NOVEL_KIND = "novel_strong"
    if kind == STRONG_NOVEL_KIND and task == "outline" and chapter:
        try:
            from app.outline_writeback import clean_outline_archive_content

            cleaned = clean_outline_archive_content(text, chapter)
            return cleaned or text
        except Exception:
            return text
    return text


def _archive_required_after_confirm(kind: str, task: str, chapter: int | None) -> bool:
    try:
        from app.project_kinds import STRONG_NOVEL_KIND
    except Exception:
        STRONG_NOVEL_KIND = "novel_strong"
    if kind == STRONG_NOVEL_KIND:
        return task in {"outline", "prose"} and bool(chapter)
    return bool(task)


def _task_for_impact_target(target: str, project_kind: str) -> str:
    try:
        from app.project_structure import task_for_role

        task = task_for_role(str(target or ""))
        if task:
            return task
    except Exception:
        pass
    if target == "chapter_body":
        return "prose"
    text = str(target or "")
    name = Path(text).name
    try:
        if project_kind == "novel_strong":
            from app.novel_artifacts import task_for_target

            return task_for_target(name) or ""
    except Exception:
        pass
    return {
        "concept": "logline",
        "brief": "brief",
        "character": "character",
        "beat_sheet": "beat_sheet",
        "screenplay": "screenplay",
        "shot_list": "shot_list",
        "style": "style",
        "inbox": "materials",
        "ideas": "outline",
        "draft": "draft",
        "references": "materials",
    }.get(text, "")


def _copy_intervene_request(req: InterveneRequest, **updates: Any) -> InterveneRequest:
    try:
        return req.model_copy(update=updates)
    except AttributeError:
        return req.copy(update=updates)


def _recover_intervention_context(
    req: InterveneRequest,
    accepted: str,
    progress=None,
) -> tuple[dict[str, Any], str, int | None, dict[str, Any] | None]:
    analysis = dict(req.request_analysis or {})
    recovered = None
    try:
        from app.pending_intent_memory import merge_recovered_context, recover_pending_intent

        recovered = recover_pending_intent(
            novel_id=req.novel_id,
            track=req.track,
            invocation_id=req.invocation_id,
        )
        analysis, task, chapter = merge_recovered_context(
            request_analysis=analysis,
            task=req.task,
            chapter=req.chapter,
            recovered=recovered,
        )
    except Exception:
        task, chapter = req.task, req.chapter

    # Final fallback: if memory/log recovery failed and the frontend carried no
    # analysis, ask the LLM to reconstruct the likely intent from the accepted text.
    if not recovered and accepted and not analysis.get("task"):
        try:
            from app.project_kinds import project_kind
            from app.writing_request_analysis import analyze_writing_request
            from app.writing_tools import project_progress

            _intervene_progress(progress, "llm_analysis", "LLM 重新分析意图")
            analysis = analyze_writing_request(
                message=accepted[:3000],
                mode="draft",
                task=req.task,
                chapter=req.chapter,
                project_kind=project_kind(req.novel_id),
                novel_id=req.novel_id,
                project_progress=project_progress(req.novel_id),
                model_key=req.model_preferences.get("chat"),
            )
            task = str(analysis.get("task") or task)
            chapter = _clean_positive_int(analysis.get("target_chapter")) or chapter
        except Exception as exc:
            analysis = {**analysis, "recovery_error": f"{type(exc).__name__}: {exc}"}

    context_req = _copy_intervene_request(
        req,
        request_analysis=analysis,
        task=task,
        chapter=chapter,
    )
    effective_chapter = _infer_chapter_from_intervention(context_req, accepted) if accepted else chapter
    effective_task = _infer_task_from_intervention(context_req)
    if effective_chapter:
        analysis["target_chapter"] = effective_chapter
    if effective_task:
        analysis["task"] = effective_task
    if recovered:
        analysis.setdefault("related_files", recovered.get("related_files") or [])
        analysis.setdefault("recovered_from", recovered.get("memory_source") or "pending_intent")
    return analysis, effective_task, effective_chapter, recovered


def _clean_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _complete_pending_after_archive(novel_id: str, track: str, invocation_id: str = "") -> None:
    try:
        from app.pending_intent_memory import complete_pending_intent

        complete_pending_intent(
            novel_id=novel_id,
            track=track,
            invocation_id=invocation_id,
            status="archived",
        )
    except Exception:
        pass


def _cleanup_after_success(novel_id: str, task_scope: str) -> dict[str, Any]:
    try:
        from app.writing_cleanup import cleanup_after_task

        return cleanup_after_task(novel_id, task_scope=task_scope)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _record_archive_result(
    *,
    novel_id: str,
    track: str,
    invocation_id: str,
    task: str,
    chapter: int | None,
    result: dict[str, Any],
) -> None:
    try:
        if invocation_id:
            try:
                from app.writing_invocations import finish_invocation, invocation_rel_path

                finish_invocation(
                    novel_id,
                    invocation_id,
                    status="completed",
                    label="归档已完成",
                    details={"task": task, "chapter": chapter},
                    artifacts={
                        "invocation_log": invocation_rel_path(novel_id, invocation_id),
                        "archive_file": result.get("file", ""),
                        "backup": result.get("backup", ""),
                    },
                )
            except Exception:
                pass
        append_message({
            "role": "system",
            "kind": "archive_result",
            "track": track,
            "text": "归档已完成。",
            "data": {
                "status": "archived",
                "task": task,
                "chapter": chapter,
                "invocation_id": invocation_id,
                "file": result.get("file", ""),
                "backup": result.get("backup", ""),
                "result": result,
            },
            "novel_id": novel_id,
        })
    except Exception:
        pass


class ArchiveArtifactRequest(BaseModel):
    task: str = Field(default="draft")
    content: str = Field(..., min_length=1)
    overwrite: bool = False
    track: str = Field(default="create")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    invocation_id: str = Field(default="")


class ArchiveOutlineRequest(BaseModel):
    chapter: int = Field(..., ge=1)
    content: str = Field(..., min_length=1)
    overwrite: bool = False
    track: str = Field(default="create")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    invocation_id: str = Field(default="")


class ConfirmedProviderAnswer(BaseModel):
    provider: str = Field(default="")
    name: str = Field(default="")
    status: str = Field(default="success")
    result: str = Field(default="")
    files: list[str] = Field(default_factory=list)


class ProviderConfirmRequest(BaseModel):
    answers: list[ConfirmedProviderAnswer] = Field(default_factory=list)
    chapter: int | None = None
    task: str = Field(default="prose")
    track: str = Field(default="create")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    checkpoint_id: str = Field(default="")
    invocation_id: str = Field(default="")
    model_preferences: dict[str, str] = Field(default_factory=dict)


class VisualPromptRequest(BaseModel):
    task: str = Field(default="screenplay")
    content: str = Field(default="")
    source_path: str = Field(default="")
    overwrite_script: bool = True
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    beat: int | None = None
    storyboard_dir: str = Field(default="")
    limit: int | None = None
    image_model_key: str = Field(default="")
    model_preferences: dict[str, str] = Field(default_factory=dict)


class ChatLogRequest(BaseModel):
    role: str = Field(..., min_length=1)
    kind: str = Field(default="text")
    text: str = Field(default="")
    meta: str = Field(default="")
    data: dict[str, Any] = Field(default_factory=dict)
    track: str = Field(default="normal")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)


class ProjectCreateRequest(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=40)
    project_type: str = Field(..., min_length=1)


class ProjectDeleteRequest(BaseModel):
    confirm: bool = False


class CleanupRequest(BaseModel):
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    include_global: bool = False


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/writing/status")
def status(novel_id: str = Query(DEFAULT_NOVEL_ID)) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    runtime = load_runtime_config()
    data = project_status(nid)
    data["llm"] = {
        "roles": runtime.model_roles,
        "models": available_models(runtime),
        "image_models": available_image_models(runtime),
    }
    data["novels"] = list_novels()
    try:
        from app.project_kinds import project_kind
        from app.writing_sop import sop_summary

        data["workflow_sop"] = sop_summary(project_kind(nid))
    except Exception:
        data["workflow_sop"] = {}
    try:
        from app.writing_invocations import cost_board, list_recent_invocations
        from app.writing_mission import mission_hub

        recent = list_recent_invocations(nid, limit=1)
        board = cost_board(nid, limit=20)
        mission = mission_hub(nid, limit=5)
        cost_summary = board.get("summary") or {}
        if recent:
            latest = recent[0]
            data["collaboration"] = {
                "latest_invocation_id": latest.get("id", ""),
                "latest_status": latest.get("status", ""),
                "trajectory_count": len(latest.get("trajectory") or []),
                "harness_count": len(latest.get("harness") or []),
                "budget_count": len(latest.get("budgets") or []),
                "cost_summary": cost_summary,
                "mission_stage": mission.get("active_stage", ""),
            }
        else:
            data["collaboration"] = {
                "latest_invocation_id": "",
                "latest_status": "empty",
                "trajectory_count": 0,
                "harness_count": 0,
                "budget_count": 0,
                "cost_summary": cost_summary,
                "mission_stage": mission.get("active_stage", ""),
            }
    except Exception:
        data["collaboration"] = {}
    return data


@app.get("/api/writing/pending-status")
def pending_workflow_status(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    track: str = Query("create"),
    invocation_id: str = Query(""),
) -> dict[str, Any]:
    from app.pending_intent_memory import latest_pending_workflow_status

    return latest_pending_workflow_status(
        novel_id=novel_id,
        track=track,
        invocation_id=invocation_id,
    )


@app.post("/api/writing/pending-status")
def update_pending_workflow_status_endpoint(req: PendingWorkflowStatusRequest) -> dict[str, Any]:
    from app.pending_intent_memory import update_pending_workflow_status

    return update_pending_workflow_status(
        novel_id=req.novel_id,
        track=req.track,
        invocation_id=req.invocation_id,
        workflow_status=req.workflow_status,
    )


@app.get("/api/writing/workflow-stages")
def workflow_stages_endpoint() -> dict[str, Any]:
    return {"ok": True, "presets": STAGE_PRESETS, "labels": STAGE_LABELS}


@app.get("/api/writing/models")
def writing_models() -> dict[str, Any]:
    runtime = load_runtime_config()
    return {
        "ok": True,
        "roles": runtime.model_roles,
        "models": available_models(runtime),
        "image_models": available_image_models(runtime),
    }


@app.get("/api/writing/sop")
def writing_sop_endpoint(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    task: str = Query("prose"),
) -> dict[str, Any]:
    from app.project_kinds import project_kind
    from app.writing_sop import sop_for_task

    nid = normalize_novel_id(novel_id)
    return {"ok": True, "sop": sop_for_task(project_kind(nid), task)}


@app.get("/api/writing/doctor")
def writing_doctor(novel_id: str = Query(DEFAULT_NOVEL_ID)) -> dict[str, Any]:
    from app.writing_doctor import run_writing_doctor

    return run_writing_doctor(novel_id)


@app.get("/api/writing/cleanup-preview")
def writing_cleanup_preview(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    include_global: bool = Query(False),
) -> dict[str, Any]:
    from app.writing_cleanup import cleanup_preview

    return cleanup_preview(novel_id, include_global=include_global)


@app.post("/api/writing/cleanup")
def writing_cleanup(req: CleanupRequest) -> dict[str, Any]:
    from app.writing_cleanup import cleanup_after_task

    return cleanup_after_task(
        req.novel_id,
        task_scope="manual",
        dry_run=False,
        include_global=req.include_global,
    )


@app.post("/api/writing/need-audit")
def writing_need_audit(req: NeedAuditRequest) -> dict[str, Any]:
    from app.project_kinds import project_kind
    from app.writing_need_audit import audit_need

    nid = normalize_novel_id(req.novel_id)
    return {"ok": True, "audit": audit_need(
        message=req.message,
        project_kind=project_kind(nid),
        task=req.task,
        chapter=req.chapter,
        use_provider_source=req.use_provider_source,
    )}


@app.get("/api/writing/mission")
def writing_mission(novel_id: str = Query(DEFAULT_NOVEL_ID), limit: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    from app.writing_mission import mission_hub

    return mission_hub(novel_id, limit=limit)


@app.get("/api/writing/files")
def files(novel_id: str | None = Query(None)) -> dict[str, Any]:
    if novel_id:
        nid = normalize_novel_id(novel_id)
        root = novel_dir(nid)
        tree = build_file_tree(WRITING_ROOT, str(root.relative_to(WRITING_ROOT)).replace("\\", "/"))
        annotate_file_tree_with_structure(tree, nid)
        return {"root": tree, "novel_id": nid, "scope": "novel"}
    return {"root": build_file_tree(WRITING_ROOT), "scope": "writing"}


@app.get("/api/writing/file")
def file_preview(path: str = Query(...)) -> dict[str, Any]:
    target = safe_project_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    policy = path_policy(target)
    if target.suffix.lower() not in PREVIEW_SUFFIXES:
        return {
            "path": path,
            "name": target.name,
            "content": "",
            "truncated": False,
            "previewable": False,
            "editable": False,
            "protected": policy["protected"],
            "policy_reason": policy["reason"],
            "message": "当前文件类型暂不支持预览。",
        }
    text = target.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > MAX_PREVIEW_CHARS
    if truncated:
        text = text[:MAX_PREVIEW_CHARS]
    return {
        "path": path,
        "name": target.name,
        "content": text,
        "truncated": truncated,
        "previewable": True,
        "editable": policy["editable"],
        "protected": policy["protected"],
        "policy_reason": policy["reason"],
        "message": editable_message(target),
        "size": target.stat().st_size,
    }


@app.post("/api/writing/file")
def file_save(req: FileSaveRequest) -> dict[str, Any]:
    nid = normalize_novel_id(req.novel_id)
    target = safe_project_path(req.path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.suffix.lower() not in PREVIEW_SUFFIXES:
        raise HTTPException(status_code=400, detail="当前文件类型不支持保存")
    if is_framework_file(target):
        raise HTTPException(status_code=403, detail=editable_message(target))
    old_content = target.read_text(encoding="utf-8", errors="replace")
    try:
        target.write_text(req.content, encoding="utf-8", newline="")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入失败：{exc}")
    update = {"ok": True, "kind": "file", "message": "文件已直接保存，未执行联动分析。", "actions": []}
    if req.run_update_flow:
        try:
            from app.file_update_flow import FileUpdateContext, after_file_save
            update = after_file_save(FileUpdateContext(
                path=target,
                rel_path=req.path,
                old_content=old_content,
                new_content=req.content,
                track="create",
                novel_id=novel_id_from_path(target) or nid,
            ))
        except Exception as exc:
            update = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": True,
        "path": req.path,
        "name": target.name,
        "size": target.stat().st_size,
        "novel_id": novel_id_from_path(target),
        "update": update,
    }


@app.post("/api/writing/file-update/apply")
def file_update_apply(req: PendingUpdateRequest) -> dict[str, Any]:
    from app.file_update_flow import apply_pending_update
    return apply_pending_update(req.id)


@app.post("/api/writing/file-update/reject")
def file_update_reject(req: PendingUpdateRequest) -> dict[str, Any]:
    from app.file_update_flow import reject_pending_update
    return reject_pending_update(req.id)


def read_provider_answer_file(path: str) -> str:
    rel = Path(path.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        return ""
    target = (ROOT / rel).resolve()
    writing_root = WRITING_ROOT.resolve()
    if not str(target).startswith(str(writing_root)) or not target.is_file():
        return ""
    suffix = target.suffix.lower()
    if suffix in {".txt", ".md", ".json"}:
        return target.read_text(encoding="utf-8", errors="replace")[:80_000]
    if suffix == ".docx":
        try:
            import re as _re
            import zipfile
            import xml.etree.ElementTree as ET

            with zipfile.ZipFile(target) as zf:
                xml = zf.read("word/document.xml")
            root = ET.fromstring(xml)
            texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
            return _re.sub(r"\n{3,}", "\n\n", "\n".join(texts)).strip()[:80_000]
        except Exception:
            return ""
    return ""


MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB 上传上限，防磁盘溢出


@app.post("/api/writing/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload.bin").name
    target = UPLOAD_DIR / safe_name
    written = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="文件超过 20MB 上限")
                out.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"上传失败：{exc}")
    return {
        "name": safe_name,
        "path": str(target.relative_to(ROOT)).replace("\\", "/"),
        "content_type": file.content_type,
        "size": target.stat().st_size,
    }


@app.post("/api/writing/reference-novels/import-stream")
async def import_reference_novel_stream(
    file: UploadFile = File(...),
    novel_id: str = Query(DEFAULT_NOVEL_ID),
) -> StreamingResponse:
    from app.reference_importer import MAX_REFERENCE_NOVEL_BYTES, import_reference_novel

    filename = Path(file.filename or "reference.txt").name
    content = await file.read(MAX_REFERENCE_NOVEL_BYTES + 1)
    if len(content) > MAX_REFERENCE_NOVEL_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 120MB 上限")

    def events():
        q: queue.Queue[tuple[str, Any]] = queue.Queue()

        def progress(stage: str, label: str, status: str = "running", details: dict[str, Any] | None = None) -> None:
            q.put(("progress", {
                "stage": stage,
                "label": label,
                "status": status,
                "details": details or {},
            }))

        def run() -> None:
            try:
                data = import_reference_novel(filename=filename, content=content, progress=progress)
                data["project_status"] = project_status(normalize_novel_id(novel_id))
                q.put(("done", data))
            except Exception as exc:
                q.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
            finally:
                q.put(("end", {}))

        threading.Thread(target=run, daemon=True).start()
        while True:
            event, data = q.get()
            if event == "end":
                break
            yield _web_sse(event, data)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/ai-providers/status")
def ai_provider_status() -> dict[str, Any]:
    return provider_status()


@app.post("/api/ai-providers/run")
async def ai_provider_run(req: AIProviderRunRequest) -> dict[str, Any]:
    return await run_provider_fanout(
        message=req.message,
        mode=req.mode,
        chapter=req.chapter,
        attachments=req.attachments,
        login_confirmed=req.login_confirmed,
        format_for_writing=req.format_for_writing,
        novel_id=req.novel_id,
    )


@app.post("/api/ai-providers/run-async")
async def ai_provider_run_async(req: AIProviderRunRequest) -> dict[str, Any]:
    """后台启动协同任务，立即返回 job_id；前端轮询 job 状态展示实时进度。"""
    return start_provider_job(
        message=req.message,
        mode=req.mode,
        chapter=req.chapter,
        attachments=req.attachments,
        login_confirmed=req.login_confirmed,
        format_for_writing=req.format_for_writing,
        novel_id=req.novel_id,
    )


@app.get("/api/ai-providers/job/{job_id}")
def ai_provider_job(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job.snapshot()


@app.get("/api/writing/invocation/{invocation_id}")
def writing_invocation(invocation_id: str, novel_id: str = Query(DEFAULT_NOVEL_ID)) -> dict[str, Any]:
    from app.writing_invocations import get_invocation

    data = get_invocation(novel_id, invocation_id)
    if data is None:
        raise HTTPException(status_code=404, detail="任务记录不存在")
    return {"ok": True, "invocation": data}


@app.get("/api/writing/cost-board")
def writing_cost_board(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    from app.writing_invocations import cost_board

    return cost_board(novel_id, limit=limit)


@app.get("/api/writing/harness-suggestions")
def writing_harness_suggestions(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    from app.writing_auto_harness import harness_suggestions

    return harness_suggestions(novel_id, limit=limit)


@app.get("/api/writing/trajectory/{invocation_id}")
def writing_trajectory_endpoint(invocation_id: str, novel_id: str = Query(DEFAULT_NOVEL_ID)) -> dict[str, Any]:
    from app.writing_trajectory import trajectory_review

    data = trajectory_review(novel_id, invocation_id)
    if data is None:
        raise HTTPException(status_code=404, detail="任务轨迹不存在")
    return data


@app.get("/api/writing/review-packet/{invocation_id}")
def writing_review_packet_endpoint(
    invocation_id: str,
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    write_file: bool = Query(True),
) -> dict[str, Any]:
    from app.writing_review_packet import review_packet

    data = review_packet(novel_id, invocation_id, write_file=write_file)
    if data is None:
        raise HTTPException(status_code=404, detail="任务记录不存在")
    return data


@app.get("/api/writing/lessons")
def writing_lessons_endpoint(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    from app.writing_lessons import list_lessons

    return list_lessons(limit=limit)


@app.get("/api/writing/lessons/suggestions")
def writing_lesson_suggestions(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    from app.writing_lessons import lesson_suggestions

    return lesson_suggestions(novel_id, limit=limit)


@app.post("/api/writing/lessons/adopt")
def writing_lesson_adopt(req: LessonAdoptRequest) -> dict[str, Any]:
    from app.writing_lessons import adopt_lesson

    try:
        return adopt_lesson(
            req.title,
            req.draft_markdown,
            req.source_invocation_id,
            novel_id=req.novel_id,
            task=req.task,
            apply_to_skill=req.apply_to_skill,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/writing/wiki")
def writing_wiki_endpoint(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    limit: int = Query(100, ge=1, le=300),
) -> dict[str, Any]:
    from app.writing_wiki import list_wiki

    return list_wiki(novel_id, limit=limit)


@app.get("/api/writing/wiki/recall")
def writing_wiki_recall_endpoint(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    query: str = Query(""),
    task: str = Query(""),
    limit: int = Query(8, ge=1, le=30),
) -> dict[str, Any]:
    from app.writing_wiki import recall_wiki

    return {"ok": True, "novel_id": normalize_novel_id(novel_id), "items": recall_wiki(novel_id, query=query, task=task, limit=limit)}


@app.post("/api/writing/wiki/adopt")
def writing_wiki_adopt(req: WikiAdoptRequest) -> dict[str, Any]:
    from app.writing_wiki import adopt_wiki_entry

    try:
        return adopt_wiki_entry(
            req.novel_id,
            title=req.title,
            content=req.content,
            category=req.category,
            source=req.source,
            task=req.task,
            authority=req.authority,
            tags=req.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/writing/wiki/seed")
def writing_wiki_seed(novel_id: str = Query(DEFAULT_NOVEL_ID)) -> dict[str, Any]:
    from app.writing_wiki import seed_wiki_from_existing

    return seed_wiki_from_existing(novel_id)


@app.get("/api/writing/project-wiki")
def writing_project_wiki_endpoint(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    limit: int = Query(100, ge=1, le=300),
    category: str = Query(""),
) -> dict[str, Any]:
    from app.project_wiki import list_project_wiki

    return list_project_wiki(novel_id, limit=limit, category=category)


@app.get("/api/writing/project-wiki/entry")
def writing_project_wiki_entry_endpoint(
    entry_id: str = Query(..., min_length=1),
    novel_id: str = Query(DEFAULT_NOVEL_ID),
) -> dict[str, Any]:
    from app.project_wiki import get_project_wiki_entry

    try:
        return get_project_wiki_entry(novel_id, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/writing/project-wiki/recall")
def writing_project_wiki_recall_endpoint(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    query: str = Query(""),
    task: str = Query(""),
    limit: int = Query(8, ge=1, le=30),
) -> dict[str, Any]:
    from app.project_wiki import recall_project_wiki

    return {"ok": True, "novel_id": normalize_novel_id(novel_id), "items": recall_project_wiki(novel_id, query=query, task=task, limit=limit)}


@app.post("/api/writing/project-wiki/upsert")
def writing_project_wiki_upsert(req: ProjectWikiUpsertRequest) -> dict[str, Any]:
    from app.project_wiki import upsert_project_wiki_entry

    try:
        return upsert_project_wiki_entry(
            req.novel_id,
            title=req.title,
            content=req.content,
            category=req.category,
            source=req.source,
            task=req.task,
            entry_id=req.entry_id,
            tags=req.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/writing/project-wiki/delete")
def writing_project_wiki_delete(req: ProjectWikiDeleteRequest) -> dict[str, Any]:
    from app.project_wiki import delete_project_wiki_entry

    try:
        return delete_project_wiki_entry(req.novel_id, req.entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/writing/project-wiki/seed")
def writing_project_wiki_seed(novel_id: str = Query(DEFAULT_NOVEL_ID)) -> dict[str, Any]:
    from app.project_wiki import seed_project_wiki_from_structure

    return seed_project_wiki_from_structure(novel_id)


@app.get("/api/writing/memory-governance")
def writing_memory_governance(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    from app.writing_memory_governance import memory_governance_report

    return memory_governance_report(novel_id, limit=limit)


@app.post("/api/writing/memory-governance/promote")
def writing_memory_promote(req: MemoryPromoteRequest) -> dict[str, Any]:
    from app.writing_memory_governance import promote_memory_candidate

    try:
        return promote_memory_candidate(
            req.novel_id,
            req.text,
            title=req.title,
            source=req.source,
            task=req.task,
            target=req.target,
            track=req.track,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/writing/benchmark")
def writing_benchmark(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    from app.writing_benchmark import run_writing_benchmark

    return run_writing_benchmark(novel_id, limit=limit)


@app.get("/api/writing/recall-eval")
def writing_recall_eval(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    from app.writing_recall_eval import recall_eval

    return recall_eval(novel_id, limit=limit)


@app.get("/api/writing/skills")
def writing_skills(
    novel_id: str = Query(DEFAULT_NOVEL_ID),
    task: str = Query(""),
) -> dict[str, Any]:
    from app.writing_skills_registry import skills_registry

    return skills_registry(novel_id, task=task)


@app.get("/api/chat/history")
def chat_history(novel_id: str = Query(DEFAULT_NOVEL_ID)) -> dict[str, Any]:
    """加载最新最多 100 条对话记录（重启/刷新不丢失）。"""
    return _clean_chat_history(load_recent(novel_id))


@app.get("/api/chat/history/before")
def chat_history_before(seq: int = Query(..., ge=1), limit: int = Query(20, ge=1, le=100),
                        novel_id: str = Query(DEFAULT_NOVEL_ID)) -> dict[str, Any]:
    """向上滚动时加载 seq 之前的历史记录，每次最多 20 条。"""
    return _clean_chat_history(load_before(seq, limit, novel_id))


@app.post("/api/chat/log")
def chat_log(req: ChatLogRequest) -> dict[str, Any]:
    """持久化一条对话消息（用户输入、助手回复、provider 结果卡片等），按 track 区分创作/普通。"""
    return append_message(_clean_chat_message(req.model_dump()))


def _clean_chat_message(message: dict[str, Any]) -> dict[str, Any]:
    if message.get("kind") != "draft_result":
        return message
    try:
        from app.final_text_cleaner import clean_final_draft
    except Exception:
        return message
    data = dict(message.get("data") or {})
    task = str(data.get("task") or "")
    kind = str(data.get("project_kind") or "")
    cleaned_text = clean_final_draft(str(message.get("text") or ""), task=task, project_kind=kind)
    if cleaned_text:
        message["text"] = cleaned_text
    for key in ("original", "archive_content"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            data[key] = clean_final_draft(value, task=task, project_kind=kind)
    message["data"] = data
    return message


def _clean_chat_history(response: dict[str, Any]) -> dict[str, Any]:
    messages = response.get("messages")
    if not isinstance(messages, list):
        return response
    return {**response, "messages": [_clean_chat_message(dict(item)) for item in messages]}


@app.post("/api/writing/project")
def create_writing_project(req: ProjectCreateRequest) -> dict[str, Any]:
    """创建新创作项目。项目 ID 仅允许英文和数字，避免中文目录影响检索/路径处理。"""
    project_id = req.project_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9]{1,40}", project_id):
        raise HTTPException(status_code=400, detail="项目名只支持英文和数字，长度 1-40")
    type_map = {
        "novel": "novel_strong",
        "short_film": "short_film",
        "generic": "generic",
        "casual": "generic",
        "随想": "generic",
    }
    kind = type_map.get(req.project_type)
    if not kind:
        raise HTTPException(status_code=400, detail="项目类型不支持")
    from app.project_kinds import create_project
    result = create_project(project_id, kind)
    if result.get("exists"):
        raise HTTPException(status_code=409, detail=result.get("message", "项目已存在"))
    append_message({
        "role": "system",
        "kind": "text",
        "text": f"项目 {project_id} 已创建。",
        "track": "normal",
        "novel_id": project_id,
    })
    return {"ok": True, "novel_id": project_id, "project_kind": result.get("kind"), **result}


@app.delete("/api/writing/project/{project_id}")
def delete_writing_project(project_id: str, req: ProjectDeleteRequest | None = None) -> dict[str, Any]:
    """Soft-delete a writing project by moving files and chat logs to the hidden trash area."""
    if req is not None and not req.confirm:
        raise HTTPException(status_code=400, detail="缺少删除确认")
    try:
        nid = normalize_novel_id(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    target = novel_dir(nid)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail=f"项目 {nid} 不存在")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    trash_root = WRITING_ROOT / ".trash" / "projects"
    trash_root.mkdir(parents=True, exist_ok=True)
    trash_target = trash_root / f"{nid}_{stamp}"
    index = 1
    while trash_target.exists():
        trash_target = trash_root / f"{nid}_{stamp}_{index}"
        index += 1
    try:
        shutil.move(str(target), str(trash_target))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"项目移动到回收区失败：{exc}")
    chat_result = soft_delete_project_messages(nid, WRITING_ROOT / ".trash" / "chat_history")
    novels = list_novels()
    next_novel = novels[0]["id"] if novels else ""
    return {
        "ok": True,
        "deleted": nid,
        "trash_path": str(trash_target.relative_to(ROOT)).replace("\\", "/"),
        "chat": chat_result,
        "next_novel": next_novel,
        "novels": novels,
    }


def _web_sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _intervene_progress(progress, stage: str, label: str, status: str = "running", **details: Any) -> None:
    if not progress:
        return
    progress({
        "stage": stage,
        "label": label,
        "status": status,
        "at": time.time(),
        "details": {key: value for key, value in details.items() if value not in (None, "", [], {})},
    })


@app.post("/api/writing/intervene")
def writing_intervene(req: InterveneRequest) -> dict[str, Any]:
    return _writing_intervene_impl(req)


@app.post("/api/writing/intervene-stream")
def writing_intervene_stream(req: InterveneRequest) -> StreamingResponse:
    def events():
        q: queue.Queue[tuple[str, Any]] = queue.Queue()
        done_marker = object()
        stages = stage_preset("intervention")
        done: list[str] = []

        def progress(data: dict[str, Any]) -> None:
            stage = str(data.get("stage") or "")
            if stage:
                for item in stages:
                    if item == stage:
                        break
                    if item not in done:
                        done.append(item)
                save_pending_workflow_snapshot(
                    novel_id=req.novel_id,
                    track=req.track,
                    invocation_id=req.invocation_id,
                    stages=stages,
                    current=stage,
                    done=done,
                    status=str(data.get("status") or "running"),
                    task=req.task,
                    chapter=req.chapter,
                    source="intervene_stream",
                )
            q.put(("progress", data))

        def run() -> None:
            try:
                result = _writing_intervene_impl(req, progress=progress)
                q.put(("done", result))
            except Exception as exc:
                q.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
            finally:
                q.put(("_closed", done_marker))

        threading.Thread(target=run, daemon=True).start()
        while True:
            event, data = q.get()
            if data is done_marker:
                break
            yield _web_sse(event, data)

    return StreamingResponse(events(), media_type="text/event-stream")


def _writing_intervene_impl(req: InterveneRequest, progress=None) -> dict[str, Any]:
    """创作模式人工干预：记录确认/拒绝/其他的决定，供后续创作流程学习优化。

    decision=other 时，用户提交的文本视为本轮采纳的最终答案，并持久化。
    人物/大纲环节若被采纳，固化为长期设定。
    """
    decision = req.decision if req.decision in {"confirm", "reject", "other"} else "other"
    _intervene_progress(progress, "submit", "提交确认", decision=decision)
    # 记录干预决定（带 track，进入对话记录的学习轨道）
    append_message({
        "role": "system", "kind": "intervene", "track": req.track,
        "text": {"confirm": "用户确认采纳本轮回答", "reject": "用户拒绝本轮回答",
                 "other": "用户提供了自定义答案"}[decision],
        "data": {"decision": decision, "chapter": req.chapter, "task": req.task,
                 "invocation_id": req.invocation_id,
                 "request_analysis": req.request_analysis,
                 "original": req.original[:2000], "user_text": req.user_text[:8000]},
        "novel_id": req.novel_id,
    })
    accepted = req.user_text if decision == "other" else (req.original if decision == "confirm" else "")
    if accepted:
        try:
            from app.final_text_cleaner import clean_final_draft
            from app.project_kinds import project_kind

            accepted = clean_final_draft(accepted, task=req.task, project_kind=project_kind(req.novel_id))
        except Exception:
            accepted = accepted.strip()
    recovered_intent = None
    if accepted:
        _intervene_progress(progress, "memory_lookup", "记忆查找")
        request_analysis, effective_task, effective_chapter, recovered_intent = _recover_intervention_context(req, accepted, progress=progress)
    else:
        request_analysis = dict(req.request_analysis or {})
        effective_chapter = req.chapter
        effective_task = req.task
    impact_plan = None
    pending_updates: list[dict[str, Any]] = []
    wiki = None
    project_wiki = None
    technique_wiki = None
    if decision in {"confirm", "other"} and accepted:
        _intervene_progress(progress, "knowledge_settle", "知识沉淀")
        try:
            from app.writing_wiki import adopt_wiki_entry

            wiki_tasks = {
                "character": ("character", "角色共识"),
                "outline": ("setting", "大纲共识"),
                "beat_sheet": ("consensus", "节拍共识"),
                "logline": ("consensus", "概念共识"),
                "screenplay": ("setting", "剧本共识"),
                "shot_list": ("setting", "分镜共识"),
                "fix": ("lesson", "修订共识"),
            }
            if effective_task in wiki_tasks:
                category, label = wiki_tasks[effective_task]
                wiki = adopt_wiki_entry(
                    req.novel_id,
                    title=f"{label}：{effective_task}",
                    content=accepted[:5000],
                    category=category,
                    source=f"intervene:{decision}",
                    task=effective_task,
                    authority="human_confirmed",
                )
        except Exception as exc:
            wiki = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            from app.project_wiki import upsert_project_wiki_entry

            project_wiki = upsert_project_wiki_entry(
                req.novel_id,
                title=f"确认采纳：{effective_task}" + (f"｜第{effective_chapter}章" if effective_chapter else ""),
                content="\n".join(filter(None, [
                    f"任务：{effective_task}",
                    f"章节：第{effective_chapter}章" if effective_chapter else "",
                    "类型：用户确认采纳",
                    "",
                    accepted[:4000],
                ])),
                category="decision",
                source=f"intervene:{decision}:{req.invocation_id or effective_task}",
                task=effective_task,
                tags=["确认采纳", effective_task, *( [f"第{effective_chapter}章"] if effective_chapter else [] )],
            )
        except Exception as exc:
            project_wiki = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            from app.project_wiki import upsert_project_wiki_entry
            from app.project_kinds import project_kind
            from app.writing_techniques import technique_context_for_task

            kind_for_technique = project_kind(req.novel_id)
            technique_ctx = technique_context_for_task(
                query="\n".join(filter(None, [
                    str((request_analysis or {}).get("generator_instruction") or ""),
                    str((request_analysis or {}).get("reason") or ""),
                    accepted[:1200],
                ])),
                outline="",
                project_kind=kind_for_technique,
                task=effective_task,
                model_key=req.model_preferences.get("review"),
                max_lines=6,
            )
            if technique_ctx.get("lines"):
                technique_wiki = upsert_project_wiki_entry(
                    req.novel_id,
                    title=f"本轮采用技法：{effective_task}" + (f"｜第{effective_chapter}章" if effective_chapter else ""),
                    content="\n".join(filter(None, [
                        f"任务：{effective_task}",
                        f"章节：第{effective_chapter}章" if effective_chapter else "",
                        "类型：确认采纳后的技法应用记录",
                        "",
                        "本轮可复用技法法则：",
                        *[f"- {line}" for line in technique_ctx.get("lines") or []],
                        "",
                        "使用边界：这些内容只作为本项目后续创作/审查的表达法则，不代表必须照搬本轮剧情、句子、物件或人物。",
                    ])),
                    category="note",
                    source=f"technique:{decision}:{req.invocation_id or effective_task}",
                    task=effective_task,
                    tags=["写作技巧", "技法沉淀", effective_task, *( [f"第{effective_chapter}章"] if effective_chapter else [] )],
                )
        except Exception as exc:
            technique_wiki = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    # 采纳的人物/大纲产出固化为长期设定（与 graph finalize 行为一致），并增量入产出向量库。
    if decision in {"confirm", "other"} and accepted and effective_task in {"character", "outline"}:
        _intervene_progress(progress, "memory_write", "长期记忆写入", task=effective_task, chapter=effective_chapter)
        try:
            from app.writing_memory import save_setting
            key = f"{effective_task}:{effective_chapter}" if effective_chapter else f"{effective_task}:latest"
            save_setting(
                req.track,
                key,
                {"task": effective_task, "content": accepted[:4000], "source": "human"},
                project=req.novel_id,
            )
        except Exception:
            pass
        try:
            from app.output_index import index_confirmed
            index_confirmed(req.track, "setting", effective_chapter, text=accepted, novel_id=req.novel_id)
        except Exception:
            pass
    # 推进偏好学习状态机（learning→suggest→auto），返回更新后的视图。
    policy = None
    try:
        from app.intervene_policy import record_decision
        _intervene_progress(progress, "policy_update", "采纳策略更新", task=effective_task)
        policy = record_decision(req.track, effective_task, decision, req.user_text)
    except Exception:
        pass
    # 确认/改写后即时回写连续性记忆。正文统一走 file_update_flow，
    # 避免 writeback_on_confirm 与文件联动链重复摘要。
    writeback = None
    file_update = None
    try:
        from app.project_kinds import STRONG_NOVEL_KIND, project_kind
        kind = project_kind(req.novel_id)
    except Exception:
        kind = ""
    if decision in {"confirm", "other"} and accepted:
        try:
            from app.project_impact import heuristic_project_impact

            _intervene_progress(progress, "impact_analyze", "影响范围分析", task=effective_task, chapter=effective_chapter)
            # Confirmation must be a fast, recoverable human gate. The request
            # has already been analyzed by LLM and stored in pending intent, so
            # use the deterministic project-structure impact pass here instead
            # of blocking the UI on another review-model call.
            impact_plan = heuristic_project_impact(
                task=effective_task,
                chapter=effective_chapter,
                accepted=accepted,
                novel_id=req.novel_id,
                request_analysis=request_analysis,
            )
        except Exception as exc:
            impact_plan = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if decision in {"confirm", "other"} and accepted and effective_task == "prose" and kind == STRONG_NOVEL_KIND:
        try:
            from app.file_update_flow import after_prose_confirm
            _intervene_progress(progress, "primary_write", "主要文件改写", target="chapter_body", chapter=effective_chapter)
            file_update = after_prose_confirm(effective_chapter, accepted, track=req.track, novel_id=req.novel_id)
            summary = file_update.get("summary") if isinstance(file_update, dict) else None
            writeback = {
                "ok": bool(file_update and file_update.get("ok")),
                "summarized": bool(summary and summary.get("ok")),
                "chapter": effective_chapter,
                "open_threads": ((summary or {}).get("summary") or {}).get("open_threads", []),
            }
        except Exception as exc:
            file_update = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            writeback = {"ok": False, "summarized": False, "error": file_update["error"]}
    elif decision in {"confirm", "other"} and accepted:
        try:
            from app.confirm_writeback import writeback_on_confirm
            _intervene_progress(progress, "primary_write", "主要文件改写", task=effective_task, chapter=effective_chapter)
            writeback = writeback_on_confirm(effective_task, effective_chapter, accepted, track=req.track, novel_id=req.novel_id)
        except Exception:
            pass
        try:
            from app.project_artifacts import save_artifact
            if kind != STRONG_NOVEL_KIND:
                artifact = save_artifact(effective_task, accepted, novel_id=req.novel_id, overwrite=True, track=req.track)
                writeback = {**(writeback or {}), "artifact": artifact}
        except Exception:
            pass
        try:
            if kind == STRONG_NOVEL_KIND and effective_task in {
                "logline", "brief", "materials", "setting", "world", "worldview",
                "character", "characters", "beat_sheet", "plot", "outline",
                "expansion", "fix",
            } and not (effective_task == "outline" and effective_chapter):
                from app.novel_artifacts import save_novel_artifact

                _intervene_progress(progress, "primary_artifact", "结构文件保存", task=effective_task, chapter=effective_chapter)
                novel_artifact = save_novel_artifact(
                    task=effective_task,
                    content=accepted,
                    novel_id=req.novel_id,
                    chapter=effective_chapter,
                    track=req.track,
                )
                writeback = {**(writeback or {}), "novel_artifact": novel_artifact}
        except Exception as exc:
            writeback = {**(writeback or {}), "novel_artifact": {"ok": False, "error": f"{type(exc).__name__}: {exc}"}}
    if decision in {"confirm", "other"} and accepted and impact_plan:
        try:
            extra_saved = []
            primary_file = (
                ((writeback or {}).get("novel_artifact") or {}).get("file")
                or ((writeback or {}).get("artifact") or {}).get("file")
                or ""
            )
            for change in impact_plan.get("changes") or []:
                if not change.get("auto_apply"):
                    continue
                target = str(change.get("target") or "")
                if target == "chapter_body":
                    continue
                target_task = _task_for_impact_target(target, kind)
                if not target_task:
                    continue
                target_path = str(change.get("target_path") or target)
                if primary_file and (primary_file.endswith(target_path) or primary_file.endswith(Path(target_path).name)):
                    continue
                if kind == STRONG_NOVEL_KIND:
                    from app.novel_artifacts import save_novel_artifact

                    _intervene_progress(progress, "related_write", "关联文件改写", target=target)
                    result = save_novel_artifact(
                        task=target_task,
                        content=change.get("patch") or accepted,
                        novel_id=req.novel_id,
                        chapter=effective_chapter,
                        track=req.track,
                    )
                else:
                    from app.project_artifacts import save_artifact

                    _intervene_progress(progress, "related_write", "关联文件改写", target=target)
                    result = save_artifact(
                        target_task,
                        change.get("patch") or accepted,
                        novel_id=req.novel_id,
                        overwrite=True,
                        track=req.track,
                    )
                extra_saved.append(result)
            if extra_saved:
                writeback = {**(writeback or {}), "impact_auto_saved": extra_saved}
        except Exception as exc:
            writeback = {**(writeback or {}), "impact_auto_saved_error": f"{type(exc).__name__}: {exc}"}
    if decision in {"confirm", "other"} and accepted and impact_plan:
        try:
            from app.file_update_flow import create_pending_update

            _intervene_progress(progress, "related_pending", "关联文件待确认生成")
            source_path = f"intervene:{req.invocation_id or effective_task}"
            primary_file = (
                ((writeback or {}).get("novel_artifact") or {}).get("file")
                or ((writeback or {}).get("artifact") or {}).get("file")
                or ""
            )
            for change in impact_plan.get("changes") or []:
                target = str(change.get("target") or "")
                target_path = str(change.get("target_path") or target)
                if target == "chapter_body":
                    continue
                if effective_task == "outline" and effective_chapter and kind == STRONG_NOVEL_KIND and target in {"outline"}:
                    continue
                if change.get("auto_apply"):
                    continue
                if primary_file and (primary_file.endswith(target_path) or primary_file.endswith(Path(target_path).name)):
                    continue
                proposal = create_pending_update(
                    novel_id=req.novel_id,
                    target_name=target_path,
                    reason=change.get("reason") or "确认稿影响该材料，需要人工确认是否同步。",
                    patch=change.get("patch") or accepted[:1200],
                    source_path=source_path,
                    source="impact_analysis",
                )
                if proposal:
                    pending_updates.append(proposal)
        except Exception as exc:
            pending_updates.append({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    archive_required_for_invocation = decision in {"confirm", "other"} and bool(accepted) and _archive_required_after_confirm(
        kind,
        effective_task,
        effective_chapter,
    )
    if req.invocation_id:
        try:
            from app.writing_invocations import append_event, finish_invocation, invocation_rel_path
            _intervene_progress(progress, "invocation_finalize", "任务状态归档", invocation_id=req.invocation_id)
            status = "rejected" if decision == "reject" else ("awaiting_archive" if archive_required_for_invocation else "completed")
            label = "用户拒绝本轮产出" if decision == "reject" else (
                "用户确认采纳，等待归档" if archive_required_for_invocation else "用户确认采纳"
            )
            append_event(
                req.novel_id,
                req.invocation_id,
                "user_confirmed_output",
                label,
                node="user_confirm",
                status=status,
                details={"decision": decision, "task": effective_task, "chapter": effective_chapter},
            )
            finish_invocation(
                req.novel_id,
                req.invocation_id,
                status=status,
                label=label,
                details={"decision": decision, "task": effective_task, "chapter": effective_chapter},
                artifacts={"invocation_log": invocation_rel_path(req.novel_id, req.invocation_id)},
            )
        except Exception:
            pass
    writeback_hint = _build_writeback_hint(
        task=effective_task,
        chapter=effective_chapter,
        kind=kind,
        writeback=writeback,
    )
    archive_content = _archive_content_for(effective_task, effective_chapter, accepted, kind)
    archive_required = archive_required_for_invocation
    pending_intent_status = None
    if decision == "reject" or (decision in {"confirm", "other"} and accepted and not writeback_hint and not archive_required):
        try:
            from app.pending_intent_memory import complete_pending_intent

            _intervene_progress(progress, "pending_clear", "清理待确认记忆", invocation_id=req.invocation_id)
            pending_intent_status = complete_pending_intent(
                novel_id=req.novel_id,
                track=req.track,
                invocation_id=req.invocation_id,
                status="rejected" if decision == "reject" else "completed",
            )
        except Exception as exc:
            pending_intent_status = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    cleanup = None
    try:
        from app.writing_cleanup import cleanup_after_task

        _intervene_progress(progress, "cleanup", "清理临时缓存", task=effective_task, chapter=effective_chapter)
        cleanup = cleanup_after_task(req.novel_id, task_scope="intervene")
    except Exception as exc:
        cleanup = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result = {
        "ok": True,
        "decision": decision,
        "accepted": accepted,
        "archive_content": archive_content,
        "normalized_chapter": effective_chapter,
        "normalized_task": effective_task,
        "request_analysis": request_analysis,
        "recovered_intent": recovered_intent,
        "pending_intent_status": pending_intent_status,
        "policy": policy,
        "writeback": writeback,
        "file_update": file_update,
        "impact_plan": impact_plan,
        "pending_updates": pending_updates,
        "writeback_hint": writeback_hint,
        "wiki": wiki,
        "project_wiki": project_wiki,
        "technique_wiki": technique_wiki,
        "cleanup": cleanup,
    }
    if archive_required:
        save_pending_workflow_snapshot(
            novel_id=req.novel_id,
            track=req.track,
            invocation_id=req.invocation_id,
            stages=stage_preset("archive"),
            current="archive_submit",
            done=[],
            status="awaiting_archive",
            task=effective_task,
            chapter=effective_chapter,
            source="intervene_archive_pending",
        )
        append_message({
            "role": "system",
            "kind": "archive_pending",
            "track": req.track,
            "text": "用户已确认采纳，等待归档。",
            "data": {
                "status": "pending",
                "task": effective_task,
                "chapter": effective_chapter,
                "project_kind": kind,
                "invocation_id": req.invocation_id,
                "request_analysis": request_analysis,
                "accepted": archive_content or accepted,
                "archive_content": archive_content,
                "writeback_hint": writeback_hint,
                "created_from": "intervene",
            },
            "novel_id": req.novel_id,
        })
    _intervene_progress(
        progress,
        "complete",
        "已完成归档" if not writeback_hint else "确认完成，等待写回归档",
        status="done",
        task=effective_task,
        chapter=effective_chapter,
    )
    return result


class ArchiveChapterRequest(BaseModel):
    chapter: int = Field(..., ge=1)
    content: str = Field(..., min_length=1)
    title: str = Field(default="")
    overwrite: bool = False
    track: str = Field(default="create")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    invocation_id: str = Field(default="")


@app.post("/api/writing/archive-chapter")
def archive_chapter_endpoint(req: ArchiveChapterRequest) -> dict[str, Any]:
    """显式留档：把确认的正文写入 chapter-NN-*.md 并标记完成状态（破坏性写入需 overwrite）。"""
    from app.confirm_writeback import archive_chapter, resolve_chapter_archive_title

    request_analysis: dict[str, Any] = {}
    try:
        from app.pending_intent_memory import recover_pending_intent

        recovered = recover_pending_intent(
            novel_id=req.novel_id,
            track=req.track,
            invocation_id=req.invocation_id,
        )
        if isinstance(recovered, dict):
            request_analysis = dict(recovered.get("analysis") or {})
    except Exception:
        request_analysis = {}
    title_resolution = resolve_chapter_archive_title(req.chapter, req.novel_id, request_analysis)
    result = archive_chapter(
        req.chapter,
        req.content,
        title_resolution.get("title") or "",
        req.overwrite,
        track=req.track,
        novel_id=req.novel_id,
        request_analysis=request_analysis,
    )
    result["title_resolution"] = title_resolution
    if result.get("ok"):
        _complete_pending_after_archive(req.novel_id, req.track, req.invocation_id)
        _record_archive_result(
            novel_id=req.novel_id,
            track=req.track,
            invocation_id=req.invocation_id,
            task="prose",
            chapter=req.chapter,
            result=result,
        )
        result["cleanup"] = _cleanup_after_success(req.novel_id, "archive_chapter")
    return result


@app.post("/api/writing/archive-artifact")
def archive_artifact_endpoint(req: ArchiveArtifactRequest) -> dict[str, Any]:
    """显式保存非小说章节项目产物，如 002 的剧本、分镜、角色表。"""
    from app.project_artifacts import save_artifact
    result = save_artifact(req.task, req.content, novel_id=req.novel_id, overwrite=req.overwrite, track=req.track)
    if result.get("ok"):
        _complete_pending_after_archive(req.novel_id, req.track, req.invocation_id)
        _record_archive_result(
            novel_id=req.novel_id,
            track=req.track,
            invocation_id=req.invocation_id,
            task=req.task,
            chapter=None,
            result=result,
        )
        result["cleanup"] = _cleanup_after_success(req.novel_id, "archive_artifact")
    return result


@app.post("/api/writing/archive-outline")
def archive_outline_endpoint(req: ArchiveOutlineRequest) -> dict[str, Any]:
    """显式写回小说大纲的某一章段落；需先确认采纳，再二次确认覆盖。"""
    from app.outline_writeback import archive_outline_chapter

    result = archive_outline_chapter(
        novel_id=req.novel_id,
        chapter=req.chapter,
        content=req.content,
        overwrite=req.overwrite,
        track=req.track,
    )
    if result.get("ok"):
        _complete_pending_after_archive(req.novel_id, req.track, req.invocation_id)
        _record_archive_result(
            novel_id=req.novel_id,
            track=req.track,
            invocation_id=req.invocation_id,
            task="outline",
            chapter=req.chapter,
            result=result,
        )
        result["cleanup"] = _cleanup_after_success(req.novel_id, "archive_outline")
    return result


@app.post("/api/writing/provider-confirm")
def provider_confirm(req: ProviderConfirmRequest) -> dict[str, Any]:
    """用户确认/编辑 provider 材料后，继续融合与审查流程。"""
    return _provider_confirm_impl(req)


@app.post("/api/writing/provider-confirm-stream")
def provider_confirm_stream(req: ProviderConfirmRequest) -> StreamingResponse:
    """用户确认 provider 材料后，用 SSE 透出融合/审查的状态流转。"""
    def events():
        q: queue.Queue[tuple[str, Any]] = queue.Queue()
        done_marker = object()
        stages = stage_preset("provider_confirm")
        done: list[str] = []

        def progress(data: dict[str, Any]) -> None:
            stage = str(data.get("stage") or "")
            status = str(data.get("status") or "running")
            if stage:
                if status == "done" and stage not in done:
                    done.append(stage)
                save_pending_workflow_snapshot(
                    novel_id=req.novel_id,
                    track=req.track,
                    invocation_id=req.invocation_id,
                    stages=stages,
                    current=stage,
                    done=done,
                    status=status,
                    task=req.task,
                    chapter=req.chapter,
                    source="provider_confirm_stream",
                )
            q.put(("progress", data))

        def token(text: str) -> None:
            if text:
                q.put(("token", {"text": text}))

        def run() -> None:
            try:
                result = _provider_confirm_impl(req, progress=progress, token=token)
                q.put(("done", result))
            except Exception as exc:
                q.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
            finally:
                q.put(("_closed", done_marker))

        threading.Thread(target=run, daemon=True).start()
        while True:
            event, data = q.get()
            if data is done_marker:
                break
            yield _web_sse(event, data)

    return StreamingResponse(events(), media_type="text/event-stream")


def _provider_confirm_progress(progress, stage: str, label: str, status: str = "running", **details: Any) -> None:
    if not progress:
        return
    progress({
        "stage": stage,
        "label": label,
        "status": status,
        "at": time.time(),
        "details": {key: value for key, value in details.items() if value not in (None, "", [], {})},
    })


def _confirmed_provider_answers(req: ProviderConfirmRequest) -> list[dict[str, Any]]:
    confirmed: list[dict[str, Any]] = []
    for item in req.answers:
        text = (item.result or "").strip()
        file_parts: list[str] = []
        for file_path in item.files or []:
            file_text = read_provider_answer_file(file_path)
            if file_text:
                file_parts.append(f"### {Path(file_path).name}\n{file_text}")
        if file_parts:
            text = "\n\n".join(filter(None, [
                text,
                "## 用户上传的 provider 回答文件材料",
                "\n\n".join(file_parts),
            ])).strip()
        if text:
            confirmed.append({
                "provider": item.provider,
                "name": item.name or item.provider,
                "status": item.status or "success",
                "result": text,
                "files": item.files,
            })
    return confirmed


def _provider_confirm_impl(req: ProviderConfirmRequest, progress=None, token=None) -> dict[str, Any]:
    """用户确认/编辑 provider 材料后，继续融合与审查流程。"""
    nid = normalize_novel_id(req.novel_id)
    _provider_confirm_progress(progress, "provider_confirm_gate", "确认材料", "running")
    confirmed = _confirmed_provider_answers(req)
    if not confirmed:
        raise HTTPException(status_code=400, detail="请至少确认一个 provider 回答或上传可读取的回答文件")
    _provider_confirm_progress(progress, "provider_confirm_gate", "确认材料", "done", answers=len(confirmed))

    try:
        from app.intervene_policy import policy_view
        from app.project_kinds import project_kind
        from app.writing_graph import GRAPH_RECURSION_LIMIT, get_graph
        from app.writing_invocations import append_event, finish_invocation, invocation_rel_path
        from app.writing_sop import sop_for_task
        from app.writing_memory import thread_id_for
        from langgraph.types import Command

        graph = get_graph()
        thread_id = thread_id_for(req.track, nid)
        graph_cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": GRAPH_RECURSION_LIMIT}
        if req.checkpoint_id:
            graph_cfg["configurable"]["checkpoint_id"] = req.checkpoint_id
        snapshot = graph.get_state(graph_cfg)
        vals = snapshot.values or {}
        kind = vals.get("project_kind") or project_kind(nid)
        bundle = vals.get("bundle") or {
            "task": req.task,
            "chapter": req.chapter,
            "novel_id": nid,
            "project_kind": kind,
            "materials": {},
            "spec": "",
        }
        bundle["novel_id"] = nid
        bundle["project_kind"] = bundle.get("project_kind") or kind
        bundle["model_preferences"] = req.model_preferences
        workflow_sop = vals.get("workflow_sop") or bundle.get("workflow_sop") or sop_for_task(kind, req.task)
        bundle["workflow_sop"] = workflow_sop
        actions = list(vals.get("actions") or [])
        actions.append("provider_confirm(user)")
        invocation_id = req.invocation_id or vals.get("invocation_id") or ""
        append_event(
            nid,
            invocation_id,
            "provider_material_confirmed",
            "用户确认 provider 材料",
            node="provider_confirm_gate",
            status="running",
            details={
                "answers": len(confirmed),
                "checkpoint_id": req.checkpoint_id,
                "sop_stage": workflow_sop.get("stage"),
                "role": workflow_sop.get("role_label"),
                "mode": workflow_sop.get("mode"),
            },
        )

        update = {
            "draft": "",
            "bundle": bundle,
            "project_kind": kind,
            "workflow_sop": workflow_sop,
            "task": req.task,
            "chapter": req.chapter,
            "provider_answers": confirmed,
            "merge_info": {},
            "pre_review": {},
            "model_review": {},
            "awaiting_provider_confirm": False,
            "provider_failed": False,
            "invocation_id": invocation_id,
            "model_preferences": req.model_preferences,
            "iterations": 0,
            "actions": actions,
            "data": {},
        }

        # Resume the real LangGraph path from the human confirmation gate:
        # confirmed provider materials -> generate -> pre_review conditional edge
        # -> model_review conditional edge -> draft_finalize. This preserves
        # review loop behavior instead of manually calling the review nodes here.
        append_event(
            nid,
            invocation_id,
            "graph_resume",
            "从用户确认点恢复到 generate",
            node="generate",
            status="running",
            details={"thread_id": thread_id},
        )
        for event in graph.stream(
            Command(update=update, goto="generate"),
            config=graph_cfg,
            stream_mode=["messages", "updates", "custom"],
        ):
            mode = "updates"
            payload = event
            if isinstance(event, tuple) and len(event) == 2:
                mode, payload = event
            if mode == "messages":
                chunk, meta = payload
                tags = (meta or {}).get("tags") or []
                text = getattr(chunk, "content", "") or ""
                if "prose_merge" in tags and text and token:
                    token(text)
            elif mode == "custom":
                if isinstance(payload, dict) and payload.get("type") == "stage":
                    stage = str(payload.get("stage") or "")
                    status = str(payload.get("status") or "running")
                    label_map = {
                        "provider_consensus": "共识归纳",
                        "provider_digest": "逐篇五维评分",
                        "provider_merge": "融合生成",
                    }
                    details = {k: v for k, v in payload.items() if k not in {"type", "stage", "status"}}
                    append_event(
                        nid,
                        invocation_id,
                        "provider_stage",
                        f"{label_map.get(stage, stage)}{'完成' if status == 'done' else '进行中'}",
                        node=stage,
                        status=status,
                        details=details,
                    )
                    _provider_confirm_progress(progress, stage, label_map.get(stage, stage), status, **details)
            elif mode == "updates":
                for node in (payload or {}).keys():
                    append_event(
                        nid,
                        invocation_id,
                        "graph_node_completed",
                        f"{node} 完成",
                        node=node,
                        status="running",
                    )
                    labels = {
                        "generate": "生成融合稿",
                        "pre_review": "预审查",
                        "model_review": "模型审查",
                        "draft_finalize": "定稿返回",
                    }
                    if node in labels:
                        _provider_confirm_progress(progress, node, labels[node], "done")

        latest = graph.get_state({"configurable": {"thread_id": thread_id}})
        final = latest.values or {}
        data = dict(final.get("data") or {})
        if not data:
            data = {
                "draft": final.get("draft", ""),
                "provider_answers": final.get("provider_answers") or confirmed,
                "merge_info": final.get("merge_info") or {},
                "pre_review": final.get("pre_review") or {},
                "model_review": final.get("model_review") or {},
                "review_strategy": final.get("review_strategy") or {},
                "iterations": final.get("iterations", 0),
                "provider_failed": bool(final.get("provider_failed")),
                "artifacts": {},
                "project_kind": final.get("project_kind") or kind,
            }
        data["policy"] = policy_view(req.track, req.task)
        data["task"] = data.get("task") or req.task
        data["chapter"] = data.get("chapter") or req.chapter
        data["project_kind"] = data.get("project_kind") or kind
        data["invocation_id"] = invocation_id
        data["invocation_log"] = invocation_rel_path(nid, invocation_id) if invocation_id else ""
        data["workflow_sop"] = final.get("workflow_sop") or workflow_sop
        draft = data.get("draft") or final.get("draft", "")
        try:
            from app.final_text_cleaner import clean_final_draft

            draft = clean_final_draft(draft, task=req.task, project_kind=kind)
            data["draft"] = draft
        except Exception:
            pass
        finish_invocation(
            nid,
            invocation_id,
            status="awaiting_confirm" if draft else "failed",
            label="等待用户确认采纳" if draft else "用户确认材料后融合失败",
            details={
                "task": req.task,
                "chapter": req.chapter,
                "actions": final.get("actions") or actions,
                "draft_length": len(draft),
            },
            artifacts={"invocation_log": data.get("invocation_log", "")},
        )
        data["cleanup"] = _cleanup_after_success(nid, "provider_confirm")
        return {
            "ok": True,
            "answer": draft,
            "intent": final.get("intent", "draft"),
            "actions": final.get("actions") or actions,
            "data": data,
        }
    except HTTPException:
        raise
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.post("/api/writing/visual-prompts-stream")
def visual_prompts_stream(req: VisualPromptRequest) -> StreamingResponse:
    """短片确认脚本后：生成每个节拍的场景、人物、三视图和分镜帧生图提示词。"""
    from app.short_film_visual_flow import stream_visual_prompts
    return StreamingResponse(
        stream_visual_prompts(
            novel_id=req.novel_id,
            task=req.task,
            content=req.content,
            source_path=req.source_path,
            overwrite_script=req.overwrite_script,
        ),
        media_type="text/event-stream",
    )


@app.post("/api/writing/storyboard-images-stream")
def storyboard_images_stream(req: VisualPromptRequest) -> StreamingResponse:
    """短片分镜生图流程。支持按单个分镜目录/节拍编号限制执行范围。"""
    from app.short_film_visual_flow import stream_storyboard_images
    return StreamingResponse(
        stream_storyboard_images(
            req.novel_id,
            beat=req.beat,
            storyboard_dir=req.storyboard_dir,
            limit=req.limit,
            image_model_key=req.image_model_key or req.model_preferences.get("image"),
        ),
        media_type="text/event-stream",
    )


@app.get("/api/writing/chapter-status")
def chapter_status_endpoint(novel_id: str = Query(DEFAULT_NOVEL_ID)) -> dict[str, Any]:
    from app.confirm_writeback import chapter_status
    return {"status": chapter_status(novel_id)}


@app.get("/api/writing/stats")
def writing_stats(track: str = Query("create")) -> dict[str, Any]:
    """干预/回环统计：各 task 的 confirm/reject/改写率 + 平均回环次数，供学习优化。"""
    from app.intervene_stats import all_stats
    return all_stats(track)


class FallbackGenerateRequest(BaseModel):
    message: str = Field(default="")
    chapter: int | None = None
    task: str = Field(default="prose")
    track: str = Field(default="create")
    novel_id: str = Field(default=DEFAULT_NOVEL_ID)
    model_preferences: dict[str, str] = Field(default_factory=dict)


@app.post("/api/writing/fallback-generate")
def fallback_generate(req: FallbackGenerateRequest) -> dict[str, Any]:
    """provider 全失败时的兜底：用 API 模型(claude)做材料驱动生成，用户确认后调用。"""
    from app.writing_generate import generate_prose
    from app.writing_tools import assemble_material
    try:
        data = assemble_material(chapter=req.chapter, query=req.message or req.task, task=req.task, novel_id=req.novel_id)
        bundle = data.get("bundle") or {}
        out = generate_prose(bundle, model_key=req.model_preferences.get("writing"))
        return {"ok": out.get("ok", False), "model": out.get("model"),
                "draft": out.get("text", ""), "task": req.task, "chapter": req.chapter}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.get("/api/chat/history/track/{track}")
def chat_history_track(track: str, limit: int = Query(0, ge=0, le=1000),
                       novel_id: str | None = Query(None)) -> dict[str, Any]:
    """按模式（create/normal）加载对话记录，供后续创作流程学习/优化使用。"""
    return load_by_track(track, limit, novel_id)


@app.post("/api/ai-providers/{provider_id}/open")
async def ai_provider_open(provider_id: str) -> dict[str, Any]:
    return await bridge.open_provider(provider_id)


@app.post("/api/ai-providers/{provider_id}/pin")
async def ai_provider_pin(provider_id: str) -> dict[str, Any]:
    return await bridge.pin_current_conversation(provider_id)


@app.post("/api/ai-providers/{provider_id}/reset-conversation")
async def ai_provider_reset_conversation(provider_id: str) -> dict[str, Any]:
    return bridge.reset_conversation(provider_id)


def _ready_chat_model(preferred: str = "gpt") -> str:
    cfg = load_runtime_config()
    return resolve_text_model(cfg, "chat", preferred or None)


@app.post("/api/writing/plain-chat")
def plain_chat(req: PlainChatRequest) -> dict[str, Any]:
    """Pure chat: no LangGraph nodes, no material assembly, no project writeback."""
    try:
        model_key = _ready_chat_model(req.model_key or req.model_preferences.get("chat", ""))
        cfg = load_runtime_config()
        llm = create_llm(cfg, model_key, temperature=0.4, max_tokens=3000, timeout=90, max_retries=1)
        resp = llm.invoke([
            {
                "role": "system",
                "content": "你是一个中文写作项目里的普通聊天助手。直接回答用户问题，不进入创作流程，不修改项目文件。",
            },
            {"role": "human", "content": req.message},
        ])
        text = (getattr(resp, "content", "") or "").strip()
        return {"ok": bool(text), "answer": text, "model": model_key, "novel_id": normalize_novel_id(req.novel_id)}
    except Exception as exc:
        return {"ok": False, "answer": "", "error": f"{type(exc).__name__}: {exc}"}


@app.post("/api/writing/chat", response_model=WritingChatResponse)
def chat(req: WritingChatRequest) -> WritingChatResponse:
    agent = WritingAgent()
    # 创作产出类请求：若用户在消息中明确要求修改/调整该类问题，重置其干预偏好（回到学习）。
    policy_reset = False
    if req.mode in {"draft", "revise"}:
        try:
            from app.intervene_policy import maybe_reset_on_message
            policy_reset = maybe_reset_on_message(req.track, req.task, req.message)
        except Exception:
            pass
    try:
        result = agent.run(
            message=req.message,
            mode=req.mode,
            chapter=req.chapter,
            task=req.task,
            dimension=req.dimension,
            top_k=req.top_k,
            login_confirmed=req.login_confirmed,
            use_provider_source=req.use_provider_source,
            track=req.track,
            novel_id=req.novel_id,
            model_preferences=req.model_preferences,
        )
        data = dict(result.data)
        # 创作产出回答附带干预偏好视图，前端据此决定渲染（正常/建议默认/自动提交）。
        if result.intent in {"draft", "revise"}:
            try:
                from app.intervene_policy import policy_view
                pv = policy_view(req.track, req.task)
                pv["reset"] = policy_reset
                data["policy"] = pv
            except Exception:
                pass
        return WritingChatResponse(
            answer=result.answer,
            intent=result.intent,
            actions=result.actions,
            data=data,
        )
    except WritingToolError as exc:
        return WritingChatResponse(answer=str(exc), intent=req.mode, error=str(exc))


@app.post("/api/writing/build-index", response_model=WritingChatResponse)
def build_index() -> WritingChatResponse:
    return chat(WritingChatRequest(mode="build_index"))


@app.post("/api/writing/draft-stream")
def draft_stream(req: WritingChatRequest) -> StreamingResponse:
    """创作产出（draft/revise）流式：SSE 逐字推送融合/生成 token + 节点进度 + 终态。"""
    from app.writing_stream import stream_draft

    # 用户对该类问题要求修改 → 重置干预偏好（与 /chat 一致）。
    if req.mode in {"draft", "revise"}:
        try:
            from app.intervene_policy import maybe_reset_on_message
            maybe_reset_on_message(req.track, req.task, req.message)
        except Exception:
            pass
    inputs = {
        "user_message": req.message, "mode": req.mode, "chapter": req.chapter,
        "task": req.task, "dimension": req.dimension, "top_k": req.top_k,
        "login_confirmed": req.login_confirmed, "use_provider_source": req.use_provider_source,
        "skip_material_assemble": req.skip_material_assemble,
        "track": req.track, "novel_id": req.novel_id,
        "model_preferences": req.model_preferences,
    }
    if req.message:
        inputs["messages"] = [{"role": "user", "content": req.message}]
    return StreamingResponse(stream_draft(inputs, track=req.track, novel_id=req.novel_id),
                             media_type="text/event-stream")


def safe_project_path(path: str) -> Path:
    clean = path.replace("\\", "/").lstrip("/")
    prefix = "projects/writing/"
    if clean.startswith(prefix):
        clean = clean[len(prefix):]
    rel = Path(clean)
    if rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(status_code=400, detail="非法路径")
    target = (WRITING_ROOT / rel).resolve()
    root = WRITING_ROOT.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="路径不在 writing 项目内")
    return target


def build_file_tree(root: Path, rel: str = "") -> dict[str, Any]:
    current = root / rel if rel else root
    rel_posix = rel.replace("\\", "/")
    children: list[dict[str, Any]] = []
    for item in sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        child_rel = f"{rel_posix}/{item.name}" if rel_posix else item.name
        child_rel = child_rel.replace("\\", "/")
        if item.is_dir():
            if item.name in IGNORED_DIRS or child_rel in IGNORED_REL_DIRS:
                continue
            children.append(build_file_tree(root, child_rel))
        else:
            if item.name in IGNORED_FILES:
                continue
            children.append({
                "type": "file",
                "name": item.name,
                "path": child_rel,
                "previewable": item.suffix.lower() in PREVIEW_SUFFIXES,
                "size": item.stat().st_size,
            })
    return {
        "type": "directory",
        "name": current.name,
        "path": rel_posix,
        "children": children,
    }


def annotate_file_tree_with_structure(tree: dict[str, Any], novel_id: str) -> None:
    """Attach project Wiki roles to file-tree nodes.

    The UI should not infer workflow task from concrete filenames. Project
    structure Wiki is the shared routing contract for canonical and migrated
    project files.
    """
    try:
        from app.project_structure import load_project_structure, task_for_role
    except Exception:
        return
    try:
        project_root = str(novel_dir(novel_id).relative_to(WRITING_ROOT)).replace("\\", "/")
        docs = (load_project_structure(novel_id).get("documents") or {})
    except Exception:
        return

    by_path: dict[str, dict[str, str]] = {}
    for role, spec in docs.items():
        paths = [spec.get("path"), spec.get("canonical_path"), *(spec.get("aliases") or [])]
        for rel in paths:
            rel_text = str(rel or "").replace("\\", "/").strip("/")
            if not rel_text:
                continue
            by_path[rel_text] = {
                "role": role,
                "label": str(spec.get("label") or role),
                "task": task_for_role(role) or "",
            }

    def visit(node: dict[str, Any]) -> None:
        path = str(node.get("path") or "").replace("\\", "/")
        project_rel = path
        prefix = f"{project_root}/"
        if project_rel.startswith(prefix):
            project_rel = project_rel[len(prefix):]
        meta = by_path.get(project_rel)
        if meta and node.get("type") == "file":
            node["structure_role"] = meta["role"]
            node["structure_label"] = meta["label"]
            node["task"] = meta["task"]
        for child in node.get("children") or []:
            if isinstance(child, dict):
                visit(child)

    visit(tree)
