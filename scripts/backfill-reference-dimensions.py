from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.reference_importer import backfill_reference_dimensions


def main() -> None:
    parser = argparse.ArgumentParser(description="按统一上限回填参考小说基础维度与 method_* 创作方法维度")
    parser.add_argument(
        "--scope",
        choices=("all", "basic", "method"),
        default="all",
        help="回填范围：all=基础维度+method_*，basic=仅基础维度，method=仅method_*",
    )
    parser.add_argument("--rebuild-index", action="store_true", help="回填有变化后重建 TF-IDF 语义索引")
    parser.add_argument("--dimension-limit", type=int, default=None, help="每本书每个最终入库维度最多条数，默认 48")
    parser.add_argument("--method-fallback-limit", type=int, default=None, help="method_* 严格关键词无命中时，每个维度最多兜底条数，默认 8")
    args = parser.parse_args()
    result = backfill_reference_dimensions(
        scope=args.scope,
        rebuild_index=args.rebuild_index,
        dimension_limit=args.dimension_limit,
        method_fallback_limit=args.method_fallback_limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("warnings"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
