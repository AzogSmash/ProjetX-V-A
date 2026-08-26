CREATE TABLE industrial_users(
 discord_user_id INTEGER PRIMARY KEY, credits INTEGER NOT NULL DEFAULT 0 CHECK(credits>=0),
 primary_job TEXT CHECK(primary_job IS NULL OR primary_job IN('miner','merchant','blacksmith','banker')),
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_companies(
 id INTEGER PRIMARY KEY AUTOINCREMENT,owner_discord_user_id INTEGER NOT NULL UNIQUE REFERENCES industrial_users(discord_user_id),
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 3 AND 40),job_type TEXT NOT NULL CHECK(job_type IN('miner','merchant','blacksmith','banker')),
 level INTEGER NOT NULL DEFAULT 1 CHECK(level>=1),is_first_company INTEGER NOT NULL DEFAULT 1 CHECK(is_first_company IN(0,1)),
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_ai_companies(
 id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,job_type TEXT NOT NULL UNIQUE CHECK(job_type IN('miner','merchant','blacksmith')),
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN(0,1)),efficiency_percent INTEGER NOT NULL DEFAULT 60 CHECK(efficiency_percent BETWEEN 1 AND 100),
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_actors(
 id INTEGER PRIMARY KEY AUTOINCREMENT,actor_type TEXT NOT NULL CHECK(actor_type IN('player','ai')),
 discord_user_id INTEGER UNIQUE REFERENCES industrial_users(discord_user_id),ai_company_id INTEGER UNIQUE REFERENCES industrial_ai_companies(id),
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()),
 CHECK((actor_type='player' AND discord_user_id IS NOT NULL AND ai_company_id IS NULL) OR
       (actor_type='ai' AND ai_company_id IS NOT NULL AND discord_user_id IS NULL)));
CREATE TABLE industrial_inventory(
 actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),owner_discord_user_id INTEGER REFERENCES industrial_users(discord_user_id),
 resource_type TEXT NOT NULL CHECK(resource_type IN('iron_ore','iron_ingot')),quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),
 updated_at INTEGER NOT NULL DEFAULT(unixepoch()),PRIMARY KEY(actor_id,resource_type),UNIQUE(owner_discord_user_id,resource_type));
CREATE TABLE industrial_mines(
 owner_discord_user_id INTEGER PRIMARY KEY REFERENCES industrial_users(discord_user_id),company_id INTEGER NOT NULL UNIQUE REFERENCES industrial_companies(id),
 resource_type TEXT NOT NULL DEFAULT 'iron_ore' CHECK(resource_type='iron_ore'),stock INTEGER NOT NULL DEFAULT 0 CHECK(stock>=0),
 storage_level INTEGER NOT NULL DEFAULT 1 CHECK(storage_level>=1),production_level INTEGER NOT NULL DEFAULT 1 CHECK(production_level>=1),
 quality_level INTEGER NOT NULL DEFAULT 1 CHECK(quality_level>=1),production_progress INTEGER NOT NULL DEFAULT 0 CHECK(production_progress BETWEEN 0 AND 3599),
 last_production_at INTEGER NOT NULL DEFAULT(unixepoch()),created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_market_orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT,owner_actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),owner_discord_user_id INTEGER REFERENCES industrial_users(discord_user_id),
 company_id INTEGER REFERENCES industrial_companies(id),side TEXT NOT NULL CHECK(side IN('buy','sell')),resource_type TEXT NOT NULL CHECK(resource_type IN('iron_ore','iron_ingot')),
 original_quantity INTEGER NOT NULL CHECK(original_quantity BETWEEN 1 AND 1000000),remaining_quantity INTEGER NOT NULL CHECK(remaining_quantity>=0 AND remaining_quantity<=original_quantity),
 unit_price INTEGER NOT NULL CHECK(unit_price BETWEEN 1 AND 1000000),escrow_quantity INTEGER NOT NULL DEFAULT 0 CHECK(escrow_quantity>=0),
 escrow_credits INTEGER NOT NULL DEFAULT 0 CHECK(escrow_credits>=0),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','filled','cancelled')),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 80),created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()),closed_at INTEGER);
