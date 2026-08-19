"""MongoDB-backed long-term memory for the predictive care agent.

Selected with ``CARE_MEMORY_BACKEND=mongo``. Records live in the
``memories`` collection (one document per remembered fact) and the structured
customer profile in ``profiles`` (one document per app/user), so long-term
memory is queryable with plain Mongo tooling while behavioural signals and
conversation sessions stay in Redis.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from datetime import timezone
from typing import Any

from pymongo import AsyncMongoClient
from pymongo import DESCENDING
from typing_extensions import override

from .config import settings
from .memory import CareMemoryService

logger = logging.getLogger(__name__)


class MongoMemoryService(CareMemoryService):
    """Long-term memory persisted in MongoDB."""

    RECORDS_COLLECTION = "memories"
    PROFILES_COLLECTION = "profiles"

    def __init__(
        self,
        mongo_url: str | None = None,
        database: str | None = None,
        app_name: str | None = None,
        client: AsyncMongoClient | None = None,
    ):
        super().__init__(app_name=app_name)
        self.mongo_url = mongo_url or settings.MONGO_URL
        self.database = database or settings.MONGO_DB
        self._client = client
        self._indexes_ready = False

    @property
    def client(self) -> AsyncMongoClient:
        if self._client is None:
            self._client = AsyncMongoClient(
                self.mongo_url, tz_aware=False, serverSelectionTimeoutMS=5000
            )
        return self._client

    @property
    def records(self) -> Any:
        return self.client[self.database][self.RECORDS_COLLECTION]

    @property
    def profiles(self) -> Any:
        return self.client[self.database][self.PROFILES_COLLECTION]

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        await self.records.create_index(
            [("app_name", 1), ("user_id", 1), ("timestamp", DESCENDING)]
        )
        if settings.MEMORY_TTL_SECONDS > 0:
            await self.records.create_index(
                "created_at", expireAfterSeconds=settings.MEMORY_TTL_SECONDS
            )
        self._indexes_ready = True

    @override
    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._indexes_ready = False

    # ------------------------------------------------------------------
    # Storage primitives
    # ------------------------------------------------------------------

    @override
    async def _append_record(
        self, app_name: str, user_id: str, record: dict[str, Any]
    ) -> None:
        await self._ensure_indexes()
        await self.records.insert_one(
            {
                "app_name": app_name,
                "user_id": user_id,
                # Dedicated BSON date so an optional TTL index can expire records.
                "created_at": datetime.fromtimestamp(
                    float(record["timestamp"]), tz=timezone.utc
                ).replace(tzinfo=None),
                **record,
            }
        )
        # Trim the oldest entries so a long-lived demo user cannot grow unbounded.
        query = {"app_name": app_name, "user_id": user_id}
        overflow = await self.records.count_documents(query) - self.MAX_ENTRIES
        if overflow > 0:
            stale = self.records.find(query).sort("timestamp", 1).limit(overflow)
            ids = [doc["_id"] async for doc in stale]
            await self.records.delete_many({"_id": {"$in": ids}})

    @override
    async def _read_records(
        self, app_name: str, user_id: str, limit: int
    ) -> list[dict[str, Any]]:
        await self._ensure_indexes()
        cursor = (
            self.records.find({"app_name": app_name, "user_id": user_id})
            .sort("timestamp", DESCENDING)
            .limit(limit)
        )
        return [
            {
                "text": doc.get("text", ""),
                "author": doc.get("author", "system"),
                "kind": doc.get("kind", "fact"),
                "timestamp": doc.get("timestamp", 0.0),
                "metadata": doc.get("metadata", {}),
            }
            async for doc in cursor
        ]

    @override
    async def _read_profile(self, app_name: str, user_id: str) -> dict[str, Any]:
        doc = await self.profiles.find_one({"app_name": app_name, "user_id": user_id})
        if not doc:
            return {}
        return dict(doc.get("facts", {}))

    @override
    async def _write_profile(
        self, app_name: str, user_id: str, updates: Mapping[str, Any]
    ) -> None:
        await self.profiles.update_one(
            {"app_name": app_name, "user_id": user_id},
            {"$set": {f"facts.{key}": value for key, value in updates.items()}},
            upsert=True,
        )

    @override
    async def _delete_user(self, app_name: str, user_id: str) -> None:
        query = {"app_name": app_name, "user_id": user_id}
        await self.records.delete_many(query)
        await self.profiles.delete_one(query)
