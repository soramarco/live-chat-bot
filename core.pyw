import sys
import os
import time
import math
import json
import tempfile
import threading
import traceback
import io
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

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
log("CORE_BUILD_ID=2026-10-12-core-vertically-centered-v6")

try:
    from PyQt5.QtCore import Qt, QTimer, QUrl, QPoint, QRect, QRectF, QPointF, QSize, pyqtSignal, pyqtProperty, QPropertyAnimation, QEasingCurve
    from PyQt5.QtGui import (
        QFont, QColor, QPainter, QBrush, QPainterPath, QPixmap, QImage, QPen, QFontMetrics,
    )
    from PyQt5.QtWidgets import (
        QApplication, QLabel, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
        QGraphicsDropShadowEffect, QLineEdit, QPushButton, QMessageBox, QSizePolicy,
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

VIDEO_EXTS = (
    ".mp4", ".m4v", ".mov", ".webm", ".avi", ".mkv", ".wmv",
    ".flv", ".mpeg", ".mpg", ".3gp", ".ogv", ".ts", ".m2ts",
)


def is_video_media(url, content_type, content):
    """Détecte les vidéos par type MIME, extension ou signature du fichier."""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    path = urlparse(url or "").path.lower()
    data = content or b""

    if mime.startswith("video/") or path.endswith(VIDEO_EXTS):
        return True

    # MP4 / MOV / M4V : les fichiers ISO-BMFF contiennent généralement 'ftyp' ici.
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return True
    # WebM / Matroska (signature EBML).
    if data.startswith(bytes.fromhex("1a45dfa3")):
        return True
    # AVI.
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"AVI ":
        return True
    # FLV.
    if data.startswith(b"FLV"):
        return True

    return False


def infer_video_suffix(url, content_type, content):
    """Choisit une extension locale cohérente pour aider le lecteur Qt."""
    path = urlparse(url or "").path.lower()
    for ext in VIDEO_EXTS:
        if path.endswith(ext):
            return ext

    mime = (content_type or "").split(";", 1)[0].strip().lower()
    mime_suffixes = {
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
        "video/x-matroska": ".mkv",
        "video/x-msvideo": ".avi",
        "video/avi": ".avi",
        "video/x-ms-wmv": ".wmv",
        "video/x-flv": ".flv",
        "video/mpeg": ".mpeg",
        "video/3gpp": ".3gp",
        "video/mp2t": ".ts",
    }
    if mime in mime_suffixes:
        return mime_suffixes[mime]

    data = content or b""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return ".mp4"
    if data.startswith(bytes.fromhex("1a45dfa3")):
        return ".webm"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"AVI ":
        return ".avi"
    if data.startswith(b"FLV"):
        return ".flv"
    return ".mp4"


class UnsupportedMedia(Exception):
    pass

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
        ctypes.set_last_error(0)
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
# EN-TETE (avatar + pseudo) AVEC FLOTTEMENT FLUIDE ET CENTRAGE CORRIGÉ
# ---------------------------------------------------------------------------
class FloatingHeader(QWidget):
    PAD = 6
    GAP = 10
    AMP = 3.0      # amplitude douce du flottement en pixels
    SPEED = 1.8    # vitesse (radians/seconde) : plus petit = plus lent

    def __init__(self, parent=None):
        super().__init__(parent)
        self._avatar = None
        self._text = ""
        self._font = QFont("Segoe UI", 20, QFont.Bold)
        self._paint_opacity = 1.0
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)  # environ 60 images/s
        self._timer.timeout.connect(self.update)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def getPaintOpacity(self):
        return self._paint_opacity

    def setPaintOpacity(self, value):
        self._paint_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    paintOpacity = pyqtProperty(float, fget=getPaintOpacity, fset=setPaintOpacity)

    def set_content(self, text, avatar, font):
        self._text = text
        self._avatar = avatar
        self._font = font
        # L'animation ne tourne que lorsque l'en-tête contient réellement du contenu.
        if (self._text or self._avatar) and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.updateGeometry()
        self.update()

    def clear_content(self):
        self._text = ""
        self._avatar = None
        self._timer.stop()
        self.updateGeometry()
        self.update()

    def _text_width(self, fm):
        try:
            return fm.horizontalAdvance(self._text)
        except AttributeError:
            return fm.width(self._text)

    def sizeHint(self):
        if not self._text and not self._avatar:
            return QSize(0, 0)
        fm = QFontMetrics(self._font)
        text_w = self._text_width(fm) if self._text else 0
        av_w = self._avatar.width() if self._avatar else 0
        av_h = self._avatar.height() if self._avatar else 0
        gap = self.GAP if (self._avatar and self._text) else 0
        w = av_w + gap + text_w + 2 * self.PAD + 6
        h = int(max(av_h, fm.height()) + 2 * self.PAD + 2 * self.AMP)
        return QSize(w, h)

    def minimumSizeHint(self):
        return self.sizeHint()

    def showEvent(self, event):
        if self._text or self._avatar:
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        if not self._text and not self._avatar:
            return
        p = QPainter(self)
        p.setRenderHints(
            QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        p.setOpacity(self._paint_opacity)
        offset = math.sin((time.monotonic() - self._t0) * self.SPEED) * self.AMP
        fm = QFontMetrics(self._font)
        cy = self.height() / 2.0 + offset

        # Correction : calcul précis pour centrer le bloc complet (avatar + texte) au milieu du widget
        av_w = self._avatar.width() if self._avatar else 0
        text_w = self._text_width(fm) if self._text else 0
        gap = self.GAP if (self._avatar and self._text) else 0
        total_content_width = av_w + gap + text_w
        
        x = (self.width() - total_content_width) / 2.0
        if x < 0:
            x = self.PAD + 3.0

        if self._avatar:
            p.drawPixmap(QPointF(x, cy - self._avatar.height() / 2.0), self._avatar)
            x += self._avatar.width() + self.GAP
        if self._text:
            baseline = cy - fm.height() / 2.0 + fm.ascent()
            path = QPainterPath()
            path.addText(QPointF(x, baseline), self._font, self._text)
            p.setPen(QPen(QColor(0, 0, 0, 220), 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.fillPath(path, QBrush(QColor(255, 255, 255)))
        p.end()

# ---------------------------------------------------------------------------
# SURFACE D'AFFICHAGE PEINTE DIRECTEMENT (images et frames vidéo)
# ---------------------------------------------------------------------------
class FrameDisplayWidget(QWidget):
    """Affiche l'image courante par QPainter, sans cache pixmap de QLabel."""
    def __init__(self, parent=None, fixed_box=False):
        super().__init__(parent)
        self._image = QImage()
        self._paint_opacity = 1.0
        self._fixed_box = fixed_box
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def getPaintOpacity(self):
        return self._paint_opacity

    def setPaintOpacity(self, value):
        self._paint_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    paintOpacity = pyqtProperty(float, fget=getPaintOpacity, fset=setPaintOpacity)

    def setPixmap(self, pixmap):
        if pixmap is None or pixmap.isNull():
            self.clear()
            return
        self._image = pixmap.toImage().copy()
        if not self._fixed_box:
            self.setFixedSize(pixmap.size())
        self.updateGeometry()
        self.update()
        self.repaint()

    def setImage(self, image):
        if image is None or image.isNull():
            self.clear()
            return
        # Toujours posséder une copie indépendante des buffers vidéo/Qt.
        self._image = image.copy()
        self.update()
        self.repaint()

    def clear(self):
        self._image = QImage()
        self.update()
        self.repaint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # Les widgets vidéo utilisent une boîte fixe (850x480 ou 380x215) :
        # effacer TOUTE la boîte à chaque frame empêche les pixels d'une ancienne
        # frame paysage de rester derrière une nouvelle frame portrait, et inversement.
        # L'effacement et le dessin ont lieu dans le même paintEvent.
        if self._fixed_box or self._image.isNull() or self.width() <= 0 or self.height() <= 0:
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))

        if self._image.isNull() or self.width() <= 0 or self.height() <= 0:
            painter.end()
            return

        target_size = self._image.size().scaled(self.size(), Qt.KeepAspectRatio)
        x = (self.width() - target_size.width()) // 2
        y = (self.height() - target_size.height()) // 2
        target = QRectF(x, y, target_size.width(), target_size.height())

        if self._paint_opacity >= 0.999:
            # En régime normal, remplacer directement les pixels de l'image.
            # Ne pas effacer toute la surface transparente puis recomposer
            # chaque frame : cela peut provoquer des artefacts de composition
            # sur les fenêtres Qt translucides sous Windows (image visible en
            # capture mais absente/ fantôme sur l'écran).
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.setOpacity(1.0)
            painter.drawImage(target, self._image)
        else:
            # Pendant le fondu uniquement, on doit repartir d'une surface vide
            # pour éviter de mélanger plusieurs frames semi-transparentes.
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setOpacity(self._paint_opacity)
            painter.drawImage(target, self._image)
        painter.end()


# ---------------------------------------------------------------------------
# AFFICHAGE VIDEO IMAGE PAR IMAGE
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
            # Détacher les pixels AVANT de libérer la frame. Si une conversion
            # change le format, Qt crée déjà un nouveau buffer; ne pas le recopier.
            # Si le format est déjà RGB32, une copie explicite reste nécessaire
            # car l'image source pointe encore sur le buffer mappé de QVideoFrame.
            if img.format() == QImage.Format_RGB32:
                img = img.copy()
            else:
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
# FENETRE VIDEO NATIVE OPAQUE
# ---------------------------------------------------------------------------
class OpaqueVideoWindow(QWidget):
    """Fenêtre native opaque indépendante du canvas translucide plein écran.

    Sous Windows, certaines combinaisons QWidget enfant + WA_TranslucentBackground
    peuvent livrer des frames valides (alpha 255) que DWM compose mal. La vidéo
    est donc dessinée dans son propre HWND opaque, à la position du placeholder.
    """
    def __init__(self):
        flags = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                 Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self._frame = QImage()
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background-color: #000000; border: none;")
        self.hide()

    def show_frame(self, frame, geometry):
        if frame is None or frame.isNull() or geometry.width() <= 0 or geometry.height() <= 0:
            self.clear_frame()
            return
        # Les frames proviennent déjà de FrameSurface en RGB32 et sont détachées.
        # QImage utilise le partage implicite : évite une copie mémoire à chaque frame.
        self._frame = (frame if frame.format() == QImage.Format_RGB32
                       else frame.convertToFormat(QImage.Format_RGB32).copy())
        # Ne modifier la fenêtre que si sa géométrie change; ne pas la remonter
        # au premier plan ni forcer un repaint synchrone à chaque frame vidéo.
        geometry_changed = self.geometry() != geometry
        if geometry_changed:
            self.setGeometry(geometry)
        if not self.isVisible():
            self.show()
            self.raise_()
        elif geometry_changed:
            self.raise_()
        # update() laisse Qt regrouper les frames si le PC est momentanément occupé.
        self.update()

    def clear_frame(self):
        self._frame = QImage()
        self.hide()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(event.rect(), QColor(0, 0, 0, 255))
        if not self._frame.isNull():
            if self._frame.size() == self.size():
                # La frame est déjà à la taille exacte de la fenêtre : pas de redimensionnement.
                painter.drawImage(0, 0, self._frame)
            else:
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                painter.drawImage(self.rect(), self._frame)
        painter.end()


# ---------------------------------------------------------------------------
# OVERLAY
# ---------------------------------------------------------------------------
class OverlayWindow(QWidget):
    media_ready = pyqtSignal(dict)
    settings_ready = pyqtSignal(dict)
    hide_requested = pyqtSignal()
    cancel_current_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, discord_pseudo):
        super().__init__()
        self.discord_pseudo = discord_pseudo
        self.video_path = None
        self.current_loaded_url = None
        self.last_done_url = None
        self.is_clearing = False
        self.current_max_size = 850
        self._video_frames_shown = 0
        self._video_frames_received = 0
        self._video_box_size = QSize(850, 480)
        self._last_video_source_size = QSize()
        self._last_video_source_frame = QImage()
        self._fade_animations = []
        self.current_position = None
        self.current_game_mode = None
        self.current_author = ""
        self.current_avatar_source = None
        self._current_image_pixmap = None
        self.current_loaded_item_key = None
        # État suivi uniquement par le thread de polling, avec accès protégé.
        # On observe les changements du serveur même pendant une lecture :
        # Stop/avance en file doit interrompre le média affiché immédiatement.
        self._network_state_lock = threading.RLock()
        self._network_active_key = None
        self._network_last_done_key = None

        self.media_ready.connect(self.handle_new_media)
        self.settings_ready.connect(self.apply_live_settings)
        self.hide_requested.connect(self.hide_overlay_ui)
        self.cancel_current_requested.connect(self.cancel_current_media)

        self.init_ui()
        self.init_network()

    def init_ui(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        log(f"Taille de l'overlay : {screen.width()}x{screen.height()}")

        self.main_layout = QHBoxLayout(self)
        # Léger décalage vertical vers le bas : conserve les positions
        # horizontales gauche / centre / droite et suit les deux rendus vidéo.
        self.main_layout.setContentsMargins(40, 30, 40, 0)

        self.container = QWidget(self)
        # On évite setWindowOpacity sur la fenêtre plein écran transparente :
        # l'opacité globale peut perturber la composition graphique de Windows.
        # Le fondu d'entrée est peint localement par FloatingHeader/FrameDisplayWidget.
        container_layout = QVBoxLayout(self.container)
        container_layout.setAlignment(Qt.AlignCenter)
        container_layout.setSpacing(12)

        self.header = FloatingHeader(self.container)
        container_layout.addWidget(self.header)

        self.image_label = FrameDisplayWidget(self.container, fixed_box=False)
        container_layout.addWidget(self.image_label, 0, Qt.AlignCenter)

        # La vidéo est dessinée dans une zone aux dimensions exactes de son ratio.
        # En mode normal, cette zone possède un fond opaque : sur une fenêtre
        # plein écran translucide Windows, un QLabel transparent peut composer
        # ses pixels en semi-transparence (visible seulement au screenshot).
        # En mode jeu, on conserve le rendu compact déjà fonctionnel.
        # Mode jeu : conserver le QLabel natif qui fonctionnait déjà.
        self.video_label = QLabel(self.container)
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background: transparent; border: none;")
        self.video_label.setAutoFillBackground(False)
        self.video_label.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.video_label.setFixedSize(850, 480)
        container_layout.addWidget(self.video_label, 0, Qt.AlignCenter)
        self.video_label.hide()

        # Mode normal : utiliser le même moteur de peinture que les images fixes.
        # Les frames vidéo n'empruntent plus le chemin QLabel + pixmap dans la
        # fenêtre translucide plein écran, source probable de l'image fantôme.
        self.video_canvas = FrameDisplayWidget(self.container, fixed_box=False)
        self.video_canvas.setFixedSize(850, 480)
        # En mode normal, ce widget est seulement un placeholder transparent
        # dans le layout. Les pixels réels sont affichés par video_window,
        # une fenêtre native opaque au-dessus du bureau.
        container_layout.addWidget(self.video_canvas, 0, Qt.AlignCenter)
        self.video_canvas.hide()
        self.video_window = OpaqueVideoWindow()

        self.video_surface = FrameSurface(self)
        self.video_surface.frame_ready.connect(self.on_video_frame)

        self.media_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        self.media_player.setVideoOutput(self.video_surface)
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

    def show_with_fade(self):
        """Fondu local des widgets, sans animer l'opacité de la fenêtre Windows."""
        for animation in self._fade_animations:
            animation.stop()
            animation.deleteLater()
        self._fade_animations = []

        # L'opacité locale du QLabel vidéo n'est pas animée : une animation
        # d'opacité sur les frames dans une fenêtre translucide provoquait des
        # frames invisibles sous Windows. Le header et les images gardent leur fondu.
        targets = [self.header]
        if self.video_label.isHidden() and not self.image_label.isHidden():
            targets.append(self.image_label)

        for widget in targets:
            widget.setPaintOpacity(0.0)

        self.container.show()
        self.container.raise_()
        self.raise_()

        for widget in targets:
            animation = QPropertyAnimation(widget, b"paintOpacity", self)
            animation.setDuration(280)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.start()
            self._fade_animations.append(animation)

    def show_notice(self, text, ms=4000):
        self.header.clear_content()
        self.image_label.hide()
        self.video_label.hide()
        self.video_canvas.hide()
        self.video_window.clear_frame()
        self.text_label.setText(text)
        self.text_label.show()
        self.container.show()
        self.raise_()
        QTimer.singleShot(ms, self._end_notice)

    def _end_notice(self):
        if self.current_loaded_url is None:
            self.text_label.clear()
            self.container.hide()

    def update_alignment(self, position):
        """Garde gauche/centre/droite horizontalement et centre le groupe verticalement.

        Le container avait une hauteur préférée et pouvait rester près du haut de
        l'écran. L'alignement Qt.AlignVCenter le place au milieu de la zone écran,
        comme sur la capture de référence, quel que soit le choix horizontal.
        Les widgets vidéo natifs suivent ce container via mapToGlobal().
        """
        while self.main_layout.count():
            self.main_layout.takeAt(0)
        if position == "left":
            self.main_layout.addWidget(self.container, 0, Qt.AlignVCenter)
            self.main_layout.addStretch(1)
        elif position == "right":
            self.main_layout.addStretch(1)
            self.main_layout.addWidget(self.container, 0, Qt.AlignVCenter)
        else:
            self.main_layout.addStretch(1)
            self.main_layout.addWidget(self.container, 0, Qt.AlignVCenter)
            self.main_layout.addStretch(1)

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

    def _make_displayable_image(self, session, url, content):
        if not QImage.fromData(content).isNull():
            return content
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(content)).convert("RGBA")
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            png = buf.getvalue()
            if not QImage.fromData(png).isNull():
                log("Image convertie avec Pillow")
                return png
        except Exception as e:
            log(f"Conversion Pillow impossible : {e}")

        try:
            parts = urlparse(url)
            if parts.netloc in ("cdn.discordapp.com", "media.discordapp.net"):
                query = dict(parse_qsl(parts.query))
                query["format"] = "png"
                new_url = urlunparse(parts._replace(netloc="media.discordapp.net",
                                                    query=urlencode(query)))
                r = session.get(new_url, timeout=15)
                if r.status_code == 200 and not QImage.fromData(r.content).isNull():
                    log("Image convertie via le proxy Discord")
                    return r.content
        except Exception as e:
            log(f"Conversion via proxy impossible : {e}")
        return None

    def request_quit(self, reason):
        log("Fermeture demandée : " + reason)
        # L'arrêt passe par la boucle Qt pour permettre le nettoyage du fichier vidéo.
        self.quit_requested.emit()

    def network_worker(self):
        """Interroge le serveur et suit les changements de média en temps réel.

        La file est pilotée par le serveur : le client ne lance qu'un élément à
        la fois. Si Stop retire l'élément courant ou si le serveur promeut le
        suivant, on arrête localement l'ancien média sans envoyer un second pop.
        """
        session = requests.Session()
        last_logged = None
        last_err = None
        count_404 = 0
        fail_url = None
        fail_count = 0
        # Le launcher déjà distribué ne fait que démarrer ce cœur une fois.
        # Pour éviter un deuxième programme de surveillance, le cœur reste en
        # arrière-plan quand le Live Chat est inactif et ne masque que l'overlay.
        # Ainsi, une prochaine activation sera détectée sans modifier le launcher.
        server_was_inactive = False

        while True:
            try:
                res = session.get(SERVER_URL, params={"user": self.discord_pseudo}, timeout=10)

                if res.status_code == 404:
                    count_404 += 1
                    if count_404 >= 3 and not server_was_inactive:
                        log("Serveur HTTP 404 répété : overlay masqué; le cœur reste en attente et réessaie")
                        server_was_inactive = True
                        with self._network_state_lock:
                            self._network_active_key = None
                            self._network_last_done_key = None
                        self.hide_requested.emit()
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
                    if not server_was_inactive:
                        log("Live Chat désactivé : overlay et média masqués; le cœur reste en attente de la prochaine activation")
                        server_was_inactive = True
                        with self._network_state_lock:
                            self._network_active_key = None
                            self._network_last_done_key = None
                        self.hide_requested.emit()
                    time.sleep(1.2)
                    continue

                # Le processus reste vivant pendant l'état inactif. Dès que le
                # serveur repasse actif, ce même cœur reçoit les nouveaux médias.
                server_was_inactive = False

                self.settings_ready.emit({
                    "position": data.get("position", "center"),
                    "game_mode": bool(data.get("game_mode", False)),
                })

                new_url = data.get("url")
                if not new_url:
                    # Aucune source active côté serveur : effacer l'ancien overlay.
                    # Ne pas envoyer /pop_meme ici : le serveur a déjà vidé la file.
                    with self._network_state_lock:
                        had_active = self._network_active_key is not None
                        self._network_active_key = None
                        self._network_last_done_key = None
                    if had_active:
                        log("Le serveur n'a plus de média actif : arrêt local immédiat")
                        self.cancel_current_requested.emit()
                    time.sleep(0.8)
                    continue

                item_key = str(data.get("item_id") or new_url)
                with self._network_state_lock:
                    active_key = self._network_active_key
                    last_done_key = self._network_last_done_key
                    if item_key == active_key or item_key == last_done_key:
                        should_fetch = False
                        changed_item = False
                    else:
                        changed_item = active_key is not None and active_key != item_key
                        self._network_active_key = item_key
                        should_fetch = True

                if not should_fetch:
                    time.sleep(0.45)
                    continue

                if changed_item:
                    log(f"Média actif changé côté serveur ({active_key} -> {item_key}) : arrêt de l'ancien sans pop")
                    self.cancel_current_requested.emit()

                try:
                    media_res = session.get(new_url, timeout=20)
                    if media_res.status_code != 200:
                        raise RuntimeError(f"téléchargement du média : HTTP {media_res.status_code}")

                    data["url"] = new_url
                    data["item_id"] = item_key
                    data["content_bytes"] = media_res.content
                    data["content_type"] = media_res.headers.get("Content-Type", "")
                    log(f"Média téléchargé ({len(media_res.content)} octets, {data['content_type']})")

                    if not is_video_media(new_url, data["content_type"], media_res.content):
                        displayable = self._make_displayable_image(session, new_url, media_res.content)
                        if displayable is None:
                            raise UnsupportedMedia(
                                "format d'image non supporté (" + data["content_type"] + ")")
                        data["content_bytes"] = displayable

                    avatar_url = data.get("avatar", "")
                    if avatar_url:
                        try:
                            ar = session.get(avatar_url, timeout=5)
                            if ar.status_code == 200:
                                data["avatar_bytes"] = ar.content
                        except Exception as e:
                            log(f"Téléchargement de l'avatar ignoré : {e}")

                    fail_url, fail_count = None, 0
                    self.media_ready.emit(data)
                except UnsupportedMedia as e:
                    log(f"Média ignoré : {e}")
                    self._post_pop_and_release(item_key, session=session)
                    fail_url, fail_count = None, 0
                except Exception as e:
                    log(f"Échec média : {e}")
                    if fail_url == new_url:
                        fail_count += 1
                    else:
                        fail_url, fail_count = new_url, 1
                    if fail_count >= 3:
                        log("Média abandonné après 3 échecs, on le passe")
                        self._post_pop_and_release(item_key, session=session)
                        fail_url, fail_count = None, 0
                    else:
                        with self._network_state_lock:
                            if self._network_active_key == item_key:
                                self._network_active_key = None
                        time.sleep(2)

            except Exception as e:
                msg = f"Erreur réseau : {e}"
                if msg != last_err:
                    log(msg)
                    last_err = msg
                time.sleep(2)

    def _post_pop_and_release(self, completed_key, session=None):
        """Passe l'élément terminé uniquement si c'est encore l'élément actif."""
        client = session or requests
        last_error = None
        for attempt in range(4):
            try:
                response = client.post(
                    POP_URL,
                    params={"user": self.discord_pseudo, "item_id": str(completed_key or "")},
                    timeout=8,
                )
                try:
                    result = response.json()
                except Exception:
                    result = {}
                log(f"Réponse pop_meme : HTTP {response.status_code}, {result}")

                status = result.get("status")
                if response.status_code < 400 and status in ("success", "stale"):
                    # stale signifie que Stop a déjà avancé le serveur. Surtout ne
                    # jamais retenter un pop, car cela ferait sauter le suivant.
                    if status == "stale":
                        log("Pop ignoré : l'élément a déjà été remplacé côté serveur")
                    with self._network_state_lock:
                        self._network_last_done_key = str(completed_key or "")
                        if self._network_active_key == str(completed_key or ""):
                            self._network_active_key = None
                    return

                last_error = f"HTTP {response.status_code}, statut={status}"
                if status not in ("ignored",):
                    break
            except Exception as e:
                last_error = str(e)
                log(f"Erreur pop_meme (tentative {attempt + 1}/4) : {e}")

            # L'anti-double pop du serveur est de 1,5 s. Réessayer sans libérer
            # l'identifiant de l'élément courant pendant l'attente.
            if attempt < 3:
                time.sleep(1.6)

        log(f"Impossible de confirmer pop_meme après les tentatives : {last_error}")
        with self._network_state_lock:
            if self._network_active_key == str(completed_key or ""):
                self._network_active_key = None

    def _render_video_frame_for_mode(self, source_frame):
        """Mode jeu : chemin QLabel historique ; mode normal : fenêtre opaque native."""
        if source_frame is None or source_frame.isNull():
            return
        frame = (source_frame if source_frame.format() == QImage.Format_RGB32
                 else source_frame.convertToFormat(QImage.Format_RGB32).copy())
        if frame.isNull():
            log("Frame vidéo RGB32 vide; frame ignorée")
            return

        box = self._video_box_size
        target = frame.size().scaled(box, Qt.KeepAspectRatio)
        if target.isEmpty():
            return
        # Conserver le redimensionnement de haute qualité, mais éviter de le recalculer
        # quand la frame est déjà à la bonne taille.
        display_frame = (frame if frame.size() == target else
                         frame.scaled(target, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))

        if self.current_game_mode:
            # Mode jeu : garder le chemin QLabel. Ne nettoyer la fenêtre native
            # que lors du basculement, pas sur chaque frame en mode jeu.
            if self.video_window.isVisible():
                self.video_window.clear_frame()
            if not self.video_canvas.isHidden():
                self.video_canvas.clear()
                self.video_canvas.hide()
            self.video_label.setFixedSize(box)
            pixmap = QPixmap.fromImage(display_frame)
            if pixmap.isNull():
                return
            if self.video_label.isHidden():
                self.video_label.show()
            self.video_label.setPixmap(pixmap)
            # setPixmap planifie déjà le repaint; un repaint synchrone bloquerait
            # la boucle graphique à chaque frame sur les machines modestes.
            return

        # Mode normal : ne dessine PAS la vidéo dans un enfant de la fenêtre
        # WA_TranslucentBackground. On ne garde ici qu'un placeholder transparent
        # de la bonne taille pour que la mise en page conserve header / média / texte.
        if not self.video_label.isHidden():
            self.video_label.clear()
            self.video_label.hide()
        layout_changed = False
        # Le canvas est un placeholder vide; ne pas appeler clear()/repaint à chaque frame.
        if self.video_canvas.size() != target:
            old_size = self.video_canvas.size()
            self.video_canvas.setFixedSize(target)
            layout_changed = True
            log(f"Canvas placeholder normal redimensionné : {old_size.width()}x{old_size.height()} "
                f"-> {target.width()}x{target.height()}")
        if self.video_canvas.isHidden():
            self.video_canvas.show()
            layout_changed = True
        # Ces opérations coûteuses ne sont nécessaires qu'au changement de layout,
        # pas à chaque image décodée (souvent 30 ou 60 fois par seconde).
        if layout_changed:
            self.container.adjustSize()
            if self.container.layout() is not None:
                self.container.layout().activate()
            self.main_layout.activate()

        top_left = self.video_canvas.mapToGlobal(QPoint(0, 0))
        geometry = QRect(top_left, target)
        self.video_window.show_frame(display_frame, geometry)
        log_once_key = "_opaque_video_window_logged"
        if not getattr(self, log_once_key, False):
            setattr(self, log_once_key, True)
            log(f"VIDEO_WINDOW_NATIVE_TEST: géométrie={geometry.x()},{geometry.y()} "
                f"{geometry.width()}x{geometry.height()}, frame={display_frame.width()}x{display_frame.height()}, "
                f"mode_jeu=False, visible={self.video_window.isVisible()}, native={self.video_window.testAttribute(Qt.WA_NativeWindow)}")

    def apply_live_settings(self, data, force=False):
        """Applique position et mode jeu même si la vidéo actuelle n'a pas changé."""
        position = data.get("position", "center")
        if position not in ("left", "center", "right"):
            position = "center"
        is_game_mode = bool(data.get("game_mode", False))

        if (not force and position == self.current_position
                and is_game_mode == self.current_game_mode):
            return

        self.current_position = position
        self.current_game_mode = is_game_mode
        if is_game_mode:
            self.current_max_size = 380
            self.set_video_box(380, 215)
            header_font = QFont("Segoe UI", 13, QFont.Bold)
            self.text_label.setFont(QFont("Segoe UI", 15, QFont.Bold))
            self.text_label.setMaximumWidth(380)
            avatar_size = 26
        else:
            # Le cadre garde un fond transparent comme en mode jeu ; sa géométrie
            # suit toujours le ratio vidéo dans la boîte maximale 850x480.
            self.current_max_size = 850
            self.set_video_box(850, 480)
            header_font = QFont("Segoe UI", 20, QFont.Bold)
            self.text_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
            self.text_label.setMaximumWidth(850)
            avatar_size = 40

        self.update_alignment(position)
        self.main_layout.activate()
        if self.container.layout() is not None:
            self.container.layout().activate()

        if self.current_author:
            avatar = None
            if self.current_avatar_source is not None and not self.current_avatar_source.isNull():
                avatar = create_discord_avatar_with_ring(self.current_avatar_source, size=avatar_size)
            self.header.set_content(self.current_author, avatar, header_font)

        if self.current_loaded_url and not self._last_video_source_frame.isNull():
            self._render_video_frame_for_mode(self._last_video_source_frame)

        if self._current_image_pixmap is not None and not self._current_image_pixmap.isNull():
            pixmap = self._current_image_pixmap
            if pixmap.width() > self.current_max_size or pixmap.height() > self.current_max_size:
                pixmap = pixmap.scaled(self.current_max_size, self.current_max_size,
                                       Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(pixmap)

    def handle_new_media(self, data):
        try:
            self.is_clearing = False
            # Cet état est propre au thread graphique ; le thread réseau n'y touche plus.
            self.current_loaded_url = data.get("url")
            self.current_loaded_item_key = str(data.get("item_id") or data.get("url") or "")
            self._last_video_source_frame = QImage()
            self.media_player.stop()
            self.apply_live_settings(data, force=True)
            if self.current_game_mode:
                header_font = QFont("Segoe UI", 13, QFont.Bold)
                avatar_size = 26
            else:
                header_font = QFont("Segoe UI", 20, QFont.Bold)
                avatar_size = 40

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

            avatar_source = None
            avatar_bytes = data.get("avatar_bytes")
            if avatar_bytes:
                img = QImage.fromData(avatar_bytes)
                pix = QPixmap.fromImage(img)
                if not pix.isNull():
                    avatar_source = pix
            self.current_author = str(author)
            self.current_avatar_source = avatar_source
            avatar_pix = None
            if avatar_source is not None:
                avatar_pix = create_discord_avatar_with_ring(avatar_source, size=avatar_size)
            self.header.set_content(self.current_author, avatar_pix, header_font)

            content = data.get("content_bytes") or b""
            self._current_image_pixmap = None
            path = urlparse(url).path.lower()
            ctype = data.get("content_type") or ""
            is_vid = is_video_media(url, ctype, content)

            if is_vid:
                # Vider l'image précédente avant d'afficher la vidéo afin d'éviter
                # que le backing store transparent conserve une ancienne image.
                self.image_label.clear()
                self.image_label.hide()
                self.video_label.clear()
                self.video_label.hide()
                self.video_canvas.clear()
                self.video_canvas.hide()
                self.video_window.clear_frame()
                self._video_frames_shown = 0
                self._cleanup_video_file()

                suffix = infer_video_suffix(url, ctype, content)
                self._video_frames_shown = 0
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                tmp.write(content)
                tmp.flush()
                tmp.close()
                self.video_path = tmp.name
                log(f"Lecture vidéo : {self.video_path}")

                self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(self.video_path)))
                self.show_with_fade()
                self.raise_()
                QTimer.singleShot(100, self.media_player.play)
                self.close_timer.start(300000)
            else:
                self.video_label.clear()
                self.video_label.hide()
                self.video_canvas.clear()
                self.video_canvas.hide()
                self.video_window.clear_frame()
                self.image_label.show()

                image = QImage.fromData(content)
                if image.isNull():
                    raise UnsupportedMedia("Qt ne peut pas décoder cette image après conversion")
                pixmap = QPixmap.fromImage(image)
                if pixmap.isNull():
                    raise UnsupportedMedia("impossible de créer le bitmap de l'image")
                if pixmap.width() > self.current_max_size or pixmap.height() > self.current_max_size:
                    pixmap = pixmap.scaled(self.current_max_size, self.current_max_size,
                                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._current_image_pixmap = QPixmap.fromImage(image)
                self.image_label.setPixmap(pixmap)
                self.show_with_fade()
                self.raise_()
                self.close_timer.start(7000)
        except Exception:
            log("Erreur dans handle_new_media :\n" + traceback.format_exc())
            self.finish_media_playback()

    def set_video_box(self, w, h):
        """Définit la boîte de rendu fixe; l'image garde son ratio à l'intérieur."""
        self._video_box_size = QSize(int(w), int(h))
        self.video_surface.target_size = (int(w), int(h))
        self.video_label.setFixedSize(int(w), int(h))
        if self.current_game_mode:
            self.video_canvas.setFixedSize(int(w), int(h))
        elif self.video_canvas.isHidden() or self._last_video_source_frame.isNull():
            self.video_canvas.setFixedSize(int(w), int(h))

    def on_video_frame(self, img):
        if self.current_loaded_url is None or img is None or img.isNull():
            return
        try:
            # FrameSurface émet déjà une QImage RGB32 détachée du buffer vidéo.
            # On partage cette image implicitement au lieu de la recopier deux fois.
            frame = (img if img.format() == QImage.Format_RGB32
                     else img.convertToFormat(QImage.Format_RGB32).copy())
            if frame.isNull():
                log("Image vidéo reçue mais copie RGB32 impossible")
                return
            self._last_video_source_size = frame.size()
            self._last_video_source_frame = frame  # copie implicite, sans recopier les pixels
            self._video_frames_received += 1
            self._render_video_frame_for_mode(frame)
            self._video_frames_shown += 1
            if self._video_frames_shown == 1:
                target = frame.size().scaled(self._video_box_size, Qt.KeepAspectRatio)
                alpha = (frame.pixel(frame.width() // 2, frame.height() // 2) >> 24) & 0xff
                renderer = "QLabel" if self.current_game_mode else "OpaqueVideoWindow"
                box_size = self._video_box_size
                log(f"CORE_RENDER_TEST : première frame source={frame.width()}x{frame.height()}, "
                    f"cible={target.width()}x{target.height()}, boîte={box_size.width()}x{box_size.height()}, "
                    f"mode_jeu={self.current_game_mode}, alpha={alpha}, renderer={renderer}")
        except Exception:
            log("Erreur d'affichage d'une frame vidéo :\n" + traceback.format_exc())

    def paintEvent(self, event):
        # Le top-level est une fenêtre transparente plein écran. Effacer sa zone
        # en alpha=0 à chaque vrai repaint empêche de garder des fragments dans
        # le backing store après qu'un QLabel vidéo a changé de dimensions.
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(event.rect(), QColor(0, 0, 0, 0))
        painter.end()

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

    def cancel_current_media(self):
        """Arrête le média local parce que le serveur l'a déjà retiré/remplacé.

        Ne pas appeler /pop_meme : cet élément n'est plus celui de la file côté
        serveur, et un pop tardif pourrait sinon supprimer l'élément suivant.
        """
        if self.current_loaded_url is None and self.video_path is None:
            self.video_window.clear_frame()
            self.container.hide()
            return
        log("Arrêt local du média remplacé/retiré côté serveur")
        self.is_clearing = True
        self.close_timer.stop()
        try:
            self.media_player.stop()
        except Exception:
            pass
        self.hide_overlay_ui()
        self.is_clearing = False

    def finish_media_playback(self):
        if self.is_clearing or self.current_loaded_url is None:
            return
        self.is_clearing = True
        self.close_timer.stop()
        completed_url = self.current_loaded_url
        completed_key = self.current_loaded_item_key or completed_url
        self.last_done_url = completed_url
        self.hide_overlay_ui()
        # La file ne se débloque qu'en retirant cet item précis côté serveur.
        threading.Thread(target=self._send_pop_request, args=(completed_key,), daemon=True).start()

    def _send_pop_request(self, completed_key=None):
        try:
            self._post_pop_and_release(completed_key or self.last_done_url)
        except Exception as e:
            log(f"Erreur pop_meme : {e}")
            with self._network_state_lock:
                if completed_key and self._network_active_key == str(completed_key):
                    self._network_active_key = None

    def _cleanup_video_file(self):
        if self.video_path:
            try:
                self.media_player.stop()
                self.media_player.setMedia(QMediaContent()) 
                if os.path.exists(self.video_path):
                    os.remove(self.video_path)
            except Exception:
                pass
            self.video_path = None

    def hide_overlay_ui(self):
        # Aucun fondu sortant : masquer immédiatement. La fenêtre elle-même reste
        # toujours à opacité 1 pour éviter les problèmes de composition de Windows.
        for animation in self._fade_animations:
            animation.stop()
            animation.deleteLater()
        self._fade_animations = []
        self.header.setPaintOpacity(1.0)
        self.image_label.setPaintOpacity(1.0)
        # Le canvas vidéo personnalisé n'a pas de fondu sortant; on restaure
        # seulement l'opacité locale des widgets animés.
        self.close_timer.stop()
        self.media_player.stop()
        self._last_video_source_frame = QImage()
        self.image_label.clear()
        self.video_label.clear()
        self.video_label.hide()
        self.video_canvas.clear()
        self.video_canvas.hide()
        self.video_window.clear_frame()
        self.image_label.hide()
        self.header.clear_content()
        self.text_label.clear()
        self.container.hide()
        self._cleanup_video_file()
        self.current_author = ""
        self.current_avatar_source = None
        self._current_image_pixmap = None
        self.current_loaded_url = None
        self.current_loaded_item_key = None
        # is_clearing repassera à False quand le média suivant sera accepté.

    def cleanup(self):
        self.video_window.clear_frame()
        self._cleanup_video_file()



def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not acquire_single_instance():
        log("Une autre instance tourne déjà")
        QMessageBox.warning(None, "Live Chat déjà actif", "Le live chat est déjà actif sur le PC !")
        return 0

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