CREATE INDEX industrial_market_book_idx ON industrial_market_orders(resource_type,side,status,unit_price,created_at,id);
CREATE TABLE industrial_market_trades(
 id INTEGER PRIMARY KEY AUTOINCREMENT,resource_type TEXT NOT NULL CHECK(resource_type IN('iron_ore','iron_ingot')),quantity INTEGER NOT NULL CHECK(quantity>0),
 unit_price INTEGER NOT NULL CHECK(unit_price>0),total_price INTEGER NOT NULL CHECK(total_price=quantity*unit_price),seller_actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),
 buyer_actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),seller_discord_user_id INTEGER REFERENCES industrial_users(discord_user_id),buyer_discord_user_id INTEGER REFERENCES industrial_users(discord_user_id),
 sell_order_id INTEGER REFERENCES industrial_market_orders(id),buy_order_id INTEGER REFERENCES industrial_market_orders(id),created_at INTEGER NOT NULL DEFAULT(unixepoch()),CHECK(seller_actor_id<>buyer_actor_id));
CREATE TABLE industrial_merchants(
 owner_discord_user_id INTEGER PRIMARY KEY REFERENCES industrial_users(discord_user_id),company_id INTEGER NOT NULL UNIQUE REFERENCES industrial_companies(id),
 truck_count INTEGER NOT NULL DEFAULT 1 CHECK(truck_count>=1),truck_capacity_level INTEGER NOT NULL DEFAULT 1 CHECK(truck_capacity_level>=1),
 truck_speed_level INTEGER NOT NULL DEFAULT 1 CHECK(truck_speed_level>=1),warehouse_level INTEGER NOT NULL DEFAULT 1 CHECK(warehouse_level>=1),
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_merchant_upgrades(
 id INTEGER PRIMARY KEY AUTOINCREMENT,owner_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),upgrade_type TEXT NOT NULL,
 previous_level INTEGER NOT NULL,new_level INTEGER NOT NULL,cost INTEGER NOT NULL CHECK(cost>0),balance_after INTEGER NOT NULL CHECK(balance_after>=0),request_id TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_blacksmiths(
 owner_discord_user_id INTEGER PRIMARY KEY REFERENCES industrial_users(discord_user_id),company_id INTEGER NOT NULL UNIQUE REFERENCES industrial_companies(id),
 forge_level INTEGER NOT NULL DEFAULT 1,speed_level INTEGER NOT NULL DEFAULT 1,storage_level INTEGER NOT NULL DEFAULT 1,yield_level INTEGER NOT NULL DEFAULT 1,
 created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()),CHECK(forge_level>=1 AND speed_level>=1 AND storage_level>=1 AND yield_level>=1));
CREATE TABLE industrial_forge_jobs(
 id INTEGER PRIMARY KEY AUTOINCREMENT,owner_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),company_id INTEGER NOT NULL REFERENCES industrial_companies(id),
 forge_slot INTEGER NOT NULL CHECK(forge_slot>=1),resource_input TEXT NOT NULL DEFAULT 'iron_ore',resource_output TEXT NOT NULL DEFAULT 'iron_ingot',
 input_quantity INTEGER NOT NULL CHECK(input_quantity>0),output_quantity INTEGER NOT NULL CHECK(output_quantity>0),speed_level_at_start INTEGER NOT NULL,yield_level_at_start INTEGER NOT NULL,
 started_at INTEGER NOT NULL,finishes_at INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'processing' CHECK(status IN('processing','completed','collected')),
 request_id TEXT NOT NULL UNIQUE,completed_at INTEGER,collected_at INTEGER);
