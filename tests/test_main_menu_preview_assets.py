from character_library import PROJECT_ROOT
from ui.transparent_window import TransparentWindow


def test_main_menu_preview_uses_project_relative_choppr_assets():
    assert (PROJECT_ROOT / "assets/webm/characters/Choppr/BG_Final.png").is_file()
    assert (PROJECT_ROOT / "assets/webm/characters/Choppr/motions/Idle.webm").is_file()


def test_main_menu_preview_does_not_depend_on_a_conversation_payload():
    calls = []
    window = type(
        "Window",
        (),
        {
            "MAIN_MENU_BACKGROUND": "assets/webm/characters/Choppr/BG_Final.png",
            "MAIN_MENU_CHARACTER_MOTION": "assets/webm/characters/Choppr/motions/Idle.webm",
            "_run_javascript": lambda _self, *args: calls.append(args),
        },
    )()

    TransparentWindow._set_main_menu_preview(window)

    assert calls[0][0] == "setMainMenuPreview"
