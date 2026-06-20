import discord
from discord.ext import commands, tasks
import asyncio
import os
import random
import json
import io
from collections import defaultdict
from keep_alive import keep_alive
from dotenv import load_dotenv
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# Récupération des IDs des salons de logs depuis les variables d'environnement
LOG_MODERATION_CHANNEL_ID = os.getenv("1515780858483576923")
LOG_GIVEAWAY_CHANNEL_ID = os.getenv("1515781002817966341")
LOG_GENERAL_CHANNEL_ID = os.getenv("1515781072783151314")

# Intents du bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.messages = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- Variables globales pour la persistance des données ---
# Les données seront chargées depuis data.json
warns = {}
punitions = {}
morse_punitions = {}
mutes = {}
silenced_users = {}
coins = defaultdict(int)
giveaway_data = {}
giveaway_tasks = {}
daily_cooldowns = {}    # str(user_id) -> ISO datetime
work_cooldowns = {}     # str(user_id) -> ISO datetime
active_bj = {}          # (guild_id, user_id) -> BlackjackGame
poker_games = {}        # guild_id -> PokerGame

# ── Systèmes avancés ──────────────────────────────────────────────────
CRYPTO_BASE = {'BTC': 45000, 'ETH': 3000, 'DOGE': 15, 'SOL': 12000, 'XRP': 60}
CRYPTO_DISPLAY = {
    'BTC': 'Bitcoin 🟠', 'ETH': 'Ethereum 🔷',
    'DOGE': 'Dogecoin 🐕', 'SOL': 'Solana 🟣', 'XRP': 'Ripple 🔵'
}
CRYPTO_SYMBOLS = list(CRYPTO_BASE.keys())
# Salon où poster les news crypto (pumps/dumps). 0 = désactivé.
NEWS_CRYPTO_CHANNEL_ID = 1517799380999078020
CRYPTO_NEWS_UP = [
    "🚀 {name} s'envole ! Une grande institution vient d'investir massivement.",
    "📈 {name} en plein boom : l'adoption explose chez les commerçants.",
    "🔥 {name} bat un nouveau record, les investisseurs affluent !",
    "🎉 Une annonce majeure propulse {name} vers le sommet !",
    "💎 Les baleines accumulent {name} : le cours grimpe en flèche.",
    "🌕 {name} part en mode \"to the moon\", accrochez-vous !",
    "🤑 Ceux qui ont HODL {name} se frottent les mains aujourd'hui.",
    "🏦 Une banque centrale ajoute {name} à ses réserves, le marché s'emballe.",
    "📱 Une app virale intègre {name} comme moyen de paiement.",
    "🐳 Une baleine vient d'acheter pour des millions de {name}.",
    "🦍 Les apes sont de sortie : {name} explose à la hausse !",
    "💸 Elon a tweeté sur {name}... évidemment ça pompe.",
    "🧠 Les \"experts\" qui disaient de vendre {name} pleurent maintenant.",
    "🩳 Shorters liquidés en masse : {name} continue de grimper.",
    "🎯 {name} casse sa résistance et s'envole.",
    "🌐 Adoption mondiale en hausse : {name} séduit les institutions.",
    "🔓 Un partenariat surprise fait décoller {name}.",
    "📊 Volume record sur {name} : le marché est en feu.",
    "🥳 Les holders de {name} ouvrent le champagne.",
    "⚡ {name} surfe sur une vague d'achats incontrôlable.",
    "🤝 Un géant de la tech annonce une intégration de {name}.",
    "💰 FOMO généralisé : tout le monde se rue sur {name}.",
    "📰 \"{name} va remplacer le dollar\" titre un média... le cours s'emballe.",
    "🛒 De plus en plus de boutiques acceptent {name}.",
    "🔝 {name} dans le top des cryptos qui performent cette semaine.",
    "🦅 Breakout confirmé : {name} décolle proprement.",
    "😎 Diamond hands récompensés, les paper hands de {name} en PLS.",
    "🎢 {name} monte si vite que même les bulls ont le vertige.",
    "🏆 {name} élue crypto de l'année par une bande de degens sur Twitter.",
    "💹 Les analystes relèvent leurs objectifs sur {name}.",
    "🔋 Énergie haussière maximale sur {name}.",
    "🎁 Airdrop surprise : la hype fait grimper {name}.",
    "🧨 Short squeeze brutal : {name} pulvérise les vendeurs.",
    "🌟 Une célébrité annonce détenir du {name}, le cours s'envole.",
    "📈 Green candle de folie sur {name}, les graphiques sont magnifiques.",
    "🤴 {name} reprend son trône, longue vie au roi.",
    "🚄 TGV haussier : {name} ne s'arrête plus.",
    "💪 {name} montre les muscles et écrase la résistance.",
    "🪙 Pénurie de {name} sur les exchanges : le prix s'envole.",
    "🎇 Feu d'artifice sur {name} : pump spectaculaire.",
    "🦄 {name} fait un move de licorne, totalement irréel.",
    "🧲 {name} aimante les capitaux, la hausse s'accélère.",
    "🏄 Tout le monde surfe la vague {name} aujourd'hui.",
    "🔆 Sentiment ultra-bullish : {name} brille.",
    "🤗 Même ta grand-mère veut acheter du {name} maintenant.",
    "🚀 Houston, on a un décollage : {name} part en orbite.",
    "💎🙌 Les vrais ont tenu {name}, et ça paye.",
    "📡 Signal d'achat partout : {name} s'enflamme.",
    "🥂 Bull run confirmé sur {name}, santé les holders !",
    "🏦 Wall Street découvre {name} et panique d'en vouloir.",
]
CRYPTO_NEWS_DOWN = [
    "📉 {name} dévisse ! Une régulation surprise fait paniquer le marché.",
    "🐻 {name} s'effondre : vague de ventes massives.",
    "⚠️ {name} chute après une faille de sécurité signalée.",
    "💥 Krach soudain sur {name} : les traders fuient.",
    "🩸 Sell-off généralisé : {name} plonge dans le rouge.",
    "🪦 RIP les portefeuilles : {name} se fait massacrer.",
    "😭 Ceux qui ont acheté le top de {name} pleurent ce soir.",
    "🧻🙌 Paper hands en panique : {name} dégringole.",
    "🚽 {name} part dans les toilettes, tirez la chasse.",
    "📛 Une enquête vise {name}, le cours s'écroule.",
    "🤡 Les \"to the moon\" d'hier sur {name}... c'était la lune en carton.",
    "💸 {name} fait fondre les économies des holders.",
    "🐋 Une baleine dump tout son {name}, panique totale.",
    "❄️ Crypto winter pour {name} : froid glacial sur les cours.",
    "🔻 Support cassé : {name} chute sans filet.",
    "🥶 {name} se gèle en plein vol, atterrissage brutal.",
    "😱 Et ça baisse encore : {name} en chute libre.",
    "🧨 Liquidations en cascade : les longs sur {name} explosent.",
    "🪤 Bull trap parfait : {name} piège les acheteurs avant de plonger.",
    "📰 Un média annonce la \"mort de {name}\", le marché flippe.",
    "🛑 Stop tout : {name} s'écroule d'un coup.",
    "🤮 Dump écœurant sur {name}, le rouge domine.",
    "🦴 Plus rien à gratter : {name} se fait dépouiller.",
    "💀 {name} flirte avec ses plus bas, ambiance cimetière.",
    "🌊 Vague de ventes : {name} se noie.",
    "🏳️ Capitulation : les holders de {name} jettent l'éponge.",
    "🎢 Descente vertigineuse pour {name}, ça secoue.",
    "🙈 Personne ne veut regarder son portefeuille {name} aujourd'hui.",
    "🧯 Panique sur {name} : tout le monde veut sortir.",
    "📉 Red candle de l'enfer sur {name}.",
    "🐍 Rug pull ? Non, juste {name} qui fait du {name}... ça plonge.",
    "🫠 {name} se liquéfie sous la pression vendeuse.",
    "🪨 {name} coule comme une pierre.",
    "😬 Aïe : {name} efface des semaines de gains en une nuit.",
    "🥊 Le marché met un uppercut à {name}.",
    "🚨 Alerte rouge : {name} s'enfonce.",
    "🧊 Refroidissement brutal pour {name}, les bulls KO.",
    "💔 Cœurs brisés chez les fans de {name}.",
    "🪙 Trop de {name} à vendre, pas assez d'acheteurs : ça s'écroule.",
    "🦤 {name} fait le dodo : extinction de la hausse.",
    "📦 Les institutionnels larguent {name} en masse.",
    "🕳️ {name} tombe dans un trou noir, aucune résistance ne tient.",
    "🤕 Lendemain de pump douloureux : {name} corrige sévèrement.",
    "🧂 Beaucoup de larmes salées chez les traders de {name}.",
    "⛓️ {name} enchaîne les rouges, le moral est au plus bas.",
    "🪂 Sans parachute : {name} chute libre.",
    "🐌 Le rebond de {name} ? On l'attend toujours... pendant que ça baisse.",
    "🫥 {name} disparaît des radars haussiers.",
    "📉 \"Buy the dip\" qu'ils disaient... le dip de {name} a un sous-sol.",
    "💩 Soyons honnêtes : {name} c'est la cata aujourd'hui.",
]
SHOP_ITEMS = {
    1: {'name': '🍀 Porte-bonheur',       'price': 500,  'desc': 'Daily = 650 coins', 'unique': True},
    2: {'name': '⚒️ Équipement Pro',      'price': 1000, 'desc': 'Travail : 50–400 coins', 'unique': True},
    3: {'name': '🛡️ Bouclier Anti-Vol',  'price': 800,  'desc': 'Bloque le prochain vol subi', 'unique': False},
    4: {'name': '🎟️ Ticket à gratter',   'price': 200,  'desc': '1 ticket à gratter', 'unique': False},
    5: {'name': '💼 Pack ×5 Tickets',    'price': 5000,  'desc': '5 tickets à gratter', 'unique': False},
    6: {'name': '🏭 Amélioration Usine', 'price': 3000, 'desc': '+15% production usine', 'unique': True},
    7: {'name': '📈 Cours de Trading',   'price': 2000, 'desc': '+15% gains ventes crypto', 'unique': True},
}
JOBS = {
    'hacker': {'name': '💻 Hacker',   'action' : '`!hacker @cible', 'desc': 'Vole la crypto des autres (cd 1h)'},
    'mineur':  {'name': '⛏️ Mineur',   'action': '`!miner`',         'desc': 'Mine 50–200 coins par heure'},
    'escroc':  {'name': '🎭 Escroc',   'action': 'Bonus passif',     'desc': '+20% succès sur `!voler`'},
    'gardien': {'name': '🛡️ Gardien', 'action': 'Bonus passif',     'desc': '-50% pertes si quelqu\'un vous vole'},
}
RACE_DRIVERS_BASE = [
    {'name': 'Rapido 🔴',   'wins': 5, 'races': 10},
    {'name': 'FlashX 🔵',   'wins': 4, 'races': 10},
    {'name': 'Tonnerre ⚡',  'wins': 3, 'races': 10},
    {'name': 'Turbo 🟡',    'wins': 2, 'races': 10},
    {'name': 'Éclair 🟢',   'wins': 1, 'races': 10},
]
# Nb de trèfles → (poids, gain, label)
SCRATCH_PRIZES = {
    0: (18, 0,      "😢 Aucun trèfle... Rien cette fois."),
    1: (40, 500,    "🍀 1 trèfle — +500 coins !"),
    2: (20, 2000,   "🍀🍀 2 trèfles — +2 000 coins !"),
    3: (15, 5000,   "🍀🍀🍀 3 trèfles — +5 000 coins !"),
    4: ( 5, 10000,  "🍀🍀🍀🍀 4 trèfles — +10 000 coins !"),
    5: ( 2, 100000, "🍀🍀🍀🍀🍀 5 trèfles — +100 000 coins !! 🎉"),
}

crypto_prices    = dict(CRYPTO_BASE)
price_history    = {}   # str(symbol) -> [float, ...]  (30 derniers points)
crypto_trends    = {}   # str(symbol) -> float  (tendance/momentum courant)
# Volatilité propre à chaque crypto (écart-type du bruit par tick)
CRYPTO_VOL = {'BTC': 0.012, 'ETH': 0.018, 'SOL': 0.025, 'XRP': 0.030, 'DOGE': 0.045}
crypto_holdings  = {}   # str(uid) -> {symbol: float}
safes            = {}   # str(uid) -> int
factories        = {}   # str(uid) -> {'workers': int, 'last': ISO, 'upgraded': bool}
jobs_data        = {}   # str(uid) -> {'job': str}
owned_items      = {}   # str(uid) -> {str(item_id): int}
theft_cooldowns  = {}   # str(uid) -> ISO
miner_cooldowns  = {}   # str(uid) -> ISO
hacker_cooldowns = {}   # str(uid) -> ISO
risque_cooldowns = {}   # str(uid) -> ISO  (cooldown 3h)
rob_cooldowns    = {}   # str(uid) -> ISO  (cooldown 12h)
race_bets        = {}   # str(uid) -> {'driver': int, 'amount': int}
race_drivers_live = [dict(d) for d in RACE_DRIVERS_BASE]
race_accepting    = False
tournaments      = {}   # str(guild_id) → tournament dict
teams            = {}   # str(team_id) -> {name, leader, members:[uid,...], treasury:int, created:ISO}
user_team        = {}   # str(uid) -> str(team_id)
team_state       = {'competition_open': False, 'next_id': 1}
disabled_cmds    = set()  # noms de commandes désactivées
cmd_role_perms   = {}   # name -> [role_id, ...] (allowed roles, empty=all)

# ── Configuration prix / mises (modifiable par !prix_casino) ─────────────
BOT_OWNER_ID = 1056848438270115900   # happy_gt3 — créateur du bot
MAX_FACTORY_WORKERS = 10
DEFAULT_FACTORY_COSTS = [500, 1000, 2000, 5000, 7500, 10000, 15000, 25000, 55000, 100000]
FACTORY_HIRE_COOLDOWN_HOURS = 24
RISQUE_COOLDOWN_HOURS = 3
GAMES_WITH_LIMITS = ['slots', 'coinflip', 'roulette', 'bj', 'duel', 'mines', 'poker', 'course']

# Cooldowns par commande (en heures) — modifiable via !cooldown
DEFAULT_COOLDOWNS_H = {
    'daily':     24,
    'travail':   1,
    'risque':    3,
    'voler':     0.5,
    'miner':     0.25,
    'hacker':    1,
    'rob':       12,
    'embaucher': 24,
}

casino_config = {
    'shop_prices':   {},  # str(item_id) -> int
    'factory_costs': [],  # liste de 10 prix (override DEFAULT_FACTORY_COSTS)
    'min_bets':      {},  # str(game) -> int
    'max_bets':      {},  # str(game) -> int
    'cooldowns':     {},  # str(cmd) -> heures (override DEFAULT_COOLDOWNS_H)
}


def is_bot_owner(user) -> bool:
    """Vérifie si l'utilisateur est le créateur du bot (happy_gt3)."""
    return getattr(user, 'id', None) == BOT_OWNER_ID


def cooldown_h(cmd: str) -> float:
    """Retourne le cooldown actuel d'une commande en heures (override config ou défaut)."""
    overrides = casino_config.get('cooldowns', {}) or {}
    if cmd in overrides:
        return float(overrides[cmd])
    return float(DEFAULT_COOLDOWNS_H.get(cmd, 0))


def _shop_price(item_id: int) -> int:
    """Retourne le prix actuel d'un item (override config ou défaut)."""
    return casino_config['shop_prices'].get(str(item_id), SHOP_ITEMS[item_id]['price'])


def _check_bet_limits(game: str, mise: int):
    """Vérifie min/max bet. Retourne un message d'erreur ou None."""
    mn = casino_config['min_bets'].get(game)
    mx = casino_config['max_bets'].get(game)
    if mn is not None and mise < mn:
        return f"❌ Mise minimum pour ce jeu : **{mn:,} coins**."
    if mx is not None and mise > mx:
        return f"❌ Mise maximum pour ce jeu : **{mx:,} coins**."
    return None


def _user_team_id(user_id):
    """Retourne l'ID du team de l'utilisateur ou None."""
    return user_team.get(str(user_id))


def _team_of(user_id):
    """Retourne le dict de team de l'utilisateur ou None."""
    tid = _user_team_id(user_id)
    return teams.get(tid) if tid else None

# Nom du fichier de données
DATA_FILE = 'data.json'

# --- Fonctions de chargement et de sauvegarde des données ---
def load_data():
    global warns, mutes, silenced_users, coins, giveaway_data, daily_cooldowns, work_cooldowns
    global crypto_prices, price_history, crypto_trends, crypto_holdings, safes, factories, jobs_data, owned_items
    global theft_cooldowns, miner_cooldowns, hacker_cooldowns, risque_cooldowns, rob_cooldowns
    global race_bets, race_drivers_live, race_accepting
    global teams, user_team, disabled_cmds, cmd_role_perms
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                data = json.load(f)

                # S'assurer que les clés sont des chaînes pour les guild_id
                warns = {int(k): v for k, v in data.get('warns', {}).items()}

                loaded_mutes_raw = data.get('mutes', {})
                mutes_temp = {}
                for g_id_str, users_data in loaded_mutes_raw.items():
                    guild_id = int(g_id_str)
                    mutes_temp[guild_id] = {}
                    for u_id_str, mute_info in users_data.items():
                        user_id = int(u_id_str)
                        if "end_time" in mute_info and mute_info["end_time"]:
                            try:
                                mute_info["end_time"] = datetime.fromisoformat(mute_info["end_time"])
                            except ValueError:
                                mute_info["end_time"] = None
                        mutes_temp[guild_id][user_id] = mute_info
                mutes = mutes_temp

                loaded_silenced_raw = data.get('silenced_users', {})
                silenced_users_temp = {}
                for g_id_str, user_ids_list in loaded_silenced_raw.items():
                    guild_id = int(g_id_str)
                    silenced_users_temp[guild_id] = [int(uid) for uid in user_ids_list]
                silenced_users = silenced_users_temp

                loaded_giveaway_data = data.get('giveaway_data', {})
                giveaway_data_temp = {}
                for g_id_str, gw_info in loaded_giveaway_data.items():
                    if "end_time" in gw_info and gw_info["end_time"]:
                        try:
                            gw_info["end_time"] = datetime.fromisoformat(gw_info["end_time"])
                        except ValueError:
                            gw_info["end_time"] = None
                    giveaway_data_temp[int(g_id_str)] = gw_info
                giveaway_data = giveaway_data_temp

                loaded_coins = data.get('coins', {})
                coins = defaultdict(int, {int(k): v for k, v in loaded_coins.items()})

                daily_cooldowns  = data.get('daily_cooldowns', {})
                work_cooldowns   = data.get('work_cooldowns', {})
                crypto_prices    = data.get('crypto_prices', dict(CRYPTO_BASE))
                price_history    = data.get('price_history', {})
                crypto_trends    = data.get('crypto_trends', {})
                crypto_holdings  = data.get('crypto_holdings', {})
                safes            = data.get('safes', {})
                factories        = data.get('factories', {})
                jobs_data        = data.get('jobs_data', {})
                owned_items      = data.get('owned_items', {})
                theft_cooldowns  = data.get('theft_cooldowns', {})
                miner_cooldowns  = data.get('miner_cooldowns', {})
                hacker_cooldowns = data.get('hacker_cooldowns', {})
                risque_cooldowns = data.get('risque_cooldowns', {})
                rob_cooldowns    = data.get('rob_cooldowns', {})
                race_bets        = data.get('race_bets', {})
                race_drivers_live = data.get('race_drivers_live', [dict(d) for d in RACE_DRIVERS_BASE])
                race_accepting   = data.get('race_accepting', False)
                teams            = data.get('teams', {})
                user_team        = data.get('user_team', {})
                ts = data.get('team_state', {})
                team_state['competition_open'] = ts.get('competition_open', False)
                team_state['next_id'] = ts.get('next_id', 1)
                disabled_cmds    = set(data.get('disabled_cmds', []))
                cmd_role_perms   = data.get('cmd_role_perms', {})
                loaded_cfg = data.get('casino_config', {})
                if isinstance(loaded_cfg, dict):
                    casino_config['shop_prices']   = loaded_cfg.get('shop_prices', {}) or {}
                    casino_config['factory_costs'] = loaded_cfg.get('factory_costs', []) or []
                    casino_config['min_bets']      = loaded_cfg.get('min_bets', {}) or {}
                    casino_config['max_bets']      = loaded_cfg.get('max_bets', {}) or {}
                    casino_config['cooldowns']     = loaded_cfg.get('cooldowns', {}) or {}

                print("Données chargées avec succès.")
            except json.JSONDecodeError:
                print("Erreur de décodage JSON. Le fichier de données est corrompu ou vide. Initialisation des données.")
                warns = {}
                mutes = {}
                silenced_users = {}
                coins = defaultdict(int)
                giveaway_data = {}
            except Exception as e:
                print(f"Erreur inattendue lors du chargement des données: {e}. Initialisation des données.")
                warns = {}
                mutes = {}
                silenced_users = {}
                coins = defaultdict(int)
                giveaway_data = {}
    else:
        print("Fichier de données non trouvé. Initialisation des données.")
        warns = {}
        mutes = {}
        silenced_users = {}
        coins = defaultdict(int)
        giveaway_data = {}

def save_data():
    data_to_save = {
        'warns': {str(k): v for k, v in warns.items()},
        'coins': dict(coins),
    }

    mutes_for_save = {}
    for guild_id, guild_mutes in mutes.items():
        mutes_for_save[str(guild_id)] = {}
        for user_id, mute_info in guild_mutes.items():
            info_copy = mute_info.copy()
            if "end_time" in info_copy and info_copy["end_time"]:
                info_copy["end_time"] = info_copy["end_time"].isoformat()
            mutes_for_save[str(guild_id)][str(user_id)] = info_copy
    data_to_save['mutes'] = mutes_for_save

    silenced_for_save = {str(k): v for k, v in silenced_users.items()}
    data_to_save['silenced_users'] = silenced_for_save

    giveaway_for_save = {}
    for guild_id, gw_info in giveaway_data.items():
        info_copy = gw_info.copy()
        if "end_time" in info_copy and info_copy["end_time"]:
            info_copy["end_time"] = info_copy["end_time"].isoformat()
        giveaway_for_save[str(guild_id)] = info_copy
    data_to_save['giveaway_data'] = giveaway_for_save

    data_to_save['daily_cooldowns']  = daily_cooldowns
    data_to_save['work_cooldowns']   = work_cooldowns
    data_to_save['crypto_prices']    = crypto_prices
    data_to_save['price_history']    = price_history
    data_to_save['crypto_trends']    = crypto_trends
    data_to_save['crypto_holdings']  = crypto_holdings
    data_to_save['safes']            = safes
    data_to_save['factories']        = factories
    data_to_save['jobs_data']        = jobs_data
    data_to_save['owned_items']      = owned_items
    data_to_save['theft_cooldowns']  = theft_cooldowns
    data_to_save['miner_cooldowns']  = miner_cooldowns
    data_to_save['hacker_cooldowns'] = hacker_cooldowns
    data_to_save['risque_cooldowns'] = risque_cooldowns
    data_to_save['rob_cooldowns']    = rob_cooldowns
    data_to_save['race_bets']        = race_bets
    data_to_save['race_drivers_live'] = race_drivers_live
    data_to_save['race_accepting']   = race_accepting
    data_to_save['teams']            = teams
    data_to_save['user_team']        = user_team
    data_to_save['team_state']       = dict(team_state)
    data_to_save['disabled_cmds']    = list(disabled_cmds)
    data_to_save['cmd_role_perms']   = cmd_role_perms
    data_to_save['casino_config']    = casino_config

    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data_to_save, f, indent=4)
        print("Données sauvegardées avec succès.")
    except Exception as e:
        print(f"Erreur lors de la sauvegarde des données: {e}")

# --- Fonction utilitaire pour envoyer des messages de log ---
async def send_log_message(guild, channel_id, title, description, color, fields=None):
    if not channel_id:
        print(f"L'ID du salon de logs n'est pas configuré pour le type : {title}.")
        return

    log_channel = guild.get_channel(int(channel_id))
    if not log_channel:
        print(f"Le salon de logs avec l'ID {channel_id} est introuvable pour le log '{title}'.")
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    if fields:
        for name, value, inline in fields:
            if not isinstance(value, str):
                value = str(value)
            embed.add_field(name=name, value=value, inline=inline)

    embed.set_footer(text=f"Bot ID: {bot.user.id}")

    try:
        await log_channel.send(embed=embed)
    except discord.Forbidden:
        print(f"Le bot n'a pas la permission d'envoyer des messages dans le salon de logs ({log_channel.name}).")
    except Exception as e:
        print(f"Erreur lors de l'envoi du message de log dans {log_channel.name}: {e}")

# --- Tâche en arrière-plan pour vérifier les mutes expirés ---
@tasks.loop(minutes=1)
async def check_mutes():
    await bot.wait_until_ready()
    current_time = datetime.now()

    guild_ids_to_unmute = []

    # Faire une copie du dictionnaire pour permettre des modifications pendant l'itération
    for guild_id, guild_mutes in list(mutes.items()):
        guild = bot.get_guild(guild_id)

        if not guild:
            guild_ids_to_unmute.append(guild_id)
            continue

        mute_role = discord.utils.get(guild.roles, name="Muted")
        if not mute_role:
            print(f"Rôle 'Muted' non trouvé pour la guilde {guild.name}. Skipping mute check.")
            continue

        for user_id, mute_info in list(guild_mutes.items()):
            end_time = mute_info.get("end_time")
            if end_time and current_time >= end_time:
                member = guild.get_member(user_id)
                if member and mute_role in member.roles:
                    try:
                        await member.remove_roles(mute_role, reason="Fin du mute temporaire (vérification automatique)")
                        fields_unmute_auto_log = [
                            ("Utilisateur unmute", member.mention, True),
                            ("Raison", "Fin du mute automatique", False),
                            ("Durée initiale", str(end_time - current_time), True)
                        ]
                        await send_log_message(guild, LOG_MODERATION_CHANNEL_ID, "🔊 Auto-Unmute (Fin de durée)", f"{member.mention} a été unmute automatiquement.", discord.Color.green(), fields_unmute_auto_log)

                    except Exception as e:
                        print(f"Erreur lors de l'unmute de {member.display_name} (ID: {user_id}): {e}")

                # Supprimer l'utilisateur du dictionnaire de mutes, qu'il ait été unmute ou non
                # (si le membre a quitté le serveur, ou si le rôle a été enlevé manuellement)
                if user_id in mutes[guild_id]:
                    del mutes[guild_id][user_id]

        if not mutes[guild_id]:
            guild_ids_to_unmute.append(guild_id)

    # Nettoyer les guildes vides
    for guild_id in guild_ids_to_unmute:
        if guild_id in mutes:
            del mutes[guild_id]

    save_data()


# --- Fin de la tâche de vérification des mutes ---

@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")
    load_data()
    if not check_mutes.is_running():
        check_mutes.start()
    if not update_crypto_prices.is_running():
        update_crypto_prices.start()
    print("Bot prêt et fonctionnel !")


# ── Liste des commandes toujours autorisées (anti-bricking) ──────────────
ALWAYS_ALLOWED_CMDS = {'gestion', 'permission', 'cooldown', 'cd', 'aide'}


@bot.check
async def _global_command_gate(ctx):
    """Vérifie : (1) commande désactivée, (2) restrictions de rôle, (3) bypass owner/admin."""
    cmd_name = ctx.command.name if ctx.command else ''
    # Le créateur passe toujours
    if is_bot_owner(ctx.author):
        return True
    # Les commandes critiques de gestion sont toujours actives pour les admins
    if cmd_name in ALWAYS_ALLOWED_CMDS and ctx.guild and ctx.author.guild_permissions.administrator:
        return True
    # Commande désactivée par !gestion
    if cmd_name in disabled_cmds:
        try:
            await ctx.send(f"🚫 La commande `!{cmd_name}` est actuellement **désactivée** par un administrateur.")
        except Exception:
            pass
        return False
    # Restrictions de rôle (!permission)
    allowed_roles = cmd_role_perms.get(cmd_name)
    if allowed_roles and ctx.guild:
        # Les admins du serveur passent toujours
        if ctx.author.guild_permissions.administrator:
            return True
        user_role_ids = {r.id for r in ctx.author.roles}
        if not (user_role_ids & set(allowed_roles)):
            try:
                await ctx.send(
                    f"🔒 La commande `!{cmd_name}` est restreinte à certains rôles. "
                    f"Vous n'avez pas la permission de l'utiliser."
                )
            except Exception:
                pass
            return False
    return True


bot.remove_command("help")

# ── Usages des commandes (pour les messages d'erreur) ────────────────────
COMMAND_USAGE = {
    'coins':         '`!coins` — Voir votre solde\n`!coins @membre` — Voir le solde d\'un autre',
    'give':          '`!give @membre <montant>`\nEx : `!give @Ami 1000`',
    'roulette':      '`!roulette <mise|all> <choix>`\nChoix : `rouge` `noir` `pair` `impair` ou un numéro `0-36`\nEx : `!roulette 200 rouge` · `!roulette all 15`',
    'slots':         '`!slots <mise|all>`\nEx : `!slots 150` · `!slots all`',
    'bj':            '`!bj <mise|all>` — Démarrer une partie (jouez ensuite avec les boutons)\nEx : `!bj 100` · `!bj all`',
    'blackjack':     '`!bj <mise|all>` — Démarrer une partie (boutons : Tirer / Rester / Doubler / Abandonner)',
    'coinflip':      '`!coinflip <mise|all> <pile|face>`\nEx : `!coinflip 100 pile` · `!cf all face`',
    'cf':            '`!coinflip <mise|all> <pile|face>`\nEx : `!cf 500 pile`',
    'duel':          '`!duel @membre <mise|all>`\nEx : `!duel @Joueur 500`',
    'mines':         '`!mines <mise|all>`\nEx : `!mines 300` · `!mines all`',
    'poker':         '`!poker start <ante>` — Créer une table\n*(Tout le reste se joue avec les boutons : Rejoindre / Démarrer / Fold / Call / Check / Raise / All-in / Voir mes cartes)*',
    'graphique':     '`!graphique <SYM>`\nSymboles disponibles : `BTC` `ETH` `DOGE` `SOL` `XRP`\nEx : `!graphique BTC`',
    'chart':         '`!graphique <SYM>` — Ex : `!graphique ETH`',
    'courbe':        '`!graphique <SYM>` — Ex : `!graphique DOGE`',
    'acheter_crypto':'`!acheter_crypto <SYM> <coins à dépenser>`\nEx : `!acheter_crypto BTC 1000`',
    'vendre_crypto': '`!vendre_crypto <SYM> <quantité|tout>`\nEx : `!vendre_crypto ETH 0.5` · `!vendre_crypto BTC tout`',
    'choisir_metier':'`!choisir_metier <metier>`\nMétiers : `hacker` `mineur` `escroc` `gardien` `trader`',
    'hacker':        '`!hacker @membre` — Voler la crypto d\'un joueur\n*(Réservé au métier Hacker)*',
    'voler':         '`!voler @membre` — Voler le coffre d\'un joueur (5-20% du coffre)\nEx : `!voler @Riche`',
    'rob':           '`!rob @membre` — Voler le cash d\'un joueur (55% réussite, 5-15% du cash · -0 à 300 si raté)\nCooldown 12h',
    'coffre':        '`!coffre` — Ouvrir le coffre (boutons Déposer / Retirer)',
    'team':          '`!team` — Système de clubs (créer / rejoindre / quitter / trésorerie)',
    'gdt':           '`!gdt` *(Admin)* — Gérer la compétition inter-clubs (ouvrir/fermer/récompenser)',
    'gestion':       '`!gestion` *(Owner/Admin)* — Activer/désactiver n\'importe quelle commande',
    'permission':    '`!permission` *(Owner)* — Restreindre une commande à certains rôles Discord',
    'cooldown':      '`!cooldown` ou `!cd` *(Owner/Admin)* — Modifier les cooldowns des commandes',
    'cd':            '`!cd` — Voir/modifier les cooldowns',
    'acheter':       '`!acheter <n°>` — Numéro de l\'item affiché dans `!shop`\nEx : `!acheter 1`',
    'parier':        '`!parier <n°pilote> <mise|all>`\nVoir les pilotes avec `!course`\nEx : `!parier 3 500` · `!parier 1 all`',
    'bet':           '`!parier <n°pilote> <mise|all>`\nEx : `!parier 2 1000`',
    'addcoins':      '`!addcoins @membre <montant>` *(Admin)*\nEx : `!addcoins @Joueur 5000`',
    'removecoins':   '`!removecoins @membre <montant>` *(Admin)*\nEx : `!removecoins @Joueur 200`',
    'prix_casino':   '`!prix_casino` *(Admin)* — Modifier prix shop/usine et limites de mise des jeux',
    'warn':          '`!warn @membre <raison>`\nEx : `!warn @Joueur Spam répété`',
    'mute':          '`!mute @membre <durée> <raison>`\nDurées : `10m` `1h` `1j`\nEx : `!mute @Joueur 1h Flood`',
    'unmute':        '`!unmute @membre`',
    'ban':           '`!ban @membre <raison>`\nEx : `!ban @Joueur Comportement toxique`',
    'unban':         '`!unban <ID ou @membre>`',
    'clear':         '`!clear <nombre>` — Supprimer des messages\nEx : `!clear 20`',
    'rename':        '`!rename @membre <nouveau pseudo>`\nEx : `!rename @Joueur NouveauNom`',
    'giverole':      '`!giverole @membre <nom du rôle>`\nEx : `!giverole @Joueur VIP`',
    'sanctions':     '`!sanctions @membre`',
    'say':           '`!say <message>`',
    'dm':            '`!dm @membre <message>`',
}

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        usage = COMMAND_USAGE.get(ctx.command.name if ctx.command else '')
        embed = discord.Embed(
            title=f"❓ Argument manquant — `!{ctx.command.name if ctx.command else '?'}`",
            description=usage or "Tapez `!aide` pour voir les commandes disponibles.",
            color=0xe74c3c
        )
        embed.set_footer(text="<obligatoire>  •  [optionnel]  •  a|b = un choix parmi a ou b")
        return await ctx.send(embed=embed)

    if isinstance(error, commands.BadArgument):
        usage = COMMAND_USAGE.get(ctx.command.name if ctx.command else '')
        embed = discord.Embed(
            title=f"❓ Argument invalide — `!{ctx.command.name if ctx.command else '?'}`",
            description=usage or "Tapez `!aide` pour voir les commandes disponibles.",
            color=0xe67e22
        )
        embed.set_footer(text="Vérifiez la syntaxe ci-dessus et réessayez.")
        return await ctx.send(embed=embed)

    if isinstance(error, commands.CommandNotFound):
        return  # Ignorer silencieusement

    if isinstance(error, commands.CheckFailure):
        # Si le check global a déjà envoyé un message, on ne ré-envoie rien
        return

    if isinstance(error, commands.CommandOnCooldown):
        return  # Géré dans chaque commande individuellement

    # Erreur inattendue — propager
    raise error


