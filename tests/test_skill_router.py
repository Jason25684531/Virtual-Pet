"""任務 3.4:SkillRouter 決定性命中 — 正規化、priority/order 解衝突、不洩漏跨角色狀態。"""

from pet_harness.models.skill import Skill
from pet_harness.skills.skill_router import SkillRouter


def _skill(name: str, triggers: list[str], **kwargs) -> Skill:
    return Skill(
        name=name,
        description=f"fixture {name}",
        triggers=triggers,
        behavior=kwargs.get("behavior", "idle"),
        xp_reward=kwargs.get("xp_reward", 1),
    )


def test_chinese_alias_matches_after_normalization():
    music = _skill("music_bgm", ["放歌"])
    router = SkillRouter([music])

    matched = router.match("幫我放歌")

    assert matched is not None
    assert matched.name == "music_bgm"


def test_whitespace_and_case_normalization():
    joke = _skill("joke_skill", ["Tell Me A Joke"])
    router = SkillRouter([joke])

    assert router.match("please   TELL ME A     JOKE now") is not None
    assert router.match("tell me a joke") is not None


def test_longest_trigger_wins_over_shorter_substring_match():
    music_only = _skill("music_only", ["音樂"])
    play_music = _skill("play_music", ["播放音樂"])
    router = SkillRouter([music_only, play_music])

    diagnostics = router.match_diagnostics("請播放音樂")

    assert diagnostics["matched"] is True
    assert diagnostics["skill_id"] == "play_music"
    assert diagnostics["trigger"] == "播放音樂"
    candidate_ids = {c["skill_id"] for c in diagnostics["candidates"]}
    assert candidate_ids == {"music_only", "play_music"}


def test_priority_breaks_equal_length_trigger_tie():
    low = _skill("low_priority", ["gogo"])
    high = _skill("high_priority", ["gogo"])
    router = SkillRouter([low, high], priorities={"low_priority": 1, "high_priority": 5})

    matched = router.match("let's gogo now")

    assert matched.name == "high_priority"


def test_declared_order_breaks_equal_priority_tie():
    first = _skill("first_skill", ["gogo"])
    second = _skill("second_skill", ["gogo"])
    router = SkillRouter([first, second])

    assert router.match("gogo").name == "first_skill"

    router_reordered = SkillRouter([second, first])
    assert router_reordered.match("gogo").name == "second_skill"


def test_no_match_returns_none_and_empty_diagnostics():
    joke = _skill("joke_skill", ["joke"])
    router = SkillRouter([joke])

    assert router.match("totally unrelated text") is None
    diagnostics = router.match_diagnostics("totally unrelated text")
    assert diagnostics == {"matched": False, "skill_id": None, "trigger": None, "candidates": []}


def test_no_cross_character_leakage_between_independent_routers():
    miku_router = SkillRouter([_skill("music_bgm", ["放歌"])])
    choppr_router = SkillRouter([_skill("music_bgm", [])])

    assert miku_router.match("幫我放歌") is not None
    assert choppr_router.match("幫我放歌") is None
