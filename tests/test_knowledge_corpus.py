import pytest

from pet_harness.knowledge.corpus_parser import (
    CorpusError,
    chunk_to_memory_item,
    collect_keywords,
    parse_corpus,
    validate_corpus,
)
from pet_harness.knowledge.gate import needs_retrieval

_GOOD = """## A1. core_system｜核心系統

### er_core_0001｜生命力的作用

```yaml
id: er_core_0001
dataset_version: 1.0.0
language: zh-TW
game: test_game
domain: core_system
topic: 屬性系統 / Stats
title: 生命力的作用
entity_type: stat
volatility: evergreen
as_of_patch: null
spoiler_level: none
difficulty: beginner
keywords: [生命力, Vigor]
aliases: [血量]
related_ids: [er_core_0002]
```

生命力決定角色的最大 HP。

### er_core_0002｜集中力與 FP

```yaml
id: er_core_0002
dataset_version: 1.0.0
language: zh-TW
game: test_game
domain: core_system
topic: 屬性系統 / Stats
title: 集中力與 FP
entity_type: stat
volatility: evergreen
as_of_patch: null
spoiler_level: none
difficulty: beginner
keywords: [集中力, Mind]
aliases: [藍量]
related_ids: []
```

集中力決定 FP 上限。
"""


def test_parse_splits_on_chunk_headers_and_domain_headings_are_not_chunks():
    chunks = parse_corpus(_GOOD)
    assert [c.id for c in chunks] == ["er_core_0001", "er_core_0002"]
    assert chunks[0].domain == "core_system"
    assert chunks[0].content == "生命力決定角色的最大 HP。"


def test_valid_corpus_passes_all_checks():
    assert validate_corpus(parse_corpus(_GOOD)) == []


def test_missing_required_field_is_rejected():
    bad = _GOOD.replace("entity_type: stat\n", "", 1)
    errors = validate_corpus(parse_corpus(bad))
    assert any("缺少必填欄位" in e and "entity_type" in e for e in errors)


def test_duplicate_id_is_rejected():
    bad = _GOOD.replace("er_core_0002", "er_core_0001")
    errors = validate_corpus(parse_corpus(bad))
    assert any("id 重複" in e for e in errors)


def test_title_mismatch_is_rejected():
    bad = _GOOD.replace("title: 生命力的作用", "title: 別的標題", 1)
    errors = validate_corpus(parse_corpus(bad))
    assert any("標題與 metadata.title 不一致" in e for e in errors)


def test_dangling_related_id_is_rejected():
    bad = _GOOD.replace("related_ids: [er_core_0002]", "related_ids: [er_core_9999]", 1)
    errors = validate_corpus(parse_corpus(bad))
    assert any("related_ids 指向不存在的" in e for e in errors)


def test_invalid_enum_value_is_rejected():
    bad = _GOOD.replace("difficulty: beginner", "difficulty: godlike", 1)
    errors = validate_corpus(parse_corpus(bad))
    assert any("不是合法值" in e for e in errors)


def test_missing_yaml_fence_raises_corpus_error():
    bad = "### er_x_0001｜標題\n\n沒有 yaml fence 的內文。\n"
    with pytest.raises(CorpusError):
        parse_corpus(bad)


def test_chunk_to_memory_item_is_deterministic_and_carries_embedding_text():
    chunk = parse_corpus(_GOOD)[0]
    item_a = chunk_to_memory_item(chunk, created_at="2026-01-01T00:00:00+00:00")
    item_b = chunk_to_memory_item(chunk, created_at="2026-01-01T00:00:00+00:00")
    assert item_a.memory_id == item_b.memory_id  # 同一 chunk_id 重灌得到相同 point id
    assert item_a.memory_key == "er_core_0001"
    assert item_a.status == "active"
    assert "生命力的作用" in item_a.text
    assert "生命力決定角色的最大 HP" in item_a.text


def test_collect_keywords_merges_keywords_and_aliases_and_drops_single_chars():
    chunks = parse_corpus(_GOOD)
    keywords = collect_keywords(chunks)
    assert {"生命力", "Vigor", "血量", "集中力", "Mind", "藍量"} <= keywords


def test_gate_matches_on_keyword_or_alias():
    keywords = collect_keywords(parse_corpus(_GOOD))
    assert needs_retrieval("血量要點多少", keywords)
    assert needs_retrieval("Vigor怎麼加點", keywords)
    assert not needs_retrieval("今天天氣真好", keywords)


def test_gate_defaults_closed_without_keywords():
    assert needs_retrieval("血量要點多少", frozenset()) is False


def test_gate_matches_case_insensitively():
    # 語料中的別名是 "Vigor"（大寫 V）；使用者常見輸入是全小寫。
    keywords = collect_keywords(parse_corpus(_GOOD))
    assert needs_retrieval("vigor怎麼加點", keywords)


def test_known_game_length_range_is_enforced():
    bad = _GOOD.replace("game: test_game", "game: elden_ring")
    errors = validate_corpus(parse_corpus(bad))
    assert any("content 長度" in e and "(270, 565)" in e for e in errors)
