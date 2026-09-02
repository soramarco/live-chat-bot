import sys
import io
import os
import tempfile
import requests
import vlc
from PIL import Image as PILImage
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QRect
from PyQt5.QtGui import QColor, QPixmap, QPainter, QPainterPath, QPen, QFont

SERVER_URL = "http://127.0.0.1:5000"

def get_circular_avatar(image_bytes=None, size=46, fallback_letter="?"):
    out_pixmap = QPixmap(size, size)
    out_pixmap.fill(Qt.transparent)

    pixmap = QPixmap()
    if image_bytes:
        pixmap.loadFromData(image_bytes)

    painter = QPainter(out_pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)

    if not pixmap.isNull():
        scaled = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (size - scaled.width()) // 2
        y = (size - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
    else:
        painter.fillRect(0, 0, size, size, QColor("#5865F2"))
        painter.setPen(QColor("#FFFFFF"))
        font = QFont("Arial", 16, QFont.Bold)
        painter.setFont(font)
        painter.drawText(0, 0, size, size, Qt.AlignCenter, fallback_letter.upper())

    painter.setClipping(False)

    pen = QPen(QColor("#23a55a"))
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(1, 1, size - 2, size - 2)

    painter.end()
    return out_pixmap

class MemeOverlay(QWidget):
    def __init__(self):
        super().__init__()
        
        self.setWindowFlags(
            Qt.FramelessWindowHint | 
            Qt.WindowStaysOnTopHint | 
            Qt.SubWindow |
            Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(12)
        self.main_layout.setAlignment(Qt.AlignCenter)
        self.setLayout(self.main_layout)

        # Bloc Auteur
        self.author_widget = QWidget(self)
        self.author_layout = QHBoxLayout(self.author_widget)
        self.author_layout.setContentsMargins(0, 0, 0, 0)
        self.author_layout.setSpacing(12)
        self.author_layout.setAlignment(Qt.AlignCenter)

        self.avatar_label = QLabel(self.author_widget)
        self.avatar_label.setFixedSize(46, 46)
        
        self.author_name_label = QLabel(self.author_widget)
        self.author_name_label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-family: 'Arial', sans-serif;
                font-weight: 800;
                font-size: 22px;
                background: transparent;
            }
        """)
        shadow_author = QGraphicsDropShadowEffect()
        shadow_author.setBlurRadius(8)
        shadow_author.setColor(QColor(0, 0, 0, 255))
        shadow_author.setOffset(2, 2)
        self.author_name_label.setGraphicsEffect(shadow_author)

        self.author_layout.addWidget(self.avatar_label)
        self.author_layout.addWidget(self.author_name_label)
        self.main_layout.addWidget(self.author_widget, alignment=Qt.AlignCenter)
        self.author_widget.hide()

        # Conteneur Vidéo
        self.video_frame = QWidget(self)
        self.video_frame.setStyleSheet("background: transparent;")
        self.main_layout.addWidget(self.video_frame, alignment=Qt.AlignCenter)
        self.video_frame.hide()

        self.vlc_instance = vlc.Instance("--no-xlib")
        self.vlc_player = self.vlc_instance.media_player_new()
        
        self.vlc_event_manager = self.vlc_player.event_manager()
        self.vlc_event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self.handle_vlc_end)
        
        # Conteneur Image
        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background: transparent;")
        self.main_layout.addWidget(self.image_label, alignment=Qt.AlignCenter)
        self.image_label.hide()

        # Légende
        self.caption_label = QLabel(self)
        self.caption_label.setAlignment(Qt.AlignCenter)
        self.caption_label.setWordWrap(True)
        self.caption_label.setStyleSheet("""
            QLabel {
                color: white;
                font-family: 'Impact', 'Arial Black', sans-serif;
                font-size: 32px;
                background-color: transparent;
                padding: 6px;
            }
        """)
        
        shadow_caption = QGraphicsDropShadowEffect()
        shadow_caption.setBlurRadius(10)
        shadow_caption.setColor(QColor(0, 0, 0, 255))
        shadow_caption.setOffset(2, 2)
        self.caption_label.setGraphicsEffect(shadow_caption)
        
        self.main_layout.addWidget(self.caption_label, alignment=Qt.AlignCenter)
        self.caption_label.hide()

        self.fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self.fade_anim.setDuration(250)

        # Vérification très rapide (300ms) pour intercepter le signal "stop/skip" instantanément
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_for_meme)
        self.timer.start(300)

        self.check_video_timer = QTimer(self)
        self.check_video_timer.timeout.connect(self.update_video_aspect)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide_all)

        self.is_playing = False
        self.video_resized = False
        self.current_temp_file = None

    def check_for_meme(self):
        try:
            res = requests.get(f"{SERVER_URL}/get_meme", timeout=1)
            if res.status_code == 200:
                data = res.json()
                cmd = data.get("command")
                
                if cmd == "stop":
                    self.hide_all()
                elif cmd == "play" and not self.is_playing:
                    meme_data = data.get("data", {})
                    url = meme_data.get("url")
                    caption = meme_data.get("caption", "")
                    author_name = meme_data.get("author_name", "")
                    author_avatar = meme_data.get("author_avatar", "")
                    if url:
                        self.play_meme(url, caption, author_name, author_avatar)
        except Exception:
            pass

    def play_meme(self, url, caption, author_name, author_avatar):
        self.is_playing = True
        self.video_resized = False
        self.hide_all_silent_ui()

        screen = QApplication.primaryScreen().geometry()
        max_height = int(screen.height() * 0.70)
        max_width = int(screen.width() * 0.70)

        if author_name:
            self.author_name_label.setText(author_name)
            avatar_bytes = None
            if author_avatar:
                try:
                    headers = {'User-Agent': 'Mozilla/5.0'}
                    resp = requests.get(author_avatar, headers=headers, timeout=3)
                    if resp.status_code == 200:
                        avatar_bytes = resp.content
                except Exception:
                    pass

            first_letter = author_name[0] if author_name else "?"
            pix = get_circular_avatar(avatar_bytes, 46, fallback_letter=first_letter)
            self.avatar_label.setPixmap(pix)
            self.avatar_label.show()
            self.author_widget.show()

        if caption:
            self.caption_label.setText(caption)
            self.caption_label.setMaximumWidth(max_width)
            self.caption_label.show()

        is_video = any(ext in url.lower() for ext in ['.mp4', '.webm', '.mov', 'video'])

        if is_video:
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                vid_data = requests.get(url, headers=headers, timeout=10).content
                
                tf = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
                tf.write(vid_data)
                tf.close()
                self.current_temp_file = tf.name

                init_w = int(max_height * 0.8)
                init_h = max_height
                self.video_frame.setFixedSize(init_w, init_h)
                self.video_frame.show()
                
                target_w = max_width
                target_h = max_height + 150
                self.center_and_show(target_w, target_h, screen)

                media = self.vlc_instance.media_new(self.current_temp_file)
                self.vlc_player.set_media(media)
                if sys.platform.startswith('win'):
                    self.vlc_player.set_hwnd(self.video_frame.winId())
                self.vlc_player.play()

                self.check_video_timer.start(100)
            except Exception:
                self.hide_all()
        else:
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                img_data = requests.get(url, headers=headers, timeout=5).content
                
                pixmap = QPixmap()
                if pixmap.loadFromData(img_data):
                    scaled_pixmap = pixmap.scaled(max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.image_label.setPixmap(scaled_pixmap)
                    self.image_label.show()

                    target_w = scaled_pixmap.width() + 40
                    target_h = scaled_pixmap.height() + 140
                    self.center_and_show(target_w, target_h, screen)

                    self.hide_timer.start(7000)
                else:
                    self.hide_all()
            except Exception:
                self.hide_all()

    def center_and_show(self, target_w, target_h, screen):
        x = int((screen.width() - target_w) / 2)
        y = int((screen.height() - target_h) / 2)
        
        self.setGeometry(x, y, target_w, target_h)
        self.setWindowOpacity(0.0)
        self.show()

        self.fade_anim.stop()
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.start()

    def update_video_aspect(self):
        if not self.video_resized:
            size = self.vlc_player.video_get_size(0)
            if size and size[0] > 0 and size[1] > 0:
                v_width, v_height = size[0], size[1]
                screen = QApplication.primaryScreen().geometry()
                
                if v_width >= v_height:
                    target_width = int(screen.width() * 0.65)
                    target_height = int(target_width * (v_height / v_width))
                else:
                    target_height = int(screen.height() * 0.65)
                    target_width = int(target_height * (v_width / v_height))

                self.video_frame.setFixedSize(target_width, target_height)
                self.caption_label.setMaximumWidth(target_width)
                
                final_w = max(target_width + 40, self.caption_label.sizeHint().width() + 40)
                final_h = target_height + 150
                
                final_x = int((screen.width() - final_w) / 2)
                final_y = int((screen.height() - final_h) / 2)
                
                self.setGeometry(final_x, final_y, final_w, final_h)
                self.video_resized = True
                self.check_video_timer.stop()

    def handle_vlc_end(self, event):
        QTimer.singleShot(0, self.hide_all)

    def hide_all_silent_ui(self):
        self.check_video_timer.stop()
        self.hide_timer.stop()
        self.vlc_player.stop()

    def hide_all(self):
        self.hide_all_silent_ui()
        self.author_widget.hide()
        self.video_frame.hide()
        self.image_label.hide()
        self.caption_label.hide()
        self.hide()
        
        try:
            requests.post(f"{SERVER_URL}/media_ended", timeout=1)
        except Exception:
            pass

        if self.current_temp_file and os.path.exists(self.current_temp_file):
            try:
                os.remove(self.current_temp_file)
            except Exception:
                pass
            self.current_temp_file = None
            
        self.is_playing = False

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = MemeOverlay()
    sys.exit(app.exec_())