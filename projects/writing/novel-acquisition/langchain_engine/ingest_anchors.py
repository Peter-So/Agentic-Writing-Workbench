"""Ingest all anchor files into ChromaDB via sidecar services.

Usage:
    python3 ingest_anchors.py [--reset]

Connects to:
    - http://chroma:8000 (ChromaDB)
    - http://embedding:8001 (Embedding service)
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# --- Config ---
for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import NOVEL_ACQUISITION_DIR

ANCHOR_DIR = NOVEL_ACQUISITION_DIR / "anchors"
CHROMA_URL = os.environ.get("CHROMA_URL", "http://chroma:8000")
EMBEDDING_URL = os.environ.get("EMBEDDING_URL", "http://embedding:8001/embed")
COLLECTION_NAME = "five_dimensions"
BATCH_SIZE = 50  # documents per ChromaDB upsert batch
EMBED_BATCH_SIZE = 64  # texts per embedding call
VALID_DIMENSIONS = {"scenes", "psychology", "characters", "twists"}


def chroma_api(method: str, path: str, body=None):
    """Call ChromaDB REST API."""
    url = f"{CHROMA_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if e.fp else ""
        raise RuntimeError(f"ChromaDB {method} {path} -> {e.code}: {err_body}")


def get_embeddings(texts: list) -> list:
    """Get embeddings from sidecar in batches."""
    all_embs = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i:i + EMBED_BATCH_SIZE]
        payload = json.dumps({"texts": batch}).encode("utf-8")
        req = urllib.request.Request(
            EMBEDDING_URL, data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        all_embs.extend(result["embeddings"])
    return all_embs


def get_or_create_collection(name: str, reset: bool = False) -> str:
    """Get or create a ChromaDB collection, return its ID."""
    if reset:
        # Try delete first
        try:
            chroma_api("DELETE", f"/api/v2/tenants/default_tenant/databases/default_database/collections/{name}")
            print(f"  Deleted existing collection '{name}'")
        except Exception:
            pass

    # Create collection
    try:
        result = chroma_api("POST", "/api/v2/tenants/default_tenant/databases/default_database/collections", {
            "name": name,
            "metadata": {"hnsw:space": "cosine"},
            "get_or_create": True
        })
        coll_id = result.get("id", "")
        print(f"  Collection '{name}' ready (id={coll_id[:8]}...)")
        return coll_id
    except Exception as e:
        raise RuntimeError(f"Failed to create collection: {e}")


def upsert_batch(collection_id: str, ids: list, documents: list,
                 embeddings: list, metadatas: list):
    """Upsert a batch into ChromaDB."""
    chroma_api("POST", f"/api/v2/tenants/default_tenant/databases/default_database/collections/{collection_id}/upsert", {
        "ids": ids,
        "documents": documents,
        "embeddings": embeddings,
        "metadatas": metadatas
    })


def parse_anchors(filepath: Path) -> list:
    """Parse an anchor JSON file into (id, text, metadata) tuples."""
    book_name = filepath.stem
    with open(filepath, "r", encoding="utf-8") as f:
        anchors = json.load(f)

    records = []
    for anchor_idx, anchor in enumerate(anchors):
        label = anchor.get("label", f"anchor_{anchor_idx}")
        category = anchor.get("category", "")
        dims = anchor.get("dimensions", {})

        for dim_name, paragraphs in dims.items():
            if dim_name not in VALID_DIMENSIONS:
                continue
            if not isinstance(paragraphs, list):
                continue
            for para_idx, text in enumerate(paragraphs):
                if not text or not isinstance(text, str) or len(text.strip()) < 10:
                    continue
                doc_id = f"{book_name}_{anchor_idx}_{dim_name}_{para_idx}"
                metadata = {
                    "book": book_name,
                    "anchor_label": label,
                    "anchor_category": category,
                    "dimension": dim_name,
                    "anchor_idx": anchor_idx,
                    "para_idx": para_idx,
                    "text_length": len(text)
                }
                records.append((doc_id, text.strip(), metadata))
    return records


def main():
    reset = "--reset" in sys.argv
    print(f"=== 五维资料库 ChromaDB 入库 ===")
    print(f"Anchor目录: {ANCHOR_DIR}")
    print(f"ChromaDB: {CHROMA_URL}")
    print(f"Embedding: {EMBEDDING_URL}")
    print(f"Reset: {reset}")
    print()

    # Verify services
    heartbeat = chroma_api("GET", "/api/v2/heartbeat")
    print(f"✓ ChromaDB heartbeat: {heartbeat}")

    # Get/create collection
    coll_id = get_or_create_collection(COLLECTION_NAME, reset=reset)
    print()

    # Parse all anchor files
    anchor_files = sorted(ANCHOR_DIR.glob("*.json"))
    print(f"Found {len(anchor_files)} anchor files")

    all_records = []
    for af in anchor_files:
        records = parse_anchors(af)
        all_records.extend(records)
    print(f"Total records to ingest: {len(all_records)}")
    print()

    # Batch ingest
    total = len(all_records)
    ingested = 0
    start_time = time.time()

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = all_records[batch_start:batch_end]

        ids = [r[0] for r in batch]
        documents = [r[1] for r in batch]
        metadatas = [r[2] for r in batch]

        # Get embeddings
        embeddings = get_embeddings(documents)

        # Upsert to ChromaDB
        upsert_batch(coll_id, ids, documents, embeddings, metadatas)

        ingested += len(batch)
        elapsed = time.time() - start_time
        rate = ingested / elapsed if elapsed > 0 else 0
        eta = (total - ingested) / rate if rate > 0 else 0
        print(f"  [{ingested}/{total}] {ingested/total*100:.1f}% | "
              f"{rate:.1f} docs/s | ETA {eta:.0f}s")

    elapsed_total = time.time() - start_time
    print(f"\n✓ 入库完成: {ingested} docs in {elapsed_total:.1f}s")
    print(f"  Collection: {COLLECTION_NAME}")
    print(f"  Avg rate: {ingested/elapsed_total:.1f} docs/s")

    # Verify count
    try:
        count_resp = chroma_api("GET", f"/api/v2/tenants/default_tenant/databases/default_database/collections/{COLLECTION_NAME}/count")
        print(f"  Verified count: {count_resp}")
    except Exception as e:
        print(f"  Count check: {e}")


if __name__ == "__main__":
    main()
