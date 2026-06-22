from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from common import ensure_dir, iter_text_files, novel_id_for_path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_done(output_root: Path) -> set[str]:
    done: set[str] = set()
    state_file = output_root / "_state" / "completed.jsonl"
    if not state_file.exists():
        return done
    with state_file.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    done.add(json.loads(line)["novel_id"])
                except Exception:  # noqa: BLE001
                    continue
    return done


def has_complete_outputs(output_root: Path, novel_id: str) -> bool:
    book_dir = output_root / novel_id
    required = (
        "manifest.json",
        "chapter_index.jsonl",
        "knowledge_bundle.json",
        "chunk_index.jsonl",
    )
    return all((book_dir / name).exists() and (book_dir / name).stat().st_size > 0 for name in required)


def append_state(path: Path, record: dict[str, object]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False))
        handle.write("\n")


def run_one(script: Path, book_file: Path, output_root: Path, chunk_size: int, overlap: int, no_write_text: bool, log_dir: Path) -> dict[str, object]:
    novel_id = novel_id_for_path(book_file)
    log_path = log_dir / f"{novel_id}.log"
    cmd = [
        sys.executable,
        str(script),
        "--book-file",
        str(book_file),
        "--output-root",
        str(output_root),
        "--chunk-size",
        str(chunk_size),
        "--overlap",
        str(overlap),
    ]
    if no_write_text:
        cmd.append("--no-write-text")

    started = now_iso()
    with log_path.open("w", encoding="utf-8") as log_handle:
        log_handle.write(f"[{started}] start {book_file}\n")
        log_handle.flush()
        try:
            process = subprocess.run(
                cmd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            returncode = process.returncode
        except Exception:  # noqa: BLE001
            returncode = 997
            log_handle.write(traceback.format_exc())
        finished = now_iso()
        log_handle.write(f"[{finished}] exit code {returncode}\n")

    return {
        "novel_id": novel_id,
        "book_file": str(book_file.resolve()),
        "started_at": started,
        "finished_at": now_iso(),
        "returncode": returncode,
        "log_file": str(log_path.resolve()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch process a novel corpus with a thread pool.")
    parser.add_argument("--corpus-dir", required=True, help="Directory that contains novel text files.")
    parser.add_argument("--output-root", required=True, help="Root directory for per-book outputs.")
    parser.add_argument("--workers", type=int, default=10, help="Number of concurrent workers.")
    parser.add_argument("--chunk-size", type=int, default=6000, help="Character window for long chapters.")
    parser.add_argument("--overlap", type=int, default=400, help="Overlap between adjacent chunks.")
    parser.add_argument("--no-write-text", action="store_true", help="Only emit index rows; do not write chapter text files.")
    parser.add_argument("--resume", action="store_true", help="Skip books already recorded as completed.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    corpus_dir = Path(args.corpus_dir)
    output_root = ensure_dir(Path(args.output_root))
    log_dir = ensure_dir(output_root / "_logs")
    state_dir = ensure_dir(output_root / "_state")
    completed_path = state_dir / "completed.jsonl"
    summary_path = state_dir / "summary.json"

    script = Path(__file__).resolve().parent / "process_single_novel.py"
    books = list(iter_text_files(corpus_dir))
    completed = load_done(output_root) if args.resume else set()
    if args.resume:
        completed.update(
            novel_id_for_path(path)
            for path in books
            if has_complete_outputs(output_root, novel_id_for_path(path))
        )
    pending = [path for path in books if novel_id_for_path(path) not in completed]

    summary = {
        "corpus_dir": str(corpus_dir.resolve()),
        "output_root": str(output_root.resolve()),
        "workers": args.workers,
        "chunk_size": args.chunk_size,
        "overlap": args.overlap,
        "pending_count": len(pending),
        "completed_count": len(completed),
        "started_at": now_iso(),
        "status": "running",
        "failed_count": 0,
    }
    ensure_dir(summary_path.parent)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    success_count = 0
    failed_count = 0
    try:
        with cf.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    run_one,
                    script,
                    book_file,
                    output_root,
                    args.chunk_size,
                    args.overlap,
                    args.no_write_text,
                    log_dir,
                ): book_file
                for book_file in pending
            }

            for future in cf.as_completed(futures):
                book_file = futures[future]
                try:
                    result = future.result()
                except Exception:  # noqa: BLE001
                    result = {
                        "novel_id": novel_id_for_path(book_file),
                        "book_file": str(book_file.resolve()),
                        "started_at": "",
                        "finished_at": now_iso(),
                        "returncode": 998,
                        "log_file": "",
                        "error": traceback.format_exc(),
                    }
                if result.get("returncode") == 0 and has_complete_outputs(output_root, str(result["novel_id"])):
                    append_state(completed_path, result)
                    success_count += 1
                else:
                    append_state(state_dir / "failed.jsonl", result)
                    failed_count += 1

                summary["completed_count"] = len(completed) + success_count
                summary["failed_count"] = failed_count
                summary["last_update_at"] = now_iso()
                summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except BaseException:  # noqa: BLE001
        summary["finished_at"] = now_iso()
        summary["status"] = "failed"
        summary["error"] = traceback.format_exc()
        summary["completed_count"] = len(completed) + success_count
        summary["failed_count"] = failed_count
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        raise

    summary["finished_at"] = now_iso()
    summary["status"] = "completed" if failed_count == 0 else "completed_with_failures"
    summary["completed_count"] = len(completed) + success_count
    summary["failed_count"] = failed_count
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
