"""產生 Windows 發布版 VirtualPet_Release/（Nuitka standalone + 核心模組 Cython .pyd）。

由 scripts/build_release.ps1 以專案 venv 呼叫；不要用系統 Python 執行。
程式邏輯編譯鎖住；.agentic / assets / ComfyUI_Json / config 以原結構放在外部，修改後重啟即生效。
流程：清除舊 build → 複製原始碼到 build/stage → Cython 編譯核心模組 → Nuitka
→ 複製外部內容 / 模型 / runtime 工具 → 檢查完整性、無原始碼、無機密。
"""

from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
STAGE = BUILD / "stage"
RELEASE = ROOT / "VirtualPet_Release"
VENDOR = ROOT / "scripts" / "vendor"  # build_release.ps1 下載的第三方安裝檔（Lively）
PY = Path(sys.executable)
BASE_PY = Path(sys.base_prefix)
SITE = Path(sys.prefix) / "Lib" / "site-packages"
LOCAL = Path(os.environ["LOCALAPPDATA"])
FFMPEG_DIR = Path(os.environ.get("ChocolateyInstall", "C:/ProgramData/chocolatey")) / "lib/ffmpeg/tools/ffmpeg"
GCC_DIR = LOCAL / "Nuitka/Nuitka/Cache/downloads/gcc/x86_64/15.2.0posix-13.0.0-msvcrt-r6/mingw64/bin"

FIRST_PARTY_PACKAGES = ("pet_harness", "ui", "sensors", "api_client")
FIRST_PARTY_TOP_MODULES = (
    "main", "config", "action_dispatcher", "action_services", "audio_playback", "audio_worker",
    "character_library", "interaction_trace", "tts_playback", "release_bootstrap", "secure_env",
)
# 核心邏輯：設定 / prompt / skills 路由 / 記憶 / engine / 行為 / 成長 / workflow 組裝。
# __init__.py 一律留給 Nuitka（package 本體做成 .pyd 會讓 Nuitka 找不到子模組）。
CYTHON_GLOBS = (
    "config.py",
    "secure_env.py",  # 含 DPAPI entropy，編成 .pyd
    "pet_harness/agent/*.py",
    "pet_harness/skills/*.py",
    "pet_harness/memory/*.py",
    "pet_harness/engine/*.py",
    "pet_harness/knowledge/*.py",
    "pet_harness/behavior/behavior_manager.py",
    "pet_harness/xp/*.py",
    "pet_harness/character/router.py",
    "pet_harness/asset/workflow_patcher.py",
    "pet_harness/asset/asset_orchestrator.py",
    "pet_harness/asset/growth_trigger.py",
    "pet_harness/tools/safety_guard.py",
    "pet_harness/tools/network_policy.py",
)
# 外部可修改內容：原結構複製，不編入 EXE。
EXTERNAL_DIRS = (".agentic", "assets", "ComfyUI_Json", "data/knowledge")
FASTEMBED_MODELS = (
    "models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
    "models--Qdrant--bm25",
    "models--jinaai--jina-reranker-v2-base-multilingual",
)
VC_RUNTIME = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "vcomp140.dll", "concrt140.dll")
# Release 必須具備的元件：顯示名稱 → 相對 Release 的 glob（主要檔案，大小需 > 0）。
REQUIRED = {
    "VirtualPet.exe": "VirtualPet.exe",
    ".agentic soul": ".agentic/soul.md",
    ".agentic skills": ".agentic/skills/*.md",
    "角色 persona（personal.json）": "data/characters/*/personal.json",
    "assets characters": "assets/characters/*/manifest.json",
    "faster-whisper large-v3-turbo": "models/whisper/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/*/model.bin",
    "MiniLM embedding": "models/fastembed/models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q/snapshots/*/model_optimized.onnx",
    "BM25": "models/fastembed/models--Qdrant--bm25/snapshots/*/*",
    "jina reranker": "models/fastembed/models--jinaai--jina-reranker-v2-base-multilingual/snapshots/*/onnx/model.onnx",
    "Silero VAD": "models/vad/silero_vad.onnx",
    "Playwright Chromium": "ms-playwright/chromium-*/chrome-win64/chrome.exe",
    "Playwright driver": "playwright/driver/node.exe",
    "ffplay": "ffmpeg/ffplay.exe",
    "CUDA cuBLAS": "nvidia/cublas/bin/*.dll",
    "CUDA cuDNN": "nvidia/cudnn/bin/*.dll",
    "VC++ runtime": "msvcp140.dll",
    "QtWebEngine resources": "PyQt5/Qt5/resources/icudtl.dat",
    "QtWebEngine resources (root)": "resources/icudtl.dat",
    "QtWebEngine locales": "PyQt5/Qt5/translations/qtwebengine_locales/zh-TW.pak",
    "Lively installer": "lively/lively_setup_*.exe",
    "ECHOES wallpaper": "lively/echoes_background/LivelyInfo.json",
    "ffmpeg LICENSE": "ffmpeg/LICENSE.txt",
    "Lively LICENSE": "lively/LICENSE.txt",
    "Playwright/Chromium LICENSE": "ms-playwright/LICENSE",
}


