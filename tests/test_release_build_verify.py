"""Release build 的驗證與授權彙整(release-handover-and-compliance)。

只用 tmp_path 假目錄;絕不執行 build 腳本本體(main 會 clean 並刪除 VirtualPet_Release/)。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_release.py"


@pytest.fixture()
def bb(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("build_release_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    release = tmp_path / "release"
    (release / "data").mkdir(parents=True)
    monkeypatch.setattr(module, "RELEASE", release)
    monkeypatch.setattr(module, "REQUIRED", {})
    monkeypatch.setattr(module, "VC_RUNTIME", ())
    monkeypatch.setattr(module, "_secret_values", lambda: [])
    return module


def _verify_error(bb) -> str:
    with pytest.raises(SystemExit) as raised:
        bb.verify()
    return str(raised.value)


def test_clean_release_with_empty_logs_dir_passes(bb):
    (bb.RELEASE / "logs").mkdir()  # build 本來就會建立空的 logs/
    bb.verify()


@pytest.mark.parametrize("relative", ["logs/echoes.log", "debug/events/e.json", "data/runtime/browser_profile/Cookies"])
def test_launched_release_is_rejected(bb, relative):
    target = bb.RELEASE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    assert Path(relative).parts[-1] in _verify_error(bb) or Path(relative).parts[0] in _verify_error(bb)


def test_secret_straddling_chunk_boundary_is_found(tmp_path):
    spec = importlib.util.spec_from_file_location("bb2", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    secret = b"SECRETVALUE123"
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"x" * 10 + secret + b"y" * 10)
    assert module._contains_secret(blob, [secret], chunk=16)  # 機密被切在兩塊之間
    assert not module._contains_secret(blob, [b"OTHERVALUE999"], chunk=16)


def test_secret_in_file_larger_than_old_64mb_limit_is_found(bb, monkeypatch):
    monkeypatch.setattr(bb, "_secret_values", lambda: [b"SECRETVALUE123"])
    big = bb.RELEASE / "VirtualPet.exe"
    with big.open("wb") as handle:
        handle.write(b"\0" * (65 * 1024 * 1024))
        handle.write(b"SECRETVALUE123")
    assert "VirtualPet.exe" in _verify_error(bb)


def test_verify_only_never_cleans_or_builds(bb, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("verify-only 不得 clean / build")

    for name in ("clean", "stage_sources", "compile_core", "assemble", "nuitka"):
        monkeypatch.setattr(bb, name, forbidden)
    called = []
    monkeypatch.setattr(bb, "verify", lambda: called.append(True))
    monkeypatch.setattr(sys, "argv", ["build_release.py", "--verify-only"])
    bb.main()
    assert called == [True]


def test_notices_are_required_and_flag_noncommercial_first(bb):
    spec = importlib.util.spec_from_file_location("bb3", SCRIPT)
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    assert real.REQUIRED["THIRD_PARTY_NOTICES"] == "THIRD_PARTY_NOTICES.txt"

    bb.REQUIRED = {"THIRD_PARTY_NOTICES": "THIRD_PARTY_NOTICES.txt"}
    assert "THIRD_PARTY_NOTICES" in _verify_error(bb)

    text = real.render_notices([("zlib-ish", "1.0", "MIT", None), *real.NON_PYTHON_NOTICES])
    assert text.index("jina-reranker") < text.index("== 其餘元件 ==") < text.index("zlib-ish")
    assert "CC-BY-NC-4.0" in text.split("== 其餘元件 ==")[0]


@pytest.mark.parametrize("license_text,copyleft", [
    ("GNU General Public License v3 (GPLv3)", True), ("GPLv3", True), ("AGPL-3.0", True),
    ("GNU Lesser General Public License v3 (LGPLv3)", False), ("LGPL-2.1", False), ("MIT", False), ("Apache-2.0", False),
])
def test_copyleft_detection(bb, license_text, copyleft):
    assert bb._is_copyleft(license_text) is copyleft
