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
    admin_roles = await get_admin_roles()
    return any(r.id in admin_roles for r in member.roles)

async def admin_guard(interaction: discord.Interaction) -> bool:
    if await is_repcl_admin(interaction.user):
        return True
    msg = "❌ No tienes permisos para usar este comando."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)
    return False

def can_target(actor: discord.Member, target: discord.Member) -> Tuple[bool, str]:
    if target.id == actor.id:
        return False, "No puedes aplicarte esto a ti mismo."
    if target.bot:
        return False, "No puedes aplicar esto a un bot."
    if target.id == target.guild.owner_id:
        return False, "No puedes aplicar esto al dueño del servidor."
    if actor.id == actor.guild.owner_id:
        return True, ""
    if target.top_role >= actor.top_role:
        return False, "El usuario tiene un rol igual o superior al tuyo."
    return True, ""

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Evita que una excepción deje el comando en 'está pensando...' para siempre."""
    traceback.print_exception(type(error), error, error.__traceback__)
    msg = f"⚠️ Ocurrió un error inesperado: `{trunc(getattr(error, 'original', error), 200)}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 6. AUDITORÍA (DB persistente + embed al canal privado)
# ---------------------------------------------------------------------------
async def audit(guild: Optional[discord.Guild], title: str, action: str, *, color: int = CYAN,
                user=None, user_id: Optional[int] = None, user_tag: Optional[str] = None,
                channel=None, channel_id: Optional[int] = None, channel_name: Optional[str] = None,
                content: Optional[str] = None, fields: Optional[List[tuple]] = None,
                kind: str = "general", files: Optional[List[discord.File]] = None):
    if user is not None:
        user_id, user_tag = user.id, str(user)
    if channel is not None:
        channel_id, channel_name = channel.id, getattr(channel, "name", None)
    ts = now_iso()
    try:
        await q_exec(
            """INSERT INTO audit_events(guild_id,kind,action,user_id,user_tag,channel_id,channel_name,content,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (guild.id if guild else 0, kind, action, user_id, user_tag, channel_id, channel_name, content, ts))
    except Exception as e:
        print(f"[!] No se pudo guardar auditoría: {e}")
    if guild is None:
        return
    ch_id = await get_int("messages_channel_id") if kind == "messages" else 0
    ch_id = ch_id or await get_int("audit_channel_id")
    ch = guild.get_channel(ch_id) if ch_id else None
    if not isinstance(ch, discord.TextChannel):
        return
    embed = discord.Embed(title=title, color=color, timestamp=utcnow())
    if user is not None:
        embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
    elif user_id:
        embed.set_author(name=f"{user_tag or 'Desconocido'} ({user_id})")
    if content:
        embed.description = trunc(content, 4000)
    embed.add_field(name="👤 Usuario", value=f"<@{user_id}> (`{user_id}`)" if user_id else "—", inline=True)
    embed.add_field(name="📍 Canal", value=f"<#{channel_id}>" if channel_id else "—", inline=True)
    embed.add_field(name="⚡ Acción", value=action, inline=True)
    for name, value, inline in (fields or []):
        embed.add_field(name=name, value=trunc(value, 1024) or "—", inline=inline)
    embed.add_field(name="🕒 Fecha y hora", value=dts(ts, "F"), inline=False)
    try:
        await ch.send(embed=embed, files=files or [], allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        print(f"[!] No se pudo enviar al canal de auditoría: {e}")

async def log_admin(interaction: discord.Interaction, action: str, detail: str = ""):
    await audit(interaction.guild, "🛠️ AUDITORÍA • ACCIÓN ADMINISTRATIVA", action, color=DARK,
                user=interaction.user, channel=interaction.channel, content=detail or None)

async def find_executor(guild: discord.Guild, action: discord.AuditLogAction, target_id: int):
    try:
        async for e in guild.audit_logs(limit=6, action=action):
            if e.target and e.target.id == target_id and (utcnow() - e.created_at).total_seconds() < 20:
                return e.user, e.reason
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------------------
# 7. ROLES DE DISCORD ↔ RANGOS DE ROBLOX
# ---------------------------------------------------------------------------
def find_group_role(roles: List[dict], text: str) -> Optional[dict]:
    """Busca un rol del grupo por nivel, nombre exacto, normalizado o parcial único."""
    text = (text or "").strip()
    norm = normalize(text)
    for r in roles:
        if str(r.get("rank")) == text:
            return r
    for r in roles:
        if r.get("name", "").lower() == text.lower():
            return r
    if norm:
        for r in roles:
            if normalize(r.get("name", "")) == norm:
                return r
        partial = [r for r in roles if norm in normalize(r.get("name", "")) or normalize(r.get("name", "")) in norm]
        partial = [r for r in partial if normalize(r.get("name", ""))]
        if len(partial) == 1:
            return partial[0]
    return None

def match_discord_role(guild: discord.Guild, roblox_name: str, rank: int, custom: Dict[int, int]) -> Optional[discord.Role]:
    if rank in custom:
        r = guild.get_role(custom[rank])
        if r:
            return r
    target = normalize(roblox_name)
    if not target:
        return None
    pool = [r for r in guild.roles if not r.is_default() and not r.managed]
    for r in pool:
        if normalize(r.name) == target:
            return r
    cands = [r for r in pool if normalize(r.name) and (target in normalize(r.name) or normalize(r.name) in target)]
    return cands[0] if len(cands) == 1 else None   # ambiguo -> mejor no adivinar

def find_citizen_role(guild: discord.Guild, configured_id: int) -> Optional[discord.Role]:
    if configured_id:
        r = guild.get_role(configured_id)
        if r:
            return r
    for r in guild.roles:
        if not r.is_default() and not r.managed and any(k in normalize(r.name) for k in ("ciudadano", "civil")):
            return r
    return None

async def safe_roles(member: discord.Member, add: List[discord.Role], remove: List[discord.Role], reason: str) -> bool:
    ok = True
    try:
        if remove:
            await member.remove_roles(*remove, reason=reason)
        if add:
            await member.add_roles(*add, reason=reason)
    except discord.Forbidden:
        ok = False
        print(f"[!] 403 al editar roles de {member}: sube el rol del bot por encima de los roles militares.")
    except Exception as e:
        ok = False
        print(f"[!] Error editando roles de {member}: {e}")
    return ok

async def set_nick(member: discord.Member, nick: str):
    nick = trunc(nick, 32)
    if member.id == member.guild.owner_id or member.nick == nick:
        return
    try:
        await member.edit(nick=nick)
    except Exception:
        pass

async def managed_role_ids(guild: discord.Guild) -> set:
    custom = await get_custom_mappings()
    ids = set(custom.values())
    for r in await roblox.get_group_roles(await get_group_id()):
        if r["rank"] > 0:
            dr = match_discord_role(guild, r["name"], r["rank"], custom)
            if dr:
                ids.add(dr.id)
    return ids

async def apply_roles(member: discord.Member, roblox_name: str, group_role: dict) -> Tuple[str, str]:
    """Devuelve (tipo, etiqueta): tipo ∈ blacklist | citizen | military."""
    guild = member.guild
    custom = await get_custom_mappings()
    managed = await managed_role_ids(guild)
    citizen = find_citizen_role(guild, await get_int("citizen_role_id"))
    bl_role = guild.get_role(await get_int("blacklist_role_id")) if await get_int("blacklist_role_id") else None

    # --- Blacklist: se quitan roles gestionados y se pone el rol de blacklist
    if await get_blacklist(member.id):
        rem = [r for r in member.roles if r.id in managed or (citizen and r.id == citizen.id)]
        add = [bl_role] if bl_role and bl_role not in member.roles else []
        await safe_roles(member, add, rem, "Usuario en Blacklist REPCL")
        return "blacklist", "Blacklist REPCL"

    # Si ya no está en blacklist pero conserva el rol, se retira
    extra_rem = [bl_role] if bl_role and bl_role in member.roles else []

    if not group_role.get("in_group"):
        add = [citizen] if citizen and citizen not in member.roles else []
        rem = [r for r in member.roles if r.id in managed] + extra_rem
        await safe_roles(member, add, rem, "Verificado: no pertenece al grupo")
        await set_nick(member, f"[CIV] {roblox_name}")
        return "citizen", citizen.name if citizen else "Ciudadano Chileno"

    target = match_discord_role(guild, group_role["role_name"], group_role["rank"], custom)
    rem = [r for r in member.roles if (r.id in managed and (not target or r.id != target.id))] + extra_rem
    if citizen and citizen in member.roles:
        rem.append(citizen)
    add = [target] if target and target not in member.roles else []
    await safe_roles(member, add, rem, f"Rango Roblox: {group_role['role_name']}")
    label = target.name if target else group_role["role_name"]
    await set_nick(member, f"[{label}] {roblox_name}")
    return "military", label

async def finalize_link(member: discord.Member, roblox_id: int, roblox_name: str) -> Optional[Tuple[dict, str, str]]:
    info = await roblox.get_user_group_role(roblox_id, await get_group_id())
    if info is None:
        return None
    kind, label = await apply_roles(member, roblox_name, info)
    await save_linked(member.id, roblox_id, roblox_name, info["rank"], info["role_name"] or "Ciudadano")
    return info, kind, label

async def sync_member(member: discord.Member, refresh_name: bool = False) -> Optional[dict]:
    """Sincroniza los roles de un miembro con su rango actual de Roblox."""
    linked = await get_linked(member.id)
    if not linked:
        return None
    info = await roblox.get_user_group_role(linked["roblox_id"], await get_group_id())
    if info is None:
        return {"error": "api"}
    name = linked["roblox_username"]
    if refresh_name:
        u = await roblox.get_user(linked["roblox_id"])
        if u and u.get("name"):
            name = u["name"]
    kind, label = await apply_roles(member, name, info)
    new_name = info["role_name"] or "Ciudadano"
    changed = int(info["rank"]) != int(linked["current_rank_id"] or 0)
    await q_exec("UPDATE linked_users SET roblox_username=?, current_rank_id=?, current_rank_name=? WHERE discord_id=?",
                 (name, info["rank"], new_name, member.id))
    if changed and kind != "blacklist":
        await log_rank_change(member.id, linked["roblox_id"], linked["current_rank_name"] or "Ciudadano",
                              new_name, 0, "Sistema (cambio detectado en Roblox)", "SINCRONIZACIÓN")
        await audit(member.guild, "🔄 AUDITORÍA • RANGO ACTUALIZADO DESDE ROBLOX", "Sincronización automática",
                    color=GREEN, user=member,
                    fields=[("Rango anterior", linked["current_rank_name"] or "Ciudadano", True),
                            ("Rango nuevo", new_name, True), ("Rol de Discord", label, True)])
    return {"kind": kind, "label": label, "changed": changed, "rank_name": new_name}

async def run_full_sync(guild: discord.Guild) -> Tuple[int, int, int]:
    rows = await q_all("SELECT discord_id FROM linked_users")
    total = changed = errors = 0
    for r in rows:
        m = guild.get_member(r["discord_id"])
        if not m or m.bot:
            continue
        total += 1
        try:
            res = await sync_member(m)
            if res and res.get("error"):
                errors += 1
            elif res and res.get("changed"):
                changed += 1
        except Exception as e:
            errors += 1
            print(f"[!] Sync de {m}: {e}")
        await asyncio.sleep(1.2)   # respeta límites de la API de Roblox
    return total, changed, errors


# ---------------------------------------------------------------------------
# 8. TAREAS EN SEGUNDO PLANO
# ---------------------------------------------------------------------------
@tasks.loop(minutes=SYNC_INTERVAL_MIN)
async def auto_sync_loop():
    for guild in bot.guilds:
        total, changed, errors = await run_full_sync(guild)
        print(f"[sync] {guild.name}: {total} revisados, {changed} cambios, {errors} errores")

@auto_sync_loop.before_loop
async def _before_sync():
    await bot.wait_until_ready()

@tasks.loop(seconds=60)
async def expiry_loop():
    now = now_iso()
    rows = await q_all("SELECT * FROM sanctions WHERE active=1 AND expires_at IS NOT NULL AND expires_at<=?", (now,))
    for s in rows:
        await q_exec("UPDATE sanctions SET active=0 WHERE id=?", (s["id"],))
        for guild in bot.guilds:
            member = guild.get_member(s["user_id"])
            if s["sanction_type"] == "suspend" and member:
                rid = await get_int("suspension_role_id")
                role = guild.get_role(rid) if rid else None
                if role and role in member.roles:
                    await safe_roles(member, [], [role], "Fin de suspensión temporal")
            elif s["sanction_type"] == "bl_temp":
                await q_exec("DELETE FROM blacklist WHERE user_id=? AND expires_at IS NOT NULL AND expires_at<=?",
                             (s["user_id"], now))
                if member and not await get_blacklist(member.id):
                    if await get_linked(member.id):
                        await sync_member(member)
                    else:
                        rid = await get_int("blacklist_role_id")
                        role = guild.get_role(rid) if rid else None
                        if role and role in member.roles:
                            await safe_roles(member, [], [role], "Fin de blacklist temporal")
            await audit(guild, "⏱️ AUDITORÍA • SANCIÓN EXPIRADA", f"Expiró {SANCTION_TYPES.get(s['sanction_type'], s['sanction_type'])}",
                        color=GREEN, user_id=s["user_id"], user_tag=s["user_tag"],
                        fields=[("ID sanción", s["id"], True)])

@expiry_loop.before_loop
async def _before_expiry():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# 9. VERIFICACIÓN CON DOBLE FACTOR (código en la biografía de Roblox)
# ---------------------------------------------------------------------------
def verified_embed(member, roblox_id, name, info, kind, label, avatar) -> discord.Embed:
    e = discord.Embed(title="🇨🇱 ¡CUENTA VINCULADA Y VERIFICADA CON ÉXITO!",
                      description="Tu cuenta de Discord quedó enlazada a tu perfil de **Roblox** en **REPCL • Ejército de Chile**.",
                      color=BLUE if kind == "citizen" else RED, timestamp=utcnow())
    e.set_thumbnail(url=avatar)
    e.add_field(name="👤 Usuario de Roblox", value=f"**[{name}](https://www.roblox.com/users/{roblox_id}/profile)**", inline=True)
    e.add_field(name="🆔 ID de Roblox", value=f"`{roblox_id}`", inline=True)
    if kind == "citizen":
        e.add_field(name="🛡️ Estado", value="❌ **No perteneces al grupo de Roblox**\nSe te asignó el rol de **Ciudadano Chileno**.", inline=False)
        e.add_field(name="📌 ¿Quieres alistarte?", value="Únete al grupo y pulsa **🔄 Actualizar Rango** para recibir tu rango militar.", inline=False)
    else:
        e.add_field(name="🎖️ Rango detectado", value=f"✅ **{info['role_name']}** (Nivel `{info['rank']}`)", inline=True)
        e.add_field(name="🏷️ Rol de Discord", value=f"**@{label}**", inline=True)
    e.set_footer(text="REPCL • Sistema de Verificación Oficial")
    return e

class VerificationModal(discord.ui.Modal, title="🇨🇱 Verificación Roblox • Ejército de Chile"):
    roblox_username = discord.ui.TextInput(label="Tu usuario exacto de Roblox", placeholder="Ej: JuanPerezCL",
                                           min_length=3, max_length=30, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        username = self.roblox_username.value.strip()

        bl = await get_blacklist(interaction.user.id)
        if bl:
            await interaction.followup.send(embed=discord.Embed(
                title="⛔ ACCESO DENEGADO • BLACKLIST",
                description=f"**Motivo:** {bl['reason']}\n**Fecha:** {dts(bl['created_at'])}\n**Administrador:** `{bl['admin_tag']}`",
                color=RED), ephemeral=True)
            return

        data = await roblox.get_user_by_username(username)
        if not data:
            await interaction.followup.send(f"❌ No se encontró el usuario **`{username}`** en Roblox (o Roblox no respondió). Revisa el nombre.", ephemeral=True)
            return
        roblox_id, exact = data["id"], data["name"]

        other = await q_one("SELECT discord_id FROM linked_users WHERE roblox_id=? AND discord_id!=?", (roblox_id, interaction.user.id))
        if other:
            await interaction.followup.send("⛔ Esa cuenta de Roblox ya está vinculada a otro usuario de Discord. Si es tuya, pide a un administrador que la desvincule.", ephemeral=True)
            return

        code = f"REPCL-{secrets.token_hex(3).upper()}"
        expires = (utcnow() + datetime.timedelta(minutes=VERIFY_TTL_MIN)).strftime("%Y-%m-%dT%H:%M:%S")
        await q_exec("INSERT OR REPLACE INTO pending_verifications(discord_id,roblox_id,roblox_username,code,expires_at) VALUES(?,?,?,?,?)",
                     (interaction.user.id, roblox_id, exact, code, expires))
        embed = discord.Embed(
            title="🔐 CONFIRMA QUE ERES EL DUEÑO DE LA CUENTA",
            description=(f"Para verificar la cuenta **{exact}**, entra a tu perfil de Roblox ➔ **Editar Perfil** ➔ "
                         f"pega este código en tu descripción (**Acerca de mí**):\n\n# `{code}`\n\n"
                         f"Luego pulsa **✅ Confirmar Identidad**.\n*El código vence {dts(expires, 'R')}. "
                         f"Roblox puede tardar unos segundos en reflejar el cambio.*"),
            color=YELLOW)
        await interaction.followup.send(embed=embed, view=ConfirmIdentityView(), ephemeral=True)


class ConfirmIdentityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Confirmar Identidad", style=discord.ButtonStyle.success, custom_id="repcl_btn_confirm_identity")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        row = await q_one("SELECT * FROM pending_verifications WHERE discord_id=?", (interaction.user.id,))
        if not row:
            await interaction.followup.send("❌ No tienes una verificación pendiente. Pulsa **🔗 Verificar Roblox** de nuevo.", ephemeral=True)
            return
        if row["expires_at"] <= now_iso():
            await q_exec("DELETE FROM pending_verifications WHERE discord_id=?", (interaction.user.id,))
            await interaction.followup.send("⌛ Tu código venció. Pulsa **🔗 Verificar Roblox** para generar uno nuevo.", ephemeral=True)
            return
        if await get_blacklist(interaction.user.id):
            await interaction.followup.send("⛔ Estás en la Blacklist de REPCL.", ephemeral=True)
            return
        user = await roblox.get_user(row["roblox_id"])
        if user is None:
            await interaction.followup.send("⚠️ No pude consultar Roblox ahora mismo. Inténtalo en unos segundos.", ephemeral=True)
            return
        if row["code"].lower() not in (user.get("description") or "").lower():
            await interaction.followup.send(f"❌ No encontré el código `{row['code']}` en la biografía de **{row['roblox_username']}**. "
                                            "Guárdalo en tu perfil, espera unos segundos y pulsa de nuevo.", ephemeral=True)
            return

        res = await finalize_link(interaction.user, row["roblox_id"], user.get("name", row["roblox_username"]))
        if res is None:
            await interaction.followup.send("⚠️ Roblox no respondió al consultar el grupo. Inténtalo de nuevo en unos segundos.", ephemeral=True)
            return
        info, kind, label = res
        await q_exec("DELETE FROM pending_verifications WHERE discord_id=?", (interaction.user.id,))
        avatar = await roblox.get_thumbnail(row["roblox_id"])
        await interaction.followup.send(embed=verified_embed(interaction.user, row["roblox_id"], user.get("name"), info, kind, label, avatar), ephemeral=True)
        await audit(interaction.guild, "📋 AUDITORÍA • NUEVA VERIFICACIÓN ROBLOX", "Verificación (doble factor)", color=GREEN,
                    user=interaction.user, channel=interaction.channel,
                    fields=[("Roblox", f"[{user.get('name')}](https://www.roblox.com/users/{row['roblox_id']}/profile)", True),
                            ("Rango / Rol", label, True)])

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, custom_id="repcl_btn_cancel_identity")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await q_exec("DELETE FROM pending_verifications WHERE discord_id=?", (interaction.user.id,))
        await interaction.response.send_message("🗑️ Verificación cancelada.", ephemeral=True)


class VerifyPersistentView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verificar Roblox", style=discord.ButtonStyle.danger, custom_id="repcl_btn_verify_roblox", emoji="🔗")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await get_blacklist(interaction.user.id):
            await interaction.response.send_message("⛔ Estás en la Blacklist de REPCL.", ephemeral=True)
            return
        await interaction.response.send_modal(VerificationModal())

    @discord.ui.button(label="Actualizar Rango", style=discord.ButtonStyle.secondary, custom_id="repcl_btn_refresh_rank", emoji="🎖️")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not await get_linked(interaction.user.id):
            await interaction.followup.send("❌ Aún no tienes una cuenta vinculada. Pulsa **🔗 Verificar Roblox** primero.", ephemeral=True)
            return
        res = await sync_member(interaction.user, refresh_name=True)
        if res and res.get("error"):
            await interaction.followup.send("⚠️ Roblox no respondió. Inténtalo de nuevo en unos segundos.", ephemeral=True)
            return
        await interaction.followup.send(f"✅ Sincronización completa.\n- **Rango Roblox:** `{res['rank_name']}`\n- **Rol asignado:** `{res['label']}`", ephemeral=True)


@slash("panel-verificacion", "[ADMIN] Envía el panel oficial de verificación")
async def cmd_panel(interaction: discord.Interaction):
    if not await admin_guard(interaction):
        return
    embed = discord.Embed(
        title="🇨🇱 REPCL • EJÉRCITO DE CHILE | VERIFICACIÓN OFICIAL",
        description=("Para acceder al servidor y recibir tus roles debes vincular tu cuenta de **Roblox**.\n\n"
                     "1️⃣ Pulsa **🔗 Verificar Roblox**.\n2️⃣ Escribe tu usuario exacto de Roblox.\n"
                     "3️⃣ Pega el **código único** en tu biografía de Roblox y pulsa **✅ Confirmar Identidad**.\n\n"
                     "• **Si perteneces al grupo:** recibes tu rango militar (`@Soldado Conscripto`, `@Cabo`, `@Sargento`…).\n"
                     "• **Si no perteneces:** recibes **🇨🇱 Ciudadano Chileno**.\n\n"
                     "*Si ascendes en Roblox, el bot lo detecta solo; también puedes pulsar **🔄 Actualizar Rango**.*"),
        color=RED)
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)
    embed.set_footer(text="Ejército de Chile • Siempre Vencedor, Jamás Vencido")
    await interaction.channel.send(embed=embed, view=VerifyPersistentView())
    await interaction.response.send_message("✅ Panel enviado a este canal.", ephemeral=True)
    await log_admin(interaction, "Panel de verificación enviado")


