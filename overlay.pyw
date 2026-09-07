import hashlib
import sys
import time
import requests
from io import BytesIO
import tempfile
import os
import threading
import subprocess
import cv2

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QUrl
from PyQt5.QtGui import QImage, QPixmap, QFont, QColor, QPainter, QBrush, QPainterPath
from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout, QHBoxLayout, QGraphicsDropShadowEffect, QLineEdit, QPushButton, QMessageBox
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

GITHUB_RAW_URL = "https://raw.githubusercontent.com/soramarco/live-chat-bot/main/overlay.pyw"

def check_for_updates():
    try:
        response = requests.get(GITHUB_RAW_URL, timeout=3)
        if response.status_code == 200:
            remote_code = response.content
            current_file_path = os.path.abspath(__file__)
            
            with open(current_file_path, "rb") as f:
                local_code = f.read()
                
            if hashlib.md5(remote_code).digest() != hashlib.md5(local_code).digest():
                print("[INFO] Une mise à jour est disponible. Téléchargement...")
                temp_update_path = "overlay_new.pyw"
                with open(temp_update_path, "wb") as f:
                    f.write(remote_code)
                
                batch_content = """
@echo off
timeout /t 2 /nobreak > nul
move /y overlay_new.pyw overlay.pyw
start pythonw.exe overlay.pyw
del "%~f0"
"""
                with open("update.bat", "w") as b:
                    b.write(batch_content)
                
                print("[INFO] Mise à jour prête. Redémarrage...")
                subprocess.Popen("update.bat", shell=True)
                sys.exit(0)
    except Exception as e:
        print(f"[AVERTISSEMENT] Impossible de vérifier les mises à jour : {e}")

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
            self.close()

def create_discord_avatar_with_ring(pixmap):
    size = 40
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
        self.media_player = None
        self.video_file = None
        self.video_capture = None
        self.fps = 30
        self.video_timer = QTimer(self)
        self.is_clearing = False
        self.media_in_progress = False
        self.current_loaded_url = None
        self.is_transitioning = False
        
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

        self.media_player = QMediaPlayer(None)
        self.media_player.stateChanged.connect(self.handle_media_state_changed)
        
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
        self.signals.update_media.connect(self.handle_new_media)
        t = threading.Thread(target=self.network_worker, daemon=True)
        t.start()

    def network_worker(self):
        while True:
            try:
                res = requests.get(f"{SERVER_URL}?user={self.discord_pseudo}", timeout=3)
                if res.status_code == 200:
                    data = res.json()
                    
                    if data.get("status") == "inactive":
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
                        try:
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
                                
                                self.media_in_progress = True
                                self.signals.update_media.emit(data)
                            else:
                                self.is_transitioning = False
                        except Exception as e:
                            print(f"Erreur téléchargement média : {e}")
                            self.is_transitioning = False
                    else:
                        time.sleep(1.5)
                else:
                    time.sleep(1.5)
            except Exception as e:
                print(f"Erreur requête serveur : {e}")
                time.sleep(2)

    def handle_new_media(self, data):
        try:
            self.media_player.stop()
            self.video_timer.stop()
            if self.video_capture:
                self.video_capture.release()
                self.video_capture = None
            
            position = data.get("position", "center")
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

            self.author_label.setText(f"{author}")

            avatar_bytes = data.get("avatar_bytes")
            if avatar_bytes:
                img = QImage.fromData(avatar_bytes)
                pix = QPixmap.fromImage(img)
                ring_pixmap = create_discord_avatar_with_ring(pix)
                self.avatar_label.setPixmap(ring_pixmap)
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
                if file_fps > 5 and file_fps < 120:
                    self.fps = file_fps
                else:
                    self.fps = 30
                
                self.container.show()
                self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(self.video_file.name)))
                self.media_player.setVolume(100)
                self.media_player.play()
                
                self.video_timer.start(int(1000 / self.fps))
                self.close_timer.stop()
            else:
                image = QImage.fromData(content)
                pixmap = QPixmap.fromImage(image)
                
                max_size = 850
                if pixmap.width() > max_size or pixmap.height() > max_size:
                    pixmap = pixmap.scaled(max_size, max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    
                self.media_display_label.setPixmap(pixmap)
                self.container.show()
                self.close_timer.start(7000)
        except Exception as e:
            print(f"Erreur handle_new_media : {e}")
            self.hide_overlay_ui()
        finally:
            self.is_transitioning = False

    def update_video_frame(self):
        if self.video_capture and self.video_capture.isOpened():
            audio_pos_sec = self.media_player.position() / 1000.0
            current_frame_pos = self.video_capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            
            if abs(audio_pos_sec - current_frame_pos) > 0.15:
                target_frame = int(audio_pos_sec * self.fps)
                self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

            ret, frame = self.video_capture.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                self.signals.video_frame_ready.emit(qt_image)
            else:
                self.video_timer.stop()

    def display_frame(self, image):
        pixmap = QPixmap.fromImage(image)
        max_size = 850
        if pixmap.width() > max_size or pixmap.height() > max_size:
            pixmap = pixmap.scaled(max_size, max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.media_display_label.setPixmap(pixmap)

    def handle_media_state_changed(self, state):
        if state == QMediaPlayer.StoppedState and not self.is_clearing and not self.is_transitioning:
            QTimer.singleShot(100, self.finish_media_playback)

    def finish_media_playback(self):
        if self.is_clearing:
            return
        
        self.is_clearing = True
        threading.Thread(target=self._send_pop_request, daemon=True).start()
        self.hide_overlay_ui()

    def _send_pop_request(self):
        try:
            requests.post(POP_URL, timeout=3)
        except Exception as e:
            print(f"Erreur pop_meme : {e}")

    def hide_overlay_ui(self):
        self.media_player.stop()
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

        self.media_in_progress = False
        self.current_loaded_url = None
        QTimer.singleShot(500, lambda: setattr(self, 'is_transitioning', False))
        self.is_clearing = False

def main():
    check_for_updates()

    app = QApplication(sys.argv)
    
    setup = SetupDialog()
    setup.show()
    app.exec_()
    
    if not setup.pseudo:
        sys.exit(0)

    safe_pseudo_filename = "".join(c for c in setup.pseudo if c.isalnum() or c in ('_', '-'))
    lock_file_path = os.path.join(tempfile.gettempdir(), f"overlay_discord_{safe_pseudo_filename}.lock")
    
    if os.path.exists(lock_file_path):
        try:
            with open(lock_file_path, "r") as f:
                old_pid = int(f.read().strip())
            
            try:
                import psutil
                process_exists = psutil.pid_exists(old_pid)
            except ImportError:
                process_exists = True 

            if process_exists:
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Warning)
                msg.setText(f"L'overlay pour '{setup.pseudo}' est déjà lancé sur ce PC !")
                msg.setWindowTitle("Déjà en cours d'exécution")
                msg.exec_()
                sys.exit(0)
            else:
                os.remove(lock_file_path)
        except:
            try:
                os.remove(lock_file_path)
            except:
                pass

    try:
        with open(lock_file_path, "w") as f:
            f.write(str(os.getpid()))
    except:
        pass

    try:
        overlay = OverlayWindow(setup.pseudo)
        overlay.show()
        exit_code = app.exec_()
    finally:
        if os.path.exists(lock_file_path):
            try:
                os.remove(lock_file_path)
            except:
                pass
        sys.exit(exit_code)

if __name__ == "__main__":
    main()
