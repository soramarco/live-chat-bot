import sys
import time
import math
import requests
from io import BytesIO
import tempfile
import os
import threading
import cv2
import json

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QImage, QPixmap, QFont, QColor, QPainter, QBrush, QPainterPath
from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout, QHBoxLayout, QGraphicsDropShadowEffect, QLineEdit, QPushButton, QGraphicsOpacityEffect

CONFIG_FILE = os.path.join(os.path.expanduser("~"), "overlay_config_discord.txt")
SERVER_URL = "https://live-chat-bot-ctus.onrender.com/get_next_meme"
POP_URL = "https://live-chat-bot-ctus.onrender.com/pop_meme"

class SetupDialog(QWidget):
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
            except:
                pass

        label = QLabel("Entre ton pseudo Discord exact :", self)
        label.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(label)

        self.input_field = QLineEdit(self)
        self.input_field.setText(saved_pseudo)
        self.input_field.setStyleSheet("padding: 8px; font-size: 14px; background: #1e1f22; border: 1px solid #383a40; border-radius: 4px; color: white;")
        layout.addWidget(self.input_field)

        btn = QPushButton("Lancer l'Overlay", self)
        btn.setStyleSheet("margin-top: 10px; padding: 8px; font-size: 14px; background: #5865F2; color: white; border: none; border-radius: 4px; font-weight: bold;")
        btn.clicked.connect(self.validate)
        layout.addWidget(btn)

    def validate(self):
        text = self.input_field.text().strip()
        if text:
            self.pseudo = text
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write(text)
            except:
                pass
            self.hide()

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

class WorkerSignals(QObject):
    update_media = pyqtSignal(dict)
    video_frame_ready = pyqtSignal(QImage)
    force_close = pyqtSignal()

