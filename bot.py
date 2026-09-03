import os
from threading import Thread
from flask import Flask, render_template_string
import discord
from collections import deque

# --- CONFIGURATION FLASK & PAGE D'OVERLAY ---
app = Flask(__name__)

meme_queue = deque()
live_chat_active = False

OVERLAY_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Live Chat Overlay</title>
    <style>
        body { background-color: transparent; margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; overflow: hidden; font-family: sans-serif; }
        .container { display: flex; flex-direction: column; align-items: center; max-width: 90%; max-height: 90%; }
        .author-box { display: flex; align-items: center; background: rgba(15, 15, 15, 0.94); padding: 8px 16px; border-radius: 23px; margin-bottom: 12px; color: white; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
        .author-box img { width: 38px; height: 38px; border-radius: 50%; margin-right: 10px; object-fit: cover; }
        .author-box span { font-size: 20px; font-weight: bold; }
        #media-container { max-width: 100%; max-height: 65vh; display: flex; justify-content: center; }
        #meme-img, #meme-video { max-width: 100%; max-height: 65vh; object-fit: contain; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); display: none; }
        .text-box { background: rgba(0, 0, 0, 0.9); padding: 12px 24px; border-radius: 14px; margin-top: 12px; color: white; font-size: 26px; font-weight: bold; text-align: center; max-width: 1100px; word-break: break-word; box-shadow: 0 4px 15px rgba(0,0,0,0.4); display: none; }
    </style>
</head>
<body>
    <div class="container">
        <div id="author-box" class="author-box" style="display: none;">
            <img id="author-avatar" src="" alt="Avatar">
            <span id="author-name"></span>
        </div>
        <div id="media-container">
            <img id="meme-img" src="" alt="">
            <video id="meme-video" autoplay controls style="display:none;"></video>
        </div>
        <div id="text-box" class="text-box"></div>
    </div>
    <script>
        let isDisplaying = false;

        async function pollQueue() {
            if (isDisplaying) return;
            try {
                let res = await fetch('/get_next_meme');
                let data = await res.json();

                if (data.url) {
                    isDisplaying = true;
                    let authorBox = document.getElementById('author-box');
                    let authorAvatar = document.getElementById('author-avatar');
                    let authorName = document.getElementById('author-name');
                    let img = document.getElementById('meme-img');
                    let video = document.getElementById('meme-video');
                    let textBox = document.getElementById('text-box');

                    authorAvatar.src = data.avatar;
                    authorName.innerText = data.name;
                    authorBox.style.display = 'flex';

                    // Gestion du texte Discord en dessous
                    if (data.content && data.content.trim() !== "") {
                        textBox.innerText = data.content;
                        textBox.style.display = 'block';
                    } else {
                        textBox.style.display = 'none';
                    }

                    let lowerUrl = data.url.toLowerCase();
                    let isVideo = lowerUrl.includes('.mp4') || lowerUrl.includes('.webm') || lowerUrl.includes('.mov');

                    if (isVideo) {
                        video.src = data.url;
                        video.style.display = 'block';
                        img.style.display = 'none';
                        video.load();
                        video.play().catch(e => console.log("Erreur lecture auto video:", e));
                        video.onended = () => hideMedia();
                    } else {
                        img.src = data.url;
                        img.style.display = 'block';
                        video.style.display = 'none';
                        setTimeout(() => hideMedia(), 9000);
                    }
                }
            } catch (e) { console.error(e); }
        }

        function hideMedia() {
            document.getElementById('author-box').style.display = 'none';
            document.getElementById('meme-img').style.display = 'none';
            let video = document.getElementById('meme-video');
            video.style.display = 'none';
            video.pause();
            video.src = '';
            document.getElementById('meme-img').src = '';
            document.getElementById('text-box').style.display = 'none';
            isDisplaying = false;
        }

        setInterval(pollQueue, 1500);
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

@app.route('/get_next_meme')
def get_next_meme():
    global live_chat_active, meme_queue
    if not live_chat_active or not meme_queue:
        return {"url": "", "name": "", "avatar": "", "content": ""}
    
    item = meme_queue.popleft()
    return {
        "url": item["url"],
        "name": item["name"],
        "avatar": item["avatar"],
        "content": item["content"]
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
        global live_chat_active, meme_queue
        live_chat_active = not live_chat_active

        if live_chat_active:
            status_text = "🟢 Le Live Chat est ACTIF ! Les mèmes et vidéos s'affichent."
            button.style = discord.ButtonStyle.success
        else:
            status_text = "🔴 Le Live Chat est INACTIF. Tout est mis en pause et la file est vidée."
            button.style = discord.ButtonStyle.secondary
            meme_queue.clear()

        await interaction.response.edit_message(content=status_text, view=self)

@client.event
async def on_ready():
    print(f'Connecté en tant que {client.user}')
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == CHANNEL_NAME:
                global live_chat_active
                live_chat_active = False
                meme_queue.clear()
                view = LiveChatControlView()
                await channel.send("🔴 Le Live Chat est INACTIF. Vous pouvez envoyer des mèmes, mais ils ne s'affichent pas.", view=view)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.channel.name == CHANNEL_NAME:
        if live_chat_active:
            if message.attachments:
                global meme_queue
                meme_data = {
                    "url": message.attachments[0].url,
                    "name": message.author.display_name,
                    "avatar": message.author.display_avatar.url,
                    "content": message.content  # <--- Récupération du texte du message !
                }
                meme_queue.append(meme_data)


if __name__ == "__main__":
    keep_alive()
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if TOKEN:
        client.run(TOKEN)
