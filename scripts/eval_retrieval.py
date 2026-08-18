from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_harness import run_evaluation, run_threshold_sweep


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-set", default="tests/data/retrieval_eval_set.json")
    parser.add_argument("--real-encoder", action="store_true")
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()
    cases = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    report = run_evaluation(cases, real_encoder=args.real_encoder)
    if args.real_encoder and args.sweep:
        calibration = run_threshold_sweep(cases, [round(i / 20, 2) for i in range(19)])
        report["baseline"] = calibration.get("rows", [{}])[0] if calibration.get("rows") else {}
        report["threshold_sweep"] = calibration.get("rows", [])
        report["selection"] = calibration.get("selection", {})
        report["real_model_calibration"] = calibration.get("calibration", report).get("real_model_calibration", report.get("real_model_calibration"))
        selected = report["selection"].get("selected_threshold", 0.0)
        if report["threshold_sweep"] and report["selection"].get("selected") and report["valid_relevant_cases"] >= 10 and report["valid_no_memory_cases"] >= 5:
            report["final"] = run_evaluation(cases, real_encoder=True)
        else:
            report["final"] = report
    output = Path("outputs/retrieval_eval/latest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Evaluation Summary")
    for key in ("cases", "valid_relevant_cases", "valid_no_memory_cases", "recall_at_3", "mrr_at_3", "no_memory_rejection_rate", "production_qdrant_calls_per_query", "latency_ms", "real_model_calibration", "selection"):
        if key in report:
            print(f"{key}: {report[key]}")
    print("Dense cosine calibration:", json.dumps(report["dense_scores"], ensure_ascii=False))
    for failure in report["case_results"]:
        print("FAILED", json.dumps(failure, ensure_ascii=False))
    return 2 if report["configuration_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
