from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any


class WorkflowPatchError(ValueError):
    pass


class WorkflowPatcher:
    def __init__(self, template: str | Path) -> None:
        self.template = Path(template)
        self._source = json.loads(self.template.read_text(encoding="utf-8"))

    def fresh(self) -> dict[str, Any]:
        return copy.deepcopy(self._source)

    @staticmethod
    def _input(workflow: dict[str, Any], node_id: str, key: str) -> dict[str, Any]:
        try:
            inputs = workflow[node_id]["inputs"]
        except KeyError as error:
            raise WorkflowPatchError(f"missing node: {node_id}") from error
        if key not in inputs:
            raise WorkflowPatchError(f"missing input: {node_id}.{key}")
        return inputs

    def parse_active_motions(self) -> list[tuple[str, int, str]]:
        inputs = self._input(self._source, "756", "motion_id_1")
        motions: list[tuple[str, int, str]] = []
        names = {"雀躍大笑": "laugh", "Cheerful Laugh": "laugh", "薄怒嘟嘴": "angry", "Annoyed Pout": "angry", "尷尬擺手": "awkward", "Awkward Hand Wave": "awkward", "無言微翻白眼": "speechless", "Speechless Eye Roll": "speechless", "專心聆聽": "listen", "Attentive Listening": "listen", "愉悅微笑": "idle", "Pleasant Smile": "idle", "Waving": "wave_response"}
        for slot in range(1, 31):
            value = inputs.get(f"motion_id_{slot}")
            if not isinstance(value, list) or not value:
                continue
            node_id = str(value[0])
            title = str(self._source.get(node_id, {}).get("_meta", {}).get("title", ""))
            key = next((item for label, item in names.items() if label in title), re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or f"motion_{slot}")
            motions.append((key, slot, node_id))
        return motions

    def patch_video(self, *, image: str, selector: int, motion_slot: int, seed: int, prefix: str) -> dict[str, Any]:
        workflow = self.fresh()
        self._input(workflow, "735", "select")["select"] = selector
        for node_id in ("733", "728", "730"):
            if node_id not in workflow:
                raise WorkflowPatchError(f"missing node: {node_id}")
            workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": image}, "_meta": workflow[node_id].get("_meta", {})}
        inputs = self._input(workflow, "756", "motion_id_1")
        for slot in range(1, 31):
            key = f"motion_id_{slot}"
            if key not in inputs:
                raise WorkflowPatchError(f"missing input: 756.{key}")
            if slot != motion_slot:
                inputs[key] = 0
        self._input(workflow, "708", "seed")["seed"] = seed
        self._input(workflow, "770", "filename_prefix")["filename_prefix"] = prefix
        return self._inject_secrets(workflow)

    def patch_validation(self, *, image: str, prefix: str) -> dict[str, Any]:
        workflow = self.fresh()
        self._input(workflow, "12", "image")["image"] = image
        self._input(workflow, "16", "filename_prefix")["filename_prefix"] = prefix
        return self._inject_secrets(workflow)

    def patch_background(self, *, character_image: str, room_image: str, prefix: str) -> dict[str, Any]:
        workflow = self.fresh()
        self._input(workflow, "12", "image")["image"] = character_image
        self._input(workflow, "14", "image")["image"] = room_image
        self._input(workflow, "16", "filename_prefix")["filename_prefix"] = prefix
        return self._inject_secrets(workflow)

    def patch_image(self, *, image: str, variant: str, seed: int, prefix: str, generation_context: str = "") -> dict[str, Any]:
        selector = {"og": 1, "development": 2, "event": 3}.get(variant)
        if selector is None:
            raise WorkflowPatchError(f"unknown variant: {variant}")
        workflow = self.fresh()
        self._input(workflow, "472", "image")["image"] = image
        self._input(workflow, "736", "value")["value"] = generation_context[:2000]
        self._input(workflow, "496", "select")["select"] = selector
        for node_id in ("498", "501", "504"):
            workflow.pop(node_id, None)
        for node_id in ("462", "487"):
            self._input(workflow, node_id, "seed")["seed"] = seed
        for node_id in ("475", "467", "492"):
            self._input(workflow, node_id, "filename_prefix")["filename_prefix"] = prefix
        return self._inject_secrets(workflow)

    @staticmethod
    def _inject_secrets(workflow: dict[str, Any]) -> dict[str, Any]:
        key, endpoint = os.getenv("AZURE_OPENAI_API_KEY", ""), os.getenv("AZURE_OPENAI_ENDPOINT", "")
        for node in workflow.values():
            inputs = node.get("inputs", {})
            if node.get("class_type") == "Azure_ChatGPT_Node":
                if key:
                    inputs["api_key"] = key
                if endpoint:
                    inputs["azure_endpoint"] = endpoint
            if node.get("class_type") == "ParallelGen_Azure_EditSubmit":
                # 影像模型可能部署在另一個 Azure 資源:key 與端點都優先取 IMAGES 專用變數。
                images_key = os.getenv("AZURE_OPENAI_IMAGES_API_KEY", "") or key
                if images_key:
                    inputs["api_key"] = images_key
                images_endpoint = os.getenv("AZURE_OPENAI_IMAGES_ENDPOINT", "") or (f"{endpoint.rstrip('/')}/openai/v1/images/generations" if endpoint else "")
                if images_endpoint:
                    inputs["endpoint_url"] = images_endpoint
        return workflow
