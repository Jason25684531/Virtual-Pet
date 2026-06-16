import json
import subprocess
import sys
from pathlib import Path

from pet_harness.agent.api_provider import APIProvider
from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.agent.result_parser import ResultParser
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.agent_result import AgentResult
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderStatus, ProviderType
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter
from pet_harness.storage.sqlite_store import SQLiteStore


class FakeResponse:
    def __init__(self, payload, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload


def test_provider_config_round_trip_and_store_persistence(tmp_path):
    config = ProviderConfig(
        provider_type=ProviderType.API,
        base_url="https://example.invalid/v1/chat/completions",
        model_name="demo-model",
        api_key_env_var="ECHOES_API_KEY",
        timeout_seconds=12.5,
        fallback_provider=ProviderType.LOW_SPEC,
        routing_fallback_enabled=True,
        routing_confidence_threshold=0.77,
        metadata={"endpoint_class": "chat_completions"},
    )

    payload = config.to_dict()

    assert payload["provider_type"] == "api"
    assert payload["fallback_provider"] == "low_spec"
    assert payload["api_key_env_var"] == "ECHOES_API_KEY"

    store = SQLiteStore(tmp_path / "pet_state.db")
    store.initialize()
    store.set_provider_config(config)

    loaded = store.get_provider_config()
    assert loaded.provider_type is ProviderType.API
    assert loaded.model_name == "demo-model"
    assert loaded.fallback_provider is ProviderType.LOW_SPEC
    assert loaded.routing_confidence_threshold == 0.77


def test_provider_status_survives_store_reopen(tmp_path):
    store = SQLiteStore(tmp_path / "pet_state.db")
    store.initialize()
    store.set_provider_status(
        ProviderStatus(
            provider_type=ProviderType.LOW_SPEC,
            healthy=False,
            message="missing api key",
            metadata={"error_category": "missing_api_key"},
        )
    )

    reopened = SQLiteStore(tmp_path / "pet_state.db")
    reopened.initialize()

    assert reopened.get_provider_status()["provider_type"] == "low_spec"
    assert reopened.get_provider_status()["metadata"]["error_category"] == "missing_api_key"


def test_result_parser_handles_clean_fenced_and_malformed_output():
    parser = ResultParser()

    clean = parser.parse(
        json.dumps(
            {
                "reply": "hi",
                "matched_skill": "music_bgm",
                "behavior_hint": "music_idle",
                "confidence": 0.9,
                "tool_request": {"tool_name": "music_search"},
                "notes": "short summary",
            }
        ),
        provider_type=ProviderType.API,
    )
    assert clean.reply == "hi"
    assert clean.matched_skill == "music_bgm"
    assert clean.parser_status == "parsed_json"

    fenced = parser.parse(
        """```json
{"reply":"hello","matched_skill":"game_news","confidence":0.8}
```""",
        provider_type=ProviderType.API,
    )
    assert fenced.reply == "hello"
    assert fenced.matched_skill == "game_news"
    assert fenced.parser_status == "parsed_fenced_json"

    malformed = parser.parse("not json at all", provider_type=ProviderType.API)
    assert malformed.fallback_used is True
    assert malformed.raw_text == "not json at all"
    assert malformed.parser_status == "fallback_invalid_json"


def test_agent_result_serialization_keeps_raw_and_metadata():
    result = AgentResult(
        reply="hello",
        matched_skill="music_bgm",
        behavior_hint="music_idle",
        confidence=0.8,
        tool_request={"tool_name": "music_search"},
        raw_text='{"reply":"hello"}',
        raw_json={"reply": "hello"},
        parser_status="parsed_json",
        provider_type="api",
        fallback_used=False,
        metadata={"source": "pytest"},
    )

    payload = result.to_dict()
    assert payload["raw_text"] == '{"reply":"hello"}'
    assert payload["tool_request"]["tool_name"] == "music_search"
    assert payload["metadata"]["source"] == "pytest"


def test_prompt_builder_includes_agentic_context_and_output_contract(tmp_path):
    agentic_root = tmp_path / ".agentic"
    skills_dir = agentic_root / "skills"
    skills_dir.mkdir(parents=True)
    (agentic_root / "soul.md").write_text("Soul line", encoding="utf-8")
    (agentic_root / "agentic.md").write_text("Agentic line", encoding="utf-8")
    (skills_dir / "music_bgm.md").write_text(
        "\n".join(
            [
                "name: music_bgm",
                "description: Play music.",
                "trigger: music, bgm",
                "behavior: music_idle",
                "xp_reward: 8",
            ]
        ),
        encoding="utf-8",
    )

    skills = SkillLoader(skills_dir).load_skills()
    builder = PromptBuilder(agentic_root)
    prompt = builder.build(
        event=UserEvent(text="play music"),
        skills=skills,
        state_snapshot={"user_progress": {"xp_total": 12}},
        matched_skill=skills[0],
    )

    assert "Soul line" in prompt.prompt
    assert "Agentic line" in prompt.prompt
    assert "music_bgm" in prompt.prompt
    assert "reply" in prompt.prompt
    assert "matched_skill" in prompt.prompt
    assert "private chain-of-thought" in prompt.prompt


def test_api_provider_uses_mocked_transport_and_normalizes_content(monkeypatch):
    monkeypatch.setenv("ECHOES_API_KEY", "secret")
    provider = APIProvider(
        ProviderConfig(
            provider_type=ProviderType.API,
            base_url="https://example.invalid/v1/chat/completions",
            model_name="demo-model",
            api_key_env_var="ECHOES_API_KEY",
        ),
        request_fn=lambda **_: FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "reply": "api hello",
                                    "matched_skill": "music_bgm",
                                    "confidence": 0.9,
                                }
                            )
                        }
                    }
                ]
            }
        ),
    )

    reply = provider.generate_reply(
        UserEvent(text="hello"),
        prompt_text="prompt body",
    )

    assert reply.provider_status.healthy is True
    assert '"reply": "api hello"' in reply.raw_text
    assert reply.prompt_text == "prompt body"


