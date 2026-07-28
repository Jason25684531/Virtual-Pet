from __future__ import annotations

import argparse
import json
import statistics
import time

import requests

PROMPTS = ("那個呢？", "他後來怎麼了？", "剛才提到的水果是什麼？", "小白呢？")


def measure(model: str, base_url: str, samples: int) -> dict:
    url = f"{base_url.rstrip('/')}/api/generate"
    values = []
    for index in range(samples + 1):
        started = time.perf_counter()
        response = requests.post(url, json={"model": model, "prompt": f"將這句改寫成獨立查詢，只輸出查詢：{PROMPTS[index % len(PROMPTS)]}", "stream": False}, timeout=60)
        response.raise_for_status()
        if index:
            values.append((time.perf_counter() - started) * 1000)
    ordered = sorted(values)
    return {"model_name": model, "base_url": base_url, "samples": len(values), "p50_ms": statistics.median(values), "p95_ms": ordered[round(.95 * (len(ordered) - 1))], "samples_ms": values}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    if args.samples < 20: parser.error("--samples MUST be at least 20")
    print(json.dumps(measure(args.model, args.base_url, args.samples), ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
