"""產生 Windows 發布版 VirtualPet_Release/（Nuitka standalone + 核心模組 Cython .pyd）。

由 scripts/build_release.ps1 以專案 venv 呼叫；不要用系統 Python 執行。
流程：清除舊 build → 複製原始碼到 build/stage → 內嵌加密資源 → Cython 編譯核心模組
→ Nuitka → 複製 assets / data / ffplay / CUDA runtime → 檢查 Release 不含原始碼。
"""

from __future__ import annotations

import ast
import importlib.util
import os
import secrets
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
STAGE = BUILD / "stage"
RELEASE = ROOT / "VirtualPet_Release"
PY = Path(sys.executable)
BASE_PY = Path(sys.base_prefix)
GCC_DIR = Path(os.environ["LOCALAPPDATA"]) / "Nuitka/Nuitka/Cache/downloads/gcc/x86_64/15.2.0posix-13.0.0-msvcrt-r6/mingw64/bin"

FIRST_PARTY_PACKAGES = ("pet_harness", "ui", "sensors", "api_client")
FIRST_PARTY_TOP_MODULES = (
    "main", "config", "action_dispatcher", "action_services", "audio_playback",
    "audio_worker", "character_library", "interaction_trace", "tts_playback",
)
# 核心邏輯：prompt / skills 路由 / 記憶 / engine / 行為 / 成長 / workflow 組裝。
# __init__.py 一律留給 Nuitka（package 本體做成 .pyd 會讓 Nuitka 找不到子模組）。
CYTHON_GLOBS = (
    "config.py",
    "pet_harness/protected_resources.py",
    "pet_harness/_embedded_resources.py",
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
# 這些資料夾內容改為加密內嵌，Release 不出現明文檔。使用者自建 skill 屬 runtime data，不內嵌。
EMBED_DIRS = (".agentic", "ComfyUI_Json")
EMBED_SKIP = (".agentic/skills/user/",)


def log(message: str) -> None:
    print(f"[build] {message}", flush=True)


def run(cmd: list[str], **kwargs) -> None:
    log(" ".join(str(c) for c in cmd[:6]) + (" ..." if len(cmd) > 6 else ""))
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def clean() -> None:
    for path in (BUILD, RELEASE):
        if path.exists():
            shutil.rmtree(path)
    STAGE.mkdir(parents=True)


def stage_sources() -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "assets", "ref_pic")
    for name in FIRST_PARTY_PACKAGES:
        shutil.copytree(ROOT / name, STAGE / name, ignore=ignore)
    for name in FIRST_PARTY_TOP_MODULES:
        shutil.copy2(ROOT / f"{name}.py", STAGE / f"{name}.py")


def embed_resources() -> None:
    sys.path.insert(0, str(ROOT))
    from pet_harness.protected_resources import pack

    files: dict[str, bytes] = {}
    for folder in EMBED_DIRS:
        for path in sorted((ROOT / folder).rglob("*")):
            key = path.relative_to(ROOT).as_posix()
            if path.is_file() and not key.startswith(EMBED_SKIP):
                files[key] = path.read_bytes()
    key = secrets.token_bytes(32)
    (STAGE / "pet_harness" / "_embedded_resources.py").write_text(
        f"KEY = {key!r}\nBLOB = {pack(files, key)!r}\n", encoding="utf-8"
    )
    log(f"內嵌加密 {len(files)} 個資源檔：{', '.join(EMBED_DIRS)}")


def cython_targets() -> list[Path]:
    return sorted({p for pattern in CYTHON_GLOBS for p in STAGE.glob(pattern) if p.name != "__init__.py"})


def cythonize(path: Path) -> None:
    c_file = path.with_suffix(".c")
    pyd = path.with_name(f"{path.stem}.cp{sys.version_info.major}{sys.version_info.minor}-win_amd64.pyd")
    subprocess.run([PY, "-m", "cython", "-3", "-q", str(path), "-o", str(c_file)], check=True)
    subprocess.run(
        [GCC_DIR / "gcc.exe", "-shared", "-O2", "-static-libgcc", "-DMS_WIN64", "-DNDEBUG",
         f"-I{BASE_PY / 'include'}", str(c_file), f"-L{BASE_PY}", f"-lpython{sys.version_info.major}{sys.version_info.minor}",
         "-o", str(pyd)],
        check=True, env={**os.environ, "PATH": f"{GCC_DIR}{os.pathsep}{os.environ['PATH']}"},
    )
    c_file.unlink()


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
                # `from pkg import submodule` 也要帶到子模組
                for alias in node.names:
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
    targets = cython_targets()
    hidden_imports = imports_of(targets)
    log(f"Cython 編譯 {len(targets)} 個核心模組")
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        list(pool.map(cythonize, targets))
    for path in targets:
        path.unlink()
    modules = [module_name(p) for p in targets]
    # .pyd 必須能在 venv 直接 import，否則 Nuitka 打包後只會更難除錯。
    run([PY, "-c", "import importlib,sys;[importlib.import_module(m) for m in sys.argv[1:]]", *modules], cwd=STAGE)
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
        f"--jobs={os.cpu_count()}",
        *[f"--include-package={name}" for name in FIRST_PARTY_PACKAGES],
        *[f"--include-module={name}" for name in FIRST_PARTY_TOP_MODULES if name != "main"],
        *[f"--include-module={name}" for name in hidden_imports],
        "--include-module=nvidia.cublas", "--include-module=nvidia.cudnn", "--include-module=nvidia.cuda_nvrtc",
        "--include-package-data=jieba,opencc,faster_whisper,fastembed,playwright",
        f"--include-data-dir={STAGE / 'ui' / 'web_container'}=ui/web_container",
        f"--include-data-dir={ROOT / 'ui' / 'assets'}=ui/assets",
        f"--include-data-files={STAGE / 'pet_harness' / 'storage' / 'schema.sql'}=pet_harness/storage/schema.sql",
        "--nofollow-import-to=pytest,_pytest,tests,IPython,matplotlib,tkinter,openpyxl",
        STAGE / "main.py",
    ]
    run(cmd, cwd=STAGE)
    return BUILD / "main.dist"


