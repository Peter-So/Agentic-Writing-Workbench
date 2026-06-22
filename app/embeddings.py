from __future__ import annotations

from typing import Iterable

import httpx


class SidecarEmbeddings:
    """Small HTTP client for the sentence-transformers sidecar."""

    def __init__(self, endpoint: str, timeout: float = 60.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
        payload = {"texts": list(texts), "normalize": True}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self.endpoint, json=payload)
            response.raise_for_status()
            return response.json()["embeddings"]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def health(self) -> dict:
        base = self.endpoint.rsplit("/", 1)[0]
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{base}/health")
            response.raise_for_status()
            return response.json()

