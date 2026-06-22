from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any

from langchain_core.prompts import ChatPromptTemplate

from app.ai_providers import PROVIDERS
from app.ai_web_bridge import bridge

if TYPE_CHECKING:
    from app.ai_provider_jobs import ProviderJob


PROVIDER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是创作项目的 AI 协同审稿助手。请严格基于输入任务和项目类型回答，输出结构统一、可合并、可追踪。

输出格式：
1. 核心判断
2. 可采用建议
3. 风险/问题
4. 可直接用于写作任务的素材或修改句

规则：
- 不新增与项目设定冲突的内容。
- 不空泛夸奖。
- 所有建议都要能落到动作、场景、人物声音或结构上。"""),
    ("human", """项目：writing / {novel_id} / {project_kind}
任务模式：{mode}
章节：{chapter}
附件：{attachments}

用户输入：
{message}

请按统一格式输出。"""),
])


@dataclass
class ProviderRunResult:
    provider: str
    name: str
    status: str
    result: str


def provider_status() -> dict[str, Any]:
    pinned = getattr(bridge, "_conversation_urls", {}) or {}
    providers = [{**p, "pinned_conversation": pinned.get(p["id"], "")} for p in PROVIDERS]
    return {"providers": providers}


def build_provider_prompt(message: str, mode: str, chapter: int | None, attachments: list[str],
                          novel_id: str | None = None, project_kind: str | None = None) -> str:
    if not project_kind:
        try:
            from app.project_kinds import project_kind as detect_project_kind
            project_kind = detect_project_kind(novel_id)
        except Exception:
            project_kind = "generic"
    messages = PROVIDER_PROMPT.format_messages(
        novel_id=novel_id or "未指定",
        project_kind=project_kind or "generic",
        message=message,
        mode=mode,
        chapter=chapter or "未指定",
        attachments=", ".join(attachments) if attachments else "无",
    )
    return "\n\n".join(f"[{msg.type.upper()}]\n{msg.content}" for msg in messages)


async def run_provider_fanout(
    message: str,
    mode: str,
    chapter: int | None,
    attachments: list[str],
    login_confirmed: dict[str, bool],
    format_for_writing: bool = False,
    novel_id: str | None = None,
    job: "ProviderJob | None" = None,
    on_complete=None,
) -> dict[str, Any]:
    selected = [p for p in PROVIDERS if login_confirmed.get(p["id"])]
    prompt = build_provider_prompt(message, mode, chapter, attachments, novel_id=novel_id) if format_for_writing else message
    if not selected:
        return {
            "ok": False,
            "status": "provider_required",
            "message": "请至少勾选一个已登录的在线 AI provider。",
            "missing": [p["id"] for p in PROVIDERS],
            "prompt": prompt,
            "format_for_writing": format_for_writing,
            "selected": [],
            "results": [],
        }

    tasks = [run_one_provider(p, prompt, job) for p in selected]
    if on_complete is None:
        results = await asyncio.gather(*tasks)
    else:
        # 边完成边回调：每家完成即 on_complete(result)，供逐家透出进度。
        results = []
        for fut in asyncio.as_completed(tasks):
            item = await fut
            results.append(item)
            try:
                on_complete(item)
            except Exception:
                pass
    return {
        "ok": any(item.get("status") == "success" for item in results),
        "status": "completed",
        "message": "AI provider 协同执行完成。成功和失败明细如下。",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "prompt": prompt,
        "format_for_writing": format_for_writing,
        "selected": [p["id"] for p in selected],
        "results": results,
    }


def start_provider_job(
    message: str,
    mode: str,
    chapter: int | None,
    attachments: list[str],
    login_confirmed: dict[str, bool],
    format_for_writing: bool = False,
    novel_id: str | None = None,
) -> dict[str, Any]:
    """创建后台协同任务并立即返回 job_id；前端轮询 /status 获取实时进度。"""
    from app.ai_provider_jobs import jobs

    selected = [p for p in PROVIDERS if login_confirmed.get(p["id"])]
    if not selected:
        prompt = build_provider_prompt(message, mode, chapter, attachments, novel_id=novel_id) if format_for_writing else message
        return {
            "ok": False,
            "status": "provider_required",
            "message": "请至少勾选一个已登录的在线 AI provider。",
            "missing": [p["id"] for p in PROVIDERS],
            "prompt": prompt,
            "format_for_writing": format_for_writing,
            "selected": [],
            "results": [],
        }
    prompt = build_provider_prompt(message, mode, chapter, attachments, novel_id=novel_id) if format_for_writing else message
    job = jobs.create([p["id"] for p in selected], prompt, format_for_writing)

    async def _runner() -> None:
        try:
            job.result = await run_provider_fanout(
                message, mode, chapter, attachments, login_confirmed, format_for_writing, novel_id=novel_id, job=job
            )
        except Exception as exc:
            job.result = {
                "ok": False,
                "status": "failed",
                "message": f"协同执行异常：{type(exc).__name__}: {exc}",
                "format_for_writing": format_for_writing,
                "selected": [p["id"] for p in selected],
                "results": [],
            }
        finally:
            job.done = True

    asyncio.create_task(_runner())
    return {"ok": True, "job_id": job.job_id, "selected": [p["id"] for p in selected]}


async def run_one_provider(provider: dict[str, Any], prompt: str, job: "ProviderJob | None" = None) -> dict[str, Any]:
    started = perf_counter()
    if job is not None:
        job.mark_running(provider["id"])
    try:
        result = await bridge.run_prompt(provider["id"], prompt)
        result["elapsed_seconds"] = round(perf_counter() - started, 2)
        if job is not None:
            job.mark_done(provider["id"], result.get("status", "completed"),
                          result=result.get("result", ""), name=result.get("name") or provider["name"])
        return result
    except Exception as exc:
        data = ProviderRunResult(
            provider=provider["id"],
            name=provider["name"],
            status="failed",
            result=f"{type(exc).__name__}: {exc}",
        ).__dict__
        data["elapsed_seconds"] = round(perf_counter() - started, 2)
        if job is not None:
            job.mark_done(provider["id"], "failed", result=data["result"], name=provider["name"])
        return data
