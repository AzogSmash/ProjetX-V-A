CREATE TABLE industrial_tutorial_progress(
 discord_user_id INTEGER PRIMARY KEY,
 path TEXT NOT NULL DEFAULT 'common' CHECK(path IN('common','miner','merchant','blacksmith','banker')),
 current_step INTEGER NOT NULL DEFAULT 0 CHECK(current_step BETWEEN 0 AND 7),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','stopped','completed')),
 started_at INTEGER NOT NULL DEFAULT(unixepoch()),
 updated_at INTEGER NOT NULL DEFAULT(unixepoch()),
 completed_at INTEGER
);

CREATE TABLE industrial_tutorial_actions(
 request_id TEXT PRIMARY KEY,
 discord_user_id INTEGER NOT NULL,
 action TEXT NOT NULL CHECK(action IN('start','next','restart','stop')),
 created_at INTEGER NOT NULL DEFAULT(unixepoch())
);

CREATE INDEX industrial_tutorial_status_idx
 ON industrial_tutorial_progress(status,updated_at);
