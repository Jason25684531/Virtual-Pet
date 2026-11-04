"""Small, deterministic answer table for the live demo."""

from __future__ import annotations

from dataclasses import dataclass
import re


_NON_WORD = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)


@dataclass(frozen=True)
class DemoScript:
    name: str
    keyword_groups: tuple[tuple[str, ...], ...]
    answer_en: str
    answer_zh: str | None = None

    def matches(self, text: str) -> bool:
        normalized = _normalize(text)
        return all(
            any(_normalize(keyword) in normalized for keyword in group)
            for group in self.keyword_groups
        )

    def answer_for(self, text: str) -> str:
        if self.answer_zh and _contains_cjk(text):
            return self.answer_zh
        return self.answer_en


DEMO_SCRIPTS = (
    DemoScript(
        name="home_decor",
        keyword_groups=(
            ("home decor", "居家", "家居"),
            ("cute", "recommend", "推薦", "擺設", "裝飾"),
        ),
        answer_en=(
            "Absolutely! Try a small ceramic vase, a warm table lamp, and a cute "
            "plant pot. A few soft colors and natural textures can make your "
            "space feel cozy without feeling cluttered."
        ),
        answer_zh=(
            "想打造療癒又可愛的小家，可以擺上奶油色抱枕、雲朵造型小夜燈，"
            "再搭配幾盆迷你綠植。牆面掛上小幅插畫或照片，搭配木質小物與柔和燈串，"
            "整體就會變得溫暖又有生活感"
        ),
    ),
    DemoScript(
        name="elden_ring",
        keyword_groups=(
            ("elden ring", "艾爾登", "老頭環"),
            ("basics", "how to play", "forgot", "基礎", "怎麼玩", "複習"),
        ),
        answer_en=(
            "Start by remembering three things: manage your stamina, learn the "
            "right dodge timing, and watch enemy patterns. Explore at your own "
            "pace, upgrade your weapon when you can, and do not be afraid to run "
            "past a fight."
        ),
        answer_zh=(
            "《艾爾登法環》是一款開放世界動作角色扮演遊戲。玩家探索地圖、"
            "擊敗敵人與頭目，取得裝備與符文提升角色。可自由選擇近戰、魔法或遠程玩法，"
            "並透過探索與戰鬥逐步解開世界的故事。"
        ),
    ),
)


def match(text: str) -> tuple[DemoScript, str] | None:
    for script in DEMO_SCRIPTS:
        if script.matches(text):
            return script, script.answer_for(text)
    return None


def _normalize(text: str) -> str:
    return _NON_WORD.sub("", str(text or "").casefold())


def _contains_cjk(text: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in str(text or ""))
