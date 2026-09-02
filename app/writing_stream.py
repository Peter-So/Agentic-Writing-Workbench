from __future__ import annotations

import json
from typing import Any, Iterator

from app.writing_invocations import (
    append_event,
    append_trajectory,
    begin_invocation,
    finish_invocation,
    invocation_rel_path,
)
from app.writing_state_summary import summarize_state_delta
from app.project_kinds import project_kind
from app.writing_sop import sop_for_task
from app.workflow_status import STAGE_LABELS, draft_stages, save_pending_workflow_snapshot
from app.writing_graph import GRAPH_RECURSION_LIMIT, get_graph
from app.writing_memory import thread_id_for

# 正文流式 SSE 生成器（P2/P3 共用）：
# graph.stream(stream_mode=["messages","updates"]) →
#   messages: 仅带 draft_generation 标记的 LLM token → event: token
#   updates : 节点完成 → event: node（进度）
#   结束    : 取最终 state → event: done（draft/审查/policy/兜底标记）
# 只透出"正文产出"那次 LLM 调用的 token；逐篇提要(digest)/审查的 token 不外泄；
_TOKEN_TAG = "draft_generation"


def _next_pending_stage(stages: list[str], done_nodes: list[str], fallback: str = "") -> str:
    done = set(done_nodes)
    for stage in stages:
        if stage not in done:
            return stage
    return fallback or (stages[-1] if stages else "")


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _final_data(state: dict[str, Any], intent: str, task: str, chapter: int | None,
                track: str) -> dict[str, Any]:
    """组织 done 事件的终态数据；附 policy 视图（与 /chat 一致）。"""
    data = dict(state.get("data") or {})
    if not data:
        data = {
            "draft": state.get("draft", ""),
            "pre_review": state.get("pre_review") or {},
            "model_review": state.get("model_review") or {},
            "iterations": state.get("iterations", 0),
            "merge_info": state.get("merge_info") or {},
        }
    if intent in {"draft", "revise"}:
        try:
            from app.intervene_policy import policy_view
            data["policy"] = policy_view(track, task)
        except Exception:
            pass
    invocation_id = state.get("invocation_id") or data.get("invocation_id")
    if invocation_id:
        data["invocation_id"] = invocation_id
        data["invocation_log"] = invocation_rel_path(state.get("novel_id", "001"), invocation_id)
    return {"intent": intent, "task": task, "chapter": chapter, "data": data}


