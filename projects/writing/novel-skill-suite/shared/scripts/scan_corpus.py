from __future__ import annotations

import argparse
from pathlib import Path

from common import dump_json, extract_chapter_spans, iter_text_files, normalize_whitespace, now_iso, novel_id_for_path, read_text, slugify


def build_manifest(corpus_dir: Path, extensions: list[str]) -> dict[str, object]:
    books: list[dict[str, object]] = []
    for path in iter_text_files(corpus_dir, extensions):
        text = read_text(path)
        chapter_spans = extract_chapter_spans(text)
        books.append(
            {
                "novel_id": novel_id_for_path(path),
                "file_path": str(path.resolve()),
                "title": normalize_whitespace(path.stem),
                "author": "",
                "size_bytes": path.stat().st_size,
                "chapter_count": len(chapter_spans),
                "status": "scanned",
            }
        )
    return {
        "corpus_id": slugify(corpus_dir.name),
        "generated_at": now_iso(),
        "source_root": str(corpus_dir.resolve()),
        "books": books,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan a local novel corpus and build a manifest.")
    parser.add_argument("--corpus-dir", required=True, help="Directory that contains novel text files.")
    parser.add_argument("--output", required=True, help="Output manifest JSON path.")
    parser.add_argument("--extensions", nargs="+", default=[".txt"], help="File extensions to include.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    corpus_dir = Path(args.corpus_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(corpus_dir, args.extensions)
    dump_json(output, manifest)


if __name__ == "__main__":
    main()
