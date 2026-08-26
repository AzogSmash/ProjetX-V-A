CREATE TABLE industrial_seasons(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 season_number INTEGER NOT NULL UNIQUE CHECK(season_number>0),
 name TEXT NOT NULL,
 starts_at INTEGER NOT NULL,
 ends_at INTEGER NOT NULL CHECK(ends_at>starts_at),
 status TEXT NOT NULL CHECK(status IN('upcoming','active','finished')),
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),
 finished_at INTEGER
);
CREATE UNIQUE INDEX industrial_one_active_season_idx ON industrial_seasons(status) WHERE status='active';
CREATE INDEX industrial_seasons_status_time_idx ON industrial_seasons(status,starts_at,ends_at);

CREATE TABLE industrial_season_scores(
 season_id INTEGER NOT NULL REFERENCES industrial_seasons(id),
 actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),
 discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 category TEXT NOT NULL CHECK(category IN('overall','mine','merchant','forge','bank','delivery','contracts')),
 score INTEGER NOT NULL DEFAULT 0 CHECK(score>=0),
 frozen INTEGER NOT NULL DEFAULT 0 CHECK(frozen IN(0,1)),
 updated_at INTEGER NOT NULL DEFAULT(unixepoch()),
 PRIMARY KEY(season_id,actor_id,category)
);
CREATE INDEX industrial_season_scores_rank_idx ON industrial_season_scores(season_id,category,score DESC,actor_id);

CREATE TABLE industrial_titles(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 slug TEXT NOT NULL UNIQUE,
 display_name TEXT NOT NULL,
 description TEXT NOT NULL,
 source TEXT NOT NULL,
 rarity TEXT NOT NULL DEFAULT 'common' CHECK(rarity IN('common','uncommon','rare','epic','legendary')),
 created_at INTEGER NOT NULL DEFAULT(unixepoch())
);
CREATE TABLE industrial_user_titles(
 discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 title_id INTEGER NOT NULL REFERENCES industrial_titles(id),
 source_reference TEXT NOT NULL,
 unlocked_at INTEGER NOT NULL DEFAULT(unixepoch()),
 equipped INTEGER NOT NULL DEFAULT 0 CHECK(equipped IN(0,1)),
 PRIMARY KEY(discord_user_id,title_id)
);
CREATE UNIQUE INDEX industrial_one_equipped_title_idx ON industrial_user_titles(discord_user_id) WHERE equipped=1;
CREATE INDEX industrial_user_titles_user_idx ON industrial_user_titles(discord_user_id,unlocked_at DESC);
CREATE TABLE industrial_title_actions(
 request_id TEXT PRIMARY KEY,
 discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 action TEXT NOT NULL CHECK(action IN('equip','remove')),
 title_id INTEGER REFERENCES industrial_titles(id),
 created_at INTEGER NOT NULL DEFAULT(unixepoch())
);

CREATE TABLE industrial_season_rewards(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 season_id INTEGER NOT NULL REFERENCES industrial_seasons(id),
 discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 category TEXT NOT NULL,
 rank INTEGER NOT NULL CHECK(rank>0),
 reward_type TEXT NOT NULL CHECK(reward_type IN('title','badge','trophy','reputation','cosmetic')),
 reward_value TEXT NOT NULL,
 granted_at INTEGER NOT NULL DEFAULT(unixepoch()),
 UNIQUE(season_id,discord_user_id,category,reward_type)
);

CREATE TABLE industrial_economic_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 cycle_key TEXT NOT NULL UNIQUE,
 event_type TEXT NOT NULL CHECK(event_type IN('mining_rush','industrial_boom','world_demand','logistics_rush','delivery_bonus')),
 display_name TEXT NOT NULL,
 starts_at INTEGER NOT NULL,
 ends_at INTEGER NOT NULL CHECK(ends_at>starts_at),
 multiplier_bps INTEGER NOT NULL CHECK(multiplier_bps BETWEEN 8000 AND 12500),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('upcoming','active','finished')),
 created_at INTEGER NOT NULL DEFAULT(unixepoch())
);
CREATE INDEX industrial_events_active_idx ON industrial_economic_events(status,starts_at,ends_at);

