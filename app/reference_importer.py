from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from app.config import ROOT
from app.novel_context import WRITING_ROOT


NOVEL_ACQ_DIR = WRITING_ROOT / "novel-acquisition"
REFERENCE_NOVELS_DIR = Path(
    os.getenv("WRITING_REFERENCE_NOVELS_DIR") or (WRITING_ROOT / "references" / "novels")
).expanduser()
WORK_NOVELS_DIR = NOVEL_ACQ_DIR / "novels"
EXTRACTED_DIR = NOVEL_ACQ_DIR / "extracted"
NOVEL_LIST = NOVEL_ACQ_DIR / "novel_list.json"
ANALYZER = NOVEL_ACQ_DIR / "analyzer.py"
SEMANTIC_SEARCH = NOVEL_ACQ_DIR / "semantic_search.py"
MAX_REFERENCE_NOVEL_BYTES = 120 * 1024 * 1024
DEFAULT_REFERENCE_DIMENSION_LIMIT = 48
DEFAULT_METHOD_DIMENSION_LIMIT = DEFAULT_REFERENCE_DIMENSION_LIMIT
DEFAULT_METHOD_DIMENSION_FALLBACK_LIMIT = 8
METHOD_DIMENSION_PREFIX = "method_"
LEGACY_REFERENCE_DIMENSIONS = ("scenes", "psychology", "characters", "twists", "intelligence")
ANALYZER_REFERENCE_LIMIT_ENV = "WRITING_REFERENCE_DIMENSION_LIMIT"
ANALYZER_TWIST_PAIR_LIMIT_ENV = "WRITING_REFERENCE_TWIST_PAIR_LIMIT"


Progress = Callable[[str, str, str, dict[str, Any] | None], None]


