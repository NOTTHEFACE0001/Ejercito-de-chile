"""
=============================================================================
  🇨🇱 REPCL • EJÉRCITO DE CHILE | BOT OFICIAL v2
  Verificación Roblox ↔ Discord (anti-suplantación), Ascensos/Descensos,
  Sanciones, Blacklist, Auditoría persistente y Tickets.

  Requisitos:  pip install -U discord.py aiosqlite aiohttp python-dotenv
  Variables:   DISCORD_TOKEN (o DISCORD_BOT_TOKEN), ROBLOX_COOKIE,
               ROBLOX_GROUP_ID (opcional, valor inicial), GUILD_ID (opcional),
               DATA_DIR (carpeta del volumen persistente, ej. /data),
               SYNC_INTERVAL_MIN (opcional, por defecto 15)
  Intents privilegiados a activar en el portal de Discord:
               SERVER MEMBERS INTENT + MESSAGE CONTENT INTENT
=============================================================================
"""

import os
import re
import io
import sys
import json
import uuid
import asyncio
import secrets
import datetime
import threading
import traceback
import unicodedata
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any, List, Tuple

import aiohttp
import aiosqlite
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ---------------------------------------------------------------------------
# 1. CONFIGURACIÓN BASE
# ---------------------------------------------------------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
ROBLOX_COOKIE = os.getenv("ROBLOX_COOKIE", "")
GUILD_ID = os.getenv("GUILD_ID")
DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "repcl_bot.db")
SYNC_INTERVAL_MIN = int(os.getenv("SYNC_INTERVAL_MIN", "15") or 15)

_env_gid = (os.getenv("ROBLOX_GROUP_ID") or "").strip()
DEFAULT_GROUP_ID = int(_env_gid) if _env_gid.isdigit() else 13698921

RED, BLUE, GREEN, ORANGE, YELLOW, DARK, CYAN = 0xD52B1E, 0x0039A6, 0x2ECC71, 0xE67E22, 0xF1C40F, 0x2C3E50, 0x3498DB
VERIFY_TTL_MIN = 15

SUSPICIOUS_KEYWORDS = [
    "raid", "raideo", "nuke", "dox", "doxxeo", "iplogger", "grabify", "leaks",
    "filtracion", "token grabber", "nitro gratis", "free robux", "exploit",
]
KEYWORD_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in SUSPICIOUS_KEYWORDS) + r")\b", re.I)
URL_RE = re.compile(r"https?://\S+")

SANCTION_TYPES = {
    "warn": "⚠️ Advertencia",
    "mute": "🔇 Silenciar",
    "suspend": "⏸️ Suspensión temporal",
    "bl_temp": "🚫 Blacklist temporal",
    "bl_perm": "⛔ Blacklist permanente",
    "kick": "🚷 Expulsión",
    "ban": "🔨 Ban",
}
DEFAULT_DURATION = {"mute": "1h", "suspend": "7d", "bl_temp": "7d"}


# ---------------------------------------------------------------------------
# 2. UTILIDADES
# ---------------------------------------------------------------------------
def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)

def now_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%S")

def parse_iso(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)

def dts(s: Optional[str], style: str = "f") -> str:
    """Convierte ISO UTC a marca de tiempo nativa de Discord."""
    try:
        return f"<t:{int(parse_iso(s).timestamp())}:{style}>"
    except Exception:
        return str(s or "—")

def trunc(s: Any, n: int) -> str:
    s = str(s if s is not None else "")
    return s if len(s) <= n else s[: n - 1] + "…"

_DUR_RE = re.compile(r"(\d+)\s*([smhdw])", re.I)

def parse_duration(text: Optional[str]) -> Optional[datetime.timedelta]:
    """'30m', '12h', '7d', '1w2d' -> timedelta. 'permanente' -> None. Inválido -> ValueError."""
    if not text:
        return None
    t = text.strip().lower()
    if t in ("permanente", "perm", "indefinida", "indefinido", "n/a", "para siempre"):
        return None
    found = _DUR_RE.findall(t)
    if not found or _DUR_RE.sub("", t).strip():
        raise ValueError("duración inválida")
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    secs = sum(int(n) * mult[u.lower()] for n, u in found)
    if secs <= 0:
        raise ValueError("duración inválida")
    return datetime.timedelta(seconds=secs)

def normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ASCII", "ignore").decode("utf-8").lower()
    s = re.sub(r"\[.*?\]|\(.*?\)", "", s)
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return " ".join(s.split())


# ---------------------------------------------------------------------------
# 3. BASE DE DATOS (una sola conexión SQLite persistente)
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS linked_users (
    discord_id INTEGER PRIMARY KEY,
    roblox_id INTEGER NOT NULL,
    roblox_username TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    current_rank_id INTEGER DEFAULT 0,
    current_rank_name TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS pending_verifications (
    discord_id INTEGER PRIMARY KEY,
    roblox_id INTEGER NOT NULL,
    roblox_username TEXT NOT NULL,
    code TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rank_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id INTEGER NOT NULL,
    roblox_id INTEGER NOT NULL,
    old_rank TEXT NOT NULL,
    new_rank TEXT NOT NULL,
    admin_id INTEGER NOT NULL,
    admin_name TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    action_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sanctions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    user_tag TEXT NOT NULL,
    admin_id INTEGER NOT NULL,
    admin_tag TEXT NOT NULL,
    sanction_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    duration TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY,
    roblox_id INTEGER DEFAULT 0,
    reason TEXT NOT NULL,
    admin_id INTEGER NOT NULL,
    admin_tag TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS custom_role_mappings (
    roblox_rank_id INTEGER PRIMARY KEY,
    roblox_rank_name TEXT NOT NULL,
    discord_role_id INTEGER NOT NULL,
    discord_role_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message_log (
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER, channel_id INTEGER, channel_name TEXT,
    author_id INTEGER, author_tag TEXT,
    content TEXT, attachments TEXT, links TEXT,
    created_at TEXT, deleted INTEGER DEFAULT 0, deleted_at TEXT, edited INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_msg_author ON message_log(author_id);
CREATE INDEX IF NOT EXISTS idx_msg_channel ON message_log(channel_id);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER, kind TEXT, action TEXT,
    user_id INTEGER, user_tag TEXT,
    channel_id INTEGER, channel_name TEXT,
    content TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(user_id);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER, user_id INTEGER, user_tag TEXT,
    status TEXT DEFAULT 'open', created_at TEXT, closed_at TEXT, closed_by TEXT
);
"""

_db: Optional[aiosqlite.Connection] = None

async def db_init():
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.executescript(SCHEMA)
    await _db.commit()

async def q_exec(sql: str, params: tuple = ()):
    cur = await _db.execute(sql, params)
    await _db.commit()
    return cur

async def q_one(sql: str, params: tuple = ()):
    async with _db.execute(sql, params) as cur:
        return await cur.fetchone()

async def q_all(sql: str, params: tuple = ()):
    async with _db.execute(sql, params) as cur:
        return await cur.fetchall()

# --- Ajustes persistentes ---------------------------------------------------
async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    row = await q_one("SELECT value FROM bot_settings WHERE key=?", (key,))
    return row["value"] if row else default

async def set_setting(key: str, value: Any):
    await q_exec(
        "INSERT INTO bot_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )

async def get_int(key: str, default: int = 0) -> int:
    try:
        return int(await get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default

async def get_group_id() -> int:
    return await get_int("roblox_group_id", 0) or DEFAULT_GROUP_ID

async def get_admin_roles() -> List[int]:
    try:
        return [int(x) for x in json.loads(await get_setting("admin_role_ids", "[]"))]
    except Exception:
        return []

# --- Cuentas vinculadas -----------------------------------------------------
async def get_linked(discord_id: int):
    return await q_one("SELECT * FROM linked_users WHERE discord_id=?", (discord_id,))

async def save_linked(discord_id: int, roblox_id: int, username: str, rank_id: int, rank_name: str):
    await q_exec(
        """INSERT INTO linked_users(discord_id,roblox_id,roblox_username,linked_at,current_rank_id,current_rank_name)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(discord_id) DO UPDATE SET roblox_id=excluded.roblox_id,
             roblox_username=excluded.roblox_username, linked_at=excluded.linked_at,
             current_rank_id=excluded.current_rank_id, current_rank_name=excluded.current_rank_name""",
        (discord_id, roblox_id, username, now_iso(), rank_id, rank_name),
    )

async def log_rank_change(discord_id, roblox_id, old_rank, new_rank, admin_id, admin_name, action_type):
    await q_exec(
        """INSERT INTO rank_history(discord_id,roblox_id,old_rank,new_rank,admin_id,admin_name,timestamp,action_type)
           VALUES(?,?,?,?,?,?,?,?)""",
        (discord_id, roblox_id, old_rank, new_rank, admin_id, admin_name, now_iso(), action_type),
    )

# --- Mapeos de rango personalizados -----------------------------------------
async def get_custom_mappings() -> Dict[int, int]:
    rows = await q_all("SELECT roblox_rank_id, discord_role_id FROM custom_role_mappings")
    return {r["roblox_rank_id"]: r["discord_role_id"] for r in rows}

# --- Sanciones / blacklist --------------------------------------------------
async def add_sanction(user_id, user_tag, admin_id, admin_tag, s_type, reason, duration, expires_at) -> str:
    s_id = f"REPCL-{uuid.uuid4().hex[:6].upper()}"
    await q_exec(
        """INSERT INTO sanctions(id,user_id,user_tag,admin_id,admin_tag,sanction_type,reason,duration,created_at,expires_at,active)
           VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
        (s_id, user_id, user_tag, admin_id, admin_tag, s_type, reason, duration, now_iso(), expires_at),
    )
    return s_id

async def get_blacklist(user_id: int) -> Optional[dict]:
    row = await q_one("SELECT * FROM blacklist WHERE user_id=?", (user_id,))
    if not row:
        return None
    d = dict(row)
    if d.get("expires_at") and d["expires_at"] <= now_iso():
        return None
    return d

async def set_blacklist(user_id, roblox_id, reason, admin_id, admin_tag, expires_at=None):
    await q_exec(
        """INSERT OR REPLACE INTO blacklist(user_id,roblox_id,reason,admin_id,admin_tag,created_at,expires_at)
           VALUES(?,?,?,?,?,?,?)""",
        (user_id, roblox_id, reason, admin_id, admin_tag, now_iso(), expires_at),
    )

async def remove_blacklist(user_id: int) -> bool:
    cur = await q_exec("DELETE FROM blacklist WHERE user_id=?", (user_id,))
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# 4. CLIENTE ASÍNCRONO DE ROBLOX (con timeout y sin tragarse errores)
# ---------------------------------------------------------------------------
class RobloxClient:
    def __init__(self, cookie: str):
        self.cookie = cookie
        self.csrf: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._roles_cache: Dict[int, Tuple[float, List[dict]]] = {}

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "REPCL-DiscordBot/2.0"},
                timeout=aiohttp.ClientTimeout(total=8),
            )
        return self._session

    async def _req(self, method: str, url: str, **kw) -> Tuple[int, Any, Dict[str, str]]:
        """Devuelve (status, json, headers). status=0 si hubo error de red/timeout."""
        try:
            s = await self.session()
            async with s.request(method, url, **kw) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                return resp.status, data, {k.lower(): v for k, v in resp.headers.items()}
        except Exception as e:
            print(f"[!] Roblox {method} {url} falló: {type(e).__name__}: {e}")
            return 0, None, {}

    async def fetch_csrf(self) -> Optional[str]:
        _, _, h = await self._req("POST", "https://auth.roblox.com/v2/logout",
                                  headers={"Cookie": f".ROBLOSECURITY={self.cookie}"})
        self.csrf = h.get("x-csrf-token") or self.csrf
        return self.csrf

    async def get_user_by_username(self, username: str) -> Optional[dict]:
        st, data, _ = await self._req("POST", "https://users.roblox.com/v1/usernames/users",
                                      json={"usernames": [username], "excludeBannedUsers": False})
        if st == 200 and data and data.get("data"):
            return data["data"][0]
        return None

    async def get_user(self, user_id: int) -> Optional[dict]:
        st, data, _ = await self._req("GET", f"https://users.roblox.com/v1/users/{user_id}")
        return data if st == 200 else None

    async def get_thumbnail(self, user_id: int) -> str:
        st, data, _ = await self._req(
            "GET", f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png&isCircular=false")
        if st == 200 and data and data.get("data"):
            return data["data"][0].get("imageUrl") or ""
        return "https://www.roblox.com/images/default-avatar.png"

    async def get_user_group_role(self, user_id: int, group_id: int) -> Optional[dict]:
        """None = la API falló (NO asumir que no está en el grupo)."""
        st, data, _ = await self._req("GET", f"https://groups.roblox.com/v1/users/{user_id}/groups/roles")
        if st != 200 or data is None:
            return None
        for entry in data.get("data", []):
            if int(entry.get("group", {}).get("id", 0)) == int(group_id):
                return {
                    "in_group": True,
                    "group_name": entry["group"]["name"],
                    "rank": int(entry["role"]["rank"]),
                    "role_id": entry["role"]["id"],
                    "role_name": entry["role"]["name"],
                }
        return {"in_group": False, "rank": 0, "role_id": None, "role_name": None}

    async def get_group_roles(self, group_id: int, force: bool = False) -> List[dict]:
        import time
        cached = self._roles_cache.get(group_id)
        if cached and not force and time.time() - cached[0] < 300:
            return cached[1]
        st, data, _ = await self._req("GET", f"https://groups.roblox.com/v1/groups/{group_id}/roles")
        if st == 200 and data:
            roles = data.get("roles", [])
            self._roles_cache[group_id] = (time.time(), roles)
            return roles
        return cached[1] if cached else []

    async def get_group_info(self, group_id: int) -> Optional[dict]:
        st, data, _ = await self._req("GET", f"https://groups.roblox.com/v1/groups/{group_id}")
        return data if st == 200 else None

    async def set_user_rank(self, group_id: int, user_id: int, role_id: int) -> dict:
        if not self.cookie:
            return {"success": False, "error": "No se configuró ROBLOX_COOKIE en las variables de entorno."}
        url = f"https://groups.roblox.com/v1/groups/{group_id}/users/{user_id}"
        for _ in range(3):
            headers = {"Cookie": f".ROBLOSECURITY={self.cookie}", "Content-Type": "application/json"}
            if self.csrf:
                headers["x-csrf-token"] = self.csrf
            st, data, h = await self._req("PATCH", url, json={"roleId": role_id}, headers=headers)
            if st == 200:
                return {"success": True}
            if st == 403 and h.get("x-csrf-token") and h["x-csrf-token"] != self.csrf:
                self.csrf = h["x-csrf-token"]
                continue
            if st == 403 and not self.csrf and await self.fetch_csrf():
                continue
            try:
                msg = data["errors"][0]["message"]
            except Exception:
                msg = "Timeout/sin respuesta de Roblox" if st == 0 else f"HTTP {st}"
            return {"success": False, "error": msg}
        return {"success": False, "error": "No se pudo renovar el token CSRF de Roblox."}

roblox = RobloxClient(ROBLOX_COOKIE)


# ---------------------------------------------------------------------------
# 5. BOT, ÁRBOL DE COMANDOS Y PERMISOS
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class RepclTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Los usuarios en Blacklist no pueden usar ningún comando del bot."""
        if interaction.guild and interaction.user.id != interaction.guild.owner_id:
            bl = await get_blacklist(interaction.user.id)
            if bl:
                await interaction.response.send_message(
                    f"⛔ Estás en la **Blacklist de REPCL** y no puedes usar este comando.\n**Motivo:** {bl['reason']}",
                    ephemeral=True)
                return False
        return True

class RepclBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None, tree_cls=RepclTree)

    async def setup_hook(self):
        await db_init()
        self.add_view(VerifyPersistentView())
        self.add_view(ConfirmIdentityView())
        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())
        auto_sync_loop.start()
        expiry_loop.start()
        try:
            if GUILD_ID and GUILD_ID.isdigit():
                g = discord.Object(id=int(GUILD_ID))
                self.tree.copy_global_to(guild=g)
                synced = await self.tree.sync(guild=g)
                print(f"[+] {len(synced)} comandos sincronizados en el servidor {GUILD_ID}")
            else:
                synced = await self.tree.sync()
                print(f"[+] {len(synced)} comandos sincronizados globalmente")
        except Exception as e:
            print(f"[!] Error sincronizando comandos: {e}")

bot = RepclBot()

def slash(name: str, description: str):
    def deco(fn):
        fn = app_commands.guild_only()(fn)
        return bot.tree.command(name=name, description=description)(fn)
    return deco

async def is_repcl_admin(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or member.id == member.guild.owner_id:
        return True
    admin_roles = await get_admin