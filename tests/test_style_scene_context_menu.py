from ui.transparent_window import build_style_scene_menu_items


def test_style_scene_menu_marks_ready_active_current_and_follow():
    items = build_style_scene_menu_items(
        "pet",
        [
            {"variant": "og", "state": "ready", "is_active": True},
            {"variant": "event", "state": "generating", "is_active": False},
            {"variant": "empty", "state": "empty", "is_active": False},
        ],
        [{"scene_id": "og", "is_current": True}],
        "follow",
        False,
    )

    assert items[0]["checked"] is False
    assert next(item for item in items if item.get("variant") == "og")["enabled"]
    assert not next(item for item in items if item.get("variant") == "event")["enabled"]
    assert not next(item for item in items if item.get("variant") == "empty")["enabled"]
    assert next(item for item in items if item["kind"] == "follow")["checked"]
    assert next(item for item in items if item.get("scene_id") == "og")["checked"]


def test_style_scene_menu_fills_missing_scenes_as_disabled():
    items = build_style_scene_menu_items("pet", [], [{"scene_id": "og"}], "manual", True)
    scenes = [item for item in items if item["kind"] == "scene"]

    assert [item["scene_id"] for item in scenes] == [
        "og",
        "development_a",
        "development_b",
        "event",
    ]
    assert [item["enabled"] for item in scenes] == [True, False, False, False]

