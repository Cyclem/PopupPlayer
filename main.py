import sys
import os
import re
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QLabel,
                             QFileDialog, QSlider, QVBoxLayout, QFrame)
from PyQt6.QtCore import Qt, QUrl, QTimer, QRect, QPoint, QSettings
from PyQt6.QtGui import QPixmap, QPainterPath, QPainter, QColor, QFont, QFontMetrics
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def parse_subtitles(file_path):
    subtitles = []
    if not os.path.exists(file_path): return subtitles
    ext = os.path.splitext(file_path)[1].lower()
    content = None
    for enc in ['utf-8', 'gbk', 'utf-16', 'utf-8-sig']:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                content = f.read().replace('\r\n', '\n')
            break
        except: continue
    if not content: return subtitles
    try:
        if ext == ".srt":
            blocks = content.split('\n\n')
            for block in blocks:
                lines = [l for l in block.split('\n') if l.strip()]
                if len(lines) >= 3:
                    times = re.findall(r'(\d+):(\d+):(\d+),(\d+)', lines[1])
                    if len(times) == 2:
                        start_ms = int(times[0][0])*3600000 + int(times[0][1])*60000 + int(times[0][2])*1000 + int(times[0][3])
                        end_ms = int(times[1][0])*3600000 + int(times[1][1])*60000 + int(times[1][2])*1000 + int(times[1][3])
                        text = re.sub(r'<[^>]*>', '', "\n".join(lines[2:]))
                        subtitles.append({'start': start_ms, 'end': end_ms, 'text': text})
        elif ext == ".ass":
            for line in content.split('\n'):
                if line.startswith("Dialogue:"):
                    parts = line.split(',', 9)
                    if len(parts) >= 10:
                        start_t = re.findall(r'(\d+):(\d+):(\d+)\.(\d+)', parts[1])
                        end_t = re.findall(r'(\d+):(\d+):(\d+)\.(\d+)', parts[2])
                        if start_t and end_t:
                            s_ms = int(start_t[0][0])*3600000 + int(start_t[0][1])*60000 + int(start_t[0][2])*1000 + int(start_t[0][3])*10
                            e_ms = int(end_t[0][0])*3600000 + int(end_t[0][1])*60000 + int(end_t[0][2])*1000 + int(end_t[0][3])*10
                            text = re.sub(r'\{.*?\}', '', parts[9]).replace(r'\N', '\n')
                            subtitles.append({'start': s_ms, 'end': e_ms, 'text': text.strip()})
    except Exception as e:
        print(f"解析出错: {e}")
    return subtitles

