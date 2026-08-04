"""4.3.1 — transcribing 狀態納入 set_stt_state/_get_stt_control_descriptor 白名單，
按鈕於 transcribing 期間為 no-op；unavailable 提示文案改為中性 STT 文案。

沿用既有慣例（見 test_developer_input_provider_and_tts.py）：以 unbound method
搭配假 self（MagicMock）驗證，不需建構真正的 QWebEngineView。
"""

from unittest.mock import MagicMock

from ui.transparent_window import TransparentWindow


def _make_fake_window(stt_available=True, stt_state="idle"):
    fake = MagicMock()
    fake._stt_available = stt_available
    fake._stt_state = stt_state
    return fake


def test_transcribing_state_accepted_by_set_stt_state():
    fake = _make_fake_window()
    TransparentWindow.set_stt_state(fake, "transcribing")

    assert fake._stt_state == "transcribing"
    assert fake._stt_listening is False
    assert fake._stt_available is True
    fake._apply_stt_button_state.assert_called_once()


def test_unknown_state_still_falls_back_to_idle():
    fake = _make_fake_window()
    TransparentWindow.set_stt_state(fake, "bogus")

    assert fake._stt_state == "idle"


def test_transcribing_descriptor_is_disabled_with_label():
    fake = _make_fake_window(stt_state="transcribing")
    descriptor = TransparentWindow._get_stt_control_descriptor(fake)

    assert descriptor["state"] == "transcribing"
    assert descriptor["enabled"] is False
    assert descriptor["label"]


def test_button_click_is_noop_while_transcribing():
    fake = _make_fake_window(stt_state="transcribing")
    TransparentWindow._handle_stt_button_clicked(fake)

    fake.stt_start_requested.emit.assert_not_called()
    fake.stt_stop_requested.emit.assert_not_called()
    fake.set_action_status.assert_not_called()


def test_button_click_when_unavailable_shows_neutral_message():
    fake = _make_fake_window(stt_available=False, stt_state="idle")
    TransparentWindow._handle_stt_button_clicked(fake)

    fake.set_action_status.assert_called_once()
    args, _kwargs = fake.set_action_status.call_args
    assert args[0] == "語音輸入尚未就緒。"
    fake.stt_start_requested.emit.assert_not_called()


def test_button_click_when_idle_starts_recording():
    fake = _make_fake_window(stt_available=True, stt_state="idle")
    TransparentWindow._handle_stt_button_clicked(fake)

    fake.stt_start_requested.emit.assert_called_once()


def test_button_click_when_listening_stops_recording():
    fake = _make_fake_window(stt_available=True, stt_state="listening")
    TransparentWindow._handle_stt_button_clicked(fake)

    fake.stt_stop_requested.emit.assert_called_once()


def test_loading_is_distinct_from_unavailable_and_still_not_clickable():
    """模型預載期間不該顯示成「不可用」——那讀起來像永久壞掉，
    而不是「等一下就好」。但按鈕仍不能按。"""
    fake = _make_fake_window()
    TransparentWindow.set_stt_state(fake, "loading")

    assert fake._stt_state == "loading"
    assert fake._stt_available is False
    assert fake._stt_listening is False


def test_loading_descriptor_reports_loading_rather_than_unavailable():
    fake = _make_fake_window(stt_available=False, stt_state="loading")
    descriptor = TransparentWindow._get_stt_control_descriptor(fake)

    assert descriptor["state"] == "loading"
    assert descriptor["enabled"] is False
    assert "不可用" not in descriptor["label"]


def test_preload_failure_still_reports_unavailable():
    fake = _make_fake_window(stt_available=False, stt_state="unavailable")
    descriptor = TransparentWindow._get_stt_control_descriptor(fake)

    assert descriptor["state"] == "unavailable"
    assert descriptor["enabled"] is False


def test_model_becoming_ready_after_loading_returns_to_idle():
    fake = _make_fake_window(stt_available=False, stt_state="loading")
    TransparentWindow.set_stt_available(fake, True)

    assert fake._stt_state == "idle"
    assert fake._stt_available is True
