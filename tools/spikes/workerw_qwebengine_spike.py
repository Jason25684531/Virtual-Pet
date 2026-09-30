"""Isolated feasibility spike: can ECHOES's visual layer live on the Windows
desktop layer (Progman/WorkerW), beneath desktop icons, without breaking icon
interaction or QtWebEngine rendering?

Standalone script. Does NOT import ui/, harness/, main.py or any production
module — never wired into production startup.

Usage (run with the project venv):
    .venv/Scripts/python.exe tools/spikes/workerw_qwebengine_spike.py --spike a --duration 20
    .venv/Scripts/python.exe tools/spikes/workerw_qwebengine_spike.py --spike b --duration 20
    .venv/Scripts/python.exe tools/spikes/workerw_qwebengine_spike.py --spike c --duration 25 --transparent
    .venv/Scripts/python.exe tools/spikes/workerw_qwebengine_spike.py --spike a --duration 60 --reattach-test
"""
import argparse
import atexit
import ctypes
import ctypes.wintypes as wintypes
import os
import platform
import sys
from pathlib import Path

LOG_PREFIX = "[WORKERW SPIKE]"


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


# ── Win32 bindings (ctypes only, no pywin32 dependency) ────────────────────

user32 = ctypes.windll.user32
WM_SPAWN_WORKERW = 0x052C
SMTO_NORMAL = 0x0000

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _get_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def enum_top_level_windows() -> list[tuple[int, str]]:
    """Top-level windows in current Z-order (top -> bottom), with class names."""
    windows: list[tuple[int, str]] = []

    def callback(hwnd, _lparam):
        windows.append((hwnd, _get_class_name(hwnd)))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return windows


def discover_desktop_target() -> tuple[int, int | None]:
    """Run the standard WorkerW mount discovery, logging before/after state.

    Returns (target_hwnd, shell_defview_owner_hwnd_or_None).
    """
    progman = user32.FindWindowW("Progman", None)
    log(f"progman_hwnd={progman}")

    before = enum_top_level_windows()
    workerw_before = sum(1 for _, cls in before if cls == "WorkerW")
    log(f"workerw_count_before_0x052C={workerw_before}")

    result = ctypes.c_ulong()
    sent = user32.SendMessageTimeoutW(
        progman, WM_SPAWN_WORKERW, 0, 0, SMTO_NORMAL, 1000, ctypes.byref(result)
    )
    log(f"send_0x052C_ok={bool(sent)}")

    after = enum_top_level_windows()
    workerw_after = sum(1 for _, cls in after if cls == "WorkerW")
    log(f"workerw_count_after_0x052C={workerw_after}")
    log(f"0x052C_changed_hierarchy={workerw_after != workerw_before}")

    shell_owner = None
    next_workerw = None
    for index, (hwnd, cls) in enumerate(after):
        if cls in ("WorkerW", "Progman"):
            child = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            if child:
                shell_owner = hwnd
                if index + 1 < len(after) and after[index + 1][1] == "WorkerW":
                    next_workerw = after[index + 1][0]
                break

    log(f"shell_defview_owner_hwnd={shell_owner}")
    log(f"workerw_sibling_hwnd={next_workerw}")

    if next_workerw:
        log(f"target_hwnd={next_workerw} source=workerw_sibling")
        return next_workerw, shell_owner
    log(f"target_hwnd={progman} source=progman_fallback (no WorkerW sibling found)")
    return progman, shell_owner