CREATE UNIQUE INDEX industrial_active_forge_slot_idx ON industrial_forge_jobs(owner_discord_user_id,forge_slot) WHERE status='processing';
CREATE TABLE industrial_forge_upgrades(
 id INTEGER PRIMARY KEY AUTOINCREMENT,owner_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),upgrade_type TEXT NOT NULL,
 previous_level INTEGER NOT NULL,new_level INTEGER NOT NULL,cost INTEGER NOT NULL,balance_after INTEGER NOT NULL,request_id TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_forge_collections(
 id INTEGER PRIMARY KEY AUTOINCREMENT,owner_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),collected_quantity INTEGER NOT NULL,
 inventory_quantity INTEGER NOT NULL,request_id TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_transports(
 id INTEGER PRIMARY KEY AUTOINCREMENT,sender_actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),receiver_actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),operator_actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),
 sender_company_id INTEGER REFERENCES industrial_companies(id),receiver_company_id INTEGER REFERENCES industrial_companies(id),merchant_discord_user_id INTEGER REFERENCES industrial_users(discord_user_id),
 transport_type TEXT NOT NULL CHECK(transport_type IN('ore_to_blacksmith','ingot_to_banker','ai_supply')),resource_type TEXT NOT NULL CHECK(resource_type IN('iron_ore','iron_ingot')),
 quantity INTEGER NOT NULL CHECK(quantity>0),departure_at INTEGER NOT NULL,arrival_at INTEGER NOT NULL,original_duration_seconds INTEGER NOT NULL,current_duration_seconds INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'in_transit' CHECK(status IN('in_transit','delivered')),truck_slot INTEGER NOT NULL,request_id TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL DEFAULT(unixepoch()),completed_at INTEGER);
CREATE UNIQUE INDEX industrial_active_truck_idx ON industrial_transports(operator_actor_id,truck_slot) WHERE status='in_transit';
CREATE TABLE industrial_ingot_shipments(
 id INTEGER PRIMARY KEY AUTOINCREMENT,blacksmith_company_id INTEGER NOT NULL REFERENCES industrial_companies(id),blacksmith_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),
 merchant_company_id INTEGER NOT NULL REFERENCES industrial_companies(id),merchant_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),banker_company_id INTEGER NOT NULL REFERENCES industrial_companies(id),
 banker_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),resource_type TEXT NOT NULL DEFAULT 'iron_ingot',quantity INTEGER NOT NULL CHECK(quantity>0),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','accepted','cancelled')),request_id TEXT NOT NULL UNIQUE,accept_request_id TEXT UNIQUE,cancel_request_id TEXT UNIQUE,
 transport_id INTEGER UNIQUE REFERENCES industrial_transports(id),created_at INTEGER NOT NULL DEFAULT(unixepoch()),accepted_at INTEGER,cancelled_at INTEGER,updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_bankers(owner_discord_user_id INTEGER PRIMARY KEY REFERENCES industrial_users(discord_user_id),company_id INTEGER NOT NULL UNIQUE REFERENCES industrial_companies(id),created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_world_sales(
 id INTEGER PRIMARY KEY AUTOINCREMENT,banker_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),banker_company_id INTEGER NOT NULL REFERENCES industrial_companies(id),
 resource_type TEXT NOT NULL DEFAULT 'iron_ingot',quantity INTEGER NOT NULL CHECK(quantity>0),unit_price INTEGER NOT NULL CHECK(unit_price BETWEEN 50 AND 120),total_credits INTEGER NOT NULL CHECK(total_credits=quantity*unit_price),
 balance_after INTEGER NOT NULL CHECK(balance_after>=0),request_id TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_delivery_profiles(discord_user_id INTEGER PRIMARY KEY REFERENCES industrial_users(discord_user_id),delivery_level INTEGER NOT NULL DEFAULT 1,delivery_xp INTEGER NOT NULL DEFAULT 0,completed_deliveries INTEGER NOT NULL DEFAULT 0,delivery_cooldown_until INTEGER,created_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_delivery_missions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,transport_id INTEGER NOT NULL UNIQUE REFERENCES industrial_transports(id),merchant_actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),merchant_discord_user_id INTEGER REFERENCES industrial_users(discord_user_id),
 resource_type TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>0),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','accepted','refunded')),commission_max INTEGER NOT NULL CHECK(commission_max>=0),
 escrow_remaining INTEGER NOT NULL CHECK(escrow_remaining>=0),courier_discord_user_id INTEGER REFERENCES industrial_users(discord_user_id),commission_paid INTEGER,merchant_refund INTEGER,saved_seconds INTEGER,xp_awarded INTEGER,
 accept_request_id TEXT UNIQUE,created_at INTEGER NOT NULL DEFAULT(unixepoch()),accepted_at INTEGER,refunded_at INTEGER);
