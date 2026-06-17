CREATE TABLE IF NOT EXISTS user_progress (
    user_id TEXT PRIMARY KEY,
    xp_total INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    triggers_json TEXT NOT NULL,
    behavior TEXT NOT NULL,
    xp_reward INTEGER NOT NULL,
    required_tool TEXT,
    unlock_reward TEXT,
    file_path TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_progress (
    skill_name TEXT PRIMARY KEY,
    xp_total INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    item_id TEXT PRIMARY KEY,
    reward_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    unlocked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reward_unlocks (
    reward_id TEXT PRIMARY KEY,
    reward_type TEXT NOT NULL,
    unlock_reason TEXT NOT NULL,
    xp_threshold INTEGER NOT NULL,
    inventory_item_id TEXT NOT NULL,
    unlocked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS behavior_state (
    state_key TEXT PRIMARY KEY,
    behavior_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT,
    input_payload TEXT NOT NULL,
    output_payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_status (
    provider_type TEXT PRIMARY KEY,
    healthy INTEGER NOT NULL,
    message TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    request_id TEXT,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_manifest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    source_event_id TEXT,
    reward_id TEXT,
    asset_type TEXT NOT NULL,
    status TEXT NOT NULL,
    asset_id TEXT,
    file_path TEXT,
    webm_key TEXT,
    request_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
