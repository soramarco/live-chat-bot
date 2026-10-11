import os
import asyncio
import threading
import json
import time
from threading import Thread

import discord
from discord.ext import commands
from flask import Flask, jsonify, request

app = Flask(__name__)

STORAGE_FILE = "bot_storage.json"
TARGET_CHANNEL_ID = 1544497366814560318  # Salon Discord Live Chat

global_queue = []
current_active_item = None
active_users = set()
user_positions = {}
user_game_modes = {}
last_panel_user = None
panel_message = None
panel_channel_id = None
data_lock = threading.RLock()
last_pop_time = 0


def load_data():
    global user_positions, active_users, user_game_modes, last_panel_user
    if not os.path.exists(STORAGE_FILE):
        return
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        user_positions = data.get("user_positions", {})
        active_users = set(data.get("active_users", []))
        user_game_modes = data.get("user_game_modes", {})
        last_panel_user = data.get("last_panel_user")
        # Migration pour l'ancien fichier de stockage : si un seul utilisateur
        # est actif, on peut sans ambiguïté afficher ses vrais réglages.
        if not last_panel_user and len(active_users) == 1:
            last_panel_user = next(iter(active_users))
        print(
            f"[DATA] Données chargées : {len(user_positions)} utilisateur(s), "
            f"{len(active_users)} actif(s)."
        )
    except Exception as e:
        print(f"[ERREUR] Chargement stockage : {e}")


