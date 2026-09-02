import os
import discord
import requests

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

SERVER_URL = "http://127.0.0.1:5000"
CHANNEL_NAME = "live-chat"

# État global du live chat
live_chat_active = False

class StopButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛑 Arrêter / Passer", style=discord.ButtonStyle.danger)
    async def stop_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            requests.post(f"{SERVER_URL}/stop_meme", timeout=2)
            await interaction.response.defer()
            try:
                await interaction.message.delete()
            except Exception:
                pass
        except Exception:
            pass

class LiveChatControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔴 Live Chat Désactivé", style=discord.ButtonStyle.danger, custom_id="persistent_live_chat_toggle")
    async def toggle_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        global live_chat_active
        live_chat_active = not live_chat_active
        
        if live_chat_active:
            button.label = "🟢 Live Chat Activé"
            button.style = discord.ButtonStyle.success
            status_text = "🟢 **Le Live Chat est ACTIF.** Les mèmes s'affichent sur l'overlay."
        else:
            button.label = "🔴 Live Chat Désactivé"
            button.style = discord.ButtonStyle.danger
            status_text = "🔴 **Le Live Chat est INACTIF.** Vous pouvez envoyer des mèmes, mais ils ne s'affichent pas."

        await interaction.response.edit_message(content=status_text, view=self)

@client.event
async def on_ready():
    client.add_view(LiveChatControlView())
    print(f"Bot connecté en tant que {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("!setup_live") and message.channel.name == CHANNEL_NAME:
        view = LiveChatControlView()
        sent_msg = await message.channel.send("🔴 **Le Live Chat est INACTIF.** Vous pouvez envoyer des mèmes, mais ils ne s'affichent pas.", view=view)
        try:
            await sent_msg.pin()
            await message.delete()
        except Exception:
            pass
        return

    if message.channel.name == CHANNEL_NAME and message.attachments:
        if not live_chat_active:
            return

        sent_msg = await message.reply("🎬 En cours de lecture...", view=StopButtonView())
        
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

# Récupération sécurisée du token (via l'environnement)
TOKEN = os.environ.get("DISCORD_TOKEN")
client.run(TOKEN)