from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from common import ensure_dir
from writer_common import build_book_asset, build_corpus_playbook, load_json


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def asset_complete(book_dir: Path, writer_root: Path) -> bool:
    out_dir = writer_root / book_dir.name
    return (out_dir / "book_asset.json").exists() and (out_dir / "book_asset.md").exists()


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False))
        handle.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch build writer assets from semantic outputs.")
    parser.add_argument("--semantic-root", required=True, help="Root directory containing processed novel outputs.")
    parser.add_argument("--writer-root", required=True, help="Output root for writer assets.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent workers.")
    parser.add_argument("--resume", action="store_true", help="Skip books that already have writer assets.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    semantic_root = Path(args.semantic_root)
    writer_root = ensure_dir(Path(args.writer_root))
    state_dir = ensure_dir(writer_root / "_state")
    log_dir = ensure_dir(writer_root / "_logs")
    summary_path = state_dir / "summary.json"
    completed_path = state_dir / "completed.jsonl"
    failed_path = state_dir / "failed.jsonl"

    book_dirs = sorted(
        [p for p in semantic_root.iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "knowledge_bundle.json").exists()],
        key=lambda p: p.name,
    )
    pending = [p for p in book_dirs if not (args.resume and asset_complete(p, writer_root))]
    summary = {
        "semantic_root": str(semantic_root.resolve()),
        "writer_root": str(writer_root.resolve()),
        "workers": args.workers,
        "book_count": len(book_dirs),
        "pending_count": len(pending),
        "completed_count": len(book_dirs) - len(pending),
        "failed_count": 0,
        "started_at": now_iso(),
        "status": "running",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    assets: list[dict[str, object]] = []
    success = 0
    failed = 0

    def worker(book_dir: Path) -> dict[str, object]:
        log_path = log_dir / f"{book_dir.name}.log"
        started = now_iso()
        try:
            asset = build_book_asset(book_dir, writer_root)
            result = {
                "novel_id": book_dir.name,
                "book_dir": str(book_dir.resolve()),
                "started_at": started,
                "finished_at": now_iso(),
                "returncode": 0,
                "log_file": str(log_path.resolve()),
            }
            with log_path.open("w", encoding="utf-8") as log:
                log.write(f"[{started}] built {book_dir}\n")
                log.write(f"[{now_iso()}] success\n")
            return {"result": result, "asset": asset}
        except Exception:
            with log_path.open("w", encoding="utf-8") as log:
                log.write(f"[{started}] failed {book_dir}\n")
                log.write(traceback.format_exc())
            return {
                "result": {
                    "novel_id": book_dir.name,
                    "book_dir": str(book_dir.resolve()),
                    "started_at": started,
                    "finished_at": now_iso(),
                    "returncode": 1,
                    "log_file": str(log_path.resolve()),
                    "error": traceback.format_exc(),
                },
                "asset": None,
            }

    try:
        with cf.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(worker, book_dir): book_dir for book_dir in pending}
            for future in cf.as_completed(futures):
                payload = future.result()
                result = payload["result"]
                asset = payload["asset"]
                if result["returncode"] == 0 and asset_complete(Path(result["book_dir"]), writer_root):
                    append_jsonl(completed_path, result)
                    success += 1
                    if asset:
                        assets.append(asset)
                else:
                    append_jsonl(failed_path, result)
                    failed += 1
                summary["completed_count"] = len(book_dirs) - len(pending) + success
                summary["failed_count"] = failed
                summary["last_update_at"] = now_iso()
                summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except BaseException:
        summary["status"] = "failed"
        summary["finished_at"] = now_iso()
        summary["error"] = traceback.format_exc()
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        raise

    all_assets: list[dict[str, object]] = []
    for book_dir in book_dirs:
        asset_path = writer_root / book_dir.name / "book_asset.json"
        if asset_path.exists():
            all_assets.append(load_json(asset_path))
    if all_assets:
        assets = all_assets
    build_corpus_playbook(assets, writer_root)

    summary["status"] = "completed" if failed == 0 else "completed_with_failures"
    summary["finished_at"] = now_iso()
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
