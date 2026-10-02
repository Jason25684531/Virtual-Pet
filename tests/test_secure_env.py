import os

import pytest

import secure_env

pytestmark = pytest.mark.skipif(os.name != "nt", reason="DPAPI 只在 Windows")
TEXT = "ELEVENLABS_API_KEY=sk_test_1234567890\nOLLAMA_MODEL=gemma3:12b\n"


def test_protect_roundtrip_is_not_plaintext():
    blob = secure_env.protect(TEXT.encode())
    assert b"sk_test_1234567890" not in blob
    assert secure_env.unprotect(blob).decode() == TEXT


def test_config_env_is_encrypted_and_plaintext_removed(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / ".env").write_text(TEXT, encoding="utf-8")

    assert secure_env.read_text(cfg / ".env") == TEXT
    assert not (cfg / ".env").exists()
    assert b"sk_test_1234567890" not in (cfg / ".env.secure").read_bytes()
    # 之後啟動只剩加密檔，仍讀得到
    assert secure_env.resolve(tmp_path) == cfg / ".env"
    assert secure_env.read_text(cfg / ".env") == TEXT


def test_new_plaintext_replaces_the_encrypted_file(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / ".env").write_text(TEXT, encoding="utf-8")
    secure_env.read_text(cfg / ".env")
    (cfg / ".env").write_text("OLLAMA_MODEL=new\n", encoding="utf-8")
    assert secure_env.read_text(cfg / ".env") == "OLLAMA_MODEL=new\n"
    assert secure_env.read_text(cfg / ".env") == "OLLAMA_MODEL=new\n"  # 第二次讀的是新的加密檔


def test_undecryptable_file_returns_none_instead_of_crashing(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / ".env.secure").write_bytes(b"copied from another machine")
    assert secure_env.read_text(cfg / ".env") is None


def test_dev_root_env_is_left_as_plaintext(tmp_path):
    (tmp_path / ".env").write_text(TEXT, encoding="utf-8")
    assert secure_env.resolve(tmp_path) == tmp_path / ".env"
    assert secure_env.read_text(tmp_path / ".env") == TEXT
    assert (tmp_path / ".env").exists() and not (tmp_path / ".env.secure").exists()