def swift_save_data():
    try:
        data = {
            "user_positions": user_positions,
            "active_users": sorted(active_users),
            "user_game_modes": user_game_modes,
            "last_panel_user": last_panel_user,
        }
        with open(STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[ERREUR] Sauvegarde stockage : {e}")


load_data()


def get_panel_username(preferred=None):
    """Un message Discord est partagé : il ne peut montrer qu'un profil à la fois.

    On conserve le profil de la dernière personne ayant utilisé les boutons.
    Si l'ancien stockage ne le connaît pas et qu'un seul utilisateur est actif,
    on utilise celui-ci pour éviter les boutons qui reviennent à leur état vierge.
    """
    global last_panel_user
    with data_lock:
        if preferred:
            return preferred
        if last_panel_user:
            return last_panel_user
        if len(active_users) == 1:
            last_panel_user = next(iter(active_users))
            swift_save_data()
            return last_panel_user
        return None


@app.route("/")
def home():
    return "Bot is alive!", 200


def get_channel_panel_content():
    with data_lock:
        count = len(active_users)
        users_str = ", ".join(f"**{u}**" for u in sorted(active_users)) if count else "_Personne_"
        displayed_user = get_panel_username()

    text = (
        f"📊 **Statut en direct** : {count} actif(s)\n"
        f"👤 **Connectés** : {users_str}\n"
    )
    if displayed_user:
        text += f"⚙️ **Réglages affichés pour** : {displayed_user}\n"
    return text + "\n👇 **Gère ton Live Chat ci-dessous :**"


class UnifiedControlView(discord.ui.View):
    def __init__(self, username=None):
        super().__init__(timeout=None)
        self.username = get_panel_username(username)
        self.update_button_styles()

    def update_button_styles(self):
        uname = self.username
        with data_lock:
            is_active = bool(uname and uname in active_users)
            is_gm = bool(uname and user_game_modes.get(uname, False))
            pos = user_positions.get(uname, "center") if uname else "center"

        if is_active:
            self.toggle_btn.label = "Désactiver mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.danger
            self.toggle_btn.emoji = "🔴"
        else:
            self.toggle_btn.label = "Activer mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.success
            self.toggle_btn.emoji = "🟢"

        if is_gm:
            self.btn_game_mode.label = "Mode Jeu : ACTIF"
            self.btn_game_mode.style = discord.ButtonStyle.success
        else:
            self.btn_game_mode.label = "Mode Jeu : INACTIF"
            self.btn_game_mode.style = discord.ButtonStyle.secondary
        self.btn_game_mode.emoji = "🎮"

        self.btn_left.style = discord.ButtonStyle.primary if pos == "left" else discord.ButtonStyle.secondary
        self.btn_center.style = discord.ButtonStyle.primary if pos == "center" else discord.ButtonStyle.secondary
        self.btn_right.style = discord.ButtonStyle.primary if pos == "right" else discord.ButtonStyle.secondary

    def remember_user(self, username):
        global last_panel_user
        self.username = username
        last_panel_user = username

    @discord.ui.button(
        label="Chargement...", style=discord.ButtonStyle.secondary,
        row=0, custom_id="unified_toggle_btn"
    )
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        uname = interaction.user.display_name
        with data_lock:
            self.remember_user(uname)
            if uname in active_users:
                active_users.remove(uname)
            else:
                active_users.add(uname)
            swift_save_data()
        self.update_button_styles()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Mode Jeu", emoji="🎮", style=discord.ButtonStyle.secondary,
        row=0, custom_id="unified_game_mode_btn"
    )
    async def btn_game_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        uname = interaction.user.display_name
        with data_lock:
            self.remember_user(uname)
            user_game_modes[uname] = not user_game_modes.get(uname, False)
            swift_save_data()
        self.update_button_styles()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Gauche", emoji="⬅️", style=discord.ButtonStyle.secondary,
        row=1, custom_id="unified_left_btn"
    )
    async def btn_left(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        uname = interaction.user.display_name
        with data_lock:
            self.remember_user(uname)
            user_positions[uname] = "left"
            swift_save_data()
        self.update_button_styles()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Centre", emoji="⏺️", style=discord.ButtonStyle.primary,
        row=1, custom_id="unified_center_btn"
    )
    async def btn_center(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        uname = interaction.user.display_name
        with data_lock:
            self.remember_user(uname)
            user_positions[uname] = "center"
            swift_save_data()
        self.update_button_styles()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Droite", emoji="➡️", style=discord.ButtonStyle.secondary,
        row=1, custom_id="unified_right_btn"
    )
    async def btn_right(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        uname = interaction.user.display_name
        with data_lock:
            self.remember_user(uname)
            user_positions[uname] = "right"
            swift_save_data()
        self.update_button_styles()
        await self.update_message(interaction)

    async def update_message(self, interaction):
        try:
            content = get_channel_panel_content()
            await interaction.message.edit(content=content, view=self)
            global panel_message, panel_channel_id
            panel_message = interaction.message
            panel_channel_id = interaction.message.channel.id
        except Exception as e:
            print(f"Erreur mise à jour message unifié : {e}")


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def is_target_channel(channel):
    return channel.id == TARGET_CHANNEL_ID


async def repost_bottom_panel(channel):
    """Met à jour le panneau en place au lieu d'en créer un vierge à chaque message."""
    global panel_message, panel_channel_id
    try:
        chosen_user = get_panel_username()
        content = get_channel_panel_content()
        view = UnifiedControlView(chosen_user)

        if panel_message is not None and panel_channel_id == channel.id:
            try:
                await panel_message.edit(content=content, view=view)
                return
            except Exception:
                panel_message = None
                panel_channel_id = None

        # Après un redémarrage, retrouver le panneau déjà présent si possible.
        existing = []
        async for old_msg in channel.history(limit=50):
            if old_msg.author == bot.user and "Statut en direct" in old_msg.content:
                existing.append(old_msg)

        existing.sort(key=lambda msg: msg.created_at, reverse=True)
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
    except Exception as e:
        print(f"Erreur mise à jour panneau bas : {e}")


@bot.event
async def on_ready():
    print(f"[DISCORD] Connexion réussie en tant que bot ({bot.user})")
    bot.add_view(UnifiedControlView())
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        await repost_bottom_panel(channel)
    else:
        print(f"[ERREUR] Salon cible {TARGET_CHANNEL_ID} introuvable dans le cache du bot.")


@bot.event
async def on_message(message):
    global current_active_item, global_queue
    if message.author.bot:
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
            bot.loop.create_task(send_control_message(item, is_active=is_first))

        # Même après un nouveau média, garder les réglages du dernier utilisateur
        # et éditer le même message : ne pas réinitialiser les boutons.
        await repost_bottom_panel(message.channel)

    await bot.process_commands(message)


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
            now = time.time()
            if now - last_pop_time < 1.5:
                return
            last_pop_time = now
            if current_active_item == self.item_ref:
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
        msg = await item["message_obj"].reply(status_text, view=view)
        item["control_message"] = msg
    except Exception as e:
        print(f"Erreur critique en envoyant le message de contrôle : {e}")


async def activate_next_item_message(item):
    try:
        if item.get("control_message"):
            view = ItemStopView(item, active=True)
            await item["control_message"].edit(
                content="🎬 **Média en cours de diffusion...**", view=view
            )
    except Exception as e:
        print(f"Erreur activation prochain message : {e}")


@app.route("/get_next_meme", methods=["GET"])
def get_next_meme():
    global current_active_item
    user = request.args.get("user", "").strip()
    if not user:
        return jsonify({"url": None, "status": "inactive"})

    with data_lock:
        if user not in active_users:
            return jsonify({"url": None, "status": "inactive"})
        position = user_positions.get(user, "center")
        game_mode = user_game_modes.get(user, False)
        if current_active_item:
            res_data = {
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
    user = request.args.get("user", "").strip()
    with data_lock:
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


async def safe_delete_msg(msg):
    try:
        await msg.delete()
    except Exception:
        pass


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    print(f"[FLASK] Démarrage du serveur web sur le port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("[ERREUR] Token Discord introuvable !")
    else:
        print("[DISCORD] Connexion en cours...")
        bot.run(TOKEN)
