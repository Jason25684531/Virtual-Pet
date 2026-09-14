"""runtime-maintainability:死碼盤點與保留理由。

把「哪些公開進入點沒有呼叫者」變成一個可執行的清單,而不是一次性的手動掃描 ——
下次有人留下沒有入口的方法時,這個測試會指出來,並逼他寫下保留理由。

掃描同時涵蓋靜態 import 與字串引用(manifest / 技能檔 / bridge 契約 / JSON workflow),
因為這個專案有不少東西是靠名字動態載入的。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.uses_repo_cwd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {
    "venv", "__pycache__", ".git", "outputs", "runtime_cache", "openspec",
    "debug", "data", "assets", "FinalReport", "ComfyUI_Json", "node_modules",
}
_CORPUS_PATTERNS = ("*.py", "*.md", "*.json", "*.js", "*.html", "*.ini", "*.txt")
# 獨立的研究 spike:有自己的 __main__,靠 docs/spikes/ 的紀錄存在,不是產品執行路徑。
_SCAN_SKIP_DIRS = _SKIP_DIRS | {"tools"}

# 沒有靜態呼叫者但必須保留,以及為什麼。刪掉任何一項都會弄壞看不見的東西。
RETAINED_WITHOUT_CALLERS = {
    "TransparentWindow.contextMenuEvent": "Qt 事件覆寫,由框架而非我們的程式碼呼叫",
    "EchoesWebPage.javaScriptConsoleMessage": "QWebEnginePage 覆寫,由 Qt 在 JS 有輸出時呼叫",
    "PetHarnessEngine.forget_memory": "驗收報告列為尚未接上 UI 的必要能力,刪掉等於刪掉唯一的實作",
}


def _source_files() -> list[Path]:
    return [
        path
        for path in PROJECT_ROOT.rglob("*.py")
        if not any(part in _SCAN_SKIP_DIRS for part in path.relative_to(PROJECT_ROOT).parts)
        and "tests" not in path.relative_to(PROJECT_ROOT).parts
    ]


def _reference_corpus() -> str:
    blobs = []
    for pattern in _CORPUS_PATTERNS:
        for path in PROJECT_ROOT.rglob(pattern):
            if any(part in _SCAN_SKIP_DIRS for part in path.relative_to(PROJECT_ROOT).parts):
                continue
            # 保留清單本身寫了這些名字;把自己算進語料會讓每一項都「看起來有引用」。
            if path.resolve() == Path(__file__).resolve():
                continue
            try:
                blobs.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return "\n".join(blobs)


def _public_entry_points_without_callers() -> dict[str, str]:
    corpus = _reference_corpus()
    orphans: dict[str, str] = {}
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) or item.name.startswith("_"):
                    continue
                if len(re.findall(rf"\b{re.escape(item.name)}\b", corpus)) <= 1:
                    orphans[f"{node.name}.{item.name}"] = f"{path.relative_to(PROJECT_ROOT)}:{item.lineno}"
    return orphans


def test_every_uncalled_entry_point_has_a_written_retention_reason():
    """6.1/6.2:沒有入口又沒有保留理由的,就是該刪的死碼。"""
    orphans = _public_entry_points_without_callers()
    undocumented = {name: where for name, where in orphans.items() if name not in RETAINED_WITHOUT_CALLERS}
    assert not undocumented, (
        "以下公開進入點沒有任何靜態或字串引用,也沒有保留理由 —— 請移除,"
        f"或在 RETAINED_WITHOUT_CALLERS 寫下為什麼必須留著:{undocumented}"
    )


def test_retention_list_does_not_rot():
    """保留理由清單本身也會過期:項目被刪掉或重新接上後要從清單移除。"""
    orphans = _public_entry_points_without_callers()
    stale = sorted(set(RETAINED_WITHOUT_CALLERS) - set(orphans))
    assert not stale, f"這些項目已經有呼叫者或已不存在,請從保留清單移除:{stale}"


def test_removed_dead_code_stays_removed():
    """本次變更移除的兩個無入口 CRUD 方法;重新出現代表又有人加了沒有呼叫者的程式碼。"""
    from pet_harness.asset.comfyui_client import ComfyUIClient
    from pet_harness.character.registry import CharacterRegistry

    assert not hasattr(ComfyUIClient, "cancel_prompt")
    assert not hasattr(CharacterRegistry, "update_profile")


def test_dynamic_entry_points_still_resolve():
    """動態載入的進入點不能因為清理而消失:技能檔、bridge 契約與角色 manifest。"""
    from pet_harness.skills.skill_loader import SkillLoader
    from ui.harness_ui_bridge import BRIDGE_CONTRACT, HarnessUiBridge

    skills = {skill.name for skill in SkillLoader(PROJECT_ROOT / ".agentic" / "skills").load_skills()}
    assert {"youtube_music_playback", "bahamut_daily_news"} <= skills

    for method in BRIDGE_CONTRACT["js_to_python"]:
        assert hasattr(HarnessUiBridge, method), method
