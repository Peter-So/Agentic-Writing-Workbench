from __future__ import annotations

import argparse
from pathlib import Path

from common import dump_json, dump_jsonl, ensure_dir, extract_chapter_spans, iter_text_files, now_iso, novel_id_for_path, read_text


def split_book(path: Path, output_dir: Path, write_text: bool = True) -> dict[str, object]:
    text = read_text(path)
    novel_id = novel_id_for_path(path)
    book_dir = output_dir / novel_id
    chapter_dir = book_dir / "chapters"
    ensure_dir(book_dir)
    rows: list[dict[str, object]] = []
    spans = extract_chapter_spans(text)

    for span in spans:
        chapter_index = int(span["chapter_index"])
        start_char = int(span["start_char"])
        end_char = int(span["end_char"])
        chapter_id = f"{novel_id}/ch{chapter_index:04d}"
        chapter_text = text[start_char:end_char].strip()
        text_path = chapter_dir / f"ch{chapter_index:04d}.txt"

        if write_text:
            chapter_dir.mkdir(parents=True, exist_ok=True)
            text_path.write_text(chapter_text, encoding="utf-8")

        rows.append(
            {
                "chapter_id": chapter_id,
                "novel_id": novel_id,
                "chapter_index": chapter_index,
                "title": span["title"],
                "file_path": str(path.resolve()),
                "text_path": str(text_path.resolve()) if write_text else "",
                "start_char": start_char,
                "end_char": end_char,
                "chapter_length": len(chapter_text),
                "created_at": now_iso(),
            }
        )
    book_manifest = {
        "novel_id": novel_id,
        "file_path": str(path.resolve()),
        "title": path.stem,
        "chapter_count": len(spans),
        "output_dir": str(book_dir.resolve()),
        "chapter_index_file": str((book_dir / "chapter_index.jsonl").resolve()),
        "status": "split",
    }
    return {"manifest": book_manifest, "chapters": rows, "book_dir": book_dir}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split novel files into chapter records.")
    parser.add_argument("--corpus-dir", required=True, help="Directory that contains novel text files.")
    parser.add_argument("--output-dir", required=True, help="Directory for per-book outputs.")
    parser.add_argument("--book-file", help="Optional single novel file to process.")
    parser.add_argument("--extensions", nargs="+", default=[".txt"], help="File extensions to include.")
    parser.add_argument("--no-write-text", action="store_true", help="Only emit index rows; do not write chapter text files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    corpus_dir = Path(args.corpus_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.book_file:
        path = Path(args.book_file)
        result = split_book(path, output_dir, write_text=not args.no_write_text)
        book_dir = Path(result["book_dir"])  # type: ignore[index]
        dump_json(book_dir / "manifest.json", result["manifest"])
        dump_jsonl(book_dir / "chapter_index.jsonl", result["chapters"])
        return

    for path in iter_text_files(corpus_dir, args.extensions):
        result = split_book(path, output_dir, write_text=not args.no_write_text)
        book_dir = Path(result["book_dir"])  # type: ignore[index]
        dump_json(book_dir / "manifest.json", result["manifest"])
        dump_jsonl(book_dir / "chapter_index.jsonl", result["chapters"])


if __name__ == "__main__":
    main()
