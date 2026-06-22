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
SEMANTIC_SEARCH = NOVEL_ACQ_DIR / "semantic-search.py"
MAX_REFERENCE_NOVEL_BYTES = 120 * 1024 * 1024


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
    - run local five-dimension extraction;
    - synthesize anchor_analysis.json so existing retrieval scripts can consume it;
    - rebuild the TF-IDF semantic index.
    """
    warnings: list[dict[str, str]] = []
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
        analysis_result = _run_json([_python(), str(ANALYZER), title], cwd=NOVEL_ACQ_DIR, timeout=900)
        if analysis_result.get("error"):
            raise RuntimeError(str(analysis_result["error"]))
        _emit(progress, "reference_import_analyze", "五维本地抽取", "done", analysis_result)
    except Exception as exc:
        warnings.append({"stage": "reference_import_analyze", "message": f"{type(exc).__name__}: {exc}"})
        _emit(progress, "reference_import_analyze", "五维本地抽取", "warn", {"warning": warnings[-1]["message"]})

    _emit(progress, "reference_import_five_dim", "写入五维库", "running")
    try:
        anchor_analysis = _synthesize_anchor_analysis(title)
        _emit(progress, "reference_import_five_dim", "写入五维库", "done", {
            "segments": anchor_analysis.get("total_dimension_matches", 0),
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
        "warnings": warnings,
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


def _synthesize_anchor_analysis(title: str) -> dict[str, Any]:
    out_dir = EXTRACTED_DIR / title
    out_dir.mkdir(parents=True, exist_ok=True)
    dims: dict[str, list[dict[str, Any]]] = {}
    for name in ("scenes", "psychology", "characters"):
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
        dims[name] = entries

    twists_payload = _read_json(out_dir / "twists.json")
    twists = []
    for item in (twists_payload.get("samples") if isinstance(twists_payload, dict) else []) or []:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            twists.append({"text": str(item["text"]).strip()[:500], "type": item.get("type", "")})

    pairs_payload = _read_json(out_dir / "foreshadowing_pairs.json")
    for pair in (pairs_payload.get("pairs") if isinstance(pairs_payload, dict) else []) or []:
        if not isinstance(pair, dict):
            continue
        setup = ((pair.get("setup") or {}).get("text") or "").strip()
        payoff = ((pair.get("payoff") or {}).get("text") or "").strip()
        if setup or payoff:
            twists.append({
                "text": setup[:300],
                "context": payoff[:500],
                "type": "foreshadowing_pair",
                "score": pair.get("score", 0),
            })
    dims["twists"] = twists
    dims.setdefault("intelligence", [])

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


def _run_json(args: list[str], *, cwd: Path, timeout: int) -> dict[str, Any]:
    stdout = _run_text(args, cwd=cwd, timeout=timeout)
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


def _run_text(args: list[str], *, cwd: Path, timeout: int) -> str:
    env = os.environ.copy()
    env["WRITING_ROOT"] = str(WRITING_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
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
