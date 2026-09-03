# bot.py
import os
import asyncio
from threading import Thread
import discord
from discord.ext import commands
from flask import Flask, jsonify, request

app = Flask(__name__)

# File d'attente globale pour les médias du live chat
media_queue = []
current_active_item = None
active_message = None
active_view = None

class StopView(discord.ui.View):
    def __init__(self, bot_instance):
        super().__init__(timeout=None)
        self.bot_instance = bot_instance

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_active_item, active_message, active_view
        
        # Désactiver le bouton
        button.disabled = True
        button.label = "Arrêté"
        for child in self.children:
            child.disabled = True
            
        try:
            if active_message:
                await active_message.edit(view=self)
        except Exception:
            pass
            
        # Forcer le passage au suivant
        current_active_item = None
        
        await interaction.response.send_message("Média arrêté et skippé avec succès !", ephemeral=True)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

LIVE_CHANNEL_NAME = "live-chat"  # Nom du channel cible

@bot.event
async def on_ready():
    print(f"Bot connecté en tant que {bot.user}")

@bot.event
async def on_message(message):
    global current_active_item, media_queue, active_message, active_view
    
    if message.author.bot:
        return
        
    if message.channel.name == LIVE_CHANNEL_NAME:
        media_url = ""
        if message.attachments:
            media_url = message.attachments[0].url
        elif "http" in message.content:
            # Extraction basique d'un lien si besoin
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
                "message_obj": message
            }
            media_queue.append(item)
            print(f"[BOT] Média ajouté à la file d'attente : {item['name']}")

    await bot.process_commands(message)

@app.route('/get_next_meme', methods=['GET'])
def get_next_meme():
    global current_active_item, media_queue, active_message, active_view
    
    # Si aucun média n'est en cours, on prend le suivant dans la file
    if current_active_item is None and media_queue:
        current_active_item = media_queue.pop(0)
        
        # Envoyer le bouton interactif sur Discord de manière asynchrone dans la boucle du bot
        future = asyncio.run_coroutine_threadsafe(send_stop_button_to_discord(current_active_item), bot.loop)
        try:
            future.result(timeout=5)
        except Exception as e:
            print(f"Erreur envoi bouton Discord: {e}")

    if current_active_item:
        return jsonify({
            "name": current_active_item["name"],
            "avatar": current_active_item["avatar"],
            "content": current_active_item["content"],
            "url": current_active_item["url"]
        })
    
    return jsonify({"url": None})

async def send_stop_button_to_discord(item):
    global active_message, active_view
    try:
        msg = item["message_obj"]
        active_view = StopView(bot)
        active_message = await msg.reply("🎬 **Média en cours de diffusion sur l'overlay...**", view=active_view)
    except Exception as e:
        print(f"Impossible d'envoyer le bouton sur le message d'origine : {e}")

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # Lancement du serveur web Flask en arrière-plan pour l'overlay
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    TOKEN = os.environ.get("DISCORD_TOKEN", "TON_TOKEN_BOT_ICI")
    bot.run(TOKEN)