@slash("forzar-verificar", "[ADMIN] Vincula manualmente a un miembro sin pedir código")
@app_commands.describe(usuario="Miembro de Discord", roblox_username="Usuario de Roblox")
async def cmd_forzar(interaction: discord.Interaction, usuario: discord.Member, roblox_username: str):
    if not await admin_guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    data = await roblox.get_user_by_username(roblox_username.strip())
    if not data:
        await interaction.followup.send("❌ Usuario de Roblox no encontrado.", ephemeral=True)
        return
    res = await finalize_link(usuario, data["id"], data["name"])
    if res is None:
        await interaction.followup.send("⚠️ Roblox no respondió. Inténtalo de nuevo.", ephemeral=True)
        return
    info, kind, label = res
    await interaction.followup.send(f"✅ {usuario.mention} vinculado a **{data['name']}** → `{label}`", ephemeral=True)
    await audit(interaction.guild, "📋 AUDITORÍA • VERIFICACIÓN FORZADA", "Verificación manual por admin", color=ORANGE,
                user=usuario, channel=interaction.channel,
                fields=[("Administrador", interaction.user.mention, True), ("Roblox", data["name"], True), ("Rol", label, True)])


@slash("desvincular-cuenta", "[ADMIN] Elimina la vinculación Roblox de un miembro")
@app_commands.describe(usuario="Miembro a desvincular")
async def cmd_desvincular_cuenta(interaction: discord.Interaction, usuario: discord.Member):
    if not await admin_guard(interaction):
        return
    cur = await q_exec("DELETE FROM linked_users WHERE discord_id=?", (usuario.id,))
    await q_exec("DELETE FROM pending_verifications WHERE discord_id=?", (usuario.id,))
    await interaction.response.send_message("✅ Cuenta desvinculada." if cur.rowcount else "ℹ️ Ese usuario no tenía cuenta vinculada.", ephemeral=True)
    await log_admin(interaction, "Cuenta de Roblox desvinculada", f"Usuario: {usuario} ({usuario.id})")


