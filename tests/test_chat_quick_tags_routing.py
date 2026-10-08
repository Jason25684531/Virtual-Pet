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
    [("can you play some relaxing music?", "youtube_music_playback"), ("can you tell me some game news?", "bahamut_daily_news")],
)
def test_quick_tag_text_routes_deterministically(skills, text, skill_name):
    assert SkillRouter(skills).match(text).name == skill_name


def test_music_tag_searches_for_relaxing_music(skills):
    music = next(s for s in skills if s.capability == "music")
    args = PetHarnessEngine._media_arguments(music, "can you play some relaxing music?")
    assert args == {"action": "search_and_play", "query": "relaxing music"}
    assert PetHarnessEngine._ack_text(music, args, "en") == "I'll play some relaxing music for you."


def test_quick_tag_news_is_limited_to_three_items(skills):
    news = next(s for s in skills if s.capability == "news")
    assert news.tool_policy["defaults"]["limit"] == 3
