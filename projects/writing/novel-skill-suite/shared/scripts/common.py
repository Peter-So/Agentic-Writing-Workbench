from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

CHAPTER_PATTERNS = [
    re.compile(r"^\s*第[0-9一二三四五六七八九十百千零两]+[章节回卷部].*$"),
    re.compile(r"^\s*chapter\s+\d+.*$", re.IGNORECASE),
    re.compile(r"^\s*ch\.?\s*\d+.*$", re.IGNORECASE),
    re.compile(r"^\s*正文[：: ]*第?[0-9一二三四五六七八九十百千零两]+[章节回卷部]?.*$"),
]

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp936")


@dataclass(frozen=True)
class EvidenceRef:
    novel_id: str
    file_path: str
    chapter_id: str
    start_char: int
    end_char: int
    excerpt: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "unknown"


def novel_id_for_path(path: Path) -> str:
    base = slugify(path.stem)
    digest = hashlib.blake2s(str(path.resolve()).encode("utf-8"), digest_size=4).hexdigest()
    return f"{base}-{digest}"


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def iter_text_files(corpus_dir: Path, extensions: Iterable[str] = (".txt",)) -> Iterator[Path]:
    suffixes = {ext.lower() for ext in extensions}
    for path in sorted(corpus_dir.iterdir(), key=lambda p: p.name):
        if path.is_file() and path.suffix.lower() in suffixes:
            yield path


def read_text(path: Path) -> str:
    last_error: Exception | None = None
    for encoding in TEXT_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise UnicodeDecodeError(
        "text",
        b"",
        0,
        1,
        f"Unable to decode {path} with known encodings: {last_error}",
    )


def chapter_heading_match(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return any(pattern.match(stripped) for pattern in CHAPTER_PATTERNS)


def extract_chapter_spans(text: str) -> list[dict[str, object]]:
    lines = text.splitlines(keepends=True)
    spans: list[dict[str, object]] = []
    offsets: list[int] = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    heading_indexes = [
        index for index, line in enumerate(lines) if chapter_heading_match(line)
    ]

    if not heading_indexes:
        return [
            {
                "chapter_index": 1,
                "title": "全文",
                "start_char": 0,
                "end_char": len(text),
            }
        ]

    for position, heading_index in enumerate(heading_indexes):
        next_heading_index = (
            heading_indexes[position + 1] if position + 1 < len(heading_indexes) else len(lines)
        )
        start_char = offsets[heading_index]
        end_char = offsets[next_heading_index]
        title = normalize_whitespace(lines[heading_index].strip())
        spans.append(
            {
                "chapter_index": position + 1,
                "title": title,
                "start_char": start_char,
                "end_char": end_char,
            }
        )
    return spans


def chunk_text(text: str, chunk_size: int = 6000, overlap: int = 400) -> list[dict[str, object]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")

    chunks: list[dict[str, object]] = []
    start = 0
    index = 1
    text_length = len(text)
    while start < text_length:
        end = min(text_length, start + chunk_size)
        chunks.append(
            {
                "chunk_index": index,
                "start_char": start,
                "end_char": end,
                "text": text[start:end],
            }
        )
        if end >= text_length:
            break
        start = end - overlap
        index += 1
    return chunks


def dump_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def dump_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def as_record(value: object) -> dict[str, object]:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)  # type: ignore[arg-type]
    if isinstance(value, dict):
        return value
    raise TypeError(f"Unsupported record type: {type(value)!r}")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
