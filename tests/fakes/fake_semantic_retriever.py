from __future__ import annotations

from pet_harness.skills.semantic_skill_retriever import BaseSemanticSkillRetriever, RetrieverStatus, SemanticCandidate


class FakeSemanticRetriever(BaseSemanticSkillRetriever):
    def __init__(self, candidates: list[SemanticCandidate] | None = None, state: str = "ready") -> None:
        self.candidates = candidates or []
        self._status = RetrieverStatus(state)
        self.manifests: list[dict] = []

    def index(self, manifest: dict) -> None:
        self.manifests.append(manifest)

    def search(self, query: str, top_k: int) -> list[SemanticCandidate]:
        return self.candidates[:top_k] if self._status.state == "ready" else []

    def status(self) -> RetrieverStatus:
        return self._status
