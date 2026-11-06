"""M1 評測（在 Virtual-Pet 主 .venv 執行，沿用專案的 FasterWhisperSTT 與 s2twp 繁體轉換）。

  .venv\\Scripts\\python local_tts_service\\scripts\\poc_eval.py cer      # 各 backend 的 CER 寫回 summary.json
  .venv\\Scripts\\python local_tts_service\\scripts\\poc_eval.py blind    # 打亂檔名的盲聽包 + 對照表
  .venv\\Scripts\\python local_tts_service\\scripts\\poc_eval.py selftest

  ...poc_eval.py transcribe [voice]  # 沒有 ref.txt 的角色資料夾產生 ref.asr.txt 草稿（需人工校對）

注意：參考文字寫「三點四十五分」而 ASR 輸出「3點45分」也算錯字，數字句的 CER 偏高，報告時要分開看。
"""

from __future__ import annotations

import csv
import json
import random
import re
import shutil
import string
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
OUT = ROOT / "poc" / "out"
_PUNCT = set(string.punctuation) | set("，。！？、；：「」『』（）…—～·《》")


_CN_DIGIT = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"兩": 2}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "萬": 10000}


def _cn_to_arabic(match: re.Match) -> str:
    run = match.group()
    if not any(c in _CN_UNIT for c in run):  # 五零九零 → 5090（逐位念法）
        return "".join(str(_CN_DIGIT[c]) for c in run)
    total = section = num = 0
    for c in run:
        if c in _CN_DIGIT:
            num = _CN_DIGIT[c]
        elif c == "萬":
            total, section, num = total + (section + num) * 10000, 0, 0
        else:
            section, num = section + (num or 1) * _CN_UNIT[c], 0
    return str(total + section + num)


def normalize(text: str) -> str:
    # 數字統一：參考文字與 ASR 兩邊都把中文數字轉阿拉伯數字（一千兩百三十 ↔ 1230），三個 backend 同一規則。
    # ponytail: 「一起」也會變「1起」，但兩邊同樣轉換，不影響比對；量詞或年份的特殊讀法（如「兩千零二十六年」）不另處理。
    text = re.sub(r"[零一二兩三四五六七八九十百千萬]+", _cn_to_arabic, unicodedata.normalize("NFKC", text).upper())
    return "".join(ch for ch in text if not ch.isspace() and ch not in _PUNCT)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    ref, hyp = normalize(ref), normalize(hyp)
    return edit_distance(ref, hyp) / max(len(ref), 1)


def run_cer() -> None:
    sys.path.insert(0, str(REPO))
    from faster_whisper import decode_audio

    import config
    from sensors.faster_whisper_stt import FasterWhisperSTT

    stts = {}
    for backend_dir in sorted(p for p in OUT.iterdir() if (p / "metrics.csv").is_file()):
        lang = "ja" if backend_dir.name.lower().endswith("_ja") else "zh"  # 輸出目錄以 _ja 結尾的是日文台詞
        if lang not in stts:
            stts[lang] = FasterWhisperSTT(config.STT_MODEL, config.STT_DEVICE, config.STT_COMPUTE_TYPE, config.STT_MODEL_PATH, language=lang)
            stts[lang].setup()
        stt = stts[lang]
        with open(backend_dir / "metrics.csv", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        errors = chars = 0
        for row in rows:
            if row["ok"] != "1":
                continue
            hyp = stt.transcribe(decode_audio(str(backend_dir / row["wav"]), sampling_rate=16000), 16000).text
            row["asr"], row["cer"] = hyp, round(cer(row["text"], hyp), 4)
            errors += edit_distance(normalize(row["text"]), normalize(hyp))
            chars += len(normalize(row["text"]))
        with open(backend_dir / "metrics.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            writer.writeheader()
            writer.writerows(rows)
        summary_path = backend_dir / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["cer_total"] = round(errors / max(chars, 1), 4)  # 以總字數加權，不是逐句平均
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{backend_dir.name}: CER {summary['cer_total']:.2%}")


def run_transcribe(voice_dir: str = str(REPO / "voice")) -> None:
    """替沒有 ref.txt 的角色寫 ref.asr.txt 草稿；ASR 會錯字、漏語助詞，必須人工校對後另存成 ref.txt。"""
    sys.path.insert(0, str(REPO))
    from faster_whisper import decode_audio

    import config
    from sensors.faster_whisper_stt import FasterWhisperSTT

    stt = None
    for char_dir in sorted(p for p in Path(voice_dir).iterdir() if p.is_dir()):
        audio = [f for f in char_dir.iterdir() if f.stem.lower() == "ref" and f.suffix.lower() in (".wav", ".aac", ".m4a", ".mp3", ".flac")]
        if len(audio) != 1 or (char_dir / "ref.txt").is_file():
            continue
        if stt is None:
            stt = FasterWhisperSTT(config.STT_MODEL, config.STT_DEVICE, config.STT_COMPUTE_TYPE, config.STT_MODEL_PATH, language="zh")
            stt.setup()
        text = stt.transcribe(decode_audio(str(audio[0]), sampling_rate=16000), 16000).text
        (char_dir / "ref.asr.txt").write_text(text + "\n", encoding="utf-8")
        print(f"{char_dir.name}: 草稿 -> {char_dir / 'ref.asr.txt'}")


def run_blind(seed: int = 7) -> None:
    items = [w for d in sorted(OUT.iterdir()) if (d / "metrics.csv").is_file() for w in sorted(d.glob("*_[0-9][0-9].wav"))]
    random.Random(seed).shuffle(items)
    pack = OUT / "blind"
    shutil.rmtree(pack, ignore_errors=True)
    pack.mkdir()
    with open(OUT / "blind_key.csv", "w", newline="", encoding="utf-8-sig") as f:  # 對照表放盲聽包外，評分完才打開
        writer = csv.writer(f)
        writer.writerow(["blind_name", "backend", "file", "similarity_1to5", "naturalness_1to5"])
        for n, wav in enumerate(items, 1):
            name = f"{n:03d}.wav"
            shutil.copy2(wav, pack / name)
            writer.writerow([name, wav.parent.name, wav.name, "", ""])
    print(f"{len(items)} 個檔案 -> {pack}；評分欄在 {OUT / 'blind_key.csv'}（先遮住 backend 欄）")


def selftest() -> None:
    assert edit_distance("abc", "abc") == 0 and edit_distance("abc", "axc") == 1 and edit_distance("", "ab") == 2
    assert cer("你好，世界！", "你好世界") == 0.0
    assert cer("ROG 展區", "rog展區") == 0.0
    assert abs(cer("甲乙丙丁", "甲乙丙") - 0.25) < 1e-9
    assert normalize("電費是一千兩百三十元") == normalize("電費是1230元") == "電費是1230元"
    assert normalize("RTX 五零九零") == normalize("RTX5090") and normalize("三十") == "30" and normalize("十") == "10"
    assert normalize("兩萬零五") == "20005" and normalize("一百零八") == "108"
    print("selftest ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    {"cer": run_cer, "blind": run_blind, "selftest": selftest, "transcribe": run_transcribe}[
        sys.argv[1] if len(sys.argv) > 1 else "selftest"](*sys.argv[2:])
