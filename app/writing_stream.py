from __future__ import annotations

import json
from typing import Any, Iterator

from app.writing_invocations import (
    append_budget,
    append_event,
    append_harness,
    append_route,
    append_trajectory,
    begin_invocation,
    finish_invocation,
    invocation_rel_path,
    update_provider,
)
from app.writing_harness import summarize_state_delta
from app.project_kinds import project_kind
from app.writing_sop import sop_for_task
from app.workflow_status import draft_stages, save_pending_workflow_snapshot
from app.writing_graph import GRAPH_RECURSION_LIMIT, get_graph
from app.writing_memory import thread_id_for

# 正文流式 SSE 生成器（P2/P3 共用）：
# graph.stream(stream_mode=["messages","updates"]) →
#   messages: 仅带 prose_merge 标记的 LLM token → event: token（融合正文/兜底正文生成）
#   updates : 节点完成 → event: node（进度）
#   结束    : 取最终 state → event: done（draft/审查/policy/兜底标记）
# 只透出"正文产出"那次 LLM 调用的 token；逐篇提要(digest)/审查的 token 不外泄；
# provider 抓取在 provider_fanout 节点，只发 node 进度，绝不逐字（沿用复制按钮一次性取整篇）。
_TOKEN_TAG = "prose_merge"


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
            "provider_failed": bool(state.get("provider_failed")),
            "merge_info": state.get("merge_info") or {},
            "request_file": state.get("request_file", ""),
            "provider_route": state.get("provider_route") or {},
            "token_budget": state.get("token_budget") or {},
            "provider_answers": state.get("provider_answers") or [],
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
    selected_provider = any(bool(value) for value in (inputs.get("login_confirmed") or {}).values())
    stages = draft_stages(
        use_provider_source=bool(inputs.get("use_provider_source")),
        selected_provider=selected_provider,
        followup=bool(inputs.get("skip_material_assemble")),
    )
    workflow_sop = sop_for_task(project_kind(novel_id), task)
    inputs["workflow_sop"] = workflow_sop
    record = begin_invocation(
        novel_id=novel_id,
        track=track,
        mode=intent,
        task=task,
        chapter=chapter,
        user_message=inputs.get("user_message", ""),
        use_provider_source=bool(inputs.get("use_provider_source")),
        login_confirmed=inputs.get("login_confirmed") or {},
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
                # 仅透出带 prose_merge 标记的正文 token（提要/审查的 LLM token 被挡）。
                if _TOKEN_TAG in tags and text:
                    yield _sse("token", {"text": text})
            elif mode == "custom":
                # provider_fanout 节点逐家透出的进度（init + 每家完成）→ SSE event: provider。
                if isinstance(payload, dict) and payload.get("type") in {"provider_init", "provider"}:
                    if payload.get("type") == "provider_init":
                        append_event(
                            novel_id,
                            invocation_id,
                            "provider_fanout_started",
                            "开始发送 provider 提问",
                            node="provider_fanout",
                            details={
                                "order": payload.get("order") or [],
                                "sop_stage": workflow_sop.get("stage"),
                                "role": workflow_sop.get("role_label"),
                                "mode": workflow_sop.get("mode"),
                            },
                        )
                        for provider in payload.get("order") or []:
                            update_provider(novel_id, invocation_id, provider, status="queued")
                    else:
                        update_provider(
                            novel_id,
                            invocation_id,
                            payload.get("provider"),
                            name=payload.get("name"),
                            status=payload.get("status", "completed"),
                            result=payload.get("result", ""),
                            elapsed_seconds=payload.get("elapsed") or payload.get("elapsed_seconds"),
                        )
                        append_event(
                            novel_id,
                            invocation_id,
                            "provider_result",
                            f"{payload.get('name') or payload.get('provider')} 返回：{payload.get('status')}",
                            node="provider_fanout",
                            provider=payload.get("provider"),
                            details={"status": payload.get("status"), "elapsed": payload.get("elapsed")},
                        )
                    yield _sse("provider", payload)
            elif mode == "updates":
                for node, _delta in (payload or {}).items():
                    last_node = node
                    summary = summarize_state_delta(node, _delta)
                    append_trajectory(novel_id, invocation_id, node, summary)
                    request_analysis = None
                    if isinstance(_delta, dict):
                        harness = _delta.get("request_harness") or ((_delta.get("bundle") or {}).get("request_harness") if isinstance(_delta.get("bundle"), dict) else None)
                        budget = _delta.get("token_budget") or ((_delta.get("bundle") or {}).get("token_budget") if isinstance(_delta.get("bundle"), dict) else None)
                        route = _delta.get("provider_route")
                        request_analysis = _delta.get("request_analysis")
                        if isinstance(harness, dict) and harness:
                            append_harness(novel_id, invocation_id, node, harness)
                        if isinstance(budget, dict) and budget:
                            append_budget(novel_id, invocation_id, node, budget)
                        if isinstance(route, dict) and route:
                            append_route(novel_id, invocation_id, node, route)
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
                                    "deliverable": request_analysis.get("deliverable"),
                                    "flow_entry": request_analysis.get("flow_entry"),
                                    "context_chapters": request_analysis.get("context_chapters") or [],
                                    "affected_files": request_analysis.get("affected_files") or [],
                                    "involved_characters": request_analysis.get("involved_characters") or [],
                                    "plot_points": request_analysis.get("plot_points") or [],
                                    "target_sections": request_analysis.get("target_sections") or [],
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
                        status="awaiting_confirm" if node == "provider_confirm_gate" else "running",
                    )
                    if node not in done_nodes:
                        done_nodes.append(node)
                    next_node = "provider_confirm_gate" if node == "provider_confirm_gate" else node
                    save_pending_workflow_snapshot(
                        novel_id=novel_id,
                        track=track,
                        invocation_id=invocation_id,
                        stages=stages,
                        current=next_node,
                        done=done_nodes,
                        task=task,
                        chapter=chapter,
                        status="awaiting_confirm" if node == "provider_confirm_gate" else "running",
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
        status = "awaiting_confirm" if final_data.get("awaiting_provider_confirm") or has_draft else "completed"
        label = "等待用户确认 provider 材料" if final_data.get("awaiting_provider_confirm") else (
            "等待用户确认采纳" if has_draft else "创作任务完成"
        )
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
                "request_file": final["data"].get("request_file", ""),
                "checkpoint_id": checkpoint_id or "",
                "provider_answers": ((final["data"].get("artifacts") or {}).get("provider_answers") or {}),
                "workflow_sop": final["data"].get("workflow_sop") or workflow_sop,
                "provider_route": final["data"].get("provider_route") or {},
                "token_budget": final["data"].get("token_budget") or {},
            },
        )
        save_pending_workflow_snapshot(
            novel_id=novel_id,
            track=track,
            invocation_id=invocation_id,
            stages=stages,
            current="provider_confirm_gate" if final_data.get("awaiting_provider_confirm") else ("user_confirm" if has_draft else "draft_finalize"),
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
