import os
import asyncio
from threading import Thread
import discord
from discord.ext import commands
from flask import Flask, jsonify, request

app = Flask(__name__)

media_queue = []
current_active_item = None
active_users = set()  # Stocke les utilisateurs ayant activé leur overlay

class ToggleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Activer / Désactiver", emoji="🟢", style=discord.ButtonStyle.green)
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        username = interaction.user.display_name
        
        if username in active_users:
            active_users.remove(username)
            await interaction.response.send_message(f"🔴 **Live Chat désactivé** sur ton overlay.", ephemeral=True)
        else:
            active_users.add(username)
            await interaction.response.send_message(f"🟢 **Live Chat activé** ! Ton overlay est prêt à recevoir les médias.", ephemeral=True)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

LIVE_CHANNEL_NAME = "live-chat"

@bot.event
async def on_ready():
    print(f"Bot connecté en tant que {bot.user}")
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == LIVE_CHANNEL_NAME:
                view = ToggleView()
                await channel.send("🟢 **Panneau de contrôle du Live Chat**\nClique sur le bouton ci-dessous pour activer ou désactiver l'affichage des médias sur ton PC :", view=view)

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

            @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
            async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
                await interaction.response.defer()

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
            @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
            async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
                await interaction.response.defer()

        if item.get("control_message"):
            view = ItemStopView(item, True)
            await item["control_message"].edit(content="🎬 **Média en cours de diffusion...**", view=view)
    except Exception as e:
        print(f"Erreur activation : {e}")

@app.route('/get_next_meme', methods=['GET'])
def get_next_meme():
    global current_active_item, media_queue
    
    user = request.args.get("user", "").strip()
    
    # Si l'utilisateur n'a pas cliqué sur Activer sur Discord, l'overlay ne reçoit rien
    if not user or user not in active_users:
        return jsonify({"url": None})

    if current_active_item is None and media_queue:
        current_active_item = media_queue.pop(0)
        asyncio.run_coroutine_threadsafe(activate_item_button(current_active_item), bot.loop)

    if current_active_item:
        # Empêche l'auteur de voir son propre média s'afficher sur son propre overlay
        if current_active_item["name"].lower() == user.lower():
            return jsonify({"url": None})
            
        return jsonify({
            "name": current_active_item["name"],
            "avatar": current_active_item["avatar"],
            "content": current_active_item["content"],
            "url": current_active_item["url"]
        })
    
    return jsonify({"url": None})

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    TOKEN = os.environ.get("DISCORD_TOKEN", "TON_TOKEN_BOT_ICI")
    bot.run(TOKEN)
