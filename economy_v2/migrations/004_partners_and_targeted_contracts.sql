CREATE TABLE industrial_partnerships(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 requester_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 target_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 low_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 high_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','accepted','removed','declined')),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 80),
 accepted_at INTEGER,
 removed_at INTEGER,
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),
 updated_at INTEGER NOT NULL DEFAULT(unixepoch()),
 CHECK(requester_discord_user_id<>target_discord_user_id),
 CHECK(low_user_id<high_user_id),
 UNIQUE(low_user_id,high_user_id)
);
ALTER TABLE industrial_contracts ADD COLUMN target_company_id INTEGER REFERENCES industrial_companies(id);
ALTER TABLE industrial_contracts ADD COLUMN target_actor_id INTEGER REFERENCES industrial_actors(id);
CREATE INDEX industrial_partnerships_user_status_idx
 ON industrial_partnerships(low_user_id,high_user_id,status);
CREATE INDEX industrial_contracts_target_status_idx
 ON industrial_contracts(target_actor_id,status,expires_at);
