#!/usr/bin/env python3
"""
ECHOES Linux 環境驗證工具

用途：
1. 檢查 PyQt5 / Qt WebEngine 常見共享庫是否缺漏。
2. 驗證 VMConnector 的 Linux token 探測順序。
3. 啟動微型 QWebEngineView，探測 WebGL 與透明背景設定。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

COMMON_QTWEBENGINE_TARGETS = (
    ("QtWebEngineProcess", Path("Qt5/libexec/QtWebEngineProcess")),
    ("QtWebEngineCore", Path("Qt5/lib/libQt5WebEngineCore.so.5")),
    ("xcb platform plugin", Path("Qt5/plugins/platforms/libqxcb.so")),
)


def locate_pyqt5_root() -> tuple[Path | None, str | None]:
    try:
        pyqt5_module = importlib.import_module("PyQt5")
    except Exception as exc:  # pragma: no cover - runtime-dependent
        return None, str(exc)

    module_file = getattr(pyqt5_module, "__file__", None)
    if not module_file:
        return None, "PyQt5 模組已載入，但找不到 __file__。"
    return Path(module_file).resolve().parent, None


def run_ldd(target: Path) -> dict[str, Any]:
    if not target.exists():
        return {
            "target": str(target),
            "exists": False,
            "missing": [],
            "status": "missing-target",
        }

    try:
        result = subprocess.run(
            ["ldd", str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except FileNotFoundError:
        return {
            "target": str(target),
            "exists": True,
            "missing": [],
            "status": "ldd-unavailable",
        }

    missing = []
    for line in result.stdout.splitlines():
        if "not found" not in line:
            continue
        library_name = line.split("=>", 1)[0].strip()
        missing.append(library_name)

    return {
        "target": str(target),
        "exists": True,
        "missing": missing,
        "status": "ok" if not missing else "missing-libraries",
    }


def verify_shared_libraries() -> dict[str, Any]:
    pyqt_root, error = locate_pyqt5_root()
    if error or pyqt_root is None:
        return {
            "status": "pyqt-unavailable",
            "pyqt_root": None,
            "targets": [],
            "missing": [],
            "error": error,
        }

    targets = []
    missing = []
    for label, relative_path in COMMON_QTWEBENGINE_TARGETS:
        report = run_ldd(pyqt_root / relative_path)
        report["label"] = label
        targets.append(report)
        missing.extend(report["missing"])

    return {
        "status": "ok" if not missing else "missing-libraries",
        "pyqt_root": str(pyqt_root),
        "targets": targets,
        "missing": sorted(set(missing)),
        "error": None,
    }


def write_openclaw_config(path: Path, token: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "gateway": {
                    "auth": {
                        "token": token,
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def verify_config_discovery() -> dict[str, Any]:
    try:
        from api_client import vm_connector as vm_connector_module
    except ModuleNotFoundError as exc:
        if exc.name != "PyQt5":
            return {
                "status": "import-failed",
                "cases": [],
                "error": str(exc),
            }

        pyqt5_module = types.ModuleType("PyQt5")
        qtcore_module = types.ModuleType("PyQt5.QtCore")

        class DummyQThread:
            def __init__(self, parent=None):
                self._parent = parent

        def dummy_pyqt_signal(*args, **kwargs):
            return None

        qtcore_module.QThread = DummyQThread
        qtcore_module.pyqtSignal = dummy_pyqt_signal
        pyqt5_module.QtCore = qtcore_module

        sys.modules.setdefault("PyQt5", pyqt5_module)
        sys.modules["PyQt5.QtCore"] = qtcore_module
        try:
            from api_client import vm_connector as vm_connector_module
        except Exception as inner_exc:
            return {
                "status": "import-failed",
                "cases": [],
                "error": str(inner_exc),
            }
    except Exception as exc:
        return {
            "status": "import-failed",
            "cases": [],
            "error": str(exc),
        }

    env_keys = (
        vm_connector_module.OPENCLAW_TOKEN_ENV,
        vm_connector_module.OPENCLAW_CONFIG_PATH_ENV,
        "XDG_CONFIG_HOME",
    )
    saved_env = {key: os.environ.get(key) for key in env_keys}
    cases = []

    with tempfile.TemporaryDirectory(prefix="echoes-linux-verify-") as temp_dir:
        temp_root = Path(temp_dir)
        fake_home = temp_root / "home"
        explicit_path = temp_root / "explicit" / "openclaw.json"
        xdg_root = temp_root / "xdg"
        home_path = fake_home / ".openclaw" / "openclaw.json"
        xdg_path = xdg_root / "openclaw" / "openclaw.json"

        write_openclaw_config(explicit_path, "explicit-token")
        write_openclaw_config(home_path, "home-token")
        write_openclaw_config(xdg_path, "xdg-token")

        def reset_env():
            for key in env_keys:
                original = saved_env[key]
                if original is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = original

        def run_case(name: str, expected: str | None, env_updates: dict[str, str | None]):
            reset_env()
            for key, value in env_updates.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

            with patch.object(vm_connector_module.Path, "home", return_value=fake_home):
                connector = vm_connector_module.VMConnector(access_token=None)
                actual = connector._load_access_token()

            cases.append(
                {
                    "name": name,
                    "expected": expected,
                    "actual": actual,
                    "passed": actual == expected,
                }
            )

        run_case(
            "env-token-priority",
            "env-token",
            {
                vm_connector_module.OPENCLAW_TOKEN_ENV: "env-token",
                vm_connector_module.OPENCLAW_CONFIG_PATH_ENV: str(explicit_path),
                "XDG_CONFIG_HOME": str(xdg_root),
            },
        )
        run_case(
            "explicit-config-path",
            "explicit-token",
            {
                vm_connector_module.OPENCLAW_TOKEN_ENV: None,
                vm_connector_module.OPENCLAW_CONFIG_PATH_ENV: str(explicit_path),
                "XDG_CONFIG_HOME": str(xdg_root),
            },
        )
        run_case(
            "home-config-default",
            "home-token",
            {
                vm_connector_module.OPENCLAW_TOKEN_ENV: None,
                vm_connector_module.OPENCLAW_CONFIG_PATH_ENV: None,
                "XDG_CONFIG_HOME": str(xdg_root),
            },
        )

        home_path.unlink()
        run_case(
            "xdg-config-fallback",
            "xdg-token",
            {
                vm_connector_module.OPENCLAW_TOKEN_ENV: None,
                vm_connector_module.OPENCLAW_CONFIG_PATH_ENV: None,
                "XDG_CONFIG_HOME": str(xdg_root),
            },
        )
        reset_env()

    all_passed = all(case["passed"] for case in cases)
    return {
        "status": "ok" if all_passed else "failed",
        "cases": cases,
        "error": None,
    }


def probe_webgl(timeout_ms: int = 10000) -> dict[str, Any]:
    display_env = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    try:
        from PyQt5.QtCore import QEventLoop, QTimer, Qt, QUrl
        from PyQt5.QtGui import QColor
        from PyQt5.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView
        from PyQt5.QtWidgets import QApplication
    except Exception as exc:
        return {
            "status": "qt-unavailable",
            "display": display_env,
            "error": str(exc),
        }

    try:
        app = QApplication.instance() or QApplication(sys.argv[:1])
    except Exception as exc:  # pragma: no cover - runtime-dependent
        return {
            "status": "qapp-failed",
            "display": display_env,
            "error": str(exc),
        }

    view = QWebEngineView()
    view.setAttribute(Qt.WA_TranslucentBackground, True)
    view.page().setBackgroundColor(QColor(0, 0, 0, 0))
    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
    settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)

    html = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <style>
    html, body {
      margin: 0;
      background: transparent;
    }
    canvas {
      width: 160px;
      height: 90px;
    }
  </style>
</head>
<body>
  <canvas id="gl-probe" width="160" height="90"></canvas>
</body>
</html>
"""
    js_probe = """
(function () {
  var canvas = document.getElementById('gl-probe');
  var gl = canvas.getContext('webgl', { alpha: true }) || canvas.getContext('experimental-webgl', { alpha: true });
  var result = {
    webglSupported: Boolean(gl),
    renderer: '',
    vendor: '',
    alpha: false,
    bodyBackground: window.getComputedStyle(document.body).backgroundColor,
    userAgent: navigator.userAgent
  };
  if (!gl) {
    return result;
  }
  result.alpha = Boolean(gl.getContextAttributes() && gl.getContextAttributes().alpha);
  var debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
  if (debugInfo) {
    result.vendor = gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) || '';
    result.renderer = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || '';
  } else {
    result.vendor = gl.getParameter(gl.VENDOR) || '';
    result.renderer = gl.getParameter(gl.RENDERER) || '';
  }
  return result;
})();
"""

    loop = QEventLoop()
    result: dict[str, Any] = {
        "status": "timeout",
        "display": display_env,
        "configuredWebGL": settings.testAttribute(QWebEngineSettings.WebGLEnabled),
        "configuredAccelerated2d": settings.testAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled),
        "waTranslucentBackground": view.testAttribute(Qt.WA_TranslucentBackground),
    }
    finished = {"done": False}

    def finish(payload: dict[str, Any]):
        if finished["done"]:
            return
        finished["done"] = True
        result.update(payload)
        loop.quit()

    def collect_js_probe():
        view.page().runJavaScript(
            js_probe,
            lambda payload: finish(
                {
                    "status": "ok" if isinstance(payload, dict) else "invalid-payload",
                    "payload": payload,
                }
            ),
        )

    def on_load(ok: bool):
        if not ok:
            finish({"status": "load-failed", "error": "QWebEngineView HTML 載入失敗。"})
            return
        QTimer.singleShot(300, collect_js_probe)

    view.loadFinished.connect(on_load)
    QTimer.singleShot(timeout_ms, lambda: finish({"status": "timeout", "error": "WebGL 探針逾時。"}))
    view.resize(320, 180)
    view.show()
    view.setHtml(html, QUrl("about:blank"))
    loop.exec_()
    view.close()

    payload = result.get("payload")
    if isinstance(payload, dict):
        renderer = str(payload.get("renderer") or "")
        renderer_lower = renderer.lower()
        result["hardwareAccelerationLikely"] = bool(
            payload.get("webglSupported")
            and renderer
            and "llvmpipe" not in renderer_lower
            and "swiftshader" not in renderer_lower
        )
        result["transparentBackgroundLikely"] = payload.get("bodyBackground") == "rgba(0, 0, 0, 0)"
    return result


