from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_models import RetrievalRequest
from pet_harness.memory.query_rewriter import LlmQueryRewriter


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the evidence Qdrant returns for one memory question.")
    parser.add_argument("question")
    parser.add_argument("--character", default="Choppr")
    parser.add_argument("--previous-user")
    parser.add_argument("--previous-assistant")
    args = parser.parse_args()
    index = HybridQdrantMemoryStore(
        character_id=args.character,
        path=Path("data/characters") / args.character / "qdrant",
    )
    rewriter = LlmQueryRewriter(lambda *_args, **_kwargs: None, enabled=False)
    retriever = ContextualMemoryRetriever(index, index.embed_dense, index.sparse_encoder, rewriter)
    result = retriever.retrieve(
        RetrievalRequest(args.character, args.question, args.previous_user, args.previous_assistant)
    )
    print(json.dumps({"trace": result.trace.to_dict(), "evidence": [item.text for item in result.evidence]}, ensure_ascii=False, indent=2))
    index.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