@slash("sincronizar", "[ADMIN] Fuerza la sincronización de rangos Roblox → Discord")
@app_commands.describe(usuario="Miembro concreto (vacío = todos los vinculados)")
async def cmd_sincronizar(interaction: discord.Interaction, usuario: Optional[discord.Member] = None):
    if not await admin_guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    if usuario:
        res = await sync_member(usuario, refresh_name=True)
        if res is None:
            await interaction.followup.send("❌ Ese usuario no tiene cuenta vinculada.", ephemeral=True)
        elif res.get("error"):
            await interaction.followup.send("⚠️ Roblox no respondió.", ephemeral=True)
        else:
            await interaction.followup.send(f"✅ {usuario.mention}: `{res['rank_name']}` → `{res['label']}`", ephemeral=True)
        return
    await interaction.followup.send("⏳ Sincronizando a todos los vinculados en segundo plano…", ephemeral=True)

    async def runner():
        t, c, e = await run_full_sync(interaction.guild)
        try:
            await interaction.followup.send(f"✅ Sincronización terminada: **{t}** revisados, **{c}** con cambio de rango, **{e}** errores.", ephemeral=True)
        except Exception:
            pass
    asyncio.create_task(runner())
    await log_admin(interaction, "Sincronización masiva iniciada")


# ---------------------------------------------------------------------------
# 10. ASCENSOS Y RANGOS
# ---------------------------------------------------------------------------
async def rank_autocomplete(interaction: discord.Interaction, current: str):
    try:
        roles = await roblox.get_group_roles(await get_group_id())
    except Exception:
        return []
    cur = current.lower()
    out = [app_commands.Choice(name=trunc(f"{r['rank']} • {r['name']}", 100), value=str(r["rank"]))
           for r in sorted(roles, key=lambda x: x["rank"], reverse=True)
           if r["rank"] > 0 and (cur in r["name"].lower() or cur in str(r["rank"]))]
    return out[:25]