class OverlayWindow(QWidget):
    def __init__(self, discord_pseudo):
        super().__init__()
        self.discord_pseudo = discord_pseudo
        self.video_file = None
        self.video_capture = None
        self.fps = 30
        self.video_timer = QTimer(self)
        self.is_clearing = False
        self.current_loaded_url = None
        self.is_transitioning = False
        self.current_max_size = 850
        
        self.float_angle = 0.0
        self.float_timer = QTimer(self)
        self.float_timer.timeout.connect(self.animate_floating_header)
        self.float_timer.start(30)
        
        self.init_ui()
        self.init_network()

    def init_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SubWindow)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.resize(1920, 1080)
        self.move(0, 0)

        try:
            import ctypes
            hwnd = int(self.winId())
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style | 0x80000 | 0x20 | 0x8)
        except Exception as e:
            print(f"Erreur style Windows layer : {e}")

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(40, 0, 40, 0)
        
        self.container = QWidget(self)
        container_layout = QVBoxLayout(self.container)
        container_layout.setAlignment(Qt.AlignCenter)
        container_layout.setSpacing(12)
        
        self.opacity_effect = QGraphicsOpacityEffect(self.container)
        self.container.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(1.0)

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
        
        self.media_display_label = QLabel(self.container)
        self.media_display_label.setAlignment(Qt.AlignCenter)
        container_layout.addWidget(self.media_display_label)

        self.signals = WorkerSignals()
        self.signals.video_frame_ready.connect(self.display_frame)
        self.signals.force_close.connect(QApplication.quit)
        
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

        self.video_timer = QTimer(self)
        self.video_timer.timeout.connect(self.update_video_frame)

    def animate_floating_header(self):
        if self.container.isVisible():
            self.float_angle += 0.08
            offset_y = int(math.sin(self.float_angle) * 3)
            self.header_widget.setContentsMargins(0, offset_y, 0, -offset_y)

    def update_alignment(self, position):
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            if item.widget() and item.widget() != self.container:
                item.widget().deleteLater()
            
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

    def init_network(self):
        print("[NETWORK] Initialisation du thread réseau...")
        self.signals.update_media.connect(self.handle_new_media)
        t = threading.Thread(target=self.network_worker, daemon=True)
        t.start()

    def network_worker(self):
        print(f"[NETWORK] Thread démarré pour l'utilisateur: {self.discord_pseudo}")
        while True:
            try:
                res = requests.get(f"{SERVER_URL}?user={self.discord_pseudo}", timeout=3)
                if res.status_code == 200:
                    data = res.json()
                    
                    if data.get("status") == "inactive":
                        print("[NETWORK] Statut inactif reçu, fermeture.")
                        self.signals.force_close.emit()
                        break

                    new_url = data.get("url")
                    if not new_url:
                        if self.current_loaded_url is not None:
                            self.current_loaded_url = None
                            QTimer.singleShot(0, self.hide_overlay_ui)
                        time.sleep(1.5)
                        continue

                    elif new_url != self.current_loaded_url and not self.is_transitioning:
                        self.is_transitioning = True
                        self.current_loaded_url = new_url
                        print(f"[NETWORK] Nouveau média détecté : {new_url}")
                        
                        media_res = requests.get(new_url, timeout=15)
                        if media_res.status_code == 200:
                            data["content_bytes"] = media_res.content
                            avatar_url = data.get("avatar", "")
                            if avatar_url:
                                try:
                                    ar = requests.get(avatar_url, timeout=3)
                                    if ar.status_code == 200:
                                        data["avatar_bytes"] = ar.content
                                except:
                                    pass
                            self.signals.update_media.emit(data)
                        else:
                            self.is_transitioning = False
                    else:
                        time.sleep(1.5)
                else:
                    time.sleep(1.5)
            except Exception as e:
                print(f"[NETWORK Erreur] {e}")
                time.sleep(2)

    def handle_new_media(self, data):
        try:
            print("[UI] Affichage du média...")
            self.video_timer.stop()
            if self.video_capture:
                self.video_capture.release()
                self.video_capture = None
            
            position = data.get("position", "center")
            is_game_mode = data.get("game_mode", False)
            
            if is_game_mode:
                self.current_max_size = 380
                self.author_label.setFont(QFont("Segoe UI", 13, QFont.Bold))
                self.text_label.setFont(QFont("Segoe UI", 15, QFont.Bold))
                self.text_label.setMaximumWidth(380)
                avatar_size = 26
            else:
                self.current_max_size = 850
                self.author_label.setFont(QFont("Segoe UI", 20, QFont.Bold))
                self.text_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
                self.text_label.setMaximumWidth(850)
                avatar_size = 40

            QTimer.singleShot(0, lambda: self.update_alignment(position))

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
            is_vid = any(ext in url.lower() for ext in [".mp4", ".mov", ".webm", ".avi"])

            if is_vid:
                if self.video_file and os.path.exists(self.video_file.name):
                    try:
                        self.video_file.close()
                        os.remove(self.video_file.name)
                    except:
                        pass

                suffix = ".mp4"
                for ext in [".webm", ".mov", ".avi", ".mp4"]:
                    if ext in url.lower():
                        suffix = ext
                        break

                self.video_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                self.video_file.write(content)
                self.video_file.flush()
                self.video_file.close()
                
                self.video_capture = cv2.VideoCapture(self.video_file.name)
                file_fps = self.video_capture.get(cv2.CAP_PROP_FPS)
                self.fps = file_fps if (file_fps and 5 < file_fps < 120) else 30
                
                self.container.show()
                self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
                self.fade_animation.setDuration(350)
                self.fade_animation.setStartValue(0.0)
                self.fade_animation.setEndValue(1.0)
                self.fade_animation.start()

                self.video_timer.start(int(1000 / self.fps))
                self.close_timer.stop()
            else:
                image = QImage.fromData(content)
                pixmap = QPixmap.fromImage(image)
                if pixmap.width() > self.current_max_size or pixmap.height() > self.current_max_size:
                    pixmap = pixmap.scaled(self.current_max_size, self.current_max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                
                self.media_display_label.setPixmap(pixmap)
                self.container.show()
                self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
                self.fade_animation.setDuration(350)
                self.fade_animation.setStartValue(0.0)
                self.fade_animation.setEndValue(1.0)
                self.fade_animation.start()

                self.close_timer.start(7000)
        except Exception as e:
            print(f"[UI Erreur] {e}")
            self.hide_overlay_ui()
        finally:
            self.is_transitioning = False

    def update_video_frame(self):
        if not (self.video_capture and self.video_capture.isOpened()):
            return
        ret, frame = self.video_capture.read()
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = frame.shape
            self.signals.video_frame_ready.emit(QImage(frame.data, w, h, ch * w, QImage.Format_RGB888))
        else:
            self.video_timer.stop()
            self.finish_media_playback()

    def display_frame(self, image):
        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > self.current_max_size or pixmap.height() > self.current_max_size:
            pixmap = pixmap.scaled(self.current_max_size, self.current_max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.media_display_label.setPixmap(pixmap)

    def finish_media_playback(self):
        if self.is_clearing:
            return
        self.is_clearing = True
        threading.Thread(target=self._send_pop_request, daemon=True).start()
        self.hide_overlay_ui()

    def _send_pop_request(self):
        try:
            requests.post(f"{POP_URL}?user={self.discord_pseudo}", timeout=3)
        except:
            pass

    def hide_overlay_ui(self):
        self.video_timer.stop()
        if self.video_capture:
            self.video_capture.release()
            self.video_capture = None
        self.media_display_label.clear()
        self.author_label.clear()
        self.avatar_label.clear()
        self.text_label.clear()
        self.container.hide()
        
        if self.video_file and os.path.exists(self.video_file.name):
            try:
                self.video_file.close()
                os.remove(self.video_file.name)
            except:
                pass
            self.video_file = None

        self.current_loaded_url = None
        QTimer.singleShot(500, lambda: setattr(self, 'is_transitioning', False))
        self.is_clearing = False

def main():
    app = QApplication(sys.argv)
    setup = SetupDialog()
    setup.show()
    app.exec_()
    
    if not setup.pseudo:
        sys.exit(0)

    overlay = OverlayWindow(setup.pseudo)
    overlay.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
