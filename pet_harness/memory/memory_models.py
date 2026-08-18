from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MemoryCandidate:
    memory_key: str
    memory_type: str
    text: str
    source_event_id: str


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    character_id: str
    user_id: str
    memory_key: str
    memory_type: str
    text: str
    status: str
    source_event_id: str | None
    created_at: str
    expires_at: str | None = None
    indexed_at: str | None = None
    superseded_by: str | None = None
    schema_version: int = 1


@dataclass(frozen=True)
class RetrievalCandidate:
    item: MemoryItem
    score: float
    fusion: str


@dataclass(frozen=True)
class RetrievalRequest:
    character_id: str
    current_turn_text: str
    previous_user_text: str | None = None
    previous_assistant_text: str | None = None
    previous_turn_age_seconds: float | None = None
    top_k: int = 5


@dataclass(frozen=True)
class RetrievalTrace:
    follow_up_detected: bool
    follow_up_reason: str | None
    rewrite_tier: int
    standalone_query: str
    fused_count: int = 0
    dense_attempted: bool = False
    sparse_attempted: bool = False
    top_score: float | None = None
    top_score_kind: str | None = None
    relevance_gate_enabled: bool = False
    dense_min_score: float = 0.0
    policy_dropped: dict[str, int] = field(default_factory=dict)
    sparse_available: bool = False
    latency_ms: dict[str, float] = field(default_factory=dict)

    @classmethod
    def empty(cls, query: str, *, rewrite_tier: int = 2) -> "RetrievalTrace":
        return cls(False, None, rewrite_tier, query)

    def to_dict(self) -> dict:
        return {
            "follow_up_detected": self.follow_up_detected,
            "follow_up_reason": self.follow_up_reason,
            "rewrite_tier": self.rewrite_tier,
            "standalone_query": self.standalone_query,
            "fused_count": self.fused_count,
            "dense_attempted": self.dense_attempted,
            "sparse_attempted": self.sparse_attempted,
            "top_score": self.top_score,
            "top_score_kind": self.top_score_kind,
            "relevance_gate_enabled": self.relevance_gate_enabled,
            "dense_min_score": self.dense_min_score,
            "policy_dropped": self.policy_dropped,
            "sparse_available": self.sparse_available,
            "latency_ms": self.latency_ms,
        }


@dataclass(frozen=True)
class RetrievalResult:
    # evidence 只能是來自 Search Index 的 Memory Item;Previous Assistant 的文字
    # 永不得出現於此(ADR-0005 Evidence Isolation)。ResultPolicy 需要 status 與
    # expires_at 才能過濾,MemoryHit 沒有這些欄位,故此處不用 MemoryHit。
    evidence: list[MemoryItem]
    trace: RetrievalTrace
