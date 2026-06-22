from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from app.config import ROOT, load_runtime_config
from app.novel_context import DEFAULT_NOVEL_ID, WRITING_ROOT, normalize_novel_id

# RAG 增量记忆：把"用户确认"的创作产出向量化写入产出库 writing_outputs（与参考库 writing_docs 分离），
# 供后续章节语义召回。sidecar 不可用时降级为写本地产出语料目录，纳入 TF-IDF 重建。
OUTPUT_COLLECTION = os.getenv("WRITING_OUTPUTS_COLLECTION", "writing_outputs")
# 降级语料目录：无 sidecar 时产出落这里，semantic_search.build_index 纳入。
FALLBACK_CORPUS_DIR = Path(
    os.getenv("WRITING_OUTPUTS_CORPUS")
    or (WRITING_ROOT / "novel-acquisition" / "outputs-corpus")
)

_lock = threading.Lock()
_clients: dict[str, Any] = {}


def _get_clients():
    """惰性构造 ChromaHttp + SidecarEmbeddings；失败返回 (None, None) 走降级。"""
    if _clients:
        return _clients.get("chroma"), _clients.get("embed")
    with _lock:
        if _clients:
            return _clients.get("chroma"), _clients.get("embed")
        try:
            from app.chroma_client import ChromaHttp
            from app.embeddings import SidecarEmbeddings
            cfg = load_runtime_config()
            if not (cfg.chroma_url and cfg.embedding_url):
                raise RuntimeError("vector sidecar disabled")
            chroma = ChromaHttp(
                base_url=cfg.chroma_url, tenant=cfg.chroma_tenant, database=cfg.chroma_database,
            )
            embed = SidecarEmbeddings(cfg.embedding_url)
            _clients["chroma"] = chroma
            _clients["embed"] = embed
        except Exception:
            _clients["chroma"] = None
            _clients["embed"] = None
        return _clients.get("chroma"), _clients.get("embed")


def sidecar_available() -> bool:
    """检测 Chroma + Embedding sidecar 是否可用。"""
    chroma, embed = _get_clients()
    if chroma is None or embed is None:
        return False
    try:
        chroma.heartbeat()
        embed.health()
        return True
    except Exception:
        return False


def _segments(text: str, max_len: int = 600) -> list[str]:
    """把正文按空行/段落切成可召回单元，过短的合并，过长的截断。"""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    out: list[str] = []
    buf = ""
    for p in parts:
        if len(buf) + len(p) <= max_len:
            buf = f"{buf}\n{p}" if buf else p
        else:
            if buf:
                out.append(buf)
            buf = p[:max_len] if len(p) > max_len else p
    if buf:
        out.append(buf)
    return out


def _units_for(kind: str, chapter: int | None, text: str, summary: dict | None,
               novel_id: str | None = None) -> list[dict]:
    """把一次产出拆成入库单元：正文分段；摘要的伏笔/事件等各自成条。"""
    ch = chapter or 0
    nid = normalize_novel_id(novel_id)
    units: list[dict] = []
    if kind == "summary" and summary:
        for field in ("open_threads", "resolved", "events", "character_changes", "facts"):
            for i, item in enumerate(summary.get(field) or []):
                if str(item).strip():
                    units.append({"id": f"{nid}:summary:{ch}:{field}:{i}", "doc": str(item).strip(),
                                  "meta": {"type": "summary", "field": field, "chapter": ch, "novel_id": nid}})
    else:
        for i, seg in enumerate(_segments(text)):
            units.append({"id": f"{nid}:{kind}:{ch}:{i}", "doc": seg,
                          "meta": {"type": kind, "chapter": ch, "novel_id": nid}})
    return units


