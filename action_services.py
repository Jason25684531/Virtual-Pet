"""
ECHOES — Host action services
在背景執行緒中執行新聞抓取與本地音樂挑選，避免阻塞 PyQt UI。
"""

from __future__ import annotations

import random
from pathlib import Path
from xml.etree import ElementTree

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from character_library import UI_MUSIC_DIR


DEFAULT_NEWS_FEED_URL = "https://feeds.bbci.co.uk/news/world/rss.xml"
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}


class NewsFetchWorker(QThread):
    """在背景執行緒抓取 RSS 標題。"""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(self, feed_url: str = DEFAULT_NEWS_FEED_URL, parent=None):
        super().__init__(parent)
        self._feed_url = feed_url

    def run(self):
        try:
            response = requests.get(self._feed_url, timeout=8)
            response.raise_for_status()
            headline = self._extract_headline(response.text)
            if not headline:
                self.finished_signal.emit(False, "新聞來源沒有可用標題。", None)
                return

            payload = {"headline": headline, "feed_url": self._feed_url}
            self.finished_signal.emit(True, headline, payload)
        except (requests.RequestException, ElementTree.ParseError) as exc:
            self.finished_signal.emit(False, f"新聞抓取失敗: {exc}", None)

    @staticmethod
    def _extract_headline(xml_text: str) -> str | None:
        root = ElementTree.fromstring(xml_text)

        channel = root.find("channel")
        if channel is not None:
            first_item = channel.find("item")
            if first_item is not None:
                title_node = first_item.find("title")
                if title_node is not None and title_node.text:
                    return title_node.text.strip()

        items = root.findall(".//item")
        for item in items:
            title_node = item.find("title")
            if title_node is not None and title_node.text:
                return title_node.text.strip()
        return None


class MusicSelectionWorker(QThread):
    """在背景執行緒掃描本地音樂並挑選可播放檔案。"""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(self, music_dir: str | Path | None = None, parent=None):
        super().__init__(parent)
        self._music_dir = Path(music_dir) if music_dir else UI_MUSIC_DIR

    def run(self):
        try:
            if not self._music_dir.is_dir():
                self.finished_signal.emit(False, f"找不到音樂資料夾: {self._music_dir}", None)
                return

            tracks = [
                path for path in self._music_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
            ]
            if not tracks:
                self.finished_signal.emit(False, f"音樂資料夾沒有可播放檔案: {self._music_dir}", None)
                return

            track = random.choice(tracks)
            payload = {"path": str(track), "title": track.stem}
            self.finished_signal.emit(True, track.stem, payload)
        except Exception as exc:
            self.finished_signal.emit(False, f"音樂挑選失敗: {exc}", None)