"""用 Qwen3-TTS 1.7B VoiceDesign 生成角色參考聲音（沒有授權真人錄音時的替代來源）。

在 .venv-qwen3tts 執行；輸出 poc/out/voices/<name>.wav 與 poc/voices.json（POC 共用的聲線清單）。
VoiceDesign 是隨機取樣：固定 seed 才能重現；聽過不滿意就換 --seed 重跑。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ref_text 避開罕見人名：VoiceDesign 會念錯，參考音與逐字稿不一致會拖累 ICL 複製。
# ponytail: 兩個 POC 角色寫死；M4 起聲線改由 registry 管，這張表不會長大
VOICES = {
    "omni-zh": {
        "instruct": "男孩聲音，約十歲，音調偏高，語氣熱情浮誇、充滿活力，台灣口音的標準中文",
        "ref_text": "大家好！我是實驗室的助理機器人，今天也要一起探索好玩的東西喔！",
    },
    "zenni-zh": {
        "instruct": "年輕女生聲音，語氣溫和可愛、親切柔和，語速適中，台灣口音的標準中文",
        "ref_text": "你好呀，很高興認識你。今天想聊些什麼呢？都可以慢慢跟我說喔。",
    },
}
LICENSE_NOTE = "Qwen3-TTS-12Hz-1.7B-VoiceDesign 生成（Apache-2.0），非真人聲音"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=str(ROOT / "models" / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"))
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")  # Windows cp950 主控台印不出的字元不讓腳本崩潰

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    import time
    t0 = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(args.model, device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa")
    print(f"load_s={time.perf_counter() - t0:.1f}")
    out_dir = ROOT / "poc" / "out" / "voices"
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, spec in VOICES.items():
        torch.manual_seed(args.seed)
        wavs, sr = model.generate_voice_design(text=spec["ref_text"], instruct=spec["instruct"], language="Chinese")
        path = out_dir / f"{name}.wav"
        sf.write(path, wavs[0], sr, subtype="PCM_16")
        seconds = len(wavs[0]) / sr
        print(f"{name}: {seconds:.1f}s -> {path}" + ("" if 3 <= seconds <= 10 else "  警告：不在 3-10 秒，換 seed 或改 ref_text"))
        entries.append({"name": name, "ref_audio": str(path), "ref_text": spec["ref_text"], "source": "designed",
                        "license_note": f"{LICENSE_NOTE}，seed={args.seed}", "instruct": spec["instruct"]})
    print(f"vram_peak_mb={torch.cuda.max_memory_allocated() / 2**20:.0f}")
    (ROOT / "poc" / "voices.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