def log(message: str) -> None:
    print(f"[build] {message}", flush=True)


def run(cmd: list, **kwargs) -> None:
    log(" ".join(str(c) for c in cmd[:6]) + (" ..." if len(cmd) > 6 else ""))
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def clean() -> None:
    # Nuitka 的 C 編譯快取（build/main.build）保留，改原始碼後重 build 只需重編有變動的部分。
    for path in (STAGE, BUILD / "main.dist", RELEASE):
        if path.exists():
            shutil.rmtree(path)
    STAGE.mkdir(parents=True)


def stage_sources() -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "assets", "ref_pic")
    for name in FIRST_PARTY_PACKAGES:
        shutil.copytree(ROOT / name, STAGE / name, ignore=ignore)
    for name in FIRST_PARTY_TOP_MODULES:
        shutil.copy2(ROOT / f"{name}.py", STAGE / f"{name}.py")


def strip_docstrings(path: Path) -> None:
    """.pyd 被任何 Python import 後 __doc__ 可直接讀出；編譯前先移除。註解本來就不會進 binary。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body                 and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)                 and isinstance(node.body[0].value.value, str):
            node.body = node.body[1:] or [ast.Pass()]
    path.write_text(ast.unparse(tree), encoding="utf-8")


def cythonize(path: Path) -> str | None:
    """成功回傳 None；失敗回傳原因，該模組保留 .py 交給 Nuitka 編譯（功能優先，不硬改原始碼）。"""
    c_file = path.with_suffix(".c")
    pyd = path.with_name(f"{path.stem}.cp{sys.version_info.major}{sys.version_info.minor}-win_amd64.pyd")
    steps = (
        [PY, "-m", "cython", "-3", str(path), "-o", str(c_file)],
        [GCC_DIR / "gcc.exe", "-shared", "-O2", "-s", "-static-libgcc", "-DMS_WIN64", "-DNDEBUG",
         f"-I{BASE_PY / 'include'}", str(c_file), f"-L{BASE_PY}", f"-lpython{sys.version_info.major}{sys.version_info.minor}",
         "-o", str(pyd)],
    )
    env = {**os.environ, "PATH": f"{GCC_DIR}{os.pathsep}{os.environ['PATH']}"}
    for cmd in steps:
        result = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env)
        if result.returncode:
            c_file.unlink(missing_ok=True)
            pyd.unlink(missing_ok=True)
            return (result.stderr or result.stdout).strip().splitlines()[-1][:200]
    c_file.unlink()
    return None


def module_name(path: Path) -> str:
    return ".".join(path.relative_to(STAGE).with_suffix("").parts)


def imports_of(paths: list[Path]) -> set[str]:
    """.pyd 內的 import Nuitka 看不到，這裡從原始碼抽出來改用 --include-module 明確帶入。"""
    names: set[str] = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module)
                for alias in node.names:  # `from pkg import submodule` 也要帶到子模組
                    if (STAGE / Path(*node.module.split("."), f"{alias.name}.py")).exists():
                        names.add(f"{node.module}.{alias.name}")
    first_party = set(FIRST_PARTY_PACKAGES) | set(FIRST_PARTY_TOP_MODULES)
    candidates = {n for n in names if n.split(".")[0] not in first_party and n != "__future__" and n not in sys.builtin_module_names}
    return {n for n in candidates if _installed(n)}


def _installed(name: str) -> bool:
    # try/except ImportError 的選用依賴若 venv 沒裝，不能交給 Nuitka 強制帶入。
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def compile_core() -> list[str]:
    targets = sorted({p for pattern in CYTHON_GLOBS for p in STAGE.glob(pattern) if p.name != "__init__.py"})
    hidden_imports = imports_of(targets)
    for path in targets:
        strip_docstrings(path)
    log(f"Cython 編譯 {len(targets)} 個核心模組")
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        failures = dict(zip(targets, pool.map(cythonize, targets)))
    compiled = [p for p, reason in failures.items() if reason is None]
    report = ["# Cython 保護報告", *(f"pyd   {module_name(p)}" for p in compiled)]
    for path, reason in failures.items():
        if reason:
            report.append(f"nuitka {module_name(path)}  # Cython 失敗：{reason}")
            log(f"Cython 失敗，改由 Nuitka 編譯：{module_name(path)}（{reason}）")
    (BUILD / "cython_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    for path in compiled:
        path.unlink()
    # .pyd 必須能在 venv 直接 import，否則 Nuitka 打包後只會更難除錯。
    run([PY, "-c", "import importlib,sys;[importlib.import_module(m) for m in sys.argv[1:]]", *map(module_name, compiled)], cwd=STAGE)
    log(f"Cython 完成：{len(compiled)}/{len(targets)} 個模組為 .pyd")
    return sorted(hidden_imports)


def nuitka(hidden_imports: list[str]) -> Path:
    cmd = [
        PY, "-m", "nuitka", "--mode=standalone", "--mingw64", "--assume-yes-for-downloads",
        f"--output-dir={BUILD}", "--output-filename=VirtualPet.exe",
        "--enable-plugin=pyqt5",
        "--windows-console-mode=disable",
        "--force-stdout-spec={PROGRAM_DIR}/logs/stdout.log",
        "--force-stderr-spec={PROGRAM_DIR}/logs/stderr.log",
        "--python-flag=no_docstrings",
        "--python-flag=isolated",
        f"--jobs={os.cpu_count()}",
        *[f"--include-package={name}" for name in FIRST_PARTY_PACKAGES],
        *[f"--include-module={name}" for name in FIRST_PARTY_TOP_MODULES if name != "main"],
        *[f"--include-module={name}" for name in hidden_imports],
        "--include-package-data=jieba,opencc,faster_whisper,fastembed,playwright",
        f"--include-data-dir={STAGE / 'ui' / 'web_container'}=ui/web_container",
        f"--include-data-dir={ROOT / 'ui' / 'assets'}=ui/assets",
        f"--include-data-files={STAGE / 'pet_harness' / 'storage' / 'schema.sql'}=pet_harness/storage/schema.sql",
        "--nofollow-import-to=pytest,_pytest,tests,IPython,matplotlib,tkinter,openpyxl",
        STAGE / "main.py",
    ]
    run(cmd, cwd=STAGE)
    return BUILD / "main.dist"


def _copy_dir(source: Path, target: Path, **kwargs) -> None:
    if not source.is_dir():
        raise SystemExit(f"[build] 失敗：找不到 {source}")
    shutil.copytree(source, target, dirs_exist_ok=True, **kwargs)


def copy_models() -> None:
    # 從開發機已校準過的快取複製（不重新下載），鎖定與檢索門檻相符的模型版本。
    _copy_dir(ROOT / "runtime_cache" / "whisper", RELEASE / "models" / "whisper", ignore=shutil.ignore_patterns(".locks"))
    fastembed = Path(os.environ.get("FASTEMBED_CACHE_PATH") or Path(os.environ["TEMP"]) / "fastembed_cache")
    for name in FASTEMBED_MODELS:
        _copy_dir(fastembed / name, RELEASE / "models" / "fastembed" / name)
    (RELEASE / "models" / "vad").mkdir(parents=True)
    shutil.copy2(ROOT / "runtime_cache" / "vad" / "silero_vad.onnx", RELEASE / "models" / "vad" / "silero_vad.onnx")


def copy_runtime_tools() -> None:
    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or LOCAL / "ms-playwright")
    for pattern in ("chromium-*", "ffmpeg-*", "winldd-*"):  # 程式用 headless=False，不帶 headless shell
        for source in browsers.glob(pattern):
            _copy_dir(source, RELEASE / "ms-playwright" / source.name)
    driver = SITE / "playwright" / "driver"
    _copy_dir(driver, RELEASE / "playwright" / "driver")  # node.exe 不是 data file，Nuitka 不會自動帶
    shutil.copy2(driver / "package" / "LICENSE", RELEASE / "ms-playwright" / "LICENSE")
    shutil.copy2(driver / "package" / "NOTICE", RELEASE / "ms-playwright" / "NOTICE")

    ffplay = FFMPEG_DIR / "bin" / "ffplay.exe" if (FFMPEG_DIR / "bin" / "ffplay.exe").exists() else shutil.which("ffplay")
    if ffplay:
        (RELEASE / "ffmpeg").mkdir()
        shutil.copy2(ffplay, RELEASE / "ffmpeg" / "ffplay.exe")
        if (FFMPEG_DIR / "LICENSE").exists():
            shutil.copy2(FFMPEG_DIR / "LICENSE", RELEASE / "ffmpeg" / "LICENSE.txt")

    # ctranslate2 走 PATH 找 cublas/cudnn；faster_whisper_stt 會把 nvidia/<pkg>/bin 前置進 PATH。
    for package in ("cublas", "cudnn", "cuda_nvrtc"):
        _copy_dir(SITE / "nvidia" / package / "bin", RELEASE / "nvidia" / package / "bin")

    # onnxruntime / ctranslate2 用 VS2022 編譯，需要 MSVCP140 >= 14.40（新版 std::mutex）；Nuitka 從
    # PyQt5 帶進來的是 14.26，載入後會在 mutex lock 時 c0000005 崩潰。VC runtime 向下相容，一律覆蓋成新版。
    system32 = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    for name in VC_RUNTIME:
        shutil.copy2(system32 / name, RELEASE / name)

    # QtWebEngine 依執行期的 Qt prefix 找 resources/locales，prefix 會在根目錄與 PyQt5/Qt5 之間變動
    # （5.15.2 不支援 QTWEBENGINE_RESOURCES_PATH）；只放一邊就會 abort（0x80000003）或頁面空白。兩邊各放一份（約 34MB）。
    qt_prefix = RELEASE / "PyQt5" / "Qt5"
    shutil.copytree(RELEASE / "resources", qt_prefix / "resources", dirs_exist_ok=True)
    shutil.copytree(RELEASE / "translations" / "qtwebengine_locales", qt_prefix / "translations" / "qtwebengine_locales", dirs_exist_ok=True)

    (RELEASE / "lively").mkdir()
    for source in VENDOR.glob("lively_*"):
        shutil.copy2(source, RELEASE / "lively" / ("LICENSE.txt" if source.name == "lively_LICENSE" else source.name))
    wallpaper = RELEASE / "lively" / "echoes_background"
    _copy_dir(ROOT / "tools" / "spikes" / "lively_background_poc", wallpaper, ignore=shutil.ignore_patterns("*.test.cjs", "README.md"))
    (wallpaper / "assets").mkdir(exist_ok=True)  # 原始碼端沒有 assets/;兩張圖都由這裡複製
    for image in ("default_room.jpg", "BG_Final.png"):  # LivelyInfo 縮圖與 LivelyProperties 預設背景
        shutil.copy2(ROOT / "assets" / "backgrounds" / image, wallpaper / "assets" / image)


def _strip_png_text(path: Path) -> None:
    """移除 PNG 的 tEXt/iTXt/zTXt chunk；像素資料不動。"""
    data = path.read_bytes()
    out, i = bytearray(data[:8]), 8
    while i < len(data):
        length = int.from_bytes(data[i:i + 4], "big")
        chunk = data[i:i + 12 + length]
        if chunk[4:8] not in (b"tEXt", b"iTXt", b"zTXt"):
            out += chunk
        i += 12 + length
    path.write_bytes(bytes(out))


def scrub_media_metadata() -> None:
    """ComfyUI 存檔時把整份 workflow（含注入的 Azure API Key）寫進 PNG text chunk 與 WebM 的
    COMMENT tag。只清 Release 的副本，開發機原檔不動；程式本身不讀這些 metadata。"""
    ffmpeg = FFMPEG_DIR / "bin" / "ffmpeg.exe" if (FFMPEG_DIR / "bin" / "ffmpeg.exe").exists() else shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("[build] 失敗：清除 WebM metadata 需要 ffmpeg")
    media = [p for p in RELEASE.joinpath("assets").rglob("*") if p.suffix.lower() in {".png", ".webm"}]
    for path in (p for p in media if p.suffix.lower() == ".png"):
        _strip_png_text(path)

    def remux(path: Path) -> None:
        # 只清全域 metadata（-map_metadata:g -1）：track 的 AlphaMode 元素必須保留，Chromium 靠它啟用 VP9 透明。
        tmp = path.with_name(path.stem + ".scrub.webm")
        subprocess.run([str(ffmpeg), "-v", "error", "-y", "-i", str(path), "-map", "0", "-map_metadata:g", "-1", "-c", "copy", str(tmp)], check=True)
        os.replace(tmp, path)

    webms = [p for p in media if p.suffix.lower() == ".webm"]
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        list(pool.map(remux, webms))
    log(f"已清除素材 metadata：PNG {len(media) - len(webms)} 個、WebM {len(webms)} 個")


def assemble(dist: Path) -> None:
    shutil.move(str(dist), RELEASE)
    for name in EXTERNAL_DIRS:
        _copy_dir(ROOT / name, RELEASE / name, ignore=shutil.ignore_patterns("Thumbs.db", "__pycache__"))
    scrub_media_metadata()
    # 角色 persona prompt / 角色專屬 skill 屬於可修改內容（同 .agentic）；state.db、qdrant 記憶是 runtime data，不帶。
    for source in (ROOT / "data" / "characters").iterdir():
        target = RELEASE / "data" / "characters" / source.name
        for name in ("personal.json", "profile.json"):
            if (source / name).is_file():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / name, target / name)
        if (source / "skills").is_dir():
            _copy_dir(source / "skills", target / "skills")
    copy_models()
    copy_runtime_tools()
    (RELEASE / "logs").mkdir(exist_ok=True)
    (RELEASE / "config").mkdir()
    shutil.copy2(ROOT / ".env.example", RELEASE / "config" / ".env.example")
    shutil.copy2(ROOT / "scripts" / "README_DEPLOY.txt", RELEASE / "README_DEPLOY.txt")
    shutil.copy2(ROOT / "scripts" / "release_run.bat", RELEASE / "run.bat")


def _secret_values() -> list[bytes]:
    values = []
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
        key, sep, value = line.partition("=")
        value = value.strip().strip('"')
        if sep and not key.lstrip().startswith("#") and any(t in key.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(value) >= 8 and "${" not in value:
            values.append(value.encode())
    return values


def _file_version(path: Path) -> tuple[int, int]:
    result = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Item '{path}').VersionInfo.ProductVersion"],
                            capture_output=True, text=True)
    parts = result.stdout.strip().split(".")
    return (int(parts[0]), int(parts[1])) if len(parts) >= 2 and parts[0].isdigit() else (0, 0)


def verify() -> None:
    errors = []
    missing = [name for name, pattern in REQUIRED.items() if not any(p.is_file() and p.stat().st_size for p in RELEASE.glob(pattern))]
    if missing:
        errors.append(f"缺少元件：{missing}")
    old_vc = [name for name in VC_RUNTIME if _file_version(RELEASE / name) < (14, 40)]
    if old_vc:
        errors.append(f"VC++ runtime 版本過舊（需 >= 14.40）：{old_vc}")
    # data/ 只允許內建內容（knowledge 索引、角色 persona）；其餘都是執行期資料，應由舊機搬移而不是被打包。
    runtime_state = [str(p.relative_to(RELEASE)) for p in (RELEASE / "data").rglob("*")
                     if p.name in {"state.db", "qdrant", "pet_state.db", "runtime", "saves"} and "knowledge" not in p.parts]
    if runtime_state:
        errors.append(f"Release 不得包含 runtime data：{runtime_state[:5]}")
    if (RELEASE / "runtime_cache").exists():
        errors.append("Release 不得包含 runtime_cache/（含 greetings 語音快取）")

    first_party = set(FIRST_PARTY_PACKAGES) | set(FIRST_PARTY_TOP_MODULES)
    secrets = _secret_values()
    leaked, external_models, secret_hits = [], [], []
    for path in RELEASE.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(RELEASE)
        if path.suffix in {".py", ".pyc", ".pyx", ".c"} and rel.parts[0].removesuffix(path.suffix) in first_party:
            leaked.append(str(rel))
        if path.suffix.lower() in {".safetensors", ".ckpt", ".gguf"}:
            external_models.append(str(rel))
        if path.name in {".env", ".env.secure"}:
            secret_hits.append(str(rel))
        # ponytail: 只掃 < 64MB（目前超過的只有模型權重與 CUDA DLL）；若素材影片超過 64MB，改成分塊串流掃描。
        if secrets and path.stat().st_size < 64 * 1024 * 1024:
            data = path.read_bytes()
            if any(value in data for value in secrets):
                secret_hits.append(str(rel))
    if leaked:
        errors.append(f"含專案原始碼：{leaked}")
    if external_models:
        errors.append(f"含 ComfyUI/Ollama 模型（應維持外部）：{external_models}")
    if secret_hits:
        errors.append(f"含機密：{secret_hits}")
    if errors:
        raise SystemExit("[build] 驗證失敗：\n  " + "\n  ".join(errors))
    size = sum(p.stat().st_size for p in RELEASE.rglob("*") if p.is_file()) / 1024**3
    log(f"驗證通過：元件齊全、無專案原始碼、無外部模型、無機密（掃描 {len(secrets)} 個 key）；Release {size:.1f} GB")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 輸出被導向檔案時預設是 ANSI 編碼
    if not (GCC_DIR / "gcc.exe").exists():
        raise SystemExit(f"[build] 找不到 MinGW64：{GCC_DIR}（build_release.ps1 會自動下載）")
    clean()
    stage_sources()
    hidden_imports = compile_core()
    assemble(nuitka(hidden_imports))
    verify()
    log(f"完成：{RELEASE}")

if __name__ == "__main__":
    main()
