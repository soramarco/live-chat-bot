import os
import asyncio
import threading
import json
from threading import Thread
import discord
from discord.ext import commands
from flask import Flask, jsonify, request

app = Flask(__name__)

STORAGE_FILE = "bot_storage.json"

# ID exact du salon #live-chat récupéré depuis ton lien Discord
TARGET_CHANNEL_ID = 1544497366814560318  

global_queue = []
current_active_item = None
active_users = set()
user_positions = {}
data_lock = threading.Lock()

main_panel_message = None
cached_response = {"data": {"url": None}, "timestamp": 0}

def load_data():
    global user_positions, active_users
    if os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                user_positions = data.get("user_positions", {})
                active_users = set(data.get("active_users", []))
                print(f"[DATA] Données chargées : {len(user_positions)} utilisateur(s), {len(active_users)} actif(s).")
        except Exception as e:
            print(f"[ERREUR] Chargement stockage : {e}")

def save_data():
    try:
        data = {
            "user_positions": user_positions,
            "active_users": list(active_users)
        }
        with open(STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[ERREUR] Sauvegarde stockage : {e}")

load_data()

@app.route('/')
def home():
    return "Bot is alive!", 200

def get_main_panel_content():
    with data_lock:
        count = len(active_users)
        if count == 0:
            users_str = "_Personne_"
        else:
            users_str = ", ".join(f"**{u}**" for u in active_users)
    return (
        "🎛️ **Panneau de contrôle du Live Chat**\n"
        f"📊 **Statut en direct** : {count} actif(s)\n"
        f"👤 **Connectés** : {users_str}\n\n"
        "Clique sur le bouton ci-dessous pour gérer ton affichage et ta position :"
    )

async def refresh_or_repost_panel(channel):
    global main_panel_message
    view = MainPanelView()
    content = get_main_panel_content()
    
    try:
        if main_panel_message:
            try:
                await main_panel_message.edit(content=content, view=view)
                return
            except Exception:
                main_panel_message = None

        async for message in channel.history(limit=30):
            if message.author == bot.user and ("Panneau de contrôle du Live Chat" in message.content or "Gérer mon Live Chat" in message.content):
                try:
                    await message.delete()
                except Exception:
                    pass
                    
        main_panel_message = await channel.send(content, view=view)
    except Exception as e:
        print(f"Erreur rafraîchissement panneau : {e}")

class PersonalControlView(discord.ui.View):
    def __init__(self, is_active, username):
        super().__init__(timeout=180)
        self.is_active = is_active
        self.username = username
        self.update_button_styles()

    def update_button_styles(self):
        if self.is_active:
            self.toggle_btn.label = "Désactiver mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.danger
            self.toggle_btn.emoji = "🔴"
        else:
            self.toggle_btn.label = "Activer mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.success
            self.toggle_btn.emoji = "🟢"

        pos = user_positions.get(self.username, "center")
        self.btn_left.style = discord.ButtonStyle.primary if pos == "left" else discord.ButtonStyle.secondary
        self.btn_center.style = discord.ButtonStyle.primary if pos == "center" else discord.ButtonStyle.secondary
        self.btn_right.style = discord.ButtonStyle.primary if pos == "right" else discord.ButtonStyle.secondary

    @discord.ui.button(label="Chargement...", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
            
        with data_lock:
            if self.username in active_users:
                active_users.remove(self.username)
                self.is_active = False
            else:
                active_users.add(self.username)
                self.is_active = True
            save_data()
        
        self.update_button_styles()
        
        current_pos = user_positions.get(self.username, 'center').upper()
        status_text = (
            f"🟢 **Ton Live Chat est ACTIF !** Position : **{current_pos}**" 
            if self.is_active 
            else f"🔴 **Ton Live Chat est DÉSACTIVÉ.** Position actuelle : **{current_pos}**"
        )
        
        try:
            await interaction.edit_original_response(content=status_text, view=self)
        except Exception as e:
            print(f"Erreur mise à jour interaction : {e}")

        if main_panel_message:
            asyncio.create_task(refresh_or_repost_panel(main_panel_message.channel))

    @discord.ui.button(label="Gauche", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_left(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        with data_lock:
            user_positions[self.username] = "left"
            save_data()
        self.update_button_styles()
        await self.update_response(interaction)

    @discord.ui.button(label="Centre", emoji="⏺️", style=discord.ButtonStyle.primary, row=1)
    async def btn_center(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        with data_lock:
            user_positions[self.username] = "center"
            save_data()
        self.update_button_styles()
        await self.update_response(interaction)

    @discord.ui.button(label="Droite", emoji="➡️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_right(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        with data_lock:
            user_positions[self.username] = "right"
            save_data()
        self.update_button_styles()
        await self.update_response(interaction)

    async def update_response(self, interaction):
        current_pos = user_positions.get(self.username, 'center').upper()
        status_text = (
            f"🟢 **Ton Live Chat est ACTIF !** Position : **{current_pos}**" 
            if self.is_active 
            else f"🔴 **Ton Live Chat est DÉSACTIVÉ.** Position actuelle : **{current_pos}**"
        )
        try:
            await interaction.edit_original_response(content=status_text, view=self)
        except Exception as e:
            print(f"Erreur mise à jour position : {e}")

class MainPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Gérer mon Live Chat", emoji="⚙️", style=discord.ButtonStyle.blurple, custom_id="main_manage_btn_persistent_v66")
    async def manage_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
            
        username = interaction.user.display_name
        with data_lock:
            is_active = username in active_users
            current_pos = user_positions.get(username, "center")
        
        status_text = (
            f"🟢 **Ton Live Chat est ACTIF !** Position : **{current_pos.upper()}**" 
            if is_active 
            else f"🔴 **Ton Live Chat est DÉSACTIVÉ.** Position actuelle : **{current_pos.upper()}**"
        )
        view = PersonalControlView(is_active, username)
        try:
            await interaction.followup.send(content=status_text, view=view, ephemeral=True)
        except Exception as e:
            print(f"Erreur d'affichage du panneau : {e}")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def is_target_channel(channel):
    return channel.id == TARGET_CHANNEL_ID

@bot.event
async def on_ready():
    global main_panel_message
    print(f"[DISCORD] Bot connecté en tant que {bot.user}")

    bot.add_view(MainPanelView())
    
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        await refresh_or_repost_panel(channel)
    else:
        print("[AVERTISSEMENT] Le salon cible introuvable avec cet ID !")

@bot.event
async def on_message(message):
    global current_active_item, global_queue, cached_response
    
    if message.author.bot:
        return
        
    if is_target_channel(message.channel):
        media_url = ""
        if message.attachments:
            media_url = message.attachments[0].url
        elif "http" in message.content:
            words = message.content.split()
            for w in words:
                if w.startswith("http"):
                    media_url = w
                    break
                    
        if media_url:
            item = {
                "name": message.author.display_name,
                "avatar": str(message.author.avatar.url) if message.author.avatar else "",
                "content": message.content.replace(media_url, "").strip(),
                "url": media_url,
                "message_obj": message,
                "control_message": None
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
                
                cached_response["timestamp"] = 0

            bot.loop.create_task(send_control_message(item, is_active=is_first))

        await asyncio.sleep(0.3)
        await refresh_or_repost_panel(message.channel)

    await bot.process_commands(message)

class ItemStopView(discord.ui.View):
    def __init__(self, item_ref, active):
        super().__init__(timeout=86400)
        self.item_ref = item_ref
        self.stop_button.disabled = not active

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
            
        global current_active_item, global_queue, cached_response
        with data_lock:
            if current_active_item == self.item_ref:
                try:
                    if self.item_ref.get("control_message"):
                        await self.item_ref["control_message"].delete()
                except Exception:
                    pass
                
                if global_queue:
                    current_active_item = global_queue.pop(0)
                    asyncio.run_coroutine_threadsafe(activate_next_item_message(current_active_item), bot.loop)
                else:
                    current_active_item = None
                cached_response["timestamp"] = 0

async def send_control_message(item, is_active):
    try:
        view = ItemStopView(item, is_active)
        status_text = "🎬 **Média en cours de diffusion...**" if is_active else "⏳ **En attente dans la file...**"
        msg = await item["message_obj"].reply(status_text, view=view)
        item["control_message"] = msg
    except Exception as e:
        print(f"Erreur critique en envoyant le message de contrôle : {e}")

async def activate_next_item_message(item):
    try:
        if item.get("control_message"):
            view = ItemStopView(item, active=True)
            await item["control_message"].edit(content="🎬 **Média en cours de diffusion...**", view=view)
    except Exception as e:
        print(f"Erreur activation prochain message : {e}")

@app.route('/get_next_meme', methods=['GET'])
def get_next_meme():
    global current_active_item, global_queue, cached_response
    user = request.args.get("user", "").strip()
    
    if not user:
        return jsonify({"url": None})

    with data_lock:
        if user not in active_users:
            return jsonify({"url": None, "status": "inactive"})

        position = user_positions.get(user, "center")

        if current_active_item:
            res_data = {
                "name": current_active_item["name"],
                "avatar": current_active_item["avatar"],
                "content": current_active_item["content"],
                "url": current_active_item["url"],
                "position": position
            }
        else:
            res_data = {"url": None, "position": position}

    return jsonify(res_data)

@app.route('/pop_meme', methods=['POST'])
def pop_meme():
    global current_active_item, global_queue, cached_response
    with data_lock:
        if current_active_item:
            if current_active_item.get("control_message"):
                asyncio.run_coroutine_threadsafe(safe_delete_msg(current_active_item["control_message"]), bot.loop)
            
            if global_queue:
                current_active_item = global_queue.pop(0)
                asyncio.run_coroutine_threadsafe(activate_next_item_message(current_active_item), bot.loop)
            else:
                current_active_item = None
            cached_response["timestamp"] = 0
            
    return jsonify({"status": "success"})

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
        print("[DISCORD] Connexion...")
        bot.run(TOKEN)
    
