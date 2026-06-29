from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import ast
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import WRITING_ROOT, novel_dir, normalize_novel_id
from app.project_kinds import assemble_generic_bundle, ensure_project_initialized, project_kind
from app.project_paths import assets_dir, outputs_dir, project_dir, skills_dir, storyboards_dir


NOVEL_ACQ_DIR = WRITING_ROOT / "novel-acquisition"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


class WritingToolError(RuntimeError):
    pass


def _python() -> str:
    return str(PYTHON if PYTHON.exists() else sys.executable)


def _env(novel_id: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["WRITING_ROOT"] = str(WRITING_ROOT)
    env["PYTHONPATH"] = str(NOVEL_ACQ_DIR)
    env["WRITING_NOVEL_DIR"] = str(novel_dir(novel_id))
    return env


def _run(args: list[str], cwd: Path | None = None, timeout: int = 120,
         novel_id: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd or WRITING_ROOT),
        env=_env(novel_id),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise WritingToolError(detail or f"command failed: {' '.join(args)}")
    return result


def _json_from_stdout(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise WritingToolError(f"无法解析工具 JSON 输出: {exc}\n{text[:500]}") from exc


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def build_semantic_index(novel_id: str | None = None) -> dict[str, Any]:
    script = NOVEL_ACQ_DIR / "semantic_search.py"
    result = _run([_python(), str(script), "--build"], cwd=NOVEL_ACQ_DIR, timeout=180, novel_id=novel_id)
    index_path = NOVEL_ACQ_DIR / "cache" / "tfidf_index.pkl"
    return {
        "ok": True,
        "index_path": str(index_path.relative_to(ROOT)).replace("\\", "/"),
        "index_exists": index_path.exists(),
        "size": index_path.stat().st_size if index_path.exists() else 0,
        "log": result.stdout.strip(),
    }


def search_references(query: str, dimension: str | None = None, top_k: int = 8,
                      novel_id: str | None = None) -> dict[str, Any]:
    if not query.strip():
        raise WritingToolError("检索词不能为空")

    semantic_index = NOVEL_ACQ_DIR / "cache" / "tfidf_index.pkl"
    if semantic_index.exists():
        script = NOVEL_ACQ_DIR / "semantic_search.py"
        args = [_python(), str(script), query, "--top", str(top_k), "--json"]
        if dimension:
            args += ["--dim", dimension]
        result = _run(args, cwd=NOVEL_ACQ_DIR, timeout=60, novel_id=novel_id)
        return {
            "engine": "semantic_tfidf",
            "query": query,
            "results": _json_from_stdout(result.stdout) or [],
            "index_ready": True,
        }

    script = NOVEL_ACQ_DIR / "five_dim_search.py"
    args = [_python(), str(script), query, "--top", str(top_k), "--json"]
    if dimension:
        args += ["--dim", dimension]
    result = _run(args, cwd=NOVEL_ACQ_DIR, timeout=60, novel_id=novel_id)
    return {
        "engine": "five_dim_exact",
        "query": query,
        "results": _json_from_stdout(result.stdout) or [],
        "index_ready": False,
        "notice": "语义索引未构建，已使用五维精确检索。可先执行 build-index 获得更好召回。",
    }


# 物料组装短期缓存：键=(task,chapter,query)，审查回环/同章重试命中即跳过子进程。
_ASSEMBLE_CACHE: dict[str, dict[str, Any]] = {}
_ASSEMBLE_CACHE_MAX = 32
_ASSEMBLE_LOCK = threading.Lock()


def _assemble_cache_key(chapter: int | None, query: str, task: str) -> str:
    import hashlib
    raw = f"{task}|{chapter}|{(query or '').strip()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def assemble_material(chapter: int | None, query: str, task: str = "prose",
                      use_cache: bool = True, novel_id: str | None = None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    novel_path = novel_dir(nid)
    key = f"{nid}:{_assemble_cache_key(chapter, query, task)}"
    if use_cache:
        with _ASSEMBLE_LOCK:
            if key in _ASSEMBLE_CACHE:
                cached = dict(_ASSEMBLE_CACHE[key])
                cached["cached"] = True
                return cached
    output_dir = outputs_dir(nid)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = output_dir / f"material_bundle_{task}_{stamp}.json"
    script = novel_path / "脚本" / "material_assembler.py"
    if not script.exists():
        script = novel_path / "scripts" / "material_assembler.py"
    if not script.exists():
        out = _assemble_generic_fallback(
            nid, query=query, task=task, chapter=chapter, reason="missing_project_assembler"
        )
        if use_cache:
            with _ASSEMBLE_LOCK:
                if len(_ASSEMBLE_CACHE) >= _ASSEMBLE_CACHE_MAX:
                    _ASSEMBLE_CACHE.pop(next(iter(_ASSEMBLE_CACHE)))
                _ASSEMBLE_CACHE[key] = out
        return out
    supported = _script_supported_tasks(script)
    if supported and task not in supported:
        out = _assemble_generic_fallback(
            nid,
            query=query,
            task=task,
            chapter=chapter,
            reason="unsupported_project_assembler_task",
            details={"script": _rel(script), "supported_tasks": supported},
        )
        if use_cache:
            with _ASSEMBLE_LOCK:
                if len(_ASSEMBLE_CACHE) >= _ASSEMBLE_CACHE_MAX:
                    _ASSEMBLE_CACHE.pop(next(iter(_ASSEMBLE_CACHE)))
                _ASSEMBLE_CACHE[key] = out
        return out
    args = [_python(), str(script), "--task", task, "--query", query, "--output", str(output)]
    if chapter:
        args += ["--chapter", str(chapter)]
    try:
        result = _run(args, cwd=novel_path, timeout=120, novel_id=nid)
    except WritingToolError as exc:
        if _is_unsupported_task_error(str(exc)):
            out = _assemble_generic_fallback(
                nid,
                query=query,
                task=task,
                chapter=chapter,
                reason="project_assembler_rejected_task",
                details={"script": _rel(script), "error": str(exc)[:500]},
            )
            if use_cache:
                with _ASSEMBLE_LOCK:
                    if len(_ASSEMBLE_CACHE) >= _ASSEMBLE_CACHE_MAX:
                        _ASSEMBLE_CACHE.pop(next(iter(_ASSEMBLE_CACHE)))
                    _ASSEMBLE_CACHE[key] = out
            return out
        raise
    if not output.exists():
        raise WritingToolError("材料组装完成但未生成输出文件")
    data = json.loads(output.read_text(encoding="utf-8"))
    data["material_health"] = assess_material_health(data, task=task, chapter=chapter)
    out = {
        "ok": True,
        "novel_id": nid,
        "task": task,
        "chapter": chapter,
        "output_path": str(output.relative_to(ROOT)).replace("\\", "/"),
        "bundle": data,
        "log": result.stdout.strip(),
        "cached": False,
    }
    if use_cache:
        with _ASSEMBLE_LOCK:
            if len(_ASSEMBLE_CACHE) >= _ASSEMBLE_CACHE_MAX:
                _ASSEMBLE_CACHE.pop(next(iter(_ASSEMBLE_CACHE)))
            _ASSEMBLE_CACHE[key] = out
    return out


def _assemble_generic_fallback(
    novel_id: str,
    *,
    query: str,
    task: str,
    chapter: int | None,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_project_initialized(novel_id, query)
    out = assemble_generic_bundle(novel_id, query=query, task=task, chapter=chapter)
    out["cached"] = False
    out["generic_branch"] = True
    out["fallback"] = {"reason": reason, **(details or {})}
    bundle = out.get("bundle") or {}
    _augment_generic_reference_materials(bundle, query=query, novel_id=novel_id)
    bundle["material_health"] = assess_material_health(bundle, task=task, chapter=chapter)
    out["bundle"] = bundle
    rewrite_material_bundle_output(out, bundle)
    return out


def _augment_generic_reference_materials(bundle: dict[str, Any], *, query: str, novel_id: str) -> None:
    """Add reference novel retrieval to generic material bundles.

    Project-specific assemblers may have richer logic. This fallback keeps new
    projects from losing the shared reference corpus when no project script exists.
    """
    if not str(query or "").strip():
        return
    materials = bundle.setdefault("materials", {})
    if materials.get("semantic_results") or materials.get("five_dim_results"):
        return
    try:
        refs = search_references(query, top_k=8, novel_id=novel_id)
    except Exception as exc:
        bundle.setdefault("material_warnings", []).append({
            "code": "reference_retrieval_failed",
            "message": f"{type(exc).__name__}: {exc}",
        })
        return
    results = refs.get("results") or []
    if refs.get("engine") == "semantic_tfidf":
        materials["semantic_results"] = results[:8]
    else:
        materials["five_dim_results"] = results[:8]
    bundle["reference_retrieval"] = {
        "engine": refs.get("engine"),
        "index_ready": refs.get("index_ready"),
        "notice": refs.get("notice", ""),
        "count": len(results),
    }


def rewrite_material_bundle_output(out: dict[str, Any], bundle: dict[str, Any]) -> None:
    output_path = out.get("output_path")
    if not output_path:
        return
    target = (ROOT / output_path).resolve()
    try:
        target.relative_to(ROOT.resolve())
    except ValueError:
        return
    if target.is_file():
        target.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_project_assets(novel_id: str | None, query: str = "", limit: int = 12) -> dict[str, Any]:
    """Collect current-project assets/references for creative material assembly.

    This only scans the selected project's asset/reference directories. It is used
    by the creative flow after intent analysis, not by plain chat.
    """
    nid = normalize_novel_id(novel_id)
    roots = [
        assets_dir(nid),
        project_dir(nid, "references"),
    ]
    keywords = _query_keywords(query)
    files: list[dict[str, Any]] = []
    text_excerpts: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "_superseded" in path.parts:
                continue
            rel = _rel(path)
            suffix = path.suffix.lower()
            item = {
                "path": rel,
                "name": path.name,
                "suffix": suffix,
                "size": path.stat().st_size,
                "kind": _asset_kind(suffix),
            }
            score = _asset_match_score(path, keywords)
            if score:
                item["match_score"] = score
            files.append(item)
            if suffix in {".md", ".txt", ".json", ".yaml", ".yml"}:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    text = ""
                excerpt = _asset_excerpt(text, keywords)
                if excerpt:
                    text_excerpts.append({"path": rel, "text": excerpt, "match_score": score})
    files.sort(key=lambda item: (item.get("match_score", 0), item.get("size", 0)), reverse=True)
    text_excerpts.sort(key=lambda item: item.get("match_score", 0), reverse=True)
    return {
        "ok": True,
        "novel_id": nid,
        "query": query,
        "roots": [_rel(root) for root in roots if root.exists()],
        "files": files[:limit],
        "text_excerpts": text_excerpts[: min(limit, 8)],
        "total_files": len(files),
    }


def _query_keywords(query: str) -> list[str]:
    words = re.findall(r"[\u4e00-\u9fa5]{2,8}|[A-Za-z0-9_]{3,}", query or "")
    stop = {"根据", "分析", "创作", "生成", "写作", "内容", "材料", "项目", "当前"}
    out: list[str] = []
    for word in words:
        if word in stop or word in out:
            continue
        out.append(word)
    return out[:16]


def _asset_kind(suffix: str) -> str:
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        return "image"
    if suffix in {".md", ".txt", ".json", ".yaml", ".yml"}:
        return "text"
    if suffix in {".docx", ".pdf"}:
        return "document"
    return "file"


def _asset_match_score(path: Path, keywords: list[str]) -> int:
    if not keywords:
        return 1
    haystack = f"{path.stem} {path.parent.name}"
    return sum(1 for word in keywords if word and word in haystack)


def _asset_excerpt(text: str, keywords: list[str], max_chars: int = 1200) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    if not keywords:
        return clean[:max_chars]
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", clean) if p.strip()]
    scored = []
    for para in paragraphs:
        score = sum(para.count(word) for word in keywords)
        if score:
            scored.append((score, para))
    if not scored:
        return clean[:max_chars]
    scored.sort(key=lambda item: item[0], reverse=True)
    return "\n\n".join(para for _, para in scored[:3])[:max_chars]


def _script_supported_tasks(script: Path) -> list[str]:
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    match = re.search(r"choices\s*=\s*(\[[\s\S]*?\])", text)
    if not match:
        return []
    try:
        parsed = ast.literal_eval(match.group(1))
    except Exception:
        return []
    return [str(item) for item in parsed if str(item).strip()]


def supported_tasks_for_assembler(script: Path) -> list[str]:
    """Return task choices declared by a project material assembler."""
    return _script_supported_tasks(script)


def _is_unsupported_task_error(text: str) -> bool:
    lowered = (text or "").lower()
    return "invalid choice" in lowered or ("--task" in lowered and "usage:" in lowered)


def assess_material_health(bundle: dict[str, Any], *, task: str, chapter: int | None = None) -> dict[str, Any]:
    """Return non-blocking dependency health for a material bundle.

    Missing auxiliary material should be visible and auditable, but it should not
    crash the creative flow. The caller may surface warnings in UI/logs.
    """
    materials = bundle.get("materials") or {}
    project_kind_value = bundle.get("project_kind") or ""
    stage_profile: dict[str, Any] = {}
    try:
        from app.writing_task_profiles import is_novel_planning_task, novel_stage_profile

        if is_novel_planning_task(project_kind_value, task) or task in {"prose", "expansion", "fix"}:
            stage_profile = novel_stage_profile(task)
    except Exception:
        stage_profile = {}
    warnings: list[dict[str, Any]] = []
    signals = {
        "chapter_outline": bool(str(materials.get("chapter_outline") or "").strip()),
        "character_profiles": bool(str(materials.get("character_profiles") or "").strip()),
        "constraints": bool(str(materials.get("constraints") or materials.get("worldbuilding") or "").strip()),
        "semantic_results": len(materials.get("semantic_results") or []),
        "five_dim_results": len(materials.get("five_dim_results") or []),
        "project_docs": _count_docs(materials.get("project_docs")),
        "cross_chapter": len(bundle.get("cross_chapter") or []),
        "output_recall": len(bundle.get("output_recall") or []),
        "wiki_items": len(bundle.get("wiki_items") or []),
        "project_wiki_items": len(bundle.get("project_wiki_items") or []),
    }

    reference_count = int(signals["semantic_results"]) + int(signals["five_dim_results"])
    if project_kind_value == "novel_strong":
        if stage_profile and stage_profile.get("flow") != "full_generation" and not signals["project_docs"] and not signals["project_wiki_items"]:
            warnings.append(_material_warning(
                "missing_planning_structure_material",
                f"当前为{stage_profile.get('label')}阶段，但未召回明确结构材料，将主要依赖用户输入生成前期规划稿。",
                {"creative_stage": stage_profile.get("id"), "expected_sections": stage_profile.get("material_sections") or []},
            ))
        if task in {"prose", "beat_sheet", "expansion", "fix"} and chapter and not signals["chapter_outline"]:
            warnings.append(_material_warning("missing_chapter_outline", "缺少目标章节大纲，正文/节拍会降级为用户请求与通用规则驱动。"))
        if task in {"prose", "beat_sheet", "character", "fix"} and not signals["character_profiles"]:
            warnings.append(_material_warning("missing_character_profiles", "缺少相关人物设定，人物声音与关系连续性可能变弱。"))
        if task in {"prose", "beat_sheet", "expansion", "fix"} and reference_count < 2:
            warnings.append(_material_warning("low_reference_recall", "五维库/参考小说召回过少，将主要依赖项目结构材料与技法知识库。", {"reference_count": reference_count}))
        if task in {"prose", "expansion", "fix"} and chapter and chapter > 1 and not signals["cross_chapter"] and not signals["output_recall"]:
            warnings.append(_material_warning("missing_continuity_memory", "未召回前文摘要或已确认产出，章节连续性需人工重点检查。"))
    elif project_kind_value == "short_film":
        if task in {"screenplay", "shot_list", "beat_sheet"} and not signals["chapter_outline"] and not signals["project_docs"]:
            warnings.append(_material_warning("missing_short_film_source", "缺少节拍/剧本/项目文档，短片生成会降级为用户请求和通用短片范式。"))
        if task == "screenplay" and not signals["character_profiles"]:
            warnings.append(_material_warning("missing_short_film_characters", "缺少角色材料，剧本对白和弧光可能不足。"))
    else:
        if not signals["project_docs"]:
            warnings.append(_material_warning("missing_generic_docs", "随想项目尚无可用项目材料，将按用户输入直接整理。"))

    level = "warn" if warnings else "ok"
    return {
        "ok": True,
        "level": level,
        "warnings": warnings,
        "signals": signals,
        "stage_profile": stage_profile,
        "expected_material_sections": stage_profile.get("material_sections") if stage_profile else [],
        "acceptance_signals": stage_profile.get("acceptance_signals") if stage_profile else [],
    }


def _count_docs(value: Any) -> int:
    if isinstance(value, dict):
        return sum(1 for text in value.values() if str(text or "").strip())
    if isinstance(value, list):
        return len(value)
    return 1 if str(value or "").strip() else 0


def _material_warning(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    item = {"code": code, "message": message}
    if details:
        item["details"] = details
    return item


def pre_review_chapter(chapter: int, novel_id: str | None = None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    novel_path = novel_dir(nid)
    script = novel_path / "脚本" / "pre_llm_review.py"
    if not script.exists():
        script = novel_path / "scripts" / "pre_llm_review.py"
    if not script.exists():
        return {
            "ok": True,
            "novel_id": nid,
            "project_kind": project_kind(nid),
            "chapter": chapter,
            "results": [{
                "ok": True,
                "issue_count": 0,
                "blocking_count": 0,
                "issues": [],
                "notice": "当前项目未配置专属预审查脚本，已跳过强制脚本审查。",
            }],
        }
    result = _run([_python(), str(script), "--chapters", str(chapter), "--json"], cwd=novel_path, timeout=60, novel_id=nid)
    data = _json_from_stdout(result.stdout) or []
    return {
        "ok": True,
        "novel_id": nid,
        "chapter": chapter,
        "results": data,
    }


_PREREVIEW_CHAIN = None
_PREREVIEW_LOCK = threading.Lock()


def _ensure_engine_on_path() -> None:
    """把 novel-acquisition 加入 sys.path，使 langchain_engine 可在进程内导入。"""
    acq = str(NOVEL_ACQ_DIR)
    if acq not in sys.path:
        sys.path.insert(0, acq)


def _get_prereview_chain():
    """惰性初始化 PreReviewChain 单例，加锁防并发首次初始化竞态。"""
    global _PREREVIEW_CHAIN
    if _PREREVIEW_CHAIN is not None:
        return _PREREVIEW_CHAIN
    with _PREREVIEW_LOCK:
        if _PREREVIEW_CHAIN is None:
            _ensure_engine_on_path()
            from langchain_engine.review_chain import PreReviewChain
            _PREREVIEW_CHAIN = PreReviewChain()
    return _PREREVIEW_CHAIN


def pre_review_text(text: str, outline: str = "", character_names: list[str] | None = None) -> dict[str, Any]:
    """对内存中的正文做硬规则预审查（进程内调用 PreReviewChain），供生成节点门禁使用。"""
    if not (text or "").strip():
        return {"ok": True, "passed": True, "blocking_count": 0, "warning_count": 0, "issues": []}
    try:
        chain = _get_prereview_chain()
        result = chain.check(text, outline=outline, character_names=character_names)
        issues = [
            {
                "rule": i.rule, "severity": i.severity, "line": i.line_num,
                "evidence": i.text_excerpt, "suggestion": i.suggestion,
            }
            for i in result.issues
        ]
        return {
            "ok": True,
            "passed": result.passed,
            "blocking_count": result.blocking_count,
            "warning_count": result.warning_count,
            "issues": issues,
        }
    except Exception as exc:
        # 预审查失败不应吞掉生成结果；标记为未门禁通过但记录原因。
        return {"ok": False, "passed": True, "blocking_count": 0, "warning_count": 0,
                "issues": [], "error": f"{type(exc).__name__}: {exc}"}


def pre_review_issues_text(issues: list[dict[str, Any]]) -> str:
    """把预审查问题整理成回环反馈文本。"""
    lines = []
    for it in issues:
        lines.append(f"- [{it.get('severity')}] {it.get('rule')} L{it.get('line')}: {it.get('evidence','')} → {it.get('suggestion','')}")
    return "\n".join(lines)


def _is_meaningful_file(path: Path, placeholders: list[str] | None = None, min_chars: int = 160) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return False
    if len(text) < min_chars:
        return False
    lowered = text.lower()
    for marker in placeholders or []:
        if marker and marker.lower() in lowered and len(text) < min_chars * 2:
            return False
    return True


def _outline_chapter_count(novel_path: Path) -> int:
    try:
        from app.project_structure import find_related_structure_file, resolve_structure_target

        _role, path = resolve_structure_target(novel_path.name, "outline", create_missing=False)
        if not path or not path.exists():
            matched = find_related_structure_file(novel_path.name, "outline")
            path = matched[1] if matched else None
        if path and path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            matches = re.findall(r"^#{1,4}\s*第\s*([0-9一二三四五六七八九十百两]+)\s*章", text, flags=re.M)
            if matches:
                return len(matches)
    except Exception:
        pass
    return 0


def _confirmed_chapters(novel_id: str, novel_path: Path) -> list[int]:
    confirmed: set[int] = set()
    try:
        try:
            from app.project_structure import resolve_structure_target

            _role, status_path = resolve_structure_target(novel_id, "chapter_status", create_missing=False)
        except Exception:
            status_path = None
        if not status_path:
            status_path = project_dir(novel_id, "memory", "章节完成状态.json")
        if not status_path.exists():
            status_path = novel_path / "章节完成状态.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        for key, value in (status or {}).items():
            if isinstance(value, dict) and value.get("confirmed"):
                try:
                    confirmed.add(int(key))
                except Exception:
                    pass
    except Exception:
        pass
    chapters_dir = project_dir(novel_id, "chapters")
    if chapters_dir.is_dir():
        for path in chapters_dir.glob("chapter-*.md"):
            match = re.search(r"chapter-(\d+)", path.name)
            if match and path.stat().st_size > 0:
                confirmed.add(int(match.group(1)))
    return sorted(confirmed)


def _novel_progress(novel_id: str, novel_path: Path) -> dict[str, Any]:
    outline_count = _outline_chapter_count(novel_path)
    completed = _confirmed_chapters(novel_id, novel_path)
    completed_max = max(completed) if completed else 0
    current = completed_max + 1 if outline_count == 0 or completed_max < outline_count else completed_max
    stages = _novel_stage_progress(novel_id, completed_count=len(completed), outline_count=outline_count)
    current_stage = next((item for item in stages if not item.get("done")), stages[-1] if stages else {})
    if outline_count and completed_max >= outline_count:
        label = f"已完成 {completed_max}/{outline_count} 章"
    else:
        label = f"第{current}章待创作"
    return {
        "kind": "novel_strong",
        "outline_chapters": outline_count,
        "completed_chapters": completed,
        "completed_count": len(completed),
        "completed_max": completed_max,
        "current_chapter": current,
        "current_stage_key": current_stage.get("key") or "prose",
        "current_stage": current_stage.get("label") or "正文",
        "status_label": label,
        "stages": stages,
        "items": [
            {"label": "当前阶段", "value": current_stage.get("label") or "正文"},
            {"label": "大纲章节", "value": outline_count or "未识别", "unit": "章" if outline_count else ""},
            {"label": "正文完成", "value": completed_max or 0, "unit": "章"},
            {"label": "当前进度", "value": label, "wide": True},
        ],
    }


def _novel_stage_progress(novel_id: str, *, completed_count: int, outline_count: int) -> list[dict[str, Any]]:
    try:
        from app.project_structure import resolve_structure_target
        from app.writing_task_profiles import NOVEL_STAGE_PROFILES
    except Exception:
        return []
    stages: list[dict[str, Any]] = []
    for key in ["concept", "setting", "world", "character", "outline", "plot"]:
        profile = NOVEL_STAGE_PROFILES.get(key) or {}
        role = profile.get("structure_role")
        _resolved_role, path = resolve_structure_target(novel_id, role, create_missing=False)
        done = bool(path and _is_meaningful_file(path, placeholders=["待补充", "待定"], min_chars=120))
        stages.append({
            "key": key,
            "label": profile.get("label") or key,
            "task": profile.get("canonical_task") or "",
            "role": role,
            "done": done,
            "path": _rel(path) if path and path.exists() else "",
        })
    stages.append({
        "key": "prose",
        "label": "正文",
        "task": "prose",
        "role": "chapter_body",
        "done": bool(outline_count and completed_count >= outline_count),
        "completed_count": completed_count,
        "outline_count": outline_count,
    })
    return stages


def _short_film_progress(novel_path: Path) -> dict[str, Any]:
    def routed(role: str, fallback: str) -> Path:
        try:
            from app.project_structure import resolve_structure_target
            _role, path = resolve_structure_target(novel_path.name, role, create_missing=False)
            if path:
                return path
        except Exception:
            pass
        return novel_path / fallback

    checks = [
        ("logline", "概念", routed("concept", "logline.md"), 600, ["待定"]),
        ("characters", "角色", routed("character", "characters.md"), 260, ["待补充"]),
        ("beat_sheet", "节拍", routed("beat_sheet", "beat_sheet.md"), 260, ["待补充"]),
        ("screenplay", "剧本", routed("screenplay", "screenplay.md"), 260, ["动作描写", "对白"]),
        ("shot_list", "分镜", routed("shot_list", "shot_list.md"), 260, ["待补充"]),
    ]
    stages = []
    for key, label, path, min_chars, placeholders in checks:
        done = _is_meaningful_file(path, placeholders, min_chars=min_chars)
        stages.append({"key": key, "label": label, "done": done, "path": str(path.relative_to(ROOT)).replace("\\", "/") if path.exists() else ""})
    storyboards = storyboards_dir(novel_path.name)
    manifest = storyboards / "visual_prompt_manifest.json"
    prompt_done = manifest.exists() and manifest.stat().st_size > 20
    image_files = []
    if storyboards.is_dir():
        for suffix in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            image_files.extend(
                path for path in storyboards.glob(f"**/{suffix}")
                if "_superseded" not in path.parts
            )
    image_done = bool(image_files)
    stages.extend([
        {"key": "visual_prompts", "label": "生词", "done": prompt_done, "path": str(manifest.relative_to(ROOT)).replace("\\", "/") if manifest.exists() else ""},
        {"key": "images", "label": "生图", "done": image_done, "count": len(image_files)},
    ])
    done_count = sum(1 for item in stages if item.get("done"))
    current = next((item["label"] for item in stages if not item.get("done")), "完成")
    return {
        "kind": "short_film",
        "stage_count": len(stages),
        "done_count": done_count,
        "current_stage": current,
        "status_label": "全部完成" if done_count == len(stages) else f"{current}待处理",
        "stages": stages,
        "items": [
            {"label": "剧本创作", "value": "完成" if next((s for s in stages if s["key"] == "screenplay"), {}).get("done") else "待处理"},
            {"label": "确认", "value": f"{done_count}/{len(stages)}"},
            {"label": "生词", "value": "完成" if prompt_done else "待处理"},
            {"label": "生图", "value": len(image_files) if image_files else "待处理", "unit": "张" if image_files else ""},
            {"label": "当前进度", "value": "全部完成" if done_count == len(stages) else f"{current}待处理", "wide": True},
        ],
    }


def _generic_progress(novel_path: Path) -> dict[str, Any]:
    files = [path for path in novel_path.glob("**/*.md") if path.is_file() and path.stat().st_size > 0 and ".git" not in path.parts]
    return {
        "kind": "generic",
        "status_label": f"{len(files)} 个随想文档",
        "items": [
            {"label": "文档", "value": len(files), "unit": "个"},
            {"label": "当前进度", "value": "随想沉淀中", "wide": True},
        ],
    }


def project_progress(novel_id: str | None = None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    novel_path = novel_dir(nid)
    kind = project_kind(nid)
    if kind == "short_film":
        return _short_film_progress(novel_path)
    if kind == "novel_strong":
        return _novel_progress(nid, novel_path)
    return _generic_progress(novel_path)


def _count_markdown_files(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(item for item in path.glob("*.md") if item.is_file())


def _reference_novel_count() -> dict[str, Any]:
    novel_list = NOVEL_ACQ_DIR / "novel_list.json"
    reference_dir = Path(os.getenv("WRITING_REFERENCE_NOVELS_DIR") or (WRITING_ROOT / "references" / "novels"))
    listed_count = 0
    try:
        data = json.loads(novel_list.read_text(encoding="utf-8"))
        novels = data.get("novels") if isinstance(data, dict) else data
        if isinstance(novels, list):
            listed_count = len(novels)
        elif isinstance(data, dict) and isinstance(data.get("total"), int):
            listed_count = int(data["total"])
    except Exception:
        listed_count = 0

    extracted_dir = NOVEL_ACQ_DIR / "extracted"
    extracted_count = len([p for p in extracted_dir.iterdir() if p.is_dir()]) if extracted_dir.is_dir() else 0
    txt_files = sorted(reference_dir.glob("*.txt")) if reference_dir.is_dir() else []
    raw_count = len(txt_files)
    return {
        "count": max(listed_count, extracted_count, raw_count),
        "listed_count": listed_count,
        "extracted_count": extracted_count,
        "raw_count": raw_count,
        "reference_dir": str(reference_dir.relative_to(ROOT)).replace("\\", "/") if reference_dir.exists() else "",
        "source": str(novel_list.relative_to(ROOT)).replace("\\", "/") if novel_list.exists() else "",
    }


def _five_dim_inventory() -> dict[str, Any]:
    anchors_dir = NOVEL_ACQ_DIR / "anchors"
    extracted_dir = NOVEL_ACQ_DIR / "extracted"
    anchor_files = sorted(anchors_dir.glob("*.json")) if anchors_dir.is_dir() else []
    analysis_files = sorted(extracted_dir.glob("*/anchor_analysis.json")) if extracted_dir.is_dir() else []
    dimension_counts: dict[str, int] = {}
    total_anchors = 0
    total_segments = 0

    for path in analysis_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        results = data.get("results") if isinstance(data, dict) else []
        if not isinstance(results, list):
            continue
        total_anchors += len(results)
        for anchor in results:
            dimensions = anchor.get("dimensions") if isinstance(anchor, dict) else {}
            if not isinstance(dimensions, dict):
                continue
            for name, entries in dimensions.items():
                count = len(entries) if isinstance(entries, list) else 0
                dimension_counts[name] = dimension_counts.get(name, 0) + count
                total_segments += count

    return {
        "anchor_file_count": len(anchor_files),
        "analysis_file_count": len(analysis_files),
        "anchor_count": total_anchors or len(anchor_files),
        "segment_count": total_segments,
        "dimensions": dict(sorted(dimension_counts.items())),
    }


def project_inventory(novel_id: str | None = None) -> dict[str, Any]:
    """项目资源盘点：仅供 UI 展示，不参与 LLM 意图分析。"""
    nid = normalize_novel_id(novel_id)
    novel_path = novel_dir(nid)
    skill_files = _count_markdown_files(skills_dir(nid))
    kind = project_kind(nid)
    if kind == "novel_strong":
        try:
            from app.novel_skills import skill_counts

            skills = skill_counts(nid)
        except Exception:
            skills = {
                "count": len(skill_files),
                "project_count": len(skill_files),
                "public_count": 0,
                "files": [path.name for path in skill_files],
            }
    elif kind == "short_film":
        try:
            from app.short_film_skill_store import skill_counts

            skills = skill_counts(nid)
        except Exception:
            skills = {
                "count": len(skill_files),
                "project_count": len(skill_files),
                "public_count": 0,
                "files": [path.name for path in skill_files],
            }
    else:
        skills = {
            "count": len(skill_files),
            "project_count": len(skill_files),
            "public_count": 0,
            "files": [path.name for path in skill_files],
        }
    return {
        "skills": skills,
        "reference_novels": _reference_novel_count(),
        "five_dim": _five_dim_inventory(),
    }


def project_status(novel_id: str | None = None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    novel_path = novel_dir(nid)
    init: dict[str, Any] = {"ok": True, "created": False}
    kind = project_kind(nid)
    if kind in {"novel_strong", "short_film", "generic"}:
        try:
            from app.novel_artifacts import ensure_novel_files
            from app.project_structure import ensure_project_structure_wiki

            if kind == "novel_strong":
                ensured = ensure_novel_files(nid)
            else:
                ensured = ensure_project_structure_wiki(nid, project_kind=kind, create_missing=True)
            init = {"ok": True, "created": bool(ensured.get("created")), "files": ensured.get("created") or []}
        except Exception as exc:
            init = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "project": "writing",
        "novel": nid,
        "project_kind": kind,
        "project_init": init,
        "novel_path": str(novel_path.relative_to(ROOT)).replace("\\", "/"),
        "writing_root": str(WRITING_ROOT),
        "semantic_index_ready": (NOVEL_ACQ_DIR / "cache" / "tfidf_index.pkl").exists(),
        "project_progress": project_progress(nid),
        "project_inventory": project_inventory(nid),
        "chapters": [
            {"chapter": idx + 1, "name": path.name, "path": str(path.relative_to(ROOT)).replace("\\", "/")}
            for idx, path in enumerate(sorted(project_dir(nid, "chapters").glob("chapter-*.md")))
        ],
    }
