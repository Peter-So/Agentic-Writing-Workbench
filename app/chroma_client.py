from __future__ import annotations

import httpx


class ChromaHttp:
    """Minimal ChromaDB HTTP health client.

    Keep collection operations in project-specific tools once the indexing
    workflow is decided. This avoids pulling ChromaDB's Python package locally.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 20.0,
        tenant: str = "default_tenant",
        database: str = "default_database",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.tenant = tenant
        self.database = database

    def heartbeat(self) -> dict | str:
        with httpx.Client(timeout=self.timeout) as client:
            for path in ("/api/v2/heartbeat", "/api/v1/heartbeat"):
                try:
                    response = client.get(f"{self.base_url}{path}")
                    response.raise_for_status()
                    try:
                        return response.json()
                    except ValueError:
                        return response.text
                except httpx.HTTPError:
                    continue
        raise RuntimeError(f"ChromaDB heartbeat failed: {self.base_url}")

    def get_or_create_collection(self, name: str, metadata: dict | None = None) -> dict:
        payload = {"name": name, "get_or_create": True}
        if metadata:
            payload["metadata"] = metadata
        path = f"/api/v2/tenants/{self.tenant}/databases/{self.database}/collections"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()

    def upsert(
        self,
        collection_id: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict] | None = None,
    ) -> dict:
        path = f"/api/v2/tenants/{self.tenant}/databases/{self.database}/collections/{collection_id}/upsert"
        payload = {
            "ids": ids,
            "embeddings": embeddings,
            "documents": documents,
            "metadatas": metadatas or [{} for _ in ids],
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}{path}", json=payload)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return {"ok": True}

    def query(
        self,
        collection_id: str,
        query_embedding: list[float],
        n_results: int = 3,
        include: list[str] | None = None,
    ) -> dict:
        path = f"/api/v2/tenants/{self.tenant}/databases/{self.database}/collections/{collection_id}/query"
        payload = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
            "include": include or ["documents", "metadatas", "distances"],
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()
