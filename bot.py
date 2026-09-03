import os
import asyncio
import threading
import time
from threading import Thread
import discord
from discord.ext import commands
from flask import Flask, jsonify, request

app = Flask(__name__)

media_queue = []
current_active_item = None
active_users = set()
user_positions = {}  # Stocke "left", "center", "right" par utilisateur
queue_lock = threading.Lock()

main_panel_message = None
cached_response = {"data": {"url": None}, "timestamp": 0}

def get_main_panel_content():
    with queue_lock:
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

async def update_persistent_panel():
    global main_panel_message
    if main_panel_message:
        try:
            view = MainPanelView()
            await main_panel_message.edit(content=get_main_panel_content(), view=view)
        except Exception as e:
            print(f"Erreur mise à jour auto du panneau : {e}")

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

    @discord.ui.button(label="Chargement...", style=discord.ButtonStyle.secondary, custom_id="toggle_personal_chat_persistent_v28", row=0)
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
            
        with queue_lock:
            if self.username in active_users:
                active_users.remove(self.username)
                self.is_active = False
            else:
                active_users.add(self.username)
                self.is_active = True
        
        self.update_button_styles()
        status_text = (
            f"🟢 **Ton Live Chat est ACTIF !** Position : **{user_positions.get(self.username, 'center').upper()}**" 
            if self.is_active 
            else "🔴 **Ton Live Chat est DÉSACTIVÉ.**"
        )
        
        try:
            await interaction.edit_original_response(content=status_text, view=self)
        except Exception as e:
            print(f"Erreur mise à jour interaction : {e}")

        asyncio.create_task(update_persistent_panel())

    @discord.ui.button(label="Gauche", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_left(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        with queue_lock:
            user_positions[self.username] = "left"
        self.update_button_styles()
        await self.update_response(interaction)

    @discord.ui.button(label="Centre", emoji="⏺️", style=discord.ButtonStyle.primary, row=1)
    async def btn_center(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        with queue_lock:
            user_positions[self.username] = "center"
        self.update_button_styles()
        await self.update_response(interaction)

    @discord.ui.button(label="Droite", emoji="➡️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_right(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        with queue_lock:
            user_positions[self.username] = "right"
        self.update_button_styles()
        await self.update_response(interaction)

    async def update_response(self, interaction):
        status_text = (
            f"🟢 **Ton Live Chat est ACTIF !** Position : **{user_positions.get(self.username, 'center').upper()}**" 
            if self.is_active 
            else f"🔴 **Ton Live Chat est DÉSACTIVÉ.** Position réglée sur : **{user_positions.get(self.username, 'center').upper()}**"
        )
        try:
            await interaction.edit_original_response(content=status_text, view=self)
        except Exception as e:
            print(f"Erreur mise à jour position : {e}")

class MainPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Gérer mon Live Chat", emoji="⚙️", style=discord.ButtonStyle.blurple, custom_id="main_manage_btn_persistent_v28")
    async def manage_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
            
        username = interaction.user.display_name
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

LIVE_CHANNEL_NAME = "live-chat"

@bot.event
async def on_ready():
    global main_panel_message
    print(f"Bot connecté en tant que {bot.user}")
    bot.add_view(MainPanelView())
    
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == LIVE_CHANNEL_NAME:
                try:
                    async for message in channel.history(limit=50):
                        if message.author == bot.user and "Panneau de contrôle du Live Chat" in message.content:
                            try:
                                await message.delete()
                            except Exception:
                                pass
                            break
                    
                    view = MainPanelView()
                    main_panel_message = await channel.send(get_main_panel_content(), view=view)
                except Exception as e:
                    print(f"Erreur envoi panneau initial : {e}")
                break

@bot.event
async def on_message(message):
    global current_active_item, media_queue, cached_response, main_panel_message
    
    if message.author.bot:
        return
        
    if message.channel.name == LIVE_CHANNEL_NAME:
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
            
            with queue_lock:
                if len(media_queue) > 15:
                    media_queue.pop(0)

                if current_active_item is None:
                    current_active_item = item
                    is_first = True
                else:
                    media_queue.append(item)
                    is_first = False
                
                cached_response["timestamp"] = 0

            bot.loop.create_task(send_control_message(item, is_active=is_first))

        try:
            async for old_msg in message.channel.history(limit=30):
                if old_msg.author == bot.user and "Panneau de contrôle du Live Chat" in old_msg.content:
                    try:
                        await old_msg.delete()
                    except Exception:
                        pass
                    break
            
            view = MainPanelView()
            main_panel_message = await message.channel.send(get_main_panel_content(), view=view)
        except Exception as e:
            print(f"Erreur déplacement panneau : {e}")

    await bot.process_commands(message)

class ItemStopView(discord.ui.View):
    def __init__(self, item_ref, active):
        super().__init__(timeout=86400)
        self.item_ref = item_ref
        self.stop_button.disabled = not active

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer()
        except Exception:
            pass
            
        global current_active_item, media_queue, cached_response
        with queue_lock:
            if current_active_item == self.item_ref:
                try:
                    if self.item_ref.get("control_message"):
                        await self.item_ref["control_message"].delete()
                except Exception:
                    pass
                
                if media_queue:
                    current_active_item = media_queue.pop(0)
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
    global current_active_item, media_queue, cached_response
    user = request.args.get("user", "").strip()
    
    if not user:
        return jsonify({"url": None})

    with queue_lock:
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
    global current_active_item, media_queue, cached_response
    with queue_lock:
        if current_active_item:
            if current_active_item.get("control_message"):
                asyncio.run_coroutine_threadsafe(safe_delete_msg(current_active_item["control_message"]), bot.loop)
            
            if media_queue:
                current_active_item = media_queue.pop(0)
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
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    TOKEN = os.environ.get("DISCORD_TOKEN", "TON_TOKEN_BOT_ICI")
    bot.run(TOKEN)
