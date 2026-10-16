import os
import asyncio
import threading
import json
import time
import tempfile
import sys
from threading import Thread

import discord
from discord.ext import commands
from flask import Flask, jsonify, request

# Render captures stdout/stderr; line buffering makes startup and retry diagnostics
# visible immediately instead of appearing delayed or out of order.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except (AttributeError, OSError):
    pass

app = Flask(__name__)
BOT_BUILD_ID = "2026-10-16-shared-playback-quorum-v1"

STORAGE_FILE = os.environ.get("BOT_STORAGE_FILE", "bot_storage.json")
TARGET_CHANNEL_ID = 1544497366814560318  # Salon Discord Live Chat

# La file de médias est commune à tous les clients Live Chat.
# Les préférences (activation, position, Mode Jeu) sont, elles, propres à chaque ID Discord.
global_queue = []
current_active_item = None
active_users = set()             # ID Discord sous forme de chaîne (anciens noms migrés à la première interaction)
user_positions = {}              # discord_user_id -> left / center / right
user_game_modes = {}             # discord_user_id -> bool
user_aliases = {}                # discord_user_id -> anciens/nouveaux noms utilisés par core.pyw
panel_message = None
panel_channel_id = None
# Garde une seule réponse privée de réglages par utilisateur pendant que le bot tourne.
# Les messages éphémères ne sont pas accessibles via l'historique du salon, donc on
# conserve la référence retournée par Discord pour pouvoir la supprimer au prochain clic.
personal_panel_messages = {}  # discord_user_id -> message éphémère
personal_panel_open_lock = asyncio.Lock()
data_lock = threading.RLock()
last_pop_time = 0

# Les overlays partagent le même média, mais la fin de lecture doit être
# confirmée par tous les clients actifs récemment vus : un PC qui termine ou
# échoue plus vite ne doit plus couper la vidéo chez les autres.
CLIENT_HEARTBEAT_TTL = 35.0
client_last_seen = {}        # discord_user_id -> time.monotonic() du dernier GET
playback_finished_by = set() # utilisateurs ayant fini normalement le média courant
playback_failed_by = set()   # utilisateurs qui n'ont pas pu lire le média courant


def _normalise_alias(value):
    return str(value or "").strip().casefold()


def _unique_names(values):
    result = []
    seen = set()
    for value in values:
        value = str(value or "").strip()
        key = _normalise_alias(value)
        if value and key and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def load_data():
    """Charge aussi l'ancien format. Les anciens noms seront migrés vers l'ID Discord
    dès que leur propriétaire ouvre le panneau privé de réglages.
    """
    global user_positions, active_users, user_game_modes, user_aliases
    if not os.path.exists(STORAGE_FILE):
        return
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        user_positions = data.get("user_positions", {}) or {}
        active_users = set(str(value) for value in data.get("active_users", []))
        user_game_modes = data.get("user_game_modes", {}) or {}
        user_aliases = {
            str(user_id): _unique_names(aliases if isinstance(aliases, list) else [aliases])
            for user_id, aliases in (data.get("user_aliases", {}) or {}).items()
        }
        print(
            f"[DATA] Données chargées : {len(user_positions)} position(s), "
            f"{len(active_users)} actif(s), {len(user_aliases)} identité(s) Discord connue(s)."
        )
    except Exception as error:
        print(f"[ERREUR] Chargement stockage : {error}")


def swift_save_data():
    """Sauvegarde atomique et sérialisée pour éviter les écritures concurrentes."""
    try:
        # Garder le verrou durant toute l'écriture empêche une sauvegarde ancienne
        # de finir après une sauvegarde plus récente.
        with data_lock:
            data = {
                "user_positions": user_positions,
                "active_users": sorted(active_users),
                "user_game_modes": user_game_modes,
                "user_aliases": user_aliases,
            }
            destination = os.path.abspath(STORAGE_FILE)
            directory = os.path.dirname(destination) or "."
            os.makedirs(directory, exist_ok=True)
            fd, temporary_path = tempfile.mkstemp(prefix="bot_storage_", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary_path, destination)
            finally:
                if os.path.exists(temporary_path):
                    try:
                        os.remove(temporary_path)
                    except OSError:
                        pass
    except Exception as error:
        print(f"[ERREUR] Sauvegarde stockage : {error}")


load_data()


