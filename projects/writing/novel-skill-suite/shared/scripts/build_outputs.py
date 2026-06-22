from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import chunk_text, dump_json, dump_jsonl, ensure_dir


def load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_bundle(chapter_index_path: Path, chunk_size: int, overlap: int) -> dict[str, object]:
    chapter_rows = load_jsonl(chapter_index_path)
    chunks: list[dict[str, object]] = []

    for row in chapter_rows:
        text_path = str(row.get("text_path", ""))
        chapter_text = ""
        if text_path:
            chapter_text = Path(text_path).read_text(encoding="utf-8")
        elif "chapter_text" in row:
            chapter_text = str(row["chapter_text"])

        chunk_records = chunk_text(chapter_text, chunk_size=chunk_size, overlap=overlap) if chapter_text else []
        for chunk in chunk_records:
            chunks.append(
                {
                    "chapter_id": row["chapter_id"],
                    "novel_id": row["novel_id"],
                    "chapter_index": row["chapter_index"],
                    "chunk_index": chunk["chunk_index"],
                    "start_char": chunk["start_char"],
                    "end_char": chunk["end_char"],
                    "text": chunk["text"],
                }
            )

    return {
        "manifest": {
            "book_dir": str(chapter_index_path.parent.resolve()),
            "chapter_index": str(chapter_index_path.resolve()),
        },
        "chapters": chapter_rows,
        "characters": [],
        "arcs": [],
        "scenes": [],
        "systems": [],
        "style_notes": [],
        "chunks": chunks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build downstream extraction bundles from chapter indexes.")
    parser.add_argument("--chapter-index", required=True, help="Path to chapter_index.jsonl.")
    parser.add_argument("--output-dir", required=True, help="Directory for bundle outputs.")
    parser.add_argument("--chunk-size", type=int, default=6000, help="Character window for long chapters.")
    parser.add_argument("--overlap", type=int, default=400, help="Overlap between adjacent chunks.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    chapter_index_path = Path(args.chapter_index)
    output_dir = Path(args.output_dir)
    ensure_dir(output_dir)

    bundle = build_bundle(chapter_index_path, args.chunk_size, args.overlap)
    dump_json(output_dir / "knowledge_bundle.json", bundle)
    dump_jsonl(output_dir / "chunk_index.jsonl", bundle["chunks"])


if __name__ == "__main__":
    main()