async def change_rank(interaction: discord.Interaction, usuario: discord.Member, nuevo_rango: str, action: str):
    if not await admin_guard(interaction):
        return
    await interaction.response.defer()
    linked = await get_linked(usuario.id)
    if not linked:
        await interaction.followup.send(f"❌ {usuario.mention} no tiene una cuenta de Roblox vinculada.")
        return
    group_id = await get_group_id()
    roles = await roblox.get_group_roles(group_id, force=True)
    if not roles:
        await interaction.followup.send("❌ No pude obtener los rangos del grupo. Revisa el ID con `/configurar-grupo`.")
        return
    target = find_group_role(roles, nuevo_rango)
    if not target or target["rank"] == 0:
        valid = "\n".join(f"• **Nivel {r['rank']}**: `{r['name']}`" for r in sorted(roles, key=lambda x: x["rank"], reverse=True) if r["rank"] > 0)
        await interaction.followup.send(f"❌ Rango **`{nuevo_rango}`** no encontrado.\n\n**Rangos disponibles:**\n{trunc(valid, 1700)}")
        return
    current = await roblox.get_user_group_role(linked["roblox_id"], group_id)
    if current is None:
        await interaction.followup.send("⚠️ Roblox no respondió. Inténtalo de nuevo.")
        return
    if not current["in_group"]:
        await interaction.followup.send(f"⚠️ **{linked['roblox_username']}** no es miembro del grupo de Roblox. Debe pulsar **Join Group** primero.")
        return
    if action == "ASCENSO" and target["rank"] <= current["rank"]:
        await interaction.followup.send(f"❌ **{target['name']}** (nivel {target['rank']}) no es superior al rango actual (**{current['role_name']}**, nivel {current['rank']}). Usa `/descender`.")
        return
    if action == "DESCENSO" and target["rank"] >= current["rank"]:
        await interaction.followup.send(f"❌ **{target['name']}** (nivel {target['rank']}) no es inferior al rango actual (**{current['role_name']}**, nivel {current['rank']}). Usa `/ascender`.")
        return

    # Permisos: el administrador debe superar tanto el rango actual como el destino
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id):
        alink = await get_linked(interaction.user.id)
        ainfo = await roblox.get_user_group_role(alink["roblox_id"], group_id) if alink else None
        if not alink or not ainfo or not ainfo["in_group"]:
            await interaction.followup.send("❌ Debes tener tu cuenta de Roblox vinculada y pertenecer al grupo para gestionar rangos.")
            return
        if ainfo["rank"] <= max(current["rank"], target["rank"]):
            await interaction.followup.send(f"❌ Tu rango (**{ainfo['role_name']}**, nivel {ainfo['rank']}) debe ser superior al rango actual del usuario y al rango destino.")
            return

    result = await roblox.set_user_rank(group_id, linked["roblox_id"], target["id"])
    if not result["success"]:
        err = str(result["error"])
        if "invalid" in err.lower() or "not exist" in err.lower():
            hint = "\n\n*(El usuario no es miembro del grupo: debe unirse en Roblox primero.)*"
        else:
            hint = "\n\n*(¿La cuenta del bot tiene permiso para cambiar rangos y su rango está por encima del destino?)*"
        await interaction.followup.send(f"⚠️ **Error de Roblox:** `{err}`{hint}")
        return

    new_info = {"in_group": True, "rank": target["rank"], "role_id": target["id"], "role_name": target["name"]}
    await apply_roles(usuario, linked["roblox_username"], new_info)
    await q_exec("UPDATE linked_users SET current_rank_id=?, current_rank_name=? WHERE discord_id=?",
                 (target["rank"], target["name"], usuario.id))
    await log_rank_change(usuario.id, linked["roblox_id"], current["role_name"], target["name"],
                          interaction.user.id, str(interaction.user), action)

    up = action == "ASCENSO"
    embed = discord.Embed(title="🎖️ ASCENSO MILITAR CONCEDIDO" if up else "📉 DECRETO DE DESCENSO MILITAR",
                          description=f"{usuario.mention} fue {'ascendido' if up else 'descendido'} en **REPCL • Ejército de Chile**.",
                          color=GREEN if up else ORANGE, timestamp=utcnow())
    embed.add_field(name="👤 Militar", value=f"{usuario.mention} (`{linked['roblox_username']}`)", inline=True)
    embed.add_field(name="Rango anterior", value=f"`{current['role_name']}`", inline=True)
    embed.add_field(name="Rango nuevo", value=f"**{target['name']}** (Nivel {target['rank']})", inline=True)
    embed.add_field(name="👮 Administrador", value=interaction.user.mention, inline=False)
    embed.set_thumbnail(url=await roblox.get_thumbnail(linked["roblox_id"]))
    await interaction.followup.send(embed=embed)
    await audit(interaction.guild, f"{'📈' if up else '📉'} AUDITORÍA • {action}", action, color=GREEN if up else ORANGE,
                user=usuario, channel=interaction.channel,
                fields=[("Rango anterior", current["role_name"], True), ("Rango nuevo", target["name"], True),
                        ("Administrador", f"{interaction.user.mention} (`{interaction.user.id}`)", False)])

@slash("ascender", "Asciende a un usuario en Roblox y actualiza su rol en Discord")
@app_commands.describe(usuario="Miembro a ascender", rango="Nivel o nombre del rango destino")
@app_commands.autocomplete(rango=rank_autocomplete)
async def cmd_ascender(interaction: discord.Interaction, usuario: discord.Member, rango: str):
    await change_rank(interaction, usuario, rango, "ASCENSO")

@slash("descender", "Desciende a un usuario en Roblox y actualiza su rol en Discord")
@app_commands.describe(usuario="Miembro a descender", rango="Nivel o nombre del rango destino")
@app_commands.autocomplete(rango=rank_autocomplete)
async def cmd_descender(interaction: discord.Interaction, usuario: discord.Member, rango: str):
    await change_rank(interaction, usuario, rango, "DESCENSO")

@slash("rango", "Muestra el rango actual de Roblox y Discord de un usuario")
@app_commands.describe(usuario="Usuario a consultar (opcional)")
async def cmd_rango(interaction: discord.Interaction, usuario: Optional[discord.Member] = None):
    target = usuario or interaction.user
    await interaction.response.defer()
    linked = await get_linked(target.id)
    if not linked:
        await interaction.followup.send(f"❌ {target.mention} no tiene una cuenta de Roblox vinculada.")
        return
    info = await roblox.get_user_group_role(linked["roblox_id"], await get_group_id())
    embed = discord.Embed(title="🇨🇱 FICHA DE SERVICIO MILITAR • REPCL", color=RED, timestamp=utcnow())
    embed.set_thumbnail(url=await roblox.get_thumbnail(linked["roblox_id"]))
    embed.add_field(name="Discord", value=f"{target.mention} (`{target.id}`)", inline=True)
    embed.add_field(name="Roblox", value=f"[{linked['roblox_username']}](https://www.roblox.com/users/{linked['roblox_id']}/profile)", inline=True)
    if info is None:
        embed.add_field(name="🎖️ Rango en Roblox", value=f"{linked['current_rank_name']} *(último dato guardado; Roblox no respondió)*", inline=False)
    elif info["in_group"]:
        embed.add_field(name="🎖️ Rango en Roblox", value=f"**{info['role_name']}** (Nivel {info['rank']})", inline=False)
    else:
        embed.add_field(name="🛡️ Estado en Roblox", value="No pertenece al grupo (Ciudadano)", inline=False)
    roles = [r.mention for r in reversed(target.roles) if not r.is_default()]
    embed.add_field(name="🏷️ Roles en Discord", value=" ".join(roles[:10]) or "Sin roles", inline=False)
    embed.add_field(name="📅 Vinculado", value=dts(linked["linked_at"], "D"), inline=True)
    await interaction.followup.send(embed=embed)

@slash("historial-rangos", "Muestra el historial de cambios de rango de un usuario")
@app_commands.describe(usuario="Usuario a consultar")
async def cmd_historial(interaction: discord.Interaction, usuario: discord.Member):
    await interaction.response.defer()
    rows = await q_all("SELECT * FROM rank_history WHERE discord_id=? ORDER BY id DESC LIMIT 10", (usuario.id,))
    if not rows:
        await interaction.followup.send(f"📋 No hay cambios de rango registrados para {usuario.mention}.")
        return
    embed = discord.Embed(title=f"📋 HISTORIAL DE RANGOS • {usuario.display_name}", color=CYAN, timestamp=utcnow())
    for r in rows:
        icon = {"ASCENSO": "📈", "DESCENSO": "📉"}.get(r["action_type"], "🔄")
        embed.add_field(name=f"{icon} {r['action_type']}: {r['old_rank']} ➔ {r['new_rank']}",
                        value=f"**Admin:** `{r['admin_name']}`\n**Fecha:** {dts(r['timestamp'])}", inline=False)
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# 11. CONFIGURACIÓN (todo persiste en SQLite)
# ---------------------------------------------------------------------------
@slash("configurar-grupo", "[ADMIN] Consulta o cambia el ID del grupo de Roblox")
@app_commands.describe(id_grupo="ID numérico del grupo (vacío = ver el actual)")
async def cmd_cfg_grupo(interaction: discord.Interaction, id_grupo: Optional[int] = None):
    if not await admin_guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    if id_grupo is not None:
        if id_grupo <= 0:
            await interaction.followup.send("❌ El ID debe ser un número mayor que 0.", ephemeral=True)
            return
        g = await roblox.get_group_info(id_grupo)
        if not g:
            await interaction.followup.send("❌ Ese grupo no existe o Roblox no respondió. Verifica el ID.", ephemeral=True)
            return
        await set_setting("roblox_group_id", id_grupo)
        roblox._roles_cache.pop(id_grupo, None)
        await interaction.followup.send(f"✅ Grupo configurado: **[{g.get('name')}](https://www.roblox.com/groups/{id_grupo})** (`{id_grupo}`)", ephemeral=True)
        await log_admin(interaction, "ID de grupo de Roblox cambiado", f"Nuevo ID: {id_grupo} ({g.get('name')})")
    else:
        gid = await get_group_id()
        g = await roblox.get_group_info(gid)
        await interaction.followup.send(f"ℹ️ Grupo actual: **{g.get('name') if g else 'Desconocido'}** (`{gid}`)\nCambiar: `/configurar-grupo id_grupo:ID`", ephemeral=True)