def test_api_provider_missing_key_falls_back_without_crashing():
    provider = APIProvider(
        ProviderConfig(
            provider_type=ProviderType.API,
            base_url="https://example.invalid/v1/chat/completions",
            model_name="demo-model",
            api_key_env_var="MISSING_ECHOES_API_KEY",
            fallback_provider=ProviderType.LOW_SPEC,
        )
    )

    reply = provider.generate_reply(
        UserEvent(text="hello"),
        prompt_text="prompt body",
    )

    assert reply.provider_status.healthy is False
    assert reply.provider_status.metadata["error_category"] == "missing_api_key"
    assert reply.provider_status.metadata["fallback_provider"] == "low_spec"
    assert reply.raw_text


def test_skill_router_prefers_deterministic_match_and_filters_provider_suggestions():
    skills = SkillLoader(Path(".agentic") / "skills").load_skills()
    router = SkillRouter(skills)

    matched, source = router.route(
        "please play some bgm",
        suggested_skill_name="game_news",
        suggested_confidence=0.99,
        allow_fallback=True,
        confidence_threshold=0.6,
    )
    assert matched.name == "music_bgm"
    assert source == "deterministic"

    fallback_match, fallback_source = router.route(
        "hello there",
        suggested_skill_name="game_news",
        suggested_confidence=0.9,
        allow_fallback=True,
        confidence_threshold=0.6,
    )
    assert fallback_match.name == "game_news"
    assert fallback_source == "provider"

    no_match, no_source = router.route(
        "hello there",
        suggested_skill_name="invented_skill",
        suggested_confidence=0.99,
        allow_fallback=True,
        confidence_threshold=0.6,
    )
    assert no_match is None
    assert no_source == "none"


def test_harness_engine_keeps_deterministic_trigger_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOES_API_KEY", "secret")
    db_path = tmp_path / "pet_state.db"
    snapshot_path = tmp_path / "latest_pet_event.json"
    store = SQLiteStore(db_path)
    store.initialize()
    store.set_provider_config(
        ProviderConfig(
            provider_type=ProviderType.API,
            base_url="https://example.invalid/v1/chat/completions",
            model_name="demo-model",
            api_key_env_var="ECHOES_API_KEY",
            routing_fallback_enabled=True,
            routing_confidence_threshold=0.6,
        )
    )

    engine = PetHarnessEngine(
        agentic_root=Path(".agentic"),
        db_path=db_path,
        snapshot_path=snapshot_path,
        request_fn=lambda **_: FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "reply": "provider reply",
                                    "matched_skill": "game_news",
                                    "behavior_hint": "news_idle",
                                    "confidence": 0.95,
                                }
                            )
                        }
                    }
                ]
            }
        ),
    )

    event = engine.handle_event({"text": "please play some bgm", "source": "pytest"})

    assert event.matched_skill == "music_bgm"
    assert event.metadata["agentic"]["skill_source"] == "deterministic"
    assert engine.last_agent_result.matched_skill == "game_news"


def test_debug_cli_supports_provider_flags_and_show_prompt(tmp_path):
    db_path = tmp_path / "pet_state.db"
    snapshot_path = tmp_path / "latest_pet_event.json"

    run = subprocess.run(
        [
            sys.executable,
            "scripts/debug_harness.py",
            "--text",
            "hello",
            "--provider",
            "api",
            "--show-prompt",
            "--db-path",
            str(db_path),
            "--snapshot-path",
            str(snapshot_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(run.stdout)
    assert payload["reply"]
    assert payload["debug_prompt"]
    assert "reply" in payload["debug_prompt"]
    assert payload["provider_status"]["metadata"]["error_category"] == "missing_api_key"
