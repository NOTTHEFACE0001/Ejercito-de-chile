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
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
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
        "roblox_group_id": 4683210,
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
        """Obtiene un token CSRF válido haciendo una petición de handshake a auth.roblox.com"""
        session = await self.get_session()
        headers = {"Cookie": f".ROBLOSECURITY={self.cookie}"}
        async with session.post("https://auth.roblox.com/v2/login", headers=headers) as resp:
            token = resp.headers.get("x-csrf-token")
            if token:
                self.csrf_token = token
                return token
        return None

    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Busca el ID de Roblox a través del nombre de usuario"""
        session = await self.get_session()
        payload = {"usernames": [username], "excludeBannedUsers": False}
        async with session.post("https://users.roblox.com/v1/usernames/users", json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("data"):
                    return data["data"][0]
        return None

    async def get_user_thumbnail(self, user_id: int) -> str:
        """Obtiene el avatar / foto de perfil del usuario de Roblox"""
        session = await self.get_session()
        url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png&isCircular=false"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("data") and len(data["data"]) > 0:
                    return data["data"][0].get("imageUrl", "")
        return "https://www.roblox.com/images/default-avatar.png"

    async def get_user_group_role(self, user_id: int, group_id: int) -> Optional[Dict[str, Any]]:
        """Consulta el rango del usuario en el grupo especificado"""
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
                            "rank": group_entry["role"]["rank"],       # e.g. 1 a 255
                            "role_id": group_entry["role"]["id"],      # ID interno del rol en Roblox
                            "role_name": group_entry["role"]["name"]   # e.g. 'Soldado Conscripto'
                        }
        return {"in_group": False, "rank": 0, "role_id": None, "role_name": None}

    async def get_group_roles(self, group_id: int) -> List[Dict[str, Any]]:
        """Devuelve la lista de roles y rangos disponibles en el grupo de Roblox"""
        session = await self.get_session()
        url = f"https://groups.roblox.com/v1/groups/{group_id}/roles"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("roles", [])
        return []

    async def set_user_rank(self, group_id: int, user_id: int, target_role_id: int) -> Dict[str, Any]:
        """Cambia el rango de un usuario dentro del grupo de Roblox usando la cookie administrativa"""
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
                    # Token CSRF expirado o ausente
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

# Funciones de consulta a la DB
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
        # Registrar vista persistente para el botón de verificación
        self.add_view(VerifyPersistentView())
        print("[+] Base de datos SQLite inicializada.")
        print("[+] Vista persistente de verificación registrada.")

bot = RepclBot()

def is_repcl_admin(member: discord.Member) -> bool:
    """Comprueba si el miembro tiene permisos de Administrador en Discord o posee un rol autorizado"""
    if member.guild_permissions.administrator or member.id == member.guild.owner_id:
        return True
    admin_roles = config.get("admin_role_ids", [])
    for role in member.roles:
        if role.id in admin_roles:
            return True
    return False

async def send_audit_log(guild: discord.Guild, embed: discord.Embed):
    """Envía un embed al canal de auditoría configurado"""
    audit_channel_id = config.get("audit_channel_id")
    if not audit_channel_id:
        return
    channel = guild.get_channel(audit_channel_id)
    if channel and isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[!] No se pudo enviar mensaje a canal de auditoría: {e}")

# ---------------------------------------------------------------------------
# 5. SISTEMA DE VERIFICACIÓN ROBLOX ↔ DISCORD
# ---------------------------------------------------------------------------
async def apply_roles_for_user(member: discord.Member, roblox_info: dict, group_role: dict):
    """Asigna o revoca los roles correspondientes de Discord basándose en el rango de Roblox"""
    guild = member.guild
    citizen_role_id = config.get("citizen_role_id")
    rank_mappings = config.get("rank_mappings", [])

    # Obtener todos los IDs de roles militares configurados
    military_role_ids = {m["discord_role_id"] for m in rank_mappings if m.get("discord_role_id")}
    
    roles_to_remove = []
    roles_to_add = []

    # Si está en blacklist
    bl = await is_blacklisted(member.id)
    if bl:
        bl_role_id = config.get("blacklist_role_id")
        if bl_role_id:
            bl_role = guild.get_role(bl_role_id)
            if bl_role and bl_role not in member.roles:
                roles_to_add.append(bl_role)
        # Quitar todos los demás roles militares y ciudadano
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
        # NO Pertenece al grupo -> Asignar Rol "🇨🇱 Ciudadano Chileno"
        if citizen_role_id:
            cit_role = guild.get_role(citizen_role_id)
            if cit_role and cit_role not in member.roles:
                roles_to_add.append(cit_role)
        
        # Quitar roles militares si los tenía
        for r in member.roles:
            if r.id in military_role_ids:
                roles_to_remove.append(r)
        
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="No pertenece al grupo Ejército de Chile")
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Verificado: Ciudadano Chileno")
        
        # Opcional: Actualizar apodo a su nombre de Roblox
        try:
            await member.edit(nick=f"[CIV] {roblox_info['name']}")
        except Exception:
            pass

        return "citizen", "🇨🇱 Ciudadano Chileno"

    else:
        # SÍ Pertenece al grupo -> Detectar su rango de Roblox y asignar rol militar
        user_rank_num = group_role.get("rank", 0)
        target_role_id = None
        matched_name = group_role.get("role_name", "Militar")

        for mapping in rank_mappings:
            if mapping.get("roblox_rank_id") == user_rank_num:
                target_role_id = mapping.get("discord_role_id")
                matched_name = mapping.get("roblox_role_name", matched_name)
                break

        # Quitar rol de ciudadano si lo tenía
        if citizen_role_id:
            c_role = guild.get_role(citizen_role_id)
            if c_role and c_role in member.roles:
                roles_to_remove.append(c_role)

        # Quitar roles militares viejos que no correspondan
        for r in member.roles:
            if r.id in military_role_ids and r.id != target_role_id:
                roles_to_remove.append(r)

        # Agregar el rol militar correspondiente
        if target_role_id:
            t_role = guild.get_role(target_role_id)
            if t_role and t_role not in member.roles:
                roles_to_add.append(t_role)

        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Actualización de Rango Militar REPCL")
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason=f"Asignación de Rango Militar: {matched_name}")

        try:
            await member.edit(nick=f"[{matched_name}] {roblox_info['name']}")
        except Exception:
            pass

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

        # 1. Verificar si está en Blacklist
        bl = await is_blacklisted(interaction.user.id)
        if bl:
            embed_bl = discord.Embed(
                title="⛔ ACCESO DENEGADO • BLACKLIST",
                description=f"Tu cuenta se encuentra registrada en la **Blacklist Oficial de REPCL**.\n\n"
                            f"**Motivo:** {bl['reason']}\n"
                            f"**Fecha:** `{bl['created_at']}`\n"
                            f"**Administrador:** `{bl['admin_tag']}`\n\n"
                            f"No puedes verificar cuentas ni acceder a las funciones del Ejército de Chile.",
                color=0xd90429
            )
            await interaction.followup.send(embed=embed_bl, ephemeral=True)
            return

        # 2. Consultar perfil en Roblox
        user_data = await roblox_client.get_user_by_username(username)
        if not user_data:
            await interaction.followup.send(
                f"❌ No se encontró ningún usuario en Roblox con el nombre **`{username}`**. Revisa la ortografía e inténtalo de nuevo.",
                ephemeral=True
            )
            return

        roblox_id = user_data["id"]
        exact_username = user_data["name"]

        # 3. Consultar pertenencia al grupo de Roblox
        group_id = config.get("roblox_group_id", 0)
        group_info = await roblox_client.get_user_group_role(roblox_id, group_id)
        avatar_url = await roblox_client.get_user_thumbnail(roblox_id)

        # 4. Asignar roles en Discord
        role_type, role_assigned_name = await apply_roles_for_user(
            interaction.user,
            {"id": roblox_id, "name": exact_username},
            group_info
        )

        # 5. Guardar vinculación en Base de Datos SQLite
        await save_linked_account(
            interaction.user.id,
            roblox_id,
            exact_username,
            group_info.get("rank", 0),
            role_assigned_name
        )

        # 6. Embed de respuesta al usuario
        embed = discord.Embed(
            title="🇨🇱 ¡CUENTA VINCULADA Y VERIFICADA CON ÉXITO!",
            description=f"Tu cuenta de Discord ha sido enlazada oficialmente a tu perfil de **Roblox** en la base de datos de **REPCL • Ejército de Chile**.",
            color=0x0039A6 if role_type == "citizen" else 0xD52B1E,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="👤 Usuario de Roblox", value=f"**[{exact_username}](https://www.roblox.com/users/{roblox_id}/profile)**", inline=True)
        embed.add_field(name="🆔 ID de Roblox", value=f"`{roblox_id}`", inline=True)
        
        if role_type == "citizen":
            embed.add_field(
                name="🛡️ Estado de Afiliación", 
                value="❌ **No perteneces al Grupo de Roblox**\nSe te ha otorgado el rol **🇨🇱 Ciudadano Chileno**.", 
                inline=False
            )
            embed.add_field(
                name="📌 ¿Quieres alistarte?", 
                value=f"Únete al grupo de Roblox del Ejército de Chile y pulsa nuevamente **🔗 Verificar Roblox** para recibir tu rango militar automáticamente.",
                inline=False
            )
        else:
            embed.add_field(
                name="🎖️ Rango Militar Detectado", 
                value=f"✅ **{group_info.get('role_name')}** (Nivel `{group_info.get('rank')}`)", 
                inline=True
            )
            embed.add_field(name="🏷️ Rol de Discord Asignado", value=f"**@{role_assigned_name}**", inline=True)

        embed.set_footer(text="REPCL • Sistema de Verificación Oficial", icon_url=config.get("server_icon_url"))
        await interaction.followup.send(embed=embed, ephemeral=True)

        # 7. Registrar en el canal de auditoría
        audit_embed = discord.Embed(
            title="📋 AUDITORÍA • NUEVA VERIFICACIÓN ROBLOX",
            color=0x2ecc71,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        audit_embed.set_thumbnail(url=avatar_url)
        audit_embed.add_field(name="Discord", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=True)
        audit_embed.add_field(name="Roblox", value=f"[{exact_username}](https://www.roblox.com/users/{roblox_id}/profile)", inline=True)
        audit_embed.add_field(name="Rango / Rol", value=f"`{role_assigned_name}`", inline=True)
        await send_audit_log(interaction.guild, audit_embed)


class VerifyPersistentView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔗 Verificar Roblox", 
        style=discord.ButtonStyle.danger, 
        custom_id="repcl_btn_verify_roblox",
        emoji="🇨🇱"
    )
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerificationModal())

    @discord.ui.button(
        label="🔄 Actualizar Rango",
        style=discord.ButtonStyle.secondary,
        custom_id="repcl_btn_refresh_rank",
        emoji="🎖️"
    )
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        linked = await get_linked_account(interaction.user.id)
        if not linked:
            await interaction.followup.send(
                "❌ Aún no tienes ninguna cuenta de Roblox vinculada. Pulsa **🔗 Verificar Roblox** primero.",
                ephemeral=True
            )
            return

        group_id = config.get("roblox_group_id", 0)
        group_info = await roblox_client.get_user_group_role(linked["roblox_id"], group_id)
        
        role_type, role_assigned = await apply_roles_for_user(
            interaction.user,
            {"id": linked["roblox_id"], "name": linked["roblox_username"]},
            group_info
        )

        await save_linked_account(
            interaction.user.id,
            linked["roblox_id"],
            linked["roblox_username"],
            group_info.get("rank", 0),
            role_assigned
        )

        await interaction.followup.send(
            f"✅ Sincronización completa con Roblox:\n- **Usuario:** `{linked['roblox_username']}`\n- **Rol Asignado:** `{role_assigned}`",
            ephemeral=True
        )

# ---------------------------------------------------------------------------
# 6. COMANDOS DE ASCENSOS Y GESTIÓN DE RANGOS
# ---------------------------------------------------------------------------
@bot.tree.command(name="panel-verificacion", description="[ADMIN] Despliega el panel oficial de verificación con botón")
async def cmd_panel_verificacion(interaction: discord.Interaction):
    if not is_repcl_admin(interaction.user):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🇨🇱 REPCL • EJÉRCITO DE CHILE | VERIFICACIÓN OFICIAL",
        description=(
            "Bienvenido al servidor oficial de **REPCL • Ejército de Chile**.\n\n"
            "Para acceder a las instalaciones, canales de comunicación y recibir tus roles oficiales, "
            "debes vincular tu cuenta de **Roblox** con Discord.\n\n"
            "**Instrucciones:**\n"
            "1️⃣ Haz clic en el botón rojo **🔗 Verificar Roblox** abajo.\n"
            "2️⃣ Ingresa tu nombre de usuario exacto de Roblox.\n"
            "3️⃣ El sistema comprobará si perteneces al grupo militar:\n"
            "   • **Si perteneces al grupo:** Recibirás tu rango militar correspondiente (`@Soldado`, `@Cabo`, `@Sargento`, etc.).\n"
            "   • **Si no perteneces:** Recibirás el rol **🇨🇱 Ciudadano Chileno**.\n\n"
            "*(Si asciendes en Roblox, puedes pulsar **🔄 Actualizar Rango** en cualquier momento)*"
        ),
        color=0xD52B1E
    )
    embed.set_thumbnail(url=config.get("server_icon_url"))
    embed.set_image(url="https://images.unsplash.com/photo-1579975096649-e773152b04cb?w=1200&auto=format&fit=crop&q=80")
    embed.set_footer(text="Ejército de Chile • Siempre Vencedor, Jamás Vencido", icon_url=config.get("server_icon_url"))

    view = VerifyPersistentView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Panel de verificación enviado correctamente a este canal.", ephemeral=True)


@bot.tree.command(name="ascender", description="Asciende a un usuario en Roblox y actualiza su rol en Discord")
@app_commands.describe(usuario="Miembro a ascender", nuevo_rango="Nombre o número de rango exacto en Roblox")
async def cmd_ascender(interaction: discord.Interaction, usuario: discord.Member, nuevo_rango: str):
    await interaction.response.defer()
    if not is_repcl_admin(interaction.user):
        await interaction.followup.send("❌ No tienes permisos suficientes para realizar ascensos militares.")
        return

    linked = await get_linked_account(usuario.id)
    if not linked:
        await interaction.followup.send(f"❌ El usuario {usuario.mention} no tiene una cuenta de Roblox vinculada.")
        return

    group_id = config.get("roblox_group_id", 0)
    roles_list = await roblox_client.get_group_roles(group_id)
    if not roles_list:
        await interaction.followup.send("❌ No se pudieron obtener los rangos del grupo de Roblox. Revisa el Group ID.")
        return

    # Buscar el rol de destino
    target_role = None
    for r in roles_list:
        if str(r.get("rank")) == nuevo_rango or r.get("name", "").lower() == nuevo_rango.lower():
            target_role = r
            break

    if not target_role:
        valid_roles = ", ".join([f"`{r['name']}` ({r['rank']})" for r in roles_list if r['rank'] > 0])
        await interaction.followup.send(f"❌ Rango **`{nuevo_rango}`** no válido.\n**Rangos disponibles:**\n{valid_roles}")
        return

    # Obtener rango actual del usuario
    current_role = await roblox_client.get_user_group_role(linked["roblox_id"], group_id)
    old_rank_name = current_role.get("role_name", "Sin Rango")

    # Modificar rango en Roblox
    result = await roblox_client.set_user_rank(group_id, linked["roblox_id"], target_role["id"])
    if not result.get("success"):
        await interaction.followup.send(f"⚠️ **Error en Roblox API:** {result.get('error')}\n*(¿La cuenta bot tiene permisos de ascender en el grupo?)*")
        return

    # Actualizar roles en Discord
    new_group_info = {"in_group": True, "rank": target_role["rank"], "role_id": target_role["id"], "role_name": target_role["name"]}
    await apply_roles_for_user(usuario, {"id": linked["roblox_id"], "name": linked["roblox_username"]}, new_group_info)
    
    # Registrar en DB
    await log_rank_change(
        usuario.id,
        linked["roblox_id"],
        old_rank_name,
        target_role["name"],
        interaction.user.id,
        str(interaction.user),
        "ASCENSO"
    )

    embed = discord.Embed(
        title="🎖️ ASCENSO MILITAR CONCEDIDO",
        description=f"El soldado {usuario.mention} ha sido ascendido formalmente en las filas de **REPCL • Ejército de Chile**.",
        color=0x2ecc71,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="👤 Militar", value=f"{usuario.mention} (`{linked['roblox_username']}`)", inline=True)
    embed.add_field(name="📉 Rango Anterior", value=f"`{old_rank_name}`", inline=True)
    embed.add_field(name="📈 Nuevo Rango", value=f"**{target_role['name']}** (Nivel {target_role['rank']})", inline=True)
    embed.add_field(name="👮 Administrador Autorizante", value=f"{interaction.user.mention}", inline=False)
    embed.set_thumbnail(url=await roblox_client.get_user_thumbnail(linked["roblox_id"]))
    embed.set_footer(text="Orden de Comandancia General", icon_url=config.get("server_icon_url"))

    await interaction.followup.send(embed=embed)
    await send_audit_log(interaction.guild, embed)


@bot.tree.command(name="descender", description="Desciende a un usuario en Roblox y actualiza su rol en Discord")
@app_commands.describe(usuario="Miembro a descender", nuevo_rango="Nombre o número de rango inferior en Roblox")
async def cmd_descender(interaction: discord.Interaction, usuario: discord.Member, nuevo_rango: str):
    await interaction.response.defer()
    if not is_repcl_admin(interaction.user):
        await interaction.followup.send("❌ No tienes permisos suficientes para realizar descensos militares.")
        return

    linked = await get_linked_account(usuario.id)
    if not linked:
        await interaction.followup.send(f"❌ El usuario {usuario.mention} no tiene una cuenta de Roblox vinculada.")
        return

    group_id = config.get("roblox_group_id", 0)
    roles_list = await roblox_client.get_group_roles(group_id)
    target_role = None
    for r in roles_list:
        if str(r.get("rank")) == nuevo_rango or r.get("name", "").lower() == nuevo_rango.lower():
            target_role = r
            break

    if not target_role:
        await interaction.followup.send(f"❌ Rango `{nuevo_rango}` no encontrado en el grupo de Roblox.")
        return

    current_role = await roblox_client.get_user_group_role(linked["roblox_id"], group_id)
    old_rank_name = current_role.get("role_name", "Sin Rango")

    result = await roblox_client.set_user_rank(group_id, linked["roblox_id"], target_role["id"])
    if not result.get("success"):
        await interaction.followup.send(f"⚠️ Error en Roblox: {result.get('error')}")
        return

    new_group_info = {"in_group": True, "rank": target_role["rank"], "role_id": target_role["id"], "role_name": target_role["name"]}
    await apply_roles_for_user(usuario, {"id": linked["roblox_id"], "name": linked["roblox_username"]}, new_group_info)

    await log_rank_change(
        usuario.id,
        linked["roblox_id"],
        old_rank_name,
        target_role["name"],
        interaction.user.id,
        str(interaction.user),
        "DESCENSO"
    )

    embed = discord.Embed(
        title="📉 DECRETO DE DESCENSO MILITAR",
        description=f"El miembro {usuario.mention} ha sido descendido de rango en el Ejército de Chile.",
        color=0xe67e22,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="👤 Militar", value=f"{usuario.mention} (`{linked['roblox_username']}`)", inline=True)
    embed.add_field(name="Rango Anterior", value=f"`{old_rank_name}`", inline=True)
    embed.add_field(name="Nuevo Rango", value=f"**{target_role['name']}** (Nivel {target_role['rank']})", inline=True)
    embed.add_field(name="👮 Oficial a Cargo", value=f"{interaction.user.mention}", inline=False)
    embed.set_thumbnail(url=await roblox_client.get_user_thumbnail(linked["roblox_id"]))

    await interaction.followup.send(embed=embed)
    await send_audit_log(interaction.guild, embed)


@bot.tree.command(name="rango", description="Muestra el rango actual de Roblox y Discord de un usuario")
@app_commands.describe(usuario="Usuario a consultar (opcional)")
async def cmd_rango(interaction: discord.Interaction, usuario: Optional[discord.Member] = None):
    target = usuario or interaction.user
    await interaction.response.defer()

    linked = await get_linked_account(target.id)
    if not linked:
        await interaction.followup.send(f"❌ {target.mention} no tiene ninguna cuenta de Roblox vinculada.")
        return

    group_id = config.get("roblox_group_id", 0)
    group_role = await roblox_client.get_user_group_role(linked["roblox_id"], group_id)
    avatar_url = await roblox_client.get_user_thumbnail(linked["roblox_id"])

    embed = discord.Embed(
        title="🇨🇱 FICHA DE SERVICIO MILITAR • REPCL",
        color=0xD52B1E,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="Discord", value=f"{target.mention} (`{target.id}`)", inline=True)
    embed.add_field(name="Roblox", value=f"[{linked['roblox_username']}](https://www.roblox.com/users/{linked['roblox_id']}/profile)", inline=True)
    
    if group_role.get("in_group"):
        embed.add_field(name="🎖️ Rango en Roblox", value=f"**{group_role['role_name']}** (Nivel {group_role['rank']})", inline=False)
    else:
        embed.add_field(name="🛡️ Estado en Roblox", value="No pertenece al grupo (Ciudadano)", inline=False)

    # Roles en Discord
    roles = [r.mention for r in target.roles if r.name != "@everyone"]
    embed.add_field(name="🏷️ Roles en Discord", value=" ".join(roles[:8]) if roles else "Sin roles", inline=False)
    embed.add_field(name="📅 Fecha de Vinculación", value=f"`{linked['linked_at'][:10]}`", inline=True)

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="historial-rangos", description="Muestra el historial de ascensos y descensos de un usuario")
@app_commands.describe(usuario="Usuario a consultar")
async def cmd_historial_rangos(interaction: discord.Interaction, usuario: discord.Member):
    await interaction.response.defer()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM rank_history WHERE discord_id = ? ORDER BY id DESC LIMIT 10", 
            (usuario.id,)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.followup.send(f"📋 No hay registros de cambios de rango para {usuario.mention}.")
        return

    embed = discord.Embed(
        title=f"📋 HISTORIAL DE RANGOS • {usuario.display_name}",
        description="Registro histórico de ascensos y descensos en el Ejército de Chile:",
        color=0x3498db,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    for r in rows:
        icon = "📈" if r["action_type"] == "ASCENSO" else "📉"
        embed.add_field(
            name=f"{icon} {r['action_type']}: {r['old_rank']} ➔ {r['new_rank']}",
            value=f"**Admin:** `{r['admin_name']}`\n**Fecha:** `{r['timestamp']}`",
            inline=False
        )

    await interaction.followup.send(embed=embed)

# ---------------------------------------------------------------------------
# 7. SISTEMA DE SANCIONES Y BLACKLIST
# ---------------------------------------------------------------------------
@bot.tree.command(name="advertir", description="Emite una advertencia formal a un usuario")
@app_commands.describe(usuario="Usuario a advertir", motivo="Razón de la advertencia")
async def cmd_advertir(interaction: discord.Interaction, usuario: discord.Member, motivo: str):
    if not is_repcl_admin(interaction.user):
        await interaction.response.send_message("❌ No tienes permisos de administración.", ephemeral=True)
        return

    s_id = await add_sanction(usuario.id, str(usuario), interaction.user.id, str(interaction.user), "⚠️ Advertencia", motivo, "N/A")

    embed = discord.Embed(
        title="⚠️ ADVERTENCIA DISCIPLINARIA",
        description=f"{usuario.mention} ha recibido una advertencia oficial.",
        color=0xf1c40f
    )
    embed.add_field(name="ID Sanción", value=f"`{s_id}`", inline=True)
    embed.add_field(name="Motivo", value=motivo, inline=True)
    embed.add_field(name="Administrador", value=interaction.user.mention, inline=False)

    try:
        await usuario.send(f"⚠️ **Has recibido una advertencia en REPCL • Ejército de Chile**\n**Motivo:** {motivo}\n**ID:** `{s_id}`")
    except Exception:
        pass

    await interaction.response.send_message(embed=embed)
    await send_audit_log(interaction.guild, embed)


@bot.tree.command(name="sancionar", description="Aplica una sanción configurable a un usuario")
@app_commands.describe(
    usuario="Usuario sancionado",
    tipo="Tipo de sanción",
    motivo="Motivo de la sanción",
    duracion="Duración (ej. 1h, 24h, 7d, Permanente)"
)
@app_commands.choices(tipo=[
    app_commands.Choice(name="⚠️ Advertencia", value="⚠️ Advertencia"),
    app_commands.Choice(name="🔇 Silenciar (Timeout)", value="🔇 Silenciar"),
    app_commands.Choice(name="⏸️ Suspensión Temporal", value="⏸️ Suspensión Temporal"),
    app_commands.Choice(name="🚫 Blacklist Temporal", value="🚫 Blacklist Temporal"),
    app_commands.Choice(name="⛔ Blacklist Permanente", value="⛔ Blacklist Permanente"),
    app_commands.Choice(name="🚷 Expulsión (Kick)", value="🚷 Expulsión")
])
async def cmd_sancionar(interaction: discord.Interaction, usuario: discord.Member, tipo: app_commands.Choice[str], motivo: str, duracion: Optional[str] = "Indefinida"):
    if not is_repcl_admin(interaction.user):
        await interaction.response.send_message("❌ No tienes permisos de administración.", ephemeral=True)
        return

    await interaction.response.defer()
    s_type = tipo.value
    s_id = await add_sanction(usuario.id, str(usuario), interaction.user.id, str(interaction.user), s_type, motivo, duracion)

    # Aplicar acciones según el tipo
    if s_type == "🔇 Silenciar":
        try:
            # 1 hora por defecto si no se especifica
            await usuario.timeout(datetime.timedelta(hours=1), reason=motivo)
        except Exception as e:
            print(f"Error aplicando timeout: {e}")

    elif s_type == "🚷 Expulsión":
        try:
            await usuario.kick(reason=motivo)
        except Exception as e:
            print(f"Error expulsando usuario: {e}")

    elif "Blacklist" in s_type:
        linked = await get_linked_account(usuario.id)
        roblox_id = linked["roblox_id"] if linked else 0
        await set_blacklist(usuario.id, roblox_id, motivo, interaction.user.id, str(interaction.user))
        # Quitar roles
        await apply_roles_for_user(usuario, {"id": roblox_id, "name": "Usuario"}, {"in_group": False})

    embed = discord.Embed(
        title="⚖️ SANCIÓN DISCIPLINARIA APLICADA",
        color=0xd63031,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="Sanción ID", value=f"`{s_id}`", inline=True)
    embed.add_field(name="Usuario", value=f"{usuario.mention} (`{usuario.id}`)", inline=True)
    embed.add_field(name="Tipo", value=f"**{s_type}**", inline=True)
    embed.add_field(name="Duración", value=f"`{duracion}`", inline=True)
    embed.add_field(name="Motivo", value=motivo, inline=False)
    embed.add_field(name="Oficial a Cargo", value=interaction.user.mention, inline=False)

    try:
        await usuario.send(f"⚖️ **Has recibido una sanción en REPCL • Ejército de Chile**\n**Tipo:** {s_type}\n**Motivo:** {motivo}\n**Duración:** {duracion}\n**ID:** `{s_id}`")
    except Exception:
        pass

    await interaction.followup.send(embed=embed)
    await send_audit_log(interaction.guild, embed)


@bot.tree.command(name="sanciones", description="Muestra el historial completo de sanciones de un usuario")
@app_commands.describe(usuario="Usuario a consultar")
async def cmd_sanciones(interaction: discord.Interaction, usuario: discord.Member):
    await interaction.response.defer()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sanctions WHERE user_id = ? ORDER BY created_at DESC", (usuario.id,)) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.followup.send(f"✅ El usuario {usuario.mention} tiene un expediente limpio (0 sanciones registradas).")
        return

    embed = discord.Embed(
        title=f"⚖️ EXPEDIENTE DISCIPLINARIO • {usuario.display_name}",
        description=f"Total de sanciones registradas: **{len(rows)}**",
        color=0xe74c3c,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    for r in rows[:8]:
        embed.add_field(
            name=f"[{r['id']}] {r['sanction_type']} • {r['duration']}",
            value=f"**Motivo:** {r['reason']}\n**Admin:** `{r['admin_tag']}`\n**Fecha:** `{r['created_at']}`",
            inline=False
        )

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="ban", description="Banea a un usuario de Discord y lo registra en la Blacklist de REPCL")
@app_commands.describe(usuario="Usuario a banear", motivo="Motivo del ban")
async def cmd_ban(interaction: discord.Interaction, usuario: discord.Member, motivo: str):
    if not is_repcl_admin(interaction.user):
        await interaction.response.send_message("❌ No tienes permisos de administración.", ephemeral=True)
        return

    await interaction.response.defer()
    linked = await get_linked_account(usuario.id)
    roblox_id = linked["roblox_id"] if linked else 0

    await set_blacklist(usuario.id, roblox_id, motivo, interaction.user.id, str(interaction.user))
    try:
        await interaction.guild.ban(usuario, reason=f"REPCL Blacklist: {motivo}")
    except Exception as e:
        await interaction.followup.send(f"⚠️ Advertencia al banear de Discord: {e}")

    embed = discord.Embed(
        title="⛔ USUARIO BANEADO Y AÑADIDO A BLACKLIST",
        color=0x2c3e50,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="Usuario", value=f"{usuario.mention} (`{usuario.id}`)", inline=True)
    embed.add_field(name="Roblox ID", value=f"`{roblox_id}`", inline=True)
    embed.add_field(name="Motivo", value=motivo, inline=False)
    embed.add_field(name="Oficial", value=interaction.user.mention, inline=False)

    await interaction.followup.send(embed=embed)
    await send_audit_log(interaction.guild, embed)


@bot.tree.command(name="unban", description="Retira el ban y elimina al usuario de la Blacklist")
@app_commands.describe(user_id="ID de Discord del usuario")
async def cmd_unban(interaction: discord.Interaction, user_id: str):
    if not is_repcl_admin(interaction.user):
        await interaction.response.send_message("❌ No tienes permisos de administración.", ephemeral=True)
        return

    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message("❌ ID no válido.", ephemeral=True)
        return

    removed = await remove_blacklist(uid)
    try:
        user_obj = discord.Object(id=uid)
        await interaction.guild.unban(user_obj)
    except Exception:
        pass

    if removed:
        await interaction.response.send_message(f"✅ El usuario `{uid}` ha sido retirado de la Blacklist y desbaneado.")
    else:
        await interaction.response.send_message(f"ℹ️ El usuario `{uid}` no estaba en la Blacklist local.")


@bot.tree.command(name="blacklist", description="Muestra los usuarios activos en la Blacklist de REPCL")
async def cmd_blacklist(interaction: discord.Interaction):
    if not is_repcl_admin(interaction.user):
        await interaction.response.send_message("❌ No tienes permisos para ver la Blacklist.", ephemeral=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM blacklist ORDER BY created_at DESC LIMIT 15") as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message("✅ No hay usuarios registrados en la Blacklist actualmente.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⛔ BLACKLIST OFICIAL • REPCL EJÉRCITO DE CHILE",
        description=f"Total de infractores vetados: **{len(rows)}**",
        color=0x2c3e50
    )
    for r in rows:
        embed.add_field(
            name=f"ID Discord: {r['user_id']} | Roblox: {r['roblox_id']}",
            value=f"**Motivo:** {r['reason']}\n**Oficial:** `{r['admin_tag']}`\n**Fecha:** `{r['created_at']}`",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="blacklist-info", description="Consulta los detalles de Blacklist de un usuario en específico")
@app_commands.describe(usuario="Usuario a consultar")
async def cmd_blacklist_info(interaction: discord.Interaction, usuario: discord.User):
    bl = await is_blacklisted(usuario.id)
    if not bl:
        await interaction.response.send_message(f"✅ El usuario {usuario.mention} no está en la Blacklist.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⛔ DETALLES DE BLACKLIST",
        color=0xd63031,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="Usuario", value=f"{usuario.mention} (`{usuario.id}`)", inline=True)
    embed.add_field(name="Roblox ID", value=f"`{bl['roblox_id']}`", inline=True)
    embed.add_field(name="Motivo", value=bl['reason'], inline=False)
    embed.add_field(name="Administrador", value=f"`{bl['admin_tag']}`", inline=True)
    embed.add_field(name="Fecha de Registro", value=f"`{bl['created_at']}`", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------------------------------------------------------------------
# 8. SISTEMA DE AUDITORÍA Y ALERTAS AUTOMÁTICAS
# ---------------------------------------------------------------------------
@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    embed = discord.Embed(
        title="🗑️ AUDITORÍA • MENSAJE ELIMINADO",
        description=f"Se ha eliminado un mensaje en {message.channel.mention}, pero su contenido fue guardado en el registro.",
        color=0xe74c3c,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_author(name=f"{message.author} ({message.author.id})", icon_url=message.author.display_avatar.url)
    embed.add_field(name="Contenido Guardado", value=message.content if message.content else "*Sin contenido de texto*", inline=False)
    
    if message.attachments:
        files_info = "\n".join([f"[{a.filename}]({a.url})" for a in message.attachments])
        embed.add_field(name="Archivos / Imágenes Adjuntas", value=files_info, inline=False)

    embed.set_footer(text=f"Canal: #{message.channel.name} | ID Mensaje: {message.id}")
    await send_audit_log(message.guild, embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or before.content == after.content or not before.guild:
        return

    embed = discord.Embed(
        title="✏️ AUDITORÍA • MENSAJE EDITADO",
        color=0xf39c12,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_author(name=f"{before.author} ({before.author.id})", icon_url=before.author.display_avatar.url)
    embed.add_field(name="Canal", value=before.channel.mention, inline=True)
    embed.add_field(name="Enlace", value=f"[Ir al mensaje]({after.jump_url})", inline=True)
    embed.add_field(name="Contenido Anterior", value=before.content[:1000] or "*Vacío*", inline=False)
    embed.add_field(name="Contenido Nuevo", value=after.content[:1000] or "*Vacío*", inline=False)

    await send_audit_log(before.guild, embed)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # Sistema de Alertas Automáticas para Conductas Sospechosas
    content_lower = message.content.lower()
    keywords = config.get("suspicious_keywords", [])
    found_keywords = [kw for kw in keywords if kw in content_lower]

    if found_keywords:
        alert_embed = discord.Embed(
            title="🚨 ALERTA AUTOMÁTICA DE SEGURIDAD • CONDUCTA SOSPECHOSA",
            description=f"El sistema ha interceptado palabras clave sospechosas enviadas por {message.author.mention}.",
            color=0xc0392b,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        alert_embed.set_author(name=f"{message.author} ({message.author.id})", icon_url=message.author.display_avatar.url)
        alert_embed.add_field(name="Palabras Detectadas", value=f"`{', '.join(found_keywords)}`", inline=True)
        alert_embed.add_field(name="Canal", value=message.channel.mention, inline=True)
        alert_embed.add_field(name="Mensaje Completo", value=message.content[:1024], inline=False)
        alert_embed.add_field(name="Acción Sugerida", value="Revisar contexto del usuario y aplicar `/sancionar` o `/ban` si es necesario.", inline=False)
        
        await send_audit_log(message.guild, alert_embed)

    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# 9. EVENTO ON_READY Y SINCRONIZACIÓN DE COMANDOS SLASH
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    print("=" * 60)
    print(f"🇨🇱 BOT OFICIAL REPCL • EJÉRCITO DE CHILE CONECTADO")
    print(f"👤 Bot: {bot.user} (ID: {bot.user.id})")
    print("=" * 60)

    try:
        if GUILD_ID and int(GUILD_ID) > 0:
            guild_obj = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            print(f"[+] Sincronizados {len(synced)} comandos Slash en el servidor ID {GUILD_ID}.")
        else:
            synced = await bot.tree.sync()
            print(f"[+] Sincronizados {len(synced)} comandos Slash globalmente.")
    except Exception as e:
        print(f"[!] Error sincronizando comandos Slash: {e}")

    activity = discord.Activity(type=discord.ActivityType.watching, name="REPCL • Ejército de Chile 🇨🇱")
    await bot.change_presence(status=discord.Status.online, activity=activity)


# ---------------------------------------------------------------------------
# 10. SERVIDOR WEB DE SALUD PARA RENDER / CLOUD HOSTING
# ---------------------------------------------------------------------------
class RenderHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html_response = (
            "<!DOCTYPE html>"
            "<html><head><title>REPCL - Ejercito de Chile</title>"
            "<style>body{background:#0d1117;color:#c9d1d9;font-family:sans-serif;text-align:center;padding:50px;}"
            "h1{color:#e63946;}.badge{background:#1f6feb;color:white;padding:5px 12px;border-radius:12px;font-size:14px;}</style></head>"
            "<body><h1>&#127464;&#127473; REPCL &bull; Ej&eacute;rcito de Chile</h1>"
            "<p>Bot de Discord & Sistema de Verificaci&oacute;n Roblox activo.</p>"
            "<p><span class='badge'>STATUS: ONLINE (200 OK)</span></p></body></html>"
        )
        self.wfile.write(html_response.encode("utf-8"))

    def log_message(self, format, *args):
        # Silenciar logs continuos de health check de Render para mantener limpia la consola
        pass

def start_health_server():
    """Inicia un servidor HTTP ligero en el puerto especificado por Render (PORT) o 8080."""
    port_env = os.getenv("PORT")
    port = int(port_env) if port_env else 8080
    try:
        server = HTTPServer(("0.0.0.0", port), RenderHealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"[+] Servidor HTTP de salud activo en puerto {port} (Render Port Binding Resuelto)")
    except Exception as e:
        print(f"[!] Advertencia al iniciar servidor web de salud: {e}")

# ---------------------------------------------------------------------------
# 11. EJECUCIÓN PRINCIPAL
# ---------------------------------------------------------------------------
def main():
    if not DISCORD_TOKEN or "tu_token" in DISCORD_TOKEN:
        print("\n[!] ERROR CRÍTICO: No se ha configurado DISCORD_BOT_TOKEN en el archivo .env")
        print("    Por favor coloca tu token de bot de Discord en el archivo .env antes de iniciar.\n")
        sys.exit(1)

    # Iniciar servidor web para Render / Koyeb / Railway
    start_health_server()

    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"[!] Error fatal iniciando el bot: {e}")

if __name__ == "__main__":
    main()
