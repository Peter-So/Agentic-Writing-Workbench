from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from common import ensure_dir


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False))
        handle.write("\n")


def semantic_complete(book_dir: Path) -> bool:
    bundle = book_dir / "knowledge_bundle.json"
    chapters = book_dir / "semantic_chapters.jsonl"
    if not bundle.exists() or not chapters.exists() or chapters.stat().st_size == 0:
        return False
    try:
        data = json.loads(bundle.read_text(encoding="utf-8"))
    except Exception:
        return False
    manifest = data.get("semantic_manifest", {})
    return isinstance(manifest, dict) and manifest.get("status") == "completed"


def run_one(script: Path, book_dir: Path, character_limit: int, scene_limit: int, log_dir: Path) -> dict[str, object]:
    log_path = log_dir / f"{book_dir.name}.semantic.log"
    started = now_iso()
    cmd = [
        sys.executable,
        str(script),
        "--book-dir",
        str(book_dir),
        "--character-limit",
        str(character_limit),
        "--scene-limit",
        str(scene_limit),
    ]
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"[{started}] semantic start {book_dir}\n")
        log.flush()
        try:
            process = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, check=False)
            returncode = process.returncode
        except Exception:
            returncode = 997
            log.write(traceback.format_exc())
        log.write(f"[{now_iso()}] semantic exit code {returncode}\n")
    return {
        "book_dir": str(book_dir.resolve()),
        "book": book_dir.name,
        "started_at": started,
        "finished_at": now_iso(),
        "returncode": returncode,
        "log_file": str(log_path.resolve()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch semantic extraction for processed novel directories.")
    parser.add_argument("--output-root", required=True, help="Root directory containing per-book outputs.")
    parser.add_argument("--workers", type=int, default=6, help="Number of concurrent semantic workers.")
    parser.add_argument("--character-limit", type=int, default=120, help="Max character candidates per book.")
    parser.add_argument("--scene-limit", type=int, default=2, help="Max scenes per chapter.")
    parser.add_argument("--resume", action="store_true", help="Skip completed semantic outputs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_root = Path(args.output_root)
    state_dir = ensure_dir(output_root / "_semantic_state")
    log_dir = ensure_dir(output_root / "_semantic_logs")
    summary_path = state_dir / "summary.json"
    completed_path = state_dir / "completed.jsonl"
    failed_path = state_dir / "failed.jsonl"
    script = Path(__file__).resolve().parent / "semantic_extract_single.py"

    book_dirs = sorted(
        [p for p in output_root.iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "knowledge_bundle.json").exists()],
        key=lambda p: p.name,
    )
    pending = [p for p in book_dirs if not (args.resume and semantic_complete(p))]
    summary = {
        "output_root": str(output_root.resolve()),
        "workers": args.workers,
        "total_books": len(book_dirs),
        "pending_count": len(pending),
        "completed_count": len(book_dirs) - len(pending),
        "failed_count": 0,
        "started_at": now_iso(),
        "status": "running",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    success = 0
    failed = 0
    try:
        with cf.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(run_one, script, book_dir, args.character_limit, args.scene_limit, log_dir): book_dir
                for book_dir in pending
            }
            for future in cf.as_completed(futures):
                book_dir = futures[future]
                try:
                    result = future.result()
                except Exception:
                    result = {
                        "book_dir": str(book_dir.resolve()),
                        "book": book_dir.name,
                        "started_at": "",
                        "finished_at": now_iso(),
                        "returncode": 998,
                        "log_file": "",
                        "error": traceback.format_exc(),
                    }
                if result.get("returncode") == 0 and semantic_complete(book_dir):
                    success += 1
                    append_jsonl(completed_path, result)
                else:
                    failed += 1
                    append_jsonl(failed_path, result)
                summary["completed_count"] = len(book_dirs) - len(pending) + success
                summary["failed_count"] = failed
                summary["last_update_at"] = now_iso()
                summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except BaseException:
        summary["status"] = "failed"
        summary["error"] = traceback.format_exc()
        summary["finished_at"] = now_iso()
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        raise

    summary["status"] = "completed" if failed == 0 else "completed_with_failures"
    summary["finished_at"] = now_iso()
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