def attach(hwnd: int, target: int) -> bool:
    prev_parent = user32.GetParent(hwnd)
    new_parent = user32.SetParent(hwnd, target)
    # SetParent's return is the *previous* parent HWND; for a window that had no
    # parent this is legitimately 0 on success (see section 9: don't trust a
    # truthy/falsy read alone). Disambiguate with GetLastError.
    ok = new_parent != 0 or ctypes.get_last_error() == 0
    log(f"set_parent_success={ok} hwnd={hwnd} prev_parent={prev_parent} target={target}")
    return ok


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def reposition_after_attach(hwnd: int, target: int, screen_x: int, screen_y: int, w: int, h: int) -> None:
    """SetParent keeps the widget's old geometry, but that geometry was set in
    screen coordinates while it's now interpreted relative to `target`'s
    client origin (Progman can span a monitor at a negative x/y offset).
    Convert the intended screen position into target-local coordinates."""
    pt = _POINT(screen_x, screen_y)
    user32.MapWindowPoints(0, target, ctypes.byref(pt), 1)
    log(f"reposition screen=({screen_x},{screen_y}) -> target_local=({pt.x},{pt.y})")
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    user32.SetWindowPos(hwnd, 0, pt.x, pt.y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)


def detach(hwnd: int) -> bool:
    ok = bool(user32.SetParent(hwnd, None))
    log(f"detach_success={ok} hwnd={hwnd}")
    return ok


# ── Environment banner ──────────────────────────────────────────────────────

def log_environment() -> None:
    log(f"os={platform.platform()}")
    try:
        import PyQt5.QtCore as qc
        log(f"pyqt5={qc.PYQT_VERSION_STR} qt_runtime={qc.QT_VERSION_STR}")
    except Exception as exc:  # pragma: no cover - diagnostic only
        log(f"pyqt5_import_failed={exc}")


# ── Spike A: plain opaque QWidget mounted onto the desktop layer ───────────

def build_spike_a_widget():
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QLabel, QWidget

    widget = QWidget(
        None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
    )
    widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # never block real icon input
    widget.setAttribute(Qt.WA_ShowWithoutActivating, True)
    widget.setStyleSheet("background-color: #C81E3A;")
    widget.setGeometry(40, 40, 480, 640)

    label = QLabel("WORKERW SPIKE A\nshould render BEHIND desktop icons", widget)
    label.setStyleSheet("color: white; font-size: 22px; font-weight: bold;")
    label.setGeometry(20, 20, 440, 100)
    label.setWordWrap(True)
    return widget


# ── Spike B/C: QWebEngineView mounted onto the desktop layer ───────────────

IDLE_WEBM = "assets/backup/char-asuna/motions/development/idle.webm"


def build_spike_html(project_root: Path, with_video: bool, transparent: bool) -> str:
    bg = "transparent" if transparent else "#00478C"
    body_extra = ""
    if with_video:
        video_url = (project_root / IDLE_WEBM).resolve().as_uri()
        body_extra = f"""
      <video id="v" autoplay loop muted playsinline src="{video_url}"></video>
      <div id="pulse">SPIKE C CSS ANIMATION</div>
    """
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body {{ margin: 0; background: {bg}; overflow: hidden; }}
  #label {{ color: white; font-size: 34px; font-family: sans-serif; padding: 24px; }}
  video {{ width: 320px; height: 320px; object-fit: contain; position: absolute; right: 20px; top: 20px; }}
  #pulse {{
    position: absolute; left: 20px; bottom: 20px; color: #7CFF7C; font-family: sans-serif;
    font-size: 20px; transform: translateY(0); opacity: 1;
    animation: pulse 1.2s ease-in-out infinite alternate;
  }}
  @keyframes pulse {{
    from {{ opacity: 0.3; transform: translateY(0px); }}
    to   {{ opacity: 1.0; transform: translateY(-16px); }}
  }}
</style></head>
<body>
  <div id="label">WORKERW QWEBENGINE SPIKE{" C" if with_video else " B"}</div>
  {body_extra}
