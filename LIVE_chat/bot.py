import os
import requests
import discord
from discord.ext import commands
from flask import Flask
from threading import Thread

# --- CONFIGURATION DU SERVEUR WEB (KEEP ALIVE) ---
app = Flask('')

@app.route('/')
def home():
    return "Je suis vivant !"

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- TON CODE DE BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

CHANNEL_NAME = "general" # Remplace par le nom de ton salon si besoin
SERVER_URL = "http://127.0.0.1:5000" # Modifiable si tu utilises ngrok plus tard pour l'overlay local
live_chat_active = False

class StopButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Arrêter la lecture", style=discord.ButtonStyle.danger)
    async def stop_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        global live_chat_active
        live_chat_active = False
        await interaction.response.send_message("Arrêt de la lecture des mèmes demandé.", ephemeral=True)

class LiveChatControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Activer/Désactiver", style=discord.ButtonStyle.primary)
    async def toggle_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

@client.event
async def on_ready():
    print(f"Connecté en tant que {client.user}")

@client.event
async def on_message(message):
    global live_chat_active

    if message.author.bot:
        return

    if message.content.startswith("!setup_live") and message.channel.name == CHANNEL_NAME:
        view = LiveChatControlView()
        sent_msg = await message.channel.send("🔴 Le live Chat est INACTIF.** Vous pouvez envoyer des mèmes, mais ils ne s'affichent pas.", view=view)
        try:
            await sent_msg.pin()
            await message.delete()
        except Exception:
            pass
        return

    if message.channel.name == CHANNEL_NAME and message.attachments:
        if not live_chat_active:
            return

        sent_msg = await message.reply("En cours de lecture...", view=StopButtonView())
        data = {
            "url": message.attachments[0].url,
            "caption": message.content,
            "author_name": message.author.display_name,
            "author_avatar": str(message.author.avatar.url) if message.author.avatar else "",
            "channel_id": sent_msg.channel.id,
            "message_id": sent_msg.id
        }
        try:
            requests.post(f"{SERVER_URL}/send_meme", json=data, timeout=3)
        except Exception as e:
            print(f"Erreur envoi serveur : {e}")

# Lancement du serveur web pour Render puis du bot Discord
keep_alive()
TOKEN = os.environ.get("DISCORD_TOKEN")
client.run(TOKEN)
