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
                        s_ms = int(times[0][0])*3600000 + int(times[0][1])*60000 + int(times[0][2])*1000 + int(times[0][3])
                        e_ms = int(times[1][0])*3600000 + int(times[1][1])*60000 + int(times[1][2])*1000 + int(times[1][3])
                        text = re.sub(r'<[^>]*>', '', "\n".join(lines[2:]))
                        subtitles.append({'start': s_ms, 'end': e_ms, 'text': text})
        elif ext == ".ass":
            for line in content.split('\n'):
                if line.startswith("Dialogue:"):
                    parts = line.split(',', 9)
                    if len(parts) >= 10:
                        st = re.findall(r'(\d+):(\d+):(\d+)\.(\d+)', parts[1])
                        et = re.findall(r'(\d+):(\d+):(\d+)\.(\d+)', parts[2])
                        if st and et:
                            s_ms = int(st[0][0])*3600000 + int(st[0][1])*60000 + int(st[0][2])*1000 + int(st[0][3])*10
                            e_ms = int(et[0][0])*3600000 + int(et[0][1])*60000 + int(et[0][2])*1000 + int(et[0][3])*10
                            text = re.sub(r'\{.*?\}', '', parts[9]).replace(r'\N', '\n')
                            subtitles.append({'start': s_ms, 'end': e_ms, 'text': text.strip()})
    except: pass
    return subtitles

