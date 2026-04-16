"""
ECHOES — 與 OpenClaw 大腦溝通的 WebSocket client。
以背景 QThread 維持腦端連線，避免阻塞 PyQt UI。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import locale
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

from PyQt5.QtCore import QThread, pyqtSignal

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ModuleNotFoundError:
    serialization = None
    Ed25519PrivateKey = None

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException
except ModuleNotFoundError:
    websockets = None
    ConnectionClosed = WebSocketException = Exception


DEFAULT_VM_WS_URL = "ws://127.0.0.1:18789"
DEFAULT_SESSION_KEY = "main"
CONNECT_PROTOCOL_VERSION = 3
CONNECT_ROLE = "operator"
CONNECT_SCOPES = ("operator.read", "operator.write")
CLIENT_ID = "node-host"
CLIENT_MODE = "node"
CLIENT_CAPS = ("tool-events",)
DEVICE_KEY_FILENAME = "device.key"
DEVICE_CLIENT_LABEL = "ECHOES-Host"
DEFAULT_WSL_USER = "comfyuilinux"
DEFAULT_OPENCLAW_DISTRO = "Ubuntu-22.04"
OPENCLAW_TOKEN_ENV = "OPENCLAW_ACCESS_TOKEN"
MESSAGE_TEXT_KEYS = ("text", "message", "content", "delta", "reply", "response")
MESSAGE_ACTION_KEYS = ("action", "action_name", "directive")
IGNORED_GATEWAY_EVENTS = {"connect.challenge", "tick", "sessions.changed", "system-presence"}
VISIBLE_MESSAGE_ROLES = {"assistant", "system", "tool", "custom_message"}
SUPPORTED_ACTION_NAMES = {
    "idle",
    "laugh",
    "angry",
    "awkward",
    "speechless",
    "listen",
    "report_news",
    "play_music",
}
HOST_UI_PROTOCOL_PREFIX = (
    "你正在回覆 ECHOES Windows Host UI。"
    "禁止自行呼叫任何搜尋、新聞、天氣、音樂或其他外部工具。"
    "你只需要依照 AGENTS.md 選擇最合適的一個具體 [ACTION:...] 白名單標籤，"
    "回覆一句簡短自然語言，並把標籤放在最後。"
    "如果是新聞、頭條、天氣需求，請交由 Host 的 report_news handler 執行。"
    "如果是音樂、放鬆、播歌需求，請交由 Host 的 play_music handler 執行。"
    "絕對不要輸出 [ACTION:*]。"
    "\n使用者訊息："
)


class VMConnector(QThread):
    """在背景執行緒中維持與 OpenClaw 大腦的 WebSocket 連線。"""

    DEVICE_KEY_PATH = Path(__file__).resolve().parents[1] / DEVICE_KEY_FILENAME

    message_received = pyqtSignal(str)

    def __init__(
        self,
        url: str = DEFAULT_VM_WS_URL,
        reconnect_delay: float = 3.0,
        session_key: str = DEFAULT_SESSION_KEY,
        access_token: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._url = url
        self._reconnect_delay = reconnect_delay
        self._session_key = session_key
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._outgoing_queue: asyncio.Queue[str | None] | None = None
        self._pending_messages: deque[str] = deque()
        self._pending_lock = threading.Lock()
        self._websocket = None
        self._last_notice_key: str | None = None
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._connect_task: asyncio.Task | None = None
        self._gateway_ready = False
        self._session_subscribed = False
        self._client_instance_id = f"echoes-host-{uuid4()}"
        self._device_key_path = self.DEVICE_KEY_PATH
        self._device_private_key: Ed25519PrivateKey | None = None
        self._device_identity_hex: str | None = None
        self._device_public_key_bytes: bytes | None = None
        self._device_public_key_b64: str | None = None
        self._device_id: str | None = None
        self._access_token: str | None = access_token.strip() if isinstance(access_token, str) and access_token.strip() else None

    def run(self):
        if websockets is None:
            warning = "警告: 尚未安裝 websockets，無法連線至 OpenClaw 大腦。"
            print(f"[ECHOES] {warning}")
            self._emit_notice("dependency-missing", warning)
            return

        if Ed25519PrivateKey is None or serialization is None:
            warning = "警告: 尚未安裝 cryptography，無法完成 OpenClaw 裝置驗證。"
            print(f"[ECHOES] {warning}")
            self._emit_notice("dependency-missing", warning)
            return

        try:
            self._load_or_generate_key()
        except Exception as exc:
            warning = "警告: 無法初始化 OpenClaw 裝置金鑰，請檢查 device.key 權限。"
            print(f"[ECHOES] {warning} {exc}")
            self._emit_notice("device-key-failed", warning)
            return

        self._access_token = self._access_token or self._load_access_token()
        if self._access_token:
            print("[ECHOES] 已載入 OpenClaw access token。")
        else:
            print("[ECHOES] 警告: 未找到 OpenClaw access token，將先嘗試僅以裝置身份驗證連線。")

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()
        self._outgoing_queue = asyncio.Queue()
        self._gateway_ready = False
        self._session_subscribed = False

        try:
            self._loop.run_until_complete(self._connection_loop())
        finally:
            self._reject_pending_requests("VMConnector 已停止。")
            asyncio.set_event_loop(None)
            self._websocket = None
            self._outgoing_queue = None
            self._stop_event = None
            self._loop.close()
            self._loop = None

    def stop(self):
        if not self._loop:
            return

        if self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)

        if self._outgoing_queue is not None:
            self._loop.call_soon_threadsafe(self._outgoing_queue.put_nowait, None)

        if self._websocket is not None:
            future = asyncio.run_coroutine_threadsafe(self._websocket.close(), self._loop)
            with contextlib.suppress(Exception):
                future.result(timeout=2)

    def send_to_brain(self, text: str) -> bool:
        message = self._build_host_chat_prompt(text)
        if websockets is None or not message:
            return False

        if self._loop and self.isRunning():
            if self._gateway_ready:
                future = asyncio.run_coroutine_threadsafe(self._request_chat_send(message), self._loop)
                future.add_done_callback(self._consume_background_future)
            else:
                with self._pending_lock:
                    self._pending_messages.append(message)
            return True

        with self._pending_lock:
            self._pending_messages.append(message)
        return True

    def send_message(self, message: str) -> bool:
        return self.send_to_brain(message)

    @staticmethod
    def _build_host_chat_prompt(text: str) -> str:
        message = (text or "").strip()
        if not message:
            return ""

        if message.startswith(("請嚴格遵守 AGENTS.md", "Strictly follow AGENTS.md")):
            return message

        if "[ACTION:" in message:
            return message

        return f"{HOST_UI_PROTOCOL_PREFIX}{message}"

    async def _connection_loop(self):
        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                print(f"[ECHOES] OpenClaw WebSocket 連線中: {self._url}")
                async with websockets.connect(
                    self._url,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=2,
                    max_size=2_000_000,
                ) as websocket:
                    self._websocket = websocket
                    self._gateway_ready = False
                    self._session_subscribed = False
                    self._last_notice_key = None
                    await self._bridge_connection(websocket)
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                warning = f"警告: 無法連線至 OpenClaw 大腦，{self._reconnect_delay:.0f} 秒後自動重試。"
                print(f"[ECHOES] {warning} {exc}")
                self._emit_notice("connect-failed", warning)
            except WebSocketException as exc:
                warning = f"警告: 與 OpenClaw 大腦的連線已中斷，{self._reconnect_delay:.0f} 秒後自動重試。"
                print(f"[ECHOES] {warning} {exc}")
                self._emit_notice("connect-failed", warning)
            except Exception as exc:
                warning = "警告: OpenClaw 連線流程發生未預期錯誤，稍後自動重試。"
                print(f"[ECHOES] {warning} {exc}")
                self._emit_notice("connect-failed", warning)
            finally:
                self._gateway_ready = False
                self._session_subscribed = False
                self._websocket = None
                if self._connect_task is not None:
                    self._connect_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._connect_task
                    self._connect_task = None
                self._reject_pending_requests("OpenClaw WebSocket 連線已中斷。")

            if self._stop_event is not None and not self._stop_event.is_set():
                await asyncio.sleep(self._reconnect_delay)

    async def _bridge_connection(self, websocket):
        receive_task = asyncio.create_task(self._receive_messages(websocket))
        send_task = asyncio.create_task(self._send_messages(websocket))

        done, pending = await asyncio.wait(
            {receive_task, send_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        for task in done:
            exception = task.exception()
            if exception and not isinstance(exception, ConnectionClosed):
                raise exception

    async def _receive_messages(self, websocket):
        async for message in websocket:
            raw_text = self._coerce_message_text(message)
            if not raw_text:
                continue

            frame = self._parse_transport_frame(raw_text)
            if frame is not None:
                if frame.get("type") == "event":
                    await self._handle_event_frame(frame, websocket)
                elif frame.get("type") == "res":
                    self._handle_response_frame(frame)
                continue

            text = self._normalize_incoming_message(raw_text)
            if text:
                self.message_received.emit(text)

    async def _send_messages(self, websocket):
        while self._stop_event is not None and not self._stop_event.is_set():
            if self._outgoing_queue is None:
                return

            message = await self._outgoing_queue.get()
            if message is None:
                return

            await websocket.send(message)

    async def _handle_event_frame(self, frame: dict, websocket):
        event_name = str(frame.get("event") or "").strip()
        payload = frame.get("payload")
        if not event_name:
            return

        if event_name == "connect.challenge":
            nonce = payload.get("nonce") if isinstance(payload, dict) else None
            if not isinstance(nonce, str) or not nonce.strip():
                warning = "警告: OpenClaw challenge 缺少 nonce，連線將重新建立。"
                print(f"[ECHOES] {warning}")
                self._emit_notice("connect-failed", warning)
                await websocket.close(code=1008, reason="connect challenge missing nonce")
                return

            if self._connect_task is None or self._connect_task.done():
                self._connect_task = asyncio.create_task(self._complete_connect_handshake(websocket, nonce.strip()))
            return

        if event_name in IGNORED_GATEWAY_EVENTS:
            return

        text = self._normalize_event_payload(event_name, payload)
        if text:
            self.message_received.emit(text)

    def _handle_response_frame(self, frame: dict):
        request_id = str(frame.get("id") or "").strip()
        if not request_id:
            return

        pending = self._pending_requests.pop(request_id, None)
        if pending is None or pending.done():
            return

        if frame.get("ok"):
            pending.set_result(frame.get("payload"))
            return

        error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
        message = str(error.get("message") or "OpenClaw request failed.").strip()
        code = str(error.get("code") or "UNKNOWN").strip()
        pending.set_exception(RuntimeError(f"{code}: {message}"))

    async def _complete_connect_handshake(self, websocket, nonce: str):
        try:
            await self._send_connect_request(nonce=nonce, timeout=8)
            self._emit_notice("auth-passed", "已通過大腦身份驗證，準備連線。")
            self._gateway_ready = True
            print("[ECHOES] 已連線至 OpenClaw 大腦。")
            self._emit_notice("connected", "已連線至 OpenClaw 大腦。")
            await self._subscribe_session_messages()
            self._flush_pending_messages()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._gateway_ready = False
            warning = f"警告: OpenClaw 握手失敗，{self._reconnect_delay:.0f} 秒後自動重試。"
            print(f"[ECHOES] {warning} {exc}")
            self._emit_notice("connect-failed", warning)
            await websocket.close(code=1008, reason="connect failed")

    async def _subscribe_session_messages(self):
        if self._session_subscribed:
            return

        try:
            await self._send_request(
                "sessions.messages.subscribe",
                {"key": self._session_key},
                timeout=5,
            )
            self._session_subscribed = True
        except Exception as exc:
            warning = f"警告: 無法訂閱 OpenClaw session {self._session_key}，改用直接聊天事件。"
            print(f"[ECHOES] {warning} {exc}")
            self._emit_notice("session-subscribe-failed", warning)

    async def _request_chat_send(self, message: str):
        if not self._gateway_ready:
            with self._pending_lock:
                self._pending_messages.appendleft(message)
            return

        try:
            await self._send_request(
                "chat.send",
                {
                    "sessionKey": self._session_key,
                    "message": message,
                    "idempotencyKey": str(uuid4()),
                },
                timeout=12,
            )
        except Exception as exc:
            warning = "警告: 傳送訊息到 OpenClaw 大腦失敗。"
            print(f"[ECHOES] {warning} {exc}")
            self._emit_notice("chat-send-failed", warning)

    async def _send_connect_request(self, nonce: str, timeout: float = 8.0):
        return await self._send_request("connect", self._build_connect_params(nonce), timeout=timeout)

    async def _send_request(self, method: str, params: dict, timeout: float = 10.0):
        if self._outgoing_queue is None or self._loop is None:
            raise RuntimeError("OpenClaw 連線尚未初始化。")

        request_id = str(uuid4())
        future = self._loop.create_future()
        self._pending_requests[request_id] = future
        frame = json.dumps(
            {
                "type": "req",
                "id": request_id,
                "method": method,
                "params": params,
            },
            ensure_ascii=False,
        )
        await self._outgoing_queue.put(frame)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_requests.pop(request_id, None)

    def _build_connect_params(self, nonce: str) -> dict:
        locale_name = locale.getlocale()[0] or locale.getdefaultlocale()[0] or "zh-TW"
        self._load_or_generate_key()
        params = {
            "minProtocol": CONNECT_PROTOCOL_VERSION,
            "maxProtocol": CONNECT_PROTOCOL_VERSION,
            "client": {
                "id": CLIENT_ID,
                "version": "echoes-host",
                "platform": sys.platform,
                "mode": CLIENT_MODE,
                "instanceId": self._client_instance_id,
            },
            "role": CONNECT_ROLE,
            "scopes": list(CONNECT_SCOPES),
            "caps": list(CLIENT_CAPS),
            "userAgent": "ECHOES-Host/PyQt5",
            "locale": locale_name,
        }

        auth_payload = self._build_connect_auth_section()
        if auth_payload:
            params["auth"] = auth_payload

        device_payload = self._build_connect_device_payload(nonce, self._access_token)
        if device_payload:
            params["device"] = device_payload
        return params

    def _load_access_token(self) -> str | None:
        env_token = os.environ.get(OPENCLAW_TOKEN_ENV, "").strip()
        if env_token:
            return env_token

        file_token = self._load_access_token_from_wsl_share()
        if file_token:
            return file_token

        return self._load_access_token_from_wsl_command()

    def _load_access_token_from_wsl_share(self) -> str | None:
        distro_names = self._list_wsl_distros()
        if DEFAULT_OPENCLAW_DISTRO not in distro_names:
            distro_names.insert(0, DEFAULT_OPENCLAW_DISTRO)

        for distro_name in distro_names:
            config_path = Path(
                f"\\\\wsl$\\{distro_name}\\home\\{DEFAULT_WSL_USER}\\.openclaw\\openclaw.json"
            )
            if not config_path.is_file():
                continue

            with contextlib.suppress(OSError, json.JSONDecodeError, TypeError, ValueError):
                config = json.loads(config_path.read_text(encoding="utf-8"))
                token = self._extract_gateway_token(config)
                if token:
                    return token
        return None

    def _load_access_token_from_wsl_command(self) -> str | None:
        command = [
            "wsl",
            "-u",
            DEFAULT_WSL_USER,
            "--",
            "bash",
            "-lc",
            "cat ~/.openclaw/openclaw.json",
        ]
        with contextlib.suppress(OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError):
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=5,
                check=True,
            )
            output = self._decode_wsl_output(result.stdout).lstrip("\ufeff").strip()
            if not output:
                return None
            config = json.loads(output)
            return self._extract_gateway_token(config)
        return None

    @staticmethod
    def _extract_gateway_token(config: object) -> str | None:
        if not isinstance(config, dict):
            return None

        gateway = config.get("gateway")
        if not isinstance(gateway, dict):
            return None

        auth = gateway.get("auth")
        if not isinstance(auth, dict):
            return None

        token = auth.get("token")
        if isinstance(token, str):
            normalized = token.strip()
            if normalized:
                return normalized
        return None

    @staticmethod
    def _list_wsl_distros() -> list[str]:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            result = subprocess.run(
                ["wsl", "-l", "-q"],
                capture_output=True,
                timeout=5,
                check=True,
            )
            output = VMConnector._decode_wsl_output(result.stdout).replace("\x00", "")
            return [line.strip() for line in output.splitlines() if line.strip()]
        return []

    @staticmethod
    def _decode_wsl_output(raw_output: bytes | str) -> str:
        if isinstance(raw_output, str):
            return raw_output

        for encoding in ("utf-8-sig", "utf-16-le", "utf-16", "cp950"):
            with contextlib.suppress(UnicodeDecodeError):
                return raw_output.decode(encoding)
        return raw_output.decode("utf-8", errors="ignore")

    def _load_or_generate_key(self) -> str:
        if self._device_private_key is not None and self._device_identity_hex:
            return self._device_identity_hex

        key_path = Path(self._device_key_path)
        if key_path.exists():
            try:
                private_key = self._deserialize_private_key(key_path.read_bytes())
            except Exception as exc:
                print(f"[ECHOES] 警告: 既有 device.key 無法讀取，將重新建立裝置身份。 {exc}")
                with contextlib.suppress(OSError):
                    key_path.unlink()
                private_key = self._generate_and_store_key(key_path)
        else:
            private_key = self._generate_and_store_key(key_path)

        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._device_private_key = private_key
        self._device_public_key_bytes = public_bytes
        self._device_public_key_b64 = self._base64url_encode(public_bytes)
        self._device_id = hashlib.sha256(public_bytes).hexdigest()
        self._device_identity_hex = public_bytes.hex()
        return self._device_identity_hex

    def _generate_and_store_key(self, key_path: Path) -> Ed25519PrivateKey:
        private_key = Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        try:
            self._write_private_key_file(key_path, private_bytes)
        except OSError:
            if key_path.exists():
                return self._deserialize_private_key(key_path.read_bytes())
            raise
        return private_key

    @staticmethod
    def _deserialize_private_key(key_bytes: bytes) -> Ed25519PrivateKey:
        if not key_bytes:
            raise ValueError("device.key is empty")

        with contextlib.suppress(ValueError, TypeError):
            loaded = serialization.load_der_private_key(key_bytes, password=None)
            if isinstance(loaded, Ed25519PrivateKey):
                return loaded

        with contextlib.suppress(ValueError, TypeError):
            loaded = serialization.load_pem_private_key(key_bytes, password=None)
            if isinstance(loaded, Ed25519PrivateKey):
                return loaded

        if len(key_bytes) == 32:
            return Ed25519PrivateKey.from_private_bytes(key_bytes)

        raise ValueError("device.key does not contain a valid Ed25519 private key")

    @staticmethod
    def _write_private_key_file(key_path: Path, key_bytes: bytes):
        temp_path = key_path.with_name(f"{key_path.name}.{uuid4().hex}.tmp")
        fd = os.open(os.fspath(temp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, key_path)
            with contextlib.suppress(OSError):
                os.chmod(key_path, 0o600)
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(temp_path)
            raise

    def _build_connect_auth_section(self) -> dict | None:
        if not self._access_token:
            return None
        return {"token": self._access_token}

    def _build_connect_device_payload(self, nonce: str, token: str | None) -> dict | None:
        if not nonce:
            return None

        self._load_or_generate_key()
        if self._device_private_key is None or self._device_public_key_b64 is None or self._device_id is None:
            raise RuntimeError("device private key is not initialized")

        signed_at = int(time.time() * 1000)
        signature_payload = self._build_device_signature_payload(nonce, signed_at, token)
        signature = self._device_private_key.sign(signature_payload.encode("utf-8"))
        return {
            "id": self._device_id,
            "publicKey": self._device_public_key_b64,
            "signature": self._base64url_encode(signature),
            "signedAt": signed_at,
            "nonce": nonce,
        }

    def _build_device_signature_payload(self, nonce: str, signed_at: int, token: str | None) -> str:
        self._load_or_generate_key()
        device_id = self._device_id or ""
        scope_text = ",".join(CONNECT_SCOPES)
        return "|".join(
            [
                "v2",
                device_id,
                CLIENT_ID,
                CLIENT_MODE,
                CONNECT_ROLE,
                scope_text,
                str(signed_at),
                token or "",
                nonce,
            ]
        )

    @staticmethod
    def _base64url_encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    def _flush_pending_messages(self):
        if not self._gateway_ready or self._loop is None:
            return

        messages: list[str] = []
        with self._pending_lock:
            while self._pending_messages:
                messages.append(self._pending_messages.popleft())

        for message in messages:
            task = asyncio.run_coroutine_threadsafe(self._request_chat_send(message), self._loop)
            task.add_done_callback(self._consume_background_future)

    def _emit_notice(self, key: str, message: str):
        if not message or self._last_notice_key == key:
            return

        self._last_notice_key = key
        self.message_received.emit(message)

    @classmethod
    def _normalize_incoming_message(cls, message: object) -> str | None:
        if isinstance(message, (dict, list)):
            return cls._extract_message_payload(message)

        raw_text = cls._coerce_message_text(message)

        if not raw_text:
            return None

        if raw_text.startswith(("{", "[")):
            with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
                normalized = cls._extract_message_payload(json.loads(raw_text))
                if normalized:
                    return normalized
                return None

        return raw_text

    @staticmethod
    def _coerce_message_text(message: object) -> str:
        if isinstance(message, bytes):
            return message.decode("utf-8", errors="ignore").strip()
        return str(message).strip()

    @staticmethod
    def _parse_transport_frame(raw_text: str) -> dict | None:
        if not raw_text.startswith("{"):
            return None

        with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict) and parsed.get("type") in {"event", "res"}:
                return parsed
        return None

    def _normalize_event_payload(self, event_name: str, payload: object) -> str | None:
        role = self._extract_payload_role(payload)
        if role and role not in VISIBLE_MESSAGE_ROLES:
            return None

        if event_name == "session.message" and isinstance(payload, dict):
            message_payload = payload.get("message")
            return self._filter_incomplete_action_token(self._extract_message_payload(message_payload))

        return self._filter_incomplete_action_token(self._extract_message_payload(payload))

    @staticmethod
    def _filter_incomplete_action_token(message: str | None) -> str | None:
        if not message:
            return None

        normalized = message.strip()
        if normalized.startswith("[ACTION") and "]" not in normalized:
            return None
        return normalized

    @staticmethod
    def _extract_payload_role(payload: object) -> str | None:
        if isinstance(payload, dict):
            message_payload = payload.get("message")
            if isinstance(message_payload, dict):
                role = message_payload.get("role")
                if isinstance(role, str):
                    return role.strip().lower() or None

            role = payload.get("role")
            if isinstance(role, str):
                return role.strip().lower() or None
        return None

    @classmethod
    def _extract_message_payload(cls, payload: object) -> str | None:
        if isinstance(payload, str):
            return payload.strip() or None

        if isinstance(payload, list):
            parts = [part for item in payload if (part := cls._extract_message_payload(item))]
            return " ".join(parts).strip() or None

        if not isinstance(payload, dict):
            return None

        if payload.get("type") == "event":
            event_name = payload.get("event")
            if isinstance(event_name, str) and event_name in IGNORED_GATEWAY_EVENTS:
                return None
            return cls._extract_message_payload(payload.get("payload"))

        if payload.get("type") == "res":
            return None

        action_token = cls._extract_action_token(payload)
        text_value = cls._extract_text_value(payload)

        nested_payload = payload.get("payload")
        if nested_payload is not None:
            if isinstance(nested_payload, dict):
                nested_action = cls._extract_action_token(nested_payload)
                nested_text = cls._extract_text_value(nested_payload)
                if not action_token:
                    action_token = nested_action
                if not text_value:
                    text_value = nested_text or cls._extract_message_payload(nested_payload)
            elif not text_value:
                text_value = cls._extract_message_payload(nested_payload)

        if action_token and text_value:
            return f"{action_token} {text_value}".strip()
        if action_token:
            return action_token
        if text_value:
            return text_value
        return None

    @classmethod
    def _extract_text_value(cls, payload: dict) -> str | None:
        for key in MESSAGE_TEXT_KEYS:
            value = payload.get(key)
            if value is None:
                continue

            text = cls._extract_message_payload(value)
            if text:
                return text
        return None

    @staticmethod
    def _extract_action_token(payload: dict) -> str | None:
        for key in MESSAGE_ACTION_KEYS:
            value = payload.get(key)
            if not isinstance(value, str):
                continue

            action_name = value.strip()
            if not action_name:
                continue
            if action_name.startswith("[ACTION:"):
                return action_name
            if action_name.lower().startswith("action:"):
                action_name = action_name.split(":", 1)[1].strip()
            normalized_action_name = action_name.lower()
            if normalized_action_name in SUPPORTED_ACTION_NAMES:
                return f"[ACTION:{normalized_action_name}]"
        return None

    def _reject_pending_requests(self, reason: str):
        for request_id, pending in list(self._pending_requests.items()):
            if pending.done():
                self._pending_requests.pop(request_id, None)
                continue
            pending.set_exception(RuntimeError(reason))
            self._pending_requests.pop(request_id, None)

    @staticmethod
    def _consume_background_future(future):
        with contextlib.suppress(asyncio.CancelledError, Exception):
            future.result()