CREATE TABLE industrial_team_members(
 company_id INTEGER NOT NULL REFERENCES industrial_companies(id),
 discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 role TEXT NOT NULL CHECK(role IN('owner','manager','employee')),
 joined_at INTEGER NOT NULL DEFAULT(unixepoch()),
 updated_at INTEGER NOT NULL DEFAULT(unixepoch()),
 PRIMARY KEY(company_id,discord_user_id)
);
CREATE UNIQUE INDEX industrial_company_owner_idx ON industrial_team_members(company_id) WHERE role='owner';
CREATE INDEX industrial_team_user_idx ON industrial_team_members(discord_user_id,role);

CREATE TABLE industrial_team_invitations(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 company_id INTEGER NOT NULL REFERENCES industrial_companies(id),
 inviter_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 invitee_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','accepted','declined','expired','cancelled')),
 request_id TEXT NOT NULL UNIQUE,
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),
 expires_at INTEGER NOT NULL DEFAULT(unixepoch()+259200),
 resolved_at INTEGER,
 CHECK(inviter_discord_user_id<>invitee_discord_user_id)
);
CREATE UNIQUE INDEX industrial_team_pending_invite_idx ON industrial_team_invitations(company_id,invitee_discord_user_id) WHERE status='pending';
CREATE INDEX industrial_team_invitee_idx ON industrial_team_invitations(invitee_discord_user_id,status,expires_at);

CREATE TABLE industrial_team_audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 company_id INTEGER NOT NULL REFERENCES industrial_companies(id),
 actor_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 target_discord_user_id INTEGER REFERENCES industrial_users(discord_user_id),
 action TEXT NOT NULL,
 metadata TEXT NOT NULL DEFAULT '{}',
 request_id TEXT NOT NULL UNIQUE,
 created_at INTEGER NOT NULL DEFAULT(unixepoch())
);
CREATE INDEX industrial_team_audit_company_time_idx ON industrial_team_audit(company_id,created_at DESC);

ALTER TABLE industrial_notification_preferences ADD COLUMN season_enabled INTEGER NOT NULL DEFAULT 1 CHECK(season_enabled IN(0,1));
ALTER TABLE industrial_notification_preferences ADD COLUMN event_enabled INTEGER NOT NULL DEFAULT 1 CHECK(event_enabled IN(0,1));
ALTER TABLE industrial_notification_preferences ADD COLUMN team_enabled INTEGER NOT NULL DEFAULT 1 CHECK(team_enabled IN(0,1));

INSERT INTO industrial_titles(slug,display_name,description,source,rarity) VALUES
 ('master_miner','⛏️ Maître Mineur','Titre de progression minière.','achievement','rare'),
 ('road_ace','🚚 As du Volant','Titre de progression logistique.','achievement','rare'),
 ('master_smith','🔨 Maître Forgeron','Titre de progression de forge.','achievement','rare'),
 ('magnate','🏦 Magnat','Titre de progression bancaire.','reputation','epic'),
 ('market_shark','📈 Requin du Marché','Titre de progression commerciale.','achievement','epic'),
 ('negotiator','🤝 Négociateur','Titre de progression contractuelle.','achievement','uncommon'),
 ('goal_getter','🎯 Objectif accompli','Titre obtenu grâce aux objectifs industriels.','objective','uncommon'),
 ('industrial_empire','🏭 Empire Industriel','Titre de très haute valeur industrielle.','reputation','legendary');

INSERT INTO industrial_team_members(company_id,discord_user_id,role)
 SELECT id,owner_discord_user_id,'owner' FROM industrial_companies;
