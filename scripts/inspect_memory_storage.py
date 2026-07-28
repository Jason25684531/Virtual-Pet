from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet_harness.storage.sqlite_store import SQLiteStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Read SQLite memory items and Qdrant hybrid points.")
    parser.add_argument("--character", default="Choppr")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    root = Path("data/characters") / args.character
    store = SQLiteStore(root / "state.db")
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT memory_id, memory_key, memory_type, text, status, indexed_at "
            "FROM memory_items ORDER BY created_at DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
    report = {"sqlite_memory_items": [dict(row) for row in rows]}
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(path=str(root / "qdrant"))
        collection = f"{args.character}_memory_hybrid"
        points, _ = client.scroll(collection, limit=args.limit, with_payload=True, with_vectors=False)
        report["qdrant"] = {
            "collection": collection,
            "count": client.count(collection).count,
            "points": [{"id": str(point.id), "payload": point.payload} for point in points],
        }
        client.close()
    except Exception as exc:
        report["qdrant_error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
