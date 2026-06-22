from __future__ import annotations

import re
from typing import Any


def analyze_provider_answers(answers: list[dict[str, Any]], limit: int = 12) -> dict[str, Any]:
    """Lightweight provider answer triangulation without another model call.

    The result is advisory: it helps the merge prompt see consensus, divergence,
    and reusable candidates before it starts fusing provider outputs.
    """
    entries: list[dict[str, Any]] = []
    for ans in answers or []:
        provider = ans.get("name") or ans.get("provider") or "provider"
        for line in _candidate_lines(ans.get("result") or ""):
            entries.append({
                "provider": provider,
                "text": line,
                "tokens": set(_terms(line)),
                "norm": _normalize(line),
            })

    groups: list[dict[str, Any]] = []
    for item in entries:
        matched = None
        for group in groups:
            if _similarity(item, group) >= 0.28:
                matched = group
                break
        if matched:
            matched["items"].append(item)
            matched["tokens"].update(item["tokens"])
            matched["norms"].append(item["norm"])
        else:
            groups.append({"tokens": set(item["tokens"]), "norms": [item["norm"]], "items": [item]})

    consensus = []
    divergences = []
    provider_count = len({(a.get("name") or a.get("provider") or "") for a in answers or []})
    for group in groups:
        providers = sorted({item["provider"] for item in group["items"]})
        texts = sorted((item["text"] for item in group["items"]), key=len, reverse=True)
        if len(providers) >= min(2, provider_count):
            consensus.append({
                "providers": providers,
                "text": texts[0],
                "support": len(providers),
            })
        else:
            divergences.append({
                "provider": providers[0] if providers else "provider",
                "text": texts[0] if texts else "",
            })

    adoptable = sorted(
        consensus + [{"providers": [item["provider"]], "text": item["text"], "support": 1} for item in divergences],
        key=lambda item: (int(item.get("support", 1)), len(item.get("text", ""))),
        reverse=True,
    )[:limit]
    unique_by_provider: dict[str, list[str]] = {}
    for item in divergences:
        unique_by_provider.setdefault(item["provider"], []).append(item["text"])

    return {
        "ok": True,
        "provider_count": provider_count,
        "candidate_count": len(entries),
        "consensus": consensus[:limit],
        "divergences": divergences[:limit],
        "adoptable_points": adoptable,
        "unique_by_provider": {k: v[:5] for k, v in unique_by_provider.items()},
    }


def format_provider_review_for_prompt(review: dict[str, Any]) -> str:
    if not review or not review.get("ok"):
        return ""
    lines = ["## Provider 交叉分析（程序归纳，供融合时参考）"]
    if review.get("consensus"):
        lines.append("### 共识")
        for item in review["consensus"][:8]:
            lines.append(f"- [{'/'.join(item.get('providers') or [])}] {item.get('text', '')}")
    if review.get("divergences"):
        lines.append("### 分歧 / 独特点")
        for item in review["divergences"][:8]:
            lines.append(f"- [{item.get('provider', '')}] {item.get('text', '')}")
    if review.get("adoptable_points"):
        lines.append("### 可采纳点")
        for item in review["adoptable_points"][:10]:
            providers = item.get("providers") or [item.get("provider", "")]
            lines.append(f"- [{'/'.join(providers)}] {item.get('text', '')}")
    return "\n".join(lines)


def _candidate_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"[\n。！？；;!?]+", text or ""):
        line = re.sub(r"^[\s\-*#>、0-9.）)]+", "", raw).strip()
        line = re.sub(r"\s+", " ", line)
        if 8 <= len(line) <= 220 and not _boilerplate(line):
            out.append(line)
    seen: set[str] = set()
    unique = []
    for line in out:
        key = line.lower()
        if key not in seen:
            seen.add(key)
            unique.append(line)
    return unique[:40]


def _terms(text: str) -> list[str]:
    terms: list[str] = []
    for part in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text or ""):
        if re.fullmatch(r"[A-Za-z0-9_]+", part):
            if len(part) >= 3:
                terms.append(part.lower())
            continue
        chunk = part.strip()
        if len(chunk) <= 2:
            terms.append(chunk)
            continue
        for size in (2, 3):
            for idx in range(0, len(chunk) - size + 1):
                token = chunk[idx:idx + size]
                if token not in _ZH_STOP_TERMS:
                    terms.append(token)
    return terms


def _similarity(item: dict[str, Any], group: dict[str, Any]) -> float:
    tokens = item["tokens"]
    group_tokens = group["tokens"]
    base = _jaccard(tokens, group_tokens)
    if tokens and group_tokens:
        containment = len(tokens & group_tokens) / max(1, min(len(tokens), len(group_tokens)))
        base = max(base, containment * 0.72)
    norm = item.get("norm", "")
    if norm:
        for other in group.get("norms") or []:
            if len(norm) >= 8 and (norm in other or other in norm):
                base = max(base, 0.9)
    return base


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？；：:、,.!?;()（）《》\"'“”‘’\-]+", "", (text or "").lower())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _boilerplate(line: str) -> bool:
    bad = ["以下是", "好的", "当然", "希望", "如果你需要", "可以进一步"]
    return any(token in line for token in bad)


_ZH_STOP_TERMS = {
    "一个", "一种", "这个", "那个", "可以", "通过", "需要", "进行", "故事", "剧本",
    "角色", "人物", "情节", "内容", "表达", "呈现", "应该", "不是", "没有",
}
