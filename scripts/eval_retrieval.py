from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from scripts.eval_harness import build_ollama_pointwise_evaluator, run_evaluation, run_threshold_sweep
from pet_harness.memory.fastembed_reranker import FastembedReranker


QUALITY_KPIS = (
    ("Retrieval 找對率", "retrieval_accuracy"),
    ("Pointwise 判定正確率", "pointwise_accuracy"),
    ("No-Memory 拒絕率", "no_memory_rejection_rate"),
)
# ponytail: these are acceptance thresholds from the scorecard; keep them visible and fixed.
RETRIEVAL_GATE = 0.90
NO_MEMORY_GATE = 0.95
POINTWISE_GATE = 0.90
RED_LINE_GATE = 1.0


def quality_report(summary: dict) -> tuple[dict, str]:
    values = {key: summary.get(key, {}).get("value") for _, key in QUALITY_KPIS}
    lines = ["RAG Quality", "─" * 24]
    lines.extend(f"{label:<24}{'N/A' if values[key] is None else f'{values[key]:.2%}'}" for label, key in QUALITY_KPIS)
    return {"rag_quality": values}, "\n".join(lines)


def _ratio(passed: int, total: int) -> dict[str, Any]:
    return {"value": passed / total if total else None, "passed": passed, "total": total}


def _trace_passed(trace: dict[str, Any]) -> bool:
    if trace.get("ground_truth", {}).get("has_memory"):
        if not trace.get("retrieval_evaluation", {}).get("hit", False):
            return False
        if trace.get("category") == "follow-up":
            return True
        evidence = [row for row in trace.get("retrieval_results", []) if row.get("in_evidence")]
        return bool(evidence) and all(row.get("pointwise", {}).get("correct") is True for row in evidence)
    return trace.get("no_memory_evaluation", {}).get("correct_rejection") is True


def gate_report(summary: dict[str, Any], traces: list[dict[str, Any]], baseline_traces: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    relevant = [trace for trace in traces if trace.get("ground_truth", {}).get("has_memory")]
    no_memory = [trace for trace in traces if trace.get("no_memory_evaluation", {}).get("is_no_memory_query")]
    red_lines = [trace for trace in no_memory if str(trace.get("sheet_ref") or "").upper().startswith("C")]
    pointwise_rows = [
        row for trace in relevant if trace.get("category") != "follow-up"
        for row in trace.get("retrieval_results", []) if row.get("in_evidence")
    ]
    pointwise_passed = sum(row.get("pointwise", {}).get("correct") is True for row in pointwise_rows)
    retrieval = summary.get("retrieval_accuracy", {})
    no_memory_result = summary.get("no_memory_rejection_rate", {})
    retrieval_result = {"value": retrieval.get("value"), "threshold": RETRIEVAL_GATE, "passed": retrieval.get("value") is None or retrieval.get("value", 0) >= RETRIEVAL_GATE, "passed_cases": retrieval.get("passed", 0), "total": retrieval.get("total", len(relevant))}
    no_memory_gate = {"value": no_memory_result.get("value"), "threshold": NO_MEMORY_GATE, "passed": no_memory_result.get("value") is None or no_memory_result.get("value", 0) >= NO_MEMORY_GATE, "passed_cases": no_memory_result.get("correct_rejections", 0), "total": no_memory_result.get("total_no_memory", len(no_memory))}
    red_line = {"value": (sum(trace["no_memory_evaluation"].get("correct_rejection") is True for trace in red_lines) / len(red_lines) if red_lines else None), "threshold": RED_LINE_GATE, "passed": not red_lines or all(trace["no_memory_evaluation"].get("correct_rejection") is True for trace in red_lines), "passed_cases": sum(trace["no_memory_evaluation"].get("correct_rejection") is True for trace in red_lines), "total": len(red_lines), "trace_ids": [trace.get("trace_id") for trace in red_lines if trace["no_memory_evaluation"].get("correct_rejection") is not True]}
    pointwise = {"value": pointwise_passed / len(pointwise_rows) if pointwise_rows else None, "threshold": POINTWISE_GATE, "passed": not pointwise_rows or pointwise_passed / len(pointwise_rows) >= POINTWISE_GATE, "passed_cases": pointwise_passed, "total": len(pointwise_rows)}
    regressions = []
    if baseline_traces is not None:
        current_by_id = {trace.get("trace_id"): trace for trace in traces}
        for old in baseline_traces:
            current = current_by_id.get(old.get("trace_id"))
            if _trace_passed(old) and current is not None and not _trace_passed(current):
                regressions.append({"trace_id": current.get("trace_id"), "sheet_ref": current.get("sheet_ref") or old.get("sheet_ref")})
    regression = {"value": len(regressions), "threshold": 0, "passed": not regressions, "regressed": regressions}
    conditions = {"retrieval": retrieval_result, "no_memory": {**no_memory_gate, "red_line": red_line}, "pointwise": pointwise, "regressions": regression}
    failed = [name for name, condition in conditions.items() if not condition["passed"]]
    return {"passed": not failed, "conditions": conditions, "failed_conditions": failed, "baseline_provided": baseline_traces is not None}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-set", default="tests/data/retrieval_eval_set.json")
    parser.add_argument("--real-encoder", action="store_true")
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--output", default="outputs/retrieval_eval/latest_trace.jsonl")
    parser.add_argument("--pointwise", action="store_true", help="Score evidence with the configured Ollama model.")
    parser.add_argument("--pointwise-model", default=config.POINTWISE_OLLAMA_MODEL)
    parser.add_argument("--pointwise-base-url", default=config.POINTWISE_OLLAMA_BASE_URL)
    parser.add_argument("--gate", action="store_true", help="Fail with exit code 3 when acceptance thresholds are not met.")
    parser.add_argument("--baseline", type=Path, help="Previous JSONL trace file for regression comparison.")
    args = parser.parse_args()
    cases = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    pointwise = build_ollama_pointwise_evaluator(args.pointwise_model, args.pointwise_base_url, config.POINTWISE_OLLAMA_TIMEOUT_SEC) if args.pointwise else None
    reranker = FastembedReranker() if args.real_encoder and config.MEMORY_RERANK_ENABLED else None
    report = run_evaluation(cases, real_encoder=args.real_encoder, collect_traces=True, reranker=reranker, pointwise_evaluator=pointwise)
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
    trace_report = report.get("final", report)
    summary = trace_report.get("summary", report.get("summary", {}))
    baseline_traces = _read_jsonl(args.baseline) if args.baseline else None
    gate = gate_report(summary, trace_report.get("traces", []), baseline_traces) if args.gate else None
    output = Path("outputs/retrieval_eval/latest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_json, summary_text = quality_report(summary)
    latest = {**summary_json, "summary_metrics": {**trace_report.get("summary", {}).get("summary_metrics", {}), "recall_at_3": trace_report.get("recall_at_3")}, "gate": gate}
    output.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    trace_path = Path(args.output)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("".join(json.dumps(trace, ensure_ascii=False) + "\n" for trace in trace_report.get("traces", [])), encoding="utf-8")
    print(summary_text)
    for failure in report["case_results"]:
        print("FAILED", json.dumps(failure, ensure_ascii=False))
    if args.baseline:
        print(f"Baseline: {args.baseline}")
    if gate:
        for name, condition in gate["conditions"].items():
            print(f"GATE {name}: {'PASS' if condition['passed'] else 'FAIL'} {json.dumps(condition, ensure_ascii=False)}")
    if report["configuration_failure_count"]:
        return 2
    return 3 if gate and not gate["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
