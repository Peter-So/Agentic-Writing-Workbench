from __future__ import annotations

import argparse
from pathlib import Path

from writer_common import build_book_asset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build writer-facing assets for one processed novel directory.")
    parser.add_argument("--book-dir", required=True, help="Per-book semantic output directory.")
    parser.add_argument("--output-root", required=True, help="Root directory for writer assets.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    book_dir = Path(args.book_dir)
    output_root = Path(args.output_root)
    build_book_asset(book_dir, output_root)


if __name__ == "__main__":
    main()