class AdPopupPlayer(QWidget):
    def __init__(self, bg_image_path):
        super().__init__()
        self.mpv_player = None 
        app_dir = os.path.dirname(sys.executable) if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.abspath(__file__))
        self.settings = QSettings(os.path.join(app_dir, "config.ini"), QSettings.Format.IniFormat)
        
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.CORNER_RADIUS = 20
        self.raw_pixmap = QPixmap(bg_image_path)
        self.scaled_bg = self.raw_pixmap
        self.aspect_ratio = self.raw_pixmap.height() / self.raw_pixmap.width()
        self.is_resizing_live = False  

        saved_geo = self.settings.value("geometry")
        if saved_geo: self.restoreGeometry(saved_geo)
        else: self.resize(380, int(380 * self.aspect_ratio))

        self.VIDEO_Y = 0.311
        self.VIDEO_H = 0.391
        self.SUB_Y = 0.715

        self.subtitles = []
        self.slider_dragging = False
        self.was_playing = False

        self.init_ui()
        if not self.settings.value("pos"): QTimer.singleShot(50, self.init_to_bottom_right)
        else: self.move(self.settings.value("pos"))

    def init_ui(self):
        # 1. 载入区
        self.start_game_hitbox = QPushButton(self)
        self.start_game_hitbox.setStyleSheet("background: transparent; border: none;")
        self.start_game_hitbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_game_hitbox.clicked.connect(self.open_file_dialog)

        # 2. 重置区
        self.reset_hitbox = QPushButton(self)
        self.reset_hitbox.setStyleSheet("background: transparent; border: none;")
        self.reset_hitbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_hitbox.clicked.connect(self.reset_to_select)

        # 3. 播放容器
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

        # 4. 透明暂停/播放层 (覆盖视频区)
        self.play_pause_hitbox = QPushButton(self.player_container)
        self.play_pause_hitbox.setStyleSheet("background: transparent; border: none;")
        self.play_pause_hitbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_pause_hitbox.clicked.connect(self.toggle_playback)

        # 5. 进度条 (大面积点击热区优化)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.hide()
        self.slider.setStyleSheet("""
            QSlider { height: 30px; background: transparent; }
            QSlider::groove:horizontal { border: none; height: 2px; background: rgba(255,255,255,40); }
            QSlider::handle:horizontal { 
                background: rgba(255, 215, 0, 180); 
                width: 10px; height: 10px; 
                margin: -4px 0; border-radius: 5px; 
            }
            QSlider::handle:horizontal:hover { 
                background: gold; width: 14px; height: 14px; 
                margin: -6px 0; border-radius: 7px; 
            }
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

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        v_rect = QRect(0, int(h * self.VIDEO_Y), w, int(h * self.VIDEO_H))
        self.player_container.setGeometry(v_rect)
        self.play_pause_hitbox.setGeometry(0, 0, v_rect.width(), v_rect.height())

        # 进度条热区高度调大到30，位置稍微向上偏移覆盖视频边缘
        self.slider.setGeometry(0, v_rect.bottom() - 15, w, 30)
        self.slider.raise_()

        self.sub_label.move((w - self.sub_label.width())//2, int(h * self.SUB_Y))
        self.sub_label.raise_()

        BG_W, BG_H = 1114, 1412
        BTN_X, BTN_Y, BTN_W, BTN_H = 302, 1217, 527, 134
        self.start_game_hitbox.setGeometry(
            int(w * BTN_X / BG_W), int(h * BTN_Y / BG_H),
            int(w * BTN_W / BG_W), int(h * BTN_H / BG_H)
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
            f_size = max(10, int(self.width()/24))
            font = QFont("Microsoft YaHei", f_size); font.setBold(True)
            metrics = QFontMetrics(font)
            while max([metrics.horizontalAdvance(l) for l in full_text.split("\n")]) > self.width()-20 and f_size > 8:
                f_size -= 1; font = QFont("Microsoft YaHei", f_size); font.setBold(True); metrics = QFontMetrics(font)
            self.sub_label.setFont(font); self.sub_label.setText(full_text); self.sub_label.adjustSize()
            self.sub_label.move((self.width()-self.sub_label.width())//2, int(self.height()*self.SUB_Y))
            self.sub_label.show(); self.sub_label.raise_()
        else:
            self.sub_label.hide()

    def on_slider_pressed(self):
        self.slider_dragging = True
        # 记录拖动前的播放状态
        self.was_playing = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        # 拖动时静音预览
        self.audio.setMuted(True)

    def on_slider_moved(self, pos):
        # 【核心修改】拖动时实时更新视频画面位置
        self.player.setPosition(pos)

    def on_slider_released(self):
        # 跳转到最终位置
        self.player.setPosition(self.slider.value())
        # 取消静音
        self.audio.setMuted(False)
        # 恢复之前的播放状态
        if self.was_playing:
            self.player.play()
        self.slider_dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            # 如果点在进度条热区，优先处理进度条，拦截拖动窗口
            if self.slider.isVisible() and self.slider.geometry().contains(pos):
                return
            # 如果点在视频区，不拖动窗口（因为那里有暂停功能）
            if self.player_container.isVisible() and self.player_container.geometry().contains(pos):
                return
            
            if QRect(0,0,45,45).contains(pos):
                self.is_resizing = self.is_resizing_live = True
                self.anchor_br = self.geometry().bottomRight()
            else:
                self.is_moving = True
                self.drag_start_pos = event.globalPosition().toPoint()
                self.start_geo = self.geometry()

    def mouseMoveEvent(self, event):
        if not event.buttons():
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if QRect(0,0,45,45).contains(event.pos()) else Qt.CursorShape.ArrowCursor)
            return
        if self.is_resizing:
            curr_pos = event.globalPosition().toPoint()
            new_w = max(280, self.anchor_br.x() - curr_pos.x())
            self.setGeometry(self.anchor_br.x() - new_w, self.anchor_br.y() - int(new_w * self.aspect_ratio), new_w, int(new_w * self.aspect_ratio))
        elif self.is_moving:
            self.move(self.start_geo.topLeft() + (event.globalPosition().toPoint() - self.drag_start_pos))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0,0,self.width(),self.height(),self.CORNER_RADIUS,self.CORNER_RADIUS)
        p.setClipPath(path)
        p.drawPixmap(self.rect(), self.scaled_bg)

    def open_file_dialog(self):
        fp, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video (*.mp4 *.mkv *.avi *.flv)")
        if fp:
            base = os.path.splitext(fp)[0]
            self.subtitles = parse_subtitles(base + ".srt") if os.path.exists(base + ".srt") else \
                             parse_subtitles(base + ".ass") if os.path.exists(base + ".ass") else []
            self.player.setSource(QUrl.fromLocalFile(os.path.abspath(fp)))
            self.player_container.show(); self.slider.show(); self.player.play()

    def reset_to_select(self):
        if self.player_container.isVisible():
            self.player.stop(); self.player_container.hide(); self.slider.hide(); self.sub_label.hide()

    def save_and_exit(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("pos", self.pos())
        self.player.stop(); QApplication.quit(); sys.exit(0)

    def init_to_bottom_right(self):
        s = QApplication.primaryScreen().availableGeometry()
        self.move(s.x() + s.width() - self.width() - 15, s.y() + s.height() - self.height() - 15)

if __name__ == '__main__':
    if sys.platform.startswith('linux'): os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication(sys.argv)
    bg = get_resource_path("ad_bg.png")
    player = AdPopupPlayer(bg)
    player.show()
    sys.exit(app.exec())
