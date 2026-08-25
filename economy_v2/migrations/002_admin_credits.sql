CREATE TABLE industrial_admin_credit_requests(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 80),
 admin_discord_user_id INTEGER NOT NULL,
 target_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 operation TEXT NOT NULL CHECK(operation IN('add','remove')),
 amount INTEGER NOT NULL CHECK(amount BETWEEN 1 AND 1000000000),
 balance_before INTEGER NOT NULL CHECK(balance_before>=0),
 balance_after INTEGER NOT NULL CHECK(balance_after>=0),
 reason TEXT,
 created_at INTEGER NOT NULL DEFAULT(unixepoch())
);
CREATE INDEX industrial_admin_credit_target_time_idx
 ON industrial_admin_credit_requests(target_discord_user_id,created_at);