def import_reference_novel(
    *,
    filename: str,
    content: bytes,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Import a txt novel into the reference corpus and rebuild local retrieval.

    The durable contract is:
    - save original txt into the reference novel library;
    - mirror it into novel-acquisition/novels/<title>/novel.txt for analyzers;
    - run local legacy five-dimension extraction;
    - synthesize anchor_analysis.json so existing retrieval scripts can consume it;
    - add method_* creative-method dimensions for the enhanced creation flow;
    - rebuild the TF-IDF semantic index.
    """
    warnings: list[dict[str, str]] = []
    anchor_analysis: dict[str, Any] = {}
    _emit(progress, "reference_import_validate", "校验 TXT 文件", "running")
    safe_name = _safe_filename(filename)
    if Path(safe_name).suffix.lower() != ".txt":
        raise ValueError("只支持导入 .txt 格式小说")
    if not content:
        raise ValueError("上传文件为空")
    if len(content) > MAX_REFERENCE_NOVEL_BYTES:
        raise ValueError("文件超过 120MB 上限")
    base_title = _title_from_filename(safe_name)
    title = _unique_title(base_title)
    _emit(progress, "reference_import_validate", "校验 TXT 文件", "done", {"title": title})

    _emit(progress, "reference_import_save", "保存参考小说", "running")
    reference_path = _unique_path(REFERENCE_NOVELS_DIR / f"{title}.txt")
    work_dir = WORK_NOVELS_DIR / title
    work_path = work_dir / "novel.txt"
    REFERENCE_NOVELS_DIR.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(content)
    shutil.copyfile(reference_path, work_path)
    _update_novel_list(title, source=reference_path)
    _emit(progress, "reference_import_save", "保存参考小说", "done", {
        "reference_path": _rel(reference_path),
        "work_path": _rel(work_path),
    })

    analysis_result: dict[str, Any] = {}
    _emit(progress, "reference_import_analyze", "五维本地抽取", "running")
    try:
        analysis_result = _run_json(
            [_python(), str(ANALYZER), title],
            cwd=NOVEL_ACQ_DIR,
            timeout=900,
            env_overrides=_reference_dimension_env(),
        )
        if analysis_result.get("error"):
            raise RuntimeError(str(analysis_result["error"]))
        _emit(progress, "reference_import_analyze", "五维本地抽取", "done", analysis_result)
    except Exception as exc:
        warnings.append({"stage": "reference_import_analyze", "message": f"{type(exc).__name__}: {exc}"})
        _emit(progress, "reference_import_analyze", "五维本地抽取", "warn", {"warning": warnings[-1]["message"]})

    _emit(progress, "reference_import_five_dim", "写入多维参考库", "running")
    try:
        anchor_analysis = _synthesize_anchor_analysis(title)
        _emit(progress, "reference_import_five_dim", "写入多维参考库", "done", {
            "segments": anchor_analysis.get("total_dimension_matches", 0),
            "dimensions": anchor_analysis.get("dimension_coverage", {}),
            "path": _rel(EXTRACTED_DIR / title / "anchor_analysis.json"),
        })
    except Exception as exc:
        warnings.append({"stage": "reference_import_five_dim", "message": f"{type(exc).__name__}: {exc}"})
        _emit(progress, "reference_import_five_dim", "写入五维库", "warn", {"warning": warnings[-1]["message"]})

    _emit(progress, "reference_import_index", "重建语义索引", "running")
    try:
        _run_text([_python(), str(SEMANTIC_SEARCH), "--build"], cwd=NOVEL_ACQ_DIR, timeout=900)
        _emit(progress, "reference_import_index", "重建语义索引", "done", {
            "index": _rel(NOVEL_ACQ_DIR / "cache" / "tfidf_index.pkl"),
        })
    except Exception as exc:
        warnings.append({"stage": "reference_import_index", "message": f"{type(exc).__name__}: {exc}"})
        _emit(progress, "reference_import_index", "重建语义索引", "warn", {"warning": warnings[-1]["message"]})

    _emit(progress, "reference_import_refresh", "刷新项目盘点", "done")
    return {
        "ok": True,
        "title": title,
        "reference_path": _rel(reference_path),
        "work_path": _rel(work_path),
        "analysis": analysis_result,
        "dimension_coverage": anchor_analysis.get("dimension_coverage", {}),
        "total_dimension_matches": anchor_analysis.get("total_dimension_matches", 0),
        "warnings": warnings,
    }


def backfill_reference_method_dimensions(
    *,
    rebuild_index: bool = False,
    method_dim_limit: int | None = None,
    method_fallback_limit: int | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Backfill method_* dimensions for already imported reference novels."""
    _emit(progress, "reference_method_backfill_scan", "扫描已导入参考库", "running")
    titles = []
    if WORK_NOVELS_DIR.is_dir():
        titles = sorted(path.name for path in WORK_NOVELS_DIR.iterdir() if (path / "novel.txt").is_file())
    _emit(progress, "reference_method_backfill_scan", "扫描已导入参考库", "done", {"count": len(titles)})

    updated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for title in titles:
        try:
            result = _backfill_anchor_analysis_methods(
                title,
                method_dim_limit=method_dim_limit,
                method_fallback_limit=method_fallback_limit,
            )
            method_dims = result.get("method_dimensions") or {}
            if result.get("changed"):
                updated.append({"title": title, "method_dimensions": method_dims, "path": result.get("path", "")})
            else:
                skipped.append({"title": title, "reason": result.get("reason") or "method_dimensions_unchanged", "method_dimensions": method_dims})
        except Exception as exc:
            warnings.append({"title": title, "message": f"{type(exc).__name__}: {exc}"})

    index_result: dict[str, Any] = {"rebuilt": False}
    if rebuild_index and updated:
        try:
            _run_text([_python(), str(SEMANTIC_SEARCH), "--build"], cwd=NOVEL_ACQ_DIR, timeout=900)
            index_result = {"rebuilt": True, "index": _rel(NOVEL_ACQ_DIR / "cache" / "tfidf_index.pkl")}
        except Exception as exc:
            warnings.append({"stage": "reference_import_index", "message": f"{type(exc).__name__}: {exc}"})
            index_result = {"rebuilt": False, "error": warnings[-1]["message"]}
    elif rebuild_index:
        index_result = {"rebuilt": False, "reason": "no_reference_method_changes"}
    return {
        "ok": True,
        "total": len(titles),
        "updated": len(updated),
        "skipped": len(skipped),
        "warnings": warnings,
        "updated_items": updated[:20],
        "skipped_items": skipped[:20],
        "index": index_result,
    }


def backfill_reference_dimensions(
    *,
    scope: str = "all",
    rebuild_index: bool = False,
    dimension_limit: int | None = None,
    method_fallback_limit: int | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Backfill reference dimensions with one shared per-dimension limit."""
    selected = (scope or "all").strip().lower()
    if selected not in {"all", "basic", "method"}:
        raise ValueError("scope must be one of: all, basic, method")
    basic_result: dict[str, Any] | None = None
    method_result: dict[str, Any] | None = None
    if selected in {"all", "basic"}:
        basic_result = backfill_reference_basic_dimensions(
            rebuild_index=False,
            dimension_limit=dimension_limit,
            progress=progress,
        )
    if selected in {"all", "method"}:
        method_result = backfill_reference_method_dimensions(
            rebuild_index=False,
            method_dim_limit=dimension_limit,
            method_fallback_limit=method_fallback_limit,
            progress=progress,
        )

    changed = int((basic_result or {}).get("updated") or 0) + int((method_result or {}).get("updated") or 0)
    warnings = [*(basic_result or {}).get("warnings", []), *(method_result or {}).get("warnings", [])]
    index_result: dict[str, Any] = {"rebuilt": False}
    if rebuild_index and changed:
        try:
            _run_text([_python(), str(SEMANTIC_SEARCH), "--build"], cwd=NOVEL_ACQ_DIR, timeout=900)
            index_result = {"rebuilt": True, "index": _rel(NOVEL_ACQ_DIR / "cache" / "tfidf_index.pkl")}
        except Exception as exc:
            warnings.append({"stage": "reference_import_index", "message": f"{type(exc).__name__}: {exc}"})
            index_result = {"rebuilt": False, "error": warnings[-1]["message"]}
    elif rebuild_index:
        index_result = {"rebuilt": False, "reason": "no_reference_dimension_changes"}

    return {
        "ok": not warnings,
        "scope": selected,
        "dimension_limit": _bounded_int(
            dimension_limit,
            env_name=ANALYZER_REFERENCE_LIMIT_ENV,
            default=DEFAULT_REFERENCE_DIMENSION_LIMIT,
            minimum=8,
            maximum=160,
        ),
        "basic": basic_result,
        "method": method_result,
        "warnings": warnings,
        "index": index_result,
    }


def backfill_reference_basic_dimensions(
    *,
    rebuild_index: bool = False,
    dimension_limit: int | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Refresh legacy reference dimensions without touching method_* dimensions."""
    dim_limit = _bounded_int(
        dimension_limit,
        env_name=ANALYZER_REFERENCE_LIMIT_ENV,
        default=DEFAULT_REFERENCE_DIMENSION_LIMIT,
        minimum=8,
        maximum=160,
    )
    _emit(progress, "reference_basic_backfill_scan", "扫描已导入参考库", "running")
    titles = []
    if WORK_NOVELS_DIR.is_dir():
        titles = sorted(path.name for path in WORK_NOVELS_DIR.iterdir() if (path / "novel.txt").is_file())
    _emit(progress, "reference_basic_backfill_scan", "扫描已导入参考库", "done", {"count": len(titles)})

    updated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for title in titles:
        try:
            _emit(progress, "reference_basic_backfill_analyze", "刷新基础维度抽取", "running", {"title": title})
            _run_json(
                [_python(), str(ANALYZER), title],
                cwd=NOVEL_ACQ_DIR,
                timeout=900,
                env_overrides=_reference_dimension_env(dim_limit),
            )
            result = _backfill_anchor_analysis_basic(title, dimension_limit=dim_limit)
            basic_dims = result.get("basic_dimensions") or {}
            if result.get("changed"):
                updated.append({"title": title, "basic_dimensions": basic_dims, "path": result.get("path", "")})
            else:
                skipped.append({"title": title, "reason": result.get("reason") or "basic_dimensions_unchanged", "basic_dimensions": basic_dims})
        except Exception as exc:
            warnings.append({"title": title, "message": f"{type(exc).__name__}: {exc}"})

    index_result: dict[str, Any] = {"rebuilt": False}
    if rebuild_index and updated:
        try:
            _run_text([_python(), str(SEMANTIC_SEARCH), "--build"], cwd=NOVEL_ACQ_DIR, timeout=900)
            index_result = {"rebuilt": True, "index": _rel(NOVEL_ACQ_DIR / "cache" / "tfidf_index.pkl")}
        except Exception as exc:
            warnings.append({"stage": "reference_import_index", "message": f"{type(exc).__name__}: {exc}"})
            index_result = {"rebuilt": False, "error": warnings[-1]["message"]}
    elif rebuild_index:
        index_result = {"rebuilt": False, "reason": "no_reference_basic_changes"}
    return {
        "ok": True,
        "dimension_limit": dim_limit,
        "total": len(titles),
        "updated": len(updated),
        "skipped": len(skipped),
        "warnings": warnings,
        "updated_items": updated[:20],
        "skipped_items": skipped[:20],
        "index": index_result,
    }


def sync_reference_work_copies(progress: Progress | None = None) -> dict[str, Any]:
    """Mirror canonical reference txt files into analyzer work directories."""
    _emit(progress, "reference_sync_scan", "扫描参考小说原文", "running")
    if not REFERENCE_NOVELS_DIR.is_dir():
        return {
            "ok": False,
            "source": _rel(REFERENCE_NOVELS_DIR),
            "error": "参考小说原文目录不存在",
            "copied": 0,
            "skipped": 0,
            "conflicts": [],
        }
    files = sorted(path for path in REFERENCE_NOVELS_DIR.glob("*.txt") if path.is_file())
    _emit(progress, "reference_sync_scan", "扫描参考小说原文", "done", {"count": len(files)})

    WORK_NOVELS_DIR.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    _emit(progress, "reference_sync_copy", "同步五维工作副本", "running", {"total": len(files)})
    for source in files:
        title = _title_from_filename(source.name)
        target_dir = WORK_NOVELS_DIR / title
        target = target_dir / "novel.txt"
        if target.exists():
            if target.stat().st_size == source.stat().st_size:
                skipped.append({"title": title, "reason": "same_size", "path": _rel(target)})
                continue
            conflict_dir = _unique_work_dir(title)
            conflict_target = conflict_dir / "novel.txt"
            conflict_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, conflict_target)
            conflicts.append({
                "title": title,
                "path": _rel(conflict_target),
                "reason": "target_exists_different_size",
            })
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append({"title": title, "path": _rel(target), "size": target.stat().st_size})

    _emit(progress, "reference_sync_copy", "同步五维工作副本", "done", {
        "copied": len(copied),
        "skipped": len(skipped),
        "conflicts": len(conflicts),
    })
    return {
        "ok": True,
        "source": _rel(REFERENCE_NOVELS_DIR),
        "target": _rel(WORK_NOVELS_DIR),
        "total": len(files),
        "copied": len(copied),
        "skipped": len(skipped),
        "conflicts": conflicts,
        "copied_items": copied[:20],
        "skipped_items": skipped[:20],
    }


def _synthesize_anchor_analysis(
    title: str,
    *,
    method_dim_limit: int | None = None,
    method_fallback_limit: int | None = None,
) -> dict[str, Any]:
    out_dir = EXTRACTED_DIR / title
    out_dir.mkdir(parents=True, exist_ok=True)
    dim_limit = _bounded_int(
        None,
        env_name=ANALYZER_REFERENCE_LIMIT_ENV,
        default=DEFAULT_REFERENCE_DIMENSION_LIMIT,
        minimum=8,
        maximum=160,
    )
    dims = _extract_legacy_dimensions_from_files(title, dimension_limit=dim_limit)
    dims.setdefault("intelligence", [])
    existing_dims = _legacy_dimensions_from_analysis(_read_json(out_dir / "anchor_analysis.json"))
    for name, entries in existing_dims.items():
        if name in LEGACY_REFERENCE_DIMENSIONS:
            dims[name] = _merge_dimension_entries(entries, dims.get(name) or [], limit=dim_limit)
    method_dims = _extract_method_dimensions(
        title,
        per_dimension_limit=method_dim_limit,
        fallback_limit=method_fallback_limit,
    )
    dims.update(method_dims)

    total_segments = sum(len(v) for v in dims.values())
    summary = _read_json(out_dir / "summary.json")
    result = {
        "title": title,
        "total_chars": summary.get("total_chars", 0) if isinstance(summary, dict) else 0,
        "total_anchors": 1,
        "v4_anchors": 0,
        "dimension_coverage": {name: len(items) for name, items in dims.items()},
        "total_dimension_matches": total_segments,
        "results": [{
            "anchor": "全书本地抽取",
            "category": "imported_reference",
            "v4_enabled": False,
            "total_matches": total_segments,
            "dimensions": dims,
        }],
    }
    (out_dir / "anchor_analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _backfill_anchor_analysis_basic(title: str, *, dimension_limit: int) -> dict[str, Any]:
    out_dir = EXTRACTED_DIR / title
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "anchor_analysis.json"
    payload = _read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list) or not payload.get("results"):
        payload = _synthesize_anchor_analysis(title)
        basic_dims = {k: v for k, v in (payload.get("dimension_coverage") or {}).items() if k in LEGACY_REFERENCE_DIMENSIONS}
        return {"changed": True, "reason": "missing_anchor_analysis", "basic_dimensions": basic_dims, "path": _rel(path)}

    extracted_dims = _extract_legacy_dimensions_from_files(title, dimension_limit=dimension_limit)
    current_basic_dims = _legacy_dimensions_from_analysis(payload)
    merged_dims: dict[str, list[dict[str, Any]]] = {}
    for name in LEGACY_REFERENCE_DIMENSIONS:
        merged_dims[name] = _merge_dimension_entries(
            current_basic_dims.get(name) or [],
            extracted_dims.get(name) or [],
            limit=dimension_limit,
        )

    comparable_current = {name: current_basic_dims.get(name) or [] for name in merged_dims}
    if _dimensions_equal(comparable_current, merged_dims):
        return {
            "changed": False,
            "reason": "basic_dimensions_unchanged",
            "basic_dimensions": {name: len(items) for name, items in comparable_current.items()},
            "path": _rel(path),
        }

    results = payload.get("results") or []
    first_anchor = results[0]
    if not isinstance(first_anchor, dict):
        first_anchor = {"anchor": "全书本地抽取", "category": "imported_reference", "v4_enabled": False}
        results[0] = first_anchor
    first_dims = first_anchor.setdefault("dimensions", {})
    if not isinstance(first_dims, dict):
        first_dims = {}
        first_anchor["dimensions"] = first_dims

    for anchor in results:
        if not isinstance(anchor, dict):
            continue
        dims = anchor.get("dimensions")
        if isinstance(dims, dict):
            for name in LEGACY_REFERENCE_DIMENSIONS:
                dims.pop(name, None)
    first_dims.update(merged_dims)
    _refresh_anchor_analysis_stats(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "changed": True,
        "reason": "basic_dimensions_updated",
        "basic_dimensions": {name: len(items) for name, items in merged_dims.items()},
        "path": _rel(path),
    }


def _backfill_anchor_analysis_methods(
    title: str,
    *,
    method_dim_limit: int | None = None,
    method_fallback_limit: int | None = None,
) -> dict[str, Any]:
    """Update only method_* dimensions in an existing reference analysis file.

    Non-method dimensions are the source of truth for the legacy reference
    library and must not be regenerated or overwritten by method backfill.
    """
    out_dir = EXTRACTED_DIR / title
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "anchor_analysis.json"
    payload = _read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list) or not payload.get("results"):
        payload = _synthesize_anchor_analysis(
            title,
            method_dim_limit=method_dim_limit,
            method_fallback_limit=method_fallback_limit,
        )
        method_dims = {k: v for k, v in (payload.get("dimension_coverage") or {}).items() if k.startswith(METHOD_DIMENSION_PREFIX)}
        return {"changed": True, "reason": "missing_anchor_analysis", "method_dimensions": method_dims, "path": _rel(path)}

    method_dims = _extract_method_dimensions(
        title,
        per_dimension_limit=method_dim_limit,
        fallback_limit=method_fallback_limit,
    )
    current_method_dims = _method_dimensions_from_analysis(payload)
    if _dimensions_equal(current_method_dims, method_dims):
        return {
            "changed": False,
            "reason": "method_dimensions_unchanged",
            "method_dimensions": {name: len(items) for name, items in current_method_dims.items()},
            "path": _rel(path),
        }

    results = payload.get("results") or []
    first_anchor = results[0]
    if not isinstance(first_anchor, dict):
        first_anchor = {"anchor": "全书本地抽取", "category": "imported_reference", "v4_enabled": False}
        results[0] = first_anchor
    first_dims = first_anchor.setdefault("dimensions", {})
    if not isinstance(first_dims, dict):
        first_dims = {}
        first_anchor["dimensions"] = first_dims

    for anchor in results:
        if not isinstance(anchor, dict):
            continue
        dims = anchor.get("dimensions")
        if isinstance(dims, dict):
            for name in list(dims.keys()):
                if str(name).startswith(METHOD_DIMENSION_PREFIX):
                    dims.pop(name, None)
    first_dims.update(method_dims)
    _refresh_anchor_analysis_stats(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "changed": True,
        "reason": "method_dimensions_updated",
        "method_dimensions": {name: len(items) for name, items in method_dims.items()},
        "path": _rel(path),
    }


