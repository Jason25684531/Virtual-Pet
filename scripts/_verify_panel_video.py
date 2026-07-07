"""Headless-ish verification that panel.webm renders full-frame (no scale/shift).

Loads ui/web_container/index.html in QWebEngine at a fixed 1600x900 viewport,
triggers playPanelVideo() with the real Chopper News_Panel.webm, then reads back
the #panel-video bounding rect and compares it to the viewport. A correct fix
means the panel video fills the whole frame (the baked scroll then lands where
authored), instead of being crammed into a small corner box.
"""
import json
import os
import sys
from pathlib import Path

from PyQt5.QtCore import QTimer, QUrl
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWebEngineWidgets import QWebEngineView

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "ui" / "web_container" / "index.html"
WEBM = ROOT / "assets" / "webm" / "characters" / "Choppr" / "motions" / "News_Panel.webm"
SHOT = ROOT / "artifacts" / "panel-video-fullframe-check.png"

VW, VH = 1600, 900

result = {"ok": False, "steps": []}


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(VW, VH)
    view.show()

    def log(msg):
        result["steps"].append(msg)
        print("[verify]", msg, flush=True)

    def run_js(js, cb):
        view.page().runJavaScript(js, cb)

    def on_load(ok):
        if not ok:
            log("page load FAILED")
            QTimer.singleShot(0, lambda: finish(2))
            return
        log("page loaded")
        QTimer.singleShot(600, trigger_panel)

    def trigger_panel():
        src = WEBM.as_uri()
        run_js(f"window.playPanelVideo({json.dumps(src)}, true, true); 'started'",
               lambda _r: QTimer.singleShot(900, measure))

    def measure():
        js = """
        (function () {
          var v = document.getElementById('panel-video');
          var r = v.getBoundingClientRect();
          var cs = getComputedStyle(v);
          return JSON.stringify({
            vw: window.innerWidth, vh: window.innerHeight,
            x: Math.round(r.left), y: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height),
            display: cs.display, objectFit: cs.objectFit,
            position: cs.position
          });
        })();
        """
        run_js(js, on_measure)

    def on_measure(raw):
        try:
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log(f"measure parse error: {exc} raw={raw!r}")
            finish(2)
            return
        result["rect"] = data
        log(f"rect={data}")
        full_w = abs(data["w"] - data["vw"]) <= 2
        full_h = abs(data["h"] - data["vh"]) <= 2
        at_origin = abs(data["x"]) <= 2 and abs(data["y"]) <= 2
        visible = data["display"] != "none"
        result["ok"] = bool(full_w and full_h and at_origin and visible)
        log(f"full_w={full_w} full_h={full_h} at_origin={at_origin} visible={visible}")
        view.grab().save(str(SHOT))
        log(f"screenshot -> {SHOT}")
        finish(0 if result["ok"] else 1)

    def finish(code):
        print("RESULT", json.dumps(result), flush=True)
        app.exit(code)

    view.loadFinished.connect(on_load)
    view.load(QUrl.fromLocalFile(str(INDEX)))
    # absolute safety timeout
    QTimer.singleShot(8000, lambda: finish(3))
    sys.exit(app.exec_())


if __name__ == "__main__":
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
    main()
