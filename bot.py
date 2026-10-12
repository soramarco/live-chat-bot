import os
import asyncio
import threading
import json
import time
import tempfile
from threading import Thread

import discord
from discord.ext import commands
from flask import Flask, jsonify, request

app = Flask(__name__)

STORAGE_FILE = os.environ.get("BOT_STORAGE_FILE", "bot_storage.json")
TARGET_CHANNEL_ID = 1544497366814560318  # Salon Discord Live Chat

# La file de médias est commune à tous les clients Live Chat.
# Les préférences (activation, position, Mode Jeu) sont, elles, propres à chaque ID Discord.
global_queue = []
current_active_item = None
active_users = set()             # ID Discord sous forme de chaîne (anciens noms migrés à la première interaction)
user_positions = {}              # discord_user_id -> left / center / right
user_game_modes = {}             # discord_user_id -> bool
user_aliases = {}                # discord_user_id -> anciens/nouveaux noms utilisés par core.pyw
panel_message = None
panel_channel_id = None
data_lock = threading.RLock()
last_pop_time = 0


def _normalise_alias(value):
    return str(value or "").strip().casefold()


def _unique_names(values):
    result = []
    seen = set()
    for value in values:
        value = str(value or "").strip()
        key = _normalise_alias(value)
        if value and key and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def load_data():
    """Charge aussi l'ancien format. Les anciens noms seront migrés vers l'ID Discord
    dès que leur propriétaire ouvre le panneau privé de réglages.
    """
    global user_positions, active_users, user_game_modes, user_aliases
    if not os.path.exists(STORAGE_FILE):
        return
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        user_positions = data.get("user_positions", {}) or {}
        active_users = set(str(value) for value in data.get("active_users", []))
        user_game_modes = data.get("user_game_modes", {}) or {}
        user_aliases = {
            str(user_id): _unique_names(aliases if isinstance(aliases, list) else [aliases])
            for user_id, aliases in (data.get("user_aliases", {}) or {}).items()
        }
        print(
            f"[DATA] Données chargées : {len(user_positions)} position(s), "
            f"{len(active_users)} actif(s), {len(user_aliases)} identité(s) Discord connue(s)."
        )
    except Exception as error:
        print(f"[ERREUR] Chargement stockage : {error}")


def swift_save_data():
    """Sauvegarde atomique et sérialisée pour éviter les écritures concurrentes."""
    try:
        # Garder le verrou durant toute l'écriture empêche une sauvegarde ancienne
        # de finir après une sauvegarde plus récente.
        with data_lock:
            data = {
                "user_positions": user_positions,
                "active_users": sorted(active_users),
                "user_game_modes": user_game_modes,
                "user_aliases": user_aliases,
            }
            destination = os.path.abspath(STORAGE_FILE)
            directory = os.path.dirname(destination) or "."
            os.makedirs(directory, exist_ok=True)
            fd, temporary_path = tempfile.mkstemp(prefix="bot_storage_", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary_path, destination)
            finally:
                if os.path.exists(temporary_path):
                    try:
                        os.remove(temporary_path)
                    except OSError:
                        pass
    except Exception as error:
        print(f"[ERREUR] Sauvegarde stockage : {error}")


load_data()