# Commande aide
def _build_help_categories(ctx):
    """Retourne la liste des catégories d'aide disponibles selon les permissions."""
    has_admin = ctx.author.guild_permissions.administrator
    has_manage_messages = ctx.author.guild_permissions.manage_messages
    has_manage_nicknames = ctx.author.guild_permissions.manage_nicknames
    has_ban_members = ctx.author.guild_permissions.ban_members
    is_owner = is_bot_owner(ctx.author)

    cats = []
    cats.append(("home", "🏠 Accueil", "Page d'accueil de l'aide", None))
    cats.append(("info", "ℹ️ Aide & Infos",
                 "Commandes générales",
                 "`!aide` — Ce menu"))
    cats.append(("eco", "🪙 Économie de base",
                 "Solde, daily, travail, coffre, rob, etc.",
                 "`!coins` — Voir votre solde · `!coins @membre`\n"
                 "`!daily` — 500 coins/jour\n"
                 "`!travail` — Travailler (cooldown 1h)\n"
                 "`!risque` — Coup risqué x2 ou rien *(cooldown 3h)*\n"
                 "`!give @membre <montant>` — Donner des coins\n"
                 "`!coffre` — Coffre-fort (boutons Déposer/Retirer)\n"
                 "`!rob @membre` — Voler le cash (55% réussite · cooldown 12h)\n"
                 "`!classement` — Top 10 des plus riches"))
    cats.append(("casino", "🎰 Jeux de casino",
                 "Slots, blackjack, roulette, poker, course, gratter…",
                 "`!slots <mise>` — Machine à sous\n"
                 "`!coinflip <mise> <pile|face>` — Pile ou face\n"
                 "`!roulette <mise> <rouge|noir|pair|impair|0-36>`\n"
                 "`!bj <mise>` — Blackjack (boutons)\n"
                 "`!duel @membre <mise>` — Duel\n"
                 "`!mines <mise>` — Mines\n"
                 "`!poker start <ante>` — Poker (boutons)\n"
                 "`!course` — Course de voitures (boutons)\n"
                 "`!gratter` — Gratter un ticket (5 cases 🍀)"))
    cats.append(("crypto", "📈 Crypto-monnaies",
                 "Achat / vente / graphique",
                 "`!crypto` — Prix en temps réel\n"
                 "`!graphique <SYM>` — Sparkline\n"
                 "`!acheter_crypto <SYM> <coins>` — Acheter\n"
                 "`!vendre_crypto <SYM> <quantité|tout>` — Vendre"))
    cats.append(("metiers", "💼 Métiers & Actions",
                 "Hacker, mineur, escroc, gardien, trader",
                 "`!metier` — Voir métier actuel\n"
                 "`!choisir_metier <nom>` — Choisir\n"
                 "`!miner` — Miner *(Mineur)*\n"
                 "`!hacker @membre` — Voler crypto *(Hacker)*\n"
                 "`!voler @membre` — Voler le **coffre** (5-20%)"))
    cats.append(("usine", "🏭 Usine passive",
                 "Production automatique de coins",
                 f"`!usine` — Voir votre usine (boutons : embaucher / collecter)\n"
                 f"Max **{MAX_FACTORY_WORKERS} employés** · 1 embauche / **{FACTORY_HIRE_COOLDOWN_HOURS}h**"))
    cats.append(("shop", "🛒 Magasin & Tickets",
                 "Items et inventaire",
                 "`!shop` — Magasin (boutons d'achat)\n"
                 "`!inventaire` — Voir vos items"))
    cats.append(("team", "👥 Clubs / Teams",
                 "Créer ou rejoindre un club de joueurs",
                 "`!team` — Interface du club (créer / rejoindre / quitter / trésorerie)"))
    cats.append(("tournoi", "🏆 Tournois",
                 "Tournois solo ou par équipes (2v2 / 3v3 / 4v4 / 5v5)",
                 "`!tournois solo` · `2v2` · `3v3` · `4v4` · `5v5` *(Admin)*\n"
                 "`!tournoi_status` — Voir le bracket\n"
                 "Résultat : **les 2 capitaines** valident le même vainqueur\n"
                 "*(Admin)* `!win <n°>` — Trancher un match"))

    if has_manage_messages or has_ban_members:
        lines = []
        if has_manage_messages:
            lines.append("`!warn` `!mute` `!unmute` `!clear` `!silence` `!unsilence` `!sanctions`")
        if has_ban_members:
            lines.append("`!ban` `!unban`")
        cats.append(("mod", "⚖️ Modération",
                     "Warns, mutes, bans, etc.",
                     "\n".join(lines)))

    if has_manage_nicknames:
        cats.append(("nick", "✏️ Gestion des pseudos",
                     "Renommer un membre",
                     "`!rename @membre <nouveau pseudo>`"))

    if has_admin:
        cats.append(("admin", "⚙️ Administration",
                     "Outils admin du serveur",
                     "`!giveaway` `!cancelgiveaway`\n"
                     "`!addcoins @membre <n>` — Ajouter des coins\n"
                     "`!removecoins @membre <n>` — Retirer des coins\n"
                     "`!prix_casino` — Modifier prix shop/usine + mises min/max\n"
                     "`!gestion` — Activer/désactiver des commandes\n"
                     "`!cooldown` (`!cd`) — Modifier les cooldowns\n"
                     "`!ouvrir_course` / `!lancer_course` — Courses\n"
                     "`!gdt` — Compétitions inter-clubs\n"
                     "`!lock` / `!unlock` — Verrouiller un salon"))

    if is_owner:
        cats.append(("owner", "👑 Créateur du Bot",
                     "Commandes réservées à happy_gt3",
                     "`!say <message>` — Faire parler le bot\n"
                     "`!dm @membre <msg>` — Envoyer un MP\n"
                     "`!dmall <msg>` — MP à tous\n"
                     "`!construction` — Reconstruire le serveur\n"
                     "`!nuke` — Effacer tous les salons\n"
                     "`!permission` — Restreindre des commandes par rôle"))

    return cats


def _help_home_embed(ctx, cats):
    embed = discord.Embed(
        title="📋 Centre d'aide",
        description=(
            "Bienvenue dans le centre d'aide !\n\n"
            "**Sélectionnez une catégorie** dans le menu déroulant ci-dessous "
            "pour voir les commandes disponibles."
        ),
        color=0x00ff88
    )
    available = "\n".join([f"• {label}" for _, label, _, body in cats if body is not None])
    embed.add_field(name="📚 Catégories disponibles", value=available or "—", inline=False)
    embed.set_footer(text=f"Demandé par {ctx.author.display_name} • Préfixe : !")
    return embed


def _help_cat_embed(ctx, cats, key):
    cat = next((c for c in cats if c[0] == key), None)
    if not cat or cat[3] is None:
        return _help_home_embed(ctx, cats)
    _, label, desc, body = cat
    embed = discord.Embed(title=label, description=desc, color=0x00ff88)
    embed.add_field(name="Commandes", value=body, inline=False)
    embed.set_footer(text=f"Demandé par {ctx.author.display_name} • Préfixe : !")
    return embed


class HelpView(discord.ui.View):
    def __init__(self, ctx, cats):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cats = cats
        # Construction des options (Discord limite à 25)
        options = []
        for key, label, desc, _ in cats[:25]:
            options.append(discord.SelectOption(
                label=label[:100], description=desc[:100], value=key
            ))
        self.select = discord.ui.Select(
            placeholder="📂 Choisis une catégorie…",
            options=options, min_values=1, max_values=1
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "❌ Ce menu d'aide n'est pas pour vous. Tapez `!aide` pour en avoir un.",
                ephemeral=True
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        key = self.select.values[0]
        embed = _help_cat_embed(self.ctx, self.cats, key)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.command()
async def aide(ctx):
    cats = _build_help_categories(ctx)
    embed = _help_home_embed(ctx, cats)
    view = HelpView(ctx, cats)
    await ctx.send(embed=embed, view=view)

@bot.event
async def on_member_join(member):
    guild = member.guild
    role = discord.utils.get(guild.roles, name="Membre")
    if role:
        try:
            await member.add_roles(role)
            print(f"Rôle 'Membre' ajouté à {member.name}")
            fields = [
                ("Membre", member.mention, True),
                ("ID Membre", member.id, True),
                ("Rôle Attribué", role.name, False)
            ]
            await send_log_message(guild, LOG_GENERAL_CHANNEL_ID, "👋 Nouveau Membre", f"{member.mention} a rejoint le serveur.", discord.Color.blue(), fields)
        except discord.Forbidden:
            print(f"Permission insuffisante pour ajouter le rôle à {member.name}")
            fields = [
                ("Membre", member.mention, True),
                ("Rôle Attribué", role.name, False),
                ("Erreur", "Permissions insuffisantes pour le bot.", False)
            ]
            await send_log_message(guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Erreur Rôle Auto", f"Impossible d'ajouter le rôle 'Membre' à {member.mention}.", discord.Color.red(), fields)
        except Exception as e:
            print(f"Erreur en ajoutant le rôle à {member.name} : {e}")
            fields = [
                ("Membre", member.mention, True),
                ("Rôle Attribué", role.name, False),
                ("Erreur", str(e), False)
            ]
            await send_log_message(guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Erreur Rôle Auto", f"Une erreur est survenue lors de l'ajout du rôle 'Membre' à {member.mention}.", discord.Color.red(), fields)
    else:
        print("Le rôle 'Membre' n'existe pas dans ce serveur.")
        fields = [
            ("Serveur", guild.name, True),
            ("Erreur", "Le rôle 'Membre' n'existe pas.", False)
        ]
        await send_log_message(guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Rôle Manquant", "Le rôle 'Membre' n'a pas été trouvé pour l'attribution automatique.", discord.Color.dark_orange(), fields)

@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return

    description = f"Message de {message.author.mention} supprimé dans {message.channel.mention}."
    fields = [
        ("Auteur", message.author.display_name, True),
        ("Contenu", message.content if message.content else "*(Contenu non textuel ou vide)*", False),
        ("Canal", message.channel.name, True)
    ]
    await send_log_message(message.guild, LOG_MODERATION_CHANNEL_ID, "🗑️ Message Supprimé", description, discord.Color.light_grey(), fields)

@bot.event
async def on_member_update(before, after):
    if before.guild is None or after.guild is None:
        return

    if before.nick != after.nick:
        description = f"Le pseudo de {after.mention} a changé."
        fields = [
            ("Ancien pseudo", before.nick if before.nick else before.name, True),
            ("Nouveau pseudo", after.nick if after.nick else after.name, True)
        ]
        await send_log_message(after.guild, LOG_GENERAL_CHANNEL_ID, "✏️ Pseudo Modifié", description, discord.Color.blue(), fields)

    if before.roles != after.roles:
        added_roles = [role for role in after.roles if role not in before.roles]
        if added_roles:
            description = f"Rôle(s) ajouté(s) à {after.mention}."
            fields = [
                ("Utilisateur", after.mention, True),
                ("Rôle(s) ajouté(s)", ", ".join([role.name for role in added_roles]), False)
            ]
            await send_log_message(after.guild, LOG_GENERAL_CHANNEL_ID, "➕ Rôle Ajouté", description, discord.Color.dark_green(), fields)

        removed_roles = [role for role in before.roles if role not in after.roles]
        if removed_roles:
            description = f"Rôle(s) retiré(s) de {after.mention}."
            fields = [
                ("Utilisateur", after.mention, True),
                ("Rôle(s) retiré(s)", ", ".join([role.name for role in removed_roles]), False)
            ]
            await send_log_message(after.guild, LOG_GENERAL_CHANNEL_ID, "➖ Rôle Retiré", description, discord.Color.dark_orange(), fields)

staff_ranks = [
    "staff test",
    "staff",
    "modérateur",
    "contrôleur",
    "administrateur",
    "gestion staff",
    "co-fondateur",
    "owner"
]

@bot.command()
async def say(ctx, *, message):
    if not is_bot_owner(ctx.author):
        return await ctx.send("❌ Seul le créateur du bot peut utiliser cette commande.")

    try:
        await ctx.message.delete()
        await ctx.send(message)
        fields = [
            ("Auteur", ctx.author.mention, True),
            ("Contenu", message, False),
            ("Canal", ctx.channel.mention, True)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "💬 Commande Say", f"{ctx.author.mention} a fait dire un message au bot.", discord.Color.light_grey(), fields)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission d'envoyer des messages ou de supprimer la commande.")
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue : {e}")


@bot.command(name="addrole")
@commands.has_permissions(manage_roles=True)
async def addrole(ctx, *, role_name: str = None):
    if role_name is None:
        await ctx.send("❌ Veuillez spécifier le nom du rôle. Exemple : `!addrole modérateur`")
        return

    if discord.utils.get(ctx.guild.roles, name=role_name):
        await ctx.send(f"ℹ️ Le rôle **{role_name}** existe déjà.")
        fields = [
            ("Demandé par", ctx.author.mention, True),
            ("Nom du rôle", role_name, True),
            ("Raison", "Le rôle existe déjà.", False)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "ℹ️ Rôle Existant (Addrole)", f"{ctx.author.mention} a tenté de créer un rôle déjà existant.", discord.Color.light_grey(), fields)
        return

    try:
        new_role = await ctx.guild.create_role(name=role_name)
        await ctx.send(f"✅ Le rôle **{role_name}** a été créé avec succès.")
        fields = [
            ("Rôle créé", new_role.mention, True),
            ("Créé par", ctx.author.mention, True)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "✨ Rôle Créé", f"Le rôle **{role_name}** a été créé.", discord.Color.blue(), fields)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de créer des rôles. Assurez-vous que mon rôle est au-dessus du rôle que vous tentez de créer.")
        fields = [
            ("Demandé par", ctx.author.mention, True),
            ("Nom du rôle", role_name, True),
            ("Erreur", "Permissions insuffisantes pour le bot.", False)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Erreur Création Rôle", f"Échec de la création du rôle '{role_name}' par {ctx.author.mention}.", discord.Color.red(), fields)
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue lors de la création du rôle : {e}")
        fields = [
            ("Demandé par", ctx.author.mention, True),
            ("Nom du rôle", role_name, True),
            ("Erreur", str(e), False)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Erreur Création Rôle", f"Une erreur inattendue est survenue lors de la création du rôle '{role_name}' par {ctx.author.mention}.", discord.Color.red(), fields)

@bot.command(name="giverole")
@commands.has_permissions(manage_roles=True)
async def giverole(ctx, member: discord.Member, *, role_name: str):
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role is None:
        await ctx.send(f"❌ Le rôle '{role_name}' n'existe pas.")
        fields = [
            ("Demandé par", ctx.author.mention, True),
            ("Membre", member.mention, True),
            ("Nom du rôle", role_name, True),
            ("Raison", "Le rôle spécifié n'existe pas.", False)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "ℹ️ Rôle Inexistant (Giverole)", f"{ctx.author.mention} a tenté de donner un rôle inexistant à {member.mention}.", discord.Color.light_grey(), fields)
        return

    if role in member.roles:
        await ctx.send(f"ℹ️ {member.mention} a déjà le rôle **{role.name}**.")
        fields = [
            ("Demandé par", ctx.author.mention, True),
            ("Membre", member.mention, True),
            ("Rôle", role.mention, True),
            ("Raison", "Le membre a déjà ce rôle.", False)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "ℹ️ Rôle Déjà Attribué", f"{ctx.author.mention} a tenté de donner un rôle déjà possédé par {member.mention}.", discord.Color.light_grey(), fields)
        return

    if ctx.author.id != ctx.guild.owner_id and ctx.author.top_role <= role:
        await ctx.send("❌ Vous ne pouvez pas vous donner un rôle égal ou supérieur au vôtre, ou donner un rôle égal ou supérieur à celui de l'utilisateur.")
        return

    try:
        await member.add_roles(role)
        await ctx.send(f"✅ Le rôle **{role.name}** a été donné à {member.mention}.")
        fields = [
            ("Rôle donné", role.mention, True),
            ("Donné à", member.mention, True),
            ("Modérateur", ctx.author.mention, True)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "➕ Rôle Attribué", f"Le rôle **{role.name}** a été attribué à {member.mention}.", discord.Color.dark_green(), fields)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission d'ajouter ce rôle. Assurez-vous que mon rôle est au-dessus du rôle concerné.")
        fields = [
            ("Demandé par", ctx.author.mention, True),
            ("Membre", member.mention, True),
            ("Rôle", role.mention, True),
            ("Erreur", "Permissions insuffisantes pour le bot.", False)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Erreur Attribution Rôle", f"Échec de l'attribution du rôle '{role.name}' à {member.mention} par {ctx.author.mention}.", discord.Color.red(), fields)
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue : {e}")
        fields = [
            ("Demandé par", ctx.author.mention, True),
            ("Membre", member.mention, True),
            ("Rôle", role.mention, True),
            ("Erreur", str(e), False)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Erreur Attribution Rôle", f"Une erreur inattendue est survenue lors de l'attribution du rôle '{role.name}' à {member.mention} par {ctx.author.mention}.", discord.Color.red(), fields)

@bot.command()
@commands.has_permissions(manage_nicknames=True)
async def rename(ctx, member: discord.Member, *, new_nickname: str):
    old_nickname = member.nick if member.nick else member.name
    try:
        await member.edit(nick=new_nickname)
        await ctx.send(f"✅ {member.mention} a été renommé en `{new_nickname}`.")
        fields = [
            ("Utilisateur", member.mention, True),
            ("Modérateur", ctx.author.mention, True),
            ("Ancien pseudo", old_nickname, True),
            ("Nouveau pseudo", new_nickname, True)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "✏️ Pseudo Changé par Commande", f"{member.mention} a été renommé.", discord.Color.blue(), fields)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de changer ce pseudo. Assurez-vous que mon rôle est au-dessus du rôle du membre concerné.")
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue : {e}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def silence(ctx, member: discord.Member):
    guild_id = ctx.guild.id
    if guild_id not in silenced_users:
        silenced_users[guild_id] = []

    if member.id in silenced_users[guild_id]:
        await ctx.send(f"ℹ️ {member.mention} est déjà silencé.")
        return

    silenced_users[guild_id].append(member.id)
    save_data()
    await ctx.send(f"🔇 Tous les messages de {member.mention} seront désormais automatiquement supprimés.")
    fields = [
        ("Utilisateur silencé", member.mention, True),
        ("Modérateur", ctx.author.mention, True)
    ]
    await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "🔇 Membre Silencé", f"{member.mention} a été ajouté à la liste des utilisateurs silencés.", discord.Color.dark_grey(), fields)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def unsilence(ctx, member: discord.Member):
    guild_id = ctx.guild.id
    if guild_id not in silenced_users or member.id not in silenced_users[guild_id]:
        await ctx.send(f"ℹ️ {member.mention} n'est pas silencé.")
        return

    silenced_users[guild_id].remove(member.id)
    if not silenced_users[guild_id]:
        del silenced_users[guild_id]
    save_data()
    await ctx.send(f"🔊 Les messages de {member.mention} ne seront plus supprimés automatiquement.")
    fields = [
        ("Utilisateur désilencé", member.mention, True),
        ("Modérateur", ctx.author.mention, True)
    ]
    await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "🔊 Membre Désilencé", f"{member.mention} a été retiré de la liste des utilisateurs silencés.", discord.Color.light_grey(), fields)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Vérification punition
    uid = str(message.author.id)
    if uid in punitions:
        data = punitions[uid]
        if message.channel.id == data['salon_id']:
            try:
                nombre_envoye = int(message.content.strip())
                attendu = data['actuel'] + 1
                if nombre_envoye == attendu:
                    data['actuel'] += 1
                    if data['actuel'] >= data['nombre']:
                        await message.channel.send(f"🎉 {message.author.mention} a compté jusqu'à **{data['nombre']}** ! Punition terminée !")
                        await _liberer_membre(message.guild, message.author)
                    else:
                        if data['actuel'] % 10 == 0:
                            await message.channel.send(f"✅ **{data['actuel']}/{data['nombre']}** — Continue !")
                else:
                    data['actuel'] = 0
                    await message.channel.send(f"❌ {message.author.mention} **FAUTE !** Tu as envoyé `{nombre_envoye}` au lieu de `{attendu}`. Repart de **1** !")
            except ValueError:
                data['actuel'] = 0
                await message.channel.send(f"❌ {message.author.mention} **FAUTE !** Ce n'est pas un nombre. Repart de **1** !")
            return

    guild_id = message.guild.id if message.guild else None
    if guild_id and guild_id in silenced_users and message.author.id in silenced_users[guild_id]:
        try:
            await message.delete()
            fields = [
                ("Auteur", message.author.mention, True),
                ("Canal", message.channel.mention, True),
                ("Contenu", message.content if message.content else "*(Contenu non textuel ou vide)*", False)
            ]
            await send_log_message(message.guild, LOG_MODERATION_CHANNEL_ID, "🗑️ Message d'Utilisateur Silencé Supprimé", f"Le message de {message.author.mention} a été supprimé car l'utilisateur est silencé.", discord.Color.red(), fields)
        except discord.Forbidden:
            print(f"❌ Impossible de supprimer le message de {message.author.name} (permissions).")
        except Exception as e:
            print(f"❌ Erreur lors de la suppression du message de {message.author.name}: {e}")
        return
    
    # Vérification punition morse
    if uid in morse_punitions:
        data = morse_punitions[uid]
        if message.channel.id == data['salon_id']:
            data['attempts'] += 1
            print(f"MORSE ATTENDU: '{data['morse']}'")
            print(f"MORSE RECU: '{message.content.strip()}'")
            if message.content.strip() == data['morse']:
                await message.channel.send(f"🎉 {message.author.mention} **BRAVO !** Tu as réussi ! Punition terminée !")
                await _liberer_membre_morse(message.guild, message.author)
            else:
                # Nouveau mot aléatoire
                new_word = random.choice(MORSE_WORDS)
                new_morse = _text_to_morse(new_word)
                data['word'] = new_word
                data['morse'] = new_morse
                buf = _morse_to_image(new_morse, new_word)
                await message.channel.send(
                    f"❌ {message.author.mention} **FAUX !** (tentative #{data['attempts']})\nNouveau mot :",
                    file=discord.File(buf, filename="morse.png")
                )
            return

    if message.content.startswith('!') and message.content.lower().endswith(' aide'):
        command_name = message.content[1:-5]
        command_help = {
            "warn": "**!warn @membre [raison]**\nDonne un avertissement à un membre. Auto-mute après 5 warns.",
            "mute": "**!mute @membre [durée] [raison]**\nMute un membre temporairement (ex: `30s`, `1m`, `2h`, `1j`) ou de manière permanente. Empêche de parler/écrire.",
            "unmute": "**!unmute @membre**\nEnlève le mute d'un membre.",
            "ban": "**!ban @membre [raison]**\nBannit définitivement un membre du serveur.",
            "unban": "**!unban ID_utilisateur**\nDébannit un utilisateur avec son ID.",
            "clear": "**!clear nombre**\nSupprime un nombre de messages dans le salon.",
            "silence": "**!silence @membre**\nSupprime automatiquement tous les messages du membre.",
            "unsilence": "**!unsilence @membre**\nArrête de supprimer les messages du membre.",
            "sanctions": "**!sanctions [@membre]**\nAffiche le nombre de warns et mutes d'un membre.",
            "addrole": "**!addrole nom_du_rôle**\nCrée un nouveau rôle sur le serveur.",
            "giverole": "**!giverole @membre nom_du_rôle**\nDonne un rôle spécifique à un membre.",
            "construction": "**!construction**\nCrée une architecture complète de serveur communautaire (créateur du bot uniquement).",
            "nuke": "**!nuke**\n⚠️ DANGER : Supprime TOUS les salons du serveur (créateur du bot uniquement).",
            "lock": "**!lock**\nVerrouille le salon actuel (empêche d'écrire).",
            "unlock": "**!unlock**\nDéverrouille le salon actuel.",
            "rename": "**!rename @membre nouveau_pseudo**\nChange le pseudo d'un membre sur le serveur.",
            "say": "**!say message**\nFait dire quelque chose au bot (créateur du bot uniquement).",
            "dm": "**!dm @membre message**\nEnvoie un message privé à un membre (créateur du bot uniquement).",
            "dmall": "**!dmall message**\nEnvoie un message privé à tous les membres (créateur du bot uniquement).",
            "giveaway": "**!giveaway durée_heures nb_gagnants lot**\nLance un giveaway.",
            "cancelgiveaway": "**!cancelgiveaway**\nAnnule le giveaway en cours.",
            "aide": "**!aide**\nAffiche la liste complète des commandes."
        }
        if command_name in command_help:
            embed = discord.Embed(
                title=f"ℹ️ Aide - !{command_name}",
                description=command_help[command_name],
                color=0x3498db
            )
            embed.set_footer(text=f"Demandé par {message.author.display_name} • Tapez !aide pour voir toutes les commandes")
            await message.channel.send(embed=embed)
            return

    await bot.process_commands(message)
    

@bot.command()
async def dm(ctx, member: discord.Member, *, message):
    if not is_bot_owner(ctx.author):
        return await ctx.send("❌ Seul le créateur du bot peut utiliser cette commande.")

    try:
        await member.send(f"📩 Message de {ctx.author.display_name} du serveur {ctx.guild.name}: {message}")
        await ctx.send(f"Message envoyé à {member.mention} ✅")
        fields = [
            ("Envoyé par", ctx.author.mention, True),
            ("Destinataire", member.mention, True),
            ("Contenu", message, False)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "📩 Message Privé Envoyé", f"Un message privé a été envoyé à {member.mention}.", discord.Color.purple(), fields)
    except discord.Forbidden:
        await ctx.send(f"❌ Impossible d'envoyer un message privé à {member.mention} (l'utilisateur a peut-être bloqué les DMs).")
        fields = [
            ("Envoyé par", ctx.author.mention, True),
            ("Destinataire", member.mention, True),
            ("Erreur", "L'utilisateur a bloqué les DMs ou autre erreur de permission.", False)
        ]
        await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Erreur Envoi DM", f"Échec de l'envoi d'un message privé à {member.mention}.", discord.Color.red(), fields)
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue lors de l'envoi du DM : {e}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    if amount < 1:
        await ctx.send("Le nombre de messages à supprimer doit être supérieur à 0.", delete_after=5)
        return

    deleted_messages = []
    try:
        deleted_messages = await ctx.channel.purge(limit=amount + 1)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de supprimer les messages dans ce salon.", delete_after=5)
        return
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue lors de la suppression des messages : {e}", delete_after=5)
        return

    deleted_count = len(deleted_messages) - 1

    confirmation = await ctx.send(f"✅ {deleted_count} messages supprimés par {ctx.author.mention}.")
    fields = [
        ("Modérateur", ctx.author.mention, True),
        ("Canal", ctx.channel.mention, True),
        ("Messages supprimés", str(deleted_count), True)
    ]
    await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "🗑️ Messages Supprimés (Clear)", f"{deleted_count} messages ont été supprimés dans {ctx.channel.mention}.", discord.Color.light_grey(), fields)
    await asyncio.sleep(5)
    try:
        await confirmation.delete()
    except discord.NotFound:
        pass

@bot.command()
async def dmall(ctx, *, message):
    if not is_bot_owner(ctx.author):
        return await ctx.send("❌ Seul le créateur du bot peut utiliser cette commande.")

    await ctx.send("Envoi en cours...")
    sent_count = 0
    failed_count = 0
    for member in ctx.guild.members:
        if not member.bot:
            try:
                await member.send(message)
                sent_count += 1
            except discord.Forbidden:
                failed_count += 1
            except Exception as e:
                print(f"Erreur lors de l'envoi de DM à {member.name}: {e}")
                failed_count += 1

    await ctx.send(f"Message envoyé à {sent_count} membres. Échec pour {failed_count} membres.")
    fields = [
        ("Envoyé par", ctx.author.mention, True),
        ("Messages envoyés", str(sent_count), True),
        ("Échecs", str(failed_count), True),
        ("Contenu du message", message, False)
    ]
    await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "📩 DM Général Envoyé", f"Un message général a été envoyé à {sent_count} membres du serveur.", discord.Color.purple(), fields)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason: str = "Aucune raison spécifiée"):
    guild_id = ctx.guild.id
    user_id = member.id

    if guild_id not in warns:
        warns[guild_id] = {}
    if user_id not in warns[guild_id]:
        warns[guild_id][user_id] = []

    warns[guild_id][user_id].append({"reason": reason, "moderator": ctx.author.name, "timestamp": datetime.now().isoformat()})
    save_data()

    num_warns = len(warns[guild_id][user_id])

    fields = [
        ("Utilisateur averti", member.mention, True),
        ("Modérateur", ctx.author.mention, True),
        ("Raison", reason, False),
        ("Total d'avertissements", str(num_warns), True)
    ]
    await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "⚠️ Avertissement", f"Un avertissement a été donné à {member.mention}.", discord.Color.orange(), fields)

    try:
        await member.send(f"⚠️ Vous avez reçu un avertissement sur **{ctx.guild.name}**.\nRaison : {reason}")
    except discord.Forbidden:
        await ctx.send(f"⚠️ Je n'ai pas pu envoyer de message privé à {member.mention} (l'utilisateur a peut-être bloqué les DMs).")
    except Exception as e:
        print(f"Erreur lors de l'envoi du DM à {member.name}: {e}")

    await ctx.send(f"{member.mention} a été averti. Nombre total d'avertissements : {num_warns}.")

    if num_warns % 5 == 0:
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if not mute_role:
             await ctx.send("❌ Le rôle 'Muted' n'existe pas. Impossible d'auto-mute.")
             return

        await member.add_roles(mute_role, reason=f"Auto-mute: {num_warns} avertissements")
        await ctx.send(f"{member.mention} a atteint {num_warns} warns et a été mute pendant 1 jour.")

        end_time = datetime.now() + timedelta(days=1)
        if ctx.guild.id not in mutes:
            mutes[ctx.guild.id] = {}
        mutes[ctx.guild.id][member.id] = {"end_time": end_time, "reason": f"Auto-mute après {num_warns} warns"}
        save_data()

        fields_mute = [
            ("Utilisateur muté", member.mention, True),
            ("Raison", f"Atteint {num_warns} avertissements", False),
            ("Durée", "1 jour", True)
        ]
        await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "🔇 Auto-Mute", f"{member.mention} a été muté automatiquement.", discord.Color.red(), fields_mute)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def sanctions(ctx, member: discord.Member = None):
    member = member or ctx.author
    guild_id = ctx.guild.id
    user_id = member.id

    num_warns = len(warns.get(guild_id, {}).get(user_id, []))
    is_muted = guild_id in mutes and user_id in mutes[guild_id]

    mute_status_text = "muté" if is_muted else "non muté"

    await ctx.send(f"{member.mention} a {num_warns} avertissements et est {mute_status_text}.")

    fields = [
        ("Demandé par", ctx.author.mention, True),
        ("Utilisateur vérifié", member.mention, True),
        ("Warns", str(num_warns), True),
        ("Est muté ?", "Oui" if is_muted else "Non", True)
    ]
    await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "📋 Sanctions Vérifiées", f"{ctx.author.mention} a vérifié les sanctions de {member.mention}.", discord.Color.light_grey(), fields)

@bot.command()
async def lock(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Seuls les administrateurs peuvent utiliser cette commande.")

    try:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        if overwrite.send_messages is False:
            await ctx.send(f"ℹ️ Le salon {channel.mention} est déjà verrouillé.")
            return

        overwrite.send_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Salon verrouillé par {ctx.author.name}")
        await ctx.send(f"🔒 Salon {channel.mention} verrouillé.")
        fields = [
            ("Modérateur", ctx.author.mention, True),
            ("Salon", channel.mention, True)
        ]
        await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "🔒 Salon Verrouillé", f"Le salon {channel.mention} a été verrouillé par {ctx.author.mention}.", discord.Color.dark_red(), fields)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de verrouiller ce salon.")
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue lors du verrouillage du salon : {e}")

@bot.command()
async def unlock(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Seuls les administrateurs peuvent utiliser cette commande.")

    try:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        if overwrite.send_messages is True or overwrite.send_messages is None:
            await ctx.send(f"ℹ️ Le salon {channel.mention} n'est pas verrouillé ou est déjà déverrouillé.")
            return

        overwrite.send_messages = True
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Salon déverrouillé par {ctx.author.name}")
        await ctx.send(f"🔓 Salon {channel.mention} déverrouillé.")
        fields = [
            ("Modérateur", ctx.author.mention, True),
            ("Salon", channel.mention, True)
        ]
        await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "🔓 Salon Déverrouillé", f"Le salon {channel.mention} a été déverrouillé par {ctx.author.mention}.", discord.Color.green(), fields)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de déverrouiller ce salon.")
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue lors du déverrouillage du salon : {e}")

