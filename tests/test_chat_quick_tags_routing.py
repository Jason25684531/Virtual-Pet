"""Chat 快捷 tag 的 send 模式文字必須由真實技能檔決定性命中(不經 LLM)。"""
from pathlib import Path

import pytest

from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter

SKILLS_DIR = Path(__file__).resolve().parents[1] / ".agentic" / "skills"


@pytest.fixture(scope="module")
def skills():
    return SkillLoader(SKILLS_DIR).load_skills()


@pytest.mark.parametrize(
    "text,skill_name",
    [("播放輕鬆的音樂", "youtube_music_playback"), ("遊戲新聞", "bahamut_daily_news")],
)
def test_quick_tag_text_routes_deterministically(skills, text, skill_name):
    assert SkillRouter(skills).match(text).name == skill_name


def test_music_tag_searches_for_relaxing_music(skills):
    music = next(s for s in skills if s.capability == "music")
    args = PetHarnessEngine._media_arguments(music, "播放輕鬆的音樂")
    assert args == {"action": "search_and_play", "query": "輕鬆的音樂"}