def register_discord_user(user):
    """Enregistre l'ID Discord et les noms de compatibilité utilisés par core.pyw.

    Le stockage utilise l'ID Discord, pas display_name. Les noms restent des alias
    pour que les versions actuelles de core.pyw (qui transmettent un pseudo texte)
    puissent retrouver les bons réglages.
    """
    user_id = str(user.id)
    display_name = getattr(user, "display_name", None)
    global_name = getattr(user, "global_name", None)
    username = getattr(user, "name", None)
    nickname = getattr(user, "nick", None)
    new_aliases = _unique_names([display_name, global_name, username, nickname])

    with data_lock:
        old_aliases = user_aliases.get(user_id, [])
        combined_aliases = _unique_names(new_aliases + old_aliases)
        alias_norms = {_normalise_alias(alias) for alias in combined_aliases}
        user_aliases[user_id] = combined_aliases

        def alias_owned_by_another_user(alias_norm):
            for other_id, aliases in user_aliases.items():
                if other_id != user_id and any(_normalise_alias(a) == alias_norm for a in aliases):
                    return True
            return False

        # Migration de l'ancien stockage indexé par display_name.
        for mapping in (user_positions, user_game_modes):
            for old_key in list(mapping.keys()):
                old_key = str(old_key)
                old_norm = _normalise_alias(old_key)
                if old_key == user_id or old_norm not in alias_norms:
                    continue
                if alias_owned_by_another_user(old_norm):
                    continue  # alias ambigu : ne jamais attribuer les données à la mauvaise personne
                if user_id not in mapping:
                    mapping[user_id] = mapping[old_key]
                mapping.pop(old_key, None)

        for old_key in list(active_users):
            old_norm = _normalise_alias(old_key)
            if old_key == user_id or old_norm not in alias_norms:
                continue
            if alias_owned_by_another_user(old_norm):
                continue
            active_users.discard(old_key)
            active_users.add(user_id)

        swift_save_data()
    return user_id