def _legacy_dimensions_from_analysis(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return {}
    merged: dict[str, list[dict[str, Any]]] = {}
    for anchor in payload.get("results") or []:
        if not isinstance(anchor, dict):
            continue
        dimensions = anchor.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            continue
        for name in LEGACY_REFERENCE_DIMENSIONS:
            entries = dimensions.get(name)
            if isinstance(entries, list):
                merged.setdefault(name, []).extend(item for item in entries if isinstance(item, dict))
    return merged


def _extract_legacy_dimensions_from_files(title: str, *, dimension_limit: int) -> dict[str, list[dict[str, Any]]]:
    out_dir = EXTRACTED_DIR / title
    dims: dict[str, list[dict[str, Any]]] = {}
    for name in ("scenes", "psychology", "characters", "intelligence"):
        payload = _read_json(out_dir / f"{name}.json")
        samples = payload.get("samples") if isinstance(payload, dict) else []
        entries = []
        for item in samples if isinstance(samples, list) else []:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    entries.append({
                        "text": text[:500],
                        "context": text[:900],
                        "zone": item.get("zone", ""),
                    })
        dims[name] = entries[:dimension_limit]

    twists_payload = _read_json(out_dir / "twists.json")
    twist_entries = []
    for item in (twists_payload.get("samples") if isinstance(twists_payload, dict) else []) or []:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            twist_entries.append({"text": str(item["text"]).strip()[:500], "type": item.get("type", "")})

    pairs_payload = _read_json(out_dir / "foreshadowing_pairs.json")
    pair_entries = []
    for pair in (pairs_payload.get("pairs") if isinstance(pairs_payload, dict) else []) or []:
        if not isinstance(pair, dict):
            continue
        setup = ((pair.get("setup") or {}).get("text") or "").strip()
        payoff = ((pair.get("payoff") or {}).get("text") or "").strip()
        if setup or payoff:
            pair_entries.append({
                "text": setup[:300],
                "context": payoff[:500],
                "type": "foreshadowing_pair",
                "score": pair.get("score", 0),
            })
    dims["twists"] = _merge_dimension_entries(twist_entries, pair_entries, limit=dimension_limit)
    return dims


def _merge_dimension_entries(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        context = str(item.get("context") or "").strip()
        key = re.sub(r"\s+", " ", f"{text}\n{context}")[:220]
        if not text or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _method_dimensions_from_analysis(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return {}
    merged: dict[str, list[dict[str, Any]]] = {}
    for anchor in payload.get("results") or []:
        if not isinstance(anchor, dict):
            continue
        dimensions = anchor.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            continue
        for name, entries in dimensions.items():
            if str(name).startswith(METHOD_DIMENSION_PREFIX) and isinstance(entries, list):
                merged.setdefault(str(name), []).extend(item for item in entries if isinstance(item, dict))
    return merged


def _dimensions_equal(left: dict[str, list[dict[str, Any]]], right: dict[str, list[dict[str, Any]]]) -> bool:
    left_keys = {k for k, v in left.items() if isinstance(v, list)}
    right_keys = {k for k, v in right.items() if isinstance(v, list)}
    if left_keys != right_keys:
        return False
    for key in sorted(left_keys):
        left_payload = json.dumps(left.get(key) or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        right_payload = json.dumps(right.get(key) or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if left_payload != right_payload:
            return False
    return True


def _refresh_anchor_analysis_stats(payload: dict[str, Any]) -> None:
    coverage: dict[str, int] = {}
    total = 0
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    for anchor in results:
        if not isinstance(anchor, dict):
            continue
        dimensions = anchor.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            continue
        anchor_total = 0
        for name, entries in dimensions.items():
            count = len(entries) if isinstance(entries, list) else 0
            coverage[str(name)] = coverage.get(str(name), 0) + count
            anchor_total += count
        anchor["total_matches"] = anchor_total
    total = sum(coverage.values())
    payload["total_anchors"] = len(results)
    payload["dimension_coverage"] = dict(sorted(coverage.items()))
    payload["total_dimension_matches"] = total


METHOD_DIMENSION_RULES: dict[str, dict[str, Any]] = {
    "method_opening_promise": {
        "label": "开篇承诺/黄金前三章",
        "keywords": ["开头", "第一", "第一次", "主角", "目标", "阻碍", "秘密", "危险", "决定", "命运"],
        "zone": "early",
        "use": "用于学习开篇如何建立处境、欲望、阻碍、类型承诺和继续阅读钩子。",
    },
    "method_motivation_chain": {
        "label": "动机-信息-收益链",
        "keywords": ["为了", "必须", "想要", "不能", "只要", "代价", "机会", "原因", "知道", "明白", "选择", "决定"],
        "use": "用于学习人物行动如何绑定欲望、信息边界、收益和代价。",
    },
    "method_chapter_function": {
        "label": "章节/场次功能",
        "keywords": ["铺垫", "线索", "冲突", "对峙", "战斗", "转折", "揭示", "真相", "恢复", "准备", "告别"],
        "use": "用于学习章节承担布局、冲突、过渡、恢复、揭示等功能时的节奏差异。",
    },
    "method_reader_experience": {
        "label": "读者体验",
        "keywords": ["突然", "没想到", "竟然", "终于", "压", "疼", "笑", "沉默", "停顿", "盯着", "握住", "松开"],
        "use": "用于学习压力、延迟、释放、余味和情绪落点。",
    },
    "method_reference_decomposition": {
        "label": "可拆解机制",
        "keywords": ["他看", "她看", "说道", "问道", "走到", "拿起", "放下", "抬头", "回头", "门口", "桌上"],
        "use": "用于拆解场景动作、对白推进、物件线索和信息释放机制，不复制具体桥段。",
    },
    "method_research_detail": {
        "label": "事实/行业细节",
        "keywords": ["年代", "月份", "公里", "分钟", "元", "票", "证", "局", "厂", "学校", "医院", "电话", "火车", "政策"],
        "use": "用于学习现实、历史、行业、制度细节如何服务可信度和剧情压力。",
    },
    "method_humanization": {
        "label": "去 AI 味/人味表达",
        "keywords": ["嗯", "啊", "吧", "喂", "算了", "没事", "别", "行了", "笑了", "骂", "低声", "嘀咕"],
        "use": "用于学习自然对白、停顿、不完整动作和生活化细节。",
    },
}


def _extract_method_dimensions(
    title: str,
    *,
    per_dimension_limit: int | None = None,
    fallback_limit: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Extract method-oriented dimensions from imported reference text.

    These dimensions complement the legacy five-dimensional corpus. They store
    short, source-backed examples that later modules can turn into mechanism
    cards; they are not injected into final prose as copyable snippets.
    """
    text = _read_work_novel_text(title)
    if not text:
        return {name: [] for name in METHOD_DIMENSION_RULES}
    dim_limit = _bounded_int(
        per_dimension_limit,
        env_name="WRITING_METHOD_DIMENSION_LIMIT",
        default=DEFAULT_METHOD_DIMENSION_LIMIT,
        minimum=8,
        maximum=160,
    )
    fallback_dim_limit = _bounded_int(
        fallback_limit,
        env_name="WRITING_METHOD_DIMENSION_FALLBACK_LIMIT",
        default=DEFAULT_METHOD_DIMENSION_FALLBACK_LIMIT,
        minimum=0,
        maximum=40,
    )
    paragraphs = _method_paragraphs(text)
    total = len(paragraphs)
    dims: dict[str, list[dict[str, Any]]] = {}
    for dim_name, rule in METHOD_DIMENSION_RULES.items():
        candidates: list[tuple[float, int, str, list[str]]] = []
        for idx, para in enumerate(paragraphs):
            if len(para) < 40 or len(para) > 900:
                continue
            zone = _paragraph_zone(idx, total)
            if rule.get("zone") and zone != rule.get("zone"):
                continue
            hits = [kw for kw in rule.get("keywords") or [] if kw in para]
            if not hits:
                continue
            density = len(hits) / max(1, len(para) / 180)
            dialogue_bonus = 0.5 if "“" in para or '"' in para else 0
            candidates.append((density + dialogue_bonus, idx, para, hits))
        selected = _spread_method_candidates(candidates, limit=dim_limit)
        if not selected and fallback_dim_limit > 0:
            fallback_candidates = _fallback_method_candidates(
                paragraphs,
                dim_name=dim_name,
                rule=rule,
                total=total,
                limit=fallback_dim_limit,
            )
            selected = _spread_method_candidates(fallback_candidates, limit=fallback_dim_limit)
        dims[dim_name] = [
            {
                "text": para[:500],
                "context": para[:900],
                "zone": _paragraph_zone(idx, total),
                "matched_keywords": hits[:12],
                "method_label": rule.get("label", ""),
                "method_use": rule.get("use", ""),
                **({"fallback_reason": "low_keyword_method_sample"} if _is_fallback_hits(hits) else {}),
            }
            for _score, idx, para, hits in selected
        ]
    return dims


def _read_work_novel_text(title: str) -> str:
    path = WORK_NOVELS_DIR / title / "novel.txt"
    if not path.exists():
        return ""
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030", "gbk", "gb2312"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _method_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    seen: set[str] = set()

    def add(clean: str) -> None:
        clean = re.sub(r"\s+", " ", clean or "").strip()
        if len(clean) < 30:
            return
        key = clean[:120]
        if key in seen:
            return
        seen.add(key)
        paragraphs.append(clean)

    for para in re.split(r"\n\s*\n+", text or ""):
        clean = re.sub(r"\s+", " ", para).strip()
        add(clean)

    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    chunk: list[str] = []
    chunk_len = 0
    for line in lines:
        chunk.append(line)
        chunk_len += len(line)
        if chunk_len >= 260:
            add(" ".join(chunk))
            chunk = []
            chunk_len = 0
    if chunk:
        add(" ".join(chunk))
    return paragraphs


def _bounded_int(value: int | None, *, env_name: str, default: int, minimum: int, maximum: int) -> int:
    raw: Any = value if value is not None else os.getenv(env_name)
    try:
        number = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _fallback_method_candidates(
    paragraphs: list[str],
    *,
    dim_name: str,
    rule: dict[str, Any],
    total: int,
    limit: int,
) -> list[tuple[float, int, str, list[str]]]:
    if not paragraphs or limit <= 0:
        return []
    fallback_keywords = _fallback_keywords_for_dimension(dim_name)
    candidates: list[tuple[float, int, str, list[str]]] = []
    for idx, para in enumerate(paragraphs):
        if len(para) < 40 or len(para) > 1200:
            continue
        zone = _paragraph_zone(idx, total)
        if rule.get("zone") and zone != rule.get("zone"):
            continue
        hits = [kw for kw in fallback_keywords if kw in para]
        dialogue_bonus = 0.5 if "“" in para or '"' in para or "说" in para else 0
        length_bonus = 0.2 if 80 <= len(para) <= 700 else 0
        if hits:
            score = len(hits) + dialogue_bonus + length_bonus
        elif dim_name == "method_opening_promise" and zone == "early":
            score = 0.4 + length_bonus
        else:
            continue
        candidates.append((score, idx, para, [f"fallback:{hit}" for hit in hits[:8]] or ["fallback:zone_sample"]))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[: max(limit * 3, limit)]


def _fallback_keywords_for_dimension(dim_name: str) -> list[str]:
    return {
        "method_opening_promise": ["主角", "一个", "这", "那", "要", "想", "离开", "遇见", "问题", "事情"],
        "method_motivation_chain": ["要", "想", "得", "因为", "所以", "决定", "知道", "明白", "不能", "为了"],
        "method_chapter_function": ["说", "问", "走", "看", "突然", "后来", "可是", "于是", "这时", "最后"],
        "method_reader_experience": ["突然", "沉默", "笑", "哭", "心", "眼", "声音", "害怕", "高兴", "难过"],
        "method_reference_decomposition": ["说", "问", "看", "拿", "走", "回", "门", "手", "眼", "桌"],
        "method_research_detail": ["年", "月", "天", "公里", "分钟", "国王", "星球", "学校", "城市", "地方"],
        "method_humanization": ["说", "问", "笑", "哭", "低声", "沉默", "手", "眼", "嗯", "啊"],
    }.get(dim_name, [])


def _is_fallback_hits(hits: list[str]) -> bool:
    return any(str(hit).startswith("fallback:") for hit in hits)


def _paragraph_zone(index: int, total: int) -> str:
    if total <= 0:
        return "unknown"
    ratio = index / max(1, total)
    if ratio < 0.2:
        return "early"
    if ratio < 0.75:
        return "middle"
    return "late"


def _spread_method_candidates(
    candidates: list[tuple[float, int, str, list[str]]],
    *,
    limit: int,
) -> list[tuple[float, int, str, list[str]]]:
    if not candidates:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected: list[tuple[float, int, str, list[str]]] = []
    seen_text: set[str] = set()
    seen_bucket: set[int] = set()
    for item in candidates:
        _score, idx, para, _hits = item
        key = para[:80]
        bucket = idx // 80
        if key in seen_text:
            continue
        if bucket in seen_bucket and len(selected) < limit // 2:
            continue
        seen_text.add(key)
        seen_bucket.add(bucket)
        selected.append(item)
        if len(selected) >= limit:
            break
    if len(selected) < min(limit, len(candidates)):
        for item in candidates:
            if item in selected:
                continue
            key = item[2][:80]
            if key in seen_text:
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
    selected.sort(key=lambda item: item[1])
    return selected


def _update_novel_list(title: str, *, source: Path) -> None:
    NOVEL_LIST.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_json(NOVEL_LIST)
    if not isinstance(payload, dict):
        payload = {"novels": []}
    novels = payload.get("novels")
    if not isinstance(novels, list):
        novels = []
    existing = {str(item.get("title") or "").strip() for item in novels if isinstance(item, dict)}
    if title not in existing:
        novels.append({
            "title": title,
            "author": "用户导入",
            "genre": "参考小说",
            "score": "",
            "tags": ["用户导入", "本地TXT"],
            "source": _rel(source),
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        })
    payload["novels"] = novels
    payload["total"] = len(novels)
    NOVEL_LIST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_json(
    args: list[str],
    *,
    cwd: Path,
    timeout: int,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    stdout = _run_text(args, cwd=cwd, timeout=timeout, env_overrides=env_overrides)
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            return data if isinstance(data, dict) else {"data": data}
        except json.JSONDecodeError:
            continue
    return {}


def _run_text(
    args: list[str],
    *,
    cwd: Path,
    timeout: int,
    env_overrides: dict[str, str] | None = None,
) -> str:
    env = os.environ.copy()
    env["WRITING_ROOT"] = str(WRITING_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_overrides:
        env.update({str(key): str(value) for key, value in env_overrides.items()})
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"exit {proc.returncode}")[-2000:])
    return proc.stdout or ""


def _reference_dimension_env(dimension_limit: int | None = None) -> dict[str, str]:
    dim_limit = _bounded_int(
        dimension_limit,
        env_name=ANALYZER_REFERENCE_LIMIT_ENV,
        default=DEFAULT_REFERENCE_DIMENSION_LIMIT,
        minimum=8,
        maximum=160,
    )
    return {
        ANALYZER_REFERENCE_LIMIT_ENV: str(dim_limit),
        ANALYZER_TWIST_PAIR_LIMIT_ENV: str(dim_limit),
        "WRITING_METHOD_DIMENSION_LIMIT": str(dim_limit),
    }


def _python() -> str:
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_filename(filename: str) -> str:
    name = Path(filename or "reference.txt").name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name or "reference.txt"


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    match = re.search(r"《([^》]+)》", stem)
    title = match.group(1).strip() if match else stem
    title = re.sub(r"[\[(（].*?[\])）]", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    return _safe_path_part(title or "未命名参考小说")


def _unique_title(title: str) -> str:
    existing = set()
    payload = _read_json(NOVEL_LIST)
    novels = payload.get("novels") if isinstance(payload, dict) else []
    for item in novels if isinstance(novels, list) else []:
        if isinstance(item, dict):
            name = str(item.get("title") or "").strip()
            if name:
                existing.add(name)
    if REFERENCE_NOVELS_DIR.is_dir():
        existing.update(path.stem for path in REFERENCE_NOVELS_DIR.glob("*.txt") if path.is_file())
    if WORK_NOVELS_DIR.is_dir():
        existing.update(path.name for path in WORK_NOVELS_DIR.iterdir() if path.is_dir())
    if EXTRACTED_DIR.is_dir():
        existing.update(path.name for path in EXTRACTED_DIR.iterdir() if path.is_dir())
    if title not in existing:
        return title
    for idx in range(2, 1000):
        candidate = _safe_path_part(f"{title}_{idx}")
        if candidate not in existing:
            return candidate
    raise RuntimeError(f"无法生成不冲突标题：{title}")


def _safe_path_part(value: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return clean[:80] or "未命名参考小说"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for idx in range(2, 1000):
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成不冲突文件名：{path}")


def _unique_work_dir(title: str) -> Path:
    base = _safe_path_part(title)
    for idx in range(2, 1000):
        candidate = WORK_NOVELS_DIR / f"{base}_{idx}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成不冲突工作目录：{title}")


def _emit(progress: Progress | None, stage: str, label: str, status: str, details: dict[str, Any] | None = None) -> None:
    if progress:
        progress(stage, label, status, details or {})


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return str(path)
