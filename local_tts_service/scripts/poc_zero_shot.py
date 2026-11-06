"""M1 zero-shot POC：三個候選用同一份句子、聲線、seed 合成並量測。

  .venv-qwen3tts\\Scripts\\python scripts\\poc_zero_shot.py --backend qwen3tts-0.6b
  .venv-qwen3tts\\Scripts\\python scripts\\poc_zero_shot.py --backend qwen3tts-1.7b
  set LOCAL_TTS_ALLOW_NONCOMMERCIAL=1 && .venv-omnivoice\\Scripts\\python scripts\\poc_zero_shot.py --backend omnivoice

輸出 poc/out/<backend>/：每句 WAV、metrics.csv、summary.json。dtype 依各官方範例（Qwen bf16、OmniVoice fp16）。

--voice-dir <dir>：改用使用者提供的角色參考音（<dir>\\<角色>\\ref.wav|aac|m4a|mp3|flac + ref.txt），
輸出到 poc/out/<backend>-user/。每個角色的 prompt 快取以「音檔內容＋逐字稿＋backend 版本」為指紋，換檔即重建。
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import importlib.metadata
import json
import os
import statistics
import subprocess
import sys
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "qwen3tts-0.6b": "Qwen3-TTS-12Hz-0.6B-Base",
    "qwen3tts-1.7b": "Qwen3-TTS-12Hz-1.7B-Base",
    "omnivoice": "OmniVoice",
}
AUDIO_EXTS = (".wav", ".aac", ".m4a", ".mp3", ".flac")


class QwenRunner:
    def __init__(self, model_dir: Path):
        import torch
        from qwen_tts import Qwen3TTSModel

        self._model = Qwen3TTSModel.from_pretrained(str(model_dir), device_map="cuda:0", dtype=torch.bfloat16,
                                                    attn_implementation="sdpa")

    PACKAGE = "qwen-tts"

    def save_prompt(self, prompt, path: Path) -> None:
        # 存成純 dict（tensor/bool/str），weights_only=True 載入，不 unpickle 任意物件
        import torch

        torch.save([dataclasses.asdict(item) for item in prompt], path)

    def load_prompt(self, path: Path):
        import torch
        from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem

        return [VoiceClonePromptItem(**d) for d in torch.load(path, map_location="cuda:0", weights_only=True)]

    def synth(self, text: str, prompt):
        wavs, sr = self._model.generate_voice_clone(text=text, language="Chinese", voice_clone_prompt=prompt)
        return wavs[0], sr


class OmniVoiceRunner:
    def __init__(self, model_dir: Path):
        if os.environ.get("LOCAL_TTS_ALLOW_NONCOMMERCIAL") != "1":
            raise SystemExit("OmniVoice 權重 CC-BY-NC：只供內部非商用比較，需設 LOCAL_TTS_ALLOW_NONCOMMERCIAL=1")
        import torch
        from omnivoice import OmniVoice

        self._model = OmniVoice.from_pretrained(str(model_dir), device_map="cuda:0", dtype=torch.float16)

    PACKAGE = "omnivoice"

    def save_prompt(self, prompt, path: Path) -> None:
        prompt.save(str(path))

    def load_prompt(self, path: Path):
        from omnivoice import VoiceClonePrompt

        return VoiceClonePrompt.load(str(path))

    def synth(self, text: str, prompt):
        return self._model.generate(text=text, voice_clone_prompt=prompt)[0], 24000  # 官方：24 kHz


def nvidia_smi_used_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def load_voice_dir(voice_dir: Path, work_dir: Path, backend: str, package: str) -> list[dict]:
    """掃 <voice_dir>/<角色>/：ref 音檔用 ffmpeg 轉 24 kHz mono WAV，ref.txt 為逐字稿，算出 prompt 快取路徑。"""
    voices, problems = [], []
    for char_dir in sorted(p for p in voice_dir.iterdir() if p.is_dir()):
        audio = [f for f in char_dir.iterdir() if f.stem.lower() == "ref" and f.suffix.lower() in AUDIO_EXTS]
        txt = char_dir / "ref.txt"
        if len(audio) != 1:
            problems.append(f"{char_dir.name}: 需要剛好一個 ref.wav/aac/m4a/mp3/flac，找到 {len(audio)} 個")
            continue
        ref_text = txt.read_text(encoding="utf-8-sig").strip() if txt.is_file() else ""
        if not ref_text:
            problems.append(f"{char_dir.name}: 缺少或空白的 ref.txt（可先跑 poc_eval.py transcribe 產生 ref.asr.txt 草稿再校對）")
            continue
        work = work_dir / char_dir.name
        work.mkdir(parents=True, exist_ok=True)
        wav = work / "ref_24k.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(audio[0]), "-ac", "1", "-ar", "24000", str(wav)], check=True)
        seconds = (os.path.getsize(wav) - 44) / (24000 * 2)  # s16 mono，扣掉 WAV header
        if not 3 <= seconds <= 10:
            print(f"警告：{char_dir.name} 參考音 {seconds:.1f}s，官方建議 3-10 秒（過長會變慢、可能降低相似度）")
        fp = hashlib.sha256(audio[0].read_bytes() + ref_text.encode("utf-8")
                            + f"{backend}:{importlib.metadata.version(package)}".encode()).hexdigest()[:16]
        voices.append({"name": char_dir.name, "ref_audio": str(wav), "ref_text": ref_text, "source_file": str(audio[0]),
                       "prompt_cache": work / f"prompt.{backend}.{fp}.pt"})
    if problems or not voices:
        raise SystemExit("參考音資料夾有問題：\n  " + "\n  ".join(problems or [f"{voice_dir} 底下沒有任何角色資料夾"]))
    return voices


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", required=True, choices=sorted(MODELS))
    ap.add_argument("--voices", default=str(ROOT / "poc" / "voices.json"))
    ap.add_argument("--voice-dir", help="使用者角色參考音資料夾；給了就忽略 --voices")
    ap.add_argument("--only", help="只跑這些角色（逗號分隔），搭配 --voice-dir")
    ap.add_argument("--tag", default="user", help="輸出到 poc/out/<backend>-<tag>/，例如 Zenni_ja")
    ap.add_argument("--sentences", default=str(ROOT / "poc" / "sentences_zh.txt"))
    ap.add_argument("--stress", type=int, default=200, help="連續穩定性測試句數，0 = 略過")
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")  # Windows cp950 主控台印不出的字元不讓腳本崩潰

    os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 模型已在本機；任何連網嘗試都應失敗而不是默默下載
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import soundfile as sf
    import torch

    sentences = [s for s in Path(args.sentences).read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    package = (OmniVoiceRunner if args.backend == "omnivoice" else QwenRunner).PACKAGE
    if args.voice_dir:
        voices = load_voice_dir(Path(args.voice_dir), ROOT / "poc" / "out" / "user_voices", args.backend, package)
        if args.only:
            wanted = [n.strip() for n in args.only.split(",")]
            missing = [n for n in wanted if n not in {v["name"] for v in voices}]
            if missing:
                raise SystemExit(f"--only 找不到角色：{missing}")
            voices = [v for v in voices if v["name"] in wanted]
        out = ROOT / "poc" / "out" / f"{args.backend}-{args.tag}"
    else:
        voices = json.loads(Path(args.voices).read_text(encoding="utf-8"))
        out = ROOT / "poc" / "out" / args.backend
    out.mkdir(parents=True, exist_ok=True)
    smi_start = nvidia_smi_used_mb()

    started = time.perf_counter()
    runner = (OmniVoiceRunner if args.backend == "omnivoice" else QwenRunner)(ROOT / "models" / MODELS[args.backend])
    torch.cuda.synchronize()
    load_s = time.perf_counter() - started

    prompts, prompt_build_s = {}, {}
    for v in voices:
        cache = Path(v.get("prompt_cache") or out / f"{v['name']}.prompt.pt")
        if v.get("prompt_cache") and cache.is_file():
            prompt_build_s[v["name"]] = "cache_hit"
        else:
            t0 = time.perf_counter()
            built = runner._model.create_voice_clone_prompt(ref_audio=v["ref_audio"], ref_text=v["ref_text"])  # 兩個套件同名同參數
            prompt_build_s[v["name"]] = round(time.perf_counter() - t0, 3)
            runner.save_prompt(built, cache)
            for stale in cache.parent.glob(f"prompt.{args.backend}.*.pt"):  # 換過參考音或逐字稿的舊快取
                if stale != cache:
                    stale.unlink()
        prompts[v["name"]] = runner.load_prompt(cache)  # 之後一律用存檔再載入的 prompt
        if args.voice_dir:
            shutil.copyfile(v["ref_audio"], out / f"{v['name']}_00_ref.wav")  # 原始聲線放在旁邊方便對聽
        print(f"[prompt] {v['name']}: {prompt_build_s[v['name']]} -> {cache.name}")
    runner.synth(sentences[0], prompts[voices[0]["name"]])  # 暖機，不計入

    rows = []
    for v in voices:
        for i, text in enumerate(sentences, 1):
            torch.manual_seed(args.seed + i)
            torch.cuda.reset_peak_memory_stats()
            row = {"backend": args.backend, "voice": v["name"], "idx": i, "chars": len(text.replace(" ", "")), "text": text}
            try:
                torch.cuda.synchronize()
                t0 = time.perf_counter()
                wav, sr = runner.synth(text, prompts[v["name"]])
                torch.cuda.synchronize()
                synth_s = time.perf_counter() - t0
                path = out / f"{v['name']}_{i:02d}.wav"
                sf.write(path, wav, sr, subtype="PCM_16")
                audio_s = len(wav) / sr
                row.update(ok=1, synth_s=round(synth_s, 3), audio_s=round(audio_s, 3),
                           rtf=round(synth_s / audio_s, 4) if audio_s else None, sample_rate=sr,
                           vram_peak_mb=round(torch.cuda.max_memory_allocated() / 2**20), wav=path.name, error="")
            except Exception as exc:  # noqa: BLE001 — POC 要記錄每一種失敗而不是中斷
                row.update(ok=0, error=f"{type(exc).__name__}: {exc}"[:200])
            rows.append(row)
            print(f"[{v['name']} {i:02d}] ok={row['ok']} synth={row.get('synth_s')} audio={row.get('audio_s')}")

    with open(out / "metrics.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        writer.writeheader()
        writer.writerows(rows)

    stress = {"n": args.stress}
    if args.stress:
        alloc_before, failures = torch.cuda.memory_allocated(), []
        for n in range(args.stress):
            v = voices[n % len(voices)]
            try:
                runner.synth(sentences[n % len(sentences)], prompts[v["name"]])
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{n}: {type(exc).__name__}: {exc}"[:200])
        alloc_after = torch.cuda.memory_allocated()
        stress.update(success_rate=round(1 - len(failures) / args.stress, 4), failures=failures[:20],
                      vram_alloc_drift_pct=round((alloc_after - alloc_before) / max(alloc_before, 1) * 100, 2))

    ok = [r for r in rows if r["ok"]]
    short = [r["synth_s"] for r in ok if r["chars"] <= 30]
    summary = {
        "backend": args.backend, "load_s": round(load_s, 2), "prompt_build_s": prompt_build_s,
        "sentences_ok": f"{len(ok)}/{len(rows)}",
        "short_synth_p50_s": round(statistics.median(short), 3) if short else None,
        "short_synth_p95_s": round(statistics.quantiles(short, n=20, method="inclusive")[-1], 3) if len(short) > 1 else None,
        "rtf_mean": round(statistics.mean(r["rtf"] for r in ok if r["rtf"]), 4) if ok else None,
        "vram_peak_mb": max((r["vram_peak_mb"] for r in ok), default=None),
        "nvidia_smi_used_mb": {"start": smi_start, "end": nvidia_smi_used_mb()},
        "stress": stress, "seed": args.seed,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
