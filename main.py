"""
ECHOES — 程式進入點
啟動 PyQt5 應用程式，顯示透明桌面寵物視窗。

啟動選項：
  --brain-mode harness   不啟動 OpenClaw WebSocket（預設，適合 UI smoke test）
  --brain-mode openclaw  啟動 OpenClaw WebSocket（完整橋接測試）
  --brain-mode auto      嘗試 OpenClaw，失敗後降級為 harness

也可透過環境變數設定（CLI 優先）：
  ECHOES_BRAIN_MODE=harness|openclaw|auto
"""

import argparse
import sys
import signal

from brain_mode import resolve_brain_mode, is_openclaw_enabled

# 情緒 → WebM 檔名對應表（供未來 VM/Sensor 模組使用）
EMOTION_MAP = {
    "開心": "laugh.webm",
    "生氣": "angry.webm",
    "尷尬": "awkward.webm",
    "無言": "speechless.webm",
    "聆聽": "listen.webm",
    "預設": "idle.webm",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ECHOES desktop pet host",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--brain-mode",
        dest="brain_mode",
        default=None,
        metavar="MODE",
        help=(
            "brain 連線模式 (harness|openclaw|auto)。\n"
            "  harness  — 不啟動 OpenClaw WebSocket（預設）\n"
            "  openclaw — 啟動 OpenClaw WebSocket\n"
            "  auto     — 嘗試 OpenClaw，失敗後降級為 harness\n"
            "也可透過環境變數 ECHOES_BRAIN_MODE 設定（CLI 優先）。"
        ),
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    brain_mode = resolve_brain_mode(args.brain_mode)
    print(f"[ECHOES] brain mode: {brain_mode}")

    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    from ui.transparent_window import TransparentWindow

    app = QApplication(sys.argv)

    # 讓 Ctrl+C 可以正常終止程序
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app.setQuitOnLastWindowClosed(False)

    # 每 200ms 讓 Python 處理一次訊號（PyQt 事件迴圈不會主動讓出 CPU 給 Python）
    # 注意：parent=app 防止 Python GC 從非主執行緒銷毀 QTimer
    app._sigint_timer = QTimer(parent=app)
    app._sigint_timer.start(200)
    app._sigint_timer.timeout.connect(lambda: None)

    window = TransparentWindow(brain_mode=brain_mode)
    window.show()

    if is_openclaw_enabled(brain_mode):
        from api_client.vm_connector import VMConnector

        window.set_action_status("正在連線 OpenClaw 大腦...", tone="working", timeout_ms=2500)

        vm_connector = VMConnector(parent=app)
        vm_connector.message_received.connect(window.dispatch_action)
        vm_connector.start()

        def shutdown_vm_connector():
            vm_connector.stop()
            if vm_connector.isRunning():
                vm_connector.wait(3000)

        app.aboutToQuit.connect(shutdown_vm_connector)
    else:
        print("[ECHOES] OpenClaw connection skipped in harness mode.")
        window.set_action_status("Harness 模式，OpenClaw 未啟動。", tone="idle", timeout_ms=3000)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
