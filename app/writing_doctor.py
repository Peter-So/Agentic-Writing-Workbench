from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from app.ai_providers import PROVIDERS
from app.ai_web_bridge import CAPTURE_LOG, PROFILE_ROOT, SESSION_STORE
from app.config import ROOT, RuntimeConfig, load_runtime_config
from app.novel_context import WRITING_ROOT, novel_dir, normalize_novel_id
from app.project_paths import logs_invocations_dir, project_dir, skills_dir, wiki_dir as project_wiki_dir
from app.project_kinds import DEFAULT_KIND, SHORT_FILM_KIND, STRONG_NOVEL_KIND, project_kind
from app.writing_file_policy import path_policy
from app.writing_sop import SOP_ROOT, sop_summary


def run_writing_doctor(novel_id: str | None = None) -> dict[str, Any]:
    """Read-only runtime/provider diagnostics for the writing cockpit."""
    nid = normalize_novel_id(novel_id)
    checks: list[dict[str, Any]] = []
    checks.extend(_runtime_checks())
    checks.extend(_project_checks(nid))
    checks.extend(_cleanup_checks(nid))
    checks.extend(_wiki_checks(nid))
    checks.extend(_memory_checks(nid))
    checks.extend(_governance_eval_checks(nid))
    checks.extend(_collaboration_checks(nid))
    checks.extend(_provider_checks())
    checks = _sort_checks_by_priority(checks)
    counts = {
        "ok": sum(1 for item in checks if item["level"] == "ok"),
        "warn": sum(1 for item in checks if item["level"] == "warn"),
        "error": sum(1 for item in checks if item["level"] == "error"),
    }
    status = "error" if counts["error"] else ("warn" if counts["warn"] else "ok")
    return {
        "ok": status != "error",
        "status": status,
        "novel_id": nid,
        "summary": counts,
        "checks": checks,
        "providers": _provider_matrix(),
    }


def _runtime_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.append(_check(
        "runtime",
        "Playwright Python 包",
        "ok" if importlib.util.find_spec("playwright") else "error",
        "已安装，可使用网页 provider 自动化。" if importlib.util.find_spec("playwright") else "未安装 playwright，provider 自动化不可用。",
        hint="运行 .\\.venv\\Scripts\\python.exe -m pip install playwright 并安装 chromium。",
    ))
    checks.append(_check(
        "runtime",
        "浏览器 profile 根目录",
        "ok" if PROFILE_ROOT.exists() else "warn",
        _rel(PROFILE_ROOT) if PROFILE_ROOT.exists() else f"目录不存在：{_rel(PROFILE_ROOT)}",
        hint="首次打开 provider 后会自动创建；若一直不存在，检查 data 目录权限。",
    ))
    checks.append(_check(
        "runtime",
        "固定会话文件",
        "ok" if SESSION_STORE.exists() else "warn",
        _rel(SESSION_STORE) if SESSION_STORE.exists() else "尚未固定任何 provider 会话。",
        hint="在 Web 中打开 provider 并点击“固定会话”，可减少发到新会话或空白页的概率。",
    ))
    checks.append(_check(
        "runtime",
        "抓取诊断日志",
        "ok" if CAPTURE_LOG.exists() else "warn",
        _rel(CAPTURE_LOG) if CAPTURE_LOG.exists() else "尚未产生 provider 抓取日志。",
        hint="provider 抓空/复制失败时查看 logs/capture_debug.log。",
    ))
    try:
        cfg = load_runtime_config()
        checks.append(_llm_config_check(cfg))
    except Exception as exc:
        checks.append(_check(
            "runtime",
            "LLM 配置",
            "error",
            f"{type(exc).__name__}: {exc}",
            hint="检查 .env.shared / .env 中的 llms 与 image_llms 配置。",
        ))
    return checks


