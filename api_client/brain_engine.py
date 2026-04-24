"""
ECHOES — Host 端本地大腦串流推論 worker。

開發提醒：
- 請先進入專案虛擬環境再執行，並安裝 `langchain`、`langchain-openai`、`python-dotenv`。
- 本模組刻意把 OpenAI 推論放在 `QThread` 中，避免阻塞 PyQt UI。
"""

from __future__ import annotations

import os
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtCore import QThread, pyqtSignal

import config
from character_library import CHARACTER_LIBRARY_DIR, CharacterLibrary, PROJECT_ROOT
from interaction_trace import InteractionLatencyTracker

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
    LANGCHAIN_IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover - 允許在依賴缺失時安全降級
    AIMessage = None  # type: ignore[assignment]
    HumanMessage = None  # type: ignore[assignment]
    SystemMessage = None  # type: ignore[assignment]
    ChatOpenAI = None  # type: ignore[assignment]
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
    model_name: str = config.OPENAI_MODEL
    knowledge_path: str = ""
    voice_id: str = ""

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
            str((manifest or {}).get("openai_model") or "").strip()
            or str((manifest or {}).get("model_name") or "").strip()
            or str((manifest or {}).get("ollama_model") or "").strip()
            or config.OPENAI_MODEL
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


SENTENCE_BOUNDARY_PATTERN = re.compile(r"[，。！？,!?\n]")
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


class _ConversationTurnMemory:
    """以 message history 保存最近幾輪對話，避免使用已棄用的 classic memory。"""

    def __init__(self, max_turns: int = 6):
        self._max_turns = max(1, int(max_turns))
        self._messages: list[object] = []

    def load_messages(self) -> list[object]:
        return list(self._messages)

    def append_exchange(self, user_input: str, assistant_reply: str):
        human_text = str(user_input or "").strip()
        ai_text = str(assistant_reply or "").strip()
        if not human_text or not ai_text:
            return

        self._messages.append(_build_human_message(human_text))
        self._messages.append(_build_ai_message(ai_text))
        overflow = len(self._messages) - (self._max_turns * 2)
        if overflow > 0:
            self._messages = self._messages[overflow:]

    def clear(self):
        self._messages = []


class BrainEngine(QThread):
    """在背景執行緒中執行 OpenAI 串流推論；本機大腦已完成與 OpenClaw 解耦。"""

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
        self._prewarm_active_profile()
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
                "`langchain`、`langchain-openai`、`python-dotenv`。"
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
                self._remember_exchange(memory, prompt_text, memory_reply)
            elif not emitted_anything:
                fallback = "我剛剛有點恍神了，請再說一次。"
                self._remember_exchange(memory, prompt_text, fallback)
                self._emit_fragment(fallback, trace_id)
            if self._latency_tracker is not None:
                self._latency_tracker.mark_brain_completed(trace_id)
        except Exception as exc:
            warning = f"警告: OpenAI 推論失敗，已改用安全回覆。({exc})"
            self.warning_emitted.emit(warning)
            if self._latency_tracker is not None:
                self._latency_tracker.mark_failure(trace_id, "brain", warning)
            self._emit_fragment("[ACTION:listen] 抱歉，我現在無法順利連線 OpenAI 大腦，請稍後再試。", trace_id)
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
    ):
        del profile
        messages = [_build_system_message(system_prompt), _build_system_message(config.HOST_ACTION_PROMPT)]
        if knowledge_context:
            messages.append(_build_system_message(knowledge_context.strip()))
        messages.extend(self._load_memory_messages(memory))
        messages.append(_build_human_message(user_input))
        return messages

    def _stream_llm_tokens(self, profile: BrainProfile, prompt: str):
        llm = self._get_or_create_llm(profile)
        for chunk in llm.stream(prompt):
            text = self._coerce_stream_chunk(chunk)
            if text:
                yield text

    def _get_or_create_llm(self, profile: BrainProfile):
        if not config.OPENAI_API_KEY:
            raise RuntimeError("缺少 OPENAI_API_KEY")

        model_name = profile.model_name or config.DEFAULT_OPENAI_MODEL
        cache_key = f"{model_name}|{os.getenv('OPENAI_TEMPERATURE', '0.4')}"
        cached = self._llm_cache.get(cache_key)
        if cached is not None:
            return cached

        llm = ChatOpenAI(
            api_key=config.OPENAI_API_KEY,
            model=model_name,
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.4")),
            streaming=True,
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
            timeout=(5, 45),
        )
        self._llm_cache[cache_key] = llm
        return llm

    def _get_or_create_memory(self, profile_id: str):
        memory = self._memory_registry.get(profile_id)
        if memory is not None:
            return memory

        memory = _ConversationTurnMemory(
            max_turns=max(1, int(os.getenv("BRAIN_MEMORY_MAX_TURNS", "6"))),
        )
        self._memory_registry[profile_id] = memory
        return memory

    def _prewarm_active_profile(self):
        try:
            profile = self._get_active_profile()
            self._get_or_create_memory(profile.profile_id)
            if config.OPENAI_API_KEY:
                self._get_or_create_llm(profile)
        except Exception:
            return

    @staticmethod
    def _load_memory_messages(memory) -> list[object]:
        load_messages = getattr(memory, "load_messages", None)
        if callable(load_messages):
            try:
                return list(load_messages())
            except Exception:
                return []

        load_memory_variables = getattr(memory, "load_memory_variables", None)
        if callable(load_memory_variables):
            history = load_memory_variables({}).get("history", "")
            history_text = str(history or "").strip()
            if history_text:
                return [_build_system_message(f"以下是你與使用者的對話歷史摘要：\n{history_text}")]
        return []

    @staticmethod
    def _remember_exchange(memory, user_input: str, assistant_reply: str):
        append_exchange = getattr(memory, "append_exchange", None)
        if callable(append_exchange):
            append_exchange(user_input, assistant_reply)
            return

        save_context = getattr(memory, "save_context", None)
        if callable(save_context):
            save_context({"input": user_input}, {"response": assistant_reply})

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


def _build_system_message(content: str) -> object:
    text = str(content or "").strip()
    if SystemMessage is None:
        return SimpleNamespace(role="system", content=text)
    return SystemMessage(content=text)


def _build_human_message(content: str) -> object:
    text = str(content or "").strip()
    if HumanMessage is None:
        return SimpleNamespace(role="user", content=text)
    return HumanMessage(content=text)


def _build_ai_message(content: str) -> object:
    text = str(content or "").strip()
    if AIMessage is None:
        return SimpleNamespace(role="assistant", content=text)
    return AIMessage(content=text)


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