@slash("configurar-rango", "[ADMIN] Vincula un rango de Roblox con un rol de Discord")
@app_commands.describe(rango_roblox="Nivel o nombre del rango (ej: 1, 255, Cabo)", rol_discord="Rol de Discord a entregar")
@app_commands.autocomplete(rango_roblox=rank_autocomplete)
async def cmd_cfg_rango(interaction: discord.Interaction, rango_roblox: str, rol_discord: discord.Role):
    if not await admin_guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    roles = await roblox.get_group_roles(await get_group_id(), force=True)
    target = find_group_role(roles, rango_roblox)
    if not target and rango_roblox.strip().isdigit() and 1 <= int(rango_roblox) <= 255:
        target = {"rank": int(rango_roblox), "name": f"Rango Nivel {rango_roblox}"}
    if not target:
        await interaction.followup.send("❌ No encontré ese rango en el grupo. Usa el número de nivel (ej. `1`, `255`).", ephemeral=True)
        return
    await q_exec("""INSERT INTO custom_role_mappings(roblox_rank_id,roblox_rank_name,discord_role_id,discord_role_name)
                    VALUES(?,?,?,?) ON CONFLICT(roblox_rank_id) DO UPDATE SET roblox_rank_name=excluded.roblox_rank_name,
                    discord_role_id=excluded.discord_role_id, discord_role_name=excluded.discord_role_name""",
                 (target["rank"], target["name"], rol_discord.id, rol_discord.name))
    warn = ""
    if rol_discord >= interaction.guild.me.top_role:
        warn = "\n⚠️ Ese rol está **por encima** del rol del bot: sube el rol del bot o no podré asignarlo."
    await interaction.followup.send(f"✅ `{target['name']}` (nivel {target['rank']}) ➔ {rol_discord.mention}{warn}", ephemeral=True)
    await log_admin(interaction, "Vinculación de rango configurada", f"{target['name']} (nivel {target['rank']}) ➔ @{rol_discord.name}")

@slash("desvincular-rango", "[ADMIN] Elimina una vinculación personalizada de rango")
@app_commands.describe(rango_roblox="Nivel numérico del rango (ej: 1, 255)")
async def cmd_desvincular_rango(interaction: discord.Interaction, rango_roblox: int):
    if not await admin_guard(interaction):
        return
    cur = await q_exec("DELETE FROM custom_role_mappings WHERE roblox_rank_id=?", (rango_roblox,))
    await interaction.response.send_message("✅ Vinculación eliminada." if cur.rowcount else "⚠️ No había vinculación para ese nivel.", ephemeral=True)
    await log_admin(interaction, "Vinculación de rango eliminada", f"Nivel {rango_roblox}")

@slash("configurar-ciudadano", "[ADMIN] Rol que reciben quienes NO están en el grupo")
@app_commands.describe(rol_discord="Rol de Ciudadano Chileno")
async def cmd_cfg_ciudadano(interaction: discord.Interaction, rol_discord: discord.Role):
    if not await admin_guard(interaction):
        return
    await set_setting("citizen_role_id", rol_discord.id)
    await interaction.response.send_message(f"✅ Rol de ciudadano: {rol_discord.mention}", ephemeral=True)
    await log_admin(interaction, "Rol de ciudadano configurado", f"@{rol_discord.name}")

@slash("configurar-rol", "[ADMIN] Configura roles especiales del bot")
@app_commands.describe(tipo="Qué configurar", rol="Rol de Discord")
@app_commands.choices(tipo=[
    app_commands.Choice(name="Rol de Blacklist", value="blacklist_role_id"),
    app_commands.Choice(name="Rol de Suspensión", value="suspension_role_id"),
    app_commands.Choice(name="Añadir rol administrador del bot", value="admin_add"),
    app_commands.Choice(name="Quitar rol administrador del bot", value="admin_del"),
])
async def cmd_cfg_rol(interaction: discord.Interaction, tipo: app_commands.Choice[str], rol: discord.Role):
    if not await admin_guard(interaction):
        return
    if tipo.value in ("admin_add", "admin_del"):
        roles = set(await get_admin_roles())
        (roles.add if tipo.value == "admin_add" else roles.discard)(rol.id)
        await set_setting("admin_role_ids", json.dumps(sorted(roles)))
    else:
        await set_setting(tipo.value, rol.id)
    await interaction.response.send_message(f"✅ **{tipo.name}**: {rol.mention}", ephemeral=True)
    await log_admin(interaction, f"Configuración: {tipo.name}", f"@{rol.name}")

@slash("configurar-canal", "[ADMIN] Configura los canales privados de auditoría")
@app_commands.describe(tipo="Qué canal configurar", canal="Canal de texto (debe ser privado)")
@app_commands.choices(tipo=[
    app_commands.Choice(name="Auditoría general (sanciones, rangos, borrados, etc.)", value="audit_channel_id"),
    app_commands.Choice(name="Registro de TODOS los mensajes enviados (opcional)", value="messages_channel_id"),
])
async def cmd_cfg_canal(interaction: discord.Interaction, tipo: app_commands.Choice[str], canal: discord.TextChannel):
    if not await admin_guard(interaction):
        return
    await set_setting(tipo.value, canal.id)
    await interaction.response.send_message(f"✅ **{tipo.name}** ➔ {canal.mention}\n*Asegúrate de que el canal sea visible solo para administradores.*", ephemeral=True)
    await log_admin(interaction, f"Canal configurado: {tipo.name}", f"#{canal.name}")

@slash("configurar-tickets", "[ADMIN] Categoría donde se crean los tickets")
@app_commands.describe(categoria="Categoría de canales")
async def cmd_cfg_tickets(interaction: discord.Interaction, categoria: discord.CategoryChannel):
    if not await admin_guard(interaction):
        return
    await set_setting("ticket_category_id", categoria.id)
    await interaction.response.send_message(f"✅ Los tickets se crearán en **{categoria.name}**.", ephemeral=True)
    await log_admin(interaction, "Categoría de tickets configurada", categoria.name)

@slash("ver-rangos", "[ADMIN] Tabla de rangos Roblox ↔ roles de Discord")
async def cmd_ver_rangos(interaction: discord.Interaction):
    if not await admin_guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    gid = await get_group_id()
    roles = await roblox.get_group_roles(gid, force=True)
    custom = await get_custom_mappings()
    guild = interaction.guild
    lines = []
    for r in sorted([x for x in roles if x["rank"] > 0], key=lambda x: x["rank"], reverse=True):
        if r["rank"] in custom:
            d = guild.get_role(custom[r["rank"]])
            st = f"{d.mention} *(personalizado)*" if d else f"⚠️ rol `{custom[r['rank']]}` borrado"
        else:
            d = match_discord_role(guild, r["name"], r["rank"], custom)
            st = f"{d.mention} *(automático)*" if d else "⚠️ **sin rol** → usa `/configurar-rango`"
        lines.append(f"• **{r['rank']}** `{r['name']}` ➔ {st}")
    embed = discord.Embed(title="🇨🇱 TABLA DE RANGOS • ROBLOX ↔ DISCORD",
                          description=f"Grupo de Roblox: `{gid}`" + ("" if roles else "\n⚠️ No pude cargar los rangos (revisa el ID o la red)."),
                          color=CYAN, timestamp=utcnow())
    chunk, n = "", 1
    for ln in lines:
        if len(chunk) + len(ln) > 950:
            embed.add_field(name=f"🎖️ Rangos ({n})", value=chunk, inline=False)
            chunk, n = "", n + 1
        chunk += ln + "\n"
    if chunk:
        embed.add_field(name=f"🎖️ Rangos ({n})", value=chunk, inline=False)
    cit = find_citizen_role(guild, await get_int("citizen_role_id"))
    embed.add_field(name="🇨🇱 Rol Ciudadano", value=cit.mention if cit else "⚠️ sin configurar", inline=True)
    await interaction.followup.send(embed=embed, ephemeral=True)

