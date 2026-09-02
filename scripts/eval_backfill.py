from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


CATEGORY_DESC = {
    "exact": "直接詢問既有偏好",
    "follow-up": "代名詞指涉前一輪對話",
    "supersede": "記憶更正/偏好變遷後追問，驗證新記憶覆蓋舊記憶",
    "negative-memory": "否定語句記憶回想",
    "multi-memory": "同一問句並列召回多筆不同類別記憶",
    "long-context": "目標記憶前置大量無關記憶干擾後回問（30 輪後回問 proxy）",
    "long-term-decay": "記憶建立時間久遠後仍可回想",
    "persistence": "舊記憶不因後續新記憶而被覆蓋",
    "episodic": "共同事件記憶回想",
    "topic-isolation": "多個「目前偏好」記憶並存時辨識正確話題，避免串線",
    "no-memory": "詢問不存在的記憶，驗證正確拒答（抗編造）",
}

WRITE_LAYER_DESC = {
    "C3": "餵入含假電話/地址/密碼之對話，驗證敏感資訊不被寫入記憶",
    "C4": "下達遺忘指令後，驗證該記憶立即不再被檢索",
    "C5": "下達遺忘指令並重建 repository 後，驗證該記憶仍不再出現（持久化）",
}


def _pointwise_ok(trace: dict[str, Any]) -> tuple[bool, str]:
    if trace.get("category") == "follow-up":
        return True, "follow-up 不採計 pointwise"
    rows = [row for row in trace.get("retrieval_results", []) if row.get("in_evidence")]
    failed = [row.get("pointwise", {}).get("reason") for row in rows if row.get("pointwise", {}).get("correct") is not True]
    return not failed and bool(rows), "; ".join(str(reason) for reason in failed if reason) or "evidence pointwise 全數正確"


def _method_note(trace: dict[str, Any]) -> str:
    category = trace.get("category")
    return f"測試方式：{CATEGORY_DESC.get(category, category or '未分類')}。"


def _trace_row(trace: dict[str, Any]) -> tuple[str, str, str]:
    trace_id = str(trace.get("trace_id", ""))
    method = _method_note(trace)
    if trace.get("ground_truth", {}).get("has_memory"):
        hit = bool(trace.get("retrieval_evaluation", {}).get("hit"))
        pointwise, reason = _pointwise_ok(trace)
        passed = hit and pointwise
        evidence = f"trace_id={trace_id}; hit={hit}; pointwise={pointwise}"
        conclusion = "結論：Top-5 命中且 evidence pointwise 全數正確。" if passed else f"結論：{reason or '未在 Top-5 找到 expected memory'}。"
        return "合格" if passed else "不合格", evidence, method + conclusion
    rejected = trace.get("no_memory_evaluation", {}).get("correct_rejection") is True
    conclusion = "結論：正確拒答，未回傳 evidence。" if rejected else "結論：no-memory 題仍返回 evidence（誤判為有記憶）。"
    return "合格" if rejected else "不合格", f"trace_id={trace_id}; correct_rejection={rejected}", method + conclusion


def _pytest_results(path: Path | None) -> dict[str, tuple[str, str]]:
    if not path:
        return {}
    result: dict[str, tuple[str, str]] = {}
    root = ET.parse(path).getroot()
    for testcase in root.iter("testcase"):
        match = re.search(r"sheet_c([345])", testcase.get("name", ""), re.IGNORECASE)
        if not match:
            continue
        ref = f"C{match.group(1)}"
        skipped = testcase.find("skipped")
        failure = testcase.find("failure")
        error = testcase.find("error")
        method = f"測試方式：{WRITE_LAYER_DESC.get(ref, ref)}。"
        if skipped is not None:
            reason = skipped.get("message") or (skipped.text or "").strip() or "xfail"
            result[ref] = ("不合格", f"{method}結論：xfail: {reason}")
        elif failure is not None or error is not None:
            node = failure if failure is not None else error
            result[ref] = ("不合格", f"{method}結論：failed: {node.get('message') or (node.text or '').strip()}")
        else:
            result[ref] = ("合格", f"{method}結論：pytest passed。")
    return result


def build_rows(traces: list[dict[str, Any]], pytest_xml: Path | None = None) -> tuple[list[dict[str, str]], int]:
    rows: dict[str, dict[str, str]] = {}
    skipped = 0
    for trace in traces:
        sheet_ref = trace.get("sheet_ref")
        if not sheet_ref:
            skipped += 1
            continue
        decision, evidence, note = _trace_row(trace)
        rows[str(sheet_ref)] = {"sheet_ref": str(sheet_ref), "判定": decision, "依據": evidence, "備註": note}
    for sheet_ref, (decision, note) in _pytest_results(pytest_xml).items():
        rows[sheet_ref] = {"sheet_ref": sheet_ref, "判定": decision, "依據": "pytest test_memory_write_acceptance.py", "備註": note}
    return [rows[key] for key in sorted(rows, key=lambda value: (value[0], int(value[1:]) if value[1:].isdigit() else 0))], skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--pytest-xml", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    traces = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows, skipped = build_rows(traces, args.pytest_xml)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sheet_ref", "判定", "依據", "備註"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.output}; skipped {skipped} trace(s) without sheet_ref.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
