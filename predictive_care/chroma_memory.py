"""Vector version of long-term memory: ChromaDB with semantic recall.

Selected with ``CARE_MEMORY_BACKEND=chroma``. Memory records are embedded with
Chroma's bundled all-MiniLM-L6-v2 ONNX model (no embedding API key needed) and
retrieved by cosine similarity, so the agent recalls *meaning* rather than
shared words: "cannot tap my card at the till" recalls a past "contactless
payment declined" resolution, which the lexical backends miss.

The structured customer profile is not a retrieval problem, so it lives in a
second collection as one document per app/user with the facts in metadata.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping
from typing import Any

from typing_extensions import override

from .config import settings
from .memory import CareMemoryService

logger = logging.getLogger(__name__)

# Chroma metadata values must be scalars, so nested payloads are stored as JSON.
_METADATA_JSON = "metadata_json"
_PROFILE_JSON = "profile_json"


class ChromaMemoryService(CareMemoryService):
    """Long-term memory persisted in ChromaDB as embeddings."""

    # A nearest-neighbour query always returns its k nearest documents, so drop
    # weak matches to keep unrelated memories out of the predictor's features.
    SIMILARITY_FLOOR = 0.2

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        collection: str | None = None,
        app_name: str | None = None,
    ):
        super().__init__(app_name=app_name)
        self.host = host or settings.CHROMA_HOST
        self.port = port or settings.CHROMA_PORT
        self.collection_name = collection or settings.CHROMA_COLLECTION
        self._client: Any = None
        self._records: Any = None
        self._profiles: Any = None
        self._lock = asyncio.Lock()

    async def _connect(self) -> None:
        """Create the client and both collections once, on first use."""
        if self._records is not None:
            return
        async with self._lock:
            if self._records is not None:
                return
            import chromadb
            from chromadb.utils import embedding_functions

            embedder = embedding_functions.DefaultEmbeddingFunction()
            self._client = await chromadb.AsyncHttpClient(host=self.host, port=self.port)
            self._records = await self._client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=embedder,
                metadata={"hnsw:space": "cosine"},
            )
            self._profiles = await self._client.get_or_create_collection(
                name=f"{self.collection_name}_profiles",
                embedding_function=embedder,
            )

    @override
    async def close(self) -> None:
        self._client = None
        self._records = None
        self._profiles = None

    @staticmethod
    def _where(app_name: str, user_id: str) -> dict[str, Any]:
        return {"$and": [{"app_name": app_name}, {"user_id": user_id}]}

    @staticmethod
    def _profile_id(app_name: str, user_id: str) -> str:
        return f"{app_name}:{user_id}"

    @staticmethod
    def _to_record(document: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        raw = metadata.get(_METADATA_JSON) or "{}"
        try:
            extra = json.loads(str(raw))
        except json.JSONDecodeError:
            extra = {}
        return {
            "text": document or "",
            "author": metadata.get("author", "system"),
            "kind": metadata.get("kind", "fact"),
            "timestamp": float(metadata.get("timestamp", 0.0)),
            "metadata": extra,
        }

    # ------------------------------------------------------------------
    # Storage primitives
    # ------------------------------------------------------------------

    @override
    async def _append_record(
        self, app_name: str, user_id: str, record: dict[str, Any]
    ) -> None:
        await self._connect()
        await self._records.add(
            ids=[uuid.uuid4().hex],
            documents=[record["text"]],
            metadatas=[
                {
                    "app_name": app_name,
                    "user_id": user_id,
                    "author": record["author"],
                    "kind": record["kind"],
                    "timestamp": float(record["timestamp"]),
                    _METADATA_JSON: json.dumps(record.get("metadata", {})),
                }
            ],
        )
        await self._trim(app_name, user_id)

    async def _trim(self, app_name: str, user_id: str) -> None:
        """Drop the oldest embeddings once a user exceeds ``MAX_ENTRIES``."""
        found = await self._records.get(
            where=self._where(app_name, user_id), include=["metadatas"]
        )
        ids = found.get("ids") or []
        if len(ids) <= self.MAX_ENTRIES:
            return
        metadatas = found.get("metadatas") or []
        oldest = sorted(
            zip(ids, metadatas), key=lambda item: float(item[1].get("timestamp", 0.0))
        )[: len(ids) - self.MAX_ENTRIES]
        await self._records.delete(ids=[item[0] for item in oldest])

    @override
    async def _read_records(
        self, app_name: str, user_id: str, limit: int
    ) -> list[dict[str, Any]]:
        await self._connect()
        found = await self._records.get(
            where=self._where(app_name, user_id), include=["documents", "metadatas"]
        )
        records = [
            self._to_record(document, metadata or {})
            for document, metadata in zip(
                found.get("documents") or [], found.get("metadatas") or []
            )
        ]
        records.sort(key=lambda record: record["timestamp"], reverse=True)
        return records[:limit]

    @override
    async def search_records(
        self,
        *,
        user_id: str,
        query: str,
        app_name: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Semantic nearest-neighbour search instead of the lexical default."""
        await self._connect()
        app_name = app_name or self.app_name
        result = await self._records.query(
            query_texts=[query],
            n_results=top_k,
            where=self._where(app_name, user_id),
            include=["documents", "metadatas", "distances"],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits = []
        for document, metadata, distance in zip(documents, metadatas, distances):
            record = self._to_record(document, metadata or {})
            # Cosine distance in [0, 2] -> similarity in [0, 1].
            record["score"] = round(max(0.0, 1.0 - float(distance)), 4)
            if record["score"] >= self.SIMILARITY_FLOOR:
                hits.append(record)
        return hits

    @override
    async def _read_profile(self, app_name: str, user_id: str) -> dict[str, Any]:
        await self._connect()
        found = await self._profiles.get(
            ids=[self._profile_id(app_name, user_id)], include=["metadatas"]
        )
        metadatas = found.get("metadatas") or []
        if not metadatas:
            return {}
        try:
            return dict(json.loads(str(metadatas[0].get(_PROFILE_JSON) or "{}")))
        except json.JSONDecodeError:
            logger.warning("Skipping corrupt profile for user=%s", user_id)
            return {}

    @override
    async def _write_profile(
        self, app_name: str, user_id: str, updates: Mapping[str, Any]
    ) -> None:
        profile = {**await self._read_profile(app_name, user_id), **updates}
        await self._profiles.upsert(
            ids=[self._profile_id(app_name, user_id)],
            documents=[f"Customer profile for {user_id}"],
            metadatas=[
                {
                    "app_name": app_name,
                    "user_id": user_id,
                    _PROFILE_JSON: json.dumps(profile),
                }
            ],
        )

    @override
    async def _delete_user(self, app_name: str, user_id: str) -> None:
        await self._connect()
        await self._records.delete(where=self._where(app_name, user_id))
        await self._profiles.delete(ids=[self._profile_id(app_name, user_id)])
