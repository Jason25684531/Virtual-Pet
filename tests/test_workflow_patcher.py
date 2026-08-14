import json
from pathlib import Path

import pytest

from pet_harness.asset.workflow_patcher import WorkflowPatchError, WorkflowPatcher


ROOT = Path(__file__).parents[1] / "ComfyUI_Json"


def test_video_patch_is_isolated_and_uses_one_motion():
    patcher = WorkflowPatcher(ROOT / "AIA_2026_video_gen_260728.json")
    motions = patcher.parse_active_motions()
    assert len(motions) == 7
    assert {key for key, _, _ in motions} == {"laugh", "angry", "awkward", "speechless", "listen", "idle", "wave_response"}
    workflow = patcher.patch_video(image="miku/job/source.png", selector=1, motion_slot=motions[0][1], seed=7, prefix="vp/miku/job/laugh")
    assert workflow["735"]["inputs"]["select"] == 1
    assert workflow["733"] == {"class_type": "LoadImage", "inputs": {"image": "miku/job/source.png"}, "_meta": workflow["733"]["_meta"]}
    assert workflow["708"]["inputs"]["seed"] == 7
    assert workflow["770"]["inputs"]["filename_prefix"] == "vp/miku/job/laugh"
    assert all(value == 0 for key, value in workflow["756"]["inputs"].items() if key.startswith("motion_id_") and key != f"motion_id_{motions[0][1]}")
    assert patcher.fresh()["733"]["class_type"] == "CRTLoadLastMedia"


def test_validation_and_background_patches_replace_only_runtime_inputs():
    validation = WorkflowPatcher(ROOT / "AIA_2026_character validation_260811_API.json")
    background = WorkflowPatcher(ROOT / "AIA_2026_background_gen_260728.json")

    assert validation.patch_validation(image="upload/character.png", prefix="vp/new/og")["12"]["inputs"]["image"] == "upload/character.png"
    assert validation.patch_validation(image="upload/character.png", prefix="vp/new/og")["16"]["inputs"]["filename_prefix"] == "vp/new/og"
    workflow = background.patch_background(character_image="upload/character.png", room_image="upload/room.png", prefix="vp/new/bg")
    assert workflow["12"]["inputs"]["image"] == "upload/character.png"
    assert workflow["14"]["inputs"]["image"] == "upload/room.png"
    assert workflow["16"]["inputs"]["filename_prefix"] == "vp/new/bg"


def test_patching_requires_expected_nodes(tmp_path):
    template = tmp_path / "workflow.json"
    template.write_text(json.dumps({"12": {"inputs": {"image": "x"}}}), encoding="utf-8")

    with pytest.raises(WorkflowPatchError, match="missing node: 16"):
        WorkflowPatcher(template).patch_validation(image="upload/character.png", prefix="vp/new/og")


def test_image_patch_uses_literal_selector_and_removes_router_chain():
    patcher = WorkflowPatcher(ROOT / "AIA_2026_image_gen_260720.json")
    workflow = patcher.patch_image(image="miku/source.png", variant="event", seed=9, prefix="vp/miku/job", generation_context="x" * 3000)
    assert workflow["496"]["inputs"]["select"] == 3
    assert all(node not in workflow for node in ("498", "501", "504", "509"))
    assert len(workflow["736"]["inputs"]["value"]) == 2000


def test_image_patch_injects_event_prompt_into_node_491():
    patcher = WorkflowPatcher(ROOT / "AIA_2026_image_gen_260720.json")
    workflow = patcher.patch_image(image="miku/source.png", variant="event", seed=9, prefix="vp/miku/job", event_prompt="這個角色手上拿春聯")
    assert workflow["491"]["inputs"]["prompt"] == "這個角色手上拿春聯"


def test_image_patch_leaves_node_491_untouched_when_no_event_prompt():
    patcher = WorkflowPatcher(ROOT / "AIA_2026_image_gen_260720.json")
    template_prompt = patcher.fresh()["491"]["inputs"]["prompt"]
    workflow = patcher.patch_image(image="miku/source.png", variant="og", seed=9, prefix="vp/miku/job")
    assert workflow["491"]["inputs"]["prompt"] == template_prompt
