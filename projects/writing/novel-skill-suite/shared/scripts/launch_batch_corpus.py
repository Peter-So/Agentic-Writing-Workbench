from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


OUTPUT_MARKERS = ("summary.json", "manifest.json", "chapter_index.jsonl", "knowledge_bundle.json", "chunk_index.jsonl")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the corpus batch processor in the background and wait for first output.")
    parser.add_argument("--corpus-dir", required=True, help="Directory that contains novel text files.")
    parser.add_argument("--output-root", required=True, help="Root directory for per-book outputs.")
    parser.add_argument("--workers", type=int, default=10, help="Number of concurrent workers.")
    parser.add_argument("--chunk-size", type=int, default=6000, help="Character window for long chapters.")
    parser.add_argument("--overlap", type=int, default=400, help="Overlap between adjacent chunks.")
    parser.add_argument("--no-write-text", action="store_true", help="Only emit index rows; do not write chapter text files.")
    parser.add_argument("--resume", action="store_true", help="Skip books already recorded as completed.")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between output checks.")
    return parser.parse_args()


def build_command(script: Path, args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(script),
        "--corpus-dir",
        str(Path(args.corpus_dir).resolve()),
        "--output-root",
        str(Path(args.output_root).resolve()),
        "--workers",
        str(args.workers),
        "--chunk-size",
        str(args.chunk_size),
        "--overlap",
        str(args.overlap),
    ]
    if args.no_write_text:
        cmd.append("--no-write-text")
    if args.resume:
        cmd.append("--resume")
    return cmd


def find_first_output(output_root: Path) -> Path | None:
    for marker in OUTPUT_MARKERS:
        candidates = sorted(output_root.rglob(marker))
        if candidates:
            return candidates[0]
    return None


def main() -> None:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve().parent / "batch_process_corpus.py"
    cmd = build_command(script, args)

    creationflags = 0
    startupinfo = None
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    child = subprocess.Popen(
        cmd,
        cwd=str(Path(args.corpus_dir).resolve()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
        close_fds=True,
    )

    while True:
        first_output = find_first_output(output_root)
        if first_output is not None:
            print(json.dumps({
                "pid": child.pid,
                "status": "started",
                "first_output": str(first_output),
                "output_root": str(output_root),
            }, ensure_ascii=False))
            return

        code = child.poll()
        if code is not None:
            print(json.dumps({
                "pid": child.pid,
                "status": "exited",
                "returncode": code,
                "output_root": str(output_root),
            }, ensure_ascii=False))
            raise SystemExit(code)

        time.sleep(max(0.1, args.poll_interval))


if __name__ == "__main__":
    main()