def resolve_api_user(value):
    """Résout le pseudo transmis par core.pyw vers un ID Discord.

    Si plusieurs comptes ont le même alias, ne choisit jamais arbitrairement un compte :
    cela évite d'exposer les réglages d'un autre utilisateur.
    """
    value = str(value or "").strip()
    if not value:
        return None

    with data_lock:
        # Prend en charge un futur core.pyw qui enverrait directement l'ID Discord.
        if value in user_aliases or value in user_positions or value in user_game_modes:
            return value

        wanted = _normalise_alias(value)
        matches = []
        for user_id, aliases in user_aliases.items():
            if any(_normalise_alias(alias) == wanted for alias in aliases):
                matches.append(user_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print(f"[SECURITE] Alias ambigu reçu de core.pyw : {value!r}; aucun profil choisi.")
            return None

        # Compatibilité avec les profils sauvegardés par l'ancienne version avant
        # que leur propriétaire ait ouvert le nouveau panneau privé.
        legacy_keys = set(str(key) for key in user_positions)
        legacy_keys.update(str(key) for key in user_game_modes)
        legacy_keys.update(str(key) for key in active_users)
        legacy_matches = {key for key in legacy_keys if _normalise_alias(key) == wanted}
        if len(legacy_matches) == 1:
            return next(iter(legacy_matches))
        return None


def get_user_settings(user_id):
    """Renvoie les préférences du compte et garantit une position latérale en Mode Jeu."""
    with data_lock:
        game_mode = bool(user_game_modes.get(user_id, False))
        position = user_positions.get(user_id, "center")
        if position not in ("left", "center", "right"):
            position = "center"
        # Migration des anciennes préférences : le Mode Jeu ne doit jamais rester centré.
        if game_mode and position == "center":
            position = "right"
            user_positions[user_id] = position
            swift_save_data()
        return {
            "active": user_id in active_users,
            "position": position,
            "game_mode": game_mode,
        }


def _display_name_for_key(key):
    key = str(key)
    aliases = user_aliases.get(key, [])
    if aliases:
        return aliases[0]
    # Ancien enregistrement de nom, avant la migration par ID.
    if not key.isdigit():
        return key
    return "Utilisateur"


@app.route("/")
def home():
    return "Bot is alive!", 200


def get_channel_panel_content():
    """Le panneau public n'affiche plus les réglages d'une personne en particulier."""
    with data_lock:
        count = len(active_users)
        users = sorted({_display_name_for_key(user_id) for user_id in active_users})
    users_text = ", ".join(f"**{name}**" for name in users) if users else "_Personne_"
    return (
        f"📊 **Statut en direct** : {count} actif(s)\n"
        f"👤 **Connectés** : {users_text}\n\n"
        "👇 **Ouvre tes réglages personnels ci-dessous. Ils ne changent que pour ton compte.**"
    )


def get_personal_panel_content(user_id):
    settings = get_user_settings(user_id)
    active_text = "Activé" if settings["active"] else "Désactivé"
    position_text = {
        "left": "Gauche",
        "center": "Centre",
        "right": "Droite",
    }.get(settings["position"], "Centre")
    return (
        "⚙️ **Tes réglages Live Chat**\n"
        f"🟢 **Live Chat :** {active_text}\n"
        f"📍 **Position :** {position_text}"
        # On garde volontairement une ligne vide avant les boutons, sans la phrase
        # supplémentaire sur la confidentialité.
        "\n\n\u200b"
    )


class PublicControlView(discord.ui.View):
    """Vue commune neutre : son apparence ne dépend d'aucun utilisateur."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Mes réglages Live Chat",
        emoji="⚙️",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="open_my_personal_livechat_settings",
    )
    async def open_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Acquitte l'interaction en créant tout de suite une réponse éphémère "en cours".
        # Toutes les opérations lentes (stockage disque, verrou, suppression/édition)
        # se font après cet accusé de réception pour éviter le délai Discord de ~3 secondes.
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as error:
            print(f"[INTERACTION] Impossible d'acquitter l'ouverture des réglages: {error}")
            return

        user_id = None
        try:
            user_id = register_discord_user(interaction.user)
            view = PersonalControlView(user_id)

            async with personal_panel_open_lock:
                previous_message = personal_panel_messages.get(user_id)
                if previous_message is not None:
                    try:
                        # Évite d'accumuler les panneaux privés pour le même utilisateur.
                        await asyncio.wait_for(previous_message.delete(), timeout=4.0)
                        personal_panel_messages.pop(user_id, None)
                    except Exception as delete_error:
                        # Si l'ancien message est encore éditable (jeton non expiré),
                        # on le réutilise puis on supprime la réponse "en cours" actuelle.
                        try:
                            await asyncio.wait_for(
                                previous_message.edit(
                                    content=get_personal_panel_content(user_id),
                                    view=view,
                                ),
                                timeout=4.0,
                            )
                            personal_panel_messages[user_id] = previous_message
                            try:
                                await interaction.delete_original_response()
                            except Exception:
                                # Même en cas d'échec de suppression, remplacer le chargement
                                # par un message court évite de laisser le spinner indéfiniment.
                                try:
                                    await interaction.edit_original_response(
                                        content="Tes réglages sont déjà ouverts dans le panneau précédent.",
                                        view=None,
                                    )
                                except Exception:
                                    pass
                            return
                        except Exception as edit_error:
                            print(
                                f"[PANEL] Ancien panneau privé non réutilisable pour {user_id}: "
                                f"suppression={delete_error}; édition={edit_error}"
                            )
                        personal_panel_messages.pop(user_id, None)

                # Modifie la réponse éphémère différée de CETTE interaction au lieu
                # d'utiliser followup.send : il n'y a ainsi qu'un seul nouveau panneau.
                await interaction.edit_original_response(
                    content=get_personal_panel_content(user_id),
                    view=view,
                )
                personal_panel_messages[user_id] = await interaction.original_response()
        except Exception as error:
            print(f"[INTERACTION] Échec ouverture réglages pour {user_id}: {type(error).__name__}: {error}")
            try:
                await interaction.edit_original_response(
                    content="⚠️ Impossible d'ouvrir tes réglages pour le moment. Réessaie dans quelques secondes.",
                    view=None,
                )
            except Exception as response_error:
                print(f"[INTERACTION] Impossible d'afficher l'erreur à l'utilisateur: {response_error}")


class PersonalControlView(discord.ui.View):
    """Chaque interaction privée est liée à l'ID Discord de son propriétaire."""

    def __init__(self, user_id):
        # Les panneaux privés peuvent être rouverts avec le bouton public s'ils expirent.
        super().__init__(timeout=900)
        self.user_id = str(user_id)
        self.update_button_styles()

    def update_button_styles(self):
        settings = get_user_settings(self.user_id)
        if settings["active"]:
            self.toggle_btn.label = "Désactiver mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.danger
            self.toggle_btn.emoji = "🔴"
        else:
            self.toggle_btn.label = "Activer mon Live Chat"
            self.toggle_btn.style = discord.ButtonStyle.success
            self.toggle_btn.emoji = "🟢"

        if settings["game_mode"]:
            self.btn_game_mode.label = "Mode Jeu : ACTIF"
            self.btn_game_mode.style = discord.ButtonStyle.success
        else:
            self.btn_game_mode.label = "Mode Jeu : INACTIF"
            self.btn_game_mode.style = discord.ButtonStyle.secondary
        self.btn_game_mode.emoji = "🎮"

        position = settings["position"]
        game_mode = settings["game_mode"]

        self.btn_left.style = discord.ButtonStyle.primary if position == "left" else discord.ButtonStyle.secondary
        self.btn_right.style = discord.ButtonStyle.primary if position == "right" else discord.ButtonStyle.secondary

        # En Mode Jeu, le bouton Centre est retiré de la vue Discord (pas simplement désactivé).
        # Quand le Mode Jeu est désactivé, on le réinsère puis on restaure explicitement
        # l'ordre d'origine des composants : Gauche, Centre, Droite.
        if game_mode:
            if self.btn_center in self.children:
                self.remove_item(self.btn_center)
        elif self.btn_center not in self.children:
            self.add_item(self.btn_center)

        # discord.py ajoute un élément supprimé à la fin de la liste des composants.
        # Sans ce tri, Centre apparaîtrait après Droite après avoir désactivé le Mode Jeu.
        component_order = {
            "personal_toggle_livechat": 0,
            "personal_toggle_game_mode": 1,
            "personal_position_left": 2,
            "personal_position_center": 3,
            "personal_position_right": 4,
        }
        self._children.sort(
            key=lambda item: component_order.get(getattr(item, "custom_id", ""), 99)
        )
        self.btn_center.style = discord.ButtonStyle.primary if position == "center" else discord.ButtonStyle.secondary

    async def _check_owner(self, interaction):
        # Valide le propriétaire avant toute écriture, puis acquitte tout de suite
        # l'interaction pour que la sauvegarde synchrone ne provoque pas de timeout.
        if str(interaction.user.id) != self.user_id:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Ces réglages ne sont accessibles qu'à la personne qui les a ouverts.",
                    ephemeral=True,
                )
            return False
        try:
            await interaction.response.defer(thinking=False)
        except Exception as error:
            print(f"[INTERACTION] Impossible d'acquitter un réglage de {self.user_id}: {error}")
            return False
        return True

    async def _refresh_private_message(self, interaction, refresh_public=False):
        try:
            self.update_button_styles()
            # Après defer(thinking=False), edit_original_response modifie le panneau
            # éphémère d'origine sans demander un second accusé de réception.
            await interaction.edit_original_response(
                content=get_personal_panel_content(self.user_id),
                view=self,
            )
        except Exception as error:
            print(f"[INTERACTION] Échec rafraîchissement panneau {self.user_id}: {error}")
            try:
                await interaction.followup.send(
                    "Les réglages ont été pris en compte, mais le panneau n'a pas pu être actualisé. Rouvre tes réglages.",
                    ephemeral=True,
                )
            except Exception as followup_error:
                print(f"[INTERACTION] Impossible d'envoyer le message de secours: {followup_error}")
            return
        if refresh_public:
            await refresh_public_panel()

    @discord.ui.button(
        label="Activer mon Live Chat",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        row=0,
        custom_id="personal_toggle_livechat",
    )
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        # Utilise l'ID Discord, jamais display_name comme clé de stockage.
        user_id = register_discord_user(interaction.user)
        if user_id != self.user_id:
            await interaction.response.send_message("Profil invalide. Rouvre tes réglages.", ephemeral=True)
            return
        with data_lock:
            if user_id in active_users:
                active_users.remove(user_id)
            else:
                active_users.add(user_id)
            # Une bascule active/inactive invalide un ancien accusé de fin : si
            # l'utilisateur revient pendant le média courant, il doit être attendu.
            playback_finished_by.discard(user_id)
            playback_failed_by.discard(user_id)
            swift_save_data()
        await self._refresh_private_message(interaction, refresh_public=True)

    @discord.ui.button(
        label="Mode Jeu : INACTIF",
        emoji="🎮",
        style=discord.ButtonStyle.secondary,
        row=0,
        custom_id="personal_toggle_game_mode",
    )
    async def btn_game_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        user_id = register_discord_user(interaction.user)
        with data_lock:
            enable_game_mode = not bool(user_game_modes.get(user_id, False))
            user_game_modes[user_id] = enable_game_mode
            # Si l'utilisateur active le Mode Jeu depuis Centre, on le bascule à Droite.
            # Le bouton Centre disparaît alors de la vue, et le serveur renverra une position valide.
            position = user_positions.get(user_id, "center")
            if enable_game_mode and position == "center":
                user_positions[user_id] = "right"
            swift_save_data()
        await self._refresh_private_message(interaction)

    @discord.ui.button(
        label="Gauche",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="personal_position_left",
    )
    async def btn_left(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        user_id = register_discord_user(interaction.user)
        with data_lock:
            user_positions[user_id] = "left"
            swift_save_data()
        await self._refresh_private_message(interaction)

    @discord.ui.button(
        label="Centre",
        emoji="⏺️",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="personal_position_center",
    )
    async def btn_center(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        user_id = register_discord_user(interaction.user)
        with data_lock:
            # Protège aussi contre un ancien panneau dont le composant Centre
            # serait encore cliquable après l'activation du Mode Jeu.
            if user_game_modes.get(user_id, False):
                user_positions[user_id] = "right"
            else:
                user_positions[user_id] = "center"
            swift_save_data()
        await self._refresh_private_message(interaction)

    @discord.ui.button(
        label="Droite",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="personal_position_right",
    )
    async def btn_right(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        user_id = register_discord_user(interaction.user)
        with data_lock:
            user_positions[user_id] = "right"
            swift_save_data()
        await self._refresh_private_message(interaction)


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Journalise l'arrivée des interactions pour distinguer un vieux panneau
    d'un callback qui commence puis échoue. Ne répond pas à l'interaction ici.
    """
    try:
        if interaction.type == discord.InteractionType.component:
            data = interaction.data or {}
            custom_id = str(data.get("custom_id", "inconnu"))
            print(f"[INTERACTION] Composant reçu : {custom_id}")
    except Exception as error:
        print(f"[INTERACTION] Journalisation impossible : {error}")


def is_target_channel(channel):
    return channel.id == TARGET_CHANNEL_ID


panel_update_lock = asyncio.Lock()


async def refresh_public_panel():
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel is not None:
        await repost_bottom_panel(channel)


async def repost_bottom_panel(channel, move_to_bottom=False):
    """Maintient un panneau public unique et neutre en bas du salon."""
    global panel_message, panel_channel_id
    async with panel_update_lock:
        try:
            content = get_channel_panel_content()
            view = PublicControlView()

            if move_to_bottom:
                old_panels = []
                if panel_message is not None and panel_channel_id == channel.id:
                    old_panels.append(panel_message)

                async for old_msg in channel.history(limit=50):
                    if (
                        old_msg.author == bot.user
                        and "Statut en direct" in (old_msg.content or "")
                        and all(old_msg.id != item.id for item in old_panels)
                    ):
                        old_panels.append(old_msg)

                # Envoie d'abord le nouveau panneau pour préserver l'ancien en cas d'échec.
                new_panel = await channel.send(content=content, view=view)
                panel_message = new_panel
                panel_channel_id = channel.id
                for old_msg in old_panels:
                    if old_msg.id == new_panel.id:
                        continue
                    try:
                        await old_msg.delete()
                    except Exception as error:
                        print(f"Impossible de supprimer l'ancien panneau {old_msg.id} : {error}")
                return True

            if panel_message is not None and panel_channel_id == channel.id:
                try:
                    await panel_message.edit(content=content, view=view)
                    return True
                except Exception:
                    panel_message = None
                    panel_channel_id = None

            existing = []
            async for old_msg in channel.history(limit=50):
                if old_msg.author == bot.user and "Statut en direct" in (old_msg.content or ""):
                    existing.append(old_msg)

            existing.sort(key=lambda message: message.created_at, reverse=True)
            if existing:
                panel_message = existing[0]
                panel_channel_id = channel.id
                await panel_message.edit(content=content, view=view)
                for duplicate in existing[1:]:
                    try:
                        await duplicate.delete()
                    except Exception:
                        pass
            else:
                panel_message = await channel.send(content=content, view=view)
                panel_channel_id = channel.id
            return True
        except Exception as error:
            print(f"Erreur mise à jour panneau bas : {error}")
            return False


@bot.event
async def on_ready():
    print(f"[DISCORD] Connexion réussie en tant que bot ({bot.user}) | BOT_BUILD_ID={BOT_BUILD_ID}")
    # La vue publique est enregistrée une seule fois par processus.
    if not getattr(bot, "_public_view_registered", False):
        bot.add_view(PublicControlView())
        bot._public_view_registered = True

    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        # Au premier on_ready après un démarrage du processus, créer un nouveau
        # panneau en bas puis supprimer l'ancien. Lors d'une reconnexion réseau
        # dans le même processus, simplement actualiser le panneau existant.
        first_ready_this_process = not getattr(bot, "_public_panel_initialized", False)
        ok = await repost_bottom_panel(channel, move_to_bottom=first_ready_this_process)
        if ok and first_ready_this_process:
            bot._public_panel_initialized = True
    else:
        print(f"[ERREUR] Salon cible {TARGET_CHANNEL_ID} introuvable dans le cache du bot.")


@bot.event
async def on_message(message):
    global current_active_item, global_queue
    if bot.user is not None and message.author.id == bot.user.id:
        return

    if message.author.bot:
        if is_target_channel(message.channel):
            await repost_bottom_panel(message.channel, move_to_bottom=True)
        return

    if is_target_channel(message.channel):
        media_url = ""
        if message.attachments:
            media_url = message.attachments[0].url
        elif "http" in message.content:
            for word in message.content.split():
                if word.startswith("http"):
                    media_url = word
                    break

        if media_url:
            item = {
                "item_id": str(message.id),
                "name": message.author.display_name,
                "avatar": str(message.author.display_avatar.url) if message.author.display_avatar else "",
                "content": message.content.replace(media_url, "").strip(),
                "url": media_url,
                "message_obj": message,
                "control_message": None,
            }
            with data_lock:
                if len(global_queue) > 15:
                    global_queue.pop(0)
                if current_active_item is None:
                    current_active_item = item
                    playback_finished_by.clear()
                    playback_failed_by.clear()
                    is_first = True
                else:
                    global_queue.append(item)
                    is_first = False
            await send_control_message(item, is_active=is_first)

    await bot.process_commands(message)
    if is_target_channel(message.channel):
        await repost_bottom_panel(message.channel, move_to_bottom=True)


class ItemStopView(discord.ui.View):
    def __init__(self, item_ref, active):
        super().__init__(timeout=86400)
        self.item_ref = item_ref
        self.stop_button.disabled = not active

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        global current_active_item, global_queue, last_pop_time
        with data_lock:
            if current_active_item != self.item_ref:
                return
            last_pop_time = time.time()
            playback_finished_by.clear()
            playback_failed_by.clear()
            try:
                if self.item_ref.get("control_message"):
                    await self.item_ref["control_message"].delete()
            except Exception:
                pass
            if global_queue:
                current_active_item = global_queue.pop(0)
                asyncio.run_coroutine_threadsafe(
                    activate_next_item_message(current_active_item), bot.loop
                )
            else:
                current_active_item = None


async def send_control_message(item, is_active):
    try:
        view = ItemStopView(item, is_active)
        status_text = (
            "🎬 **Média en cours de diffusion...**"
            if is_active else "⏳ **En attente dans la file...**"
        )
        message = await item["message_obj"].reply(status_text, view=view)
        item["control_message"] = message
    except Exception as error:
        print(f"Erreur critique en envoyant le message de contrôle : {error}")


async def activate_next_item_message(item):
    try:
        if item.get("control_message"):
            view = ItemStopView(item, active=True)
            await item["control_message"].edit(
                content="🎬 **Média en cours de diffusion...**", view=view
            )
    except Exception as error:
        print(f"Erreur activation prochain média : {error}")


def _active_playback_viewers_locked(now=None):
    """Les clients qui ont interrogé l'API récemment et dont Live Chat est actif.

    Appelé avec data_lock détenu. Un overlay fermé ne doit pas bloquer la file.
    """
    if now is None:
        now = time.monotonic()
    return {
        user_id for user_id in active_users
        if now - client_last_seen.get(user_id, 0.0) <= CLIENT_HEARTBEAT_TTL
    }


def _current_item_id_locked():
    if not current_active_item:
        return None
    return str(
        current_active_item.get("item_id")
        or getattr(current_active_item.get("message_obj"), "id", current_active_item.get("url", ""))
    )


def _advance_shared_item_locked(log_reason):
    """Avance la file pendant que data_lock est détenu; renvoie le nom suivant."""
    global current_active_item, global_queue, last_pop_time
    old_item = current_active_item
    if old_item is None:
        return None
    old_item_id = _current_item_id_locked()

    last_pop_time = time.time()
    if old_item.get("control_message") and bot.loop and bot.loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(safe_delete_msg(old_item["control_message"]), bot.loop)
        except RuntimeError as error:
            print(f"[QUEUE] Suppression du contrôle différée impossible : {error}", flush=True)

    if global_queue:
        current_active_item = global_queue.pop(0)
        if current_active_item.get("control_message") and bot.loop and bot.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(activate_next_item_message(current_active_item), bot.loop)
            except RuntimeError as error:
                print(f"[QUEUE] Activation du contrôle suivant différée impossible : {error}", flush=True)
        next_name = current_active_item.get("name")
    else:
        current_active_item = None
        next_name = None

    playback_finished_by.clear()
    playback_failed_by.clear()
    print(f"[QUEUE] Média {old_item_id or 'inconnu'} terminé — file avancée ({log_reason}).", flush=True)
    return next_name


def _advance_if_all_recent_viewers_reported_locked(now=None):
    """Évite qu'un overlay fermé juste avant le dernier rapport bloque la file."""
    if current_active_item is None:
        return False
    expected = _active_playback_viewers_locked(now)
    if not expected:
        return False
    reported = (playback_finished_by | playback_failed_by) & expected
    if expected.issubset(reported):
        _advance_shared_item_locked("tous les overlays actifs ont fini ou sont devenus inactifs")
        return True
    return False


@app.route("/get_next_meme", methods=["GET"])
def get_next_meme():
    global current_active_item
    requested_name = request.args.get("user", "").strip()
    user_id = resolve_api_user(requested_name)

    with data_lock:
        if user_id is None or user_id not in active_users:
            return jsonify({
                "url": None,
                "position": "center",
                "game_mode": False,
                "status": "inactive",
            })

        # Présence client utilisée pour ne pas attendre un overlay fermé/crashé.
        now_mono = time.monotonic()
        client_last_seen[user_id] = now_mono
        # Purge légère des anciens clients inactifs pour garder le dictionnaire borné.
        for old_id, seen_at in list(client_last_seen.items()):
            if old_id not in active_users and now_mono - seen_at > 600:
                client_last_seen.pop(old_id, None)

        # Si un overlay s'est fermé pendant que les autres attendaient, ses
        # battements expirés permettent d'avancer au prochain GET, sans rester bloqué.
        _advance_if_all_recent_viewers_reported_locked(now_mono)

        position = user_positions.get(user_id, "center")
        if position not in ("left", "center", "right"):
            position = "center"
        game_mode = bool(user_game_modes.get(user_id, False))
        # Dernière barrière côté API : aucune réponse ne doit exposer Centre en Mode Jeu,
        # même si les données viennent d'une ancienne version du stockage.
        if game_mode and position == "center":
            position = "right"
            user_positions[user_id] = position
            swift_save_data()

        if current_active_item:
            item_id = str(
                current_active_item.get("item_id")
                or getattr(current_active_item.get("message_obj"), "id", current_active_item["url"])
            )
            res_data = {
                "item_id": item_id,
                "name": current_active_item["name"],
                "avatar": current_active_item["avatar"],
                "content": current_active_item["content"],
                "url": current_active_item["url"],
                "position": position,
                "game_mode": game_mode,
                "status": "active",
            }
        else:
            res_data = {
                "url": None,
                "position": position,
                "game_mode": game_mode,
                "status": "active",
            }
    return jsonify(res_data)


@app.route("/pop_meme", methods=["POST"])
def pop_meme():
    """Rapporte la fin locale d'un média partagé.

    La file n'avance que lorsque tous les overlays actifs récemment vus ont fini
    ou signalé qu'ils ne peuvent pas lire ce média. Une fin locale isolée ne coupe
    donc plus la lecture des autres membres.
    """
    global current_active_item, global_queue, last_pop_time
    requested_name = request.args.get("user", "").strip()
    requested_item_id = request.args.get("item_id", "").strip()
    reason = request.args.get("reason", "finished").strip().lower()
    if reason not in ("finished", "failed"):
        reason = "finished"
    user_id = resolve_api_user(requested_name)

    with data_lock:
        current_item_id = _current_item_id_locked()
        if requested_item_id and current_item_id != requested_item_id:
            name = current_active_item["name"] if current_active_item else None
            return jsonify({
                "status": "stale",
                "next": name,
                "current_item_id": current_item_id,
            })

        if not current_active_item:
            return jsonify({"status": "stale", "next": None, "current_item_id": None})

        if user_id is None or user_id not in active_users:
            return jsonify({"status": "inactive", "next": current_active_item.get("name")})

        now_mono = time.monotonic()
        client_last_seen[user_id] = now_mono
        if reason == "failed":
            playback_failed_by.add(user_id)
            playback_finished_by.discard(user_id)
        else:
            playback_finished_by.add(user_id)
            playback_failed_by.discard(user_id)

        expected = _active_playback_viewers_locked(now_mono)
        # Le demandeur doit compter même si son dernier poll date juste avant TTL.
        expected.add(user_id)
        reported = (playback_finished_by | playback_failed_by) & expected
        pending = expected - reported

        if pending:
            print(
                f"[QUEUE] Média {_current_item_id_locked()} : {len(reported)}/{len(expected)} "
                f"clients terminés; attente de {len(pending)}.",
                flush=True,
            )
            return jsonify({
                "status": "waiting",
                "next": current_active_item.get("name"),
                "finished": len(reported),
                "required": len(expected),
                "pending": len(pending),
            })

        # Tous les clients actifs récemment vus ont fini ou signalé un échec.
        next_name = _advance_shared_item_locked("tous les clients attendus ont fini ou échoué")
        return jsonify({"status": "success", "next": next_name})


async def safe_delete_msg(message):
    try:
        await message.delete()
    except Exception:
        pass


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    print(f"[FLASK] Démarrage du serveur web sur le port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


def _short_error_message(error, limit=240):
    """Évite d'inonder les logs avec la page HTML renvoyée par Cloudflare."""
    message = " ".join(str(error).split())
    if len(message) > limit:
        return message[:limit] + "…"
    return message


def _retry_after_from_http_exception(error):
    """Récupère Retry-After si Discord/Cloudflare le fournit."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


async def run_discord_with_backoff(token):
    """Keep Flask alive and retry Discord login without closing the reusable client."""
    delay = 60.0
    max_backoff = 900.0  # 15 minutes; an explicit Retry-After may be longer

    while True:
        wait_seconds = delay
        print(
            f"[DISCORD] Tentative de connexion maintenant; prochaine attente de secours : {int(delay)} s.",
            flush=True,
        )
        try:
            await bot.start(token, reconnect=True)
            # Normally start() remains active until shutdown. Do not restart immediately
            # if it returns; keep the retry cadence controlled.
            print("[DISCORD] bot.start() s'est terminé sans exception.", flush=True)
            wait_seconds = delay
        except asyncio.CancelledError:
            raise
        except discord.HTTPException as error:
            status = getattr(error, "status", None)
            retry_after = _retry_after_from_http_exception(error)
            if status == 429:
                wait_seconds = max(delay, retry_after or 0.0)
                print(
                    f"[DISCORD] HTTP 429 à la connexion (Discord/Cloudflare). "
                    f"Nouvel essai dans {int(wait_seconds)} s. "
                    f"Détail: {_short_error_message(error)}",
                    flush=True,
                )
            else:
                wait_seconds = max(delay, 300.0 if status in (401, 403) else 0.0)
                print(
                    f"[DISCORD] Erreur HTTP de connexion (status={status}). "
                    f"Nouvel essai dans {int(wait_seconds)} s. "
                    f"Détail: {_short_error_message(error)}",
                    flush=True,
                )
        except discord.LoginFailure as error:
            wait_seconds = max(delay, 300.0)
            print(
                f"[DISCORD] Échec d'authentification. Vérifie le nom et la présence de "
                f"DISCORD_TOKEN dans Render (valeur secrète non affichée). "
                f"Nouvel essai dans {int(wait_seconds)} s. Détail: {_short_error_message(error)}",
                flush=True,
            )
        except (OSError, asyncio.TimeoutError) as error:
            wait_seconds = delay
            print(
                f"[DISCORD] Erreur réseau temporaire: {type(error).__name__}: {error}. "
                f"Nouvel essai dans {int(wait_seconds)} s.",
                flush=True,
            )
        except Exception as error:
            wait_seconds = delay
            print(
                f"[DISCORD] Erreur inattendue: {type(error).__name__}: {_short_error_message(error)}. "
                f"Nouvel essai dans {int(wait_seconds)} s.",
                flush=True,
            )

        # Important: don't call bot.close() after a failed login. A closed discord.py
        # Client cannot reliably be reused for the next attempt. An HTTP 429 response
        # itself doesn't require destroying the Client; the next attempt reuses it.
        print(
            f"[DISCORD] Flask reste actif. Prochaine tentative dans {int(wait_seconds)} s.",
            flush=True,
        )
        await asyncio.sleep(wait_seconds)
        delay = min(max(delay * 2, 60.0), max_backoff)


async def main():
    print(f"[BUILD] BOT_BUILD_ID={BOT_BUILD_ID}", flush=True)
    print("[STARTUP] main() démarré.", flush=True)
    flask_thread = Thread(target=run_flask, daemon=True, name="flask-server")
    flask_thread.start()
    print(f"[STARTUP] Thread Flask lancé (alive={flask_thread.is_alive()}).", flush=True)

    # Let HTTP health checks succeed before attempting Discord login.
    await asyncio.sleep(1.0)
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    print(f"[STARTUP] Variable DISCORD_TOKEN présente: {'oui' if bool(token) else 'NON' }.", flush=True)
    if not token:
        print(
            "[ERREUR] DISCORD_TOKEN est absente ou vide dans Render > Environment. "
            "L'API web reste active; Discord ne peut pas se connecter.",
            flush=True,
        )
        while True:
            await asyncio.sleep(3600)

    await run_discord_with_backoff(token)


if __name__ == "__main__":
    asyncio.run(main())
