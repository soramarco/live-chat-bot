import os
from threading import Thread
from flask import Flask, render_template_string, request
import discord

# --- CONFIGURATION FLASK & PAGE WEB D'OVERLAY ---
app = Flask(__name__)

# Variable globale pour stocker le dernier mème et l'état du live
latest_meme_url = ""
live_chat_active = False

# Page web super simple affichée pour l'overlay de tout le monde
OVERLAY_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Live Chat Overlay</title>
    <style>
        body { background-color: transparent; margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; overflow: hidden; }
        img { max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }
    </style>
    <script>
        // Actualisation automatique toutes les 2 secondes pour récupérer le nouveau mème
        setInterval(async () => {
            try {
                let res = await fetch('/get_meme');
                let data = await res.json();
                let img = document.getElementById('meme-img');
                if (data.url && data.url !== img.src) {
                    img.src = data.url;
                } else if (!data.url) {
                    img.src = "";
                }
            } catch (e) { console.error(e); }
        }, 2000);
    </script>
</head>
<body>
    <img id="meme-img" src="" alt="">
</body>
</html>
"""

@app.route('/')
def home():
    return "Bot et Overlay actifs !"

@app.route('/overlay')
def overlay():
    return render_template_string(OVERLAY_HTML)

@app.route('/get_meme')
def get_meme():
    global latest_meme_url, live_chat_active
    if not live_chat_active:
        return {"url": ""}
    return {"url": latest_meme_url}

@app.route('/send_meme', methods=['POST'])
def receive_meme():
    global latest_meme_url
    data = request.json
    if data and "url" in data:
        latest_meme_url = data["url"]
    return {"status": "success"}

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

CHANNEL_NAME = "live-chat"

class StopButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Arrêter la lecture", style=discord.ButtonStyle.danger)
    async def stop_callback(self, interaction: discord.Interaction, button: discord.Button):
        global live_chat_active, latest_meme_url
        live_chat_active = False
        latest_meme_url = ""
        await interaction.response.send_message("Arrêt de la lecture des mèmes demandé.", ephemeral=True)

class LiveChatControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Activer/Désactiver", style=discord.ButtonStyle.primary)
    async def toggle_callback(self, interaction: discord.Interaction, button: discord.Button):
        global live_chat_active, latest_meme_url
        live_chat_active = not live_chat_active
        
        if live_chat_active:
            status_text = "🟢 Le Live Chat est ACTIF ! Les mèmes s'affichent sur l'écran."
            button.style = discord.ButtonStyle.success
        else:
            status_text = "🔴 Le Live Chat est INACTIF. Vous pouvez envoyer des mèmes, mais ils ne s'affichent pas."
            button.style = discord.ButtonStyle.secondary
            latest_meme_url = ""
            
        await interaction.response.edit_message(content=status_text, view=self)

@client.event
async def on_ready():
    print(f"Connecté en tant que {client.user}")
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == CHANNEL_NAME:
                global live_chat_active
                live_chat_active = False
                view = LiveChatControlView()
                await channel.send("🔴 Le Live Chat est INACTIF. Vous pouvez envoyer des mèmes, mais ils ne s'affichent pas.", view=view)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.channel.name == CHANNEL_NAME:
        if live_chat_active:
            if message.attachments:
                global latest_meme_url
                latest_meme_url = message.attachments[0].url

# Lancement du serveur web sur Render
keep_alive()

# Lancement du bot Discord
TOKEN = os.environ.get("DISCORD_TOKEN")
client.run(TOKEN)