</body></html>"""


class SpikeWebPage:
    """Factory closure so the console-forwarding QWebEnginePage stays local
    to this file (no dependency on ui/web_page_widgets.py)."""

    @staticmethod
    def build(parent):
        from PyQt5.QtWebEngineWidgets import QWebEnginePage

        class _Page(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, message, line_number, source_id):
                log(f"JS[{level}] {message} (line {line_number}, {source_id})")

        return _Page(parent)


def build_spike_bc_window(project_root: Path, with_video: bool, transparent: bool):
    from PyQt5.QtCore import Qt, QUrl
    from PyQt5.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView
    from PyQt5.QtWidgets import QMainWindow

    window = QMainWindow(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
    window.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    window.setAttribute(Qt.WA_ShowWithoutActivating, True)
    if transparent:
        window.setAttribute(Qt.WA_TranslucentBackground, True)
        window.setStyleSheet("background: transparent;")
    window.setGeometry(40, 40, 520, 640)

    view = QWebEngineView(window)
    if transparent:
        view.setStyleSheet("background: transparent;")
        view.page().setBackgroundColor(Qt.transparent)
    view.setPage(SpikeWebPage.build(view))
    view.setContextMenuPolicy(Qt.NoContextMenu)

    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
    settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
    settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
    settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)

    html = build_spike_html(project_root, with_video=with_video, transparent=transparent)
    view.setHtml(html, baseUrl=QUrl.fromLocalFile(str(project_root) + "/"))
    window.setCentralWidget(view)
    return window


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spike", choices=["a", "b", "c"], required=True)
    parser.add_argument("--duration", type=float, default=20.0, help="seconds before auto cleanup+exit")
    parser.add_argument("--transparent", action="store_true", help="spike b/c: use transparent background instead of opaque")
    parser.add_argument("--reattach-test", action="store_true", help="poll target validity every 1s and re-discover/re-attach if it dies (e.g. explorer.exe restart)")
    args = parser.parse_args()

    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--enable-logging=stderr")

    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    log_environment()

    project_root = Path(__file__).resolve().parents[2]

    app = QApplication(sys.argv)

    target, shell_owner = discover_desktop_target()

    if args.spike == "a":
        widget = build_spike_a_widget()
    else:
        widget = build_spike_bc_window(
            project_root, with_video=(args.spike == "c"), transparent=args.transparent
        )

    hwnd = int(widget.winId())
    screen_geom = widget.geometry()  # captured before SetParent changes coordinate space
    attach(hwnd, target)
    reposition_after_attach(hwnd, target, screen_geom.x(), screen_geom.y(), screen_geom.width(), screen_geom.height())
    widget.show()

    state = {"target": target}

    cleaned_up = {"done": False}

    def cleanup():
        if cleaned_up["done"]:
            return
        cleaned_up["done"] = True
        log("cleanup: detaching and closing spike window")
        try:
            detach(hwnd)
        except Exception as exc:  # pragma: no cover - best-effort cleanup
            log(f"cleanup: detach failed: {exc}")
        widget.close()

    atexit.register(cleanup)
    app.aboutToQuit.connect(cleanup)

    def status_tick():
        alive = bool(user32.IsWindow(state["target"]))
        parent = user32.GetParent(hwnd)
        log(f"status_tick target_alive={alive} current_parent={parent} expected_target={state['target']}")

    status_timer = QTimer()
    status_timer.timeout.connect(status_tick)
    status_timer.start(5000)

    reattach_timer = None
    if args.reattach_test:
        def reattach_tick():
            if not user32.IsWindow(state["target"]):
                log("reattach: target_hwnd_invalid -> rediscovering (explorer.exe restart?)")
                new_target, _owner = discover_desktop_target()
                state["target"] = new_target
                attach(hwnd, new_target)
                reposition_after_attach(hwnd, new_target, screen_geom.x(), screen_geom.y(), screen_geom.width(), screen_geom.height())

        reattach_timer = QTimer()
        reattach_timer.timeout.connect(reattach_tick)
        reattach_timer.start(1000)

    QTimer.singleShot(int(args.duration * 1000), app.quit)

    log(f"spike_started spike={args.spike} duration={args.duration} transparent={args.transparent} reattach_test={args.reattach_test}")
    exit_code = app.exec_()
    cleanup()
    log(f"spike_ended exit_code={exit_code}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
