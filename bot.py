import os
import asyncio
from threading import Thread
import discord
from discord.ext import commands
from flask import Flask, jsonify, request

app = Flask(__name__)

media_queue = []
current_active_item = None

class StopView(discord.ui.View):
    def __init__(self, bot_instance, item_ref, is_active=False):
        super().__init__(timeout=None)
        self.bot_instance = bot_instance
        self.item_ref = item_ref
        self.stop_button.disabled = not is_active

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_active_item, media_queue
        
        try:
            if self.item_ref.get("control_message"):
                await self.item_ref["control_message"].delete()
        except Exception:
            pass
            
        if current_active_item == self.item_ref:
            # On passe directement au suivant s'il y en a un dans la file !
            if media_queue:
                current_active_item = media_queue.pop(0)
                asyncio.run_coroutine_threadsafe(activate_item_button(current_active_item), bot.loop)
            else:
                current_active_item = None
            
        await interaction.response.defer()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

LIVE_CHANNEL_NAME = "live-chat"

@bot.event
async def on_ready():
    print(f"Bot connecté en tant que {bot.user}")

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
        view = StopView(bot, item, is_active=is_active)
        status_text = "🎬 **Média en cours de diffusion sur l'overlay...**" if is_active else "⏳ **En attente dans la file...**"
        msg = await item["message_obj"].reply(status_text, view=view)
        item["control_message"] = msg
    except Exception as e:
        print(f"Impossible d'envoyer le bouton initial : {e}")

@app.route('/get_next_meme', methods=['GET'])
def get_next_meme():
    global current_active_item, media_queue
    
    # Si rien n'est en cours mais qu'il y a du monde dans la file
    if current_active_item is None and media_queue:
        current_active_item = media_queue.pop(0)
        asyncio.run_coroutine_threadsafe(activate_item_button(current_active_item), bot.loop)

    # On renvoie le média actif actuel (qu'il vienne d'être pris ou qu'il soit déjà en cours)
    if current_active_item:
        return jsonify({
            "name": current_active_item["name"],
            "avatar": current_active_item["avatar"],
            "content": current_active_item["content"],
            "url": current_active_item["url"]
        })
    
    return jsonify({"url": None})

async def activate_item_button(item):
    try:
        if item.get("control_message"):
            view = StopView(bot, item, is_active=True)
            await item["control_message"].edit(content="🎬 **Média en cours de diffusion sur l'overlay...**", view=view)
    except Exception as e:
        print(f"Erreur activation bouton: {e}")

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    TOKEN = os.environ.get("DISCORD_TOKEN", "TON_TOKEN_BOT_ICI")
    bot.run(TOKEN)
