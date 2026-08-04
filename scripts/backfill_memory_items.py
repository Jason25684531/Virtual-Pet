from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_extractor import WholeTurnMemoryExtractor, is_eligible_memory_item
from pet_harness.memory.memory_item_repository import MemoryItemRepository
from pet_harness.storage.sqlite_store import SQLiteStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill SQLite memory items into the hybrid index.")
    parser.add_argument("--character", default="miku")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path("data/characters") / args.character
    store = SQLiteStore(root / "state.db")
    store.initialize()
    repository = MemoryItemRepository(store, args.character)
    extractor = WholeTurnMemoryExtractor()
    with store.connect() as conn:
        rows = conn.execute("SELECT memory_id, memory_key, text FROM memory_items WHERE status='active'").fetchall()
    invalid_ids = [row["memory_id"] for row in rows if not is_eligible_memory_item(row["memory_key"], row["text"])]
    if invalid_ids and not args.dry_run:
        with store.connect() as conn, conn:
            conn.executemany("UPDATE memory_items SET status='superseded' WHERE memory_id=?", [(item_id,) for item_id in invalid_ids])
    superseded = len(invalid_ids)
    created = 0
    with store.connect() as conn:
        events = conn.execute("SELECT event_id, input_payload, output_payload FROM event_log ORDER BY id").fetchall()
    event_ids = {event["event_id"] for event in events}
    legacy = []
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(path=str(root / "qdrant"))
        legacy, _ = client.scroll(collection_name=f"{args.character}_memory", limit=500, with_payload=True, with_vectors=False)
        client.close()
    except Exception:
        legacy = []
    for event in events:
        import json

        user = json.loads(event["input_payload"]).get("text", "")
        reply = json.loads(event["output_payload"]).get("reply", "")
        candidates = extractor.extract(event["event_id"], user, reply)
        if not args.dry_run:
            created += len(repository.upsert_candidates(candidates))
        else:
            created += len(candidates)
    for point in legacy:
        payload = point.payload or {}
        event_id = payload.get("event_id")
        if event_id in event_ids:
            continue
        text = str(payload.get("text", "")).strip()
        if not text:
            continue
        candidates = extractor.extract(str(event_id or point.id), text, "")
        if not args.dry_run:
            created += len(repository.upsert_candidates(candidates))
        else:
            created += len(candidates)
    pending = repository.list_pending_index()
    indexed = 0
    if not args.dry_run and pending:
        index = HybridQdrantMemoryStore(character_id=args.character, path=root / "qdrant")
        indexed_ids = index.index(pending)
        repository.mark_indexed(indexed_ids)
        indexed = len(indexed_ids)
    print(f"character={args.character} event_log={len(events)} legacy={len(legacy)} candidates={created} superseded={superseded} pending={len(pending)} indexed={indexed} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