async def _run_giveaway(ctx, guild_id, message, duration_hours, winners_count, prize):
    try:
        await asyncio.sleep(duration_hours * 3600)
    except asyncio.CancelledError:
        return

    if guild_id not in giveaway_data:
        return

    try:
        new_msg = await ctx.channel.fetch_message(message.id)
    except discord.NotFound:
        await ctx.send("❌ Le message du giveaway a été supprimé. Impossible de choisir un gagnant.")
        giveaway_data.pop(guild_id, None)
        giveaway_tasks.pop(guild_id, None)
        save_data()
        return
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue lors de la récupération du message du giveaway : {e}")
        giveaway_data.pop(guild_id, None)
        giveaway_tasks.pop(guild_id, None)
        return

    users = []
    for reaction in new_msg.reactions:
        if str(reaction.emoji) == "🎉":
            async for user in reaction.users():
                if not user.bot:
                    users.append(user)
            break

    if len(users) < winners_count:
        await ctx.send(f"❌ Pas assez de participants ({len(users)}) pour choisir {winners_count} gagnant(s). Giveaway annulé.")
        fields_fail = [
            ("Lot", prize, False),
            ("Raison", f"Pas assez de participants ({len(users)})", True),
            ("Participants", str(len(users)), True)
        ]
        await send_log_message(ctx.guild, LOG_GIVEAWAY_CHANNEL_ID, "❌ Giveaway Annulé (Manque de Participants)", f"Le giveaway pour '{prize}' n'a pas eu assez de participants.", discord.Color.dark_grey(), fields_fail)
        giveaway_data.pop(guild_id, None)
        giveaway_tasks.pop(guild_id, None)
        save_data()
        return

    winners_list = random.sample(users, winners_count)
    gagnants_mentions = ", ".join(user.mention for user in winners_list)
    await ctx.send(f"🎉 Félicitations {gagnants_mentions} ! Vous avez gagné **{prize}** !")

    fields_end = [
        ("Lot", prize, False),
        ("Gagnant(s)", gagnants_mentions, True),
        ("Nombre de participants", str(len(users)), True)
    ]
    await send_log_message(ctx.guild, LOG_GIVEAWAY_CHANNEL_ID, "✅ Giveaway Terminé", f"Le giveaway pour **{prize}** est terminé. Félicitations aux gagnants !", discord.Color.green(), fields_end)

    giveaway_data.pop(guild_id, None)
    giveaway_tasks.pop(guild_id, None)
    save_data()


@bot.command()
@commands.has_permissions(administrator=True)
async def giveaway(ctx, duration_hours: float, winners_count: int, *, prize: str):
    if duration_hours <= 0 or winners_count <= 0:
        await ctx.send("❌ La durée et le nombre de gagnants doivent être supérieurs à zéro.")
        return

    guild_id = ctx.guild.id
    if guild_id in giveaway_data and giveaway_data[guild_id]:
        await ctx.send("❌ Un giveaway est déjà en cours sur ce serveur. Annulez-le d'abord avec `!cancelgiveaway`.")
        return

    end_time = datetime.now() + timedelta(hours=duration_hours)

    embed = discord.Embed(title="🎉 Giveaway 🎉", description=f"Lot : **{prize}**", color=0xffc300)
    embed.add_field(name="Durée", value=f"{duration_hours} heure(s)")
    embed.add_field(name="Nombre de gagnants", value=winners_count)
    embed.set_footer(text=f"Réagissez 🎉 pour participer ! Se termine le {end_time.strftime('%d/%m/%Y à %H:%M')}")

    message = await ctx.send(embed=embed)
    await message.add_reaction("🎉")

    giveaway_data[guild_id] = {
        "message_id": message.id,
        "channel_id": ctx.channel.id,
        "guild_id": ctx.guild.id,
        "winners": winners_count,
        "prize": prize,
        "end_time": end_time.isoformat()
    }
    save_data()

    fields_start = [
        ("Lancé par", ctx.author.mention, True),
        ("Lot", prize, False),
        ("Durée", f"{duration_hours} heure(s)", True),
        ("Gagnants", str(winners_count), True),
        ("Canal", ctx.channel.mention, True)
    ]
    await send_log_message(ctx.guild, LOG_GIVEAWAY_CHANNEL_ID, "🎉 Giveaway Démarré", f"Un nouveau giveaway a été lancé par {ctx.author.mention}.", discord.Color.gold(), fields_start)

    task = asyncio.create_task(_run_giveaway(ctx, guild_id, message, duration_hours, winners_count, prize))
    giveaway_tasks[guild_id] = task


@bot.command()
@commands.has_permissions(administrator=True)
async def cancelgiveaway(ctx):
    guild_id = ctx.guild.id
    if guild_id not in giveaway_data or not giveaway_data[guild_id]:
        await ctx.send("Aucun giveaway en cours sur ce serveur.")
        return

    giveaway_info = giveaway_data[guild_id]
    message_id = giveaway_info["message_id"]
    channel_id = giveaway_info["channel_id"]
    prize = giveaway_info["prize"]

    task = giveaway_tasks.pop(guild_id, None)
    if task and not task.done():
        task.cancel()

    try:
        channel = bot.get_channel(channel_id)
        if channel:
            message = await channel.fetch_message(message_id)
            await message.delete()
    except discord.NotFound:
        pass
    except Exception as e:
        print(f"Erreur lors de la suppression du message du giveaway: {e}")

    fields_cancel = [
        ("Annulé par", ctx.author.mention, True),
        ("Lot", prize, False)
    ]
    await send_log_message(ctx.guild, LOG_GIVEAWAY_CHANNEL_ID, "❌ Giveaway Annulé", f"Le giveaway pour '{prize}' a été annulé par {ctx.author.mention}.", discord.Color.red(), fields_cancel)

    del giveaway_data[guild_id]
    save_data()
    await ctx.send("❌ Giveaway annulé.")

@bot.command()
async def nuke(ctx):
    if not is_bot_owner(ctx.author):
        return await ctx.send("❌ Seul le créateur du bot peut utiliser cette commande.")

    confirmation_message = await ctx.send("⚠️ **ATTENTION :** Cette commande va supprimer TOUS les salons de ce serveur. Confirmez en tapant `CONFIRMER` dans les 10 secondes.")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRMER"

    try:
        await bot.wait_for('message', check=check, timeout=10.0)
    except asyncio.TimeoutError:
        await ctx.send("❌ Commande annulée. Vous n'avez pas confirmé à temps.")
        await confirmation_message.delete()
        return
    except Exception as e:
        await ctx.send(f"Une erreur est survenue lors de l'attente de la confirmation: {e}")
        await confirmation_message.delete()
        return

    await confirmation_message.delete()
    await ctx.send("💥 Confirmation reçue. Suppression de tous les salons en cours... (Cela peut prendre un certain temps)")

    guild = ctx.guild
    channels_to_delete = list(guild.channels)

    deleted_channels_count = 0
    failed_channels = []

    for channel in channels_to_delete:
        try:
            await channel.delete()
            deleted_channels_count += 1
        except discord.Forbidden:
            failed_channels.append(f"{channel.name} (Permissions insuffisantes)")
        except Exception as e:
            failed_channels.append(f"{channel.name} ({e})")
            print(f"Impossible de supprimer {channel.name}: {e}")

    final_message = f"💥 {deleted_channels_count} salons ont été supprimés."
    if failed_channels:
        final_message += "\nCertains salons n'ont pas pu être supprimés :\n" + "\n".join(failed_channels)

    if len(final_message) > 2000:
        final_message = f"💥 {deleted_channels_count} salons ont été supprimés. Trop de salons en échec pour tout lister."

    try:
        await ctx.send(final_message)
    except (discord.NotFound, discord.HTTPException):
        pass
    fields = [
        ("Exécuté par", ctx.author.mention, True),
        ("Salons supprimés", str(deleted_channels_count), True),
        ("Salons échoués", "\n".join(failed_channels) if failed_channels else "Aucun", False)
    ]
    await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "🚨 NUKE EXÉCUTÉ", f"{ctx.author.mention} a exécuté la commande Nuke sur le serveur !", discord.Color.red(), fields)

@bot.command()
async def construction(ctx):
    if not is_bot_owner(ctx.author):
        return await ctx.send("❌ Seul le créateur du bot peut utiliser cette commande.")

    await ctx.send("🔧 Création de l'architecture du serveur en cours... Cela peut prendre un moment.")

    guild = ctx.guild

    roles_to_create = [
        {"name": "Owner", "permissions": discord.Permissions.all(), "color": 0xFF0000},
        {"name": "Co-Fondateur", "permissions": discord.Permissions.all_channel(), "color": 0xFF4500},
        {"name": "Gestion Staff", "permissions": discord.Permissions(manage_guild=True, manage_roles=True, kick_members=True), "color": 0xDAA520},
        {"name": "Administrateur", "permissions": discord.Permissions(kick_members=True, ban_members=True, manage_messages=True), "color": 0x00BFFF},
        {"name": "Modérateur", "permissions": discord.Permissions(kick_members=True, manage_messages=True), "color": 0x32CD32},
        {"name": "Staff", "permissions": discord.Permissions(read_messages=True, send_messages=True), "color": 0xADD8E6},
        {"name": "Membre", "permissions": discord.Permissions(send_messages=True, read_message_history=True), "color": 0x99AAB5},
        {"name": "Visiteur", "permissions": discord.Permissions(read_messages=True), "color": 0xCCCCCC},
        {"name": "Muted", "permissions": discord.Permissions(send_messages=False, speak=False, add_reactions=False), "color": 0x6A0DAD},
        {"name": "Aventurier", "permissions": discord.Permissions.none(), "color": 0x8B4513},
        {"name": "Artiste", "permissions": discord.Permissions.none(), "color": 0xFF69B4},
        {"name": "Gamer Pro", "permissions": discord.Permissions.none(), "color": 0x4B0082}
    ]

    for role_info in roles_to_create:
        role_name = role_info["name"]
        existing_role = discord.utils.get(guild.roles, name=role_name)
        if not existing_role:
            try:
                await guild.create_role(
                    name=role_name,
                    permissions=role_info["permissions"],
                    colour=discord.Colour(role_info["color"]),
                    reason=f"Créé via commande !construction par {ctx.author.name}"
                )
            except discord.Forbidden:
                await ctx.send(f"❌ Impossible de créer le rôle **{role_name}** (permissions).")
            except Exception as e:
                await ctx.send(f"❌ Erreur lors de la création du rôle **{role_name}** : {e}")

    await asyncio.sleep(2)

    everyone_role = guild.default_role
    muted_role = discord.utils.get(guild.roles, name="Muted")

    categories = [
        {"name": "📢 Infos", "channels": [
            {"name": "📌・règlement", "type": "text", "desc": "Règlement du serveur, merci de le respecter."},
            {"name": "📢・annonces", "type": "text", "desc": "Annonces importantes.", "overwrites": {everyone_role: discord.PermissionOverwrite(send_messages=False)}},
            {"name": "📣・news", "type": "text", "desc": "Actualités et nouveautés.", "overwrites": {everyone_role: discord.PermissionOverwrite(send_messages=False)}},
            {"name": "✅・validation", "type": "text", "desc": "Validez le règlement ici pour accéder au reste du serveur."}
        ]},
        {"name": "💬 Général", "channels": [
            {"name": "💬・général", "type": "text", "desc": "Salon de discussion général."},
            {"name": "📷・média", "type": "text", "desc": "Partagez vos images et vidéos."},
            {"name": "🎨・créations", "type": "text", "desc": "Montrez vos créations artistiques."},
            {"name": "💡・suggestions", "type": "text", "desc": "Vos idées pour le serveur."}
        ]},
        {"name": "🎮 Jeux", "channels": [
            {"name": "🎮・vos-jeux", "type": "text", "desc": "Discussions sur vos jeux préférés."},
            {"name": "📈・statistiques", "type": "text", "desc": "Statistiques et classements."}
        ]},
        {"name": "📊 Sondages", "channels": [
            {"name": "🗳・sondages", "type": "text", "desc": "Votez aux sondages.", "overwrites": {everyone_role: discord.PermissionOverwrite(send_messages=False)}}
        ]},
        {"name": "📞 Vocaux", "channels": [
            {"name": "🔊 salon-général", "type": "voice", "desc": "Salon vocal principal."},
            {"name": "🎧 chill", "type": "voice", "desc": "Salon vocal détente."},
            {"name": "🔒 privé", "type": "voice", "desc": "Salon vocal privé réservé."}
        ]}
    ]

    log_channels_specific = [
        {"name": "📝logs-modération", "id_var": LOG_MODERATION_CHANNEL_ID, "desc": "Logs des actions de modération."},
        {"name": "🎁logs-giveaway", "id_var": LOG_GIVEAWAY_CHANNEL_ID, "desc": "Logs des giveaways."},
        {"name": "📑logs-général", "id_var": LOG_GENERAL_CHANNEL_ID, "desc": "Logs généraux du bot et du serveur."}
    ]

    created_items_log = []

    for cat_info in categories:
        cat_name = cat_info["name"]
        existing_cat = discord.utils.get(guild.categories, name=cat_name)
        if existing_cat:
            cat = existing_cat
        else:
            try:
                cat = await guild.create_category(cat_name, reason=f"Créé via commande !construction par {ctx.author.name}")
                await ctx.send(f"✅ Catégorie **{cat_name}** créée.")
            except discord.Forbidden:
                await ctx.send(f"❌ Impossible de créer la catégorie **{cat_name}** (permissions).")
                continue
            except Exception as e:
                await ctx.send(f"❌ Erreur lors de la création de la catégorie **{cat_name}** : {e}")
                continue
        created_items_log.append(f"Catégorie: {cat.name}")

        for ch_info in cat_info["channels"]:
            ch_name = ch_info["name"]
            ch_type = ch_info["type"]
            ch_desc = ch_info["desc"]
            ch_overwrites = ch_info.get("overwrites", {})

            default_overwrites = {
                muted_role: discord.PermissionOverwrite(send_messages=False, speak=False, add_reactions=False)
            } if muted_role else {}

            for role_obj, perm_overwrite in ch_overwrites.items():
                if role_obj not in default_overwrites:
                    default_overwrites[role_obj] = perm_overwrite
                else:
                    for attr in ['send_messages', 'speak', 'add_reactions', 'read_messages', 'connect', 'view_channel']:
                        spec_val = getattr(perm_overwrite, attr, None)
                        if spec_val is not None:
                            setattr(default_overwrites[role_obj], attr, spec_val)

            existing_channel = discord.utils.get(cat.channels, name=ch_name)
            if existing_channel:
                channel_obj = existing_channel
                try:
                    await channel_obj.edit(overwrites=default_overwrites, reason=f"Mise à jour via !construction par {ctx.author.name}")
                except discord.Forbidden:
                    await ctx.send(f"❌ Impossible de mettre à jour les permissions de {ch_name} (permissions).")
            else:
                try:
                    if ch_type == "text":
                        channel_obj = await guild.create_text_channel(ch_name, category=cat, overwrites=default_overwrites, reason=f"Créé via !construction par {ctx.author.name}")
                        if ch_desc:
                            await channel_obj.send(ch_desc)
                    elif ch_type == "voice":
                        channel_obj = await guild.create_voice_channel(ch_name, category=cat, overwrites=default_overwrites, reason=f"Créé via !construction par {ctx.author.name}")
                    await ctx.send(f"✅ Salon **{ch_name}** créé.")
                except discord.Forbidden:
                    await ctx.send(f"❌ Impossible de créer le salon **{ch_name}** (permissions).")
                    continue
                except Exception as e:
                    await ctx.send(f"❌ Erreur lors de la création du salon **{ch_name}** : {e}")
                    continue
            created_items_log.append(f"Salon: {channel_obj.name} ({ch_type})")

    for ch_info in log_channels_specific:
        ch_name = ch_info["name"]
        ch_desc = ch_info["desc"]

        existing_channel = discord.utils.get(guild.text_channels, name=ch_name)
        if existing_channel:
            channel_obj = existing_channel
        else:
            try:
                log_overwrites = {
                    everyone_role: discord.PermissionOverwrite(read_messages=False, view_channel=False),
                    muted_role: discord.PermissionOverwrite(send_messages=False, speak=False, add_reactions=False, read_messages=False, view_channel=False)
                } if muted_role else {everyone_role: discord.PermissionOverwrite(read_messages=False, view_channel=False)}

                staff_roles_for_logs = ["Owner", "Co-Fondateur", "Gestion Staff", "Administrateur", "Modérateur", "Staff"]
                for role_name in staff_roles_for_logs:
                    role = discord.utils.get(guild.roles, name=role_name)
                    if role:
                        log_overwrites[role] = discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True)

                channel_obj = await guild.create_text_channel(ch_name, overwrites=log_overwrites, reason=f"Créé via commande !construction par {ctx.author.name}")
                await ctx.send(f"✅ Salon de log **{ch_name}** créé.")
            except discord.Forbidden:
                await ctx.send(f"❌ Impossible de créer le salon de log **{ch_name}** (permissions).")
                continue
            except Exception as e:
                await ctx.send(f"❌ Erreur lors de la création du salon de log **{ch_name}** : {e}")
                continue
        created_items_log.append(f"Salon de log: {channel_obj.name}")
        history = [m async for m in channel_obj.history(limit=1)]
        if ch_desc and (not existing_channel or not history):
            await channel_obj.send(ch_desc)

    await ctx.send("✅ Architecture créée et rôles mis à jour.")
    fields_log_final = [
        ("Exécuté par", ctx.author.mention, True),
        ("Éléments créés/mis à jour", "\n".join(created_items_log) if created_items_log else "Aucun", False)
    ]
    await send_log_message(ctx.guild, LOG_GENERAL_CHANNEL_ID, "🛠️ Architecture Serveur Créée/Mise à jour", f"{ctx.author.mention} a créé ou mis à jour l'architecture du serveur.", discord.Color.blue(), fields_log_final)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def mute(ctx, member: discord.Member, duration: str = None, *, reason: str = "Aucune raison spécifiée"):
    guild = ctx.guild
    mute_role = discord.utils.get(guild.roles, name="Muted")

    if not mute_role:
        await ctx.send("Le rôle 'Muted' n'existe pas. Je vais le créer et configurer ses permissions.")
        try:
            mute_role = await guild.create_role(name="Muted", permissions=discord.Permissions.none())
            for channel in guild.channels:
                try:
                    await channel.set_permissions(mute_role, send_messages=False, speak=False, add_reactions=False)
                except discord.Forbidden:
                    print(f"Impossible de définir les permissions pour le rôle Muted dans le salon {channel.name} (Forbidden).")
            await ctx.send("Le rôle 'Muted' a été créé et ses permissions ont été configurées.")
        except discord.Forbidden:
            await ctx.send("❌ Je n'ai pas la permission de créer le rôle 'Muted' ou de configurer ses permissions. Mon rôle doit être plus haut que le rôle 'Muted' et avoir 'Gérer les rôles'.")
            return

    if mute_role in member.roles:
        await ctx.send(f"ℹ️ {member.mention} est déjà muté.")
        return

    if ctx.author.top_role <= member.top_role and ctx.author.id != ctx.guild.owner_id:
        await ctx.send("❌ Vous ne pouvez pas muter un membre ayant un rôle égal ou supérieur au vôtre.")
        return

    if member.id == bot.user.id:
        await ctx.send("❌ Je ne peux pas me muter moi-même.")
        return
    if member.id == ctx.guild.owner_id:
        await ctx.send("❌ Vous ne pouvez pas muter le propriétaire du serveur.")
        return

    end_time = None
    log_duration_text = "Permanent"

    if duration:
        try:
            num = float(duration[:-1])
            unit = duration[-1].lower()
            if unit == 's':
                end_time = datetime.now() + timedelta(seconds=num)
                log_duration_text = f"{num} seconde(s)"
            elif unit == 'm':
                end_time = datetime.now() + timedelta(minutes=num)
                log_duration_text = f"{num} minute(s)"
            elif unit == 'h':
                end_time = datetime.now() + timedelta(hours=num)
                log_duration_text = f"{num} heure(s)"
            elif unit == 'j':
                end_time = datetime.now() + timedelta(days=num)
                log_duration_text = f"{num} jour(s)"
            else:
                await ctx.send("❌ Format de durée invalide. Le mute sera permanent. Ex: `30s`, `1.5h`, `7j`")
                duration = None
        except ValueError:
            await ctx.send("❌ Format de durée invalide. Le mute sera permanent. Ex: `30s`, `1.5h`, `7j`")
            duration = None

    try:
        await member.add_roles(mute_role, reason=reason)
        guild_id = ctx.guild.id
        user_id = member.id
        if guild_id not in mutes:
            mutes[guild_id] = {}
        mutes[guild_id][user_id] = {"end_time": end_time, "reason": reason}
        save_data()

        await ctx.send(f"{member.mention} a été mute pour {log_duration_text} (Raison : {reason}).")

        dm_message = f"🔇 Vous avez été mute sur **{guild.name}**."
        if reason: dm_message += f"\nRaison : {reason}"
        if log_duration_text != "Permanent": dm_message += f"\nFin du mute : {end_time.strftime('%Y-%m-%d %H:%M:%S')} (heure locale)"
        try:
            await member.send(dm_message)
        except discord.Forbidden:
            await ctx.send(f"⚠️ Je n'ai pas pu envoyer de message privé à {member.mention} (l'utilisateur a peut-être bloqué les DMs).")
        except Exception as e:
            print(f"Erreur lors de l'envoi du DM à {member.name}: {e}")

        fields_log = [
            ("Utilisateur muté", member.mention, True),
            ("Modérateur", ctx.author.mention, True),
            ("Raison", reason, False),
            ("Durée", log_duration_text, True)
        ]
        log_title = "🔇 Membre Muté Temporairement" if duration else "🔇 Membre Muté Permanent"
        log_color = discord.Color.red() if duration else discord.Color.dark_red()
        await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, log_title, f"{member.mention} a été muté.", log_color, fields_log)

    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission d'ajouter le rôle 'Muted' à ce membre. Mon rôle doit être au-dessus du rôle 'Muted' et des autres rôles du membre.")
    except Exception as e:
        await ctx.send(f"❌ Une erreur inattendue est survenue lors du mute : {e}")


@bot.command()
@commands.has_permissions(manage_messages=True)
async def unmute(ctx, member: discord.Member):
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role:
        await ctx.send("Le rôle 'Muted' n'existe pas sur ce serveur.")
        return

    if mute_role not in member.roles:
        await ctx.send(f"{member.mention} n'a pas le rôle 'Muted'.")
        return

    try:
        await member.remove_roles(mute_role, reason=f"Unmute par {ctx.author.name}")
        guild_id = ctx.guild.id
        user_id = member.id
        if guild_id in mutes and user_id in mutes[guild_id]:
            del mutes[guild_id][user_id]
            if not mutes[guild_id]:
                del mutes[guild_id]
            save_data()

        await ctx.send(f"✅ {member.mention} a été unmute avec succès.")

        try:
            await member.send(f"🔊 Vous avez été unmute sur **{ctx.guild.name}**.")
        except discord.Forbidden:
            await ctx.send(f"⚠️ Je n'ai pas pu envoyer de message privé à {member.mention}.")
        except Exception as e:
            print(f"Erreur lors de l'envoi du DM à {member.name}: {e}")

        fields = [
            ("Utilisateur unmute", member.mention, True),
            ("Modérateur", ctx.author.mention, True)
        ]
        await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "🔊 Membre Unmute Manuellement", f"{member.mention} a été unmute manuellement par {ctx.author.mention}.", discord.Color.green(), fields)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de retirer le rôle 'Muted' à ce membre. Assurez-vous que mon rôle est au-dessus du rôle 'Muted'.")
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue lors de l'unmute : {e}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason=None):
    if member.id == ctx.author.id:
        await ctx.send("❌ Vous ne pouvez pas vous bannir vous-même.")
        return
    if member.id == bot.user.id:
        await ctx.send("❌ Je ne peux pas me bannir moi-même.")
        return
    if member.id == ctx.guild.owner_id:
        await ctx.send("❌ Vous ne pouvez pas bannir le propriétaire du serveur.")
        return
    if ctx.author.top_role <= member.top_role and ctx.author.id != ctx.guild.owner_id:
        await ctx.send("❌ Vous ne pouvez pas bannir un membre ayant un rôle égal ou supérieur au vôtre.")
        return

    try:
        await member.ban(reason=reason)
        await ctx.send(f"{member.mention} a été banni. Raison : {reason if reason else 'Non spécifiée'}")

        try:
            await member.send(f"🚫 Vous avez été banni du serveur **{ctx.guild.name}**.\nRaison : {reason if reason else 'Non spécifiée'}")
        except discord.Forbidden:
            await ctx.send(f"⚠️ Je n'ai pas pu envoyer de message privé à {member.mention}.")
        except Exception as e:
            print(f"Erreur lors de l'envoi du DM à {member.name}: {e}")

        fields = [
            ("Utilisateur banni", member.mention, True),
            ("ID Utilisateur", member.id, True),
            ("Modérateur", ctx.author.mention, True),
            ("Raison", reason if reason else "Non spécifiée", False)
        ]
        await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "🚫 Membre Banni", f"{member.mention} a été banni du serveur par {ctx.author.mention}.", discord.Color.dark_red(), fields)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de bannir ce membre. Assurez-vous que mon rôle est au-dessus du rôle du membre concerné.")
    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue lors du bannissement : {e}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, *, member_id: int):
    banned_users = [entry async for entry in ctx.guild.bans()]
    unbanned_user = None

    for ban_entry in banned_users:
        if ban_entry.user.id == member_id:
            unbanned_user = ban_entry.user
            break

    if unbanned_user:
        try:
            await ctx.guild.unban(unbanned_user, reason=f"Débanni par {ctx.author.name}")
            await ctx.send(f"✅ {unbanned_user.mention} a été débanni.")

            fields = [
                ("Utilisateur débanni", unbanned_user.mention, True),
                ("ID Utilisateur", unbanned_user.id, True),
                ("Modérateur", ctx.author.mention, True)
            ]
            await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "✅ Membre Débanni", f"{unbanned_user.mention} (ID: {member_id}) a été débanni par {ctx.author.mention}.", discord.Color.green(), fields)
        except discord.Forbidden:
            await ctx.send("❌ Je n'ai pas la permission de débannir cet utilisateur.")
        except Exception as e:
            await ctx.send(f"❌ Une erreur est survenue lors du débannissement : {e}")
    else:
        await ctx.send(f"Utilisateur avec l'ID {member_id} non trouvé dans la liste des bannis.")
        fields = [
            ("Demandé par", ctx.author.mention, True),
            ("ID cherché", str(member_id), True),
            ("Raison", "ID non trouvé dans la liste des bannis.", False)
        ]
        await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "⚠️ Échec Débannissement", f"{ctx.author.mention} a tenté de débannir l'ID {member_id} qui n'est pas banni.", discord.Color.orange(), fields)

# =======================================================================
# ============================= CASINO ==================================
# =======================================================================

from itertools import combinations as _comb

SUITS    = ['♠', '♥', '♦', '♣']
RANKS    = ['2','3','4','5','6','7','8','9','10','J','Q','K','A']
RANK_VAL = {r: i for i, r in enumerate(RANKS, 2)}
RED_SUITS = {'♥', '♦'}
ROULETTE_RED = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
SLOT_SYMS = ['🍒','🍋','🍊','🍇','🍉','⭐','💎']
SLOT_W    = [30, 25, 20, 15, 10, 5, 2]
HAND_NAMES = ['Carte Haute','Paire','Double Paire','Brelan',
              'Suite','Couleur','Full House','Carré','Quinte Flush']


# ---- Utilitaires cartes ----

def _new_deck():
    d = [{'r': r, 's': s} for r in RANKS for s in SUITS]
    random.shuffle(d)
    return d

def _card(c):
    color = '🔴' if c['s'] in RED_SUITS else '⚫'
    return f"{color}`{c['r']}{c['s']}`"

def _hand(h):
    return ' '.join(_card(c) for c in h)


# ---- Blackjack ----

def _bj_val(c):
    if c['r'] in ('J','Q','K'): return 10
    if c['r'] == 'A': return 11
    return int(c['r'])

def _bj_total(hand):
    t = sum(_bj_val(c) for c in hand)
    aces = sum(1 for c in hand if c['r'] == 'A')
    while t > 21 and aces:
        t -= 10
        aces -= 1
    return t

class BlackjackGame:
    def __init__(self, bet):
        self.deck   = _new_deck()
        self.bet    = bet
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]

    def pt(self): return _bj_total(self.player)
    def dt(self): return _bj_total(self.dealer)

    def hit(self):   self.player.append(self.deck.pop())
    def stand(self):
        while self.dt() < 17:
            self.dealer.append(self.deck.pop())

    def natural(self): return self.pt() == 21 and len(self.player) == 2

    def result(self):
        pt, dt = self.pt(), self.dt()
        if pt > 21:            return 'bust'
        if dt > 21 or pt > dt: return 'win'
        if pt == dt:           return 'push'
        return 'lose'

def _bj_embed(game, reveal=False, title="🃏 Blackjack"):
    if reveal:
        dealer_info = f"{_hand(game.dealer)} ({game.dt()})"
    else:
        dealer_info = f"{_card(game.dealer[0])} 🂠"
    embed = discord.Embed(title=title, color=0x27ae60)
    embed.add_field(name="🎩 Croupier", value=dealer_info, inline=False)
    embed.add_field(name=f"🃏 Votre main ({game.pt()})", value=_hand(game.player), inline=False)
    embed.add_field(name="💰 Mise", value=f"{game.bet:,} coins", inline=True)
    if not reveal:
        embed.set_footer(text="Utilisez les boutons ci-dessous pour jouer.")
    return embed

def _resolve_bj(uid, game, r):
    if r == 'win':  coins[uid] += game.bet * 2
    elif r == 'push': coins[uid] += game.bet

_BJ_RESULT = {
    'win':  (0x2ecc71, "🎉 **Gagné !**"),
    'push': (0x95a5a6, "🤝 **Égalité !** Mise remboursée."),
    'lose': (0xe74c3c, "😢 **Perdu !**"),
    'bust': (0xe74c3c, "💥 **Bust !**"),
}


# ---- Évaluateur de main poker ----

def _score5(cards):
    ranks = sorted([RANK_VAL[c['r']] for c in cards], reverse=True)
    suits  = [c['s'] for c in cards]
    flush  = len(set(suits)) == 1
    straight = len(set(ranks)) == 5 and ranks[0] - ranks[4] == 4
    if not straight and set(ranks) == {14, 5, 4, 3, 2}:
        straight = True
        ranks = [5, 4, 3, 2, 1]
    cnt = {}
    for r in ranks:
        cnt[r] = cnt.get(r, 0) + 1
    grp = sorted(cnt.items(), key=lambda x: (x[1], x[0]), reverse=True)
    gr  = [r for r, _ in grp]
    gc  = [c for _, c in grp]
    if straight and flush: return (8, ranks)
    if gc[0] == 4:         return (7, gr)
    if gc[:2] == [3, 2]:   return (6, gr)
    if flush:              return (5, ranks)
    if straight:           return (4, ranks)
    if gc[0] == 3:         return (3, gr)
    if gc[:2] == [2, 2]:   return (2, gr)
    if gc[0] == 2:         return (1, gr)
    return (0, gr)

def _best_hand(hole, community):
    best = None
    for combo in _comb(hole + community, 5):
        s = _score5(list(combo))
        if best is None or s > best:
            best = s
    return best


# ---- PokerGame ----