def stream_draft(inputs: dict[str, Any], track: str = "create", novel_id: str = "001") -> Iterator[str]:
    """同步生成器，产出 SSE 文本块。inputs 同 chat（含 mode/task/chapter/...）。"""
    g = get_graph()
    cfg = {"configurable": {"thread_id": thread_id_for(track, novel_id)}}
    cfg.setdefault("recursion_limit", GRAPH_RECURSION_LIMIT)
    intent = inputs.get("mode", "draft")
    task = inputs.get("task", "prose")
    chapter = inputs.get("chapter")
    invocation_id = inputs.get("invocation_id")
    stages = draft_stages()
    workflow_sop = sop_for_task(project_kind(novel_id), task)
    inputs["workflow_sop"] = workflow_sop
    record = begin_invocation(
        novel_id=novel_id,
        track=track,
        mode=intent,
        task=task,
        chapter=chapter,
        user_message=inputs.get("user_message", ""),
        workflow_sop=workflow_sop,
        invocation_id=invocation_id,
    )
    invocation_id = record["id"]
    inputs["invocation_id"] = invocation_id
    save_pending_workflow_snapshot(
        novel_id=novel_id,
        track=track,
        invocation_id=invocation_id,
        stages=stages,
        current="request_analyze",
        done=[],
        task=task,
        chapter=chapter,
        status="running",
        source="backend_stream",
    )
    yield _sse("invocation", {
        "invocation_id": invocation_id,
        "status": "running",
        "log": invocation_rel_path(novel_id, invocation_id),
        "workflow_sop": workflow_sop,
    })
    last_node = None
    done_nodes: list[str] = []
    try:
        for mode, payload in g.stream(inputs, config=cfg, stream_mode=["messages", "updates", "custom"]):
            if mode == "messages":
                chunk, meta = payload
                tags = (meta or {}).get("tags") or []
                text = getattr(chunk, "content", "") or ""
                # 仅透出正文生成 token，审查等模型调用不外泄。
                if _TOKEN_TAG in tags and text:
                    yield _sse("token", {"text": text})
            elif mode == "custom":
                if isinstance(payload, dict) and payload.get("type") == "stage":
                    stage = str(payload.get("stage") or "")
                    stage_status = str(payload.get("status") or "running")
                    if not stage:
                        continue
                    details = {k: v for k, v in payload.items() if k not in {"type", "stage", "status"}}
                    append_event(
                        novel_id,
                        invocation_id,
                        "graph_stage",
                        f"{STAGE_LABELS.get(stage, stage)}{'完成' if stage_status == 'done' else '进行中'}",
                        node=stage,
                        status=stage_status,
                        details=details,
                    )
                    if stage in stages:
                        if stage_status == "done" and stage not in done_nodes:
                            done_nodes.append(stage)
                        stage_index = stages.index(stage)
                        current_stage = stage
                        if stage_status == "done" and stage_index + 1 < len(stages):
                            current_stage = stages[stage_index + 1]
                        save_pending_workflow_snapshot(
                            novel_id=novel_id,
                            track=track,
                            invocation_id=invocation_id,
                            stages=stages,
                            current=current_stage,
                            done=done_nodes,
                            task=task,
                            chapter=chapter,
                            status="running",
                            source="backend_stream_stage",
                        )
                    yield _sse("stage", {"stage": stage, "status": stage_status, **details})
            elif mode == "updates":
                for node, _delta in (payload or {}).items():
                    last_node = node
                    summary = summarize_state_delta(node, _delta)
                    append_trajectory(novel_id, invocation_id, node, summary)
                    request_analysis = None
                    if isinstance(_delta, dict):
                        request_analysis = _delta.get("request_analysis")
                        if node == "request_analyze" and isinstance(request_analysis, dict):
                            pending_intent = _delta.get("pending_intent") if isinstance(_delta, dict) else {}
                            append_event(
                                novel_id,
                                invocation_id,
                                "request_analyzed",
                                "LLM 理解用户请求",
                                node=node,
                                details={
                                    "target_chapter": request_analysis.get("target_chapter"),
                                    "task": request_analysis.get("task"),
                                    "creative_stage": request_analysis.get("creative_stage"),
                                    "creative_stage_label": request_analysis.get("creative_stage_label"),
                                    "flow_complexity": request_analysis.get("flow_complexity"),
                                    "deliverable": request_analysis.get("deliverable"),
                                    "flow_entry": request_analysis.get("flow_entry"),
                                    "node_flow": request_analysis.get("node_flow") or [],
                                    "stage_conflict": request_analysis.get("stage_conflict") or {},
                                    "context_chapters": request_analysis.get("context_chapters") or [],
                                    "affected_files": request_analysis.get("affected_files") or [],
                                    "involved_characters": request_analysis.get("involved_characters") or [],
                                    "plot_points": request_analysis.get("plot_points") or [],
                                    "target_sections": request_analysis.get("target_sections") or [],
                                    "prose_locations": request_analysis.get("prose_locations") or [],
                                    "related_files": (pending_intent or {}).get("related_files") or [],
                                    "pending_intent_id": (pending_intent or {}).get("id", ""),
                                    "request_analysis": request_analysis,
                                    "reason": request_analysis.get("reason") or request_analysis.get("error") or "",
                                },
                            )
                        material_health = None
                        if isinstance(_delta.get("bundle"), dict):
                            material_health = (_delta.get("bundle") or {}).get("material_health")
                        if node == "draft_assemble" and isinstance(material_health, dict) and material_health.get("level") == "warn":
                            append_event(
                                novel_id,
                                invocation_id,
                                "material_health_warning",
                                "材料依赖存在降级项",
                                node=node,
                                status="warn",
                                details={
                                    "warnings": material_health.get("warnings") or [],
                                    "signals": material_health.get("signals") or {},
                                },
                            )
                    append_event(
                        novel_id,
                        invocation_id,
                        "graph_node_completed",
                        f"{node} 完成",
                        node=node,
                        status="running",
                    )
                    if node not in done_nodes:
                        done_nodes.append(node)
                    next_node = _next_pending_stage(stages, done_nodes, fallback=node)
                    save_pending_workflow_snapshot(
                        novel_id=novel_id,
                        track=track,
                        invocation_id=invocation_id,
                        stages=stages,
                        current=next_node,
                        done=done_nodes,
                        task=task,
                        chapter=chapter,
                        status="running",
                        source="backend_stream",
                    )
                    node_payload = {"node": node}
                    if node == "request_analyze" and isinstance(request_analysis, dict):
                        node_payload["request_analysis"] = request_analysis
                    if node == "draft_assemble" and isinstance(_delta, dict) and isinstance((_delta.get("bundle") or {}).get("material_health"), dict):
                        node_payload["material_health"] = (_delta.get("bundle") or {}).get("material_health")
                    yield _sse("node", node_payload)
        # 流结束，取最终 state。
        thread_cfg = {"configurable": {"thread_id": cfg["configurable"]["thread_id"]}}
        snapshot = g.get_state(thread_cfg)
        vals = snapshot.values
        final = _final_data(vals, vals.get("intent", intent),
                            vals.get("task", task), vals.get("chapter", chapter), track)
        configurable = (snapshot.config or {}).get("configurable") or {}
        checkpoint_id = configurable.get("checkpoint_id")
        if checkpoint_id:
            final.setdefault("data", {})["checkpoint_id"] = checkpoint_id
            final["data"]["thread_id"] = configurable.get("thread_id")
        final.setdefault("data", {})["invocation_id"] = invocation_id
        final["data"]["invocation_log"] = invocation_rel_path(novel_id, invocation_id) if invocation_id else ""
        final_data = final.get("data", {})
        has_draft = bool((final_data.get("draft") or final.get("draft") or "").strip())
        status = "awaiting_confirm" if has_draft else "completed"
        label = "等待用户确认采纳" if has_draft else "创作任务完成"
        finish_invocation(
            novel_id,
            invocation_id,
            status=status,
            label=label,
            details={
                "intent": final.get("intent"),
                "task": final.get("task"),
                "chapter": final.get("chapter"),
                "request_analysis": final.get("data", {}).get("request_analysis") or vals.get("request_analysis") or {},
                "checkpoint_id": checkpoint_id,
                "sop_stage": (vals.get("workflow_sop") or workflow_sop).get("stage") if isinstance(vals.get("workflow_sop") or workflow_sop, dict) else "",
            },
            artifacts={
                "invocation_log": final["data"].get("invocation_log", ""),
                "checkpoint_id": checkpoint_id or "",
                "workflow_sop": final["data"].get("workflow_sop") or workflow_sop,
            },
        )
        save_pending_workflow_snapshot(
            novel_id=novel_id,
            track=track,
            invocation_id=invocation_id,
            stages=stages,
            current="user_confirm" if has_draft else "draft_finalize",
            done=done_nodes,
            task=final.get("task") or task,
            chapter=final.get("chapter") or chapter,
            status=status,
            source="backend_stream",
        )
        try:
            from app.writing_cleanup import cleanup_after_task

            final.setdefault("data", {})["cleanup"] = cleanup_after_task(novel_id, task_scope="draft_stream")
        except Exception as exc:
            final.setdefault("data", {})["cleanup"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        yield _sse("done", final)
    except Exception as exc:
        finish_invocation(
            novel_id,
            invocation_id,
            status="failed",
            label="创作任务失败",
            details={"error": f"{type(exc).__name__}: {exc}", "last_node": last_node},
        )
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}", "last_node": last_node})