class AdPopupPlayer(QWidget):
    def __init__(self, bg_image_path):
        super().__init__()
        app_dir = os.path.dirname(sys.executable) if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.abspath(__file__))
        self.settings = QSettings(os.path.join(app_dir, "config.ini"), QSettings.Format.IniFormat)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.CORNER_RADIUS = 20
        self.raw_pixmap = QPixmap(bg_image_path)
        self.scaled_bg = self.raw_pixmap
        self.aspect_ratio = self.raw_pixmap.height() / self.raw_pixmap.width()
        self.is_resizing_live = False  # 用于缩放优化

        saved_geo = self.settings.value("geometry")
        if saved_geo: self.restoreGeometry(saved_geo)
        else: self.resize(380, int(380 * self.aspect_ratio))

        self.VIDEO_Y = 0.311
        self.VIDEO_H = 0.391
        self.SUB_Y = 0.715

        self.subtitles = []
        self.current_subtitle = ""

        self.slider_dragging = False
        self.was_playing = False

        self.init_ui()
        if not self.settings.value("pos"): QTimer.singleShot(50, self.init_to_bottom_right)
        else: self.move(self.settings.value("pos"))

    def init_ui(self):
        self.start_game_hitbox = QPushButton(self)
        self.start_game_hitbox.setStyleSheet("background: transparent; border: none;")
        self.start_game_hitbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_game_hitbox.clicked.connect(self.open_file_dialog)

        self.reset_hitbox = QPushButton(self)
        self.reset_hitbox.setStyleSheet("background: transparent; border: none;")
        self.reset_hitbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_hitbox.clicked.connect(self.reset_to_select)

        self.player_container = QFrame(self)
        self.player_container.hide()
        layout = QVBoxLayout(self.player_container)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(0)
        self.video_widget = QVideoWidget()
        layout.addWidget(self.video_widget)

        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setStyleSheet("""
            QSlider { min-height: 8px; max-height: 8px; background: transparent; }
            QSlider::groove:horizontal { border: none; height: 2px; background: rgba(255,255,255,40); }
            QSlider::handle:horizontal { background: rgba(255,215,0,180); width: 12px; height: 12px; margin: -5px 0; border-radius:6px; }
        """)
        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderMoved.connect(self.on_slider_moved)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(lambda dur: self.slider.setRange(0, dur))

        self.sub_label = QLabel(self)
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_label.setStyleSheet("color: #FFFF00; font-weight:bold; background: transparent;")
        self.sub_label.setWordWrap(True)
        self.sub_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.sub_label.raise_()

        self.close_btn = QPushButton(self)
        self.close_btn.setStyleSheet("background: transparent; border: none;")
        self.close_btn.clicked.connect(self.save_and_exit)

        self.is_resizing = self.is_moving = False

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        v_rect = QRect(0, int(h * self.VIDEO_Y), w, int(h * self.VIDEO_H))
        self.player_container.setGeometry(v_rect)

        video_bottom = v_rect.bottom()
        self.slider.setGeometry(0, video_bottom - 4, w, 8)
        self.slider.raise_()

        self.sub_label.move((w - self.sub_label.width())//2, int(h * self.SUB_Y))
        self.sub_label.raise_()

        # 背景图原始尺寸
        BG_W = 1114
        BG_H = 1412

        # 开始游戏按钮实际区域
        BTN_X = 302
        BTN_Y = 1217
        BTN_W = 527
        BTN_H = 134

        self.start_game_hitbox.setGeometry(
            int(w * BTN_X / BG_W),
            int(h * BTN_Y / BG_H),
            int(w * BTN_W / BG_W),
            int(h * BTN_H / BG_H)
        )
        self.reset_hitbox.setGeometry(int(w * 0.85), int(h * 0.94), int(w * 0.15), int(h * 0.06))
        btn_sz = int(w * 0.12)
        self.close_btn.setGeometry(w - btn_sz, 0, btn_sz, btn_sz)

        mode = Qt.TransformationMode.FastTransformation if self.is_resizing_live else Qt.TransformationMode.SmoothTransformation
        self.scaled_bg = self.raw_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, mode)
        super().resizeEvent(event)

    def on_position_changed(self, position):
        if not self.slider_dragging:
            self.slider.setValue(position)
        matched_texts = [sub['text'] for sub in self.subtitles if sub['start'] <= position <= sub['end']]
        if matched_texts:
            full_text = "\n".join(matched_texts)
            font_size = max(10, int(self.width()/24))
            font = QFont("Microsoft YaHei", font_size)
            font.setBold(True)

            metrics = QFontMetrics(font)
            lines = full_text.split("\n")
            max_width = max([metrics.horizontalAdvance(line) for line in lines])

            while max_width > self.width() - 20 and font_size > 8:
                font_size -= 1
                font = QFont("Microsoft YaHei", font_size)
                font.setBold(True)
                metrics = QFontMetrics(font)
                max_width = max([metrics.horizontalAdvance(line) for line in lines])

            self.sub_label.setFont(font)
            self.sub_label.setText(full_text)
            self.sub_label.adjustSize()
            self.sub_label.move((self.width() - self.sub_label.width())//2, int(self.height()*self.SUB_Y))
            self.sub_label.show()
            self.sub_label.raise_()
        else:
            self.sub_label.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            if (
                    self.player_container.isVisible()
                    and self.player_container.geometry().contains(pos)
                    and not self.start_game_hitbox.geometry().contains(pos)
            ):
                return
            if QRect(0,0,45,45).contains(pos):
                self.is_resizing = True
                self.is_resizing_live = True
                self.anchor_br = self.geometry().bottomRight()
            else:
                self.is_moving = True
                self.drag_start_pos = event.globalPosition().toPoint()
                self.start_geo = self.geometry()

    def mouseReleaseEvent(self, event):
        if self.is_resizing_live:
            # 高质量渲染
            self.scaled_bg = self.raw_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.update()
        self.is_resizing = self.is_moving = self.is_resizing_live = False

    def mouseMoveEvent(self, event):
        if not event.buttons():
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if QRect(0,0,45,45).contains(event.pos()) else Qt.CursorShape.ArrowCursor)
            return
        if self.is_resizing:
            curr_pos = event.globalPosition().toPoint()
            new_w = max(280, self.anchor_br.x() - curr_pos.x())
            new_h = int(new_w * self.aspect_ratio)
            self.setGeometry(self.anchor_br.x() - new_w, self.anchor_br.y() - new_h, new_w, new_h)
        elif self.is_moving:
            delta = event.globalPosition().toPoint() - self.drag_start_pos
            self.move(self.start_geo.topLeft() + delta)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0,0,self.width(),self.height(),self.CORNER_RADIUS,self.CORNER_RADIUS)
        p.setClipPath(path)
        p.drawPixmap(self.rect(), self.scaled_bg)

    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video (*.mp4 *.mkv *.avi *.flv)")
        if file_path:
            base_path = os.path.splitext(file_path)[0]
            if os.path.exists(base_path + ".srt"):
                self.subtitles = parse_subtitles(base_path + ".srt")
            elif os.path.exists(base_path + ".ass"):
                self.subtitles = parse_subtitles(base_path + ".ass")
            else:
                self.subtitles = []

            self.player.setSource(QUrl.fromLocalFile(os.path.abspath(file_path)))
            self.player_container.show()
            self.player.play()

    def reset_to_select(self):
        if self.player_container.isVisible():
            self.player.stop()
            self.player_container.hide()
            self.sub_label.hide()

    def save_and_exit(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("pos", self.pos())
        self.player.stop()
        QApplication.quit()
        sys.exit(0)

    def init_to_bottom_right(self):
        s = QApplication.primaryScreen().availableGeometry()
        self.move(s.x() + s.width() - self.width() - 15, s.y() + s.height() - self.height() - 15)

    def on_slider_pressed(self):
        self.slider_dragging = True

        self.was_playing = (
                self.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
        )

        self.player.pause()

        self.was_playing = (
                self.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
        )

    def on_slider_moved(self, pos):
        self.player.setPosition(pos)

    def on_slider_released(self):
        self.player.setPosition(self.slider.value())

        if self.was_playing:
            self.player.play()

        self.slider_dragging = False

if __name__ == '__main__':
    if sys.platform.startswith('linux'):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    app = QApplication(sys.argv)
    bg = get_resource_path("ad_bg.png")
    player = AdPopupPlayer(bg)
    player.show()
    sys.exit(app.exec())