class PokerGame:
    def __init__(self, host_id, ante, channel_id):
        self.host_id    = host_id
        self.ante       = ante
        self.channel_id = channel_id
        self.phase      = 'waiting'
        self.players    = []
        self.stacks     = {}
        self.hands      = {}
        self.folded     = set()
        self.all_in_set = set()
        self.community  = []
        self.deck       = []
        self.pot        = 0
        self.bets       = {}
        self.acted      = set()
        self.action_idx = 0
        self.sb_idx     = 0

    def add_player(self, uid, stack):
        if uid not in self.players:
            self.players.append(uid)
            self.stacks[uid] = stack

    def active(self):
        return [p for p in self.players if p not in self.folded]

    def can_act(self):
        return [p for p in self.active() if p not in self.all_in_set]

    def current_player(self):
        can = self.can_act()
        if not can: return None
        for i in range(len(self.players)):
            idx = (self.action_idx + i) % len(self.players)
            p   = self.players[idx]
            if p in can:
                self.action_idx = idx
                return p
        return None

    def start(self):
        self.deck      = _new_deck()
        self.community = []
        self.folded    = set()
        self.all_in_set = set()
        self.pot       = 0
        self.bets      = {p: 0 for p in self.players}
        self.acted     = set()
        for p in self.players:
            self.hands[p] = [self.deck.pop(), self.deck.pop()]
        sb = self.players[self.sb_idx % len(self.players)]
        bb = self.players[(self.sb_idx + 1) % len(self.players)]
        sb_amt = min(self.ante // 2, self.stacks[sb])
        bb_amt = min(self.ante,      self.stacks[bb])
        self._deduct(sb, sb_amt); self._deduct(bb, bb_amt)
        self.pot += sb_amt + bb_amt
        self.bets[sb] = sb_amt; self.bets[bb] = bb_amt
        bb_idx = self.players.index(bb)
        self.action_idx = (bb_idx + 1) % len(self.players)
        self.phase = 'preflop'

    def _deduct(self, uid, amt):
        self.stacks[uid] = max(0, self.stacks[uid] - amt)

    def max_bet(self):
        return max(self.bets.values()) if self.bets else 0

    def to_call(self, uid):
        return max(0, self.max_bet() - self.bets.get(uid, 0))

    def _next(self):
        self.action_idx = (self.action_idx + 1) % len(self.players)

    def do_fold(self, uid):
        self.folded.add(uid); self.acted.add(uid); self._next()

    def do_call(self, uid):
        amt = min(self.to_call(uid), self.stacks[uid])
        self._deduct(uid, amt); self.pot += amt
        self.bets[uid] = self.bets.get(uid, 0) + amt
        if self.stacks[uid] == 0: self.all_in_set.add(uid)
        self.acted.add(uid); self._next()

    def do_check(self, uid):
        self.acted.add(uid); self._next()

    def do_raise(self, uid, raise_by):
        total = min(self.to_call(uid) + raise_by, self.stacks[uid])
        self._deduct(uid, total); self.pot += total
        self.bets[uid] = self.bets.get(uid, 0) + total
        if self.stacks[uid] == 0: self.all_in_set.add(uid)
        self.acted = {uid}; self._next()

    def do_allin(self, uid):
        amt = self.stacks[uid]
        prev = self.bets.get(uid, 0)
        self._deduct(uid, amt); self.pot += amt
        self.bets[uid] = prev + amt
        self.all_in_set.add(uid)
        if amt > self.to_call(uid): self.acted = {uid}
        else: self.acted.add(uid)
        self._next()

    def street_over(self):
        can = self.can_act()
        if not can: return True
        mb = self.max_bet()
        for p in can:
            if p not in self.acted:           return False
            if self.bets.get(p, 0) < mb:      return False
        return True

    def next_street(self):
        self.bets  = {p: 0 for p in self.players}
        self.acted = set()
        if   self.phase == 'preflop':
            self.community = [self.deck.pop() for _ in range(3)]
            self.phase = 'flop'
        elif self.phase == 'flop':
            self.community.append(self.deck.pop()); self.phase = 'turn'
        elif self.phase == 'turn':
            self.community.append(self.deck.pop()); self.phase = 'river'
        elif self.phase == 'river':
            self.phase = 'showdown'; return
        active = self.active()
        if active:
            self.action_idx = self.players.index(active[0])

    def winners(self):
        act = self.active()
        if len(act) == 1: return act
        scores = {p: _best_hand(self.hands[p], self.community) for p in act}
        best   = max(scores.values())
        return [p for p, s in scores.items() if s == best]

    def pay_out(self, wlist):
        share = self.pot // len(wlist)
        rem   = self.pot % len(wlist)
        for i, w in enumerate(wlist):
            self.stacks[w] += share + (1 if i < rem else 0)
        self.pot = 0


# =======================================================================
# ========================= COMMANDES CASINO ============================
# =======================================================================

@bot.command(name="coins")
async def cmd_coins(ctx, member: discord.Member = None):
    target = member or ctx.author
    embed  = discord.Embed(
        title="💰 Solde de Coins",
        description=f"{target.mention} possède **{coins[target.id]:,} 🪙 coins**.",
        color=0xf1c40f
    )
    await ctx.send(embed=embed)


@bot.command(name="daily")
async def cmd_daily(ctx):
    uid  = str(ctx.author.id)
    now  = datetime.now()
    BASE_AMOUNT = 500
    LUCKY_BONUS = 150
    has_lucky = _has_item(ctx.author.id, 1)
    AMOUNT = BASE_AMOUNT + (LUCKY_BONUS if has_lucky else 0)
    cd_h = cooldown_h('daily')
    if uid in daily_cooldowns:
        last = datetime.fromisoformat(daily_cooldowns[uid])
        wait = last + timedelta(hours=cd_h) - now
        if wait.total_seconds() > 0:
            h, rem = divmod(int(wait.total_seconds()), 3600)
            m = rem // 60
            await ctx.send(f"⏳ {ctx.author.mention}, patientez encore **{h}h {m}min** avant votre prochain daily.")
            return
    daily_cooldowns[uid] = now.isoformat()
    coins[ctx.author.id] += AMOUNT
    save_data()
    bonus_str = f"\n🍀 Bonus Porte-bonheur : **+{LUCKY_BONUS} coins** !" if has_lucky else ""
    embed = discord.Embed(
        title="🎁 Daily Coins !",
        description=f"{ctx.author.mention} a reçu **{AMOUNT:,} 🪙 coins** !{bonus_str}\n💰 Solde : **{coins[ctx.author.id]:,} coins**",
        color=0xf1c40f
    )
    embed.set_footer(text=f"Revenez dans {cd_h:g}h !")
    await ctx.send(embed=embed)


@bot.command(name="travail")
async def cmd_travail(ctx):
    uid = str(ctx.author.id)
    now = datetime.now()
    cd_h = cooldown_h('travail')
    if uid in work_cooldowns:
        last = datetime.fromisoformat(work_cooldowns[uid])
        wait = last + timedelta(hours=cd_h) - now
        if wait.total_seconds() > 0:
            h, rem = divmod(int(wait.total_seconds()), 3600)
            m = rem // 60
            await ctx.send(f"⏳ {ctx.author.mention}, vous êtes fatigué(e) ! Revenez dans **{h}h {m}min**.")
            return
    has_pro = _has_item(ctx.author.id, 2)
    amount = random.randint(50, 400) if has_pro else random.randint(10, 300)
    work_cooldowns[uid] = now.isoformat()
    coins[ctx.author.id] += amount
    save_data()
    jobs = [
        "gardien de nuit 🌙", "livreur de pizza 🍕", "programmeur 💻",
        "mécanicien 🔧", "cuisinier 👨‍🍳", "pêcheur 🎣", "jardinier 🌿",
        "videur de boîte 🚪", "DJ 🎧", "tuteur en maths 📐",
        "chauffeur de taxi 🚕", "artiste de rue 🎨", "coiffeur ✂️",
        "plombier 🔩", "photographe 📸"
    ]
    job = random.choice(jobs)
    pro_str = "\n⚒️ *Équipement Pro actif — meilleur salaire !*" if has_pro else ""
    embed = discord.Embed(
        title="💼 Travail effectué !",
        description=(
            f"{ctx.author.mention} a travaillé comme **{job}**\n"
            f"et a gagné **{amount:,} 🪙 coins** !{pro_str}\n\n"
            f"💰 Solde : **{coins[ctx.author.id]:,} coins**"
        ),
        color=0x2ecc71
    )
    embed.set_footer(text="Disponible à nouveau dans 2 heures.")
    await ctx.send(embed=embed)


@bot.command(name="risque")
async def cmd_risque(ctx):
    uid = ctx.author.id
    uid_str = str(uid)
    # Vérification du cooldown (modifiable via !cooldown)
    now = datetime.now()
    last_iso = risque_cooldowns.get(uid_str)
    if last_iso:
        try:
            last = datetime.fromisoformat(last_iso)
            elapsed = (now - last).total_seconds()
            cooldown_sec = cooldown_h('risque') * 3600
            if elapsed < cooldown_sec:
                remaining = cooldown_sec - elapsed
                h = int(remaining // 3600)
                m = int((remaining % 3600) // 60)
                return await ctx.send(
                    f"⏳ Vous avez déjà tenté un coup risqué récemment ! "
                    f"Réessayez dans **{h}h {m}min**."
                )
        except ValueError:
            pass
    risque_cooldowns[uid_str] = now.isoformat()
    if random.random() < 0.55:
        amount = random.randint(200, 600)
        coins[uid] += amount
        save_data()
        embed = discord.Embed(
            title="🎲 Risque — Victoire !",
            description=(
                f"🎉 {ctx.author.mention} a pris le risque et **gagné {amount:,} 🪙 coins** !\n"
                f"💰 Solde : **{coins[uid]:,} coins**\n"
                f"⏳ Prochain risque dans **{RISQUE_COOLDOWN_HOURS}h**."
            ),
            color=0x2ecc71
        )
    else:
        amount = random.randint(100, 300)
        loss   = min(amount, coins[uid])
        coins[uid] -= loss
        save_data()
        embed = discord.Embed(
            title="🎲 Risque — Échec !",
            description=(
                f"😢 {ctx.author.mention} a pris le risque et **perdu {loss:,} 🪙 coins**...\n"
                f"💰 Solde : **{coins[uid]:,} coins**\n"
                f"⏳ Prochain risque dans **{RISQUE_COOLDOWN_HOURS}h**."
            ),
            color=0xe74c3c
        )
    await ctx.send(embed=embed)


@bot.command(name="give")
async def cmd_give(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        await ctx.send("❌ Le montant doit être supérieur à 0."); return
    if member.id == ctx.author.id:
        await ctx.send("❌ Vous ne pouvez pas vous envoyer des coins à vous-même."); return
    if coins[ctx.author.id] < amount:
        await ctx.send(f"❌ Pas assez de coins. Solde : **{coins[ctx.author.id]:,} coins**"); return
    coins[ctx.author.id] -= amount
    coins[member.id]      += amount
    save_data()
    embed = discord.Embed(
        title="💸 Transfert de Coins",
        description=f"{ctx.author.mention} a envoyé **{amount:,} 🪙 coins** à {member.mention} !",
        color=0x2ecc71
    )
    await ctx.send(embed=embed)


@bot.command(name="roulette")
async def cmd_roulette(ctx, mise: str, *, choix: str):
    choix = choix.lower().strip()
    mise, err = _resolve_mise(mise, ctx.author.id, 'roulette')
    if err: return await ctx.send(err)

    numero    = random.randint(0, 36)
    is_red    = numero in ROULETTE_RED
    is_black  = numero != 0 and not is_red
    col_emoji = '🔴' if is_red else ('🟢' if numero == 0 else '⚫')

    mult = 0; bet_desc = ""
    if   choix in ('rouge','red'):     bet_desc = "Rouge 🔴";              mult = 2 if is_red   else 0
    elif choix in ('noir','black'):    bet_desc = "Noir ⚫";               mult = 2 if is_black else 0
    elif choix in ('pair','even'):     bet_desc = "Pair";                  mult = 2 if (numero != 0 and numero % 2 == 0) else 0
    elif choix in ('impair','odd'):    bet_desc = "Impair";                mult = 2 if numero % 2 == 1 else 0
    elif choix in ('manque','1-18'):   bet_desc = "Manque (1–18)";         mult = 2 if 1  <= numero <= 18 else 0
    elif choix in ('passe','19-36'):   bet_desc = "Passe (19–36)";         mult = 2 if 19 <= numero <= 36 else 0
    elif choix in ('1-12','1ere','1ère'):  bet_desc = "1ère douzaine";     mult = 3 if 1  <= numero <= 12 else 0
    elif choix in ('13-24','2eme','2ème'): bet_desc = "2ème douzaine";     mult = 3 if 13 <= numero <= 24 else 0
    elif choix in ('25-36','3eme','3ème'): bet_desc = "3ème douzaine";     mult = 3 if 25 <= numero <= 36 else 0
    else:
        try:
            t = int(choix)
            if 0 <= t <= 36: bet_desc = f"Numéro {t}"; mult = 36 if numero == t else 0
            else: await ctx.send("❌ Numéro invalide (0–36)."); return
        except ValueError:
            await ctx.send(
                "❌ Pari invalide.\n"
                "Options : `rouge` `noir` `pair` `impair` `manque` `passe` `1-12` `13-24` `25-36` ou un numéro (0–36)."
            ); return

    coins[ctx.author.id] -= mise
    if mult > 0:
        gain = mise * mult; coins[ctx.author.id] += gain
        net = gain - mise; result_text = f"🎉 **Gagné !** +{net:,} coins"; color = 0x2ecc71
    else:
        result_text = f"😢 **Perdu !** -{mise:,} coins"; color = 0xe74c3c
    save_data()

    embed = discord.Embed(title="🎡 Roulette", color=color)
    embed.add_field(name="🎯 Numéro sorti", value=f"{col_emoji} **{numero}**", inline=True)
    embed.add_field(name="🎲 Votre pari",   value=bet_desc,                   inline=True)
    embed.add_field(name="📊 Résultat",      value=result_text,                inline=False)
    embed.add_field(name="💰 Solde",         value=f"{coins[ctx.author.id]:,} coins", inline=True)
    embed.set_footer(text="Rouge/Noir/Pair/Impair = ×2 | Douzaine = ×3 | Numéro plein = ×36")
    await ctx.send(embed=embed)


@bot.command(name="slots")
async def cmd_slots(ctx, mise: str):
    mise, err = _resolve_mise(mise, ctx.author.id, 'slots')
    if err: return await ctx.send(err)

    result  = random.choices(SLOT_SYMS, weights=SLOT_W, k=3)
    display = ' | '.join(result)
    coins[ctx.author.id] -= mise

    if result[0] == result[1] == result[2]:
        sym  = result[0]
        mult = 50 if sym == '💎' else 20 if sym == '⭐' else 10 if sym in ('🍉','🍇') else 5
        gain = mise * mult; coins[ctx.author.id] += gain
        net  = gain - mise
        result_text = f"🎉 **JACKPOT ! 3× {sym}** — +{net:,} coins (×{mult})"
        color = 0xf1c40f
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        gain = int(mise * 1.5); coins[ctx.author.id] += gain
        net  = gain - mise
        result_text = f"✨ **Deux identiques !** — +{net:,} coins (×1.5)"
        color = 0x2ecc71
    else:
        result_text = f"😢 **Perdu !** — -{mise:,} coins"; color = 0xe74c3c
    save_data()

    embed = discord.Embed(title="🎰 Machine à Sous", color=color)
    embed.add_field(name="🎰 Rouleaux",  value=f"**[ {display} ]**",              inline=False)
    embed.add_field(name="📊 Résultat",  value=result_text,                        inline=False)
    embed.add_field(name="💰 Solde",     value=f"{coins[ctx.author.id]:,} coins", inline=True)
    embed.set_footer(text="💎×3=50× | ⭐×3=20× | 🍉🍇×3=10× | autres×3=5× | 2 identiques=1.5×")
    await ctx.send(embed=embed)


class BlackjackView(discord.ui.View):
    def __init__(self, author_id: int, key, game):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.key = key
        self.game = game

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Ce n'est pas votre partie de blackjack !", ephemeral=True)
            return False
        return True

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    async def _finish(self, interaction, title, color, result_text):
        self._disable_all()
        embed = _bj_embed(self.game, reveal=True, title=title)
        embed.color = color
        embed.add_field(name="Résultat", value=result_text, inline=False)
        embed.add_field(name="💳 Solde", value=f"{coins[self.author_id]:,} coins", inline=True)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Tirer", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game
        uid = self.author_id
        game.hit()
        if game.pt() > 21:
            active_bj.pop(self.key, None); save_data()
            await self._finish(interaction, "🃏 Blackjack — Bust !", 0xe74c3c,
                               f"💥 **Bust !** (-{game.bet:,} coins)")
        elif game.pt() == 21:
            game.stand(); r = game.result()
            active_bj.pop(self.key, None); _resolve_bj(uid, game, r); save_data()
            col, txt = _BJ_RESULT[r]
            sign = '+' if r in ('win', 'push') else '-'
            await self._finish(interaction, "🃏 Blackjack — 21 !", col,
                               f"{txt} ({sign}{game.bet:,} coins)")
        else:
            self.double_btn.disabled = True
            await interaction.response.edit_message(embed=_bj_embed(game), view=self)

    @discord.ui.button(label="Rester", style=discord.ButtonStyle.success, emoji="✋")
    async def stand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game
        uid = self.author_id
        game.stand(); r = game.result()
        active_bj.pop(self.key, None); _resolve_bj(uid, game, r); save_data()
        col, txt = _BJ_RESULT[r]
        titles = {'win': 'Gagné !', 'push': 'Égalité', 'lose': 'Perdu', 'bust': 'Bust !'}
        sign = '+' if r in ('win', 'push') else '-'
        await self._finish(interaction, f"🃏 Blackjack — {titles[r]}", col,
                           f"{txt} ({sign}{game.bet:,} coins)")

    @discord.ui.button(label="Doubler", style=discord.ButtonStyle.danger, emoji="💰")
    async def double_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game
        uid = self.author_id
        if len(game.player) != 2:
            return await interaction.response.send_message(
                "❌ Le double n'est possible qu'avec 2 cartes.", ephemeral=True)
        if coins[uid] < game.bet:
            return await interaction.response.send_message(
                "❌ Pas assez de coins pour doubler.", ephemeral=True)
        coins[uid] -= game.bet
        game.bet *= 2
        game.hit(); game.stand(); r = game.result()
        active_bj.pop(self.key, None); _resolve_bj(uid, game, r); save_data()
        col, txt = _BJ_RESULT[r]
        sign = '+' if r in ('win', 'push') else '-'
        await self._finish(interaction, "🃏 Blackjack — Double", col,
                           f"{txt} ({sign}{game.bet:,} coins)")

    @discord.ui.button(label="Abandonner", style=discord.ButtonStyle.secondary, emoji="🏳️")
    async def quit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Forfait : la mise est perdue, comme un "lose"
        game = self.game
        active_bj.pop(self.key, None); save_data()
        await self._finish(interaction, "🃏 Blackjack — Abandon", 0x95a5a6,
                           f"🏳️ **Abandon.** (-{game.bet:,} coins)")

    async def on_timeout(self):
        if self.key in active_bj:
            game = active_bj.pop(self.key)
            game.stand(); r = game.result()
            _resolve_bj(self.author_id, game, r)
            save_data()
        self._disable_all()


@bot.command(name="bj", aliases=["blackjack"])
async def cmd_bj(ctx, mise: str = None):
    """Démarre une partie de blackjack jouable avec des boutons."""
    uid = ctx.author.id
    gid = ctx.guild.id if ctx.guild else 0
    key = (gid, uid)

    if key in active_bj:
        await ctx.send("❌ Vous avez déjà une partie en cours !")
        return

    if not mise:
        embed = discord.Embed(title="🃏 Blackjack", color=0x27ae60, description=(
            "Lance une partie avec une mise, puis joue avec les **boutons** !\n\n"
            "**Usage :** `!bj <mise>` ou `!bj all`\n"
            "Ex : `!bj 100` · `!bj all`"
        ))
        await ctx.send(embed=embed)
        return

    mise, err = _resolve_mise(mise, uid, 'bj')
    if err:
        return await ctx.send(err)

    game = BlackjackGame(mise)
    coins[uid] -= mise
    active_bj[key] = game
    save_data()

    if game.natural():
        winnings = int(mise * 2.5)
        coins[uid] += winnings
        active_bj.pop(key, None); save_data()
        embed = _bj_embed(game, reveal=True, title="🃏 Blackjack — BLACKJACK NATUREL !")
        embed.color = 0xf1c40f
        embed.add_field(name="🎉 Blackjack naturel !",
                        value=f"+{winnings - mise:,} coins (×2.5)", inline=False)
        embed.add_field(name="💳 Solde", value=f"{coins[uid]:,} coins", inline=True)
        await ctx.send(embed=embed)
        return

    view = BlackjackView(uid, key, game)
    # Si le solde ne permet pas de doubler, on désactive le bouton dès le départ
    if coins[uid] < game.bet:
        view.double_btn.disabled = True
    await ctx.send(embed=_bj_embed(game), view=view)


@bot.command(name="coinflip", aliases=["cf"])
async def cmd_coinflip(ctx, mise: str, choix: str):
    choix = choix.lower()
    if choix not in ('pile', 'face', 'p', 'f'):
        await ctx.send("❌ Choisissez `pile` ou `face`."); return
    mise, err = _resolve_mise(mise, ctx.author.id, 'coinflip')
    if err: return await ctx.send(err)

    result = random.choice(['pile', 'face'])
    player_choice = 'pile' if choix in ('pile', 'p') else 'face'
    result_emoji  = '🟡' if result == 'pile' else '⚪'

    coins[ctx.author.id] -= mise
    if player_choice == result:
        coins[ctx.author.id] += mise * 2
        outcome = f"🎉 **Gagné !** +{mise:,} coins"; color = 0x2ecc71
    else:
        outcome = f"😢 **Perdu !** -{mise:,} coins"; color = 0xe74c3c
    save_data()

    embed = discord.Embed(title="🪙 Pile ou Face", color=color)
    embed.add_field(name="Résultat",    value=f"{result_emoji} **{result.capitalize()}**",  inline=True)
    embed.add_field(name="Votre choix", value=f"**{player_choice.capitalize()}**",          inline=True)
    embed.add_field(name="📊",          value=outcome,                                       inline=False)
    embed.add_field(name="💰 Solde",    value=f"{coins[ctx.author.id]:,} coins",            inline=True)
    await ctx.send(embed=embed)


@bot.command(name="duel")
async def cmd_duel(ctx, member: discord.Member, mise: str):
    if member.id == ctx.author.id:
        await ctx.send("❌ Vous ne pouvez pas vous défier vous-même."); return
    if member.bot:
        await ctx.send("❌ Vous ne pouvez pas défier un bot."); return
    mise, err = _resolve_mise(mise, ctx.author.id, 'duel')
    if err: return await ctx.send(err)
    if coins[ctx.author.id] < mise:
        await ctx.send(f"❌ {ctx.author.mention}, vous n'avez pas assez de coins."); return
    if coins[member.id] < mise:
        await ctx.send(f"❌ {member.mention} n'a pas assez de coins."); return

    embed = discord.Embed(
        title="⚔️ Défi lancé !",
        description=(
            f"{ctx.author.mention} défie {member.mention} pour **{mise:,} 🪙 coins** !\n\n"
            f"{member.mention}, tapez `!accept` dans les 30 secondes pour accepter."
        ),
        color=0xe67e22
    )
    await ctx.send(embed=embed)

    def check(m):
        return m.author == member and m.channel == ctx.channel and m.content.lower() == '!accept'

    try:
        await bot.wait_for('message', check=check, timeout=30.0)
    except asyncio.TimeoutError:
        await ctx.send(f"⏰ {member.mention} n'a pas accepté le défi à temps. Duel annulé."); return

    if coins[ctx.author.id] < mise or coins[member.id] < mise:
        await ctx.send("❌ Un des joueurs n'a plus assez de coins."); return

    winner = random.choice([ctx.author, member])
    loser  = member if winner == ctx.author else ctx.author
    coins[ctx.author.id] -= mise
    coins[member.id]      -= mise
    coins[winner.id]      += mise * 2
    save_data()

    embed = discord.Embed(
        title="⚔️ Duel — Résultat !",
        description=(
            f"🏆 {winner.mention} remporte le duel et gagne **{mise * 2:,} 🪙 coins** !\n"
            f"😢 {loser.mention} perd **{mise:,} coins**."
        ),
        color=0xf1c40f
    )
    embed.add_field(name=f"💰 {ctx.author.display_name}", value=f"{coins[ctx.author.id]:,} coins", inline=True)
    embed.add_field(name=f"💰 {member.display_name}",     value=f"{coins[member.id]:,} coins",     inline=True)
    await ctx.send(embed=embed)


@bot.command(name="classement", aliases=["top", "leaderboard", "lb"])
async def cmd_classement(ctx):
    guild_members = {m.id for m in ctx.guild.members if not m.bot}
    totals = []
    for uid, amt in coins.items():
        if uid in guild_members:
            coffre = safes.get(str(uid), 0)
            totals.append((uid, amt + coffre))
    top = sorted(totals, key=lambda x: x[1], reverse=True)[:10]
    if not top:
        await ctx.send("Aucun joueur avec des coins sur ce serveur."); return
    medals = ['🥇','🥈','🥉'] + ['🔹'] * 7
    lines  = []
    for i, (uid, amt) in enumerate(top):
        m    = ctx.guild.get_member(uid)
        name = m.display_name if m else f"<@{uid}>"
        cash   = coins[uid]
        coffre = safes.get(str(uid), 0)
        lines.append(f"{medals[i]} **{name}** — {amt:,} coins *(💵 {cash:,} + 🔒 {coffre:,})*")
    embed = discord.Embed(title="🏆 Classement des Coins", description='\n'.join(lines), color=0xf1c40f)
    await ctx.send(embed=embed)


async def _poker_end_if_one_left(channel, guild, game, gid):
    """Si un seul joueur actif reste, lui donne le pot. Retourne True si la main est finie."""
    if len(game.active()) != 1:
        return False
    winner_id = game.active()[0]
    wm = guild.get_member(winner_id) if guild else None
    game.stacks[winner_id] += game.pot
    game.pot = 0
    for p in game.players:
        if game.stacks[p] > 0:
            coins[p] += game.stacks[p]
    poker_games.pop(gid, None)
    save_data()
    embed = discord.Embed(
        title="🏆 Poker — Victoire !",
        description=(
            f"Tous les adversaires se sont couchés !\n"
            f"🏆 {wm.mention if wm else f'<@{winner_id}>'} remporte le pot !"
        ),
        color=0xf1c40f
    )
    await channel.send(embed=embed)
    return True


class PokerRaiseModal(discord.ui.Modal, title="🃏 Relancer (raise)"):
    montant = discord.ui.TextInput(label="Montant de la relance", placeholder="Ex : 200", required=True, max_length=15)

    def __init__(self, gid, uid):
        super().__init__()
        self.gid = gid
        self.uid = uid

    async def on_submit(self, interaction: discord.Interaction):
        game = poker_games.get(self.gid)
        if not game or game.phase in ('waiting', 'showdown'):
            return await interaction.response.send_message("❌ Aucune partie active.", ephemeral=True)
        if game.current_player() != self.uid:
            return await interaction.response.send_message("❌ Ce n'est plus votre tour.", ephemeral=True)
        try:
            rb = int(str(self.montant.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        if rb <= 0:
            return await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        if game.stacks[self.uid] < game.to_call(self.uid) + rb:
            return await interaction.response.send_message("❌ Pas assez de chips. Utilisez le bouton All-in.", ephemeral=True)
        game.do_raise(self.uid, rb)
        await interaction.response.send_message(
            f"🃏 <@{self.uid}> **relance** de {rb:,} coins !"
        )
        await _poker_after_action(interaction.channel, interaction.guild, game, self.gid)


class PokerActionView(discord.ui.View):
    def __init__(self, gid):
        super().__init__(timeout=600)
        self.gid = gid

    async def _check_turn(self, interaction):
        game = poker_games.get(self.gid)
        if not game or game.phase in ('waiting', 'showdown'):
            await interaction.response.send_message("❌ Aucune partie active.", ephemeral=True)
            return None
        if game.current_player() != interaction.user.id:
            await interaction.response.send_message(
                f"❌ Ce n'est pas votre tour ! C'est au tour de <@{game.current_player()}>.",
                ephemeral=True
            )
            return None
        return game

    @discord.ui.button(label="Se coucher", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def fold_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = await self._check_turn(interaction)
        if not game: return
        game.do_fold(interaction.user.id)
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"🃏 {interaction.user.mention} **se couche** (fold).")
        await _poker_after_action(interaction.channel, interaction.guild, game, self.gid)

    @discord.ui.button(label="Suivre", style=discord.ButtonStyle.primary, emoji="✅")
    async def call_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = await self._check_turn(interaction)
        if not game: return
        tc = game.to_call(interaction.user.id)
        if tc == 0:
            return await interaction.response.send_message("❌ Rien à suivre — utilisez **Checker**.", ephemeral=True)
        game.do_call(interaction.user.id)
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"🃏 {interaction.user.mention} **suit** ({tc:,} coins).")
        await _poker_after_action(interaction.channel, interaction.guild, game, self.gid)

    @discord.ui.button(label="Checker", style=discord.ButtonStyle.secondary, emoji="👌")
    async def check_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = await self._check_turn(interaction)
        if not game: return
        if game.to_call(interaction.user.id) > 0:
            return await interaction.response.send_message(
                f"❌ Vous devez payer {game.to_call(interaction.user.id):,} coins (utilisez **Suivre** ou **Se coucher**).",
                ephemeral=True
            )
        game.do_check(interaction.user.id)
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"🃏 {interaction.user.mention} **checke**.")
        await _poker_after_action(interaction.channel, interaction.guild, game, self.gid)

    @discord.ui.button(label="Relancer", style=discord.ButtonStyle.success, emoji="💸")
    async def raise_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = poker_games.get(self.gid)
        if not game or game.phase in ('waiting', 'showdown'):
            return await interaction.response.send_message("❌ Aucune partie active.", ephemeral=True)
        if game.current_player() != interaction.user.id:
            return await interaction.response.send_message(
                f"❌ Ce n'est pas votre tour ! C'est au tour de <@{game.current_player()}>.",
                ephemeral=True
            )
        await interaction.response.send_modal(PokerRaiseModal(self.gid, interaction.user.id))

    @discord.ui.button(label="All-in", style=discord.ButtonStyle.danger, emoji="🔥")
    async def allin_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = await self._check_turn(interaction)
        if not game: return
        game.do_allin(interaction.user.id)
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"🃏 {interaction.user.mention} est **all-in** ! 🔥")
        await _poker_after_action(interaction.channel, interaction.guild, game, self.gid)

    @discord.ui.button(label="Voir mes cartes", style=discord.ButtonStyle.secondary, emoji="🔍", row=1)
    async def show_cards_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = poker_games.get(self.gid)
        if not game or game.phase in ('waiting', 'showdown'):
            return await interaction.response.send_message("❌ Aucune partie active.", ephemeral=True)
        uid = interaction.user.id
        if uid not in game.players:
            return await interaction.response.send_message("❌ Vous n'êtes pas à cette table.", ephemeral=True)
        if uid not in game.hands:
            return await interaction.response.send_message("❌ Vous n'avez pas de cartes.", ephemeral=True)
        h = game.hands[uid]
        embed = discord.Embed(
            title="🃏 Vos cartes secrètes",
            description=f"## {_card(h[0])}  {_card(h[1])}",
            color=0x9b59b6
        )
        if game.community:
            embed.add_field(name="🎴 Cartes communes", value=_hand(game.community), inline=False)
        embed.set_footer(text="Seul vous voyez ce message.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def _poker_after_action(channel, guild, game, gid):
    """Logique post-action : vérif fin de main, avance des streets, prompt suivant."""
    if await _poker_end_if_one_left(channel, guild, game, gid):
        return
    if game.street_over():
        await _poker_advance_streets(channel, game, gid)
    else:
        await _poker_prompt(channel, game, gid)


class PokerLobbyView(discord.ui.View):
    def __init__(self, gid, host_id):
        super().__init__(timeout=600)
        self.gid = gid
        self.host_id = host_id

    @discord.ui.button(label="Rejoindre la table", style=discord.ButtonStyle.success, emoji="✋")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = poker_games.get(self.gid)
        if not game:
            return await interaction.response.send_message("❌ Table introuvable.", ephemeral=True)
        if game.phase != 'waiting':
            return await interaction.response.send_message("❌ La partie a déjà commencé.", ephemeral=True)
        uid = interaction.user.id
        if uid in game.players:
            return await interaction.response.send_message("❌ Vous êtes déjà à la table.", ephemeral=True)
        if len(game.players) >= 8:
            return await interaction.response.send_message("❌ La table est complète (8 joueurs max).", ephemeral=True)
        if coins[uid] < game.ante * 10:
            return await interaction.response.send_message(
                f"❌ Il vous faut au moins **{game.ante * 10:,} coins** pour rejoindre.",
                ephemeral=True
            )
        buy_in = min(game.ante * 20, coins[uid])
        game.add_player(uid, buy_in)
        coins[uid] -= buy_in
        save_data()
        lines = [f"{i+1}. <@{p}> — {game.stacks[p]:,} chips" for i, p in enumerate(game.players)]
        embed = discord.Embed(
            title="🃏 Table de Poker",
            description=(
                f"**Ante :** {game.ante:,} coins\n\n"
                f"Cliquez sur **Rejoindre** pour participer.\n"
                f"L'hôte (<@{self.host_id}>) lance la partie avec **Démarrer**."
            ),
            color=0x8e44ad
        )
        embed.add_field(name=f"👥 Joueurs ({len(game.players)}/8)", value='\n'.join(lines), inline=False)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(
            f"✅ {interaction.user.mention} a rejoint avec **{buy_in:,} chips** !"
        )

    @discord.ui.button(label="Démarrer (hôte)", style=discord.ButtonStyle.primary, emoji="▶️")
    async def begin_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = poker_games.get(self.gid)
        if not game:
            return await interaction.response.send_message("❌ Table introuvable.", ephemeral=True)
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message("❌ Seul l'hôte peut lancer la partie.", ephemeral=True)
        if game.phase != 'waiting':
            return await interaction.response.send_message("❌ La partie a déjà commencé.", ephemeral=True)
        if len(game.players) < 2:
            return await interaction.response.send_message("❌ Il faut au moins 2 joueurs.", ephemeral=True)
        game.start()
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        mentions = ', '.join(f"<@{p}>" for p in game.players)
        await interaction.channel.send(
            f"🃏 **La partie commence !** Joueurs : {mentions}\n"
            f"📋 Cliquez sur le bouton **🔍 Voir mes cartes** dans les embeds d'action "
            f"pour consulter votre main (visible uniquement par vous)."
        )
        await _poker_status_embed(interaction.channel, game)
        await _poker_prompt(interaction.channel, game, self.gid)

    @discord.ui.button(label="Annuler (hôte)", style=discord.ButtonStyle.secondary, emoji="🚫")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = poker_games.get(self.gid)
        if not game:
            return await interaction.response.send_message("❌ Table introuvable.", ephemeral=True)
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message("❌ Seul l'hôte peut annuler la table.", ephemeral=True)
        if game.phase != 'waiting':
            return await interaction.response.send_message("❌ La partie a déjà commencé, impossible d'annuler.", ephemeral=True)
        # Rembourser tout le monde
        for p in game.players:
            coins[p] += game.stacks.get(p, 0)
        poker_games.pop(self.gid, None)
        save_data()
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("🚫 La table a été annulée. Buy-ins remboursés.")


@bot.command(name="poker")
async def cmd_poker(ctx, action: str = None, *, args: str = None):
    gid = ctx.guild.id
    uid = ctx.author.id

    if action is None:
        embed = discord.Embed(title="🃏 Poker — Texas Hold'em", color=0x8e44ad, description=(
            "**Lancer une partie :**\n"
            "`!poker start <ante>` — Créer une table\n\n"
            "Ensuite, **tout se joue avec les boutons** :\n"
            "• ✋ Rejoindre / ▶️ Démarrer dans le lobby\n"
            "• 🏳️ Fold / ✅ Call / 👌 Check / 💸 Raise / 🔥 All-in pendant la partie\n\n"
            "`!poker status` — État de la table\n"
            "*(Les commandes texte fold/call/etc. restent disponibles en fallback.)*"
        ))
        await ctx.send(embed=embed)
        return

    action = action.lower()

    if action == 'start':
        if gid in poker_games:
            await ctx.send("❌ Une table est déjà ouverte sur ce serveur."); return
        try:    ante = int(args) if args else 100
        except: ante = 100
        if ante <= 0:
            await ctx.send("❌ L'ante doit être supérieure à 0."); return
        # Vérification limites min/max pour le poker
        limit_err = _check_bet_limits('poker', ante)
        if limit_err:
            await ctx.send(limit_err); return
        if coins[uid] < ante * 10:
            await ctx.send(f"❌ Il vous faut au moins **{ante * 10:,} coins** pour créer une table."); return
        buy_in = min(ante * 20, coins[uid])
        game   = PokerGame(uid, ante, ctx.channel.id)
        game.add_player(uid, buy_in)
        coins[uid] -= buy_in
        poker_games[gid] = game
        save_data()
        embed = discord.Embed(
            title="🃏 Table de Poker",
            description=(
                f"**Ante :** {ante:,} coins\n\n"
                f"Cliquez sur **Rejoindre** pour participer.\n"
                f"L'hôte ({ctx.author.mention}) lance la partie avec **Démarrer**."
            ),
            color=0x8e44ad
        )
        embed.add_field(name="👥 Joueurs (1/8)", value=f"1. {ctx.author.mention} — {buy_in:,} chips", inline=False)
        await ctx.send(embed=embed, view=PokerLobbyView(gid, uid))

    elif action == 'join':
        if gid not in poker_games:
            await ctx.send("❌ Aucune table en cours. Créez-en une avec `!poker start <ante>`."); return
        game = poker_games[gid]
        if game.phase != 'waiting':
            await ctx.send("❌ La partie a déjà commencé."); return
        if uid in game.players:
            await ctx.send("❌ Vous êtes déjà à la table."); return
        if len(game.players) >= 8:
            await ctx.send("❌ La table est complète (8 joueurs max)."); return
        if coins[uid] < game.ante * 10:
            await ctx.send(f"❌ Il vous faut au moins **{game.ante * 10:,} coins** pour rejoindre."); return
        buy_in = min(game.ante * 20, coins[uid])
        game.add_player(uid, buy_in)
        coins[uid] -= buy_in
        save_data()
        lines = [f"{i+1}. <@{p}> — {game.stacks[p]:,} chips" for i, p in enumerate(game.players)]
        embed = discord.Embed(
            title="🃏 Joueur rejoint !",
            description=f"{ctx.author.mention} a rejoint la table avec **{buy_in:,} chips** !",
            color=0x8e44ad
        )
        embed.add_field(name=f"👥 Joueurs ({len(game.players)}/8)", value='\n'.join(lines), inline=False)
        await ctx.send(embed=embed)

    elif action == 'begin':
        if gid not in poker_games:
            await ctx.send("❌ Aucune table en cours."); return
        game = poker_games[gid]
        if game.host_id != uid:
            await ctx.send("❌ Seul le créateur peut lancer la partie."); return
        if game.phase != 'waiting':
            await ctx.send("❌ La partie a déjà commencé."); return
        if len(game.players) < 2:
            await ctx.send("❌ Il faut au moins 2 joueurs."); return
        game.start()
        mentions = ', '.join(f"<@{p}>" for p in game.players)
        await ctx.send(
            f"🃏 **La partie commence !** Joueurs : {mentions}\n"
            f"📋 Cliquez sur **🔍 Voir mes cartes** dans les embeds d'action "
            f"pour consulter votre main (visible uniquement par vous)."
        )
        await _poker_status_embed(ctx, game)
        await _poker_prompt(ctx, game, gid)

    elif action == 'status':
        if gid not in poker_games:
            await ctx.send("❌ Aucune table de poker en cours."); return
        await _poker_status_embed(ctx, poker_games[gid])

    elif action in ('fold', 'call', 'check', 'raise', 'allin'):
        if gid not in poker_games:
            await ctx.send("❌ Aucune table en cours."); return
        game = poker_games[gid]
        if game.phase in ('waiting', 'showdown'):
            await ctx.send("❌ Aucune partie active en ce moment."); return
        cp = game.current_player()
        if cp != uid:
            await ctx.send(f"❌ Ce n'est pas votre tour ! C'est au tour de <@{cp}>."); return

        if action == 'fold':
            game.do_fold(uid)
            await ctx.send(f"🃏 {ctx.author.mention} **se couche** (fold).")

        elif action == 'call':
            tc = game.to_call(uid)
            if tc == 0:
                await ctx.send("❌ Rien à suivre. Utilisez `!poker check`."); return
            game.do_call(uid)
            await ctx.send(f"🃏 {ctx.author.mention} **suit** ({tc:,} coins).")

        elif action == 'check':
            if game.to_call(uid) > 0:
                await ctx.send(
                    f"❌ Vous devez payer {game.to_call(uid):,} coins. "
                    f"Utilisez `!poker call` ou `!poker fold`."
                ); return
            game.do_check(uid)
            await ctx.send(f"🃏 {ctx.author.mention} **checke**.")

        elif action == 'raise':
            try:    rb = int(args) if args else game.ante
            except: rb = game.ante
            if rb <= 0:
                await ctx.send("❌ Montant invalide."); return
            if game.stacks[uid] < game.to_call(uid) + rb:
                await ctx.send(f"❌ Pas assez de chips. Utilisez `!poker allin`."); return
            game.do_raise(uid, rb)
            await ctx.send(f"🃏 {ctx.author.mention} **relance** de {rb:,} coins !")

        elif action == 'allin':
            game.do_allin(uid)
            await ctx.send(f"🃏 {ctx.author.mention} est **all-in** ! 🔥")

        # Vérifier s'il ne reste qu'un joueur actif
        if len(game.active()) == 1:
            winner_id = game.active()[0]
            wm = ctx.guild.get_member(winner_id)
            game.stacks[winner_id] += game.pot
            game.pot = 0
            for p in game.players:
                if game.stacks[p] > 0:
                    coins[p] += game.stacks[p]
            del poker_games[gid]
            save_data()
            embed = discord.Embed(
                title="🏆 Poker — Victoire !",
                description=(
                    f"Tous les adversaires se sont couchés !\n"
                    f"🏆 {wm.mention if wm else f'<@{winner_id}>'} remporte le pot !"
                ),
                color=0xf1c40f
            )
            await ctx.send(embed=embed)
            return

        # Avancer les streets ou demander l'action suivante
        if game.street_over():
            await _poker_advance_streets(ctx, game, gid)
        else:
            await _poker_prompt(ctx, game, gid)

    else:
        await ctx.send("❌ Action inconnue. Tapez `!poker` pour l'aide.")


async def _poker_advance_streets(target, game, gid):
    """Avance automatiquement les streets tant que personne ne peut agir (all-in) ou jusqu'au showdown."""
    LABELS = {'flop': 'Flop', 'turn': 'Turn', 'river': 'River'}
    while game.street_over():
        if game.phase == 'river':
            await _poker_showdown(target, game, gid)
            return
        game.next_street()
        if game.phase == 'showdown':
            await _poker_showdown(target, game, gid)
            return
        embed = discord.Embed(
            title=f"🃏 Poker — {LABELS.get(game.phase, game.phase.capitalize())}",
            color=0x8e44ad,
            description="*(Tous les joueurs sont all-in — cartes révélées automatiquement)*"
        )
        embed.add_field(name="🎴 Cartes communes", value=_hand(game.community), inline=False)
        embed.add_field(name="💰 Pot", value=f"{game.pot:,} coins", inline=True)
        await target.send(embed=embed)
        await asyncio.sleep(2)
    await _poker_prompt(target, game, gid)


async def _poker_status_embed(target, game):
    cp    = game.current_player() if game.phase not in ('waiting', 'showdown') else None
    lines = []
    for p in game.players:
        if   p in game.folded:     st = "❌ Couché"
        elif p in game.all_in_set: st = "🔵 All-in"
        else:                       st = "✅ En jeu"
        arrow = " 👈 **(à jouer)**" if cp == p else ""
        lines.append(f"<@{p}> — **{game.stacks[p]:,}** chips — {st}{arrow}")
    embed = discord.Embed(title="🃏 Poker — État de la table", color=0x8e44ad)
    embed.add_field(name="👥 Joueurs", value='\n'.join(lines) or "Aucun", inline=False)
    if game.phase not in ('waiting', 'showdown'):
        embed.add_field(name="Phase",  value=game.phase.capitalize(), inline=True)
        embed.add_field(name="💰 Pot", value=f"{game.pot:,} coins",   inline=True)
        if game.community:
            embed.add_field(name="🎴 Cartes communes", value=_hand(game.community), inline=False)
    await target.send(embed=embed)


async def _poker_prompt(target, game, gid):
    cp = game.current_player()
    if cp is None: return
    tc = game.to_call(cp)
    actions_help = (
        f"✅ **Suivre** ({tc:,}) · 🏳️ **Fold** · 💸 **Raise** · 🔥 **All-in**"
        if tc > 0
        else "👌 **Check** · 💸 **Raise** · 🔥 **All-in**"
    )
    embed = discord.Embed(
        title="🃏 À vous de jouer !",
        description=f"<@{cp}>, c'est votre tour !\n\n{actions_help}",
        color=0x9b59b6
    )
    embed.add_field(name="💰 Pot",        value=f"{game.pot:,} coins",         inline=True)
    embed.add_field(name="💳 Vos chips",  value=f"{game.stacks[cp]:,} coins",  inline=True)
    if game.community:
        embed.add_field(name="🎴 Communes", value=_hand(game.community), inline=False)
    await target.send(embed=embed, view=PokerActionView(gid))


async def _poker_showdown(target, game, gid):
    scores = {p: _best_hand(game.hands[p], game.community) for p in game.active()}
    embed  = discord.Embed(title="🃏 Poker — Showdown !", color=0xf1c40f)
    if game.community:
        embed.add_field(name="🎴 Cartes communes", value=_hand(game.community), inline=False)
    for p in game.active():
        s         = scores.get(p)
        hand_name = HAND_NAMES[s[0]] if s is not None else "?"
        embed.add_field(name=f"<@{p}> — {hand_name}", value=_hand(game.hands[p]), inline=False)
    pot_total    = game.pot
    winners_list = game.winners()
    game.pay_out(winners_list)
    for p in game.players:
        if game.stacks[p] > 0:
            coins[p] += game.stacks[p]
    share        = pot_total // len(winners_list) if winners_list else 0
    winners_str  = ', '.join(f"<@{w}>" for w in winners_list)
    embed.add_field(
        name="🏆 Gagnant(s)",
        value=f"{winners_str} remporte(nt) **{share:,} coins** chacun !",
        inline=False
    )
    embed.add_field(name="💰 Pot total", value=f"{pot_total:,} coins", inline=True)
    del poker_games[gid]
    save_data()
    await target.send(embed=embed)


# =======================================================================
# =================== SYSTÈMES AVANCÉS ==================================
# =======================================================================

# ── Helpers ──────────────────────────────────────────────────────────────

def _resolve_mise(raw, uid: int, game: str = None):
    """Résout 'all'/'tout' en solde total. Retourne (montant: int, erreur: str|None).
    Si 'game' est fourni, applique aussi les limites min/max de casino_config."""
    if isinstance(raw, str) and raw.lower() in ('all', 'tout'):
        amount = coins[uid]
        if amount <= 0:
            return 0, "❌ Vous n'avez pas de coins à miser."
    else:
        try:
            amount = int(raw)
        except (ValueError, TypeError):
            return 0, "❌ Mise invalide. Entrez un nombre ou `all`."
        if amount <= 0:
            return 0, "❌ La mise doit être supérieure à 0."
        if coins[uid] < amount:
            return 0, f"❌ Pas assez de coins. Solde : **{coins[uid]:,} coins**"
    if game:
        # Vérification des limites de mise pour ce jeu
        err = _check_bet_limits(game, amount)
        if err:
            return 0, err
    return amount, None

def _has_item(uid: int, item_id: int) -> bool:
    return owned_items.get(str(uid), {}).get(str(item_id), 0) > 0

def _use_item(uid: int, item_id: int):
    oi = owned_items.setdefault(str(uid), {})
    if oi.get(str(item_id), 0) > 0:
        oi[str(item_id)] -= 1
        if oi[str(item_id)] == 0:
            del oi[str(item_id)]

def _get_job(uid: int) -> str:
    return jobs_data.get(str(uid), {}).get('job', '')

def _cd_ok(cd_dict: dict, uid, hours: float):
    """Returns (can_act, wait_str). Sets cooldown if can_act."""
    key = str(uid)
    now = datetime.now()
    if key in cd_dict:
        last = datetime.fromisoformat(cd_dict[key])
        wait = last + timedelta(hours=hours) - now
        if wait.total_seconds() > 0:
            h, rem = divmod(int(wait.total_seconds()), 3600)
            m = rem // 60
            return False, f"**{h}h {m}min**"
    cd_dict[key] = now.isoformat()
    return True, ""

def _factory_rate(workers: int, upgraded: bool) -> float:
    """Taux horaire total : 50+100+...+(workers×50) = 50×n×(n+1)/2"""
    base = 50 * workers * (workers + 1) / 2
    return base * 1.15 if upgraded else base

def _factory_cost_next(current_workers: int):
    """Prix du prochain employé. Retourne None si le maximum est atteint."""
    if current_workers >= MAX_FACTORY_WORKERS:
        return None
    costs = casino_config.get('factory_costs') or DEFAULT_FACTORY_COSTS
    if current_workers >= len(costs):
        return None
    return costs[current_workers]

def _factory_hire_remaining(uid_str: str):
    """Retourne le nombre de secondes restantes avant la prochaine embauche, ou 0."""
    f = factories.get(uid_str)
    if not f: return 0
    last_iso = f.get('last_hire')
    if not last_iso: return 0
    try:
        last = datetime.fromisoformat(last_iso)
    except ValueError:
        return 0
    cd = cooldown_h('embaucher') * 3600
    elapsed = (datetime.now() - last).total_seconds()
    return max(0, cd - elapsed)

def _factory_earnings(uid_str: str) -> int:
    f = factories.get(uid_str)
    if not f or f.get('workers', 0) == 0: return 0
    last      = datetime.fromisoformat(f['last'])
    hours     = (datetime.now() - last).total_seconds() / 3600
    upgraded  = f.get('upgraded') or _has_item(int(uid_str), 6)
    rate      = _factory_rate(f['workers'], upgraded)
    earn      = rate * hours
    return int(min(earn, rate * 168))  # cap 1 semaine

def _race_odds(idx: int) -> float:
    d  = race_drivers_live[idx]
    wr = d['wins'] / max(d['races'], 1)
    return round(max(1.1, 1 / max(wr, 0.05)), 2)


# ── Tâche de fond : prix crypto ──────────────────────────────────────────

def _crypto_news_embed(symbol, direction, old_price, new_price):
    name = CRYPTO_DISPLAY.get(symbol, symbol)
    pct  = ((new_price - old_price) / old_price * 100) if old_price else 0
    if direction == 'up':
        headline, color = random.choice(CRYPTO_NEWS_UP), 0x2ecc71
    else:
        headline, color = random.choice(CRYPTO_NEWS_DOWN), 0xe74c3c
    embed = discord.Embed(title="📰 News Crypto", description=headline.format(name=name), color=color)
    embed.add_field(name="Cours actuel", value=f"**{new_price:,.2f}** coins ({pct:+.1f}%)", inline=True)
    embed.set_footer(text="!crypto pour tous les cours · !graphique <SYM>")
    return embed


@tasks.loop(seconds=90)
async def update_crypto_prices():
    """Modèle de marché simulé : momentum (tendances), retour à la moyenne,
    volatilité propre à chaque crypto et 'news' occasionnelles (pumps/dumps)."""
    news = []  # (symbol, direction, old_price)
    for s in CRYPTO_SYMBOLS:
        price = crypto_prices[s]
        base  = CRYPTO_BASE[s]
        vol   = CRYPTO_VOL.get(s, 0.02)

        # 1) Tendance persistante (momentum) — processus AR(1) :
        #    une hausse/baisse a tendance à se prolonger sur plusieurs ticks.
        trend = crypto_trends.get(s, 0.0) * 0.85 + random.gauss(0, vol * 0.5)

        # 2) Évènement "news" rare et irrégulier (~1.2% par crypto par tick) :
        #    choc marqué qui lance une vraie tendance haussière ou baissière.
        if random.random() < 0.012:
            shock  = random.gauss(0, vol * 4)
            trend += shock
            news.append((s, 'up' if shock >= 0 else 'down', price))

        # 3) Retour à la moyenne : le prix est doucement ramené vers sa base,
        #    d'autant plus fort qu'il s'en est éloigné.
        reversion = 0.02 * (base - price) / base

        # 4) Bruit de marché aléatoire de faible amplitude.
        noise = random.gauss(0, vol)

        change    = trend + reversion + noise
        new_price = price * (1 + change)

        # Bornes : on évite 0 et les valeurs absurdes (entre 20% et 500% de la base).
        new_price = round(max(base * 0.2, min(base * 5, new_price)), 2)

        crypto_prices[s] = new_price
        crypto_trends[s] = trend
        hist = price_history.setdefault(s, [])
        hist.append(new_price)
        if len(hist) > 30:
            price_history[s] = hist[-30:]
    save_data()

    # Annonce des news dans le salon dédié (si configuré)
    if news and NEWS_CRYPTO_CHANNEL_ID:
        channel = bot.get_channel(NEWS_CRYPTO_CHANNEL_ID)
        if channel:
            for s, direction, old_price in news:
                try:
                    await channel.send(embed=_crypto_news_embed(s, direction, old_price, crypto_prices[s]))
                except discord.HTTPException:
                    pass

@update_crypto_prices.before_loop
async def _before_crypto():
    await bot.wait_until_ready()


# ── Mines (boutons interactifs) ──────────────────────────────────────────

class MinesView(discord.ui.View):
    def __init__(self, author_id: int, bet: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.bet       = bet
        self.diamonds  = 0
        self.game_over = False
        self.bomb_pos  = set(random.sample(range(12), 3))
        self._pfx      = random.randint(10**6, 10**7 - 1)

        for i in range(12):
            btn = discord.ui.Button(
                label="?", style=discord.ButtonStyle.secondary,
                custom_id=f"mn_{self._pfx}_{i}", row=i // 4
            )
            btn.callback = self._make_cb(i)
            self.add_item(btn)

        cash_btn = discord.ui.Button(
            label="💰 Encaisser", style=discord.ButtonStyle.success,
            custom_id=f"mn_{self._pfx}_co", row=3
        )
        cash_btn.callback = self._cashout
        self.add_item(cash_btn)

    def _mult(self) -> float:
        return round(1.0 + self.diamonds * 0.1, 2)

    def _payout(self) -> int:
        return int(self.bet * self._mult())

    def _make_cb(self, idx: int):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                return await interaction.response.send_message("❌ Ce n'est pas votre jeu !", ephemeral=True)
            if self.game_over:
                return await interaction.response.send_message("❌ Partie terminée.", ephemeral=True)
            btn = next((c for c in self.children if getattr(c, 'custom_id', '') == f"mn_{self._pfx}_{idx}"), None)
            if btn and btn.disabled:
                return await interaction.response.send_message("❌ Case déjà révélée.", ephemeral=True)
            if idx in self.bomb_pos:
                self.game_over = True
                for c in self.children:
                    c.disabled = True
                    cid = getattr(c, 'custom_id', '')
                    suffix = cid.split('_')[-1]
                    if suffix.isdigit():
                        pos = int(suffix)
                        if pos in self.bomb_pos:
                            c.label = "💣"; c.style = discord.ButtonStyle.danger
                embed = discord.Embed(title="💣 BOOM ! — Mines", color=0xe74c3c,
                    description=f"Vous avez touché une bombe !\nMise perdue : **{self.bet:,} coins** 😢")
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                self.diamonds += 1
                if btn:
                    btn.label = "💎"; btn.style = discord.ButtonStyle.primary; btn.disabled = True
                if self.diamonds == 9:
                    self.game_over = True
                    for c in self.children: c.disabled = True
                    win = self._payout()
                    coins[self.author_id] += win
                    save_data()
                    embed = discord.Embed(title="💎 MINES — Victoire totale !", color=0xf1c40f,
                        description=f"Vous avez trouvé **tous** les diamants !\n🎉 +**{win - self.bet:,} coins** (×{self._mult()})")
                else:
                    embed = discord.Embed(title="💎 Mines", color=0x3498db,
                        description=(
                            f"💎 **{self.diamonds}** diamant(s) trouvé(s)\n"
                            f"Multiplicateur : **×{self._mult():.1f}**\n"
                            f"Gain potentiel : **{self._payout():,} coins**\n\n"
                            "Continuez ou encaissez !"
                        ))
                await interaction.response.edit_message(embed=embed, view=self)
        return cb

    async def _cashout(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Ce n'est pas votre jeu !", ephemeral=True)
        if self.game_over:
            return await interaction.response.send_message("❌ Partie déjà terminée.", ephemeral=True)
        if self.diamonds == 0:
            return await interaction.response.send_message("❌ Révélez au moins une case avant d'encaisser !", ephemeral=True)
        self.game_over = True
        win = self._payout()
        coins[self.author_id] += win
        save_data()
        for c in self.children: c.disabled = True
        embed = discord.Embed(title="💰 Mines — Encaissé !", color=0x2ecc71,
            description=(
                f"Vous encaissez **{win:,} coins** (×{self._mult():.1f}) !\n"
                f"Profit net : **+{win - self.bet:,} coins** 🎉"
            ))
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        if not self.game_over and self.diamonds > 0:
            self.game_over = True
            win = self._payout()
            coins[self.author_id] += win
            save_data()


# ── !mines ───────────────────────────────────────────────────────────────

@bot.command(name="mines")
async def cmd_mines(ctx, mise: str):
    mise, err = _resolve_mise(mise, ctx.author.id, 'mines')
    if err: return await ctx.send(err)
    coins[ctx.author.id] -= mise
    save_data()
    view  = MinesView(ctx.author.id, mise)
    embed = discord.Embed(title="💣 Mines", color=0x3498db, description=(
        "**12 cases** — **3 bombes** cachées\n"
        "Chaque 💎 trouvé ajoute **×0.1** au multiplicateur\n"
        "Touchez une 💣 = mise perdue !\n\n"
        "Cliquez pour révéler une case ou encaissez vos gains."
    ))
    embed.add_field(name="💰 Mise", value=f"{mise:,} coins", inline=True)
    embed.add_field(name="Multiplicateur", value="×1.0", inline=True)
    await ctx.send(embed=embed, view=view)


# ── Crypto ───────────────────────────────────────────────────────────────

@bot.command(name="crypto")
async def cmd_crypto(ctx):
    embed = discord.Embed(title="📈 Marché Crypto (en coins)", color=0xf39c12)
    for s in CRYPTO_SYMBOLS:
        p = crypto_prices[s]
        embed.add_field(name=f"{CRYPTO_DISPLAY[s]} ({s})", value=f"**{p:,.2f}**", inline=True)
    uid = str(ctx.author.id)
    h   = {s: q for s, q in crypto_holdings.get(uid, {}).items() if q > 0.000001}
    if h:
        lines = []
        total = 0
        for s, qty in h.items():
            val = qty * crypto_prices.get(s, 0)
            total += val
            lines.append(f"**{s}** : {qty:.6f} ≈ {val:,.0f} coins")
        embed.add_field(name="💼 Votre portefeuille",
            value='\n'.join(lines) + f"\n📊 Total ≈ **{total:,.0f} coins**", inline=False)
    embed.set_footer(text="Prix actualisés toutes les 90s | !acheter_crypto <SYM> <coins> | !vendre_crypto <SYM> <qté> | !graphique <SYM>")
    await ctx.send(embed=embed)

@bot.command(name="graphique", aliases=["chart", "courbe", "graph"])
async def cmd_graphique(ctx, symbol: str = None):
    if symbol is None:
        return await ctx.send(f"❌ Précisez un symbole. Ex : `!graphique BTC`\nDisponibles : {', '.join(CRYPTO_SYMBOLS)}")
    symbol = symbol.upper()
    if symbol not in CRYPTO_SYMBOLS:
        return await ctx.send(f"❌ Symbole invalide. Disponibles : {', '.join(CRYPTO_SYMBOLS)}")
    history = price_history.get(symbol, [])
    current = crypto_prices[symbol]
    if len(history) < 2:
        return await ctx.send(f"⏳ Pas encore assez de données pour **{symbol}**. Revenez dans ~3 minutes !")
    # Sparkline avec blocs Unicode
    BARS   = '▁▂▃▄▅▆▇█'
    mn, mx = min(history), max(history)
    rng    = mx - mn if mx != mn else 1
    spark  = ''.join(BARS[min(7, int((p - mn) / rng * 7))] for p in history)
    # Variation
    oldest     = history[0]
    change_pct = ((current - oldest) / oldest) * 100
    trend      = "📈" if change_pct >= 0 else "📉"
    color      = 0x2ecc71 if change_pct >= 0 else 0xe74c3c
    sign       = "+" if change_pct >= 0 else ""
    # Dernières variations entre chaque point
    deltas = []
    for i in range(1, min(6, len(history))):
        d = ((history[i] - history[i-1]) / history[i-1]) * 100
        deltas.append(f"{'🟢' if d >= 0 else '🔴'} {'+' if d >= 0 else ''}{d:.2f}%")
    embed = discord.Embed(
        title=f"{trend} {CRYPTO_DISPLAY[symbol]} ({symbol}) — Courbe en temps réel",
        color=color
    )
    embed.add_field(name="📊 Évolution ({} points)".format(len(history)),
        value=f"```{spark}```", inline=False)
    embed.add_field(name="💰 Prix actuel",    value=f"**{current:,.2f} coins**",  inline=True)
    embed.add_field(name="📉 Min période",    value=f"{mn:,.2f} coins",            inline=True)
    embed.add_field(name="📈 Max période",    value=f"{mx:,.2f} coins",            inline=True)
    embed.add_field(name="📊 Variation",      value=f"**{sign}{change_pct:.2f}%** depuis le début", inline=True)
    if deltas:
        embed.add_field(name="⏱️ Dernières variations", value='\n'.join(deltas), inline=False)
    embed.set_footer(text="Mise à jour toutes les 90s | Tapez !crypto pour voir tous les prix")
    await ctx.send(embed=embed)

@bot.command(name="acheter_crypto")
async def cmd_acheter_crypto(ctx, symbol: str, montant: int):
    symbol = symbol.upper()
    if symbol not in CRYPTO_SYMBOLS:
        return await ctx.send(f"❌ Symbole invalide. Disponibles : {', '.join(CRYPTO_SYMBOLS)}")
    if montant <= 0:
        return await ctx.send("❌ Montant invalide.")
    if coins[ctx.author.id] < montant:
        return await ctx.send(f"❌ Pas assez de coins. Solde : **{coins[ctx.author.id]:,} coins**")
    price = crypto_prices[symbol]
    qty   = montant / price
    coins[ctx.author.id] -= montant
    uid   = str(ctx.author.id)
    crypto_holdings.setdefault(uid, {})
    crypto_holdings[uid][symbol] = round(crypto_holdings[uid].get(symbol, 0) + qty, 8)
    save_data()
    embed = discord.Embed(title="💹 Achat Crypto !", color=0x2ecc71, description=(
        f"Vous avez acheté **{qty:.6f} {symbol}** pour **{montant:,} coins**\n"
        f"Prix unitaire : {price:,.2f} coins\n"
        f"💼 {symbol} total : **{crypto_holdings[uid][symbol]:.6f}**"
    ))
    await ctx.send(embed=embed)

@bot.command(name="vendre_crypto")
async def cmd_vendre_crypto(ctx, symbol: str, qty_str: str):
    symbol = symbol.upper()
    if symbol not in CRYPTO_SYMBOLS:
        return await ctx.send(f"❌ Symbole invalide. Disponibles : {', '.join(CRYPTO_SYMBOLS)}")
    uid  = str(ctx.author.id)
    held = crypto_holdings.get(uid, {}).get(symbol, 0)
    if held < 0.000001:
        return await ctx.send(f"❌ Vous ne possédez pas de {symbol}.")
    try:
        qty = held if qty_str.lower() == 'tout' else float(qty_str)
    except ValueError:
        return await ctx.send("❌ Quantité invalide. Ex : `!vendre_crypto BTC 0.5` ou `!vendre_crypto BTC tout`")
    qty = min(max(qty, 0), held)
    if qty < 0.000001:
        return await ctx.send("❌ Quantité invalide.")
    price   = crypto_prices[symbol]
    revenue = qty * price
    bonus   = revenue * 0.15 if (_get_job(ctx.author.id) == 'trader' or _has_item(ctx.author.id, 7)) else 0
    total   = int(revenue + bonus)
    coins[ctx.author.id] += total
    new_qty = round(held - qty, 8)
    if new_qty < 0.000001:
        crypto_holdings[uid].pop(symbol, None)
    else:
        crypto_holdings[uid][symbol] = new_qty
    save_data()
    embed = discord.Embed(title="💹 Vente Crypto !", color=0xe74c3c, description=(
        f"Vendu **{qty:.6f} {symbol}** à **{price:,.2f} coins**\n"
        f"Revenus : **{int(revenue):,} coins**" +
        (f" + bonus trader **+{int(bonus):,}**" if bonus else "") +
        f"\n💰 Reçu : **{total:,} coins**"
    ))
    await ctx.send(embed=embed)


# ── Métiers ───────────────────────────────────────────────────────────────

@bot.command(name="metier", aliases=["job", "emploi"])
async def cmd_metier(ctx):
    current = _get_job(ctx.author.id)
    embed   = discord.Embed(title="💼 Métiers disponibles", color=0x9b59b6,
        description="Choisissez avec `!choisir_metier <nom>`\n")
    for key, info in JOBS.items():
        marker = " ✅ **(actuel)**" if key == current else ""
        embed.add_field(
            name=f"{info['name']}{marker}",
            value=f"{info['desc']}\nAction : {info['action']}",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name="choisir_metier")
async def cmd_choisir_metier(ctx, metier: str):
    metier = metier.lower()
    if metier not in JOBS:
        return await ctx.send(f"❌ Métier inconnu. Disponibles : {', '.join(JOBS.keys())}")
    uid = str(ctx.author.id)
    jobs_data.setdefault(uid, {})['job'] = metier
    save_data()
    info = JOBS[metier]
    embed = discord.Embed(title=f"💼 Nouveau métier : {info['name']}", color=0x9b59b6,
        description=f"{info['desc']}\nAction : {info['action']}")
    await ctx.send(embed=embed)

@bot.command(name="miner")
async def cmd_miner(ctx):
    if _get_job(ctx.author.id) != 'mineur':
        return await ctx.send("❌ Vous devez être **⛏️ Mineur**. Tapez `!choisir_metier mineur`.")
    ok, wait = _cd_ok(miner_cooldowns, ctx.author.id, cooldown_h('miner'))
    if not ok:
        return await ctx.send(f"⏳ {ctx.author.mention}, vos mines sont épuisées ! Revenez dans {wait}.")
    amount = random.randint(50, 200)
    coins[ctx.author.id] += amount
    save_data()
    embed = discord.Embed(title="⛏️ Minage réussi !", color=0x95a5a6,
        description=f"{ctx.author.mention} a miné **{amount:,} 🪙 coins** !\n💰 Solde : **{coins[ctx.author.id]:,} coins**")
    embed.set_footer(text="Revenez dans 1 heure.")
    await ctx.send(embed=embed)

@bot.command(name="hacker")
async def cmd_hacker(ctx, cible: discord.Member):
    if _get_job(ctx.author.id) != 'hacker':
        return await ctx.send("❌ Vous devez être **💻 Hacker**. Tapez `!choisir_metier hacker`.")
    if cible.id == ctx.author.id or cible.bot:
        return await ctx.send("❌ Cible invalide.")
    ok, wait = _cd_ok(hacker_cooldowns, ctx.author.id, cooldown_h('hacker'))
    if not ok:
        return await ctx.send(f"⏳ {ctx.author.mention}, système refroidi dans {wait}.")
    uid_t  = str(cible.id)
    owned  = {s: q for s, q in crypto_holdings.get(uid_t, {}).items() if q > 0.000001}
    if not owned:
        return await ctx.send(f"❌ {cible.mention} ne possède aucune crypto à voler !")
    symbol = random.choice(list(owned.keys()))
    held   = owned[symbol]
    if random.random() < 0.60:
        pct        = random.uniform(0.05, 0.25)
        stolen_qty = round(held * pct, 8)
        crypto_holdings[uid_t][symbol]    = round(held - stolen_qty, 8)
        uid_a = str(ctx.author.id)
        crypto_holdings.setdefault(uid_a, {})
        crypto_holdings[uid_a][symbol]    = round(crypto_holdings[uid_a].get(symbol, 0) + stolen_qty, 8)
        val = int(stolen_qty * crypto_prices.get(symbol, 0))
        save_data()
        embed = discord.Embed(title="💻 Hack réussi !", color=0x2ecc71,
            description=f"🔓 Volé **{stolen_qty:.6f} {symbol}** à {cible.mention}\nValeur ≈ **{val:,} coins**")
    else:
        fine = min(random.randint(200, 600), coins[ctx.author.id])
        coins[ctx.author.id] -= fine
        save_data()
        embed = discord.Embed(title="💻 Hack échoué !", color=0xe74c3c,
            description=f"🚨 Vous vous êtes fait repérer ! Amende : **-{fine:,} coins**")
    await ctx.send(embed=embed)


# ── Coffre-fort ───────────────────────────────────────────────────────────

# ═════════════════════════════════════════════════════════════════════════
# ── Système de Teams / Clubs (!team + !gdt) ──────────────────────────────
# ═════════════════════════════════════════════════════════════════════════

MAX_TEAM_NAME_LEN = 30


def _new_team_id():
    tid = team_state['next_id']
    team_state['next_id'] = tid + 1
    return str(tid)


def _team_summary_lines():
    """Liste de toutes les teams pour l'affichage."""
    if not teams:
        return "*Aucun club n'existe encore. Soyez le premier à en créer un !*"
    lines = []
    sorted_teams = sorted(teams.items(), key=lambda kv: -kv[1].get('treasury', 0))
    for tid, t in sorted_teams[:10]:
        lines.append(
            f"**{t['name']}** ({len(t['members'])} membre{'s' if len(t['members'])>1 else ''})"
            f" · 💰 {t.get('treasury', 0):,} coins"
        )
    return '\n'.join(lines)


def _team_embed(ctx):
    user_t = _team_of(ctx.author.id)
    if user_t:
        leader_id = user_t['leader']
        leader_m = ctx.guild.get_member(leader_id) if ctx.guild else None
        leader_name = leader_m.display_name if leader_m else f"<@{leader_id}>"
        members_str = []
        for uid in user_t['members'][:20]:
            m = ctx.guild.get_member(uid) if ctx.guild else None
            name = m.display_name if m else f"<@{uid}>"
            marker = " 👑" if uid == leader_id else ""
            members_str.append(f"• {name}{marker}")
        embed = discord.Embed(
            title=f"👥 Club : {user_t['name']}",
            description=(
                f"👑 **Chef :** {leader_name}\n"
                f"👥 **Membres :** {len(user_t['members'])}\n"
                f"💰 **Trésorerie :** {user_t.get('treasury', 0):,} coins\n\n"
                f"**Liste des membres :**\n" + ('\n'.join(members_str) or "—")
            ),
            color=0x9b59b6
        )
        comp = "🟢 OUVERTE" if team_state.get('competition_open') else "🔴 fermée"
        embed.set_footer(text=f"Compétition inter-clubs : {comp}")
    else:
        embed = discord.Embed(
            title="👥 Système de Clubs",
            description=(
                "Vous n'êtes dans aucun club.\n\n"
                "Utilisez les boutons ci-dessous pour **créer** un nouveau club ou "
                "**rejoindre** un club existant."
            ),
            color=0x9b59b6
        )
        embed.add_field(name="🏆 Top des clubs", value=_team_summary_lines(), inline=False)
        comp = "🟢 OUVERTE — chaque action compte !" if team_state.get('competition_open') else "🔴 fermée"
        embed.add_field(name="Compétition inter-clubs", value=comp, inline=False)
    return embed


class TeamCreateModal(discord.ui.Modal, title="🆕 Créer un nouveau club"):
    name_input = discord.ui.TextInput(
        label="Nom du club", placeholder="Ex : Les Loups",
        required=True, min_length=2, max_length=MAX_TEAM_NAME_LEN
    )

    def __init__(self, author_id):
        super().__init__()
        self.author_id = author_id

    async def on_submit(self, interaction: discord.Interaction):
        if _user_team_id(self.author_id):
            return await interaction.response.send_message("❌ Vous êtes déjà dans un club.", ephemeral=True)
        name = str(self.name_input.value).strip()
        if not name:
            return await interaction.response.send_message("❌ Nom invalide.", ephemeral=True)
        if any(t['name'].lower() == name.lower() for t in teams.values()):
            return await interaction.response.send_message("❌ Un club avec ce nom existe déjà.", ephemeral=True)
        tid = _new_team_id()
        teams[tid] = {
            'name': name, 'leader': self.author_id,
            'members': [self.author_id], 'treasury': 0,
            'created': datetime.now().isoformat(),
        }
        user_team[str(self.author_id)] = tid
        save_data()
        await interaction.response.send_message(
            f"✅ Club **{name}** créé ! Vous en êtes le 👑 chef.\nTapez `!team` pour gérer votre club.",
            ephemeral=True
        )


class TeamJoinView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=120)
        self.author_id = author_id
        if not teams:
            self.add_item(discord.ui.Button(label="Aucun club", style=discord.ButtonStyle.secondary, disabled=True))
            return
        options = []
        for tid, t in list(teams.items())[:25]:
            options.append(discord.SelectOption(
                label=f"{t['name']} ({len(t['members'])} membres)"[:100],
                description=f"💰 Trésorerie : {t.get('treasury', 0):,} coins"[:100],
                value=tid
            ))
        self.select = discord.ui.Select(placeholder="Choisir un club à rejoindre…", options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Ce n'est pas votre menu.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction):
        if _user_team_id(self.author_id):
            return await interaction.response.send_message("❌ Vous êtes déjà dans un club.", ephemeral=True)
        tid = self.select.values[0]
        team = teams.get(tid)
        if not team:
            return await interaction.response.send_message("❌ Club introuvable.", ephemeral=True)
        team['members'].append(self.author_id)
        user_team[str(self.author_id)] = tid
        save_data()
        await interaction.response.send_message(
            f"✅ Vous avez rejoint **{team['name']}** ! Tapez `!team`.", ephemeral=True
        )


class TeamTreasuryModal(discord.ui.Modal):
    montant = discord.ui.TextInput(label="Montant", placeholder="Ex : 1000", required=True, max_length=15)

    def __init__(self, author_id, mode):
        super().__init__(title=("💰 Déposer dans la trésorerie" if mode == 'deposit' else "💸 Retirer de la trésorerie"))
        self.author_id = author_id
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        team = _team_of(self.author_id)
        if not team:
            return await interaction.response.send_message("❌ Vous n'êtes plus dans un club.", ephemeral=True)
        try:
            m = int(str(self.montant.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        if m <= 0:
            return await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        if self.mode == 'deposit':
            if coins[self.author_id] < m:
                return await interaction.response.send_message(
                    f"❌ Pas assez de cash. Solde : **{coins[self.author_id]:,} coins**", ephemeral=True
                )
            coins[self.author_id] -= m
            team['treasury'] = team.get('treasury', 0) + m
            save_data()
            await interaction.response.send_message(
                f"💰 Vous avez déposé **{m:,} coins** dans la trésorerie de **{team['name']}**.\n"
                f"Trésorerie : **{team['treasury']:,} coins**", ephemeral=True
            )
        else:  # withdraw
            if team.get('treasury', 0) < m:
                return await interaction.response.send_message(
                    f"❌ Pas assez dans la trésorerie. Disponible : **{team.get('treasury', 0):,} coins**", ephemeral=True
                )
            team['treasury'] -= m
            coins[self.author_id] += m
            save_data()
            await interaction.response.send_message(
                f"💸 Vous avez retiré **{m:,} coins** de la trésorerie de **{team['name']}**.\n"
                f"Trésorerie restante : **{team['treasury']:,} coins**", ephemeral=True
            )


class TeamView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.author_id = ctx.author.id
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        if _user_team_id(self.author_id):
            # Membre d'un club
            self.add_item(self._make_btn("💰 Déposer", discord.ButtonStyle.success, "deposit"))
            self.add_item(self._make_btn("💸 Retirer", discord.ButtonStyle.danger, "withdraw"))
            self.add_item(self._make_btn("🚪 Quitter", discord.ButtonStyle.secondary, "leave"))
            self.add_item(self._make_btn("🔄 Actualiser", discord.ButtonStyle.primary, "refresh"))
        else:
            # Pas de club
            self.add_item(self._make_btn("🆕 Créer un club", discord.ButtonStyle.success, "create"))
            self.add_item(self._make_btn("✋ Rejoindre", discord.ButtonStyle.primary, "join"))
            self.add_item(self._make_btn("🔄 Actualiser", discord.ButtonStyle.secondary, "refresh"))

    def _make_btn(self, label, style, action):
        btn = discord.ui.Button(label=label, style=style)
        async def callback(interaction, action=action, self=self):
            await self._handle(interaction, action)
        btn.callback = callback
        return btn

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Ce n'est pas votre menu. Tapez `!team`.", ephemeral=True)
            return False
        return True

    async def _handle(self, interaction, action):
        if action == 'create':
            return await interaction.response.send_modal(TeamCreateModal(self.author_id))
        if action == 'join':
            if _user_team_id(self.author_id):
                return await interaction.response.send_message("❌ Vous êtes déjà dans un club.", ephemeral=True)
            return await interaction.response.send_message(
                "Choisissez un club à rejoindre :",
                view=TeamJoinView(self.author_id), ephemeral=True
            )
        if action == 'leave':
            t = _team_of(self.author_id)
            if not t:
                return await interaction.response.send_message("❌ Vous n'êtes pas dans un club.", ephemeral=True)
            if t['leader'] == self.author_id and len(t['members']) > 1:
                return await interaction.response.send_message(
                    "❌ Vous êtes le chef. Transférez d'abord le leadership ou supprimez le club (cliquez à nouveau après avoir fait partir tout le monde).",
                    ephemeral=True
                )
            t['members'].remove(self.author_id)
            user_team.pop(str(self.author_id), None)
            if not t['members']:
                # Supprimer le club et rembourser la trésorerie au dernier chef
                refund = t.get('treasury', 0)
                coins[self.author_id] += refund
                tid = next((k for k, v in teams.items() if v is t), None)
                if tid: teams.pop(tid, None)
                save_data()
                self._build_buttons()
                await interaction.response.edit_message(embed=_team_embed(self.ctx), view=self)
                return await interaction.followup.send(
                    f"🚪 Vous avez quitté le club, qui a été dissous. Trésorerie remboursée : **{refund:,} coins**.",
                    ephemeral=True
                )
            save_data()
            self._build_buttons()
            await interaction.response.edit_message(embed=_team_embed(self.ctx), view=self)
            return await interaction.followup.send("🚪 Vous avez quitté le club.", ephemeral=True)
        if action == 'deposit':
            if not _team_of(self.author_id):
                return await interaction.response.send_message("❌ Vous n'êtes pas dans un club.", ephemeral=True)
            return await interaction.response.send_modal(TeamTreasuryModal(self.author_id, 'deposit'))
        if action == 'withdraw':
            if not _team_of(self.author_id):
                return await interaction.response.send_message("❌ Vous n'êtes pas dans un club.", ephemeral=True)
            return await interaction.response.send_modal(TeamTreasuryModal(self.author_id, 'withdraw'))
        if action == 'refresh':
            self._build_buttons()
            return await interaction.response.edit_message(embed=_team_embed(self.ctx), view=self)


@bot.command(name="team", aliases=["club", "guilde"])
async def cmd_team(ctx):
    await ctx.send(embed=_team_embed(ctx), view=TeamView(ctx))


# ── !gdt — Gestion compétitions inter-clubs (Admin) ──────────────────────

class GdtRewardModal(discord.ui.Modal, title="🏆 Récompenser un club"):
    team_id_input = discord.ui.TextInput(label="ID du club (voir !gdt)", placeholder="Ex : 1", required=True, max_length=5)
    amount_input = discord.ui.TextInput(label="Montant à verser dans la trésorerie", placeholder="Ex : 50000", required=True, max_length=15)

    async def on_submit(self, interaction: discord.Interaction):
        tid = str(self.team_id_input.value).strip()
        if tid not in teams:
            return await interaction.response.send_message(f"❌ Club #{tid} introuvable.", ephemeral=True)
        try:
            amt = int(str(self.amount_input.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        teams[tid]['treasury'] = teams[tid].get('treasury', 0) + amt
        save_data()
        await interaction.response.send_message(
            f"🏆 Le club **{teams[tid]['name']}** a reçu **{amt:,} coins** dans sa trésorerie !",
            ephemeral=False
        )


def _gdt_embed():
    state = "🟢 **OUVERTE**" if team_state.get('competition_open') else "🔴 **fermée**"
    embed = discord.Embed(
        title="🏆 Compétition inter-clubs",
        description=f"**État :** {state}\n\n"
                    "Utilisez les boutons pour ouvrir/fermer la compétition ou récompenser un club.",
        color=0xf1c40f
    )
    if teams:
        lines = []
        for tid, t in sorted(teams.items(), key=lambda kv: -kv[1].get('treasury', 0)):
            lines.append(f"`#{tid}` **{t['name']}** — 👥 {len(t['members'])} · 💰 {t.get('treasury', 0):,} coins")
        embed.add_field(name="📊 Classement des clubs", value='\n'.join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="Clubs", value="*Aucun club n'existe encore.*", inline=False)
    return embed


class GdtView(discord.ui.View):
    def __init__(self, admin_id):
        super().__init__(timeout=300)
        self.admin_id = admin_id

    async def interaction_check(self, interaction):
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux admins.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Ouvrir la compétition", style=discord.ButtonStyle.success, emoji="🟢")
    async def open_btn(self, interaction, button):
        team_state['competition_open'] = True
        save_data()
        await interaction.response.edit_message(embed=_gdt_embed(), view=self)
        await interaction.followup.send("🟢 La compétition inter-clubs est **OUVERTE** !")

    @discord.ui.button(label="Fermer la compétition", style=discord.ButtonStyle.danger, emoji="🔴")
    async def close_btn(self, interaction, button):
        team_state['competition_open'] = False
        save_data()
        await interaction.response.edit_message(embed=_gdt_embed(), view=self)
        await interaction.followup.send("🔴 La compétition inter-clubs est **fermée**.")

    @discord.ui.button(label="Récompenser un club", style=discord.ButtonStyle.primary, emoji="🏆")
    async def reward_btn(self, interaction, button):
        await interaction.response.send_modal(GdtRewardModal())

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_btn(self, interaction, button):
        await interaction.response.edit_message(embed=_gdt_embed(), view=self)


@bot.command(name="gdt", aliases=["competition_clubs"])
@commands.has_permissions(administrator=True)
async def cmd_gdt(ctx):
    await ctx.send(embed=_gdt_embed(), view=GdtView(ctx.author.id))



class CoffreDepositModal(discord.ui.Modal, title="🏦 Déposer dans le coffre"):
    montant = discord.ui.TextInput(label="Montant à déposer", placeholder="Ex : 500 ou all", required=True, max_length=15)

    def __init__(self, author_id):
        super().__init__()
        self.author_id = author_id

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(self.author_id)
        bal_cash = coins[self.author_id]
        raw = str(self.montant.value).strip().lower()
        if raw in ('all', 'tout'):
            m = bal_cash
        else:
            try:
                m = int(raw)
            except ValueError:
                return await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        if m <= 0 or bal_cash < m:
            return await interaction.response.send_message(f"❌ Pas assez de cash. Cash : **{bal_cash:,} coins**", ephemeral=True)
        coins[self.author_id] -= m
        safes[uid] = safes.get(uid, 0) + m
        save_data()
        await interaction.response.send_message(
            f"🔒 **{m:,} coins** déposés !\n💵 Cash : **{coins[self.author_id]:,}** | 🔒 Coffre : **{safes[uid]:,}**",
            ephemeral=True
        )


class CoffreWithdrawModal(discord.ui.Modal, title="🏦 Retirer du coffre"):
    montant = discord.ui.TextInput(label="Montant à retirer", placeholder="Ex : 500 ou all", required=True, max_length=15)

    def __init__(self, author_id):
        super().__init__()
        self.author_id = author_id

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(self.author_id)
        bal_coffre = safes.get(uid, 0)
        raw = str(self.montant.value).strip().lower()
        if raw in ('all', 'tout'):
            m = bal_coffre
        else:
            try:
                m = int(raw)
            except ValueError:
                return await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        if m <= 0 or bal_coffre < m:
            return await interaction.response.send_message(f"❌ Pas assez dans le coffre. Coffre : **{bal_coffre:,} coins**", ephemeral=True)
        safes[uid] = bal_coffre - m
        coins[self.author_id] += m
        save_data()
        await interaction.response.send_message(
            f"🔓 **{m:,} coins** retirés !\n💵 Cash : **{coins[self.author_id]:,}** | 🔒 Coffre : **{safes[uid]:,}**",
            ephemeral=True
        )


class CoffreView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=180)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Ce coffre n'est pas le vôtre. Tapez `!coffre`.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Déposer", style=discord.ButtonStyle.success, emoji="💰")
    async def deposit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CoffreDepositModal(self.author_id))

    @discord.ui.button(label="Retirer", style=discord.ButtonStyle.danger, emoji="🔓")
    async def withdraw_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CoffreWithdrawModal(self.author_id))


@bot.command(name="coffre", aliases=["vault", "banque"])
async def cmd_coffre(ctx):
    uid = str(ctx.author.id)
    bal_coffre = safes.get(uid, 0)
    bal_cash = coins[ctx.author.id]
    embed = discord.Embed(title="🏦 Votre Coffre-Fort", color=0xf1c40f, description=(
        f"💵 **Cash** (volable) : **{bal_cash:,} coins**\n"
        f"🔒 **Coffre** (sécurisé) : **{bal_coffre:,} coins**\n\n"
        "Cliquez sur les boutons ci-dessous pour gérer votre coffre."
    ))
    await ctx.send(embed=embed, view=CoffreView(ctx.author.id))


# ── Vol de cash ───────────────────────────────────────────────────────────

@bot.command(name="voler", aliases=["steal"])
async def cmd_voler(ctx, cible: discord.Member):
    if cible.id == ctx.author.id or cible.bot:
        return await ctx.send("❌ Cible invalide.")
    cd_hours = cooldown_h('voler')
    ok, wait = _cd_ok(theft_cooldowns, ctx.author.id, cd_hours)
    if not ok:
        return await ctx.send(f"⏳ {ctx.author.mention}, attendez encore {wait} avant de tenter un autre vol.")
    safe_cible = safes.get(str(cible.id), 0)
    if safe_cible < 300:
        return await ctx.send(f"❌ Le coffre de {cible.mention} est trop pauvre (min 300 coins dans le coffre).")
    # Vérifier bouclier
    if _has_item(cible.id, 3):
        _use_item(cible.id, 3)
        save_data()
        return await ctx.send(f"🛡️ {cible.mention} possède un **Bouclier Anti-Vol** ! Le vol a été bloqué. (bouclier utilisé)")
    base_rate = 0.55
    if _get_job(ctx.author.id) == 'escroc': base_rate += 0.20
    if random.random() < base_rate:
        pct    = random.uniform(0.05, 0.20)
        stolen = int(safe_cible * pct)
        stolen = max(50, stolen)
        if _get_job(cible.id) == 'gardien': stolen //= 2
        safes[str(cible.id)] = safe_cible - stolen
        coins[ctx.author.id] += stolen
        save_data()
        embed = discord.Embed(title="🦹 Vol de coffre réussi !", color=0x2ecc71,
            description=(
                f"Vous avez crocheté le coffre de {cible.mention} et volé **{stolen:,} coins** "
                f"({pct*100:.1f}% du coffre) !\n💰 Solde : **{coins[ctx.author.id]:,} coins**"
            ))
    else:
        fine = min(random.randint(100, 350), coins[ctx.author.id])
        coins[ctx.author.id] -= fine
        save_data()
        embed = discord.Embed(title="🚨 Vol raté !", color=0xe74c3c,
            description=f"Vous vous êtes fait attraper en train de crocheter le coffre ! Amende : **-{fine:,} coins**\n💰 Solde : **{coins[ctx.author.id]:,} coins**")
    await ctx.send(embed=embed)


# ── !rob — Voler le cash d'un joueur (Casino, accessible à tous) ─────
@bot.command(name="rob")
async def cmd_rob(ctx, cible: discord.Member):
    if cible.id == ctx.author.id or cible.bot:
        return await ctx.send("❌ Cible invalide.")
    cd_hours = cooldown_h('rob')
    ok, wait = _cd_ok(rob_cooldowns, ctx.author.id, cd_hours)
    if not ok:
        return await ctx.send(f"⏳ {ctx.author.mention}, attendez encore {wait} avant un nouveau vol.")
    cash_cible = coins[cible.id]
    if cash_cible < 200:
        return await ctx.send(f"❌ {cible.mention} n'a pas assez de cash à voler (min 200 coins en poche).")
    # Bouclier
    if _has_item(cible.id, 3):
        _use_item(cible.id, 3)
        save_data()
        return await ctx.send(f"🛡️ {cible.mention} possède un **Bouclier Anti-Vol** ! Le vol a été bloqué.")
    if random.random() < 0.55:
        pct    = random.uniform(0.05, 0.15)
        stolen = int(cash_cible * pct)
        stolen = max(20, stolen)
        coins[ctx.author.id] += stolen
        coins[cible.id]      -= stolen
        save_data()
        embed = discord.Embed(title="🦹 Rob réussi !", color=0x2ecc71,
            description=(
                f"Vous avez braqué {cible.mention} et volé **{stolen:,} coins** "
                f"({pct*100:.1f}% de son cash) !\n💰 Solde : **{coins[ctx.author.id]:,} coins**\n"
                f"⏳ Prochain rob dans **{cd_hours:g}h**."
            ))
    else:
        loss = random.randint(0, 300)
        loss = min(loss, coins[ctx.author.id])
        coins[ctx.author.id] -= loss
        save_data()
        embed = discord.Embed(title="🚨 Rob raté !", color=0xe74c3c,
            description=(
                f"{cible.mention} vous a repéré ! Vous perdez **{loss:,} coins**.\n"
                f"💰 Solde : **{coins[ctx.author.id]:,} coins**\n"
                f"⏳ Prochain rob dans **{cd_hours:g}h**."
            ))
    await ctx.send(embed=embed)


# ── Magasin ───────────────────────────────────────────────────────────────

# ═════════════════════════════════════════════════════════════════════════
# ── !gestion : activer / désactiver des commandes (Admin & Owner) ───────
# ═════════════════════════════════════════════════════════════════════════

def _all_command_names():
    """Liste de toutes les commandes enregistrées du bot, triées."""
    return sorted(c.name for c in bot.commands)


def _gestion_embed():
    cmds = _all_command_names()
    if disabled_cmds:
        disabled_list = ', '.join(f"`!{c}`" for c in sorted(disabled_cmds))
    else:
        disabled_list = "*Aucune commande désactivée.*"
    embed = discord.Embed(
        title="🛠️ Gestion des commandes",
        description=(
            f"Total : **{len(cmds)} commandes** disponibles.\n"
            f"Désactivées : **{len(disabled_cmds)}**\n\n"
            "Utilisez les menus déroulants ci-dessous pour activer ou désactiver une commande."
        ),
        color=0x3498db
    )
    embed.add_field(name="🚫 Commandes désactivées", value=disabled_list[:1024], inline=False)
    return embed


class GestionView(discord.ui.View):
    def __init__(self, admin_id):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        # Discord limite à 25 options par menu — paginé en groupes
        cmds = _all_command_names()
        # Bouton désactiver : commandes actives
        active = [c for c in cmds if c not in disabled_cmds and c not in ALWAYS_ALLOWED_CMDS][:25]
        # Bouton activer : commandes désactivées
        inactive = sorted(disabled_cmds)[:25]

        if active:
            self.disable_select = discord.ui.Select(
                placeholder=f"🚫 Désactiver une commande ({len(active)} dispos)",
                options=[discord.SelectOption(label=f"!{c}"[:100], value=c) for c in active]
            )
            self.disable_select.callback = self._on_disable
            self.add_item(self.disable_select)
        if inactive:
            self.enable_select = discord.ui.Select(
                placeholder=f"✅ Réactiver une commande ({len(inactive)})",
                options=[discord.SelectOption(label=f"!{c}"[:100], value=c) for c in inactive]
            )
            self.enable_select.callback = self._on_enable
            self.add_item(self.enable_select)

    async def interaction_check(self, interaction):
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux admins/owner.", ephemeral=True)
            return False
        return True

    async def _on_disable(self, interaction):
        cmd = self.disable_select.values[0]
        if cmd in ALWAYS_ALLOWED_CMDS:
            return await interaction.response.send_message(
                f"❌ `!{cmd}` ne peut pas être désactivée (anti-brick).", ephemeral=True
            )
        disabled_cmds.add(cmd)
        save_data()
        new_view = GestionView(self.admin_id)
        await interaction.response.edit_message(embed=_gestion_embed(), view=new_view)
        await interaction.followup.send(f"🚫 `!{cmd}` a été **désactivée**.", ephemeral=True)

    async def _on_enable(self, interaction):
        cmd = self.enable_select.values[0]
        disabled_cmds.discard(cmd)
        save_data()
        new_view = GestionView(self.admin_id)
        await interaction.response.edit_message(embed=_gestion_embed(), view=new_view)
        await interaction.followup.send(f"✅ `!{cmd}` a été **réactivée**.", ephemeral=True)


@bot.command(name="gestion")
async def cmd_gestion(ctx):
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs ou au créateur du bot.")
    await ctx.send(embed=_gestion_embed(), view=GestionView(ctx.author.id))


# ═════════════════════════════════════════════════════════════════════════
# ── !permission : restreindre une commande à certains rôles (Owner) ─────
# ═════════════════════════════════════════════════════════════════════════

class PermSelectRolesView(discord.ui.View):
    """Menu pour sélectionner les rôles autorisés à utiliser la commande choisie."""
    def __init__(self, ctx, cmd_name):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cmd_name = cmd_name
        roles = [r for r in ctx.guild.roles if not r.is_default()][:25]
        # Récupérer les rôles déjà autorisés
        current = set(cmd_role_perms.get(cmd_name, []))
        options = []
        for r in roles:
            options.append(discord.SelectOption(
                label=r.name[:100], value=str(r.id),
                default=(r.id in current)
            ))
        if not options:
            options = [discord.SelectOption(label="Aucun rôle disponible", value="none")]
        self.select = discord.ui.Select(
            placeholder=f"Rôles autorisés pour !{cmd_name}",
            options=options, min_values=0, max_values=len(options)
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def interaction_check(self, interaction):
        if not is_bot_owner(interaction.user):
            await interaction.response.send_message("❌ Réservé au créateur du bot.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction):
        values = [v for v in self.select.values if v != "none"]
        if values:
            cmd_role_perms[self.cmd_name] = [int(v) for v in values]
        else:
            cmd_role_perms.pop(self.cmd_name, None)
        save_data()
        roles_str = ', '.join(f"<@&{rid}>" for rid in cmd_role_perms.get(self.cmd_name, []))
        await interaction.response.send_message(
            f"✅ Permissions pour `!{self.cmd_name}` mises à jour.\n"
            f"Rôles autorisés : {roles_str if roles_str else '*tous* (aucune restriction)'}\n"
            f"*(Les admins du serveur passent toujours.)*",
            ephemeral=True
        )

    @discord.ui.button(label="Supprimer la restriction", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def clear_btn(self, interaction, button):
        cmd_role_perms.pop(self.cmd_name, None)
        save_data()
        await interaction.response.send_message(
            f"✅ Restriction supprimée pour `!{self.cmd_name}`. Tout le monde peut maintenant l'utiliser.",
            ephemeral=True
        )


def _perm_embed():
    if cmd_role_perms:
        lines = []
        for cmd, roles in cmd_role_perms.items():
            roles_str = ', '.join(f"<@&{rid}>" for rid in roles)
            lines.append(f"`!{cmd}` → {roles_str}")
        body = '\n'.join(lines)[:1024]
    else:
        body = "*Aucune restriction définie. Toutes les commandes sont accessibles à tous.*"
    embed = discord.Embed(
        title="🔒 Permissions par rôle",
        description="Sélectionnez une commande pour modifier les rôles autorisés à l'utiliser.\n"
                    "*Les admins du serveur passent toujours, peu importe la restriction.*",
        color=0xe67e22
    )
    embed.add_field(name="Restrictions actuelles", value=body, inline=False)
    return embed


class PermissionView(discord.ui.View):
    PAGE_SIZE = 25

    def __init__(self, ctx, page: int = 0):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.page = page
        cmds = _all_command_names()
        self.total_pages = max(1, (len(cmds) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        start = page * self.PAGE_SIZE
        page_cmds = cmds[start:start + self.PAGE_SIZE]
        options = [discord.SelectOption(label=f"!{c}"[:100], value=c) for c in page_cmds]
        self.select = discord.ui.Select(
            placeholder=f"📂 Page {page + 1}/{self.total_pages} — choisir une commande",
            options=options
        )
        self.select.callback = self._on_pick
        self.add_item(self.select)
        if page > 0:
            prev_btn = discord.ui.Button(label="◀ Précédent", style=discord.ButtonStyle.secondary, row=1)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)
        if page < self.total_pages - 1:
            next_btn = discord.ui.Button(label="Suivant ▶", style=discord.ButtonStyle.secondary, row=1)
            next_btn.callback = self._next
            self.add_item(next_btn)

    async def interaction_check(self, interaction):
        if not is_bot_owner(interaction.user):
            await interaction.response.send_message("❌ Réservé au créateur du bot.", ephemeral=True)
            return False
        return True

    async def _on_pick(self, interaction):
        cmd = self.select.values[0]
        await interaction.response.send_message(
            f"🔒 Configuration des rôles pour `!{cmd}` :\n"
            f"*Cochez les rôles autorisés. Décocher tous = aucune restriction.*",
            view=PermSelectRolesView(self.ctx, cmd),
            ephemeral=True
        )

    async def _prev(self, interaction):
        await interaction.response.edit_message(view=PermissionView(self.ctx, self.page - 1))

    async def _next(self, interaction):
        await interaction.response.edit_message(view=PermissionView(self.ctx, self.page + 1))


@bot.command(name="permission", aliases=["permissions", "perm"])
async def cmd_permission(ctx):
    if not is_bot_owner(ctx.author):
        return await ctx.send("❌ Réservé au créateur du bot.")
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")
    await ctx.send(embed=_perm_embed(), view=PermissionView(ctx))


# ═════════════════════════════════════════════════════════════════════════
# ── !cooldown / !cd : modifier les cooldowns (Admin & Owner) ────────────
# ═════════════════════════════════════════════════════════════════════════

class CooldownModal(discord.ui.Modal):
    new_value = discord.ui.TextInput(
        label="Nouveau cooldown (en heures, 0 = défaut)",
        placeholder="Ex : 6 ou 0.5 ou 0",
        required=True, max_length=10
    )

    def __init__(self, cmd_name):
        super().__init__(title=f"⏳ Cooldown de !{cmd_name}")
        self.cmd_name = cmd_name

    async def on_submit(self, interaction: discord.Interaction):
        try:
            v = float(str(self.new_value.value).strip().replace(',', '.'))
        except ValueError:
            return await interaction.response.send_message("❌ Valeur invalide.", ephemeral=True)
        if v < 0:
            return await interaction.response.send_message("❌ Le cooldown ne peut pas être négatif.", ephemeral=True)
        if v == 0:
            casino_config['cooldowns'].pop(self.cmd_name, None)
            msg = f"✅ Cooldown de `!{self.cmd_name}` réinitialisé à **{DEFAULT_COOLDOWNS_H[self.cmd_name]:g}h** (défaut)."
        else:
            casino_config['cooldowns'][self.cmd_name] = v
            msg = f"✅ Cooldown de `!{self.cmd_name}` réglé à **{v:g}h**."
        save_data()
        await interaction.response.send_message(msg, ephemeral=True)


def _cd_embed():
    embed = discord.Embed(
        title="⏳ Cooldowns des commandes",
        description="Sélectionnez une commande pour modifier son cooldown.",
        color=0x3498db
    )
    lines = []
    for cmd in sorted(DEFAULT_COOLDOWNS_H.keys()):
        current = cooldown_h(cmd)
        default = DEFAULT_COOLDOWNS_H[cmd]
        flag = " 🔧" if cmd in casino_config.get('cooldowns', {}) else ""
        lines.append(f"`!{cmd}` — **{current:g}h** *(défaut {default:g}h)*{flag}")
    embed.add_field(name="Cooldowns actuels", value='\n'.join(lines), inline=False)
    embed.set_footer(text="🔧 = valeur personnalisée")
    return embed


class CooldownView(discord.ui.View):
    def __init__(self, admin_id):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        options = [
            discord.SelectOption(
                label=f"!{cmd} ({cooldown_h(cmd):g}h)"[:100],
                value=cmd,
                description=f"Défaut : {DEFAULT_COOLDOWNS_H[cmd]:g}h"[:100],
            )
            for cmd in sorted(DEFAULT_COOLDOWNS_H.keys())
        ]
        self.select = discord.ui.Select(
            placeholder="⏳ Choisir une commande à modifier…",
            options=options
        )
        self.select.callback = self._on_pick
        self.add_item(self.select)

    async def interaction_check(self, interaction):
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux admins/owner.", ephemeral=True)
            return False
        return True

    async def _on_pick(self, interaction):
        cmd = self.select.values[0]
        await interaction.response.send_modal(CooldownModal(cmd))


@bot.command(name="cooldown", aliases=["cd"])
async def cmd_cooldown(ctx):
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs ou au créateur du bot.")
    await ctx.send(embed=_cd_embed(), view=CooldownView(ctx.author.id))


# ===== Commande !prix_casino (admin only) ============================

class PrixShopModal(discord.ui.Modal, title="🛒 Modifier le prix d'un item"):
    item_id_input = discord.ui.TextInput(label="ID de l'item (1 à 7)", placeholder="Ex : 3", required=True, max_length=2)
    prix_input = discord.ui.TextInput(label="Nouveau prix (en coins, 0 = défaut)", placeholder="Ex : 1500 ou 0", required=True, max_length=15)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            iid = int(str(self.item_id_input.value).strip())
            price = int(str(self.prix_input.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Valeurs invalides.", ephemeral=True)
        if iid not in SHOP_ITEMS:
            return await interaction.response.send_message(f"❌ Item #{iid} introuvable.", ephemeral=True)
        if price < 0:
            return await interaction.response.send_message("❌ Le prix ne peut pas être négatif.", ephemeral=True)
        if price == 0:
            casino_config['shop_prices'].pop(str(iid), None)
            msg = f"✅ Prix de **{SHOP_ITEMS[iid]['name']}** réinitialisé à **{SHOP_ITEMS[iid]['price']:,} coins** (défaut)."
        else:
            casino_config['shop_prices'][str(iid)] = price
            msg = f"✅ Prix de **{SHOP_ITEMS[iid]['name']}** réglé à **{price:,} coins**."
        save_data()
        await interaction.response.send_message(msg, ephemeral=True)


class PrixUsineModal(discord.ui.Modal, title="🏭 Modifier le prix d'un employé d'usine"):
    pos_input = discord.ui.TextInput(label="N° d'employé (1 à 10)", placeholder="Ex : 3", required=True, max_length=2)
    prix_input = discord.ui.TextInput(label="Nouveau prix (0 = défaut)", placeholder="Ex : 7500 ou 0", required=True, max_length=15)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            pos = int(str(self.pos_input.value).strip())
            price = int(str(self.prix_input.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Valeurs invalides.", ephemeral=True)
        if pos < 1 or pos > MAX_FACTORY_WORKERS:
            return await interaction.response.send_message(f"❌ N° employé doit être entre 1 et {MAX_FACTORY_WORKERS}.", ephemeral=True)
        if price < 0:
            return await interaction.response.send_message("❌ Le prix ne peut pas être négatif.", ephemeral=True)
        # On stocke la liste courante, en s'assurant qu'elle a la bonne taille
        costs = list(casino_config.get('factory_costs') or DEFAULT_FACTORY_COSTS)
        while len(costs) < MAX_FACTORY_WORKERS:
            costs.append(DEFAULT_FACTORY_COSTS[len(costs)] if len(costs) < len(DEFAULT_FACTORY_COSTS) else costs[-1] * 2)
        if price == 0:
            costs[pos - 1] = DEFAULT_FACTORY_COSTS[pos - 1]
            msg = f"✅ Prix du **{pos}ᵉ employé** réinitialisé à **{DEFAULT_FACTORY_COSTS[pos-1]:,} coins** (défaut)."
        else:
            costs[pos - 1] = price
            msg = f"✅ Prix du **{pos}ᵉ employé** réglé à **{price:,} coins**."
        casino_config['factory_costs'] = costs
        save_data()
        await interaction.response.send_message(msg, ephemeral=True)


class PrixMiseModal(discord.ui.Modal, title="🎰 Modifier les limites de mise"):
    game_input = discord.ui.TextInput(
        label="Jeu (slots/coinflip/roulette/bj/duel/mines/poker/course)",
        placeholder="Ex : slots", required=True, max_length=20)
    min_input = discord.ui.TextInput(
        label="Mise minimum (0 = aucune limite)",
        placeholder="Ex : 100 ou 0", required=True, max_length=15)
    max_input = discord.ui.TextInput(
        label="Mise maximum (0 = aucune limite)",
        placeholder="Ex : 100000 ou 0", required=True, max_length=15)

    async def on_submit(self, interaction: discord.Interaction):
        game = str(self.game_input.value).strip().lower()
        if game not in GAMES_WITH_LIMITS:
            return await interaction.response.send_message(
                f"❌ Jeu inconnu. Choisissez parmi : {', '.join(GAMES_WITH_LIMITS)}.",
                ephemeral=True
            )
        try:
            mn = int(str(self.min_input.value).strip())
            mx = int(str(self.max_input.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Valeurs invalides.", ephemeral=True)
        if mn < 0 or mx < 0:
            return await interaction.response.send_message("❌ Les valeurs ne peuvent pas être négatives.", ephemeral=True)
        if mn > 0 and mx > 0 and mn > mx:
            return await interaction.response.send_message("❌ Le minimum ne peut pas être supérieur au maximum.", ephemeral=True)
        if mn == 0:
            casino_config['min_bets'].pop(game, None)
        else:
            casino_config['min_bets'][game] = mn
        if mx == 0:
            casino_config['max_bets'].pop(game, None)
        else:
            casino_config['max_bets'][game] = mx
        save_data()
        mn_txt = f"{mn:,}" if mn > 0 else "aucune"
        mx_txt = f"{mx:,}" if mx > 0 else "aucune"
        await interaction.response.send_message(
            f"✅ **{game.upper()}** — mise min : **{mn_txt}**, mise max : **{mx_txt}**.",
            ephemeral=True
        )


def _prix_casino_embed():
    embed = discord.Embed(
        title="⚙️ Configuration Casino",
        description="Modifiez les **prix du magasin**, **prix des employés d'usine**, et les **limites de mise** des jeux.",
        color=0xf39c12
    )
    # Section magasin
    lines = []
    for iid, info in SHOP_ITEMS.items():
        price = _shop_price(iid)
        custom = "🔧" if str(iid) in casino_config['shop_prices'] else ""
        lines.append(f"**{iid}.** {info['name']} — {price:,} coins {custom}")
    embed.add_field(name="🛒 Magasin", value='\n'.join(lines)[:1024], inline=False)
    # Section usine
    costs = casino_config.get('factory_costs') or DEFAULT_FACTORY_COSTS
    while len(costs) < MAX_FACTORY_WORKERS:
        costs = list(costs) + [DEFAULT_FACTORY_COSTS[len(costs)]]
    is_custom = bool(casino_config.get('factory_costs'))
    cost_str = ' · '.join(f"{i+1}={c:,}" for i, c in enumerate(costs[:MAX_FACTORY_WORKERS]))
    embed.add_field(
        name=f"🏭 Usine (employés 1→{MAX_FACTORY_WORKERS}) {'🔧' if is_custom else ''}",
        value=cost_str,
        inline=False
    )
    # Section mises
    bet_lines = []
    for g in GAMES_WITH_LIMITS:
        mn = casino_config['min_bets'].get(g)
        mx = casino_config['max_bets'].get(g)
        if mn or mx:
            mn_t = f"{mn:,}" if mn else "—"
            mx_t = f"{mx:,}" if mx else "—"
            bet_lines.append(f"**{g}** : min {mn_t}, max {mx_t}")
    if not bet_lines:
        bet_lines.append("*Aucune limite configurée (par défaut)*")
    embed.add_field(name="🎰 Limites de mise", value='\n'.join(bet_lines)[:1024], inline=False)
    embed.set_footer(text="🔧 = valeur personnalisée")
    return embed


class PrixCasinoView(discord.ui.View):
    def __init__(self, admin_id):
        super().__init__(timeout=300)
        self.admin_id = admin_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prix Magasin", style=discord.ButtonStyle.primary, emoji="🛒")
    async def shop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PrixShopModal())

    @discord.ui.button(label="Prix Employés", style=discord.ButtonStyle.primary, emoji="👷")
    async def usine_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PrixUsineModal())

    @discord.ui.button(label="Limites de Mise", style=discord.ButtonStyle.primary, emoji="🎰")
    async def bet_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PrixMiseModal())

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_prix_casino_embed(), view=self)

    @discord.ui.button(label="Tout réinitialiser", style=discord.ButtonStyle.danger, emoji="♻️", row=1)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        casino_config['shop_prices']   = {}
        casino_config['factory_costs'] = []
        casino_config['min_bets']      = {}
        casino_config['max_bets']      = {}
        save_data()
        await interaction.response.edit_message(embed=_prix_casino_embed(), view=self)
        await interaction.followup.send("♻️ Toutes les valeurs ont été réinitialisées aux valeurs par défaut.", ephemeral=True)


@bot.command(name="prix_casino", aliases=["casino_config", "prixcasino"])
@commands.has_permissions(administrator=True)
async def cmd_prix_casino(ctx):
    await ctx.send(embed=_prix_casino_embed(), view=PrixCasinoView(ctx.author.id))



def _shop_embed(author_id):
    uid = str(author_id)
    items = owned_items.get(uid, {})
    embed = discord.Embed(title="🛒 Magasin", color=0xe67e22,
        description=f"💰 Votre solde : **{coins[author_id]:,} coins**\n\nChoisissez un item dans le menu déroulant ci-dessous.")
    for iid, info in SHOP_ITEMS.items():
        cnt = items.get(str(iid), 0)
        tag = (" ✅ *(possédé)*" if info['unique'] and cnt > 0
               else f" *(×{cnt})*" if cnt > 0 else "")
        price = _shop_price(iid)
        embed.add_field(
            name=f"**{iid}.** {info['name']} — {price:,} coins{tag}",
            value=info['desc'], inline=False
        )
    return embed


def _do_purchase(author_id, item_id):
    """Effectue un achat. Retourne (success: bool, message: str)."""
    if item_id not in SHOP_ITEMS:
        return False, "❌ Article introuvable."
    info = SHOP_ITEMS[item_id]
    price = _shop_price(item_id)
    uid = str(author_id)
    if info['unique'] and owned_items.get(uid, {}).get(str(item_id), 0) > 0:
        return False, f"❌ Vous possédez déjà **{info['name']}**."
    if coins[author_id] < price:
        return False, f"❌ Pas assez de coins. Prix : **{price:,}** | Solde : **{coins[author_id]:,}**"
    coins[author_id] -= price
    oi = owned_items.setdefault(uid, {})
    if item_id == 5:
        oi[str(4)] = oi.get(str(4), 0) + 5
    else:
        oi[str(item_id)] = oi.get(str(item_id), 0) + 1
    save_data()
    return True, f"✅ Achat confirmé : **{info['name']}** pour **{price:,} coins** !\n💰 Solde : **{coins[author_id]:,} coins**"


class ShopView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=180)
        self.author_id = author_id
        options = []
        for iid, info in SHOP_ITEMS.items():
            price = _shop_price(iid)
            options.append(discord.SelectOption(
                label=f"{iid}. {info['name']}"[:100],
                description=f"{price:,} coins — {info['desc']}"[:100],
                value=str(iid),
                emoji="🛒"
            ))
        self.select = discord.ui.Select(
            placeholder="🛒 Choisissez un item à acheter…",
            options=options, min_values=1, max_values=1
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Ce magasin n'est pas le vôtre. Tapez `!shop`.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        item_id = int(self.select.values[0])
        ok, msg = _do_purchase(self.author_id, item_id)
        embed = _shop_embed(self.author_id)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(msg, ephemeral=True)


@bot.command(name="shop", aliases=["magasin", "boutique"])
async def cmd_shop(ctx):
    await ctx.send(embed=_shop_embed(ctx.author.id), view=ShopView(ctx.author.id))


@bot.command(name="acheter", aliases=["buy"])
async def cmd_acheter(ctx, item_id: int):
    ok, msg = _do_purchase(ctx.author.id, item_id)
    await ctx.send(msg)

@bot.command(name="inventaire", aliases=["inv"])
async def cmd_inventaire(ctx):
    uid   = str(ctx.author.id)
    items = owned_items.get(uid, {})
    lines = []
    for iid_str, cnt in items.items():
        iid = int(iid_str)
        if iid in SHOP_ITEMS and cnt > 0:
            lines.append(f"• {SHOP_ITEMS[iid]['name']} ×{cnt}")
    embed = discord.Embed(title="🎒 Inventaire", color=0x9b59b6,
        description='\n'.join(lines) if lines else "Votre inventaire est vide. Achetez des objets avec `!shop` !")
    await ctx.send(embed=embed)


# ── Ticket à gratter (interactif) ────────────────────────────────────────

class ScratchView(discord.ui.View):
    def __init__(self, author_id: int, n_clovers: int):
        super().__init__(timeout=180)
        self.author_id = author_id
        self._pfx      = random.randint(100000, 999999)
        self.cells     = [True] * n_clovers + [False] * (5 - n_clovers)
        random.shuffle(self.cells)
        self.revealed  = [False] * 5
        self.done      = False
        for i in range(5):
            btn = discord.ui.Button(
                label="❓",
                style=discord.ButtonStyle.secondary,
                custom_id=f"sc_{self._pfx}_{i}",
                row=0
            )
            btn.callback = self._make_cb(i)
            self.add_item(btn)

    def _make_cb(self, idx: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                return await interaction.response.send_message(
                    "❌ Ce n'est pas votre ticket !", ephemeral=True)
            if self.revealed[idx] or self.done:
                return await interaction.response.defer()

            self.revealed[idx] = True
            # Met à jour le bouton cliqué
            for item in self.children:
                if item.custom_id == f"sc_{self._pfx}_{idx}":
                    item.label    = "🍀" if self.cells[idx] else "⬛"
                    item.style    = (discord.ButtonStyle.success
                                     if self.cells[idx]
                                     else discord.ButtonStyle.secondary)
                    item.disabled = True
                    break

            n_found    = sum(1 for j, r in enumerate(self.revealed) if r and self.cells[j])
            remaining  = self.revealed.count(False)

            if all(self.revealed):
                # Toutes les cases grattées → résultat final
                self.done = True
                for item in self.children:
                    item.disabled = True
                _, prize, label = SCRATCH_PRIZES[n_found]
                if prize > 0:
                    coins[self.author_id] += prize
                save_data()
                uid          = str(self.author_id)
                tickets_left = owned_items.get(uid, {}).get('4', 0)
                color        = 0x2ecc71 if prize > 0 else 0x95a5a6
                embed = discord.Embed(
                    title="🎟️ Ticket à Gratter — Résultat !",
                    description=f"**{label}**",
                    color=color
                )
                embed.add_field(name="🍀 Trèfles", value=f"{n_found}/5",      inline=True)
                embed.add_field(name="💰 Gain",    value=f"+{prize:,} coins" if prize else "Rien", inline=True)
                embed.add_field(name="💳 Solde",   value=f"{coins[self.author_id]:,} coins", inline=True)
                embed.add_field(name="🎟️ Tickets restants", value=str(tickets_left), inline=True)
            else:
                embed = discord.Embed(
                    title="🎟️ Grattez votre ticket !",
                    description=(
                        f"🍀 Trouvés jusqu'ici : **{n_found}**  |  "
                        f"❓ Cases restantes : **{remaining}**"
                    ),
                    color=0xf1c40f
                )
                embed.set_footer(text="Cliquez sur ❓ pour gratter les cases !")

            await interaction.response.edit_message(embed=embed, view=self)
        return callback


@bot.command(name="gratter", aliases=["scratch"])
async def cmd_gratter(ctx):
    uid = str(ctx.author.id)
    if owned_items.get(uid, {}).get('4', 0) <= 0:
        return await ctx.send(
            "❌ Vous n'avez pas de ticket à gratter.\n"
            "Achetez-en avec `!acheter 4` (1500 coins) ou un pack×5 avec `!acheter 5` (7000 coins)."
        )
    _use_item(ctx.author.id, 4)
    save_data()

    # Génération prédéterminée du nombre de trèfles
    weights   = [SCRATCH_PRIZES[i][0] for i in range(6)]   # [18,40,20,15,5,2]
    n_clovers = random.choices(range(6), weights=weights, k=1)[0]

    view = ScratchView(ctx.author.id, n_clovers)
    tickets_left = owned_items.get(uid, {}).get('4', 0)

    embed = discord.Embed(
        title="🎟️ Ticket à Gratter !",
        description=(
            "Cliquez sur les **5 boutons** pour révéler les cases !\n"
            "Trouvez un maximum de 🍀 pour remporter le gros lot."
        ),
        color=0xf1c40f
    )
    embed.add_field(name="🏆 Gains", value=(
        "1 🍀 → **500 coins**\n"
        "2 🍀 → **2 000 coins**\n"
        "3 🍀 → **5 000 coins**\n"
        "4 🍀 → **10 000 coins**\n"
        "5 🍀 → **100 000 coins** 🎉"
    ), inline=True)
    embed.add_field(name="🎟️ Tickets restants", value=str(tickets_left), inline=True)
    embed.set_footer(text="Grattez les 5 cases pour découvrir votre lot !")
    await ctx.send(embed=embed, view=view)


# ── Usine ─────────────────────────────────────────────────────────────────

def _usine_embed(author_id):
    uid = str(author_id)
    f = factories.get(uid, {'workers': 0, 'last': datetime.now().isoformat(), 'upgraded': False})
    pending = _factory_earnings(uid)
    workers = f['workers']
    upgraded = f.get('upgraded') or _has_item(author_id, 6)
    rate = _factory_rate(workers, upgraded)
    next_cost = _factory_cost_next(workers)
    remaining = _factory_hire_remaining(uid)

    if next_cost is None:
        hire_line = f"✅ **Usine au maximum** ({MAX_FACTORY_WORKERS}/{MAX_FACTORY_WORKERS} employés)"
    elif remaining > 0:
        h = int(remaining // 3600)
        m = int((remaining % 3600) // 60)
        hire_line = f"⏳ Prochain employé : **{next_cost:,} coins** *(dispo dans {h}h {m}min)*"
    else:
        hire_line = f"💼 Prochain employé : **{next_cost:,} coins** *(dispo)*"

    embed = discord.Embed(title="🏭 Votre Usine", color=0x7f8c8d, description=(
        f"👷 **Employés :** {workers}/{MAX_FACTORY_WORKERS}\n"
        f"⚡ **Production :** {rate:,.0f} coins/heure\n"
        f"💰 **En attente :** {pending:,} coins\n"
        + ("🔧 **Usine améliorée** (+15% production)\n" if upgraded else "") +
        f"\n{hire_line}\n"
        "Utilisez les boutons ci-dessous."
    ))
    return embed, pending, next_cost


class UsineView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=180)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Ce n'est pas votre usine. Tapez `!usine`.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Embaucher", style=discord.ButtonStyle.success, emoji="👷")
    async def hire_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(self.author_id)
        f = factories.setdefault(uid, {'workers': 0, 'last': datetime.now().isoformat(), 'upgraded': False})
        cost = _factory_cost_next(f['workers'])
        if cost is None:
            return await interaction.response.send_message(
                f"❌ Vous avez déjà atteint le **maximum de {MAX_FACTORY_WORKERS} employés**.",
                ephemeral=True
            )
        remaining = _factory_hire_remaining(uid)
        if remaining > 0:
            h = int(remaining // 3600)
            m = int((remaining % 3600) // 60)
            return await interaction.response.send_message(
                f"⏳ Vous devez attendre **{h}h {m}min** avant d'embaucher un nouvel employé.",
                ephemeral=True
            )
        if coins[self.author_id] < cost:
            return await interaction.response.send_message(
                f"❌ Il vous faut **{cost:,} coins**. Solde : **{coins[self.author_id]:,}**",
                ephemeral=True
            )
        pending = _factory_earnings(uid)
        if pending > 0:
            coins[self.author_id] += pending
            f['last'] = datetime.now().isoformat()
        coins[self.author_id] -= cost
        f['workers'] += 1
        f['last_hire'] = datetime.now().isoformat()
        save_data()
        embed, _, _ = _usine_embed(self.author_id)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(
            f"👷 Employé #{f['workers']} recruté pour **{cost:,} coins** !\n"
            f"⏳ Prochain employé dispo dans **{cooldown_h('embaucher'):g}h**.",
            ephemeral=True
        )

    @discord.ui.button(label="Collecter", style=discord.ButtonStyle.primary, emoji="💰")
    async def collect_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(self.author_id)
        pending = _factory_earnings(uid)
        if pending <= 0:
            return await interaction.response.send_message(
                "❌ Aucun gain à collecter. Embauchez des employés !", ephemeral=True
            )
        coins[self.author_id] += pending
        factories[uid]['last'] = datetime.now().isoformat()
        save_data()
        embed, _, _ = _usine_embed(self.author_id)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(
            f"🏭 **{pending:,} coins** collectés ! Solde : **{coins[self.author_id]:,}**",
            ephemeral=True
        )

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed, _, _ = _usine_embed(self.author_id)
        await interaction.response.edit_message(embed=embed, view=self)


@bot.command(name="usine", aliases=["factory"])
async def cmd_usine(ctx):
    embed, _, _ = _usine_embed(ctx.author.id)
    await ctx.send(embed=embed, view=UsineView(ctx.author.id))


@bot.command(name="embaucher", aliases=["hire"])
async def cmd_embaucher(ctx):
    uid = str(ctx.author.id)
    f = factories.setdefault(uid, {'workers': 0, 'last': datetime.now().isoformat(), 'upgraded': False})
    cost = _factory_cost_next(f['workers'])
    if cost is None:
        return await ctx.send(f"❌ Vous avez déjà atteint le **maximum de {MAX_FACTORY_WORKERS} employés**.")
    remaining = _factory_hire_remaining(uid)
    if remaining > 0:
        h = int(remaining // 3600)
        m = int((remaining % 3600) // 60)
        return await ctx.send(f"⏳ Vous devez attendre **{h}h {m}min** avant d'embaucher un nouvel employé.")
    if coins[ctx.author.id] < cost:
        return await ctx.send(f"❌ Il vous faut **{cost:,} coins** pour embaucher. Solde : **{coins[ctx.author.id]:,}**")
    pending = _factory_earnings(uid)
    if pending > 0:
        coins[ctx.author.id] += pending
        f['last'] = datetime.now().isoformat()
    coins[ctx.author.id] -= cost
    f['workers'] += 1
    f['last_hire'] = datetime.now().isoformat()
    save_data()
    embed = discord.Embed(title="👷 Employé recruté !", color=0x2ecc71,
        description=(
            f"Vous avez recruté votre **{f['workers']}e employé(e)** pour **{cost:,} coins** !\n"
            f"👷 Effectif total : **{f['workers']}/{MAX_FACTORY_WORKERS}**\n"
            f"💰 Solde : **{coins[ctx.author.id]:,} coins**\n"
            f"⏳ Prochain employé dispo dans **{cooldown_h('embaucher'):g}h**."
        ))
    await ctx.send(embed=embed)

@bot.command(name="collecter", aliases=["collect", "recolter"])
async def cmd_collecter(ctx):
    uid     = str(ctx.author.id)
    pending = _factory_earnings(uid)
    if pending <= 0:
        return await ctx.send("❌ Aucun gain à collecter. Embauchez des employés avec `!embaucher` !")
    coins[ctx.author.id]   += pending
    factories[uid]['last']  = datetime.now().isoformat()
    save_data()
    embed = discord.Embed(title="🏭 Gains collectés !", color=0x2ecc71,
        description=f"Vous avez collecté **{pending:,} 🪙 coins** de votre usine !\n💰 Solde : **{coins[ctx.author.id]:,} coins**")
    await ctx.send(embed=embed)


# ── Course de voitures ────────────────────────────────────────────────────

def _course_embed():
    status = "✅ **Paris ouverts !**" if race_accepting else "⏸️ Paris fermés — attendez l'ouverture par un admin"
    embed = discord.Embed(title="🏎️ Courses de Voitures", color=0xe74c3c,
        description=f"{status}\n\nCliquez sur **🎯 Parier** ci-dessous pour miser sur un pilote.\n")
    total_bets = {}
    for b in race_bets.values():
        d = b['driver']
        total_bets[d] = total_bets.get(d, 0) + b['amount']
    for i, d in enumerate(race_drivers_live):
        wr = d['wins'] / max(d['races'], 1)
        odds = _race_odds(i)
        bets = total_bets.get(i, 0)
        embed.add_field(
            name=f"**{i+1}.** {d['name']}",
            value=f"Victoires : {d['wins']}/{d['races']} ({wr*100:.0f}%) | Cote : **×{odds}** | Paris : {bets:,} coins",
            inline=False
        )
    return embed


class CourseBetModal(discord.ui.Modal, title="🏎️ Parier sur la course"):
    pilote = discord.ui.TextInput(label="Numéro du pilote (1 à 5)", placeholder="Ex : 3", required=True, max_length=2)
    mise = discord.ui.TextInput(label="Mise", placeholder="Ex : 500 ou all", required=True, max_length=15)

    def __init__(self, author_id):
        super().__init__()
        self.author_id = author_id

    async def on_submit(self, interaction: discord.Interaction):
        if not race_accepting:
            return await interaction.response.send_message("❌ Les paris ne sont pas ouverts.", ephemeral=True)
        try:
            p = int(str(self.pilote.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Numéro de pilote invalide.", ephemeral=True)
        if p < 1 or p > len(race_drivers_live):
            return await interaction.response.send_message(f"❌ Pilote invalide (1–{len(race_drivers_live)}).", ephemeral=True)
        m, err = _resolve_mise(str(self.mise.value).strip(), self.author_id, 'course')
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        uid = str(self.author_id)
        if uid in race_bets:
            coins[self.author_id] += race_bets[uid]['amount']
        coins[self.author_id] -= m
        race_bets[uid] = {'driver': p - 1, 'amount': m}
        save_data()
        driver_name = race_drivers_live[p - 1]['name']
        odds = _race_odds(p - 1)
        await interaction.response.send_message(
            f"🏎️ Pari enregistré : **{m:,} coins** sur **{driver_name}** (cote ×{odds}) !",
            ephemeral=True
        )


class CourseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Parier", style=discord.ButtonStyle.success, emoji="🎯")
    async def bet_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not race_accepting:
            return await interaction.response.send_message("❌ Les paris ne sont pas ouverts.", ephemeral=True)
        await interaction.response.send_modal(CourseBetModal(interaction.user.id))

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_course_embed(), view=self)

    @discord.ui.button(label="Ouvrir les paris (Admin)", style=discord.ButtonStyle.primary, emoji="🔓", row=1)
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Réservé aux admins.", ephemeral=True)
        global race_accepting, race_bets
        race_accepting = True
        race_bets = {}
        save_data()
        await interaction.response.edit_message(embed=_course_embed(), view=self)
        await interaction.followup.send("✅ Les paris sont désormais ouverts !")

    @discord.ui.button(label="Lancer la course (Admin)", style=discord.ButtonStyle.danger, emoji="🏁", row=1)
    async def launch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Réservé aux admins.", ephemeral=True)
        if not race_accepting:
            return await interaction.response.send_message("❌ Aucune course ouverte.", ephemeral=True)
        await interaction.response.defer()
        await _run_race(interaction.channel, interaction.guild)


async def _run_race(channel, guild):
    """Lance la course (extraction de l'ancien lancer_course)."""
    global race_accepting, race_bets
    race_accepting = False

    total_bets = {}
    for b in race_bets.values():
        d = b['driver']
        total_bets[d] = total_bets.get(d, 0) + b['amount']
    grand_total = sum(total_bets.values()) or 1

    weights = []
    for i, d in enumerate(race_drivers_live):
        wr = d['wins'] / max(d['races'], 1)
        pop_factor = 1 - 0.2 * (total_bets.get(i, 0) / grand_total)
        weights.append(max(0.01, wr * pop_factor))

    winner_idx = random.choices(range(len(race_drivers_live)), weights=weights, k=1)[0]
    winner = race_drivers_live[winner_idx]
    for d in race_drivers_live:
        d['races'] += 1
    race_drivers_live[winner_idx]['wins'] += 1

    laps = [
        "🏎️ Les moteurs rugissent... C'est parti !",
        "⚡ Premier virage — bagarre en tête !",
        "🔥 Mi-course — les pilotes se battent !",
        f"🏁 **ARRIVÉE — {winner['name']} remporte la course !**"
    ]
    msg = await channel.send(laps[0])
    for txt in laps[1:]:
        await asyncio.sleep(2)
        await msg.edit(content=txt)

    winners_lines = []
    for uid, binfo in race_bets.items():
        uid_int = int(uid)
        if binfo['driver'] == winner_idx:
            odds = _race_odds(winner_idx)
            payout = int(binfo['amount'] * odds)
            coins[uid_int] += payout
            m = guild.get_member(uid_int)
            name = m.display_name if m else f"<@{uid}>"
            winners_lines.append(f"🏆 **{name}** : +**{payout - binfo['amount']:,}** coins (×{odds})")

    embed = discord.Embed(title=f"🏁 {winner['name']} remporte la course !", color=0xf1c40f)
    if winners_lines:
        embed.add_field(name="🏆 Gagnants", value='\n'.join(winners_lines[:10]), inline=False)
    else:
        embed.add_field(name="Dommage !", value="Personne n'avait misé sur le bon pilote.", inline=False)
    race_bets = {}
    save_data()
    await channel.send(embed=embed)


@bot.command(name="course", aliases=["race", "courses"])
async def cmd_course(ctx):
    await ctx.send(embed=_course_embed(), view=CourseView())


@bot.command(name="parier", aliases=["bet"])
async def cmd_parier(ctx, pilote: int, mise: str):
    if not race_accepting:
        return await ctx.send("❌ Les paris ne sont pas ouverts. Un admin doit utiliser `!ouvrir_course`.")
    if pilote < 1 or pilote > len(race_drivers_live):
        return await ctx.send(f"❌ Pilote invalide (1–{len(race_drivers_live)}).")
    mise, err = _resolve_mise(mise, ctx.author.id, 'course')
    if err: return await ctx.send(err)
    uid = str(ctx.author.id)
    if uid in race_bets:
        coins[ctx.author.id] += race_bets[uid]['amount']
    coins[ctx.author.id] -= mise
    race_bets[uid] = {'driver': pilote - 1, 'amount': mise}
    save_data()
    driver_name = race_drivers_live[pilote - 1]['name']
    odds        = _race_odds(pilote - 1)
    await ctx.send(f"🏎️ {ctx.author.mention} a misé **{mise:,} coins** sur **{driver_name}** (cote ×{odds}) !")

@bot.command(name="ouvrir_course")
@commands.has_permissions(administrator=True)
async def cmd_ouvrir_course(ctx):
    global race_accepting, race_bets
    race_accepting = True
    race_bets      = {}
    save_data()
    embed = discord.Embed(title="🏎️ Paris ouverts !", color=0x2ecc71,
        description="Les paris sont maintenant ouverts !\n`!course` — Voir les pilotes\n`!parier <n°> <mise>` — Miser\n\nL'admin lancera la course avec `!lancer_course`.")
    await ctx.send(embed=embed)

@bot.command(name="lancer_course")
@commands.has_permissions(administrator=True)
async def cmd_lancer_course(ctx):
    global race_accepting, race_bets
    if not race_accepting:
        return await ctx.send("❌ Ouvrez d'abord les paris avec `!ouvrir_course`.")
    race_accepting = False

    total_bets = {}
    for b in race_bets.values():
        d = b['driver']
        total_bets[d] = total_bets.get(d, 0) + b['amount']
    grand_total = sum(total_bets.values()) or 1

    weights = []
    for i, d in enumerate(race_drivers_live):
        wr         = d['wins'] / max(d['races'], 1)
        pop_factor = 1 - 0.2 * (total_bets.get(i, 0) / grand_total)
        weights.append(max(0.01, wr * pop_factor))

    winner_idx = random.choices(range(len(race_drivers_live)), weights=weights, k=1)[0]
    winner     = race_drivers_live[winner_idx]
    for d in race_drivers_live: d['races'] += 1
    race_drivers_live[winner_idx]['wins'] += 1

    laps = [
        "🏎️ Les moteurs rugissent... C'est parti !",
        "⚡ Premier virage — bagarre en tête !",
        "🔥 Mi-course — les pilotes se battent !",
        f"🏁 **ARRIVÉE — {winner['name']} remporte la course !**"
    ]
    msg = await ctx.send(laps[0])
    for txt in laps[1:]:
        await asyncio.sleep(2)
        await msg.edit(content=txt)

    winners_lines = []
    for uid, binfo in race_bets.items():
        uid_int = int(uid)
        if binfo['driver'] == winner_idx:
            odds   = _race_odds(winner_idx)
            payout = int(binfo['amount'] * odds)
            coins[uid_int] += payout
            m    = ctx.guild.get_member(uid_int)
            name = m.display_name if m else f"<@{uid}>"
            winners_lines.append(f"🏆 **{name}** : +**{payout - binfo['amount']:,}** coins (×{odds})")

    embed = discord.Embed(title=f"🏁 {winner['name']} remporte la course !", color=0xf1c40f)
    if winners_lines:
        embed.add_field(name="🏆 Gagnants", value='\n'.join(winners_lines[:10]), inline=False)
    else:
        embed.add_field(name="Dommage !", value="Personne n'avait misé sur le bon pilote.", inline=False)
    race_bets = {}
    save_data()
    await ctx.send(embed=embed)


# ── Admin — gestion des coins ─────────────────────────────────────────────

@bot.command(name="addcoins")
@commands.has_permissions(administrator=True)
async def cmd_addcoins(ctx, member: discord.Member, amount: int):
    coins[member.id] += amount
    save_data()
    verb = "ajouté à" if amount >= 0 else "retiré de"
    embed = discord.Embed(title="⚙️ Modification de coins", color=0x3498db,
        description=f"**{abs(amount):,} coins** {verb} {member.mention}.\n💰 Nouveau solde : **{coins[member.id]:,} coins**")
    await ctx.send(embed=embed)

@bot.command(name="removecoins")
@commands.has_permissions(administrator=True)
async def cmd_removecoins(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        return await ctx.send("❌ Montant invalide.")
    taken = min(amount, coins[member.id])
    coins[member.id] -= taken
    save_data()
    embed = discord.Embed(title="⚙️ Modification de coins", color=0xe74c3c,
        description=f"**{taken:,} coins** retirés de {member.mention}.\n💰 Nouveau solde : **{coins[member.id]:,} coins**")
    await ctx.send(embed=embed)


# =======================================================================
# ========================== TOURNOI ====================================
# =======================================================================

# ── Helpers tournoi ───────────────────────────────────────────────────────

def _t_participant(t, idx):
    for p in t['participants']:
        if p['idx'] == idx:
            return p
    return None

def _t_name(t, idx):
    p = _t_participant(t, idx)
    return p['name'] if p else f"Joueur #{idx+1}"

def _generate_round_matches(idxs: list, next_id: int):
    pool = list(idxs)
    matches = []
    for i in range(0, len(pool), 2):
        p1 = pool[i]
        p2 = pool[i+1] if i+1 < len(pool) else None
        matches.append({
            'match_id': next_id,
            'p1': p1, 'p2': p2,
            'winner': p1 if p2 is None else None,
        })
        next_id += 1
    return matches, next_id

def _round_done(matches):
    return all(m['winner'] is not None for m in matches)

def _team_size(t):
    return t.get('team_size', 1)

def _mode_label(t):
    ts = _team_size(t)
    return "Solo (1v1)" if ts == 1 else f"Équipes ({ts}v{ts})"

def _p_members(p):
    return p.get('members') or [p['captain']]

def _team_full(t, p):
    return len(_p_members(p)) >= _team_size(t)

def _build_tournament_embed(t, gid):
    status_map = {'registering': '📋 Inscriptions ouvertes', 'active': '⚔️ En cours', 'finished': '✅ Terminé'}
    ts = _team_size(t)
    embed = discord.Embed(
        title=f"🏆 Tournoi {_mode_label(t)}",
        description=(
            f"**Statut :** {status_map.get(t['status'], t['status'])}\n"
            f"**Prix :** {t['prize']:,} coins\n"
            f"**{'Équipes' if ts > 1 else 'Joueurs'} :** {len(t['participants'])}"
        ),
        color=0xf39c12
    )
    if t['participants']:
        lines = []
        for p in t['participants'][:20]:
            if ts > 1:
                members = _p_members(p)
                full = "✅" if len(members) >= ts else f"⏳ {len(members)}/{ts}"
                mem_str = ', '.join(f"<@{u}>" for u in members)
                lines.append(f"{p['idx']+1}. **{p['name']}** [{full}] — {mem_str}")
            else:
                lines.append(f"{p['idx']+1}. **{p['name']}**")
        title = "👥 Équipes inscrites" if ts > 1 else "👥 Inscrits"
        embed.add_field(name=title, value='\n'.join(lines), inline=False)
    return embed

async def _post_round(guild, t: dict, round_idx: int, gid: str):
    channel = guild.get_channel(t['channel_id'])
    if not channel:
        return
    rn = round_idx + 1
    matches = t['rounds'][round_idx]
    embed = discord.Embed(title=f"🏆 Tournoi — Tour {rn}", color=0xf39c12)
    for m in matches:
        p1n = _t_name(t, m['p1'])
        if m['p2'] is None:
            embed.add_field(name=f"✅ Match #{m['match_id']} — BYE",
                value=f"**{p1n}** passe automatiquement.", inline=False)
        else:
            p2n   = _t_name(t, m['p2'])
            cap1  = _t_participant(t, m['p1'])['captain']
            cap2  = _t_participant(t, m['p2'])['captain']
            done  = "✅ Terminé" if m['winner'] is not None else "⚔️ En attente"
            embed.add_field(
                name=f"⚔️ Match #{m['match_id']} — {p1n} VS {p2n}",
                value=f"<@{cap1}> VS <@{cap2}> — {done}",
                inline=False
            )
    await channel.send(embed=embed)
    for m in matches:
        if m['p2'] is not None and m['winner'] is None:
            p1  = _t_participant(t, m['p1'])
            p2  = _t_participant(t, m['p2'])
            view = MatchView(gid, m['match_id'], p1['name'], p2['name'],
                             p1['captain'], p2['captain'], m['p1'], m['p2'])
            me = discord.Embed(
                title=f"⚔️ Match #{m['match_id']}",
                description=(
                    f"**{p1['name']}** (<@{p1['captain']}>) **VS** "
                    f"**{p2['name']}** (<@{p2['captain']}>)\n\n"
                    f"⚠️ **Les deux capitaines** doivent cliquer le **même** "
                    f"vainqueur pour valider le match.\n"
                    f"*(Admin : `!win {m['p1']+1}` / `!win {m['p2']+1}` pour trancher.)*"
                ),
                color=0x9b59b6
            )
            await channel.send(embed=me, view=view)

async def _advance_tournament(guild, t: dict, gid: str):
    current = t['rounds'][t['current_round']]
    if not _round_done(current):
        return
    channel = guild.get_channel(t['channel_id'])
    winners = [m['winner'] for m in current]
    if len(winners) == 1:
        winner  = _t_participant(t, winners[0])
        prize   = t['prize']
        members = _p_members(winner) if winner else []
        prize_line = ""
        if winner and prize > 0 and members:
            share = prize // len(members)
            for m_uid in members:
                coins[m_uid] += share
            save_data()
            if len(members) > 1:
                prize_line = (f"💰 **{prize:,} coins** répartis entre "
                              f"**{len(members)}** membres (**{share:,}** chacun) !")
            else:
                prize_line = f"💰 **{prize:,} coins** versés au vainqueur !"
        t['status'] = 'finished'
        members_str = ', '.join(f"<@{u}>" for u in members)
        embed = discord.Embed(
            title="🏆 Tournoi terminé — Vainqueur !",
            description=(
                f"🥇 **{winner['name']}** remporte le tournoi !\n"
                f"{members_str}\n\n"
                + prize_line
            ),
            color=0xf1c40f
        )
        if channel:
            await channel.send(embed=embed)
        if gid in tournaments:
            del tournaments[gid]
        return
    # Prochain tour
    t['current_round'] += 1
    random.shuffle(winners)
    new_matches, t['next_match_id'] = _generate_round_matches(winners, t['next_match_id'])
    t['rounds'].append(new_matches)
    if channel:
        await channel.send(embed=discord.Embed(
            title=f"✅ Tour {t['current_round']} terminé !",
            description=f"**{len(winners)}** joueurs/équipes passent au tour suivant.",
            color=0x2ecc71
        ))
        await asyncio.sleep(2)
    await _post_round(guild, t, t['current_round'], gid)
    if _round_done(t['rounds'][t['current_round']]):
        await _advance_tournament(guild, t, gid)

# ── Views tournoi ─────────────────────────────────────────────────────────

def _already_registered(t, uid):
    """L'utilisateur fait-il déjà partie d'une équipe/inscription ?"""
    for p in t['participants']:
        if uid in _p_members(p):
            return True
    return False


class TournamentJoinView(discord.ui.View):
    def __init__(self, guild_id: str, team_size: int = 1):
        super().__init__(timeout=None)
        self.guild_id  = guild_id
        self.team_size = team_size
        if team_size == 1:
            btn = discord.ui.Button(label="✋ Rejoindre le tournoi",
                                    style=discord.ButtonStyle.success,
                                    custom_id="t_join_btn")
            btn.callback = self._solo_join
            self.add_item(btn)
        else:
            create = discord.ui.Button(label="➕ Créer une équipe",
                                       style=discord.ButtonStyle.success,
                                       custom_id="t_create_team")
            create.callback = self._create_team
            self.add_item(create)
            join = discord.ui.Button(label="🤝 Rejoindre une équipe",
                                     style=discord.ButtonStyle.primary,
                                     custom_id="t_join_team")
            join.callback = self._join_team
            self.add_item(join)

    async def _refresh(self, interaction, t, gid):
        embed = _build_tournament_embed(t, gid)
        embed.add_field(
            name="⚙️ Admin",
            value="`!prix_tournoi <montant>` · `!ouverture_tournoi`",
            inline=False
        )
        # Édite le message du tableau (et pas un éventuel message éphémère)
        board_id = t.get('board_message_id')
        if board_id and interaction.message and interaction.message.id == board_id:
            await interaction.message.edit(embed=embed, view=self)
            return
        channel = interaction.guild.get_channel(t['channel_id'])
        if channel and board_id:
            try:
                board = await channel.fetch_message(board_id)
                await board.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def _solo_join(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        t   = tournaments.get(gid)
        if not t or t['status'] != 'registering':
            return await interaction.response.send_message(
                "❌ Les inscriptions sont fermées.", ephemeral=True)
        uid = interaction.user.id
        if _already_registered(t, uid):
            return await interaction.response.send_message(
                "❌ Vous êtes déjà inscrit !", ephemeral=True)
        idx = len(t['participants'])
        t['participants'].append({
            'idx': idx, 'captain': uid,
            'name': interaction.user.display_name,
            'members': [uid],
        })
        await self._refresh(interaction, t, gid)
        await interaction.response.send_message(
            f"✅ **{interaction.user.display_name}** a rejoint le tournoi ! "
            f"({len(t['participants'])} inscrit(s))"
        )

    async def _create_team(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        t   = tournaments.get(gid)
        if not t or t['status'] != 'registering':
            return await interaction.response.send_message(
                "❌ Les inscriptions sont fermées.", ephemeral=True)
        uid = interaction.user.id
        if _already_registered(t, uid):
            return await interaction.response.send_message(
                "❌ Vous faites déjà partie d'une équipe !", ephemeral=True)
        idx = len(t['participants'])
        t['participants'].append({
            'idx': idx, 'captain': uid,
            'name': f"Équipe {interaction.user.display_name}",
            'members': [uid],
        })
        await self._refresh(interaction, t, gid)
        await interaction.response.send_message(
            f"✅ **{interaction.user.display_name}** a créé une équipe "
            f"(capitaine). Il manque **{self.team_size - 1}** joueur(s)."
        )

    async def _join_team(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        t   = tournaments.get(gid)
        if not t or t['status'] != 'registering':
            return await interaction.response.send_message(
                "❌ Les inscriptions sont fermées.", ephemeral=True)
        uid = interaction.user.id
        if _already_registered(t, uid):
            return await interaction.response.send_message(
                "❌ Vous faites déjà partie d'une équipe !", ephemeral=True)
        open_teams = [p for p in t['participants'] if not _team_full(t, p)]
        if not open_teams:
            return await interaction.response.send_message(
                "❌ Aucune équipe disponible. Créez-en une avec **➕ Créer une équipe**.",
                ephemeral=True)
        await interaction.response.send_message(
            "🤝 Choisissez l'équipe à rejoindre :",
            view=_JoinTeamSelectView(gid, self, open_teams),
            ephemeral=True
        )


class _JoinTeamSelectView(discord.ui.View):
    def __init__(self, guild_id, parent_view, open_teams):
        super().__init__(timeout=120)
        self.guild_id    = guild_id
        self.parent_view = parent_view
        t = tournaments.get(guild_id)
        ts = _team_size(t) if t else 1
        options = [
            discord.SelectOption(
                label=p['name'][:100],
                value=str(p['idx']),
                description=f"{len(_p_members(p))}/{ts} joueurs"
            )
            for p in open_teams[:25]
        ]
        self.select = discord.ui.Select(placeholder="Choisir une équipe…", options=options)
        self.select.callback = self._on_pick
        self.add_item(self.select)

    async def _on_pick(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        t   = tournaments.get(gid)
        if not t or t['status'] != 'registering':
            return await interaction.response.send_message(
                "❌ Les inscriptions sont fermées.", ephemeral=True)
        uid = interaction.user.id
        if _already_registered(t, uid):
            return await interaction.response.send_message(
                "❌ Vous faites déjà partie d'une équipe !", ephemeral=True)
        idx = int(self.select.values[0])
        p   = _t_participant(t, idx)
        if not p or _team_full(t, p):
            return await interaction.response.send_message(
                "❌ Cette équipe est introuvable ou déjà complète.", ephemeral=True)
        p.setdefault('members', [p['captain']]).append(uid)
        ts        = _team_size(t)
        remaining = ts - len(_p_members(p))
        full_txt  = "✅ Équipe complète !" if remaining <= 0 else f"Il manque **{remaining}** joueur(s)."
        await self.parent_view._refresh(interaction, t, gid)
        await interaction.response.send_message(
            f"✅ **{interaction.user.display_name}** a rejoint **{p['name']}** ! {full_txt}",
            ephemeral=False
        )


class MatchView(discord.ui.View):
    def __init__(self, guild_id: str, match_id: int,
                 p1_name: str, p2_name: str,
                 p1_cap: int,  p2_cap: int,
                 p1_idx: int,  p2_idx: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.match_id = match_id
        self.p1_cap   = p1_cap
        self.p2_cap   = p2_cap
        self.p1_idx   = p1_idx
        self.p2_idx   = p2_idx
        for idx, name, cid in [(p1_idx, p1_name, f"tw_{guild_id}_{match_id}_1"),
                               (p2_idx, p2_name, f"tw_{guild_id}_{match_id}_2")]:
            btn = discord.ui.Button(
                label=f"🏆 {name[:40]} a gagné",
                style=discord.ButtonStyle.success,
                custom_id=cid
            )
            btn.callback = self._make_cb(idx)
            self.add_item(btn)

    async def _finalize(self, interaction, t, gid, match, winner_idx, by_admin):
        match['winner'] = winner_idx
        loser_idx   = self.p2_idx if winner_idx == self.p1_idx else self.p1_idx
        winner_name = _t_name(t, winner_idx)
        loser_name  = _t_name(t, loser_idx)
        for item in self.children:
            item.disabled = True
        desc = (f"🏆 **{winner_name}** remporte le match !\n"
                f"❌ **{loser_name}** est éliminé.")
        if by_admin:
            desc += "\n*(résultat tranché par un admin)*"
        else:
            desc += "\n*(confirmé par les deux capitaines ✅)*"
        result_embed = discord.Embed(
            title=f"✅ Match #{self.match_id} — Résultat",
            description=desc,
            color=0x2ecc71
        )
        await interaction.response.edit_message(embed=result_embed, view=self)
        await _advance_tournament(interaction.guild, t, gid)

    def _make_cb(self, winner_idx: int):
        async def callback(interaction: discord.Interaction):
            gid = str(interaction.guild_id)
            t   = tournaments.get(gid)
            if not t or t['status'] != 'active':
                return await interaction.response.send_message(
                    "❌ Aucun tournoi actif.", ephemeral=True)
            match = next((m for m in t['rounds'][t['current_round']]
                          if m['match_id'] == self.match_id), None)
            if not match or match['winner'] is not None:
                return await interaction.response.send_message(
                    "❌ Ce match est déjà résolu.", ephemeral=True)
            uid        = interaction.user.id
            is_admin   = interaction.user.guild_permissions.administrator
            is_captain = uid in (self.p1_cap, self.p2_cap)
            if not is_captain and not is_admin:
                return await interaction.response.send_message(
                    "❌ Seuls les deux capitaines ou un admin peuvent déclarer le résultat.",
                    ephemeral=True)

            # Un admin (non-capitaine) tranche directement
            if is_admin and not is_captain:
                return await self._finalize(interaction, t, gid, match, winner_idx, by_admin=True)

            # Vote d'un capitaine
            votes = match.setdefault('votes', {})
            votes[uid] = winner_idx
            c1, c2 = self.p1_cap, self.p2_cap

            # Les deux capitaines se sont prononcés
            if c1 in votes and c2 in votes:
                if votes[c1] == votes[c2]:
                    return await self._finalize(interaction, t, gid, match, votes[c1], by_admin=False)
                # Désaccord : on réinitialise les votes
                match['votes'] = {}
                conflict = discord.Embed(
                    title=f"⚠️ Match #{self.match_id} — Désaccord !",
                    description=(
                        "Les deux capitaines ont désigné des vainqueurs **différents**.\n"
                        "Les votes ont été réinitialisés — mettez-vous d'accord et recliquez.\n"
                        "*(Un admin peut trancher avec son bouton ou `!win <n°>`.)*"
                    ),
                    color=0xe74c3c
                )
                return await interaction.response.edit_message(embed=conflict, view=self)

            # En attente du second capitaine
            other_cap = c2 if uid == c1 else c1
            waiting = discord.Embed(
                title=f"⚔️ Match #{self.match_id} — Vote enregistré",
                description=(
                    f"<@{uid}> a voté pour **{_t_name(t, winner_idx)}**.\n"
                    f"⏳ En attente de la confirmation de <@{other_cap}>…\n\n"
                    "*Le match sera validé quand les **deux capitaines** auront "
                    "désigné le **même** vainqueur.*"
                ),
                color=0xf39c12
            )
            await interaction.response.edit_message(embed=waiting, view=self)
        return callback

# ── Commandes tournoi ─────────────────────────────────────────────────────

@bot.command(name="tournois", aliases=["tournoi", "tournament"])
@commands.has_permissions(administrator=True)
async def cmd_tournoi(ctx, mode: str = None):
    gid = str(ctx.guild.id)
    if gid in tournaments:
        return await ctx.send(
            "❌ Un tournoi est déjà en cours. Annulez-le avec `!annuler_tournoi`.")
    if mode is None:
        return await ctx.send(
            "❓ **Usage :**\n"
            "`!tournois solo` — Tournoi individuel 1v1 (bouton pour rejoindre)\n"
            "`!tournois 2v2` — Tournoi par équipes de 2\n"
            "`!tournois 3v3` — Tournoi par équipes de 3\n"
            "`!tournois 4v4` — Tournoi par équipes de 4\n"
            "`!tournois 5v5` — Tournoi par équipes de 5")
    m = mode.lower().strip()
    # Modes : solo / 2v2 / 3v3 / 4v4 / 5v5 (le 'v' ou 'vs' accepté)
    if m in ('solo', 's', '1v1', '1vs1'):
        t_mode, team_size = 'solo', 1
    elif m in ('2v2', '2vs2'):
        t_mode, team_size = 'team', 2
    elif m in ('3v3', '3vs3'):
        t_mode, team_size = 'team', 3
    elif m in ('4v4', '4vs4'):
        t_mode, team_size = 'team', 4
    elif m in ('5v5', '5vs5'):
        t_mode, team_size = 'team', 5
    else:
        return await ctx.send(
            "❌ Mode invalide.\n"
            "Modes possibles : `solo`, `2v2`, `3v3`, `4v4`, `5v5`.")

    tournaments[gid] = {
        'mode': t_mode, 'team_size': team_size, 'prize': 0,
        'status': 'registering', 'host_id': ctx.author.id,
        'channel_id': ctx.channel.id, 'participants': [],
        'rounds': [], 'current_round': 0, 'next_match_id': 1,
    }
    t = tournaments[gid]
    embed = _build_tournament_embed(t, gid)
    embed.add_field(
        name="⚙️ Configuration (Admin)",
        value=(
            "`!prix_tournoi <montant>` — Définir le prix\n"
            "`!ouverture_tournoi` — Lancer et générer le tableau"
        ),
        inline=False
    )
    if team_size > 1:
        join_txt = (
            f"Équipes de **{team_size}** joueurs.\n"
            "➕ **Créer une équipe** (vous en devenez capitaine)\n"
            "🤝 **Rejoindre une équipe** existante."
        )
    else:
        join_txt = "Cliquez sur le bouton ci-dessous pour rejoindre !"
    embed.add_field(name="📋 Inscriptions", value=join_txt, inline=False)
    board = await ctx.send(embed=embed, view=TournamentJoinView(gid, team_size))
    t['board_message_id'] = board.id


@bot.command(name="prix_tournoi", aliases=["set_prize", "prize_tournoi"])
@commands.has_permissions(administrator=True)
async def cmd_prix_tournoi(ctx, montant: int):
    gid = str(ctx.guild.id)
    t   = tournaments.get(gid)
    if not t:
        return await ctx.send("❌ Aucun tournoi en cours.")
    if montant < 0:
        return await ctx.send("❌ Le montant doit être positif.")
    t['prize'] = montant
    await ctx.send(embed=discord.Embed(
        title="💰 Prix du tournoi défini !",
        description=f"Le vainqueur remportera **{montant:,} coins** !",
        color=0xf1c40f
    ))


@bot.command(name="ouverture_tournoi",
             aliases=["debut_tournoi", "bracket", "open_tournoi",
                      "lancer_tournoi", "start_tournoi"])
@commands.has_permissions(administrator=True)
async def cmd_ouverture_tournoi(ctx):
    gid = str(ctx.guild.id)
    t   = tournaments.get(gid)
    if not t:
        return await ctx.send("❌ Aucun tournoi en cours.")
    if t['status'] != 'registering':
        return await ctx.send("❌ Le tournoi est déjà lancé ou terminé.")

    ts = _team_size(t)
    dropped = []
    if ts > 1:
        # On écarte les équipes incomplètes
        complete = [p for p in t['participants'] if _team_full(t, p)]
        dropped  = [p for p in t['participants'] if not _team_full(t, p)]
        if len(complete) < 2:
            return await ctx.send(
                f"❌ Il faut au moins **2 équipes complètes** ({ts} joueurs) "
                f"pour lancer le tournoi.")
        t['participants'] = complete

    if len(t['participants']) < 2:
        return await ctx.send(
            "❌ Il faut au moins **2 participants** pour ouvrir le tableau.")
    if dropped:
        names = ', '.join(p['name'] for p in dropped)
        await ctx.send(f"⚠️ Équipe(s) incomplète(s) écartée(s) : **{names}**.")
    idxs = [p['idx'] for p in t['participants']]
    random.shuffle(idxs)
    round0, t['next_match_id'] = _generate_round_matches(idxs, t['next_match_id'])
    t['rounds'].append(round0)
    t['status']        = 'active'
    t['current_round'] = 0
    byes = sum(1 for m in round0 if m['p2'] is None)
    embed = discord.Embed(
        title="🏆 Tournoi lancé ! Le tableau est généré.",
        description=(
            f"👥 **{len(t['participants'])}** participants\n"
            f"⚔️ **{len(round0) - byes}** match(s) au 1er tour"
            + (f"\n🟢 **{byes}** BYE(s) automatique(s)" if byes else "") +
            f"\n💰 Prix : **{t['prize']:,} coins**"
        ),
        color=0x2ecc71
    )
    await ctx.send(embed=embed)
    await _post_round(ctx.guild, t, 0, gid)
    if _round_done(round0):
        await _advance_tournament(ctx.guild, t, gid)


@bot.command(name="win", aliases=["victoire"])
async def cmd_win(ctx, numero: int):
    gid = str(ctx.guild.id)
    t   = tournaments.get(gid)
    if not t or t['status'] != 'active':
        return await ctx.send("❌ Aucun tournoi actif en ce moment.")
    winner_idx = numero - 1
    p = _t_participant(t, winner_idx)
    if not p:
        return await ctx.send(f"❌ Participant #{numero} introuvable.")
    match = next((m for m in t['rounds'][t['current_round']]
                  if m['winner'] is None and
                  (m['p1'] == winner_idx or m['p2'] == winner_idx)), None)
    if not match:
        return await ctx.send(
            f"❌ **{p['name']}** n'a pas de match actif en ce moment.")
    p1 = _t_participant(t, match['p1'])
    p2 = _t_participant(t, match['p2']) if match['p2'] is not None else None
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send(
            "❌ Seul un **administrateur du serveur** peut déclarer le vainqueur d'un match.")
    loser_idx  = match['p2'] if winner_idx == match['p1'] else match['p1']
    match['winner'] = winner_idx
    embed = discord.Embed(
        title=f"✅ Match #{match['match_id']} — Résultat déclaré",
        description=(
            f"🏆 **{p['name']}** remporte le match !\n"
            f"❌ **{_t_name(t, loser_idx)}** est éliminé."
        ),
        color=0x2ecc71
    )
    await ctx.send(embed=embed)
    await _advance_tournament(ctx.guild, t, gid)


@bot.command(name="tournoi_status", aliases=["t_status", "bracket_status"])
async def cmd_tournoi_status(ctx):
    gid = str(ctx.guild.id)
    t   = tournaments.get(gid)
    if not t:
        return await ctx.send("❌ Aucun tournoi en cours.")
    embed = _build_tournament_embed(t, gid)
    if t['status'] == 'active' and t['rounds']:
        lines = []
        for m in t['rounds'][t['current_round']]:
            p1n = _t_name(t, m['p1'])
            if m['p2'] is None:
                lines.append(f"Match #{m['match_id']}: **{p1n}** (BYE ✅)")
            else:
                p2n  = _t_name(t, m['p2'])
                st   = f"✅ {_t_name(t, m['winner'])} gagne" if m['winner'] else "⚔️ En cours"
                lines.append(f"Match #{m['match_id']}: **{p1n}** vs **{p2n}** — {st}")
        embed.add_field(
            name=f"⚔️ Tour {t['current_round']+1}",
            value='\n'.join(lines) or "—",
            inline=False
        )
    await ctx.send(embed=embed)


@bot.command(name="annuler_tournoi", aliases=["cancel_tournoi", "cancel_tournament"])
@commands.has_permissions(administrator=True)
async def cmd_annuler_tournoi(ctx):
    gid = str(ctx.guild.id)
    if gid not in tournaments:
        return await ctx.send("❌ Aucun tournoi en cours.")
    del tournaments[gid]
    await ctx.send("✅ Le tournoi a été annulé.")

@bot.command(name="punition")
@commands.has_permissions(administrator=True)
async def cmd_punition(ctx, nombre: int, membre: discord.Member):
    if nombre <= 0:
        return await ctx.send("❌ Le nombre doit être supérieur à 0.")
    
    guild = ctx.guild
    
    # Créer le salon de punition
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        membre: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
    }
    salon = await guild.create_text_channel(
        f"punition-{membre.display_name}",
        overwrites=overwrites,
        reason=f"Punition pour {membre.display_name}"
    )
    
    # Couper l'accès à tous les autres salons
    for channel in guild.channels:
        if channel.id != salon.id:
            try:
                await channel.set_permissions(membre, view_channel=False, send_messages=False)
            except:
                pass
    
    # Sauvegarder la punition
    punitions[str(membre.id)] = {
        'salon_id': salon.id,
        'nombre': nombre,
        'actuel': 0,
        'guild_id': guild.id
    }
    
    await salon.send(
        f"🔒 {membre.mention} tu es en **punition** !\n"
        f"Tu dois compter de **1** jusqu'à **{nombre}** sans faire de faute.\n"
        f"⚠️ Si tu te trompes, ça repart de **0** !\n\n"
        f"Commence à compter : **1**"
    )
    await ctx.send(f"✅ {membre.mention} est en punition. Il doit compter jusqu'à {nombre}.")


@bot.command(name="annuler_punition")
@commands.has_permissions(administrator=True)
async def cmd_annuler_punition(ctx, membre: discord.Member):
    uid = str(membre.id)
    if uid not in punitions:
        return await ctx.send(f"❌ {membre.mention} n'est pas en punition.")
    
    await _liberer_membre(ctx.guild, membre)
    await ctx.send(f"✅ La punition de {membre.mention} a été annulée.")


async def _liberer_membre(guild, membre):
    uid = str(membre.id)
    if uid not in punitions:
        return
    
    data = punitions[uid]
    
    # Supprimer le salon de punition
    salon = guild.get_channel(data['salon_id'])
    if salon:
        await salon.delete()
    
    # Rendre l'accès aux salons
    for channel in guild.channels:
        try:
            await channel.set_permissions(membre, overwrite=None)
        except:
            pass
    
    del punitions[uid]
    
    try:
        await membre.send(f"✅ Ta punition sur **{guild.name}** est terminée, tu as retrouvé accès aux salons !")
    except:
        pass


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Vérification punition
    uid = str(message.author.id)
    if uid in punitions:
        data = punitions[uid]
        if message.channel.id == data['salon_id']:
            try:
                nombre_envoye = int(message.content.strip())
                attendu = data['actuel'] + 1
                
                if nombre_envoye == attendu:
                    data['actuel'] += 1
                    if data['actuel'] >= data['nombre']:
                        await message.channel.send(f"🎉 {message.author.mention} a compté jusqu'à **{data['nombre']}** ! Punition terminée !")
                        await _liberer_membre(message.guild, message.author)
                    else:
                        if data['actuel'] % 10 == 0:
                            await message.channel.send(f"✅ **{data['actuel']}/{data['nombre']}** — Continue !")
                else:
                    data['actuel'] = 0
                    await message.channel.send(f"❌ {message.author.mention} **FAUTE !** Tu as envoyé `{nombre_envoye}` au lieu de `{attendu}`. Repart de **1** !")
            except ValueError:
                data['actuel'] = 0
                await message.channel.send(f"❌ {message.author.mention} **FAUTE !** Ce n'est pas un nombre. Repart de **1** !")
            return

    # ... reste du on_message existant
    guild_id = message.guild.id if message.guild else None
    if guild_id and guild_id in silenced_users and message.author.id in silenced_users[guild_id]:
        try:
            await message.delete()
            fields = [
                ("Auteur", message.author.mention, True),
                ("Canal", message.channel.mention, True),
                ("Contenu", message.content if message.content else "*(Contenu non textuel ou vide)*", False)
            ]
            await send_log_message(message.guild, LOG_MODERATION_CHANNEL_ID, "🗑️ Message d'Utilisateur Silencé Supprimé", f"Le message de {message.author.mention} a été supprimé car l'utilisateur est silencé.", discord.Color.red(), fields)
        except discord.Forbidden:
            print(f"❌ Impossible de supprimer le message de {message.author.name} (permissions).")
        except Exception as e:
            print(f"❌ Erreur lors de la suppression du message de {message.author.name}: {e}")
        return

    if message.content.startswith('!') and message.content.lower().endswith(' aide'):
        command_name = message.content[1:-5]
        command_help = {
            "warn": "**!warn @membre [raison]**\nDonne un avertissement à un membre. Auto-mute après 5 warns.",
        }
        if command_name in command_help:
            embed = discord.Embed(
                title=f"ℹ️ Aide - !{command_name}",
                description=command_help[command_name],
                color=0x3498db
            )
            embed.set_footer(text=f"Demandé par {message.author.display_name} • Tapez !aide pour voir toutes les commandes")
            await message.channel.send(embed=embed)
            return

    await bot.process_commands(message)
    
    

MORSE_CODE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
    'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
    'Y': '-.--', 'Z': '--..', '0': '-----', '1': '.----', '2': '..---',
    '3': '...--', '4': '....-', '5': '.....', '6': '-....', '7': '--...',
    '8': '---..', '9': '----.'
}

MORSE_WORDS = [
    "CHAT", "CHIEN", "DISCORD", "BONJOUR", "PYTHON", "SERVEUR",
    "PUNITION", "COMPTE", "GAMING", "MUSIQUE", "SOLEIL", "DRAGON"
]

def _text_to_morse(text):
    return ' '.join(MORSE_CODE.get(c, '') for c in text.upper() if c in MORSE_CODE)

def _morse_to_image(morse_text, word):
    width, height = 800, 150
    img = Image.new('RGB', (width, height), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    
    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    draw.text((20, 20), f"Mot à coder : {word}", fill=(255, 200, 0), font=font_big)
    draw.text((20, 80), morse_text, fill=(255, 255, 255), font=font_big)
    draw.text((20, 120), "Recopiez le code morse ci-dessus ↑", fill=(150, 150, 150), font=font_small)
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf

morse_punitions = {}

@bot.command(name="morse")
@commands.has_permissions(administrator=True)
async def cmd_morse(ctx, membre: discord.Member):
    guild = ctx.guild
    
    if str(membre.id) in morse_punitions:
        return await ctx.send(f"❌ {membre.mention} est déjà en punition morse !")
    
    # Créer le salon
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        membre: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
    }
    salon = await guild.create_text_channel(
        f"morse-{membre.display_name}",
        overwrites=overwrites,
        reason=f"Punition morse pour {membre.display_name}"
    )
    
    word = random.choice(MORSE_WORDS)
    morse = _text_to_morse(word)
    
    morse_punitions[str(membre.id)] = {
        'salon_id': salon.id,
        'guild_id': guild.id,
        'word': word,
        'morse': morse,
        'attempts': 0
    }
    
    buf = _morse_to_image(morse, word)
    await salon.send(
        f"🔒 {membre.mention} tu es en **punition morse** !\n"
        f"Recopie exactement le code morse affiché dans l'image ci-dessous.\n"
        f"⚠️ Pas de copier-coller possible — c'est une image !\n"
        f"✅ Réussis pour retrouver accès aux salons.",
        file=discord.File(buf, filename="morse.png")
    )
    
    # Couper l'accès aux autres salons
    for channel in guild.channels:
        if channel.id != salon.id:
            try:
                await channel.set_permissions(membre, view_channel=False, send_messages=False)
            except:
                pass
    
    await ctx.send(f"✅ {membre.mention} est en punition morse !")


async def _liberer_membre_morse(guild, membre):
    uid = str(membre.id)
    if uid not in morse_punitions:
        return
    data = morse_punitions[uid]
    salon = guild.get_channel(data['salon_id'])
    if salon:
        await salon.delete()
    for channel in guild.channels:
        try:
            await channel.set_permissions(membre, overwrite=None)
        except:
            pass
    del morse_punitions[uid]
    try:
        await membre.send(f"✅ Ta punition morse sur **{guild.name}** est terminée !")
    except:
        pass


@bot.command(name="annuler_morse")
@commands.has_permissions(administrator=True)
async def cmd_annuler_morse(ctx, membre: discord.Member):
    if str(membre.id) not in morse_punitions:
        return await ctx.send(f"❌ {membre.mention} n'est pas en punition morse.")
    await _liberer_membre_morse(ctx.guild, membre)
    await ctx.send(f"✅ Punition morse de {membre.mention} annulée.")
    
# =======================================================================
# ======================== FIN CASINO ===================================
# =======================================================================

keep_alive()

token = os.getenv("TOKEN")
if token is not None:
    bot.run(token)
else:
    print("Erreur : Le token Discord n'est pas défini dans les variables d'environnement. Veuillez le configurer.")