CREATE TABLE industrial_contracts(
 id INTEGER PRIMARY KEY AUTOINCREMENT,creator_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),accepter_discord_user_id INTEGER REFERENCES industrial_users(discord_user_id),
 resource_type TEXT NOT NULL CHECK(resource_type IN('iron_ore','iron_ingot')),quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 1000000),total_price INTEGER NOT NULL CHECK(total_price BETWEEN 1 AND 1000000000),
 escrow_credits INTEGER NOT NULL CHECK(escrow_credits>=0),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','completed','cancelled','expired')),request_id TEXT NOT NULL UNIQUE,
 accept_request_id TEXT UNIQUE,cancel_request_id TEXT UNIQUE,created_at INTEGER NOT NULL DEFAULT(unixepoch()),expires_at INTEGER NOT NULL DEFAULT(unixepoch()+259200),completed_at INTEGER,cancelled_at INTEGER);
CREATE TABLE industrial_user_activity(discord_user_id INTEGER PRIMARY KEY REFERENCES industrial_users(discord_user_id),last_active_at INTEGER NOT NULL DEFAULT(unixepoch()),command_count INTEGER NOT NULL DEFAULT 1);
CREATE TABLE industrial_ai_accounts(actor_id INTEGER PRIMARY KEY REFERENCES industrial_actors(id),credits INTEGER NOT NULL DEFAULT 0 CHECK(credits>=0),updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_ai_cash_events(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),event_type TEXT NOT NULL,amount INTEGER NOT NULL,balance_after INTEGER NOT NULL CHECK(balance_after>=0),reference_type TEXT,reference_id INTEGER,created_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_ai_production(actor_id INTEGER PRIMARY KEY REFERENCES industrial_actors(id),resource_type TEXT NOT NULL,rate_per_hour INTEGER NOT NULL DEFAULT 6,capacity INTEGER NOT NULL DEFAULT 300,total_produced INTEGER NOT NULL DEFAULT 0,production_progress INTEGER NOT NULL DEFAULT 0,last_production_at INTEGER NOT NULL DEFAULT(unixepoch()),updated_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_ai_supply_purchases(id INTEGER PRIMARY KEY AUTOINCREMENT,buyer_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),producer_actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),operator_actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),resource_type TEXT NOT NULL,quantity INTEGER NOT NULL,total_price INTEGER NOT NULL,transport_id INTEGER REFERENCES industrial_transports(id),request_id TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_resource_events(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL REFERENCES industrial_actors(id),actor_type TEXT NOT NULL CHECK(actor_type IN('player','ai')),event_type TEXT NOT NULL,resource_type TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>0),created_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,transaction_type TEXT NOT NULL,monetary_effect TEXT NOT NULL CHECK(monetary_effect IN('source','transfer','sink','none')),actor_id INTEGER REFERENCES industrial_actors(id),counterparty_actor_id INTEGER REFERENCES industrial_actors(id),resource_type TEXT,quantity INTEGER CHECK(quantity IS NULL OR quantity>=0),credits INTEGER CHECK(credits IS NULL OR credits>=0),reference_type TEXT,reference_id INTEGER,metadata TEXT NOT NULL DEFAULT '{}',created_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE TABLE industrial_mine_upgrade_requests(request_id TEXT PRIMARY KEY,owner_discord_user_id INTEGER NOT NULL REFERENCES industrial_users(discord_user_id),upgrade_type TEXT NOT NULL,previous_level INTEGER NOT NULL,new_level INTEGER NOT NULL,upgrade_cost INTEGER NOT NULL,wallet_balance INTEGER NOT NULL,created_at INTEGER NOT NULL DEFAULT(unixepoch()));
CREATE INDEX industrial_inventory_owner_idx ON industrial_inventory(owner_discord_user_id,resource_type);
CREATE INDEX industrial_transports_arrival_idx ON industrial_transports(status,arrival_at);
CREATE INDEX industrial_contracts_open_idx ON industrial_contracts(status,expires_at);
CREATE INDEX industrial_transactions_actor_idx ON industrial_transactions(actor_id,created_at);
