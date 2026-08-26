import discord
from discord.ext import commands, tasks
import asyncio
import aiohttp
import os
import logging
import math
import random
import json
import io
import re
import unicodedata
from collections import defaultdict, deque
from dotenv import load_dotenv
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont
from keep_alive import keep_alive
import db_bs
import db_members
from economy_v2 import (
    economy_router,
    validate_command_names,
)
from economy_v2.backups import start_backup_scheduler

load_dotenv()


def _format_ranked_updated_at(iso_ts: str | None) -> str | None:
    """Formate le timestamptz renvoyé par Supabase (bs_ranked_cache.updated_at)
    en '%d/%m %H:%M', comme l'ancien bs_family_ranked_updated_at fabriqué à la main."""
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace('Z', '+00:00'))
    except ValueError:
        return iso_ts
    return dt.astimezone(BS_SEASON_TZ).strftime('%d/%m %H:%M')

# IDs des salons de logs — étaient enveloppés dans os.getenv(...) alors que
# ce sont déjà les IDs numériques directs (aucune variable d'environnement ne
# s'appelle littéralement "1515780858483576923"), ce qui rendait ces 3 logs
# silencieusement inactifs (send_log_message ne fait rien si channel_id est
# None). Corrigé le 26/07/2026. Salon de logs unifié le 11/08/2026 : les 3
# constantes pointent toutes vers le même salon (demande explicite "je veux
# tooooout les logs du bot" au même endroit, plus besoin de jongler entre
# plusieurs salons de logs).
LOG_MODERATION_CHANNEL_ID = 1528513026691563540
LOG_GIVEAWAY_CHANNEL_ID = 1528513026691563540
LOG_GENERAL_CHANNEL_ID = 1528513026691563540
LEAVE_LOG_CHANNEL_ID = 1513110805707620405  # salon staff uniquement — rapport de départ détaillé
BRAWLSTARS_API_KEY = (os.getenv("BRAWLSTARS_API_KEY") or "").strip() or None

# ── Système de tickets maison (remplace tickets.bot) ──
TICKET_CATEGORY_ID = 1513110806382772407  # catégorie Discord par défaut où sont créés les salons de ticket
# Override par motif de ticket (clé de TICKET_CATEGORIES -> ID de catégorie Discord) —
# voir !set_ticket. Un motif absent de ce dict retombe sur TICKET_CATEGORY_ID.
TICKET_CATEGORY_IDS: dict[str, int] = {}
LOG_TICKET_CHANNEL_ID = 1513117932228706374  # salon #logs-ticket
# Mêmes IDs de rôle que STAFF_ROLE_IDS côté site (src/lib/access.ts) — pour
# que "staff" veuille dire la même chose partout. Reste le fallback pour un
# motif custom (ajouté via !set_ticket) qui n'a pas d'entrée dédiée ci-dessous.
TICKET_STAFF_ROLE_IDS = {1513110804595998788, 1516514610881237084}

# Nouvelle hiérarchie de rôles (demande du 20/08/2026, voir sondage #général).
ROLE_FONDA_ID = 1513110804621430888
ROLE_ADMIN_ID = 1539949380881358898  # ex-"Technicien Discord", renommé Admin
ROLE_MODERATEUR_ID = 1516514610881237084  # = Staff Discord (déjà dans TICKET_STAFF_ROLE_IDS)
ROLE_RECRUTEUR_ID = 1517971980299534517
ROLE_PRESIDENT_ID = 1513110804621430889
ROLE_VICE_PRESIDENT_ID = 1513110804621430887
ROLE_CONSEILLER_ID = 1513110804621430886

DISCORD_MOD_STAFF_ROLE_IDS = {ROLE_FONDA_ID, ROLE_ADMIN_ID, ROLE_MODERATEUR_ID}


def _is_discord_mod_staff(member: discord.Member) -> bool:
    """Ligne Fonda/Admin/Modérateur uniquement — pas le staff club (Recruteur/
    Président/Vice-président/Conseiller). Utilisé pour !lock(_serveur) :
    demande du 21/08/2026, seuls ces 3 rôles gardent l'écriture pendant un
    verrouillage."""
    return is_bot_owner(member) or member.guild_permissions.administrator or any(
        r.id in DISCORD_MOD_STAFF_ROLE_IDS for r in member.roles
    )

# Qui voit le salon d'un ticket, par motif (clé de TICKET_CATEGORIES) — les
# incidents serveur, les candidatures générales et "autre" ne concernent que
# la ligne Fonda/Admin/Modérateur, pas le staff de club (confirmé par les
# Fonda le 21/08/2026) ; les candidatures club concernent le staff de club +
# les recruteurs, pas la modération générale. Tout motif custom ajouté via
# !set_ticket qui n'a pas d'entrée ici retombe sur TICKET_STAFF_ROLE_IDS (voir
# _ticket_staff_role_ids_for). Listes (pas des set) : l'ordre est repris tel
# quel dans le message d'accueil du ticket (voir _join_fr_ou).
TICKET_CATEGORY_STAFF_ROLE_IDS: dict[str, list[int]] = {
    "candidature": [ROLE_FONDA_ID, ROLE_ADMIN_ID, ROLE_MODERATEUR_ID],
    "club_recruitment": [ROLE_RECRUTEUR_ID, ROLE_PRESIDENT_ID, ROLE_VICE_PRESIDENT_ID, ROLE_CONSEILLER_ID],
    "incident": [ROLE_FONDA_ID, ROLE_ADMIN_ID, ROLE_MODERATEUR_ID],
    "other": [ROLE_FONDA_ID, ROLE_ADMIN_ID, ROLE_MODERATEUR_ID],
}
# Alias conservé pour la clarté du code appelant (même règle que les incidents
# côté site, voir _is_incident_staff dans keep_alive.py).
TICKET_INCIDENT_STAFF_ROLE_IDS = TICKET_CATEGORY_STAFF_ROLE_IDS["incident"]


def _ticket_staff_role_ids_for(category: str) -> list[int] | set[int]:
    return TICKET_CATEGORY_STAFF_ROLE_IDS.get(category, TICKET_STAFF_ROLE_IDS)


def _join_fr_ou(items: list[str]) -> str:
    """Liste à la française : virgules entre les items, "ou" avant le
    dernier (ex: "A, B ou C") — "Un membre du A ou B ou C" ne se lit pas
    naturellement dès qu'il y a 3+ rôles (voir #logs-ticket du 20/08/2026)."""
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " ou " + items[-1]


# Motifs proposés dans le panel de ticket (!ticket_panel) — clé -> libellé affiché
# (emoji + texte). Défauts ci-dessous, modifiables via !set_ticket ajouter/retirer
# (persisté dans data.json, voir load_data/save_data).
TICKET_CATEGORIES: dict[str, str] = {
    "candidature": "💼 Candidature",
    "club_recruitment": "🎯 Recrutement Club",
    "incident": "🔴 Incident",
    "other": "❓ Autre",
}

# ── Déclaration d'absences ──
LOG_ABSENCE_CHANNEL_ID = 1538829307810676746
ABSENCE_ROLE_ID = 1538836433609953391  # rôle "Absent", ajouté/retiré automatiquement (voir _sync_absence_role, sync_absence_roles)

# Intents du bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.messages = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)
bot_start_time = datetime.now()
_slash_synced = False
slash_global_purged = False  # persisté : la purge des commandes globales ne doit se faire qu'une fois

# --- Variables globales pour la persistance des données ---
# Les données seront chargées depuis data.json
warns = {}
punitions = {}
mutes = {}
silenced_users = {}
# Journal d'audit des actions de modération (warn/mute/ban/silence/punition) —
# contrairement à warns/mutes/punitions qui ne décrivent que l'état ACTUEL (et perdent
# toute trace une fois résolus/levés), ce journal est append-only : chaque action y
# laisse une entrée permanente. Voir _log_moderation. Indexé par membre (dict, pas de
# plafond global) pour que l'historique d'un membre ne soit jamais tronqué par
# l'activité de modération sur d'autres membres.
moderation_log: dict[str, list] = {}
coins = defaultdict(int)
giveaway_data = {}
giveaway_tasks = {}
daily_cooldowns = {}    # str(user_id) -> ISO datetime
work_cooldowns = {}     # str(user_id) -> ISO datetime
beg_cooldowns = {}      # str(user_id) -> ISO datetime
active_bj = {}          # (guild_id, user_id) -> BlackjackGame
poker_games = {}        # guild_id -> PokerGame
active_hl = {}          # user_id -> HigherLowerView (partie en cours)
active_mines = {}       # user_id -> MinesView (partie en cours)
pirated_users = set()  # user_ids espionnés par CASINO_HINT_USER_ID (toggle via !pirater)

# ── Systèmes avancés ──────────────────────────────────────────────────
CRYPTO_BASE = {'BTC': 45000, 'ETH': 3000, 'DOGE': 10, 'SOL': 12000, 'XRP': 60}
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
    4: {'name': '🎟️ Ticket à gratter',   'price': 200,  'desc': '1 ticket à gratter', 'unique': False},
    5: {'name': '💼 Pack ×5 Tickets',    'price': 5000,  'desc': '5 tickets à gratter', 'unique': False},
    6: {'name': '🏭 Amélioration Usine', 'price': 3000,    'desc': '+15% production usine (unique)', 'unique': True},
    7: {'name': '📈 Cours de Trading',   'price': 2000,    'desc': '+15% gains ventes crypto (unique)', 'unique': True},
    8: {'name': '🏪 Ouvrir Épicerie',    'price': 80_000,  'desc': 'Débloque l\'épicerie · Requiert : Usine 10/10 + améliorée', 'unique': True, 'biz': 'epicerie'},
    9: {'name': '🍔 Ouvrir Fast Food',   'price': 300_000, 'desc': 'Débloque le fast food · Requiert : Épicerie 8/8 + améliorée', 'unique': True, 'biz': 'fastfood'},
   10: {'name': '🍽️ Ouvrir Restaurant', 'price': 800_000, 'desc': 'Débloque le restaurant · Requiert : Fast Food 10/10 + amélioré', 'unique': True, 'biz': 'restaurant'},
}
# Boucliers (remplace les anciens items 3 "Bouclier Anti-Vol" et 11 "Antivirus") :
# protection active à durée fixe contre !voler/!rob/!hacker, achetée via !bouclier <durée>.
# Ne se brise jamais suite à une attaque subie ; se brise uniquement si le porteur attaque
# lui-même (!voler/!rob/!hacker), avec un cooldown de rachat qui scale avec la durée cassée.
SHIELD_TIERS = {
    '12h': {'hours': 12,  'price': 1000, 'cooldown_min': 15},
    '24h': {'hours': 24,  'price': 2000, 'cooldown_min': 30},
    '72h': {'hours': 72,  'price': 4000, 'cooldown_min': 60},
    '7j':  {'hours': 168, 'price': 7000, 'cooldown_min': 120},
}
SHIELD_STREAK_WINDOW_H = 2    # fenêtre pour considérer deux casses comme "rapprochées"
SHIELD_STREAK_MULT     = 1.5  # multiplicateur du cooldown de rachat par casse rapprochée
STEAL_GRACE_MIN        = 30   # protection minimale après avoir subi une attaque (remplace les 3h/6h)

# Les boucliers sont aussi achetables via !shop / !acheter (items 3, 11-13 — comble les
# trous laissés par les anciens items 3/11 retirés) — même état partagé (shield_active)
# que !bouclier, prix toujours pris depuis SHIELD_TIERS.
_SHIELD_ITEM_IDS = {'12h': 3, '24h': 11, '72h': 12, '7j': 13}
SHOP_ITEMS.update({
    _SHIELD_ITEM_IDS[tier]: {
        'name': f'🛡️ Bouclier {tier}', 'price': info['price'],
        'desc': f"Protection totale {tier} contre vol/rob/hack — voir `!bouclier` (cooldown si cassé : {info['cooldown_min']} min)",
        'unique': False, 'shield_tier': tier,
    }
    for tier, info in SHIELD_TIERS.items()
})

BIZ_DEFS = {
    'epicerie': {
        'name': 'Épicerie', 'emoji': '🏪', 'color': 0x27ae60,
        'shop_item': 8, 'open_cost': 80_000,
        'max_workers': 8,
        'worker_costs': [3_000, 4_500, 6_000, 8_000, 10_000, 13_000, 16_000, 20_000],
        'base_rate': 100, 'upgrade_cost': 8_000, 'upgrade_bonus': 0.20,
        'requires': ('factory', 10, True),
        'hire_cd_hours': 2,
    },
    'fastfood': {
        'name': 'Fast Food', 'emoji': '🍔', 'color': 0xe67e22,
        'shop_item': 9, 'open_cost': 300_000,
        'max_workers': 10,
        'worker_costs': [5_000, 7_500, 10_000, 13_500, 17_000, 22_000, 27_500, 34_000, 42_000, 52_000],
        'base_rate': 73, 'upgrade_cost': 60_000, 'upgrade_bonus': 0.20,
        'requires': ('epicerie', 8, True),
        'hire_cd_hours': 4,
    },
    'restaurant': {
        'name': 'Restaurant Gastronomique', 'emoji': '🍽️', 'color': 0x8e44ad,
        'shop_item': 10, 'open_cost': 800_000,
        'max_workers': 12,
        'worker_costs': [16_000, 24_000, 34_000, 46_000, 60_000, 78_000, 98_000, 122_000, 148_000, 178_000, 212_000, 250_000],
        'base_rate': 55, 'upgrade_cost': None, 'upgrade_bonus': None,
        'requires': ('fastfood', 10, True),
        'rep_mult': [1.0, 1.1, 1.2, 1.35, 1.5, 1.7],
        'hire_cd_hours': 8,
    },
}
JOBS = {
    'hacker': {'name': '💻 Hacker',   'action' : '`!hacker @cible', 'desc': 'Vole la crypto des autres (cd 1h)'},
    'mineur':  {'name': '⛏️ Mineur',   'action': '`!miner`',         'desc': 'Mine 50–200 coins par heure'},
    'escroc':  {'name': '🎭 Escroc',   'action': 'Bonus passif',     'desc': '+20% succès sur `!voler` · +20% montant sur `!rob`'},
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
    0: (45, 0,      "😢 Aucun trèfle... Rien cette fois."),
    1: (33, 300,    "🍀 1 trèfle — +300 coins !"),
    2: (14, 1500,   "🍀🍀 2 trèfles — +1 500 coins !"),
    3: ( 6, 4000,   "🍀🍀🍀 3 trèfles — +4 000 coins !"),
    4: ( 2, 15000,  "🍀🍀🍀🍀 4 trèfles — +15 000 coins !"),
    5: ( 0.3, 100000, "🍀🍀🍀🍀🍀 5 trèfles — +100 000 coins !! 🎉"),
}

crypto_prices    = dict(CRYPTO_BASE)
price_history    = {}   # str(symbol) -> [float, ...]  (30 derniers points)
crypto_trends    = {}   # str(symbol) -> float  (tendance/momentum courant)
# Volatilité propre à chaque crypto (écart-type du bruit par tick)
CRYPTO_VOL   = {'BTC': 0.013, 'ETH': 0.018, 'SOL': 0.023, 'XRP': 0.029, 'DOGE': 0.033}
CRYPTO_FLOOR = {'BTC': 0.60, 'ETH': 0.60, 'SOL': 0.60, 'XRP': 0.60, 'DOGE': 0.50}
CRYPTO_CEIL  = {'BTC': 2.00, 'ETH': 2.00, 'SOL': 2.00, 'XRP': 2.00, 'DOGE': 1.50}
crypto_holdings  = {}   # str(uid) -> {symbol: float}
safes            = {}   # str(uid) -> int
factories        = {}   # str(uid) -> {'workers': int, 'last': ISO, 'upgraded': bool}
jobs_data        = {}   # str(uid) -> {'job': str}
owned_items      = {}   # str(uid) -> {str(item_id): int}
businesses       = {}   # str(uid) -> {biz_key: {workers, last, upgraded, last_hire, [reputation, last_collect]}}
locations        = {}   # str(uid) -> {'ville': str, 'lat': float, 'lon': float}
theft_cooldowns  = {}   # str(uid) -> ISO
miner_cooldowns  = {}   # str(uid) -> ISO
hacker_cooldowns = {}   # str(uid) -> ISO
risque_cooldowns = {}   # str(uid) -> ISO  (cooldown 3h)
rob_cooldowns    = {}   # str(uid) -> ISO  (cooldown 12h)
steal_immunity   = {}   # str(uid) -> ISO expiration (grâce de 30min après avoir subi une attaque)
shield_active       = {}   # str(uid) -> {'tier','hours','until'} — bouclier payant en cours
shield_cooldown     = {}   # str(uid) -> {'until','min_hours'} — verrou de rachat après une casse volontaire
shield_break_streak = {}   # str(uid) -> {'count','last_break'} — escalade anti-spam d'attaques
race_bets        = {}   # str(uid) -> {'driver': int, 'amount': int}
race_drivers_live = [dict(d) for d in RACE_DRIVERS_BASE]
race_accepting    = False
tournaments      = {}   # str(guild_id) → tournament dict
teams            = {}   # str(team_id) -> {name, leader, members:[uid,...], treasury:int, created:ISO}
user_team        = {}   # str(uid) -> str(team_id)
team_state       = {'competition_open': False, 'next_id': 1}
disabled_cmds    = set()  # noms de commandes désactivées
cmd_role_perms   = {}   # name -> [role_id, ...] (allowed roles, empty=all)
casino_banned_users = set()  # uid (int) bannis de toutes les commandes casino
casino_paused    = False  # pause volontaire de tout le casino (!casino_pause), PAS persistée entre redémarrages

# ── Nouvelles fonctionnalités ─────────────────────────────────────────────
# str(channel_id) -> {str(target_id): true|false|null} — valeur de send_messages
# AVANT verrouillage, pour chaque cible (rôle @everyone ou rôle staff) touchée par
# !lock/!lock_serveur, afin que !unlock(_serveur) restaure l'état exact plutôt que
# de rouvrir en aveugle un salon qui était peut-être déjà restreint avant.
lock_overwrite_backup = {}
daily_streaks     = {}   # str(uid) -> {'streak': int, 'last_day': 'YYYY-MM-DD'}
ticket_purchases  = {}   # str(uid) -> {'count': int, 'day': 'YYYY-MM-DD'}
birthdays        = {}   # str(uid) -> {'day': int, 'month': int, 'guild_id': int}
crypto_alerts    = {}   # str(uid) -> [{'symbol': str, 'target': float, 'direction': str}]
tournament_elo   = {}   # str(uid) -> int (score ELO tournoi)
bs_accounts      = {}   # str(uid) -> {'tag','name','trophies','ranked_pts','ranked_tier'}
bs_role_config   = {'trophies': {}, 'ranked': {}}  # 'trophies': {str(min): role_id}, 'ranked': {tier_name: role_id}
# bs_family_clubs, bs_family_ranked_cache, bs_family_ranked_updated_at et
# bs_trophy_history vivent désormais dans Supabase (voir db_bs.py) — plus de
# globals ni de persistance JSON pour ces champs.
# str(tag_clan) -> {'name','description','type','requiredTrophies','trophies','members':[{tag,name,trophies,role}]}
# — volontairement PAS persisté dans data.json : entièrement reconstruit depuis l'API
# officielle à chaque sync_trophy_history (toutes les heures), donc une valeur périmée
# après un redémarrage n'a aucun intérêt à être gardée sur disque.
bs_family_club_details = {}
FAMILY_CLUBS_PANEL_CHANNEL_ID = None  # salon du panel auto-actualisé (!clubs_panel), voir refresh_family_clubs_panel
# Un message par embed (header + un par clan), pas un seul message multi-embeds : Discord limite
# à 6000 caractères cumulés tous embeds confondus PAR MESSAGE, qu'un roster de 30 membres peut
# à lui seul approcher — incident du 21/08/2026 (HTTPException 50035 "Embed size exceeds 6000").
FAMILY_CLUBS_PANEL_MESSAGE_IDS = []  # liste ordonnée [header_id, club1_id, club2_id, ...], éditée en place
ADMIN_LOG_CHANNEL_ID = 1528513026691563540  # écrasé par load_data() si !set_admin_log/!set_logs a déjà été utilisé
CASINO_LOG_CHANNEL_ID = None  # pas de salon dédié par défaut — à configurer via !set_logs casino
draft_sessions       = {}   # channel_id -> session dict (phase de ban Brawl Stars)
theft_stats           = {}   # str(uid_victim) -> {'attempts': int, 'success': int}
snipe_cache           = {}   # channel_id -> [{'author', 'content', 'at', 'attachments'}, ...]
daily_sell_volume     = {}   # str(uid) -> {symbol: {date_str: float}} — volume brut vendu dans la journée
crypto_buy_cooldowns  = {}   # str(uid) -> {symbol: ISO} — CD 30min entre achats du même symbole
crypto_sell_cooldowns = {}   # str(uid) -> {symbol: ISO} — CD 30min entre ventes du même symbole
crypto_hold_since    = {}   # str(uid) -> {symbol: ISO} — timestamp dernier achat (hold min 10min)
cold_wallets         = {}   # str(uid) -> {symbol: {'qty': float, 'locked_until': ISO}}
crypto_market_frozen = False  # si True, achats et ventes crypto désactivés

# ── Ranked 1v1 interne (indépendant de !duel, tournament_elo et RANKED_TIERS BS) ──
ranked_1v1        = {}   # str(uid) -> {'points','wins','losses','reputation','banned_until'}
ranked_challenges = {}   # str(id) -> {'type':'open'|'target','challenger','target','guild_id','channel_id','message_id','created_at'}
ranked_pending    = {}   # "minuid_maxuid" -> {'p1','p2','guild_id','channel_id','message_id','created_at','votes':{}}
ranked_pair_daily = {}   # "minuid_maxuid" -> {'date':'YYYY-MM-DD','count':int}
ranked_reports    = {}   # str(target_uid) -> [{'reporter','reason','guild_id','created_at','resolved'}]
ranked_report_cooldowns = {}  # "reporter_target" -> ISO datetime
ranked_season_month = None  # "YYYY-MM" — mois de la saison en cours
casino_season_month = None  # "YYYY-MM" — dernier mois où le casino a été reset automatiquement
BS_SEASON_TZ = ZoneInfo("Europe/Paris")
# bs_season_month, bs_season_start_date et bs_trophy_evolution_history vivent
# désormais dans Supabase (bs_season_state / bs_season_archive, voir db_bs.py).
RANKED_CHALLENGE_CHANNEL_ID = 1526529629974695977  # #commandes... — tableau des défis 1v1
RANKED_LOG_CHANNEL_ID       = 1526529856421105715  # #chat-duels — log public des résultats validés
RANKED_1V1_TIERS = [
    (0,    '🥉 Bronze'),
    (100,  '🥈 Argent'),
    (250,  '🥇 Or'),
    (450,  '💎 Diamant'),
    (700,  '🔥 Mythique'),
    (1000, '👑 Légende'),
]
RANKED_1V1_DELTA = {
    # tier_diff (adversaire - joueur) -> (gain_victoire, perte_defaite)
    1:  (25, -15),  # adversaire mieux classé
    0:  (20, -20),  # même niveau
    -1: (15, -25),  # adversaire moins bien classé
}
RANKED_REP_START         = 100
RANKED_REP_PENALTY       = 20
RANKED_REP_BAN_THRESHOLD = 40
RANKED_REP_BAN_HOURS     = 72
RANKED_CHALLENGE_TTL_H   = 24
RANKED_MAX_DUELS_PER_DAY_PAIR = 2
RANKED_SEARCH_ROLE_ID    = 1526561617695866901  # "Recherche Duel" — porté tant qu'un défi/duel 1v1 est en cours


def _r1v1_profile(uid_str: str) -> dict:
    return ranked_1v1.setdefault(uid_str, {
        'points': 0, 'wins': 0, 'losses': 0,
        'reputation': RANKED_REP_START, 'banned_until': None,
    })


def _r1v1_tier_index(points: int) -> int:
    idx = 0
    for i, (min_pts, _name) in enumerate(RANKED_1V1_TIERS):
        if points >= min_pts:
            idx = i
    return idx


def _r1v1_tier_name(points: int) -> str:
    return RANKED_1V1_TIERS[_r1v1_tier_index(points)][1]


def _r1v1_delta(is_win: bool, own_points: int, opp_points: int) -> int:
    diff = max(-1, min(1, _r1v1_tier_index(opp_points) - _r1v1_tier_index(own_points)))
    win_gain, loss_loss = RANKED_1V1_DELTA[diff]
    return win_gain if is_win else loss_loss


def _r1v1_pair_key(uid1, uid2) -> str:
    a, b = sorted([int(uid1), int(uid2)])
    return f"{a}_{b}"


def _r1v1_banned(uid_str: str):
    """Retourne (banni: bool, temps_restant_str: str|None)."""
    prof = ranked_1v1.get(uid_str)
    if not prof or not prof.get('banned_until'):
        return False, None
    until = datetime.fromisoformat(prof['banned_until'])
    now = datetime.now()
    if until <= now:
        prof['banned_until'] = None
        return False, None
    rem = until - now
    h, rem_s = divmod(int(rem.total_seconds()), 3600)
    m = rem_s // 60
    return True, (f"{h}h {m}min" if h else f"{m}min")


def _r1v1_pair_cap_ok(uid1, uid2) -> bool:
    key = _r1v1_pair_key(uid1, uid2)
    today = datetime.now().strftime('%Y-%m-%d')
    entry = ranked_pair_daily.get(key)
    if not entry or entry.get('date') != today:
        return True
    return entry.get('count', 0) < RANKED_MAX_DUELS_PER_DAY_PAIR


def _r1v1_pair_increment(uid1, uid2):
    key = _r1v1_pair_key(uid1, uid2)
    today = datetime.now().strftime('%Y-%m-%d')
    entry = ranked_pair_daily.get(key)
    if not entry or entry.get('date') != today:
        entry = {'date': today, 'count': 0}
    entry['count'] += 1
    ranked_pair_daily[key] = entry


def _r1v1_apply_result(winner_id: int, loser_id: int):
    """Met à jour points/wins/losses des deux joueurs. Retourne (win_delta, loss_delta)."""
    wp = _r1v1_profile(str(winner_id))
    lp = _r1v1_profile(str(loser_id))
    win_delta = _r1v1_delta(True, wp['points'], lp['points'])
    loss_delta = _r1v1_delta(False, lp['points'], wp['points'])
    wp['points'] = max(0, wp['points'] + win_delta)
    lp['points'] = max(0, lp['points'] + loss_delta)
    wp['wins'] += 1
    lp['losses'] += 1
    _r1v1_pair_increment(winner_id, loser_id)
    return win_delta, loss_delta


_R1V1_MONTH_NAMES_FR = [
    'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
    'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre',
]


def _r1v1_month_label(month_key: str) -> str:
    year, month = month_key.split('-')
    return f"{_R1V1_MONTH_NAMES_FR[int(month) - 1]} {year}"


def _r1v1_leaderboard_entries(guild, month=None):
    """Entrées triées par points pour la saison en cours (month=None) ou une saison archivée."""
    source = ranked_1v1 if month is None else db_bs.get_ranked_1v1_season(month)
    entries = []
    for uid_str, prof in source.items():
        if not (prof.get('wins', 0) or prof.get('losses', 0)):
            continue
        member = guild.get_member(int(uid_str)) if guild else None
        name = member.display_name if member else f"<@{uid_str}>"
        entries.append({
            'name': name, 'points': prof.get('points', 0),
            'tier': _r1v1_tier_name(prof.get('points', 0)),
            'wins': prof.get('wins', 0), 'losses': prof.get('losses', 0),
        })
    entries.sort(key=lambda e: e['points'], reverse=True)
    return entries


# ── Configuration prix / mises (modifiable par !prix_casino) ─────────────
BOT_OWNER_IDS = {1056848438270115900, 550678866839207937}   # happy_gt3 & Clément — créateurs du bot
PROTECTED_FROM_PUNISH_ID = 550678866839207937  # Azog — utilisé par les réactions cosmétiques (ping/jeux) et !rush

# ID du joueur qui reçoit un aperçu privé (éphémère) des résultats du casino.
# Les jeux interactifs (higherlower, mines) affichent un hint uniquement
# visible par ce joueur ; les jeux instantanés (coinflip, roulette, slots,
# risque) ajustent silencieusement le résultat en sa faveur.
CASINO_HINT_USER_ID = 1056848438270115900  # happy_gt3

# Active/désactive les avantages casino ci-dessus (résultats truqués + hints)
# pour CASINO_HINT_USER_ID — toggle via !triche, utilisable en MP au bot.
casino_cheat_enabled = True

# Extension du territoire active. Les extensions sont temporaires et ne sont
# volontairement pas persistées : un redémarrage du bot met fin à leur effet.
territory_extension = {'name': None, 'until': None, 'channel_id': None}
territory_extension_prompts = set()
territory_extension_daily = {}  # extension_id -> timestamp ISO de la dernière activation


def _casino_chance_multiplier() -> float:
    until = territory_extension.get('until')
    if until and datetime.now() < until:
        return 7.77 if territory_extension.get('name') == 'idle_death_gamble' else 1.0
    if until:
        territory_extension.update(name=None, until=None, channel_id=None)
    return 1.0


def _casino_success(base_chance: float) -> bool:
    """Jet de chance casino affecté par l'extension du territoire active."""
    chance = max(0.0, min(1.0, base_chance * _casino_chance_multiplier()))
    return random.random() < chance


def _casino_lucky_randint(low: int, high: int) -> int:
    """Effectue le nombre de tirages bonus actif et garde le meilleur gain."""
    return max(random.randint(low, high) for _ in range(_casino_luck_attempts()))


def _casino_luck_attempts() -> int:
    """Convertit un multiplicateur décimal en nombre de tirages sans le tronquer."""
    multiplier = _casino_chance_multiplier()
    whole = math.floor(multiplier)
    return max(1, whole + (1 if random.random() < multiplier - whole else 0))

# Immunisés aux commandes de modération négatives (warn/mute/ban/silence/punition) : Azog + Vynaro (happy_gt3)
MOD_IMMUNE_IDS = {550678866839207937, 1056848438270115900}

PROTECTED_REJECT_LINES = [
    "😤 Tu veux punir {target} ? Mais t'es fada toi.",
    "❌ Niet. {target} est increvable ici.",
    "🚫 {target} est intouchable, retente ta chance sur quelqu'un d'autre.",
    "😂 Punir {target} ? Dans tes rêves.",
    "👑 On ne touche pas à la royauté ({target}).",
    "🛡️ {target} a une immunité divine, désolé pour toi.",
    "💀 Tu vas t'attirer des ennuis en t'attaquant à {target}.",
    "🐐 {target} est au-dessus de ces lois, circule.",
    "🙅 Sanction refusée : {target} est protégé par décret royal.",
    "😤 T'as cru quoi là, sanctionner {target} ? Non non non.",
    "🚨 Alerte : tentative de punition sur {target} détectée et bloquée.",
    "👀 {target} te regarde essayer de le punir, ça ne marchera pas.",
    "🔒 Accès refusé — {target} est verrouillé contre toute sanction.",
    "😎 {target} esquive encore une fois, comme toujours.",
    "🐐 Le GOAT {target} ne se laisse pas faire.",
]

async def _check_protected_target(ctx, member: discord.Member) -> bool:
    """Bloque une commande de modération négative visant un membre de MOD_IMMUNE_IDS.
    Retourne True si la commande doit s'arrêter là."""
    if member and member.id in MOD_IMMUNE_IDS:
        await ctx.send(random.choice(PROTECTED_REJECT_LINES).format(target=member.mention))
        return True
    return False


def _log_moderation(action: str, target, moderator, reason: str = None, extra: str = None):
    """Ajoute une entrée au journal d'audit (voir moderation_log) — n'appelle PAS
    save_data() lui-même, à faire par l'appelant juste après (comme pour toute
    autre mutation d'état)."""
    moderation_log.setdefault(str(target.id), []).append({
        'action': action,
        'target_id': str(target.id),
        'target_name': target.display_name,
        'moderator': moderator.display_name,
        'reason': reason,
        'extra': extra,
        'timestamp': datetime.now().isoformat(),
    })


# ═════════════════════════════════════════════════════════════════════════
# ── Anti-raid / anti-spam automatique ───────────────────────────────────
# Deux détecteurs indépendants sur chaque message (voir on_message) :
#  1) flood de messages (5 en 5s) -> avertissement, puis mute si ça continue
#  2) pings de rôle répétés sur des messages distincts (3 en 15s) -> mute direct
# Seuils volontairement larges pour ne jamais gêner une conversation
# normale. Demande du 10/08/2026.
# ═════════════════════════════════════════════════════════════════════════
ANTIRAID_MSG_WINDOW_SECONDS = 5
ANTIRAID_MSG_THRESHOLD = 5
ANTIRAID_WARN_ESCALATE_SECONDS = 20  # si le flood continue dans cette fenêtre après l'avertissement -> mute
ANTIRAID_ROLE_PING_WINDOW_SECONDS = 15
ANTIRAID_ROLE_PING_THRESHOLD = 3
ANTIRAID_MUTE_MINUTES = 5
ANTIRAID_REPEAT_WINDOW_SECONDS = 30 * 60  # récidive dans les 30 min -> mute suivant plus long
ANTIRAID_MUTE_MAX_MINUTES = 30
ANTIRAID_STAFF_ROLE_ID = 1513110804595998788
ANTIRAID_STAFF_CHANNEL_ID = 1516398004012318863

_antiraid_msg_times: dict[int, deque] = {}
_antiraid_role_ping_times: dict[int, deque] = {}
_antiraid_last_warned: dict[int, datetime] = {}
_antiraid_repeat: dict[int, tuple[int, datetime]] = {}  # uid -> (nb de mutes anti-raid consécutifs, dernier mute)


def _antiraid_exempt(member: discord.Member) -> bool:
    return member.id in MOD_IMMUNE_IDS or member.guild_permissions.administrator


def _is_punition_channel(channel_id: int) -> bool:
    """Salons de !punition (voir punitions) — le principe même de cette punition
    demande d'y compter rapidement (spam de nombres), ce que l'anti-raid
    confondrait avec un flood et sanctionnerait par-dessus la punition déjà en cours."""
    return any(d.get('salon_id') == channel_id for d in punitions.values())


async def _antiraid_mute(message: discord.Message, reason: str) -> None:
    """Mute anti-raid — réutilise exactement le mécanisme de !mute (rôle
    'Muted' + mutes[guild.id][member.id], démute géré par check_mutes,
    déjà en tâche de fond)."""
    guild = message.guild
    member = message.author

    mute_role = discord.utils.get(guild.roles, name="Muted")
    if not mute_role:
        try:
            mute_role = await guild.create_role(name="Muted", permissions=discord.Permissions.none())
            for channel in guild.channels:
                try:
                    await channel.set_permissions(mute_role, send_messages=False, speak=False, add_reactions=False)
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            return
    if mute_role in member.roles:
        return

    # Récidive dans les 30 min -> mute suivant plus long (10, 20, 30 min max).
    count, last_mute = _antiraid_repeat.get(member.id, (0, None))
    if last_mute and (datetime.now() - last_mute).total_seconds() > ANTIRAID_REPEAT_WINDOW_SECONDS:
        count = 0
    count += 1
    minutes = min(ANTIRAID_MUTE_MINUTES * count, ANTIRAID_MUTE_MAX_MINUTES)
    now = datetime.now()
    _antiraid_repeat[member.id] = (count, now)
    end_time = now + timedelta(minutes=minutes)

    try:
        await member.add_roles(mute_role, reason=f"Anti-raid : {reason}")
    except discord.Forbidden:
        return
    guild_id = guild.id
    user_id = member.id
    if guild_id not in mutes:
        mutes[guild_id] = {}
    mutes[guild_id][user_id] = {"end_time": end_time, "reason": f"Anti-raid automatique : {reason}"}
    _log_moderation('mute_auto_antiraid', member, guild.me, reason=reason, extra=f"{minutes} min")
    save_data()

    try:
        await message.channel.send(
            f"🔇 {member.mention} a été mute {minutes} min (anti-raid : {reason}).",
            delete_after=15,
        )
    except Exception:
        pass

    staff_channel = guild.get_channel(ANTIRAID_STAFF_CHANNEL_ID)
    if staff_channel:
        try:
            await staff_channel.send(
                content=f"<@&{ANTIRAID_STAFF_ROLE_ID}>",
                embed=discord.Embed(
                    title="🚨 Anti-raid : mute automatique",
                    description=(
                        f"{member.mention} (`{member}`) a été mute **{minutes} min**.\n"
                        f"**Raison :** {reason}\n"
                        f"**Salon :** {message.channel.mention}\n"
                        f"**Récidive :** {count}"
                    ),
                    color=0xe74c3c,
                    timestamp=discord.utils.utcnow(),
                ),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except Exception:
            pass


async def _antiraid_check(message: discord.Message) -> bool:
    """Retourne True si le message a déclenché un mute (l'appelant peut
    alors arrêter de traiter ce message)."""
    if not message.guild or not isinstance(message.author, discord.Member):
        return False
    member = message.author
    if _antiraid_exempt(member):
        return False
    if _is_punition_channel(message.channel.id):
        return False

    now = datetime.now()
    uid = member.id

    # Pings de rôle répétés sur des messages distincts -> mute direct.
    if message.role_mentions:
        ping_times = _antiraid_role_ping_times.setdefault(uid, deque())
        ping_times.append(now)
        while ping_times and (now - ping_times[0]).total_seconds() > ANTIRAID_ROLE_PING_WINDOW_SECONDS:
            ping_times.popleft()
        if len(ping_times) >= ANTIRAID_ROLE_PING_THRESHOLD:
            ping_times.clear()
            await _antiraid_mute(message, "pings de rôle répétés sur plusieurs messages")
            return True

    # Flood de messages -> avertissement, puis mute si ça continue.
    times = _antiraid_msg_times.setdefault(uid, deque())
    times.append(now)
    while times and (now - times[0]).total_seconds() > ANTIRAID_MSG_WINDOW_SECONDS:
        times.popleft()
    if len(times) >= ANTIRAID_MSG_THRESHOLD:
        last_warn = _antiraid_last_warned.get(uid)
        if last_warn and (now - last_warn).total_seconds() < ANTIRAID_WARN_ESCALATE_SECONDS:
            times.clear()
            await _antiraid_mute(message, "flood de messages malgré l'avertissement")
            return True
        times.clear()
        _antiraid_last_warned[uid] = now
        try:
            await message.channel.send(
                f"⚠️ {member.mention} ralentis un peu, tu postes trop vite — pense aux autres membres du salon.",
                delete_after=10,
            )
        except Exception:
            pass
    return False


# ── Réactions Azog : surprises cosmétiques sur mention/commandes, aucun blocage ──────────
AZOG_PING_LINES = [
    "Quelqu'un a osé prononcer le nom du GOAT 🐐",
    "👑 Le roi a été invoqué.",
    "Attention, présence du patron détectée.",
    "On m'a sonné ?",
    "💀 Vous parlez du boss dans son dos ?",
    "Azog voit tout. Azog sait tout.",
    "🐐 Le GOAT passait par là.",
    "Chuuut, on ne réveille pas la légende pour rien.",
    "Chaque mention de son nom ajoute +1 à sa légende.",
    "Le serveur tremble légèrement.",
    "😤 Qui a osé ?",
    "Le patron a des yeux partout.",
]
AZOG_PING_EMOJIS = ['🐐', '👑', '💀', '😤', '🔥', '👀']
AZOG_PING_CHANCE = 0.22
AZOG_PING_COOLDOWN_MIN = 12
_azog_ping_last = None  # datetime | None — cooldown global anti-spam, pas persisté (purement cosmétique)

AZOG_VICTIM_SUCCESS_LINES = [
    "😱 Quelqu'un vient de braquer le **GOAT**... courage {attacker}, t'en auras besoin.",
    "🚨 Alerte générale : le patron vient de se faire voler. Priez pour {attacker}.",
    "💀 {attacker} vient peut-être de signer son arrêt de mort en s'attaquant à Azog.",
    "Personne ne sort indemne d'un coup contre le GOAT... bonne chance {attacker}.",
]
AZOG_VICTIM_FAIL_LINES = [
    "😂 {attacker} a essayé de toucher au **GOAT**. Résultat prévisible.",
    "Le patron ne se laisse pas faire aussi facilement, {attacker}.",
    "🐐 On ne s'attaque pas à la légende impunément, {attacker}.",
    "Tentative... courageuse de {attacker} sur le boss. Raté.",
]
AZOG_DUEL_WIN_LINES = [
    "🐐 Sans surprise, le **GOAT** l'emporte encore.",
    "👑 Une victoire de plus pour la légende.",
]
AZOG_DUEL_LOSE_LINES = [
    "😱 Le **GOAT** vient de tomber ! Journée historique.",
    "Même les légendes trébuchent parfois... suspect.",
]
AZOG_GIFT_LINES = [
    "🙏 Un tribut digne du **GOAT**.",
    "👑 Offrande acceptée par sa majesté Azog.",
]


def _azog_flavor(lines, **kwargs) -> str:
    return random.choice(lines).format(**kwargs)


async def _maybe_azog_ping_reaction(message):
    """Réaction surprise (rare, cooldownée) quand Azog est mentionné dans un message normal."""
    global _azog_ping_last
    if message.author.id == PROTECTED_FROM_PUNISH_ID:
        return
    if not any(m.id == PROTECTED_FROM_PUNISH_ID for m in message.mentions):
        return
    now = datetime.now()
    if _azog_ping_last and (now - _azog_ping_last).total_seconds() < AZOG_PING_COOLDOWN_MIN * 60:
        return
    if random.random() > AZOG_PING_CHANCE:
        return
    _azog_ping_last = now
    try:
        if random.random() < 0.5:
            await message.add_reaction(random.choice(AZOG_PING_EMOJIS))
        else:
            await message.channel.send(random.choice(AZOG_PING_LINES))
    except Exception:
        pass


BS_FAMILY_GUILD_ID = 1513110804499795988  # serveur Discord de la famille — voir sync_discord_members
DEV_ROLE_ID = 1513110804595998789  # rôle Technicien Discord (développeurs du bot)
DEV_PING_CHANCE = 0.30
DEV_PING_COOLDOWN_MIN = 10
_dev_ping_last = None  # datetime | None — cooldown global anti-spam, pas persisté (purement cosmétique)
DEV_PING_LINES = [
    "Allez on se réveille les mangeurs de carte graphique, ça coince encore.",
    "Y'a un bug quelque part, sortez de vos grottes.",
    "Le café refroidit pendant que vous debug depuis 3h.",
    "Encore un stack overflow à l'horizon les gars.",
    "Quelqu'un a encore oublié un point-virgule ?",
    "Les devs sont réveillés ou ils dorment sur leur clavier ?",
    "Ça sent le `git blame` qui va faire mal.",
    "Un ping technicien = quelqu'un a encore tout cassé.",
    "Sortez la caféine, ça va être une longue nuit.",
    "Encore une erreur 404 dans vos vies sociales ?",
    "Le serveur va bien merci de demander, vous par contre...",
    "Qui a touché au code sans tester avant de push ?",
    "Ambiance 'ça marchait sur ma machine' dans 3, 2, 1...",
    "Les mangeurs de RGB sont convoqués.",
    "On dirait qu'il y a encore une exception non catchée.",
    "Debug mode activé, plaignez-vous après.",
    "Un problème dans le Matrix, sortez de vos IDE.",
    "Ça pue le rollback dans les prochaines minutes.",
    "Vos souris ont trop chauffé, on dirait.",
    "Nouvelle mission : trouvez le bug avant qu'il vous trouve.",
]
DEV_PING_POSITIVE_LINES = [
    "Ah enfin un peu d'amour pour les mangeurs de carte graphique 🥹",
    "Merci à vous, les héros de l'ombre du code.",
    "Une ovation méritée pour l'équipe technique !",
    "On applaudit les artisans du bug-free (ou presque).",
    "Enfin un ping qui fait plaisir au cœur des devs.",
    "Les développeurs rougissent de fierté.",
    "Ça fait chaud au cœur, merci !",
    "Une tape dans le dos pour ceux qui codent la nuit.",
    "Le café n'aura pas été bu pour rien aujourd'hui.",
    "Un +1 pour l'équipe technique, bien mérité.",
    "Les mangeurs de carte graphique apprécient la reconnaissance.",
    "Ça fait plaisir de voir que ça tourne bien !",
    "Merci, ça motive à continuer de coder.",
    "L'équipe technique reçoit vos louanges avec fierté.",
    "Un peu de gratitude, ça fait toujours plaisir aux devs.",
    "Bravo à vous aussi d'avoir remarqué le travail bien fait.",
    "Les développeurs se sentent (enfin) valorisés.",
    "Merci, on va pouvoir dormir l'esprit tranquille ce soir.",
    "Une petite victoire de plus pour l'équipe.",
    "Ça fait plaisir, merci du soutien !",
]
DEV_PING_POSITIVE_KEYWORDS = [
    'merci', 'bravo', 'nickel', 'parfait', 'top', 'gg', 'super', 'génial', 'genial',
    'incroyable', 'ça marche', 'ca marche', 'stylé', 'style', 'bien joué', 'bien joue',
    'félicit', 'felicit', 'excellent', 'propre', 'clean', 'énorme', 'enorme', 'fier', 'love',
]
DEV_PING_NEGATIVE_KEYWORDS = [
    'bug', 'marche pas', 'marche plus', 'cassé', 'casse', 'erreur', 'plante', 'planté',
    'down', 'lag', 'déconne', 'deconne', 'problème', 'probleme', 'souci', 'ça bug', 'ca bug',
    'crash', 'nul', 'chiant', 'relou', 'buggé', 'bugge', 'foutu', 'marche toujours pas',
]


def _dev_ping_pick_lines(content: str) -> list:
    """Choisit la banque positive ou négative selon des mots-clés simples dans le message.
    Ambigu ou aucun mot-clé -> banque négative (le ton d'origine, le plus fréquent en pratique)."""
    lower = content.lower()
    is_negative = any(k in lower for k in DEV_PING_NEGATIVE_KEYWORDS)
    is_positive = any(k in lower for k in DEV_PING_POSITIVE_KEYWORDS)
    if is_positive and not is_negative:
        return DEV_PING_POSITIVE_LINES
    return DEV_PING_LINES


RUSH_USER_ID = 602807768046632971  # aussi autorisé : créateurs du bot + Azog
TWISTY_USER_ID = 860057663064899584  # aussi autorisé : créateurs du bot + Azog

@bot.hybrid_command(name="rush")
async def cmd_rush(ctx):
    if ctx.author.id != RUSH_USER_ID and not is_bot_owner(ctx.author) and ctx.author.id != PROTECTED_FROM_PUNISH_ID:
        return await ctx.send("❌ Cette commande est réservée à Rush (et rien qu'à lui, désolé).")
    embed = discord.Embed(title="👑 Le verdict est tombé", description="**Rush** > Twisty", color=0xf1c40f)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="twisty")
async def cmd_twisty(ctx):
    if ctx.author.id != TWISTY_USER_ID and not is_bot_owner(ctx.author) and ctx.author.id != PROTECTED_FROM_PUNISH_ID:
        return await ctx.send("❌ Cette commande est réservée à Twisty (et rien qu'à lui, désolé).")
    embed = discord.Embed(title="👑 Le verdict est tombé", description="**Twisty** > Rush", color=0x3498db)
    await ctx.send(embed=embed)


async def _maybe_dev_ping_reaction(message):
    """Réaction surprise (30%, cooldown 10 min) quand le rôle Technicien est mentionné —
    banque positive ou négative choisie selon le ton du message."""
    global _dev_ping_last
    if not any(r.id == DEV_ROLE_ID for r in message.role_mentions):
        return
    now = datetime.now()
    if _dev_ping_last and (now - _dev_ping_last).total_seconds() < DEV_PING_COOLDOWN_MIN * 60:
        return
    if random.random() > DEV_PING_CHANCE:
        return
    _dev_ping_last = now
    try:
        await message.channel.send(random.choice(_dev_ping_pick_lines(message.content)))
    except Exception:
        pass


GRAMMAR_ROAST_COOLDOWN_MIN = 3  # par utilisateur, anti-spam — pas persisté (purement cosmétique)
_grammar_roast_last: dict = {}  # {user_id: datetime} dernier roast reçu

# Détection par regex des fautes de français les plus emblématiques/meme (pas une
# vraie analyse grammaticale) : sava/ça va, je c'est/je sais, c'est/s'est, et/est,
# son/sont, a/à... Chaque entrée : (regex, correction). La correction est soit une
# chaîne fixe, soit une fonction du match (quand elle dépend du texte matché).
GRAMMAR_MISTAKE_PATTERNS = [
    (re.compile(r"\bsava\b", re.IGNORECASE), "ça va"),
    (re.compile(r"\bsa\s+(va|marche|craint|d[ée]pend|part|arrive|passe|vaut|chie|saoule|gave)\b", re.IGNORECASE),
     lambda m: f"ça {m.group(1)}"),
    (re.compile(r"\bje\s+c['’]est\b", re.IGNORECASE), "je sais"),
    (re.compile(r"\btu\s+c['’]est\b", re.IGNORECASE), "tu sais"),
    (re.compile(r"\b(il|elle|on|ils|elles)\s+c['’]est\b", re.IGNORECASE), lambda m: f"{m.group(1)} s'est"),
    (re.compile(r"\bc['’]et\b", re.IGNORECASE), "c'est"),
    (re.compile(
        r"\b(il|elle|on)\s+et\s+(?!(?:il|elle|on|nous|vous|ils|elles|moi|toi|lui|eux|je|tu)\b)",
        re.IGNORECASE), lambda m: f"{m.group(1)} est"),
    (re.compile(r"\b(ils|elles)\s+son\b", re.IGNORECASE), lambda m: f"{m.group(1)} sont"),
    (re.compile(r"\bje\s+peu\b", re.IGNORECASE), "je peux"),
    (re.compile(r"\bquand\s+a\s+(moi|toi|lui|elle|nous|vous|eux|elles)\b", re.IGNORECASE),
     lambda m: f"quant à {m.group(1)}"),
    (re.compile(r"\bsans\s+doutes\b", re.IGNORECASE), "sans doute"),
    (re.compile(r"\bmalgr[eè]s\b", re.IGNORECASE), "malgré"),
    # Infinitif au lieu du participe passé après avoir — "j'ai manger", "il a jouer"...
    (re.compile(
        r"\b(j['’]ai|tu\s+as|il\s+a|elle\s+a|on\s+a|nous\s+avons|vous\s+avez|ils\s+ont|elles\s+ont)\s+"
        r"(mang|jou|regard|parl|mont|donn|pass|arriv|rest|aim|chant|march|travaill|oubli|racont|achet|"
        r"utilis|essay|continu|commenc|termin|pr[ée]par|appel|demand|gagn|perd|ador|d[ée]test|invit|"
        r"cuisin|dans|nag|voyag|post|envoy|cherch|trouv|chang|ferm|aid|pleur|tap|frapp|saut)er\b",
        re.IGNORECASE), lambda m: f"{m.group(1)} {m.group(2)}é"),
    # "Bien jouer !" en message de félicitation isolé au lieu de "Bien joué !"
    (re.compile(r"^(?:<@!?\d+>\s*)*bien\s+jouer\b\s*[!.]*\s*$", re.IGNORECASE), "bien joué"),
    # "o" phonétique à la place de "au" — vraiment ça pique les yeux
    (re.compile(r"\bo\s+(revoir|secours|final|d[ée]but|bout|moins|pire|mieux|fond|milieu|sujet)\b", re.IGNORECASE),
     lambda m: f"au {m.group(1)}"),
    # Retranscriptions phonétiques du son "qu"/"k" et autres — écriture SMS abusive
    (re.compile(r"\bkoi\b", re.IGNORECASE), "quoi"),
    (re.compile(r"\bpkoi\b", re.IGNORECASE), "pourquoi"),
    (re.compile(r"\bkan\b", re.IGNORECASE), "quand"),
    (re.compile(r"\bkomen(?:t)?\b", re.IGNORECASE), "comment"),
    (re.compile(r"\beske\b", re.IGNORECASE), "est-ce que"),
    (re.compile(r"\bo\s*jourd['’]?h?ui\b", re.IGNORECASE), "aujourd'hui"),
    (re.compile(r"\baujourd(?:hui|ui)\b", re.IGNORECASE), "aujourd'hui"),
    (re.compile(r"\bdabor\b", re.IGNORECASE), "d'abord"),
    (re.compile(r"\bbiensur\b", re.IGNORECASE), "bien sûr"),
]

GRAMMAR_ROAST_LINES = [
    "💀 {mention} a écrit « {wrong} »... le niveau CE1 est en PLS.",
    "🚨 Alerte orthographe : {mention} confond encore. C'est chaud, on dit **{correct}**, pas « {wrong} ».",
    "📚 Quelqu'un peut ramener {mention} à l'école please, on a un souci là.",
    "😭 « {wrong} » au lieu de **{correct}**... {mention} c'est une agression envers la langue française.",
    "🔴 SIGNALEMENT : {mention} vient de commettre un crime contre le français avec « {wrong} ».",
    "📉 Niveau français de {mention} : en chute libre. On dit **{correct}**, pas « {wrong} ».",
    "🧠 Petit rappel gratuit pour {mention} : c'est **{correct}**, pas « {wrong} ».",
    "😂 {mention} vient de traumatiser tous les correcteurs orthographiques du serveur.",
    "🚔 La police de la grammaire débarque chez {mention} pour « {wrong} ».",
    "👴 Même ta grand-mère écrit mieux que ça, {mention}...",
    "🎓 {mention}, ton diplôme de français vient d'être révoqué. On dit **{correct}**.",
    "🩹 Y'a une faute qui saigne encore là, {mention} : **{correct}**, pas « {wrong} ».",
    "🫡 Respect aux enseignants de {mention}, ils ont fait ce qu'ils ont pu.",
    "🛂 {mention}, tes papiers viennent de t'être retirés pour cette faute horrible. Direction le CP.",
    "📛 Urgent : la nationalité française de {mention} est en cours de révocation suite à « {wrong} ».",
    "🚨 {mention} vient d'être placé en garde à vue par la Brigade Anti-Cacographie pour « {wrong} ».",
    "⚰️ Molière vient de se retourner trois fois dans sa tombe à cause de {mention}.",
    "🪪 Carte d'identité de {mention} confisquée : motif « incapable d'écrire », pièce à conviction « {wrong} ».",
    "🧾 {mention} écope d'une amende pour outrage à la langue française avec « {wrong} ».",
    "🏫 Convocation immédiate de {mention} au tribunal de l'Académie française. On dit **{correct}**.",
    "📞 On a appelé l'ambassade, {mention} est prié de rendre son passeport pour « {wrong} ».",
    "🚁 Un hélicoptère de la police linguistique est en approche vers {mention}.",
    "⚖️ {mention} risque l'expulsion du groupe WhatsApp familial pour « {wrong} ».",
    "🩺 On a diagnostiqué {mention} : dyslexie sévère à « {wrong} », pronostic vital engagé.",
]


async def _maybe_grammar_roast_reaction(message):
    """Réaction troll (cooldown 3 min par utilisateur) quand un message contient une
    faute de français emblématique. Répond au message avec une pique aléatoire."""
    global _grammar_roast_last
    content = message.content
    if not content or content.startswith('?'):
        return
    now = datetime.now()
    last = _grammar_roast_last.get(message.author.id)
    if last and (now - last).total_seconds() < GRAMMAR_ROAST_COOLDOWN_MIN * 60:
        return
    for pattern, correction in GRAMMAR_MISTAKE_PATTERNS:
        match = pattern.search(content)
        if not match:
            continue
        wrong = match.group(0).strip()
        correct = correction(match) if callable(correction) else correction
        _grammar_roast_last[message.author.id] = now
        try:
            line = random.choice(GRAMMAR_ROAST_LINES).format(
                mention=message.author.mention, wrong=wrong, correct=correct)
            await message.reply(line, mention_author=False)
        except Exception:
            pass
        return


MAX_FACTORY_WORKERS = 10
DEFAULT_FACTORY_COSTS = [500, 1000, 2000, 5000, 7500, 10000, 15000, 25000, 55000, 100000]
FACTORY_HIRE_COOLDOWN_HOURS = 24
RISQUE_COOLDOWN_HOURS = 3
GAMES_WITH_LIMITS = ['slots', 'coinflip', 'roulette', 'bj', 'duel', 'mines', 'poker', 'course', 'higherlower']

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
    'mendier':   0.5,
}

BEG_THRESHOLD = 50   # solde max pour pouvoir mendier
BEG_MIN, BEG_MAX = 20, 80

casino_config = {
    'shop_prices':   {},  # str(item_id) -> int
    'factory_costs': [],  # liste de 10 prix (override DEFAULT_FACTORY_COSTS)
    'min_bets':      {},  # str(game) -> int
    'max_bets':      {},  # str(game) -> int
    'cooldowns':     {},  # str(cmd) -> heures (override DEFAULT_COOLDOWNS_H)
    'biz_overrides': {},  # biz_key -> {'base_rate': int, 'open_cost': int, 'upgrade_cost': int}
}


def is_bot_owner(user) -> bool:
    """Vérifie si l'utilisateur est un créateur du bot (happy_gt3 ou Clément)."""
    return getattr(user, 'id', None) in BOT_OWNER_IDS


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

# Nom du fichier de données — sur Railway, monté via Volume sur /data
import shutil
DATA_FILE = os.environ.get('DATA_FILE', '/data/data.json' if os.path.isdir('/data') else 'data.json')
if not os.path.exists(DATA_FILE) and os.path.exists('/app/data.json'):
    os.makedirs(os.path.dirname(DATA_FILE) or '.', exist_ok=True)
    shutil.copy('/app/data.json', DATA_FILE)

# ── Filet de sécurité contre la perte silencieuse de données ──
# Incident du 20/07/2026 : un bug applicatif (double `bot.run()` dans le même
# process) a fait tourner une instance fantôme qui a fini par appeler
# save_data() avec un état quasi vide (bs_trophy_history reparti de zéro),
# écrasant le vrai fichier. Le seul .bak existant ne garde qu'UNE copie,
# réécrite à chaque sauvegarde — donc écrasé lui aussi avant que quiconque
# ne remarque le problème (des heures plus tard, via !evo).
# Deux filets indépendants, en plus du .bak déjà en place :
#  1. Des instantanés horodatés (au plus 1/heure) qu'on conserve plusieurs
#     jours, pour pouvoir remonter le temps même si le problème n'est
#     détecté que tard.
#  2. Un garde-fou qui refuse d'écraser DATA_FILE si le nouveau contenu est
#     radicalement plus petit que l'actuel (signe quasi certain d'un état
#     appauvri plutôt que d'une vraie purge volontaire) — dans ce cas on
#     écrit le payload suspect à côté pour inspection au lieu de l'appliquer.
DATA_BACKUP_DIR = os.path.join(os.path.dirname(DATA_FILE) or '.', 'backups')
DATA_BACKUP_RETENTION = 72          # ~72 instantanés, throttlés à 1/h -> 3 jours d'historique
DATA_BACKUP_MIN_INTERVAL = timedelta(hours=1)
DATA_SHRINK_MIN_OLD_SIZE = 5000     # en dessous, fichier encore trop jeune pour que la garde soit utile
DATA_SHRINK_RATIO = 0.6             # nouveau contenu < 60% de l'actuel -> suspect
_last_backup_snapshot_at = None
# True si data.json contient encore les champs BS migrés vers Supabase (voir
# load_data) — dans ce cas le premier save_data() post-migration rétrécit
# fortement le fichier PAR CONCEPTION (ces champs ne sont plus jamais
# réécrits), pas par perte de données : le garde-fou anti-shrink le
# bloquerait sinon indéfiniment puisqu'il compare toujours à l'ancien gros
# fichier. on_ready() force donc UNE resauvegarde pour établir la nouvelle
# taille de référence, puis ce flag ne redevient jamais vrai.
_bs_legacy_fields_pending_resave = False


def _snapshot_backup_if_due():
    """Copie DATA_FILE dans backups/ avec horodatage, au plus une fois par heure,
    et purge les instantanés au-delà de la rétention. Best-effort : ne doit
    jamais empêcher une sauvegarde normale de se terminer."""
    global _last_backup_snapshot_at
    if not os.path.exists(DATA_FILE):
        return
    now = datetime.now()
    if _last_backup_snapshot_at and now - _last_backup_snapshot_at < DATA_BACKUP_MIN_INTERVAL:
        return
    try:
        os.makedirs(DATA_BACKUP_DIR, exist_ok=True)
        snapshot_path = os.path.join(DATA_BACKUP_DIR, f"data-{now.strftime('%Y%m%dT%H%M%S')}.json")
        shutil.copy2(DATA_FILE, snapshot_path)
        _last_backup_snapshot_at = now
        snapshots = sorted(f for f in os.listdir(DATA_BACKUP_DIR) if f.startswith('data-'))
        for stale in snapshots[:-DATA_BACKUP_RETENTION]:
            try:
                os.remove(os.path.join(DATA_BACKUP_DIR, stale))
            except OSError:
                pass
    except Exception as e:
        logging.warning("Instantané de sauvegarde impossible : %s", e)


def _is_dangerous_shrink(new_payload_bytes: bytes) -> bool:
    """True si new_payload est nettement plus petit que DATA_FILE actuel —
    signe probable d'un état appauvri (bug, double instance...) plutôt
    qu'une purge volontaire légitime."""
    if not os.path.exists(DATA_FILE):
        return False
    try:
        old_size = os.path.getsize(DATA_FILE)
    except OSError:
        return False
    if old_size < DATA_SHRINK_MIN_OLD_SIZE:
        return False
    return len(new_payload_bytes) < old_size * DATA_SHRINK_RATIO


def _resolve_data_path():
    """Retourne le fichier à charger : DATA_FILE s'il contient du JSON valide,
    sinon la sauvegarde .bak la plus récente (protection contre un fichier
    tronqué/corrompu par un arrêt en plein milieu d'une écriture)."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8-sig') as f:
                json.load(f)
            return DATA_FILE
        except Exception as e:
            logging.warning("%s invalide (%s), tentative de restauration depuis .bak", DATA_FILE, e)
    bak_path = f"{DATA_FILE}.bak"
    if os.path.exists(bak_path):
        try:
            with open(bak_path, 'r', encoding='utf-8-sig') as f:
                json.load(f)
            logging.warning("Restauration réussie depuis %s.", bak_path)
            return bak_path
        except Exception:
            pass
    return DATA_FILE


# --- Fonctions de chargement et de sauvegarde des données ---
def load_data():
    global warns, mutes, silenced_users, coins, giveaway_data, daily_cooldowns, work_cooldowns, beg_cooldowns
    global crypto_prices, price_history, crypto_trends, crypto_holdings, safes, factories, jobs_data, owned_items
    global theft_cooldowns, miner_cooldowns, hacker_cooldowns, risque_cooldowns, rob_cooldowns, steal_immunity
    global shield_active, shield_cooldown, shield_break_streak
    global race_bets, race_drivers_live, race_accepting
    global teams, user_team, disabled_cmds, cmd_role_perms, tournaments, casino_banned_users, casino_cheat_enabled
    global territory_extension_daily
    global daily_streaks, ticket_purchases, birthdays, crypto_alerts, tournament_elo, ADMIN_LOG_CHANNEL_ID, locations, businesses
    global CASINO_LOG_CHANNEL_ID, LOG_MODERATION_CHANNEL_ID, LOG_GIVEAWAY_CHANNEL_ID, LOG_GENERAL_CHANNEL_ID, LOG_TICKET_CHANNEL_ID
    global TICKET_CATEGORY_ID, TICKET_CATEGORY_IDS, TICKET_CATEGORIES
    global LEAVE_LOG_CHANNEL_ID
    global bs_accounts, bs_role_config
    global FAMILY_CLUBS_PANEL_CHANNEL_ID, FAMILY_CLUBS_PANEL_MESSAGE_IDS
    global lock_overwrite_backup
    global crypto_buy_cooldowns, crypto_sell_cooldowns, crypto_hold_since, cold_wallets, theft_stats, daily_sell_volume, crypto_market_frozen
    global ranked_1v1, ranked_challenges, ranked_pending, ranked_pair_daily, ranked_reports, ranked_report_cooldowns
    global ranked_season_month, slash_global_purged, casino_season_month
    global punitions, moderation_log
    load_path = _resolve_data_path()
    if os.path.exists(load_path):
        with open(load_path, 'r', encoding='utf-8-sig') as f:
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
                beg_cooldowns    = data.get('beg_cooldowns', {})
                crypto_prices    = data.get('crypto_prices', dict(CRYPTO_BASE))
                # Clamp les prix dans la fourchette par crypto au redémarrage
                for _s, _b in CRYPTO_BASE.items():
                    if _s in crypto_prices:
                        crypto_prices[_s] = round(max(_b * CRYPTO_FLOOR[_s], min(_b * CRYPTO_CEIL[_s], crypto_prices[_s])), 2)
                price_history    = data.get('price_history', {})
                crypto_trends    = data.get('crypto_trends', {})
                crypto_holdings  = data.get('crypto_holdings', {})
                safes            = data.get('safes', {})
                factories        = data.get('factories', {})
                jobs_data        = data.get('jobs_data', {})
                owned_items      = data.get('owned_items', {})
                owned_items.setdefault('550678866839207937', {})['7'] = 1
                theft_cooldowns  = data.get('theft_cooldowns', {})
                miner_cooldowns  = data.get('miner_cooldowns', {})
                hacker_cooldowns = data.get('hacker_cooldowns', {})
                risque_cooldowns = data.get('risque_cooldowns', {})
                rob_cooldowns    = data.get('rob_cooldowns', {})
                steal_immunity   = data.get('steal_immunity', {})
                shield_active       = data.get('shield_active', {})
                shield_cooldown     = data.get('shield_cooldown', {})
                shield_break_streak = data.get('shield_break_streak', {})
                race_bets        = data.get('race_bets', {})
                race_drivers_live = data.get('race_drivers_live', [dict(d) for d in RACE_DRIVERS_BASE])
                race_accepting   = data.get('race_accepting', False)
                teams            = data.get('teams', {})
                user_team        = data.get('user_team', {})
                ts = data.get('team_state', {})
                team_state['competition_open'] = ts.get('competition_open', False)
                team_state['next_id'] = ts.get('next_id', 1)
                disabled_cmds    = set(data.get('disabled_cmds', []))
                casino_cheat_enabled = bool(data.get('casino_cheat_enabled', True))
                territory_extension_daily = data.get('territory_extension_daily', {})
                cmd_role_perms   = data.get('cmd_role_perms', {})
                casino_banned_users = set(data.get('casino_banned_users', []))
                daily_streaks    = data.get('daily_streaks', {})
                ticket_purchases = data.get('ticket_purchases', {})
                birthdays        = data.get('birthdays', {})
                crypto_alerts    = data.get('crypto_alerts', {})
                tournament_elo   = data.get('tournament_elo', {})
                tournaments      = data.get('tournaments', {})
                locations        = data.get('locations', {})
                bs_accounts      = data.get('bs_accounts', {})
                loaded_bs_roles  = data.get('bs_role_config', {})
                if isinstance(loaded_bs_roles, dict):
                    bs_role_config['trophies'] = loaded_bs_roles.get('trophies', {}) or {}
                    bs_role_config['ranked']   = loaded_bs_roles.get('ranked', {}) or {}
                businesses           = data.get('businesses', {})
                theft_stats           = data.get('theft_stats', {})
                daily_sell_volume     = data.get('daily_sell_volume', {})
                crypto_market_frozen  = data.get('crypto_market_frozen', False)
                crypto_buy_cooldowns  = data.get('crypto_buy_cooldowns', {})
                crypto_sell_cooldowns = data.get('crypto_sell_cooldowns', {})
                crypto_hold_since     = data.get('crypto_hold_since', {})
                cold_wallets         = data.get('cold_wallets', {})
                # Migration: ancien format {sym: {'qty':x,'locked_until':y}} → nouveau {sym: [batch,...]}
                for _uid, _wallet in cold_wallets.items():
                    for _sym in list(_wallet.keys()):
                        if isinstance(_wallet[_sym], dict):
                            _wallet[_sym] = [_wallet[_sym]]
                # Défaut le 11/08/2026 : salon de logs unifié, tant que !set_admin_log
                # n'a jamais été utilisé pour pointer ailleurs explicitement.
                ADMIN_LOG_CHANNEL_ID = data.get('admin_log_channel_id', 1528513026691563540)
                CASINO_LOG_CHANNEL_ID     = data.get('casino_log_channel_id')
                LOG_MODERATION_CHANNEL_ID = data.get('log_moderation_channel_id', LOG_MODERATION_CHANNEL_ID)
                LOG_GIVEAWAY_CHANNEL_ID   = data.get('log_giveaway_channel_id', LOG_GIVEAWAY_CHANNEL_ID)
                LOG_GENERAL_CHANNEL_ID    = data.get('log_general_channel_id', LOG_GENERAL_CHANNEL_ID)
                LOG_TICKET_CHANNEL_ID     = data.get('log_ticket_channel_id', LOG_TICKET_CHANNEL_ID)
                TICKET_CATEGORY_ID        = data.get('ticket_category_id', TICKET_CATEGORY_ID)
                TICKET_CATEGORY_IDS       = {k: int(v) for k, v in data.get('ticket_category_ids', {}).items()}
                if data.get('ticket_categories'):
                    TICKET_CATEGORIES = data['ticket_categories']
                LEAVE_LOG_CHANNEL_ID      = data.get('leave_log_channel_id', LEAVE_LOG_CHANNEL_ID)
                FAMILY_CLUBS_PANEL_CHANNEL_ID = data.get('family_clubs_panel_channel_id', FAMILY_CLUBS_PANEL_CHANNEL_ID)
                FAMILY_CLUBS_PANEL_MESSAGE_IDS = data.get('family_clubs_panel_message_ids', FAMILY_CLUBS_PANEL_MESSAGE_IDS)
                lock_overwrite_backup = data.get('lock_overwrite_backup', {})
                # punitions : voir incident du 21/07/2026, un redémarrage en pleine punition
                # laissait le membre bloqué dans tous les salons (les restrictions Discord
                # survivent au redémarrage, mais plus le dict en mémoire qui permet à
                # !annuler_punition de savoir qu'il faut les lever).
                punitions       = data.get('punitions', {})
                # Migration one-shot depuis l'ancien format liste plafonnée (voir _log_moderation) :
                # une liste signifie données pré-migration, à réindexer par target_id.
                _raw_modlog = data.get('moderation_log', {})
                if isinstance(_raw_modlog, list):
                    moderation_log = {}
                    for _entry in _raw_modlog:
                        moderation_log.setdefault(_entry['target_id'], []).append(_entry)
                else:
                    moderation_log = _raw_modlog
                ranked_1v1        = data.get('ranked_1v1', {})
                ranked_challenges = data.get('ranked_challenges', {})
                ranked_pending    = data.get('ranked_pending', {})
                ranked_pair_daily = data.get('ranked_pair_daily', {})
                ranked_reports    = data.get('ranked_reports', {})
                ranked_report_cooldowns = data.get('ranked_report_cooldowns', {})
                # Migration one-shot vers Supabase (voir db_bs.archive_ranked_1v1_season) —
                # ranked_1v1_history vivait avant dans data.json, même fragilité que les
                # champs BS avant leur propre migration (voir _bs_legacy_fields_pending_resave
                # un peu plus haut). Upsert idempotent : sans risque si ça tourne 2 fois
                # avant que le champ disparaisse de data.json (il n'est plus dans
                # data_to_save, donc le prochain save_data() l'efface pour de bon).
                _legacy_ranked_1v1_history = data.get('ranked_1v1_history', {})
                for _season_month, _entries in _legacy_ranked_1v1_history.items():
                    db_bs.archive_ranked_1v1_season(_season_month, _entries)
                if _legacy_ranked_1v1_history:
                    logging.warning(
                        "Migration Supabase : %d saison(s) ranked 1v1 migrées depuis data.json.",
                        len(_legacy_ranked_1v1_history),
                    )
                ranked_season_month = data.get('ranked_season_month')
                casino_season_month = data.get('casino_season_month')
                slash_global_purged = data.get('slash_global_purged', False)
                loaded_cfg = data.get('casino_config', {})
                if isinstance(loaded_cfg, dict):
                    casino_config['shop_prices']   = loaded_cfg.get('shop_prices', {}) or {}
                    casino_config['factory_costs'] = loaded_cfg.get('factory_costs', []) or []
                    casino_config['min_bets']      = loaded_cfg.get('min_bets', {}) or {}
                    casino_config['max_bets']      = loaded_cfg.get('max_bets', {}) or {}
                    casino_config['cooldowns']     = loaded_cfg.get('cooldowns', {}) or {}
                    casino_config['biz_overrides'] = loaded_cfg.get('biz_overrides', {}) or {}

                # Trace de richesse des données au chargement : en cas de futur incident,
                # ces chiffres permettent de voir IMMÉDIATEMENT dans les logs si une
                # collection est repartie anormalement bas, sans attendre qu'un joueur
                # remarque le problème des heures plus tard (cf. incident du 20/07/2026).
                logging.warning(
                    "Données chargées avec succès depuis %s — coins:%d warns:%d "
                    "(tracking BS : voir Supabase, plus persisté dans ce fichier)",
                    DATA_FILE, len(coins), len(warns),
                )

                global _bs_legacy_fields_pending_resave
                _bs_legacy_fields_pending_resave = any(
                    k in data for k in (
                        'bs_family_clubs', 'bs_trophy_history', 'bs_family_ranked_cache',
                        'bs_season_month', 'bs_season_start_date', 'bs_trophy_evolution_history',
                    )
                )

                # Migration : reset tickets (items 4 et 5) + remboursement au prix d'achat
                _ticket_migrated = False
                for _uid, _items in owned_items.items():
                    _nb4 = _items.get('4', 0)
                    _nb5 = _items.get('5', 0)
                    if _nb4 > 0 or _nb5 > 0:
                        _refund = _nb4 * 200 + _nb5 * 5000
                        coins[int(_uid)] += _refund
                        _items.pop('4', None)
                        _items.pop('5', None)
                        logging.warning("Migration tickets : %s — %d tickets + %d packs remboursés (%d coins)", _uid, _nb4, _nb5, _refund)
                        _ticket_migrated = True
                if _ticket_migrated:
                    ticket_purchases.clear()
                    save_data()

                # Migration : anciens items 3 "Bouclier Anti-Vol" (800) et 11 "Antivirus" (2000)
                # retirés du shop, leurs IDs réattribués aux nouveaux boucliers à durée — sans ce
                # nettoyage un résidu s'affichait comme un faux bouclier permanent dans !inventaire.
                _shield_migrated = False
                _OLD_SHIELD_PRICES = {'3': 800, '11': 2000}
                for _uid, _items in owned_items.items():
                    for _iid, _old_price in _OLD_SHIELD_PRICES.items():
                        _cnt = _items.get(_iid, 0)
                        if _cnt > 0:
                            _refund = _cnt * _old_price
                            coins[int(_uid)] += _refund
                            _items.pop(_iid, None)
                            logging.warning("Migration boucliers : %s — %d× item %s remboursé (%d coins)", _uid, _cnt, _iid, _refund)
                            _shield_migrated = True
                if _shield_migrated:
                    save_data()

            except json.JSONDecodeError as e:
                # exc_info=True : trace complète dans les logs, pour pouvoir identifier
                # EXACTEMENT quel champ a fait planter le chargement si ça se reproduit
                # (cf. incident du 20/07/2026, où on n'avait pas ce niveau de détail).
                logging.error("ERREUR JSON dans %s : %s — données réinitialisées", DATA_FILE, e, exc_info=True)
                warns = {}
                mutes = {}
                silenced_users = {}
                coins = defaultdict(int)
                giveaway_data = {}
            except Exception as e:
                logging.error("ERREUR chargement données : %s — données réinitialisées", e, exc_info=True)
                warns = {}
                mutes = {}
                silenced_users = {}
                coins = defaultdict(int)
                giveaway_data = {}
    else:
        logging.warning("Fichier %s non trouvé — données réinitialisées", DATA_FILE)
        warns = {}
        mutes = {}
        silenced_users = {}
        coins = defaultdict(int)
        giveaway_data = {}

def save_data(force: bool = False):
    """`force=True` contourne le garde-fou anti-shrink — réservé au
    resave unique post-migration Supabase (voir on_ready), jamais à un
    appel normal."""
    data_to_save = {
        'warns': {str(k): v for k, v in warns.items()},
        'coins': dict(coins),
    }

    mutes_for_save = {}
    for guild_id, guild_mutes in mutes.items():
        mutes_for_save[str(guild_id)] = {}
        for user_id, mute_info in guild_mutes.items():
            info_copy = mute_info.copy()
            if "end_time" in info_copy and isinstance(info_copy["end_time"], datetime):
                info_copy["end_time"] = info_copy["end_time"].isoformat()
            mutes_for_save[str(guild_id)][str(user_id)] = info_copy
    data_to_save['mutes'] = mutes_for_save

    silenced_for_save = {str(k): v for k, v in silenced_users.items()}
    data_to_save['silenced_users'] = silenced_for_save

    giveaway_for_save = {}
    for guild_id, gw_info in giveaway_data.items():
        info_copy = gw_info.copy()
        if "end_time" in info_copy and isinstance(info_copy["end_time"], datetime):
            info_copy["end_time"] = info_copy["end_time"].isoformat()
        giveaway_for_save[str(guild_id)] = info_copy
    data_to_save['giveaway_data'] = giveaway_for_save

    data_to_save['daily_cooldowns']  = daily_cooldowns
    data_to_save['work_cooldowns']   = work_cooldowns
    data_to_save['beg_cooldowns']    = beg_cooldowns
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
    data_to_save['steal_immunity']   = steal_immunity
    data_to_save['shield_active']       = shield_active
    data_to_save['shield_cooldown']     = shield_cooldown
    data_to_save['shield_break_streak'] = shield_break_streak
    data_to_save['race_bets']        = race_bets
    data_to_save['race_drivers_live'] = race_drivers_live
    data_to_save['race_accepting']   = race_accepting
    data_to_save['teams']            = teams
    data_to_save['user_team']        = user_team
    data_to_save['team_state']       = dict(team_state)
    data_to_save['disabled_cmds']    = list(disabled_cmds)
    data_to_save['casino_cheat_enabled'] = casino_cheat_enabled
    data_to_save['territory_extension_daily'] = territory_extension_daily
    data_to_save['cmd_role_perms']   = cmd_role_perms
    data_to_save['casino_banned_users'] = list(casino_banned_users)
    data_to_save['casino_config']    = casino_config
    data_to_save['daily_streaks']    = daily_streaks
    data_to_save['ticket_purchases'] = ticket_purchases
    data_to_save['birthdays']        = birthdays
    data_to_save['crypto_alerts']    = crypto_alerts
    data_to_save['tournament_elo']   = tournament_elo
    data_to_save['tournaments']      = tournaments
    data_to_save['locations']        = locations
    data_to_save['bs_accounts']      = bs_accounts
    data_to_save['bs_role_config']   = bs_role_config
    data_to_save['businesses']           = businesses
    data_to_save['theft_stats']           = theft_stats
    data_to_save['daily_sell_volume']     = daily_sell_volume
    data_to_save['crypto_market_frozen']  = crypto_market_frozen
    data_to_save['crypto_buy_cooldowns']  = crypto_buy_cooldowns
    data_to_save['crypto_sell_cooldowns'] = crypto_sell_cooldowns
    data_to_save['crypto_hold_since']     = crypto_hold_since
    data_to_save['cold_wallets']         = cold_wallets
    data_to_save['admin_log_channel_id'] = ADMIN_LOG_CHANNEL_ID
    data_to_save['casino_log_channel_id'] = CASINO_LOG_CHANNEL_ID
    data_to_save['log_moderation_channel_id'] = LOG_MODERATION_CHANNEL_ID
    data_to_save['log_giveaway_channel_id']   = LOG_GIVEAWAY_CHANNEL_ID
    data_to_save['log_general_channel_id']    = LOG_GENERAL_CHANNEL_ID
    data_to_save['log_ticket_channel_id']     = LOG_TICKET_CHANNEL_ID
    data_to_save['ticket_category_id']        = TICKET_CATEGORY_ID
    data_to_save['ticket_category_ids']       = TICKET_CATEGORY_IDS
    data_to_save['ticket_categories']         = TICKET_CATEGORIES
    data_to_save['leave_log_channel_id']      = LEAVE_LOG_CHANNEL_ID
    data_to_save['family_clubs_panel_channel_id']  = FAMILY_CLUBS_PANEL_CHANNEL_ID
    data_to_save['family_clubs_panel_message_ids'] = FAMILY_CLUBS_PANEL_MESSAGE_IDS
    data_to_save['lock_overwrite_backup'] = lock_overwrite_backup
    data_to_save['punitions']       = punitions
    data_to_save['moderation_log']  = moderation_log
    data_to_save['ranked_1v1']        = ranked_1v1
    data_to_save['ranked_challenges'] = ranked_challenges
    data_to_save['ranked_pending']    = ranked_pending
    data_to_save['ranked_pair_daily'] = ranked_pair_daily
    data_to_save['ranked_reports']    = ranked_reports
    data_to_save['ranked_report_cooldowns'] = ranked_report_cooldowns
    data_to_save['ranked_season_month'] = ranked_season_month
    data_to_save['casino_season_month'] = casino_season_month
    data_to_save['slash_global_purged'] = slash_global_purged

    try:
        payload_bytes = json.dumps(data_to_save, indent=4, ensure_ascii=False).encode('utf-8')

        if not force and _is_dangerous_shrink(payload_bytes):
            # Refuse d'écraser un fichier riche par un état radicalement plus petit
            # (voir incident du 20/07/2026) — on garde DATA_FILE tel quel et on écrit
            # le payload suspect à côté pour inspection manuelle, au lieu de propager
            # silencieusement la perte de données.
            os.makedirs(DATA_BACKUP_DIR, exist_ok=True)
            rejected_path = os.path.join(
                DATA_BACKUP_DIR, f"REJECTED-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
            )
            try:
                with open(rejected_path, 'wb') as f:
                    f.write(payload_bytes)
            except Exception:
                pass
            logging.critical(
                "save_data() BLOQUÉ : nouveau contenu (%d octets) très inférieur à %s (%d octets) — "
                "payload suspect conservé dans %s pour inspection manuelle. Aucune écriture effectuée.",
                len(payload_bytes), DATA_FILE, os.path.getsize(DATA_FILE), rejected_path,
            )
            return

        # Instantané horodaté (best-effort, throttlé) AVANT d'écraser quoi que ce soit —
        # garde plusieurs jours d'historique, contrairement au .bak unique ci-dessous qui
        # est lui-même réécrit à chaque sauvegarde.
        _snapshot_backup_if_due()

        # Écriture atomique : on écrit dans un fichier temporaire puis on remplace l'ancien
        # d'un coup (os.replace est atomique). Sans ça, un crash/redémarrage pendant
        # l'écriture directe de DATA_FILE peut le laisser tronqué/invalide, et le prochain
        # démarrage réinitialiserait alors TOUTES les données (coins, ranked_1v1, etc.).
        tmp_path = f"{DATA_FILE}.tmp"
        with open(tmp_path, 'wb') as f:
            f.write(payload_bytes)
        # Garde une copie du dernier état connu-bon AVANT de le remplacer, pour pouvoir
        # restaurer automatiquement si jamais DATA_FILE finit corrompu malgré l'écriture atomique.
        if os.path.exists(DATA_FILE):
            try:
                shutil.copy2(DATA_FILE, f"{DATA_FILE}.bak")
            except Exception:
                pass
        os.replace(tmp_path, DATA_FILE)
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
    logging.warning("Connecté en tant que %s — DATA_FILE=%s — fichier_existe=%s", bot.user, DATA_FILE, os.path.exists(DATA_FILE))
    start_backup_scheduler()

    # Vues de tickets en tout premier (avant load_data/re-registration des
    # clans, plus lents) : minimise la fenêtre où un clic sur un vieux salon
    # de ticket échouerait faute de vue enregistrée après un redémarrage.
    bot.add_view(TicketPanelView())
    for row in db_bs.list_open_tickets():
        bot.add_view(TicketControlView(row["id"]))
    bot.add_view(AbsencePanelView())
    bot.add_view(BsTagOnboardingView())

    load_data()

    if _bs_legacy_fields_pending_resave:
        # data.json contient encore les champs BS migrés vers Supabase — le premier
        # save_data() va donc être bien plus petit QUE PAR CONCEPTION (voir le
        # commentaire sur _bs_legacy_fields_pending_resave). On force ce resave unique
        # pour établir la nouvelle taille de référence, sinon le garde-fou anti-shrink
        # bloquerait indéfiniment TOUTES les sauvegardes (coins, warns, etc. inclus),
        # pas seulement le tracking BS.
        save_data(force=True)
        logging.warning("Migration Supabase : data.json re-sauvegardé sans les champs BS legacy.")

    # Les commandes dynamiques par clan (!projetx, etc.) ne survivent pas à un
    # redémarrage, donc on les réenregistre systématiquement ici. bs_family_clubs
    # vit maintenant dans Supabase (voir db_bs.py), plus dans data.json.
    for entry in db_bs.list_family_clubs():
        _bs_register_club_command(entry, fail_on_economy_conflict=True)

    # Re-enregistre les views de tournoi pour que les boutons fonctionnent après restart
    _registered_join_ids = set()
    for gid, t in tournaments.items():
        ts = t.get('team_size', 1)
        board_id = t.get('board_message_id')
        if t.get('status') == 'registering':
            view = TournamentJoinView(gid, ts)
            if board_id:
                bot.add_view(view, message_id=board_id)
            elif gid not in _registered_join_ids:
                bot.add_view(view)
                _registered_join_ids.add(gid)
        elif t.get('status') == 'active' and t.get('rounds'):
            cur = t.get('current_round', 0)
            if cur < len(t['rounds']):
                for m in t['rounds'][cur]:
                    if m['p2'] is not None and m['winner'] is None:
                        p1 = _t_participant(t, m['p1'])
                        p2 = _t_participant(t, m['p2'])
                        if p1 and p2:
                            bot.add_view(MatchView(gid, m['match_id'], p1['name'], p2['name'],
                                                   p1['captain'], p2['captain'], m['p1'], m['p2']))
    # Relance les giveaways encore en cours (les tâches asyncio ne survivent
    # pas à un redémarrage ; les données, elles, sont persistées dans data.json).
    _resume_giveaways()

    if not check_mutes.is_running():
        check_mutes.start()
    if not update_crypto_prices.is_running():
        update_crypto_prices.start()
    if not check_birthdays.is_running():
        check_birthdays.start()
    if not sync_bs_roles.is_running():
        sync_bs_roles.start()
    if not sync_family_ranked.is_running():
        sync_family_ranked.start()
    if not sync_trophy_history.is_running():
        sync_trophy_history.start()
    if not check_ranked_season.is_running():
        check_ranked_season.start()
    if not check_casino_season.is_running():
        check_casino_season.start()
    if not check_bs_season.is_running():
        check_bs_season.start()
    if not sync_discord_members.is_running():
        sync_discord_members.start()
    if not sync_absence_roles.is_running():
        sync_absence_roles.start()
    if not remind_bs_tag_missing.is_running():
        remind_bs_tag_missing.start()
    if not refresh_family_clubs_panel_task.is_running():
        refresh_family_clubs_panel_task.start()

    global _slash_synced, slash_global_purged
    if not _slash_synced:
        try:
            # copy_global_to lit la liste globale EN MÉMOIRE : il faut donc synchroniser
            # les serveurs d'abord, et ne purger le registre global (distant) qu'ensuite —
            # clear_commands(guild=None) vide aussi cette liste en mémoire, donc le faire
            # avant la boucle ferait copier une liste déjà vide (0 commande partout).
            for guild in bot.guilds:
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                logging.warning("Slash commands synchronisées sur %s : %d", guild.name, len(synced))
            # Purge les commandes globales distantes une seule fois pour de bon (persisté) :
            # un ancien déploiement les avait enregistrées en plus des commandes par serveur,
            # ce qui créait des doublons. On ne refait PAS ça à chaque démarrage, sinon la
            # liste globale en mémoire resterait vide pour le reste du process et casserait
            # la synchro d'un nouveau serveur (on_guild_join) ou de !bs_famille ajouter.
            if not slash_global_purged:
                bot.tree.clear_commands(guild=None)
                await bot.tree.sync()
                slash_global_purged = True
                save_data()
            _slash_synced = True
        except Exception as e:
            logging.warning("Erreur de synchronisation des slash commands : %s", e)

    logging.warning("Bot prêt et fonctionnel !")


# ── Liste des commandes toujours autorisées (anti-bricking) ──────────────
# !permission retiré le 09/08/2026 : c'est justement l'outil qui accorde des
# accès, donc elle doit suivre la même règle "propriétaire par défaut" que
# le reste (voir ADMIN_LOCKED_CMDS ci-dessous) plutôt que rester ouverte à
# n'importe quel admin Discord. !gestion reste ici : c'est le seul filet de
# sécurité qui permet de réactiver une commande désactivée par erreur (dont
# !permission elle-même), il doit rester accessible à tout admin sous peine
# de bricker le bot si jamais gestion se retrouvait lui-même bloqué.
ALWAYS_ALLOWED_CMDS = {'gestion', 'cooldown', 'cd', 'aide'}

# ── Commandes sensibles : réservées au propriétaire du serveur par défaut,
# sauf rôle explicitement autorisé via !perm (décision du 09/08/2026 —
# remplace l'ancien "tout admin Discord passe") ──
ADMIN_LOCKED_CMDS = {
    'giveaway', 'cancelgiveaway', 'listgiveaways', 'gdt', 'prix_casino', 'ouvrir_course', 'lancer_course',
    'freeze_crypto', 'addcoins', 'removecoins', 'tournois', 'prix_tournoi',
    'ouverture_tournoi', 'annuler_tournoi', 'tournoi_retirer', 'tournoi_ajouter', 'tournoi_deplacer',
    'punition', 'annuler_punition', 'set_admin_log', 'set_logs',
    'ranked_sanction', 'signalements', 'ranked_ajuster', 'ranked_set', 'reset_casino', 'reset_duels', 'ranked_liberer',
    'casino_ban', 'casino_unban', 'casino_pause', 'casino_resume', 'ticket_panel', 'set_ticket', 'permission',
}

# ── Anti-macro casino : incident du 23/07/2026 (martingale rouge/noir via
# macro, tellement rapide que Discord n'affichait plus les messages côté
# client mais traitait quand même chaque commande côté bot). Toute commande
# qui touche `coins` passe par un cooldown court ici, plus une pause/ban
# globaux gérables par un admin (!casino_pause, !casino_ban). ──────────────
CASINO_CMDS = {
    'daily', 'travail', 'work', 'mendier', 'beg', 'risque', 'roulette_russe', 'give',
    'roulette', 'slots', 'machine', 'bj', 'blackjack', 'coinflip', 'cf', 'duel', 'pvp',
    'poker', 'pk', 'mines', 'higherlower', 'hl', 'voler', 'steal', 'rob',
    'gratter', 'scratch', 'course', 'parier', 'hacker', 'hack', 'miner',
}
CASINO_COOLDOWN_SECONDS = 3
# uid (int) -> datetime du dernier coup casino — anti-macro uniquement,
# pas besoin de survivre à un redémarrage (contrairement à daily_cooldowns
# et cie, qui représentent une vraie limite à conserver).
_casino_last_use: dict[int, "datetime"] = {}


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
    # Casino : pause volontaire (!casino_pause), avant un déploiement par ex.
    # — s'applique même aux admins, sinon ça perd son intérêt.
    if cmd_name in CASINO_CMDS and casino_paused:
        try:
            await ctx.send("⏸️ Le casino est temporairement **en pause**. Réessaie dans quelques minutes.")
        except Exception:
            pass
        return False
    # Casino : compte banni (!casino_ban) — bloqué même s'il est admin.
    if cmd_name in CASINO_CMDS and ctx.author.id in casino_banned_users:
        try:
            await ctx.send("🚫 Tu n'as plus accès aux commandes casino.")
        except Exception:
            pass
        return False
    # Casino : cooldown court anti-macro (voir incident du 23/07/2026) —
    # appliqué même aux admins pour rester cohérent avec les deux checks
    # ci-dessus, mais sans bloquer un admin qui teste une commande une fois.
    if cmd_name in CASINO_CMDS:
        now = datetime.now()
        last = _casino_last_use.get(ctx.author.id)
        if last and (now - last).total_seconds() < CASINO_COOLDOWN_SECONDS:
            try:
                await ctx.send("⏳ Trop rapide, souffle un peu avant le prochain coup.", delete_after=5)
            except Exception:
                pass
            return False
        _casino_last_use[ctx.author.id] = now
    # Les admins du serveur passent toujours, SAUF pour les commandes admin
    # sensibles (ADMIN_LOCKED_CMDS) : celles-ci sont réservées au propriétaire
    # du serveur par défaut — ctx.guild.owner_id, calculé dynamiquement,
    # jamais un ID codé en dur — jusqu'à ce qu'un rôle soit explicitement
    # autorisé via !permission. Décision du 09/08/2026 (remplace l'ancien
    # PUNITION_ALLOWED_USER_IDS codé en dur, généralisée à toute commande
    # ADMIN_LOCKED_CMDS plutôt que de garder un mécanisme à part pour !punition).
    if ctx.guild and ctx.author.guild_permissions.administrator:
        if cmd_name not in ADMIN_LOCKED_CMDS or ctx.author.id == ctx.guild.owner_id:
            return True
    # Restrictions de rôle (!permission)
    allowed_roles = cmd_role_perms.get(cmd_name)
    if allowed_roles and ctx.guild:
        user_role_ids = {r.id for r in ctx.author.roles}
        if user_role_ids & set(allowed_roles):
            return True
    # Commande sensible sans rôle explicitement autorisé : réservée au propriétaire du serveur
    if cmd_name in ADMIN_LOCKED_CMDS and not (allowed_roles and ctx.guild):
        try:
            await ctx.send(f"❌ La commande `!{cmd_name}` est réservée au propriétaire du serveur (ou à un rôle autorisé via `!perm`).")
        except Exception:
            pass
        return False
    if allowed_roles and ctx.guild:
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
    'give':          '`!give @membre <montant|all>`\nEx : `!give @Ami 1000` · `!give @Ami all`',
    'roulette':      '`!roulette <mise|all> <choix>`\nChoix : `rouge` `noir` `pair` `impair` `manque` `passe` `1-12` `13-24` `25-36` `voisins` `tiers` `orphelins` ou un numéro `0-36`\nEx : `!roulette 200 rouge` · `!roulette all 15` · plusieurs paris : `!roulette 100 rouge 50 17`',
    'slots':         '`!slots <mise|all>`\nEx : `!slots 150` · `!slots all`',
    'bj':            '`!bj <mise|all>` — Démarrer une partie (jouez ensuite avec les boutons)\nEx : `!bj 100` · `!bj all`',
    'blackjack':     '`!bj <mise|all>` — Démarrer une partie (boutons : Tirer / Rester / Doubler / Abandonner)',
    'coinflip':      '`!coinflip <mise|all> <pile|face>`\nEx : `!coinflip 100 pile` · `!cf all face`',
    'cf':            '`!coinflip <mise|all> <pile|face>`\nEx : `!cf 500 pile`',
    'duel':          '`!duel @membre <mise|all>`\nEx : `!duel @Joueur 500`',
    'mines':         '`!mines <mise|all>`\nEx : `!mines 300` · `!mines all`',
    'higherlower':   '`!higherlower <mise>` (`!hl`) — Plus haut/bas/égal, multiplicateur croissant',
    'poker':         '`!poker start <ante>` — Créer une table\n*(Tout le reste se joue avec les boutons : Rejoindre / Démarrer / Fold / Call / Check / Raise / All-in / Voir mes cartes)*',
    'bslink':        '`!bslink <tag>`\nEx : `!bslink #2ABC123`',
    'lierbs':        '`!bslink <tag>` (alias `!lierbs`)\nEx : `!lierbs #2ABC123`',
    'bsprofil':      '`!bsprofil [@membre]`\nEx : `!bsprofil` · `!bs @Joueur`',
    'bs':            '`!bsprofil [@membre]` (alias `!bs`)\nEx : `!bs @Joueur`',
    'bs_roles':      '`!bs_roles trophees <min> @role` · `!bs_roles ranked <min_points> @role` · `!bs_roles liste` · `!bs_roles_panel` (version panel) *(Admin)*',
    'bs_famille':    '`!bs_famille ajouter <tag_clan>` · `!bs_famille retirer <tag_clan>` · `!bs_famille liste` *(Admin)*',
    'classement_trophees_famille': '`!classement_trophees_famille` (alias `!ctf`, `!top_famille`)',
    'evolution_trophees': '`!evolution_trophees` (alias `!evo`, `!evolution`) — Progression de trophées de la saison BS en cours, sélecteur de saisons passées et filtre par clan',
    'classement_ranked_famille':   '`!classement_ranked_famille` (alias `!crf`, `!top_ranked_famille`)',
    'famille_stats': '`!famille_stats` (alias `!fs`, `!stats_famille`) — Vue d\'ensemble de la famille',
    'graphique':     '`!graphique <SYM>`\nSymboles disponibles : `BTC` `ETH` `DOGE` `SOL` `XRP`\nEx : `!graphique BTC`',
    'chart':         '`!graphique <SYM>` — Ex : `!graphique ETH`',
    'courbe':        '`!graphique <SYM>` — Ex : `!graphique DOGE`',
    'acheter_crypto':'`!acheter_crypto <SYM> <coins|all>`\nEx : `!acheter_crypto BTC 1000` · `!acheter_crypto BTC all`',
    'vendre_crypto': '`!vendre_crypto <SYM> <quantité|all>`\nEx : `!vendre_crypto ETH 0.5` · `!vendre_crypto BTC all`',
    'choisir_metier':'`!choisir_metier <metier>`\nMétiers : `hacker` `mineur` `escroc` `gardien` `trader`',
    'hacker':        '`!hacker @membre` — Voler la crypto d\'un joueur\n*(Réservé au métier Hacker)*',
    'voler':         '`!voler @membre` — Voler le coffre d\'un joueur (5-20% du coffre)\nLa victime gagne une **immunité de 6h** après un vol réussi\nEx : `!voler @Riche`',
    'rob':           '`!rob @membre` — Voler le cash d\'un joueur (55% réussite, 5-15% du cash · -0 à 300 si raté)\nEscroc : +20% sur le montant volé · Cooldown 12h',
    'coffre':        '`!coffre` — Ouvrir le coffre (boutons Déposer / Retirer)',
    'team':          '`!team` — Système de clubs (créer / rejoindre / quitter / trésorerie)',
    'gdt':           '`!gdt` *(Admin)* — Gérer la compétition inter-clubs (ouvrir/fermer/récompenser)',
    'gestion':       '`!gestion` *(Owner/Admin)* — Activer/désactiver n\'importe quelle commande',
    'permission':    '`!permission` *(Owner)* — Restreindre une commande à certains rôles Discord',
    'cooldown':      '`!cd_set` ou `!cooldown_set` *(Owner/Admin)* — Modifier les cooldowns des commandes',
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
    'giverole':      '`!giverole @membre <@role>`\nEx : `!giverole @Joueur @VIP`',
    'sanctions':     '`!sanctions @membre`',
    'historique_moderation': '`!historique_moderation [@membre]`',
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
                 "`!aide` — Ce menu\n"
                 "`!profil` (`!profile`, `!stats`) — Voir sa fiche complète\n"
                 "`!cd` (`!cooldown`) — Voir tous tes cooldowns en cours *(privé)*\n"
                 "`!anniversaire JJ/MM` (`!anniv`) — Enregistrer son anniversaire\n"
                 "`!stats_serveur` (`!serveur`) — Vue globale du serveur\n"
                 "`!snipe [nb] [@membre]` — Voir le(s) dernier(s) message(s) supprimé(s) du salon\n"
                 "`!help_staff` — Ta fiche staff (mission + commandes) si tu as un rôle staff\n"
                 "`!help_fonda` `!help_admin` `!help_modo` `!help_recruteur` `!help_president` "
                 "`!help_vicepre` `!help_conseiller` — Fiche d'un rôle staff en particulier"))
    cats.append(("eco", "🪙 Économie de base",
                 "Solde, daily, travail, coffre, rob, etc.",
                 "`!coins` (`!bal`, `!solde`) — Voir votre solde\n"
                 "`!daily` (`!d`) — 500 coins/jour + bonus streak\n"
                 "`!travail` (`!trav`, `!work`) — Travailler (cooldown 1h)\n"
                 "`!mendier` (`!beg`) — Filet de sécurité si solde ≤ 50 et daily/travail épuisés\n"
                 "`!risque` (`!risk`) — Coup risqué x2 ou rien *(cooldown 3h)*\n"
                 "`!give @membre <montant|all>` — Donner des coins\n"
                 "`!coffre` (`!banque`, `!vault`) — Coffre-fort (Déposer/Retirer)\n"
                 "`!rob @membre` — Voler le cash *(cooldown 12h)*\n"
                 "`!classement` (`!top`, `!lb`) — Top 10 des plus riches"))
    cats.append(("casino", "🎰 Jeux de casino",
                 "Slots, blackjack, roulette, poker, course, gratter…",
                 "`!slots <mise>` (`!sl`, `!machine`) — Machine à sous\n"
                 "`!coinflip <mise> <pile|face>` (`!cf`) — Pile ou face\n"
                 "`!roulette <mise> <rouge|noir|pair|impair|voisins|tiers|orphelins|0-36>` (`!rou`) — plusieurs paris possibles en une commande\n"
                 "`!bj <mise>` (`!blackjack`) — Blackjack (boutons)\n"
                 "`!duel @membre <mise>` (`!pvp`) — Duel\n"
                 "`!mines <mise>` (`!mn`) — Mines\n"
                 "`!poker start <ante>` (`!pk`) — Poker (boutons)\n"
                 "`!course` (`!race`) — Course de voitures\n"
                 "`!parier <pilote> <mise>` (`!bet`) — Parier sur une course\n"
                 "`!gratter` (`!scratch`) — Gratter un ticket (5 cases 🍀)\n"
                 "`!higherlower <mise>` (`!hl`) — Plus haut/bas/égal, multiplicateur croissant\n"))
    cats.append(("crypto", "📈 Crypto-monnaies",
                 "Marché simulé · 5 cryptos · Prix actualisés toutes les 90s",
                 "`!crypto` (`!cr`) — Prix actuels + portefeuille\n"
                 "`!graphique <SYM>` (`!chart`) — Courbe + variations récentes\n"
                 "`!acheter_crypto <SYM> <coins|all>` (`!buyc`) — Acheter\n"
                 "`!vendre_crypto <SYM> <qté|all>` (`!vc`) — Vendre\n"
                 "`!alerte <SYM> >prix` — Alerte hausse · `!alerte <SYM> <prix` — Alerte baisse\n"
                 "`!suppr_alerte [SYM]` — Supprimer alertes\n"
                 "`!top_crypto` — Top portefeuilles\n"
                 "`!coldwallet` (`!cwallet`) — Cold wallet sécurisé (non-hackable)\n"
                 "`!coldwallet <qté|all> <SYM>` — Déposer (lock 12h par batch)\n"
                 "`!coldwallet retirer <qté|all> <SYM>` — Retirer les batches débloqués\n"
                 "\n**⚙️ Règles :** ⏳ CD 30min/achat · 🔒 Hold 10min avant vente · 📊 Slippage sur ventes >5k coins (max −10%) · 💼 Trader/Cours Trading = +15%\n"
                 "**📡 Symboles :** `BTC` `ETH` `SOL` `XRP` `DOGE`"))
    cats.append(("metiers", "💼 Métiers & Actions",
                 "Hacker, mineur, escroc, gardien, trader",
                 "`!metier` (`!job`, `!emploi`) — Voir métier actuel\n"
                 "`!choisir_metier <nom>` (`!cm`, `!set_job`) — Choisir un métier\n"
                 "`!miner` — Miner *(Mineur · cooldown 15min)*\n"
                 "`!hacker @membre` (`!hack`) — Voler crypto *(Hacker · cooldown 1h)*\n"
                 "`!voler @membre` (`!steal`) — Voler le **coffre** *(cooldown 30min)*"))
    cats.append(("usine", "🏭 Usine passive",
                 "Production automatique de coins",
                 f"`!usine` (`!factory`) — Voir votre usine\n"
                 f"`!embaucher` (`!hire`) — Embaucher un employé *(cooldown {FACTORY_HIRE_COOLDOWN_HOURS}h)*\n"
                 f"`!collecter` (`!collect`) — Collecter la production\n"
                 f"Max **{MAX_FACTORY_WORKERS} employés**"))
    cats.append(("commerces", "🏪 Commerces",
                 "Chaîne de business à débloquer progressivement",
                 "`!epicerie` — Épicerie 🏪 *(débloque après usine 10/10 + améliorée)*\n"
                 "`!fastfood` — Fast Food 🍔 *(débloque après épicerie 8/8 + améliorée)*\n"
                 "`!restaurant` (`!resto`) — Restaurant 🍽️ *(débloque après fast food 10/10 + amélioré)*\n"
                 "Chaque commerce : embaucher, collecter, améliorer — via boutons\n"
                 "Le restaurant a un système de ⭐ **Réputation** : collecte toutes les 12h–24h = +1 prog, 4 progs = +⭐, oublier 24h = retour à 0⭐\n"
                 "Achetez les commerces via `!shop` (items 8, 9, 10)"))
    cats.append(("shop", "🛒 Magasin & Tickets",
                 "Items et inventaire",
                 "`!shop` (`!boutique`) — Magasin (boutons d'achat)\n"
                 "`!acheter <item_id>` (`!buy`) — Acheter un item\n"
                 "`!inventaire` (`!inv`) — Voir vos items\n"
                 "\n**Items disponibles :**\n"
                 "1. 🍀 Porte-bonheur — Daily = 650 coins\n"
                 "2. ⚒️ Équipement Pro — Travail : 50–400 coins\n"
                 "4/5. 🎟️ Ticket à gratter\n"
                 "6. 🏭 Amélioration Usine — +15% production\n"
                 "7. 📈 Cours de Trading — +15% gains ventes crypto\n"
                 "8/9/10. Commerces (Épicerie / Fast Food / Restaurant)\n"
                 "3/11/12/13. 🛡️ Boucliers (12h/24h/72h/7j) — ou directement via `!bouclier <durée>`\n"
                 "\n**Protection :** boucliers — protection totale contre `!voler`/`!rob`/`!hacker` "
                 "pendant la durée choisie ; se brise si **tu attaques quelqu'un** pendant qu'il est actif "
                 "(le cooldown de rachat dépend du palier cassé — voir `!bouclier`)."))
    cats.append(("team", "👥 Clubs / Teams",
                 "Créer ou rejoindre un club de joueurs",
                 "`!team` (`!club`, `!guilde`) — Interface du club\n"
                 "`!gdt` *(Admin)* — Compétitions inter-clubs"))
    cats.append(("duel1v1", "⚔️ Ranked 1v1",
                 "Défis 1v1 internes au serveur, classement par saison",
                 "`!1v1` — Lance un défi ouvert (premier arrivé, premier servi)\n"
                 "`!1v1 @membre` — Défie un membre précis\n"
                 "`!1v1` (une fois le duel accepté) — Déclare le résultat (vote à 2)\n"
                 "`!classement_1v1` (`!top_1v1`) — Classement, avec sélecteur de saisons passées\n"
                 "`!signaler @membre <raison>` — Signaler un comportement pas fairplay au staff"))
    cats.append(("tournoi", "🏆 Tournois & Draft",
                 "Tournois, ELO et phase de ban Brawl Stars",
                 "`!tournois solo` · `2v2` · `3v3` · `4v4` · `5v5` *(Admin)*\n"
                 "`!tournoi_status` (`!t_status`) — Voir le bracket\n"
                 "`!classement_tournoi` (`!elo`) — Classement ELO\n"
                 "Résultat : **les 2 capitaines** valident le même vainqueur\n"
                 "*(Admin)* `!win <n°>` (`!victoire`) — Trancher un match\n"
                 "*(Admin)* `!prix_tournoi <montant>` — Définir la récompense\n"
                 "*(Admin)* `!tournoi_ajouter @membre [équipe]` — Ajouter un joueur\n"
                 "*(Admin)* `!tournoi_retirer @membre` — Retirer un joueur\n"
                 "`!draft <1v1|2v2|3v3|4v4|5v5> @cap2` — Phase de ban Brawl Stars"))
    cats.append(("brawlstars", "🎮 Profil Brawl Stars",
                 "Lie ton compte en jeu pour un rôle auto selon tes trophées/rang classé",
                 "`!bslink <tag>` (`!lierbs`) — Lier ton compte Brawl Stars\n"
                 "`!bsprofil [@membre]` (`!bs`) — Voir/rafraîchir trophées et rang classé\n"
                 "*(Admin)* `!bs_roles trophees <min> @role` — Palier de trophées → rôle\n"
                 "*(Admin)* `!bs_roles ranked <min_points> @role` — Palier de points classé → rôle\n"
                 "*(Admin)* `!bs_roles liste` — Voir la configuration\n"
                 "*(Admin)* `!bs_roles_panel` — Même chose via un panel interactif (menus + sélection de rôle)\n"
                 "*(Staff)* `!bs_stats_liaison` (`!bs_lies`) — Combien de membres ont lié leur compte\n"
                 "*(Staff)* `!relancer_tag_bs` (`!bs_tag_relance`) — Forcer l'envoi immédiat du rappel MP aux membres sans tag lié\n"
                 "`!classement_trophees_famille` (`!ctf`) — Classement trophées de la famille de clans\n"
                 "`!evolution_trophees` (`!evo`) — Progression de trophées depuis le début de la saison BS en cours (+ historique des saisons passées, par membre/clan)\n"
                 "`!classement_ranked_famille` (`!crf`) — Classement classé de la famille (mis à jour ttes les 4h)\n"
                 "`!famille_stats` (`!fs`) — Vue d'ensemble : membres, trophées, répartition par clan/rang\n"
                 "*(Admin)* `!bs_famille ajouter/retirer <tag_clan>` — Gérer les clans de la famille\n"
                 "*(Admin)* `!bs_famille_panel` — Même chose via un panel (select pour retirer + modal pour ajouter)\n"
                 "*(Admin)* `!clubs_panel` (`!maj_clubs`) — Poste/rafraîchit le panel des clubs de la famille dans le salon courant "
                 "(roster + trophées par clan, auto-actualisé toutes les 24h, remplace un screenshot posté à la main)\n"
                 "`!recrutement <clan>` (`!pitch_club`) — Fiche de recrutement à jour pour un clan (self-service, pas besoin d'attendre un staff)\n"
                 "Chaque clan ajouté obtient aussi sa propre commande (ex : `!projetx`) — voir `!bs_famille liste`"))
    cats.append(("tickets", "🎫 Tickets",
                 "Contacter le staff (candidature, recrutement club, incident, autre)",
                 "Utilise le panel posté dans le salon dédié : choisis un motif, un salon privé "
                 "est créé automatiquement, avec le staff concerné mentionné dedans (le rôle "
                 "mentionné dépend du motif — ex. incident → Staff Discord uniquement)\n"
                 "*(Admin)* `!ticket_panel` — Poste le panel d'ouverture dans le salon courant\n"
                 "*(Admin)* `!set_ticket` — Configure les motifs proposés et leur catégorie Discord de création\n"
                 "*(Staff)* `!fermer_ticket` (`!close_ticket`) — Ferme le ticket du salon courant "
                 "(délai optionnel), ou `!fermer_ticket #salon|<id>|@membre [raison]` depuis n'importe où"))
    cats.append(("absences", "🌴 Absences",
                 "Déclarer, consulter et gérer les absences",
                 "`!absence_panel` — Poste le panel de déclaration dans le salon courant "
                 "(club → 🟡 partielle/🔴 totale → formulaire)\n"
                 "`!absences` *(Staff)* — Panel interactif : filtrer par club, trier, "
                 "modifier/supprimer une absence via les menus (pas de saisie manuelle de club)\n"
                 "`!absence_ajouter @membre` *(Staff)* — Déclarer une absence pour quelqu'un d'autre\n"
                 "`!absence_modifier <id>` — Modifier une absence par son numéro (l'auteur ou le staff)\n"
                 "`!supprimer_absence <id>` — Supprimer une absence par son numéro (l'auteur ou le staff)"))

    if has_manage_messages or has_ban_members:
        lines = []
        if has_manage_messages:
            lines.append("`!warn` `!mute` `!unmute` `!clear` `!silence` `!unsilence` `!sanctions`")
            lines.append("`!historique_moderation [@membre]` (`!modlog`) — Détail chronologique des sanctions (raison, modérateur, date)")
            lines.append("`!punition <nb> @membre` (`!pun`) — Punition morse")
            lines.append("`!annuler_punition @membre` (`!apun`) — Annuler punition")
        if has_ban_members:
            lines.append("`!ban` `!unban`")
        cats.append(("mod", "⚖️ Modération",
                     "Warns, mutes, bans, punitions…",
                     "\n".join(lines)))

    if has_manage_nicknames:
        cats.append(("nick", "✏️ Gestion des pseudos",
                     "Renommer un membre",
                     "`!rename @membre <nouveau pseudo>`"))

    if has_admin:
        cats.append(("admin", "⚙️ Administration",
                     "Outils admin du serveur",
                     "`!giveaway` `!cancelgiveaway`\n"
                     "`!addcoins @membre <n> [cash|coffre]` (`!addc`) — Ajouter des coins (cash par défaut)\n"
                     "`!removecoins @membre <n> [cash|coffre]` (`!rmc`) — Retirer des coins\n"
                     "`!prix_casino` (`!prixcasino`) — Prix shop/usine + mises min/max\n"
                     "`!gestion` (`!gest`, `!admin`) — Activer/désactiver des commandes\n"
                     "`!permission` (`!perm`) — Restreindre/déléguer des commandes par rôle\n"
                     "`!cd_set` (`!cooldown_set`) — Modifier les cooldowns\n"
                     "`!freeze_crypto` — Geler/dégeler le marché crypto\n"
                     "`!ouvrir_course` (`!oc`) / `!lancer_course` (`!lc`) — Courses\n"
                     "`!ouverture_tournoi` (`!bracket`) — Lancer le tournoi\n"
                     "`!annuler_tournoi` — Annuler le tournoi en cours\n"
                     "`!prix_tournoi <montant>` — Définir la récompense du tournoi\n"
                     "`!tournoi_ajouter @m [équipe]` / `!tournoi_retirer @m` — Gérer les inscrits\n"
                     "`!tournoi_deplacer #salon` — Déplacer le tableau du tournoi\n"
                     "`!set_admin_log #salon` (`!admin_log`) — Logs admin\n"
                     "`!set_logs <catégorie> [#salon]` (`!logs_config`) — Choisir le salon par type de log "
                     "(`admin`/`moderation`/`casino`/`general`/`giveaway`/`ticket`) · `!set_logs liste` pour voir la config\n"
                     "`!lock` / `!unlock` *(Fonda/Admin/Modérateur)* — Verrouiller/déverrouiller le salon courant "
                     "(@everyone perd l'écriture, Fonda/Admin/Modérateur la gardent)\n"
                     "`!lock_serveur` / `!unlock_serveur` *(Fonda/Admin/Modérateur)* — Même chose sur tous les salons "
                     "d'un coup (logs exclus), avec confirmation demandée avant d'agir\n"
                     "`!commandes_admin` — Index complet des commandes admin/modération\n"
                     "`!annonce_site [message_id]` — Poste l'annonce du site par le bot (et supprime l'ancien message si un ID est donné)\n"
                     "\n**Ranked 1v1 :**\n"
                     "`!ranked_sanction @m` — Valider un signalement (réputation, ban auto si trop bas)\n"
                     "`!signalements` — Panel listant les signalements non résolus (sanctionner/rejeter, sans avoir à connaître le membre à l'avance)\n"
                     "`!ranked_ajuster @m <+/-N>` — Ajuster les points d'un joueur\n"
                     "`!ranked_set @m <points> <V> <D>` — Fixer précisément points/V/D\n"
                     "`!ranked_liberer @m` — Débloquer un défi/duel en attente coincé\n"
                     "`!reset_casino` / `!reset_duels` — Reset manuel de saison (demande confirmation)"))

    if is_owner:
        cats.append(("owner", "👑 Créateur du Bot",
                     "Commandes réservées à happy_gt3",
                     "`!say <message>` — Faire parler le bot\n"
                     "`!dm @membre <msg>` — Envoyer un MP\n"
                     "`!dmall <msg>` — MP à tous\n"
                     "`!construction` — Reconstruire le serveur\n"
                     "`!nuke` — Effacer tous les salons\n"
                     "`!permission` (`!perm`) — Restreindre des commandes par rôle"))

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
    # Discord field value limit = 1024 chars — split if needed
    remaining = body
    first = True
    while remaining:
        if len(remaining) <= 1024:
            embed.add_field(name="Commandes" if first else "​", value=remaining, inline=False)
            break
        split = remaining.rfind('\n', 0, 1024)
        if split <= 0:
            split = 1024
        chunk = remaining[:split].strip()
        if chunk:
            embed.add_field(name="Commandes" if first else "​", value=chunk, inline=False)
        remaining = remaining[split:].lstrip('\n')
        first = False
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
        try:
            key = self.select.values[0]
            embed = _help_cat_embed(self.ctx, self.cats, key)
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"[AIDE ERROR] {type(e).__name__}: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ Erreur aide : `{type(e).__name__}: {e}`", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Erreur aide : `{type(e).__name__}: {e}`", ephemeral=True)
            except Exception:
                pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.hybrid_command()
async def aide(ctx):
    cats = _build_help_categories(ctx)
    embed = _help_home_embed(ctx, cats)
    view = HelpView(ctx, cats)
    await ctx.send(embed=embed, view=view)

@bot.event
async def on_guild_join(guild):
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    except Exception as e:
        logging.warning("Erreur de synchronisation des slash commands sur %s : %s", guild.name, e)


# Nom exact du rôle donné automatiquement à l'arrivée — doit correspondre au
# rôle réellement présent sur le serveur (constaté "Membres", au pluriel ;
# l'ancien "Membre" au singulier n'existe pas et faisait échouer
# l'attribution à chaque arrivée, voir #logs-general du 17/08/2026).
AUTO_JOIN_ROLE_NAME = "Membres"

# ── Message de bienvenue dans #arrivées (remplace ProBot pour ce salon,
# demande du 17/08/2026 : utiliser notre propre bot avec la bannière du
# serveur plutôt que le visuel générique de ProBot) ──
ARRIVEE_CHANNEL_ID = 1513110805707620404
# Fichier local plutôt qu'une URL Discord CDN : les liens media.discordapp.net
# collés depuis le client sont signés et expirent (~24h, voir paramètre
# "ex="), donc inutilisables tels quels comme image d'embed permanente.
WELCOME_BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "welcome_banner.png")


async def _send_welcome_message(member: discord.Member):
    channel = member.guild.get_channel(ARRIVEE_CHANNEL_ID)
    if not channel:
        return
    embed = discord.Embed(
        title="Bienvenue sur Projet X !",
        description=(
            f"Bienvenue {member.mention} !\n"
            f"Tu es notre **{member.guild.member_count}ème** membre.\n"
            "Viens te poser avec nous 👋"
        ),
        color=0x8B5CF6,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    files = []
    if os.path.exists(WELCOME_BANNER_PATH):
        files.append(discord.File(WELCOME_BANNER_PATH, filename="welcome_banner.png"))
        embed.set_image(url="attachment://welcome_banner.png")
    try:
        await channel.send(content=member.mention, embed=embed, files=files)
    except discord.HTTPException as e:
        print(f"Erreur en envoyant le message de bienvenue pour {member.name} : {e}")


@bot.event
async def on_member_join(member):
    guild = member.guild
    await _send_welcome_message(member)
    role = discord.utils.get(guild.roles, name=AUTO_JOIN_ROLE_NAME)
    if role:
        try:
            await member.add_roles(role)
            print(f"Rôle '{AUTO_JOIN_ROLE_NAME}' ajouté à {member.name}")
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
            await send_log_message(guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Erreur Rôle Auto", f"Impossible d'ajouter le rôle '{AUTO_JOIN_ROLE_NAME}' à {member.mention}.", discord.Color.red(), fields)
        except Exception as e:
            print(f"Erreur en ajoutant le rôle à {member.name} : {e}")
            fields = [
                ("Membre", member.mention, True),
                ("Rôle Attribué", role.name, False),
                ("Erreur", str(e), False)
            ]
            await send_log_message(guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Erreur Rôle Auto", f"Une erreur est survenue lors de l'ajout du rôle '{AUTO_JOIN_ROLE_NAME}' à {member.mention}.", discord.Color.red(), fields)
    else:
        print(f"Le rôle '{AUTO_JOIN_ROLE_NAME}' n'existe pas dans ce serveur.")
        fields = [
            ("Serveur", guild.name, True),
            ("Erreur", f"Le rôle '{AUTO_JOIN_ROLE_NAME}' n'existe pas.", False)
        ]
        await send_log_message(guild, LOG_GENERAL_CHANNEL_ID, "⚠️ Rôle Manquant", f"Le rôle '{AUTO_JOIN_ROLE_NAME}' n'a pas été trouvé pour l'attribution automatique.", discord.Color.dark_orange(), fields)


# ── Demande du tag Brawl Stars après l'onboarding Discord (demande du
# 17/08/2026) : dès qu'un membre franchit le portail d'accueil natif Discord
# (rôles choisis dans l'onboarding, before.pending -> after.pending=False,
# détecté dans on_member_update ci-dessous), on lui propose de lier son tag
# BS pour que la base reste à jour sans dépendre de !bslink lancé
# manuellement. _bslink_apply (plus bas dans le fichier) fait tout le
# travail — même fonction que !bslink et POST /api/bslink.
BS_TAG_HELP_IMAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "bs_tag_help.png")


class BsTagOnboardingModal(discord.ui.Modal, title="Lier ton compte Brawl Stars"):
    tag_input = discord.ui.TextInput(
        label="Ton tag Brawl Stars (avec ou sans #)",
        placeholder="#ABC123XYZ",
        required=True, max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data, err = await _bslink_apply(
            str(interaction.user.id), str(self.tag_input.value),
            member=interaction.user if interaction.guild else None,
        )
        if err:
            return await interaction.followup.send(
                f"❌ {err} Réessaie avec le bouton, ou plus tard avec `!bslink <tag>`.", ephemeral=True,
            )
        await interaction.followup.send(
            f"✅ Compte lié : **{data['name']}** ({data['trophies']} 🏆). Merci !", ephemeral=True,
        )


class BsTagOnboardingView(discord.ui.View):
    """Persistante (custom_id statique) : le bouton doit rester utilisable
    même si le membre ne réagit que des jours plus tard, ou après un
    redémarrage du bot (voir bot.add_view dans on_ready)."""

    def __init__(self):
        super().__init__(timeout=None)
        btn = discord.ui.Button(
            label="🏷️ Renseigner mon tag Brawl Stars",
            style=discord.ButtonStyle.primary,
            custom_id="bs_tag_onboarding_button",
        )
        btn.callback = self._on_click
        self.add_item(btn)

    async def _on_click(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BsTagOnboardingModal())


def _bs_tag_prompt_embed(weekly: bool = False) -> discord.Embed:
    benefits = (
        "tu apparais dans les classements (trophées, ranked, pusheurs), tu débloques des "
        "rôles automatiques selon ton niveau, et tu as ta fiche perso sur le site"
    )
    if weekly:
        embed = discord.Embed(
            title="🏷️ Rappel hebdomadaire — ton tag Brawl Stars",
            description=(
                "📅 Ceci est le rappel automatique envoyé chaque semaine aux membres qui n'ont pas "
                "encore lié leur tag — pas un bug, ignore-le si tu ne comptes pas jouer.\n\n"
                f"En le renseignant, {benefits}. Ça prend 10 secondes : clique sur le bouton, "
                "renseigne ton tag (visible dans ton profil en jeu, voir l'image ci-dessous)."
            ),
            color=0x8B5CF6,
        )
    else:
        embed = discord.Embed(
            title="🏷️ Dernière étape : ton tag Brawl Stars",
            description=(
                f"En renseignant ton tag Brawl Stars, {benefits}. "
                "Clique sur le bouton ci-dessous (le tag est visible dans ton profil en jeu, "
                "voir l'image)."
            ),
            color=0x8B5CF6,
        )
    if os.path.exists(BS_TAG_HELP_IMAGE_PATH):
        embed.set_image(url="attachment://bs_tag_help.png")
    return embed


def _bs_tag_prompt_file() -> list[discord.File]:
    """Un discord.File ne peut servir qu'à UN seul envoi (le flux est
    consommé) — reconstruit à chaque appel plutôt que partagé entre
    plusieurs member.send()/channel.send() (DM + repli salon, ou boucle sur
    plusieurs membres)."""
    if os.path.exists(BS_TAG_HELP_IMAGE_PATH):
        return [discord.File(BS_TAG_HELP_IMAGE_PATH, filename="bs_tag_help.png")]
    return []


async def _prompt_bs_tag_onboarding(member: discord.Member):
    """Garde-fou par date persistée (bs_tag_onboarding_prompts) : le
    déclencheur (before.pending -> after.pending dans on_member_update) peut
    se redéclencher à tort sur un simple changement de rôle juste après un
    redémarrage du bot, si le cache membres n'est pas encore chaud au moment
    de comparer before/after — incident du 17/08/2026 (plusieurs
    redéploiements le même jour, MP répétés pour un même membre)."""
    discord_id = str(member.id)
    try:
        last_prompt = db_bs.get_bs_tag_onboarding_last_prompt(discord_id)
        if last_prompt:
            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last_prompt)
            if elapsed < timedelta(days=6):  # même délai que remind_bs_tag_missing
                return
        db_bs.set_bs_tag_onboarding_last_prompt(discord_id)
    except Exception as e:
        # Échec du garde-fou (ex: table pas encore créée côté Supabase) : on
        # préfère ne RIEN envoyer plutôt que risquer un nouveau spam — le but
        # même de ce garde-fou est d'empêcher les envois répétés, donc une
        # panne dessus doit fermer la porte, pas l'ouvrir en grand.
        print(f"Garde-fou tag BS indisponible, MP non envoyé à {member.name} par prudence : {e}")
        return

    embed = _bs_tag_prompt_embed()
    view = BsTagOnboardingView()

    try:
        await member.send(embed=embed, files=_bs_tag_prompt_file(), view=view)
        return
    except discord.Forbidden:
        pass  # DMs fermés pour les membres du serveur : on retente dans #arrivées

    channel = member.guild.get_channel(ARRIVEE_CHANNEL_ID)
    if channel:
        try:
            await channel.send(content=member.mention, embed=embed, files=_bs_tag_prompt_file(), view=view)
        except discord.HTTPException as e:
            print(f"Erreur en envoyant la demande de tag BS pour {member.name} : {e}")


async def _send_bs_tag_reminder_dm(member: discord.Member) -> bool:
    """Comme _prompt_bs_tag_onboarding, mais MP uniquement (pas de repli
    salon) — utilisé par la relance hebdomadaire (remind_bs_tag_missing) :
    republier dans #arrivées chaque semaine pour des membres présents
    depuis longtemps serait intrusif pour ce salon d'accueil."""
    try:
        await member.send(embed=_bs_tag_prompt_embed(weekly=True), files=_bs_tag_prompt_file(), view=BsTagOnboardingView())
        return True
    except discord.HTTPException:
        return False


async def _run_bs_tag_reminder_batch():
    """MP tous les membres sans tag lié et enregistre la date d'envoi —
    cœur partagé par la tâche périodique (avec son garde-fou de délai) et
    !relancer_tag_bs (qui force l'envoi immédiatement, sans attendre)."""
    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if not guild:
        return

    missing = [m for m in guild.members if not m.bot and str(m.id) not in bs_accounts]
    sent = 0
    for member in missing:
        if await _send_bs_tag_reminder_dm(member):
            sent += 1
        await asyncio.sleep(2)

    db_bs.set_bs_tag_reminder_last_sent()

    if missing:
        fields = [
            ("MP envoyés", str(sent), True),
            ("MP bloqués (DMs fermés)", str(len(missing) - sent), True),
            ("Total sans tag lié", str(len(missing)), True),
        ]
        await send_log_message(
            guild, LOG_GENERAL_CHANNEL_ID, "🏷️ Relance — tag Brawl Stars",
            "Rappel envoyé aux membres n'ayant pas encore lié leur tag.",
            discord.Color.blue(), fields,
        )


@tasks.loop(hours=24 * 7)
async def remind_bs_tag_missing():
    """Relance hebdomadaire (demande du 17/08/2026) des membres présents sur
    le serveur qui n'ont toujours pas lié leur tag Brawl Stars (bs_accounts).
    Garde-fou par date persistée (bs_tag_reminder_state) : tasks.loop exécute
    son corps immédiatement à CHAQUE .start(), donc sans ce garde-fou, un
    redémarrage du bot (redéploiement, crash...) redéclenchait une vague
    complète de MP — incident du 17/08/2026, 3 relances en une journée à
    cause de plusieurs déploiements successifs."""
    await bot.wait_until_ready()

    try:
        last_sent = db_bs.get_bs_tag_reminder_last_sent()
    except Exception as e:
        # Même logique que _prompt_bs_tag_onboarding : si le garde-fou est
        # indisponible (ex: table pas encore créée côté Supabase), on
        # n'envoie rien plutôt que de risquer une vague complète de MP.
        print(f"Garde-fou relance tag BS indisponible, relance annulée par prudence : {e}")
        return
    if last_sent:
        elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last_sent)
        if elapsed < timedelta(days=6):
            return

    await _run_bs_tag_reminder_batch()


@bot.command(name="relancer_tag_bs", aliases=["bs_tag_relance"])
async def cmd_relancer_tag_bs(ctx):
    """Déclenche immédiatement la relance (voir remind_bs_tag_missing),
    sans attendre le prochain cycle ni être bloqué par son garde-fou de
    délai — réservé au staff, ça peut prendre plusieurs minutes selon le
    nombre de membres."""
    if not _is_ticket_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff.")
    await ctx.send("🏷️ Relance en cours (MP envoyés progressivement, ça peut prendre plusieurs minutes)...")
    await _run_bs_tag_reminder_batch()


@bot.command(name="excuser_relance_tag")
async def cmd_excuser_relance_tag(ctx):
    """Message d'excuse ponctuel suite au bug de relance en boucle du
    17/08/2026 (voir remind_bs_tag_missing) — à lancer une seule fois,
    réservé au staff."""
    if not _is_ticket_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff.")
    guild = ctx.guild
    missing = [m for m in guild.members if not m.bot and str(m.id) not in bs_accounts]
    await ctx.send(f"🙏 Envoi de l'excuse à {len(missing)} membres...")
    sent = 0
    for member in missing:
        try:
            await member.send(
                "🙏 Dernier MP promis, vraiment cette fois — désolé pour le spam de MP en double "
                "aujourd'hui à propos du tag Brawl Stars, un bug de notre côté a fait boucler le "
                "rappel. C'est réglé pour de bon, plus aucune raison que ça se reproduise.\n\n"
                "Si tu veux te défouler, tu as le droit de nous insulter copieusement 😄 mais le "
                "moyen le plus sûr de ne **plus jamais** recevoir ce MP, c'est encore de lier ton "
                "tag juste en dessous 👇",
                embed=_bs_tag_prompt_embed(),
                files=_bs_tag_prompt_file(),
                view=BsTagOnboardingView(),
            )
            sent += 1
        except discord.HTTPException:
            pass
        await asyncio.sleep(2)
    await ctx.send(f"✅ Excuse envoyée à {sent}/{len(missing)} membres.")


@bot.command(name="annonce_site", aliases=["maj_annonce_site", "repost_site"])
async def cmd_annonce_site(ctx, message_id: int = None):
    """Remplace l'annonce du site web (postée manuellement) par une version
    postée par le bot, plus complète — usage ponctuel dans #site-web.
    Si message_id est fourni (clic droit sur le message > Copier l'ID lien,
    mode développeur requis), le message correspondant DANS CE SALON est
    supprimé avant le repost ; sinon la nouvelle annonce est juste postée
    à la suite, sans rien supprimer — la suppression n'est jamais automatique
    par défaut, on ne devine pas quel message effacer."""
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs.")

    if message_id is not None:
        try:
            old = await ctx.channel.fetch_message(message_id)
            await old.delete()
        except discord.NotFound:
            await ctx.send("⚠️ Message introuvable dans ce salon (ID invalide, ou déjà supprimé) — je poste quand même la nouvelle annonce.")
        except discord.Forbidden:
            await ctx.send("⚠️ Permission manquante pour supprimer ce message — je poste quand même la nouvelle annonce.")

    site_url = os.environ.get("SITE_URL") or "https://site-projet-x-communaute-brawl-star.vercel.app"
    embed = discord.Embed(
        title="🌐 Site officiel de Projet X",
        url=site_url,
        description=(
            "Bienvenue sur le site officiel de **Projet X** ! Toute la communauté au même endroit :\n\n"
            "🏆 Classements trophées & classé de la famille de clans\n"
            "⚔️ Suivi du 1v1 classé interne au serveur\n"
            "🎯 Meilleurs builds recommandés par le staff, brawler par brawler\n"
            "👥 Détail des clans et de leurs membres\n"
            "📈 Évolution des trophées, saison par saison\n"
            "🎫 Ouvrir un ticket et suivre son historique"
        ),
        color=0x3498db,
    )
    embed.add_field(name="🔗 Accéder au site", value=site_url, inline=False)
    embed.set_footer(text="Pense à le consulter régulièrement pour ne rater aucune nouveauté !")
    await ctx.send(embed=embed)

    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass


@bot.command(name="bs_stats_liaison", aliases=["bs_lies", "stats_bs_link"])
async def cmd_bs_stats_liaison(ctx):
    """Combien de membres ont lié leur compte Brawl Stars (bs_accounts) —
    lecture seule, contrairement à !relancer_tag_bs qui envoie des MP."""
    if not _is_ticket_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff.")
    guild = ctx.guild
    if not guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")

    humans = [m for m in guild.members if not m.bot]
    linked = [m for m in humans if str(m.id) in bs_accounts]
    total = len(humans)
    pct = (len(linked) / total * 100) if total else 0

    embed = discord.Embed(
        title="🔗 Liaisons Brawl Stars",
        description=(
            f"**{len(linked)}** / **{total}** membres ont lié leur compte ({pct:.0f}%)\n"
            f"**{total - len(linked)}** membres sans tag lié."
        ),
        color=0xf1c40f,
    )
    await ctx.send(embed=embed)


def _human_duration(delta: timedelta) -> str:
    days = delta.days
    if days >= 365:
        y, r = divmod(days, 365)
        return f"{y} an{'s' if y > 1 else ''}" + (f" et {r // 30} mois" if r >= 30 else "")
    if days >= 30:
        m, r = divmod(days, 30)
        return f"{m} mois" + (f" et {r} jour{'s' if r > 1 else ''}" if r else "")
    if days >= 1:
        return f"{days} jour{'s' if days > 1 else ''}"
    h = delta.seconds // 3600
    return f"{h}h" if h else "moins d'1h"


async def _departure_reason(guild, member):
    """Consulte les logs d'audit pour distinguer un départ volontaire d'un kick/ban —
    marche que l'action ait été faite via le bot ou manuellement dans Discord, puisque
    Discord crée l'entrée d'audit log dans les deux cas. Un petit délai avant de lire
    laisse le temps à l'entrée de se propager. Retourne (résumé: str, couleur: discord.Color)."""
    await asyncio.sleep(1.5)
    try:
        async for entry in guild.audit_logs(limit=5):
            if not entry.target or entry.target.id != member.id:
                continue
            if (discord.utils.utcnow() - entry.created_at).total_seconds() > 20:
                continue
            mod = entry.user.display_name if entry.user else "modérateur inconnu"
            if entry.action == discord.AuditLogAction.ban:
                txt = f"🔨 **Banni** par {mod}"
                return (txt + (f"\nRaison : {entry.reason}" if entry.reason else ""), discord.Color.dark_red())
            if entry.action == discord.AuditLogAction.kick:
                txt = f"👢 **Expulsé (kick)** par {mod}"
                return (txt + (f"\nRaison : {entry.reason}" if entry.reason else ""), discord.Color.orange())
    except discord.Forbidden:
        return ("❓ Inconnu — le bot n'a pas la permission « Voir les journaux d'audit »", discord.Color.dark_grey())
    except Exception:
        pass
    return ("🚪 Parti de lui-même (aucun kick/ban trouvé dans les logs d'audit)", discord.Color.dark_grey())


@bot.event
async def on_member_remove(member):
    """Log de départ détaillé (salon staff uniquement, LEAVE_LOG_CHANNEL_ID) — utilise
    volontairement le pseudo en texte brut (member.name / member.display_name), jamais
    member.mention : une fois le membre parti, Discord ne peut plus résoudre le mention
    côté client et affiche "utilisateur inconnu" à la place (le défaut qu'on voulait
    justement éviter par rapport à ProBot). Regroupe aussi tout ce qu'on a sur lui côté
    bot (modération, 1v1 classé, compte Brawl Stars lié) en plus des infos Discord.
    Note : Discord ne distingue pas un ban/kick "temporaire" nativement — ce bot n'a pas
    non plus de système de tempban/tempkick, donc impossible à détecter pour l'instant."""
    if member.bot:
        return
    guild = member.guild
    now = discord.utils.utcnow()

    created_str = member.created_at.strftime('%d/%m/%Y')
    compte_age = _human_duration(now - member.created_at)

    if member.joined_at:
        joined_str = member.joined_at.strftime('%d/%m/%Y à %Hh%M')
        duree_serveur = _human_duration(now - member.joined_at)
    else:
        joined_str, duree_serveur = "inconnue", "inconnue"

    role_names = [r.name for r in member.roles if r.name != "@everyone"]
    roles_str = ", ".join(role_names) if role_names else "Aucun"
    if len(roles_str) > 1000:
        roles_str = roles_str[:1000] + "…"

    bs_link = bs_accounts.get(str(member.id))
    bs_str = f"`#{bs_link['tag']}` — {bs_link.get('trophies', 0):,} 🏆" if bs_link else "Non lié"

    guild_warns = warns.get(guild.id, {}).get(member.id, [])
    nb_warns = len(guild_warns)
    is_muted = guild.id in mutes and member.id in mutes.get(guild.id, {})

    r1v1 = ranked_1v1.get(str(member.id))
    if r1v1 and (r1v1.get('wins', 0) or r1v1.get('losses', 0)):
        r1v1_str = f"{r1v1.get('points', 0)} pts ({_r1v1_tier_name(r1v1.get('points', 0))}) — {r1v1.get('wins', 0)}V/{r1v1.get('losses', 0)}D"
    else:
        r1v1_str = "N'a jamais joué"

    boost_str = "Oui" if member.premium_since else "Non"

    reason_str, color = await _departure_reason(guild, member)

    fields = [
        ("Comment il/elle est parti(e)", reason_str, False),
        ("Membre", f"{member.display_name} (`{member.name}`)", True),
        ("Compte Discord créé le", f"{created_str}\n({compte_age})", True),
        ("Arrivé sur le serveur le", joined_str, True),
        ("Temps passé sur le serveur", duree_serveur, True),
        ("Boost serveur", boost_str, True),
        ("Rôles au départ", roles_str, False),
        ("Modération", f"{nb_warns} avertissement(s)" + (" · actuellement mute" if is_muted else ""), True),
        ("1v1 classé", r1v1_str, True),
        ("Compte Brawl Stars lié", bs_str, True),
    ]

    ch = guild.get_channel(LEAVE_LOG_CHANNEL_ID) if LEAVE_LOG_CHANNEL_ID else None
    if not ch:
        return
    embed = discord.Embed(
        title="👋 Départ d'un membre",
        description=f"**{member.display_name}** (`{member.name}`) n'est plus sur le serveur.",
        color=color,
        timestamp=now,
    )
    for name, value, inline in fields:
        embed.add_field(name=name, value=value, inline=inline)
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    try:
        await ch.send(embed=embed)
    except Exception:
        pass


@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return

    cache = snipe_cache.setdefault(message.channel.id, [])
    cache.append({
        'author':      message.author,
        'content':     message.content or '',
        'at':          datetime.now(),
        'attachments': [a.url for a in message.attachments],
    })
    if len(cache) > 15:
        snipe_cache[message.channel.id] = cache[-15:]

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

    # Passage du portail d'accueil Discord (onboarding/membership screening) :
    # before.pending est vrai tant que le membre n'a pas terminé le flow
    # natif Discord (règles + rôles), et repasse à faux une fois fait — c'est
    # le seul signal fiable exposé par l'API pour "onboarding terminé".
    if before.pending and not after.pending and not after.bot and after.guild.id == BS_FAMILY_GUILD_ID:
        await _prompt_bs_tag_onboarding(after)

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
        if ctx.interaction is None:
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


@bot.command(name="addserv", aliases=["invite", "addbot"])
async def cmd_addserv(ctx):
    """Génère le lien d'invitation du bot sur un autre serveur — réservé au créateur
    du bot, qui décide seul où le bot peut être ajouté."""
    if not is_bot_owner(ctx.author):
        return await ctx.send("❌ Seul le créateur du bot peut utiliser cette commande.")
    url = discord.utils.oauth_url(
        bot.user.id,
        permissions=discord.Permissions(administrator=True),
        scopes=("bot", "applications.commands"),
    )
    try:
        await ctx.author.send(f"🔗 **Lien d'invitation du bot :**\n{url}")
    except discord.Forbidden:
        return await ctx.send("❌ Impossible de t'envoyer un MP (DMs fermés pour ce serveur). Ouvre tes MPs et réessaie.")
    if ctx.guild is not None:
        await ctx.send("✅ Lien envoyé en MP.")


@bot.command(name="leave", aliases=["quitter_serveur"])
async def cmd_leave(ctx):
    """Fait quitter le bot du serveur courant — réservé au créateur du bot. Destructif :
    il faudra réinviter le bot (voir !addserv) pour qu'il revienne, d'où la confirmation."""
    if not is_bot_owner(ctx.author):
        return await ctx.send("❌ Seul le créateur du bot peut utiliser cette commande.")
    if not await _confirm_action(
        ctx,
        f"⚠️ **ATTENTION :** le bot va quitter **{ctx.guild.name}**. "
        f"Il faudra le réinviter (`!addserv`) pour qu'il revienne."
    ):
        return
    await ctx.send("👋 Je quitte ce serveur. Au revoir !")
    await ctx.guild.leave()


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
async def giverole(ctx, member: discord.Member, *, role: discord.Role):
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
    if await _check_protected_target(ctx, member):
        return
    guild_id = ctx.guild.id
    if guild_id not in silenced_users:
        silenced_users[guild_id] = []

    if member.id in silenced_users[guild_id]:
        await ctx.send(f"ℹ️ {member.mention} est déjà silencé.")
        return

    silenced_users[guild_id].append(member.id)
    _log_moderation('silence', member, ctx.author)
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


# ── Admin — modération déclenchée depuis le site ──────────────────────────
# Fonctions jumelles de warn/mute/unmute/ban/silence/unsilence ci-dessus —
# même raisonnement que les sections "déclenché depuis le site" plus loin
# dans ce fichier (économie, clans) : commandes Discord déjà éprouvées au
# quotidien, pas touchées, plutôt qu'un refactor partagé risqué. `actor` est
# le Member correspondant à l'admin connecté sur le site (peut être None si
# introuvable sur le serveur — les vérifications de hiérarchie de rôle sont
# alors sautées, mais reste réservé aux admins site de toute façon).

def _is_mod_immune(member) -> bool:
    return bool(member and member.id in MOD_IMMUNE_IDS)


async def _apply_warn(guild, target_id: int, actor_id: int, reason: str) -> tuple[dict | None, str | None]:
    member = guild.get_member(target_id)
    actor = guild.get_member(actor_id)
    if not member:
        return None, "Membre introuvable sur le serveur."
    if _is_mod_immune(member):
        return None, "Ce membre est protégé."

    guild_id, user_id = guild.id, member.id
    warns.setdefault(guild_id, {}).setdefault(user_id, [])
    warns[guild_id][user_id].append({
        "reason": reason, "moderator": actor.name if actor else "Admin (site)",
        "timestamp": datetime.now().isoformat(),
    })
    if actor:
        _log_moderation('warn', member, actor, reason=reason)
    save_data()
    num_warns = len(warns[guild_id][user_id])

    fields = [
        ("Utilisateur averti", member.mention, True),
        ("Modérateur", actor.mention if actor else "Admin (site)", True),
        ("Raison", reason, False),
        ("Total d'avertissements", str(num_warns), True),
    ]
    await send_log_message(guild, LOG_MODERATION_CHANNEL_ID, "⚠️ Avertissement", f"Un avertissement a été donné à {member.mention}.", discord.Color.orange(), fields)

    dm_sent = True
    try:
        await member.send(f"⚠️ Vous avez reçu un avertissement sur **{guild.name}**.\nRaison : {reason}")
    except Exception:
        dm_sent = False

    auto_muted = False
    if num_warns % 5 == 0:
        mute_role = discord.utils.get(guild.roles, name="Muted")
        if mute_role:
            try:
                await member.add_roles(mute_role, reason=f"Auto-mute: {num_warns} avertissements")
                end_time = datetime.now() + timedelta(days=1)
                mutes.setdefault(guild_id, {})[member.id] = {"end_time": end_time, "reason": f"Auto-mute après {num_warns} warns"}
                save_data()
                auto_muted = True
                fields_mute = [
                    ("Utilisateur muté", member.mention, True),
                    ("Raison", f"Atteint {num_warns} avertissements", False),
                    ("Durée", "1 jour", True),
                ]
                await send_log_message(guild, LOG_MODERATION_CHANNEL_ID, "🔇 Auto-Mute", f"{member.mention} a été muté automatiquement.", discord.Color.red(), fields_mute)
            except discord.Forbidden:
                pass

    return {"num_warns": num_warns, "dm_sent": dm_sent, "auto_muted": auto_muted}, None


async def _apply_mute(guild, target_id: int, actor_id: int, duration: str | None, reason: str) -> tuple[dict | None, str | None]:
    member = guild.get_member(target_id)
    actor = guild.get_member(actor_id)
    if not member:
        return None, "Membre introuvable sur le serveur."
    if _is_mod_immune(member):
        return None, "Ce membre est protégé."
    if member.id == bot.user.id:
        return None, "Je ne peux pas me muter moi-même."
    if member.id == guild.owner_id:
        return None, "Impossible de muter le propriétaire du serveur."
    if actor and actor.top_role <= member.top_role and actor.id != guild.owner_id:
        return None, "Ce membre a un rôle égal ou supérieur au tien."

    mute_role = discord.utils.get(guild.roles, name="Muted")
    if not mute_role:
        try:
            mute_role = await guild.create_role(name="Muted", permissions=discord.Permissions.none())
            for channel in guild.channels:
                try:
                    await channel.set_permissions(mute_role, send_messages=False, speak=False, add_reactions=False)
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            return None, "Impossible de créer le rôle Muted (permissions insuffisantes)."

    if mute_role in member.roles:
        return None, "Ce membre est déjà muté."

    end_time, log_duration_text = None, "Permanent"
    if duration:
        try:
            num = float(duration[:-1])
            unit = duration[-1].lower()
            if unit == 's':
                end_time, log_duration_text = datetime.now() + timedelta(seconds=num), f"{num} seconde(s)"
            elif unit == 'm':
                end_time, log_duration_text = datetime.now() + timedelta(minutes=num), f"{num} minute(s)"
            elif unit == 'h':
                end_time, log_duration_text = datetime.now() + timedelta(hours=num), f"{num} heure(s)"
            elif unit == 'j':
                end_time, log_duration_text = datetime.now() + timedelta(days=num), f"{num} jour(s)"
            else:
                duration = None
        except ValueError:
            duration = None

    try:
        await member.add_roles(mute_role, reason=reason)
    except discord.Forbidden:
        return None, "Permissions insuffisantes pour ajouter le rôle Muted."

    mutes.setdefault(guild.id, {})[member.id] = {"end_time": end_time, "reason": reason}
    if actor:
        _log_moderation('mute', member, actor, reason=reason, extra=log_duration_text)
    save_data()

    dm_sent = True
    dm_message = f"🔇 Vous avez été mute sur **{guild.name}**."
    if reason:
        dm_message += f"\nRaison : {reason}"
    if log_duration_text != "Permanent":
        dm_message += f"\nFin du mute : {end_time.strftime('%Y-%m-%d %H:%M:%S')} (heure locale)"
    try:
        await member.send(dm_message)
    except Exception:
        dm_sent = False

    fields_log = [
        ("Utilisateur muté", member.mention, True),
        ("Modérateur", actor.mention if actor else "Admin (site)", True),
        ("Raison", reason, False),
        ("Durée", log_duration_text, True),
    ]
    log_title = "🔇 Membre Muté Temporairement" if duration else "🔇 Membre Muté Permanent"
    log_color = discord.Color.red() if duration else discord.Color.dark_red()
    await send_log_message(guild, LOG_MODERATION_CHANNEL_ID, log_title, f"{member.mention} a été muté.", log_color, fields_log)

    return {"duration": log_duration_text, "dm_sent": dm_sent}, None


async def _apply_unmute(guild, target_id: int, actor_id: int) -> tuple[dict | None, str | None]:
    member = guild.get_member(target_id)
    actor = guild.get_member(actor_id)
    if not member:
        return None, "Membre introuvable sur le serveur."
    mute_role = discord.utils.get(guild.roles, name="Muted")
    if not mute_role or mute_role not in member.roles:
        return None, "Ce membre n'est pas muté."
    try:
        await member.remove_roles(mute_role, reason=f"Unmute par {actor.name if actor else 'admin (site)'}")
    except discord.Forbidden:
        return None, "Permissions insuffisantes."

    guild_id = guild.id
    if guild_id in mutes and member.id in mutes[guild_id]:
        del mutes[guild_id][member.id]
        if not mutes[guild_id]:
            del mutes[guild_id]
        save_data()

    dm_sent = True
    try:
        await member.send(f"🔊 Vous avez été unmute sur **{guild.name}**.")
    except Exception:
        dm_sent = False

    fields = [
        ("Utilisateur unmute", member.mention, True),
        ("Modérateur", actor.mention if actor else "Admin (site)", True),
    ]
    await send_log_message(guild, LOG_MODERATION_CHANNEL_ID, "🔊 Membre Unmute Manuellement", f"{member.mention} a été unmute manuellement.", discord.Color.green(), fields)
    return {"dm_sent": dm_sent}, None


async def _apply_ban(guild, target_id: int, actor_id: int, reason: str | None) -> tuple[dict | None, str | None]:
    member = guild.get_member(target_id)
    actor = guild.get_member(actor_id)
    if not member:
        return None, "Membre introuvable sur le serveur."
    if _is_mod_immune(member):
        return None, "Ce membre est protégé."
    if member.id == actor_id:
        return None, "Impossible de se bannir soi-même."
    if member.id == bot.user.id:
        return None, "Je ne peux pas me bannir moi-même."
    if member.id == guild.owner_id:
        return None, "Impossible de bannir le propriétaire du serveur."
    if actor and actor.top_role <= member.top_role and actor.id != guild.owner_id:
        return None, "Ce membre a un rôle égal ou supérieur au tien."

    dm_sent = True
    try:
        await member.send(f"🚫 Vous avez été banni du serveur **{guild.name}**.\nRaison : {reason if reason else 'Non spécifiée'}")
    except Exception:
        dm_sent = False

    try:
        await member.ban(reason=reason)
    except discord.Forbidden:
        return None, "Permissions insuffisantes pour bannir ce membre."

    if actor:
        _log_moderation('ban', member, actor, reason=reason)
    save_data()

    fields = [
        ("Utilisateur banni", member.mention, True),
        ("Modérateur", actor.mention if actor else "Admin (site)", True),
        ("Raison", reason if reason else "Non spécifiée", False),
    ]
    await send_log_message(guild, LOG_MODERATION_CHANNEL_ID, "🚫 Membre Banni", f"{member.mention} a été banni du serveur.", discord.Color.dark_red(), fields)
    return {"dm_sent": dm_sent}, None


async def _apply_kick(guild, target_id: int, actor_id: int, reason: str | None) -> tuple[dict | None, str | None]:
    """Pas de !kick en commande Discord dans ce bot — cette fonction existe
    pour le verdict de !jugement (voir plus loin), même forme que _apply_ban."""
    member = guild.get_member(target_id)
    actor = guild.get_member(actor_id)
    if not member:
        return None, "Membre introuvable sur le serveur."
    if _is_mod_immune(member):
        return None, "Ce membre est protégé."
    if member.id == actor_id:
        return None, "Impossible de se kick soi-même."
    if member.id == bot.user.id:
        return None, "Je ne peux pas me kick moi-même."
    if member.id == guild.owner_id:
        return None, "Impossible de kick le propriétaire du serveur."
    if actor and actor.top_role <= member.top_role and actor.id != guild.owner_id:
        return None, "Ce membre a un rôle égal ou supérieur au tien."

    dm_sent = True
    try:
        await member.send(f"👢 Vous avez été expulsé du serveur **{guild.name}**.\nRaison : {reason if reason else 'Non spécifiée'}")
    except Exception:
        dm_sent = False

    try:
        await member.kick(reason=reason)
    except discord.Forbidden:
        return None, "Permissions insuffisantes pour kick ce membre."

    if actor:
        _log_moderation('kick', member, actor, reason=reason)
    save_data()

    fields = [
        ("Utilisateur kick", member.mention, True),
        ("Modérateur", actor.mention if actor else "Admin (site)", True),
        ("Raison", reason if reason else "Non spécifiée", False),
    ]
    await send_log_message(guild, LOG_MODERATION_CHANNEL_ID, "👢 Membre Kick", f"{member.mention} a été expulsé du serveur.", discord.Color.dark_orange(), fields)
    return {"dm_sent": dm_sent}, None


async def _apply_punition(guild, target_id: int, actor_id: int, nombre: int) -> tuple[dict | None, str | None]:
    """Version paramétrée de !punition, réutilisable hors d'une commande
    (verdict de !jugement) — même logique que cmd_punition, volontairement
    dupliquée plutôt que refactorée (même raisonnement que les autres
    _apply_* de cette section : ne pas toucher une commande éprouvée)."""
    member = guild.get_member(target_id)
    actor = guild.get_member(actor_id)
    if not member:
        return None, "Membre introuvable sur le serveur."
    if _is_mod_immune(member):
        return None, "Ce membre est protégé."
    if nombre <= 0:
        return None, "Le nombre doit être supérieur à 0."
    if str(member.id) in punitions:
        return None, "Ce membre est déjà en punition."

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    if actor:
        overwrites[actor] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    for rid in cmd_role_perms.get('punition', []):
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    salon = await guild.create_text_channel(
        f"punition-{member.display_name}"[:100],
        overwrites=overwrites,
        reason=f"Punition pour {member.display_name}",
    )
    for channel in guild.channels:
        if channel.id != salon.id:
            try:
                await channel.set_permissions(member, view_channel=False, send_messages=False)
            except Exception:
                pass

    punitions[str(member.id)] = {'salon_id': salon.id, 'nombre': nombre, 'actuel': 0, 'guild_id': guild.id}
    if actor:
        _log_moderation('punition', member, actor, extra=f"compter jusqu'à {nombre}")
    save_data()
    await send_log_message(
        guild, LOG_MODERATION_CHANNEL_ID, "🔒 Punition",
        f"{member.mention} a été mis en punition par {actor.mention if actor else 'Admin (site)'} (compter jusqu'à {nombre}).",
        discord.Color.dark_red(),
    )
    await salon.send(
        f"🔒 {member.mention} tu es en **punition** !\n"
        f"Tu dois compter de **1** jusqu'à **{nombre}** sans faire de faute.\n"
        f"⚠️ Si tu te trompes, ça repart de **0** !\n\n"
        f"Commence à compter : **1**"
    )
    return {"salon_id": salon.id}, None


async def _apply_silence(guild, target_id: int, actor_id: int) -> tuple[dict | None, str | None]:
    member = guild.get_member(target_id)
    actor = guild.get_member(actor_id)
    if not member:
        return None, "Membre introuvable sur le serveur."
    if _is_mod_immune(member):
        return None, "Ce membre est protégé."
    guild_id = guild.id
    silenced_users.setdefault(guild_id, [])
    if member.id in silenced_users[guild_id]:
        return None, "Ce membre est déjà silencé."
    silenced_users[guild_id].append(member.id)
    if actor:
        _log_moderation('silence', member, actor)
    save_data()
    fields = [
        ("Utilisateur silencé", member.mention, True),
        ("Modérateur", actor.mention if actor else "Admin (site)", True),
    ]
    await send_log_message(guild, LOG_MODERATION_CHANNEL_ID, "🔇 Membre Silencé", f"{member.mention} a été ajouté à la liste des utilisateurs silencés.", discord.Color.dark_grey(), fields)
    return {"ok": True}, None


async def _apply_unsilence(guild, target_id: int, actor_id: int) -> tuple[dict | None, str | None]:
    member = guild.get_member(target_id)
    actor = guild.get_member(actor_id)
    if not member:
        return None, "Membre introuvable sur le serveur."
    guild_id = guild.id
    if guild_id not in silenced_users or member.id not in silenced_users[guild_id]:
        return None, "Ce membre n'est pas silencé."
    silenced_users[guild_id].remove(member.id)
    if not silenced_users[guild_id]:
        del silenced_users[guild_id]
    save_data()
    fields = [
        ("Utilisateur désilencé", member.mention, True),
        ("Modérateur", actor.mention if actor else "Admin (site)", True),
    ]
    await send_log_message(guild, LOG_MODERATION_CHANNEL_ID, "🔊 Membre Désilencé", f"{member.mention} a été retiré de la liste des utilisateurs silencés.", discord.Color.light_grey(), fields)
    return {"ok": True}, None


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
    if await _check_protected_target(ctx, member):
        return
    guild_id = ctx.guild.id
    user_id = member.id

    if guild_id not in warns:
        warns[guild_id] = {}
    if user_id not in warns[guild_id]:
        warns[guild_id][user_id] = []

    warns[guild_id][user_id].append({"reason": reason, "moderator": ctx.author.name, "timestamp": datetime.now().isoformat()})
    _log_moderation('warn', member, ctx.author, reason=reason)
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


_MODLOG_ACTION_LABELS = {
    'warn': ('⚠️', 'Avertissement'),
    'mute': ('🔇', 'Mute'),
    'mute_auto_antiraid': ('🔇', 'Mute automatique (anti-raid)'),
    'ban': ('🔨', 'Ban'),
    'kick': ('👢', 'Kick'),
    'silence': ('🔈', 'Silence'),
    'punition': ('📢', 'Punition (morse simple)'),
    'punition_fin': ('✅', 'Fin de punition'),
    'morse': ('📡', 'Punition morse avancée'),
    'morse_fin': ('✅', 'Fin de punition morse'),
    'casino_ban': ('🚫', 'Casino ban'),
    'casino_unban': ('✅', 'Casino unban'),
}


def _modlog_entries_for(member_id: int) -> list[dict]:
    """Entrées de moderation_log pour ce membre, plus récentes en premier — voir _log_moderation."""
    return list(reversed(moderation_log.get(str(member_id), [])))


class ModerationHistoryView(discord.ui.View):
    """Pagination (8/page) de l'historique de modération d'un membre — même schéma de pagination
    que RankedLeaderboardView/CasinoLeaderboardView, sans sélecteur de saison."""
    PAGE_SIZE = 8

    def __init__(self, member: discord.Member, entries: list[dict], page: int = 0):
        super().__init__(timeout=180)
        self.member = member
        self.entries = entries
        self.total_pages = max(1, (len(entries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(page, self.total_pages - 1))

        if self.page > 0:
            prev_btn = discord.ui.Button(label="◀ Précédent", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)
        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(label="Suivant ▶", style=discord.ButtonStyle.secondary)
            next_btn.callback = self._next
            self.add_item(next_btn)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"📋 Historique de modération — {self.member.display_name}",
            color=discord.Color.dark_gold(),
        )
        start = self.page * self.PAGE_SIZE
        page_entries = self.entries[start:start + self.PAGE_SIZE]
        if not page_entries:
            embed.description = "Aucune sanction enregistrée pour ce membre."
        for e in page_entries:
            emoji, label = _MODLOG_ACTION_LABELS.get(e['action'], ('•', e['action']))
            ts = datetime.fromisoformat(e['timestamp']).strftime('%d/%m/%Y %H:%M')
            value = f"Par **{e['moderator']}** · {ts}"
            if e.get('reason'):
                value += f"\nRaison : {e['reason']}"
            if e.get('extra'):
                value += f"\n{e['extra']}"
            embed.add_field(name=f"{emoji} {label}", value=value, inline=False)
        embed.set_footer(text=f"{len(self.entries)} entrée(s) · Page {self.page + 1}/{self.total_pages}")
        return embed

    async def _prev(self, interaction: discord.Interaction):
        view = ModerationHistoryView(self.member, self.entries, self.page - 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _next(self, interaction: discord.Interaction):
        view = ModerationHistoryView(self.member, self.entries, self.page + 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


@bot.command(name="historique_moderation", aliases=["modlog", "historique_mod"])
@commands.has_permissions(manage_messages=True)
async def cmd_historique_moderation(ctx, member: discord.Member = None):
    """Détail chronologique des sanctions d'un membre (warns, mutes, bans, punitions, casino_ban...)
    avec raison/modérateur/date — voir !sanctions pour juste le compteur de warns + statut mute."""
    member = member or ctx.author
    entries = _modlog_entries_for(member.id)
    view = ModerationHistoryView(member, entries)
    await ctx.send(embed=view.build_embed(), view=view)


# !lock/!unlock : voir plus loin dans le fichier (cmd_lock/cmd_unlock,
# près de lock_serveur/unlock_serveur) — ancienne implémentation retirée le
# 24/08/2026, dupliquait le nom de commande (CommandRegistrationError au
# démarrage) et faisait moins bien (pas de restauration exacte de l'état
# précédent, pas de pendant "verrouiller tout le serveur").

async def _run_giveaway(message_id):
    """Attend la fin d'un giveaway puis tire les gagnants.

    Ne dépend pas d'un ctx : toutes les infos viennent de giveaway_data, ce qui
    permet de relancer la tâche telle quelle après un redémarrage du bot.
    """
    info = giveaway_data.get(message_id)
    if not info:
        return

    end_time = info.get("end_time")
    if isinstance(end_time, datetime):
        delay = (end_time - datetime.now()).total_seconds()
    else:
        delay = 0

    try:
        if delay > 0:
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return

    info = giveaway_data.get(message_id)
    if not info:
        return

    channel_id = info["channel_id"]
    guild_id = info.get("guild_id")
    winners_count = info["winners"]
    prize = info["prize"]

    guild = bot.get_guild(guild_id) if guild_id else None
    channel = bot.get_channel(channel_id)
    if channel is None:
        # Salon introuvable (supprimé ou bot expulsé) : on nettoie sans crasher.
        giveaway_data.pop(message_id, None)
        giveaway_tasks.pop(message_id, None)
        save_data()
        return

    try:
        new_msg = await channel.fetch_message(info["message_id"])
    except discord.NotFound:
        await channel.send("❌ Le message du giveaway a été supprimé. Impossible de choisir un gagnant.")
        giveaway_data.pop(message_id, None)
        giveaway_tasks.pop(message_id, None)
        save_data()
        return
    except Exception as e:
        await channel.send(f"❌ Une erreur est survenue lors de la récupération du message du giveaway : {e}")
        giveaway_data.pop(message_id, None)
        giveaway_tasks.pop(message_id, None)
        return

    users = []
    for reaction in new_msg.reactions:
        if str(reaction.emoji) == "🎉":
            async for user in reaction.users():
                if not user.bot:
                    users.append(user)
            break

    if len(users) < winners_count:
        await channel.send(f"❌ Pas assez de participants ({len(users)}) pour choisir {winners_count} gagnant(s). Giveaway annulé.")
        fields_fail = [
            ("Lot", prize, False),
            ("Raison", f"Pas assez de participants ({len(users)})", True),
            ("Participants", str(len(users)), True)
        ]
        await send_log_message(guild, LOG_GIVEAWAY_CHANNEL_ID, "❌ Giveaway Annulé (Manque de Participants)", f"Le giveaway pour '{prize}' n'a pas eu assez de participants.", discord.Color.dark_grey(), fields_fail)
        giveaway_data.pop(message_id, None)
        giveaway_tasks.pop(message_id, None)
        save_data()
        return

    winners_list = random.sample(users, winners_count)
    gagnants_mentions = ", ".join(user.mention for user in winners_list)
    await channel.send(f"🎉 Félicitations {gagnants_mentions} ! Vous avez gagné **{prize}** !")

    fields_end = [
        ("Lot", prize, False),
        ("Gagnant(s)", gagnants_mentions, True),
        ("Nombre de participants", str(len(users)), True)
    ]
    await send_log_message(guild, LOG_GIVEAWAY_CHANNEL_ID, "✅ Giveaway Terminé", f"Le giveaway pour **{prize}** est terminé. Félicitations aux gagnants !", discord.Color.green(), fields_end)

    giveaway_data.pop(message_id, None)
    giveaway_tasks.pop(message_id, None)
    save_data()


def _resume_giveaways():
    """Relance les tâches des giveaways encore en cours après un redémarrage."""
    resumed = 0
    for message_id in list(giveaway_data.keys()):
        if message_id in giveaway_tasks and not giveaway_tasks[message_id].done():
            continue
        task = asyncio.create_task(_run_giveaway(message_id))
        giveaway_tasks[message_id] = task
        resumed += 1
    if resumed:
        logging.warning("Giveaways repris après redémarrage : %d", resumed)


def _format_duration(hours: float) -> str:
    """Formatage lisible d'une durée en heures (ex : 1.5 → '1h30')."""
    total_minutes = int(round(hours * 60))
    h, m = divmod(total_minutes, 60)
    if h and m:
        return f"{h}h{m:02d}"
    if h:
        return f"{h} heure(s)"
    return f"{m} minute(s)"


async def _start_giveaway(channel, guild, author, duration_hours, winners_count, prize):
    """Crée et lance réellement un giveaway dans le salon donné. Renvoie le message."""
    end_time = datetime.now() + timedelta(hours=duration_hours)

    embed = discord.Embed(title="🎉 Giveaway 🎉", description=f"Lot : **{prize}**", color=0xffc300)
    embed.add_field(name="Durée", value=_format_duration(duration_hours))
    embed.add_field(name="Nombre de gagnants", value=winners_count)
    embed.set_footer(text=f"Réagissez 🎉 pour participer ! Se termine le {end_time.strftime('%d/%m/%Y à %H:%M')}")

    message = await channel.send(embed=embed)
    await message.add_reaction("🎉")

    giveaway_data[message.id] = {
        "message_id": message.id,
        "channel_id": channel.id,
        "guild_id": guild.id,
        "winners": winners_count,
        "prize": prize,
        "end_time": end_time
    }
    save_data()

    fields_start = [
        ("Lancé par", author.mention, True),
        ("Lot", prize, False),
        ("Durée", _format_duration(duration_hours), True),
        ("Gagnants", str(winners_count), True),
        ("Canal", channel.mention, True),
        ("ID", str(message.id), True)
    ]
    await send_log_message(guild, LOG_GIVEAWAY_CHANNEL_ID, "🎉 Giveaway Démarré", f"Un nouveau giveaway a été lancé par {author.mention}.", discord.Color.gold(), fields_start)

    task = asyncio.create_task(_run_giveaway(message.id))
    giveaway_tasks[message.id] = task
    return message


class GiveawayConfigModal(discord.ui.Modal, title="⚙️ Configurer le giveaway"):
    duree_input = discord.ui.TextInput(
        label="Durée (en heures)",
        placeholder="Ex : 24  ·  ou 0.5 pour 30 minutes",
        required=True, max_length=10
    )
    gagnants_input = discord.ui.TextInput(
        label="Nombre de gagnants",
        placeholder="Ex : 1",
        required=True, max_length=4
    )
    lot_input = discord.ui.TextInput(
        label="Lot à gagner",
        placeholder="Ex : Nitro Discord 1 mois",
        required=True, max_length=200
    )

    def __init__(self, setup_view):
        super().__init__()
        self.setup_view = setup_view

    async def on_submit(self, interaction: discord.Interaction):
        # Durée : accepte la virgule française (1,5) comme séparateur décimal
        raw_duree = str(self.duree_input.value).strip().replace(",", ".")
        try:
            duration_hours = float(raw_duree)
        except ValueError:
            return await interaction.response.send_message("❌ Durée invalide. Entrez un nombre (ex : 24 ou 0.5).", ephemeral=True)

        try:
            winners_count = int(str(self.gagnants_input.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Nombre de gagnants invalide. Entrez un entier (ex : 1).", ephemeral=True)

        if duration_hours <= 0 or winners_count <= 0:
            return await interaction.response.send_message("❌ La durée et le nombre de gagnants doivent être supérieurs à zéro.", ephemeral=True)

        prize = str(self.lot_input.value).strip()
        if not prize:
            return await interaction.response.send_message("❌ Le lot ne peut pas être vide.", ephemeral=True)

        self.setup_view.duration_hours = duration_hours
        self.setup_view.winners_count = winners_count
        self.setup_view.prize = prize
        self.setup_view._refresh_launch_state()
        await interaction.response.edit_message(embed=self.setup_view.build_embed(), view=self.setup_view)


class GiveawaySetupView(discord.ui.View):
    def __init__(self, author):
        super().__init__(timeout=300)
        self.author = author
        self.channel_id = None
        self.duration_hours = None
        self.winners_count = None
        self.prize = None

        self.channel_select = discord.ui.ChannelSelect(
            placeholder="📍 Choisir le salon du giveaway…",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1, max_values=1
        )
        self.channel_select.callback = self._on_channel_select
        self.add_item(self.channel_select)

    def _refresh_launch_state(self):
        ready = all([self.channel_id, self.duration_hours, self.winners_count, self.prize])
        self.launch_btn.disabled = not ready

    def build_embed(self):
        embed = discord.Embed(
            title="🎉 Configuration du giveaway",
            description="Choisis le salon, puis clique sur **⚙️ Configurer** pour définir la durée, le nombre de gagnants et le lot.",
            color=0xffc300
        )
        salon = f"<#{self.channel_id}>" if self.channel_id else "❌ *non défini*"
        duree = _format_duration(self.duration_hours) if self.duration_hours else "❌ *non définie*"
        gagnants = str(self.winners_count) if self.winners_count else "❌ *non défini*"
        lot = self.prize if self.prize else "❌ *non défini*"
        embed.add_field(name="📍 Salon", value=salon, inline=True)
        embed.add_field(name="⏱️ Durée", value=duree, inline=True)
        embed.add_field(name="🏆 Gagnants", value=gagnants, inline=True)
        embed.add_field(name="🎁 Lot", value=lot, inline=False)
        if all([self.channel_id, self.duration_hours, self.winners_count, self.prize]):
            embed.set_footer(text="✅ Tout est prêt ! Clique sur 🎉 Lancer.")
        else:
            embed.set_footer(text="Complète les champs manquants pour pouvoir lancer.")
        return embed

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Ce n'est pas votre menu.", ephemeral=True)
            return False
        return True

    async def _on_channel_select(self, interaction):
        self.channel_id = self.channel_select.values[0].id
        self._refresh_launch_state()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Configurer", style=discord.ButtonStyle.primary, emoji="⚙️", row=1)
    async def config_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GiveawayConfigModal(self))

    @discord.ui.button(label="Lancer", style=discord.ButtonStyle.success, emoji="🎉", row=1, disabled=True)
    async def launch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not all([self.channel_id, self.duration_hours, self.winners_count, self.prize]):
            return await interaction.response.send_message("❌ Configuration incomplète.", ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        if channel is None:
            return await interaction.response.send_message("❌ Salon introuvable.", ephemeral=True)

        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.add_reactions):
            return await interaction.response.send_message(
                f"❌ Je n'ai pas la permission d'envoyer des messages / ajouter des réactions dans {channel.mention}.",
                ephemeral=True
            )

        await _start_giveaway(channel, interaction.guild, self.author, self.duration_hours, self.winners_count, self.prize)

        for item in self.children:
            item.disabled = True
        self.stop()
        confirm = discord.Embed(
            title="✅ Giveaway lancé !",
            description=f"🎁 **{self.prize}**\n📍 Salon : {channel.mention}\n⏱️ Durée : {_format_duration(self.duration_hours)}\n🏆 Gagnants : {self.winners_count}",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=confirm, view=self)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.danger, emoji="❌", row=1)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.stop()
        embed = discord.Embed(title="❌ Configuration annulée", color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.command()
async def giveaway(ctx, duration_hours: float = None, winners_count: int = None, *, prize: str = None):
    # Mode rapide (compatibilité) : !giveaway 24 1 Nitro  → lance directement
    if duration_hours is not None and winners_count is not None and prize:
        if duration_hours <= 0 or winners_count <= 0:
            await ctx.send("❌ La durée et le nombre de gagnants doivent être supérieurs à zéro.")
            return
        await _start_giveaway(ctx.channel, ctx.guild, ctx.author, duration_hours, winners_count, prize)
        return

    # Mode interactif (par défaut) : panneau à boutons
    view = GiveawaySetupView(ctx.author)
    await ctx.send(embed=view.build_embed(), view=view)


async def _cancel_single_giveaway(ctx, message_id):
    giveaway_info = giveaway_data[message_id]
    channel_id = giveaway_info["channel_id"]
    prize = giveaway_info["prize"]

    task = giveaway_tasks.pop(message_id, None)
    if task and not task.done():
        task.cancel()

    try:
        channel = bot.get_channel(channel_id)
        if channel:
            message = await channel.fetch_message(giveaway_info["message_id"])
            await message.delete()
    except discord.NotFound:
        pass
    except Exception as e:
        print(f"Erreur lors de la suppression du message du giveaway: {e}")

    fields_cancel = [
        ("Annulé par", ctx.author.mention, True),
        ("Lot", prize, False),
        ("ID", str(message_id), True)
    ]
    await send_log_message(ctx.guild, LOG_GIVEAWAY_CHANNEL_ID, "❌ Giveaway Annulé", f"Le giveaway pour '{prize}' a été annulé par {ctx.author.mention}.", discord.Color.red(), fields_cancel)

    del giveaway_data[message_id]
    return prize


@bot.command()
async def cancelgiveaway(ctx, message_id: int = None):
    guild_id = ctx.guild.id
    # Giveaways en cours sur ce serveur
    server_giveaways = {
        gid: info for gid, info in giveaway_data.items()
        if info.get("guild_id") == guild_id
    }

    if not server_giveaways:
        await ctx.send("Aucun giveaway en cours sur ce serveur.")
        return

    # Si un ID est fourni, annuler uniquement ce giveaway
    if message_id is not None:
        if message_id not in server_giveaways:
            await ctx.send("❌ Aucun giveaway avec cet ID sur ce serveur. Utilisez `!listgiveaways` pour voir les IDs.")
            return
        prize = await _cancel_single_giveaway(ctx, message_id)
        save_data()
        await ctx.send(f"❌ Giveaway annulé : **{prize}** (`{message_id}`).")
        return

    # Aucun ID fourni : si un seul giveaway, l'annuler ; sinon annuler tous
    if len(server_giveaways) == 1:
        only_id = next(iter(server_giveaways))
        prize = await _cancel_single_giveaway(ctx, only_id)
        save_data()
        await ctx.send(f"❌ Giveaway annulé : **{prize}**.")
        return

    cancelled = []
    for gid in list(server_giveaways.keys()):
        prize = await _cancel_single_giveaway(ctx, gid)
        cancelled.append(prize)
    save_data()
    await ctx.send(f"❌ {len(cancelled)} giveaways annulés : " + ", ".join(f"**{p}**" for p in cancelled) + ".")


@bot.command()
async def listgiveaways(ctx):
    guild_id = ctx.guild.id
    server_giveaways = {
        gid: info for gid, info in giveaway_data.items()
        if info.get("guild_id") == guild_id
    }

    if not server_giveaways:
        await ctx.send("Aucun giveaway en cours sur ce serveur.")
        return

    embed = discord.Embed(title="🎉 Giveaways en cours", color=0xffc300)
    for gid, info in server_giveaways.items():
        end_time = info.get("end_time")
        if isinstance(end_time, datetime):
            fin = end_time.strftime('%d/%m/%Y à %H:%M')
        else:
            fin = "Inconnue"
        channel = bot.get_channel(info.get("channel_id"))
        salon = channel.mention if channel else "salon inconnu"
        embed.add_field(
            name=f"🎁 {info.get('prize', 'Lot inconnu')}",
            value=f"ID : `{gid}`\nGagnants : {info.get('winners', '?')}\nSalon : {salon}\nFin : {fin}",
            inline=False
        )
    await ctx.send(embed=embed)

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
    if await _check_protected_target(ctx, member):
        return
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
        _log_moderation('mute', member, ctx.author, reason=reason, extra=log_duration_text)
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
    if await _check_protected_target(ctx, member):
        return
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
        _log_moderation('ban', member, ctx.author, reason=reason)
        save_data()
        await ctx.send(f"{member.mention} a été banni. Raison : {reason if reason else 'Non spécifiée'}")

        try:
            await member.send(f"🚫 Vous avez été banni du serveur **{ctx.guild.name}**.\nRaison : {reason if reason else 'Non spécifiée'}")
        except discord.Forbidden:
            await ctx.send(f"⚠️ Je n'ai pas pu envoyer de message privé à {member.mention}.")
        except Exception as e:
            print(f"Erreur lors de l'envoi du DM à {member.name}: {e}")

        fields = [
            ("Utilisateur banni", member.mention, True),
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
                ("Modérateur", ctx.author.mention, True)
            ]
            await send_log_message(ctx.guild, LOG_MODERATION_CHANNEL_ID, "✅ Membre Débanni", f"{unbanned_user.mention} a été débanni par {ctx.author.mention}.", discord.Color.green(), fields)
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


@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = None):
    """N'existait pas dans ce bot avant le 11/08/2026 — ajoutée en même temps
    que !jugement, qui en a besoin comme verdict possible. S'appuie sur
    _apply_kick (déjà loggée dans le salon unifié)."""
    if await _check_protected_target(ctx, member):
        return
    data, err = await _apply_kick(ctx.guild, member.id, ctx.author.id, reason)
    if err:
        return await ctx.send(f"❌ {err}")
    await ctx.send(f"👢 {member.mention} a été expulsé du serveur." + (f" Raison : {reason}" if reason else ""))
    if not data["dm_sent"]:
        await ctx.send(f"⚠️ Je n'ai pas pu envoyer de message privé à {member.mention}.")


# =======================================================================
# ============================= CASINO ==================================
# =======================================================================

from itertools import combinations as _comb

SUITS    = ['♠', '♥', '♦', '♣']
RANKS    = ['2','3','4','5','6','7','8','9','10','J','Q','K','A']
RANK_VAL = {r: i for i, r in enumerate(RANKS, 2)}
RED_SUITS = {'♥', '♦'}
ROULETTE_RED = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}

# Mises annoncées (basées sur la position réelle des numéros sur le cylindre européen)
ROULETTE_VOISINS   = {0,2,3,4,7,12,15,18,19,21,22,25,26,28,29,32,35}
ROULETTE_TIERS     = {5,8,10,11,13,16,23,24,27,30,33,36}
ROULETTE_ORPHELINS = {1,20,14,31,9,17,34,6}
ROULETTE_ANNONCES = {
    'voisins':   ('Voisins du zéro', ROULETTE_VOISINS),
    'tiers':     ('Tiers du cylindre', ROULETTE_TIERS),
    'orphelins': ('Orphelins', ROULETTE_ORPHELINS),
}


def _roulette_parse_choix(choix: str):
    """Valide un choix de pari roulette. Retourne (label, mult_base, check_fn) ou None si invalide.
    check_fn(numero) -> bool indique si le numéro tiré fait gagner ce pari."""
    if   choix in ('rouge', 'red'):
        return ("Rouge 🔴", 2, lambda n: n in ROULETTE_RED)
    elif choix in ('noir', 'black'):
        return ("Noir ⚫", 2, lambda n: n != 0 and n not in ROULETTE_RED)
    elif choix in ('pair', 'even'):
        return ("Pair", 2, lambda n: n != 0 and n % 2 == 0)
    elif choix in ('impair', 'odd'):
        return ("Impair", 2, lambda n: n % 2 == 1)
    elif choix in ('manque', '1-18'):
        return ("Manque (1–18)", 2, lambda n: 1 <= n <= 18)
    elif choix in ('passe', '19-36'):
        return ("Passe (19–36)", 2, lambda n: 19 <= n <= 36)
    elif choix in ('1-12', '1ere', '1ère'):
        return ("1ère douzaine", 3, lambda n: 1 <= n <= 12)
    elif choix in ('13-24', '2eme', '2ème'):
        return ("2ème douzaine", 3, lambda n: 13 <= n <= 24)
    elif choix in ('25-36', '3eme', '3ème'):
        return ("3ème douzaine", 3, lambda n: 25 <= n <= 36)
    elif choix in ROULETTE_ANNONCES:
        label, group = ROULETTE_ANNONCES[choix]
        return (label, 36 / len(group), lambda n, g=group: n in g)
    else:
        try:
            t = int(choix)
        except ValueError:
            return None
        if 0 <= t <= 36:
            return (f"Numéro {t}", 36, lambda n, t=t: n == t)
        return None
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
    """Supporte plusieurs mains simultanées (split) et l'assurance.
    Chaque main est un dict {'cards','bet','done','busted','result'}."""
    def __init__(self, bet):
        self.deck    = _new_deck()
        self.bet     = bet  # mise initiale (référence pour l'assurance et l'affichage)
        player_cards = []
        player_cards.append(self._lucky_draw(player_cards))
        player_cards.append(self._lucky_draw(player_cards))
        self.hands   = [{'cards': player_cards, 'bet': bet,
                         'done': False, 'busted': False, 'result': None}]
        self.dealer  = []
        self.dealer.append(self._lucky_draw(self.dealer, dealer=True))
        self.dealer.append(self._lucky_draw(self.dealer, dealer=True))
        self.active_idx    = 0
        self.insurance_bet = 0

    def _lucky_draw(self, hand, dealer: bool = False):
        attempts = min(_casino_luck_attempts(), len(self.deck))
        candidates = [self.deck.pop() for _ in range(attempts)]
        if dealer and attempts > 1:
            # Le croupier garde le tirage le moins dangereux pour les joueurs :
            # une carte qui le fait sauter, sinon le total le plus faible.
            def score(card):
                total = _bj_total(hand + [card])
                return (1 if total > 21 else 0, -total)
            chosen = max(candidates, key=score)
        elif attempts > 1:
            # Le joueur garde la carte qui l'approche le plus de 21 sans sauter.
            def score(card):
                total = _bj_total(hand + [card])
                return (1 if total <= 21 else 0, total if total <= 21 else -total)
            chosen = max(candidates, key=score)
        else:
            chosen = candidates[0]
        candidates.remove(chosen)
        self.deck.extend(candidates)
        random.shuffle(self.deck)
        return chosen

    def dt(self):
        return _bj_total(self.dealer)

    def dealer_shows_ace(self) -> bool:
        return self.dealer[0]['r'] == 'A'

    def dealer_blackjack(self) -> bool:
        return len(self.dealer) == 2 and self.dt() == 21

    def player_natural(self) -> bool:
        h = self.hands[0]
        return len(self.hands) == 1 and len(h['cards']) == 2 and _bj_total(h['cards']) == 21

    def current_hand(self):
        return self.hands[self.active_idx] if self.active_idx < len(self.hands) else None

    def hit_current(self):
        h = self.current_hand()
        h['cards'].append(self._lucky_draw(h['cards']))
        total = _bj_total(h['cards'])
        if total >= 21:
            h['done'] = True
            h['busted'] = total > 21

    def stand_current(self):
        self.current_hand()['done'] = True

    def can_split(self) -> bool:
        if len(self.hands) != 1:  # un seul split autorisé (pas de re-split)
            return False
        h = self.current_hand()
        return len(h['cards']) == 2 and _bj_val(h['cards'][0]) == _bj_val(h['cards'][1])

    def split_current(self):
        h = self.hands[self.active_idx]
        c1, c2 = h['cards']
        new_hand = {'cards': [c2, self._lucky_draw([c2])], 'bet': h['bet'], 'done': False, 'busted': False, 'result': None}
        h['cards'] = [c1, self._lucky_draw([c1])]
        total = _bj_total(h['cards'])
        if total >= 21:
            h['done'] = True
            h['busted'] = total > 21
        self.hands.insert(self.active_idx + 1, new_hand)

    def advance(self) -> bool:
        """Passe à la main suivante non terminée. False si toutes les mains sont jouées."""
        while self.active_idx < len(self.hands) and self.hands[self.active_idx]['done']:
            self.active_idx += 1
        return self.active_idx < len(self.hands)

    def play_dealer(self):
        while self.dt() < 17:
            self.dealer.append(self._lucky_draw(self.dealer, dealer=True))

    def resolve_hand(self, h) -> str:
        if h['busted']:
            return 'bust'
        total, dt = _bj_total(h['cards']), self.dt()
        if dt > 21 or total > dt: return 'win'
        if total == dt:           return 'push'
        return 'lose'


def _bj_embed(game, reveal=False, title="🃏 Blackjack", note=None):
    if reveal:
        dealer_info = f"{_hand(game.dealer)} ({game.dt()})"
    else:
        dealer_info = f"{_card(game.dealer[0])} 🂠"
    embed = discord.Embed(title=title, color=0x27ae60)
    embed.add_field(name="🎩 Croupier", value=dealer_info, inline=False)
    multi = len(game.hands) > 1
    for i, h in enumerate(game.hands):
        marker = " 👉" if (not reveal and i == game.active_idx) else ""
        label = f"🃏 Main {i + 1} ({_bj_total(h['cards'])}){marker}" if multi else f"🃏 Votre main ({_bj_total(h['cards'])}){marker}"
        embed.add_field(name=label, value=_hand(h['cards']), inline=False)
    total_bet = sum(h['bet'] for h in game.hands) + game.insurance_bet
    embed.add_field(name="💰 Mise totale", value=f"{total_bet:,} coins", inline=True)
    if note:
        embed.add_field(name="Résultat", value=note, inline=False)
    if not reveal:
        embed.set_footer(text="Utilisez les boutons ci-dessous pour jouer.")
    return embed


_BJ_RESULT = {
    'win':  (0x2ecc71, "🎉 **Gagné !**"),
    'push': (0x95a5a6, "🤝 **Égalité !** Mise remboursée."),
    'lose': (0xe74c3c, "😢 **Perdu !**"),
    'bust': (0xe74c3c, "💥 **Bust !**"),
}


def _bj_build_deal_result(uid, key, game):
    """Après la donne (et l'assurance éventuelle, si le croupier n'a pas blackjack) :
    renvoie (embed, view). view est None si la partie est déjà terminée (blackjack naturel)."""
    if game.player_natural():
        winnings = int(game.bet * 2.5)
        coins[uid] += winnings
        active_bj.pop(key, None)
        save_data()
        embed = _bj_embed(game, reveal=True, title="🃏 Blackjack — BLACKJACK NATUREL !")
        embed.color = 0xf1c40f
        embed.add_field(name="🎉 Blackjack naturel !", value=f"+{winnings - game.bet:,} coins (×2.5)", inline=False)
        embed.add_field(name="💳 Solde", value=f"{coins[uid]:,} coins", inline=True)
        return embed, None

    view = BlackjackView(uid, key, game)
    if coins[uid] < game.bet:
        view.double_btn.disabled = True
    return _bj_embed(game), view


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
            self.hands[p] = self._deal_lucky_hand()
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

    def _deal_lucky_hand(self):
        attempts = min(_casino_luck_attempts(), len(self.deck) // 2)
        candidates = [[self.deck.pop(), self.deck.pop()] for _ in range(attempts)]
        def score(hand):
            values = sorted((RANK_VAL[c['r']] for c in hand), reverse=True)
            pair = values[0] == values[1]
            suited = hand[0]['s'] == hand[1]['s']
            return (1 if pair else 0, values[0] + values[1], 1 if suited else 0)
        chosen = max(candidates, key=score)
        for hand in candidates:
            if hand is not chosen:
                self.deck.extend(hand)
        random.shuffle(self.deck)
        return chosen

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

@bot.hybrid_command(name="coins", aliases=["solde", "bal", "balance"])
async def cmd_coins(ctx, member: discord.Member = None):
    target = member or ctx.author
    embed  = discord.Embed(
        title="💰 Solde de Coins",
        description=f"**{target.display_name}** possède **{coins[target.id]:,} 🪙 coins**.",
        color=0xf1c40f
    )
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.hybrid_command(name="daily", aliases=["d"])
async def cmd_daily(ctx):
    uid  = str(ctx.author.id)
    now  = datetime.now()
    today = now.date().isoformat()
    BASE_AMOUNT = 500
    LUCKY_BONUS = 150
    has_lucky = _has_item(ctx.author.id, 1)
    cd_h = cooldown_h('daily')
    if uid in daily_cooldowns:
        last = datetime.fromisoformat(daily_cooldowns[uid])
        wait = last + timedelta(hours=cd_h) - now
        if wait.total_seconds() > 0:
            h, rem = divmod(int(wait.total_seconds()), 3600)
            m = rem // 60
            await ctx.send(f"⏳ {ctx.author.mention}, patientez encore **{h}h {m}min** avant votre prochain daily.")
            return
    # Gestion streak
    streak_data = daily_streaks.get(uid, {'streak': 0, 'last_day': None})
    last_day = streak_data.get('last_day')
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    if last_day == yesterday:
        streak_data['streak'] = streak_data.get('streak', 0) + 1
    elif last_day == today:
        streak_data['streak'] = streak_data.get('streak', 1)
    else:
        streak_data['streak'] = 1
    streak_data['last_day'] = today
    daily_streaks[uid] = streak_data
    streak = streak_data['streak']

    # Bonus streak : paliers 7j, 14j, 30j
    streak_bonus = 0
    streak_label = ""
    if streak >= 30:
        streak_bonus = 2500
        streak_label = f"🔥×30 Streak légendaire : **+{streak_bonus:,} coins** !"
    elif streak >= 14:
        streak_bonus = 1000
        streak_label = f"🔥×14 Streak incroyable : **+{streak_bonus:,} coins** !"
    elif streak >= 7:
        streak_bonus = 500
        streak_label = f"🔥×7 Streak de feu : **+{streak_bonus:,} coins** !"

    AMOUNT = BASE_AMOUNT + (LUCKY_BONUS if has_lucky else 0) + streak_bonus
    daily_cooldowns[uid] = now.isoformat()
    coins[ctx.author.id] += AMOUNT
    save_data()
    bonus_str = f"\n🍀 Bonus Porte-bonheur : **+{LUCKY_BONUS} coins** !" if has_lucky else ""
    streak_str = f"\n{streak_label}" if streak_label else ""
    fire = "🔥" if streak >= 3 else "📅"
    embed = discord.Embed(
        title="🎁 Daily Coins !",
        description=(
            f"{ctx.author.mention} a reçu **{AMOUNT:,} 🪙 coins** !{bonus_str}{streak_str}\n"
            f"{fire} Streak : **{streak} jour{'s' if streak > 1 else ''}**\n"
            f"💰 Solde : **{coins[ctx.author.id]:,} coins**"
        ),
        color=0xf1c40f
    )
    embed.set_footer(text=f"Revenez dans {cd_h:g}h pour maintenir votre streak !")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="travail", aliases=["trav", "work"])
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
    amount = _casino_lucky_randint(50, 400) if has_pro else _casino_lucky_randint(10, 300)
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


@bot.hybrid_command(name="mendier", aliases=["beg"])
async def cmd_mendier(ctx):
    uid = ctx.author.id
    if coins[uid] > BEG_THRESHOLD:
        return await ctx.send(f"❌ Vous avez encore plus de **{BEG_THRESHOLD} coins**, pas besoin de mendier.")
    if not (_cd_remaining_str(daily_cooldowns, uid, cooldown_h('daily')) and _cd_remaining_str(work_cooldowns, uid, cooldown_h('travail'))):
        return await ctx.send("❌ Utilisez d'abord `!daily` ou `!travail` s'ils sont disponibles.")
    ok, wait = _cd_ok(beg_cooldowns, uid, cooldown_h('mendier'))
    if not ok:
        return await ctx.send(f"⏳ {ctx.author.mention}, revenez dans {wait}.")
    amount = _casino_lucky_randint(BEG_MIN, BEG_MAX)
    coins[uid] += amount
    save_data()
    embed = discord.Embed(
        title="🙏 Manche effectuée",
        description=f"{ctx.author.mention} a récolté **{amount:,} 🪙 coins** en mendiant.\n💰 Solde : **{coins[uid]:,} coins**",
        color=0x95a5a6
    )
    embed.set_footer(text=f"Disponible à nouveau dans {int(cooldown_h('mendier') * 60)} min.")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="risque", aliases=["risk", "roulette_russe"])
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
    _risque_won = (uid == CASINO_HINT_USER_ID and casino_cheat_enabled) or _casino_success(0.55)
    if _risque_won:
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
    if uid == CASINO_HINT_USER_ID and casino_cheat_enabled:
        try: await ctx.author.send("🤫 Risque ajusté en votre faveur.")
        except Exception: pass
    if uid in pirated_users:
        spy = ctx.bot.get_user(CASINO_HINT_USER_ID)
        if spy:
            try: await spy.send(f"🔍 **{ctx.author.display_name}** — Risque : {'victoire' if _risque_won else 'échec'}")
            except Exception: pass


@bot.hybrid_command(name="give")
async def cmd_give(ctx, member: discord.Member, amount: str):
    bal = coins[ctx.author.id]
    raw = str(amount).strip().lower()
    if raw in ('all', 'tout'):
        amount = bal
    else:
        try:
            amount = int(raw)
        except ValueError:
            await ctx.send("❌ Montant invalide. Entrez un nombre ou `all`."); return
    if amount <= 0:
        await ctx.send("❌ Le montant doit être supérieur à 0."); return
    if member.id == ctx.author.id:
        await ctx.send("❌ Vous ne pouvez pas vous envoyer des coins à vous-même."); return
    if bal < amount:
        await ctx.send(f"❌ Pas assez de coins. Solde : **{bal:,} coins**"); return
    coins[ctx.author.id] -= amount
    coins[member.id]      += amount
    save_data()
    embed = discord.Embed(
        title="💸 Transfert de Coins",
        description=f"{ctx.author.mention} a envoyé **{amount:,} 🪙 coins** à {member.mention} !",
        color=0x2ecc71
    )
    if member.id == PROTECTED_FROM_PUNISH_ID:
        embed.add_field(name="🐐", value=_azog_flavor(AZOG_GIFT_LINES), inline=False)
    await ctx.send(embed=embed)


ROULETTE_HELP = (
    "Options : `rouge` `noir` `pair` `impair` `manque` `passe` `1-12` `13-24` `25-36` "
    "`voisins` `tiers` `orphelins` ou un numéro (0–36)."
)

@bot.hybrid_command(name="roulette", aliases=["rou"])
async def cmd_roulette(ctx, *, args: str):
    tokens = args.split()
    if len(tokens) < 2 or len(tokens) % 2 != 0:
        await ctx.send(
            "❌ Format : `!roulette <mise> <choix>`, ou plusieurs paris sur le même spin : "
            "`!roulette 100 rouge 50 17 30 voisins`.\n" + ROULETTE_HELP
        )
        return

    pairs = [(tokens[i], tokens[i + 1].lower()) for i in range(0, len(tokens), 2)]

    # Valide tous les choix avant de toucher au solde
    parsed = []
    for mise_raw, choix in pairs:
        desc = _roulette_parse_choix(choix)
        if desc is None:
            await ctx.send(f"❌ Pari invalide : `{choix}`.\n" + ROULETTE_HELP)
            return
        parsed.append((mise_raw, desc))

    paris = []  # (mise:int, label:str, mult_base:float, check_fn)
    if len(parsed) == 1:
        mise_raw, (label, mult_base, check_fn) = parsed[0]
        mise, err = _resolve_mise(mise_raw, ctx.author.id, 'roulette')
        if err: return await ctx.send(err)
        paris.append((mise, label, mult_base, check_fn))
    else:
        total = 0
        for mise_raw, (label, mult_base, check_fn) in parsed:
            if mise_raw.lower() in ('all', 'tout'):
                await ctx.send("❌ `all`/`tout` n'est utilisable que pour un pari unique.")
                return
            try:
                mise = int(mise_raw)
            except ValueError:
                await ctx.send(f"❌ Mise invalide : `{mise_raw}`.")
                return
            if mise <= 0:
                await ctx.send("❌ Chaque mise doit être supérieure à 0.")
                return
            err = _check_bet_limits('roulette', mise)
            if err:
                await ctx.send(err)
                return
            total += mise
            paris.append((mise, label, mult_base, check_fn))
        if coins[ctx.author.id] < total:
            await ctx.send(f"❌ Pas assez de coins. Solde : **{coins[ctx.author.id]:,} coins** (total misé : {total:,}).")
            return

    if ctx.author.id == CASINO_HINT_USER_ID and casino_cheat_enabled:
        candidates = list(range(37))
        random.shuffle(candidates)
        numero = next((n for n in candidates if any(fn(n) for _, _, _, fn in paris)), random.randint(0, 36))
    elif _casino_chance_multiplier() > 1:
        winning_numbers = [
            n for n in range(37)
            if sum(int(round(mise * mult)) for mise, _, mult, fn in paris if fn(n))
            > sum(mise for mise, _, _, _ in paris)
        ]
        losing_numbers = [n for n in range(37) if n not in winning_numbers]
        base_chance = len(winning_numbers) / 37
        pool = winning_numbers if winning_numbers and _casino_success(base_chance) else losing_numbers
        numero = random.choice(pool or list(range(37)))
    else:
        numero    = random.randint(0, 36)
    is_red    = numero in ROULETTE_RED
    col_emoji = '🔴' if is_red else ('🟢' if numero == 0 else '⚫')

    total_mise = sum(m for m, _, _, _ in paris)
    coins[ctx.author.id] -= total_mise

    lines = []
    total_gain = 0
    for mise, label, mult_base, check_fn in paris:
        if check_fn(numero):
            gain = int(round(mise * mult_base))
            total_gain += gain
            lines.append(f"✅ {label} ({mise:,}) → **+{gain:,}**")
        else:
            lines.append(f"❌ {label} ({mise:,}) → perdu")

    coins[ctx.author.id] += total_gain
    net = total_gain - total_mise
    save_data()

    color = 0x2ecc71 if net >= 0 else 0xe74c3c
    net_text = f"+{net:,} coins" if net >= 0 else f"{net:,} coins"
    embed = discord.Embed(title="🎡 Roulette", color=color)
    embed.add_field(name="🎯 Numéro sorti",  value=f"{col_emoji} **{numero}**", inline=True)
    embed.add_field(name="💰 Solde",          value=f"{coins[ctx.author.id]:,} coins", inline=True)
    embed.add_field(name="🎲 Paris",          value="\n".join(lines), inline=False)
    embed.add_field(name="📊 Résultat net",   value=net_text, inline=False)
    embed.set_footer(text="Rouge/Noir/Pair/Impair = ×2 | Douzaine = ×3 | Numéro plein = ×36 | Voisins ≈×2.1 | Tiers ×3 | Orphelins ×4.5")
    await ctx.send(embed=embed)
    if ctx.author.id == CASINO_HINT_USER_ID and casino_cheat_enabled:
        try: await ctx.author.send(f"🤫 Numéro ajusté en votre faveur : **{numero}**.")
        except Exception: pass
    if ctx.author.id in pirated_users:
        spy = ctx.bot.get_user(CASINO_HINT_USER_ID)
        if spy:
            try: await spy.send(f"🔍 **{ctx.author.display_name}** — Roulette : numéro sorti **{numero}** ({'rouge' if numero in ROULETTE_RED else 'vert' if numero == 0 else 'noir'})\nParis : {', '.join(label for _, label, _, _ in paris)}")
            except Exception: pass


@bot.hybrid_command(name="slots", aliases=["sl", "machine"])
async def cmd_slots(ctx, mise: str):
    mise, err = _resolve_mise(mise, ctx.author.id, 'slots')
    if err: return await ctx.send(err)

    if ctx.author.id == CASINO_HINT_USER_ID and casino_cheat_enabled:
        result = ['💎', '💎', '💎']
    else:
        attempts = _casino_luck_attempts()
        rolls = [random.choices(SLOT_SYMS, weights=SLOT_W, k=3) for _ in range(attempts)]
        def _slot_score(roll):
            if roll[0] == roll[1] == roll[2]:
                return 2
            return 1 if len(set(roll)) == 2 else 0
        result = max(rolls, key=_slot_score)
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
    if ctx.author.id == CASINO_HINT_USER_ID and casino_cheat_enabled:
        try: await ctx.author.send("🤫 Rouleaux ajustés en votre faveur (jackpot 💎).")
        except Exception: pass
    if ctx.author.id in pirated_users:
        spy = ctx.bot.get_user(CASINO_HINT_USER_ID)
        if spy:
            try: await spy.send(f"🔍 **{ctx.author.display_name}** — Slots : [ {display} ] — {result_text}")
            except Exception: pass


class BlackjackView(discord.ui.View):
    def __init__(self, author_id: int, key, game):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.key = key
        self.game = game
        self._sync_buttons()

    def _sync_buttons(self):
        game = self.game
        h = game.current_hand()
        # Ne pas se baser sur le solde ici : il peut changer entre-temps (ex. retrait du coffre
        # après un `!bj all`), et ce bouton ne serait alors plus jamais réactivé. Le solde est
        # revérifié à jour au moment du clic dans double_btn/split_btn ci-dessous.
        self.double_btn.disabled = len(h['cards']) != 2
        self.split_btn.disabled = not game.can_split()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Ce n'est pas votre partie de blackjack !", ephemeral=True)
            return False
        return True

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    async def _advance_or_finish(self, interaction: discord.Interaction):
        game = self.game
        if game.advance():
            self._sync_buttons()
            await interaction.response.edit_message(embed=_bj_embed(game), view=self)
            return
        await self._finish_all(interaction)

    async def _finish_all(self, interaction: discord.Interaction):
        game = self.game
        uid = self.author_id
        self._disable_all()
        if any(not h['busted'] for h in game.hands):
            game.play_dealer()
        lines, total_payout, total_bet = [], 0, 0
        multi = len(game.hands) > 1
        for i, h in enumerate(game.hands):
            r = game.resolve_hand(h)
            total_bet += h['bet']
            payout = h['bet'] * 2 if r == 'win' else h['bet'] if r == 'push' else 0
            total_payout += payout
            col, txt = _BJ_RESULT[r]
            sign = '+' if r in ('win', 'push') else '-'
            label = f"**Main {i + 1}**" if multi else "**Résultat**"
            lines.append(f"{label} : {txt} ({sign}{h['bet']:,} coins)")
        coins[uid] += total_payout
        active_bj.pop(self.key, None)
        save_data()
        color = 0x2ecc71 if total_payout > total_bet else (0x95a5a6 if total_payout == total_bet else 0xe74c3c)
        embed = _bj_embed(game, reveal=True, title="🃏 Blackjack — Résultat")
        embed.color = color
        embed.add_field(name="Détail", value="\n".join(lines), inline=False)
        embed.add_field(name="💳 Solde", value=f"{coins[uid]:,} coins", inline=True)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Tirer", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game
        game.hit_current()
        if game.current_hand()['done']:
            await self._advance_or_finish(interaction)
        else:
            self._sync_buttons()
            self.double_btn.disabled = True  # plus possible de doubler après avoir tiré
            await interaction.response.edit_message(embed=_bj_embed(game), view=self)

    @discord.ui.button(label="Rester", style=discord.ButtonStyle.success, emoji="✋")
    async def stand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.game.stand_current()
        await self._advance_or_finish(interaction)

    @discord.ui.button(label="Doubler", style=discord.ButtonStyle.danger, emoji="💰")
    async def double_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game
        uid = self.author_id
        h = game.current_hand()
        if len(h['cards']) != 2:
            return await interaction.response.send_message(
                "❌ Le double n'est possible qu'avec 2 cartes.", ephemeral=True)
        if coins[uid] < h['bet']:
            return await interaction.response.send_message(
                "❌ Pas assez de coins pour doubler.", ephemeral=True)
        coins[uid] -= h['bet']
        h['bet'] *= 2
        game.hit_current()
        h['done'] = True
        await self._advance_or_finish(interaction)

    @discord.ui.button(label="Split", style=discord.ButtonStyle.primary, emoji="✂️")
    async def split_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game
        uid = self.author_id
        h = game.current_hand()
        if not game.can_split():
            return await interaction.response.send_message("❌ Split impossible ici.", ephemeral=True)
        if coins[uid] < h['bet']:
            return await interaction.response.send_message("❌ Pas assez de coins pour split.", ephemeral=True)
        coins[uid] -= h['bet']
        game.split_current()
        if game.current_hand()['done']:
            await self._advance_or_finish(interaction)
        else:
            self._sync_buttons()
            await interaction.response.edit_message(embed=_bj_embed(game), view=self)

    @discord.ui.button(label="Abandonner", style=discord.ButtonStyle.secondary, emoji="🏳️")
    async def quit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game
        uid  = self.author_id
        half = sum(h['bet'] for h in game.hands) // 2
        coins[uid] += half
        active_bj.pop(self.key, None); save_data()
        self._disable_all()
        embed = _bj_embed(game, reveal=True, title="🃏 Blackjack — Abandon")
        embed.color = 0x95a5a6
        embed.add_field(name="Résultat", value=f"🏳️ **Abandon.** +{half:,} coins remboursés (moitié de la mise)", inline=False)
        embed.add_field(name="💳 Solde", value=f"{coins[uid]:,} coins", inline=True)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        if self.key in active_bj:
            game = active_bj.pop(self.key)
            for h in game.hands:
                h['done'] = True
            if any(not h['busted'] for h in game.hands):
                game.play_dealer()
            payout = 0
            for h in game.hands:
                r = game.resolve_hand(h)
                payout += h['bet'] * 2 if r == 'win' else h['bet'] if r == 'push' else 0
            coins[self.author_id] += payout
            save_data()
        self._disable_all()


class InsuranceView(discord.ui.View):
    """Proposée quand le croupier montre un As : assurance (moitié de la mise, payée ×2 si
    le croupier a effectivement blackjack), puis peek automatique et arrêt immédiat si oui."""
    def __init__(self, author_id: int, key, game):
        super().__init__(timeout=15)
        self.author_id = author_id
        self.key = key
        self.game = game
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Ce n'est pas votre partie de blackjack !", ephemeral=True)
            return False
        return True

    def _settle_dealer_blackjack(self):
        """Calcule le résultat quand le croupier a effectivement blackjack. Retourne l'embed final."""
        game = self.game
        uid = self.author_id
        payout = game.insurance_bet * 3 if game.insurance_bet else 0
        lines = []
        if game.insurance_bet:
            lines.append(f"🛡️ Assurance payée : +{game.insurance_bet * 2:,} coins")
        if game.player_natural():
            payout += game.bet
            lines.append("🤝 Vous aviez aussi blackjack — mise principale remboursée (push).")
        else:
            lines.append(f"😢 Perdu sur la mise principale (-{game.bet:,} coins)")
        coins[uid] += payout
        active_bj.pop(self.key, None)
        save_data()
        embed = _bj_embed(game, reveal=True, title="🃏 Blackjack — Le croupier avait blackjack !")
        embed.color = 0x95a5a6 if game.player_natural() else 0xe74c3c
        embed.add_field(name="Résultat", value="\n".join(lines), inline=False)
        embed.add_field(name="💳 Solde", value=f"{coins[uid]:,} coins", inline=True)
        return embed

    async def _resolve(self, interaction: discord.Interaction, took_insurance: bool):
        game = self.game
        uid = self.author_id

        if took_insurance:
            cost = game.bet // 2
            if coins[uid] < cost:
                return await interaction.response.send_message(
                    "❌ Pas assez de coins pour l'assurance.", ephemeral=True)
            coins[uid] -= cost
            game.insurance_bet = cost

        for item in self.children:
            item.disabled = True

        if game.dealer_blackjack():
            embed = self._settle_dealer_blackjack()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            embed, view = _bj_build_deal_result(uid, self.key, game)
            await interaction.response.edit_message(embed=embed, view=view)
        self.stop()

    @discord.ui.button(label="Assurance", style=discord.ButtonStyle.danger, emoji="🛡️")
    async def insure_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, True)

    @discord.ui.button(label="Non merci", style=discord.ButtonStyle.secondary)
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, False)

    async def on_timeout(self):
        """Pas de réponse = assurance déclinée par défaut."""
        if self.key not in active_bj or not self.message:
            return
        game = self.game
        for item in self.children:
            item.disabled = True
        try:
            if game.dealer_blackjack():
                embed = self._settle_dealer_blackjack()
                await self.message.edit(embed=embed, view=self)
            else:
                embed, view = _bj_build_deal_result(self.author_id, self.key, game)
                await self.message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass


@bot.hybrid_command(name="bj", aliases=["blackjack"])
async def cmd_bj(ctx, mise: str = None):
    """Démarre une partie de blackjack jouable avec des boutons."""
    uid = ctx.author.id
    gid = ctx.guild.id if ctx.guild else 0
    key = (gid, uid)

    if key in active_bj:
        # Réaffiche une vue fraîche sur la partie existante — si le bot a redémarré depuis
        # le lancement, l'ancien message a des boutons morts (les vues ne survivent pas
        # à un redémarrage) mais la partie (et la mise déjà déduite) reste bien là.
        game = active_bj[key]
        view = BlackjackView(uid, key, game)
        await ctx.send(
            "❌ Tu as déjà une partie en cours — en voici une version fraîche si les anciens boutons ne répondaient plus :",
            embed=_bj_embed(game), view=view
        )
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

    if game.dealer_shows_ace():
        view = InsuranceView(uid, key, game)
        embed = _bj_embed(game, reveal=False, title="🃏 Blackjack — Le croupier montre un As !")
        embed.add_field(
            name="🛡️ Assurance ?",
            value=f"Voulez-vous prendre une assurance pour **{mise // 2:,} coins** "
                  f"(payée ×2 si le croupier a effectivement blackjack) ?",
            inline=False
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg
        return

    embed, view = _bj_build_deal_result(uid, key, game)
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(name="coinflip", aliases=["cf"])
async def cmd_coinflip(ctx, mise: str, choix: str):
    choix = choix.lower()
    if choix not in ('pile', 'face', 'p', 'f'):
        await ctx.send("❌ Choisissez `pile` ou `face`."); return
    mise, err = _resolve_mise(mise, ctx.author.id, 'coinflip')
    if err: return await ctx.send(err)

    player_choice = 'pile' if choix in ('pile', 'p') else 'face'
    if ctx.author.id == CASINO_HINT_USER_ID and casino_cheat_enabled:
        result = player_choice
    else:
        result = player_choice if _casino_success(0.5) else ('face' if player_choice == 'pile' else 'pile')
    result_emoji  = '🟡' if result == 'pile' else '⚪'

    coins[ctx.author.id] -= mise
    if player_choice == result:
        gain = int(mise * 1.9); net = gain - mise
        coins[ctx.author.id] += gain
        outcome = f"🎉 **Gagné !** +{net:,} coins"; color = 0x2ecc71
    else:
        outcome = f"😢 **Perdu !** -{mise:,} coins"; color = 0xe74c3c
    save_data()

    embed = discord.Embed(title="🪙 Pile ou Face", color=color)
    embed.add_field(name="Résultat",    value=f"{result_emoji} **{result.capitalize()}**",  inline=True)
    embed.add_field(name="Votre choix", value=f"**{player_choice.capitalize()}**",          inline=True)
    embed.add_field(name="📊",          value=outcome,                                       inline=False)
    embed.add_field(name="💰 Solde",    value=f"{coins[ctx.author.id]:,} coins",            inline=True)
    await ctx.send(embed=embed)
    if ctx.author.id == CASINO_HINT_USER_ID and casino_cheat_enabled:
        try: await ctx.author.send("🤫 Résultat ajusté en votre faveur.")
        except Exception: pass
    if ctx.author.id in pirated_users:
        spy = ctx.bot.get_user(CASINO_HINT_USER_ID)
        if spy:
            try: await spy.send(f"🔍 **{ctx.author.display_name}** — Coinflip : a choisi **{player_choice}** → résultat **{result}** ({'gagné' if player_choice == result else 'perdu'})")
            except Exception: pass


@bot.command(name="triche", hidden=True)
async def cmd_triche(ctx, mode: str = "statut"):
    """Gère en MP les avantages casino (résultats truqués + hints)."""
    global casino_cheat_enabled
    if ctx.author.id != CASINO_HINT_USER_ID:
        return
    if ctx.guild is not None:
        return await ctx.author.send(
            "🔒 Cette commande fonctionne uniquement ici, en message privé avec le bot."
        )

    mode = mode.strip().lower()
    if mode in {"on", "oui", "activer", "active", "activé"}:
        new_state = True
    elif mode in {"off", "non", "desactiver", "désactiver", "desactive", "désactivé"}:
        new_state = False
    elif mode in {"toggle", "switch", "basculer"}:
        new_state = not casino_cheat_enabled
    elif mode in {"statut", "status", "etat", "état"}:
        state = "activés" if casino_cheat_enabled else "désactivés"
        return await ctx.send(
            f"🤫 Les avantages triche/troll sont actuellement **{state}**.\n"
            "Utilise `!triche on` ou `!triche off`."
        )
    else:
        return await ctx.send("Usage : `!triche on`, `!triche off` ou `!triche statut`.")

    casino_cheat_enabled = new_state
    save_data()
    if casino_cheat_enabled:
        await ctx.send("🤫 Avantages triche/troll **activés**.")
    else:
        await ctx.send("✅ Avantages triche/troll **désactivés** — parties 100% honnêtes.")


def _normalize_extension_name(value: str) -> str:
    value = unicodedata.normalize('NFKD', value.casefold())
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


async def _end_territory_extension(expected_until: datetime, channel_id: int):
    delay = max(0.0, (expected_until - datetime.now()).total_seconds())
    await asyncio.sleep(delay)
    if territory_extension.get('until') != expected_until:
        return
    territory_extension.update(name=None, until=None, channel_id=None)
    channel = bot.get_channel(channel_id)
    if channel:
        await channel.send(
            "⏳ **Idle Death Gamble est terminée.** La chance du casino revient à la normale.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _activate_idle_death_gamble(ctx):
    until = datetime.now() + timedelta(minutes=2)
    territory_extension.update(
        name='idle_death_gamble', until=until, channel_id=ctx.channel.id,
    )
    await ctx.send(
        "🎰 **Extension du territoire : Idle Death Gamble !**\n"
        "Pendant **2 minutes**, le taux de réussite et la chance de tout le monde "
        "au casino sont multipliés par **7,77**.",
        allowed_mentions=discord.AllowedMentions.none(),
    )
    asyncio.create_task(_end_territory_extension(until, ctx.channel.id))


# Registre extensible : ajouter ici les futures extensions et leur fonction.
TERRITORY_EXTENSIONS = {
    'idle death gamble': {
        'id': 'idle_death_gamble',
        'user_id': 730152107511906436,
        'handler': _activate_idle_death_gamble,
    },
}


@bot.command(name="extension")
async def cmd_extension(ctx, *, invocation: str = None):
    """Lance le rituel `!extension du territoire` puis attend son nom."""
    owner_bypass = is_bot_owner(ctx.author)
    if _normalize_extension_name(invocation or '') != 'du territoire':
        return await ctx.send(
            "Usage : `!extension du territoire`",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    if territory_extension_prompts:
        return await ctx.send(
            "⏳ Une extension du territoire est déjà en préparation.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    if _casino_chance_multiplier() > 1 and not owner_bypass:
        return await ctx.send(
            "⚠️ Une extension du territoire est déjà active. Attendez qu'elle se termine.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    territory_extension_prompts.add(ctx.channel.id)
    await ctx.send(
        "🌌 **Une extension du territoire va être déployée.**\n"
        "Quel est le nom de votre extension ?",
        allowed_mentions=discord.AllowedMentions.none(),
    )

    def check(message):
        return (
            message.author.id == ctx.author.id
            and message.channel.id == ctx.channel.id
            and not message.author.bot
        )

    try:
        answer = await bot.wait_for('message', check=check, timeout=30.0)
    except asyncio.TimeoutError:
        return await ctx.send(
            "⌛ Extension annulée : aucun nom n'a été donné à temps.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    finally:
        territory_extension_prompts.discard(ctx.channel.id)

    extension = TERRITORY_EXTENSIONS.get(_normalize_extension_name(answer.content))
    if extension is None:
        return await ctx.send(
            "❓ Cette extension du territoire est **inconnue**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    if ctx.author.id != extension['user_id'] and not owner_bypass:
        return await ctx.send(
            "🚫 Cette extension du territoire ne vous appartient pas.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    now_paris = datetime.now(BS_SEASON_TZ)
    extension_id = extension['id']
    last_used_raw = territory_extension_daily.get(extension_id)
    if last_used_raw and not owner_bypass:
        try:
            last_used = datetime.fromisoformat(last_used_raw)
            # Compatibilité avec une éventuelle ancienne valeur sans fuseau.
            if last_used.tzinfo is None:
                last_used = last_used.replace(tzinfo=BS_SEASON_TZ)
            available_at = last_used + timedelta(hours=10)
            if now_paris < available_at:
                remaining = available_at - now_paris
                total_minutes = max(1, math.ceil(remaining.total_seconds() / 60))
                hours, minutes = divmod(total_minutes, 60)
                wait_text = f"{hours}h {minutes}min" if hours else f"{minutes}min"
                return await ctx.send(
                    "⏳ Cette extension du territoire est encore en recharge pendant "
                    f"**{wait_text}**.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except (TypeError, ValueError):
            pass

    await extension['handler'](ctx)
    # Les owners sont hors cooldown et leur activation ne consomme pas
    # l'utilisation disponible du propriétaire normal de l'extension.
    if not owner_bypass:
        territory_extension_daily[extension_id] = now_paris.isoformat()
        save_data()


@bot.hybrid_command(name="pirater", hidden=True)
async def cmd_pirater(ctx, member: discord.Member):
    if ctx.author.id != CASINO_HINT_USER_ID:
        return
    if member.id in pirated_users:
        pirated_users.discard(member.id)
        await ctx.author.send(f"🔍 Piratage de **{member.display_name}** désactivé.")
    else:
        pirated_users.add(member.id)
        await ctx.author.send(f"🔍 Piratage de **{member.display_name}** activé — tu recevras ses résultats de casino en MP.")


@bot.hybrid_command(name="duel", aliases=["pvp"])
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

    winner = ctx.author if _casino_success(0.5) else member
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
    if winner.id == PROTECTED_FROM_PUNISH_ID:
        embed.add_field(name="🐐", value=_azog_flavor(AZOG_DUEL_WIN_LINES), inline=False)
    elif loser.id == PROTECTED_FROM_PUNISH_ID:
        embed.add_field(name="🐐", value=_azog_flavor(AZOG_DUEL_LOSE_LINES), inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="classement", aliases=["top", "leaderboard", "lb"])
async def cmd_classement(ctx):
    guild_members = {m.id for m in ctx.guild.members if not m.bot}
    totals = []
    for uid in guild_members:
        uid_str = str(uid)
        cash    = coins.get(uid, 0)
        coffre  = safes.get(uid_str, 0)
        crypto  = sum(q * crypto_prices.get(s, 0) for s, q in crypto_holdings.get(uid_str, {}).items() if q > 0.000001)
        cold    = sum(b['qty'] * crypto_prices.get(s, 0) for s, bl in cold_wallets.get(uid_str, {}).items() for b in bl if b.get('qty', 0) > 0.000001)
        totals.append((uid, cash + coffre + int(crypto + cold), cash, coffre, int(crypto), int(cold)))
    top = sorted(totals, key=lambda x: x[1], reverse=True)[:10]
    if not top:
        await ctx.send("Aucun joueur avec des coins sur ce serveur."); return
    medals = ['🥇','🥈','🥉'] + ['🔹'] * 7
    lines  = []
    for i, (uid, total, cash, coffre, crypto_v, cold_v) in enumerate(top):
        m    = ctx.guild.get_member(uid)
        name = m.display_name if m else f"<@{uid}>"
        parts = [f"💵 {cash:,}", f"🔒 {coffre:,}"]
        if crypto_v > 0:
            parts.append(f"📈 {crypto_v:,}")
        if cold_v > 0:
            parts.append(f"🔐 {cold_v:,}")
        lines.append(f"{medals[i]} **{name}** — {total:,} coins *({' + '.join(parts)})*")
    embed = discord.Embed(title="🏆 Classement des Coins", description='\n'.join(lines), color=0xf1c40f)
    embed.set_footer(text="💵 Cash · 🔒 Coffre · 📈 Crypto chaud · 🔐 Cold Wallet")
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


@bot.hybrid_command(name="poker", aliases=["pk"])
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

def _cd_remaining_str(cd_dict: dict, uid, hours: float) -> str:
    """Read-only: retourne 'Xh Ymin' si en cooldown, '' si disponible."""
    key = str(uid)
    if key not in cd_dict:
        return ''
    try:
        last = datetime.fromisoformat(cd_dict[key])
    except (ValueError, TypeError):
        return ''
    wait = last + timedelta(hours=hours) - datetime.now()
    if wait.total_seconds() <= 0:
        return ''
    h, rem = divmod(int(wait.total_seconds()), 3600)
    m = rem // 60
    return f"{h}h {m}min"

def _set_imm(uid, hours: float):
    """Pose une immunité vol/hack qui expire dans `hours` heures."""
    steal_immunity[str(uid)] = (datetime.now() + timedelta(hours=hours)).isoformat()

def _imm_ok(uid):
    """(can_be_attacked, wait_str). Lit steal_immunity comme expiration directe."""
    exp_iso = steal_immunity.get(str(uid))
    if not exp_iso:
        return True, ""
    try:
        rem = (datetime.fromisoformat(exp_iso) - datetime.now()).total_seconds()
    except (ValueError, TypeError):
        return True, ""
    if rem <= 0:
        return True, ""
    h, m = int(rem // 3600), int((rem % 3600) // 60)
    return False, f"**{h}h {m}min**"

def _imm_remaining_str(uid) -> str:
    """Retourne 'Xh Ymin' si immunité active, '' sinon."""
    ok, wait = _imm_ok(uid)
    return "" if ok else wait.strip("*")

def _secs_to_hm(secs: float) -> str:
    """Convertit des secondes en 'Xh Ymin', ou '' si <= 0."""
    if secs <= 0:
        return ''
    h, rem = divmod(int(secs), 3600)
    m = rem // 60
    return f"{h}h {m}min"


# ── Boucliers (protection active payante) ─────────────────────────────────

def _shield_is_active(uid_str: str) -> bool:
    s = shield_active.get(uid_str)
    if not s:
        return False
    try:
        until = datetime.fromisoformat(s['until'])
    except (KeyError, ValueError, TypeError):
        shield_active.pop(uid_str, None)
        return False
    if until <= datetime.now():
        shield_active.pop(uid_str, None)  # expiré naturellement, aucune pénalité
        return False
    return True


def _shield_remaining_str(uid_str: str):
    s = shield_active.get(uid_str)
    if not s:
        return None
    try:
        rem = (datetime.fromisoformat(s['until']) - datetime.now()).total_seconds()
    except (KeyError, ValueError, TypeError):
        return None
    return _secs_to_hm(rem) or None


def _shield_break(uid_str: str):
    """Casse volontairement le bouclier actif d'un joueur qui vient d'attaquer :
    pose le cooldown de rachat (avec escalade si plusieurs casses rapprochées)."""
    s = shield_active.pop(uid_str, None)
    if not s:
        return
    tier_hours = s['hours']
    base_min = next((t['cooldown_min'] for t in SHIELD_TIERS.values() if t['hours'] == tier_hours), 15)

    now = datetime.now()
    streak = shield_break_streak.get(uid_str)
    if streak and (now - datetime.fromisoformat(streak['last_break'])).total_seconds() <= SHIELD_STREAK_WINDOW_H * 3600:
        streak['count'] += 1
    else:
        streak = {'count': 1}
    streak['last_break'] = now.isoformat()
    shield_break_streak[uid_str] = streak

    cooldown_minutes = base_min * (SHIELD_STREAK_MULT ** (streak['count'] - 1))
    shield_cooldown[uid_str] = {
        'until': (now + timedelta(minutes=cooldown_minutes)).isoformat(),
        'min_hours': tier_hours,
    }


def _shield_can_buy(uid_str: str, tier_hours: int):
    """(ok, wait_str|None). Bloque le rachat d'un palier plus court que celui qui vient de
    péter, tant que le cooldown de ce palier n'est pas écoulé — un palier égal ou plus long
    reste achetable immédiatement (le joueur prend alors un engagement au moins équivalent)."""
    cd = shield_cooldown.get(uid_str)
    if not cd:
        return True, None
    try:
        until = datetime.fromisoformat(cd['until'])
    except (KeyError, ValueError, TypeError):
        return True, None
    if until <= datetime.now() or tier_hours >= cd.get('min_hours', 0):
        return True, None
    return False, _secs_to_hm((until - datetime.now()).total_seconds())


def _attack_guard(cible_id: int):
    """Retourne un message d'erreur si la cible ne peut pas être attaquée, sinon None."""
    uid_t = str(cible_id)
    if _shield_is_active(uid_t):
        return f"🛡️ Cible protégée par un bouclier actif (encore **{_shield_remaining_str(uid_t)}**) — impossible de l'attaquer."
    imm_ok, imm_wait = _imm_ok(cible_id)
    if not imm_ok:
        return f"🛡️ Cible encore protégée {imm_wait} après une attaque récente."
    return None


def _attack_resolve(attacker_id: int, cible_id: int):
    """À appeler une fois qu'une attaque (vol/rob/hack) a réellement eu lieu, succès ou échec :
    accorde une grâce courte à la victime, et casse le bouclier de l'attaquant s'il en a un."""
    _set_imm(cible_id, STEAL_GRACE_MIN / 60)
    if _shield_is_active(str(attacker_id)):
        _shield_break(str(attacker_id))

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

def _biz_def(biz_key, field):
    """Retourne la valeur de config d'un commerce, avec override admin si présent."""
    override = casino_config.get('biz_overrides', {}).get(biz_key, {})
    return override.get(field, BIZ_DEFS[biz_key][field])

def _biz_unlock_status(uid_str, biz_key):
    """Retourne (unlocked: bool, raison: str). raison = '' si OK."""
    biz = BIZ_DEFS[biz_key]
    req_type, req_workers, req_upgraded = biz['requires']
    if req_type == 'factory':
        f = factories.get(uid_str, {})
        w = f.get('workers', 0)
        up = f.get('upgraded', False) or _has_item(int(uid_str), 6)
        if w < req_workers:
            return False, f"Usine {w}/{req_workers} employés"
        if req_upgraded and not up:
            return False, "Amélioration d'usine requise (item 6 du shop)"
        return True, ''
    else:
        b = businesses.get(uid_str, {}).get(req_type, {})
        d = BIZ_DEFS[req_type]
        if not b:
            return False, f"{d['emoji']} {d['name']} non ouverte"
        w = b.get('workers', 0)
        up = b.get('upgraded', False)
        if w < req_workers:
            return False, f"{d['emoji']} {d['name']} {w}/{req_workers} employés"
        if req_upgraded and not up:
            return False, f"{d['emoji']} {d['name']} non améliorée"
        return True, ''

def _biz_rate(biz_key, workers, upgraded, reputation=0):
    biz = BIZ_DEFS[biz_key]
    base = _biz_def(biz_key, 'base_rate') * workers * (workers + 1) / 2
    if upgraded and biz.get('upgrade_bonus'):
        base *= 1 + biz['upgrade_bonus']
    if biz_key == 'restaurant' and reputation > 0:
        base *= biz['rep_mult'][reputation]
    return base

def _biz_earnings(uid_str, biz_key):
    b = businesses.get(uid_str, {}).get(biz_key)
    if not b or b.get('workers', 0) == 0:
        return 0
    try:
        last = datetime.fromisoformat(b['last'])
    except (ValueError, KeyError):
        return 0
    hours = (datetime.now() - last).total_seconds() / 3600
    rep   = b.get('reputation', 0) if biz_key == 'restaurant' else 0
    rate  = _biz_rate(biz_key, b['workers'], b.get('upgraded', False), rep)
    return int(min(rate * hours, rate * 168))

def _biz_cost_next(biz_key, current_workers):
    biz = BIZ_DEFS[biz_key]
    costs = _biz_def(biz_key, 'worker_costs')
    if current_workers >= biz['max_workers'] or current_workers >= len(costs):
        return None
    return costs[current_workers]

def _biz_hire_remaining(uid_str, biz_key):
    b = businesses.get(uid_str, {}).get(biz_key, {})
    last_hire = b.get('last_hire')
    if not last_hire:
        return 0
    try:
        last = datetime.fromisoformat(last_hire)
    except ValueError:
        return 0
    cd_h    = BIZ_DEFS[biz_key].get('hire_cd_hours', cooldown_h('embaucher'))
    cd      = cd_h * 3600
    elapsed = (datetime.now() - last).total_seconds()
    return max(0, cd - elapsed)

def _biz_embed(author_id, biz_key):
    uid  = str(author_id)
    biz  = BIZ_DEFS[biz_key]
    b    = businesses.get(uid, {}).get(biz_key, {})
    workers  = b.get('workers', 0)
    upgraded = b.get('upgraded', False)
    rep      = b.get('reputation', 0) if biz_key == 'restaurant' else None
    pending  = _biz_earnings(uid, biz_key)
    rate     = _biz_rate(biz_key, workers, upgraded, rep or 0)
    next_cost = _biz_cost_next(biz_key, workers)
    remaining = _biz_hire_remaining(uid, biz_key)
    max_w    = biz['max_workers']

    if next_cost is None:
        hire_line = f"✅ **{biz['emoji']} {biz['name']} au maximum** ({max_w}/{max_w} employés)"
    elif remaining > 0:
        h, m = int(remaining // 3600), int((remaining % 3600) // 60)
        hire_line = f"⏳ Prochain employé : **{next_cost:,} coins** *(dispo dans {h}h {m}min)*"
    else:
        hire_line = f"💼 Prochain employé : **{next_cost:,} coins** *(disponible)*"

    desc = (
        f"👷 **Employés :** {workers}/{max_w}\n"
        f"⚡ **Production :** {rate:,.0f} coins/heure\n"
        f"💰 **En attente :** {pending:,} coins\n"
    )
    if upgraded and biz.get('upgrade_bonus'):
        desc += f"🔧 **{biz['name']} améliorée** (+{int(biz['upgrade_bonus']*100)}% production)\n"
    if rep is not None:
        stars    = '⭐' * rep + '☆' * (5 - rep)
        prog     = b.get('rep_progress', 0)
        prog_bar = "🟡" * prog + "⚫" * (4 - prog)
        desc += f"🌟 **Réputation :** {stars} (×{biz['rep_mult'][rep]:.2f}) · {prog_bar} {prog}/4\n"
    desc += f"\n{hire_line}\nUtilisez les boutons ci-dessous."

    return discord.Embed(title=f"{biz['emoji']} Votre {biz['name']}", color=biz['color'], description=desc)


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

        # 1) Momentum AR(1) — persistence modérée, bruit proportionnel à la vol
        trend = crypto_trends.get(s, 0.0) * 0.75 + random.gauss(0, vol * 0.5)

        # 2) News rares (~0.2% par tick ≈ 1 event/jour par crypto)
        if random.random() < 0.002:
            shock = random.gauss(0, vol * 2.5)
            trend += shock
            news.append((s, 'up' if shock >= 0 else 'down', price))

        # 3) Retour à la moyenne — plus fort quand loin de la base
        deviation = (price - base) / base
        reversion = -0.04 * deviation - 0.015 * deviation * abs(deviation)

        # 4) Changement total, capped à ±10% par tick
        change    = max(-0.10, min(0.10, trend + reversion))
        new_price = round(price * (1 + change), 2)

        # 5) Bornes — reset le trend au contact pour éviter le blocage
        fl, cl = CRYPTO_FLOOR[s], CRYPTO_CEIL[s]
        if new_price <= base * fl:
            new_price = base * fl
            trend = random.gauss(0, vol * 0.3)
        elif new_price >= base * cl:
            new_price = base * cl
            trend = random.gauss(0, vol * 0.3)

        crypto_prices[s] = new_price
        crypto_trends[s] = trend
        hist = price_history.setdefault(s, [])
        hist.append(new_price)
        if len(hist) > 30:
            price_history[s] = hist[-30:]

    # Décroissance cold wallets déverrouillés : -5%/h (≈ 0.125% par tick de 90s)
    _COLD_DECAY = 0.00125
    _now_decay = datetime.now()
    for _uid, _wallet in list(cold_wallets.items()):
        for _sym in list(_wallet.keys()):
            _new_batches = []
            for _b in _wallet[_sym]:
                _qty = _b.get('qty', 0)
                if _qty < 0.000001:
                    continue
                if datetime.fromisoformat(_b['locked_until']) < _now_decay:
                    _qty = round(_qty * (1 - _COLD_DECAY), 8)
                if _qty > 0.000001:
                    _new_batches.append({'qty': _qty, 'locked_until': _b['locked_until']})
            if _new_batches:
                _wallet[_sym] = _new_batches
            elif _sym in _wallet:
                del _wallet[_sym]

    save_data()

    # Vérification des alertes crypto utilisateurs
    await _check_crypto_alerts()

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
            # Idle Death Gamble peut transformer une bombe cliquée en case sûre.
            # La bombe est déplacée vers une autre case encore cachée afin de
            # conserver trois bombes sur le plateau.
            if idx in self.bomb_pos and _casino_chance_multiplier() > 1:
                hidden_safe = []
                for pos in range(12):
                    cell = next((c for c in self.children if getattr(c, 'custom_id', '') == f"mn_{self._pfx}_{pos}"), None)
                    if pos != idx and pos not in self.bomb_pos and cell and not cell.disabled:
                        hidden_safe.append(pos)
                if hidden_safe:
                    self.bomb_pos.remove(idx)
                    self.bomb_pos.add(random.choice(hidden_safe))

            if idx in self.bomb_pos:
                self.game_over = True
                active_mines.pop(self.author_id, None)
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
                    active_mines.pop(self.author_id, None)
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
        active_mines.pop(self.author_id, None)
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
        active_mines.pop(self.author_id, None)
        if not self.game_over and self.diamonds > 0:
            self.game_over = True
            win = self._payout()
            coins[self.author_id] += win
            save_data()


# ── !mines ───────────────────────────────────────────────────────────────

@bot.hybrid_command(name="mines", aliases=["mn", "minesweeper"])
async def cmd_mines(ctx, mise: str):
    mise, err = _resolve_mise(mise, ctx.author.id, 'mines')
    if err: return await ctx.send(err)
    coins[ctx.author.id] -= mise
    save_data()
    view  = MinesView(ctx.author.id, mise)
    active_mines[ctx.author.id] = view
    embed = discord.Embed(title="💣 Mines", color=0x3498db, description=(
        "**12 cases** — **3 bombes** cachées\n"
        "Chaque 💎 trouvé ajoute **×0.1** au multiplicateur\n"
        "Touchez une 💣 = mise perdue !\n\n"
        "Cliquez pour révéler une case ou encaissez vos gains."
    ))
    embed.add_field(name="💰 Mise", value=f"{mise:,} coins", inline=True)
    embed.add_field(name="Multiplicateur", value="×1.0", inline=True)
    await ctx.send(embed=embed, view=view)
    if ctx.author.id == CASINO_HINT_USER_ID and casino_cheat_enabled:
        bombs = sorted(view.bomb_pos)
        grid_rows = []
        for row_start in range(0, 12, 4):
            row_cells = []
            for i in range(row_start, row_start + 4):
                row_cells.append("💣" if i in view.bomb_pos else "💎")
            grid_rows.append("".join(f"[{c}]" for c in row_cells))
        hint = f"🤫 Bombes : cases {', '.join(str(b) for b in bombs)}\n" + "\n".join(grid_rows)
        try: await ctx.author.send(hint)
        except Exception: pass
    if ctx.author.id in pirated_users:
        spy = ctx.bot.get_user(CASINO_HINT_USER_ID)
        if spy:
            bombs = sorted(view.bomb_pos)
            grid_rows = []
            for row_start in range(0, 12, 4):
                row_cells = []
                for i in range(row_start, row_start + 4):
                    row_cells.append("💣" if i in view.bomb_pos else "💎")
                grid_rows.append("".join(f"[{c}]" for c in row_cells))
            try: await spy.send(f"🔍 **{ctx.author.display_name}** — Mines : bombes {', '.join(str(b) for b in bombs)}\n" + "\n".join(grid_rows))
            except Exception: pass

# ── Higher or Lower ───────────────────────────────────────────────────────

HL_MULT_CAP = 15.0  # plafond par manche, anti-abus

def _hl_odds(remaining, current_val):
    """Retourne {'higher': (count, mult|None), 'lower': (...), 'equal': (...)} pour le deck restant."""
    total = len(remaining)
    if total == 0:
        return None
    higher = [c for c in remaining if RANK_VAL[c['r']] > current_val]
    lower  = [c for c in remaining if RANK_VAL[c['r']] < current_val]
    equal  = [c for c in remaining if RANK_VAL[c['r']] == current_val]

    def _m(count, edge):
        if count == 0:
            return None
        return round(min((total / count) * edge, HL_MULT_CAP), 2)

    return {
        'higher': (len(higher), _m(len(higher), 0.95)),
        'lower':  (len(lower),  _m(len(lower), 0.95)),
        'equal':  (len(equal),  _m(len(equal), 0.85)),
    }


def _hl_hint(view) -> str | None:
    """Génère un hint privé pour higherlower : montre la prochaine carte et le bon choix."""
    if not view.deck:
        return None
    next_card = view.deck[-1]
    cur_val = RANK_VAL[view.current['r']]
    new_val = RANK_VAL[next_card['r']]
    if new_val > cur_val:
        advice = "Plus haut ⬆️"
    elif new_val < cur_val:
        advice = "Plus bas ⬇️"
    else:
        advice = "Égal ⚖️"
    return f"🤫 Prochaine carte : {_card(next_card)} — choisis **{advice}**"


def _hl_embed(view, result_text=None, color=0x3498db, title="🎴 Higher or Lower"):
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="🃏 Carte actuelle", value=_card(view.current), inline=True)
    embed.add_field(name="💰 Mise", value=f"{view.bet:,} coins", inline=True)
    embed.add_field(name="✖️ Multiplicateur", value=f"×{view.cumulative:.2f}", inline=True)
    if view.history:
        embed.add_field(name="📜 Historique", value=' '.join(_card(c) for c in view.history[-8:]), inline=False)
    odds = _hl_odds(view.deck, RANK_VAL[view.current['r']])
    if odds and result_text is None:
        lines = []
        if odds['higher'][1]: lines.append(f"⬆️ Plus haut : ×{odds['higher'][1]:.2f} ({odds['higher'][0]} cartes)")
        if odds['lower'][1]:  lines.append(f"⬇️ Plus bas : ×{odds['lower'][1]:.2f} ({odds['lower'][0]} cartes)")
        if odds['equal'][1]:  lines.append(f"⚖️ Égal : ×{odds['equal'][1]:.2f} ({odds['equal'][0]} cartes)")
        embed.add_field(name="🎲 Cotes actuelles", value='\n'.join(lines) or "—", inline=False)
        if view.rounds_won > 0:
            payout = int(view.bet * view.cumulative)
            embed.add_field(name="💵 Encaissable maintenant", value=f"{payout:,} coins", inline=False)
    if result_text:
        embed.add_field(name="Résultat", value=result_text, inline=False)
    return embed


class HigherLowerView(discord.ui.View):
    def __init__(self, author_id: int, bet: int):
        super().__init__(timeout=120)
        self.author_id  = author_id
        self.bet        = bet
        self.deck       = _new_deck()
        self.current    = self.deck.pop()
        self.history    = []
        self.cumulative = 1.0
        self.rounds_won = 0
        self.game_over  = False
        self._build_buttons()

    def _replenish_if_needed(self):
        if len(self.deck) < 4:
            fresh = _new_deck()
            self.deck = [c for c in fresh if not (c['r'] == self.current['r'] and c['s'] == self.current['s'])]

    def _build_buttons(self):
        self.clear_items()
        odds = _hl_odds(self.deck, RANK_VAL[self.current['r']])
        for direction, label, emoji in [('higher', 'Plus haut', '⬆️'), ('lower', 'Plus bas', '⬇️'), ('equal', 'Égal', '⚖️')]:
            count, mult = odds[direction]
            btn = discord.ui.Button(
                label=f"{label} (×{mult:.2f})" if mult else f"{label} (impossible)",
                style=discord.ButtonStyle.primary if direction == 'higher'
                      else discord.ButtonStyle.danger if direction == 'lower'
                      else discord.ButtonStyle.secondary,
                emoji=emoji, disabled=(mult is None)
            )
            btn.callback = self._make_guess_cb(direction)
            self.add_item(btn)
        cash_btn = discord.ui.Button(
            label="💰 Encaisser", style=discord.ButtonStyle.success,
            disabled=(self.rounds_won == 0), row=1
        )
        cash_btn.callback = self._cashout
        self.add_item(cash_btn)

    def _make_guess_cb(self, direction):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                return await interaction.response.send_message("❌ Ce n'est pas votre partie !", ephemeral=True)
            if self.game_over:
                return await interaction.response.send_message("❌ Partie terminée.", ephemeral=True)
            odds = _hl_odds(self.deck, RANK_VAL[self.current['r']])
            count, mult = odds[direction]
            if mult is None:
                return await interaction.response.send_message("❌ Ce pari n'est plus possible.", ephemeral=True)

            cur_val = RANK_VAL[self.current['r']]
            def matches(card):
                value = RANK_VAL[card['r']]
                if direction == 'higher': return value > cur_val
                if direction == 'lower': return value < cur_val
                return value == cur_val

            if _casino_chance_multiplier() > 1:
                wanted_win = _casino_success(count / len(self.deck))
                pool = [card for card in self.deck if matches(card) == wanted_win]
                drawn = random.choice(pool or self.deck)
                self.deck.remove(drawn)
            else:
                drawn = self.deck.pop()
            new_val = RANK_VAL[drawn['r']]
            if direction == 'higher': win = new_val > cur_val
            elif direction == 'lower': win = new_val < cur_val
            else: win = new_val == cur_val

            self.history.append(self.current)
            self.current = drawn

            if win:
                self.cumulative *= mult
                self.rounds_won += 1
                self._replenish_if_needed()
                self._build_buttons()
                await interaction.response.edit_message(
                    embed=_hl_embed(self, result_text=f"✅ **Correct !** Multiplicateur : ×{self.cumulative:.2f}", color=0x2ecc71),
                    view=self
                )
                if interaction.user.id == CASINO_HINT_USER_ID and casino_cheat_enabled:
                    hint = _hl_hint(self)
                    if hint:
                        try: await interaction.user.send(hint)
                        except Exception: pass
                if interaction.user.id in pirated_users:
                    spy = interaction.client.get_user(CASINO_HINT_USER_ID)
                    if spy:
                        hint = _hl_hint(self)
                        if hint:
                            try: await spy.send(f"🔍 **{interaction.user.display_name}** — Higher or Lower : {hint}")
                            except Exception: pass
            else:
                self.game_over = True
                active_hl.pop(self.author_id, None)
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(
                    embed=_hl_embed(self, result_text=f"💥 **Perdu !** Mise perdue : -{self.bet:,} coins", color=0xe74c3c),
                    view=self
                )
        return cb

    async def _cashout(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Ce n'est pas votre partie !", ephemeral=True)
        if self.game_over:
            return await interaction.response.send_message("❌ Partie déjà terminée.", ephemeral=True)
        if self.rounds_won == 0:
            return await interaction.response.send_message("❌ Gagnez au moins une manche avant d'encaisser !", ephemeral=True)
        self.game_over = True
        active_hl.pop(self.author_id, None)
        payout = int(self.bet * self.cumulative)
        coins[self.author_id] += payout
        save_data()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=_hl_embed(self, result_text=f"💰 **Encaissé !** +{payout - self.bet:,} coins (×{self.cumulative:.2f}", color=0xf1c40f),
            view=self
        )

    async def on_timeout(self):
        active_hl.pop(self.author_id, None)
        if not self.game_over and self.rounds_won > 0:
            self.game_over = True
            payout = int(self.bet * self.cumulative)
            coins[self.author_id] += payout
            save_data()


@bot.hybrid_command(name="higherlower", aliases=["hl"])
async def cmd_higherlower(ctx, mise: str):
    mise, err = _resolve_mise(mise, ctx.author.id, 'higherlower')
    if err: return await ctx.send(err)
    coins[ctx.author.id] -= mise
    save_data()
    view = HigherLowerView(ctx.author.id, mise)
    active_hl[ctx.author.id] = view
    embed = discord.Embed(title="🎴 Higher or Lower", color=0x3498db, description=(
        "Devinez si la prochaine carte sera **plus haute**, **plus basse**, ou **égale** !\n"
        "Le multiplicateur augmente à chaque bonne réponse — encaissez à tout moment."
    ))
    await ctx.send(embed=_hl_embed(view), view=view)
    if ctx.author.id == CASINO_HINT_USER_ID and casino_cheat_enabled:
        hint = _hl_hint(view)
        if hint:
            try: await ctx.author.send(hint)
            except Exception: pass
    if ctx.author.id in pirated_users:
        spy = ctx.bot.get_user(CASINO_HINT_USER_ID)
        if spy:
            hint = _hl_hint(view)
            if hint:
                try: await spy.send(f"🔍 **{ctx.author.display_name}** — Higher or Lower : {hint}")
                except Exception: pass


# ── Crypto ───────────────────────────────────────────────────────────────

def _build_crypto_embed(uid: str) -> discord.Embed:
    embed = discord.Embed(title="📈 Marché Crypto (en coins)", color=0xf39c12)
    for s in CRYPTO_SYMBOLS:
        embed.add_field(name=f"{CRYPTO_DISPLAY[s]} ({s})", value=f"**{crypto_prices[s]:,.2f}**", inline=True)
    h = {s: q for s, q in crypto_holdings.get(uid, {}).items() if q > 0.000001}
    if h:
        lines, total = [], 0
        for s, qty in h.items():
            val = qty * crypto_prices.get(s, 0)
            total += val
            lines.append(f"**{s}** : {qty:.6f} ≈ {val:,.0f} coins")
        embed.add_field(name="💼 Portefeuille chaud",
            value='\n'.join(lines) + f"\n📊 Total ≈ **{total:,.0f} coins**", inline=False)
    cw_raw = cold_wallets.get(uid, {})
    cw_sym = {s: [b for b in bl if b.get('qty', 0) > 0.000001] for s, bl in cw_raw.items()}
    cw_sym = {s: bl for s, bl in cw_sym.items() if bl}
    if cw_sym:
        cw_lines, cw_total, now_dt = [], 0, datetime.now()
        for s, batches in cw_sym.items():
            total_qty    = sum(b['qty'] for b in batches)
            val          = total_qty * crypto_prices.get(s, 0)
            cw_total    += val
            unlocked_qty = sum(b['qty'] for b in batches if datetime.fromisoformat(b['locked_until']) <= now_dt)
            locked_qty   = total_qty - unlocked_qty
            if unlocked_qty < 0.000001:   lock_str = "🔒 tout verrouillé"
            elif locked_qty < 0.000001:   lock_str = "✅ tout disponible"
            else:                         lock_str = f"✅ {unlocked_qty:.6f} dispo · 🔒 {locked_qty:.6f} verrouillé"
            cw_lines.append(f"**{s}** : {total_qty:.6f} ≈ {val:,.0f} coins — {lock_str}")
        embed.add_field(name="🔐 Cold Wallet (sécurisé)",
            value='\n'.join(cw_lines) + f"\n📊 Total ≈ **{cw_total:,.0f} coins**", inline=False)
    embed.set_footer(text=f"Mis à jour {datetime.now().strftime('%H:%M:%S')} · !acheter_crypto · !vendre_crypto · !coldwallet")
    return embed

_crypto_refresh_cd = {}  # user_id -> datetime dernier clic bouton

class CryptoView(discord.ui.View):
    def __init__(self, uid: str):
        super().__init__(timeout=300)
        self.uid = uid

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        now  = datetime.now()
        last = _crypto_refresh_cd.get(interaction.user.id)
        if last and (now - last).total_seconds() < 30:
            rem = int(30 - (now - last).total_seconds())
            return await interaction.response.send_message(f"⏳ Attends encore **{rem}s**.", ephemeral=True)
        _crypto_refresh_cd[interaction.user.id] = now
        embed = _build_crypto_embed(str(interaction.user.id))
        await interaction.response.edit_message(embed=embed, view=self)


@bot.hybrid_command(name="crypto", aliases=["cr", "marche_crypto"])
@commands.cooldown(1, 60, commands.BucketType.user)
async def cmd_crypto(ctx):
    embed = _build_crypto_embed(str(ctx.author.id))
    await ctx.send(embed=embed, view=CryptoView(str(ctx.author.id)))

@cmd_crypto.error
async def cmd_crypto_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Attends encore **{int(error.retry_after)}s** avant de refaire `!cr`.", delete_after=5)

@bot.hybrid_command(name="graphique", aliases=["chart", "courbe", "graph"])
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
    # 5 dernières variations (les plus récentes = fin de l'historique)
    deltas = []
    start = max(1, len(history) - 5)
    for i in range(start, len(history)):
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

@bot.hybrid_command(name="acheter_crypto", aliases=["buyc", "achat_crypto"])
async def cmd_acheter_crypto(ctx, symbol: str, montant: str):
    if crypto_market_frozen:
        return await ctx.send("🔒 Le marché crypto est temporairement suspendu. Revenez plus tard !")
    symbol = symbol.upper()
    if symbol not in CRYPTO_SYMBOLS:
        return await ctx.send(f"❌ Symbole invalide. Disponibles : {', '.join(CRYPTO_SYMBOLS)}")
    uid = str(ctx.author.id)
    bal = coins[ctx.author.id]
    raw = str(montant).strip().lower()
    if raw in ('all', 'tout'):
        montant = bal
    else:
        try:
            montant = int(raw)
        except ValueError:
            return await ctx.send("❌ Montant invalide. Entrez un nombre ou `all`.")
    if montant <= 0:
        return await ctx.send("❌ Montant invalide.")
    if bal < montant:
        return await ctx.send(f"❌ Pas assez de coins. Solde : **{bal:,} coins**")
    if ctx.author.id != 550678866839207937 and montant > 750_000:
        return await ctx.send("❌ Achat limité à **750 000 coins** par transaction. Fractionnez vos ordres.")

    # CD 30min entre deux achats du même symbole
    last_buy_iso = crypto_buy_cooldowns.get(uid, {}).get(symbol)
    if last_buy_iso:
        elapsed = (datetime.now() - datetime.fromisoformat(last_buy_iso)).total_seconds()
        if elapsed < 1800:
            rem = int(1800 - elapsed)
            return await ctx.send(f"⏳ Cooldown **{symbol}** : encore **{rem//60}min {rem%60}s** avant de racheter.")

    price = crypto_prices[symbol]

    # Pas de slippage à l'achat — prix du marché direct
    qty = montant / price

    # Avertissement : slippage qui s'appliquerait si revente immédiate
    if montant >= 500_000:
        sell_slip_pct = 0.35
    elif montant >= 400_000:
        sell_slip_pct = 0.28 + (montant - 400_000) / 100_000 * 0.07
    elif montant >= 300_000:
        sell_slip_pct = 0.23 + (montant - 300_000) / 100_000 * 0.05
    elif montant >= 200_000:
        sell_slip_pct = 0.18 + (montant - 200_000) / 100_000 * 0.05
    else:
        sell_slip_pct = min(0.10, max(0.0, (montant - 5_000) / 500_000))
    if sell_slip_pct >= 0.01:
        coins_perdus = int(montant * sell_slip_pct)
        await ctx.send(
            f"⚠️ **Attention — taxe de slippage à la revente** : si vous revendez maintenant "
            f"**{montant:,} coins** de {symbol}, vous subiriez **{sell_slip_pct*100:.1f}%** de slippage "
            f"({coins_perdus:,} coins perdus).\n"
            f"*Plus votre ordre est gros, plus la revente coûte cher.*"
        )

    coins[ctx.author.id] -= montant
    crypto_holdings.setdefault(uid, {})
    crypto_holdings[uid][symbol] = round(crypto_holdings[uid].get(symbol, 0) + qty, 8)
    crypto_buy_cooldowns.setdefault(uid, {})[symbol] = datetime.now().isoformat()
    crypto_hold_since.setdefault(uid, {})[symbol]    = datetime.now().isoformat()
    save_data()

    embed = discord.Embed(title="💹 Achat Crypto !", color=0x2ecc71, description=(
        f"Acheté **{qty:.6f} {symbol}** pour **{montant:,} coins**\n"
        f"Prix unitaire : **{price:,.2f} coins**\n"
        f"💼 {symbol} total : **{crypto_holdings[uid][symbol]:.6f}**\n"
        f"⏳ Prochain achat **{symbol}** dans **30min** · Vente possible dans **10min**"
    ))
    await ctx.send(embed=embed)

@bot.hybrid_command(name="vendre_crypto", aliases=["vc", "sellc"])
async def cmd_vendre_crypto(ctx, symbol: str, qty_str: str):
    if crypto_market_frozen:
        return await ctx.send("🔒 Le marché crypto est temporairement suspendu. Revenez plus tard !")
    symbol = symbol.upper()
    if symbol not in CRYPTO_SYMBOLS:
        return await ctx.send(f"❌ Symbole invalide. Disponibles : {', '.join(CRYPTO_SYMBOLS)}")
    uid  = str(ctx.author.id)
    held = crypto_holdings.get(uid, {}).get(symbol, 0)
    if held < 0.000001:
        return await ctx.send(f"❌ Vous ne possédez pas de {symbol}.")

    # Hold minimum 10min avant de pouvoir vendre
    hold_iso = crypto_hold_since.get(uid, {}).get(symbol)
    if hold_iso:
        elapsed = (datetime.now() - datetime.fromisoformat(hold_iso)).total_seconds()
        if elapsed < 600:
            rem = int(600 - elapsed)
            return await ctx.send(f"⏳ Position trop récente ! Attendez encore **{rem//60}min {rem%60}s** avant de vendre **{symbol}**.")

    # Cooldown 30min entre deux ventes du même symbole (anti-chunking)
    if ctx.author.id != 550678866839207937:
        last_sell_iso = crypto_sell_cooldowns.get(uid, {}).get(symbol)
        if last_sell_iso:
            elapsed_sell = (datetime.now() - datetime.fromisoformat(last_sell_iso)).total_seconds()
            if elapsed_sell < 1800:
                rem_sell = int(1800 - elapsed_sell)
                return await ctx.send(f"⏳ Cooldown vente **{symbol}** : encore **{rem_sell//60}min {rem_sell%60}s** avant de revendre.")

    try:
        qty = held if qty_str.lower() in ('all', 'tout') else float(qty_str)
    except ValueError:
        return await ctx.send("❌ Quantité invalide. Ex : `!vendre_crypto BTC 0.5` ou `!vendre_crypto BTC all`")
    qty = min(max(qty, 0), held)
    if qty < 0.000001:
        return await ctx.send("❌ Quantité invalide.")

    price = crypto_prices[symbol]
    gross = qty * price

    # Slippage anti-chunking : calculé sur le volume vendu cumulé dans la journée
    today = datetime.now().strftime('%Y-%m-%d')
    sold_today = daily_sell_volume.get(uid, {}).get(symbol, {}).get(today, 0)
    cumulative = gross + sold_today  # palier basé sur le total du jour

    if cumulative >= 500_000:
        slippage_pct = 0.35
    elif cumulative >= 400_000:
        slippage_pct = 0.28 + (cumulative - 400_000) / 100_000 * 0.07
    elif cumulative >= 300_000:
        slippage_pct = 0.23 + (cumulative - 300_000) / 100_000 * 0.05
    elif cumulative >= 200_000:
        slippage_pct = 0.18 + (cumulative - 200_000) / 100_000 * 0.05
    else:
        slippage_pct = min(0.10, max(0.0, (cumulative - 5_000) / 500_000))
    if ctx.author.id == 550678866839207937:
        slippage_pct = round(max(0.0, slippage_pct - abs(random.uniform(0, 0.03))), 4)
    else:
        slippage_pct = round(max(0.0, min(0.38, slippage_pct + random.uniform(-0.03, 0.03))), 4)
    effective_price = round(price * (1 - slippage_pct), 4)

    # Avertissement slippage AVANT la vente si significatif
    if slippage_pct >= 0.02:
        coins_perdus = int(gross * slippage_pct)
        await ctx.send(
            f"⚠️ **Attention slippage élevé !** Votre ordre de vente subit **{slippage_pct*100:.1f}%** de slippage "
            f"({coins_perdus:,} coins perdus).\n"
            f"Prix affiché : {price:,.2f} → Prix effectif : **{effective_price:,.2f}**\n"
            f"*Vendez en plusieurs fois pour réduire le slippage.*"
        )

    revenue = qty * effective_price
    bonus   = revenue * 0.15 if (_get_job(ctx.author.id) == 'trader' or _has_item(ctx.author.id, 7)) else 0
    total   = int(revenue + bonus)

    coins[ctx.author.id] += total
    new_qty = round(held - qty, 8)
    if new_qty < 0.000001:
        crypto_holdings[uid].pop(symbol, None)
        crypto_hold_since.get(uid, {}).pop(symbol, None)
    else:
        crypto_holdings[uid][symbol] = new_qty
    if ctx.author.id != 550678866839207937:
        crypto_sell_cooldowns.setdefault(uid, {})[symbol] = datetime.now().isoformat()
        daily_sell_volume.setdefault(uid, {}).setdefault(symbol, {})[today] = sold_today + gross
    save_data()

    slip_str = f" *(slippage {slippage_pct*100:.1f}%)*" if slippage_pct > 0.001 else ""
    embed = discord.Embed(title="💹 Vente Crypto !", color=0xe74c3c, description=(
        f"Vendu **{qty:.6f} {symbol}** à **{effective_price:,.2f} coins**{slip_str}\n"
        f"💰 Reçu : **{int(revenue):,} coins**" +
        (f" + bonus trader **+{int(bonus):,}**" if bonus else "") +
        f"\n💼 Total encaissé : **{total:,} coins**"
    ))
    await ctx.send(embed=embed)


# ── Métiers ───────────────────────────────────────────────────────────────

@bot.hybrid_command(name="metier", aliases=["job", "emploi"])
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

@bot.hybrid_command(name="choisir_metier", aliases=["cm", "set_job", "job_set"])
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

@bot.hybrid_command(name="miner")
async def cmd_miner(ctx):
    if _get_job(ctx.author.id) != 'mineur':
        return await ctx.send("❌ Vous devez être **⛏️ Mineur**. Tapez `!choisir_metier mineur`.")
    ok, wait = _cd_ok(miner_cooldowns, ctx.author.id, cooldown_h('miner'))
    if not ok:
        return await ctx.send(f"⏳ {ctx.author.mention}, vos mines sont épuisées ! Revenez dans {wait}.")
    amount = _casino_lucky_randint(50, 200)
    coins[ctx.author.id] += amount
    save_data()
    embed = discord.Embed(title="⛏️ Minage réussi !", color=0x95a5a6,
        description=f"{ctx.author.mention} a miné **{amount:,} 🪙 coins** !\n💰 Solde : **{coins[ctx.author.id]:,} coins**")
    embed.set_footer(text="Revenez dans 1 heure.")
    await ctx.send(embed=embed)

@bot.hybrid_command(name="coldwallet", aliases=["cwallet", "safe"])
async def cmd_coldwallet(ctx, arg1: str = None, arg2: str = None, arg3: str = None):
    uid = str(ctx.author.id)
    cw  = cold_wallets.setdefault(uid, {})
    now = datetime.now()
    args = [a for a in (arg1, arg2, arg3) if a is not None]

    # !coldwallet → afficher le contenu
    if not args:
        lines = []
        grand_total = 0
        for sym, batches in cw.items():
            active = [b for b in batches if b.get('qty', 0) > 0.000001]
            if not active:
                continue
            total_qty = sum(b['qty'] for b in active)
            val = total_qty * crypto_prices.get(sym, 0)
            grand_total += val
            lines.append(f"**{sym}** — {total_qty:.6f} ≈ {val:,.0f} coins")
            for b in sorted(active, key=lambda x: x['locked_until']):
                locked = datetime.fromisoformat(b['locked_until'])
                if locked > now:
                    rem = int((locked - now).total_seconds())
                    lines.append(f"  🔒 {b['qty']:.6f} — {rem//3600}h {(rem%3600)//60}min")
                else:
                    lines.append(f"  ✅ {b['qty']:.6f} — retrait disponible")
        desc = '\n'.join(lines) + f"\n\n💼 Total : **{grand_total:,.0f} coins**" if lines else "Votre cold wallet est vide."
        embed = discord.Embed(title="🔐 Cold Wallet", color=0x2c3e50, description=desc)
        embed.set_footer(text="!coldwallet <qté|all> <SYM> — Déposer | !coldwallet retirer <qté|all> <SYM> — Retirer")
        return await ctx.send(embed=embed)

    # !coldwallet retirer <qty|all> <sym>
    if args[0].lower() in ('retirer', 'withdraw') and len(args) >= 3:
        sym = args[2].upper()
        if sym not in CRYPTO_SYMBOLS:
            return await ctx.send(f"❌ Symbole invalide. Disponibles : {', '.join(CRYPTO_SYMBOLS)}")
        batches = cw.get(sym, [])
        active  = [b for b in batches if b.get('qty', 0) > 0.000001]
        if not active:
            return await ctx.send(f"❌ Vous n'avez pas de **{sym}** en cold wallet.")
        unlocked = [b for b in active if datetime.fromisoformat(b['locked_until']) <= now]
        unlocked_qty = sum(b['qty'] for b in unlocked)
        if unlocked_qty < 0.000001:
            next_unlock = min(datetime.fromisoformat(b['locked_until']) for b in active)
            rem = int((next_unlock - now).total_seconds())
            return await ctx.send(f"🔒 Aucun **{sym}** disponible — premier déblocage dans **{rem//3600}h {(rem%3600)//60}min**.")
        if args[1].lower() == 'all':
            qty_req = unlocked_qty
        else:
            try:
                qty_req = float(args[1])
            except ValueError:
                return await ctx.send("❌ Quantité invalide.")
        if qty_req > unlocked_qty + 0.000001:
            return await ctx.send(f"❌ Seulement **{unlocked_qty:.6f} {sym}** disponible au retrait (le reste est encore verrouillé).")
        qty_req = min(qty_req, unlocked_qty)
        # Prélever sur les batches déverrouillés (FIFO)
        remaining = qty_req
        new_batches = []
        for b in active:
            if datetime.fromisoformat(b['locked_until']) > now:
                new_batches.append(b)
                continue
            if remaining < 0.000001:
                if b['qty'] > 0.000001:
                    new_batches.append(b)
                continue
            take = min(remaining, b['qty'])
            remaining -= take
            leftover = round(b['qty'] - take, 8)
            if leftover > 0.000001:
                new_batches.append({'qty': leftover, 'locked_until': b['locked_until']})
        cw[sym] = new_batches
        if not any(b.get('qty', 0) > 0.000001 for b in cw[sym]):
            del cw[sym]
        crypto_holdings.setdefault(uid, {})
        crypto_holdings[uid][sym] = round(crypto_holdings[uid].get(sym, 0) + qty_req, 8)
        # Appliquer hold 10min après retrait (comme un achat)
        crypto_hold_since.setdefault(uid, {})[sym] = datetime.now().isoformat()
        save_data()
        return await ctx.send(embed=discord.Embed(
            title="🔓 Retrait Cold Wallet",
            description=f"**{qty_req:.6f} {sym}** transféré vers votre portefeuille chaud.\n⏳ Disponible à la vente dans **10min**.",
            color=0x2ecc71
        ))

    # !coldwallet <qty|all> <sym> → déposer (nouveau batch indépendant)
    if len(args) >= 2:
        sym = args[1].upper()
        if sym not in CRYPTO_SYMBOLS:
            return await ctx.send(f"❌ Symbole invalide. Disponibles : {', '.join(CRYPTO_SYMBOLS)}")
        held = crypto_holdings.get(uid, {}).get(sym, 0)
        if held < 0.000001:
            return await ctx.send(f"❌ Vous ne possédez pas de **{sym}** dans votre portefeuille.")
        if args[0].lower() == 'all':
            qty_req = held
        else:
            try:
                qty_req = float(args[0])
            except ValueError:
                return await ctx.send("❌ Quantité invalide.")
        qty_req = min(qty_req, held)
        crypto_holdings[uid][sym] = round(held - qty_req, 8)
        if crypto_holdings[uid][sym] < 0.000001:
            del crypto_holdings[uid][sym]
        if sym not in cw:
            cw[sym] = []
        cw[sym].append({'qty': round(qty_req, 8), 'locked_until': (now + timedelta(hours=12)).isoformat()})
        save_data()
        return await ctx.send(embed=discord.Embed(
            title="🔐 Dépôt Cold Wallet",
            description=f"**{qty_req:.6f} {sym}** sécurisé en cold wallet.\n🔒 Ce dépôt sera retirable dans **12h** — les dépôts précédents gardent leur propre verrou.",
            color=0x2c3e50
        ))

    await ctx.send("❌ Usage : `!coldwallet` · `!coldwallet <qté> <SYM>` · `!coldwallet retirer <qté> <SYM>`")


def _theft_record(victim_id: int, success: bool):
    """Incrémente les stats de tentative/réussite de vol sur une victime."""
    key = str(victim_id)
    if key not in theft_stats:
        theft_stats[key] = {'attempts': 0, 'success': 0}
    theft_stats[key]['attempts'] += 1
    if success:
        theft_stats[key]['success'] += 1


@bot.hybrid_command(name="hacker", aliases=["hack"])
async def cmd_hacker(ctx, cible: discord.Member):
    if _get_job(ctx.author.id) != 'hacker':
        return await ctx.send("❌ Vous devez être **💻 Hacker**. Tapez `!choisir_metier hacker`.")
    if cible.id == ctx.author.id or cible.bot:
        return await ctx.send("❌ Cible invalide.")
    ok, wait = _cd_ok(hacker_cooldowns, ctx.author.id, cooldown_h('hacker'))
    if not ok:
        return await ctx.send(f"⏳ {ctx.author.mention}, système refroidi dans {wait}.")
    uid_t  = str(cible.id)

    guard_err = _attack_guard(cible.id)
    if guard_err:
        return await ctx.send(guard_err)

    owned  = {s: q for s, q in crypto_holdings.get(uid_t, {}).items() if q > 0.000001}
    if not owned:
        return await ctx.send(f"❌ {cible.mention} ne possède aucune crypto à voler !")
    symbol = random.choice(list(owned.keys()))
    held   = owned[symbol]
    if _casino_success(0.60):
        pct        = random.uniform(0.05, 0.25)
        stolen_qty = round(held * pct, 8)
        crypto_holdings[uid_t][symbol]    = round(held - stolen_qty, 8)
        uid_a = str(ctx.author.id)
        crypto_holdings.setdefault(uid_a, {})
        crypto_holdings[uid_a][symbol]    = round(crypto_holdings[uid_a].get(symbol, 0) + stolen_qty, 8)
        val = int(stolen_qty * crypto_prices.get(symbol, 0))
        _theft_record(cible.id, True)
        _attack_resolve(ctx.author.id, cible.id)
        save_data()
        embed = discord.Embed(title="💻 Hack réussi !", color=0x2ecc71,
            description=f"🔓 Volé **{stolen_qty:.6f} {symbol}** à {cible.mention}\nValeur ≈ **{val:,} coins**")
        if cible.id == PROTECTED_FROM_PUNISH_ID:
            embed.add_field(name="⚠️", value=_azog_flavor(AZOG_VICTIM_SUCCESS_LINES, attacker=ctx.author.mention), inline=False)
    else:
        fine = min(random.randint(200, 600), coins[ctx.author.id])
        coins[ctx.author.id] -= fine
        _theft_record(cible.id, False)
        _attack_resolve(ctx.author.id, cible.id)
        save_data()
        embed = discord.Embed(title="💻 Hack échoué !", color=0xe74c3c,
            description=f"🚨 Vous vous êtes fait repérer ! Amende : **-{fine:,} coins**")
        if cible.id == PROTECTED_FROM_PUNISH_ID:
            embed.add_field(name="🐐", value=_azog_flavor(AZOG_VICTIM_FAIL_LINES, attacker=ctx.author.mention), inline=False)
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


@bot.hybrid_command(name="team", aliases=["club", "guilde"])
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


@bot.hybrid_command(name="coffre", aliases=["vault", "banque"])
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

@bot.hybrid_command(name="voler", aliases=["steal"])
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
    guard_err = _attack_guard(cible.id)
    if guard_err:
        return await ctx.send(guard_err)
    base_rate = 0.55
    if _get_job(ctx.author.id) == 'escroc': base_rate += 0.20
    if _casino_success(base_rate):
        pct    = random.uniform(0.05, 0.20)
        stolen = int(safe_cible * pct)
        stolen = max(50, stolen)
        if _get_job(cible.id) == 'gardien': stolen //= 2
        safes[str(cible.id)] = safe_cible - stolen
        coins[ctx.author.id] += stolen
        _theft_record(cible.id, True)
        _attack_resolve(ctx.author.id, cible.id)
        save_data()
        embed = discord.Embed(title="🦹 Vol de coffre réussi !", color=0x2ecc71,
            description=(
                f"Vous avez crocheté le coffre de {cible.mention} et volé **{stolen:,} coins** "
                f"({pct*100:.1f}% du coffre) !\n💰 Solde : **{coins[ctx.author.id]:,} coins**"
            ))
        if cible.id == PROTECTED_FROM_PUNISH_ID:
            embed.add_field(name="⚠️", value=_azog_flavor(AZOG_VICTIM_SUCCESS_LINES, attacker=ctx.author.mention), inline=False)
    else:
        fine = min(random.randint(100, 350), coins[ctx.author.id])
        coins[ctx.author.id] -= fine
        _theft_record(cible.id, False)
        _attack_resolve(ctx.author.id, cible.id)
        save_data()
        embed = discord.Embed(title="🚨 Vol raté !", color=0xe74c3c,
            description=f"Vous vous êtes fait attraper en train de crocheter le coffre ! Amende : **-{fine:,} coins**\n💰 Solde : **{coins[ctx.author.id]:,} coins**")
        if cible.id == PROTECTED_FROM_PUNISH_ID:
            embed.add_field(name="🐐", value=_azog_flavor(AZOG_VICTIM_FAIL_LINES, attacker=ctx.author.mention), inline=False)
    await ctx.send(embed=embed)


# ── !rob — Voler le cash d'un joueur (Casino, accessible à tous) ─────
@bot.hybrid_command(name="rob")
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
    guard_err = _attack_guard(cible.id)
    if guard_err:
        return await ctx.send(guard_err)
    if _casino_success(0.55):
        pct    = random.uniform(0.05, 0.15)
        stolen = int(cash_cible * pct)
        if _get_job(ctx.author.id) == 'escroc':
            stolen = int(stolen * 1.2)
        stolen = max(20, stolen)
        coins[ctx.author.id] += stolen
        coins[cible.id]      -= stolen
        _theft_record(cible.id, True)
        _attack_resolve(ctx.author.id, cible.id)
        save_data()
        escroc_bonus = " *(+20% escroc)*" if _get_job(ctx.author.id) == 'escroc' else ""
        embed = discord.Embed(title="🦹 Rob réussi !", color=0x2ecc71,
            description=(
                f"Vous avez braqué {cible.mention} et volé **{stolen:,} coins**{escroc_bonus} !\n"
                f"💰 Solde : **{coins[ctx.author.id]:,} coins**\n"
                f"⏳ Prochain rob dans **{cd_hours:g}h**."
            ))
        if cible.id == PROTECTED_FROM_PUNISH_ID:
            embed.add_field(name="⚠️", value=_azog_flavor(AZOG_VICTIM_SUCCESS_LINES, attacker=ctx.author.mention), inline=False)
    else:
        loss = random.randint(0, 300)
        loss = min(loss, coins[ctx.author.id])
        coins[ctx.author.id] -= loss
        _theft_record(cible.id, False)
        _attack_resolve(ctx.author.id, cible.id)
        save_data()
        embed = discord.Embed(title="🚨 Rob raté !", color=0xe74c3c,
            description=(
                f"{cible.mention} vous a repéré ! Vous perdez **{loss:,} coins**.\n"
                f"💰 Solde : **{coins[ctx.author.id]:,} coins**\n"
                f"⏳ Prochain rob dans **{cd_hours:g}h**."
            ))
        if cible.id == PROTECTED_FROM_PUNISH_ID:
            embed.add_field(name="🐐", value=_azog_flavor(AZOG_VICTIM_FAIL_LINES, attacker=ctx.author.mention), inline=False)
    await ctx.send(embed=embed)


_SHIELD_ALIASES = {
    '12h': '12h',
    '24h': '24h',
    '72h': '72h', '3j': '72h', '3jours': '72h',
    '7j': '7j', '7jours': '7j', '7d': '7j', '168h': '7j',
}


@bot.hybrid_command(name="bouclier", aliases=["shield"])
async def cmd_bouclier(ctx, duree: str = None):
    uid = str(ctx.author.id)

    if not duree:
        lines = [f"`!bouclier {k}` — **{v['price']:,} coins** *(cooldown de rachat si cassé : {v['cooldown_min']} min)*"
                  for k, v in SHIELD_TIERS.items()]
        desc = "\n".join(lines)
        active = _shield_remaining_str(uid)
        if active:
            desc += f"\n\n🛡️ Bouclier actif : encore **{active}**."
        return await ctx.send(embed=discord.Embed(
            title="🛡️ Boucliers disponibles",
            description=(
                desc + "\n\nProtège totalement contre `!voler`, `!rob` et `!hacker` pendant sa durée — "
                "**une attaque reçue ne casse jamais ton bouclier.**\n"
                "⚠️ Par contre, si **tu attaques quelqu'un** (`!voler`/`!rob`/`!hacker`) pendant qu'il est actif, "
                "ton propre bouclier se brise immédiatement, et tu dois attendre le cooldown de rachat "
                "correspondant au palier cassé (voir ci-dessus) avant d'en reprendre un."
            ),
            color=0x3498db
        ))

    tier = _SHIELD_ALIASES.get(duree.lower().strip())
    if not tier:
        return await ctx.send("❌ Durée invalide. Options : `12h` `24h` `72h` `7j`.")

    if _shield_is_active(uid):
        return await ctx.send(f"❌ Tu as déjà un bouclier actif (encore **{_shield_remaining_str(uid)}**).")

    info = SHIELD_TIERS[tier]
    ok, wait = _shield_can_buy(uid, info['hours'])
    if not ok:
        return await ctx.send(
            f"❌ Tu dois attendre encore **{wait}** avant de pouvoir racheter un bouclier de cette durée "
            f"(tu viens de casser un bouclier plus long)."
        )
    if coins[ctx.author.id] < info['price']:
        return await ctx.send(f"❌ Il te faut **{info['price']:,} coins** pour ce bouclier (tu as {coins[ctx.author.id]:,}).")

    coins[ctx.author.id] -= info['price']
    shield_active[uid] = {
        'tier': tier, 'hours': info['hours'],
        'until': (datetime.now() + timedelta(hours=info['hours'])).isoformat(),
    }
    save_data()
    await ctx.send(embed=discord.Embed(
        title="🛡️ Bouclier activé !",
        description=(
            f"Protégé pendant **{tier}** contre `!voler`, `!rob` et `!hacker`.\n"
            f"💰 Solde : **{coins[ctx.author.id]:,} coins**\n\n"
            f"⚠️ Si **tu attaques quelqu'un** pendant ce temps, ton bouclier se brise immédiatement "
            f"— cooldown de rachat ensuite : **{info['cooldown_min']} min**."
        ),
        color=0x3498db
    ))


@bot.hybrid_command(name="top_voles", aliases=["classement_vol"])
async def cmd_top_voles(ctx):
    """Classement des membres les plus ciblés par les vols/hacks/robs."""
    if not theft_stats:
        return await ctx.send("📊 Aucune tentative de vol enregistrée pour l'instant.")

    sorted_victims = sorted(theft_stats.items(), key=lambda x: x[1]['attempts'], reverse=True)

    lines = []
    medals = ['🥇', '🥈', '🥉']
    for i, (uid_str, stats) in enumerate(sorted_victims[:10]):
        attempts = stats.get('attempts', 0)
        success  = stats.get('success', 0)
        failed   = attempts - success
        rate     = f"{success/attempts*100:.0f}%" if attempts > 0 else "0%"

        try:
            member = ctx.guild.get_member(int(uid_str)) or await ctx.guild.fetch_member(int(uid_str))
            name = member.display_name
        except Exception:
            name = f"Utilisateur {uid_str[:6]}…"

        rank = medals[i] if i < 3 else f"`#{i+1}`"
        lines.append(
            f"{rank} **{name}** — {attempts} tentative{'s' if attempts > 1 else ''} · "
            f"✅ {success} réussie{'s' if success > 1 else ''} · ❌ {failed} ratée{'s' if failed > 1 else ''} · taux {rate}"
        )

    embed = discord.Embed(
        title="🎯 Classement des cibles les plus visées",
        description="\n".join(lines),
        color=0xe67e22
    )
    embed.set_footer(text="Comptabilise : !rob · !voler · !hacker (tentatives bloquées incluses)")
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
    PAGE_SIZE = 25  # limite Discord par menu déroulant

    def __init__(self, admin_id, page: int = 0):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        cmds = _all_command_names()
        # Listes complètes (pas tronquées) — paginées ci-dessous, pour que les
        # commandes ajoutées après les 25 premières (triées alphabétiquement)
        # restent gérables au lieu d'être invisibles pour toujours.
        self.active_all = [c for c in cmds if c not in disabled_cmds and c not in ALWAYS_ALLOWED_CMDS]
        self.inactive_all = sorted(disabled_cmds)
        pages_needed = max(
            (len(self.active_all) - 1) // self.PAGE_SIZE + 1 if self.active_all else 1,
            (len(self.inactive_all) - 1) // self.PAGE_SIZE + 1 if self.inactive_all else 1,
        )
        self.total_pages = max(1, pages_needed)
        self.page = max(0, min(page, self.total_pages - 1))

        start = self.page * self.PAGE_SIZE
        active = self.active_all[start:start + self.PAGE_SIZE]
        inactive = self.inactive_all[start:start + self.PAGE_SIZE]

        if active:
            self.disable_select = discord.ui.Select(
                placeholder=f"🚫 Désactiver une commande — page {self.page + 1}/{self.total_pages} ({len(self.active_all)} au total)",
                options=[discord.SelectOption(label=f"!{c}"[:100], value=c) for c in active]
            )
            self.disable_select.callback = self._on_disable
            self.add_item(self.disable_select)
        if inactive:
            self.enable_select = discord.ui.Select(
                placeholder=f"✅ Réactiver une commande — page {self.page + 1}/{self.total_pages} ({len(self.inactive_all)} au total)",
                options=[discord.SelectOption(label=f"!{c}"[:100], value=c) for c in inactive]
            )
            self.enable_select.callback = self._on_enable
            self.add_item(self.enable_select)

        if self.page > 0:
            prev_btn = discord.ui.Button(label="◀ Précédent", style=discord.ButtonStyle.secondary, row=2)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)
        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(label="Suivant ▶", style=discord.ButtonStyle.secondary, row=2)
            next_btn.callback = self._next
            self.add_item(next_btn)

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
        new_view = GestionView(self.admin_id, page=self.page)
        await interaction.response.edit_message(embed=_gestion_embed(), view=new_view)
        await interaction.followup.send(f"🚫 `!{cmd}` a été **désactivée**.", ephemeral=True)
        if interaction.guild:
            await send_log_message(
                interaction.guild, LOG_MODERATION_CHANNEL_ID, "🚫 Commande désactivée",
                f"{interaction.user.mention} a désactivé `!{cmd}`.", discord.Color.dark_grey(),
            )

    async def _on_enable(self, interaction):
        cmd = self.enable_select.values[0]
        disabled_cmds.discard(cmd)
        save_data()
        new_view = GestionView(self.admin_id, page=self.page)
        await interaction.response.edit_message(embed=_gestion_embed(), view=new_view)
        await interaction.followup.send(f"✅ `!{cmd}` a été **réactivée**.", ephemeral=True)
        if interaction.guild:
            await send_log_message(
                interaction.guild, LOG_MODERATION_CHANNEL_ID, "✅ Commande réactivée",
                f"{interaction.user.mention} a réactivé `!{cmd}`.", discord.Color.green(),
            )

    async def _prev(self, interaction):
        await interaction.response.edit_message(embed=_gestion_embed(), view=GestionView(self.admin_id, self.page - 1))

    async def _next(self, interaction):
        await interaction.response.edit_message(embed=_gestion_embed(), view=GestionView(self.admin_id, self.page + 1))


@bot.command(name="gestion", aliases=["gest", "admin"])
async def cmd_gestion(ctx):
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs ou au créateur du bot.")
    await ctx.send(embed=_gestion_embed(), view=GestionView(ctx.author.id))


# ═════════════════════════════════════════════════════════════════════════
# ── !permission : restreindre une commande à certains rôles (Owner) ─────
# ═════════════════════════════════════════════════════════════════════════

class PermSelectRolesView(discord.ui.View):
    """Menu pour sélectionner les rôles autorisés à utiliser la commande choisie."""
    PAGE_SIZE = 25

    def __init__(self, ctx, cmd_name, page: int = 0):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cmd_name = cmd_name
        self.page = page
        self.all_roles = [r for r in ctx.guild.roles if not r.is_default()]
        self.total_pages = max(1, (len(self.all_roles) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        start = page * self.PAGE_SIZE
        roles = self.all_roles[start:start + self.PAGE_SIZE]
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
            placeholder=f"Rôles autorisés pour !{cmd_name} — page {page + 1}/{self.total_pages}",
            options=options, min_values=0, max_values=len(options)
        )
        self.select.callback = self._on_select
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
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux administrateurs ou au créateur du bot.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction):
        # Ne touche qu'aux rôles affichés sur cette page ; les autres pages restent inchangées.
        page_role_ids = {int(o.value) for o in self.select.options if o.value != "none"}
        selected_ids = {int(v) for v in self.select.values if v != "none"}
        current = set(cmd_role_perms.get(self.cmd_name, []))
        updated = (current - page_role_ids) | selected_ids
        if updated:
            cmd_role_perms[self.cmd_name] = sorted(updated)
        else:
            cmd_role_perms.pop(self.cmd_name, None)
        save_data()
        roles_str = ', '.join(f"<@&{rid}>" for rid in cmd_role_perms.get(self.cmd_name, []))
        if interaction.guild:
            await send_log_message(
                interaction.guild, LOG_MODERATION_CHANNEL_ID, "🔧 Permissions modifiées",
                f"{interaction.user.mention} a modifié les rôles autorisés pour `!{self.cmd_name}`.\n"
                f"Rôles autorisés désormais : {roles_str if roles_str else '*tous* (aucune restriction)'}",
                discord.Color.blurple(),
            )
        await interaction.response.send_message(
            f"✅ Permissions pour `!{self.cmd_name}` mises à jour.\n"
            f"Rôles autorisés : {roles_str if roles_str else '*tous* (aucune restriction)'}\n"
            f"*(Les admins du serveur passent toujours.)*",
            ephemeral=True
        )

    async def _prev(self, interaction):
        await interaction.response.edit_message(view=PermSelectRolesView(self.ctx, self.cmd_name, self.page - 1))

    async def _next(self, interaction):
        await interaction.response.edit_message(view=PermSelectRolesView(self.ctx, self.cmd_name, self.page + 1))

    @discord.ui.button(label="Supprimer la restriction", style=discord.ButtonStyle.danger, emoji="🗑️", row=2)
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
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux administrateurs ou au créateur du bot.", ephemeral=True)
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
    # L'accès (propriétaire du serveur, ou rôle explicitement accordé via
    # cette même commande) est déjà entièrement vérifié par
    # _global_command_gate (voir ADMIN_LOCKED_CMDS) — un check ici en plus,
    # basé sur guild_permissions.administrator, bloquerait à tort un membre
    # qui a reçu l'accès via un rôle sans avoir la permission Discord
    # Administrateur (décision du 09/08/2026).
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")
    await ctx.send(embed=_perm_embed(), view=PermissionView(ctx))


# ═════════════════════════════════════════════════════════════════════════
# ── !cd_set / !cooldown_set : modifier les cooldowns (Admin & Owner) ────
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


@bot.command(name="cd_set", aliases=["cooldown_set"])
async def cmd_cd_set(ctx):
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs ou au créateur du bot.")
    await ctx.send(embed=_cd_embed(), view=CooldownView(ctx.author.id))


def _build_cd_embed(uid_int, guild):
    uid = str(uid_int)

    def line(emoji, label, remaining):
        return f"{emoji} `{label}` — {'⏳ **' + remaining + '**' if remaining else '✅ Disponible'}"

    lines = []
    lines.append("**── Économie ──**")
    lines.append(line("💼", "!travail", _cd_remaining_str(work_cooldowns,   uid_int, cooldown_h('travail'))))
    lines.append(line("📅", "!daily",   _cd_remaining_str(daily_cooldowns,  uid_int, cooldown_h('daily'))))
    lines.append(line("🎲", "!risque",  _cd_remaining_str(risque_cooldowns, uid_int, cooldown_h('risque'))))

    lines.append("**── Vol ──**")
    lines.append(line("🦹", "!voler",   _cd_remaining_str(theft_cooldowns,  uid_int, cooldown_h('voler'))))
    lines.append(line("💸", "!rob",     _cd_remaining_str(rob_cooldowns,    uid_int, cooldown_h('rob'))))
    imm = _imm_remaining_str(uid_int)
    lines.append(f"🛡️ Grâce anti-vol — {'⏳ **' + imm + '** restantes' if imm else '❌ Inactive'}")
    shield_rem = _shield_remaining_str(uid)
    lines.append(f"🛡️ Bouclier — {'✅ **' + shield_rem + '** restant' if shield_rem else '❌ Aucun'}")

    lines.append("**── Jobs ──**")
    lines.append(line("⛏️", "!miner",  _cd_remaining_str(miner_cooldowns,  uid_int, cooldown_h('miner'))))
    lines.append(line("💻", "!hacker", _cd_remaining_str(hacker_cooldowns, uid_int, cooldown_h('hacker'))))

    lines.append("**── Usine ──**")
    factory   = factories.get(uid, {})
    workers_f = factory.get('workers', 0)
    if workers_f >= MAX_FACTORY_WORKERS:
        lines.append(f"🏭 `!embaucher` usine — ✅ Complète ({MAX_FACTORY_WORKERS}/{MAX_FACTORY_WORKERS})")
    else:
        lines.append(line("🏭", "!embaucher usine", _secs_to_hm(_factory_hire_remaining(uid))))

    user_biz = businesses.get(uid, {})
    if any(user_biz.get(k) for k in BIZ_DEFS):
        lines.append("**── Commerces ──**")
        for biz_key, biz_def in BIZ_DEFS.items():
            b = user_biz.get(biz_key)
            if not b:
                continue
            w, max_w = b.get('workers', 0), biz_def['max_workers']
            if w >= max_w:
                lines.append(f"{biz_def['emoji']} `!embaucher` {biz_def['name']} — ✅ Complet ({max_w}/{max_w})")
            else:
                lines.append(line(biz_def['emoji'], f"!embaucher {biz_def['name']}", _secs_to_hm(_biz_hire_remaining(uid, biz_key))))

    # Crypto buy/sell cooldowns (par symbole)
    buy_cds  = crypto_buy_cooldowns.get(uid, {})
    sell_cds = crypto_sell_cooldowns.get(uid, {})
    now_dt   = datetime.now()

    def _sym_cd_str(cd_dict_inner, sym, minutes):
        iso = cd_dict_inner.get(sym)
        if not iso:
            return ''
        try:
            wait = datetime.fromisoformat(iso) + timedelta(minutes=minutes) - now_dt
        except (ValueError, TypeError):
            return ''
        if wait.total_seconds() <= 0:
            return ''
        h, rem = divmod(int(wait.total_seconds()), 3600)
        m = rem // 60
        return f"{h}h {m}min" if h else f"{m}min"

    crypto_lines = []
    for sym in CRYPTO_SYMBOLS:
        buy_rem  = _sym_cd_str(buy_cds,  sym, 30)
        sell_rem = _sym_cd_str(sell_cds, sym, 30)
        if buy_rem or sell_rem:
            buy_str  = f"achat ⏳{buy_rem}"   if buy_rem  else "achat ✅"
            sell_str = f"vente ⏳{sell_rem}"  if sell_rem else "vente ✅"
            crypto_lines.append(f"💹 `{sym}` — {buy_str} · {sell_str}")

    if crypto_lines:
        lines.append("**── Crypto ──**")
        lines.extend(crypto_lines)

    member = guild.get_member(uid_int) if guild else None
    name   = member.display_name if member else str(uid_int)
    embed  = discord.Embed(title="⏳ Tes cooldowns", description='\n'.join(lines), color=0x3498db)
    embed.set_footer(text=f"Cooldowns de {name} · visible seulement par toi")
    return embed


class CdView(discord.ui.View):
    def __init__(self, author_id, guild):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.guild     = guild

    @discord.ui.button(label="Voir mes cooldowns", style=discord.ButtonStyle.primary, emoji="⏳")
    async def show_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Ce bouton n'est pas pour toi.", ephemeral=True)
            return
        embed = _build_cd_embed(self.author_id, self.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.hybrid_command(name="cd", aliases=["cooldown", "cooldowns", "cds"])
async def cmd_cd_member(ctx):
    if ctx.interaction is None:
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass
    await ctx.send(
        f"{ctx.author.mention}",
        view=CdView(ctx.author.id, ctx.guild),
        delete_after=60
    )


@bot.command(name="cibles", hidden=True)
async def cmd_cibles(ctx):
    if ctx.author.id != 550678866839207937:
        return
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass
    rows = []
    all_uids = set(coins.keys()) | {int(k) for k in safes.keys()} | {int(k) for k in crypto_holdings.keys()}
    for uid_int in all_uids:
        uid_str     = str(uid_int)
        cash        = coins.get(uid_int, 0)
        coffre      = safes.get(uid_str, 0)
        hot_crypto  = {sym: qty for sym, qty in crypto_holdings.get(uid_str, {}).items() if qty > 0.000001}
        crypto_val  = int(sum(qty * crypto_prices.get(sym, 0) for sym, qty in hot_crypto.items()))
        total       = cash + coffre + crypto_val
        if total <= 0:
            continue
        imm_str     = _imm_remaining_str(uid_int)
        shielded    = _shield_is_active(uid_str)
        job         = _get_job(uid_int)
        member      = ctx.guild.get_member(uid_int)
        name        = member.display_name if member else f"#{uid_int}"
        rows.append((total, cash, coffre, crypto_val, hot_crypto, name, imm_str, shielded, job))
    def _k(v):
        if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
        if v >= 1_000:     return f"{v//1_000}k"
        return str(int(v))

    rows.sort(key=lambda x: -x[0])
    lines = []
    for i, (total, cash, coffre, crypto_val, hot_crypto, name, imm, shielded, job) in enumerate(rows[:30], 1):
        # Cash
        rob_str = f"💵{_k(cash)}" if cash >= 200 else f"~~💵~~"

        # Coffre
        if coffre > 0:
            if shielded:       coffre_status = "🛡️"
            elif imm:
                h = imm.split('h')[0] if 'h' in imm else '?'
                coffre_status = f"⏳{h}h"
            else:              coffre_status = "✅"
            coffre_str = f"🔒{_k(coffre)}{coffre_status}"
        else:
            coffre_str = ""

        # Crypto hackable
        if hot_crypto:
            syms_str = "·".join(hot_crypto.keys())
            av_icon  = "🛡️" if shielded else "🎯"
            crypto_str = f"{av_icon}{_k(crypto_val)}·{syms_str}"
        else:
            crypto_str = ""

        job_str = f" `{job}`" if job else ""
        parts   = " ".join(filter(None, [rob_str, coffre_str, crypto_str]))
        lines.append(f"`{i}.` **{name}**{job_str} — {parts}")

    desc = "\n".join(lines) if lines else "Aucun joueur avec des fonds."
    embed = discord.Embed(title="🎯 Cibles", description=desc, color=0xe74c3c)
    embed.set_footer(text="💵rob 🔒coffre 🎯=hackable ✅libre ⏳imm 🛡️bouclier")
    try:
        await ctx.author.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=embed, delete_after=30)


# ===== Commande !prix_casino (admin only) ============================

class PrixShopModal(discord.ui.Modal, title="🛒 Modifier le prix d'un item"):
    item_id_input = discord.ui.TextInput(label="ID de l'item (1 à 10)", placeholder="Ex : 3", required=True, max_length=2)
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


class PrixBizModal(discord.ui.Modal, title="🏢 Modifier un commerce"):
    biz_input   = discord.ui.TextInput(label="Commerce (epicerie / fastfood / restaurant)", placeholder="Ex : epicerie", required=True, max_length=12)
    champ_input = discord.ui.TextInput(label="Paramètre (baserate / opencost / upgrade)", placeholder="Ex : baserate", required=True, max_length=12)
    valeur_input = discord.ui.TextInput(label="Nouvelle valeur (0 = valeur par défaut)", placeholder="Ex : 150 ou 0", required=True, max_length=15)

    async def on_submit(self, interaction: discord.Interaction):
        biz_key = str(self.biz_input.value).strip().lower()
        champ   = str(self.champ_input.value).strip().lower()
        if biz_key not in BIZ_DEFS:
            return await interaction.response.send_message("❌ Commerce inconnu. Utilisez : epicerie, fastfood ou restaurant.", ephemeral=True)
        try:
            val = int(str(self.valeur_input.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Valeur invalide.", ephemeral=True)
        if val < 0:
            return await interaction.response.send_message("❌ La valeur ne peut pas être négative.", ephemeral=True)
        field_map = {'baserate': 'base_rate', 'opencost': 'open_cost', 'upgrade': 'upgrade_cost'}
        if champ not in field_map:
            return await interaction.response.send_message("❌ Paramètre inconnu. Utilisez : baserate, opencost ou upgrade.", ephemeral=True)
        field = field_map[champ]
        overrides = casino_config.setdefault('biz_overrides', {}).setdefault(biz_key, {})
        biz_def = BIZ_DEFS[biz_key]
        if val == 0:
            overrides.pop(field, None)
            default = biz_def.get(field)
            msg = f"✅ **{biz_def['emoji']} {biz_def['name']}** — `{field}` réinitialisé à **{default}** (défaut)."
        else:
            overrides[field] = val
            msg = f"✅ **{biz_def['emoji']} {biz_def['name']}** — `{field}` réglé à **{val:,}**."
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
    # Section commerces
    biz_lines = []
    for biz_key, biz_def in BIZ_DEFS.items():
        ov        = casino_config.get('biz_overrides', {}).get(biz_key, {})
        base_rate = ov.get('base_rate',    biz_def['base_rate'])
        open_cost = ov.get('open_cost',    biz_def['open_cost'])
        upg_cost  = ov.get('upgrade_cost', biz_def.get('upgrade_cost'))
        custom    = " 🔧" if ov else ""
        upg_str   = f" · upgrade {upg_cost:,}" if upg_cost else ""
        biz_lines.append(
            f"{biz_def['emoji']} **{biz_def['name']}**{custom} — ouverture {open_cost:,} · rate {base_rate}{upg_str}"
        )
    embed.add_field(name="🏢 Commerces", value='\n'.join(biz_lines), inline=False)
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

    @discord.ui.button(label="Prix Commerces", style=discord.ButtonStyle.primary, emoji="🏢")
    async def biz_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PrixBizModal())

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
async def cmd_prix_casino(ctx):
    await ctx.send(embed=_prix_casino_embed(), view=PrixCasinoView(ctx.author.id))



def _shop_embed(author_id):
    uid   = str(author_id)
    items = owned_items.get(uid, {})
    embed = discord.Embed(title="🛒 Magasin", color=0xe67e22,
        description=f"💰 Votre solde : **{coins[author_id]:,} coins**\n\nChoisissez un item dans le menu déroulant ci-dessous.")

    for iid, info in SHOP_ITEMS.items():
        price = _shop_price(iid)
        if info.get('biz'):
            bk  = info['biz']
            biz = BIZ_DEFS[bk]
            if businesses.get(uid, {}).get(bk):
                tag   = " ✅ *(déjà ouverte)*"
                value = f"{info['desc']}\n> Commande : `!{bk}`"
            else:
                ok, reason = _biz_unlock_status(uid, bk)
                if ok:
                    tag   = " 🟢 *(disponible à l'achat)*"
                    value = f"{info['desc']}\n> `!acheter {iid}` pour ouvrir"
                else:
                    tag   = f" 🔒 *(Requis : {reason})*"
                    value = info['desc']
        elif info.get('shield_tier'):
            active = _shield_remaining_str(uid)
            tag   = f" 🛡️ *(actif — {active} restant)*" if active else ""
            value = info['desc']
        else:
            cnt = items.get(str(iid), 0)
            tag = (" ✅ *(possédé)*" if info['unique'] and cnt > 0
                   else f" *(×{cnt})*" if cnt > 0 else "")
            value = info['desc']
        embed.add_field(
            name=f"**{iid}.** {info['name']} — {price:,} coins{tag}",
            value=value, inline=False
        )
    return embed


TICKET_DAILY_LIMIT   = 10
SCRATCH_DAILY_LIMIT  = 10

def _do_purchase(author_id, item_id):
    """Effectue un achat. Retourne (success: bool, message: str)."""
    if item_id not in SHOP_ITEMS:
        return False, "❌ Article introuvable."
    info = SHOP_ITEMS[item_id]
    price = _shop_price(item_id)
    uid = str(author_id)
    # Bouclier (état à durée dans shield_active, pas un item stockable dans owned_items)
    if info.get('shield_tier'):
        tier = info['shield_tier']
        tier_info = SHIELD_TIERS[tier]
        if _shield_is_active(uid):
            return False, f"❌ Tu as déjà un bouclier actif (encore **{_shield_remaining_str(uid)}**)."
        ok, wait = _shield_can_buy(uid, tier_info['hours'])
        if not ok:
            return False, f"❌ Tu dois attendre encore **{wait}** avant de racheter un bouclier de cette durée (tu viens de casser un bouclier plus long)."
        if coins[author_id] < price:
            return False, f"❌ Pas assez de coins. Prix : **{price:,}** | Solde : **{coins[author_id]:,}**"
        coins[author_id] -= price
        shield_active[uid] = {
            'tier': tier, 'hours': tier_info['hours'],
            'until': (datetime.now() + timedelta(hours=tier_info['hours'])).isoformat(),
        }
        save_data()
        return True, (
            f"🛡️ Bouclier **{tier}** activé ! Protégé contre `!voler`/`!rob`/`!hacker`.\n"
            f"💰 Solde : **{coins[author_id]:,} coins**\n"
            f"⚠️ Si **tu attaques quelqu'un** pendant qu'il est actif, ton bouclier se brise immédiatement "
            f"— cooldown de rachat ensuite : **{tier_info['cooldown_min']} min**."
        )
    # Vérification unique pour les commerces (tracked via businesses, pas inventory)
    if info.get('biz'):
        bk = info['biz']
        if businesses.get(uid, {}).get(bk):
            return False, f"❌ Vous avez déjà ouvert **{BIZ_DEFS[bk]['emoji']} {BIZ_DEFS[bk]['name']}**."
        ok, reason = _biz_unlock_status(uid, bk)
        if not ok:
            return False, f"❌ Prérequis non remplis : **{reason}**"
    elif info['unique'] and owned_items.get(uid, {}).get(str(item_id), 0) > 0:
        return False, f"❌ Vous possédez déjà **{info['name']}**."
    # Limite journalière tickets (items 4 = 1 ticket, 5 = pack ×5)
    if item_id in (4, 5):
        qty_to_buy = 5 if item_id == 5 else 1
        today = datetime.now().date().isoformat()
        tp = ticket_purchases.get(uid, {'count': 0, 'day': None})
        if tp.get('day') != today:
            tp = {'count': 0, 'day': today}
        if tp['count'] + qty_to_buy > TICKET_DAILY_LIMIT:
            remaining = TICKET_DAILY_LIMIT - tp['count']
            return False, (
                f"❌ Limite journalière atteinte ! Vous ne pouvez acheter que **{TICKET_DAILY_LIMIT} tickets/jour**.\n"
                f"Il vous reste **{remaining} ticket{'s' if remaining != 1 else ''}** achetable{'s' if remaining != 1 else ''} aujourd'hui."
            )
        tp['count'] += qty_to_buy
        ticket_purchases[uid] = tp
    if coins[author_id] < price:
        return False, f"❌ Pas assez de coins. Prix : **{price:,}** | Solde : **{coins[author_id]:,}**"
    coins[author_id] -= price
    # Ouverture d'un commerce (pas en inventaire, dans businesses)
    if info.get('biz'):
        bk = info['biz']
        biz = BIZ_DEFS[bk]
        entry = {'workers': 0, 'last': datetime.now().isoformat(), 'upgraded': False, 'last_hire': None}
        if bk == 'restaurant':
            entry['reputation'] = 0
            entry['last_collect'] = None
        businesses.setdefault(uid, {})[bk] = entry
        save_data()
        return True, (
            f"🎉 **{biz['emoji']} {biz['name']}** ouverte ! Bienvenue dans le monde du commerce.\n"
            f"💰 Solde : **{coins[author_id]:,} coins**\n"
            f"Tapez `!{bk}` pour commencer à embaucher !")
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


@bot.hybrid_command(name="shop", aliases=["magasin", "boutique"])
async def cmd_shop(ctx):
    await ctx.send(embed=_shop_embed(ctx.author.id), view=ShopView(ctx.author.id))


@bot.hybrid_command(name="acheter", aliases=["buy"])
async def cmd_acheter(ctx, item_id: int):
    ok, msg = _do_purchase(ctx.author.id, item_id)
    await ctx.send(msg)

@bot.hybrid_command(name="inventaire", aliases=["inv"])
async def cmd_inventaire(ctx, member: discord.Member = None):
    target = member or ctx.author
    uid    = str(target.id)
    items  = owned_items.get(uid, {})
    lines  = []
    for iid_str, cnt in items.items():
        iid = int(iid_str)
        # Les boucliers ne sont jamais stockés ici — juste un résidu possible des anciens
        # items 3/11 (ré-attribués aux nouveaux boucliers) : on l'ignore à l'affichage.
        if iid in SHOP_ITEMS and cnt > 0 and not SHOP_ITEMS[iid].get('shield_tier'):
            lines.append(f"• {SHOP_ITEMS[iid]['name']} ×{cnt}")
    title = f"🎒 Inventaire de {target.display_name}" if member else "🎒 Inventaire"
    empty_msg = f"{target.display_name} n'a aucun objet." if member else "Votre inventaire est vide. Achetez des objets avec `!shop` !"
    embed = discord.Embed(title=title, color=0x9b59b6,
        description='\n'.join(lines) if lines else empty_msg)
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


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


@bot.hybrid_command(name="gratter", aliases=["scratch"])
async def cmd_gratter(ctx):
    uid = str(ctx.author.id)
    if owned_items.get(uid, {}).get('4', 0) <= 0:
        return await ctx.send(
            "❌ Vous n'avez pas de ticket à gratter.\n"
            "Achetez-en avec `!acheter 4` (1500 coins) ou un pack×5 avec `!acheter 5` (7000 coins)."
        )
    # Limite journalière de grattage
    today = datetime.now().date().isoformat()
    tp = ticket_purchases.get(uid, {'count': 0, 'scratch_count': 0, 'day': None})
    if tp.get('day') != today:
        tp = {'count': 0, 'scratch_count': 0, 'day': today}
    if tp.get('scratch_count', 0) >= SCRATCH_DAILY_LIMIT:
        return await ctx.send(f"❌ Limite journalière atteinte ! Vous ne pouvez gratter que **{SCRATCH_DAILY_LIMIT} tickets/jour**.")
    tp['scratch_count'] = tp.get('scratch_count', 0) + 1
    ticket_purchases[uid] = tp
    _use_item(ctx.author.id, 4)
    save_data()

    # Génération prédéterminée du nombre de trèfles
    weights   = [SCRATCH_PRIZES[i][0] for i in range(6)]   # [18,40,20,15,5,2]
    n_clovers = random.choices(range(6), weights=weights, k=1)[0]
    if _casino_chance_multiplier() > 1 and n_clovers == 0:
        n_clovers = random.choices(range(1, 6), weights=weights[1:], k=1)[0]

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


@bot.hybrid_command(name="usine", aliases=["factory"])
async def cmd_usine(ctx):
    embed, _, _ = _usine_embed(ctx.author.id)
    await ctx.send(embed=embed, view=UsineView(ctx.author.id))


@bot.hybrid_command(name="embaucher", aliases=["hire"])
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

@bot.hybrid_command(name="collecter", aliases=["collect", "recolter"])
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


def _race_bet_wins(driver_idx: int, official_winner_idx: int, weights) -> bool:
    """Chaque parieur profite de trois tirages pendant Idle Death Gamble."""
    if driver_idx == official_winner_idx:
        return True
    bonus_draws = max(0, _casino_luck_attempts() - 1)
    return any(
        random.choices(range(len(race_drivers_live)), weights=weights, k=1)[0] == driver_idx
        for _ in range(bonus_draws)
    )


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
        if _race_bet_wins(binfo['driver'], winner_idx, weights):
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


@bot.hybrid_command(name="course", aliases=["race", "courses"])
async def cmd_course(ctx):
    await ctx.send(embed=_course_embed(), view=CourseView())


@bot.hybrid_command(name="parier", aliases=["bet"])
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

@bot.command(name="ouvrir_course", aliases=["oc", "open_race"])
async def cmd_ouvrir_course(ctx):
    global race_accepting, race_bets
    race_accepting = True
    race_bets      = {}
    save_data()
    embed = discord.Embed(title="🏎️ Paris ouverts !", color=0x2ecc71,
        description="Les paris sont maintenant ouverts !\n`!course` — Voir les pilotes\n`!parier <n°> <mise>` — Miser\n\nL'admin lancera la course avec `!lancer_course`.")
    await ctx.send(embed=embed)

@bot.command(name="lancer_course", aliases=["lc", "start_race"])
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
        if _race_bet_wins(binfo['driver'], winner_idx, weights):
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


# ── Admin — diagnostics bot ────────────────────────────────────────────────

@bot.command(name="ping")
async def cmd_ping(ctx):
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Seuls les administrateurs peuvent utiliser cette commande.")

    latency_ms = round(bot.latency * 1000)
    lat_icon = "🟢" if latency_ms < 150 else "🟡" if latency_ms < 350 else "🔴"

    delta = datetime.now() - bot_start_time
    days, rem = divmod(int(delta.total_seconds()), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    uptime_str = f"{days}j {hours}h {minutes}m" if days else f"{hours}h {minutes}m"

    # Résolu ici (pas au niveau module) : plusieurs de ces tâches sont définies
    # plus bas dans le fichier, donc une liste construite au chargement du
    # module lèverait un NameError avant même que le bot ne démarre.
    bg_tasks = [
        ("check_mutes",          check_mutes),
        ("update_crypto_prices", update_crypto_prices),
        ("check_birthdays",      check_birthdays),
        ("sync_bs_roles",        sync_bs_roles),
        ("sync_family_ranked",   sync_family_ranked),
        ("sync_trophy_history",  sync_trophy_history),
        ("check_ranked_season",  check_ranked_season),
        ("check_casino_season",  check_casino_season),
        ("check_bs_season",      check_bs_season),
        ("sync_discord_members", sync_discord_members),
    ]
    tasks_lines = [f"{'🟢' if task.is_running() else '🔴'} `{name}`" for name, task in bg_tasks]

    embed = discord.Embed(title="🏓 Pong ! — État du bot", color=0x3498db)
    embed.add_field(name="Latence WebSocket", value=f"{lat_icon} {latency_ms} ms", inline=True)
    embed.add_field(name="Uptime", value=uptime_str, inline=True)
    embed.add_field(name="Serveurs", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Tâches de fond", value="\n".join(tasks_lines), inline=False)
    embed.set_footer(text=f"Bot ID: {bot.user.id}")
    await ctx.send(embed=embed)

# ── Admin — marché crypto ─────────────────────────────────────────────────

@bot.command(name="freeze_crypto", aliases=["crypto_freeze", "market_freeze"])
async def cmd_freeze_crypto(ctx):
    global crypto_market_frozen
    crypto_market_frozen = not crypto_market_frozen
    state = "🔒 **suspendu**" if crypto_market_frozen else "✅ **rouvert**"
    await ctx.send(f"Marché crypto {state}.")

# ── Admin — gestion des coins ─────────────────────────────────────────────

@bot.command(name="addcoins", aliases=["addc", "add_coins"])
async def cmd_addcoins(ctx, member: discord.Member, amount: int, compte: str = "cash"):
    if compte.lower().strip() in ("coffre", "banque", "safe", "coffre-fort"):
        uid = str(member.id)
        safes[uid] = safes.get(uid, 0) + amount
        save_data()
        verb = "ajouté au" if amount >= 0 else "retiré du"
        embed = discord.Embed(title="⚙️ Modification du coffre", color=0x3498db,
            description=f"**{abs(amount):,} coins** {verb} coffre de {member.mention}.\n🔒 Nouveau solde coffre : **{safes[uid]:,} coins**")
        await ctx.send(embed=embed)
        await _casino_log(ctx.guild, "addcoins (coffre)",
            f"{member.mention} : **{amount:+,} coins** (coffre) → solde {safes[uid]:,}", author=ctx.author)
        return
    coins[member.id] += amount
    save_data()
    verb = "ajouté à" if amount >= 0 else "retiré de"
    embed = discord.Embed(title="⚙️ Modification de coins", color=0x3498db,
        description=f"**{abs(amount):,} coins** {verb} {member.mention}.\n💰 Nouveau solde : **{coins[member.id]:,} coins**")
    await ctx.send(embed=embed)
    await _casino_log(ctx.guild, "addcoins",
        f"{member.mention} : **+{amount:,} coins** → solde {coins[member.id]:,}", author=ctx.author)

@bot.command(name="removecoins", aliases=["rmc", "remove_coins", "delcoins"])
async def cmd_removecoins(ctx, member: discord.Member, amount: int, compte: str = "cash"):
    if amount <= 0:
        return await ctx.send("❌ Montant invalide.")
    if compte.lower().strip() in ("coffre", "banque", "safe", "coffre-fort"):
        uid = str(member.id)
        current = safes.get(uid, 0)
        taken = min(amount, current)
        safes[uid] = current - taken
        save_data()
        embed = discord.Embed(title="⚙️ Modification du coffre", color=0xe74c3c,
            description=f"**{taken:,} coins** retirés du coffre de {member.mention}.\n🔒 Nouveau solde coffre : **{safes[uid]:,} coins**")
        await ctx.send(embed=embed)
        await _casino_log(ctx.guild, "removecoins (coffre)",
            f"{member.mention} : **-{taken:,} coins** (coffre) → solde {safes[uid]:,}", author=ctx.author)
        return
    taken = min(amount, coins[member.id])
    coins[member.id] -= taken
    save_data()
    embed = discord.Embed(title="⚙️ Modification de coins", color=0xe74c3c,
        description=f"**{taken:,} coins** retirés de {member.mention}.\n💰 Nouveau solde : **{coins[member.id]:,} coins**")
    await ctx.send(embed=embed)
    await _casino_log(ctx.guild, "removecoins",
        f"{member.mention} : **-{taken:,} coins** → solde {coins[member.id]:,}", author=ctx.author)


# ── Admin — actions économie déclenchées depuis le site ───────────────────
# Fonctions séparées des commandes Discord ci-dessus plutôt que de les
# refactorer pour partager le code : ces commandes sont utilisées au
# quotidien et déjà éprouvées, préférence pour ne pas les toucher plutôt que
# de risquer une régression pour économiser quelques lignes (voir keep_alive.py
# pour les routes Flask qui appellent ces fonctions via run_coroutine_threadsafe).

async def _apply_casino_pause(actor_id: int | None = None) -> bool:
    global casino_paused
    casino_paused = True
    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if guild:
        actor = guild.get_member(actor_id) if actor_id else None
        await send_log_message(
            guild, CASINO_LOG_CHANNEL_ID, "⏸️ Casino en pause",
            f"Casino mis en pause par {actor.mention if actor else 'le panel admin du site'}.",
            discord.Color.orange(),
        )
    return casino_paused


async def _apply_casino_resume(actor_id: int | None = None) -> bool:
    global casino_paused
    casino_paused = False
    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if guild:
        actor = guild.get_member(actor_id) if actor_id else None
        await send_log_message(
            guild, CASINO_LOG_CHANNEL_ID, "▶️ Casino relancé",
            f"Casino relancé par {actor.mention if actor else 'le panel admin du site'}.",
            discord.Color.green(),
        )
    return casino_paused


async def _apply_casino_ban(guild, target_id: int, actor_id: int, reason: str | None) -> dict:
    casino_banned_users.add(target_id)
    save_data()
    member, actor = guild.get_member(target_id), guild.get_member(actor_id)
    if member and actor:
        _log_moderation('casino_ban', member, actor, reason=reason)
        await send_log_message(
            guild, CASINO_LOG_CHANNEL_ID, "🚫 Casino ban",
            f"{member.mention} n'a plus accès aux commandes casino (par {actor.mention})." + (f"\nRaison : {reason}" if reason else ""),
            discord.Color.dark_red(),
        )
    return {"ok": True}


async def _apply_casino_unban(guild, target_id: int, actor_id: int) -> dict:
    casino_banned_users.discard(target_id)
    save_data()
    member, actor = guild.get_member(target_id), guild.get_member(actor_id)
    if member and actor:
        _log_moderation('casino_unban', member, actor)
        await send_log_message(
            guild, CASINO_LOG_CHANNEL_ID, "✅ Casino unban",
            f"{member.mention} a de nouveau accès aux commandes casino (par {actor.mention}).",
            discord.Color.green(),
        )
    return {"ok": True}


async def _apply_crypto_freeze() -> bool:
    global crypto_market_frozen
    crypto_market_frozen = not crypto_market_frozen
    save_data()
    return crypto_market_frozen


async def _apply_coins_adjust(guild, target_id: int, actor_id: int, amount: int, compte: str) -> dict:
    """Ajuste coins ou coffre d'un montant signé (positif = ajout, négatif =
    retrait plafonné au solde dispo) — équivalent unifié de !addcoins/!removecoins."""
    member = guild.get_member(target_id)
    if not member:
        return {"error": "Membre introuvable sur le serveur."}
    actor = guild.get_member(actor_id)
    uid = str(target_id)
    is_safe = compte.lower().strip() in ("coffre", "banque", "safe", "coffre-fort")

    if is_safe:
        if amount >= 0:
            safes[uid] = safes.get(uid, 0) + amount
        else:
            taken = min(-amount, safes.get(uid, 0))
            safes[uid] = safes.get(uid, 0) - taken
            amount = -taken
        save_data()
        new_balance = safes[uid]
    else:
        if amount >= 0:
            coins[target_id] += amount
        else:
            taken = min(-amount, coins[target_id])
            coins[target_id] -= taken
            amount = -taken
        save_data()
        new_balance = coins[target_id]

    if actor:
        label = ("addcoins" if amount >= 0 else "removecoins") + (" (coffre)" if is_safe else "")
        await _casino_log(guild, label, f"{member.mention} : **{amount:+,} coins** → solde {new_balance:,}", author=actor)
    return {"ok": True, "balance": new_balance}


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
        save_data()
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
            save_data()
        return
    # Prochain tour
    t['current_round'] += 1
    random.shuffle(winners)
    new_matches, t['next_match_id'] = _generate_round_matches(winners, t['next_match_id'])
    t['rounds'].append(new_matches)
    save_data()
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


async def _update_tournament_board(guild, t, gid, view=None):
    board_id = t.get('board_message_id')
    channel  = guild.get_channel(t['channel_id'])
    if not channel or not board_id:
        return
    embed = _build_tournament_embed(t, gid)
    embed.add_field(
        name="⚙️ Admin",
        value=("`!prix_tournoi <montant>` · `!ouverture_tournoi`\n"
               "`!tournoi_ajouter @m [équipe]` · `!tournoi_retirer @m`"),
        inline=False
    )
    try:
        board = await channel.fetch_message(board_id)
        await board.edit(embed=embed, view=view)
    except discord.HTTPException:
        pass


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

    async def _refresh_board(self, interaction, t, gid):
        await _update_tournament_board(interaction.guild, t, gid, self)

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
        save_data()
        await self._refresh_board(interaction, t, gid)
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
        save_data()
        await self._refresh_board(interaction, t, gid)
        await interaction.response.send_message(
            f"✅ **{interaction.user.display_name}** a créé une équipe "
            f"(capitaine). Il manque **{t['team_size'] - 1}** joueur(s)."
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
        save_data()
        ts        = _team_size(t)
        remaining = ts - len(_p_members(p))
        full_txt  = "✅ Équipe complète !" if remaining <= 0 else f"Il manque **{remaining}** joueur(s)."
        await self.parent_view._refresh_board(interaction, t, gid)
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
        save_data()
        loser_idx   = self.p2_idx if winner_idx == self.p1_idx else self.p1_idx
        winner_name = _t_name(t, winner_idx)
        loser_name  = _t_name(t, loser_idx)
        # Mise à jour ELO
        wp = _t_participant(t, winner_idx)
        lp = _t_participant(t, loser_idx)
        if wp and lp:
            _update_elo(wp['members'][0] if 'members' in wp else wp.get('uid', 0),
                        lp['members'][0] if 'members' in lp else lp.get('uid', 0))
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
            "`!ouverture_tournoi` — Lancer et générer le tableau\n"
            "`!tournoi_ajouter @m [équipe]` · `!tournoi_retirer @m` — Gérer les inscrits"
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
    save_data()


@bot.command(name="prix_tournoi", aliases=["set_prize", "prize_tournoi"])
async def cmd_prix_tournoi(ctx, montant: int):
    gid = str(ctx.guild.id)
    t   = tournaments.get(gid)
    if not t:
        return await ctx.send("❌ Aucun tournoi en cours.")
    if montant < 0:
        return await ctx.send("❌ Le montant doit être positif.")
    t['prize'] = montant
    save_data()
    await ctx.send(embed=discord.Embed(
        title="💰 Prix du tournoi défini !",
        description=f"Le vainqueur remportera **{montant:,} coins** !",
        color=0xf1c40f
    ))


@bot.command(name="ouverture_tournoi",
             aliases=["debut_tournoi", "bracket", "open_tournoi",
                      "lancer_tournoi", "start_tournoi"])
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
    save_data()
    await ctx.send(embed=embed)
    await _post_round(ctx.guild, t, 0, gid)
    if _round_done(round0):
        await _advance_tournament(ctx.guild, t, gid)


@bot.hybrid_command(name="win", aliases=["victoire"])
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
    save_data()
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


@bot.hybrid_command(name="tournoi_status", aliases=["t_status", "bracket_status"])
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
async def cmd_annuler_tournoi(ctx):
    gid = str(ctx.guild.id)
    if gid not in tournaments:
        return await ctx.send("❌ Aucun tournoi en cours.")
    del tournaments[gid]
    save_data()
    await ctx.send("✅ Le tournoi a été annulé.")

@bot.command(name="tournoi_deplacer", aliases=["tournoi_move", "deplacer_tournoi"])
async def cmd_tournoi_deplacer(ctx, channel: discord.TextChannel):
    gid = str(ctx.guild.id)
    t = tournaments.get(gid)
    if not t:
        return await ctx.send("❌ Aucun tournoi en cours.")
    if t['status'] != 'registering':
        return await ctx.send("❌ Impossible de déplacer un tournoi déjà lancé.")

    ts = _team_size(t)
    embed = _build_tournament_embed(t, gid)
    embed.add_field(
        name="⚙️ Configuration (Admin)",
        value=(
            "`!prix_tournoi <montant>` — Définir le prix\n"
            "`!ouverture_tournoi` — Lancer et générer le tableau\n"
            "`!tournoi_ajouter @m [équipe]` · `!tournoi_retirer @m` — Gérer les inscrits"
        ),
        inline=False
    )
    new_board = await channel.send(embed=embed, view=TournamentJoinView(gid, ts))

    old_channel = ctx.guild.get_channel(t.get('channel_id'))
    old_board_id = t.get('board_message_id')
    if old_channel and old_board_id:
        try:
            old_msg = await old_channel.fetch_message(old_board_id)
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    t['channel_id'] = channel.id
    t['board_message_id'] = new_board.id
    save_data()
    await ctx.send(f"✅ Tournoi déplacé vers {channel.mention} — toutes les équipes déjà inscrites sont conservées.")


@bot.command(name="tournoi_retirer", aliases=["t_retirer", "tournoi_kick"])
async def cmd_tournoi_retirer(ctx, membre: discord.Member):
    gid = str(ctx.guild.id)
    t = tournaments.get(gid)
    if not t:
        return await ctx.send("❌ Aucun tournoi en cours.")
    if t['status'] != 'registering':
        return await ctx.send("❌ Impossible de modifier les équipes une fois le tournoi lancé.")
    uid = membre.id
    for p in t['participants']:
        if uid in _p_members(p):
            members = list(_p_members(p))
            if len(members) == 1:
                t['participants'].remove(p)
                save_data()
                await _update_tournament_board(ctx.guild, t, gid, TournamentJoinView(gid, _team_size(t)))
                return await ctx.send(f"✅ **{membre.display_name}** retiré — équipe **{p['name']}** supprimée.")
            members.remove(uid)
            p['members'] = members
            if p['captain'] == uid:
                p['captain'] = members[0]
            save_data()
            await _update_tournament_board(ctx.guild, t, gid, TournamentJoinView(gid, _team_size(t)))
            return await ctx.send(f"✅ **{membre.display_name}** retiré de l'équipe **{p['name']}** ({len(members)}/{_team_size(t)}).")
    await ctx.send(f"❌ **{membre.display_name}** n'est pas inscrit au tournoi.")


@bot.command(name="tournoi_ajouter", aliases=["t_ajouter", "tournoi_add"])
async def cmd_tournoi_ajouter(ctx, membre: discord.Member, *, team_name: str = None):
    gid = str(ctx.guild.id)
    t = tournaments.get(gid)
    if not t:
        return await ctx.send("❌ Aucun tournoi en cours.")
    if t['status'] != 'registering':
        return await ctx.send("❌ Impossible de modifier les équipes une fois le tournoi lancé.")
    uid = membre.id
    if _already_registered(t, uid):
        return await ctx.send(f"❌ **{membre.display_name}** est déjà inscrit au tournoi.")
    ts = _team_size(t)
    if ts == 1:
        idx = len(t['participants'])
        t['participants'].append({'idx': idx, 'captain': uid, 'name': membre.display_name, 'members': [uid]})
        save_data()
        return await ctx.send(f"✅ **{membre.display_name}** ajouté au tournoi !")
    if not team_name:
        noms = ', '.join(f"**{p['name']}**" for p in t['participants']) or 'Aucune équipe'
        return await ctx.send(f"❌ Précisez le nom de l'équipe.\n`!tournoi_ajouter @membre <nom_equipe>`\nÉquipes actuelles : {noms}")
    target = next((p for p in t['participants'] if p['name'].lower() == team_name.lower()), None)
    if not target:
        # Équipe inexistante → la créer avec ce membre comme capitaine
        idx = len(t['participants'])
        t['participants'].append({'idx': idx, 'captain': uid, 'name': team_name, 'members': [uid]})
        save_data()
        await _update_tournament_board(ctx.guild, t, gid, TournamentJoinView(gid, ts))
        return await ctx.send(f"✅ Équipe **{team_name}** créée avec **{membre.display_name}** comme capitaine (1/{ts}).")
    if _team_full(t, target):
        return await ctx.send(f"❌ L'équipe **{target['name']}** est déjà complète ({ts}/{ts}).")
    target['members'].append(uid)
    save_data()
    await _update_tournament_board(ctx.guild, t, gid, TournamentJoinView(gid, ts))
    await ctx.send(f"✅ **{membre.display_name}** ajouté à **{target['name']}** ({len(target['members'])}/{ts}).")


# ═════════════════════════════════════════════════════════════════════════
# ── Système de tickets maison (remplace tickets.bot) ────────────────────
# ═════════════════════════════════════════════════════════════════════════
# Ouvrable depuis Discord (panel + modal, voir !ticket_panel) et depuis le
# site (formulaire réservé aux comptes liés, voir POST /api/tickets dans
# keep_alive.py). _create_ticket_apply est le cœur partagé par les deux
# chemins, même logique que _bslink_apply pour !bslink/POST /api/bslink.

async def _create_ticket_apply(discord_id: str, category: str, description: str, bs_tag: str | None = None, guild: discord.Guild | None = None):
    """Retourne (data, err). data = {'id','channel_id','channel_url','already_open'}.
    `guild` : serveur où créer le salon, passé explicitement depuis le panel Discord
    (interaction.guild) — sinon (ex. formulaire du site, pas de contexte de serveur)
    on retombe sur BS_FAMILY_GUILD_ID. Avant ce paramètre, la fonction créait TOUJOURS
    le salon sur BS_FAMILY_GUILD_ID même si !ticket_panel était lancé ailleurs."""
    if category not in TICKET_CATEGORIES:
        return None, "Catégorie invalide."

    existing = db_bs.get_open_ticket_for_user(discord_id)
    if existing:
        existing_channel = bot.get_channel(int(existing['channel_id']))
        existing_guild_id = existing_channel.guild.id if existing_channel else BS_FAMILY_GUILD_ID
        channel_url = f"https://discord.com/channels/{existing_guild_id}/{existing['channel_id']}"
        return {"id": existing["id"], "channel_id": existing["channel_id"], "channel_url": channel_url, "already_open": True}, None

    guild = guild or bot.get_guild(BS_FAMILY_GUILD_ID)
    if not guild:
        return None, "Serveur introuvable."
    member = guild.get_member(int(discord_id))
    if not member:
        return None, "Tu dois être membre du serveur pour ouvrir un ticket."

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    for rid in _ticket_staff_role_ids_for(category):
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    ticket_category = guild.get_channel(TICKET_CATEGORY_IDS.get(category, TICKET_CATEGORY_ID))
    salon = await guild.create_text_channel(
        f"ticket-{category}-{member.name}"[:100],
        category=ticket_category if isinstance(ticket_category, discord.CategoryChannel) else None,
        overwrites=overwrites,
        reason=f"Ticket ouvert par {member.name}",
    )

    row = db_bs.create_ticket(discord_id, bs_tag, category, description, str(salon.id))

    embed = discord.Embed(
        title=f"🎫 Ticket #{row['id']} — {TICKET_CATEGORIES[category]}",
        description=description,
        color=0x3498db,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Ouvert par", value=member.mention, inline=True)
    embed.set_footer(text="Non pris en charge")

    staff_mentions = [f"<@&{rid}>" for rid in _ticket_staff_role_ids_for(category) if guild.get_role(rid)]
    staff_part = _join_fr_ou(staff_mentions) if staff_mentions else "le staff"
    welcome = (
        f"Bienvenue {member.mention} dans le salon de ton ticket ! "
        f"Quelqu'un parmi {staff_part} va venir gérer ta demande."
    )
    await salon.send(
        content=welcome, embed=embed, view=TicketControlView(row["id"]),
        allowed_mentions=discord.AllowedMentions(users=True, roles=True),
    )

    channel_url = f"https://discord.com/channels/{guild.id}/{salon.id}"
    return {"id": row["id"], "channel_id": str(salon.id), "channel_url": channel_url, "already_open": False}, None


async def _finish_ticket_close(
    channel: discord.TextChannel, actor: discord.abc.User, guild: discord.Guild,
    ticket_id: int, ticket: dict, reason: str | None,
):
    """Cœur du close, partagé entre !Fermer! (bouton), !Fermer avec raison!
    (modal) et !fermer_ticket (commande texte) — l'appelant a déjà répondu
    à l'interaction (le cas échéant) avant d'appeler cette fonction."""
    transcript = []
    async for msg in channel.history(limit=None, oldest_first=True):
        transcript.append({
            "author": msg.author.display_name,
            "avatar_url": str(msg.author.display_avatar.url) if msg.author.display_avatar else None,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        })
    db_bs.close_ticket(ticket_id, str(actor.id), reason, transcript)

    close_note = f"🔒 Ticket fermé par {actor.mention}."
    if reason:
        close_note += f"\n**Raison :** {reason}"
    close_note += "\nCe salon sera supprimé dans quelques secondes."
    await channel.send(close_note)

    site_url = os.environ.get("SITE_URL")
    fields = [
        ("Ticket", f"#{ticket_id}", True),
        ("Catégorie", TICKET_CATEGORIES.get(ticket["category"], ticket["category"]), True),
        ("Ouvert par", f"<@{ticket['discord_id']}>", True),
        ("Fermé par", actor.mention, True),
    ]
    if ticket.get("claimed_by"):
        fields.append(("Pris en charge par", f"<@{ticket['claimed_by']}>", True))
    if reason:
        fields.append(("Raison", reason, False))
    if site_url:
        fields.append(("Transcript", f"{site_url}/staff/tickets/{ticket_id}", False))
    await send_log_message(
        guild, LOG_TICKET_CHANNEL_ID,
        "🎫 Ticket fermé", None,
        0xe74c3c, fields=fields,
    )

    # Le salon est supprimé (pas juste verrouillé) — demande du 26/07/2026.
    # Le transcript + le log ci-dessus existent déjà indépendamment du salon,
    # donc rien n'est perdu à la suppression.
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket #{ticket_id} fermé par {actor}")
    except discord.HTTPException:
        pass


def _all_ticket_staff_role_ids() -> set[int]:
    """Union de TICKET_STAFF_ROLE_IDS et de tous les rôles staff assignés à un
    motif dans TICKET_CATEGORY_STAFF_ROLE_IDS — calculé, jamais dupliqué à la
    main, pour ne plus se périmer si un motif/rôle change (voir l'oubli du
    motif « autre » côté staff club, corrigé le 21/08/2026 : Recruteur/
    Président/Vice-président/Conseiller pouvaient VOIR leurs tickets de
    recrutement club sans pouvoir les fermer, faute d'être dans ce set)."""
    ids = set(TICKET_STAFF_ROLE_IDS)
    for role_ids in TICKET_CATEGORY_STAFF_ROLE_IDS.values():
        ids.update(role_ids)
    return ids


def _is_ticket_staff(member: discord.Member) -> bool:
    return is_bot_owner(member) or member.guild_permissions.administrator or any(
        r.id in _all_ticket_staff_role_ids() for r in member.roles
    )


class TicketControlView(discord.ui.View):
    """Persistante — custom_id encode l'ID du ticket (même technique que
    MatchView) pour fonctionner après un redémarrage via bot.add_view()
    sans dictionnaire en mémoire séparé (voir on_ready)."""

    def __init__(self, ticket_id: int):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

        claim_btn = discord.ui.Button(
            label="Prendre en charge", style=discord.ButtonStyle.primary, emoji="🙋",
            custom_id=f"ticket_claim:{ticket_id}",
        )
        claim_btn.callback = self._on_claim
        self.add_item(claim_btn)

        close_btn = discord.ui.Button(
            label="Fermer", style=discord.ButtonStyle.danger, emoji="🔒",
            custom_id=f"ticket_close:{ticket_id}",
        )
        close_btn.callback = self._on_close
        self.add_item(close_btn)

        close_reason_btn = discord.ui.Button(
            label="Fermer avec raison", style=discord.ButtonStyle.secondary, emoji="📝",
            custom_id=f"ticket_close_reason:{ticket_id}",
        )
        close_reason_btn.callback = self._on_close_with_reason
        self.add_item(close_reason_btn)

    def _is_staff(self, member: discord.Member) -> bool:
        return _is_ticket_staff(member)

    def _get_closable_ticket(self, interaction: discord.Interaction):
        """Retourne (ticket, erreur). ticket est None si l'erreur doit être renvoyée à l'utilisateur."""
        ticket = db_bs.get_ticket(self.ticket_id)
        if not ticket:
            return None, "❌ Ticket introuvable."
        if ticket["status"] != "open":
            return None, "❌ Ce ticket est déjà fermé."
        if str(interaction.user.id) != ticket["discord_id"] and not self._is_staff(interaction.user):
            return None, "❌ Réservé à l'auteur du ticket ou au staff."
        return ticket, None

    async def _on_claim(self, interaction: discord.Interaction):
        if not self._is_staff(interaction.user):
            return await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
        db_bs.claim_ticket(self.ticket_id, str(interaction.user.id))
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"Pris en charge par {interaction.user.display_name}")
        await interaction.response.edit_message(embed=embed)

    async def _on_close(self, interaction: discord.Interaction):
        ticket, err = self._get_closable_ticket(interaction)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        await interaction.response.defer()
        await _finish_ticket_close(interaction.channel, interaction.user, interaction.guild, self.ticket_id, ticket, None)

    async def _on_close_with_reason(self, interaction: discord.Interaction):
        ticket, err = self._get_closable_ticket(interaction)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        await interaction.response.send_modal(TicketCloseReasonModal(self))


class TicketCloseReasonModal(discord.ui.Modal, title="Fermer le ticket"):
    reason_input = discord.ui.TextInput(
        label="Raison de la fermeture", required=True, max_length=200,
    )

    def __init__(self, view: TicketControlView):
        super().__init__()
        self.view_ref = view

    async def on_submit(self, interaction: discord.Interaction):
        ticket, err = self.view_ref._get_closable_ticket(interaction)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        await interaction.response.defer()
        await _finish_ticket_close(
            interaction.channel, interaction.user, interaction.guild,
            self.view_ref.ticket_id, ticket, str(self.reason_input.value),
        )


class TicketDescriptionModal(discord.ui.Modal):
    description_input = discord.ui.TextInput(
        label="Décris ta demande",
        style=discord.TextStyle.paragraph,
        placeholder="Explique en quelques lignes ce dont tu as besoin...",
        required=True, max_length=1000,
    )

    def __init__(self, category: str):
        super().__init__(title=f"Nouveau ticket — {TICKET_CATEGORIES[category]}")
        self.category = category

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data, err = await _create_ticket_apply(str(interaction.user.id), self.category, str(self.description_input.value), guild=interaction.guild)
        if err:
            return await interaction.followup.send(f"❌ {err}", ephemeral=True)
        if data["already_open"]:
            return await interaction.followup.send(f"Tu as déjà un ticket ouvert : <#{data['channel_id']}>", ephemeral=True)
        await interaction.followup.send(f"✅ Ticket créé : <#{data['channel_id']}>", ephemeral=True)


class TicketPanelView(discord.ui.View):
    """Persistante (custom_id statique — pas besoin d'ID dynamique, la
    sélection ouvre juste un modal)."""

    def __init__(self):
        super().__init__(timeout=None)
        self.select = discord.ui.Select(
            placeholder="Choisis une catégorie pour ouvrir un ticket",
            custom_id="ticket_panel_select",
            options=[discord.SelectOption(label=label, value=key) for key, label in TICKET_CATEGORIES.items()],
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        category = self.select.values[0]
        await interaction.response.send_modal(TicketDescriptionModal(category))


@bot.command(name="ticket_panel")
async def cmd_ticket_panel(ctx):
    """Poste le panel d'ouverture de ticket dans le salon courant — à lancer une
    fois manuellement (pas auto-posté à chaque redémarrage, voir on_ready pour
    le ré-enregistrement des vues existantes). Pour gérer les motifs proposés
    et leur catégorie Discord de création, voir !set_ticket."""
    embed = discord.Embed(
        title="🎫 Ouvrir un ticket",
        description="Choisis une catégorie ci-dessous pour contacter le staff.",
        color=0x3498db,
    )
    await ctx.send(embed=embed, view=TicketPanelView())


def _set_ticket_embed(guild, selected_key: str | None) -> discord.Embed:
    embed = discord.Embed(title="🎫 Configuration des tickets", color=0x3498db)
    for key, label in TICKET_CATEGORIES.items():
        cid = TICKET_CATEGORY_IDS.get(key)
        cat = guild.get_channel(cid) if (guild and cid) else None
        marker = "➡️ " if key == selected_key else ""
        embed.add_field(
            name=f"{marker}{label}",
            value=f"`{key}` — {cat.name if cat else '*catégorie par défaut*'}",
            inline=False,
        )
    embed.set_footer(text="Choisis un motif dans le menu, puis sa catégorie ou un bouton d'action.")
    return embed


class AddTicketMotifModal(discord.ui.Modal, title="Ajouter un motif de ticket"):
    key_input = discord.ui.TextInput(label="Clé technique (minuscules, sans espace)", placeholder="support", max_length=32)
    emoji_input = discord.ui.TextInput(label="Emoji", placeholder="🛠️", max_length=8, required=False)
    label_input = discord.ui.TextInput(label="Libellé affiché", placeholder="Support technique", max_length=80)

    async def on_submit(self, interaction: discord.Interaction):
        global TICKET_CATEGORIES
        key = str(self.key_input.value).strip().lower()
        if not re.match(r'^[a-z0-9_]+$', key):
            return await interaction.response.send_message("❌ Clé invalide : lettres minuscules, chiffres et `_` uniquement.", ephemeral=True)
        if key in TICKET_CATEGORIES:
            return await interaction.response.send_message(f"❌ Le motif `{key}` existe déjà.", ephemeral=True)
        label = f"{str(self.emoji_input.value).strip()} {str(self.label_input.value).strip()}".strip()
        TICKET_CATEGORIES[key] = label
        save_data()
        await interaction.response.send_message(
            f"✅ Motif **{label}** (`{key}`) ajouté. Relance `!set_ticket` pour le voir dans le menu, "
            f"et `!ticket_panel` où tu veux l'afficher (un panel déjà posté ne se met pas à jour tout seul).",
            ephemeral=True,
        )


class SetTicketView(discord.ui.View):
    """Config interactive des motifs de ticket et de leur catégorie de création —
    même esprit que SetLogsView (menu + ChannelSelect natif)."""

    def __init__(self, guild, selected_key: str | None = None):
        super().__init__(timeout=300)
        self.guild = guild
        self.selected_key = selected_key if selected_key in TICKET_CATEGORIES else next(iter(TICKET_CATEGORIES), None)

        motif_options = [
            discord.SelectOption(label=label[:100], value=key, default=(key == self.selected_key))
            for key, label in TICKET_CATEGORIES.items()
        ]
        self.motif_select = discord.ui.Select(placeholder="🎫 Choisir un motif à configurer…", options=motif_options, row=0)
        self.motif_select.callback = self._on_motif
        self.add_item(self.motif_select)

        if self.selected_key:
            self.cat_select = discord.ui.ChannelSelect(
                placeholder=f"📁 Catégorie pour « {TICKET_CATEGORIES[self.selected_key]} »…"[:150],
                channel_types=[discord.ChannelType.category], row=1,
            )
            self.cat_select.callback = self._on_category
            self.add_item(self.cat_select)

        add_btn = discord.ui.Button(label="➕ Ajouter un motif", style=discord.ButtonStyle.secondary, row=2)
        add_btn.callback = self._on_add
        self.add_item(add_btn)

        if self.selected_key:
            reset_btn = discord.ui.Button(label="↩️ Catégorie par défaut", style=discord.ButtonStyle.secondary, row=2)
            reset_btn.callback = self._on_reset
            self.add_item(reset_btn)

            remove_btn = discord.ui.Button(
                label="🗑️ Retirer ce motif", style=discord.ButtonStyle.danger, row=2,
                disabled=(len(TICKET_CATEGORIES) <= 1),
            )
            remove_btn.callback = self._on_remove
            self.add_item(remove_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux admins/owner.", ephemeral=True)
            return False
        return True

    async def _on_motif(self, interaction: discord.Interaction):
        view = SetTicketView(self.guild, self.motif_select.values[0])
        await interaction.response.edit_message(embed=_set_ticket_embed(self.guild, view.selected_key), view=view)

    async def _on_category(self, interaction: discord.Interaction):
        global TICKET_CATEGORY_IDS
        channel = self.cat_select.values[0]
        TICKET_CATEGORY_IDS[self.selected_key] = channel.id
        save_data()
        view = SetTicketView(self.guild, self.selected_key)
        await interaction.response.edit_message(
            content=f"✅ Catégorie de **{TICKET_CATEGORIES[self.selected_key]}** mise à jour.",
            embed=_set_ticket_embed(self.guild, self.selected_key), view=view,
        )

    async def _on_reset(self, interaction: discord.Interaction):
        global TICKET_CATEGORY_IDS
        TICKET_CATEGORY_IDS.pop(self.selected_key, None)
        save_data()
        view = SetTicketView(self.guild, self.selected_key)
        await interaction.response.edit_message(
            content=f"✅ **{TICKET_CATEGORIES[self.selected_key]}** retombe sur la catégorie par défaut.",
            embed=_set_ticket_embed(self.guild, self.selected_key), view=view,
        )

    async def _on_remove(self, interaction: discord.Interaction):
        global TICKET_CATEGORIES, TICKET_CATEGORY_IDS
        if len(TICKET_CATEGORIES) <= 1:
            return await interaction.response.send_message("❌ Impossible de retirer le dernier motif.", ephemeral=True)
        label = TICKET_CATEGORIES.pop(self.selected_key)
        TICKET_CATEGORY_IDS.pop(self.selected_key, None)
        save_data()
        new_key = next(iter(TICKET_CATEGORIES))
        view = SetTicketView(self.guild, new_key)
        await interaction.response.edit_message(
            content=f"✅ Motif **{label}** retiré. Relance `!ticket_panel` où il était affiché pour mettre à jour le menu.",
            embed=_set_ticket_embed(self.guild, new_key), view=view,
        )

    async def _on_add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddTicketMotifModal())


@bot.command(name="set_ticket")
async def cmd_set_ticket(ctx):
    """Interface interactive (menu + boutons) pour gérer les motifs proposés dans
    !ticket_panel et leur catégorie Discord de création."""
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")
    view = SetTicketView(ctx.guild)
    await ctx.send(embed=_set_ticket_embed(ctx.guild, view.selected_key), view=view)


DEFAULT_TICKET_CLOSE_DELAY_S = 10
_CHANNEL_MENTION_RE = re.compile(r"^<#(\d+)>$")
_USER_MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def _resolve_ticket_arg(ctx, arg: str) -> dict | None:
    """Résout arg vers un ticket ouvert : mention de salon (#salon, ce qui
    donne <#id> une fois envoyé — marche sans le mode développeur, contrairement
    à un ID de salon copié à la main), ID de salon brut, ID de ticket, ou
    mention d'un membre (@membre) — utile quand le salon du ticket n'est plus
    accessible (ex. supprimé, ou créé sur un autre serveur avant le fix du
    18/08/2026) mais que le ticket est encore marqué ouvert en base."""
    m = _USER_MENTION_RE.match(arg)
    if m:
        return db_bs.get_open_ticket_for_user(m.group(1))
    m = _CHANNEL_MENTION_RE.match(arg)
    channel_id = m.group(1) if m else (arg if arg.isdigit() and len(arg) >= 15 else None)
    if channel_id:
        return db_bs.get_ticket_by_channel(channel_id)
    if arg.isdigit():
        return db_bs.get_ticket(int(arg))
    return None


@bot.command(name="fermer_ticket", aliases=["close_ticket", "ticket_close"])
async def cmd_fermer_ticket(ctx, arg: str = None, *, reste: str = None):
    """Deux usages :
    - Lancée dans le salon d'un ticket, sans argument de ciblage : ferme CE
      ticket, après un délai en secondes optionnel (`!fermer_ticket 30`,
      sinon délai par défaut).
    - Lancée avec une mention de salon, un ID de ticket ou une mention de
      membre en premier argument (depuis n'importe quel salon) : ferme ce
      ticket immédiatement, avec une raison optionnelle
      (`!fermer_ticket #ticket-incident-bob raison ici`, `!fermer_ticket 42 raison ici`
      ou `!fermer_ticket @membre raison ici`). La mention de membre marche même si
      le salon du ticket est introuvable (supprimé, ou sur un autre serveur)."""
    if not _is_ticket_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff.")

    current = db_bs.get_ticket_by_channel(str(ctx.channel.id))
    if current:
        delay = DEFAULT_TICKET_CLOSE_DELAY_S
        if arg is not None:
            try:
                delay = max(0, int(arg))
            except ValueError:
                return await ctx.send(
                    "❌ Dans le salon d'un ticket, le seul argument accepté est un délai en secondes. "
                    "Ex : `!fermer_ticket 30`"
                )
        await ctx.send(f"🔒 Ce ticket sera fermé dans {delay} seconde(s) par {ctx.author.mention}.")
        await asyncio.sleep(delay)
        # Re-vérifié après le délai : le ticket a pu être fermé/repris entre temps.
        ticket = db_bs.get_ticket(current["id"])
        if not ticket or ticket["status"] != "open":
            return
        await _finish_ticket_close(ctx.channel, ctx.author, ctx.guild, current["id"], ticket, None)
        return

    if arg is None:
        return await ctx.send(
            "❌ Utilisation : `!fermer_ticket` dans le salon du ticket (délai optionnel en secondes), "
            "ou `!fermer_ticket #salon-du-ticket [raison]` / `!fermer_ticket <id> [raison]` / `!fermer_ticket @membre [raison]` depuis n'importe quel salon."
        )
    ticket = _resolve_ticket_arg(ctx, arg)
    if not ticket:
        return await ctx.send(f"❌ Ticket introuvable pour `{arg}`.")
    ticket_id = ticket["id"]
    if ticket["status"] != "open":
        return await ctx.send(f"❌ Le ticket #{ticket_id} est déjà fermé.")

    channel = ctx.guild.get_channel(int(ticket["channel_id"]))
    if not channel:
        # Salon déjà supprimé manuellement : on ferme quand même l'enregistrement.
        db_bs.close_ticket(ticket_id, str(ctx.author.id), reste, [])
        return await ctx.send(f"✅ Ticket #{ticket_id} marqué comme fermé (le salon n'existait déjà plus).")

    await _finish_ticket_close(channel, ctx.author, ctx.guild, ticket_id, ticket, reste)
    await ctx.send(f"✅ Ticket #{ticket_id} fermé.")


# ═════════════════════════════════════════════════════════════════════════
# ── Déclaration d'absences ───────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════
# Ouvrable depuis Discord (panel + select club + modal, voir !absence_panel).
# Même architecture que le système de tickets ci-dessus : _create_absence_apply,
# _update_absence_apply et _delete_absence_apply sont le cœur partagé par
# Discord et le site (voir GET/POST /api/admin/absences* dans keep_alive.py).

_ABSENCE_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")


def _parse_absence_date(raw: str) -> date | None:
    """Parse 'JJ/MM' ou 'JJ/MM/AAAA' (année courante par défaut). Retourne
    None si le format ou la date est invalide — l'appelant renvoie alors une
    erreur à l'utilisateur plutôt que de planter."""
    m = _ABSENCE_DATE_RE.match(raw.strip())
    if not m:
        return None
    day, month, year = m.groups()
    year = int(year) if year else datetime.now().year
    if year < 100:
        year += 2000
    try:
        return date(year, int(month), int(day))
    except ValueError:
        return None


def _absence_is_active(absence: dict, today: date) -> bool:
    """Vrai si `today` tombe dans la période déclarée. Une absence sans date
    de retour est considérée active indéfiniment jusqu'à suppression/modif —
    même logique que l'affichage "?" dans !absences."""
    start = date.fromisoformat(absence["start_date"])
    if start > today:
        return False
    if absence.get("return_date"):
        return date.fromisoformat(absence["return_date"]) >= today
    return True


async def _sync_absence_role(guild: discord.Guild, discord_id: str) -> None:
    """Ajoute/retire le rôle ABSENCE_ROLE_ID selon qu'une absence de ce
    membre couvre la date du jour — appelé juste après chaque
    création/modif/suppression pour un effet immédiat. La tâche périodique
    sync_absence_roles rattrape les cas où personne n'agit sur la
    déclaration (début différé, fin de période atteinte sans suppression)."""
    role = guild.get_role(ABSENCE_ROLE_ID)
    if not role:
        return
    member = guild.get_member(int(discord_id))
    if not member:
        return
    today = datetime.now(BS_SEASON_TZ).date()
    should_have = any(
        _absence_is_active(row, today) for row in db_bs.list_absences_for_member(discord_id)
    )
    has = role in member.roles
    try:
        if should_have and not has:
            await member.add_roles(role, reason="Absence en cours")
        elif not should_have and has:
            await member.remove_roles(role, reason="Absence terminée/supprimée")
    except discord.HTTPException:
        pass


def _is_absence_staff(member: discord.Member) -> bool:
    """Staff autorisé à déclarer/modifier/supprimer une absence pour
    n'importe quel membre (pas seulement la sienne) — basé sur le rôle staff
    (même TICKET_STAFF_ROLE_IDS que les tickets, demande du 17/08/2026 :
    rôle staff plutôt que juste "administrateur"), l'auteur d'une absence
    garde toujours le droit de gérer la sienne indépendamment de ce check."""
    return is_bot_owner(member) or member.guild_permissions.administrator or any(
        r.id in TICKET_STAFF_ROLE_IDS for r in member.roles
    )


ABSENCE_TYPE_LABELS = {"partielle": "🟡 Partielle (temps de jeu réduit)", "totale": "🔴 Totale (aucune connexion)"}


async def _create_absence_apply(
    discord_id: str, club: str, absence_type: str, start_raw: str, return_raw: str | None,
    reason: str, missed_event: str | None, declared_by: discord.Member | None = None,
):
    """Retourne (data, err). data = {'id','club','start_date','return_date'}.
    declared_by est renseigné quand un membre du staff déclare pour quelqu'un
    d'autre (!absence_ajouter) — juste pour le log, discord_id reste la
    personne concernée par l'absence."""
    start_date = _parse_absence_date(start_raw)
    if not start_date:
        return None, f"Date de début invalide : `{start_raw}` (format attendu JJ/MM ou JJ/MM/AAAA)."

    return_date = None
    if return_raw:
        return_date = _parse_absence_date(return_raw)
        if not return_date:
            return None, f"Date de retour invalide : `{return_raw}` (format attendu JJ/MM ou JJ/MM/AAAA)."
        if return_date < start_date:
            return None, "La date de retour ne peut pas être avant la date de début."

    row = db_bs.create_absence(
        discord_id, club, absence_type, start_date.isoformat(),
        return_date.isoformat() if return_date else None,
        reason, missed_event,
    )

    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if guild:
        await _sync_absence_role(guild, discord_id)
        fields = [
            ("Membre", f"<@{discord_id}>", True),
            ("Club", club, True),
            ("Type", ABSENCE_TYPE_LABELS.get(absence_type, absence_type), True),
            ("Début", start_date.strftime("%d/%m/%Y"), True),
        ]
        if return_date:
            fields.append(("Retour prévu", return_date.strftime("%d/%m/%Y"), True))
        if missed_event:
            fields.append(("Événement manqué", missed_event, False))
        fields.append(("Raison", reason, False))
        if declared_by and declared_by.id != int(discord_id):
            fields.append(("Déclarée par (staff)", declared_by.mention, True))
        await send_log_message(
            guild, LOG_ABSENCE_CHANNEL_ID,
            "🌴 Absence déclarée", None, discord.Color.blue(), fields=fields,
        )

    return {
        "id": row["id"], "club": club,
        "start_date": start_date.isoformat(),
        "return_date": return_date.isoformat() if return_date else None,
    }, None


async def _update_absence_apply(
    absence_id: int, actor: discord.Member, absence_type: str, start_raw: str, return_raw: str | None,
    reason: str, missed_event: str | None,
):
    """Retourne (ok, err). Autorisé pour l'auteur de la déclaration ou le staff."""
    absence = db_bs.get_absence(absence_id)
    if not absence:
        return False, "Absence introuvable."
    if str(actor.id) != absence["discord_id"] and not _is_absence_staff(actor):
        return False, "Réservé à l'auteur de la déclaration ou au staff."

    if absence_type not in ABSENCE_TYPE_LABELS:
        return False, "Type d'absence invalide (partielle ou totale)."

    start_date = _parse_absence_date(start_raw)
    if not start_date:
        return False, f"Date de début invalide : `{start_raw}` (format attendu JJ/MM ou JJ/MM/AAAA)."
    return_date = None
    if return_raw:
        return_date = _parse_absence_date(return_raw)
        if not return_date:
            return False, f"Date de retour invalide : `{return_raw}` (format attendu JJ/MM ou JJ/MM/AAAA)."
        if return_date < start_date:
            return False, "La date de retour ne peut pas être avant la date de début."

    db_bs.update_absence(
        absence_id, absence_type, start_date.isoformat(),
        return_date.isoformat() if return_date else None,
        reason, missed_event,
    )

    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if guild:
        await _sync_absence_role(guild, absence["discord_id"])
        fields = [
            ("Absence", f"#{absence_id}", True),
            ("Membre", f"<@{absence['discord_id']}>", True),
            ("Club", absence["club"], True),
            ("Type", ABSENCE_TYPE_LABELS.get(absence_type, absence_type), True),
            ("Modifiée par", actor.mention, True),
        ]
        await send_log_message(
            guild, LOG_ABSENCE_CHANNEL_ID,
            "✏️ Absence modifiée", None, discord.Color.orange(), fields=fields,
        )
    return True, None


async def _delete_absence_apply(absence_id: int, actor: discord.Member):
    """Retourne (ok, err). Autorisé pour l'auteur de la déclaration ou le staff."""
    absence = db_bs.get_absence(absence_id)
    if not absence:
        return False, "Absence introuvable."
    if str(actor.id) != absence["discord_id"] and not _is_absence_staff(actor):
        return False, "Réservé à l'auteur de la déclaration ou au staff."

    db_bs.delete_absence(absence_id)

    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if guild:
        await _sync_absence_role(guild, absence["discord_id"])
        fields = [
            ("Absence", f"#{absence_id}", True),
            ("Membre", f"<@{absence['discord_id']}>", True),
            ("Club", absence["club"], True),
            ("Supprimée par", actor.mention, True),
        ]
        await send_log_message(
            guild, LOG_ABSENCE_CHANNEL_ID,
            "🗑️ Absence supprimée", None, discord.Color.light_grey(), fields=fields,
        )
    return True, None


def _absence_club_options() -> list[discord.SelectOption]:
    clubs = db_bs.list_family_clubs()[:25]  # limite Discord : 25 options par select
    return [
        discord.SelectOption(label=c["name"][:100], value=c["name"][:100], description=f"#{c['tag']}")
        for c in clubs
    ] or [discord.SelectOption(label="Aucun club configuré", value="none")]


class AbsenceModal(discord.ui.Modal):
    start_input = discord.ui.TextInput(
        label="Date de début (JJ/MM/AAAA)", placeholder="17/08/2026",
        required=True, max_length=10,
    )
    return_input = discord.ui.TextInput(
        label="Date de retour prévue (optionnel)", placeholder="24/08/2026",
        required=False, max_length=10,
    )
    missed_event_input = discord.ui.TextInput(
        label="Événement manqué (optionnel)", placeholder="Ex : War du 20/08, scrim...",
        required=False, max_length=100,
    )
    reason_input = discord.ui.TextInput(
        label="Raison", style=discord.TextStyle.paragraph,
        required=True, max_length=300,
    )

    def __init__(self, club: str, absence_type: str, target: discord.Member | None = None):
        super().__init__(title=f"Déclarer une absence — {club}"[:45])
        self.club = club
        self.absence_type = absence_type
        self.target = target  # renseigné quand le staff déclare pour quelqu'un d'autre

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        target_id = str(self.target.id) if self.target else str(interaction.user.id)
        data, err = await _create_absence_apply(
            target_id, self.club, self.absence_type,
            str(self.start_input.value),
            str(self.return_input.value) if self.return_input.value else None,
            str(self.reason_input.value),
            str(self.missed_event_input.value) if self.missed_event_input.value else None,
            declared_by=interaction.user,
        )
        if err:
            return await interaction.followup.send(f"❌ {err}", ephemeral=True)
        who = f"pour {self.target.mention}" if self.target else "pour toi"
        await interaction.followup.send(
            f"✅ Absence #{data['id']} déclarée {who} — **{data['club']}**. "
            f"Retirable avec `!supprimer_absence {data['id']}`.",
            ephemeral=True,
        )


class AbsenceEditModal(discord.ui.Modal):
    # Type en texte libre ici (pas de select dans un modal Discord) — validé
    # côté _update_absence_apply contre ABSENCE_TYPE_LABELS, erreur claire si
    # mal orthographié plutôt qu'une valeur silencieusement invalide.
    absence_type_input = discord.ui.TextInput(label="Type (partielle ou totale)", required=True, max_length=10)
    start_input = discord.ui.TextInput(label="Date de début (JJ/MM/AAAA)", required=True, max_length=10)
    return_input = discord.ui.TextInput(label="Date de retour prévue (optionnel)", required=False, max_length=10)
    missed_event_input = discord.ui.TextInput(label="Événement manqué (optionnel)", required=False, max_length=100)
    reason_input = discord.ui.TextInput(label="Raison", style=discord.TextStyle.paragraph, required=True, max_length=300)

    def __init__(self, absence: dict):
        super().__init__(title=f"Modifier l'absence #{absence['id']} — {absence['club']}"[:45])
        self.absence_id = absence["id"]
        self.absence_type_input.default = absence.get("absence_type", "totale")
        self.start_input.default = datetime.fromisoformat(absence["start_date"]).strftime("%d/%m/%Y")
        if absence.get("return_date"):
            self.return_input.default = datetime.fromisoformat(absence["return_date"]).strftime("%d/%m/%Y")
        if absence.get("missed_event"):
            self.missed_event_input.default = absence["missed_event"]
        self.reason_input.default = absence["reason"]

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ok, err = await _update_absence_apply(
            self.absence_id, interaction.user,
            str(self.absence_type_input.value).strip().lower(),
            str(self.start_input.value),
            str(self.return_input.value) if self.return_input.value else None,
            str(self.reason_input.value),
            str(self.missed_event_input.value) if self.missed_event_input.value else None,
        )
        if not ok:
            return await interaction.followup.send(f"❌ {err}", ephemeral=True)
        await interaction.followup.send(f"✅ Absence #{self.absence_id} modifiée.", ephemeral=True)


_ABSENCE_TYPE_OPTIONS = [
    discord.SelectOption(label="Absence partielle", value="partielle", emoji="🟡", description="Temps de jeu réduit"),
    discord.SelectOption(label="Absence totale", value="totale", emoji="🔴", description="Aucune connexion possible"),
]


class AbsenceTypeSelectView(discord.ui.View):
    """Non persistante, instanciée à chaque sélection de club — étape
    intermédiaire avant le modal (club -> type -> formulaire). Volontairement
    PAS fusionnée dans AbsencePanelView : cette dernière est persistante et
    partagée par tous les membres qui cliquent dessus (un seul objet Python
    enregistré via bot.add_view), donc y stocker un choix "en cours" par
    utilisateur mélangerait les sélections de deux personnes qui déclarent
    en même temps. Ici, chaque clic sur le select club envoie un message
    éphémère avec une instance fraîche de cette vue, propre à cette seule
    interaction."""

    def __init__(self, club: str, target: discord.Member | None = None):
        super().__init__(timeout=180)
        self.club = club
        self.target = target
        self.select = discord.ui.Select(placeholder="Absence partielle ou totale ?", options=_ABSENCE_TYPE_OPTIONS)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AbsenceModal(self.club, self.select.values[0], target=self.target))


class AbsencePanelView(discord.ui.View):
    """Persistante (custom_id statique — même technique que TicketPanelView) :
    choisir un club envoie un message éphémère pour choisir le type
    d'absence (voir AbsenceTypeSelectView), qui ouvre ensuite le modal."""

    def __init__(self):
        super().__init__(timeout=None)
        self.select = discord.ui.Select(
            placeholder="Choisis ton club pour déclarer une absence",
            custom_id="absence_panel_select",
            options=_absence_club_options(),
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        club = self.select.values[0]
        if club == "none":
            return await interaction.response.send_message("❌ Aucun club n'est configuré pour le moment.", ephemeral=True)
        await interaction.response.send_message(
            f"Club : **{club}** — dernière étape :", view=AbsenceTypeSelectView(club), ephemeral=True,
        )


class AbsenceStaffTargetView(discord.ui.View):
    """Non persistante (timeout court, pas de custom_id statique) : le staff
    choisit un club pour déclarer une absence AU NOM d'un membre ciblé
    (!absence_ajouter), puis le type d'absence (AbsenceTypeSelectView)."""

    def __init__(self, target: discord.Member):
        super().__init__(timeout=300)
        self.target = target
        self.select = discord.ui.Select(
            placeholder=f"Choisis le club de {target.display_name}",
            options=_absence_club_options(),
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        club = self.select.values[0]
        if club == "none":
            return await interaction.response.send_message("❌ Aucun club n'est configuré pour le moment.", ephemeral=True)
        await interaction.response.send_message(
            f"Club : **{club}** — dernière étape :", view=AbsenceTypeSelectView(club, target=self.target), ephemeral=True,
        )


class AbsenceEditPromptView(discord.ui.View):
    """Un seul bouton pour ouvrir le modal de modification — une commande
    texte ne peut pas ouvrir un modal directement (il faut une interaction),
    même technique que TicketCloseReasonModal côté tickets."""

    def __init__(self, absence: dict, requester_id: int):
        super().__init__(timeout=120)
        self.absence = absence
        self.requester_id = requester_id
        btn = discord.ui.Button(label="✏️ Modifier", style=discord.ButtonStyle.primary)
        btn.callback = self._on_edit
        self.add_item(btn)

    async def _on_edit(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message("❌ Ce bouton n'est pas pour toi.", ephemeral=True)
        await interaction.response.send_modal(AbsenceEditModal(self.absence))


class AbsenceActionView(discord.ui.View):
    """Modifier/Supprimer pour UNE absence précise — affiché après
    sélection dans AbsenceStaffPanelView. Modifier réutilise AbsenceEditModal,
    Supprimer réutilise _delete_absence_apply (même logique que
    !absence_modifier/!supprimer_absence en commande directe)."""

    def __init__(self, absence: dict, requester_id: int):
        super().__init__(timeout=120)
        self.absence = absence
        self.requester_id = requester_id
        edit_btn = discord.ui.Button(label="✏️ Modifier", style=discord.ButtonStyle.primary)
        edit_btn.callback = self._on_edit
        self.add_item(edit_btn)
        delete_btn = discord.ui.Button(label="🗑️ Supprimer", style=discord.ButtonStyle.danger)
        delete_btn.callback = self._on_delete
        self.add_item(delete_btn)

    async def _on_edit(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message("❌ Ce bouton n'est pas pour toi.", ephemeral=True)
        await interaction.response.send_modal(AbsenceEditModal(self.absence))

    async def _on_delete(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message("❌ Ce bouton n'est pas pour toi.", ephemeral=True)
        ok, err = await _delete_absence_apply(self.absence["id"], interaction.user)
        if not ok:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)
        await interaction.response.edit_message(content=f"✅ Absence #{self.absence['id']} supprimée.", view=None)


class AbsenceStaffPanelView(discord.ui.View):
    """Panel interactif pour le staff (!absences) : filtre par club (select)
    et tri (bouton) au lieu d'un argument texte libre — un club mal
    orthographié ou un caractère Unicode qui se ressemble sans être identique
    (ex: 'ProjetΣ' tapé 'projet∑') faisait échouer silencieusement l'ancien
    `!absences <club>` (incident du 17/08/2026). Choisir une absence dans la
    liste ouvre un second panel Modifier/Supprimer (AbsenceActionView)."""

    def __init__(self, requester_id: int, club_filter: str | None = None, sort_by: str = "date"):
        super().__init__(timeout=300)
        self.requester_id = requester_id
        self.club_filter = club_filter
        self.sort_by = sort_by
        self._rows: list[dict] = []
        self._build()

    def _load_rows(self) -> list[dict]:
        rows = db_bs.list_absences(self.club_filter)
        if self.sort_by == "club":
            rows = sorted(rows, key=lambda r: (r["club"], r["start_date"]))
        return rows

    def _build(self):
        self.clear_items()
        self._rows = self._load_rows()

        clubs = sorted({r["club"] for r in db_bs.list_absences()})
        club_options = [discord.SelectOption(label="Tous les clubs", value="__all__", default=self.club_filter is None)]
        club_options += [
            discord.SelectOption(label=c[:100], value=c[:100], default=(c == self.club_filter))
            for c in clubs[:24]
        ]
        self.club_select = discord.ui.Select(placeholder="Filtrer par club", options=club_options)
        self.club_select.callback = self._on_club_select
        self.add_item(self.club_select)

        if self._rows:
            self.abs_select = discord.ui.Select(
                placeholder="Choisir une absence à modifier/supprimer",
                options=[
                    discord.SelectOption(label=f"#{r['id']} — {r['club']} — {r['start_date']}"[:100], value=str(r["id"]))
                    for r in self._rows[:25]
                ],
            )
            self.abs_select.callback = self._on_pick
            self.add_item(self.abs_select)

        sort_btn = discord.ui.Button(
            label=f"Trier par {'date' if self.sort_by == 'club' else 'club'}",
            style=discord.ButtonStyle.secondary,
        )
        sort_btn.callback = self._on_toggle_sort
        self.add_item(sort_btn)

    def _build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"🌴 Absences{f' — {self.club_filter}' if self.club_filter else ''}",
            color=discord.Color.blue(),
        )
        if not self._rows:
            embed.description = "Aucune absence enregistrée."
        for row in self._rows[:25]:  # limite embed : 25 champs max
            retour = row["return_date"] or "?"
            type_label = ABSENCE_TYPE_LABELS.get(row.get("absence_type"), row.get("absence_type") or "?")
            value = f"<@{row['discord_id']}> — {type_label} — retour prévu : {retour}\n{row['reason']}"
            if row.get("missed_event"):
                value += f"\n🎯 Manqué : {row['missed_event']}"
            embed.add_field(name=f"#{row['id']} — {row['club']} — {row['start_date']}", value=value, inline=False)
        embed.set_footer(text=f"Tri : {'club' if self.sort_by == 'club' else 'date'} · {len(self._rows)} résultat(s)")
        return embed

    async def _refresh(self, interaction: discord.Interaction):
        self._build()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Ce panel n'est pas pour toi — lance `!absences` toi-même.", ephemeral=True)
            return False
        return True

    async def _on_club_select(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        value = self.club_select.values[0]
        self.club_filter = None if value == "__all__" else value
        await self._refresh(interaction)

    async def _on_toggle_sort(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.sort_by = "club" if self.sort_by == "date" else "date"
        await self._refresh(interaction)

    async def _on_pick(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        absence_id = int(self.abs_select.values[0])
        absence = db_bs.get_absence(absence_id)
        if not absence:
            return await interaction.response.send_message("❌ Absence introuvable (déjà supprimée ?).", ephemeral=True)
        type_label = ABSENCE_TYPE_LABELS.get(absence.get("absence_type"), absence.get("absence_type") or "?")
        await interaction.response.send_message(
            f"**Absence #{absence_id}** — {absence['club']} — <@{absence['discord_id']}> — {type_label}",
            view=AbsenceActionView(absence, interaction.user.id),
            ephemeral=True,
        )


@bot.command(name="absence_panel")
async def cmd_absence_panel(ctx):
    """Poste le panel de déclaration d'absence dans le salon courant — à
    lancer une fois manuellement (même logique que !ticket_panel, pas
    auto-reposté à chaque redémarrage, voir on_ready pour le
    ré-enregistrement de la vue existante)."""
    embed = discord.Embed(
        title="🌴 Déclarer une absence",
        description="Choisis ton club ci-dessous, puis le type d'absence (partielle ou totale), "
        "avant de remplir le formulaire (dates, raison, événement manqué éventuel).",
        color=discord.Color.blue(),
    )
    await ctx.send(embed=embed, view=AbsencePanelView())


@bot.command(name="absence_ajouter", aliases=["ajouter_absence"])
async def cmd_absence_ajouter(ctx, membre: discord.Member):
    """Réservé au staff : déclare une absence au nom d'un autre membre
    (même flux select club + modal que le panel public, voir AbsenceStaffTargetView)."""
    if not _is_absence_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff.")
    await ctx.send(
        f"Déclaration d'absence pour {membre.mention} — choisis son club ci-dessous :",
        view=AbsenceStaffTargetView(membre),
    )


@bot.command(name="absence_modifier", aliases=["modifier_absence"])
async def cmd_absence_modifier(ctx, absence_id: int):
    """Ouvre le modal de modification (bouton, voir AbsenceEditPromptView) —
    réservé à l'auteur de la déclaration ou au staff."""
    absence = db_bs.get_absence(absence_id)
    if not absence:
        return await ctx.send("❌ Absence introuvable.")
    if str(ctx.author.id) != absence["discord_id"] and not _is_absence_staff(ctx.author):
        return await ctx.send("❌ Réservé à l'auteur de la déclaration ou au staff.")
    await ctx.send(
        f"Modification de l'absence #{absence_id} ({absence['club']}) :",
        view=AbsenceEditPromptView(absence, ctx.author.id),
    )


@bot.command(name="absences")
async def cmd_absences(ctx):
    """Panel interactif pour le staff : liste des absences, filtrable par
    club et triable via les menus/boutons, avec modifier/supprimer par
    sélection (voir AbsenceStaffPanelView) — remplace l'ancien
    `!absences <club>` en texte libre, source d'erreurs silencieuses
    (casse, caractères Unicode qui se ressemblent sans être identiques)."""
    if not _is_absence_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff.")
    view = AbsenceStaffPanelView(ctx.author.id)
    await ctx.send(embed=view._build_embed(), view=view)


@bot.command(name="supprimer_absence", aliases=["delete_absence", "absence_supprimer"])
async def cmd_supprimer_absence(ctx, absence_id: int):
    """Supprime une déclaration d'absence — l'auteur peut supprimer la
    sienne, le staff peut supprimer celle de n'importe qui."""
    ok, err = await _delete_absence_apply(absence_id, ctx.author)
    if not ok:
        return await ctx.send(f"❌ {err}")
    await ctx.send(f"✅ Absence #{absence_id} supprimée.")


@tasks.loop(hours=1)
async def sync_absence_roles():
    """Rattrape les cas que _sync_absence_role (appelé à la création/modif/
    suppression) ne peut pas couvrir tout seul : une absence déclarée à
    l'avance dont le début vient d'arriver (le rôle doit apparaître), ou une
    absence dont le retour est passé sans que personne ne supprime la
    déclaration (le rôle doit disparaître)."""
    await bot.wait_until_ready()
    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if not guild:
        return
    role = guild.get_role(ABSENCE_ROLE_ID)
    if not role:
        return

    today = datetime.now(BS_SEASON_TZ).date()
    active_ids = {
        row["discord_id"] for row in db_bs.list_absences() if _absence_is_active(row, today)
    }

    for discord_id in active_ids:
        member = guild.get_member(int(discord_id))
        if member and role not in member.roles:
            try:
                await member.add_roles(role, reason="Absence en cours (sync automatique)")
            except discord.HTTPException:
                pass

    for member in role.members:
        if str(member.id) not in active_ids:
            try:
                await member.remove_roles(role, reason="Absence terminée (sync automatique)")
            except discord.HTTPException:
                pass


# ── !jugement : vote collectif du staff sur la sanction d'un membre ────────
# Demande du 10-11/08/2026 (voir #staff) : "!jugement @membre" propose
# mute/ban/kick/punition/relaxe, le staff vote par boutons, et seul celui
# qui a lancé la commande peut clôturer le vote pour éviter tout abus
# ("celui qui lance la commande doit confirmer le vote avant que la
# sanction soit appliquée"). Réservé au rôle staff Discord et aux admins,
# aussi bien pour lancer que pour voter.
# ═════════════════════════════════════════════════════════════════════════
JUGEMENT_STAFF_ROLE_ID = 1516514610881237084
JUGEMENT_PUNITION_COUNT = 50
JUGEMENT_MUTE_MINUTES = 60

# Ordre de sévérité pour départager une égalité au moment de la clôture —
# la sanction la plus sévère l'emporte plutôt qu'un choix arbitraire.
JUGEMENT_OPTIONS = [
    ("ban", "🚫 Ban", discord.ButtonStyle.danger),
    ("kick", "👢 Kick", discord.ButtonStyle.danger),
    ("mute", f"🔇 Mute ({JUGEMENT_MUTE_MINUTES}min)", discord.ButtonStyle.secondary),
    ("punition", f"🔒 Punition ({JUGEMENT_PUNITION_COUNT})", discord.ButtonStyle.secondary),
    ("relaxe", "✅ Relaxe", discord.ButtonStyle.success),
]
JUGEMENT_SEVERITY_ORDER = [key for key, _, _ in JUGEMENT_OPTIONS]  # déjà du plus au moins sévère


def _jugement_authorized(member: discord.Member) -> bool:
    return (
        is_bot_owner(member)
        or member.guild_permissions.administrator
        or any(r.id == JUGEMENT_STAFF_ROLE_ID for r in member.roles)
    )


class JugementView(discord.ui.View):
    def __init__(self, target: discord.Member, launcher_id: int):
        super().__init__(timeout=None)  # reste ouvert tant que pas clôturé manuellement
        self.target = target
        self.launcher_id = launcher_id
        self.votes: dict[str, set[int]] = {key: set() for key, _, _ in JUGEMENT_OPTIONS}
        self.closed = False

        for key, label, style in JUGEMENT_OPTIONS:
            btn = discord.ui.Button(label=label, style=style, custom_id=f"jugement_vote_{key}_{target.id}")
            btn.callback = self._make_vote_callback(key)
            self.add_item(btn)

        close_btn = discord.ui.Button(
            label="🔨 Clôturer le vote", style=discord.ButtonStyle.primary,
            row=1, custom_id=f"jugement_close_{target.id}",
        )
        close_btn.callback = self._on_close
        self.add_item(close_btn)

    def _make_vote_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            if self.closed:
                return await interaction.response.send_message("❌ Ce vote est déjà clôturé.", ephemeral=True)
            if not _jugement_authorized(interaction.user):
                return await interaction.response.send_message("❌ Réservé au staff Discord et aux admins.", ephemeral=True)
            for voters in self.votes.values():
                voters.discard(interaction.user.id)
            self.votes[key].add(interaction.user.id)
            await interaction.response.edit_message(embed=self._build_embed())
        return callback

    def _build_embed(self, verdict_line: str | None = None) -> discord.Embed:
        title = f"⚖️ Jugement de {self.target.display_name}"
        embed = discord.Embed(
            title=title + (" — Terminé" if self.closed else ""),
            description=(
                "Le staff vote la sanction. Un vote par personne (tu peux changer d'avis).\n"
                f"Seul <@{self.launcher_id}> peut clôturer le vote."
            ),
            color=0x95a5a6 if self.closed else 0x3498db,
        )
        for key, label, _ in JUGEMENT_OPTIONS:
            embed.add_field(name=label, value=str(len(self.votes[key])), inline=True)
        if verdict_line:
            embed.add_field(name="Verdict", value=verdict_line, inline=False)
        return embed

    async def _on_close(self, interaction: discord.Interaction):
        if self.closed:
            return await interaction.response.send_message("❌ Ce vote est déjà clôturé.", ephemeral=True)
        if interaction.user.id != self.launcher_id and not is_bot_owner(interaction.user):
            return await interaction.response.send_message(
                "❌ Seul la personne qui a lancé le jugement peut le clôturer.", ephemeral=True
            )

        self.closed = True
        counts = {key: len(voters) for key, voters in self.votes.items()}
        max_votes = max(counts.values())
        if max_votes == 0:
            winner = "relaxe"
        else:
            tied = {key for key, c in counts.items() if c == max_votes}
            winner = next(key for key in JUGEMENT_SEVERITY_ORDER if key in tied)

        for item in self.children:
            item.disabled = True
        await interaction.response.defer()

        verdict_line = await self._apply_verdict(interaction, winner, counts[winner])
        await interaction.message.edit(embed=self._build_embed(verdict_line), view=self)

    async def _apply_verdict(self, interaction: discord.Interaction, winner: str, nb_votes: int) -> str:
        guild, actor, member = interaction.guild, interaction.user, self.target
        reason = f"Verdict du jugement collectif ({nb_votes} vote(s))"

        if winner == "relaxe":
            await send_log_message(
                guild, LOG_MODERATION_CHANNEL_ID, "⚖️ Jugement — Relaxe",
                f"{member.mention} a été relaxé par jugement collectif (clôturé par {actor.mention}).",
                discord.Color.green(),
            )
            return f"✅ **Relaxe** — {nb_votes} vote(s), aucune sanction appliquée."

        if winner == "ban":
            _, err = await _apply_ban(guild, member.id, actor.id, reason)
            return f"🚫 **Ban** ({nb_votes} vote(s))" + (f" — échec : {err}" if err else "")

        if winner == "kick":
            _, err = await _apply_kick(guild, member.id, actor.id, reason)
            return f"👢 **Kick** ({nb_votes} vote(s))" + (f" — échec : {err}" if err else "")

        if winner == "mute":
            _, err = await _apply_mute(guild, member.id, actor.id, f"{JUGEMENT_MUTE_MINUTES}m", reason)
            return f"🔇 **Mute {JUGEMENT_MUTE_MINUTES} min** ({nb_votes} vote(s))" + (f" — échec : {err}" if err else "")

        _, err = await _apply_punition(guild, member.id, actor.id, JUGEMENT_PUNITION_COUNT)
        return f"🔒 **Punition** (compter jusqu'à {JUGEMENT_PUNITION_COUNT}, {nb_votes} vote(s))" + (f" — échec : {err}" if err else "")


@bot.command(name="jugement", aliases=["judge", "tribunal"])
async def cmd_jugement(ctx, membre: discord.Member):
    if not _jugement_authorized(ctx.author):
        return await ctx.send("❌ Réservé au staff Discord et aux admins.")
    if _is_mod_immune(membre):
        return await ctx.send(random.choice(PROTECTED_REJECT_LINES).format(target=membre.mention))
    if membre.id == bot.user.id:
        return await ctx.send("❌ On ne juge pas le juge.")

    await send_log_message(
        ctx.guild, LOG_MODERATION_CHANNEL_ID, "⚖️ Jugement lancé",
        f"{ctx.author.mention} a lancé un jugement contre {membre.mention}.",
        discord.Color.blurple(),
    )
    view = JugementView(membre, ctx.author.id)
    await ctx.send(embed=view._build_embed(), view=view)


@bot.command(name="punition", aliases=["pun", "punir"])
async def cmd_punition(ctx, nombre: int, membre: discord.Member):
    if await _check_protected_target(ctx, membre):
        return
    if nombre <= 0:
        return await ctx.send("❌ Le nombre doit être supérieur à 0.")

    guild = ctx.guild

    # Créer le salon de punition
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        membre: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        ctx.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    # Les rôles autorisés à utiliser !punition via !perm doivent aussi voir le salon créé
    for rid in cmd_role_perms.get('punition', []):
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
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
    _log_moderation('punition', membre, ctx.author, extra=f"compter jusqu'à {nombre}")
    save_data()
    await send_log_message(
        guild, LOG_MODERATION_CHANNEL_ID, "🔒 Punition",
        f"{membre.mention} a été mis en punition par {ctx.author.mention} (compter jusqu'à {nombre}).",
        discord.Color.dark_red(),
    )

    await salon.send(
        f"🔒 {membre.mention} tu es en **punition** !\n"
        f"Tu dois compter de **1** jusqu'à **{nombre}** sans faire de faute.\n"
        f"⚠️ Si tu te trompes, ça repart de **0** !\n\n"
        f"Commence à compter : **1**"
    )
    await ctx.send(f"✅ {membre.mention} est en punition. Il doit compter jusqu'à {nombre}.")


@bot.command(name="annuler_punition", aliases=["apun", "unpunish"])
async def cmd_annuler_punition(ctx, membre: discord.Member):
    uid = str(membre.id)
    if uid not in punitions:
        return await ctx.send(f"❌ {membre.mention} n'est pas en punition.")

    await _liberer_membre(ctx.guild, membre, resolved_by=ctx.author)
    await ctx.send(f"✅ La punition de {membre.mention} a été annulée.")


@bot.hybrid_command(name="snipe")
async def cmd_snipe(ctx, arg1: str = None, arg2: str = None):
    args = [a for a in (arg1, arg2) if a is not None]
    nb     = 1
    target = None
    for arg in args:
        if arg.isdigit():
            nb = max(1, min(int(arg), 10))
        else:
            try:
                target = await commands.MemberConverter().convert(ctx, arg)
            except commands.BadArgument:
                pass

    cache = snipe_cache.get(ctx.channel.id, [])
    entries = list(reversed(cache))
    if target:
        entries = [e for e in entries if e['author'].id == target.id]

    if not entries:
        msg = f"Aucun message supprimé de **{target.display_name}**." if target else "Aucun message supprimé récemment."
        return await ctx.send(f"🔍 {msg}")

    entries = entries[:nb]
    for i, e in enumerate(entries):
        ts   = e['at'].strftime('%H:%M:%S')
        desc = e['content'] or '*(pas de texte)*'
        if e['attachments']:
            desc += '\n' + '\n'.join(e['attachments'])
        embed = discord.Embed(
            description=desc,
            color=0xe74c3c,
            timestamp=e['at']
        )
        embed.set_author(name=e['author'].display_name, icon_url=e['author'].display_avatar.url)
        embed.set_footer(text=f"Supprimé à {ts}" + (f" — {i+1}/{len(entries)}" if len(entries) > 1 else ""))
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def _liberer_membre(guild, membre, resolved_by=None):
    """resolved_by : membre qui a tapé !annuler_punition, ou None si la punition
    s'est terminée toute seule (compte réussi jusqu'au bout)."""
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
    extra = "annulée manuellement" if resolved_by else "terminée en comptant jusqu'au bout"
    _log_moderation('punition_fin', membre, resolved_by or guild.me, extra=extra)
    save_data()
    await send_log_message(
        guild, LOG_MODERATION_CHANNEL_ID, "🔓 Fin de punition",
        f"La punition de {membre.mention} est terminée ({extra}, par "
        f"{(resolved_by.mention if resolved_by else 'auto')}).",
        discord.Color.green(),
    )

    try:
        await membre.send(f"✅ Ta punition sur **{guild.name}** est terminée, tu as retrouvé accès aux salons !")
    except:
        pass


@bot.event
async def on_message(message):
    """Version fusionnée — il existait DEUX handlers on_message dans ce fichier
    (celui-ci + un plus ancien vers la ligne 2165), et @bot.event fait que seul
    le DERNIER défini l'emporte silencieusement (discord.py fait juste
    setattr(bot, 'on_message', coro), pas d'empilement). Le second (celui qui
    tournait vraiment) n'avait ni la vérification punition morse ni la liste
    complète des `!commande aide` — la résolution automatique d'une punition
    morse en tapant le bon code ne marchait donc jamais (incident du
    21/07/2026). Fusionné ici, l'ancien doublon supprimé."""
    if message.author.bot:
        return

    if await _antiraid_check(message):
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
            "historique_moderation": "**!historique_moderation [@membre]** (`!modlog`)\nAffiche le détail chronologique des sanctions d'un membre (warns, mutes, bans, punitions...), avec raison, modérateur et date.",
            "addrole": "**!addrole nom_du_rôle**\nCrée un nouveau rôle sur le serveur.",
            "giverole": "**!giverole @membre @role**\nDonne un rôle spécifique à un membre.",
            "construction": "**!construction**\nCrée une architecture complète de serveur communautaire (créateur du bot uniquement).",
            "nuke": "**!nuke**\n⚠️ DANGER : Supprime TOUS les salons du serveur (créateur du bot uniquement).",
            "lock": "**!lock**\nVerrouille le salon actuel (empêche d'écrire, sauf Fonda/Admin/Modérateur).",
            "unlock": "**!unlock**\nDéverrouille le salon actuel.",
            "lock_serveur": "**!lock_serveur**\nVerrouille tous les salons du serveur (sauf logs), sauf Fonda/Admin/Modérateur. Demande confirmation.",
            "unlock_serveur": "**!unlock_serveur**\nDéverrouille tous les salons actuellement verrouillés.",
            "rename": "**!rename @membre nouveau_pseudo**\nChange le pseudo d'un membre sur le serveur.",
            "say": "**!say message**\nFait dire quelque chose au bot (créateur du bot uniquement).",
            "dm": "**!dm @membre message**\nEnvoie un message privé à un membre (créateur du bot uniquement).",
            "dmall": "**!dmall message**\nEnvoie un message privé à tous les membres (créateur du bot uniquement).",
            "giveaway": "**!giveaway**\nOuvre un panneau à boutons pour configurer et lancer un giveaway (salon, durée, gagnants, lot).\nMode rapide : `!giveaway durée_heures nb_gagnants lot`. Plusieurs giveaways simultanés possibles.",
            "cancelgiveaway": "**!cancelgiveaway [id]**\nAnnule un giveaway précis (par ID) ou tous ceux du serveur si aucun ID.",
            "listgiveaways": "**!listgiveaways**\nAffiche la liste des giveaways en cours et leurs IDs.",
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

    if not message.content.startswith("!"):
        await _maybe_azog_ping_reaction(message)
        await _maybe_dev_ping_reaction(message)
        await _maybe_grammar_roast_reaction(message)

    if await economy_router.handle(message):
        return

    await bot.process_commands(message)



# =======================================================================
# ======================== NOUVELLES FONCTIONNALITÉS ====================
# =======================================================================

# ── Profil complet ───────────────────────────────────────────────────────
@bot.hybrid_command(name="profil", aliases=["profile", "stats"])
async def cmd_profil(ctx, member: discord.Member = None):
    member = member or ctx.author
    uid = str(member.id)
    uid_int = member.id

    cash    = coins[uid_int]
    coffre  = safes.get(uid, 0)
    total   = cash + coffre
    items   = owned_items.get(uid, {})
    item_lines = [f"{SHOP_ITEMS[int(k)]['name']} ×{v}" for k, v in items.items() if int(k) in SHOP_ITEMS and v > 0]
    h_crypto = {s: q for s, q in crypto_holdings.get(uid, {}).items() if q > 0.000001}
    crypto_val = sum(q * crypto_prices.get(s, 0) for s, q in h_crypto.items())
    cold_val = sum(
        b['qty'] * crypto_prices.get(s, 0)
        for s, batches in cold_wallets.get(uid, {}).items()
        for b in batches if b.get('qty', 0) > 0.000001
    )
    streak = daily_streaks.get(uid, {}).get('streak', 0)
    elo    = tournament_elo.get(uid, 1000)
    job    = jobs_data.get(uid, {}).get('job', None)
    job_str = JOBS[job]['name'] if job and job in JOBS else "Aucun"
    factory = factories.get(uid, {})
    workers = factory.get('workers', 0) if factory else 0
    imm_ok, imm_wait = _imm_ok(member.id)
    imm_str = None if imm_ok else f"🛡️ Grâce anti-vol active encore {imm_wait}"
    shield_rem = _shield_remaining_str(uid)
    if shield_rem:
        imm_str = (imm_str + "\n" if imm_str else "") + f"🛡️ Bouclier actif — **{shield_rem}** restant"

    embed = discord.Embed(
        title=f"👤 Profil de {member.display_name}",
        color=0x9b59b6
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="💵 Cash", value=f"{cash:,} coins", inline=True)
    embed.add_field(name="🔒 Coffre", value=f"{coffre:,} coins", inline=True)
    embed.add_field(name="💎 Total", value=f"{total:,} coins", inline=True)
    if crypto_val > 0 or cold_val > 0:
        crypto_parts = []
        if crypto_val > 0:
            crypto_parts.append(f"🔥 Chaud : **{crypto_val:,.0f}**")
        if cold_val > 0:
            crypto_parts.append(f"🔐 Cold : **{cold_val:,.0f}**")
        if crypto_val > 0 and cold_val > 0:
            crypto_parts.append(f"Total : **{crypto_val + cold_val:,.0f}**")
        embed.add_field(name="📈 Crypto (coins)", value="\n".join(crypto_parts), inline=True)
    embed.add_field(name="🔥 Streak Daily", value=f"{streak} jour{'s' if streak != 1 else ''}", inline=True)
    embed.add_field(name="🏆 ELO Tournoi", value=str(elo), inline=True)
    r1v1 = ranked_1v1.get(uid)
    if r1v1 and (r1v1.get('wins', 0) or r1v1.get('losses', 0)):
        rep = r1v1.get('reputation', 100)
        rep_str = f" · ⚠️ Réputation {rep}/100" if rep < 100 else ""
        embed.add_field(
            name="🥊 Classé 1v1",
            value=f"{_r1v1_tier_name(r1v1.get('points', 0))} — **{r1v1.get('points', 0)} pts**\n"
                  f"{r1v1.get('wins', 0)}V / {r1v1.get('losses', 0)}D{rep_str}",
            inline=True
        )
    embed.add_field(name="💼 Métier", value=job_str, inline=True)
    factory_rate = _factory_rate(workers, factories.get(uid, {}).get('upgraded', False) or _has_item(uid_int, 6))
    factory_pending = _factory_earnings(uid)
    embed.add_field(name="🏭 Usine", value=f"{workers} ouvrier{'s' if workers != 1 else ''} · {factory_rate:,.0f}/h · {factory_pending:,} en attente", inline=False)
    biz_lines = []
    for biz_key, biz_def in BIZ_DEFS.items():
        user_biz = businesses.get(uid, {}).get(biz_key)
        if user_biz:
            w       = user_biz.get('workers', 0)
            max_w   = biz_def['max_workers']
            upg     = user_biz.get('upgraded', False)
            pending = _biz_earnings(uid, biz_key)
            rate    = _biz_rate(biz_key, w, upg, user_biz.get('reputation', 0))
            upg_str = " 🔧" if upg else ""
            rep_str = ""
            if biz_key == 'restaurant':
                rep      = user_biz.get('reputation', 0)
                prog     = user_biz.get('rep_progress', 0)
                prog_bar = "🟡" * prog + "⚫" * (4 - prog)
                rep_str  = f" · {'⭐' * rep if rep else '☆ 0⭐'} {prog_bar}"
            biz_lines.append(
                f"{biz_def['emoji']} **{biz_def['name']}**{upg_str} — {w}/{max_w} emp · {rate:,.0f}/h · {pending:,} en attente{rep_str}"
            )
    if biz_lines:
        embed.add_field(name="🏢 Commerces", value='\n'.join(biz_lines), inline=False)
    if imm_str:
        embed.add_field(name="🛡️ Protection", value=imm_str, inline=False)
    if item_lines:
        embed.add_field(name="🎒 Inventaire", value='\n'.join(item_lines), inline=False)
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


# ── Anniversaires ────────────────────────────────────────────────────────
@bot.hybrid_command(name="anniversaire", aliases=["birthday", "anniv"])
async def cmd_anniversaire(ctx, date: str = None):
    uid = str(ctx.author.id)
    if date is None:
        bd = birthdays.get(uid)
        if not bd:
            return await ctx.send("❌ Aucun anniversaire enregistré. Ex : `!anniversaire 15/06`")
        return await ctx.send(f"🎂 Votre anniversaire est le **{bd['day']:02d}/{bd['month']:02d}**.")
    if uid in birthdays:
        bd = birthdays[uid]
        return await ctx.send(f"❌ Votre anniversaire est déjà enregistré le **{bd['day']:02d}/{bd['month']:02d}** et ne peut pas être modifié.")
    try:
        parts = date.replace('-', '/').split('/')
        day, month = int(parts[0]), int(parts[1])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            raise ValueError
    except (ValueError, IndexError):
        return await ctx.send("❌ Format invalide. Ex : `!anniversaire 15/06`")
    birthdays[uid] = {'day': day, 'month': month, 'guild_id': ctx.guild.id}
    save_data()
    await ctx.send(f"🎂 Anniversaire enregistré : **{day:02d}/{month:02d}** ! Je vous souhaiterai automatiquement bonne fête !\n⚠️ *Cette date est définitive et ne peut plus être modifiée.*")


@tasks.loop(hours=1)
async def check_birthdays():
    now = datetime.now()
    if now.hour != 9:
        return
    today_day, today_month = now.day, now.month
    for uid, bd in birthdays.items():
        if bd.get('day') == today_day and bd.get('month') == today_month:
            guild = bot.get_guild(bd.get('guild_id', 0))
            if not guild:
                continue
            member = guild.get_member(int(uid))
            if not member:
                continue
            coins[int(uid)] += 1000
            save_data()
            sys_ch = guild.system_channel
            if sys_ch:
                try:
                    await sys_ch.send(
                        f"🎂🎉 Joyeux anniversaire {member.mention} ! "
                        f"Le serveur t'offre **1 000 coins** pour ton jour spécial ! 🎁"
                    )
                except Exception:
                    pass


# ── Alertes prix crypto ───────────────────────────────────────────────────
@bot.hybrid_command(name="alerte_crypto", aliases=["alerte", "crypto_alert"])
async def cmd_alerte_crypto(ctx, symbol: str = None, target: str = None):
    uid = str(ctx.author.id)
    if symbol is None:
        alerts = crypto_alerts.get(uid, [])
        if not alerts:
            return await ctx.send("❌ Aucune alerte. Ex : `!alerte BTC >50000` (hausse) ou `!alerte BTC <30000` (baisse)")
        lines = [f"• **{a['symbol']}** : {'📈 ≥' if a['direction']=='up' else '📉 ≤'} {a['target']:,.0f} coins" for a in alerts]
        return await ctx.send(embed=discord.Embed(title="🔔 Vos alertes crypto", description='\n'.join(lines), color=0xf39c12))
    symbol = symbol.upper()
    if symbol not in CRYPTO_SYMBOLS:
        return await ctx.send(f"❌ Symbole invalide. Disponibles : {', '.join(CRYPTO_SYMBOLS)}")
    if target is None:
        return await ctx.send("❌ Précisez un prix. Ex : `!alerte BTC >50000` ou `!alerte BTC <30000`")
    # Syntaxe >prix ou <prix (direction explicite)
    forced_dir = None
    raw = target.strip()
    if raw.startswith('>'):
        forced_dir = 'up'
        raw = raw[1:]
    elif raw.startswith('<'):
        forced_dir = 'down'
        raw = raw[1:]
    try:
        target_price = float(raw)
    except ValueError:
        return await ctx.send("❌ Prix invalide. Ex : `!alerte BTC >50000` ou `!alerte BTC <30000`")
    current = crypto_prices[symbol]
    direction = forced_dir if forced_dir else ('up' if target_price > current else 'down')
    alerts = crypto_alerts.setdefault(uid, [])
    already = sum(1 for a in alerts if a['symbol'] == symbol)
    if already >= 2:
        return await ctx.send(f"❌ Maximum 2 alertes actives par crypto. Supprimez une alerte **{symbol}** avec `!suppr_alerte {symbol}`.")
    alerts.append({'symbol': symbol, 'target': target_price, 'direction': direction})
    save_data()
    sign = "≥" if direction == 'up' else "≤"
    dir_emoji = "📈" if direction == 'up' else "📉"
    await ctx.send(
        f"🔔 Alerte créée ! {dir_emoji} Je vous notifierai quand **{symbol}** atteindra **{sign} {target_price:,.0f} coins** "
        f"*(prix actuel : {current:,.2f})*"
    )


@bot.hybrid_command(name="suppr_alerte", aliases=["del_alerte", "remove_alert"])
async def cmd_suppr_alerte(ctx, symbol: str = None):
    uid = str(ctx.author.id)
    alerts = crypto_alerts.get(uid, [])
    if not alerts:
        return await ctx.send("❌ Aucune alerte active.")
    if symbol:
        symbol = symbol.upper()
        before = len(alerts)
        crypto_alerts[uid] = [a for a in alerts if a['symbol'] != symbol]
        removed = before - len(crypto_alerts[uid])
        save_data()
        return await ctx.send(f"✅ {removed} alerte(s) supprimée(s) pour **{symbol}**.")
    crypto_alerts[uid] = []
    save_data()
    await ctx.send("✅ Toutes vos alertes crypto ont été supprimées.")


async def _check_crypto_alerts():
    for uid, alerts in list(crypto_alerts.items()):
        remaining = []
        triggered = []
        for a in alerts:
            s = a['symbol']
            price = crypto_prices.get(s, 0)
            hit = (a['direction'] == 'up' and price >= a['target']) or \
                  (a['direction'] == 'down' and price <= a['target'])
            if hit:
                triggered.append((s, a['target'], price))
            else:
                remaining.append(a)
        if triggered:
            crypto_alerts[uid] = remaining
            try:
                user = await bot.fetch_user(int(uid))
                for s, target, price in triggered:
                    await user.send(
                        f"🔔 **Alerte crypto déclenchée !**\n"
                        f"**{s}** a atteint **{price:,.2f} coins** (cible : {target:,.0f})"
                    )
            except Exception:
                pass
    save_data()


# ── Classement crypto (portfolio) ─────────────────────────────────────────
@bot.hybrid_command(name="top_crypto", aliases=["classement_crypto", "crypto_top"])
async def cmd_top_crypto(ctx):
    guild_members = {m.id for m in ctx.guild.members if not m.bot}
    scores = []
    all_uids = set(crypto_holdings.keys()) | set(cold_wallets.keys())
    for uid_str in all_uids:
        uid_int = int(uid_str)
        if uid_int not in guild_members:
            continue
        hot_val  = sum(q * crypto_prices.get(s, 0) for s, q in crypto_holdings.get(uid_str, {}).items() if q > 0.000001)
        cold_val = sum(b['qty'] * crypto_prices.get(s, 0) for s, bl in cold_wallets.get(uid_str, {}).items() for b in bl if b.get('qty', 0) > 0.000001)
        total_val = hot_val + cold_val
        if total_val > 0:
            scores.append((uid_int, total_val, hot_val, cold_val))
    scores.sort(key=lambda x: x[1], reverse=True)
    top = scores[:10]
    if not top:
        return await ctx.send("Aucun investisseur crypto sur ce serveur pour l'instant.")
    medals = ['🥇','🥈','🥉'] + ['🔹'] * 7
    lines = []
    for i, (uid_int, total_val, hot_val, cold_val) in enumerate(top):
        m = ctx.guild.get_member(uid_int)
        name = m.display_name if m else f"<@{uid_int}>"
        detail = []
        if hot_val > 0:
            detail.append(f"🔥 {hot_val:,.0f}")
        if cold_val > 0:
            detail.append(f"🔐 {cold_val:,.0f}")
        detail_str = f" *({' + '.join(detail)})*" if len(detail) > 1 else ""
        lines.append(f"{medals[i]} **{name}** — ≈ {total_val:,.0f} coins{detail_str}")
    embed = discord.Embed(title="📈 Top Investisseurs Crypto", description='\n'.join(lines), color=0xf39c12)
    embed.set_footer(text="🔥 Portefeuille chaud · 🔐 Cold Wallet (verrouillé, non hackable)")
    await ctx.send(embed=embed)


# ── Stats serveur ─────────────────────────────────────────────────────────
@bot.hybrid_command(name="stats_serveur", aliases=["stats-serveur", "server_stats", "serveur"])
async def cmd_stats_serveur(ctx):
    guild_members = {m.id for m in ctx.guild.members if not m.bot}
    total_coins   = sum(coins[uid] + safes.get(str(uid), 0) for uid in guild_members)
    total_players = sum(1 for uid in guild_members if coins[uid] > 0)
    total_crypto = sum(
        sum(q * crypto_prices.get(s, 0) for s, q in crypto_holdings.get(str(uid), {}).items() if q > 0.000001) +
        sum(b['qty'] * crypto_prices.get(s, 0) for s, bl in cold_wallets.get(str(uid), {}).items() for b in bl if b.get('qty', 0) > 0.000001)
        for uid in guild_members
    )
    richest = max(guild_members, key=lambda uid: coins[uid] + safes.get(str(uid), 0), default=None)
    richest_str = ""
    if richest:
        m = ctx.guild.get_member(richest)
        richest_str = f"**{m.display_name if m else richest}** ({coins[richest] + safes.get(str(richest), 0):,} coins)"
    active_factories = sum(1 for uid in guild_members if factories.get(str(uid), {}).get('workers', 0) > 0)
    embed = discord.Embed(title="📊 Statistiques du Serveur", color=0x3498db)
    embed.add_field(name="👥 Joueurs actifs", value=str(total_players), inline=True)
    embed.add_field(name="💰 Coins en circulation", value=f"{total_coins:,}", inline=True)
    embed.add_field(name="📈 Valeur crypto totale", value=f"≈ {total_crypto:,.0f} coins", inline=True)
    embed.add_field(name="🏭 Usines actives", value=str(active_factories), inline=True)
    if richest_str:
        embed.add_field(name="👑 Joueur le plus riche", value=richest_str, inline=False)
    embed.set_footer(text=f"Serveur : {ctx.guild.name} • {datetime.now().strftime('%d/%m/%Y')}")
    await ctx.send(embed=embed)


# ── Marché entre joueurs ──────────────────────────────────────────────────

# ── ELO tournoi ───────────────────────────────────────────────────────────
def _update_elo(winner_id: int, loser_id: int):
    K = 32
    ew = tournament_elo.setdefault(str(winner_id), 1000)
    el = tournament_elo.setdefault(str(loser_id), 1000)
    exp_w = 1 / (1 + 10 ** ((el - ew) / 400))
    exp_l = 1 - exp_w
    tournament_elo[str(winner_id)] = round(ew + K * (1 - exp_w))
    tournament_elo[str(loser_id)]  = round(el + K * (0 - exp_l))
    save_data()


@bot.hybrid_command(name="classement_tournoi", aliases=["elo", "top_elo"])
async def cmd_classement_tournoi(ctx):
    guild_members = {m.id for m in ctx.guild.members if not m.bot}
    scores = [(uid_int, tournament_elo.get(str(uid_int), 1000)) for uid_int in guild_members if str(uid_int) in tournament_elo]
    scores.sort(key=lambda x: x[1], reverse=True)
    top = scores[:10]
    if not top:
        return await ctx.send("Aucun joueur avec un score ELO pour l'instant.")
    medals = ['🥇','🥈','🥉'] + ['🔹'] * 7
    lines = []
    for i, (uid_int, elo) in enumerate(top):
        m = ctx.guild.get_member(uid_int)
        name = m.display_name if m else f"<@{uid_int}>"
        lines.append(f"{medals[i]} **{name}** — {elo} pts ELO")
    embed = discord.Embed(title="🏆 Classement ELO Tournois", description='\n'.join(lines), color=0xe74c3c)
    await ctx.send(embed=embed)


# ── Config salons de logs ─────────────────────────────────────────────────
@bot.command(name="set_admin_log", aliases=["admin_log"])
async def cmd_set_admin_log(ctx, channel: discord.TextChannel = None):
    """Redondant avec `!set_logs admin` (même variable ADMIN_LOG_CHANNEL_ID,
    voir LOG_CATEGORY_VARS) — gardée pour compatibilité mais `!set_logs`
    couvre ce cas et les 6 autres catégories via un panel."""
    global ADMIN_LOG_CHANNEL_ID
    if channel is None:
        channel = ctx.channel
    ADMIN_LOG_CHANNEL_ID = channel.id
    save_data()
    await ctx.send(f"✅ Logs admin configurés dans {channel.mention}.\n💡 `!set_logs` gère ce salon (et les autres catégories de logs) via un panel.")


LOG_CATEGORY_VARS = {
    'admin':      'ADMIN_LOG_CHANNEL_ID',
    'moderation': 'LOG_MODERATION_CHANNEL_ID',
    'casino':     'CASINO_LOG_CHANNEL_ID',
    'general':    'LOG_GENERAL_CHANNEL_ID',
    'giveaway':   'LOG_GIVEAWAY_CHANNEL_ID',
    'ticket':     'LOG_TICKET_CHANNEL_ID',
    'depart':     'LEAVE_LOG_CHANNEL_ID',
}
LOG_CATEGORY_EMOJIS = {
    'admin': '🔐', 'moderation': '🛡️', 'casino': '🪙',
    'general': '📋', 'giveaway': '🎉', 'ticket': '🎫', 'depart': '👋',
}


class SetLogsView(discord.ui.View):
    """Sélecteur de catégorie + sélecteur natif de salon Discord (aucune saisie manuelle) —
    même esprit que les autres vues à sélecteurs du bot (ex. CooldownView)."""

    def __init__(self, guild, category='admin'):
        super().__init__(timeout=300)
        self.guild = guild
        self.category = category

        cat_options = [
            discord.SelectOption(
                label=cat.capitalize(), value=cat,
                emoji=LOG_CATEGORY_EMOJIS.get(cat), default=(cat == self.category)
            )
            for cat in LOG_CATEGORY_VARS
        ]
        self.cat_select = discord.ui.Select(placeholder="📋 Choisir une catégorie de log…", options=cat_options, row=0)
        self.cat_select.callback = self._on_category
        self.add_item(self.cat_select)

        self.channel_select = discord.ui.ChannelSelect(
            placeholder=f"Choisir le salon pour « {self.category} »…",
            channel_types=[discord.ChannelType.text], row=1,
        )
        self.channel_select.callback = self._on_channel
        self.add_item(self.channel_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux admins/owner.", ephemeral=True)
            return False
        return True

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="📋 Configuration des salons de logs", color=0x3498db)
        for cat, varname in LOG_CATEGORY_VARS.items():
            cid = globals().get(varname)
            ch = self.guild.get_channel(cid) if cid else None
            marker = "👉 " if cat == self.category else ""
            emoji = LOG_CATEGORY_EMOJIS.get(cat, '')
            embed.add_field(
                name=f"{marker}{emoji} {cat.capitalize()}",
                value=ch.mention if ch else "*non configuré*",
                inline=True,
            )
        embed.set_footer(text="Choisis une catégorie, puis un salon dans les menus ci-dessous.")
        return embed

    async def _on_category(self, interaction: discord.Interaction):
        view = SetLogsView(self.guild, self.cat_select.values[0])
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _on_channel(self, interaction: discord.Interaction):
        channel = self.channel_select.values[0]
        globals()[LOG_CATEGORY_VARS[self.category]] = channel.id
        save_data()
        view = SetLogsView(self.guild, self.category)
        await interaction.response.edit_message(
            content=f"✅ Logs **{self.category}** configurés dans <#{channel.id}>.",
            embed=view.build_embed(), view=view,
        )


@bot.command(name="set_logs", aliases=["logs_config"])
async def cmd_set_logs(ctx, categorie: str = None, channel: discord.TextChannel = None):
    if categorie is None:
        view = SetLogsView(ctx.guild)
        return await ctx.send(embed=view.build_embed(), view=view)

    if categorie.lower() not in LOG_CATEGORY_VARS and categorie.lower() != 'liste':
        cats = ", ".join(f"`{c}`" for c in LOG_CATEGORY_VARS)
        return await ctx.send(
            f"**Usage :** `!set_logs` (menus interactifs) · `!set_logs <catégorie> [#salon]` · `!set_logs liste`\n"
            f"Catégories : {cats}\n"
            f"Sans `#salon`, utilise le salon actuel."
        )
    categorie = categorie.lower()

    if categorie == 'liste':
        lines = []
        for cat, varname in LOG_CATEGORY_VARS.items():
            cid = globals().get(varname)
            ch = ctx.guild.get_channel(cid) if (ctx.guild and cid) else None
            lines.append(f"**{cat}** : {ch.mention if ch else '*non configuré*'}")
        return await ctx.send("📋 **Salons de logs configurés :**\n" + "\n".join(lines))

    if channel is None:
        channel = ctx.channel
    globals()[LOG_CATEGORY_VARS[categorie]] = channel.id
    save_data()
    await ctx.send(f"✅ Logs **{categorie}** configurés dans {channel.mention}.")


async def _admin_log(guild, title: str, description: str, color=0xe74c3c, author: discord.Member = None):
    if not ADMIN_LOG_CHANNEL_ID:
        return
    ch = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
    if not ch:
        return
    embed = discord.Embed(title=f"🔐 {title}", description=description, color=color, timestamp=discord.utils.utcnow())
    if author:
        embed.set_footer(text=f"Par {author.display_name} ({author.id})", icon_url=author.display_avatar.url)
    try:
        await ch.send(embed=embed)
    except Exception:
        pass


async def _casino_log(guild, title: str, description: str, color=0x2ecc71, author: discord.Member = None):
    """Même mécanique que _admin_log, mais dans le salon casino dédié (!set_logs casino).
    Si non configuré, ne fait rien (pas de fallback silencieux vers admin — évite de mélanger
    les deux catégories tant que l'admin n'a pas explicitement choisi un salon)."""
    if not CASINO_LOG_CHANNEL_ID:
        return
    ch = guild.get_channel(CASINO_LOG_CHANNEL_ID)
    if not ch:
        return
    embed = discord.Embed(title=f"🪙 {title}", description=description, color=color, timestamp=discord.utils.utcnow())
    if author:
        embed.set_footer(text=f"Par {author.display_name} ({author.id})", icon_url=author.display_avatar.url)
    try:
        await ch.send(embed=embed)
    except Exception:
        pass


# =======================================================================
# ======================== FIN CASINO ===================================
# =======================================================================

# =======================================================================
# ======================== SYSTÈME DRAFT (BAN PHASE) ====================
# =======================================================================

class BrawlerBanModal(discord.ui.Modal, title="🚫 Bannir un Brawler"):
    brawler = discord.ui.TextInput(
        label="Nom du brawler à bannir",
        placeholder="Ex : Shelly, Colt, Spike…",
        required=True,
        max_length=64,
    )

    def __init__(self, channel_id: int, uid: int, team: int):
        super().__init__()
        self.channel_id = channel_id
        self.uid = uid
        self.team = team

    async def on_submit(self, interaction: discord.Interaction):
        sess = draft_sessions.get(self.channel_id)
        if not sess or sess['phase'] != 'ban':
            return await interaction.response.send_message(
                "❌ Cette session draft n'est plus active.", ephemeral=True
            )

        brawler_name = self.brawler.value.strip()
        if not brawler_name:
            return await interaction.response.send_message("❌ Nom invalide.", ephemeral=True)

        ban_list  = sess['bans1'] if self.team == 1 else sess['bans2']
        team_list = sess['team1'] if self.team == 1 else sess['team2']

        if any(b['uid'] == self.uid for b in ban_list):
            return await interaction.response.send_message(
                "❌ Vous avez déjà banni un brawler.", ephemeral=True
            )

        ban_list.append({'uid': self.uid, 'brawler': brawler_name})

        my_bans = "\n".join(f"• <@{b['uid']}> → **{b['brawler']}**" for b in ban_list)
        await interaction.response.send_message(
            f"✅ Vous avez banni **{brawler_name}** !\n\n"
            f"**Bans de votre équipe :**\n{my_bans}\n\n"
            f"*{len(ban_list)}/{len(team_list)} membres ont banni.*",
            ephemeral=True,
        )

        total_members = len(sess['team1']) + len(sess['team2'])
        total_bans    = len(sess['bans1']) + len(sess['bans2'])
        if total_bans >= total_members:
            await _do_draft_reveal(interaction.channel, self.channel_id)


async def _do_draft_reveal(channel, channel_id: int):
    sess = draft_sessions.pop(channel_id, None)
    if not sess:
        return

    embed = discord.Embed(title="🚫 Phase de Ban — Résultats", color=0xff4444)

    t1_lines = "\n".join(
        f"• <@{b['uid']}> → **{b['brawler']}**" for b in sess['bans1']
    ) or "*Aucun ban*"
    t2_lines = "\n".join(
        f"• <@{b['uid']}> → **{b['brawler']}**" for b in sess['bans2']
    ) or "*Aucun ban*"

    embed.add_field(name="🔵 Équipe 1 — Bans", value=t1_lines, inline=True)
    embed.add_field(name="🔴 Équipe 2 — Bans", value=t2_lines, inline=True)

    banned_all = ", ".join(
        f"**{b['brawler']}**" for b in sess['bans1'] + sess['bans2']
    )
    if banned_all:
        embed.set_footer(text=f"Brawlers bannis : {banned_all}")

    await channel.send("🏁 **Tous les bans sont révélés !**", embed=embed)


def _draft_setup_embed(sess):
    n   = sess['mode']
    t1  = sess['team1']
    t2  = sess['team2']
    embed = discord.Embed(
        title=f"🎮 Draft {n}v{n} — Phase de recrutement",
        description=(
            f"**Capitaine équipe 1 :** <@{sess['cap1']}>\n"
            f"**Capitaine équipe 2 :** <@{sess['cap2']}>\n\n"
            f"Chaque équipe a besoin de **{n} joueur(s)**.\n"
            "Rejoignez une équipe en cliquant sur les boutons !"
        ),
        color=0x5865f2,
    )
    t1_val = "\n".join(f"• <@{uid}>" for uid in t1) if t1 else "*En attente…*"
    t2_val = "\n".join(f"• <@{uid}>" for uid in t2) if t2 else "*En attente…*"
    embed.add_field(name=f"🔵 Équipe 1 ({len(t1)}/{n})", value=t1_val, inline=True)
    embed.add_field(name=f"🔴 Équipe 2 ({len(t2)}/{n})", value=t2_val, inline=True)
    return embed


def _ban_phase_embed(sess):
    n  = sess['mode']
    t1 = sess['team1']
    t2 = sess['team2']
    embed = discord.Embed(
        title=f"🚫 Draft {n}v{n} — Phase de Ban",
        description=(
            "Chaque joueur peut bannir **1 brawler**.\n"
            "Cliquez sur **🚫 Bannir** pour entrer votre ban.\n"
            "Les bans de l'équipe adverse sont **cachés** jusqu'à la révélation.\n\n"
            "Quand tout le monde a banni, les bans sont révélés automatiquement."
        ),
        color=0xff4444,
    )
    n1 = len(sess['bans1'])
    n2 = len(sess['bans2'])
    embed.add_field(
        name=f"🔵 Équipe 1 — {n1}/{len(t1)} bans",
        value=" ".join(f"<@{uid}>" for uid in t1),
        inline=True,
    )
    embed.add_field(
        name=f"🔴 Équipe 2 — {n2}/{len(t2)} bans",
        value=" ".join(f"<@{uid}>" for uid in t2),
        inline=True,
    )
    return embed


class DraftSetupView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=300)
        self.channel_id = channel_id

    @discord.ui.button(label="🔵 Rejoindre Équipe 1", style=discord.ButtonStyle.primary)
    async def join_t1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._join(interaction, 1)

    @discord.ui.button(label="🔴 Rejoindre Équipe 2", style=discord.ButtonStyle.danger)
    async def join_t2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._join(interaction, 2)

    async def _join(self, interaction: discord.Interaction, team: int):
        sess = draft_sessions.get(self.channel_id)
        if not sess or sess['phase'] != 'setup':
            return await interaction.response.send_message(
                "❌ Cette session n'est plus active.", ephemeral=True
            )

        uid = interaction.user.id
        t1, t2 = sess['team1'], sess['team2']
        n = sess['mode']

        if uid in t1 or uid in t2:
            return await interaction.response.send_message(
                "❌ Vous avez déjà rejoint une équipe.", ephemeral=True
            )

        target = t1 if team == 1 else t2
        if len(target) >= n:
            return await interaction.response.send_message(
                f"❌ L'équipe {team} est déjà complète !", ephemeral=True
            )

        target.append(uid)
        await interaction.response.send_message(
            f"✅ Vous avez rejoint l'**Équipe {team}** !", ephemeral=True
        )

        embed = _draft_setup_embed(sess)

        if len(t1) == n and len(t2) == n:
            sess['phase'] = 'ban'
            for item in self.children:
                item.disabled = True
            await interaction.message.edit(embed=embed, view=self)
            ban_view  = BanPhaseView(self.channel_id)
            ban_embed = _ban_phase_embed(sess)
            mentions  = " ".join(f"<@{uid}>" for uid in t1 + t2)
            ban_msg = await interaction.channel.send(
                f"✅ Les deux équipes sont complètes ! La phase de ban commence.\n{mentions}",
                embed=ban_embed,
                view=ban_view,
            )
            sess['ban_msg_id'] = ban_msg.id
        else:
            await interaction.message.edit(embed=embed, view=self)

    async def on_timeout(self):
        draft_sessions.pop(self.channel_id, None)
        for item in self.children:
            item.disabled = True


class BanPhaseView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=600)
        self.channel_id = channel_id

    @discord.ui.button(label="🚫 Bannir un Brawler", style=discord.ButtonStyle.danger)
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        sess = draft_sessions.get(self.channel_id)
        if not sess or sess['phase'] != 'ban':
            return await interaction.response.send_message(
                "❌ Cette session n'est plus active.", ephemeral=True
            )

        uid = interaction.user.id
        if uid in sess['team1']:
            team = 1
        elif uid in sess['team2']:
            team = 2
        else:
            return await interaction.response.send_message(
                "❌ Vous ne faites pas partie de ce draft.", ephemeral=True
            )

        ban_list = sess['bans1'] if team == 1 else sess['bans2']
        if any(b['uid'] == uid for b in ban_list):
            return await interaction.response.send_message(
                "❌ Vous avez déjà banni un brawler.", ephemeral=True
            )

        await interaction.response.send_modal(
            BrawlerBanModal(self.channel_id, uid, team)
        )

    @discord.ui.button(label="🏁 Révéler maintenant", style=discord.ButtonStyle.secondary)
    async def force_reveal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        sess = draft_sessions.get(self.channel_id)
        if not sess or sess['phase'] != 'ban':
            return await interaction.response.send_message(
                "❌ Cette session n'est plus active.", ephemeral=True
            )

        uid      = interaction.user.id
        is_cap   = uid in (sess['cap1'], sess['cap2'])
        is_admin = interaction.user.guild_permissions.administrator

        if not is_cap and not is_admin:
            return await interaction.response.send_message(
                "❌ Seuls les capitaines ou admins peuvent forcer la révélation.", ephemeral=True
            )

        await interaction.response.send_message(
            "🏁 Révélation forcée par un capitaine/admin !", ephemeral=False
        )
        await _do_draft_reveal(interaction.channel, self.channel_id)

    async def on_timeout(self):
        draft_sessions.pop(self.channel_id, None)


@bot.hybrid_command(name="draft")
async def cmd_draft(ctx, mode: str = None, captain2: discord.Member = None):
    """Phase de ban style Brawl Stars — !draft <1v1|2v2|3v3|4v4|5v5> @capitaine2"""
    if ctx.channel.id in draft_sessions:
        return await ctx.send(
            "❌ Une session draft est déjà en cours dans ce salon. "
            "Attendez qu'elle se termine ou qu'elle expire."
        )

    if not mode or not captain2:
        return await ctx.send(
            "❌ Usage : `!draft <mode> @capitaine2`\n"
            "Modes disponibles : `1v1`, `2v2`, `3v3`, `4v4`, `5v5`"
        )

    mode_str = mode.lower().strip()
    valid_modes = {'1v1': 1, '2v2': 2, '3v3': 3, '4v4': 4, '5v5': 5}
    if mode_str not in valid_modes:
        return await ctx.send(
            "❌ Mode invalide. Utilisez : `1v1`, `2v2`, `3v3`, `4v4` ou `5v5`"
        )

    n    = valid_modes[mode_str]
    cap1 = ctx.author
    cap2 = captain2

    if cap2.bot:
        return await ctx.send("❌ Un bot ne peut pas être capitaine.")
    if cap1.id == cap2.id:
        return await ctx.send("❌ Les deux capitaines doivent être différents.")

    sess = {
        'mode':       n,
        'team1':      [cap1.id],
        'team2':      [cap2.id],
        'cap1':       cap1.id,
        'cap2':       cap2.id,
        'bans1':      [],
        'bans2':      [],
        'phase':      'setup',
        'ban_msg_id': None,
    }
    draft_sessions[ctx.channel.id] = sess

    if n == 1:
        sess['phase'] = 'ban'
        ban_view  = BanPhaseView(ctx.channel.id)
        ban_embed = _ban_phase_embed(sess)
        await ctx.send(
            f"🎮 **Draft 1v1** : <@{cap1.id}> vs <@{cap2.id}> — Phase de ban !\n"
            f"<@{cap1.id}> <@{cap2.id}>",
            embed=ban_embed,
            view=ban_view,
        )
    else:
        setup_view  = DraftSetupView(ctx.channel.id)
        setup_embed = _draft_setup_embed(sess)
        await ctx.send(
            f"🎮 **Draft {mode_str}** lancé par <@{cap1.id}> !\n"
            f"Capitaines : <@{cap1.id}> (🔵) vs <@{cap2.id}> (🔴)\n"
            f"Rejoignez une équipe ci-dessous.",
            embed=setup_embed,
            view=setup_view,
        )


# === COMMERCES ============================================================

class BusinessView(discord.ui.View):
    def __init__(self, author_id, biz_key):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.biz_key   = biz_key
        self._build()

    def _build(self):
        self.clear_items()
        uid = str(self.author_id)
        biz = BIZ_DEFS[self.biz_key]
        b   = businesses.get(uid, {}).get(self.biz_key, {})
        upgraded = b.get('upgraded', False)
        v = self

        hire = discord.ui.Button(label="Embaucher", style=discord.ButtonStyle.success, emoji="👷")
        async def on_hire(inter, _v=v): await _v._hire(inter)
        hire.callback = on_hire
        self.add_item(hire)

        collect = discord.ui.Button(label="Collecter", style=discord.ButtonStyle.primary, emoji="💰")
        async def on_collect(inter, _v=v): await _v._collect(inter)
        collect.callback = on_collect
        self.add_item(collect)

        if biz.get('upgrade_cost'):
            if upgraded:
                btn = discord.ui.Button(label="Améliorée ✅", style=discord.ButtonStyle.secondary, emoji="🔧", disabled=True)
            else:
                btn = discord.ui.Button(label=f"Améliorer ({biz['upgrade_cost']:,})", style=discord.ButtonStyle.secondary, emoji="🔧")
                async def on_upg(inter, _v=v): await _v._upgrade(inter)
                btn.callback = on_upg
            self.add_item(btn)

        if self.biz_key == 'restaurant':
            rep_btn = discord.ui.Button(label="Réputation", style=discord.ButtonStyle.secondary, emoji="⭐")
            async def on_rep(inter, _v=v): await _v._rep_info(inter)
            rep_btn.callback = on_rep
            self.add_item(rep_btn)

        refresh = discord.ui.Button(label="Actualiser", style=discord.ButtonStyle.secondary, emoji="🔄")
        async def on_refresh(inter, _v=v): await _v._refresh(inter)
        refresh.callback = on_refresh
        self.add_item(refresh)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            biz = BIZ_DEFS[self.biz_key]
            await interaction.response.send_message(
                f"❌ Ce n'est pas votre {biz['name']}. Tapez `!{self.biz_key}`.", ephemeral=True)
            return False
        return True

    async def _hire(self, interaction: discord.Interaction):
        uid = str(self.author_id)
        bk  = self.biz_key
        biz = BIZ_DEFS[bk]
        b   = businesses.get(uid, {}).get(bk)
        if not b:
            return await interaction.response.send_message("❌ Commerce non ouvert.", ephemeral=True)
        remaining = _biz_hire_remaining(uid, bk)
        if remaining > 0 and not (self.author_id == 550678866839207937 and bk == 'restaurant'):
            h, m = int(remaining // 3600), int((remaining % 3600) // 60)
            return await interaction.response.send_message(f"⏳ Attendez **{h}h {m}min** avant d'embaucher.", ephemeral=True)
        cost = _biz_cost_next(bk, b['workers'])
        if cost is None:
            return await interaction.response.send_message(f"❌ Maximum de **{biz['max_workers']} employés** atteint.", ephemeral=True)
        if coins[self.author_id] < cost:
            return await interaction.response.send_message(f"❌ Il vous faut **{cost:,} coins**. Solde : **{coins[self.author_id]:,}**", ephemeral=True)
        pending = _biz_earnings(uid, bk)
        if pending > 0:
            coins[self.author_id] += pending
            b['last'] = datetime.now().isoformat()
        coins[self.author_id] -= cost
        b['workers']   += 1
        b['last_hire']  = datetime.now().isoformat()
        save_data()
        self._build()
        await interaction.response.edit_message(embed=_biz_embed(self.author_id, bk), view=self)
        cd_h = BIZ_DEFS[bk].get('hire_cd_hours', cooldown_h('embaucher'))
        await interaction.followup.send(
            f"👷 Employé #{b['workers']} recruté pour **{cost:,} coins** !\n"
            f"⏳ Prochain employé dispo dans **{cd_h:g}h**.", ephemeral=True)

    async def _collect(self, interaction: discord.Interaction):
        uid = str(self.author_id)
        bk  = self.biz_key
        b   = businesses.get(uid, {}).get(bk)
        if not b:
            return await interaction.response.send_message("❌ Commerce non ouvert.", ephemeral=True)
        pending = _biz_earnings(uid, bk)
        if pending <= 0:
            return await interaction.response.send_message("❌ Aucun gain à collecter. Embauchez des employés !", ephemeral=True)
        if bk == 'restaurant':
            last_col = b.get('last_collect')
            if last_col:
                h_since = (datetime.now() - datetime.fromisoformat(last_col)).total_seconds() / 3600
                if h_since > 24:
                    b['reputation']   = 0
                    b['rep_progress'] = 0
                elif h_since >= 12:
                    prog = b.get('rep_progress', 0) + 1
                    if prog >= 4:
                        b['reputation']   = min(5, b.get('reputation', 0) + 1)
                        b['rep_progress'] = 0
                    else:
                        b['rep_progress'] = prog
                # < 12h : argent collecté mais pas de progression rep
            b['last_collect'] = datetime.now().isoformat()
        coins[self.author_id] += pending
        b['last'] = datetime.now().isoformat()
        save_data()
        await interaction.response.edit_message(embed=_biz_embed(self.author_id, bk), view=self)
        await interaction.followup.send(
            f"{BIZ_DEFS[bk]['emoji']} **{pending:,} coins** collectés ! Solde : **{coins[self.author_id]:,}**", ephemeral=True)

    async def _upgrade(self, interaction: discord.Interaction):
        uid = str(self.author_id)
        bk  = self.biz_key
        biz = BIZ_DEFS[bk]
        b   = businesses.get(uid, {}).get(bk)
        if not b:
            return await interaction.response.send_message("❌ Commerce non ouvert.", ephemeral=True)
        if b.get('upgraded', False):
            return await interaction.response.send_message("✅ Ce commerce est déjà amélioré.", ephemeral=True)
        cost = biz['upgrade_cost']
        if coins[self.author_id] < cost:
            return await interaction.response.send_message(f"❌ Il vous faut **{cost:,} coins**. Solde : **{coins[self.author_id]:,}**", ephemeral=True)
        coins[self.author_id] -= cost
        b['upgraded'] = True
        save_data()
        self._build()
        await interaction.response.edit_message(embed=_biz_embed(self.author_id, bk), view=self)
        await interaction.followup.send(
            f"🔧 **{biz['emoji']} {biz['name']}** améliorée ! +{int(biz['upgrade_bonus']*100)}% production.\n"
            f"💰 Solde : **{coins[self.author_id]:,} coins**", ephemeral=True)

    async def _rep_info(self, interaction: discord.Interaction):
        uid = str(self.author_id)
        b   = businesses.get(uid, {}).get('restaurant', {})
        rep = b.get('reputation', 0)
        biz = BIZ_DEFS['restaurant']
        stars = '⭐' * rep + '☆' * (5 - rep)
        last_col = b.get('last_collect')
        prog = b.get('rep_progress', 0)
        if last_col:
            h_since = (datetime.now() - datetime.fromisoformat(last_col)).total_seconds() / 3600
            if h_since > 24:
                trend = "💀 **Réputation perdue** — tu es repassé à 0⭐ !"
            elif h_since >= 12:
                trend = f"✅ Collecte valide maintenant *(encore {4 - prog} prog pour +⭐)*"
            else:
                h_left = 12 - h_since
                trend = f"⏳ Prochaine collecte valide dans **{int(h_left)}h {int((h_left % 1) * 60)}min** *(trop récente)*"
        else:
            trend = "📈 Première collecte = début de progression"
        prog_bar = "🟡" * prog + "⚫" * (4 - prog)
        await interaction.response.send_message(
            f"🌟 **Réputation** : {stars}\n"
            f"✖️ Multiplicateur : **×{biz['rep_mult'][rep]:.2f}**\n"
            f"📊 Progression prochaine ⭐ : {prog_bar} ({prog}/4)\n"
            f"{trend}\n\n"
            "**Règles :**\n"
            "• Collectez toutes les **12h–24h** → +1 progression\n"
            "• **4 progressions** = +⭐\n"
            "• Sans collecte **+24h** → retour à **0⭐** immédiat\n"
            "• ⭐⭐⭐⭐⭐ = **×1.70** de production", ephemeral=True)

    async def _refresh(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=_biz_embed(self.author_id, self.biz_key), view=self)


async def _cmd_biz(ctx, biz_key):
    uid = str(ctx.author.id)
    b   = businesses.get(uid, {}).get(biz_key)
    biz = BIZ_DEFS[biz_key]
    if not b:
        ok, reason = _biz_unlock_status(uid, biz_key)
        if not ok:
            return await ctx.send(
                f"🔒 **{biz['emoji']} {biz['name']}** non débloquée.\n"
                f"Prérequis manquants : **{reason}**\n"
                f"Remplissez les conditions puis achetez-la avec `!acheter {biz['shop_item']}`.")
        return await ctx.send(
            f"{biz['emoji']} **{biz['name']}** disponible !\n"
            f"Ouvrez-la pour **{biz['open_cost']:,} coins** avec `!acheter {biz['shop_item']}`.")
    await ctx.send(embed=_biz_embed(ctx.author.id, biz_key), view=BusinessView(ctx.author.id, biz_key))

@bot.hybrid_command(name="epicerie")
async def cmd_epicerie(ctx):
    await _cmd_biz(ctx, 'epicerie')

@bot.hybrid_command(name="fastfood", aliases=["fast_food"])
async def cmd_fastfood(ctx):
    await _cmd_biz(ctx, 'fastfood')

@bot.hybrid_command(name="restaurant", aliases=["resto"])
async def cmd_restaurant(ctx):
    await _cmd_biz(ctx, 'restaurant')


# === CARTE COMMUNAUTAIRE ===

_CARTE_COLORS = [
    '#FF4444', '#4477FF', '#44DD88', '#FFD700',
    '#FF44FF', '#00DDDD', '#FF8800', '#AA44FF',
    '#FF4488', '#88FF44', '#00BBFF', '#FF6622',
]

_CARTE_PRESETS = {
    'monde':  {'label': '🌍 Monde',   'zoom': 2, 'clat': 25.0, 'clon': 10.0, 'cluster_max': 2},
    'europe': {'label': '🌍 Europe',  'zoom': 4, 'clat': 52.0, 'clon': 12.0, 'cluster_max': 3},
    'france': {'label': '🇫🇷 France', 'zoom': 6, 'clat': 46.5, 'clon': 2.5, 'cluster_max': None},
}

def _latlon_to_px(lat, lon, zoom, center_lat, center_lon, img_w, img_h):
    n = 2 ** zoom
    ts = 256
    def _x(lo): return (lo + 180) / 360 * n
    def _y(la):
        r = math.radians(la)
        return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n
    px = (_x(lon) - _x(center_lon)) * ts + img_w / 2
    py = (_y(lat) - _y(center_lat)) * ts + img_h / 2
    return int(px), int(py)

def _carte_font(size):
    from PIL import ImageFont
    for path in [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf',
    ]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ── Brawl Stars : liaison de compte + rôles auto (trophées / classé) ────────
BS_API_BASE  = "https://bsproxy.royaleapi.dev/v1"

RANKED_TIERS = [
    (0,     "Bronze 1"),   (250,   "Bronze 2"),   (500,   "Bronze 3"),
    (750,   "Argent 1"),   (1000,  "Argent 2"),   (1250,  "Argent 3"),
    (1500,  "Or 1"),       (2000,  "Or 2"),        (2500,  "Or 3"),
    (3000,  "Diamant 1"),  (3500,  "Diamant 2"),   (4000,  "Diamant 3"),
    (4500,  "Mythique 1"), (5000,  "Mythique 2"),  (5500,  "Mythique 3"),
    (6000,  "Légendaire 1"),  (6750,  "Légendaire 2"),   (7500,  "Légendaire 3"),
    (8250,  "Masters 1"),  (9250,  "Masters 2"),   (10250, "Masters 3"),
    (11250, "Pro"),
]

def _ranked_tier_name(points: int) -> str:
    tier = RANKED_TIERS[0][1]
    for min_pts, name in RANKED_TIERS:
        if points >= min_pts:
            tier = name
        else:
            break
    return tier


# Nom de palier tel que renvoyé par l'API officielle (ex: "MASTERS I", "LEGENDARY I",
# "GOLD II" — anglais, tout en majuscules, chiffre romain ; confirmé sur payload réel le
# 22/07/2026, PRO sans chiffre). Sert de fallback prioritaire sur highestAllTimeRankedRankName
# pour les comptes dont le record all-time a été fait sous l'ancien système Ranked, où le
# score numérique (highestAllTimeRankedElo) est absent/à 0 mais le nom reste renseigné.
_RANKED_TIER_NAME_MAP = {
    "BRONZE I": "Bronze 1", "BRONZE II": "Bronze 2", "BRONZE III": "Bronze 3",
    "SILVER I": "Argent 1", "SILVER II": "Argent 2", "SILVER III": "Argent 3",
    "GOLD I": "Or 1", "GOLD II": "Or 2", "GOLD III": "Or 3",
    "DIAMOND I": "Diamant 1", "DIAMOND II": "Diamant 2", "DIAMOND III": "Diamant 3",
    "MYTHIC I": "Mythique 1", "MYTHIC II": "Mythique 2", "MYTHIC III": "Mythique 3",
    "LEGENDARY I": "Légendaire 1", "LEGENDARY II": "Légendaire 2", "LEGENDARY III": "Légendaire 3",
    "MASTERS I": "Masters 1", "MASTERS II": "Masters 2", "MASTERS III": "Masters 3",
    "PRO": "Pro",
}


def _ranked_tier_name_from_api_name(name) -> str | None:
    if not name:
        return None
    return _RANKED_TIER_NAME_MAP.get(name.strip().upper())


def _bs_strip_markup(text):
    """Retire les balises de couleur du jeu (ex: '<c4>ProjetZ</c>' -> 'ProjetZ')."""
    if not text:
        return text
    return re.sub(r'</?c\d*>', '', text).strip()


_BS_GREEK_MAP = {
    'Δ': 'delta', 'α': 'alpha', 'β': 'beta', 'Ω': 'omega', 'Σ': 'sigma',
    'θ': 'theta', 'λ': 'lambda', 'π': 'pi', 'Φ': 'phi', 'Ψ': 'psi', 'Χ': 'chi',
}

def _bs_slug(name: str) -> str:
    """Nom de clan -> nom de commande valide (ex: 'ProjetΔ' -> 'projetdelta')."""
    transliterated = ''.join(_BS_GREEK_MAP.get(ch, ch) for ch in (name or ''))
    ascii_name = unicodedata.normalize('NFKD', transliterated).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]', '', ascii_name.lower()) or 'clan'


def _bs_alias(slug: str, reserved: set) -> str:
    """Alias court pour une commande de clan (ex: 'projetx' -> 'px'), en évitant les collisions
    avec les commandes déjà enregistrées et les autres clans de la famille."""
    candidates = []
    if slug.startswith('projet') and len(slug) > 6:
        candidates.append('p' + slug[6])
    candidates.append(slug[:2])
    candidates.append(slug[:3])
    for c in candidates:
        if c and c not in reserved and bot.get_command(c) is None:
            return c
    base, i = (slug[:3] or 'cl'), 1
    while f"{base}{i}" in reserved or bot.get_command(f"{base}{i}") is not None:
        i += 1
    return f"{base}{i}"


def _bs_extract_ranked(player: dict):
    """Points classés (actuels ET record all-time) à partir d'un payload /players officiel
    déjà récupéré — rankedElo/rankedRankName/highestAllTimeRankedElo/highestAllTimeRankedRankName
    sont exposés directement par l'API officielle (contrairement à ce qu'indiquait un ancien
    commentaire ici, qui passait par api.rnt.dev, une source tierce non officielle, pour cette
    donnée). Le nom de palier renvoyé par l'API prime sur le calcul par score : ça couvre aussi
    les comptes dont le record all-time a été fait sous l'ancien système Ranked (score absent/à
    0, ou pas sur la même échelle que le système actuel, dans ce cas — mais le nom de palier
    reste renseigné) — voir TIER_LOGIC_BRIEF.md. highest_rank (highestAllTimeRankedRank, un
    entier de palier fourni par l'API) sert à trier plusieurs records all-time entre eux de façon
    fiable même quand highest_pts n'est pas comparable d'un joueur à l'autre pour cette raison.
    Retourne (pts, tier, highest_pts, highest_tier, highest_rank), chaque valeur pouvant être
    None si absente du payload."""
    pts = player.get('rankedElo')
    tier = _ranked_tier_name_from_api_name(player.get('rankedRankName'))
    if tier is None and pts is not None:
        tier = _ranked_tier_name(pts)

    highest_pts = player.get('highestAllTimeRankedElo')
    highest_tier = _ranked_tier_name_from_api_name(player.get('highestAllTimeRankedRankName'))
    if highest_tier is None and highest_pts is not None:
        highest_tier = _ranked_tier_name(highest_pts)
    highest_rank = player.get('highestAllTimeRankedRank')

    return pts, tier, highest_pts, highest_tier, highest_rank


async def _bs_fetch_ranked_pts(session: aiohttp.ClientSession, clean_tag: str):
    """Variante réseau de _bs_extract_ranked, pour les cas où on n'a pas déjà le payload
    joueur sous la main (sync en masse depuis la liste de membres d'un clan, qui elle ne
    contient pas ces champs — seul l'appel /players par joueur les expose). _bs_fetch_player
    a déjà son payload et appelle _bs_extract_ranked directement, sans passer par ici."""
    if not BRAWLSTARS_API_KEY:
        return None, None, None, None, None
    headers = {"Authorization": f"Bearer {BRAWLSTARS_API_KEY}", "Accept": "application/json"}
    try:
        async with session.get(
            f"{BS_API_BASE}/players/%23{clean_tag}", headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                return None, None, None, None, None
            player = await resp.json(content_type=None)
    except Exception:
        return None, None, None, None, None  # Rang classé indisponible — pas bloquant pour l'appelant
    return _bs_extract_ranked(player)


async def _bs_fetch_player(tag: str):
    """Trophées + rang classé, le tout depuis l'API officielle (proxy RoyaleAPI, pas besoin
    d'IP fixe) — un seul appel /players, le rang classé y est exposé directement (voir
    _bs_extract_ranked). Retourne (data: dict|None, err: str|None)."""
    clean = tag.strip().lstrip('#').upper()
    if not clean:
        return None, "❌ Tag invalide. Exemple : `#2ABC123`."
    if not BRAWLSTARS_API_KEY:
        return None, "🔑 Aucune clé API Brawl Stars configurée. Préviens un admin."

    headers = {"Authorization": f"Bearer {BRAWLSTARS_API_KEY}", "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BS_API_BASE}/players/%23{clean}",
                headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 404:
                    return None, "❓ Joueur introuvable. Vérifie que le tag est correct."
                if resp.status == 403:
                    return None, "🔑 Clé API Brawl Stars invalide ou expirée. Préviens un admin."
                if resp.status == 429:
                    return None, "⏳ Trop de requêtes vers l'API Brawl Stars, réessaie dans quelques secondes."
                if resp.status == 503:
                    return None, "🔧 L'API Brawl Stars est en maintenance, réessaie plus tard."
                if resp.status != 200:
                    return None, f"❌ Erreur inattendue de l'API Brawl Stars ({resp.status})."
                player = await resp.json(content_type=None)

            ranked_pts, ranked_tier, highest_ranked_pts, highest_ranked_tier, highest_ranked_rank = _bs_extract_ranked(player)
    except Exception as e:
        logging.warning(f"[bs] erreur réseau API Brawl Stars pour tag '{clean}': {type(e).__name__}: {e}")
        return None, "🌐 Impossible de contacter l'API Brawl Stars. Réessaie plus tard."

    return {
        'tag': player.get('tag', f"#{clean}"),
        'name': _bs_strip_markup(player.get('name')) or '?',
        'trophies': player.get('trophies', 0),
        'ranked_pts': ranked_pts,
        'ranked_tier': ranked_tier,
        'highest_ranked_pts': highest_ranked_pts,
        'highest_ranked_tier': highest_ranked_tier,
        'highest_ranked_rank': highest_ranked_rank,
        'club': _bs_strip_markup((player.get('club') or {}).get('name')),
        'victories_3v3': player.get('3vs3Victories', 0),
        'victories_solo': player.get('soloVictories', 0),
        'victories_duo': player.get('duoVictories', 0),
        'exp_level': player.get('expLevel', 0),
    }, None


async def _bs_fetch_club(tag: str):
    """Un seul appel API officiel = tag/nom/trophées de TOUS les membres du clan
    (contrairement au rang classé qui nécessite un appel par joueur).
    Retourne (data: dict|None, err: str|None)."""
    clean = tag.strip().lstrip('#').upper()
    if not clean:
        return None, "❌ Tag de clan invalide."
    if not BRAWLSTARS_API_KEY:
        return None, "🔑 Aucune clé API Brawl Stars configurée. Préviens un admin."

    headers = {"Authorization": f"Bearer {BRAWLSTARS_API_KEY}", "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BS_API_BASE}/clubs/%23{clean}",
                headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 404:
                    return None, f"Clan `#{clean}` introuvable."
                if resp.status == 403:
                    return None, "Clé API Brawl Stars invalide ou expirée."
                if resp.status == 429:
                    return None, "Trop de requêtes vers l'API Brawl Stars, réessaie dans quelques secondes."
                if resp.status == 503:
                    return None, "L'API Brawl Stars est en maintenance, réessaie plus tard."
                if resp.status != 200:
                    return None, f"Erreur inattendue de l'API Brawl Stars ({resp.status})."
                club = await resp.json(content_type=None)
    except Exception as e:
        logging.warning(f"[bs] erreur réseau clan '{clean}': {type(e).__name__}: {e}")
        return None, "Impossible de contacter l'API Brawl Stars."

    members = [
        {
            'tag': (m.get('tag') or '').lstrip('#').upper(),
            'name': _bs_strip_markup(m.get('name')) or '?',
            'trophies': m.get('trophies', 0),
            'role': m.get('role', 'member'),
        }
        for m in club.get('members', [])
    ]
    return {
        'tag': club.get('tag', f"#{clean}"),
        'name': _bs_strip_markup(club.get('name')) or '?',
        'trophies': club.get('trophies', 0),
        'description': _bs_strip_markup(club.get('description', '')),
        'type': club.get('type', 'open'),
        'requiredTrophies': club.get('requiredTrophies', 0),
        'members': members,
    }, None


def _bs_best_tier(category: str, value: int):
    """Retourne (seuil_min, role_id) du palier le plus haut atteint, ou None."""
    best = None
    for min_str, role_id in bs_role_config[category].items():
        try:
            mn = int(min_str)
        except (ValueError, TypeError):
            continue
        if value >= mn and (best is None or mn > best[0]):
            best = (mn, role_id)
    return best


def _bs_expected_role(category: str, value: int):
    best = _bs_best_tier(category, value)
    return best[1] if best else None


BS_CONGRATS_CHANNEL_ID = 1513110809109205151

async def _bs_announce_promotion(member: discord.Member, old_acc: dict, new_data: dict):
    """Poste un message de félicitations dans le salon général si le membre vient de
    passer un palier (trophées et/ou classé) par rapport à son état précédent connu.
    Ne doit JAMAIS être appelé lors du tout premier !bslink (pas d'état précédent =
    pas de progression, juste une inscription) — seulement lors d'un rafraîchissement
    ultérieur (!bsprofil ou sync horaire)."""
    if not old_acc:
        return  # pas d'état précédent → rien à comparer, on ne notifie pas

    gains = []

    old_trophy = _bs_best_tier('trophies', old_acc.get('trophies', 0))
    new_trophy = _bs_best_tier('trophies', new_data.get('trophies', 0))
    if new_trophy and (old_trophy is None or new_trophy[0] > old_trophy[0]):
        gains.append(f"🏆 **{new_data.get('trophies', 0):,} trophées** → <@&{new_trophy[1]}>")

    old_ranked_pts = old_acc.get('ranked_pts')
    new_ranked_pts = new_data.get('ranked_pts')
    if new_ranked_pts is not None:
        old_rank = _bs_best_tier('ranked', old_ranked_pts) if old_ranked_pts is not None else None
        new_rank = _bs_best_tier('ranked', new_ranked_pts)
        if new_rank and (old_rank is None or new_rank[0] > old_rank[0]):
            tier_label = new_data.get('ranked_tier') or 'Classé'
            gains.append(f"🎖️ **{tier_label}** ({new_ranked_pts:,} pts) → <@&{new_rank[1]}>")

    if not gains:
        return

    channel = bot.get_channel(BS_CONGRATS_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(
        title="🎉 Nouveau palier Brawl Stars !",
        description=f"{member.mention} vient de progresser :\n" + "\n".join(gains),
        color=0xf1c40f
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    try:
        await channel.send(content=member.mention, embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
    except (discord.Forbidden, discord.HTTPException):
        pass


async def _bs_sync_member_roles(member: discord.Member, trophies: int, ranked_pts):
    """Ajoute/retire les rôles Brawl Stars du membre selon ses trophées (toujours à jour,
    source fiable) et ses points classés (seulement si `ranked_pts` est connu — sinon on ne
    touche pas aux rôles classé plutôt que de les retirer à tort)."""
    guild = member.guild
    to_add, to_remove = [], []

    expected_trophy_rid = _bs_expected_role('trophies', trophies)
    for role_id in set(bs_role_config['trophies'].values()):
        role = guild.get_role(role_id)
        if not role:
            continue
        has_it = role in member.roles
        should_have = (role_id == expected_trophy_rid)
        if should_have and not has_it:
            to_add.append(role)
        elif not should_have and has_it:
            to_remove.append(role)

    if ranked_pts is not None:
        expected_rank_rid = _bs_expected_role('ranked', ranked_pts)
        for role_id in set(bs_role_config['ranked'].values()):
            role = guild.get_role(role_id)
            if not role:
                continue
            has_it = role in member.roles
            should_have = (role_id == expected_rank_rid)
            if should_have and not has_it:
                to_add.append(role)
            elif not should_have and has_it:
                to_remove.append(role)

    try:
        if to_remove:
            await member.remove_roles(*to_remove, reason="Sync Brawl Stars")
        if to_add:
            await member.add_roles(*to_add, reason="Sync Brawl Stars")
    except (discord.Forbidden, discord.HTTPException):
        pass


def _bs_embed(member: discord.Member, acc: dict) -> discord.Embed:
    embed = discord.Embed(title=f"🎮 Profil Brawl Stars de {member.display_name}", color=0xf1c40f)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🏷️ Pseudo", value=acc.get('name', '?'), inline=True)
    embed.add_field(name="🔖 Tag", value=acc.get('tag', '?'), inline=True)
    embed.add_field(name="🏆 Trophées", value=f"{acc.get('trophies', 0):,}", inline=True)
    if acc.get('ranked_tier'):
        embed.add_field(name="🎖️ Rang classé", value=f"{acc['ranked_tier']} ({acc.get('ranked_pts', 0):,} pts)", inline=True)
    else:
        embed.add_field(name="🎖️ Rang classé", value="Indisponible pour le moment", inline=True)
    embed.add_field(name="🌿 Club", value=acc.get('club') or "Sans club", inline=True)
    return embed


async def _bslink_apply(discord_id: str, tag: str, member: discord.Member = None):
    """Cœur de !bslink, réutilisé par la commande Discord ET par /api/bslink
    (liaison depuis le site — voir keep_alive.py). Retourne (data, err)."""
    data, err = await _bs_fetch_player(tag)
    if err:
        return None, err

    bs_accounts[discord_id] = data
    save_data()

    if member is None:
        guild = bot.get_guild(BS_FAMILY_GUILD_ID)
        member = guild.get_member(int(discord_id)) if guild else None
    if member:
        await _bs_sync_member_roles(member, data['trophies'], data['ranked_pts'])

    return data, None


@bot.hybrid_command(name="bslink", aliases=["lierbs"])
async def cmd_bslink(ctx, tag: str):
    data, err = await _bslink_apply(str(ctx.author.id), tag, member=ctx.author if ctx.guild else None)
    if err:
        return await ctx.send(err)

    embed = _bs_embed(ctx.author, data)
    embed.title = f"✅ Compte Brawl Stars lié — {ctx.author.display_name}"
    await ctx.send(embed=embed)


@bot.hybrid_command(name="bsprofil", aliases=["bs"])
async def cmd_bsprofil(ctx, member: discord.Member = None):
    member = member or ctx.author
    uid = str(member.id)
    acc = bs_accounts.get(uid)
    if not acc:
        who = "Tu n'as" if member == ctx.author else f"{member.display_name} n'a"
        return await ctx.send(f"❌ {who} pas encore lié de compte Brawl Stars. Utilise `!bslink <tag>`.")

    data, err = await _bs_fetch_player(acc['tag'])
    if data:
        if data['ranked_tier'] is None:
            data['ranked_pts']  = acc.get('ranked_pts')
            data['ranked_tier'] = acc.get('ranked_tier')
        if ctx.guild:
            await _bs_announce_promotion(member, acc, data)
        bs_accounts[uid] = data
        save_data()
        acc = data
        if ctx.guild:
            await _bs_sync_member_roles(member, acc['trophies'], acc['ranked_pts'])
    # en cas d'échec du rafraîchissement, on affiche simplement les dernières données connues

    await ctx.send(embed=_bs_embed(member, acc))


@bot.command(name="bs_roles", aliases=["bsroles"])
async def cmd_bs_roles(ctx, action: str = None, *, reste: str = None):
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs.")

    ranked_ref = " · ".join(f"{name} = {pts:,}" for pts, name in RANKED_TIERS)
    usage = (
        "**Usage :**\n"
        "`!bs_roles trophees <min> @role` — définit un palier de trophées\n"
        "`!bs_roles ranked <min_points> @role` — définit un palier de points classé\n"
        "`!bs_roles trophees|ranked <min> retirer` — supprime un palier\n"
        "`!bs_roles liste` — affiche la configuration actuelle\n"
        "💡 `!bs_roles_panel` — même chose via un panel avec menus/RoleSelect\n\n"
        f"**Repères points classé :** {ranked_ref}\n"
        "Un palier couvre tout jusqu'au suivant configuré : ex. `!bs_roles ranked 3000 @Diamant` "
        "couvre tout Diamant (I à III) si tu n'as qu'un seul rôle pour ce rang."
    )

    if action is None or action.lower() not in ('trophees', 'rank', 'ranked', 'liste'):
        return await ctx.send(usage)
    action = action.lower()
    category = 'trophies' if action == 'trophees' else 'ranked'

    if action == 'liste':
        lines = []
        if bs_role_config['trophies']:
            trophy_lines = "\n".join(
                f"≥ {int(mn):,} 🏆 → <@&{rid}>"
                for mn, rid in sorted(bs_role_config['trophies'].items(), key=lambda x: int(x[0]))
            )
            lines.append(f"**Trophées**\n{trophy_lines}")
        if bs_role_config['ranked']:
            rank_lines = "\n".join(
                f"≥ {int(mn):,} pts → <@&{rid}>"
                for mn, rid in sorted(bs_role_config['ranked'].items(), key=lambda x: int(x[0]))
            )
            lines.append(f"**Classé**\n{rank_lines}")
        if not lines:
            return await ctx.send("Aucun palier configuré pour l'instant.")
        embed = discord.Embed(title="🎮 Config rôles Brawl Stars", description="\n\n".join(lines), color=0xf1c40f)
        return await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    if not reste:
        return await ctx.send(usage)

    parts = reste.rsplit(None, 1)
    if len(parts) != 2:
        return await ctx.send(usage)
    key_raw, role_token = parts

    try:
        key = str(int(key_raw.replace(',', '').replace(' ', '')))
    except ValueError:
        return await ctx.send(f"❌ Le seuil doit être un nombre. Ex : `!bs_roles {action} 3000 @role`.")

    if role_token.lower() in ('retirer', 'remove', 'aucun'):
        if key in bs_role_config[category]:
            del bs_role_config[category][key]
            save_data()
            return await ctx.send(f"✅ Palier **{key}** retiré ({'trophées' if category == 'trophies' else 'classé'}).")
        return await ctx.send(f"ℹ️ Aucun rôle configuré pour **{key}**.")

    try:
        role = await commands.RoleConverter().convert(ctx, role_token)
    except commands.BadArgument:
        return await ctx.send("❌ Rôle introuvable. Mentionne le rôle (`@role`) ou donne son ID.")

    bs_role_config[category][key] = role.id
    save_data()
    unit = "🏆" if category == 'trophies' else "pts"
    await ctx.send(f"✅ ≥ {int(key):,} {unit} → {role.mention}", allowed_mentions=discord.AllowedMentions.none())


def _bs_role_config_embed(guild) -> discord.Embed:
    def _line(mn, rid, ranked: bool) -> str:
        role = guild.get_role(rid)
        target = role.mention if role else "`rôle supprimé`"
        return f"≥ {int(mn):,} pts ({_ranked_tier_name(int(mn))}) → {target}" if ranked else f"≥ {int(mn):,} 🏆 → {target}"

    trophy_items = sorted(bs_role_config['trophies'].items(), key=lambda x: int(x[0]))
    rank_items = sorted(bs_role_config['ranked'].items(), key=lambda x: int(x[0]))
    trophy_desc = "\n".join(_line(mn, rid, False) for mn, rid in trophy_items) or "*Aucun palier configuré.*"
    rank_desc = "\n".join(_line(mn, rid, True) for mn, rid in rank_items) or "*Aucun palier configuré.*"

    embed = discord.Embed(title="🎮 Config rôles Brawl Stars", color=0xf1c40f)
    embed.add_field(name="🏆 Trophées", value=trophy_desc[:1024], inline=False)
    embed.add_field(name="🎖️ Classé", value=rank_desc[:1024], inline=False)
    embed.set_footer(text="Choisis une catégorie puis un palier, ou ajoute un palier de trophées. Un rôle classé couvre tout le rang jusqu'au prochain palier configuré.")
    return embed


class BsTrophyRoleAssignView(discord.ui.View):
    """Étape 2 de l'ajout d'un palier trophées (un Modal ne peut pas contenir
    de RoleSelect) — non persistante, instance fraîche par interaction, même
    principe que AbsenceTypeSelectView : jamais partagée entre utilisateurs."""

    def __init__(self, key: str):
        super().__init__(timeout=180)
        self.key = key
        self.role_select = discord.ui.RoleSelect(placeholder="Choisir le rôle à attribuer…")
        self.role_select.callback = self._on_role
        self.add_item(self.role_select)

    async def _on_role(self, interaction: discord.Interaction):
        role = self.role_select.values[0]
        bs_role_config['trophies'][self.key] = role.id
        save_data()
        await interaction.response.edit_message(
            content=f"✅ ≥ {int(self.key):,} 🏆 → {role.mention}\nRelance `!bs_roles_panel` pour voir la config à jour.",
            view=None, allowed_mentions=discord.AllowedMentions.none(),
        )


class AddTrophyThresholdModal(discord.ui.Modal, title="Ajouter un palier de trophées"):
    threshold_input = discord.ui.TextInput(label="Seuil de trophées (ex: 100000)", placeholder="100000", max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.threshold_input.value).replace(' ', '').replace(',', '').replace('.', '')
        try:
            key = str(int(raw))
        except ValueError:
            return await interaction.response.send_message("❌ Le seuil doit être un nombre entier.", ephemeral=True)
        if key in bs_role_config['trophies']:
            return await interaction.response.send_message(
                f"❌ Un palier existe déjà pour ≥ {int(key):,} 🏆. Modifie-le depuis `!bs_roles_panel`.", ephemeral=True,
            )
        view = BsTrophyRoleAssignView(key)
        await interaction.response.send_message(
            f"Seuil **≥ {int(key):,} 🏆** — choisis maintenant le rôle à attribuer :", view=view, ephemeral=True,
        )


class BsRolesView(discord.ui.View):
    """Config interactive des paliers trophées/classé → rôle Discord, même esprit
    que SetTicketView (menu + select natif). Les paliers classé listent les 22
    sous-paliers de RANKED_TIERS, mais un seul rôle configuré sur le 1er
    sous-palier d'un rang (ex. Or 1 = 1500 pts) couvre tout le rang jusqu'au
    prochain palier configuré — pas besoin d'un rôle par sous-palier (cf.
    !bs_roles). Les paliers trophées sont arbitraires (pas de barème fixe côté
    jeu) donc listés à partir de ce qui est déjà configuré, avec un bouton
    dédié pour en ajouter un nouveau."""

    def __init__(self, guild, mode: str = 'trophies', selected_key: str | None = None):
        super().__init__(timeout=300)
        self.guild = guild
        self.mode = mode if mode in ('trophies', 'ranked') else 'trophies'

        mode_options = [
            discord.SelectOption(label="🏆 Trophées", value='trophies', default=(self.mode == 'trophies')),
            discord.SelectOption(label="🎖️ Classé", value='ranked', default=(self.mode == 'ranked')),
        ]
        self.mode_select = discord.ui.Select(placeholder="Choisir une catégorie…", options=mode_options, row=0)
        self.mode_select.callback = self._on_mode
        self.add_item(self.mode_select)

        if self.mode == 'ranked':
            key_values = {str(pts) for pts, _name in RANKED_TIERS}
            key_options = [
                discord.SelectOption(
                    label=f"{name} — {pts:,} pts"[:100], value=str(pts),
                    default=(selected_key == str(pts)),
                    description=self._current_role_desc('ranked', str(pts)),
                )
                for pts, name in RANKED_TIERS
            ]
            key_placeholder = "🎖️ Choisir un palier classé…"
        else:
            trophy_keys = sorted(bs_role_config['trophies'].items(), key=lambda x: int(x[0]))
            key_values = {mn for mn, _rid in trophy_keys}
            key_options = [
                discord.SelectOption(
                    label=f"≥ {int(mn):,} 🏆"[:100], value=mn, default=(selected_key == mn),
                    description=self._current_role_desc('trophies', mn),
                )
                for mn, _rid in trophy_keys
            ]
            key_placeholder = "🏆 Choisir un palier trophées…" if key_options else "Aucun palier — ajoute-en un ci-dessous ➕"

        self.selected_key = selected_key if selected_key in key_values else None
        if key_options:
            self.key_select = discord.ui.Select(placeholder=key_placeholder, options=key_options[:25], row=1)
            self.key_select.callback = self._on_key
            self.add_item(self.key_select)

        if self.selected_key:
            self.role_select = discord.ui.RoleSelect(placeholder="Choisir le rôle à attribuer…", row=2)
            self.role_select.callback = self._on_role
            self.add_item(self.role_select)

            remove_btn = discord.ui.Button(
                label="🗑️ Retirer ce palier", style=discord.ButtonStyle.danger, row=3,
                disabled=(self.selected_key not in bs_role_config[self.mode]),
            )
            remove_btn.callback = self._on_remove
            self.add_item(remove_btn)

        if self.mode == 'trophies':
            add_btn = discord.ui.Button(label="➕ Ajouter un palier", style=discord.ButtonStyle.secondary, row=3)
            add_btn.callback = self._on_add
            self.add_item(add_btn)

    def _current_role_desc(self, mode: str, key: str) -> str:
        rid = bs_role_config[mode].get(key)
        role = self.guild.get_role(rid) if rid else None
        if role:
            return f"Actuel : @{role.name}"[:100]
        return "Rôle supprimé" if rid else "Non configuré"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux admins/owner.", ephemeral=True)
            return False
        return True

    async def _on_mode(self, interaction: discord.Interaction):
        view = BsRolesView(self.guild, self.mode_select.values[0])
        await interaction.response.edit_message(
            embed=_bs_role_config_embed(self.guild), view=view, allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _on_key(self, interaction: discord.Interaction):
        view = BsRolesView(self.guild, self.mode, self.key_select.values[0])
        await interaction.response.edit_message(
            embed=_bs_role_config_embed(self.guild), view=view, allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _on_role(self, interaction: discord.Interaction):
        role = self.role_select.values[0]
        bs_role_config[self.mode][self.selected_key] = role.id
        save_data()
        view = BsRolesView(self.guild, self.mode, self.selected_key)
        await interaction.response.edit_message(
            content=f"✅ Palier mis à jour → {role.mention}",
            embed=_bs_role_config_embed(self.guild), view=view, allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _on_remove(self, interaction: discord.Interaction):
        bs_role_config[self.mode].pop(self.selected_key, None)
        save_data()
        view = BsRolesView(self.guild, self.mode)
        await interaction.response.edit_message(
            content=f"✅ Palier retiré ({'trophées' if self.mode == 'trophies' else 'classé'}).",
            embed=_bs_role_config_embed(self.guild), view=view, allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _on_add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddTrophyThresholdModal())


@bot.command(name="bs_roles_panel", aliases=["bsrolespanel"])
async def cmd_bs_roles_panel(ctx):
    """Interface interactive (menus + RoleSelect natif) pour configurer les
    paliers trophées/classé → rôle, en alternative à !bs_roles."""
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs.")
    view = BsRolesView(ctx.guild)
    await ctx.send(embed=_bs_role_config_embed(ctx.guild), view=view, allowed_mentions=discord.AllowedMentions.none())


@tasks.loop(hours=1)
async def sync_bs_roles():
    for uid_str, acc in list(bs_accounts.items()):
        data, err = await _bs_fetch_player(acc.get('tag', ''))
        if data:
            if data['ranked_tier'] is None:
                data['ranked_pts']  = acc.get('ranked_pts')
                data['ranked_tier'] = acc.get('ranked_tier')
            # _bs_announce_promotion poste sur un salon fixe (BS_CONGRATS_CHANNEL_ID),
            # pas un par serveur — l'appeler une fois par guild partagée avec le membre
            # envoyait le même message plusieurs fois de suite au même endroit si le
            # bot est sur plusieurs serveurs (incident du 21/08/2026, Yann mentionné
            # 3 fois pour le même palier). _bs_sync_member_roles, elle, reste bien
            # appelée pour chaque guild : les rôles sont propres à chaque serveur.
            announced = False
            for guild in bot.guilds:
                member = guild.get_member(int(uid_str))
                if member:
                    if not announced:
                        await _bs_announce_promotion(member, acc, data)
                        announced = True
                    await _bs_sync_member_roles(member, data['trophies'], data['ranked_pts'])
            bs_accounts[uid_str] = data
        await asyncio.sleep(1)
    save_data()


@bot.command(name="bs_famille", aliases=["bsfamille"])
async def cmd_bs_famille(ctx, action: str = None, tag: str = None):
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs.")

    usage = (
        "**Usage :**\n"
        "`!bs_famille ajouter <tag_clan>` — ajoute un clan à la famille\n"
        "`!bs_famille retirer <tag_clan>` — retire un clan\n"
        "`!bs_famille liste` — affiche les clans configurés\n"
        "💡 `!bs_famille_panel` — même chose via un panel (select + modal)"
    )
    if action is None or action.lower() not in ('ajouter', 'add', 'retirer', 'remove', 'liste'):
        return await ctx.send(usage)
    action = action.lower()

    if action == 'liste':
        family_clubs = db_bs.list_family_clubs()
        if not family_clubs:
            return await ctx.send("Aucun clan configuré pour l'instant.")
        await ctx.typing()
        ok, failed = [], []
        for entry in family_clubs:
            data, err = await _bs_fetch_club(entry['tag'])
            if data:
                data['entry'] = entry
                ok.append(data)
            else:
                failed.append(f"`#{entry['tag']}` — ⚠️ {err}")
        ok.sort(key=lambda c: c['trophies'], reverse=True)
        lines = []
        for c in ok:
            e = c['entry']
            cmd_hint = f"`!{e['slug']}`" + (f" / `!{e['alias']}`" if e.get('alias') else "")
            lines.append(f"**{c['name']}** — `#{e['tag']}` — {c['trophies']:,} 🏆 ({len(c['members'])} membres) — {cmd_hint}")
        return await ctx.send("**Clans de la famille (triés par trophées) :**\n" + "\n".join(lines + failed))

    if not tag:
        return await ctx.send(usage)
    clean = tag.strip().lstrip('#').upper()

    if action in ('ajouter', 'add'):
        if db_bs.club_exists(clean):
            return await ctx.send(f"ℹ️ `#{clean}` est déjà dans la famille.")
        data, err = await _bs_fetch_club(clean)
        if err:
            return await ctx.send(f"❌ Impossible de vérifier ce clan : {err}")

        slug = _bs_slug(data['name'])
        if bot.get_command(slug) is not None:
            return await ctx.send(
                f"❌ Le nom de clan **{data['name']}** donnerait la commande `!{slug}`, "
                f"qui existe déjà. Renomme le clan en jeu ou préviens le développeur."
            )
        existing = db_bs.list_family_clubs()
        reserved = {e['slug'] for e in existing} | {e['alias'] for e in existing if e.get('alias')}
        alias = _bs_alias(slug, reserved)

        entry = {'tag': clean, 'name': data['name'], 'slug': slug, 'alias': alias}
        economy_conflicts = economy_router.command_names & {slug.casefold(), alias.casefold()}
        if economy_conflicts:
            conflict = sorted(economy_conflicts)[0]
            logging.error(
                '[ECONOMY] Dynamic command registration refused: "%s" conflicts with a ? command',
                conflict,
            )
            return await ctx.send(
                f'❌ La commande dynamique `!{conflict}` entre en conflit avec `?{conflict}`. '
                "Le clan n'a pas été ajouté."
            )
        db_bs.add_family_club(clean, data['name'], slug, alias)
        _bs_register_club_command(entry)
        if ctx.guild:
            bot.tree.copy_global_to(guild=ctx.guild)
            await bot.tree.sync(guild=ctx.guild)
        return await ctx.send(
            f"✅ **{data['name']}** (`#{clean}`) ajouté à la famille — {len(data['members'])} membres.\n"
            f"Commande dédiée : `!{slug}` (alias `!{alias}`)."
        )

    existing = db_bs.list_family_clubs()
    entry = next((e for e in existing if e['tag'] == clean), None)
    if entry:
        db_bs.remove_family_club(clean)
        _bs_unregister_club_command(entry)
        if ctx.guild:
            await bot.tree.sync(guild=ctx.guild)
        return await ctx.send(f"✅ `#{clean}` (**{entry['name']}**) retiré de la famille, commande `!{entry['slug']}` supprimée.")
    return await ctx.send(f"ℹ️ `#{clean}` n'était pas dans la famille.")


# ── Admin — ajout/retrait de clan déclenché depuis le site ────────────────
# Fonctions jumelles de cmd_bs_famille (voir ci-dessus) plutôt qu'un refactor
# partagé — même raisonnement que _apply_casino_pause & co : commande déjà
# utilisée telle quelle, pas de risque à prendre pour économiser des lignes.

async def _apply_bs_famille_add(tag: str) -> tuple[dict | None, str | None]:
    clean = tag.strip().lstrip('#').upper()
    if db_bs.club_exists(clean):
        return None, f"#{clean} est déjà dans la famille."
    data, err = await _bs_fetch_club(clean)
    if err:
        return None, f"Impossible de vérifier ce clan : {err}"

    slug = _bs_slug(data['name'])
    if bot.get_command(slug) is not None:
        return None, f"Le nom de clan {data['name']} donnerait la commande !{slug}, qui existe déjà."
    existing = db_bs.list_family_clubs()
    reserved = {e['slug'] for e in existing} | {e['alias'] for e in existing if e.get('alias')}
    alias = _bs_alias(slug, reserved)

    entry = {'tag': clean, 'name': data['name'], 'slug': slug, 'alias': alias}
    economy_conflicts = economy_router.command_names & {slug.casefold(), alias.casefold()}
    if economy_conflicts:
        conflict = sorted(economy_conflicts)[0]
        logging.error(
            '[ECONOMY] Dynamic command registration refused: "%s" conflicts with a ? command',
            conflict,
        )
        return None, (
            f'La commande dynamique !{conflict} entre en conflit avec ?{conflict}. '
            "Le clan n'a pas été ajouté."
        )
    db_bs.add_family_club(clean, data['name'], slug, alias)
    _bs_register_club_command(entry)
    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if guild:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    return {"tag": clean, "name": data['name'], "slug": slug, "alias": alias, "member_count": len(data['members'])}, None


async def _apply_bs_famille_remove(tag: str) -> tuple[dict | None, str | None]:
    clean = tag.strip().lstrip('#').upper()
    existing = db_bs.list_family_clubs()
    entry = next((e for e in existing if e['tag'] == clean), None)
    if not entry:
        return None, f"#{clean} n'était pas dans la famille."
    db_bs.remove_family_club(clean)
    _bs_unregister_club_command(entry)
    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if guild:
        await bot.tree.sync(guild=guild)
    return {"tag": clean, "name": entry['name'], "slug": entry['slug']}, None


def _bs_famille_embed(family_clubs: list[dict]) -> discord.Embed:
    embed = discord.Embed(title="🏠 Clans de la famille Brawl Stars", color=0xf1c40f)
    if family_clubs:
        embed.description = "\n".join(
            f"**{e['name']}** — `#{e['tag']}` — `!{e['slug']}`" + (f" / `!{e['alias']}`" if e.get('alias') else "")
            for e in family_clubs
        )
    else:
        embed.description = "*Aucun clan configuré.*"
    embed.set_footer(text="Choisis un clan à retirer dans le menu, ou ajoute-en un nouveau.")
    return embed


class AddFamilyClubModal(discord.ui.Modal, title="Ajouter un clan à la famille"):
    tag_input = discord.ui.TextInput(label="Tag du clan (avec ou sans #)", placeholder="#2ABC123", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        # Le fetch vers l'API Brawl Stars peut prendre plusieurs secondes — au-delà
        # des 3s d'ack Discord, d'où le defer avant l'appel (voir _apply_bs_famille_add).
        await interaction.response.defer(ephemeral=True, thinking=True)
        data, err = await _apply_bs_famille_add(str(self.tag_input.value))
        if err:
            return await interaction.followup.send(f"❌ {err}", ephemeral=True)
        await interaction.followup.send(
            f"✅ **{data['name']}** (`#{data['tag']}`) ajouté — {data['member_count']} membres.\n"
            f"Commande dédiée : `!{data['slug']}` (alias `!{data['alias']}`).\n"
            f"Relance `!bs_famille_panel` pour voir la liste à jour.",
            ephemeral=True,
        )


class BsFamilleView(discord.ui.View):
    """Config interactive des clans de la famille — select natif pour retirer,
    modal pour ajouter (pas de Select Discord possible pour un tag de clan en
    jeu, contrairement à un rôle/salon)."""

    def __init__(self, family_clubs: list[dict]):
        super().__init__(timeout=300)
        self.family_clubs = family_clubs

        if family_clubs:
            options = [
                discord.SelectOption(label=f"{e['name']} (#{e['tag']})"[:100], value=e['tag'])
                for e in family_clubs[:25]
            ]
            self.remove_select = discord.ui.Select(placeholder="🗑️ Choisir un clan à retirer…", options=options, row=0)
            self.remove_select.callback = self._on_remove
            self.add_item(self.remove_select)

        add_btn = discord.ui.Button(label="➕ Ajouter un clan", style=discord.ButtonStyle.secondary, row=1)
        add_btn.callback = self._on_add
        self.add_item(add_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux admins/owner.", ephemeral=True)
            return False
        return True

    async def _on_remove(self, interaction: discord.Interaction):
        tag = self.remove_select.values[0]
        data, err = await _apply_bs_famille_remove(tag)
        if err:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)
        family_clubs = db_bs.list_family_clubs()
        view = BsFamilleView(family_clubs)
        await interaction.response.edit_message(
            content=f"✅ **{data['name']}** (`#{data['tag']}`) retiré, commande `!{data['slug']}` supprimée.",
            embed=_bs_famille_embed(family_clubs), view=view,
        )

    async def _on_add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddFamilyClubModal())


@bot.command(name="bs_famille_panel", aliases=["bsfamillepanel"])
async def cmd_bs_famille_panel(ctx):
    """Interface interactive (select + modal) pour gérer les clans de la
    famille, en alternative à !bs_famille ajouter/retirer."""
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs.")
    family_clubs = db_bs.list_family_clubs()
    await ctx.send(embed=_bs_famille_embed(family_clubs), view=BsFamilleView(family_clubs))


# ═════════════════════════════════════════════════════════════════════════
# ── Panel #infos-clubs (!clubs_panel) — remplace le screenshot manuel ─────
# ═════════════════════════════════════════════════════════════════════════
# L'API Brawl Stars n'expose JAMAIS le statut "en ligne" en temps réel (donnée
# uniquement visible en jeu) — contrairement au screenshot manuel qu'il
# remplace, ce panel ne peut donc pas l'afficher. Tout le reste (rôle, nom,
# trophées par membre) vient de /clubs/#TAG (_bs_fetch_club), sans appel
# supplémentaire par joueur.
CLUB_ROLE_ICONS  = {'president': '👑', 'vicePresident': '🥈', 'senior': '⭐', 'member': '👤'}
CLUB_ROLE_LABELS = {'president': 'Président', 'vicePresident': 'Vice-président', 'senior': 'Aîné', 'member': 'Membre'}
CLUB_TYPE_LABELS = {'open': '🟢 Ouvert', 'inviteOnly': '🟡 Sur invitation', 'closed': '🔴 Fermé'}


def _family_club_embed(club: dict, entry: dict) -> discord.Embed:
    members = sorted(club['members'], key=lambda m: m['trophies'], reverse=True)
    avg_trophies = club['trophies'] // len(members) if members else 0
    role_counts = {r: sum(1 for m in members if m['role'] == r) for r in CLUB_ROLE_ICONS}

    embed = discord.Embed(
        title=f"🏰 {club['name']}",
        description=(club.get('description') or "*Pas de description.*")[:400],
        color=0x3498db,
    )
    # Infos du club en premier (grille compacte), roster ensuite.
    embed.add_field(name="🔖 Tag", value=f"`#{club['tag'].lstrip('#')}`", inline=True)
    embed.add_field(name="🚪 Type", value=CLUB_TYPE_LABELS.get(club['type'], club['type']), inline=True)
    embed.add_field(name="👥 Effectif", value=f"{len(members)}/30", inline=True)
    embed.add_field(name="🏆 Trophées du club", value=f"{club['trophies']:,}", inline=True)
    embed.add_field(name="📊 Trophées requis", value=f"{club['requiredTrophies']:,}", inline=True)
    embed.add_field(name="📈 Moyenne / membre", value=f"{avg_trophies:,} 🏆", inline=True)
    embed.add_field(
        name="🎖️ Répartition des rôles",
        value=" · ".join(
            f"{CLUB_ROLE_ICONS[r]} {role_counts[r]} {CLUB_ROLE_LABELS[r]}"
            for r in ('president', 'vicePresident', 'senior', 'member')
        ),
        inline=False,
    )

    roster_lines = [f"{CLUB_ROLE_ICONS.get(m['role'], '👤')} **{m['name']}** — {m['trophies']:,} 🏆" for m in members]
    for i, chunk in enumerate(_chunk_lines(roster_lines, limit=1000)):
        embed.add_field(name=f"📋 Roster ({len(members)})" if i == 0 else "📋 Roster (suite)", value=chunk, inline=False)

    cmd_hint = f"!{entry['slug']}" + (f" / !{entry['alias']}" if entry.get('alias') else "")
    embed.set_footer(text=f"Commande dédiée : {cmd_hint} · Statut en ligne non disponible via l'API")
    return embed


async def _refresh_family_clubs_panel() -> tuple[bool, str]:
    """Cœur partagé par la tâche quotidienne et !clubs_panel. Un message par
    embed (header + un par clan) plutôt qu'un seul message multi-embeds :
    Discord limite à 6000 caractères cumulés PAR MESSAGE tous embeds
    confondus, qu'un roster de 30 membres peut à lui seul approcher — c'est
    ce qui faisait planter la version à un seul message multi-embeds.
    Chaque message est édité en place s'il existe déjà (FAMILY_CLUBS_PANEL_MESSAGE_IDS),
    sinon posté dans FAMILY_CLUBS_PANEL_CHANNEL_ID."""
    global FAMILY_CLUBS_PANEL_MESSAGE_IDS
    if not FAMILY_CLUBS_PANEL_CHANNEL_ID:
        return False, "❌ Aucun salon configuré — lance `!clubs_panel` dans le salon voulu d'abord."
    channel = bot.get_channel(FAMILY_CLUBS_PANEL_CHANNEL_ID)
    if not channel:
        return False, "❌ Le salon configuré est introuvable (supprimé ?)."

    entries = db_bs.list_family_clubs()
    if not entries:
        return False, "❌ Aucun clan configuré dans la famille (voir `!bs_famille_panel`)."

    ok_clubs = []
    for entry in entries:
        data, err = await _bs_fetch_club(entry['tag'])
        if data:
            ok_clubs.append((data, entry))
    if not ok_clubs:
        return False, "❌ Impossible de récupérer les données d'un seul clan (API indisponible ?)."
    ok_clubs.sort(key=lambda t: t[0]['trophies'], reverse=True)

    total_trophies = sum(c['trophies'] for c, _e in ok_clubs)
    total_members = sum(len(c['members']) for c, _e in ok_clubs)
    medals = ['🥇', '🥈', '🥉']
    ranking_lines = [
        f"{medals[i] if i < 3 else f'**{i + 1}.**'} {c['name']} — {c['trophies']:,} 🏆 ({len(c['members'])}/30)"
        for i, (c, _e) in enumerate(ok_clubs)
    ]
    header_embed = discord.Embed(
        title="🏆 État actuel des clubs de la famille",
        description=(
            f"**{len(ok_clubs)}** clans · **{total_members}** membres · **{total_trophies:,}** 🏆 cumulés\n\n"
            + "\n".join(ranking_lines)
        ),
        color=0xf1c40f,
    )
    if channel.guild and channel.guild.icon:
        header_embed.set_thumbnail(url=channel.guild.icon.url)
    header_embed.set_footer(text=f"Dernière actualisation : {discord.utils.format_dt(discord.utils.utcnow(), style='f')} · auto toutes les 24h")
    all_embeds = [header_embed] + [_family_club_embed(c, e) for c, e in ok_clubs]

    old_ids = list(FAMILY_CLUBS_PANEL_MESSAGE_IDS)
    new_ids = []
    for i, embed in enumerate(all_embeds):
        msg = None
        if i < len(old_ids):
            try:
                msg = await channel.fetch_message(old_ids[i])
            except (discord.NotFound, discord.Forbidden):
                msg = None
        if msg:
            await msg.edit(content=None, embed=embed)
            new_ids.append(msg.id)
        else:
            msg = await channel.send(embed=embed)
            new_ids.append(msg.id)

    # Messages en trop (un clan retiré de la famille depuis le dernier
    # rafraîchissement) — supprimés pour ne pas laisser un roster périmé traîner.
    for old_id in old_ids[len(all_embeds):]:
        try:
            leftover = await channel.fetch_message(old_id)
            await leftover.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    if new_ids != FAMILY_CLUBS_PANEL_MESSAGE_IDS:
        FAMILY_CLUBS_PANEL_MESSAGE_IDS = new_ids
        save_data()

    return True, f"✅ Panel actualisé dans {channel.mention} ({len(ok_clubs)}/{len(entries)} clans récupérés, {len(all_embeds)} messages)."


@tasks.loop(hours=24)
async def refresh_family_clubs_panel_task():
    await bot.wait_until_ready()
    if not FAMILY_CLUBS_PANEL_CHANNEL_ID:
        return
    ok, msg = await _refresh_family_clubs_panel()
    if not ok:
        print(f"[clubs_panel] Échec du rafraîchissement automatique : {msg}")


@bot.command(name="clubs_panel", aliases=["maj_clubs", "clubs_panel_ici"])
async def cmd_clubs_panel(ctx):
    """Poste (la 1ère fois) ou force le rafraîchissement immédiat du panel
    #infos-clubs dans le salon courant — remplace le screenshot manuel.
    Un rafraîchissement automatique tourne aussi toutes les 24h
    (refresh_family_clubs_panel_task), qui édite ce même message en place."""
    global FAMILY_CLUBS_PANEL_CHANNEL_ID
    if not (ctx.author.guild_permissions.administrator or is_bot_owner(ctx.author)):
        return await ctx.send("❌ Réservé aux administrateurs.")
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")

    if FAMILY_CLUBS_PANEL_CHANNEL_ID != ctx.channel.id:
        FAMILY_CLUBS_PANEL_CHANNEL_ID = ctx.channel.id
        global FAMILY_CLUBS_PANEL_MESSAGE_IDS
        FAMILY_CLUBS_PANEL_MESSAGE_IDS = []  # nouveau salon → nouveaux messages, pas d'édition d'anciens messages d'un autre salon
        save_data()

    async with ctx.typing():
        ok, result_msg = await _refresh_family_clubs_panel()
    await ctx.send(result_msg)


class BsFamilyLeaderboardView(discord.ui.View):
    """Vue paginée (30/page) avec podium distinct pour le top 3 et filtre par clan.
    Les données sont capturées une seule fois à l'appel de la commande — changer de page
    ou de filtre ne fait AUCUN nouvel appel API, on ne fait que re-trancher la liste déjà
    récupérée, gardée en mémoire sur la vue."""
    PAGE_SIZE = 30

    def __init__(self, title, color, entries, unit, clubs, value_key,
                 extra_key=None, extra_note=None, club_filter=None, page=0):
        super().__init__(timeout=300)
        self.title = title
        self.color = color
        self.entries = entries
        self.unit = unit
        self.clubs = clubs
        self.value_key = value_key
        self.extra_key = extra_key
        self.extra_note = extra_note
        self.club_filter = club_filter

        self.filtered = [e for e in entries if club_filter is None or e['club'] == club_filter]
        self.total_pages = max(1, (len(self.filtered) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(page, self.total_pages - 1))

        self.select = None
        if len(clubs) > 1:
            options = [discord.SelectOption(label="🌐 Tous les clans", value="__all__", default=(club_filter is None))]
            for c in clubs[:24]:
                options.append(discord.SelectOption(label=c[:100], value=c, default=(c == club_filter)))
            self.select = discord.ui.Select(placeholder="Filtrer par clan…", options=options)
            self.select.callback = self._on_filter
            self.add_item(self.select)

        if self.page > 0:
            prev_btn = discord.ui.Button(label="◀ Précédent", style=discord.ButtonStyle.secondary, row=1)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)
        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(label="Suivant ▶", style=discord.ButtonStyle.secondary, row=1)
            next_btn.callback = self._next
            self.add_item(next_btn)

    def _clone(self, **overrides):
        kwargs = dict(
            title=self.title, color=self.color, entries=self.entries, unit=self.unit, clubs=self.clubs,
            value_key=self.value_key, extra_key=self.extra_key, extra_note=self.extra_note,
            club_filter=self.club_filter, page=self.page,
        )
        kwargs.update(overrides)
        return BsFamilyLeaderboardView(**kwargs)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.title, color=self.color)
        start = self.page * self.PAGE_SIZE
        page_entries = self.filtered[start:start + self.PAGE_SIZE]

        if self.page == 0:
            medals = ['🥇', '🥈', '🥉']
            for i, e in enumerate(self.filtered[:3]):
                value_line = f"**{e[self.value_key]:,} {self.unit}**"
                if self.extra_key and e.get(self.extra_key):
                    value_line = f"{e[self.extra_key]}\n{value_line}"
                embed.add_field(name=f"{medals[i]} {e['name']}", value=f"{value_line}\n*{e['club']}*", inline=True)
            rest, rank_offset = page_entries[3:], 4
        else:
            rest, rank_offset = page_entries, start + 1

        if rest:
            lines = []
            for i, e in enumerate(rest):
                rank = rank_offset + i
                extra = f"{e[self.extra_key]} · " if self.extra_key and e.get(self.extra_key) else ""
                lines.append(f"**{rank}.** {e['name']} — {extra}{e[self.value_key]:,} {self.unit} *({e['club']})*")
            for i in range(0, len(lines), 10):
                embed.add_field(name=chr(8203), value="\n".join(lines[i:i + 10]), inline=False)
        elif not self.filtered:
            embed.description = "Aucun membre à afficher."

        club_txt = self.club_filter or "tous les clans"
        footer = f"{len(self.filtered)} membres ({club_txt}) · Page {self.page + 1}/{self.total_pages}"
        if self.extra_note:
            footer += f" · {self.extra_note}"
        embed.set_footer(text=footer)
        return embed

    async def _on_filter(self, interaction: discord.Interaction):
        value = self.select.values[0]
        view = self._clone(club_filter=None if value == "__all__" else value, page=0)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _prev(self, interaction: discord.Interaction):
        view = self._clone(page=self.page - 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _next(self, interaction: discord.Interaction):
        view = self._clone(page=self.page + 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


async def _bs_club_command_callback(ctx, club_tag: str):
    """Callback partagé par toutes les commandes dédiées à un clan (ex: !projetx)."""
    await ctx.typing()
    data, err = await _bs_fetch_club(club_tag)
    if err:
        return await ctx.send(f"❌ {err}")

    members = [{'name': m['name'], 'trophies': m['trophies'], 'club': data['name']} for m in data['members']]
    members.sort(key=lambda m: m['trophies'], reverse=True)

    view = BsFamilyLeaderboardView(
        title=f"🏆 {data['name']}", color=0xf1c40f,
        entries=members, unit="🏆", clubs=[data['name']], value_key='trophies',
        extra_note=f"{data['trophies']:,} trophées cumulés",
    )
    await ctx.send(embed=view.build_embed(), view=view)


def _bs_register_club_command(entry: dict, *, fail_on_economy_conflict: bool = False) -> bool:
    """(Ré)enregistre la commande dédiée d'un clan (ex: !projetx / !px) auprès du bot.
    Appelé au démarrage pour chaque clan déjà configuré, et à chaque !bs_famille ajouter."""
    dynamic_names = {entry['slug']}
    if entry.get('alias'):
        dynamic_names.add(entry['alias'])
    collisions = {name.casefold() for name in dynamic_names} & economy_router.command_names
    if collisions:
        names = ", ".join(f'"{name}"' for name in sorted(collisions))
        error_message = f"Conflit détecté entre une commande ! dynamique et ?: {names}"
        if fail_on_economy_conflict:
            raise RuntimeError(error_message)
        logging.error("[ECONOMY] %s — enregistrement dynamique refusé", error_message)
        return False
    if bot.get_command(entry['slug']) is not None:
        return False
    club_tag = entry['tag']

    async def _cb(ctx):
        await _bs_club_command_callback(ctx, club_tag)

    cmd = commands.HybridCommand(_cb, name=entry['slug'], aliases=[entry['alias']] if entry.get('alias') else [])
    cmd.help = f"Classement trophées du clan {entry['name']}"
    bot.add_command(cmd)
    return True


def _bs_unregister_club_command(entry: dict):
    bot.remove_command(entry['slug'])


def _recruitment_club_embed(club: dict, entry: dict) -> discord.Embed:
    """Fiche « prête à envoyer » pour un recruteur — contrairement à
    _family_club_embed (staff, roster complet dans #infos-clubs), pas de
    liste de membres : juste de quoi convaincre un prospect. Appel API live
    à chaque fois (_bs_fetch_club, pas de cache), donc toujours à jour —
    plus besoin d'attendre qu'un staff envoie un screenshot manuel du jeu
    (demande du 21/08/2026)."""
    embed = discord.Embed(
        title=f"🏰 {club['name']}",
        description=club.get('description') or "*Pas de description.*",
        color=0x2ecc71,
    )
    embed.add_field(name="🔖 Tag", value=f"`#{club['tag'].lstrip('#')}`", inline=True)
    embed.add_field(name="🚪 Type", value=CLUB_TYPE_LABELS.get(club['type'], club['type']), inline=True)
    embed.add_field(name="👥 Places", value=f"{len(club['members'])}/30", inline=True)
    embed.add_field(name="🏆 Trophées du club", value=f"{club['trophies']:,}", inline=True)
    embed.add_field(name="📊 Trophées requis", value=f"{club['requiredTrophies']:,}", inline=True)
    embed.set_footer(text=f"Pour rejoindre : rechercher « {club['name']} » ou le tag #{club['tag'].lstrip('#')} en jeu · Actualisé à l'instant")
    return embed


class RecrutementClubSelectView(discord.ui.View):
    """Sélection cliquable du clan pour !recrutement sans argument — pas
    besoin de connaître le nom/slug exact à taper (demande du 21/08/2026)."""

    def __init__(self, entries: list[dict]):
        super().__init__(timeout=180)
        self.entries_by_tag = {e['tag']: e for e in entries}
        options = [
            discord.SelectOption(label=e['name'][:100], value=e['tag'], description=f"!{e['slug']}"[:100])
            for e in entries[:25]
        ]
        self.select = discord.ui.Select(placeholder="🏰 Choisir un clan…", options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        tag = self.select.values[0]
        entry = self.entries_by_tag.get(tag)
        await interaction.response.defer(thinking=True)
        data, err = await _bs_fetch_club(tag)
        if err:
            return await interaction.followup.send(f"❌ {err}", ephemeral=True)
        await interaction.followup.send(
            content=f"🔖 Tag à copier : `#{data['tag'].lstrip('#')}`",
            embed=_recruitment_club_embed(data, entry),
        )


@bot.hybrid_command(name="recrutement", aliases=["pitch_club", "fiche_club"])
async def cmd_recrutement(ctx, *, club: str = None):
    """Fiche de recrutement à jour pour un clan de la famille (nom, tag, slug
    ou alias) — self-service pour les recruteurs : plus besoin d'attendre
    qu'un staff envoie un screenshot du jeu à chaque fois. Sans argument,
    menu cliquable pour choisir le clan."""
    entries = db_bs.list_family_clubs()
    if not entries:
        return await ctx.send("❌ Aucun clan configuré dans la famille (voir `!bs_famille_panel`).")

    if not club:
        return await ctx.send(
            "🏰 Choisis un clan dans le menu (ou tape directement `!recrutement <nom>` la prochaine fois) :",
            view=RecrutementClubSelectView(entries),
        )

    needle = club.strip().lower().lstrip('#')
    entry = next(
        (e for e in entries if needle in (e['slug'], (e.get('alias') or '').lower(), e['name'].lower(), e['tag'].lower())),
        None,
    )
    if not entry:
        return await ctx.send(f"❌ Clan `{club}` introuvable dans la famille. `!recrutement` sans argument pour voir la liste.")

    await ctx.typing()
    data, err = await _bs_fetch_club(entry['tag'])
    if err:
        return await ctx.send(f"❌ {err}")

    await ctx.send(
        content=f"🔖 Tag à copier : `#{data['tag'].lstrip('#')}`",
        embed=_recruitment_club_embed(data, entry),
    )


@bot.hybrid_command(name="classement_trophees_famille", aliases=["ctf", "top_famille"])
async def cmd_classement_trophees_famille(ctx):
    family_clubs = db_bs.list_family_clubs()
    if not family_clubs:
        return await ctx.send("❌ Aucun clan configuré. Utilise `!bs_famille ajouter <tag>` (Admin).")

    await ctx.typing()
    all_members, errors, total_family = [], [], 0
    for club in family_clubs:
        data, err = await _bs_fetch_club(club['tag'])
        if err:
            errors.append(f"`#{club['tag']}` : {err}")
            continue
        for m in data['members']:
            all_members.append({'name': m['name'], 'trophies': m['trophies'], 'club': data['name']})
        total_family += sum(m['trophies'] for m in data['members'])

    if not all_members:
        return await ctx.send("❌ Impossible de récupérer les données des clans.\n" + "\n".join(errors))

    all_members.sort(key=lambda m: m['trophies'], reverse=True)
    clubs = list(dict.fromkeys(m['club'] for m in all_members))
    note = f"{total_family:,} trophées cumulés"
    if errors:
        note += f" · {len(errors)} clan(s) injoignable(s)"

    view = BsFamilyLeaderboardView(
        title="🏆 Classement Trophées — Famille", color=0xf1c40f,
        entries=all_members, unit="🏆", clubs=clubs, value_key='trophies', extra_note=note,
    )
    await ctx.send(embed=view.build_embed(), view=view)


async def _bs_evolution_current_entries():
    """Calcule l'évolution depuis le début de la saison Brawl Stars en cours à
    partir des données déjà synchronisées (comme /api/famille/evolution côté
    site), plutôt que d'un appel API en direct par clan comme avant — un seul
    clan injoignable au moment précis de l'appel faisait disparaître
    silencieusement tous ses membres du résultat (constaté le 11/08/2026 :
    2 clans sur 7 manquants du menu de filtre, sans message d'erreur clair).
    Les données synchronisées sont rafraîchies toutes les heures
    (sync_trophy_history) et la baseline de saison vient de bs_season_baseline
    (figée au reset) — largement assez à jour, et ne dépend plus de la
    disponibilité de l'API Brawl Stars au moment précis où la commande tourne."""
    state = db_bs.get_season_state()
    start_date = state['season_start_date'] or datetime.now(BS_SEASON_TZ).strftime('%Y-%m-%d')
    raw = db_bs.get_season_evolution(start_date, state['season_month'])

    all_members = []
    for e in raw:
        joined_note = None
        if e.get('joined_note'):
            d = e['joined_note']
            joined_note = f"depuis le {d[8:10]}/{d[5:7]}"
        all_members.append({
            'name': e['name'] or '?',
            'club': e['club'] or '?',
            'delta': e['delta'],
            'joined_note': joined_note,
        })

    all_members.sort(key=lambda m: m['delta'], reverse=True)
    clubs = list(dict.fromkeys(m['club'] for m in all_members if m['club'] != '?'))
    note = "Depuis le début de la saison Brawl Stars en cours"
    return all_members, clubs, note


def _bs_evolution_archived_entries(month: str):
    """Relit une saison de push archivée depuis Supabase (aucun appel API)."""
    archived = db_bs.get_archived_season(month)
    entries = [
        {'name': v['name'], 'club': v['club'], 'delta': v['delta'], 'joined_note': None}
        for v in archived
    ]
    entries.sort(key=lambda e: e['delta'], reverse=True)
    clubs = list(dict.fromkeys(e['club'] for e in entries))
    note = f"Saison archivée : {_r1v1_month_label(month)}"
    return entries, clubs, note


class BsEvolutionView(discord.ui.View):
    """Évolution des trophées : sélecteur de saison (actuelle en direct ou archivée) + filtre
    par clan + pagination. Même esprit que BsFamilyLeaderboardView (podium/pagination/clan) et
    RankedLeaderboardView (sélecteur de saisons passées), combinés pour ce cas précis."""
    PAGE_SIZE = 30

    def __init__(self, entries, clubs, month, note, club_filter=None, page=0):
        super().__init__(timeout=300)
        self.entries = entries
        self.clubs = clubs
        self.month = month  # None = saison en cours
        self.note = note
        self.club_filter = club_filter

        self.filtered = [e for e in entries if club_filter is None or e['club'] == club_filter]
        self.total_pages = max(1, (len(self.filtered) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(page, self.total_pages - 1))

        past_seasons = db_bs.list_archived_seasons()
        if past_seasons:
            season_options = [discord.SelectOption(label="📅 Saison actuelle", value="__current__", default=(month is None))]
            for m in past_seasons[:24]:
                season_options.append(discord.SelectOption(label=_r1v1_month_label(m)[:100], value=m, default=(m == month)))
            self.season_select = discord.ui.Select(placeholder="📅 Choisir une saison…", options=season_options)
            self.season_select.callback = self._on_season
            self.add_item(self.season_select)

        if len(clubs) > 1:
            club_options = [discord.SelectOption(label="🌐 Tous les clans", value="__all__", default=(club_filter is None))]
            for c in clubs[:24]:
                club_options.append(discord.SelectOption(label=c[:100], value=c, default=(c == club_filter)))
            self.club_select = discord.ui.Select(placeholder="Filtrer par clan…", options=club_options, row=1)
            self.club_select.callback = self._on_club
            self.add_item(self.club_select)

        if self.page > 0:
            prev_btn = discord.ui.Button(label="◀ Précédent", style=discord.ButtonStyle.secondary, row=2)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)
        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(label="Suivant ▶", style=discord.ButtonStyle.secondary, row=2)
            next_btn.callback = self._next
            self.add_item(next_btn)

    def build_embed(self) -> discord.Embed:
        month_label = "Saison actuelle" if self.month is None else _r1v1_month_label(self.month)
        embed = discord.Embed(title=f"📈 Évolution des Trophées — {month_label}", color=0x3498db)
        start = self.page * self.PAGE_SIZE
        page_entries = self.filtered[start:start + self.PAGE_SIZE]

        if self.page == 0:
            medals = ['🥇', '🥈', '🥉']
            for i, e in enumerate(self.filtered[:3]):
                value_line = f"**{e['delta']:+,} 🏆**"
                if e.get('joined_note'):
                    value_line = f"{e['joined_note']}\n{value_line}"
                embed.add_field(name=f"{medals[i]} {e['name']}", value=f"{value_line}\n*{e['club']}*", inline=True)
            rest, rank_offset = page_entries[3:], 4
        else:
            rest, rank_offset = page_entries, start + 1

        if rest:
            lines = []
            for i, e in enumerate(rest):
                rank = rank_offset + i
                extra = f"{e['joined_note']} · " if e.get('joined_note') else ""
                lines.append(f"**{rank}.** {e['name']} — {extra}{e['delta']:+,} 🏆 *({e['club']})*")
            for i in range(0, len(lines), 10):
                embed.add_field(name=chr(8203), value="\n".join(lines[i:i + 10]), inline=False)
        elif not self.filtered:
            embed.description = "Aucune donnée pour cette période."

        club_txt = self.club_filter or "tous les clans"
        club_total = sum(e['delta'] for e in self.filtered)
        footer = f"{len(self.filtered)} membre(s) ({club_txt}) · Total {club_total:+,} 🏆 · Page {self.page + 1}/{self.total_pages}"
        if self.note:
            footer += f" · {self.note}"
        embed.set_footer(text=footer)
        return embed

    async def _on_season(self, interaction: discord.Interaction):
        value = self.season_select.values[0]
        if value == "__current__":
            await interaction.response.defer()
            entries, clubs, note = await _bs_evolution_current_entries()
            view = BsEvolutionView(entries, clubs, None, note, club_filter=self.club_filter, page=0)
            await interaction.edit_original_response(embed=view.build_embed(), view=view)
        else:
            entries, clubs, note = _bs_evolution_archived_entries(value)
            view = BsEvolutionView(entries, clubs, value, note, club_filter=self.club_filter, page=0)
            await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _on_club(self, interaction: discord.Interaction):
        value = self.club_select.values[0]
        view = BsEvolutionView(self.entries, self.clubs, self.month, self.note,
                                club_filter=None if value == "__all__" else value, page=0)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _prev(self, interaction: discord.Interaction):
        view = BsEvolutionView(self.entries, self.clubs, self.month, self.note, self.club_filter, self.page - 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _next(self, interaction: discord.Interaction):
        view = BsEvolutionView(self.entries, self.clubs, self.month, self.note, self.club_filter, self.page + 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


@bot.hybrid_command(name="evolution_trophees", aliases=["evo", "evolution"])
async def cmd_evolution_trophees(ctx):
    if not db_bs.list_family_clubs():
        return await ctx.send("❌ Aucun clan configuré. Utilise `!bs_famille ajouter <tag>` (Admin).")

    await ctx.typing()
    entries, clubs, note = await _bs_evolution_current_entries()
    if not entries:
        return await ctx.send("❌ Pas assez de données pour calculer une évolution.")

    view = BsEvolutionView(entries, clubs, None, note)
    await ctx.send(embed=view.build_embed(), view=view)


def _first_thursday_10(year: int, month: int) -> datetime:
    """1er jeudi du mois à 10h, heure de Paris (aware datetime)."""
    d = datetime(year, month, 1, 10, 0, 0, tzinfo=BS_SEASON_TZ)
    offset = (3 - d.weekday()) % 7  # weekday(): lundi=0 … jeudi=3
    return d.replace(day=1 + offset)


def _most_recent_season_start(now: datetime) -> datetime:
    """1er jeudi 10h (heure de Paris) le plus récent déjà passé — calcul
    déterministe à partir de l'horloge, indépendant de tout état persisté.
    Utilisé pour amorcer/recaler bs_season_start_date sans jamais retomber
    sur un arbitraire "maintenant" (voir incident du 20/07/2026 ci-dessous)."""
    year, month = now.year, now.month
    for _ in range(4):
        candidate = _first_thursday_10(year, month)
        if candidate <= now:
            return candidate
        month -= 1
        if month < 1:
            month, year = 12, year - 1
    return _first_thursday_10(year, month)


@tasks.loop(minutes=30)
async def check_bs_season():
    """Détecte le début d'une nouvelle saison Brawl Stars (1er jeudi du mois, à partir de 10h
    heure de Paris — avec rattrapage si le bot était hors ligne pendant toute la fenêtre) et
    archive la progression de trophées de la saison qui se termine dans bs_trophy_evolution_history.

    bs_season_start_date étant utilisé comme borne pour calculer la progression de chaque
    membre (!evo, /api/famille/evolution), une régression ici efface silencieusement des
    semaines de suivi (incident du 20/07/2026 : cette valeur avait fini remise à "aujourd'hui"
    au lieu du vrai début de saison, sans doute suite à un redémarrage où bs_season_month
    était retombé à None). On calcule donc maintenant la date correcte de façon déterministe
    (1er jeudi 10h) plutôt que d'utiliser `today` en dur, et on revalide/recale à chaque tick."""
    await bot.wait_until_ready()
    if not db_bs.list_family_clubs():
        return

    now = datetime.now(BS_SEASON_TZ)
    current_month = now.strftime('%Y-%m')
    today = now.strftime('%Y-%m-%d')

    state = db_bs.get_season_state()
    season_month = state['season_month']
    season_start_date = state['season_start_date']

    if season_month is None:
        start = _most_recent_season_start(now)
        new_month = start.strftime('%Y-%m')
        db_bs.set_season_state(new_month, start.strftime('%Y-%m-%d'))
        latest = db_bs.get_latest_trophies()
        db_bs.save_season_baseline(new_month, {p['tag']: p['trophies'] for p in latest})
        return

    if season_month == current_month:
        # Filet de sécurité : si le pointeur a dérivé de la vraie date de bascule
        # (cf. docstring), on le recale silencieusement sans repasser par
        # l'archivage puisqu'on reste dans le même mois de saison.
        correct_start_date = _most_recent_season_start(now).strftime('%Y-%m-%d')
        if season_start_date != correct_start_date:
            logging.warning(
                "check_bs_season: bs_season_start_date incohérent (%s), recalage sur %s",
                season_start_date, correct_start_date,
            )
            db_bs.set_season_state(season_month, correct_start_date)
        return

    is_first_thursday = now.day <= 7 and now.weekday() == 3
    if not ((is_first_thursday and now.hour >= 10) or now.day > 7):
        return

    ended_month = season_month
    start_date = season_start_date or today
    entries = db_bs.get_season_evolution(start_date, ended_month)
    archive = [
        {'tag': e['tag'], 'name': e['name'], 'club': e['club'], 'start': e['start'], 'end': e['end'], 'delta': e['delta']}
        for e in entries
    ]
    if archive:
        db_bs.archive_season(ended_month, archive)

    new_start = _most_recent_season_start(now)
    new_month = new_start.strftime('%Y-%m')
    db_bs.set_season_state(new_month, new_start.strftime('%Y-%m-%d'))

    # Capture immédiate de la valeur de départ de la nouvelle saison (voir
    # save_season_baseline) — sans ça, "start" dépendrait du premier sync
    # quotidien classique, potentiellement écrasé plusieurs fois avant que
    # quiconque ne le consulte (incident du 07/08/2026).
    latest = db_bs.get_latest_trophies()
    db_bs.save_season_baseline(new_month, {p['tag']: p['trophies'] for p in latest})


@tasks.loop(hours=4)
async def sync_family_ranked():
    """Rafraîchit le cache des points classés de toute la famille en arrière-plan.
    Fait exprès de ne PAS tourner à chaque commande : avec ~150 membres, ça reste un appel
    /players officiel par personne (la liste de membres d'un clan ne contient pas le rang
    classé), donc interroger tout le monde à la demande serait lent et solliciterait
    inutilement l'API à chaque fois qu'un membre tape la commande."""
    clubs = db_bs.list_family_clubs()
    if not clubs:
        return

    new_cache = []
    async with aiohttp.ClientSession() as session:
        for club in clubs:
            data, err = await _bs_fetch_club(club['tag'])
            if err:
                continue
            for m in data['members']:
                if not m['tag']:
                    continue
                ranked_pts, ranked_tier, highest_ranked_pts, highest_ranked_tier, highest_ranked_rank = await _bs_fetch_ranked_pts(session, m['tag'])
                if ranked_pts is not None:
                    new_cache.append({
                        'tag': m['tag'], 'name': m['name'], 'club': data['name'],
                        'ranked_pts': ranked_pts, 'ranked_tier': ranked_tier,
                        'highest_ranked_pts': highest_ranked_pts, 'highest_ranked_tier': highest_ranked_tier,
                        'highest_ranked_rank': highest_ranked_rank,
                    })
                await asyncio.sleep(0.6)

    if new_cache:
        db_bs.replace_ranked_cache(new_cache)


@tasks.loop(hours=1)
async def sync_trophy_history():
    """Rafraîchit toutes les heures le point du jour des trophées de tous les membres de la famille
    de clans (par tag Brawl Stars, pas besoin de !bslink) pour alimenter !evolution_trophees.
    Une seule entrée par jour est conservée (upsert idempotent côté Supabase, pas de doublon) —
    tourner plus souvent rend juste la valeur de fin de saison archivée par check_bs_season plus
    précise. Réutilise _bs_fetch_club (déjà appelé par sync_family_ranked)."""
    clubs = db_bs.list_family_clubs()
    if not clubs:
        return

    # Paris, comme check_bs_season/BS_SEASON_TZ — datetime.now() nu suivait
    # le fuseau du serveur (UTC sur Railway), décalant la bascule du jour de
    # ~2h par rapport à l'heure de Paris utilisée partout ailleurs (trouvé le
    # 07/08/2026 : la nouvelle ligne quotidienne, donc le premier delta
    # pusheur visible d'une saison, apparaissait avec ce retard).
    today = datetime.now(BS_SEASON_TZ).strftime('%Y-%m-%d')
    synced_club_tags = []
    all_current_tags = []
    for club in clubs:
        data, err = await _bs_fetch_club(club['tag'])
        if err:
            continue

        bs_family_club_details[club['tag']] = {
            'name': data['name'],
            'description': data.get('description', ''),
            'type': data.get('type', 'open'),
            'requiredTrophies': data.get('requiredTrophies', 0),
            'trophies': data['trophies'],
            'members': [
                {'tag': m['tag'], 'name': m['name'], 'trophies': m['trophies'], 'role': m.get('role', 'member')}
                for m in data['members'] if m['tag']
            ],
        }

        db_bs.upsert_members_snapshot(today, club['tag'], data['name'], data['members'])
        synced_club_tags.append(club['tag'])
        all_current_tags.extend(m['tag'] for m in data['members'] if m['tag'])
        await asyncio.sleep(0.3)

    # Nettoie les joueurs partis d'un clan de la famille sans en rejoindre un
    # autre suivi — seulement pour les clans synchronisés avec succès cette
    # passe, pour ne jamais effacer à tort les membres d'un clan en échec.
    db_bs.clear_stale_club_members(synced_club_tags, all_current_tags)


@tasks.loop(minutes=15)
async def sync_discord_members():
    """Miroir dans Supabase de l'état réel du serveur Discord (ID + rôles + permission
    admin de chaque membre) — alimente la résolution du niveau d'accès côté site (invité /
    membre de clan / staff / admin). Le site ne connaît jamais les rôles Discord autrement
    que via cette table : pas de scope OAuth supplémentaire, pas de token qui expire."""
    await bot.wait_until_ready()
    guild = bot.get_guild(BS_FAMILY_GUILD_ID)
    if guild is None:
        return
    members = [
        {
            'discord_id': str(m.id),
            'username': m.name,
            'role_ids': [str(r.id) for r in m.roles if r.id != guild.id],
            'is_admin': m.guild_permissions.administrator,
        }
        for m in guild.members if not m.bot
    ]
    if members:
        db_members.sync_members(members)


@bot.hybrid_command(name="classement_ranked_famille", aliases=["crf", "top_ranked_famille"])
async def cmd_classement_ranked_famille(ctx):
    ranked_cache = db_bs.get_ranked_cache()
    if not ranked_cache:
        return await ctx.send(
            "❌ Le cache classé n'est pas encore prêt (la première synchronisation peut prendre "
            "quelques minutes après le démarrage du bot, ou après le premier `!bs_famille ajouter`). Réessaie plus tard."
        )

    entries = sorted(ranked_cache, key=lambda m: m.get('ranked_pts', 0), reverse=True)
    clubs = list(dict.fromkeys(m['club'] for m in entries))
    updated_at = _format_ranked_updated_at(db_bs.get_ranked_updated_at())
    note = f"Dernière mise à jour : {updated_at}" if updated_at else None

    view = BsFamilyLeaderboardView(
        title="🎖️ Classement Classé — Famille", color=0x9b59b6,
        entries=entries, unit="pts", clubs=clubs, value_key='ranked_pts',
        extra_key='ranked_tier', extra_note=note,
    )
    await ctx.send(embed=view.build_embed(), view=view)


@bot.hybrid_command(name="famille_stats", aliases=["fs", "stats_famille"])
async def cmd_famille_stats(ctx):
    clubs = db_bs.list_family_clubs()
    if not clubs:
        return await ctx.send("❌ Aucun clan configuré. Utilise `!bs_famille ajouter <tag>` (Admin).")

    await ctx.typing()
    club_rows, errors = [], []
    total_members, total_trophies = 0, 0
    for entry in clubs:
        data, err = await _bs_fetch_club(entry['tag'])
        if err:
            errors.append(f"`#{entry['tag']}` : {err}")
            continue
        total_members  += len(data['members'])
        total_trophies += data['trophies']
        club_rows.append({
            'name': data['name'], 'trophies': data['trophies'],
            'members': len(data['members']), 'slug': entry['slug'], 'alias': entry.get('alias'),
        })

    if not club_rows:
        return await ctx.send("❌ Impossible de récupérer les données des clans.\n" + "\n".join(errors))

    club_rows.sort(key=lambda c: c['trophies'], reverse=True)
    avg = total_trophies / total_members if total_members else 0

    embed = discord.Embed(title="📊 Statistiques de la Famille", color=0x3498db)
    embed.add_field(name="👥 Membres", value=f"{total_members}", inline=True)
    embed.add_field(name="🏆 Trophées cumulés", value=f"{total_trophies:,}", inline=True)
    embed.add_field(name="📈 Moyenne / membre", value=f"{avg:,.0f}", inline=True)

    medals = ['🥇', '🥈', '🥉']
    club_lines = []
    for i, c in enumerate(club_rows):
        rank = medals[i] if i < 3 else f"`#{i + 1}`"
        cmd_hint = f"`!{c['slug']}`" + (f" / `!{c['alias']}`" if c.get('alias') else "")
        club_lines.append(f"{rank} **{c['name']}** — {c['trophies']:,} 🏆 · {c['members']} membres · {cmd_hint}")
    embed.add_field(name=f"🏰 Clans ({len(club_rows)})", value="\n".join(club_lines), inline=False)

    ranked_cache = db_bs.get_ranked_cache()
    if ranked_cache:
        tier_counts = {}
        for m in ranked_cache:
            t = m.get('ranked_tier') or '?'
            tier_counts[t] = tier_counts.get(t, 0) + 1
        tier_order = [name for _, name in RANKED_TIERS]
        sorted_tiers = sorted(
            tier_counts.items(),
            key=lambda x: tier_order.index(x[0]) if x[0] in tier_order else -1,
            reverse=True,
        )
        tier_lines = [f"**{name}** : {count}" for name, count in sorted_tiers]
        value = "\n".join(tier_lines[:15]) if tier_lines else "Aucune donnée"
        updated_at = _format_ranked_updated_at(db_bs.get_ranked_updated_at())
        note = f"\n\n*Cache classé — {updated_at}*" if updated_at else ""
        embed.add_field(name="🎖️ Répartition classé", value=value + note, inline=False)
    else:
        embed.add_field(name="🎖️ Répartition classé", value="Cache pas encore prêt (premier cycle en cours).", inline=False)

    if errors:
        embed.add_field(name="⚠️ Clans injoignables", value="\n".join(errors), inline=False)

    await ctx.send(embed=embed)


@bot.hybrid_command(name='maville')
async def cmd_maville(ctx, *, ville: str = None):
    uid = str(ctx.author.id)
    if not ville:
        if uid in locations:
            await ctx.send(f"📍 Ta ville enregistrée : **{locations[uid]['ville']}**")
        else:
            await ctx.send("Tu n'as pas encore enregistré ta ville. Utilise `!maville <nom de ville>`")
        return

    ville = ville.strip()
    try:
        async with aiohttp.ClientSession() as session:
            params  = {"q": ville, "format": "json", "limit": 1}
            headers = {"User-Agent": "VynaroCasinoBot/1.0 Discord bot"}
            async with session.get(
                "https://nominatim.openstreetmap.org/search",
                params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                results = await resp.json(content_type=None)
    except Exception as e:
        logging.warning(f"[maville] erreur nominatim pour '{ville}': {e}")
        await ctx.send("❌ Erreur réseau lors de la recherche. Réessaie dans quelques instants.")
        return

    if not results or not isinstance(results, list):
        await ctx.send(f"❌ **{ville}** introuvable. Essaie un nom plus précis.")
        return

    lat = float(results[0]['lat'])
    lon = float(results[0]['lon'])
    display_name = results[0]['display_name'].split(',')[0].strip()

    locations[uid] = {"ville": display_name, "lat": lat, "lon": lon}
    save_data()
    await ctx.send(f"✅ Ville enregistrée : **{display_name}**")


def _cluster_points(entries, radius):
    """Regroupe les entrées dont les positions pixel sont à moins de `radius` les unes des autres."""
    n = len(entries)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            dx = entries[i]['px'] - entries[j]['px']
            dy = entries[i]['py'] - entries[j]['py']
            if dx * dx + dy * dy <= radius * radius:
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(entries[i])
    return list(groups.values())


def _render_carte(preset, guild):
    from staticmap import StaticMap, CircleMarker

    IMG_W, IMG_H = 1200, 650
    zoom = preset['zoom']
    clat, clon = preset['clat'], preset['clon']
    cluster_max = preset.get('cluster_max')

    loc_items = list(locations.items())
    entries = []
    for i, (uid, loc) in enumerate(loc_items):
        px, py = _latlon_to_px(loc['lat'], loc['lon'], zoom, clat, clon, IMG_W, IMG_H)
        member = guild.get_member(int(uid))
        name   = member.display_name if member else loc['ville']
        color  = _CARTE_COLORS[i % len(_CARTE_COLORS)]
        entries.append({'lat': loc['lat'], 'lon': loc['lon'], 'px': px, 'py': py, 'name': name, 'color': color})

    clusters = _cluster_points(entries, radius=26)

    m = StaticMap(IMG_W, IMG_H)
    for cluster in clusters:
        if cluster_max and len(cluster) > cluster_max:
            continue  # les paquets agrégés n'affichent pas de points individuels, juste la bulle
        for e in cluster:
            m.add_marker(CircleMarker((e['lon'], e['lat']), e['color'], 14))

    image = m.render(zoom=zoom, center=[clon, clat]).convert('RGBA')
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    font = _carte_font(15)
    font_bubble = _carte_font(16)

    PAD_X, PAD_Y, GAP = 6, 3, 3
    placed = []  # boîtes déjà posées : (x0, y0, x1, y1)

    def overlaps(box):
        x0, y0, x1, y1 = box
        for px0, py0, px1, py1 in placed:
            if x0 < px1 and x1 > px0 and y0 < py1 and y1 > py0:
                return True
        return False

    def place_label(anchor_x, anchor_y, text, color):
        bbox = odraw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        box_w, box_h = tw + PAD_X * 2, th + PAD_Y * 2
        label_x = anchor_x + 12
        label_y = anchor_y - box_h / 2
        while overlaps((label_x, label_y, label_x + box_w, label_y + box_h)):
            label_y += box_h + GAP
        placed.append((label_x, label_y, label_x + box_w, label_y + box_h))

        if abs((label_y + box_h / 2) - anchor_y) > box_h:
            odraw.line((anchor_x, anchor_y, label_x, label_y + box_h / 2), fill=(90, 90, 90, 200), width=1)

        odraw.rounded_rectangle(
            (label_x, label_y, label_x + box_w, label_y + box_h),
            radius=5, fill=(20, 20, 20, 175)
        )
        odraw.text((label_x + PAD_X - bbox[0], label_y + PAD_Y - bbox[1]), text, fill=color, font=font)

    # Chaque paquet est traité de haut en bas pour un empilement stable
    bubble_r = 18
    LEGEND_MAX = 6  # au-delà, un empilement individuel devient une colonne illisible
    font_legend = _carte_font(13)
    render_jobs = []  # (anchor_y, callable)
    for cluster in clusters:
        cx = sum(e['px'] for e in cluster) / len(cluster)
        cy = sum(e['py'] for e in cluster) / len(cluster)
        if cluster_max and len(cluster) > cluster_max:
            def draw_bubble(cx=cx, cy=cy, count=len(cluster)):
                odraw.ellipse(
                    (cx - bubble_r, cy - bubble_r, cx + bubble_r, cy + bubble_r),
                    fill=(44, 62, 80, 235), outline=(255, 255, 255, 235), width=2
                )
                text = str(count)
                bbox = odraw.textbbox((0, 0), text, font=font_bubble)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                odraw.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, fill=(255, 255, 255, 255), font=font_bubble)
            render_jobs.append((cy, draw_bubble))
        elif len(cluster) > LEGEND_MAX:
            # Paquet trop dense pour un empilement individuel : petit panneau en grille
            def draw_legend(cx=cx, cy=cy, members=cluster):
                row_h, col_w, max_rows = 20, 150, 8
                n = len(members)
                cols = max(1, math.ceil(n / max_rows))
                rows = math.ceil(n / cols)
                box_w, box_h = cols * col_w + 12, rows * row_h + 12
                x0, y0 = cx + 15, cy - box_h / 2
                odraw.line((cx, cy, x0, y0 + box_h / 2), fill=(90, 90, 90, 200), width=1)
                odraw.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=6, fill=(20, 20, 20, 205))
                for idx, mm in enumerate(sorted(members, key=lambda m: m['name'].lower())):
                    col, row = divmod(idx, rows)
                    tx, ty = x0 + 8 + col * col_w, y0 + 6 + row * row_h
                    odraw.ellipse((tx, ty + 5, tx + 9, ty + 14), fill=mm['color'])
                    label = mm['name'] if len(mm['name']) <= 18 else mm['name'][:17] + '…'
                    odraw.text((tx + 14, ty), label, fill=(255, 255, 255, 255), font=font_legend)
            render_jobs.append((cy, draw_legend))
        else:
            # Toutes les étiquettes du paquet s'ancrent sur le même point → empilement propre
            for e in sorted(cluster, key=lambda e: e['py']):
                def draw_label(cx=cx, cy=cy, name=e['name'], color=e['color']):
                    place_label(cx, cy, name, color)
                render_jobs.append((cy, draw_label))

    render_jobs.sort(key=lambda job: job[0])
    for _, job in render_jobs:
        job()

    image = Image.alpha_composite(image, overlay).convert('RGB')

    buf = io.BytesIO()
    image.save(buf, 'PNG')
    buf.seek(0)
    return buf


class CarteView(discord.ui.View):
    def __init__(self, guild, current='monde'):
        super().__init__(timeout=180)
        self.guild   = guild
        self.current = current
        self._build()

    def _build(self):
        self.clear_items()
        for key, preset in _CARTE_PRESETS.items():
            btn = discord.ui.Button(
                label=preset['label'],
                style=discord.ButtonStyle.primary if key == self.current else discord.ButtonStyle.secondary,
            )
            btn.callback = self._make_cb(key)
            self.add_item(btn)

    def _make_cb(self, key):
        async def callback(interaction: discord.Interaction):
            if key == self.current:
                await interaction.response.defer()
                return
            self.current = key
            self._build()
            await interaction.response.defer()
            try:
                buf = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _render_carte(_CARTE_PRESETS[key], self.guild)
                )
            except Exception as e:
                await interaction.followup.send(f"❌ Erreur : {e}", ephemeral=True)
                return
            await interaction.edit_original_response(
                content=self._content(),
                attachments=[discord.File(buf, 'carte.png')],
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return callback

    def _content(self):
        count  = len(locations)
        header = f"🗺️ **Carte de la communauté** — {count} membre{'s' if count > 1 else ''} enregistré{'s' if count > 1 else ''}"
        lines  = [header]
        for uid, loc in locations.items():
            member = self.guild.get_member(int(uid))
            name   = member.display_name if member else loc['ville']
            lines.append(f"**{name}** — {loc['ville']}")
        return '\n'.join(lines)


@bot.hybrid_command(name='carte')
async def cmd_carte(ctx):
    if not locations:
        await ctx.send("Personne n'a encore enregistré sa ville avec `!maville`.")
        return

    await ctx.typing()
    view = CarteView(ctx.guild, current='monde')

    try:
        buf = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _render_carte(_CARTE_PRESETS['monde'], ctx.guild)
        )
    except Exception as e:
        await ctx.send(f"❌ Erreur lors de la génération de la carte : {e}")
        return

    await ctx.send(view._content(), file=discord.File(buf, 'carte.png'), view=view, allowed_mentions=discord.AllowedMentions.none())


# ═════════════════════════════════════════════════════════════════════════
# ── Ranked 1v1 interne au serveur ───────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════

@tasks.loop(hours=6)
async def check_ranked_season():
    """Archive et remet à zéro le classement ranked 1v1 au changement de mois.
    Se base sur le mois stocké (pas l'heure exacte) pour rattraper un redémarrage manqué."""
    await bot.wait_until_ready()
    global ranked_season_month
    current_month = datetime.now().strftime('%Y-%m')
    if ranked_season_month is None:
        ranked_season_month = current_month
        save_data()
        return
    if current_month == ranked_season_month:
        return

    ended_month = ranked_season_month
    db_bs.archive_ranked_1v1_season(ended_month, {
        uid: {'points': p.get('points', 0), 'wins': p.get('wins', 0), 'losses': p.get('losses', 0)}
        for uid, p in ranked_1v1.items() if p.get('wins', 0) or p.get('losses', 0)
    })
    for p in ranked_1v1.values():
        p['points'] = 0
        p['wins']   = 0
        p['losses'] = 0
    ranked_season_month = current_month
    save_data()

    announcement = (
        f"🏆 **Nouvelle saison ranked 1v1 !**\n"
        f"Le classement de **{_r1v1_month_label(ended_month)}** est archivé "
        f"(consultable via `!classement_1v1`) — tout le monde repart de **0 point**."
    )
    for guild in bot.guilds:
        log_ch = guild.get_channel(RANKED_LOG_CHANNEL_ID)
        if log_ch:
            try:
                await log_ch.send(announcement)
            except Exception:
                pass


async def _r1v1_set_role(guild, uid: int, active: bool):
    """Pose ou retire le rôle 'Recherche Duel' — porté tant qu'un défi ou un duel 1v1 est en cours."""
    if not guild:
        return
    role = guild.get_role(RANKED_SEARCH_ROLE_ID)
    member = guild.get_member(uid)
    if not role or not member:
        return
    try:
        if active and role not in member.roles:
            await member.add_roles(role, reason="Défi/duel 1v1 en cours")
        elif not active and role in member.roles:
            await member.remove_roles(role, reason="Défi/duel 1v1 terminé")
    except (discord.Forbidden, discord.HTTPException):
        pass


async def _r1v1_purge_stale_challenges(guild=None):
    now = datetime.now()
    for cid in list(ranked_challenges.keys()):
        ch = ranked_challenges[cid]
        try:
            created = datetime.fromisoformat(ch['created_at'])
        except Exception:
            ranked_challenges.pop(cid, None)
            continue
        if (now - created).total_seconds() > RANKED_CHALLENGE_TTL_H * 3600:
            ranked_challenges.pop(cid, None)
            if guild:
                await _r1v1_set_role(guild, ch['challenger'], False)


def _r1v1_is_engaged(uid: int) -> bool:
    """True si uid a un duel en attente de résultat ou un défi actif en tant que challenger."""
    for v in ranked_pending.values():
        if uid in (v['p1'], v['p2']):
            return True
    for v in ranked_challenges.values():
        if v.get('challenger') == uid:
            return True
    return False


def _r1v1_find_pending(uid: int):
    for key, v in ranked_pending.items():
        if uid in (v['p1'], v['p2']):
            return key, v
    return None, None


class RankedChallengeView(discord.ui.View):
    """Défi 1v1 en attente d'acceptation — ouvert (n'importe qui) ou ciblé (un membre précis)."""
    def __init__(self, challenge_id: str, challenger_id: int, target_id: int = None, guild_id: int = None):
        super().__init__(timeout=RANKED_CHALLENGE_TTL_H * 3600)
        self.challenge_id = challenge_id
        self.challenger_id = challenger_id
        self.target_id = target_id
        self.guild_id = guild_id

    async def on_timeout(self):
        ranked_challenges.pop(self.challenge_id, None)
        save_data()
        guild = bot.get_guild(self.guild_id) if self.guild_id else None
        await _r1v1_set_role(guild, self.challenger_id, False)

    @discord.ui.button(label="✅ Accepter", style=discord.ButtonStyle.success)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        challenge = ranked_challenges.get(self.challenge_id)
        if not challenge:
            return await interaction.response.send_message("❌ Ce défi n'est plus disponible.", ephemeral=True)
        uid = interaction.user.id
        if uid == self.challenger_id:
            return await interaction.response.send_message("❌ Tu ne peux pas accepter ton propre défi.", ephemeral=True)
        if self.target_id and uid != self.target_id:
            return await interaction.response.send_message("❌ Ce défi n'est pas pour toi.", ephemeral=True)
        banned, wait = _r1v1_banned(str(uid))
        if banned:
            return await interaction.response.send_message(f"❌ Tu es exclu des 1v1 classés encore {wait}.", ephemeral=True)
        if _r1v1_is_engaged(uid):
            return await interaction.response.send_message(
                "❌ Tu as déjà un duel ou un défi en cours, termine-le avant d'en accepter un autre.", ephemeral=True)
        if not _r1v1_pair_cap_ok(uid, self.challenger_id):
            return await interaction.response.send_message(
                f"❌ Vous avez déjà fait {RANKED_MAX_DUELS_PER_DAY_PAIR} duels aujourd'hui ensemble. Réessayez demain.",
                ephemeral=True
            )

        ranked_challenges.pop(self.challenge_id, None)
        pair_key = _r1v1_pair_key(uid, self.challenger_id)
        ranked_pending[pair_key] = {
            'p1': self.challenger_id, 'p2': uid,
            'guild_id': interaction.guild_id, 'created_at': datetime.now().isoformat(),
            'votes': {},
        }
        save_data()
        await _r1v1_set_role(interaction.guild, uid, True)
        for item in self.children:
            item.disabled = True
        challenger = interaction.guild.get_member(self.challenger_id)
        challenger_name = challenger.display_name if challenger else f"<@{self.challenger_id}>"
        await interaction.response.edit_message(
            content=f"⚔️ **{challenger_name}** vs **{interaction.user.display_name}** — duel accepté !\n"
                    f"Une fois le combat terminé en jeu, refaites `!1v1` pour déclarer le résultat.",
            view=self
        )

    @discord.ui.button(label="Retirer le défi", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.challenger_id:
            return await interaction.response.send_message("❌ Seul l'auteur du défi peut le retirer.", ephemeral=True)
        ranked_challenges.pop(self.challenge_id, None)
        save_data()
        await _r1v1_set_role(interaction.guild, self.challenger_id, False)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="🚫 Défi retiré.", view=self)


class RankedResultView(discord.ui.View):
    """Vote à deux pour valider le résultat d'un duel 1v1 classé — même mécanique que MatchView (tournois)."""
    def __init__(self, pair_key: str, p1_id: int, p2_id: int, p1_name: str, p2_name: str):
        super().__init__(timeout=None)
        self.pair_key = pair_key
        self.p1_id, self.p2_id = p1_id, p2_id
        for pid, name in [(p1_id, p1_name), (p2_id, p2_name)]:
            btn = discord.ui.Button(label=f"🏆 {name[:60]} a gagné", style=discord.ButtonStyle.success)
            btn.callback = self._make_cb(pid)
            self.add_item(btn)
        cancel_btn = discord.ui.Button(label="🚫 Annuler le duel", style=discord.ButtonStyle.danger)
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _finalize(self, interaction, winner_id, loser_id, by_admin):
        win_delta, loss_delta = _r1v1_apply_result(winner_id, loser_id)
        ranked_pending.pop(self.pair_key, None)
        save_data()
        guild = interaction.guild
        await _r1v1_set_role(guild, winner_id, False)
        await _r1v1_set_role(guild, loser_id, False)
        for item in self.children:
            item.disabled = True
        winner = guild.get_member(winner_id) if guild else None
        loser = guild.get_member(loser_id) if guild else None
        wname = winner.display_name if winner else f"<@{winner_id}>"
        lname = loser.display_name if loser else f"<@{loser_id}>"
        wp = ranked_1v1[str(winner_id)]
        lp = ranked_1v1[str(loser_id)]
        desc = (
            f"🏆 **{wname}** bat **{lname}** !\n"
            f"{wname} : {win_delta:+d} pts → **{wp['points']}** ({_r1v1_tier_name(wp['points'])})\n"
            f"{lname} : {loss_delta:+d} pts → **{lp['points']}** ({_r1v1_tier_name(lp['points'])})"
        )
        if by_admin:
            desc += "\n*(résultat tranché par un admin)*"
        if winner_id == PROTECTED_FROM_PUNISH_ID:
            desc += "\n" + _azog_flavor(AZOG_DUEL_WIN_LINES)
        elif loser_id == PROTECTED_FROM_PUNISH_ID:
            desc += "\n" + _azog_flavor(AZOG_DUEL_LOSE_LINES)
        embed = discord.Embed(title="⚔️ Duel 1v1 — Résultat", description=desc, color=0x2ecc71)
        await interaction.response.edit_message(content=None, embed=embed, view=self)
        if guild:
            log_ch = guild.get_channel(RANKED_LOG_CHANNEL_ID)
            if log_ch:
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass

    def _make_cb(self, winner_id):
        async def callback(interaction: discord.Interaction):
            pending = ranked_pending.get(self.pair_key)
            if not pending:
                return await interaction.response.send_message("❌ Ce duel n'est plus en attente.", ephemeral=True)
            uid = interaction.user.id
            is_admin = bool(interaction.guild) and interaction.user.guild_permissions.administrator
            is_player = uid in (self.p1_id, self.p2_id)
            if not is_player and not is_admin:
                return await interaction.response.send_message(
                    "❌ Seuls les deux joueurs (ou un admin) peuvent déclarer le résultat.", ephemeral=True)

            loser_id = self.p2_id if winner_id == self.p1_id else self.p1_id

            if is_admin and not is_player:
                return await self._finalize(interaction, winner_id, loser_id, by_admin=True)

            votes = pending.setdefault('votes', {})
            votes[uid] = winner_id
            c1, c2 = self.p1_id, self.p2_id
            if c1 in votes and c2 in votes:
                if votes[c1] == votes[c2]:
                    win = votes[c1]
                    lose = self.p2_id if win == self.p1_id else self.p1_id
                    return await self._finalize(interaction, win, lose, by_admin=False)
                # Désaccord : reset des votes, alerte staff
                pending['votes'] = {}
                save_data()
                guild = interaction.guild
                p1 = guild.get_member(self.p1_id) if guild else None
                p2 = guild.get_member(self.p2_id) if guild else None
                p1n = p1.display_name if p1 else f"<@{self.p1_id}>"
                p2n = p2.display_name if p2 else f"<@{self.p2_id}>"
                conflict = discord.Embed(
                    title="⚠️ Duel 1v1 — Désaccord",
                    description=(
                        f"{p1n} et {p2n} ont désigné des vainqueurs différents.\n"
                        "Le staff a été notifié et va trancher.\n"
                        "Si vous pensez que l'autre joueur est de mauvaise foi, "
                        "vous pouvez le signaler avec `!signaler @joueur <raison>`."
                    ),
                    color=0xe74c3c
                )
                await interaction.response.edit_message(content=None, embed=conflict, view=self)
                if guild:
                    if guild.get_channel(RANKED_LOG_CHANNEL_ID):
                        try:
                            await guild.get_channel(RANKED_LOG_CHANNEL_ID).send(embed=conflict)
                        except Exception:
                            pass
                    await _admin_log(
                        guild, "Duel 1v1 contesté",
                        f"{p1n} et {p2n} ne sont pas d'accord sur le résultat de leur duel 1v1.",
                        color=0xe74c3c
                    )
                    if ADMIN_LOG_CHANNEL_ID:
                        admin_ch = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
                        if admin_ch:
                            resolve_view = RankedResultView(self.pair_key, self.p1_id, self.p2_id, p1n, p2n)
                            try:
                                await admin_ch.send("Trancher ce duel :", view=resolve_view)
                            except Exception:
                                pass
                return

            other_id = c2 if uid == c1 else c1
            waiting = discord.Embed(
                title="⚔️ Duel 1v1 — Vote enregistré",
                description=f"<@{uid}> a voté. En attente de la confirmation de <@{other_id}>…",
                color=0xe67e22
            )
            await interaction.response.edit_message(content=None, embed=waiting, view=self)
        return callback

    async def _cancel(self, interaction: discord.Interaction):
        pending = ranked_pending.get(self.pair_key)
        if not pending:
            return await interaction.response.send_message("❌ Ce duel n'est plus en attente.", ephemeral=True)
        uid = interaction.user.id
        is_admin = bool(interaction.guild) and interaction.user.guild_permissions.administrator
        if uid not in (self.p1_id, self.p2_id) and not is_admin:
            return await interaction.response.send_message("❌ Seuls les deux joueurs (ou un admin) peuvent annuler.", ephemeral=True)
        ranked_pending.pop(self.pair_key, None)
        save_data()
        await _r1v1_set_role(interaction.guild, self.p1_id, False)
        await _r1v1_set_role(interaction.guild, self.p2_id, False)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="🚫 Duel annulé.", embed=None, view=self)


class RankedLeaderboardView(discord.ui.View):
    """Podium top 3 + pagination (30/page) + sélecteur de saison (mois passés archivés)."""
    PAGE_SIZE = 30

    def __init__(self, guild, month=None, page=0):
        super().__init__(timeout=300)
        self.guild = guild
        self.month = month  # None = saison en cours
        self.entries = _r1v1_leaderboard_entries(guild, month)
        self.total_pages = max(1, (len(self.entries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(page, self.total_pages - 1))

        past_months = db_bs.list_ranked_1v1_seasons()
        if past_months:
            current_key = ranked_season_month or datetime.now().strftime('%Y-%m')
            options = [discord.SelectOption(
                label=f"📅 Saison actuelle ({_r1v1_month_label(current_key)})"[:100],
                value="__current__", default=(month is None)
            )]
            for m in past_months[:24]:
                options.append(discord.SelectOption(label=_r1v1_month_label(m)[:100], value=m, default=(m == month)))
            self.month_select = discord.ui.Select(placeholder="📅 Choisir une saison…", options=options)
            self.month_select.callback = self._on_month
            self.add_item(self.month_select)

        if self.page > 0:
            prev_btn = discord.ui.Button(label="◀ Précédent", style=discord.ButtonStyle.secondary, row=1)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)
        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(label="Suivant ▶", style=discord.ButtonStyle.secondary, row=1)
            next_btn.callback = self._next
            self.add_item(next_btn)

    def build_embed(self) -> discord.Embed:
        month_label = "Saison actuelle" if self.month is None else _r1v1_month_label(self.month)
        embed = discord.Embed(title=f"🥊 Classement Ranked 1v1 — {month_label}", color=0xc0392b)
        start = self.page * self.PAGE_SIZE
        page_entries = self.entries[start:start + self.PAGE_SIZE]

        if self.page == 0:
            medals = ['🥇', '🥈', '🥉']
            for i, e in enumerate(self.entries[:3]):
                embed.add_field(
                    name=f"{medals[i]} {e['name']}",
                    value=f"{e['tier']}\n**{e['points']:,} pts** · {e['wins']}V/{e['losses']}D",
                    inline=True
                )
            rest, rank_offset = page_entries[3:], 4
        else:
            rest, rank_offset = page_entries, start + 1

        if rest:
            lines = []
            for i, e in enumerate(rest):
                rank = rank_offset + i
                lines.append(f"**{rank}.** {e['name']} — {e['tier']} · **{e['points']:,} pts** ({e['wins']}V/{e['losses']}D)")
            for i in range(0, len(lines), 10):
                embed.add_field(name=chr(8203), value="\n".join(lines[i:i + 10]), inline=False)
        elif not self.entries:
            embed.description = "Personne n'a fait de duel classé sur cette période."

        embed.set_footer(text=f"{len(self.entries)} joueur(s) classé(s) · Page {self.page + 1}/{self.total_pages}")
        return embed

    async def _on_month(self, interaction: discord.Interaction):
        value = self.month_select.values[0]
        month = None if value == "__current__" else value
        view = RankedLeaderboardView(self.guild, month, 0)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _prev(self, interaction: discord.Interaction):
        view = RankedLeaderboardView(self.guild, self.month, self.page - 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _next(self, interaction: discord.Interaction):
        view = RankedLeaderboardView(self.guild, self.month, self.page + 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


@bot.hybrid_command(name="1v1", aliases=["ranked"])
async def cmd_1v1(ctx, membre: discord.Member = None):
    author_id = ctx.author.id

    # Un duel accepté attend un résultat → on affiche le vote, peu importe la mention
    pair_key, pending = _r1v1_find_pending(author_id)
    if pending:
        guild = ctx.guild
        p1 = guild.get_member(pending['p1']) if guild else None
        p2 = guild.get_member(pending['p2']) if guild else None
        p1n = p1.display_name if p1 else f"<@{pending['p1']}>"
        p2n = p2.display_name if p2 else f"<@{pending['p2']}>"
        view = RankedResultView(pair_key, pending['p1'], pending['p2'], p1n, p2n)
        embed = discord.Embed(
            title="⚔️ Déclarer le résultat du duel",
            description=f"{p1n} vs {p2n}\nCliquez sur le vainqueur (les deux joueurs doivent être d'accord).",
            color=0xe67e22
        )
        return await ctx.send(embed=embed, view=view)

    await _r1v1_purge_stale_challenges(ctx.guild)

    existing_id = next((cid for cid, ch in ranked_challenges.items() if ch.get('challenger') == author_id), None)
    if existing_id:
        # Réaffiche un bouton fonctionnel — si le bot a redémarré depuis la création du défi,
        # l'ancien message a des boutons morts (les vues ne survivent pas à un redémarrage).
        ch = ranked_challenges[existing_id]
        view = RankedChallengeView(existing_id, author_id, ch.get('target'), ctx.guild.id if ctx.guild else None)
        return await ctx.send(
            "❌ Tu as déjà un défi en attente. Utilise les boutons ci-dessous pour l'accepter/annuler :",
            view=view
        )

    banned, wait = _r1v1_banned(str(author_id))
    if banned:
        return await ctx.send(f"❌ Tu es exclu des 1v1 classés encore {wait}.")

    if membre:
        if membre.id == author_id or membre.bot:
            return await ctx.send("❌ Cible invalide.")
        target_banned, target_wait = _r1v1_banned(str(membre.id))
        if target_banned:
            return await ctx.send(f"❌ {membre.mention} est exclu des 1v1 classés encore {target_wait}.")
        if _r1v1_is_engaged(membre.id):
            return await ctx.send(f"❌ {membre.mention} a déjà un duel ou un défi en cours.")
        if not _r1v1_pair_cap_ok(author_id, membre.id):
            return await ctx.send(
                f"❌ Vous avez déjà fait {RANKED_MAX_DUELS_PER_DAY_PAIR} duels aujourd'hui ensemble. Réessayez demain.")

    challenge_id = f"{author_id}_{int(datetime.now().timestamp())}"
    ranked_challenges[challenge_id] = {
        'type': 'target' if membre else 'open',
        'challenger': author_id,
        'target': membre.id if membre else None,
        'guild_id': ctx.guild.id if ctx.guild else None,
        'created_at': datetime.now().isoformat(),
    }
    save_data()
    await _r1v1_set_role(ctx.guild, author_id, True)

    view = RankedChallengeView(challenge_id, author_id, membre.id if membre else None, ctx.guild.id if ctx.guild else None)
    if membre:
        content = f"⚔️ {ctx.author.mention} défie {membre.mention} en 1v1 classé ! Seul {membre.mention} peut accepter."
    else:
        content = f"⚔️ {ctx.author.mention} cherche un adversaire pour un 1v1 classé ! Premier arrivé, premier servi."

    challenge_channel = ctx.guild.get_channel(RANKED_CHALLENGE_CHANNEL_ID) if ctx.guild else None
    target_channel = challenge_channel or ctx.channel
    msg = await target_channel.send(content, view=view)
    ranked_challenges[challenge_id]['channel_id'] = target_channel.id
    ranked_challenges[challenge_id]['message_id'] = msg.id
    save_data()
    if target_channel.id != ctx.channel.id:
        await ctx.send(f"✅ Défi envoyé dans {target_channel.mention} !")


@bot.hybrid_command(name="classement_1v1", aliases=["top_1v1"])
async def cmd_classement_1v1(ctx):
    view = RankedLeaderboardView(ctx.guild)
    await ctx.send(embed=view.build_embed(), view=view)


@bot.hybrid_command(name="signaler")
async def cmd_signaler(ctx, joueur: discord.Member, *, raison: str):
    if joueur.id == ctx.author.id or joueur.bot:
        return await ctx.send("❌ Cible invalide.")
    key = f"{ctx.author.id}_{joueur.id}"
    last = ranked_report_cooldowns.get(key)
    if last:
        elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
        if elapsed < 86400:
            wait_h = max(1, int((86400 - elapsed) // 3600))
            return await ctx.send(f"❌ Tu as déjà signalé ce joueur récemment. Réessaie dans ~{wait_h}h.")
    ranked_report_cooldowns[key] = datetime.now().isoformat()
    ranked_reports.setdefault(str(joueur.id), []).append({
        'reporter': ctx.author.id, 'reason': raison,
        'guild_id': ctx.guild.id if ctx.guild else None,
        'created_at': datetime.now().isoformat(), 'resolved': False,
    })
    save_data()
    if ctx.guild:
        await _admin_log(
            ctx.guild, "🚩 Signalement 1v1",
            f"{ctx.author.mention} a signalé {joueur.mention}.\n**Raison :** {raison}",
            color=0xe67e22, author=ctx.author
        )
    await ctx.send(
        f"✅ Signalement envoyé au staff concernant {joueur.mention}.\n"
        f"⚠️ Un signalement validé par le staff peut faire baisser sa réputation et l'exclure temporairement des 1v1 classés."
    )


@bot.command(name="ranked_sanction")
async def cmd_ranked_sanction(ctx, joueur: discord.Member):
    reports = ranked_reports.get(str(joueur.id), [])
    unresolved = next((r for r in reports if not r.get('resolved')), None)
    if not unresolved:
        return await ctx.send(f"❌ Aucun signalement non résolu pour {joueur.mention}.")
    unresolved['resolved'] = True
    prof = _r1v1_profile(str(joueur.id))
    prof['reputation'] = max(0, prof['reputation'] - RANKED_REP_PENALTY)
    banned_msg = ""
    if prof['reputation'] <= RANKED_REP_BAN_THRESHOLD:
        until = datetime.now() + timedelta(hours=RANKED_REP_BAN_HOURS)
        prof['banned_until'] = until.isoformat()
        banned_msg = f"\n🚫 Réputation trop basse : exclu des 1v1 classés pendant {RANKED_REP_BAN_HOURS}h."
    save_data()
    await ctx.send(f"✅ Signalement traité pour {joueur.mention}. Réputation : **{prof['reputation']}/100**.{banned_msg}")
    if ctx.guild:
        await _admin_log(
            ctx.guild, "⚖️ Sanction 1v1 appliquée",
            f"{ctx.author.mention} a validé un signalement contre {joueur.mention}. "
            f"Réputation → {prof['reputation']}/100.{banned_msg}",
            color=0xc0392b, author=ctx.author
        )


def _pending_ranked_reports(guild_id: int | None):
    """Liste (target_uid, index, report) des signalements non résolus, du plus
    ancien au plus récent — guild_id optionnel pour ne montrer que ceux du
    serveur courant (un report sans guild_id enregistré passe quand même,
    même logique que cmd_ranked_sanction qui ne filtre pas non plus par serveur)."""
    out = []
    for target_uid, reports in ranked_reports.items():
        for i, r in enumerate(reports):
            if r.get('resolved'):
                continue
            if guild_id is not None and r.get('guild_id') not in (None, guild_id):
                continue
            out.append((target_uid, i, r))
    out.sort(key=lambda t: t[2].get('created_at', ''))
    return out


def _ranked_reports_embed(guild, pending) -> discord.Embed:
    embed = discord.Embed(title="🚩 Signalements 1v1 en attente", color=0xe67e22)
    if not pending:
        embed.description = "*Aucun signalement en attente.*"
        return embed
    lines = []
    for target_uid, _i, r in pending[:25]:
        target = guild.get_member(int(target_uid)) if guild else None
        reporter = guild.get_member(r['reporter']) if guild else None
        target_name = target.display_name if target else f"ID {target_uid}"
        reporter_name = reporter.display_name if reporter else f"ID {r['reporter']}"
        lines.append(f"**{target_name}** — signalé par {reporter_name} : {r['reason'][:80]}")
    embed.description = "\n".join(lines)
    embed.set_footer(text="Choisis un signalement dans le menu pour le traiter.")
    return embed


class RankedReportActionView(discord.ui.View):
    """Étape 2 : sanctionner ou rejeter le signalement choisi dans RankedReportsView."""

    def __init__(self, guild, target_uid: str, index: int):
        super().__init__(timeout=180)
        self.guild = guild
        self.target_uid = target_uid
        self.index = index

        sanction_btn = discord.ui.Button(label="⚖️ Sanctionner", style=discord.ButtonStyle.danger)
        sanction_btn.callback = self._on_sanction
        self.add_item(sanction_btn)

        reject_btn = discord.ui.Button(label="🗑️ Rejeter (sans pénalité)", style=discord.ButtonStyle.secondary)
        reject_btn.callback = self._on_reject
        self.add_item(reject_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux admins/owner.", ephemeral=True)
            return False
        return True

    def _report(self):
        reports = ranked_reports.get(self.target_uid, [])
        return reports[self.index] if self.index < len(reports) else None

    async def _refresh(self, interaction: discord.Interaction, content: str):
        pending = _pending_ranked_reports(self.guild.id if self.guild else None)
        await interaction.response.edit_message(
            content=content, embed=_ranked_reports_embed(self.guild, pending),
            view=RankedReportsView(self.guild, pending), allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _on_sanction(self, interaction: discord.Interaction):
        report = self._report()
        if not report or report.get('resolved'):
            return await self._refresh(interaction, "ℹ️ Ce signalement a déjà été traité.")
        report['resolved'] = True
        prof = _r1v1_profile(self.target_uid)
        prof['reputation'] = max(0, prof['reputation'] - RANKED_REP_PENALTY)
        banned_msg = ""
        if prof['reputation'] <= RANKED_REP_BAN_THRESHOLD:
            until = datetime.now() + timedelta(hours=RANKED_REP_BAN_HOURS)
            prof['banned_until'] = until.isoformat()
            banned_msg = f"\n🚫 Réputation trop basse : exclu des 1v1 classés pendant {RANKED_REP_BAN_HOURS}h."
        save_data()
        member = self.guild.get_member(int(self.target_uid)) if self.guild else None
        name = member.mention if member else f"<@{self.target_uid}>"
        if self.guild:
            await _admin_log(
                self.guild, "⚖️ Sanction 1v1 appliquée",
                f"{interaction.user.mention} a validé un signalement contre {name} (via panel). "
                f"Réputation → {prof['reputation']}/100.{banned_msg}",
                color=0xc0392b, author=interaction.user,
            )
        await self._refresh(interaction, f"✅ Signalement traité pour {name}. Réputation : **{prof['reputation']}/100**.{banned_msg}")

    async def _on_reject(self, interaction: discord.Interaction):
        report = self._report()
        if not report or report.get('resolved'):
            return await self._refresh(interaction, "ℹ️ Ce signalement a déjà été traité.")
        report['resolved'] = True
        save_data()
        member = self.guild.get_member(int(self.target_uid)) if self.guild else None
        name = member.mention if member else f"<@{self.target_uid}>"
        await self._refresh(interaction, f"🗑️ Signalement rejeté pour {name} (aucune pénalité appliquée).")


class RankedReportsView(discord.ui.View):
    """Panel listant les signalements 1v1 non résolus, en alternative à
    !ranked_sanction @membre qui exige de déjà savoir qui a un signalement
    en attente — même esprit que AbsenceStaffPanelView."""

    def __init__(self, guild, pending):
        super().__init__(timeout=300)
        self.guild = guild
        self.pending = pending

        if pending:
            options = []
            for target_uid, i, r in pending[:25]:
                member = guild.get_member(int(target_uid)) if guild else None
                name = member.display_name if member else f"ID {target_uid}"
                options.append(discord.SelectOption(label=name[:100], description=r['reason'][:100], value=f"{target_uid}:{i}"))
            self.report_select = discord.ui.Select(placeholder="🚩 Choisir un signalement à traiter…", options=options, row=0)
            self.report_select.callback = self._on_select
            self.add_item(self.report_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (interaction.user.guild_permissions.administrator or is_bot_owner(interaction.user)):
            await interaction.response.send_message("❌ Réservé aux admins/owner.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        target_uid, idx = self.report_select.values[0].split(":")
        report = ranked_reports.get(target_uid, [])[int(idx)]
        member = self.guild.get_member(int(target_uid)) if self.guild else None
        name = member.mention if member else f"<@{target_uid}>"
        embed = discord.Embed(
            title="🚩 Signalement sélectionné", color=0xe67e22,
            description=f"**Joueur :** {name}\n**Signalé par :** <@{report['reporter']}>\n**Raison :** {report['reason']}",
        )
        await interaction.response.edit_message(
            embed=embed, view=RankedReportActionView(self.guild, target_uid, int(idx)),
            allowed_mentions=discord.AllowedMentions.none(),
        )


@bot.command(name="signalements", aliases=["signalements_1v1", "ranked_sanction_panel"])
async def cmd_signalements(ctx):
    """Panel listant les signalements 1v1 non résolus avec Sanctionner/Rejeter,
    en alternative à !ranked_sanction @membre. Réservé au propriétaire du
    serveur / rôle autorisé via !permission, comme !ranked_sanction (voir
    ADMIN_LOCKED_CMDS) — pas de check ici, géré par le gate global."""
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")
    pending = _pending_ranked_reports(ctx.guild.id)
    await ctx.send(embed=_ranked_reports_embed(ctx.guild, pending), view=RankedReportsView(ctx.guild, pending))


@bot.command(name="ranked_ajuster")
async def cmd_ranked_ajuster(ctx, joueur: discord.Member, delta: int):
    prof = _r1v1_profile(str(joueur.id))
    prof['points'] = max(0, prof['points'] + delta)
    save_data()
    await ctx.send(
        f"✅ Points de {joueur.mention} ajustés de {delta:+d} → "
        f"**{prof['points']} pts** ({_r1v1_tier_name(prof['points'])})."
    )


@bot.command(name="ranked_set")
async def cmd_ranked_set(ctx, joueur: discord.Member, points: int, victoires: int, defaites: int):
    """Fixe directement points/V/D d'un joueur (reconstruction manuelle après incident)."""
    prof = _r1v1_profile(str(joueur.id))
    prof['points'] = max(0, points)
    prof['wins'] = max(0, victoires)
    prof['losses'] = max(0, defaites)
    save_data()
    await ctx.send(
        f"✅ {joueur.mention} fixé à **{prof['points']} pts** ({_r1v1_tier_name(prof['points'])}) "
        f"— {prof['wins']}V/{prof['losses']}D."
    )


async def _confirm_action(ctx, warning: str) -> bool:
    """Demande de taper CONFIRMER dans les 10s. Retourne True si confirmé, False sinon
    (et envoie déjà le message d'annulation le cas échéant)."""
    confirm_msg = await ctx.send(f"{warning}\nConfirmez en tapant `CONFIRMER` dans les 10 secondes.")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRMER"

    try:
        await bot.wait_for('message', check=check, timeout=10.0)
    except asyncio.TimeoutError:
        await ctx.send("❌ Annulé — vous n'avez pas confirmé à temps.")
        return False
    finally:
        try:
            await confirm_msg.delete()
        except discord.HTTPException:
            pass
    return True


# ═════════════════════════════════════════════════════════════════════════
# ── Verrouillage de salon(s) (!lock, !lock_serveur) ────────────────────────
# ═════════════════════════════════════════════════════════════════════════
# @everyone perd send_messages ; Fonda/Admin/Modérateur reçoivent un overwrite
# explicite send_messages=True pour continuer à écrire (résolution Discord :
# l'overwrite de rôle spécifique l'emporte sur celui de @everyone). L'état
# PRÉCÉDENT de chaque cible touchée est sauvegardé dans lock_overwrite_backup
# avant modification, pour que le déverrouillage restaure exactement ce qui
# existait avant plutôt que d'ouvrir en aveugle un salon déjà restreint.

async def _lock_channel(channel: discord.TextChannel, guild: discord.Guild):
    everyone = guild.default_role
    backup = lock_overwrite_backup.setdefault(str(channel.id), {})

    ov_everyone = channel.overwrites_for(everyone)
    backup.setdefault(str(everyone.id), ov_everyone.send_messages)
    ov_everyone.send_messages = False
    await channel.set_permissions(everyone, overwrite=ov_everyone, reason="Verrouillage")

    for role_id in DISCORD_MOD_STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if not role:
            continue
        ov_role = channel.overwrites_for(role)
        backup.setdefault(str(role.id), ov_role.send_messages)
        ov_role.send_messages = True
        await channel.set_permissions(role, overwrite=ov_role, reason="Verrouillage — exception staff Discord")


async def _unlock_channel(channel: discord.TextChannel, guild: discord.Guild) -> bool:
    """Retourne False si ce salon n'était pas suivi comme verrouillé (rien à faire)."""
    backup = lock_overwrite_backup.pop(str(channel.id), None)
    if not backup:
        return False
    for target_id_str, prev_value in backup.items():
        target = guild.get_role(int(target_id_str))
        if not target:
            continue
        overwrite = channel.overwrites_for(target)
        overwrite.send_messages = prev_value
        if overwrite.is_empty():
            await channel.set_permissions(target, overwrite=None, reason="Déverrouillage")
        else:
            await channel.set_permissions(target, overwrite=overwrite, reason="Déverrouillage")
    return True


def _log_channel_ids() -> set[int]:
    ids = set()
    for varname in LOG_CATEGORY_VARS.values():
        cid = globals().get(varname)
        if cid:
            ids.add(cid)
    return ids


@bot.command(name="lock")
async def cmd_lock(ctx):
    """Verrouille le salon courant : @everyone perd le droit d'écrire, seuls
    Fonda/Admin/Modérateur le gardent. Restaure l'état exact d'avant via !unlock."""
    if not _is_discord_mod_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff Discord (Fonda/Admin/Modérateur).")
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")
    try:
        await _lock_channel(ctx.channel, ctx.guild)
    except discord.Forbidden:
        return await ctx.send("❌ Permissions insuffisantes pour modifier ce salon.")
    save_data()
    await ctx.send(f"🔒 {ctx.channel.mention} verrouillé — seuls Fonda/Admin/Modérateur peuvent encore écrire.")


@bot.command(name="unlock")
async def cmd_unlock(ctx):
    """Déverrouille le salon courant — restaure l'état d'avant !lock."""
    if not _is_discord_mod_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff Discord (Fonda/Admin/Modérateur).")
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")
    try:
        restored = await _unlock_channel(ctx.channel, ctx.guild)
    except discord.Forbidden:
        return await ctx.send("❌ Permissions insuffisantes pour modifier ce salon.")
    if not restored:
        return await ctx.send("ℹ️ Ce salon n'est pas verrouillé.")
    save_data()
    await ctx.send(f"🔓 {ctx.channel.mention} déverrouillé.")


@bot.command(name="lock_serveur", aliases=["lockserveur", "lock_server"])
async def cmd_lock_serveur(ctx):
    """Verrouille TOUS les salons textuels du serveur sauf les salons de logs
    configurés (!set_logs) — mêmes règles que !lock (Fonda/Admin/Modérateur
    gardent l'écriture). Demande confirmation avant d'agir : action à fort
    impact, touche tout le monde d'un coup."""
    if not _is_discord_mod_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff Discord (Fonda/Admin/Modérateur).")
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")

    excluded = _log_channel_ids()
    targets = [c for c in ctx.guild.text_channels if c.id not in excluded]
    if not await _confirm_action(
        ctx,
        f"⚠️ **Verrouiller {len(targets)} salon(s)** du serveur (logs exclus) ? "
        f"Seuls Fonda/Admin/Modérateur pourront encore écrire."
    ):
        return

    locked = 0
    async with ctx.typing():
        for channel in targets:
            try:
                await _lock_channel(channel, ctx.guild)
                locked += 1
            except discord.Forbidden:
                pass
            await asyncio.sleep(0.5)
    save_data()
    await ctx.send(f"🔒 Serveur verrouillé — {locked}/{len(targets)} salon(s) verrouillé(s) (logs exclus).")


@bot.command(name="unlock_serveur", aliases=["unlockserveur", "unlock_server"])
async def cmd_unlock_serveur(ctx):
    """Déverrouille tous les salons actuellement suivis comme verrouillés sur
    ce serveur (par !lock ou !lock_serveur) — restaure l'état d'avant pour
    chacun."""
    if not _is_discord_mod_staff(ctx.author):
        return await ctx.send("❌ Réservé au staff Discord (Fonda/Admin/Modérateur).")
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")

    channel_ids = [int(cid) for cid in list(lock_overwrite_backup.keys())]
    unlocked = 0
    async with ctx.typing():
        for cid in channel_ids:
            channel = ctx.guild.get_channel(cid)
            if not channel:
                lock_overwrite_backup.pop(str(cid), None)
                continue
            try:
                if await _unlock_channel(channel, ctx.guild):
                    unlocked += 1
            except discord.Forbidden:
                pass
            await asyncio.sleep(0.5)
    save_data()
    await ctx.send(f"🔓 Serveur déverrouillé — {unlocked} salon(s) restauré(s).")


def _reset_casino_state():
    """Remet à zéro coins/coffres/usines/commerces/métiers pour tout le monde.
    Ne sauvegarde pas — à l'appelant de save_data() une fois toutes les mises à jour faites."""
    coins.clear()
    safes.clear()
    factories.clear()
    businesses.clear()
    jobs_data.clear()


@bot.command(name="reset_casino")
async def cmd_reset_casino(ctx):
    """Reset manuel de la saison casino : coins, coffres, usines, commerces et métiers repartent
    à zéro. Les items achetés (boutique) et l'inventaire sont conservés. Le classement coins
    est archivé avant reset (voir db_bs.archive_casino_season), consultable ensuite via
    !classement_casino — safes/factories/businesses/jobs_data ne sont pas archivés (le
    classement casino public n'a jamais porté que sur les coins, voir /api/famille/classement_casino)."""
    nb = len(set(coins.keys()) | {int(k) for k in safes.keys()} | {int(k) for k in factories.keys()}
             | {int(k) for k in businesses.keys()} | {int(k) for k in jobs_data.keys()})
    if not await _confirm_action(
        ctx,
        f"⚠️ **ATTENTION :** Ça va reset les coins, coffres, usines, commerces et métiers de **{nb} joueur(s)**. "
        f"Le classement coins sera archivé (consultable via `!classement_casino`), le reste est irréversible."
    ):
        return

    global casino_season_month
    ended_month = casino_season_month or datetime.now().strftime('%Y-%m')
    db_bs.archive_casino_season(ended_month, {str(uid): amount for uid, amount in coins.items() if amount > 0})
    _reset_casino_state()
    casino_season_month = datetime.now().strftime('%Y-%m')
    save_data()
    await ctx.send(
        f"✅ **Casino réinitialisé !** ({nb} joueur(s) concernés)\n"
        f"Coins, coffres, usines, commerces et métiers repartent à zéro pour tout le monde.\n"
        f"Les items achetés (boutique) et l'inventaire sont conservés. "
        f"Ancien classement coins consultable via `!classement_casino`."
    )


@bot.command(name="casino_ban")
async def cmd_casino_ban(ctx, membre: discord.Member, *, raison: str = None):
    """Bloque un joueur sur toutes les commandes casino (paris, jeux, travail, vol...) —
    reste banni même s'il est admin. Voir !casino_unban pour annuler."""
    casino_banned_users.add(membre.id)
    save_data()
    _log_moderation('casino_ban', membre, ctx.author, reason=raison)
    await send_log_message(
        ctx.guild, CASINO_LOG_CHANNEL_ID, "🚫 Casino ban",
        f"{membre.mention} n'a plus accès aux commandes casino (par {ctx.author.mention})." + (f"\nRaison : {raison}" if raison else ""),
        discord.Color.dark_red(),
    )
    await ctx.send(f"🚫 {membre.mention} n'a désormais plus accès aux commandes casino." + (f" Raison : {raison}" if raison else ""))


@bot.command(name="casino_unban")
async def cmd_casino_unban(ctx, membre: discord.Member):
    """Annule un !casino_ban."""
    if membre.id not in casino_banned_users:
        return await ctx.send(f"❌ {membre.mention} n'est pas banni du casino.")
    casino_banned_users.discard(membre.id)
    save_data()
    _log_moderation('casino_unban', membre, ctx.author)
    await send_log_message(
        ctx.guild, CASINO_LOG_CHANNEL_ID, "✅ Casino unban",
        f"{membre.mention} a de nouveau accès aux commandes casino (par {ctx.author.mention}).",
        discord.Color.green(),
    )
    await ctx.send(f"✅ {membre.mention} a de nouveau accès aux commandes casino.")


def _casino_leaderboard_entries(guild, month=None):
    """Entrées triées par coins pour le mois en cours (month=None) ou une saison archivée —
    même structure que _r1v1_leaderboard_entries."""
    if month is None:
        source = [(uid, amount) for uid, amount in coins.items() if amount > 0]
    else:
        source = [(row['discord_id'], row['coins']) for row in db_bs.get_casino_season(month)]
    entries = []
    for uid, amount in source:
        member = guild.get_member(int(uid)) if guild else None
        name = member.display_name if member else f"<@{uid}>"
        entries.append({'name': name, 'coins': amount})
    entries.sort(key=lambda e: e['coins'], reverse=True)
    return entries


class CasinoLeaderboardView(discord.ui.View):
    """Podium top 3 + pagination (30/page) + sélecteur de saison (mois passés archivés) —
    même structure que RankedLeaderboardView (voir plus haut)."""
    PAGE_SIZE = 30

    def __init__(self, guild, month=None, page=0):
        super().__init__(timeout=300)
        self.guild = guild
        self.month = month  # None = mois en cours
        self.entries = _casino_leaderboard_entries(guild, month)
        self.total_pages = max(1, (len(self.entries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(page, self.total_pages - 1))

        past_months = db_bs.list_casino_seasons()
        if past_months:
            current_key = casino_season_month or datetime.now().strftime('%Y-%m')
            options = [discord.SelectOption(
                label=f"📅 Saison actuelle ({_r1v1_month_label(current_key)})"[:100],
                value="__current__", default=(month is None)
            )]
            for m in past_months[:24]:
                options.append(discord.SelectOption(label=_r1v1_month_label(m)[:100], value=m, default=(m == month)))
            self.month_select = discord.ui.Select(placeholder="📅 Choisir une saison…", options=options)
            self.month_select.callback = self._on_month
            self.add_item(self.month_select)

        if self.page > 0:
            prev_btn = discord.ui.Button(label="◀ Précédent", style=discord.ButtonStyle.secondary, row=1)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)
        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(label="Suivant ▶", style=discord.ButtonStyle.secondary, row=1)
            next_btn.callback = self._next
            self.add_item(next_btn)

    def build_embed(self) -> discord.Embed:
        month_label = "Mois en cours" if self.month is None else _r1v1_month_label(self.month)
        embed = discord.Embed(title=f"💰 Classement Casino — {month_label}", color=0xf39c12)
        start = self.page * self.PAGE_SIZE
        page_entries = self.entries[start:start + self.PAGE_SIZE]

        if self.page == 0:
            medals = ['🥇', '🥈', '🥉']
            for i, e in enumerate(self.entries[:3]):
                embed.add_field(name=f"{medals[i]} {e['name']}", value=f"**{e['coins']:,} coins**", inline=True)
            rest, rank_offset = page_entries[3:], 4
        else:
            rest, rank_offset = page_entries, start + 1

        if rest:
            lines = [f"**{rank_offset + i}.** {e['name']} — **{e['coins']:,} coins**" for i, e in enumerate(rest)]
            for i in range(0, len(lines), 10):
                embed.add_field(name=chr(8203), value="\n".join(lines[i:i + 10]), inline=False)
        elif not self.entries:
            embed.description = "Personne n'a de coins sur cette période."

        embed.set_footer(text=f"{len(self.entries)} joueur(s) classé(s) · Page {self.page + 1}/{self.total_pages}")
        return embed

    async def _on_month(self, interaction: discord.Interaction):
        value = self.month_select.values[0]
        month = None if value == "__current__" else value
        view = CasinoLeaderboardView(self.guild, month, 0)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _prev(self, interaction: discord.Interaction):
        view = CasinoLeaderboardView(self.guild, self.month, self.page - 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def _next(self, interaction: discord.Interaction):
        view = CasinoLeaderboardView(self.guild, self.month, self.page + 1)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


@bot.hybrid_command(name="classement_casino", aliases=["top_casino"])
async def cmd_classement_casino(ctx):
    view = CasinoLeaderboardView(ctx.guild)
    await ctx.send(embed=view.build_embed(), view=view)


@bot.command(name="casino_pause")
async def cmd_casino_pause(ctx):
    """Met en pause TOUTES les commandes casino pour tout le monde (y compris les admins) —
    utile juste avant un déploiement pour ne pas couper une partie en cours. Voir !casino_resume."""
    global casino_paused
    casino_paused = True
    await send_log_message(
        ctx.guild, CASINO_LOG_CHANNEL_ID, "⏸️ Casino en pause",
        f"Casino mis en pause par {ctx.author.mention}.", discord.Color.orange(),
    )
    await ctx.send("⏸️ **Casino en pause.** Plus aucune commande casino ne sera acceptée jusqu'à `!casino_resume`.")


@bot.command(name="casino_resume")
async def cmd_casino_resume(ctx):
    """Annule un !casino_pause."""
    global casino_paused
    casino_paused = False
    await send_log_message(
        ctx.guild, CASINO_LOG_CHANNEL_ID, "▶️ Casino relancé",
        f"Casino relancé par {ctx.author.mention}.", discord.Color.green(),
    )
    await ctx.send("▶️ **Casino relancé.** Les commandes casino sont de nouveau disponibles.")


@tasks.loop(hours=6)
async def check_casino_season():
    """Reset automatique du casino au changement de mois calendaire — même logique que
    check_ranked_season (duels), indépendant des saisons Brawl Stars. Archive d'abord
    dans Supabase (voir db_bs.archive_casino_season) : avant le 29/07/2026, ce reset
    effaçait tout sans rien garder nulle part, contrairement au reset 1v1 qui, lui,
    archivait déjà (incident repéré lors d'un audit des systèmes de saison)."""
    await bot.wait_until_ready()
    global casino_season_month
    current_month = datetime.now().strftime('%Y-%m')
    if casino_season_month is None:
        casino_season_month = current_month
        save_data()
        return
    if current_month == casino_season_month:
        return

    db_bs.archive_casino_season(casino_season_month, {str(uid): amount for uid, amount in coins.items() if amount > 0})
    _reset_casino_state()
    casino_season_month = current_month
    save_data()


@bot.command(name="reset_duels")
async def cmd_reset_duels(ctx):
    """Reset manuel de la saison ranked 1v1 (même logique que le reset automatique mensuel,
    déclenchée ici à la demande plutôt que d'attendre le changement de mois)."""
    nb = sum(1 for p in ranked_1v1.values() if p.get('wins', 0) or p.get('losses', 0))
    if not await _confirm_action(
        ctx,
        f"⚠️ **ATTENTION :** Ça va archiver et reset le classement 1v1 de **{nb} joueur(s)**. Irréversible."
    ):
        return

    global ranked_season_month
    ended_month = ranked_season_month or datetime.now().strftime('%Y-%m')
    db_bs.archive_ranked_1v1_season(ended_month, {
        uid: {'points': p.get('points', 0), 'wins': p.get('wins', 0), 'losses': p.get('losses', 0)}
        for uid, p in ranked_1v1.items() if p.get('wins', 0) or p.get('losses', 0)
    })
    for p in ranked_1v1.values():
        p['points'] = 0
        p['wins']   = 0
        p['losses'] = 0
    ranked_season_month = datetime.now().strftime('%Y-%m')
    save_data()
    await ctx.send(
        f"✅ **Classement 1v1 réinitialisé !** L'ancienne saison ({_r1v1_month_label(ended_month)}) "
        f"est archivée et consultable via `!classement_1v1`."
    )


@bot.command(name="ranked_liberer", aliases=["ranked_unstuck", "duel_liberer"])
async def cmd_ranked_liberer(ctx, joueur: discord.Member):
    """Efface tout défi/duel en attente bloqué pour un joueur (ex: vue morte après un redémarrage du bot)."""
    uid = joueur.id
    removed = []
    for cid in list(ranked_challenges.keys()):
        if ranked_challenges[cid].get('challenger') == uid:
            ranked_challenges.pop(cid, None)
            removed.append('défi en attente')
    for key in list(ranked_pending.keys()):
        v = ranked_pending[key]
        if uid in (v['p1'], v['p2']):
            ranked_pending.pop(key, None)
            removed.append('duel en attente de résultat')
    if not removed:
        return await ctx.send(f"ℹ️ {joueur.mention} n'a rien de bloqué en 1v1.")
    save_data()
    await ctx.send(f"✅ {joueur.mention} débloqué ({', '.join(removed)} effacé). Il peut relancer `!1v1`.")


# Libellés FR pour les permissions Discord détectées via @commands.has_permissions
# (voir _extract_permission_labels) — table de traduction, pas une liste de
# commandes : rien à retoucher ici quand une commande est ajoutée/retirée.
_ADMIN_REF_PERM_LABELS = {
    "administrator": "Administrateur",
    "manage_guild": "Gérer le serveur",
    "manage_roles": "Gérer les rôles",
    "manage_channels": "Gérer les salons",
    "manage_messages": "Gérer les messages",
    "manage_nicknames": "Gérer les pseudos",
    "kick_members": "Expulser des membres",
    "ban_members": "Bannir des membres",
    "mute_members": "Réduire au silence (vocal)",
    "moderate_members": "Timeout (mise en sourdine)",
}


def _extract_permission_labels(command) -> list[str] | None:
    """Lit la permission Discord exacte exigée par un @commands.has_permissions(...)
    directement dans la closure du check — vérifié empiriquement contre le
    vrai code de discord.py (co_freevars == ('perms',)), pas une supposition."""
    for chk in command.checks:
        try:
            if chk.__code__.co_freevars == ("perms",) and chk.__closure__:
                perms = chk.__closure__[0].cell_contents
                if isinstance(perms, dict):
                    return [_ADMIN_REF_PERM_LABELS.get(k, k) for k, v in perms.items() if v]
        except Exception:
            continue
    return None


def _admin_ref_label(cmd) -> str | None:
    """Retourne un libellé d'accès si la commande est restreinte d'une façon
    détectable dans le code (disabled_cmds, cmd_role_perms, ADMIN_LOCKED_CMDS,
    ALWAYS_ALLOWED_CMDS, ou un @commands.has_permissions) — None sinon, ce qui
    l'exclut de cette référence "admin". Les commandes protégées par une
    simple vérification manuelle en plein milieu de leur code (ex: !bs_famille,
    !bs_roles) ne peuvent pas être détectées ainsi et n'apparaîtront pas —
    limite connue, pas un bug."""
    name = cmd.name
    if name in disabled_cmds:
        return "🚫 Désactivée actuellement"
    if cmd_role_perms.get(name):
        return "🔒 Rôle(s) autorisé(s) via `!permission`"
    perm_labels = _extract_permission_labels(cmd)
    if perm_labels:
        return f"🔒 Permission Discord : {', '.join(perm_labels)}"
    if name in ADMIN_LOCKED_CMDS:
        return "🔒 Admin uniquement"
    if name in ALWAYS_ALLOWED_CMDS:
        return "🔒 Admin uniquement (outil de gestion du bot)"
    return None


def _chunk_lines(lines: list[str], limit: int = 1000) -> list[str]:
    chunks, current = [], ""
    for line in lines:
        piece = (line + "\n")
        if current and len(current) + len(piece) > limit:
            chunks.append(current)
            current = ""
        current += piece
    if current:
        chunks.append(current)
    return chunks or ["*(aucune)*"]


@bot.hybrid_command(name="commandes_admin", aliases=["modcommandes", "aide_admin", "admincommands"])
@discord.app_commands.default_permissions(administrator=True)
async def cmd_commandes_admin(ctx, *, mot_cle: str = None):
    """Référence des commandes admin/modération, générée depuis le vrai code
    (checks de permission, ADMIN_LOCKED_CMDS, cmd_role_perms, disabled_cmds)
    plutôt qu'une liste tapée à la main qui se périme — volontairement en
    préfixe only (pas de /), donc pas d'autocomplete Discord pour les
    retrouver : ce menu sert d'index. Demande du 26/07/2026."""
    if not (ctx.guild and ctx.author.guild_permissions.administrator) and not is_bot_owner(ctx.author):
        return await ctx.send("❌ Réservé aux administrateurs.")

    restricted = [(c, label) for c in bot.commands if (label := _admin_ref_label(c)) is not None]
    restricted.sort(key=lambda t: t[0].name)

    if mot_cle:
        needle = mot_cle.lower().strip()
        matches = [
            (c, label) for c, label in restricted
            if needle in " ".join([c.name, *c.aliases, COMMAND_USAGE.get(c.name, ""), c.help or ""]).lower()
        ]
        if not matches:
            return await ctx.send(f"❌ Aucune commande admin/modération ne correspond à `{mot_cle}`.")
        embed = discord.Embed(
            title=f"🔎 Commandes admin — « {mot_cle} »",
            description=f"{len(matches)} résultat(s).",
            color=0xe74c3c,
        )
        for c, label in matches[:10]:
            alias_str = f" (`!{'`, `!'.join(c.aliases)}`)" if c.aliases else ""
            usage = COMMAND_USAGE.get(c.name, f"`!{c.name} {c.signature}`".strip())
            embed.add_field(name=f"!{c.name}{alias_str}", value=f"{usage}\n{label}", inline=False)
        if len(matches) > 10:
            embed.set_footer(text=f"+{len(matches) - 10} autres résultats — affine ta recherche.")
        return await ctx.send(embed=embed)

    embed = discord.Embed(
        title="🛡️ Commandes de modération & admin",
        description=(
            f"**{len(restricted)}** commandes restreintes détectées automatiquement dans le code.\n"
            f"Utilise `!commandes_admin <mot-clé>` pour chercher une commande précise avec son détail."
        ),
        color=0xe74c3c,
    )
    lines = [f"`!{c.name}` — {label}" for c, label in restricted]
    for i, chunk in enumerate(_chunk_lines(lines)):
        embed.add_field(name="Commandes" if i == 0 else "Commandes (suite)", value=chunk, inline=False)
    embed.set_footer(
        text="Le créateur du bot passe toujours outre ces restrictions. "
        "!permission permet de déléguer une commande à un rôle non-admin."
    )
    await ctx.send(embed=embed)


# ═════════════════════════════════════════════════════════════════════════
# ── Fiches staff (!help_<rôle>) — mission + accès réel aux commandes ──────
# ═════════════════════════════════════════════════════════════════════════
# La partie "mission" est un brouillon rédigé à partir des indices déjà présents
# dans le code (catégories de tickets par rôle — voir TICKET_CATEGORY_STAFF_ROLE_IDS)
# et d'une hiérarchie clan-family classique — à corriger si besoin, ce n'est PAS
# une donnée officielle de la communauté. La partie "accès commandes" est calculée
# en direct depuis les vraies permissions du rôle Discord + cmd_role_perms (jamais
# codée en dur), donc toujours à jour même si !permission est utilisé plus tard.
STAFF_ROLE_INFO = {
    'fonda': {
        'role_id_var': 'ROLE_FONDA_ID',
        'title': "👑 Fonda",
        'mission': (
            "Fondateur·rice de Projet X — responsable en dernier ressort de la communauté. "
            "Tranche les décisions importantes, supervise le staff Discord (Admin, Modérateur) "
            "et le staff club (Président, Vice-président, Conseiller, Recruteur)."
        ),
    },
    'admin': {
        'role_id_var': 'ROLE_ADMIN_ID',
        'title': "⚙️ Admin",
        'mission': (
            "Bras droit du Fonda côté Discord : gestion technique du serveur (salons, rôles, "
            "configuration du bot), modération de dernier recours, traitement des candidatures, "
            "des incidents et des tickets « autre »."
        ),
    },
    'modo': {
        'role_id_var': 'ROLE_MODERATEUR_ID',
        'title': "🛡️ Modérateur (Staff Discord)",
        'mission': (
            "Modération au quotidien du serveur : fait respecter le règlement, traite les tickets "
            "de candidature, d'incident et « autre », intervient en cas de comportement problématique."
        ),
    },
    'recruteur': {
        'role_id_var': 'ROLE_RECRUTEUR_ID',
        'title': "🎯 Recruteur",
        'mission': (
            "En charge du recrutement dans les clans de la famille : traite les tickets de "
            "recrutement club et oriente les nouveaux joueurs vers le clan adapté à leur niveau. "
            "Outil dédié : `!recrutement <clan>` (self-service, sans argument affiche un menu "
            "cliquable pour choisir le clan) donne une fiche à jour à envoyer directement au "
            "prospect, avec le tag facilement copiable pour le rechercher en jeu."
        ),
    },
    'president': {
        'role_id_var': 'ROLE_PRESIDENT_ID',
        'title': "🏅 Président",
        'mission': (
            "Dirige un clan de la famille : gestion des membres en jeu, décisions internes au "
            "clan, relais entre son clan et le reste de la famille. Concerné par les tickets de "
            "recrutement club."
        ),
    },
    'vicepre': {
        'role_id_var': 'ROLE_VICE_PRESIDENT_ID',
        'title': "🥈 Vice-président",
        'mission': (
            "Second du Président au sein d'un clan : le supplée en son absence et l'assiste dans "
            "la gestion quotidienne du clan et le recrutement."
        ),
    },
    'conseiller': {
        'role_id_var': 'ROLE_CONSEILLER_ID',
        'title': "🧭 Conseiller",
        'mission': (
            "Conseille la direction du clan (Président/Vice-président), aide à la gestion des "
            "membres et participe au recrutement."
        ),
    },
}


def _staff_role_commands_section(role: discord.Role) -> str:
    """Calculé depuis les vraies permissions Discord du rôle + les délégations
    !permission — jamais une liste écrite à la main, pour ne jamais se périmer."""
    perms = role.permissions
    lines = []
    if perms.administrator:
        lines.append(
            "✅ **Administrateur Discord** — accès à toutes les commandes admin "
            "(`!gestion`, `!permission`, `!cd_set`, `!prix_casino`, `!set_logs`, `!bs_roles_panel`, "
            "`!bs_famille_panel`, `!annonce_site`, etc.) sauf les commandes réservées au "
            "propriétaire du serveur (voir plus bas)."
        )
    else:
        if perms.manage_messages:
            lines.append(
                "✅ Modération : `!warn` `!mute` `!unmute` `!clear` `!silence` `!unsilence` "
                "`!sanctions` `!historique_moderation` `!punition` `!annuler_punition`"
            )
        if perms.ban_members:
            lines.append("✅ `!ban` `!unban`")
        if perms.manage_nicknames:
            lines.append("✅ `!rename` — renommer un membre")
        if not (perms.manage_messages or perms.ban_members or perms.manage_nicknames):
            lines.append("➖ Aucune permission Discord spéciale — commandes générales du bot uniquement (voir `!aide`).")

    delegated = sorted(cmd for cmd, roles in cmd_role_perms.items() if role.id in (roles or []))
    if delegated:
        lines.append("🔓 Commandes sensibles déléguées via `!permission` : " + ", ".join(f"`!{c}`" for c in delegated))
    elif not perms.administrator:
        locked_sample = ", ".join(f"`!{c}`" for c in sorted(ADMIN_LOCKED_CMDS)[:6])
        lines.append(f"🔒 Pas d'accès aux commandes sensibles (ex. {locked_sample}, …) sauf délégation via `!permission`.")

    return "\n".join(lines)


def _staff_role_ticket_categories(role_id: int) -> list[str]:
    return [
        TICKET_CATEGORIES.get(cat, cat)
        for cat, role_ids in TICKET_CATEGORY_STAFF_ROLE_IDS.items()
        if role_id in role_ids
    ]


def _staff_help_embed(guild: discord.Guild, key: str) -> discord.Embed | None:
    info = STAFF_ROLE_INFO.get(key)
    if not info:
        return None
    role_id = globals().get(info['role_id_var'])
    role = guild.get_role(role_id) if role_id else None
    if not role:
        return None

    embed = discord.Embed(
        title=f"{info['title']} — Fiche staff",
        color=role.color.value or 0x95a5a6,
    )
    embed.add_field(name="🎯 Mission", value=info['mission'], inline=False)
    embed.add_field(name="⚙️ Accès commandes", value=_staff_role_commands_section(role), inline=False)

    cats = _staff_role_ticket_categories(role.id)
    if cats:
        embed.add_field(name="🎫 Tickets concernés", value=", ".join(cats), inline=False)

    embed.set_footer(text=f"Rôle Discord : {role.name} · Tape !aide pour les commandes générales")
    return embed


async def _send_staff_help(ctx, key: str):
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")
    embed = _staff_help_embed(ctx.guild, key)
    if not embed:
        return await ctx.send("❌ Ce rôle n'existe pas (ou plus) sur ce serveur.")
    await ctx.send(embed=embed)


@bot.command(name="help_fonda")
async def cmd_help_fonda(ctx):
    await _send_staff_help(ctx, 'fonda')


@bot.command(name="help_admin_role", aliases=["help_admin"])
async def cmd_help_admin_role(ctx):
    await _send_staff_help(ctx, 'admin')


@bot.command(name="help_modo", aliases=["help_moderateur"])
async def cmd_help_modo(ctx):
    await _send_staff_help(ctx, 'modo')


@bot.command(name="help_recruteur")
async def cmd_help_recruteur(ctx):
    await _send_staff_help(ctx, 'recruteur')


@bot.command(name="help_president", aliases=["help_pre"])
async def cmd_help_president(ctx):
    await _send_staff_help(ctx, 'president')


@bot.command(name="help_vicepre", aliases=["help_viceprsident", "help_vp"])
async def cmd_help_vicepre(ctx):
    await _send_staff_help(ctx, 'vicepre')


@bot.command(name="help_conseiller", aliases=["help_conseil"])
async def cmd_help_conseiller(ctx):
    await _send_staff_help(ctx, 'conseiller')


@bot.command(name="help_staff")
async def cmd_help_staff(ctx):
    """Sans argument : détecte automatiquement le(s) rôle(s) staff du membre et
    affiche sa/ses propre(s) fiche(s) — pratique pour ne pas avoir à retenir
    le bon !help_<rôle>."""
    if not ctx.guild:
        return await ctx.send("❌ Cette commande doit être utilisée dans un serveur.")
    author_role_ids = {r.id for r in ctx.author.roles}
    matches = [
        key for key, info in STAFF_ROLE_INFO.items()
        if globals().get(info['role_id_var']) in author_role_ids
    ]
    if not matches:
        return await ctx.send(
            "ℹ️ Tu n'as aucun rôle staff reconnu. Fiches disponibles : "
            + ", ".join(f"`!help_{k}`" for k in STAFF_ROLE_INFO)
        )
    for key in matches:
        embed = _staff_help_embed(ctx.guild, key)
        if embed:
            await ctx.send(embed=embed)


validate_command_names(bot, economy_router.command_names)

token = os.getenv("TOKEN")
if token is not None:
    keep_alive()
    bot.run(token)
else:
    print("Erreur : Le token Discord n'est pas défini dans les variables d'environnement. Veuillez le configurer.")
