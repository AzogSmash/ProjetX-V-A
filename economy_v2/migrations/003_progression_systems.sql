CREATE TABLE industrial_achievements(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 achievement_key TEXT NOT NULL,
 title TEXT NOT NULL,
 reputation_awarded INTEGER NOT NULL DEFAULT 0 CHECK(reputation_awarded>=0),
 earned_at INTEGER NOT NULL DEFAULT(unixepoch()),
 UNIQUE(discord_user_id,achievement_key)
);
CREATE TABLE industrial_reputation_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 event_type TEXT NOT NULL,
 reputation INTEGER NOT NULL CHECK(reputation>0),
 source_key TEXT NOT NULL,
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),
 UNIQUE(discord_user_id,source_key)
);
CREATE TABLE industrial_objective_progress(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 objective_key TEXT NOT NULL,
 period_type TEXT NOT NULL CHECK(period_type IN('daily','weekly')),
 period_start INTEGER NOT NULL,
 target INTEGER NOT NULL CHECK(target>0),
 progress INTEGER NOT NULL DEFAULT 0 CHECK(progress>=0),
 completed_at INTEGER,
 reputation_awarded INTEGER NOT NULL DEFAULT 0 CHECK(reputation_awarded>=0),
 updated_at INTEGER NOT NULL DEFAULT(unixepoch()),
 UNIQUE(discord_user_id,objective_key,period_type,period_start)
);
CREATE TABLE industrial_notification_preferences(
 discord_user_id INTEGER PRIMARY KEY REFERENCES industrial_users(discord_user_id),
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),
 market_enabled INTEGER NOT NULL DEFAULT 1 CHECK(market_enabled IN(0,1)),
 transport_enabled INTEGER NOT NULL DEFAULT 1 CHECK(transport_enabled IN(0,1)),
 forge_enabled INTEGER NOT NULL DEFAULT 1 CHECK(forge_enabled IN(0,1)),
 shipment_enabled INTEGER NOT NULL DEFAULT 1 CHECK(shipment_enabled IN(0,1)),
 contract_enabled INTEGER NOT NULL DEFAULT 1 CHECK(contract_enabled IN(0,1)),
 updated_at INTEGER NOT NULL DEFAULT(unixepoch())
);
CREATE TABLE industrial_notification_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 event_type TEXT NOT NULL,
 event_key TEXT NOT NULL,
 payload TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','sent','dismissed')),
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),
 sent_at INTEGER,
 UNIQUE(discord_user_id,event_type,event_key)
);
CREATE INDEX industrial_achievements_user_time_idx
 ON industrial_achievements(discord_user_id,earned_at DESC);
CREATE INDEX industrial_reputation_user_time_idx
 ON industrial_reputation_events(discord_user_id,created_at DESC);
CREATE INDEX industrial_objectives_user_period_idx
 ON industrial_objective_progress(discord_user_id,period_type,period_start DESC);
CREATE INDEX industrial_notifications_pending_idx
 ON industrial_notification_events(discord_user_id,status,created_at);
