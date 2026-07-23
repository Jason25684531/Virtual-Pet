from pathlib import Path

from pet_harness.asset.workflow_patcher import WorkflowPatcher


ROOT = Path(__file__).parents[1] / "ComfyUI_Json"


def test_video_patch_is_isolated_and_uses_one_motion():
    patcher = WorkflowPatcher(ROOT / "AIA_2026_video_gen_260720_api.json")
    motions = patcher.parse_active_motions()
    assert len(motions) == 7
    workflow = patcher.patch_video(image="miku/job/source.png", selector=1, motion_slot=motions[0][1], seed=7, prefix="vp/miku/job/laugh")
    assert workflow["735"]["inputs"]["select"] == 1
    assert workflow["733"] == {"class_type": "LoadImage", "inputs": {"image": "miku/job/source.png"}, "_meta": workflow["733"]["_meta"]}
    assert workflow["708"]["inputs"]["seed"] == 7
    assert workflow["657"]["inputs"]["motion_id_2"] == 0 or motions[0][1] == 2
    assert patcher.fresh()["733"]["class_type"] == "CRTLoadLastMedia"


def test_image_patch_uses_literal_selector_and_removes_router_chain():
    patcher = WorkflowPatcher(ROOT / "AIA_2026_image_gen_260720_api.json")
    workflow = patcher.patch_image(image="miku/source.png", variant="event", seed=9, prefix="vp/miku/job", generation_context="x" * 3000)
    assert workflow["496"]["inputs"]["select"] == 3
    assert all(node not in workflow for node in ("498", "501", "504"))
    assert len(workflow["736"]["inputs"]["value"]) == 2000