def _llm_config_check(cfg: RuntimeConfig) -> dict[str, Any]:
    text_total = len(cfg.models)
    text_ready = sum(1 for item in cfg.models.values() if item.ready)
    image_total = len(cfg.image_models)
    image_ready = sum(1 for item in cfg.image_models.values() if item.ready)
    role_messages: list[str] = []
    issues: list[str] = []

    for role, label in [("chat", "聊天"), ("writing", "创作"), ("review", "审查")]:
        key = (cfg.model_roles.get(role) or "").strip()
        spec = cfg.models.get(key)
        if spec and spec.ready:
            role_messages.append(f"{label}:{key}/{spec.model}")
        else:
            issues.append(f"{label}模型不可用({key or '未选择'})")

    image_key = (cfg.model_roles.get("image") or "").strip()
    image_spec = cfg.image_models.get(image_key)
    if image_spec and image_spec.ready:
        role_messages.append(f"生图:{image_key}/{image_spec.model}")
    else:
        issues.append(f"生图模型不可用({image_key or '未选择'})")

    if text_ready <= 0:
        issues.append("文本模型无可用配置")
    if image_ready <= 0:
        issues.append("生图模型无可用配置")

    level = "error" if issues else "ok"
    message = (
        f"文本模型 {text_ready}/{text_total} 可用，生图模型 {image_ready}/{image_total} 可用。"
        + (" " + "；".join(role_messages) if role_messages else "")
        + (" 异常：" + "；".join(issues) if issues else "")
    )
    return _check(
        "runtime",
        "LLM 配置",
        level,
        message,
        hint="顶部聊天/创作/审查/生图下拉框只应选择已注册且 API key 完整的模型。",
    )


def _project_checks(novel_id: str) -> list[dict[str, Any]]:
    path = novel_dir(novel_id)
    kind = project_kind(novel_id)
    checks = [
        _check(
            "project",
            "invocation 日志目录",
            "ok" if logs_invocations_dir(novel_id).exists() else "warn",
            _rel(logs_invocations_dir(novel_id)) if logs_invocations_dir(novel_id).exists() else "尚未产生创作任务日志。",
            hint="运行一次创作流后会生成 日志/调用记录/<invocation_id>.json。",
        ),
    ]
    sop = sop_summary(kind)
    checks.append(_check(
        "project",
        "写作 SOP",
        "ok" if sop.get("tasks") else "warn",
        f"{sop.get('label') or kind}，任务数 {len(sop.get('tasks') or {})}",
        hint=f"SOP 定义目录：{_rel(SOP_ROOT)}",
    ))
    checks.extend(_material_assembler_compatibility_checks(novel_id, kind, path, sop))
    checks.extend(_framework_policy_checks(path))
    return checks


def _material_assembler_compatibility_checks(
    novel_id: str,
    kind: str,
    project_path: Path,
    sop: dict[str, Any],
) -> list[dict[str, Any]]:
    expected = _expected_material_tasks(kind, sop)
    script = _material_assembler_path(project_path)
    if not script:
        return [_check(
            "project",
            "材料组装任务兼容",
            "ok",
            f"未配置项目专属组装器，{len(expected)} 个常见任务统一走通用材料组装器。",
            hint="通用组装器会按项目结构 Wiki/SOP 降级组装材料，不因缺专属脚本卡住流程。",
        )]

    try:
        from app.writing_tools import supported_tasks_for_assembler

        supported = set(supported_tasks_for_assembler(script))
    except Exception as exc:
        return [_check(
            "project",
            "材料组装任务兼容",
            "warn",
            f"读取专属组装器失败：{type(exc).__name__}: {exc}",
            hint="运行时仍会捕获不支持任务并降级到通用材料组装器；建议检查脚本 choices 声明。",
        )]

    if not supported:
        return [_check(
            "project",
            "材料组装任务兼容",
            "warn",
            f"专属组装器未声明 choices：{_rel(script)}",
            hint="建议在 argparse choices 中显式列出支持任务；否则 Doctor 无法提前判断兼容矩阵。",
        )]

    missing = sorted(expected - supported)
    covered = sorted(expected & supported)
    extra = sorted(supported - expected)
    message = (
        f"专属组装器支持 {len(supported)} 个任务；当前类型常见任务覆盖 "
        f"{len(covered)}/{len(expected)}。"
    )
    if missing:
        message += f" 缺失：{', '.join(missing)}。"
    if extra:
        message += f" 额外：{', '.join(extra[:8])}。"
    return [_check(
        "project",
        "材料组装任务兼容",
        "warn" if missing else "ok",
        message,
        hint=(
            "缺失任务运行时会自动降级到通用材料组装器，不再阻断 LangGraph；"
            "若某任务需要项目专属精细材料，可补齐专属脚本支持。"
        ),
    )]


