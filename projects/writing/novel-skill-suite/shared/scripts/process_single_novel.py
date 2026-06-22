from __future__ import annotations

import argparse
from pathlib import Path

from build_outputs import build_bundle
from common import dump_json, dump_jsonl, ensure_dir, novel_id_for_path
from split_chapters import split_book


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process one novel into isolated per-book outputs.")
    parser.add_argument("--book-file", required=True, help="Path to one novel text file.")
    parser.add_argument("--output-root", required=True, help="Root directory for per-book outputs.")
    parser.add_argument("--chunk-size", type=int, default=6000, help="Character window for long chapters.")
    parser.add_argument("--overlap", type=int, default=400, help="Overlap between adjacent chunks.")
    parser.add_argument("--no-write-text", action="store_true", help="Only emit index rows; do not write chapter text files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    book_file = Path(args.book_file)
    output_root = Path(args.output_root)
    novel_id = novel_id_for_path(book_file)
    book_dir = ensure_dir(output_root / novel_id)

    split_result = split_book(book_file, output_root, write_text=not args.no_write_text)
    chapter_index_path = book_dir / "chapter_index.jsonl"
    dump_json(book_dir / "manifest.json", split_result["manifest"])
    dump_jsonl(chapter_index_path, split_result["chapters"])

    bundle = build_bundle(chapter_index_path, args.chunk_size, args.overlap)
    dump_json(book_dir / "knowledge_bundle.json", bundle)
    dump_jsonl(book_dir / "chunk_index.jsonl", bundle["chunks"])


if __name__ == "__main__":
    main()

