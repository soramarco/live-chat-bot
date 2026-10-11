import sys
import os
import time
import math
import json
import tempfile
import threading
import traceback
from urllib.parse import urlparse

import requests

# ---------------------------------------------------------------------------
# JOURNAL DE DEBUG : C:\Users\<toi>\overlay_debug.log
# (indispensable car pythonw n'affiche aucune erreur)
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(os.path.expanduser("~"), "overlay_debug.log")
_log_lock = threading.Lock()


def log(msg):
    try:
        with _log_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


def _reset_log():
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 300000:
            os.remove(LOG_FILE)
    except Exception:
        pass


def _excepthook(exc_type, exc_value, exc_tb):
    log("CRASH :\n" + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))


sys.excepthook = _excepthook
try:
    threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)
except Exception:
    pass

_reset_log()
log("=== Démarrage core.pyw ===")

try:
    from PyQt5.QtCore import Qt, QTimer, QUrl, QRectF, pyqtSignal
    from PyQt5.QtGui import QFont, QColor, QPainter, QBrush, QPainterPath, QPixmap, QImage
    from PyQt5.QtWidgets import (
        QApplication, QLabel, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
        QGraphicsDropShadowEffect, QLineEdit, QPushButton, QMessageBox,
    )
    from PyQt5.QtMultimedia import (
        QMediaPlayer, QMediaContent, QAbstractVideoSurface, QAbstractVideoBuffer,
        QVideoFrame, QVideoSurfaceFormat,
    )
except Exception:
    log("IMPOSSIBLE D'IMPORTER PyQt5 :\n" + traceback.format_exc())
    sys.exit(1)

CONFIG_FILE = os.path.join(os.path.expanduser("~"), "overlay_config_discord.txt")
OLD_LOCK_FILE = os.path.join(tempfile.gettempdir(), "live_chat_overlay_running.lock")
BASE_URL = "https://live-chat-bot-ctus.onrender.com"
SERVER_URL = BASE_URL + "/get_next_meme"
POP_URL = BASE_URL + "/pop_meme"

VIDEO_EXTS = (".mp4", ".mov", ".webm", ".avi", ".mkv")

# ---------------------------------------------------------------------------
# UNE SEULE INSTANCE (mutex Windows : disparait tout seul si le process meurt)
# ---------------------------------------------------------------------------
_mutex_handle = None


def acquire_single_instance():
    global _mutex_handle
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        _mutex_handle = kernel32.CreateMutexW(None, False, "Local\\LiveChatOverlayMemeMutex")
        already_running = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
        return not already_running
    except Exception:
        log("Mutex impossible : " + traceback.format_exc())
        return True


# ---------------------------------------------------------------------------
# FENETRE DE CONFIGURATION DU PSEUDO
# ---------------------------------------------------------------------------
class SetupDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.pseudo = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Configuration de l'Overlay Mème")
        self.setFixedSize(350, 180)
        self.setStyleSheet("background-color: #2b2d31; color: white; font-family: 'Segoe UI';")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        saved_pseudo = ""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved_pseudo = f.read().strip()
            except Exception:
                pass

        label = QLabel("Entre ton pseudo Discord exact :", self)
        label.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(label)

        self.input_field = QLineEdit(self)
        self.input_field.setText(saved_pseudo)
        self.input_field.setStyleSheet(
            "padding: 8px; font-size: 14px; background: #1e1f22; border: 1px solid #383a40; "
            "border-radius: 4px; color: white;")
        self.input_field.returnPressed.connect(self.validate)
        layout.addWidget(self.input_field)

        btn = QPushButton("Lancer l'Overlay", self)
        btn.setStyleSheet(
            "margin-top: 10px; padding: 8px; font-size: 14px; background: #5865F2; color: white; "
            "border: none; border-radius: 4px; font-weight: bold;")
        btn.clicked.connect(self.validate)
        layout.addWidget(btn)

    def validate(self):
        text = self.input_field.text().strip()
        if text:
            self.pseudo = text
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception:
                pass
            self.accept()


def create_discord_avatar_with_ring(pixmap, size=40):
    total_size = size + 8
    result = QPixmap(total_size, total_size)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(QBrush(QColor(87, 242, 135)))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(0, 0, total_size, total_size)
    avatar_scaled = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    path = QPainterPath()
    path.addEllipse(4, 4, size, size)
    painter.setClipPath(path)
    painter.drawPixmap(4, 4, avatar_scaled)
    painter.end()
    return result



