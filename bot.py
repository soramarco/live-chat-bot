import os
from threading import Thread
from flask import Flask, render_template, request
import discord

# --- CONFIGURATION FLASK & PAGE D'OVERLAY ---
app = Flask(__name__)

# Variables globales pour stocker le mème, l'auteur, sa photo et l'état du live
latest_meme_url = ""
latest_author_name = ""
latest_author_avatar = ""
live_chat_active = False

# Page web avec le design intégrant l'image, le pseudo et la photo de profil
OVERLAY_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Live Chat Overlay</title>
    <style>
        body { background-color: transparent; margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; overflow: hidden; font-family: sans-serif; }
        .container { display: flex; flex-direction: column; align-items: center; max-width: 90%; max-height: 90%; }
        .author-box { display: flex; align-items: center; background: rgba(0, 0, 0, 0.75); padding: 8px 16px; border-radius: 20px; margin-bottom: 10px; color: white; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
        .author-box img { width: 32px; height: 32px; border-radius: 50%; margin-right: 10px; object-fit: cover; }
        .author-box span { font-size: 16px; font-weight: bold; }
        #meme-img { max-width: 100%; max-height: 75vh; object-fit: contain; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }
    </style>
</head>
<body>
    <div class="container">
        <div id="author-box" class="author-box" style="display: none;">
            <img id="author-avatar" src="" alt="Avatar">
            <span id="author-name"></span>
        </div>
        <div>
            <img id="meme-img" src="" alt="">
        </div>
    </div>
    <script>
        setInterval(async () => {
            try {
                let res = await fetch('/get_meme');
                let data = await res.json();
                let img = document.getElementById('meme-img');
                let authorBox = document.getElementById('author-box');
                let authorAvatar = document.getElementById('author-avatar');
                let authorName = document.getElementById('author-name');

                if (data.url) {
                    img.src = data.url;
                    authorAvatar.src = data.avatar;
                    authorName.innerText = data.name;
                    authorBox.style.display = 'flex';
                } else {
                    img.src = "";
                    authorBox.style.display = 'none';
                }
            } catch (e) { console.error(e); }
        }, 2000);
    </script>
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
    global latest_meme_url, latest_author_name, latest_author_avatar, live_chat_active
    if not live_chat_active:
        return {"url": "", "name": "", "avatar": ""}
    return {
        "url": latest_meme_url,
        "name": latest_author_name,
        "avatar": latest_author_avatar
    }

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()


# --- CONFIGURATION DU BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

CHANNEL_NAME = "live-chat"

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
    print(f'Connecté en tant que {client.user}')
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
                global latest_meme_url, latest_author_name, latest_author_avatar
                latest_meme_url = message.attachments[0].url
                latest_author_name = message.author.display_name
                latest_author_avatar = message.author.display_avatar.url


# --- LANCEMENT GLOBAL ---
if __name__ == "__main__":
    # Lancement du serveur web en arrière-plan
    keep_alive()
    # Lancement du bot Discord
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if TOKEN:
        client.run(TOKEN)
