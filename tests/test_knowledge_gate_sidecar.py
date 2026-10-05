"""keywords.json sidecar 缺檔是預期狀態(降為 INFO),毀損才是錯誤;兩者閘門都維持關閉。"""
import logging

from pet_harness.knowledge.gate import load_keywords, needs_retrieval


def test_missing_sidecar_logs_info_not_error_and_keeps_gate_closed(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="pet_harness.knowledge.gate"):
        keywords = load_keywords(tmp_path / "keywords.json")
    assert keywords == frozenset()
    assert not needs_retrieval("任何遊戲問題", keywords)
    assert [r.levelno for r in caplog.records] == [logging.INFO]


def test_corrupt_sidecar_is_still_an_error(tmp_path, caplog):
    path = tmp_path / "keywords.json"
    path.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="pet_harness.knowledge.gate"):
        assert load_keywords(path) == frozenset()
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_valid_sidecar_opens_gate_for_known_keyword(tmp_path):
    path = tmp_path / "keywords.json"
    path.write_text('["薩爾達"]', encoding="utf-8")
    assert needs_retrieval("薩爾達怎麼玩", load_keywords(path))
