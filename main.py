import sys
import os
import re
import hashlib
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QLabel,
                             QFileDialog, QSlider, QVBoxLayout, QFrame, QGraphicsDropShadowEffect)
from PyQt6.QtCore import Qt, QUrl, QTimer, QRect, QPoint, QSettings
from PyQt6.QtGui import QPixmap, QPainterPath, QPainter, QColor, QFont, QFontMetrics
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtCore import QStandardPaths

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
    except Exception as e:
        print(f"解析出错: {e}")
    return subtitles

class AdPopupPlayer(QWidget):
    def __init__(self, bg_image_path):
        super().__init__()
        
        # --- 配置路径强制绑定当前目录 ---
        if hasattr(sys, '_MEIPASS'):
            app_dir = os.path.dirname(sys.executable)
        else:
            app_dir = os.path.dirname(os.path.abspath(__file__))
            
        self.settings = QSettings(os.path.join(app_dir, "config.ini"), QSettings.Format.IniFormat)
        
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.CORNER_RADIUS = 20
        self.CROP_INSET = 10
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

        self.current_video_path = None
        self.pending_seek_pos = 0
        
        self.subtitles = []
        self.current_subtitle_text = ""
        self.slider_dragging = False
        self.was_playing = False

        self.auto_save_timer = QTimer(self)
        self.auto_save_timer.timeout.connect(self.save_current_progress)
        self.auto_save_timer.start(10000)

        self.init_ui()
        pos = self.settings.value("pos", type=QPoint)

        if pos:
            self.move(pos)
        else:
            QTimer.singleShot(50, self.init_to_bottom_right)

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

        self.play_pause_hitbox = QPushButton(self.player_container)
        self.play_pause_hitbox.setStyleSheet("background: transparent; border: none;")
        self.play_pause_hitbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_pause_hitbox.clicked.connect(self.toggle_playback)

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.hide()
        self.slider.setStyleSheet("""
            QSlider { height: 30px; background: transparent; }
            QSlider::groove:horizontal { border: none; height: 2px; background: rgba(255,255,255,40); }
            QSlider::handle:horizontal { background: rgba(255, 215, 0, 180); width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; }
            QSlider::handle:horizontal:hover { background: gold; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }
        """)
        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderMoved.connect(self.on_slider_moved)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)

        # 视频时长提示标签
        self.time_label = QLabel(self)
        self.time_label.hide()
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setStyleSheet("""
            QLabel{
                color: #FFFFFF;
                background: rgba(0, 0, 0, 200);
                border: 1px solid rgba(255, 255, 255, 100);
                border-radius: 6px;
                padding: 4px 10px;
                font-family: Consolas, "Courier New", monospace;
                font-size: 13px;
                font-weight: bold;
            }
        """)

        # 字幕标签
        self.sub_label = QLabel(self)
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_label.setWordWrap(True)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(4)
        shadow.setColor(QColor(0, 0, 0, 255))
        shadow.setOffset(2, 2)
        self.sub_label.setGraphicsEffect(shadow)
        self.sub_label.setStyleSheet("color: #FFFF00; font-weight:bold; background: transparent;")
        self.sub_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.sub_label.hide()

        self.close_btn = QPushButton(self)
        self.close_btn.setStyleSheet("background: transparent; border: none;")
        self.close_btn.clicked.connect(self.save_and_exit)

        self.is_resizing = self.is_moving = False

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.save_current_progress()
        else:
            self.player.play()

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        v_rect = QRect(0, int(h * self.VIDEO_Y), w, int(h * self.VIDEO_H))
        self.player_container.setGeometry(v_rect)
        self.play_pause_hitbox.setGeometry(0, 0, v_rect.width(), v_rect.height())

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

        self.current_subtitle_text = ""
        super().resizeEvent(event)

    def on_duration_changed(self, dur):
            self.slider.setRange(0, dur)
            # 当视频成功解析出总时长后，执行“跳跃”到上次保存的进度
            if self.pending_seek_pos > 0 and dur > 0:
                target_pos = self.pending_seek_pos
                self.pending_seek_pos = 0 # 立即清空标记，防止多次触发
                
                if target_pos < dur - 3000: # 如果差3秒就播完了，直接从头播
                    # 【核心修复 1】：给硬件解码器 300 毫秒的喘息时间，然后再强行跳转
                    QTimer.singleShot(300, lambda: self.player.setPosition(target_pos))

    def on_position_changed(self, position):
        if not self.slider_dragging:
            self.slider.setValue(position)
        
        matched_texts = [sub['text'] for sub in self.subtitles if sub['start'] <= position <= sub['end']]
        if matched_texts:
            full_text = "\n".join(matched_texts)
            if full_text != self.current_subtitle_text:
                self.current_subtitle_text = full_text
                f_size = max(10, int(self.width()/24))
                font = QFont("Microsoft YaHei", f_size)
                font.setBold(True)
                metrics = QFontMetrics(font)
                while (max([metrics.horizontalAdvance(l) for l in full_text.split("\n")]) > self.width()-20 and f_size > 8):
                    f_size -= 1
                    font = QFont("Microsoft YaHei", f_size)
                    font.setBold(True)
                    metrics = QFontMetrics(font)
                self.sub_label.setFont(font)
                self.sub_label.setText(full_text)
                self.sub_label.adjustSize()
                self.sub_label.move((self.width()-self.sub_label.width())//2, int(self.height()*self.SUB_Y))
            self.sub_label.show()
        else:
            self.current_subtitle_text = ""
            self.sub_label.hide()

    def on_slider_pressed(self):
        self.slider_dragging = True
        self.was_playing = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        self.audio.setMuted(True)
        # 按下时立即显示时间标签并放在视频顶部外侧
        self.update_time_label(self.slider.value())
        self.time_label.show()
        self.time_label.raise_()

    def on_slider_moved(self, pos):
        self.player.setPosition(pos)
        self.update_time_label(pos)

    def on_slider_released(self):
        self.player.setPosition(self.slider.value())
        self.audio.setMuted(False)
        if self.was_playing:
            self.player.play()
        self.slider_dragging = False
        self.time_label.hide()

    def update_time_label(self, pos):
        """更新时间文本并动态调整其坐标，使其位于视频区域的正上方"""
        total = self.player.duration()
        self.time_label.setText(f"{self.format_time(pos)} / {self.format_time(total)}")
        self.time_label.adjustSize()
        
        # 核心改动：把 Y 坐标放在视频的上面（属于背景纯图片区），永远不会被遮挡
        lbl_x = (self.width() - self.time_label.width()) // 2
        lbl_y = int(self.height() * self.VIDEO_Y) - self.time_label.height() - 10
        self.time_label.move(lbl_x, lbl_y)
        self.time_label.raise_() # 强制置顶

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            if self.slider.isVisible() and self.slider.geometry().contains(pos): return
            if self.player_container.isVisible() and self.player_container.geometry().contains(pos): return
            
            if QRect(0,0,45,45).contains(pos):
                self.is_resizing = self.is_resizing_live = True
                self.anchor_br = self.geometry().bottomRight()
            else:
                self.is_moving = True
                self.drag_start_pos = event.globalPosition().toPoint()
                self.start_geo = self.geometry()
                self.window().windowHandle().startSystemMove()

    def mouseMoveEvent(self, event):
        if not event.buttons():
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if QRect(0,0,45,45).contains(event.pos()) else Qt.CursorShape.ArrowCursor)
            return
        if self.is_resizing:
            curr_pos = event.globalPosition().toPoint()
            new_w = max(280, self.anchor_br.x() - curr_pos.x())
            self.setGeometry(self.anchor_br.x() - new_w, self.anchor_br.y() - int(new_w * self.aspect_ratio), new_w, int(new_w * self.aspect_ratio))

    def mouseReleaseEvent(self, event):
        if self.is_resizing_live:
            self.is_resizing_live = False
            self.update()
        self.is_resizing = self.is_moving = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.is_resizing_live:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(0,0,self.width(),self.height(),self.CORNER_RADIUS,self.CORNER_RADIUS)
        p.setClipPath(path)
        src = QRect(self.CROP_INSET, self.CROP_INSET, self.raw_pixmap.width()-self.CROP_INSET*2, self.raw_pixmap.height()-self.CROP_INSET*2)
        p.drawPixmap(self.rect(), self.raw_pixmap, src)

    def save_current_progress(self):
        """保存进度并刷新到文件"""
        if not self.current_video_path or not self.player_container.isVisible():
            return
        pos = self.player.position()
        dur = self.player.duration()
        if dur > 0 and pos >= dur - 3000:
            pos = 0
        key = hashlib.md5(self.current_video_path.encode("utf-8")).hexdigest()
        self.settings.setValue(f"PlayHistory/{key}", pos)
        self.settings.sync() # 强制立即写入硬盘

    def open_file_dialog(self):
            fp, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video (*.mp4 *.mkv *.avi *.flv)")
            if fp:
                # 1. 切换视频前，先保存上一部的进度
                self.save_current_progress()

                self.player.stop()
                
                # 2. 记录当前视频路径
                self.current_video_path = os.path.abspath(fp)
                
                # 3. 解析字幕
                base = os.path.splitext(self.current_video_path)[0]
                self.subtitles = parse_subtitles(base + ".srt") if os.path.exists(base + ".srt") else \
                                parse_subtitles(base + ".ass") if os.path.exists(base + ".ass") else []
                                
                # 4. 去 config.ini 里找这个视频的历史进度
                key = hashlib.md5(self.current_video_path.encode('utf-8')).hexdigest()
                
                # 【核心修复 2】：强制安全转换读取到的值为整数，防止 INI 字符串解析失败
                raw_val = self.settings.value(f"PlayHistory/{key}", 0)
                try:
                    self.pending_seek_pos = int(raw_val)
                except (ValueError, TypeError):
                    self.pending_seek_pos = 0

                self.player.setSource(QUrl.fromLocalFile(self.current_video_path))
                self.player_container.show()
                self.slider.show()
                self.player.play()

    def reset_to_select(self):
        if self.player_container.isVisible():
            self.save_current_progress()
            self.player.stop()
            self.player_container.hide()
            self.slider.hide()
            self.sub_label.hide()
            self.current_video_path = None
            self.pending_seek_pos = 0
            self.subtitles = []
            self.current_subtitle_text = ""
            self.slider.setValue(0)

# --- 统一拦截系统的关闭事件 ---
    def closeEvent(self, event):
        # 1. 抢在播放器停止前，先保存进度
        self.save_current_progress()
        
        # 2. 停止播放器（此时内部进度归 0，但进度已经存进硬盘了，没关系）
        self.player.stop()

        # 3. 保存窗口大小和位置
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("pos", self.pos())
        self.settings.sync()

        # 4. 同意关闭窗口
        event.accept()

        # 5. 直接暴力绝杀进程，断绝一切二次触发的可能性
        os._exit(0)

    # --- 按钮点击绑定的退出方法 ---
    def save_and_exit(self):
        # 点击右上角的 X 按钮时，直接呼叫窗口的 close()，它会自然触发上面的 closeEvent
        self.close()

    def init_to_bottom_right(self):
        s = QApplication.primaryScreen().availableGeometry()
        self.move(s.x() + s.width() - self.width() - 15, s.y() + s.height() - self.height() - 15)
        
    def format_time(self, ms):
        sec = ms // 1000
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

if __name__ == '__main__':
    if sys.platform.startswith('linux'): os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication(sys.argv)
    bg = get_resource_path("ad_bg.png")
    player = AdPopupPlayer(bg)
    player.show()
    sys.exit(app.exec())
