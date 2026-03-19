"""
ECHOES — ComfyUI 本機算圖 Client
負責：載入 Workflow 模板 → 動態改寫節點 → 提交 Prompt → WebSocket 監聽進度
     → 解析 History 取得輸出檔 → 搬移歸檔至 assets/webm/
"""

import json
import os
import shutil
import uuid

import requests
import websocket

from character_library import MOTION_SPECS

# ── 常數設定 ────────────────────────────────────────────────

COMFYUI_HOST = "127.0.0.1"
COMFYUI_PORT = 8188
COMFYUI_OUTPUT_DIR = r"C:\2026_AIA\ComfyUI_windows_portable\ComfyUI\output"

WORKFLOW_TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ComfyUI_API", "AIA_2026_動態生成Only.json",
)

ASSETS_WEBM_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "webm",
)

# 輸入節點 ID：VHS_LoadImagesPath
_NODE_IMAGE_INPUT = "456"
# 動作載入節點 ID：AutoMotionLoader
_NODE_MOTION_LOADER = "435"
# 輸出節點 ID：VHS_VideoCombine (帶 Alpha WebM)
_NODE_VIDEO_OUTPUT = "454"
# 文字 Prompt 節點 ID：WanVideoTextEncodeCached
_NODE_TEXT_ENCODE = "368"


# ── 核心類別 ────────────────────────────────────────────────

