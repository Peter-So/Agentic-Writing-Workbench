"""Custom LangChain Embeddings class wrapping the HTTP sidecar embedding service."""

from typing import List
import json
import os
import urllib.request

from langchain_core.embeddings import Embeddings

EMBEDDING_URL = os.environ.get("EMBEDDING_URL", "http://embedding:8001/embed")
BATCH_SIZE = 64  # max texts per request to avoid OOM on CPU


class SidecarEmbeddings(Embeddings):
    """LangChain-compatible embeddings via HTTP sidecar (bge-small-zh-v1.5, 512d)."""

    def __init__(self, url: str = EMBEDDING_URL, batch_size: int = BATCH_SIZE):
        self.url = url
        self.batch_size = batch_size

    def _call_service(self, texts: List[str]) -> List[List[float]]:
        """Call embedding sidecar in batches."""
        all_embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            payload = json.dumps({"texts": batch}).encode("utf-8")
            req = urllib.request.Request(
                self.url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            all_embeddings.extend(result["embeddings"])
        return all_embeddings

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of documents."""
        return self._call_service(texts)

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query."""
        return self._call_service([text])[0]