@slash("ver-configuracion", "[ADMIN] Muestra la configuración actual del bot")
async def cmd_ver_config(interaction: discord.Interaction):
    if not await admin_guard(interaction):
        return
    g = interaction.guild
    def role(key):
        rid = key
        return f"<@&{rid}>" if rid else "⚠️ sin configurar"
    def chan(cid):
        return f"<#{cid}>" if cid else "⚠️ sin configurar"
    admins = await get_admin_roles()
    embed = discord.Embed(title="⚙️ CONFIGURACIÓN DE REPCL BOT", color=CYAN, timestamp=utcnow())
    embed.add_field(name="Grupo de Roblox", value=f"`{await get_group_id()}`", inline=True)
    embed.add_field(name="Cookie de Roblox", value="✅ configurada" if ROBLOX_COOKIE else "⚠️ falta ROBLOX_COOKIE", inline=True)
    embed.add_field(name="Sync automático", value=f"cada {SYNC_INTERVAL_MIN} min", inline=True)
    embed.add_field(name="Canal de auditoría", value=chan(await get_int("audit_channel_id")), inline=True)
    embed.add_field(name="Canal de mensajes", value=chan(await get_int("messages_channel_id")), inline=True)
    embed.add_field(name="Categoría de tickets", value=chan(await get_int("ticket_category_id")), inline=True)
    embed.add_field(name="Rol Ciudadano", value=role(await get_int("citizen_role_id")), inline=True)
    embed.add_field(name="Rol Blacklist", value=role(await get_int("blacklist_role_id")), inline=True)
    embed.add_field(name="Rol Suspensión", value=role(await get_int("suspension_role_id")), inline=True)
    embed.add_field(name="Roles admin del bot", value=" ".join(f"<@&{i}>" for i in admins) or "Solo Administradores de Discord", inline=False)
    me = g.me
    embed.add_field(name="Permisos del bot", value=("✅ Gestionar roles, apodos y miembros" if me.guild_permissions.manage_roles and me.guild_permissions.manage_nicknames and me.guild_permissions.moderate_members
                                                    else "⚠️ Faltan permisos (Gestionar roles / apodos / moderar miembros)"), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# 12. SANCIONES Y BLACKLIST
# ---------------------------------------------------------------------------
async def apply_blacklist_roles(member: discord.Member):
    linked = await get_linked(member.id)
    info = {"in_group": False}
    if linked:
        info = await roblox.get_user_group_role(linked["roblox_id"], await get_group_id()) or {"in_group": False}
    await apply_roles(member, linked["roblox_username"] if linked else member.display_name, info)

async def dm(user: discord.abc.User, text: str):
    try:
        await user.send(text)
    except Exception:
        pass

@slash("advertir", "Emite una advertencia formal a un usuario")
@app_commands.describe(usuario="Usuario a advertir", motivo="Razón de la advertencia")
async def cmd_advertir(interaction: discord.Interaction, usuario: discord.Member, motivo: str):
    if not await admin_guard(interaction):
        return
    ok, why = can_target(interaction.user, usuario)
    if not ok:
        await interaction.response.send_message(f"❌ {why}", ephemeral=True)
        return
    await interaction.response.defer()
    s_id = await add_sanction(usuario.id, str(usuario), interaction.user.id, str(interaction.user), "warn", motivo, "N/A", None)
    embed = discord.Embed(title="⚠️ ADVERTENCIA DISCIPLINARIA", description=f"{usuario.mention} recibió una advertencia oficial.", color=YELLOW, timestamp=utcnow())
    embed.add_field(name="ID", value=f"`{s_id}`", inline=True)
    embed.add_field(name="Motivo", value=motivo, inline=True)
    embed.add_field(name="Administrador", value=interaction.user.mention, inline=False)
    await dm(usuario, f"⚠️ **Advertencia en REPCL • Ejército de Chile**\n**Motivo:** {motivo}\n**ID:** `{s_id}`")
    await interaction.followup.send(embed=embed)
    await audit(interaction.guild, "⚖️ AUDITORÍA • ADVERTENCIA", "Advertencia", color=YELLOW, user=usuario, channel=interaction.channel,
                content=motivo, fields=[("ID sanción", s_id, True), ("Administrador", f"{interaction.user.mention} (`{interaction.user.id}`)", True)])

@slash("sancionar", "Aplica una sanción a un usuario")
@app_commands.describe(usuario="Usuario sancionado", tipo="Tipo de sanción", motivo="Motivo", duracion="Ej: 30m, 12h, 7d, 1w (según el tipo)")
@app_commands.choices(tipo=[app_commands.Choice(name=v, value=k) for k, v in SANCTION_TYPES.items() if k != "ban"])
async def cmd_sancionar(interaction: discord.Interaction, usuario: discord.Member, tipo: app_commands.Choice[str], motivo: str, duracion: Optional[str] = None):
    if not await admin_guard(interaction):
        return
    ok, why = can_target(interaction.user, usuario)
    if not ok:
        await interaction.response.send_message(f"❌ {why}", ephemeral=True)
        return
    code = tipo.value
    temporal = code in DEFAULT_DURATION
    dur_txt = duracion or DEFAULT_DURATION.get(code)
    td = None
    if temporal:
        try:
            td = parse_duration(dur_txt)
        except ValueError:
            td = None
        if td is None:
            await interaction.response.send_message("❌ Duración inválida. Usa por ejemplo `30m`, `12h`, `7d`, `1w`.", ephemeral=True)
            return
    await interaction.response.defer()
    expires = (utcnow() + td).strftime("%Y-%m-%dT%H:%M:%S") if td else None
    dur_label = dur_txt if td else ("Permanente" if code == "bl_perm" else "N/A")
    label = SANCTION_TYPES[code]
    s_id = await add_sanction(usuario.id, str(usuario), interaction.user.id, str(interaction.user), code, motivo, dur_label, expires)
    notes: List[str] = []

    await dm(usuario, f"⚖️ **Sanción en REPCL • Ejército de Chile**\n**Tipo:** {label}\n**Motivo:** {motivo}\n**Duración:** {dur_label}\n**ID:** `{s_id}`")

    if code == "mute":
        try:
            await usuario.timeout(min(td, datetime.timedelta(days=28)), reason=motivo)
            if td > datetime.timedelta(days=28):
                notes.append("Discord limita el timeout a 28 días; se aplicó el máximo.")
        except Exception as e:
            notes.append(f"No se pudo silenciar: {e}")
    elif code == "suspend":
        rid = await get_int("suspension_role_id")
        role = interaction.guild.get_role(rid) if rid else None
        if not role:
            notes.append("No hay rol de suspensión (`/configurar-rol`); la sanción quedó registrada pero sin efecto en roles.")
        elif not await safe_roles(usuario, [role], [], f"Suspensión: {motivo}"):
            notes.append("No pude asignar el rol de suspensión (jerarquía/permisos).")
    elif code in ("bl_temp", "bl_perm"):
        linked = await get_linked(usuario.id)
        await set_blacklist(usuario.id, linked["roblox_id"] if linked else 0, motivo, interaction.user.id, str(interaction.user), expires)
        await apply_blacklist_roles(usuario)
    elif code == "kick":
        try:
            await usuario.kick(reason=motivo)
        except Exception as e:
            notes.append(f"No se pudo expulsar: {e}")

    embed = discord.Embed(title="⚖️ SANCIÓN DISCIPLINARIA APLICADA", color=0xD63031, timestamp=utcnow())
    embed.add_field(name="ID", value=f"`{s_id}`", inline=True)
    embed.add_field(name="Usuario", value=f"{usuario.mention} (`{usuario.id}`)", inline=True)
    embed.add_field(name="Tipo", value=f"**{label}**", inline=True)
    embed.add_field(name="Duración", value=f"`{dur_label}`" + (f" (vence {dts(expires, 'R')})" if expires else ""), inline=True)
    embed.add_field(name="Motivo", value=motivo, inline=False)
    embed.add_field(name="Oficial a cargo", value=interaction.user.mention, inline=False)
    if notes:
        embed.add_field(name="⚠️ Avisos", value="\n".join(notes), inline=False)
    await interaction.followup.send(embed=embed)
    await audit(interaction.guild, "⚖️ AUDITORÍA • SANCIÓN APLICADA", label, color=0xD63031, user=usuario, channel=interaction.channel,
                content=motivo, fields=[("ID sanción", s_id, True), ("Duración", dur_label, True),
                                         ("Administrador", f"{interaction.user.mention} (`{interaction.user.id}`)", False)])

@slash("sanciones", "Historial de sanciones de un usuario")
@app_commands.describe(usuario="Usuario a consultar")
async def cmd_sanciones(interaction: discord.Interaction, usuario: discord.Member):
    if not await admin_guard(interaction):
        return
    rows = await q_all("SELECT * FROM sanctions WHERE user_id=? ORDER BY created_at DESC", (usuario.id,))
    if not rows:
        await interaction.response.send_message(f"✅ {usuario.mention} tiene el expediente limpio.", ephemeral=True)
        return
    embed = discord.Embed(title=f"⚖️ EXPEDIENTE • {usuario.display_name}", color=0xE74C3C, timestamp=utcnow(),
                          description=f"Total: **{len(rows)}** · Activas: **{sum(1 for r in rows if r['active'])}**")
    for r in rows[:8]:
        embed.add_field(name=f"[{r['id']}] {SANCTION_TYPES.get(r['sanction_type'], r['sanction_type'])} • {r['duration']}",
                        value=f"**Motivo:** {trunc(r['reason'], 300)}\n**Admin:** `{r['admin_tag']}`\n**Fecha:** {dts(r['created_at'])}"
                              + (f"\n**Vence:** {dts(r['expires_at'])}" if r["expires_at"] else "")
                              + f"\n**Estado:** {'🟢 activa' if r['active'] else '⚪ finalizada'}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@slash("ban", "Banea a un usuario de Discord y lo registra en la Blacklist")
@app_commands.describe(usuario="Usuario a banear", motivo="Motivo del ban")
async def cmd_ban(interaction: discord.Interaction, usuario: discord.User, motivo: str):
    if not await admin_guard(interaction):
        return
    member = interaction.guild.get_member(usuario.id)
    if member:
        ok, why = can_target(interaction.user, member)
        if not ok:
            await interaction.response.send_message(f"❌ {why}", ephemeral=True)
            return
    await interaction.response.defer()
    linked = await get_linked(usuario.id)
    rid = linked["roblox_id"] if linked else 0
    await set_blacklist(usuario.id, rid, motivo, interaction.user.id, str(interaction.user), None)
    s_id = await add_sanction(usuario.id, str(usuario), interaction.user.id, str(interaction.user), "ban", motivo, "Permanente", None)
    await dm(usuario, f"⛔ **Fuiste baneado de REPCL • Ejército de Chile**\n**Motivo:** {motivo}\n**ID:** `{s_id}`")
    notes = ""
    try:
        await interaction.guild.ban(usuario, reason=f"REPCL Blacklist: {motivo}", delete_message_seconds=0)
    except Exception as e:
        notes = f"\n⚠️ No pude banear en Discord: `{e}` (igual quedó en Blacklist)."
    embed = discord.Embed(title="⛔ USUARIO BANEADO Y EN BLACKLIST", color=DARK, timestamp=utcnow())
    embed.add_field(name="Usuario", value=f"{usuario.mention} (`{usuario.id}`)", inline=True)
    embed.add_field(name="Roblox ID", value=f"`{rid}`", inline=True)
    embed.add_field(name="Motivo", value=motivo + notes, inline=False)
    embed.add_field(name="Oficial", value=interaction.user.mention, inline=False)
    await interaction.followup.send(embed=embed)
    await audit(interaction.guild, "⛔ AUDITORÍA • BAN + BLACKLIST", "Ban", color=DARK, user=usuario, channel=interaction.channel,
                content=motivo, fields=[("ID sanción", s_id, True), ("Administrador", f"{interaction.user.mention} (`{interaction.user.id}`)", True)])

@slash("unban", "Retira el ban y quita al usuario de la Blacklist")
@app_commands.describe(usuario="Usuario (puedes pegar su ID)")
async def cmd_unban(interaction: discord.Interaction, usuario: discord.User):
    if not await admin_guard(interaction):
        return
    await interaction.response.defer()
    removed = await remove_blacklist(usuario.id)
    await q_exec("UPDATE sanctions SET active=0 WHERE user_id=? AND sanction_type IN ('ban','bl_temp','bl_perm')", (usuario.id,))
    unbanned = True
    try:
        await interaction.guild.unban(usuario, reason=f"Unban por {interaction.user}")
    except discord.NotFound:
        unbanned = False
    except Exception as e:
        unbanned = False
        print(f"[!] unban: {e}")
    member = interaction.guild.get_member(usuario.id)
    if member:
        if await get_linked(member.id):
            await sync_member(member)
        else:
            rid = await get_int("blacklist_role_id")
            role = interaction.guild.get_role(rid) if rid else None
            if role and role in member.roles:
                await safe_roles(member, [], [role], "Unban")
    await interaction.followup.send(f"✅ {usuario.mention}: Blacklist {'retirada' if removed else 'no existía'} · Ban de Discord {'retirado' if unbanned else 'no existía'}.")
    await audit(interaction.guild, "✅ AUDITORÍA • UNBAN", "Unban / retiro de Blacklist", color=GREEN, user=usuario, channel=interaction.channel,
                fields=[("Administrador", f"{interaction.user.mention} (`{interaction.user.id}`)", True)])

@slash("blacklist", "Lista los usuarios en la Blacklist de REPCL")
async def cmd_blacklist(interaction: discord.Interaction):
    if not await admin_guard(interaction):
        return
    rows = await q_all("SELECT * FROM blacklist ORDER BY created_at DESC LIMIT 15")
    if not rows:
        await interaction.response.send_message("✅ La Blacklist está vacía.", ephemeral=True)
        return
    embed = discord.Embed(title="⛔ BLACKLIST OFICIAL • REPCL", description=f"Mostrando **{len(rows)}** registros más recientes", color=DARK)
    for r in rows:
        embed.add_field(name=f"Discord `{r['user_id']}` · Roblox `{r['roblox_id']}`",
                        value=f"**Motivo:** {trunc(r['reason'], 250)}\n**Oficial:** `{r['admin_tag']}`\n**Fecha:** {dts(r['created_at'])}"
                              + (f"\n**Vence:** {dts(r['expires_at'])}" if r["expires_at"] else "\n**Vence:** nunca"), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@slash("blacklist-info", "Detalles de Blacklist de un usuario")
@app_commands.describe(usuario="Usuario a consultar")
async def cmd_blacklist_info(interaction: discord.Interaction, usuario: discord.User):
    if not await admin_guard(interaction):
        return
    bl = await get_blacklist(usuario.id)
    if not bl:
        await interaction.response.send_message(f"✅ {usuario.mention} no está en la Blacklist.", ephemeral=True)
        return
    embed = discord.Embed(title="⛔ DETALLES DE BLACKLIST", color=0xD63031, timestamp=utcnow())
    embed.add_field(name="Usuario", value=f"{usuario.mention} (`{usuario.id}`)", inline=True)
    embed.add_field(name="Roblox ID", value=f"`{bl['roblox_id']}`", inline=True)
    embed.add_field(name="Motivo", value=bl["reason"], inline=False)
    embed.add_field(name="Administrador", value=f"`{bl['admin_tag']}`", inline=True)
    embed.add_field(name="Registrado", value=dts(bl["created_at"]), inline=True)
    embed.add_field(name="Vence", value=dts(bl["expires_at"]) if bl["expires_at"] else "Nunca", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# 13. CONSULTA DE AUDITORÍA (solo administradores)
# ---------------------------------------------------------------------------
@slash("auditoria", "[ADMIN] Consulta el registro de auditoría de un usuario")
@app_commands.describe(usuario="Usuario a investigar", cantidad="Cuántos registros (máx. 25)", solo_borrados="Mostrar solo mensajes que borró")
async def cmd_auditoria(interaction: discord.Interaction, usuario: discord.User, cantidad: app_commands.Range[int, 1, 25] = 10, solo_borrados: bool = False):
    if not await admin_guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    if solo_borrados:
        rows = await q_all("SELECT * FROM message_log WHERE author_id=? AND deleted=1 ORDER BY deleted_at DESC LIMIT ?", (usuario.id, cantidad))
        lines = [f"🗑️ {dts(r['deleted_at'], 'f')} · <#{r['channel_id']}>\n> {trunc((r['content'] or '*sin texto*').replace(chr(10), ' '), 300)}"
                 + (f"\n> 📎 {trunc(r['attachments'], 200)}" if r["attachments"] and r["attachments"] != "[]" else "") for r in rows]
        title = f"🗑️ MENSAJES BORRADOS • {usuario}"
    else:
        rows = await q_all("SELECT * FROM audit_events WHERE user_id=? ORDER BY id DESC LIMIT ?", (usuario.id, cantidad))
        lines = [f"`{dts(r['created_at'], 'd')}` {dts(r['created_at'], 't')} · **{r['action']}**"
                 + (f" · <#{r['channel_id']}>" if r["channel_id"] else "")
                 + (f"\n> {trunc((r['content'] or '').replace(chr(10), ' '), 200)}" if r["content"] else "") for r in rows]
        title = f"📋 AUDITORÍA • {usuario}"
    if not lines:
        await interaction.followup.send("ℹ️ No hay registros para ese usuario.", ephemeral=True)
        return
    embed = discord.Embed(title=title, description=trunc("\n".join(lines), 4000), color=CYAN, timestamp=utcnow())
    await interaction.followup.send(embed=embed, ephemeral=True)
    await log_admin(interaction, "Consulta de auditoría", f"Usuario consultado: {usuario} ({usuario.id})")


# ---------------------------------------------------------------------------
# 14. EVENTOS DE AUDITORÍA
# ---------------------------------------------------------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    atts = [{"name": a.filename, "url": a.url, "type": a.content_type} for a in message.attachments]
    links = URL_RE.findall(message.content or "")
    try:
        await q_exec(
            """INSERT OR REPLACE INTO message_log(message_id,guild_id,channel_id,channel_name,author_id,author_tag,content,attachments,links,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (message.id, message.guild.id, message.channel.id, getattr(message.channel, "name", ""), message.author.id,
             str(message.author), message.content or "", json.dumps(atts), json.dumps(links), now_iso()))
    except Exception as e:
        print(f"[!] message_log: {e}")

    ignored = {await get_int("audit_channel_id"), await get_int("messages_channel_id")}
    if await get_int("messages_channel_id") and message.channel.id not in ignored:
        fields = []
        if atts:
            fields.append(("📎 Archivos / imágenes", "\n".join(f"[{a['name']}]({a['url']})" for a in atts), False))
        if links:
            fields.append(("🔗 Enlaces", "\n".join(links[:5]), False))
        fields.append(("Mensaje", f"[Ir al mensaje]({message.jump_url})", True))
        await audit(message.guild, "💬 AUDITORÍA • MENSAJE ENVIADO", "Mensaje enviado", color=0x95A5A6, user=message.author,
                    channel=message.channel, content=message.content or "*Sin texto*", fields=fields, kind="messages")

    m = KEYWORD_RE.findall(message.content or "")
    if m:
        await audit(message.guild, "🚨 ALERTA AUTOMÁTICA • CONDUCTA SOSPECHOSA", "Palabras clave sospechosas", color=0xC0392B,
                    user=message.author, channel=message.channel, content=message.content,
                    fields=[("Palabras detectadas", ", ".join(sorted(set(x.lower() for x in m))), True),
                            ("Sugerencia", "Revisar contexto y usar `/sancionar` o `/ban` si corresponde.", False),
                            ("Mensaje", f"[Ir al mensaje]({message.jump_url})", True)])
    await bot.process_commands(message)

@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if not guild:
        return
    row = await q_one("SELECT * FROM message_log WHERE message_id=?", (payload.message_id,))
    if not row:
        return          # mensaje de bot o anterior al registro
    await q_exec("UPDATE message_log SET deleted=1, deleted_at=? WHERE message_id=?", (now_iso(), payload.message_id))
    atts = json.loads(row["attachments"] or "[]")
    fields = []
    if atts:
        fields.append(("📎 Archivos / imágenes", "\n".join(f"[{a['name']}]({a['url']})" for a in atts), False))
    links = json.loads(row["links"] or "[]")
    if links:
        fields.append(("🔗 Enlaces", "\n".join(links[:5]), False))
    fields.append(("ID mensaje", f"`{payload.message_id}`", True))
    await audit(guild, "🗑️ AUDITORÍA • MENSAJE ELIMINADO", "Mensaje eliminado (contenido conservado)", color=0xE74C3C,
                user_id=row["author_id"], user_tag=row["author_tag"], channel_id=row["channel_id"], channel_name=row["channel_name"],
                content=row["content"] or "*Sin texto*", fields=fields)

@bot.event
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if not guild:
        return
    buf = []
    for mid in payload.message_ids:
        row = await q_one("SELECT * FROM message_log WHERE message_id=?", (mid,))
        if row:
            await q_exec("UPDATE message_log SET deleted=1, deleted_at=? WHERE message_id=?", (now_iso(), mid))
            buf.append(f"[{row['created_at']}] {row['author_tag']} ({row['author_id']}): {row['content']}")
    f = discord.File(io.BytesIO("\n".join(buf).encode("utf-8")), filename="mensajes_borrados.txt") if buf else None
    await audit(guild, "🗑️ AUDITORÍA • BORRADO MASIVO", f"{len(payload.message_ids)} mensajes eliminados", color=0xE74C3C,
                channel_id=payload.channel_id, content=f"Se conservaron {len(buf)} mensajes en el archivo adjunto.", files=[f] if f else None)

@bot.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent):
    new = payload.data.get("content")
    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if new is None or not guild:
        return
    row = await q_one("SELECT * FROM message_log WHERE message_id=?", (payload.message_id,))
    if not row or row["content"] == new:
        return
    await q_exec("UPDATE message_log SET content=?, edited=1 WHERE message_id=?", (new, payload.message_id))
    await audit(guild, "✏️ AUDITORÍA • MENSAJE EDITADO", "Mensaje editado", color=0xF39C12,
                user_id=row["author_id"], user_tag=row["author_tag"], channel_id=row["channel_id"], channel_name=row["channel_name"],
                fields=[("Contenido anterior", row["content"] or "*Vacío*", False), ("Contenido nuevo", new or "*Vacío*", False),
                        ("Enlace", f"[Ir al mensaje](https://discord.com/channels/{guild.id}/{row['channel_id']}/{payload.message_id})", True)])

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    gained = set(after.roles) - set(before.roles)
    lost = set(before.roles) - set(after.roles)
    if gained or lost:
        who, reason = await find_executor(after.guild, discord.AuditLogAction.member_role_update, after.id)
        if who and who.id == bot.user.id:
            return      # lo hizo el propio bot: ya queda registrado por verificación/rangos
        fields = []
        if gained:
            fields.append(("➕ Roles añadidos", " ".join(r.mention for r in gained), False))
        if lost:
            fields.append(("➖ Roles quitados", " ".join(r.mention for r in lost), False))
        fields.append(("Realizado por", f"{who.mention} (`{who.id}`)" if who else "Desconocido", True))
        await audit(after.guild, "🏷️ AUDITORÍA • CAMBIO DE ROLES", "Cambio de roles", color=0x9B59B6, user=after, fields=fields)

@bot.event
async def on_member_join(member: discord.Member):
    await audit(member.guild, "📥 AUDITORÍA • MIEMBRO NUEVO", "Entró al servidor", color=GREEN, user=member,
                fields=[("Cuenta creada", dts(member.created_at.strftime("%Y-%m-%dT%H:%M:%S"), "R"), True)])
    bl = await get_blacklist(member.id)
    if bl:
        await apply_blacklist_roles(member)
        await audit(member.guild, "🚨 ALERTA • USUARIO EN BLACKLIST ENTRÓ", "Reingreso de usuario vetado", color=0xC0392B, user=member, content=bl["reason"])
    elif await get_linked(member.id):
        await sync_member(member)       # recupera sus roles automáticamente al volver

@bot.event
async def on_member_remove(member: discord.Member):
    await audit(member.guild, "📤 AUDITORÍA • MIEMBRO SALIÓ", "Salió del servidor", color=ORANGE, user=member)

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    who, reason = await find_executor(guild, discord.AuditLogAction.ban, user.id)
    if who and who.id == bot.user.id:
        return
    await audit(guild, "🔨 AUDITORÍA • BAN MANUAL", "Ban realizado fuera del bot", color=DARK, user=user,
                content=reason, fields=[("Realizado por", f"{who.mention} (`{who.id}`)" if who else "Desconocido", True)])

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    who, _ = await find_executor(guild, discord.AuditLogAction.unban, user.id)
    if who and who.id == bot.user.id:
        return
    await audit(guild, "✅ AUDITORÍA • UNBAN MANUAL", "Unban realizado fuera del bot", color=GREEN, user=user,
                fields=[("Realizado por", f"{who.mention} (`{who.id}`)" if who else "Desconocido", True)])


# ---------------------------------------------------------------------------
# 15. TICKETS
# ---------------------------------------------------------------------------
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Abrir Ticket", style=discord.ButtonStyle.primary, custom_id="repcl_btn_ticket_open", emoji="🎫")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if await get_blacklist(interaction.user.id):
            await interaction.followup.send("⛔ Estás en la Blacklist de REPCL.", ephemeral=True)
            return
        existing = await q_one("SELECT channel_id FROM tickets WHERE user_id=? AND status='open'", (interaction.user.id,))
        if existing and interaction.guild.get_channel(existing["channel_id"]):
            await interaction.followup.send(f"❌ Ya tienes un ticket abierto: <#{existing['channel_id']}>", ephemeral=True)
            return
        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }
        admin_roles = [guild.get_role(i) for i in await get_admin_roles()]
        for r in admin_roles:
            if r:
                overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        cat_id = await get_int("ticket_category_id")
        category = guild.get_channel(cat_id) if cat_id else None
        name = re.sub(r"[^a-z0-9-]", "", interaction.user.name.lower()) or "usuario"
        try:
            ch = await guild.create_text_channel(f"ticket-{name}"[:90], category=category if isinstance(category, discord.CategoryChannel) else None,
                                                 overwrites=overwrites, reason=f"Ticket de {interaction.user}")
        except Exception as e:
            await interaction.followup.send(f"⚠️ No pude crear el ticket: `{e}`", ephemeral=True)
            return
        await q_exec("INSERT INTO tickets(channel_id,user_id,user_tag,status,created_at) VALUES(?,?,?,'open',?)",
                     (ch.id, interaction.user.id, str(interaction.user), now_iso()))
        embed = discord.Embed(title="🎫 TICKET ABIERTO", description=f"Hola {interaction.user.mention}, describe tu caso y un oficial te atenderá.", color=BLUE)
        pings = " ".join(r.mention for r in admin_roles if r)
        await ch.send(content=pings or None, embed=embed, view=TicketControlView(),
                      allowed_mentions=discord.AllowedMentions(roles=True))
        await interaction.followup.send(f"✅ Ticket creado: {ch.mention}", ephemeral=True)
        await audit(guild, "🎫 AUDITORÍA • TICKET ABIERTO", "Ticket abierto", color=BLUE, user=interaction.user, channel=ch)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Cerrar Ticket", style=discord.ButtonStyle.danger, custom_id="repcl_btn_ticket_close", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = await q_one("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (interaction.channel.id,))
        if not t:
            await interaction.response.send_message("ℹ️ Este ticket ya está cerrado.", ephemeral=True)
            return
        if interaction.user.id != t["user_id"] and not await is_repcl_admin(interaction.user):
            await interaction.response.send_message("❌ Solo el autor o un administrador puede cerrar el ticket.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Cerrando ticket y guardando la transcripción…")
        rows = await q_all("SELECT * FROM message_log WHERE channel_id=? ORDER BY message_id ASC", (interaction.channel.id,))
        text = "\n".join(f"[{r['created_at']}] {r['author_tag']} ({r['author_id']}): {r['content']}"
                         + (f"  [adjuntos: {r['attachments']}]" if r["attachments"] and r["attachments"] != "[]" else "") for r in rows) or "(sin mensajes)"
        await q_exec("UPDATE tickets SET status='closed', closed_at=?, closed_by=? WHERE id=?", (now_iso(), str(interaction.user), t["id"]))
        f = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"ticket-{t['id']}.txt")
        await audit(interaction.guild, "🎫 AUDITORÍA • TICKET CERRADO", "Ticket cerrado", color=ORANGE,
                    user_id=t["user_id"], user_tag=t["user_tag"], channel=interaction.channel,
                    fields=[("Cerrado por", f"{interaction.user.mention} (`{interaction.user.id}`)", True)], files=[f])
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket cerrado por {interaction.user}")
        except Exception as e:
            print(f"[!] No se pudo borrar el canal del ticket: {e}")


@slash("panel-tickets", "[ADMIN] Envía el panel para abrir tickets")
async def cmd_panel_tickets(interaction: discord.Interaction):
    if not await admin_guard(interaction):
        return
    embed = discord.Embed(title="🎫 SOPORTE • REPCL EJÉRCITO DE CHILE",
                          description="¿Necesitas ayuda, quieres postular o hacer un reclamo? Pulsa el botón para abrir un ticket privado.",
                          color=BLUE)
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("✅ Panel de tickets enviado.", ephemeral=True)
    await log_admin(interaction, "Panel de tickets enviado")


# ---------------------------------------------------------------------------
# 16. EVENTO ON_READY, SALUD HTTP Y ARRANQUE
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    print("=" * 60)
    print(f"🇨🇱 REPCL • EJÉRCITO DE CHILE | conectado como {bot.user} (ID {bot.user.id})")
    print(f"   Grupo Roblox: {await get_group_id()} · DB: {DB_PATH}")
    if not ROBLOX_COOKIE:
        print("   [!] ROBLOX_COOKIE vacío: /ascender y /descender no funcionarán.")
    print("=" * 60)
    await bot.change_presence(status=discord.Status.online,
                              activity=discord.Activity(type=discord.ActivityType.watching, name="REPCL • Ejército de Chile 🇨🇱"))

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"REPCL bot: ONLINE")
    def log_message(self, *args):
        pass

def start_health_server():
    port = os.getenv("PORT")
    if not port:
        return
    try:
        server = HTTPServer(("0.0.0.0", int(port)), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[+] Servidor de salud en el puerto {port}")
    except Exception as e:
        print(f"[!] No se pudo iniciar el servidor de salud: {e}")

def main():
    if not DISCORD_TOKEN or "tu_token" in DISCORD_TOKEN:
        print("\n[!] ERROR: define DISCORD_TOKEN (o DISCORD_BOT_TOKEN) en las variables de entorno.\n")
        sys.exit(1)
    start_health_server()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
