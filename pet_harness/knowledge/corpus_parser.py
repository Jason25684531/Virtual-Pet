from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import yaml

from pet_harness.memory.memory_models import MemoryItem

# 語料交付規格（Retriveal_doc/*_Internal_Guide.docx 第 4／5 節）定義的 Parser 契約：
# `### ` 為 chunk 邊界、`｜` 前為 id、緊接的第一個 yaml fence 為 metadata。
_HEADER_RE = re.compile(r"^### (?P<id>[^\n｜]+)｜(?P<title>.+?)\s*$", re.MULTILINE)
_YAML_FENCE_RE = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)

REQUIRED_FIELDS = (
    "id", "dataset_version", "language", "game", "domain", "topic", "title",
    "entity_type", "volatility", "as_of_patch", "spoiler_level", "difficulty",
    "keywords", "aliases", "related_ids",
)
VALID_ENUMS = {
    "volatility": {"evergreen", "patch_sensitive"},
    "spoiler_level": {"none", "minor", "major"},
    "difficulty": {"beginner", "intermediate", "advanced"},
}
# 兩份 V1 語料在規格書第 2 節宣告的 content 長度區間；未知 game 不做長度檢查。
GAME_LENGTH_RANGES = {
    "elden_ring": (270, 565),
    "league_of_legends": (361, 605),
}
_KNOWLEDGE_NAMESPACE = uuid5(NAMESPACE_URL, "virtual-pet-knowledge")


class CorpusError(ValueError):
    """語料不符合 Parser 契約或驗收條件。"""


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    title: str
    content: str
    metadata: dict = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return str(self.metadata.get("domain", ""))

    @property
    def game(self) -> str:
        return str(self.metadata.get("game", ""))

    @property
    def keywords(self) -> list[str]:
        return list(self.metadata.get("keywords") or [])

    @property
    def aliases(self) -> list[str]:
        return list(self.metadata.get("aliases") or [])

    @property
    def related_ids(self) -> list[str]:
        return list(self.metadata.get("related_ids") or [])


def parse_corpus(text: str) -> list[KnowledgeChunk]:
    """依 Parser 契約切分語料 Markdown；不做驗收檢查，見 validate_corpus。"""
    headers = list(_HEADER_RE.finditer(text))
    chunks = []
    for i, header in enumerate(headers):
        chunk_id = header.group("id").strip()
        body_start = header.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[body_start:body_end]
        fence = _YAML_FENCE_RE.search(body)
        if not fence:
            raise CorpusError(f"{chunk_id}: 標題後找不到 metadata 的 yaml fence")
        metadata = yaml.safe_load(fence.group(1)) or {}
        content = body[fence.end():].strip()
        chunks.append(KnowledgeChunk(id=chunk_id, title=header.group("title").strip(), content=content, metadata=metadata))
    return chunks


def validate_corpus(chunks: list[KnowledgeChunk]) -> list[str]:
    """執行規格書第 6 節的驗收檢查，回傳不合格項目的說明；空 list 代表全數通過。"""
    errors: list[str] = []
    seen_ids: set[str] = set()
    for chunk in chunks:
        missing = [f for f in REQUIRED_FIELDS if f not in chunk.metadata]
        if missing:
            errors.append(f"{chunk.id}: 缺少必填欄位 {missing}")
        if chunk.id in seen_ids:
            errors.append(f"{chunk.id}: id 重複")
        seen_ids.add(chunk.id)
        for field_name, valid_values in VALID_ENUMS.items():
            value = chunk.metadata.get(field_name)
            if value is not None and value not in valid_values:
                errors.append(f"{chunk.id}: {field_name}={value!r} 不是合法值 {valid_values}")
        if chunk.metadata.get("title") != chunk.title:
            errors.append(f"{chunk.id}: 標題與 metadata.title 不一致（{chunk.title!r} vs {chunk.metadata.get('title')!r}）")
        length_range = GAME_LENGTH_RANGES.get(chunk.game)
        if length_range and not (length_range[0] <= len(chunk.content) <= length_range[1]):
            errors.append(f"{chunk.id}: content 長度 {len(chunk.content)} 不在 {length_range} 區間內")
    valid_ids = seen_ids
    for chunk in chunks:
        for related_id in chunk.related_ids:
            if related_id not in valid_ids:
                errors.append(f"{chunk.id}: related_ids 指向不存在的 {related_id!r}")
    return errors


def chunk_to_memory_item(chunk: KnowledgeChunk, *, created_at: str | None = None) -> MemoryItem:
    """依 design D3/D4 對應：embedding 輸入即儲存內容，memory_id 由 chunk_id 確定性推導。"""
    text = "\n".join([chunk.title, " ".join(str(k) for k in chunk.keywords), chunk.content])
    return MemoryItem(
        memory_id=str(uuid5(_KNOWLEDGE_NAMESPACE, chunk.id)),
        character_id="shared",
        user_id="default",
        memory_key=chunk.id,
        memory_type=chunk.domain or "knowledge",
        text=text,
        status="active",
        source_event_id=None,
        created_at=created_at or datetime.now(UTC).isoformat(),
        expires_at=None,
    )


def collect_keywords(chunks: list[KnowledgeChunk]) -> set[str]:
    """給檢索閘門用的關鍵詞／別名集合（design 新增：進 RAG 前的輕量判斷）。

    只收長度 >= 2 的詞，避免單字（如「的」「大」）造成幾乎每句話都命中。
    """
    terms: set[str] = set()
    for chunk in chunks:
        for term in (*chunk.keywords, *chunk.aliases):
            term = str(term).strip()
            if len(term) >= 2:
                terms.add(term)
    return terms
