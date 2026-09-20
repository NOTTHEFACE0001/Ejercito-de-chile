"""
=============================================================================
  🇨🇱 REPCL • EJÉRCITO DE CHILE | BOT OFICIAL DE DISCORD & VERIFICACIÓN
  Sistema Integral: Verificación Roblox ↔ Discord, Ascensos/Descensos en Roblox,
  Sanciones, Blacklist y Sistema de Auditoría Avanzado con Alertas Automáticas.
=============================================================================
"""

import os
import sys
import json
import uuid
import datetime
import asyncio
from typing import Optional, Dict, Any, List

import aiohttp
import aiosqlite
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands

# ---------------------------------------------------------------------------
# 1. CARGA DE CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ---------------------------------------------------------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ROBLOX_COOKIE = os.getenv("ROBLOX_COOKIE", "")
GUILD_ID = os.getenv("GUILD_ID", None)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DB_PATH = os.path.join(os.path.dirname(__file__), "repcl_bot.db")

def load_config() -> dict:
    default_config = {
        "guild_id": 0,
        "roblox_group_id": 12345678,
        "audit_channel_id": 0,
        "verification_channel_id": 0,
        "citizen_role_id": 0,
        "blacklist_role_id": 0,
        "admin_role_ids": [],
        "server_icon_url": "https://cdn.discordapp.com/attachments/1550625911395852399/1551238506519597148/content.png",
        "rank_mappings": [
            {"roblox_rank_id": 1, "roblox_role_name": "Soldado Conscripto", "discord_role_id": 0},
            {"roblox_rank_id": 2, "roblox_role_name": "Soldado Profesional", "discord_role_id": 0},
            {"roblox_rank_id": 3, "roblox_role_name": "Cabo", "discord_role_id": 0},
            {"roblox_rank_id": 4, "roblox_role_name": "Cabo Segundo", "discord_role_id": 0},
            {"roblox_rank_id": 5, "roblox_role_name": "Cabo Primero", "discord_role_id": 0},
            {"roblox_rank_id": 6, "roblox_role_name": "Sargento Segundo", "discord_role_id": 0},
            {"roblox_rank_id": 7, "roblox_role_name": "Sargento Primero", "discord_role_id": 0},
            {"roblox_rank_id": 8, "roblox_role_name": "Suboficial", "discord_role_id": 0},
            {"roblox_rank_id": 9, "roblox_role_name": "Suboficial Mayor", "discord_role_id": 0},
            {"roblox_rank_id": 10, "roblox_role_name": "Subteniente", "discord_role_id": 0},
            {"roblox_rank_id": 11, "roblox_role_name": "Teniente", "discord_role_id": 0},
            {"roblox_rank_id": 12, "roblox_role_name": "Capitán", "discord_role_id": 0},
            {"roblox_rank_id": 13, "roblox_role_name": "Mayor", "discord_role_id": 0},
            {"roblox_rank_id": 14, "roblox_role_name": "Teniente Coronel", "discord_role_id": 0},
            {"roblox_rank_id": 15, "roblox_role_name": "Coronel", "discord_role_id": 0},
            {"roblox_rank_id": 255, "roblox_role_name": "General de Ejército", "discord_role_id": 0}
        ],
        "suspicious_keywords": [
            "raid", "raideo", "nuke", "dox", "doxxeo", "iplogger", 
            "grabify", "leaks", "filtracion", "token grabber", "nitro gratis", "free robux", "exploit"
        ]
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_config.update(data)
        except Exception as e:
            print(f"[!] Error leyendo config.json: {e}. Usando configuración por defecto.")
    return default_config

config = load_config()

# ---------------------------------------------------------------------------
# 2. CLIENTE DE API DE ROBLOX ASÍNCRONO
# ---------------------------------------------------------------------------
class RobloxClient:
    def __init__(self, cookie: str):
        self.cookie = cookie
        self.csrf_token: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"User-Agent": "REPCL-DiscordBot/1.0"}
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def _get_auth_headers(self) -> Dict[str, str]:
        headers = {
            "Cookie": f".ROBLOSECURITY={self.cookie}",
            "Content-Type": "application/json"
        }
        if self.csrf_token:
            headers["x-csrf-token"] = self.csrf_token
        return headers

    async def fetch_csrf_token(self) -> Optional[str]:
        session = await self.get_session()
        headers = {"Cookie": f".ROBLOSECURITY={self.cookie}"}
        async with session.post("https://auth.roblox.com/v2/login", headers=headers) as resp:
            token = resp.headers.get("x-csrf-token")
            if token:
                self.csrf_token = token
                return token
        return None

    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        session = await self.get_session()
        payload = {"usernames": [username], "excludeBannedUsers": False}
        async with session.post("https://users.roblox.com/v1/usernames/users", json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("data"):
                    return data["data"][0]
        return None

    async def get_user_thumbnail(self, user_id: int) -> str:
        session = await self.get_session()
        url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png&isCircular=false"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("data") and len(data["data"]) > 0:
                    return data["data"][0].get("imageUrl", "")
        return "https://www.roblox.com/images/default-avatar.png"

    async def get_user_group_role(self, user_id: int, group_id: int) -> Optional[Dict[str, Any]]:
        session = await self.get_session()
        url = f"https://groups.roblox.com/v1/users/{user_id}/groups/roles"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                for group_entry in data.get("data", []):
                    if group_entry.get("group", {}).get("id") == group_id:
                        return {
                            "in_group": True,
                            "group_name": group_entry["group"]["name"],
                            "rank": group_entry["role"]["rank"],
                            "role_id": group_entry["role"]["id"],
                            "role_name": group_entry["role"]["name"]
                        }
        return {"in_group": False, "rank": 0, "role_id": None, "role_name": None}

    async def get_group_roles(self, group_id: int) -> List[Dict[str, Any]]:
        session = await self.get_session()
        url = f"https://groups.roblox.com/v1/groups/{group_id}/roles"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("roles", [])
        return []

    async def set_user_rank(self, group_id: int, user_id: int, target_role_id: int) -> Dict[str, Any]:
        if not self.cookie:
            return {"success": False, "error": "No se ha configurado la cookie de Roblox (ROBLOX_COOKIE en .env)"}

        session = await self.get_session()
        url = f"https://groups.roblox.com/v1/groups/{group_id}/users/{user_id}"
        payload = {"roleId": target_role_id}

        for attempt in range(2):
            headers = await self._get_auth_headers()
            async with session.patch(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    return {"success": True}
                elif resp.status == 403:
                    new_token = resp.headers.get("x-csrf-token")
                    if new_token:
                        self.csrf_token = new_token
                        continue
                    else:
                        fetched = await self.fetch_csrf_token()
                        if fetched:
                            continue
                try:
                    error_data = await resp.json()
                    err_msg = error_data.get("errors", [{}])[0].get("message", f"HTTP {resp.status}")
                except Exception:
                    err_msg = f"HTTP {resp.status}"
                return {"success": False, "error": err_msg}

        return {"success": False, "error": "No se pudo renovar el token CSRF de Roblox."}

roblox_client = RobloxClient(ROBLOX_COOKIE)

# ---------------------------------------------------------------------------
# 3. BASE DE DATOS SQLITE ASÍNCRONA
# ---------------------------------------------------------------------------
async def init_database():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS linked_users (
                discord_id INTEGER PRIMARY KEY,
                roblox_id INTEGER NOT NULL,
                roblox_username TEXT NOT NULL,
                linked_at TEXT NOT NULL,
                current_rank_id INTEGER DEFAULT 0,
                current_rank_name TEXT DEFAULT ''
            )
        """)
        await db.execute("""
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
            )
        """)
        await db.execute("""
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
                active INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id INTEGER PRIMARY KEY,
                roblox_id INTEGER DEFAULT 0,
                reason TEXT NOT NULL,
                admin_id INTEGER NOT NULL,
                admin_tag TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()

async def get_linked_account(discord_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM linked_users WHERE discord_id = ?", (discord_id,)) as cursor:
            return await cursor.fetchone()

async def save_linked_account(discord_id: int, roblox_id: int, username: str, rank_id: int, rank_name: str):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO linked_users (discord_id, roblox_id, roblox_username, linked_at, current_rank_id, current_rank_name)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                roblox_id = excluded.roblox_id,
                roblox_username = excluded.roblox_username,
                linked_at = excluded.linked_at,
                current_rank_id = excluded.current_rank_id,
                current_rank_name = excluded.current_rank_name
        """, (discord_id, roblox_id, username, now, rank_id, rank_name))
        await db.commit()

async def log_rank_change(discord_id: int, roblox_id: int, old_rank: str, new_rank: str, admin_id: int, admin_name: str, action_type: str):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO rank_history (discord_id, roblox_id, old_rank, new_rank, admin_id, admin_name, timestamp, action_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (discord_id, roblox_id, old_rank, new_rank, admin_id, admin_name, now, action_type))
        await db.commit()

async def add_sanction(user_id: int, user_tag: str, admin_id: int, admin_tag: str, s_type: str, reason: str, duration: str) -> str:
    s_id = f"REPCL-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO sanctions (id, user_id, user_tag, admin_id, admin_tag, sanction_type, reason, duration, created_at, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (s_id, user_id, user_tag, admin_id, admin_tag, s_type, reason, duration, now))
        await db.commit()
    return s_id

async def is_blacklisted(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM blacklist WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def set_blacklist(user_id: int, roblox_id: int, reason: str, admin_id: int, admin_tag: str):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO blacklist (user_id, roblox_id, reason, admin_id, admin_tag, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, roblox_id, reason, admin_id, admin_tag, now))
        await db.commit()

async def remove_blacklist(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount > 0

# ---------------------------------------------------------------------------
# 4. BOT CONFIGURACIÓN Y PERMISOS
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class RepclBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        await init_database()
        self.add_view(VerifyPersistentView())

bot = RepclBot()

def is_repcl_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.id == member.guild.owner_id:
        return True
    admin_roles = config.get("admin_role_ids", [])
    for role in member.roles:
        if role.id in admin_roles:
            return True
    return False

async def send_audit_log(guild: discord.Guild, embed: discord.Embed):
    audit_channel_id = config.get("audit_channel_id")
    if not audit_channel_id:
        return
    channel = guild.get_channel(audit_channel_id)
    if channel and isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# 5. SISTEMA DE VERIFICACIÓN ROBLOX ↔ DISCORD
# ---------------------------------------------------------------------------
async def apply_roles_for_user(member: discord.Member, roblox_info: dict, group_role: dict):
    guild = member.guild
    citizen_role_id = config.get("citizen_role_id")
    rank_mappings = config.get("rank_mappings", [])
    military_role_ids = {m["discord_role_id"] for m in rank_mappings if m.get("discord_role_id")}
    
    roles_to_remove = []
    roles_to_add = []

    bl = await is_blacklisted(member.id)
    if bl:
        bl_role_id = config.get("blacklist_role_id")
        if bl_role_id:
            bl_role = guild.get_role(bl_role_id)
            if bl_role and bl_role not in member.roles:
                roles_to_add.append(bl_role)
        for r in member.roles:
            if r.id in military_role_ids or r.id == citizen_role_id:
                roles_to_remove.append(r)
        if roles_to_remove or roles_to_add:
            try:
                await member.remove_roles(*roles_to_remove, reason="Usuario en Blacklist REPCL")
                await member.add_roles(*roles_to_add, reason="Usuario en Blacklist REPCL")
            except Exception:
                pass
        return "blacklist", "Blacklist REPCL"

    if not group_role.get("in_group"):
        if citizen_role_id:
            cit_role = guild.get_role(citizen_role_id)
            if cit_role and cit_role not in member.roles:
                roles_to_add.append(cit_role)
        for r in member.roles:
            if r.id in military_role_ids:
                roles_to_remove.append(r)
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="No pertenece al grupo")
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Verificado: Ciudadano Chileno")
        return "citizen", "🇨🇱 Ciudadano Chileno"
    else:
        user_rank_num = group_role.get("rank", 0)
        target_role_id = None
        matched_name = group_role.get("role_name", "Militar")

        for mapping in rank_mappings:
            if mapping.get("roblox_rank_id") == user_rank_num:
                target_role_id = mapping.get("discord_role_id")
                matched_name = mapping.get("roblox_role_name", matched_name)
                break

        if citizen_role_id:
            c_role = guild.get_role(citizen_role_id)
            if c_role and c_role in member.roles:
                roles_to_remove.append(c_role)

        for r in member.roles:
            if r.id in military_role_ids and r.id != target_role_id:
                roles_to_remove.append(r)

        if target_role_id:
            t_role = guild.get_role(target_role_id)
            if t_role and t_role not in member.roles:
                roles_to_add.append(t_role)

        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Actualización Militar REPCL")
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason=f"Rango Militar: {matched_name}")

        return "military", matched_name


class VerificationModal(discord.ui.Modal, title="🇨🇱 Verificación Roblox • Ejército de Chile"):
    roblox_username = discord.ui.TextInput(
        label="Tu Usuario Exacto de Roblox",
        placeholder="Ej: Carin_Ejercito / JuanPerezCL",
        min_length=3,
        max_length=30,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        username = self.roblox_username.value.strip()

        bl = await is_blacklisted(interaction.user.id)
        if bl:
            embed_bl = discord.Embed(
                title="⛔ ACCESO DENEGADO • BLACKLIST",
                description=f"Tu cuenta se encuentra registrada en la **Blacklist Oficial de REPCL**.\n\n"
                            f"**Motivo:** {bl['reason']}\n"
                            f"**Fecha:** `{bl['created_at']}`\n"
                            f"**Administrador:** `{bl['admin_tag']}`",
                color=0xd90429
            )
            await interaction.followup.send(embed=embed_bl, ephemeral=True)
            return

        user_data = await roblox_client.get_user_by_username(username)
        if not user_data:
            await interaction.followup.send(
                f"❌ No se encontró ningún usuario en Roblox con el nombre **`{username}`**.",
                ephemeral=True
            )
            return

        roblox_id = user_data["id"]
        exact_username = user_data["name"]

        group_id = config.get("roblox_group_id", 0)
        group_info = await roblox_client.get_user_group_role(roblox_id, group_id)
        avatar_url = await roblox_client.get_user_thumbnail(roblox_id)

        role_type, role_assigned_name = await apply_roles_for_user(
            interaction.user,
            {"id": roblox_id, "name": exact_username},
            group_info
        )

        await save_linked_account(
            interaction.user.id,
            roblox_id,
            exact_username,
            group_info.get("rank", 0),
            role_assigned_name
        )

        embed = discord.Embed(
            title="🇨🇱 ¡CUENTA VINCULADA Y VERIFICADA CON ÉXITO!",
            description=f"Tu cuenta de Discord ha sido enlazada oficialmente a tu perfil de **Roblox**.",
            color=0x0039A6 if role_type == "citizen" else 0xD52B1E,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="👤 Usuario de Roblox", value=f"**[{exact_username}](https://www.roblox.com/users/{roblox_id}/profile)**", inline=True)
        embed.add_field(name="🆔 ID de Roblox", value=f"`{roblox_id}`", inline=True)
        
        if role_type == "citizen":
            embed.add_field(name="🛡️ Estado", value="❌ **No perteneces al Grupo de Roblox**\nSe te asignó el rol **🇨🇱 Ciudadano Chileno**.", inline=False)
        else:
            embed.add_field(name="🎖️ Rango Detectado", value=f"✅ **{group_info.get('role_name')}** (Nivel `{group_info.get('rank')}`)", inline=True)
            embed.add_field(name="🏷️ Rol en Discord", value=f"**@{role_assigned_name}**", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)


class VerifyPersistentView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔗 Verificar Roblox", style=discord.ButtonStyle.danger, custom_id="repcl_btn_verify_roblox", emoji="🇨🇱")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerificationModal())

    @discord.ui.button(label="🔄 Actualizar Rango", style=discord.ButtonStyle.secondary, custom_id="repcl_btn_refresh_rank", emoji="🎖️")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        linked = await get_linked_account(interaction.user.id)
        if not linked:
            await interaction.followup.send("❌ No tienes cuenta vinculada. Pulsa **🔗 Verificar Roblox**.", ephemeral=True)
            return
        group_id = config.get("roblox_group_id", 0)
        group_info = await roblox_client.get_user_group_role(linked["roblox_id"], group_id)
        role_type, role_assigned = await apply_roles_for_user(interaction.user, {"id": linked["roblox_id"], "name": linked["roblox_username"]}, group_info)
        await interaction.followup.send(f"✅ Rango sincronizado: **{role_assigned}**", ephemeral=True)


# ---------------------------------------------------------------------------
# 6. COMANDOS SLASH (ASCENDER, DESCENDER, RANGO, SANCIONES, BLACKLIST)
# ---------------------------------------------------------------------------
@bot.tree.command(name="panel-verificacion", description="[ADMIN] Despliega el panel oficial de verificación")
async def cmd_panel(interaction: discord.Interaction):
    if not is_repcl_admin(interaction.user):
        await interaction.response.send_message("❌ Sin permisos.", ephemeral=True)
        return
    embed = discord.Embed(
        title="🇨🇱 REPCL • EJÉRCITO DE CHILE | VERIFICACIÓN OFICIAL",
        description="Presiona el botón rojo **🔗 Verificar Roblox** abajo para vincular tu cuenta.\n\n• Si perteneces al grupo de Roblox recibes tu rango militar (@Soldado, @Cabo, etc.).\n• Si no perteneces recibes **🇨🇱 Ciudadano Chileno**.",
        color=0xD52B1E
    )
    embed.set_thumbnail(url=config.get("server_icon_url"))
    await interaction.channel.send(embed=embed, view=VerifyPersistentView())
    await interaction.response.send_message("✅ Panel enviado.", ephemeral=True)


@bot.tree.command(name="ascender", description="Asciende a un usuario en Roblox y actualiza su rol en Discord")
@app_commands.describe(usuario="Miembro a ascender", nuevo_rango="Nombre o número de rango en Roblox")
async def cmd_ascender(interaction: discord.Interaction, usuario: discord.Member, nuevo_rango: str):
    await interaction.response.defer()
    if not is_repcl_admin(interaction.user):
        await interaction.followup.send("❌ Permisos insuficientes.")
        return
    linked = await get_linked_account(usuario.id)
    if not linked:
        await interaction.followup.send(f"❌ {usuario.mention} no tiene cuenta vinculada.")
        return

    group_id = config.get("roblox_group_id", 0)
    roles_list = await roblox_client.get_group_roles(group_id)
    target_role = next((r for r in roles_list if str(r.get("rank")) == nuevo_rango or r.get("name", "").lower() == nuevo_rango.lower()), None)
    if not target_role:
        await interaction.followup.send(f"❌ Rango `{nuevo_rango}` no válido en el grupo de Roblox.")
        return

    current_role = await roblox_client.get_user_group_role(linked["roblox_id"], group_id)
    old_rank = current_role.get("role_name", "Sin Rango")

    result = await roblox_client.set_user_rank(group_id, linked["roblox_id"], target_role["id"])
    if not result.get("success"):
        await interaction.followup.send(f"⚠️ Error en Roblox: {result.get('error')}")
        return

    await apply_roles_for_user(usuario, {"id": linked["roblox_id"], "name": linked["roblox_username"]}, {"in_group": True, "rank": target_role["rank"], "role_id": target_role["id"], "role_name": target_role["name"]})
    await log_rank_change(usuario.id, linked["roblox_id"], old_rank, target_role["name"], interaction.user.id, str(interaction.user), "ASCENSO")

    embed = discord.Embed(title="🎖️ ASCENSO MILITAR CONCEDIDO", description=f"{usuario.mention} ha sido ascendido a **{target_role['name']}** en Roblox y Discord.", color=0x2ecc71)
    await interaction.followup.send(embed=embed)
    await send_audit_log(interaction.guild, embed)


@bot.tree.command(name="descender", description="Desciende a un usuario en Roblox y actualiza Discord")
@app_commands.describe(usuario="Miembro a descender", nuevo_rango="Rango inferior")
async def cmd_descender(interaction: discord.Interaction, usuario: discord.Member, nuevo_rango: str):
    await interaction.response.defer()
    if not is_repcl_admin(interaction.user):
        await interaction.followup.send("❌ Permisos insuficientes.")
        return
    linked = await get_linked_account(usuario.id)
    if not linked:
        await interaction.followup.send(f"❌ {usuario.mention} no tiene cuenta vinculada.")
        return
    group_id = config.get("roblox_group_id", 0)
    roles_list = await roblox_client.get_group_roles(group_id)
    target_role = next((r for r in roles_list if str(r.get("rank")) == nuevo_rango or r.get("name", "").lower() == nuevo_rango.lower()), None)
    if not target_role:
        await interaction.followup.send("❌ Rango no encontrado.")
        return

    current_role = await roblox_client.get_user_group_role(linked["roblox_id"], group_id)
    old_rank = current_role.get("role_name", "Sin Rango")

    result = await roblox_client.set_user_rank(group_id, linked["roblox_id"], target_role["id"])
    if not result.get("success"):
        await interaction.followup.send(f"⚠️ Error en Roblox: {result.get('error')}")
        return

    await apply_roles_for_user(usuario, {"id": linked["roblox_id"], "name": linked["roblox_username"]}, {"in_group": True, "rank": target_role["rank"], "role_id": target_role["id"], "role_name": target_role["name"]})
    await log_rank_change(usuario.id, linked["roblox_id"], old_rank, target_role["name"], interaction.user.id, str(interaction.user), "DESCENSO")

    embed = discord.Embed(title="📉 DESCENSO MILITAR APLICADO", description=f"{usuario.mention} ha sido descendido a **{target_role['name']}**.", color=0xe67e22)
    await interaction.followup.send(embed=embed)
    await send_audit_log(interaction.guild, embed)


@bot.tree.command(name="rango", description="Muestra el rango actual de un militar")
async def cmd_rango(interaction: discord.Interaction, usuario: Optional[discord.Member] = None):
    target = usuario or interaction.user
    await interaction.response.defer()
    linked = await get_linked_account(target.id)
    if not linked:
        await interaction.followup.send(f"❌ {target.mention} no tiene cuenta de Roblox vinculada.")
        return
    group_info = await roblox_client.get_user_group_role(linked["roblox_id"], config.get("roblox_group_id", 0))
    thumb = await roblox_client.get_user_thumbnail(linked["roblox_id"])

    embed = discord.Embed(title="🇨🇱 FICHA MILITAR • REPCL", color=0xD52B1E)
    embed.set_thumbnail(url=thumb)
    embed.add_field(name="Discord", value=f"{target.mention} (`{target.id}`)", inline=True)
    embed.add_field(name="Roblox", value=f"[{linked['roblox_username']}](https://www.roblox.com/users/{linked['roblox_id']}/profile)", inline=True)
    embed.add_field(name="Rango en Roblox", value=f"**{group_info.get('role_name', 'Civil')}** (Nivel `{group_info.get('rank', 0)}`)", inline=False)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="historial-rangos", description="Muestra el historial de cambios de rango")
async def cmd_historial(interaction: discord.Interaction, usuario: discord.Member):
    await interaction.response.defer()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM rank_history WHERE discord_id = ? ORDER BY id DESC LIMIT 10", (usuario.id,)) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await interaction.followup.send(f"📋 Sin registros de cambios de rango para {usuario.mention}.")
        return
    embed = discord.Embed(title=f"📋 HISTORIAL DE RANGOS • {usuario.display_name}", color=0x3498db)
    for r in rows:
        embed.add_field(name=f"{r['action_type']}: {r['old_rank']} ➔ {r['new_rank']}", value=f"Admin: `{r['admin_name']}` | Fecha: `{r['timestamp']}`", inline=False)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="sancionar", description="Aplica una sanción configurable")
@app_commands.choices(tipo=[
    app_commands.Choice(name="⚠️ Advertencia", value="⚠️ Advertencia"),
    app_commands.Choice(name="🔇 Silenciar", value="🔇 Silenciar"),
    app_commands.Choice(name="⏸️ Suspensión temporal", value="⏸️ Suspensión temporal"),
    app_commands.Choice(name="🚫 Blacklist temporal", value="🚫 Blacklist temporal"),
    app_commands.Choice(name="⛔ Blacklist permanente", value="⛔ Blacklist permanente"),
    app_commands.Choice(name="🚷 Expulsión", value="🚷 Expulsión")
])
async def cmd_sancionar(interaction: discord.Interaction, usuario: discord.Member, tipo: app_commands.Choice[str], motivo: str, duracion: Optional[str] = "N/A"):
    if not is_repcl_admin(interaction.user):
        await interaction.response.send_message("❌ Sin permisos.", ephemeral=True)
        return
    await interaction.response.defer()
    s_id = await add_sanction(usuario.id, str(usuario), interaction.user.id, str(interaction.user), tipo.value, motivo, duracion)
    if "Blacklist" in tipo.value:
        linked = await get_linked_account(usuario.id)
        await set_blacklist(usuario.id, linked["roblox_id"] if linked else 0, motivo, interaction.user.id, str(interaction.user))
        await apply_roles_for_user(usuario, {}, {"in_group": False})
    elif tipo.value == "🚷 Expulsión":
        try: await usuario.kick(reason=motivo)
        except Exception: pass

    embed = discord.Embed(title="⚖️ SANCIÓN DISCIPLINARIA", color=0xd63031)
    embed.add_field(name="ID", value=f"`{s_id}`", inline=True)
    embed.add_field(name="Usuario", value=usuario.mention, inline=True)
    embed.add_field(name="Tipo", value=tipo.value, inline=True)
    embed.add_field(name="Motivo", value=motivo, inline=False)
    await interaction.followup.send(embed=embed)
    await send_audit_log(interaction.guild, embed)


@bot.tree.command(name="blacklist", description="Muestra usuarios en Blacklist")
async def cmd_bl(interaction: discord.Interaction):
    if not is_repcl_admin(interaction.user):
        await interaction.response.send_message("❌ Sin permisos.", ephemeral=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM blacklist LIMIT 15") as cursor:
            rows = await cursor.fetchall()
    embed = discord.Embed(title="⛔ BLACKLIST REPCL", description=f"Total: {len(rows)} vetados", color=0x2c3e50)
    for r in rows:
        embed.add_field(name=f"ID: {r['user_id']}", value=f"Motivo: {r['reason']}\nAdmin: `{r['admin_tag']}`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# 7. EVENTOS DE AUDITORÍA Y ALERTAS AUTOMÁTICAS
# ---------------------------------------------------------------------------
@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild: return
    embed = discord.Embed(title="🗑️ AUDITORÍA • MENSAJE ELIMINADO", description=f"Mensaje borrado en {message.channel.mention} conservado para administradores.", color=0xe74c3c)
    embed.set_author(name=f"{message.author} ({message.author.id})", icon_url=message.author.display_avatar.url)
    embed.add_field(name="Contenido Guardado", value=message.content or "*Sin texto*", inline=False)
    await send_audit_log(message.guild, embed)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or before.content == after.content or not before.guild: return
    embed = discord.Embed(title="✏️ AUDITORÍA • MENSAJE EDITADO", color=0xf39c12)
    embed.add_field(name="Anterior", value=before.content[:1000] or "*Vacío*", inline=False)
    embed.add_field(name="Nuevo", value=after.content[:1000] or "*Vacío*", inline=False)
    await send_audit_log(before.guild, embed)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild: return
    content_lower = message.content.lower()
    found = [kw for kw in config.get("suspicious_keywords", []) if kw in content_lower]
    if found:
        alert = discord.Embed(title="🚨 ALERTA DE SEGURIDAD • CONDUCTA SOSPECHOSA", color=0xc0392b)
        alert.add_field(name="Palabras", value=f"`{', '.join(found)}`", inline=True)
        alert.add_field(name="Canal", value=message.channel.mention, inline=True)
        alert.add_field(name="Contenido", value=message.content[:1000], inline=False)
        await send_audit_log(message.guild, alert)
    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"🇨🇱 REPCL Bot conectado como {bot.user}")
    await bot.tree.sync()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

