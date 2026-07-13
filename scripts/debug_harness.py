from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet_harness.agent.ollama_provider import OllamaProvider
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.provider import ProviderConfig, ProviderType
from pet_harness.runtime.provider_runtime import ProviderRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Debug the ECHOES Pet Harness Engine.")
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--text", help="Process text through the harness.")
    command.add_argument("--list-skills", action="store_true", help="List loaded skills.")
    command.add_argument("--state", action="store_true", help="Show SQLite-backed harness state.")
    command.add_argument("--recent-events", action="store_true", help="Show recent event log entries.")
    command.add_argument("--debug-status", action="store_true", help="Show debug harness status.")
    command.add_argument("--list-tools", action="store_true", help="List registered tools.")
    command.add_argument("--tool-status", action="store_true", help="Show tool registry status.")
    command.add_argument("--run-tool", help="Run a registered safe tool.")
    command.add_argument("--asset-mock", action="store_true", help="Run mock asset flow.")
    command.add_argument("--ollama-health", action="store_true", help="Run Ollama health check.")
    command.add_argument("--ollama-model", help="Check whether an Ollama model exists.")
    parser.add_argument("--agentic-root", default=".agentic")
    parser.add_argument("--db-path", default=str(Path("data") / "pet_state.db"))
    parser.add_argument(
        "--snapshot-path",
        default=str(Path("debug") / "events" / "latest_pet_event.json"),
    )
    parser.add_argument("--provider", choices=["api", "ollama"])
    parser.add_argument("--provider-base-url")
    parser.add_argument("--model")
    parser.add_argument("--show-prompt", action="store_true")
    parser.add_argument("--show-raw-result", action="store_true")
    parser.add_argument("--show-agent-result", action="store_true")
    parser.add_argument("--show-tool-result", action="store_true")
    parser.add_argument("--show-asset-result", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    return parser


def build_provider_config(args: argparse.Namespace) -> ProviderConfig | None:
    if not args.provider:
        return None
    provider_type = ProviderType(args.provider)
    return ProviderConfig(
        provider_type=provider_type,
        base_url=args.provider_base_url,
        model_name=args.model or ("demo-model" if provider_type is ProviderType.API else None),
        api_key_env_var="ECHOES_API_KEY" if provider_type is ProviderType.API else None,
        routing_fallback_enabled=provider_type is ProviderType.API,
    )


def main() -> int:
    args = build_parser().parse_args()
    runtime = ProviderRuntime()
    provider_config = build_provider_config(args)
    if provider_config is not None:
        runtime.configure(provider_config)
    engine = PetHarnessEngine(
        provider=runtime,
        agentic_root=Path(args.agentic_root),
        db_path=Path(args.db_path),
        snapshot_path=Path(args.snapshot_path),
        provider_config=provider_config,
    )

    if args.text is not None:
        output = engine.handle_event({"text": args.text, "source": "debug_cli"}).to_dict()
        if args.show_prompt:
            output["debug_prompt"] = engine.last_prompt
        if args.show_raw_result:
            output["debug_raw_result"] = engine.last_provider_raw_result
        if args.show_agent_result and engine.last_agent_result is not None:
            output["debug_agent_result"] = engine.last_agent_result.to_dict()
    elif args.list_skills:
        output = [skill.to_dict() for skill in engine.skills]
    elif args.list_tools:
        output = engine.list_tools()
    elif args.tool_status:
        output = engine.tool_status()
    elif args.run_tool:
        output = engine.run_tool(args.run_tool)
        if not args.show_tool_result:
            output = {"tool_name": output["tool_result"]["tool_name"], "status": output["tool_result"]["status"]}
    elif args.asset_mock:
        output = engine.run_asset_mock()
        if not args.show_asset_result:
            output = {"status": output["asset_result"]["status"]}
    elif args.ollama_health:
        ollama = OllamaProvider(
            runtime.get_config()
            or ProviderConfig(provider_type=ProviderType.OLLAMA, base_url=args.provider_base_url)
        )
        output = {"provider_status": ollama.provider_status_from_health().to_dict()}
    elif args.ollama_model:
        ollama = OllamaProvider(
            runtime.get_config()
            or ProviderConfig(provider_type=ProviderType.OLLAMA, base_url=args.provider_base_url)
        )
        output = {"model_check": ollama.check_model(args.ollama_model)}
    elif args.state:
        output = engine.state_snapshot()
    elif args.recent_events:
        output = engine.recent_events(limit=args.limit)
    else:
        output = engine.debug_status()

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
