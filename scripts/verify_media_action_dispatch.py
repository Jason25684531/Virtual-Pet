"""fix-media-action-motion-dispatch 驗收工具：對指定角色逐一模擬新聞／音樂技能
命中後 _resolve_behavior() 實際算出的 webm_key，離線核對每個角色是否播得出來，
以及是否誤中角色專屬的殘留素材（media-skill-routing「媒體回合的動作在指定角色
上的驗收」場景）。不需要 GUI／音訊／GPU，可重複執行作為回歸檢查。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from character_library import CharacterLibrary
from pet_harness.behavior.behavior_manager import BehaviorManager
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.skill import Skill

CHARACTERS = [
    "char-Adol", "char-Jack", "char-Kai", "char-Luke", "char-Nico", "char-ROG",
    "Choppr", "miku",
]
MEDIA_SKILLS = [
    Skill(name="bahamut_daily_news", description="d", triggers=["news"], behavior="report_news", xp_reward=7),
    Skill(name="youtube_music_playback", description="d", triggers=["music"], behavior="play_music", xp_reward=8),
]
# align-preset-character-interaction 移除後仍殘留在磁碟上的角色專屬檔案；
# 修正後的 webm_key 絕不能解析到這些路徑。
FORBIDDEN_PATH_FRAGMENTS = ("play_music_panel", "news_panel")


# BehaviorManager.resolve() only touches its `store` on the no-skill fallback
# path; every call here passes a real Skill, so that branch never runs and a
# bare stub is enough — no real SQLiteStore/tempfile needed.
_LIBRARY = CharacterLibrary()
_BEHAVIOR_MANAGER = BehaviorManager(SimpleNamespace(), Path(".agentic/behavior/behavior_map.json"))


def _resolve_for(character_id: str, skill: Skill) -> dict:
    stub = SimpleNamespace(
        character_library=_LIBRARY,
        behavior_manager=_BEHAVIOR_MANAGER,
        _character_id=character_id,
        _last_action_tag=None,
    )
    resolved_action, behavior = PetHarnessEngine._resolve_behavior(stub, skill, None)
    motion_path = stub.character_library.get_motion_path(character_id, behavior.webm_key)
    return {
        "character_id": character_id,
        "skill": skill.name,
        "behavior_id": behavior.behavior_id,
        "webm_key": behavior.webm_key,
        "resolved_action_tag": resolved_action["action_tag"] if resolved_action else None,
        "has_declared_motion": stub.character_library.has_declared_motion(character_id, behavior.webm_key),
        "motion_path": motion_path,
    }


def main() -> int:
    rows = [_resolve_for(character_id, skill) for character_id in CHARACTERS for skill in MEDIA_SKILLS]

    failures = []
    for row in rows:
        if not row["has_declared_motion"]:
            failures.append(f"{row['character_id']}/{row['skill']}: webm_key={row['webm_key']} 沒有 manifest 宣告的素材")
        path_lower = (row["motion_path"] or "").lower()
        if any(fragment in path_lower for fragment in FORBIDDEN_PATH_FRAGMENTS):
            failures.append(f"{row['character_id']}/{row['skill']}: 解析到角色專屬殘留素材 {row['motion_path']}")

    print(json.dumps(rows, ensure_ascii=False, indent=2))
    if failures:
        print("\nFAILURES:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"\n{len(rows)} 筆結果全數通過：每個角色的媒體回合 webm_key 都是該角色 manifest 宣告過的動作，" "且沒有角色播放到專屬殘留素材。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