def build_report(skip_webgl: bool) -> dict[str, Any]:
    report = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
        },
        "sharedLibraries": verify_shared_libraries(),
        "configDiscovery": verify_config_discovery(),
    }
    if skip_webgl:
        report["webglProbe"] = {
            "status": "skipped",
            "reason": "--skip-webgl",
        }
    else:
        report["webglProbe"] = probe_webgl()
    return report


def print_human_report(report: dict[str, Any]):
    print("== ECHOES Linux 驗證報告 ==")
    print(f"Platform: {report['platform']['system']} {report['platform']['release']}")

    shared = report["sharedLibraries"]
    print(f"[shared-libraries] status={shared['status']}")
    if shared.get("missing"):
        print(f"  missing: {', '.join(shared['missing'])}")
    elif shared.get("error"):
        print(f"  error: {shared['error']}")

    config = report["configDiscovery"]
    print(f"[config-discovery] status={config['status']}")
    for case in config.get("cases", []):
        status = "PASS" if case["passed"] else "FAIL"
        print(f"  - {status} {case['name']}: expected={case['expected']} actual={case['actual']}")

    webgl = report["webglProbe"]
    print(f"[webgl-probe] status={webgl['status']}")
    if isinstance(webgl.get("payload"), dict):
        payload = webgl["payload"]
        print(
            "  renderer={renderer} vendor={vendor} webgl={webgl_supported} alpha={alpha} transparent={transparent}".format(
                renderer=payload.get("renderer"),
                vendor=payload.get("vendor"),
                webgl_supported=payload.get("webglSupported"),
                alpha=payload.get("alpha"),
                transparent=webgl.get("transparentBackgroundLikely"),
            )
        )
    elif webgl.get("error"):
        print(f"  error: {webgl['error']}")


def exit_code_from_report(report: dict[str, Any]) -> int:
    failures = []
    shared = report["sharedLibraries"]
    if shared["status"] not in {"ok"}:
        failures.append("shared-libraries")

    config = report["configDiscovery"]
    if config["status"] != "ok":
        failures.append("config-discovery")

    webgl = report["webglProbe"]
    if webgl["status"] not in {"ok", "skipped"}:
        failures.append("webgl-probe")

    return 0 if not failures else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="驗證 ECHOES 在 Linux 上的執行環境。")
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出完整報告。")
    parser.add_argument("--skip-webgl", action="store_true", help="略過 QWebEngine / WebGL 探針。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(skip_webgl=args.skip_webgl)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human_report(report)

    return exit_code_from_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
