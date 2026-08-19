"""Knowledge-graph version of long-term memory: Neo4j.

Selected with ``CARE_MEMORY_BACKEND=graph``. Instead of embedding text, this
version records the *entities* behind each memory and the edges between them::

    (:Customer)-[:HAS_MEMORY]->(:Memory)-[:ABOUT_ISSUE]->(:Issue)
    (:Memory)-[:RESOLVED_WITH]->(:Action)
    (:Customer)-[:OWNS]->(:Card)<-[:ON_CARD]-(:Memory)

Retrieval therefore traverses relationships: a memory is a hit when its own text
matches, *or* when it shares an issue/action/card with a memory that matches, so
"unlock" recalls the earlier lost-card reissue on the same card even though the
two texts share no words.
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
from .memory import tokenize

logger = logging.getLogger(__name__)

# Metadata keys promoted to graph entities, mapped to (label, relationship).
_ENTITY_FIELDS: dict[str, tuple[str, str]] = {
    "issue_type": ("Issue", "ABOUT_ISSUE"),
    "action": ("Action", "RESOLVED_WITH"),
    "card_id": ("Card", "ON_CARD"),
}


class Neo4jMemoryService(CareMemoryService):
    """Long-term memory persisted as a Neo4j knowledge graph."""

    def __init__(
        self,
        url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        app_name: str | None = None,
    ):
        super().__init__(app_name=app_name)
        self.url = url or settings.NEO4J_URL
        self.user = user or settings.NEO4J_USER
        self.password = password or settings.NEO4J_PASSWORD
        self.database = database or settings.NEO4J_DATABASE
        self._driver: Any = None
        self._lock = asyncio.Lock()

    async def _connect(self) -> Any:
        if self._driver is not None:
            return self._driver
        async with self._lock:
            if self._driver is None:
                from neo4j import AsyncGraphDatabase

                driver = AsyncGraphDatabase.driver(
                    self.url,
                    auth=(self.user, self.password),
                    connection_acquisition_timeout=10,
                )
                await driver.verify_connectivity()
                await driver.execute_query(
                    "CREATE CONSTRAINT care_customer IF NOT EXISTS "
                    "FOR (c:Customer) REQUIRE (c.app_name, c.user_id) IS UNIQUE",
                    database_=self.database,
                )
                await driver.execute_query(
                    "CREATE CONSTRAINT care_memory IF NOT EXISTS "
                    "FOR (m:Memory) REQUIRE m.id IS UNIQUE",
                    database_=self.database,
                )
                self._driver = driver
        return self._driver

    async def _run(self, query: str, **params: Any) -> list[Any]:
        driver = await self._connect()
        records, _, _ = await driver.execute_query(
            query, database_=self.database, **params
        )
        return list(records)

    @override
    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    @staticmethod
    def _to_record(node: Mapping[str, Any]) -> dict[str, Any]:
        try:
            extra = json.loads(str(node.get("metadata_json") or "{}"))
        except json.JSONDecodeError:
            extra = {}
        return {
            "text": node.get("text", ""),
            "author": node.get("author", "system"),
            "kind": node.get("kind", "fact"),
            "timestamp": float(node.get("timestamp", 0.0)),
            "metadata": extra,
        }

    # ------------------------------------------------------------------
    # Storage primitives
    # ------------------------------------------------------------------

    @override
    async def _append_record(
        self, app_name: str, user_id: str, record: dict[str, Any]
    ) -> None:
        metadata = record.get("metadata", {})
        memory_id = uuid.uuid4().hex
        await self._run(
            """
            MERGE (c:Customer {app_name: $app_name, user_id: $user_id})
            CREATE (m:Memory {
                id: $id, text: $text, author: $author, kind: $kind,
                timestamp: $timestamp, metadata_json: $metadata_json
            })
            MERGE (c)-[:HAS_MEMORY]->(m)
            """,
            app_name=app_name,
            user_id=user_id,
            id=memory_id,
            text=record["text"],
            author=record["author"],
            kind=record["kind"],
            timestamp=float(record["timestamp"]),
            metadata_json=json.dumps(metadata),
        )
        await self._link_entities(app_name, user_id, memory_id, metadata)
        await self._trim(app_name, user_id)

    async def _link_entities(
        self,
        app_name: str,
        user_id: str,
        memory_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        """Promote known metadata fields to entity nodes and connect them."""
        for field, (label, relationship) in _ENTITY_FIELDS.items():
            name = metadata.get(field)
            if not name:
                continue
            await self._run(
                f"""
                MATCH (c:Customer {{app_name: $app_name, user_id: $user_id}})
                MATCH (m:Memory {{id: $memory_id}})
                MERGE (e:Entity:{label} {{name: $name}})
                MERGE (m)-[:{relationship}]->(e)
                MERGE (c)-[:KNOWS]->(e)
                """,
                app_name=app_name,
                user_id=user_id,
                memory_id=memory_id,
                name=str(name),
            )

    async def _trim(self, app_name: str, user_id: str) -> None:
        await self._run(
            """
            MATCH (c:Customer {app_name: $app_name, user_id: $user_id})
                  -[:HAS_MEMORY]->(m:Memory)
            WITH m ORDER BY m.timestamp DESC SKIP $keep
            DETACH DELETE m
            """,
            app_name=app_name,
            user_id=user_id,
            keep=self.MAX_ENTRIES,
        )

    @override
    async def _read_records(
        self, app_name: str, user_id: str, limit: int
    ) -> list[dict[str, Any]]:
        records = await self._run(
            """
            MATCH (c:Customer {app_name: $app_name, user_id: $user_id})
                  -[:HAS_MEMORY]->(m:Memory)
            RETURN m AS memory ORDER BY m.timestamp DESC LIMIT $limit
            """,
            app_name=app_name,
            user_id=user_id,
            limit=max(limit, 0),
        )
        return [self._to_record(record["memory"]) for record in records]

    @override
    async def search_records(
        self,
        *,
        user_id: str,
        query: str,
        app_name: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Rank memories by direct text match plus shared-entity traversal."""
        tokens = tokenize(query)
        if not tokens:
            return []
        rows = await self._run(
            """
            MATCH (c:Customer {app_name: $app_name, user_id: $user_id})
                  -[:HAS_MEMORY]->(m:Memory)
            WITH c, m, size([t IN $tokens WHERE toLower(m.text) CONTAINS t]) AS lexical
            OPTIONAL MATCH (m)-->(e:Entity)<--(peer:Memory)<-[:HAS_MEMORY]-(c)
            WHERE size([t IN $tokens WHERE toLower(peer.text) CONTAINS t]) > 0
               OR any(t IN $tokens WHERE toLower(e.name) CONTAINS t)
            WITH m, lexical, count(DISTINCT e) AS linked
            WHERE lexical > 0 OR linked > 0
            RETURN m AS memory, lexical, linked
            ORDER BY lexical + linked DESC, m.timestamp DESC LIMIT $top_k
            """,
            app_name=app_name or self.app_name,
            user_id=user_id,
            tokens=tokens,
            top_k=top_k,
        )
        hits = []
        for row in rows:
            record = self._to_record(row["memory"])
            weight = float(row["lexical"]) + 0.5 * float(row["linked"])
            record["score"] = round(min(weight / max(len(tokens), 1), 1.0), 4)
            record["graph_links"] = int(row["linked"])
            hits.append(record)
        return hits

    @override
    async def _read_profile(self, app_name: str, user_id: str) -> dict[str, Any]:
        records = await self._run(
            """
            MATCH (c:Customer {app_name: $app_name, user_id: $user_id})
            RETURN c.facts_json AS facts
            """,
            app_name=app_name,
            user_id=user_id,
        )
        if not records:
            return {}
        try:
            return dict(json.loads(str(records[0]["facts"] or "{}")))
        except json.JSONDecodeError:
            logger.warning("Skipping corrupt profile for user=%s", user_id)
            return {}

    @override
    async def _write_profile(
        self, app_name: str, user_id: str, updates: Mapping[str, Any]
    ) -> None:
        profile = {**await self._read_profile(app_name, user_id), **updates}
        await self._run(
            """
            MERGE (c:Customer {app_name: $app_name, user_id: $user_id})
            SET c.facts_json = $facts
            """,
            app_name=app_name,
            user_id=user_id,
            facts=json.dumps(profile),
        )

    @override
    async def _delete_user(self, app_name: str, user_id: str) -> None:
        await self._run(
            """
            MATCH (c:Customer {app_name: $app_name, user_id: $user_id})
            OPTIONAL MATCH (c)-[:HAS_MEMORY]->(m:Memory)
            DETACH DELETE c, m
            """,
            app_name=app_name,
            user_id=user_id,
        )
