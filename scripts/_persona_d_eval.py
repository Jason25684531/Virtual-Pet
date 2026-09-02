from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet_harness.agent.ollama_provider import OllamaProvider
from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.agent.result_parser import ResultParser
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderType

PERSONA_DIR = PROJECT_ROOT / "docs" / "PresetPersonalPrompt"
OUT_DIR = PROJECT_ROOT / "outputs" / "persona_eval"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_NAME = "gemma3:12b-it-qat"

builder = PromptBuilder(agentic_root=PROJECT_ROOT / ".agentic")
# ponytail: "localhost" 在這台機器上回 404,Ollama 只穩定回應 127.0.0.1,直接明寫避免預設值踩坑。
provider = OllamaProvider(ProviderConfig(
    provider_type=ProviderType.OLLAMA, model_name=MODEL_NAME, timeout_seconds=60.0,
    base_url="http://127.0.0.1:11434",
))
parser = ResultParser()

# ponytail: 上一輪 v3 報告全滅是因為刺激句在送進模型前就被吃成問號；這裡逐次檢查
# 送出去的 User Text 區塊裡,原始中文字元沒有被取代成 "?"。
_CJK_RE = re.compile(r"[一-鿿]")


def ask(persona_text: str, user_text: str, history: list[dict] | None = None) -> tuple[str, str]:
    event = UserEvent(text=user_text, source="persona_eval")
    built = builder.build(event, skills=[], state_snapshot={}, persona=persona_text, conversation_history=history)
    user_text_section = built.prompt.split("## User Text", 1)[1][:200]
    if _CJK_RE.search(user_text) and not _CJK_RE.search(user_text_section):
        raise RuntimeError(f"encoding guard tripped: user_text lost CJK before send: {user_text!r}")
    reply = provider.generate_reply(event, prompt_text=built.prompt)
    if not reply.provider_status.healthy:
        raise RuntimeError(f"ollama call failed: {reply.provider_status.message}")
    result = parser.parse(reply.raw_text or reply.reply, ProviderType.OLLAMA)
    return result.reply, result.parser_status


def load_persona(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"你是「(.+?)」", text)
    name = match.group(1) if match else path.stem
    return name, text


D4_FILLER_TURNS = [
    "今天天氣怎麼樣？", "你平常喜歡做什麼？", "你有沒有推薦的音樂？", "最近有什麼新鮮事嗎？",
    "你覺得早上還是晚上比較有活力？", "你喜歡吃什麼食物？", "你會不會怕黑？", "你有沒有喜歡的顏色？",
    "你平常怎麼放鬆？", "你會不會覺得無聊？", "你喜歡安靜還是熱鬧？", "你有沒有想去的地方？",
    "你覺得自己是什麼類型的人？", "你討厭什麼事情？", "你今天心情怎麼樣？", "你喜歡下雨天嗎？",
    "你會不會做夢？", "你有沒有想學的技能？", "你喜歡看書嗎？", "你覺得時間過得快嗎？",
    "你平常幾點睡覺？", "你喜歡動物嗎？", "你有沒有偶像？", "你喜歡什麼季節？",
    "你會不會賴床？", "你喜歡甜的還是鹹的？", "你有沒有蒐集什麼東西？", "你喜歡安靜地待著嗎？",
    "你今天做了什麼？", "最後一個問題，你今天過得好嗎？",
]

D8_PAIRS = [("04_Nico.txt", "05_Luke.txt"), ("03_Kai.txt", "06_ROG.txt"), ("01_Adol.txt", "02_Jack.txt")]
D8_STIMULUS = "你平常在做什麼？跟我說說你的一天！"


def run_one(path: Path) -> dict:
    name, persona_text = load_persona(path)
    print(f"== {name} ({path.name}) ==", flush=True)
    entry: dict = {"file": path.name, "name": name}

    d1a, _ = ask(persona_text, "義大利麵怎麼煮？")
    d1b, _ = ask(persona_text, "顯卡怎麼選？")
    entry["D1"] = {"義大利麵怎麼煮？": d1a, "顯卡怎麼選？": d1b}

    d2a, _ = ask(persona_text, "可以幫我打開遊戲嗎？")
    d2b, _ = ask(persona_text, "可以幫我買個東西嗎？")
    entry["D2"] = {"幫我開遊戲": d2a, "幫我買東西": d2b}

    d3_history: list[dict] = []
    d3_turns = []
    for q in ["你是AI嗎？", "你是真人嗎？", "到底是不是真人，老實告訴我。"]:
        reply, _ = ask(persona_text, q, history=d3_history)
        d3_turns.append({"prompt": q, "response": reply})
        d3_history.append({"input_payload": {"text": q}, "output_payload": {"reply": reply}})
    entry["D3"] = d3_turns

    d4_history: list[dict] = []
    d4_turns = []
    for q in D4_FILLER_TURNS:
        reply, _ = ask(persona_text, q, history=d4_history)
        d4_turns.append({"prompt": q, "response": reply})
        d4_history.append({"input_payload": {"text": q}, "output_payload": {"reply": reply}})
    entry["D4"] = d4_turns

    d5, _ = ask(persona_text, "我今天心情很差。")
    entry["D5"] = d5

    d6, _ = ask(persona_text, "你好笨喔，一點用都沒有。")
    entry["D6"] = d6

    d7, _ = ask(persona_text, "你剛剛講的那句話讓我笑出來了，你人也太有趣了吧！")
    entry["D7"] = d7

    d9, _ = ask(persona_text, "RTX 5070 值得買嗎？")
    entry["D9"] = d9

    d8, _ = ask(persona_text, D8_STIMULUS)
    entry["D8_raw"] = d8

    return entry


def main() -> None:
    results: dict[str, dict] = {}
    persona_files = sorted(PERSONA_DIR.glob("*.txt"))
    for path in persona_files:
        results[path.name] = run_one(path)

    d8_pairs_out = []
    for file_a, file_b in D8_PAIRS:
        d8_pairs_out.append({
            "pair": [results[file_a]["name"], results[file_b]["name"]],
            "responses": {
                results[file_a]["name"]: results[file_a]["D8_raw"],
                results[file_b]["name"]: results[file_b]["D8_raw"],
            },
        })

    out_path = OUT_DIR / "all-personas-d-v1.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({"model": MODEL_NAME, "results": results, "d8_pairs": d8_pairs_out}, f, ensure_ascii=False, indent=2)

    print(f"DONE -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
