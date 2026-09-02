from __future__ import annotations

import hashlib
from typing import Any


def _text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def summarize_state_delta(node: str, delta: Any) -> dict[str, Any]:
    if not isinstance(delta, dict):
        return {"node": node, "type": type(delta).__name__, "preview": str(delta)[:160]}
    summary: dict[str, Any] = {"node": node, "keys": sorted(str(k) for k in delta.keys())}
    for key, value in delta.items():
        summary[str(key)] = _summarize_value(value)
    return summary


def _summarize_value(value: Any) -> Any:
    if isinstance(value, str):
        return {"type": "str", "chars": len(value), "hash": _text_hash(value)}
    if isinstance(value, list):
        item = {"type": "list", "count": len(value)}
        if value and isinstance(value[0], dict):
            item["first_keys"] = sorted(str(k) for k in value[0].keys())
        return item
    if isinstance(value, dict):
        result: dict[str, Any] = {"type": "dict", "keys": sorted(str(k) for k in value.keys())[:20]}
        for name in ("ok", "level", "passed", "blocking_count"):
            if name in value:
                result[name] = value[name]
        if "draft" in value:
            result["draft"] = _summarize_value(value.get("draft"))
        return result
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__, "preview": str(value)[:160]}