def assemble(dist: Path) -> None:
    shutil.move(str(dist), RELEASE)
    # 內建角色素材（不含開發備份）；ComfyUI 生成的新素材也會寫回這裡。
    shutil.copytree(ROOT / "assets", RELEASE / "assets", ignore=shutil.ignore_patterns("backup", "Thumbs.db"))
    subprocess.run(["attrib", "+h", str(RELEASE / "assets")], check=False)
    # 共用知識庫索引是唯讀內容；個人記憶 / 存檔 / 瀏覽器 profile 屬 runtime data，由舊機搬移。
    shutil.copytree(ROOT / "data" / "knowledge", RELEASE / "data" / "knowledge")
    (RELEASE / "logs").mkdir(exist_ok=True)

    ffplay = shutil.which("ffplay")
    if ffplay:
        ffplay = Path(ffplay)
        # chocolatey 的 ffplay 是 shim；真正的執行檔在 lib/ffmpeg/tools/ffmpeg/bin。
        real = Path(os.environ.get("ChocolateyInstall", "C:/ProgramData/chocolatey")) / "lib/ffmpeg/tools/ffmpeg"
        source = real / "bin" / "ffplay.exe" if (real / "bin" / "ffplay.exe").exists() else ffplay
        (RELEASE / "ffmpeg").mkdir()
        shutil.copy2(source, RELEASE / "ffmpeg" / "ffplay.exe")
        if (real / "LICENSE").exists():
            shutil.copy2(real / "LICENSE", RELEASE / "ffmpeg" / "LICENSE.txt")
    else:
        log("警告：找不到 ffplay，Release 不含 ffmpeg/，新機需自行安裝 ffplay 並加入 PATH")

    # ctranslate2 走 PATH 找 cublas/cudnn；faster_whisper_stt 會把 nvidia/<pkg>/bin 前置進 PATH。
    site = Path(sys.prefix) / "Lib" / "site-packages"
    for package in ("cublas", "cudnn", "cuda_nvrtc"):
        source = site / "nvidia" / package / "bin"
        if source.is_dir():
            shutil.copytree(source, RELEASE / "nvidia" / package / "bin", dirs_exist_ok=True)

    shutil.copy2(ROOT / ".env.example", RELEASE / ".env.example")
    shutil.copy2(ROOT / "scripts" / "README_DEPLOY.txt", RELEASE / "README_DEPLOY.txt")


def verify() -> None:
    first_party = set(FIRST_PARTY_PACKAGES) | set(FIRST_PARTY_TOP_MODULES) | {".agentic", "ComfyUI_Json"}
    leaked, third_party = [], []
    for path in RELEASE.rglob("*"):
        rel = path.relative_to(RELEASE)
        if path.is_file() and path.suffix in {".py", ".pyc", ".pyx", ".c"}:
            (leaked if rel.parts[0].removesuffix(".py") in first_party else third_party).append(rel)
        elif rel.parts[0] in {".agentic", "ComfyUI_Json"} or path.name == ".env":
            leaked.append(rel)
    if third_party:
        log(f"第三方套件內附 {len(third_party)} 個 .py（非專案原始碼）：{[str(p) for p in third_party[:5]]}")
    if leaked:
        raise SystemExit(f"[build] 失敗：Release 含專案原始碼或機密：{[str(p) for p in leaked]}")
    pyd = sorted(str(p.relative_to(RELEASE)) for p in RELEASE.rglob("*.pyd") if p.relative_to(RELEASE).parts[0] in first_party)
    log(f"驗證通過：無專案 .py / .pyc / .env；Cython 核心模組 {len(pyd)} 個")


def main() -> None:
    if not (GCC_DIR / "gcc.exe").exists():
        raise SystemExit(f"[build] 找不到 MinGW64：{GCC_DIR}（build_release.ps1 會自動下載）")
    clean()
    stage_sources()
    embed_resources()
    hidden_imports = compile_core()
    assemble(nuitka(hidden_imports))
    verify()
    log(f"完成：{RELEASE}")


if __name__ == "__main__":
    main()
