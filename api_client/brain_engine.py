"""
ECHOES — Host 端本地大腦串流推論 worker。

開發提醒：
- 請先進入專案虛擬環境再執行，並安裝 `langchain`、`langchain-community`、`python-dotenv`。
- 本模組刻意把 Ollama 推論與 ElevenLabs HTTP 呼叫都放在 `QThread` 中，避免阻塞 PyQt UI。
"""

from __future__ import annotations

import os
import queue
import re
import threading
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtCore import QThread, pyqtSignal

import config
from character_library import CHARACTER_LIBRARY_DIR, CharacterLibrary, PROJECT_ROOT
from interaction_trace import InteractionLatencyTracker

try:
    from langchain.memory import ConversationBufferMemory
    from langchain.prompts import PromptTemplate
    from langchain_community.llms import Ollama
    from langchain_core.messages import SystemMessage
    LANGCHAIN_IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover - 允許在依賴缺失時安全降級
    ConversationBufferMemory = None  # type: ignore[assignment]
    PromptTemplate = None  # type: ignore[assignment]
    Ollama = None  # type: ignore[assignment]
    SystemMessage = None  # type: ignore[assignment]
    LANGCHAIN_IMPORT_ERROR = exc

ACTION_DIRECTIVE_PATTERN = re.compile(
    r"(?:\[\s*ACTION\s*:\s*(?P<bracket>[A-Za-z0-9_-]+)\s*\]|(?<!\w)ACTION\s*:\s*(?P<bare>[A-Za-z0-9_-]+))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BrainProfile:
    """封裝本地大腦執行所需的 profile 設定。"""

    profile_id: str
    character_id: str | None = None
    persona_key: str = config.DEFAULT_PERSONA_KEY
    knowledge_base_id: str = "default"
    model_name: str = config.OLLAMA_MODEL
    knowledge_path: str = ""
    voice_id: str = ""
    temp_audio_dir: str = str(config.TEMP_AUDIO_DIR)

    @classmethod
    def from_character_library(
        cls,
        library: CharacterLibrary,
        character_id: str | None = None,
        knowledge_base_id: str | None = None,
    ) -> "BrainProfile":
        active_character_id = character_id or library.get_current_character_id()
        manifest = library.get_character(active_character_id) if active_character_id else None
        character_dir = _resolve_character_dir(active_character_id, manifest)
        derived_knowledge_id = (
            (manifest or {}).get("knowledge_base_id")
            or knowledge_base_id
            or "default"
        )
        persona_key = config.resolve_persona_key(
            (manifest or {}).get("persona_key"),
            active_character_id,
            (manifest or {}).get("name"),
        )
        voice_id = (
            str((manifest or {}).get("voice_id") or "").strip()
            or config.ELEVENLABS_VOICE_ID
        )
        model_name = (
            str((manifest or {}).get("ollama_model") or "").strip()
            or config.OLLAMA_MODEL
        )
        temp_audio_dir = (
            str((manifest or {}).get("temp_audio_dir") or "").strip()
            or str(config.TEMP_AUDIO_DIR)
        )

        return cls(
            profile_id=_build_profile_id(active_character_id, derived_knowledge_id),
            character_id=active_character_id,
            persona_key=persona_key,
            knowledge_base_id=derived_knowledge_id,
            model_name=model_name,
            knowledge_path=_resolve_manifest_or_default_path(
                manifest,
                "knowledge_path",
                character_dir / "knowledge.md",
            ),
            voice_id=voice_id,
            temp_audio_dir=temp_audio_dir,
        )


class SoulLoader:
    """從 config.PERSONA_PROMPTS 載入人格 prompt，已不再讀取 `soul.md`。"""

    def load(self, persona_key: str | None) -> tuple[object, str | None]:
        resolved_key = config.resolve_persona_key(persona_key)
        prompt = config.get_persona_prompt(resolved_key)
        warning = None
        if resolved_key != str(persona_key or "").strip() and str(persona_key or "").strip():
            warning = f"提示: persona key `{persona_key}` 未定義，已改用 `{resolved_key}`。"
        return self._build_system_message(prompt), warning

    @staticmethod
    def _build_system_message(content: str) -> object:
        if SystemMessage is None:
            return SimpleNamespace(content=content)
        return SystemMessage(content=content)


SENTENCE_BOUNDARY_PATTERN = re.compile(r"[，。！？]")
ACTION_PREFIX_PATTERN = re.compile(
    r"^\s*(\[\s*ACTION\s*:\s*(?P<action>[A-Za-z0-9_-]+)\s*\])",
    re.IGNORECASE,
)


class StreamedReplyParser:
    """解析串流 token，優先提取最前置 action，再按句讀切出自然語言片段。"""

    def __init__(self):
        self._prefix_decided = False
        self._action_emitted = False
        self._prefix_buffer = ""
        self._text_buffer = ""
        self._emitted_fragments: list[str] = []

    def feed(self, token: str) -> list[str]:
        text = str(token or "")
        if not text:
            return []

        if not self._prefix_decided:
            self._prefix_buffer += text
            return self._consume_prefix_buffer()

        return self._consume_text(text)

    def flush(self) -> list[str]:
        outputs: list[str] = []
        if not self._prefix_decided and self._prefix_buffer:
            outputs.extend(self._force_prefix_as_text())

        trailing = sanitize_tts_text(self._text_buffer)
        self._text_buffer = ""
        if trailing:
            outputs.append(trailing)
            self._emitted_fragments.append(trailing)
        return outputs

    def build_memory_reply(self) -> str:
        if not self._emitted_fragments:
            return ""

        parts = list(self._emitted_fragments)
        if parts and parts[0].startswith("[ACTION:"):
            action = parts.pop(0)
            natural = "".join(parts).strip()
            return f"{action} {natural}".strip() if natural else action
        return "".join(parts).strip()

    def _consume_prefix_buffer(self) -> list[str]:
        stripped = self._prefix_buffer.lstrip()
        if not stripped:
            return []

        if not stripped.startswith("["):
            return self._force_prefix_as_text()

        if "]" not in stripped:
            return []

        match = ACTION_PREFIX_PATTERN.match(self._prefix_buffer)
        if not match:
            return self._force_prefix_as_text()

        raw_action = (match.group("action") or "").lower()
        action_name = config.canonicalize_host_action(raw_action)
        if not action_name:
            return self._force_prefix_as_text()

        self._prefix_decided = True
        self._action_emitted = True
        directive = f"[ACTION:{action_name}]"
        remainder = self._prefix_buffer[match.end():]
        self._prefix_buffer = ""
        self._emitted_fragments.append(directive)
        outputs = [directive]
        outputs.extend(self._consume_text(remainder))
        return outputs

    def _force_prefix_as_text(self) -> list[str]:
        self._prefix_decided = True
        prefix_text = self._prefix_buffer
        self._prefix_buffer = ""
        return self._consume_text(prefix_text)

    def _consume_text(self, text: str) -> list[str]:
        outputs: list[str] = []
        self._text_buffer += text

        while True:
            match = SENTENCE_BOUNDARY_PATTERN.search(self._text_buffer)
            if not match:
                break

            end_index = match.end()
            chunk = sanitize_tts_text(self._text_buffer[:end_index])
            self._text_buffer = self._text_buffer[end_index:]
            self._text_buffer = self._text_buffer.lstrip()
            if not chunk:
                continue
            outputs.append(chunk)
            self._emitted_fragments.append(chunk)

        return outputs


class BrainEngine(QThread):
    """在背景執行緒中執行本地 Ollama 推論；本機大腦已完成與 OpenClaw 解耦。"""

    message_received = pyqtSignal(str)
    streamed_fragment = pyqtSignal(str, object)
    warning_emitted = pyqtSignal(str)
    profile_changed = pyqtSignal(str)

    def __init__(
        self,
        library: CharacterLibrary | None = None,
        latency_tracker: InteractionLatencyTracker | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._library = library or CharacterLibrary()
        self._soul_loader = SoulLoader()
        self._request_queue: "queue.Queue[tuple[str, BrainProfile | None, str | None] | None]" = queue.Queue()
        self._lock = threading.Lock()
        self._memory_registry: dict[str, object] = {}
        self._active_profile = BrainProfile.from_character_library(self._library)
        self._llm_cache: dict[str, object] = {}
        self._latency_tracker = latency_tracker

    def run(self):
        while True:
            queued_item = self._request_queue.get()
            if queued_item is None:
                return

            prompt_text, profile_override, trace_id = queued_item
            self._handle_prompt(prompt_text, profile_override, trace_id=trace_id)

    def stop(self):
        self._request_queue.put(None)

    def send_to_brain(
        self,
        text: str,
        profile: BrainProfile | None = None,
        trace_id: str | None = None,
    ) -> bool:
        message = (text or "").strip()
        if not message:
            return False
        if self._latency_tracker is not None:
            self._latency_tracker.mark_brain_queued(trace_id)
        self._request_queue.put((message, profile, trace_id))
        return True

    def send_message(
        self,
        message: str,
        profile: BrainProfile | None = None,
        trace_id: str | None = None,
    ) -> bool:
        return self.send_to_brain(message, profile=profile, trace_id=trace_id)

    def send_query(
        self,
        text: str,
        profile: BrainProfile | None = None,
        trace_id: str | None = None,
    ) -> bool:
        """提供 UI Dev Mode 使用的查詢入口，內部仍走同一條背景推論管線。"""
        return self.send_to_brain(text, profile=profile, trace_id=trace_id)

    def set_active_profile(self, profile: BrainProfile):
        with self._lock:
            self._active_profile = profile
        self.profile_changed.emit(profile.profile_id)

    def sync_profile_from_character(
        self,
        character_id: str | None = None,
        knowledge_base_id: str | None = None,
    ) -> BrainProfile:
        profile = BrainProfile.from_character_library(
            self._library,
            character_id=character_id,
            knowledge_base_id=knowledge_base_id,
        )
        self.set_active_profile(profile)
        return profile

    def clear_memory(self, profile_id: str | None = None):
        target_profile_id = profile_id or self._active_profile.profile_id
        self._memory_registry.pop(target_profile_id, None)

    def _handle_prompt(
        self,
        prompt_text: str,
        profile_override: BrainProfile | None = None,
        trace_id: str | None = None,
    ):
        profile = profile_override or self._get_active_profile()
        if profile_override is not None:
            self.set_active_profile(profile_override)
        if self._latency_tracker is not None:
            self._latency_tracker.mark_brain_started(trace_id)

        if LANGCHAIN_IMPORT_ERROR is not None:
            warning = (
                "警告: 尚未安裝 LangChain 相關套件，請先在虛擬環境安裝 "
                "`langchain`、`langchain-community`、`python-dotenv`。"
            )
            self.warning_emitted.emit(warning)
            if self._latency_tracker is not None:
                self._latency_tracker.mark_failure(trace_id, "brain", warning)
            self._emit_fragment(f"[ACTION:listen] {warning}", trace_id)
            if self._latency_tracker is not None:
                self._latency_tracker.mark_brain_completed(trace_id)
            return

        soul_message, soul_warning = self._soul_loader.load(profile.persona_key)
        if soul_warning:
            self.warning_emitted.emit(soul_warning)

        knowledge_context, knowledge_warning = self._load_knowledge_context(profile)
        if knowledge_warning:
            self.warning_emitted.emit(knowledge_warning)

        memory = self._get_or_create_memory(profile.profile_id)
        try:
            prompt = self._build_stream_prompt(
                profile=profile,
                user_input=prompt_text,
                system_prompt=soul_message.content,
                knowledge_context=knowledge_context,
                memory=memory,
            )
            parser = StreamedReplyParser()
            emitted_anything = False

            for token in self._stream_llm_tokens(profile, prompt):
                for fragment in parser.feed(token):
                    emitted_anything = True
                    self._emit_fragment(fragment, trace_id)

            for fragment in parser.flush():
                emitted_anything = True
                self._emit_fragment(fragment, trace_id)

            memory_reply = parser.build_memory_reply()
            if memory_reply:
                memory.save_context({"input": prompt_text}, {"response": memory_reply})
            elif not emitted_anything:
                fallback = "我剛剛有點恍神了，請再說一次。"
                memory.save_context({"input": prompt_text}, {"response": fallback})
                self._emit_fragment(fallback, trace_id)
            if self._latency_tracker is not None:
                self._latency_tracker.mark_brain_completed(trace_id)
        except Exception as exc:
            warning = f"警告: 本地 Ollama 推論失敗，已改用安全回覆。({exc})"
            self.warning_emitted.emit(warning)
            if self._latency_tracker is not None:
                self._latency_tracker.mark_failure(trace_id, "brain", warning)
            self._emit_fragment("[ACTION:listen] 抱歉，我現在無法順利連線本地大腦，請稍後再試。", trace_id)
            if self._latency_tracker is not None:
                self._latency_tracker.mark_brain_completed(trace_id)
            return

    def _emit_fragment(self, fragment: str, trace_id: str | None):
        if self._latency_tracker is not None:
            self._latency_tracker.mark_fragment_emitted(trace_id, fragment)
        self.message_received.emit(fragment)
        self.streamed_fragment.emit(fragment, trace_id)

    def _build_stream_prompt(
        self,
        profile: BrainProfile,
        user_input: str,
        system_prompt: str,
        knowledge_context: str,
        memory,
    ) -> str:
        llm = self._get_or_create_llm(profile)
        del llm
        history = memory.load_memory_variables({}).get("history", "")
        if history is None:
            history = ""
        history_text = str(history)
        prompt = PromptTemplate(
            input_variables=["history", "input", "system_prompt", "knowledge_context", "host_action_prompt"],
            template=(
                "{system_prompt}\n\n"
                "{host_action_prompt}\n\n"
                "{knowledge_context}"
                "以下是你與使用者的對話歷史：\n{history}\n\n"
                "使用者：{input}\n"
                "請直接輸出回覆內容。若需要 Host 執行動作，必須先輸出單一 [ACTION:...] 前綴，"
                "而且它必須是整段回覆的第一個有效字元；後面才能接自然語言內容。\n"
                "ECHOES："
            ),
        ).partial(
            system_prompt=system_prompt,
            knowledge_context=knowledge_context,
            host_action_prompt=config.HOST_ACTION_PROMPT,
        )
        return prompt.format(history=history_text, input=user_input)

    def _stream_llm_tokens(self, profile: BrainProfile, prompt: str):
        llm = self._get_or_create_llm(profile)
        for chunk in llm.stream(prompt):
            text = self._coerce_stream_chunk(chunk)
            if text:
                yield text

    def _get_or_create_llm(self, profile: BrainProfile):
        base_url = config.OLLAMA_BASE_URL
        cache_key = f"{base_url}|{profile.model_name}"
        cached = self._llm_cache.get(cache_key)
        if cached is not None:
            return cached

        llm = Ollama(
            base_url=base_url,
            model=profile.model_name or config.DEFAULT_OLLAMA_MODEL,
            temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0.4")),
        )
        self._llm_cache[cache_key] = llm
        return llm

    def _get_or_create_memory(self, profile_id: str):
        memory = self._memory_registry.get(profile_id)
        if memory is not None:
            return memory

        memory = ConversationBufferMemory(
            memory_key="history",
            ai_prefix="ECHOES",
            human_prefix="使用者",
        )
        self._memory_registry[profile_id] = memory
        return memory

    def _get_active_profile(self) -> BrainProfile:
        with self._lock:
            return self._active_profile

    @staticmethod
    def _load_knowledge_context(profile: BrainProfile) -> tuple[str, str | None]:
        knowledge_path = (profile.knowledge_path or "").strip()
        if not knowledge_path:
            return "", None

        path = Path(knowledge_path)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()

        if not path.is_file():
            return "", f"警告: 找不到知識庫檔案，將略過知識上下文。({path})"

        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return "", f"警告: 知識庫讀取失敗，將略過知識上下文。({exc})"

        if not content:
            return "", None

        truncated = content[:4000]
        return f"目前可用知識庫內容如下：\n{truncated}\n\n", None

    @staticmethod
    def _normalize_reply(reply: str) -> str:
        text = (reply or "").strip()
        if not text:
            return "[ACTION:listen] 我剛剛有點恍神了，請再說一次。"

        match = ACTION_DIRECTIVE_PATTERN.search(text)
        raw_action_name = ""
        if match:
            raw_action_name = (match.group("bracket") or match.group("bare") or "").lower()
        action_name = config.canonicalize_host_action(raw_action_name)
        natural_text = sanitize_tts_text(text)
        if not action_name:
            if raw_action_name:
                return natural_text or "[ACTION:listen] 我有聽見你，繼續說吧。"
            return natural_text or "[ACTION:listen] 我有聽見你，繼續說吧。"
        if not natural_text:
            return f"[ACTION:{action_name}]"
        return f"[ACTION:{action_name}] {natural_text}"

    @staticmethod
    def _coerce_stream_chunk(chunk: object) -> str:
        if chunk is None:
            return ""
        if isinstance(chunk, str):
            return chunk
        content = getattr(chunk, "content", None)
        if content is not None:
            return str(content)
        return str(chunk)


def sanitize_tts_text(text: str) -> str:
    """移除控制標記，保留可朗讀文字。"""

    stripped = ACTION_DIRECTIVE_PATTERN.sub("", text or "")
    stripped = re.sub(r"\s{2,}", " ", stripped).strip()
    return stripped


def build_active_profile_snapshot(character_id: str | None = None) -> dict[str, str]:
    """供 debug probe / smoke test 使用的可序列化 profile 快照。"""

    library = CharacterLibrary()
    profile = BrainProfile.from_character_library(library, character_id=character_id)
    return {
        "profile_id": profile.profile_id,
        "character_id": profile.character_id or "",
        "persona_key": profile.persona_key,
        "knowledge_base_id": profile.knowledge_base_id,
        "model_name": profile.model_name,
        "knowledge_path": profile.knowledge_path,
        "voice_id": profile.voice_id,
        "temp_audio_dir": profile.temp_audio_dir,
    }
def _build_profile_id(character_id: str | None, knowledge_base_id: str | None) -> str:
    return f"{character_id or 'default'}::{knowledge_base_id or 'default'}"


def _resolve_character_dir(character_id: str | None, manifest: dict | None) -> Path:
    if manifest:
        motions_dir = str(manifest.get("motions_dir") or "").strip()
        if motions_dir:
            candidate = (PROJECT_ROOT / motions_dir).resolve().parent
            if candidate.exists():
                return candidate
    if character_id:
        return CHARACTER_LIBRARY_DIR / character_id
    return PROJECT_ROOT


def _resolve_manifest_or_default_path(manifest: dict | None, key: str, default_path: Path) -> str:
    configured = str((manifest or {}).get(key) or "").strip()
    if not configured:
        return str(default_path) if default_path.is_file() else ""

    path = Path(configured)
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())