# ---------------------------------------------------------------------------
# AFFICHAGE VIDEO IMAGE PAR IMAGE
# QGraphicsVideoItem / QVideoWidget ne s'affichent pas dans une fenêtre
# transparente (on entendait le son mais on ne voyait rien). On récupère donc
# chaque image de la vidéo et on la dessine dans un simple QLabel.
# ---------------------------------------------------------------------------
class FrameSurface(QAbstractVideoSurface):
    frame_ready = pyqtSignal(QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.target_size = (850, 480)
        self._img_fmt = QImage.Format_Invalid
        self._count = 0

    def supportedPixelFormats(self, handle_type=QAbstractVideoBuffer.NoHandle):
        if handle_type == QAbstractVideoBuffer.NoHandle:
            return [
                QVideoFrame.Format_RGB32,
                QVideoFrame.Format_ARGB32,
                QVideoFrame.Format_ARGB32_Premultiplied,
                QVideoFrame.Format_RGB24,
                QVideoFrame.Format_RGB565,
                QVideoFrame.Format_RGB555,
            ]
        return []

    def start(self, fmt):
        img_fmt = QVideoFrame.imageFormatFromPixelFormat(fmt.pixelFormat())
        if img_fmt == QImage.Format_Invalid or fmt.frameSize().isEmpty():
            log(f"Format vidéo non géré : {fmt.pixelFormat()}")
            return False
        self._img_fmt = img_fmt
        self._count = 0
        log(f"Surface vidéo démarrée : {fmt.frameSize().width()}x{fmt.frameSize().height()}")
        return super().start(fmt)

    def present(self, frame):
        if not frame.isValid():
            return False
        clone = QVideoFrame(frame)
        if not clone.map(QAbstractVideoBuffer.ReadOnly):
            return False
        try:
            ptr = clone.bits()
            ptr.setsize(clone.mappedBytes())
            img = QImage(ptr, clone.width(), clone.height(), clone.bytesPerLine(), self._img_fmt)
            if self.surfaceFormat().scanLineDirection() == QVideoSurfaceFormat.BottomToTop:
                img = img.mirrored(False, True)
            w, h = self.target_size
            img = img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)  # copie indépendante
            # Certains lecteurs mettent la transparence à 0 : sur une fenêtre transparente
            # l'image devient alors invisible. On force une image 100% opaque.
            img = img.convertToFormat(QImage.Format_RGB32)
        finally:
            clone.unmap()
        self._count += 1
        if self._count in (1, 2, 10, 60, 300):
            px = img.pixel(img.width() // 2, img.height() // 2)
            log(f"Image vidéo n°{self._count} : {img.width()}x{img.height()}, "
                f"format source {self._img_fmt}, pixel central {px & 0xFFFFFFFF:08X}")
        self.frame_ready.emit(img)
        return True

# ---------------------------------------------------------------------------
# OVERLAY
# ---------------------------------------------------------------------------
class OverlayWindow(QWidget):
    # Signaux : seul moyen fiable de parler à l'interface depuis le thread réseau
    media_ready = pyqtSignal(dict)
    hide_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, discord_pseudo):
        super().__init__()
        self.discord_pseudo = discord_pseudo
        self.video_path = None
        self.current_loaded_url = None
        self.last_done_url = None
        self.is_clearing = False
        self.current_max_size = 850

        self.media_ready.connect(self.handle_new_media)
        self.hide_requested.connect(self.hide_overlay_ui)

        self.float_angle = 0.0
        self.float_timer = QTimer(self)
        self.float_timer.timeout.connect(self.animate_floating_header)
        self.float_timer.start(30)

        self.init_ui()
        self.init_network()

    def init_ui(self):
        # Fenêtre sans bord, au premier plan, transparente, et "traversable" à la souris
        # (Qt.WindowTransparentForInput remplace l'ancien bricolage ctypes)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        log(f"Taille de l'overlay : {screen.width()}x{screen.height()}")

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(40, 0, 40, 0)

        self.container = QWidget(self)
        container_layout = QVBoxLayout(self.container)
        container_layout.setAlignment(Qt.AlignCenter)
        container_layout.setSpacing(12)

        self.header_widget = QWidget(self.container)
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setAlignment(Qt.AlignCenter)
        header_layout.setSpacing(10)

        self.avatar_label = QLabel(self.header_widget)
        header_layout.addWidget(self.avatar_label)

        self.author_label = QLabel(self.header_widget)
        self.author_label.setAlignment(Qt.AlignCenter)
        self.author_label.setFont(QFont("Segoe UI", 20, QFont.Bold))
        self.author_label.setStyleSheet("color: white;")
        shadow_pseudo = QGraphicsDropShadowEffect()
        shadow_pseudo.setBlurRadius(3)
        shadow_pseudo.setColor(QColor(0, 0, 0, 255))
        shadow_pseudo.setOffset(1, 1)
        self.author_label.setGraphicsEffect(shadow_pseudo)
        header_layout.addWidget(self.author_label)
        container_layout.addWidget(self.header_widget)

        self.image_label = QLabel(self.container)
        self.image_label.setAlignment(Qt.AlignCenter)
        container_layout.addWidget(self.image_label)

        self.video_label = QLabel(self.container)
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setFixedSize(850, 480)
        container_layout.addWidget(self.video_label)
        self.video_label.hide()

        self.video_surface = FrameSurface(self)
        self.video_surface.frame_ready.connect(self.on_video_frame)

        self.media_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        self.media_player.setVideoOutput(self.video_surface)
        # IMPORTANT : EndOfMedia est un "mediaStatus", pas un "state".
        # L'ancien code utilisait stateChanged -> la fin de vidéo n'était jamais détectée.
        self.media_player.mediaStatusChanged.connect(self.on_media_status)
        self.media_player.error.connect(self.on_media_error)

        self.text_label = QLabel(self.container)
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setWordWrap(True)
        self.text_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        self.text_label.setStyleSheet("color: white;")
        self.text_label.setMaximumWidth(850)
        shadow_text = QGraphicsDropShadowEffect()
        shadow_text.setBlurRadius(3)
        shadow_text.setColor(QColor(0, 0, 0, 255))
        shadow_text.setOffset(1, 1)
        self.text_label.setGraphicsEffect(shadow_text)
        container_layout.addWidget(self.text_label)

        self.main_layout.addStretch(1)
        self.main_layout.addWidget(self.container)
        self.main_layout.addStretch(1)
        self.container.hide()

        self.close_timer = QTimer(self)
        self.close_timer.setSingleShot(True)
        self.close_timer.timeout.connect(self.finish_media_playback)

    # ---- petit message de test au démarrage -------------------------------
    def show_notice(self, text, ms=4000):
        self.author_label.clear()
        self.avatar_label.hide()
        self.image_label.hide()
        self.video_label.hide()
        self.text_label.setText(text)
        self.text_label.show()
        self.container.show()
        self.raise_()
        QTimer.singleShot(ms, self._end_notice)

    def _end_notice(self):
        if self.current_loaded_url is None:
            self.text_label.clear()
            self.container.hide()

    def animate_floating_header(self):
        if self.container.isVisible():
            self.float_angle += 0.08
            offset_y = int(math.sin(self.float_angle) * 3)
            self.header_widget.setContentsMargins(0, offset_y, 0, -offset_y)

    def update_alignment(self, position):
        while self.main_layout.count():
            self.main_layout.takeAt(0)
        if position == "left":
            self.main_layout.addWidget(self.container)
            self.main_layout.addStretch(1)
        elif position == "right":
            self.main_layout.addStretch(1)
            self.main_layout.addWidget(self.container)
        else:
            self.main_layout.addStretch(1)
            self.main_layout.addWidget(self.container)
            self.main_layout.addStretch(1)

    # ---- réseau (thread séparé) -------------------------------------------
    def init_network(self):
        t = threading.Thread(target=self.network_worker, daemon=True)
        t.start()

    @staticmethod
    def _is_inactive(data):
        status = data.get("status")
        if isinstance(status, str) and status.lower() in ("inactive", "off", "disabled", "stopped"):
            return True
        if status is False or data.get("active") is False:
            return True
        return False

    def request_quit(self, reason):
        log("Fermeture demandée : " + reason)
        self.quit_requested.emit()
        # filet de sécurité : si la fermeture propre traîne, on tue le process
        threading.Timer(5, lambda: os._exit(0)).start()

    def network_worker(self):
        session = requests.Session()
        last_logged = None
        last_err = None
        count_404 = 0
        fail_url = None
        fail_count = 0

        while True:
            try:
                res = session.get(SERVER_URL, params={"user": self.discord_pseudo}, timeout=10)

                if res.status_code == 404:
                    count_404 += 1
                    if count_404 >= 3:
                        self.request_quit("le serveur répond 404 (3 fois de suite)")
                        return
                    time.sleep(1.5)
                    continue
                count_404 = 0

                if res.status_code != 200:
                    msg = f"HTTP {res.status_code} du serveur"
                    if msg != last_err:
                        log(msg)
                        last_err = msg
                    time.sleep(2)
                    continue
                last_err = None

                data = res.json()
                summary = json.dumps(data, ensure_ascii=False)[:300]
                if summary != last_logged:
                    log("Réponse serveur : " + summary)
                    last_logged = summary

                if self._is_inactive(data):
                    self.request_quit("le serveur dit que le live chat est inactif")
                    return

                new_url = data.get("url")
                if not new_url:
                    self.last_done_url = None
                    if self.current_loaded_url is not None:
                        self.current_loaded_url = None
                        self.hide_requested.emit()
                    time.sleep(1.5)
                    continue

                if new_url == self.last_done_url or new_url == self.current_loaded_url:
                    time.sleep(1.5)
                    continue

                # nouveau média à afficher
                self.current_loaded_url = new_url
                try:
                    media_res = session.get(new_url, timeout=20)
                    if media_res.status_code != 200:
                        raise RuntimeError(f"téléchargement du média : HTTP {media_res.status_code}")
                    data["content_bytes"] = media_res.content
                    data["content_type"] = media_res.headers.get("Content-Type", "")
                    log(f"Média téléchargé ({len(media_res.content)} octets, {data['content_type']})")

                    avatar_url = data.get("avatar", "")
                    if avatar_url:
                        try:
                            ar = session.get(avatar_url, timeout=5)
                            if ar.status_code == 200:
                                data["avatar_bytes"] = ar.content
                        except Exception:
                            pass
                    fail_url, fail_count = None, 0
                    self.media_ready.emit(data)
                except Exception as e:
                    log(f"Échec média : {e}")
                    self.current_loaded_url = None
                    if fail_url == new_url:
                        fail_count += 1
                    else:
                        fail_url, fail_count = new_url, 1
                    if fail_count >= 3:
                        log("Média abandonné après 3 échecs, on le passe")
                        self.last_done_url = new_url
                        self._send_pop_request()
                        fail_url, fail_count = None, 0
                    time.sleep(2)

            except Exception as e:
                msg = f"Erreur réseau : {e}"
                if msg != last_err:
                    log(msg)
                    last_err = msg
                time.sleep(2)

    # ---- affichage (thread interface) -------------------------------------
    def handle_new_media(self, data):
        try:
            self.is_clearing = False
            self.media_player.stop()
            position = data.get("position", "center")
            is_game_mode = data.get("game_mode", False)

            if is_game_mode:
                self.current_max_size = 380
                self.set_video_box(380, 215)
                self.author_label.setFont(QFont("Segoe UI", 13, QFont.Bold))
                self.text_label.setFont(QFont("Segoe UI", 15, QFont.Bold))
                self.text_label.setMaximumWidth(380)
                avatar_size = 26
            else:
                self.current_max_size = 850
                self.set_video_box(850, 480)
                self.author_label.setFont(QFont("Segoe UI", 20, QFont.Bold))
                self.text_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
                self.text_label.setMaximumWidth(850)
                avatar_size = 40

            self.update_alignment(position)

            url = data.get("url")
            if not url:
                self.hide_overlay_ui()
                return

            author = data.get("name", "")
            meme_txt = data.get("content", "")

            if meme_txt:
                self.text_label.setText(meme_txt)
                self.text_label.show()
            else:
                self.text_label.hide()

            self.author_label.setText(str(author))
            self.author_label.show()

            avatar_bytes = data.get("avatar_bytes")
            if avatar_bytes:
                img = QImage.fromData(avatar_bytes)
                pix = QPixmap.fromImage(img)
                self.avatar_label.setPixmap(create_discord_avatar_with_ring(pix, size=avatar_size))
                self.avatar_label.show()
            else:
                self.avatar_label.clear()
                self.avatar_label.hide()

            content = data.get("content_bytes")
            path = urlparse(url).path.lower()
            ctype = (data.get("content_type") or "").lower()
            is_vid = ctype.startswith("video/") or path.endswith(VIDEO_EXTS)

            if is_vid:
                self.image_label.hide()
                self.video_label.clear()
                self.video_label.show()
                self._cleanup_video_file()

                suffix = ".mp4"
                for ext in VIDEO_EXTS:
                    if path.endswith(ext):
                        suffix = ext
                        break

                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                tmp.write(content)
                tmp.flush()
                tmp.close()
                self.video_path = tmp.name
                log(f"Lecture vidéo : {self.video_path}")

                self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(self.video_path)))
                self.container.show()
                self.raise_()
                QTimer.singleShot(100, self.media_player.play)
                # filet de sécurité : si la vidéo ne se termine jamais, on la coupe après 5 min
                self.close_timer.start(300000)
            else:
                self.video_label.hide()
                self.image_label.show()

                image = QImage.fromData(content)
                pixmap = QPixmap.fromImage(image)
                if pixmap.width() > self.current_max_size or pixmap.height() > self.current_max_size:
                    pixmap = pixmap.scaled(self.current_max_size, self.current_max_size,
                                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.image_label.setPixmap(pixmap)
                self.container.show()
                self.raise_()
                self.close_timer.start(7000)
        except Exception:
            log("Erreur dans handle_new_media :\n" + traceback.format_exc())
            self.finish_media_playback()

    def set_video_box(self, w, h):
        self.video_label.setFixedSize(w, h)
        self.video_surface.target_size = (w, h)

    def on_video_frame(self, img):
        if self.current_loaded_url is not None:
            self.video_label.setPixmap(QPixmap.fromImage(img))

    def on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            log("Fin de la vidéo")
            self.finish_media_playback()
        elif status == QMediaPlayer.InvalidMedia:
            log("Vidéo invalide : codec manquant sur ce PC ?")
            QTimer.singleShot(1500, self.finish_media_playback)

    def on_media_error(self, err):
        log(f"Erreur du lecteur vidéo : {err} - {self.media_player.errorString()}")
        QTimer.singleShot(1500, self.finish_media_playback)

    def finish_media_playback(self):
        if self.is_clearing or self.current_loaded_url is None:
            return
        self.is_clearing = True
        self.close_timer.stop()
        self.last_done_url = self.current_loaded_url
        threading.Thread(target=self._send_pop_request, daemon=True).start()
        self.hide_overlay_ui()

    def _send_pop_request(self):
        try:
            requests.post(POP_URL, params={"user": self.discord_pseudo}, timeout=5)
        except Exception as e:
            log(f"Erreur pop_meme : {e}")

    def _cleanup_video_file(self):
        if self.video_path:
            try:
                self.media_player.stop()
                self.media_player.setMedia(QMediaContent())  # libère le fichier sous Windows
                if os.path.exists(self.video_path):
                    os.remove(self.video_path)
            except Exception:
                pass
            self.video_path = None

    def hide_overlay_ui(self):
        self.close_timer.stop()
        self.media_player.stop()
        self.image_label.clear()
        self.video_label.hide()
        self.image_label.hide()
        self.author_label.clear()
        self.avatar_label.clear()
        self.text_label.clear()
        self.container.hide()
        self._cleanup_video_file()
        self.current_loaded_url = None
        self.is_clearing = False

    def cleanup(self):
        self._cleanup_video_file()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not acquire_single_instance():
        log("Une autre instance tourne déjà")
        QMessageBox.warning(None, "Live Chat déjà actif", "Le live chat est déjà actif sur le PC !")
        return 0

    # nettoyage de l'ancien système de lock par fichier
    try:
        if os.path.exists(OLD_LOCK_FILE):
            os.remove(OLD_LOCK_FILE)
    except Exception:
        pass

    setup = SetupDialog()
    setup.exec_()
    if not setup.pseudo:
        log("Configuration fermée sans pseudo")
        return 0

    log(f"Pseudo : {setup.pseudo}")
    overlay = OverlayWindow(setup.pseudo)
    overlay.quit_requested.connect(app.quit)
    overlay.show()
    overlay.show_notice("Overlay connecté ✅", 4000)

    code = app.exec_()
    overlay.cleanup()
    log("=== Arrêt propre ===")
    return code


if __name__ == "__main__":
    sys.exit(main())
