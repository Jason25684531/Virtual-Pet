"""單一語音供應商契約：一次合成請求(TtsRequest)、供應商描述(TtsProvider)與
依角色/偏好組出備援鏈(build_tts_provider_chain)的唯一決定點。

`StreamingTTSWorker` 描述所有語音 worker(ElevenLabsStreamingTTSWorker、
VoAIStreamingTTSWorker、AdaptiveTTSFallbackWorker)共用的訊號與生命週期方法；
它刻意不是 `@runtime_checkable` 的 Protocol——訊號是類別層級的資料屬性
(`pyqtSignal` 實例),而 typing.Protocol 對「含非方法成員的 protocol」不支援
`issubclass()`(只支援 `isinstance()`,且需要真正建構實例)。三個 worker 也是
QThread/QObject,PyQt5 的 sip metaclass 與 `ABCMeta`/`Protocol` 的 metaclass
衝突,無法像 `Reranker`/`LLMProviderAdapter` 那樣直接繼承。故其契約一致性改由
`tests/test_dependency_boundaries.py` 以 `hasattr` 檢查類別層級屬性是否齊備,
而非 issubclass——這是 Qt 訊號這一類契約在本專案唯一可行的自動化驗證方式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class TtsRequest:
    """一次語音合成所需的全部輸入,已解析完成,供應商各自取用需要的欄位。

    `character_id` 與 `voice_id` 刻意分開:前者是角色身分(VoAI 用它查自己的
    voice config),後者是已解析好的 ElevenLabs 聲線 id——舊code讓兩者共用同一個
    `voice_id` 參數名,是 `factory.__name__` 分支存在的根因。
    """

    text: str
    reply_id: str
    trace_id: str
    character_id: str
    voice_id: str
    model_id: str
    preferred_provider: str = ""
    resolved_tts_mode: str = ""
    pcm_stream_sink: Any = None
    playback_guard: Callable[[str, str | None], bool] | None = None


class StreamingTTSWorker(Protocol):
    """語音 worker 的建構與訊號契約。

    建構慣例(非機器強制,見上方模組說明):
    `__init__(self, request: TtsRequest, parent=None, **provider_specific_kwargs)`。
    `provider_specific_kwargs` 是各供應商自己的測試注入點(如 `requests_post`),
    不屬於跨供應商契約,因此不在這裡宣告。
    """

    finished_signal: Any  # pyqtSignal(bool, str, object)
    progress_signal: Any  # pyqtSignal(str, object)
    audio_ready_signal: Any  # pyqtSignal(object, str, str)
    finished: Any  # pyqtSignal()

    def start(self) -> None: ...

    def isRunning(self) -> bool: ...  # noqa: N802 - 與 QThread 介面相容

    def quit(self) -> None: ...

    def wait(self, timeout_ms: int = 5000) -> bool: ...


# StreamingTTSWorker 的類別層級屬性:訊號 + 生命週期方法,供 guard test 逐一 hasattr 檢查。
STREAMING_TTS_WORKER_MEMBERS: tuple[str, ...] = (
    "finished_signal",
    "progress_signal",
    "audio_ready_signal",
    "finished",
    "start",
    "isRunning",
    "quit",
    "wait",
)


@dataclass(frozen=True)
class TtsProvider:
    """一個語音供應商在備援鏈中的描述。

    `should_advance_on_failure`/`fallback_reason` 讓「失敗後是否換下一個供應商」
    與「換供應商的原因怎麼取」這兩個供應商各自的業務規則,以資料的形式跟著供應商
    描述走,編排層(AdaptiveTTSFallbackWorker)因此不需要知道任何供應商的名字
    就能做決策——只是依序問「這個描述,你失敗了要不要換下一個」。
    """

    name: str
    factory: Callable[..., StreamingTTSWorker]
    should_advance_on_failure: Callable[[dict], bool] = lambda payload: True
    fallback_reason: Callable[[str, dict], str] = lambda message, payload: str(message or "provider_failed")
    needs_fallback_timeout_grace: bool = False


def _normalize_provider_name(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"voai", "elevenlabs"} else ""


def build_tts_provider_chain(
    character_id: str | None,
    preferred_provider: str | None = None,
) -> tuple[TtsProvider, ...]:
    """依角色與(選填的)明確偏好,決定這次合成要依序嘗試哪些供應商。

    未明確指定偏好時,有專屬 ElevenLabs 聲線的角色以 ElevenLabs 為首選,
    否則維持 VoAI 優先——與改寫前 AdaptiveTTSFallbackWorker.__init__ 的規則相同。
    """
    import config
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker
    from api_client.voai_client import VoAIStreamingTTSWorker

    normalized_character_id = str(character_id or "").strip()
    normalized = _normalize_provider_name(preferred_provider) or (
        "elevenlabs"
        if normalized_character_id in config.BUILTIN_CHARACTER_ELEVENLABS_VOICE_IDS
        else "voai"
    )

    voai = TtsProvider(
        name="voai",
        factory=VoAIStreamingTTSWorker,
        # VoAI 只在明確可辨識的失敗(fast_fail:缺 key、4xx/5xx、連線層錯誤)時換下一個
        # 供應商;串流中途的非定性錯誤視為終局失敗,不嘗試 ElevenLabs——與改寫前一致。
        should_advance_on_failure=lambda payload: bool(payload.get("fast_fail")),
        fallback_reason=lambda message, payload: str(payload.get("fast_fail", "unknown")),
    )
    elevenlabs = TtsProvider(
        name="elevenlabs",
        factory=ElevenLabsStreamingTTSWorker,
        # ElevenLabs 失敗一律換下一個供應商(無 fast_fail 門檻),與改寫前一致。
        fallback_reason=lambda message, payload: str(message or "elevenlabs_failed"),
        # 作為備援(非首選)進場時起播延遲較高,tts_playback 據此延長逾時寬限。
        needs_fallback_timeout_grace=True,
    )
    return (elevenlabs, voai) if normalized == "elevenlabs" else (voai, elevenlabs)