def index_confirmed(track: str, kind: str, chapter: int | None, text: str = "",
                    summary: dict | None = None, novel_id: str | None = None) -> dict[str, Any]:
    """把用户确认的产出增量入库。kind: prose|summary|setting。

    有 sidecar → embed + upsert 到 writing_outputs；无 sidecar → 写本地降级语料目录。
    全程容错：失败不抛出，返回状态供日志。
    """
    nid = normalize_novel_id(novel_id)
    units = _units_for(kind, chapter, text, summary, nid)
    if not units:
        return {"ok": True, "indexed": 0, "mode": "noop"}
    for u in units:
        u["meta"]["track"] = "create" if track == "create" else "normal"
    if sidecar_available():
        try:
            chroma, embed = _get_clients()
            coll = chroma.get_or_create_collection(OUTPUT_COLLECTION)
            cid = coll.get("id") or coll.get("collection_id") or OUTPUT_COLLECTION
            vectors = embed.embed_documents([u["doc"] for u in units])
            chroma.upsert(
                cid,
                ids=[u["id"] for u in units],
                embeddings=vectors,
                documents=[u["doc"] for u in units],
                metadatas=[u["meta"] for u in units],
            )
            return {"ok": True, "indexed": len(units), "mode": "vector"}
        except Exception as exc:
            # 向量入库失败 → 落降级语料，不阻断
            _write_fallback(units)
            return {"ok": False, "indexed": len(units), "mode": "fallback", "error": str(exc)}
    _write_fallback(units)
    return {"ok": True, "indexed": len(units), "mode": "fallback"}


def _write_fallback(units: list[dict]) -> None:
    """无 sidecar：把产出单元追加到本地语料目录（JSON），供 TF-IDF 重建纳入。"""
    try:
        FALLBACK_CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        path = FALLBACK_CORPUS_DIR / "confirmed_outputs.json"
        existing = {}
        if path.exists():
            try:
                existing = {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
            except Exception:
                existing = {}
        for u in units:
            existing[u["id"]] = {"id": u["id"], "text": u["doc"], "meta": u["meta"]}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(list(existing.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def query_outputs(query: str, n_results: int = 5, novel_id: str | None = None) -> list[dict[str, Any]]:
    """召回产出库（写后续章节时召回既往已确认内容）。

    有 sidecar 时走 Chroma 向量召回；无 sidecar 时直接查询本地降级语料，
    避免"已写入 fallback，但下一轮不能即时使用"。
    """
    if not (query or "").strip():
        return []
    nid = normalize_novel_id(novel_id)
    if not sidecar_available():
        return _query_fallback(query, n_results=n_results, novel_id=nid)
    try:
        chroma, embed = _get_clients()
        coll = chroma.get_or_create_collection(OUTPUT_COLLECTION)
        cid = coll.get("id") or coll.get("collection_id") or OUTPUT_COLLECTION
        vec = embed.embed_query(query)
        res = chroma.query(cid, vec, n_results=max(n_results * 3, n_results))
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        out = []
        for doc, meta, dist in zip(docs, metas, dists):
            meta = meta or {}
            if meta.get("novel_id", DEFAULT_NOVEL_ID) != nid:
                continue
            out.append({"text": doc, "meta": meta,
                        "score": round(1 - float(dist), 3) if dist is not None else None})
        if out:
            return out[:n_results]
        return _query_fallback(query, n_results=n_results, novel_id=nid)
    except Exception:
        return _query_fallback(query, n_results=n_results, novel_id=nid)


def _query_fallback(query: str, n_results: int, novel_id: str) -> list[dict[str, Any]]:
    """Lightweight local recall over confirmed_outputs.json when vector sidecars are unavailable."""
    path = FALLBACK_CORPUS_DIR / "confirmed_outputs.json"
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    terms = _query_terms(query)
    scored: list[dict[str, Any]] = []
    for record in records if isinstance(records, list) else []:
        meta = record.get("meta") or {}
        if meta.get("novel_id", DEFAULT_NOVEL_ID) != novel_id:
            continue
        text = str(record.get("text") or "")
        score = _fallback_score(text, terms)
        if score <= 0:
            continue
        scored.append({
            "text": text,
            "meta": meta,
            "score": round(score, 3),
            "source": "fallback_corpus",
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:n_results]


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", query or "")
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        value = term.lower()
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _fallback_score(text: str, terms: list[str]) -> float:
    if not text or not terms:
        return 0.0
    haystack = text.lower()
    hits = sum(1 for term in terms if term in haystack)
    if hits <= 0:
        return 0.0
    density = hits / max(len(terms), 1)
    return density + min(len(text), 800) / 8000
