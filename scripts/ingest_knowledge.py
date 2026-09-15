"""灌入 Retriveal_doc/ 的共用遊戲知識語料到 Qdrant（design D9：先驗收、後灌入；重灌整庫重建）。

用法： python scripts/ingest_knowledge.py
應用執行中會鎖住 Qdrant 本機目錄，執行前請先關閉應用。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from pet_harness.knowledge.corpus_parser import (
    CorpusError,
    chunk_to_memory_item,
    collect_keywords,
    parse_corpus,
    validate_corpus,
)
from pet_harness.knowledge.gate import save_keywords
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore


def _load_chunks(corpus_dir: Path) -> tuple[list, list[str]]:
    chunks, errors = [], []
    md_files = sorted(corpus_dir.glob("*.md"))
    for md_path in md_files:
        try:
            file_chunks = parse_corpus(md_path.read_text(encoding="utf-8"))
        except CorpusError as exc:
            errors.append(f"{md_path.name}: {exc}")
            continue
        chunks.extend(file_chunks)
        errors.extend(f"{md_path.name}: {error}" for error in validate_corpus(file_chunks))
    return chunks, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", default=str(config.KNOWLEDGE_CORPUS_DIR))
    parser.add_argument("--qdrant-path", default=str(config.KNOWLEDGE_QDRANT_PATH))
    parser.add_argument("--keywords-path", default=str(config.KNOWLEDGE_KEYWORDS_PATH))
    parser.add_argument("--collection", default=config.KNOWLEDGE_COLLECTION)
    args = parser.parse_args()

    corpus_dir = Path(args.corpus_dir)
    chunks, errors = _load_chunks(corpus_dir)
    if errors:
        print(f"驗收失敗，共 {len(errors)} 項不合格，未寫入向量庫：")
        for error in errors[:50]:
            print(" -", error)
        return 1
    if not chunks:
        print(f"{corpus_dir} 底下沒有找到任何 *.md 語料，未執行灌入")
        return 1

    store = HybridQdrantMemoryStore(character_id="shared", path=args.qdrant_path, collection=args.collection)
    status = store.status()
    if status.state != "ready":
        hint = "（Qdrant 本機目錄可能被應用鎖定，請先關閉應用再重試）" if "already accessed" in (status.reason or "") else ""
        print(f"向量庫尚未就緒，中止灌入：{status.reason}{hint}")
        return 1

    store.clear()  # 刪除並重建 collection：庫內容必須等於本次語料，不殘留舊 chunk
    items = [chunk_to_memory_item(chunk) for chunk in chunks]
    store.index(items)
    save_keywords(collect_keywords(chunks), args.keywords_path)

    domain_counts = Counter(chunk.domain for chunk in chunks)
    print(f"灌入完成：{len(chunks)} 個 chunk -> collection={args.collection}")
    for domain, count in sorted(domain_counts.items()):
        print(f"  - {domain}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