def _material_assembler_path(project_path: Path) -> Path | None:
    for rel in ("脚本/material_assembler.py", "scripts/material_assembler.py"):
        path = project_path / rel
        if path.is_file():
            return path
    return None


def _expected_material_tasks(kind: str, sop: dict[str, Any]) -> set[str]:
    tasks = set((sop.get("tasks") or {}).keys())
    if kind == STRONG_NOVEL_KIND:
        tasks.update({"logline", "setting", "world", "outline", "character", "beat_sheet", "prose", "expansion", "fix"})
    elif kind == SHORT_FILM_KIND:
        tasks.update({"logline", "character", "beat_sheet", "screenplay", "shot_list", "storyboard", "visual_prompt", "image", "fix", "prose"})
    elif kind == DEFAULT_KIND:
        tasks.update({"outline", "materials", "draft", "fix", "generic", "prose", "references"})
    return {str(task) for task in tasks if str(task or "").strip()}


def _framework_policy_checks(project_path: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    framework_lines: list[dict[str, Any]] = []
    protected_targets = [
        WRITING_ROOT / "README.md",
        SOP_ROOT,
        project_dir(project_path.name, "wiki", "README.md"),
        project_dir(project_path.name, "wiki", "project-structure.json"),
        project_dir(project_path.name, "wiki", "项目结构.md"),
        project_dir(project_path.name, "wiki", "project_wiki.json"),
        project_dir(project_path.name, "wiki", "index.json"),
        project_dir(project_path.name, "wiki", "project-structure-map.md"),
    ]
    for target in protected_targets:
        try:
            policy = path_policy(target)
            framework_lines.append({
                "path": _rel(target),
                "level": "ok" if policy.get("protected") else "error",
                "message": policy.get("reason") or "未识别为受保护框架路径。",
                "hint": "框架说明、SOP、运行时目录不应通过 Web 文件编辑器保存。",
            })
        except Exception as exc:
            framework_lines.append({
                "path": _rel(target),
                "level": "warn",
                "message": f"策略检查失败：{type(exc).__name__}: {exc}",
                "hint": "",
            })
    framework_level = "error" if any(item["level"] == "error" for item in framework_lines) else (
        "warn" if any(item["level"] == "warn" for item in framework_lines) else "ok"
    )
    framework_check = _check(
        "project",
        "框架保护",
        framework_level,
        f"{len(framework_lines)} 条保护策略"
        + ("正常。" if framework_level == "ok" else "需检查。"),
        hint="框架说明、SOP、Wiki 结构与运行时目录不应通过 Web 文件编辑器保存。",
    )
    framework_check["framework_lines"] = framework_lines
    checks.append(framework_check)
    try:
        sample = skills_dir(project_path.name) / "style.md"
        policy = path_policy(sample)
        checks.append(_check(
            "project",
            "作品空间可编辑",
            "ok" if policy.get("editable") else "warn",
            "novels/<id>/... 可作为作品内容和项目技能空间编辑。" if policy.get("editable") else policy.get("reason", ""),
            hint="框架保护只应限制项目根框架，不应阻断具体作品目录。",
        ))
    except Exception as exc:
        checks.append(_check(
            "project",
            "作品空间可编辑",
            "warn",
            f"策略检查失败：{type(exc).__name__}: {exc}",
        ))
    return checks


_CHECK_PRIORITY: dict[str, int] = {
    "LLM 配置": 10,
    "框架保护": 30,
    "作品空间可编辑": 40,
    "写作 SOP": 50,
    "材料组装任务兼容": 60,
    "临时缓存清理": 65,
    "Wiki 基础结构": 70,
    "项目 Wiki": 80,
    "LLM Wiki": 90,
    "短期对话记忆": 100,
    "长期设定 Store": 110,
    "跨章节摘要": 120,
    "TF-IDF 资料索引": 130,
    "本地 RAG 产出语料": 140,
    "记忆治理": 150,
    "信息边界路由": 160,
    "最近任务 trajectory": 170,
    "Prompt harness": 180,
    "Token 预算观测": 190,
    "创作回放 benchmark": 200,
    "invocation 日志目录": 220,
    "Playwright Python 包": 230,
    "浏览器 profile 根目录": 240,
    "固定会话文件": 250,
    "抓取诊断日志": 260,
}

_AREA_PRIORITY: dict[str, int] = {
    "project": 300,
    "wiki": 400,
    "memory": 500,
    "collaboration": 600,
    "runtime": 700,
    "provider": 800,
}

_LEVEL_PRIORITY = {"error": 0, "warn": 1, "ok": 2}


def _sort_checks_by_priority(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Put blocking creative-workflow health signals before auxiliary diagnostics."""
    indexed = list(enumerate(checks))

    def key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        idx, check = item
        name = str(check.get("name") or "")
        area = str(check.get("area") or "")
        priority = _CHECK_PRIORITY.get(name)
        if priority is None:
            priority = _AREA_PRIORITY.get(area, 900)
            if area == "provider":
                priority += 10
        level = _LEVEL_PRIORITY.get(str(check.get("level") or "warn"), 1)
        return (priority, level, idx)

    return [check for _, check in sorted(indexed, key=key)]


def _cleanup_checks(novel_id: str) -> list[dict[str, Any]]:
    try:
        from app.writing_cleanup import cleanup_health

        health = cleanup_health(novel_id)
        preview = health.get("preview") or {}
        sample = preview.get("sample") or []
        hint = "自动清理只处理过期 .tmp、生图 API 调试响应、已完成生图队列和过旧 _superseded 备份；记忆、恢复、Wiki、RAG、invocation 日志和正式输出不会被删除。"
        if sample:
            hint += " 示例：" + "；".join(str(item.get("path") or "") for item in sample[:3])
        return [_check(
            "project",
            "临时缓存清理",
            str(health.get("level") or "warn"),
            str(health.get("message") or ""),
            hint=hint,
        )]
    except Exception as exc:
        return [_check(
            "project",
            "临时缓存清理",
            "warn",
            f"检查失败：{type(exc).__name__}: {exc}",
            hint="清理诊断失败不会阻断创作流程，但建议检查 app/writing_cleanup.py。",
        )]


def _wiki_checks(novel_id: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    path = novel_dir(novel_id)
    wiki_dir = project_wiki_dir(novel_id)
    if not wiki_dir.exists():
        return [_check(
            "wiki",
            "Wiki 基础结构",
            "error",
            "缺失项目 wiki 目录。",
            hint="项目创建或状态刷新应生成 维基/，其中包含结构索引、项目动态 Wiki 和 LLM Wiki。",
        )]

    structure_path = wiki_dir / "project-structure.json"
    structure_data, structure_error = _read_json_dict(structure_path)
    structure_md = wiki_dir / "项目结构.md"
    basic_level = "ok"
    basic_parts = [f"目录：{_rel(wiki_dir)}"]
    basic_hint = "基础结构只检查机器路由源和人类可读结构文档；分工说明和保护策略已并入框架保护。"
    if structure_error:
        basic_level = "error"
        basic_parts.append(f"结构索引异常：{structure_error}")
    else:
        documents = structure_data.get("documents") or {}
        directories = structure_data.get("directories") or {}
        missing_docs = []
        for spec in documents.values():
            rel = spec.get("path")
            if rel and not (path / rel).exists():
                missing_docs.append(rel)
        if not documents:
            basic_level = "error"
        elif missing_docs and basic_level != "error":
            basic_level = "warn"
        basic_parts.append(f"结构文件 {len(documents)} 个，结构目录 {len(directories)} 个")
        if missing_docs:
            basic_parts.append(f"缺失：{', '.join(missing_docs[:5])}")
    if not structure_md.exists() or structure_md.stat().st_size <= 20:
        if basic_level != "error":
            basic_level = "warn"
        basic_parts.append("可读结构文档缺失或过短")
    checks.append(_check(
        "wiki",
        "Wiki 基础结构",
        basic_level,
        "；".join(basic_parts) + "。",
        hint=basic_hint,
    ))

    project_wiki_path = wiki_dir / "project_wiki.json"
    project_wiki_data, project_wiki_error = _read_json_dict(project_wiki_path)
    if project_wiki_error:
        checks.append(_check(
            "wiki",
            "项目 Wiki",
            "error",
            project_wiki_error,
            hint="项目过程知识索引损坏会导致 project_wiki_recall 失效。",
        ))
    else:
        missing_entries = []
        for item in project_wiki_data.values():
            rel = item.get("path")
            if rel and not (ROOT / rel).exists():
                missing_entries.append(rel)
        checks.append(_check(
            "wiki",
            "项目 Wiki",
            "warn" if missing_entries else "ok",
            f"条目 {len(project_wiki_data)} 条。" + (f" 缺失条目文件：{', '.join(missing_entries[:5])}" if missing_entries else ""),
            hint="项目 Wiki 负责项目状态、过程备注、待办、材料索引和项目内决定。",
        ))

    llm_index_path = wiki_dir / "index.json"
    llm_data, llm_error = _read_json_dict(llm_index_path)
    if llm_error:
        checks.append(_check(
            "wiki",
            "LLM Wiki",
            "error",
            llm_error,
            hint="LLM Wiki 索引损坏会导致稳定共识无法召回进入 prompt。",
        ))
    else:
        missing_llm = []
        for item in llm_data.values():
            rel = item.get("path")
            if rel and not (ROOT / rel).exists():
                missing_llm.append(rel)
        checks.append(_check(
            "wiki",
            "LLM Wiki",
            "warn" if missing_llm else "ok",
            f"条目 {len(llm_data)} 条。" + (f" 缺失条目文件：{', '.join(missing_llm[:5])}" if missing_llm else ""),
            hint="LLM Wiki 负责稳定规则、设定、共识和经验，权威高于普通材料。",
        ))
    return checks


def _memory_checks(novel_id: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    try:
        from app.writing_memory import MEMORY_DB_PATH

        memory_db = Path(MEMORY_DB_PATH)
        store_db = Path(MEMORY_DB_PATH.replace(".db", "_store.db"))
        checks.append(_check(
            "memory",
            "短期对话记忆",
            "ok" if memory_db.exists() else "warn",
            _rel(memory_db) if memory_db.exists() else "尚未创建 LangGraph checkpoint SQLite。",
            hint="运行一次创作流后会按 project:track 写入 checkpoint。",
        ))
        checks.append(_check(
            "memory",
            "长期设定 Store",
            "ok" if store_db.exists() else "warn",
            _rel(store_db) if store_db.exists() else "尚未创建长期设定 Store。",
            hint="人物/大纲确认后会按 novel_id + track 固化为长期设定。",
        ))
    except Exception as exc:
        checks.append(_check("memory", "SQLite 记忆库", "warn", f"检查失败：{type(exc).__name__}: {exc}"))

    try:
        from app.chapter_summary import summary_file

        path = summary_file(novel_id)
        checks.append(_check(
            "memory",
            "跨章节摘要",
            "ok" if path.exists() else "warn",
            _rel(path) if path.exists() else "尚未生成已完成章节摘要。",
            hint="正文确认或章节文件保存后会生成结构化摘要，下一章材料组装时召回。",
        ))
    except Exception as exc:
        checks.append(_check("memory", "跨章节摘要", "warn", f"检查失败：{type(exc).__name__}: {exc}"))

    try:
        from app.output_index import FALLBACK_CORPUS_DIR
        from app.writing_tools import NOVEL_ACQ_DIR

        semantic_index = NOVEL_ACQ_DIR / "cache" / "tfidf_index.pkl"
        fallback = FALLBACK_CORPUS_DIR / "confirmed_outputs.json"
        checks.append(_check(
            "memory",
            "TF-IDF 资料索引",
            "ok" if semantic_index.exists() else "warn",
            _rel(semantic_index) if semantic_index.exists() else "语义索引尚未构建。",
            hint="可通过 build-index 重建；无向量 sidecar 时作为参考资料检索兜底。",
        ))
        checks.append(_check(
            "memory",
            "本地 RAG 产出语料",
            "ok" if fallback.exists() else "warn",
            _rel(fallback) if fallback.exists() else "尚未产生 confirmed_outputs.json。",
            hint="用户确认产出会写入；无 sidecar 时 query_outputs 会直接查询该语料。",
        ))
    except Exception as exc:
        checks.append(_check("memory", "RAG 产出语料", "warn", f"检查失败：{type(exc).__name__}: {exc}"))
    return checks


def _governance_eval_checks(novel_id: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    try:
        from app.writing_memory_governance import memory_governance_report

        report = memory_governance_report(novel_id, limit=50)
        summary = report.get("summary") or {}
        conflicts = int(summary.get("conflicts") or 0)
        candidates = int(summary.get("promotion_candidates") or 0)
        checks.append(_check(
            "memory",
            "记忆治理",
            "warn" if conflicts else "ok",
            f"冲突 {conflicts} 条，晋级候选 {candidates} 条。",
            hint="冲突需人工裁决；晋级候选可采纳为 lesson 或项目技能卡。",
        ))
    except Exception as exc:
        checks.append(_check("memory", "记忆治理", "warn", f"检查失败：{type(exc).__name__}: {exc}"))
    try:
        from app.writing_benchmark import run_writing_benchmark

        bench = run_writing_benchmark(novel_id, limit=10)
        summary = bench.get("summary") or {}
        failed = int(summary.get("failed") or 0)
        checks.append(_check(
            "collaboration",
            "创作回放 benchmark",
            "warn" if failed else "ok",
            f"最近 {summary.get('records', 0)} 个任务：passed {summary.get('passed', 0)} / failed {failed}。",
            hint="用于修改 harness/SOP/skills 后做横向回放对比，不调用模型。",
        ))
    except Exception as exc:
        checks.append(_check("collaboration", "创作回放 benchmark", "warn", f"检查失败：{type(exc).__name__}: {exc}"))
    return checks


def _collaboration_checks(novel_id: str) -> list[dict[str, Any]]:
    try:
        from app.writing_invocations import list_recent_invocations

        recent = list_recent_invocations(novel_id, limit=5)
    except Exception as exc:
        return [_check(
            "collaboration",
            "协作轨迹读取",
            "warn",
            f"读取最近 invocation 失败：{type(exc).__name__}: {exc}",
            hint="检查 日志/调用记录 是否存在损坏 JSON。",
        )]
    if not recent:
        return [_check(
            "collaboration",
            "协作轨迹",
            "warn",
            "尚未产生可复盘的 trajectory。",
            hint="运行一次创作流后会写入 trajectory / harness / budgets。",
        )]

    latest = recent[0]
    checks: list[dict[str, Any]] = []
    trajectory_count = len(latest.get("trajectory") or [])
    checks.append(_check(
        "collaboration",
        "最近任务 trajectory",
        "ok" if trajectory_count else "warn",
        f"{latest.get('id')}：节点摘要 {trajectory_count} 条，状态 {latest.get('status')}",
        hint="trajectory 只存节点摘要、长度和 hash，不保存大段正文。",
    ))

    harness_items = [item for record in recent for item in (record.get("harness") or [])]
    harness_errors = [item for item in harness_items if item.get("level") == "error"]
    harness_warns = [item for item in harness_items if item.get("level") == "warn"]
    checks.append(_check(
        "collaboration",
        "Prompt harness",
        "error" if harness_errors else ("warn" if harness_warns else "ok"),
        f"最近 {len(recent)} 次任务：error {len(harness_errors)} / warn {len(harness_warns)}。",
        hint="若出现 error，provider 提问包会被本地阻断，避免错误 prompt 进入网页 AI。",
    ))

    budgets = [item for record in recent for item in (record.get("budgets") or [])]
    budget_errors = [item for item in budgets if item.get("level") == "error"]
    budget_warns = [item for item in budgets if item.get("level") == "warn"]
    if budgets:
        latest_budget = budgets[-1]
        msg = (
            f"最近估算 total={latest_budget.get('estimated_total_tokens', 0)} tokens，"
            f"prompt={latest_budget.get('prompt_tokens_est', 0)}，"
            f"providers={latest_budget.get('provider_count', 0)}。"
        )
    else:
        msg = "最近任务尚无预算记录。"
    checks.append(_check(
        "collaboration",
        "Token 预算观测",
        "error" if budget_errors else ("warn" if budget_warns or not budgets else "ok"),
        msg,
        hint="预算为近似估算，用于发现 prompt 过大或 provider fanout 过宽，不等同于账单。",
    ))
    try:
        from app.writing_invocations import cost_board

        board = cost_board(novel_id, limit=20)
        summary = board.get("summary") or {}
        checks.append(_check(
            "collaboration",
            "信息边界路由",
            "ok",
            (
                f"最近 {summary.get('invocations', 0)} 次："
                f"fanout {summary.get('fanout_routes', 0)} / "
                f"单 Agent {summary.get('single_agent_routes', 0)}；"
                f"provider 调用 {summary.get('provider_runs', 0)} 次。"
            ),
            hint="fanout 应主要出现在材料征集/关键分歧；修复和串行转化默认走单 Agent 快速路径。",
        ))
    except Exception:
        pass
    return checks


def _provider_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    pinned = _load_pinned()
    for provider in PROVIDERS:
        pid = provider["id"]
        profile = PROFILE_ROOT / pid
        has_profile = profile.exists() and any(profile.iterdir()) if profile.exists() else False
        has_pinned = bool(pinned.get(pid))
        if has_profile and has_pinned:
            level = "ok"
            message = "profile 与固定会话均存在。"
            hint = ""
        elif has_profile:
            level = "warn"
            message = "已有浏览器 profile，但未固定会话。"
            hint = "建议打开该 provider 并固定当前会话，避免发送到新会话首页。"
        else:
            level = "warn"
            message = "尚未发现有效浏览器 profile。"
            hint = "点击 provider 的“打开”，完成登录后再回来运行 Doctor。"
        checks.append(_check("provider", provider["name"], level, message, hint=hint, provider=pid))
    return checks


def _provider_matrix() -> list[dict[str, Any]]:
    pinned = _load_pinned()
    rows = []
    for provider in PROVIDERS:
        pid = provider["id"]
        profile = PROFILE_ROOT / pid
        rows.append({
            "id": pid,
            "name": provider["name"],
            "url": provider["url"],
            "automation": provider.get("automation", ""),
            "embed_status": provider.get("embed_status", ""),
            "profile": _rel(profile),
            "profile_exists": profile.exists(),
            "profile_nonempty": bool(profile.exists() and any(profile.iterdir())),
            "pinned_conversation": pinned.get(pid, ""),
        })
    return rows


def _load_pinned() -> dict[str, str]:
    try:
        import json

        data = json.loads(SESSION_STORE.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, str) and v}
    except Exception:
        return {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _read_json_dict(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, f"缺失：{_rel(path)}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"JSON 解析失败：{_rel(path)}；{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return {}, f"JSON 顶层不是对象：{_rel(path)}"
    return data, ""


def _check(
    area: str,
    name: str,
    level: str,
    message: str,
    *,
    hint: str = "",
    provider: str = "",
) -> dict[str, Any]:
    return {
        "area": area,
        "name": name,
        "level": level,
        "message": message,
        "hint": hint,
        "provider": provider,
    }


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
