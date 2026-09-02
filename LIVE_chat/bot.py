import os
from threading import Thread
from flask import Flask, request
import discord

# --- CONFIGURATION FLASK (POUR RENDER & KEEP-ALIVE) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Je suis vivant !"

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- CONFIGURATION DU BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

CHANNEL_NAME = "live-chat"  # Nom de ton salon
SERVER_URL = "https://frail-astute-breeching.ngrok-free.dev"  # Ton lien ngrok actuel
live_chat_active = False

class StopButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Arrêter la lecture", style=discord.ButtonStyle.danger)
    async def stop_callback(self, interaction: discord.Interaction, button: discord.Button):
        global live_chat_active
        live_chat_active = False
        await interaction.response.send_message("Arrêt de la lecture des mèmes demandé.", ephemeral=True)

class LiveChatControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Activer/Désactiver", style=discord.ButtonStyle.primary)
    async def toggle_callback(self, interaction: discord.Interaction, button: discord.Button):
        global live_chat_active
        live_chat_active = not live_chat_active
        
        if live_chat_active:
            status_text = "🟢 Le Live Chat est ACTIF ! Les mèmes s'affichent sur l'écran."
            button.style = discord.ButtonStyle.success
        else:
            status_text = "🔴 Le Live Chat est INACTIF. Vous pouvez envoyer des mèmes, mais ils ne s'affichent pas."
            button.style = discord.ButtonStyle.secondary
            
        await interaction.response.edit_message(content=status_text, view=self)

@client.event
async def on_ready():
    print(f"Connecté en tant que {client.user}")
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == CHANNEL_NAME:
                live_chat_active = False
                view = LiveChatControlView()
                await channel.send("🔴 Le Live Chat est INACTIF. Vous pouvez envoyer des mèmes, mais ils ne s'affichent pas.", view=view)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.channel.name == CHANNEL_NAME:
        if live_chat_active:
            # Vérifie si le message contient une image ou une pièce jointe
            if message.attachments:
                image_url = message.attachments[0].url
                
                # Envoi des données vers ton PC via ngrok
                import requests
                try:
                    requests.post(f"{SERVER_URL}/send_meme", json={"url": image_url})
                except Exception as e:
                    print(f"Erreur d'envoi vers le serveur local : {e}")

# Lancement du serveur web Keep-Alive pour Render
keep_alive()

# Lancement du bot Discord
TOKEN = os.environ.get("DISCORD_TOKEN")
client.run(TOKEN)