def register_discord_user(user):
    """Enregistre l'ID Discord et les noms de compatibilité utilisés par core.pyw.

    Le stockage utilise l'ID Discord, pas display_name. Les noms restent des alias
    pour que les versions actuelles de core.pyw (qui transmettent un pseudo texte)
    puissent retrouver les bons réglages.
    """
    user_id = str(user.id)
    display_name = getattr(user, "display_name", None)
    global_name = getattr(user, "global_name", None)
    username = getattr(user, "name", None)
    nickname = getattr(user, "nick", None)
    new_aliases = _unique_names([display_name, global_name, username, nickname])

    with data_lock:
        old_aliases = user_aliases.get(user_id, [])
        combined_aliases = _unique_names(new_aliases + old_aliases)
        alias_norms = {_normalise_alias(alias) for alias in combined_aliases}
        user_aliases[user_id] = combined_aliases

        def alias_owned_by_another_user(alias_norm):
            for other_id, aliases in user_aliases.items():
                if other_id != user_id and any(_normalise_alias(a) == alias_norm for a in aliases):
                    return True
            return False

        # Migration de l'ancien stockage indexé par display_name.
        for mapping in (user_positions, user_game_modes):
            for old_key in list(mapping.keys()):
                old_key = str(old_key)
                old_norm = _normalise_alias(old_key)
                if old_key == user_id or old_norm not in alias_norms:
                    continue
                if alias_owned_by_another_user(old_norm):
                    continue  # alias ambigu : ne jamais attribuer les données à la mauvaise personne
                if user_id not in mapping:
                    mapping[user_id] = mapping[old_key]
                mapping.pop(old_key, None)

        for old_key in list(active_users):
            old_norm = _normalise_alias(old_key)
            if old_key == user_id or old_norm not in alias_norms:
                continue
            if alias_owned_by_another_user(old_norm):
                continue
            active_users.discard(old_key)
            active_users.add(user_id)

        swift_save_data()
    return user_id


