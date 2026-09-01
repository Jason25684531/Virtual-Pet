from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from scripts.eval_harness import build_ollama_pointwise_evaluator, run_evaluation, run_threshold_sweep


QUALITY_KPIS = (
    ("Retrieval 找對率", "retrieval_accuracy"),
    ("Pointwise 判定正確率", "pointwise_accuracy"),
    ("No-Memory 拒絕率", "no_memory_rejection_rate"),
)


def quality_report(summary: dict) -> tuple[dict, str]:
    values = {key: summary.get(key, {}).get("value") for _, key in QUALITY_KPIS}
    lines = ["RAG Quality", "─" * 24]
    lines.extend(f"{label:<24}{'N/A' if values[key] is None else f'{values[key]:.2%}'}" for label, key in QUALITY_KPIS)
    return {"rag_quality": values}, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-set", default="tests/data/retrieval_eval_set.json")
    parser.add_argument("--real-encoder", action="store_true")
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--output", default="outputs/retrieval_eval/latest_trace.jsonl")
    parser.add_argument("--pointwise", action="store_true", help="Score evidence with the configured Ollama model.")
    parser.add_argument("--pointwise-model", default=config.POINTWISE_OLLAMA_MODEL)
    parser.add_argument("--pointwise-base-url", default=config.POINTWISE_OLLAMA_BASE_URL)
    args = parser.parse_args()
    cases = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    pointwise = build_ollama_pointwise_evaluator(args.pointwise_model, args.pointwise_base_url, config.POINTWISE_OLLAMA_TIMEOUT_SEC) if args.pointwise else None
    report = run_evaluation(cases, real_encoder=args.real_encoder, collect_traces=True, pointwise_evaluator=pointwise)
    if args.real_encoder and args.sweep:
        calibration = run_threshold_sweep(cases, [round(i / 20, 2) for i in range(19)])
        report["baseline"] = calibration.get("rows", [{}])[0] if calibration.get("rows") else {}
        report["threshold_sweep"] = calibration.get("rows", [])
        report["selection"] = calibration.get("selection", {})
        report["real_model_calibration"] = calibration.get("calibration", report).get("real_model_calibration", report.get("real_model_calibration"))
        selected = report["selection"].get("selected_threshold", 0.0)
        if report["threshold_sweep"] and report["selection"].get("selected") and report["valid_relevant_cases"] >= 10 and report["valid_no_memory_cases"] >= 5:
            report["final"] = run_evaluation(cases, real_encoder=True, collect_traces=True, pointwise_evaluator=pointwise)
        else:
            report["final"] = report
    output = Path("outputs/retrieval_eval/latest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_json, summary_text = quality_report(report.get("summary", {}))
    output.write_text(json.dumps(summary_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    trace_report = report.get("final", report)
    trace_path = Path(args.output)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("".join(json.dumps(trace, ensure_ascii=False) + "\n" for trace in trace_report.get("traces", [])), encoding="utf-8")
    print(summary_text)
    for failure in report["case_results"]:
        print("FAILED", json.dumps(failure, ensure_ascii=False))
    return 2 if report["configuration_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
