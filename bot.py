import os
import asyncio
from threading import Thread
import discord
from discord.ext import commands
from flask import Flask, jsonify, request

app = Flask(__name__)

media_queue = []
current_active_item = None
active_users = set()

class PersonalControlView(discord.ui.View):
    def __init__(self, is_active):
        super().__init__(timeout=180)
        self.is_active = is_active
        self.update_button_style()

    def update_button_style(self):
        if self.is_active:
            self.toggle_btn.label = "Désactiver mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.danger
            self.toggle_btn.emoji = "🔴"
        else:
            self.toggle_btn.label = "Activer mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.success
            self.toggle_btn.emoji = "🟢"

    @discord.ui.button(label="Chargement...", style=discord.ButtonStyle.secondary, custom_id="toggle_personal_chat_persistent_v7")
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        username = interaction.user.display_name
        
        if username in active_users:
            active_users.remove(username)
            self.is_active = False
        else:
            active_users.add(username)
            self.is_active = True
        
        self.update_button_style()
        
        status_text = (
            "🟢 **Ton Live Chat est ACTIF !** Les médias s'affichent sur ton PC." 
            if self.is_active 
            else "🔴 **Ton Live Chat est DÉSACTIVÉ.**"
        )
        
        try:
            await interaction.edit_original_response(content=status_text, view=self)
        except Exception:
            pass

class MainPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Gérer mon Live Chat", emoji="⚙️", style=discord.ButtonStyle.blurple, custom_id="main_manage_btn_persistent_v7")
    async def manage_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        username = interaction.user.display_name
        is_active = username in active_users
        
        status_text = (
            "🟢 **Ton Live Chat est ACTIF !** Les médias s'affichent sur ton PC." 
            if is_active 
            else "🔴 **Ton Live Chat est DÉSACTIVÉ.**"
        )
        
        view = PersonalControlView(is_active)
        
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
    print(f"Bot connecté en tant que {bot.user}")
    # Enregistre la vue de manière persistante
    bot.add_view(MainPanelView())
    
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == LIVE_CHANNEL_NAME:
                # Nettoie les anciens messages du bot pour éviter d'accumuler des boutons morts
                try:
                    async for message in channel.history(limit=10):
                        if message.author == bot.user and message.components:
                            await message.delete()
                except Exception as e:
                    print(f"Erreur nettoyage ancien panneau : {e}")
                
                # Envoie un tout nouveau panneau propre et fonctionnel
                view = MainPanelView()
                content_text = "🎛️ **Panneau de contrôle du Live Chat**\nClique sur le bouton ci-dessous pour gérer ton affichage personnel :"
                await channel.send(content_text, view=view)

@bot.event
async def on_message(message):
    global current_active_item, media_queue
    
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
            
            is_first_ever = (current_active_item is None and len(media_queue) == 0)
            media_queue.append(item)
            
            bot.loop.create_task(send_initial_button(item, is_active=is_first_ever))

    await bot.process_commands(message)

async def send_initial_button(item, is_active):
    try:
        class ItemStopView(discord.ui.View):
            def __init__(self, item_ref, active):
                super().__init__(timeout=None)
                self.item_ref = item_ref
                self.stop_button.disabled = not active

            @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="stop_btn_dynamic")
            async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.defer()
                global current_active_item, media_queue
                try:
                    if self.item_ref.get("control_message"):
                        await self.item_ref["control_message"].delete()
                except Exception:
                    pass
                if current_active_item == self.item_ref:
                    if media_queue:
                        current_active_item = media_queue.pop(0)
                        asyncio.run_coroutine_threadsafe(activate_item_button(current_active_item), bot.loop)
                    else:
                        current_active_item = None

        view = ItemStopView(item, is_active)
        status_text = "🎬 **Média en cours de diffusion...**" if is_active else "⏳ **En attente dans la file...**"
        msg = await item["message_obj"].reply(status_text, view=view)
        item["control_message"] = msg
    except Exception as e:
        print(f"Erreur bouton initial : {e}")

async def activate_item_button(item):
    try:
        class ItemStopView(discord.ui.View):
            def __init__(self, item_ref, active):
                super().__init__(timeout=None)
                self.item_ref = item_ref
            @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="stop_btn_dynamic_active")
            async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.defer()
                global current_active_item, media_queue
                try:
                    if self.item_ref.get("control_message"):
                        await self.item_ref["control_message"].delete()
                except Exception:
                    pass
                if current_active_item == self.item_ref:
                    if media_queue:
                        current_active_item = media_queue.pop(0)
                        asyncio.run_coroutine_threadsafe(activate_item_button(current_active_item), bot.loop)
                    else:
                        current_active_item = None

        if item.get("control_message"):
            view = ItemStopView(item, True)
            await item["control_message"].edit(content="🎬 **Média en cours de diffusion...**", view=view)
    except Exception as e:
        print(f"Erreur activation : {e}")

@app.route('/get_next_meme', methods=['GET'])
def get_next_meme():
    global current_active_item, media_queue
    
    user = request.args.get("user", "").strip()
    
    if not user or user not in active_users:
        return jsonify({"url": None})

    if current_active_item is None and media_queue:
        current_active_item = media_queue.pop(0)
        asyncio.run_coroutine_threadsafe(activate_item_button(current_active_item), bot.loop)

    if current_active_item:
        if current_active_item["name"].lower() == user.lower():
            return jsonify({"url": None})
            
        return jsonify({
            "name": current_active_item["name"],
            "avatar": current_active_item["avatar"],
            "content": current_active_item["content"],
            "url": current_active_item["url"]
        })
    
    return jsonify({"url": None})

@app.route('/pop_meme', methods=['POST'])
def pop_meme():
    global current_active_item, media_queue
    if current_active_item:
        try:
            if current_active_item.get("control_message"):
                asyncio.run_coroutine_threadsafe(current_active_item["control_message"].delete(), bot.loop)
        except:
            pass
        
        if media_queue:
            current_active_item = media_queue.pop(0)
            asyncio.run_coroutine_threadsafe(activate_item_button(current_active_item), bot.loop)
        else:
            current_active_item = None
            
    return jsonify({"status": "success"})

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    TOKEN = os.environ.get("DISCORD_TOKEN", "TON_TOKEN_BOT_ICI")
    bot.run(TOKEN)
    
    TOKEN = os.environ.get("DISCORD_TOKEN", "TON_TOKEN_BOT_ICI")
    bot.run(TOKEN)
