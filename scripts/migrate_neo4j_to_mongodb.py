"""
scripts/migrate_neo4j_to_mongodb.py — One-time Neo4j to MongoDB Graph Data Migration Script
============================================================================================

Migrates graph nodes and relationships from a legacy Neo4j instance to MongoDB
`graph_nodes` and `graph_relationships` collections.

Ensures:
  1. Deterministic node IDs: hash(user_id:label:name)
  2. Strict user scoping: user_id attached to every node and relationship document
  3. Idempotency: Uses upserts ($setOnInsert / $set) so re-running is safe.

Usage:
  python scripts/migrate_neo4j_to_mongodb.py --neo4j-uri bolt://localhost:7687 --neo4j-user neo4j --neo4j-password password --mongo-uri mongodb://localhost:27017
"""

import argparse
import asyncio
import logging
from typing import Any, Dict

from motor.motor_asyncio import AsyncIOMotorClient
from neo4j import AsyncGraphDatabase

from services.mongo_graph import generate_node_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("neo4j_migration")


async def migrate_neo4j_to_mongodb(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    mongo_uri: str,
    database_name: str = "mental_health",
):
    logger.info("Starting Neo4j -> MongoDB graph migration...")

    mongo_client = AsyncIOMotorClient(mongo_uri)
    db = mongo_client[database_name]

    neo4j_driver = AsyncGraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

    try:
        # 1. Export Nodes from Neo4j
        async with neo4j_driver.session() as session:
            node_query = """
            MATCH (n)
            RETURN labels(n) AS labels, properties(n) AS props
            """
            result = await session.run(node_query)
            records = await result.data()
            logger.info("Fetched %d nodes from Neo4j.", len(records))

            migrated_nodes = 0
            for record in records:
                labels = record.get("labels", [])
                props = record.get("props", {})
                user_id = props.get("user_id") or props.get("userId")
                name = props.get("name") or props.get("id")

                if not user_id or not name:
                    continue

                label = labels[0] if labels else "Entity"
                node_id = generate_node_id(user_id, label, name)

                doc = {
                    "node_id": node_id,
                    "user_id": user_id,
                    "label": label,
                    "name": name,
                    "properties": props,
                }

                await db["graph_nodes"].update_one(
                    {"node_id": node_id, "user_id": user_id},
                    {"$set": doc},
                    upsert=True,
                )
                migrated_nodes += 1

            logger.info("Successfully migrated %d nodes to MongoDB.", migrated_nodes)

            # 2. Export Relationships from Neo4j
            rel_query = """
            MATCH (a)-[r]->(b)
            RETURN labels(a) AS source_labels, properties(a) AS source_props,
                   type(r) AS rel_type, properties(r) AS rel_props,
                   labels(b) AS target_labels, properties(b) AS target_props
            """
            rel_result = await session.run(rel_query)
            rel_records = await rel_result.data()
            logger.info("Fetched %d relationships from Neo4j.", len(rel_records))

            migrated_rels = 0
            for record in rel_records:
                s_props = record.get("source_props", {})
                t_props = record.get("target_props", {})
                s_labels = record.get("source_labels", [])
                t_labels = record.get("target_labels", [])
                rel_type = record.get("rel_type")

                user_id = s_props.get("user_id") or s_props.get("userId")
                s_name = s_props.get("name") or s_props.get("id")
                t_name = t_props.get("name") or t_props.get("id")

                if not user_id or not s_name or not t_name:
                    continue

                s_label = s_labels[0] if s_labels else "Entity"
                t_label = t_labels[0] if t_labels else "Entity"

                source_id = generate_node_id(user_id, s_label, s_name)
                target_id = generate_node_id(user_id, t_label, t_name)

                rel_doc = {
                    "user_id": user_id,
                    "source_id": source_id,
                    "target_id": target_id,
                    "relation_type": rel_type,
                    "properties": record.get("rel_props", {}),
                }

                await db["graph_relationships"].update_one(
                    {
                        "user_id": user_id,
                        "source_id": source_id,
                        "target_id": target_id,
                        "relation_type": rel_type,
                    },
                    {"$set": rel_doc},
                    upsert=True,
                )
                migrated_rels += 1

            logger.info("Successfully migrated %d relationships to MongoDB.", migrated_rels)

    finally:
        await neo4j_driver.close()
        mongo_client.close()
        logger.info("Migration script execution completed.")


def main():
    parser = argparse.ArgumentParser(description="Migrate Neo4j graph data to MongoDB")
    parser.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default="password")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--db-name", default="mental_health")

    args = parser.parse_args()

    asyncio.run(
        migrate_neo4j_to_mongodb(
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            mongo_uri=args.mongo_uri,
            database_name=args.db_name,
        )
    )


if __name__ == "__main__":
    main()