def resolve_api_user(value):
    """Résout le pseudo transmis par core.pyw vers un ID Discord.

    Si plusieurs comptes ont le même alias, ne choisit jamais arbitrairement un compte :
    cela évite d'exposer les réglages d'un autre utilisateur.
    """
    value = str(value or "").strip()
    if not value:
        return None

    with data_lock:
        # Prend en charge un futur core.pyw qui enverrait directement l'ID Discord.
        if value in user_aliases or value in user_positions or value in user_game_modes:
            return value

        wanted = _normalise_alias(value)
        matches = []
        for user_id, aliases in user_aliases.items():
            if any(_normalise_alias(alias) == wanted for alias in aliases):
                matches.append(user_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print(f"[SECURITE] Alias ambigu reçu de core.pyw : {value!r}; aucun profil choisi.")
            return None

        # Compatibilité avec les profils sauvegardés par l'ancienne version avant
        # que leur propriétaire ait ouvert le nouveau panneau privé.
        legacy_keys = set(str(key) for key in user_positions)
        legacy_keys.update(str(key) for key in user_game_modes)
        legacy_keys.update(str(key) for key in active_users)
        legacy_matches = {key for key in legacy_keys if _normalise_alias(key) == wanted}
        if len(legacy_matches) == 1:
            return next(iter(legacy_matches))
        return None


def get_user_settings(user_id):
    with data_lock:
        return {
            "active": user_id in active_users,
            "position": user_positions.get(user_id, "center"),
            "game_mode": bool(user_game_modes.get(user_id, False)),
        }


def _display_name_for_key(key):
    key = str(key)
    aliases = user_aliases.get(key, [])
    if aliases:
        return aliases[0]
    # Ancien enregistrement de nom, avant la migration par ID.
    if not key.isdigit():
        return key
    return "Utilisateur"


@app.route("/")
def home():
    return "Bot is alive!", 200


def get_channel_panel_content():
    """Le panneau public n'affiche plus les réglages d'une personne en particulier."""
    with data_lock:
        count = len(active_users)
        users = sorted({_display_name_for_key(user_id) for user_id in active_users})
    users_text = ", ".join(f"**{name}**" for name in users) if users else "_Personne_"
    return (
        f"📊 **Statut en direct** : {count} actif(s)\n"
        f"👤 **Connectés** : {users_text}\n\n"
        "👇 **Ouvre tes réglages personnels ci-dessous. Ils ne changent que pour ton compte.**"
    )


def get_personal_panel_content(user_id):
    settings = get_user_settings(user_id)
    active_text = "Activé" if settings["active"] else "Désactivé"
    game_text = "Activé" if settings["game_mode"] else "Désactivé"
    position_text = {
        "left": "Gauche",
        "center": "Centre",
        "right": "Droite",
    }.get(settings["position"], "Centre")
    return (
        "⚙️ **Tes réglages Live Chat**\n"
        f"🟢 **Live Chat :** {active_text}\n"
        f"🎮 **Mode Jeu :** {game_text}\n"
        f"📍 **Position :** {position_text}\n\n"
        "Ces réglages sont privés et indépendants de ceux des autres utilisateurs."
    )


class PublicControlView(discord.ui.View):
    """Vue commune neutre : son apparence ne dépend d'aucun utilisateur."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Mes réglages Live Chat",
        emoji="⚙️",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="open_my_personal_livechat_settings",
    )
    async def open_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = register_discord_user(interaction.user)
        view = PersonalControlView(user_id)
        await interaction.response.send_message(
            content=get_personal_panel_content(user_id),
            view=view,
            ephemeral=True,
        )


class PersonalControlView(discord.ui.View):
    """Chaque interaction privée est liée à l'ID Discord de son propriétaire."""

    def __init__(self, user_id):
        # Les panneaux privés peuvent être rouverts avec le bouton public s'ils expirent.
        super().__init__(timeout=900)
        self.user_id = str(user_id)
        self.update_button_styles()

    def update_button_styles(self):
        settings = get_user_settings(self.user_id)
        if settings["active"]:
            self.toggle_btn.label = "Désactiver mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.danger
            self.toggle_btn.emoji = "🔴"
        else:
            self.toggle_btn.label = "Activer mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.success
            self.toggle_btn.emoji = "🟢"

        if settings["game_mode"]:
            self.btn_game_mode.label = "Mode Jeu : ACTIF"
            self.btn_game_mode.style = discord.ButtonStyle.success
        else:
            self.btn_game_mode.label = "Mode Jeu : INACTIF"
            self.btn_game_mode.style = discord.ButtonStyle.secondary
        self.btn_game_mode.emoji = "🎮"

        position = settings["position"]
        self.btn_left.style = discord.ButtonStyle.primary if position == "left" else discord.ButtonStyle.secondary
        self.btn_center.style = discord.ButtonStyle.primary if position == "center" else discord.ButtonStyle.secondary
        self.btn_right.style = discord.ButtonStyle.primary if position == "right" else discord.ButtonStyle.secondary

    async def _check_owner(self, interaction):
        if str(interaction.user.id) == self.user_id:
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Ces réglages ne sont accessibles qu'à la personne qui les a ouverts.",
                ephemeral=True,
            )
        return False

    async def _refresh_private_message(self, interaction, refresh_public=False):
        self.update_button_styles()
        await interaction.response.edit_message(
            content=get_personal_panel_content(self.user_id),
            view=self,
        )
        if refresh_public:
            await refresh_public_panel()

    @discord.ui.button(
        label="Activer mon Live Chat",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        row=0,
        custom_id="personal_toggle_livechat",
    )
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        # Utilise l'ID Discord, jamais display_name comme clé de stockage.
        user_id = register_discord_user(interaction.user)
        if user_id != self.user_id:
            await interaction.response.send_message("Profil invalide. Rouvre tes réglages.", ephemeral=True)
            return
        with data_lock:
            if user_id in active_users:
                active_users.remove(user_id)
            else:
                active_users.add(user_id)
            swift_save_data()
        await self._refresh_private_message(interaction, refresh_public=True)

    @discord.ui.button(
        label="Mode Jeu : INACTIF",
        emoji="🎮",
        style=discord.ButtonStyle.secondary,
        row=0,
        custom_id="personal_toggle_game_mode",
    )
    async def btn_game_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        user_id = register_discord_user(interaction.user)
        with data_lock:
            user_game_modes[user_id] = not bool(user_game_modes.get(user_id, False))
            swift_save_data()
        await self._refresh_private_message(interaction)

    @discord.ui.button(
        label="Gauche",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="personal_position_left",
    )
    async def btn_left(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        user_id = register_discord_user(interaction.user)
        with data_lock:
            user_positions[user_id] = "left"
            swift_save_data()
        await self._refresh_private_message(interaction)

    @discord.ui.button(
        label="Centre",
        emoji="⏺️",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="personal_position_center",
    )
    async def btn_center(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        user_id = register_discord_user(interaction.user)
        with data_lock:
            user_positions[user_id] = "center"
            swift_save_data()
        await self._refresh_private_message(interaction)

    @discord.ui.button(
        label="Droite",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="personal_position_right",
    )
    async def btn_right(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        user_id = register_discord_user(interaction.user)
        with data_lock:
            user_positions[user_id] = "right"
            swift_save_data()
        await self._refresh_private_message(interaction)


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def is_target_channel(channel):
    return channel.id == TARGET_CHANNEL_ID


panel_update_lock = asyncio.Lock()


async def refresh_public_panel():
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel is not None:
        await repost_bottom_panel(channel)


async def repost_bottom_panel(channel, move_to_bottom=False):
    """Maintient un panneau public unique et neutre en bas du salon."""
    global panel_message, panel_channel_id
    async with panel_update_lock:
        try:
            content = get_channel_panel_content()
            view = PublicControlView()

            if move_to_bottom:
                old_panels = []
                if panel_message is not None and panel_channel_id == channel.id:
                    old_panels.append(panel_message)

                async for old_msg in channel.history(limit=50):
                    if (
                        old_msg.author == bot.user
                        and "Statut en direct" in (old_msg.content or "")
                        and all(old_msg.id != item.id for item in old_panels)
                    ):
                        old_panels.append(old_msg)

                # Envoie d'abord le nouveau panneau pour préserver l'ancien en cas d'échec.
                new_panel = await channel.send(content=content, view=view)
                panel_message = new_panel
                panel_channel_id = channel.id
                for old_msg in old_panels:
                    if old_msg.id == new_panel.id:
                        continue
                    try:
                        await old_msg.delete()
                    except Exception as error:
                        print(f"Impossible de supprimer l'ancien panneau {old_msg.id} : {error}")
                return

            if panel_message is not None and panel_channel_id == channel.id:
                try:
                    await panel_message.edit(content=content, view=view)
                    return
                except Exception:
                    panel_message = None
                    panel_channel_id = None

            existing = []
            async for old_msg in channel.history(limit=50):
                if old_msg.author == bot.user and "Statut en direct" in (old_msg.content or ""):
                    existing.append(old_msg)

            existing.sort(key=lambda message: message.created_at, reverse=True)
            if existing:
                panel_message = existing[0]
                panel_channel_id = channel.id
                await panel_message.edit(content=content, view=view)
                for duplicate in existing[1:]:
                    try:
                        await duplicate.delete()
                    except Exception:
                        pass
            else:
                panel_message = await channel.send(content=content, view=view)
                panel_channel_id = channel.id
        except Exception as error:
            print(f"Erreur mise à jour panneau bas : {error}")


@bot.event
async def on_ready():
    print(f"[DISCORD] Connexion réussie en tant que bot ({bot.user})")
    # Le panneau public reste disponible après un redémarrage du processus.
    if not getattr(bot, "_public_view_registered", False):
        bot.add_view(PublicControlView())
        bot._public_view_registered = True
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        await repost_bottom_panel(channel)
    else:
        print(f"[ERREUR] Salon cible {TARGET_CHANNEL_ID} introuvable dans le cache du bot.")


@bot.event
async def on_message(message):
    global current_active_item, global_queue
    if bot.user is not None and message.author.id == bot.user.id:
        return

    if message.author.bot:
        if is_target_channel(message.channel):
            await repost_bottom_panel(message.channel, move_to_bottom=True)
        return

    if is_target_channel(message.channel):
        media_url = ""
        if message.attachments:
            media_url = message.attachments[0].url
        elif "http" in message.content:
            for word in message.content.split():
                if word.startswith("http"):
                    media_url = word
                    break

        if media_url:
            item = {
                "item_id": str(message.id),
                "name": message.author.display_name,
                "avatar": str(message.author.display_avatar.url) if message.author.display_avatar else "",
                "content": message.content.replace(media_url, "").strip(),
                "url": media_url,
                "message_obj": message,
                "control_message": None,
            }
            with data_lock:
                if len(global_queue) > 15:
                    global_queue.pop(0)
                if current_active_item is None:
                    current_active_item = item
                    is_first = True
                else:
                    global_queue.append(item)
                    is_first = False
            await send_control_message(item, is_active=is_first)

    await bot.process_commands(message)
    if is_target_channel(message.channel):
        await repost_bottom_panel(message.channel, move_to_bottom=True)


class ItemStopView(discord.ui.View):
    def __init__(self, item_ref, active):
        super().__init__(timeout=86400)
        self.item_ref = item_ref
        self.stop_button.disabled = not active

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        global current_active_item, global_queue, last_pop_time
        with data_lock:
            if current_active_item != self.item_ref:
                return
            last_pop_time = time.time()
            try:
                if self.item_ref.get("control_message"):
                    await self.item_ref["control_message"].delete()
            except Exception:
                pass
            if global_queue:
                current_active_item = global_queue.pop(0)
                asyncio.run_coroutine_threadsafe(
                    activate_next_item_message(current_active_item), bot.loop
                )
            else:
                current_active_item = None


async def send_control_message(item, is_active):
    try:
        view = ItemStopView(item, is_active)
        status_text = (
            "🎬 **Média en cours de diffusion...**"
            if is_active else "⏳ **En attente dans la file...**"
        )
        message = await item["message_obj"].reply(status_text, view=view)
        item["control_message"] = message
    except Exception as error:
        print(f"Erreur critique en envoyant le message de contrôle : {error}")


async def activate_next_item_message(item):
    try:
        if item.get("control_message"):
            view = ItemStopView(item, active=True)
            await item["control_message"].edit(
                content="🎬 **Média en cours de diffusion...**", view=view
            )
    except Exception as error:
        print(f"Erreur activation prochain média : {error}")


@app.route("/get_next_meme", methods=["GET"])
def get_next_meme():
    global current_active_item
    requested_name = request.args.get("user", "").strip()
    user_id = resolve_api_user(requested_name)

    with data_lock:
        if user_id is None or user_id not in active_users:
            return jsonify({
                "url": None,
                "position": "center",
                "game_mode": False,
                "status": "inactive",
            })

        position = user_positions.get(user_id, "center")
        if position not in ("left", "center", "right"):
            position = "center"
        game_mode = bool(user_game_modes.get(user_id, False))

        if current_active_item:
            item_id = str(
                current_active_item.get("item_id")
                or getattr(current_active_item.get("message_obj"), "id", current_active_item["url"])
            )
            res_data = {
                "item_id": item_id,
                "name": current_active_item["name"],
                "avatar": current_active_item["avatar"],
                "content": current_active_item["content"],
                "url": current_active_item["url"],
                "position": position,
                "game_mode": game_mode,
                "status": "active",
            }
        else:
            res_data = {
                "url": None,
                "position": position,
                "game_mode": game_mode,
                "status": "active",
            }
    return jsonify(res_data)


@app.route("/pop_meme", methods=["POST"])
def pop_meme():
    global current_active_item, global_queue, last_pop_time
    requested_item_id = request.args.get("item_id", "").strip()
    with data_lock:
        # Ignore un pop tardif d'un média terminé/remplacé : il ne doit pas avancer la file.
        if requested_item_id:
            current_item_id = None
            if current_active_item:
                current_item_id = str(
                    current_active_item.get("item_id")
                    or getattr(current_active_item.get("message_obj"), "id", current_active_item.get("url", ""))
                )
            if current_item_id != requested_item_id:
                name = current_active_item["name"] if current_active_item else None
                return jsonify({"status": "stale", "next": name, "current_item_id": current_item_id})

        now = time.time()
        if now - last_pop_time < 1.5:
            name = current_active_item["name"] if current_active_item else None
            return jsonify({"status": "ignored", "next": name})

        last_pop_time = now
        if current_active_item:
            if current_active_item.get("control_message"):
                asyncio.run_coroutine_threadsafe(
                    safe_delete_msg(current_active_item["control_message"]), bot.loop
                )
            if global_queue:
                current_active_item = global_queue.pop(0)
                asyncio.run_coroutine_threadsafe(
                    activate_next_item_message(current_active_item), bot.loop
                )
            else:
                current_active_item = None
        name = current_active_item["name"] if current_active_item else None
    return jsonify({"status": "success", "next": name})


async def safe_delete_msg(message):
    try:
        await message.delete()
    except Exception:
        pass


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    print(f"[FLASK] Démarrage du serveur web sur le port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("[ERREUR] Token Discord introuvable !")
    else:
        print("[DISCORD] Connexion en cours...")
        bot.run(TOKEN)
