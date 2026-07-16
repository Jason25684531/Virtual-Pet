from __future__ import annotations

import json
from pathlib import Path

from pet_harness.models.skill import Skill
from pet_harness.skills.intent_normalizer import normalize
from pet_harness.skills.semantic_skill_retriever import SemanticCandidate
from pet_harness.skills.skill_router import SkillRouter
from tests.fakes.fake_semantic_retriever import FakeSemanticRetriever


def test_normalizer_strips_composed_politeness_without_touching_content():
    assert normalize("可不可以麻煩幫我放一首歌嗎?").stripped_text == "放一首歌"
    assert normalize("播放能不能勇敢說愛").stripped_text == "播放能不能勇敢說愛"
    assert normalize("可以幫我嗎?").stripped_text == normalize("可以幫我嗎?").normalized_text


def test_router_uses_stripped_intent_and_keeps_trigger_matching_normalized():
    music = Skill("youtube_music_playback", "music", ["播放音樂"], "idle", 1, capability="music")
    assert SkillRouter([music]).match("可以幫我播放周杰倫的晴天嗎?") == music


def test_semantic_precedence_shadow_and_provider_fallback():
    music = Skill("youtube_music_playback", "music", ["播放音樂"], "idle", 1, capability="music")
    retriever = FakeSemanticRetriever([SemanticCandidate(music.name, 0.91), SemanticCandidate("other", 0.1)])
    router = SkillRouter([music], semantic_retriever=retriever)
    selected, source = router.route("適合工作的背景旋律", semantic_enabled=True, semantic_shadow_mode=False)
    assert (selected, source) == (music, "semantic")
    selected, source = router.route("適合工作的背景旋律", suggested_skill_name=music.name, suggested_confidence=0.9, allow_fallback=True, semantic_enabled=True, semantic_shadow_mode=True)
    assert (selected, source) == (music, "provider")
    assert router.last_route_diagnostics["semantic_shadow"]["would_have_selected"] == music.name


def test_semantic_rejects_low_score_margin_and_unknown_skill():
    skill = Skill("music", "music", [], "idle", 1)
    router = SkillRouter([skill], semantic_retriever=FakeSemanticRetriever([SemanticCandidate("unknown", 0.95), SemanticCandidate("music", 0.93)]))
    assert router.route("ambiguous", semantic_enabled=True, semantic_shadow_mode=False)[0] is None
    assert router.last_route_diagnostics["rejection_reason"] == "semantic_rejected"


def test_routing_baseline_has_no_deterministic_regression():
    from pet_harness.skills.skill_loader import SkillLoader

    rows = json.loads((Path(__file__).parent / "data" / "routing_baseline.json").read_text(encoding="utf-8"))
    router = SkillRouter(SkillLoader(Path(".agentic") / "skills").load_skills())
    assert [getattr(router.match(row["text"]), "name", None) for row in rows] == [row["skill_id"] for row in rows]
