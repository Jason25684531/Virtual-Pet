from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pet_harness.asset.asset_contract import AssetRequest, AssetResponse
from pet_harness.models.events import RewardEvent, utc_now
from pet_harness.models.skill import Skill
from pet_harness.tools.tool_models import ToolResult


DEFAULT_USER_ID = "default"

#DB儲存邏輯

class SQLiteStore:
    def __init__(self, db_path: str | Path = Path("data") / "pet_state.db") -> None:
        self.db_path = Path(db_path).resolve()

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        with self.connect() as conn:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            conn.execute(
                """
                INSERT OR IGNORE INTO user_progress (user_id, xp_total, level, updated_at)
                VALUES (?, 0, 1, ?)
                """,
                (DEFAULT_USER_ID, utc_now()),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO behavior_state (state_key, behavior_id, updated_at)
                VALUES ('default', 'idle', ?)
                """,
                (utc_now(),),
            )
            # provider config/status 已全域化(ProviderRuntime),角色 store 不再讀寫。

    def sync_skills(self, skills: list[Skill]) -> None:
        with self.connect() as conn:
            for skill in skills:
                conn.execute(
                    """
                    INSERT INTO skills
                    (name, description, triggers_json, behavior, xp_reward, required_tool,
                     unlock_reward, file_path, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        description=excluded.description,
                        triggers_json=excluded.triggers_json,
                        behavior=excluded.behavior,
                        xp_reward=excluded.xp_reward,
                        required_tool=excluded.required_tool,
                        unlock_reward=excluded.unlock_reward,
                        file_path=excluded.file_path,
                        updated_at=excluded.updated_at
                    """,
                    (
                        skill.name,
                        skill.description,
                        json.dumps(skill.triggers),
                        skill.behavior,
                        skill.xp_reward,
                        skill.required_tool,
                        skill.unlock_reward,
                        skill.file_path,
                        utc_now(),
                    ),
                )

    def list_skills(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM skills ORDER BY name").fetchall()
        return [self._skill_row(row) for row in rows]

    def add_user_xp(self, delta: int) -> dict[str, Any]:
        """同 connection、同 transaction 內 seed(缺 row 時)→ UPDATE → 讀回,
        commit 成功才回傳;例外時整個 transaction rollback 並向上傳播,
        connection 一律於 finally 關閉,不留殘留 handle。"""
        conn = self.connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO user_progress (user_id, xp_total, level, updated_at)
                    VALUES (?, 0, 1, ?)
                    """,
                    (DEFAULT_USER_ID, utc_now()),
                )
                conn.execute(
                    """
                    UPDATE user_progress
                    SET xp_total = xp_total + ?, level = MAX(1, ((xp_total + ?) / 100) + 1), updated_at = ?
                    WHERE user_id = ?
                    """,
                    (delta, delta, utc_now(), DEFAULT_USER_ID),
                )
                row = conn.execute(
                    "SELECT * FROM user_progress WHERE user_id = ?", (DEFAULT_USER_ID,)
                ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def get_user_progress(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_progress WHERE user_id = ?", (DEFAULT_USER_ID,)
            ).fetchone()
        return dict(row) if row else {"user_id": DEFAULT_USER_ID, "xp_total": 0, "level": 1}

    def add_skill_xp(self, skill_name: str, delta: int) -> dict[str, Any]:
        """同 add_user_xp 的 transaction 模式;首次 INSERT 的 level 依本次 delta
        計算,不再硬編碼為 1(delta >= 100 時舊實作會回傳錯誤的 level)。"""
        conn = self.connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO skill_progress (skill_name, xp_total, level, updated_at)
                    VALUES (?, ?, MAX(1, (? / 100) + 1), ?)
                    ON CONFLICT(skill_name) DO UPDATE SET
                        xp_total = xp_total + excluded.xp_total,
                        level = MAX(1, ((xp_total + excluded.xp_total) / 100) + 1),
                        updated_at = excluded.updated_at
                    """,
                    (skill_name, delta, delta, utc_now()),
                )
                row = conn.execute(
                    "SELECT * FROM skill_progress WHERE skill_name = ?", (skill_name,)
                ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def get_skill_progress(self, skill_name: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM skill_progress WHERE skill_name = ?", (skill_name,)
            ).fetchone()
        return dict(row) if row else {"skill_name": skill_name, "xp_total": 0, "level": 1}

    def unlock_reward(self, event: RewardEvent, metadata: dict[str, Any] | None = None) -> bool:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT reward_id FROM reward_unlocks WHERE reward_id = ?", (event.reward_id,)
            ).fetchone()
            if exists:
                return False
            conn.execute(
                """
                INSERT INTO reward_unlocks
                (reward_id, reward_type, unlock_reason, xp_threshold, inventory_item_id, unlocked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.reward_id,
                    event.reward_type,
                    event.unlock_reason,
                    event.xp_threshold,
                    event.inventory_item_id,
                    event.timestamp,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO inventory
                (item_id, reward_id, item_type, metadata_json, unlocked_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.inventory_item_id,
                    event.reward_id,
                    event.reward_type,
                    metadata_json,
                    event.timestamp,
                ),
            )
        return True

    def list_reward_unlocks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM reward_unlocks ORDER BY unlocked_at").fetchall()
        return [dict(row) for row in rows]

    def list_inventory(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM inventory ORDER BY unlocked_at").fetchall()
        return [self._json_row(row, "metadata_json", "metadata") for row in rows]

    def set_behavior_state(self, behavior_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO behavior_state (state_key, behavior_id, updated_at)
                VALUES ('default', ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    behavior_id=excluded.behavior_id,
                    updated_at=excluded.updated_at
                """,
                (behavior_id, utc_now()),
            )

    def get_behavior_state(self) -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT behavior_id FROM behavior_state WHERE state_key = 'default'"
            ).fetchone()
        return str(row["behavior_id"]) if row else "idle"

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def log_event(self, input_payload: dict[str, Any], output_payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO event_log (event_id, input_payload, output_payload, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    input_payload.get("event_id"),
                    json.dumps(input_payload, ensure_ascii=False),
                    json.dumps(output_payload, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def set_tool_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now()),
            )

    def get_tool_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM tool_settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def log_tool_result(self, result: ToolResult, request_payload: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_log (tool_name, request_id, request_json, result_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    result.tool_name,
                    result.request_id,
                    json.dumps(request_payload or {}, ensure_ascii=False),
                    json.dumps(result.to_dict(), ensure_ascii=False),
                    utc_now(),
                ),
            )

    def recent_tool_results(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "tool_name": row["tool_name"],
                "request_id": row["request_id"],
                "request": json.loads(row["request_json"] or "{}"),
                "result": json.loads(row["result_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def log_asset_manifest(self, request: AssetRequest, response: AssetResponse) -> None:
        request_payload = request.to_dict()
        response_payload = response.to_dict()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO asset_manifest (
                    request_id, source_event_id, reward_id, asset_type, status, asset_id, file_path, webm_key,
                    request_json, response_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    request.source_event_id,
                    request.requested_reward,
                    request.asset_type,
                    response.status,
                    response.asset_id,
                    response.file_path,
                    response.webm_key,
                    json.dumps(request_payload, ensure_ascii=False),
                    json.dumps(response_payload, ensure_ascii=False),
                    request.created_at,
                    utc_now(),
                ),
            )

    def list_asset_manifest(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM asset_manifest ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "request_id": row["request_id"],
                "source_event_id": row["source_event_id"],
                "reward_id": row["reward_id"],
                "asset_type": row["asset_type"],
                "status": row["status"],
                "asset_id": row["asset_id"],
                "file_path": row["file_path"],
                "webm_key": row["webm_key"],
                "request": json.loads(row["request_json"] or "{}"),
                "response": json.loads(row["response_json"] or "{}"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def insert_asset_job(self, job: dict[str, Any]) -> None:
        columns = ("job_id", "parent_job_id", "character_id", "workflow_type", "variant", "motion_key", "status", "comfy_prompt_id", "idempotency_key", "retry_count", "max_retries", "timeout_sec", "error_code", "error_message", "metadata_json", "created_at", "started_at", "completed_at")
        payload = dict(job)
        payload["metadata_json"] = json.dumps(payload.get("metadata", {}), ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(f"INSERT INTO asset_jobs ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", tuple(payload.get(key) for key in columns))

    def update_asset_job_status(self, job_id: str, status: str, **changes: Any) -> None:
        allowed = {"comfy_prompt_id", "retry_count", "error_code", "error_message", "started_at", "completed_at", "metadata_json"}
        updates = {"status": status, **{key: value for key, value in changes.items() if key in allowed}}
        if "metadata" in changes:
            updates["metadata_json"] = json.dumps(changes["metadata"], ensure_ascii=False)
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.connect() as conn:
            conn.execute(f"UPDATE asset_jobs SET {assignments} WHERE job_id=?", (*updates.values(), job_id))

    def claim_asset_job(self, job_id: str, expected_status: str, retry_count: int, started_at: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE asset_jobs SET status='uploading', retry_count=?, started_at=? "
                "WHERE job_id=? AND status=?",
                (retry_count, started_at, job_id, expected_status),
            )
        return cursor.rowcount == 1

    def get_asset_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM asset_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._asset_job_row(row) if row else None

    def find_job_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM asset_jobs WHERE idempotency_key=?", (key,)).fetchone()
        return self._asset_job_row(row) if row else None

    def list_pending_asset_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM asset_jobs WHERE status IN ('queued','uploading','submitted','running','timed_out') ORDER BY created_at").fetchall()
        return [self._asset_job_row(row) for row in rows]

    def list_asset_jobs_by_parent(self, parent_job_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM asset_jobs WHERE parent_job_id=? ORDER BY created_at", (parent_job_id,)).fetchall()
        return [self._asset_job_row(row) for row in rows]

    def insert_character_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        identity = (asset["character_id"], asset["asset_type"], asset["variant"], asset.get("motion_key"), asset.get("reward_id"), asset.get("event_id"))
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version FROM character_assets WHERE character_id=? AND asset_type=? AND variant=? AND motion_key IS ? AND reward_id IS ? AND event_id IS ?", identity).fetchone()
            version = int(row["version"]) + 1
            conn.execute("UPDATE character_assets SET active=0 WHERE character_id=? AND asset_type=? AND variant=? AND motion_key IS ? AND reward_id IS ? AND event_id IS ?", identity)
            data = {**asset, "version": version, "active": 1}
            columns = ("asset_id", "character_id", "asset_type", "variant", "motion_key", "reward_id", "level", "event_id", "file_path", "filename", "mime_type", "sha256", "version", "active", "source_job_id", "created_at")
            conn.execute(f"INSERT INTO character_assets ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", tuple(data.get(key) for key in columns))
        return {**data, "version": version, "active": True}

    def list_character_assets(self, character_id: str, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM character_assets WHERE character_id=?" + (" AND active=1" if active_only else "") + " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, (character_id,)).fetchall()
        return [dict(row) | {"active": bool(row["active"])} for row in rows]

    @staticmethod
    def _asset_job_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["metadata"] = json.loads(payload.pop("metadata_json") or "{}")
        return payload

    def recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM event_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._event_row(row) for row in rows]

    def clear_events(self) -> None:
        """清空對話事件歷史;人設變更時用來避免舊身份的問答殘留在短期記憶裡。"""
        with self.connect() as conn:
            conn.execute("DELETE FROM event_log")

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "user_progress": self.get_user_progress(),
            "skills": self.list_skills(),
            "inventory": self.list_inventory(),
            "reward_unlocks": self.list_reward_unlocks(),
            "behavior_state": self.get_behavior_state(),
            "tool_results": self.recent_tool_results(limit=10),
            "asset_manifest": self.list_asset_manifest(limit=10),
        }

    def _skill_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["triggers"] = json.loads(payload.pop("triggers_json") or "[]")
        return payload

    def _event_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["input_payload"] = json.loads(payload["input_payload"])
        payload["output_payload"] = json.loads(payload["output_payload"])
        return payload

    def _json_row(self, row: sqlite3.Row, json_key: str, output_key: str) -> dict[str, Any]:
        payload = dict(row)
        payload[output_key] = json.loads(payload.pop(json_key) or "{}")
        return payload