class ComfyUIClient:
    """與本機 ComfyUI 溝通的 Client，封裝完整算圖流程。"""

    def __init__(
        self,
        host: str = COMFYUI_HOST,
        port: int = COMFYUI_PORT,
        output_dir: str = COMFYUI_OUTPUT_DIR,
    ):
        self._host = host
        self._port = port
        self._output_dir = output_dir
        self._base_url = f"http://{host}:{port}"
        self._client_id = uuid.uuid4().hex[:8]

    # ── 公開 API ─────────────────────────────────────────

    def generate(
        self,
        image_dir: str,
        target_dir: str,
        on_progress=None,
        positive_prompt: str = "",
        negative_prompt: str = "",
    ):
        """
        完整算圖流程（阻塞式，應在背景執行緒呼叫）。

        :param image_dir:        角色圖片所在的資料夾絕對路徑
        :param target_dir:       動作影片歸檔目標資料夾
        :param on_progress:      進度回呼 callback(percent: int)，0-100
        :param positive_prompt:  正向描述文字（留空則使用 JSON 預設值）
        :param negative_prompt:  負向描述文字（留空則使用 JSON 預設值）
        :return:                 歸檔後的檔案路徑字典 {motion_key: absolute_path}
        :raises ConnectionError: ComfyUI 未啟動
        :raises RuntimeError:    算圖失敗或輸出不符預期
        """
        archived = {}
        total_motions = len(MOTION_SPECS)

        for motion_index, motion_spec in enumerate(MOTION_SPECS):
            print(
                f"[ECHOES] 生成動作 {motion_index + 1}/{total_motions}: "
                f"{motion_spec['title']} ({motion_spec['key']})"
            )

            try:
                workflow = self._load_workflow()
                self._set_image_directory(workflow, image_dir)
                self._set_motion_index(workflow, motion_index)
                if positive_prompt or negative_prompt:
                    self._set_prompts(workflow, positive_prompt, negative_prompt)

                prompt_id = self._submit_prompt(workflow)
                self._listen_progress(
                    prompt_id,
                    self._make_progress_callback(on_progress, motion_index, total_motions),
                )

                output_files = self._fetch_output_filenames(prompt_id)
                motion_path = self._collect_motion_asset(
                    output_files,
                    target_dir,
                    motion_spec,
                )
                if motion_path:
                    archived[motion_spec["key"]] = motion_path
            except Exception as exc:
                raise RuntimeError(
                    f"動作 {motion_spec['title']} ({motion_spec['key']}) 生成失敗: {exc}"
                ) from exc

        return archived

    def check_connection(self) -> bool:
        """檢查 ComfyUI 是否在線。"""
        try:
            r = requests.get(f"{self._base_url}/system_stats", timeout=5)
            return r.status_code == 200
        except requests.ConnectionError:
            return False

    # ── Workflow 模板操作 ─────────────────────────────────

    @staticmethod
    def _load_workflow() -> dict:
        """讀取 JSON 工作流模板。"""
        with open(WORKFLOW_TEMPLATE, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _set_image_directory(workflow: dict, image_dir: str):
        """動態修改 node 456 的 directory，指向使用者的圖片資料夾。"""
        workflow[_NODE_IMAGE_INPUT]["inputs"]["directory"] = image_dir

    @staticmethod
    def _set_motion_index(workflow: dict, motion_index: int):
        """指定本次 prompt 要輸出的動作序號。"""
        workflow[_NODE_MOTION_LOADER]["inputs"]["index"] = motion_index

    @staticmethod
    def _set_prompts(
        workflow: dict,
        positive_prompt: str,
        negative_prompt: str,
    ):
        """動態覆寫 node 368 的正/負向 prompt（空字串則保留 JSON 預設值）。"""
        node_inputs = workflow[_NODE_TEXT_ENCODE]["inputs"]
        if positive_prompt:
            node_inputs["positive_prompt"] = positive_prompt
        if negative_prompt:
            node_inputs["negative_prompt"] = negative_prompt

    # ── Prompt 提交 ──────────────────────────────────────

    def _submit_prompt(self, workflow: dict) -> str:
        """POST workflow 至 ComfyUI，回傳 prompt_id。"""
        payload = {
            "prompt": workflow,
            "client_id": self._client_id,
        }
        try:
            resp = requests.post(
                f"{self._base_url}/prompt",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.ConnectionError:
            raise ConnectionError(
                f"無法連線至 ComfyUI ({self._base_url})，請確認已啟動。"
            )

        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI 回傳異常：{data}")
        return prompt_id

    # ── WebSocket 進度監聽 ───────────────────────────────

    def _listen_progress(self, prompt_id: str, on_progress=None):
        """
        透過 WebSocket 監聽算圖進度，直到該 prompt 執行完畢。
        此方法為阻塞式，請在背景執行緒中呼叫。
        """
        ws_url = f"ws://{self._host}:{self._port}/ws?clientId={self._client_id}"
        ws = websocket.create_connection(ws_url, timeout=None)
        try:
            while True:
                raw = ws.recv()
                if not raw:
                    continue

                # ComfyUI 偶爾發送 binary frame（圖片預覽），直接跳過
                if isinstance(raw, bytes):
                    continue

                msg = json.loads(raw)
                msg_type = msg.get("type")
                data = msg.get("data", {})

                # 進度更新
                if msg_type == "progress":
                    value = data.get("value", 0)
                    max_val = data.get("max", 1)
                    if on_progress and max_val > 0:
                        percent = int(value / max_val * 100)
                        on_progress(min(percent, 100))

                # 某節點執行完成 / 整個 prompt 結束
                if msg_type == "executing":
                    if data.get("prompt_id") != prompt_id:
                        continue
                    # node 為 None 表示此 prompt 全部結束
                    if data.get("node") is None:
                        if on_progress:
                            on_progress(100)
                        break

                # 執行錯誤
                if msg_type == "execution_error":
                    if data.get("prompt_id") == prompt_id:
                        node_id = data.get("node_id", "?")
                        err_msg = data.get("exception_message", "未知錯誤")
                        raise RuntimeError(
                            f"ComfyUI 算圖錯誤 (node {node_id}): {err_msg}"
                        )
        finally:
            ws.close()

    @staticmethod
    def _make_progress_callback(on_progress, motion_index: int, total_motions: int):
        if not on_progress:
            return None

        def _callback(percent: int):
            overall = int(((motion_index * 100) + percent) / total_motions)
            on_progress(min(overall, 100))

        return _callback

    # ── History 解析 ─────────────────────────────────────

    def _fetch_output_filenames(self, prompt_id: str) -> list[str]:
        """從 /history API 解析 node 454 輸出的 WebM 檔名清單。"""
        resp = requests.get(
            f"{self._base_url}/history/{prompt_id}",
            timeout=30,
        )
        resp.raise_for_status()

        history = resp.json()
        prompt_data = history.get(prompt_id)
        if not prompt_data:
            raise RuntimeError(f"找不到 prompt_id={prompt_id} 的歷史紀錄。")

        outputs = prompt_data.get("outputs", {})
        node_output = outputs.get(_NODE_VIDEO_OUTPUT, {})

        # VHS_VideoCombine 的輸出結構：{"gifs": [{"filename": "...", ...}, ...]}
        gifs = node_output.get("gifs", [])
        if not gifs:
            raise RuntimeError(
                f"Node {_NODE_VIDEO_OUTPUT} 的輸出為空，算圖可能未成功。"
            )

        # 按檔名中的序號排序，確保順序穩定
        filenames = sorted(item["filename"] for item in gifs)
        return filenames

    # ── 檔案歸檔與重命名 ────────────────────────────────

    def _collect_motion_asset(
        self,
        output_files: list[str],
        target_dir: str,
        motion_spec: dict,
    ) -> str | None:
        """
        將單次 prompt 產出的 WebM 搬移至指定角色目錄並標準化命名。
        """
        os.makedirs(target_dir, exist_ok=True)

        if not output_files:
            print(f"[ECHOES] 警告: 動作 {motion_spec['key']} 未取得任何輸出檔。")
            return None

        if len(output_files) > 1:
            print(
                f"[ECHOES] 警告: 動作 {motion_spec['key']} 預期 1 支影片，"
                f"實際取得 {len(output_files)} 支。將使用第一支。"
            )

        src_name = output_files[0]
        src_path = os.path.join(self._output_dir, src_name)
        dst_name = motion_spec["filename"]
        dst_path = os.path.join(target_dir, dst_name)

        if not os.path.isfile(src_path):
            print(f"[ECHOES] 警告: 來源檔案不存在，跳過: {src_path}")
            return None

        if os.path.exists(dst_path):
            os.remove(dst_path)

        shutil.move(src_path, dst_path)
        print(f"[ECHOES] 歸檔: {src_name} → {dst_name}")
        return dst_path
