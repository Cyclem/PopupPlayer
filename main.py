import sys
import os
import re
import hashlib
import time
from PyQt6.QtCore import QObject, pyqtSignal

# ==========================================================
# DLL 搜索路径预处理
# ==========================================================
curr_dir = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys, '_MEIPASS'):
    curr_dir = sys._MEIPASS
os.environ["PATH"] = curr_dir + os.pathsep + os.environ.get("PATH", "")

import mpv  
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QLabel,
                             QFileDialog, QSlider, QVBoxLayout, QFrame, QGraphicsDropShadowEffect)
from PyQt6.QtCore import Qt, QTimer, QRect, QPoint, QSettings
from PyQt6.QtGui import QPixmap, QPainterPath, QPainter, QColor, QFont, QFontMetrics

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'): return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# --- 纯净版字幕解析器 ---
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
                        s = int(times[0][0])*3600000 + int(times[0][1])*60000 + int(times[0][2])*1000 + int(times[0][3])
                        e = int(times[1][0])*3600000 + int(times[1][1])*60000 + int(times[1][2])*1000 + int(times[1][3])
                        subtitles.append({'start': s, 'end': e, 'text': re.sub(r'<[^>]*>', '', "\n".join(lines[2:]))})
    except: pass
    return subtitles

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_cookie_path():
    return os.path.join(get_app_dir(), "cookies.txt")


class AdPopupPlayer(QWidget):
    time_signal = pyqtSignal(float)
    
    def __init__(self, bg_image_path):
        super().__init__()
        
        self.is_resizing = False
        self.slider_dragging = False
        self.was_playing = False
        self.mpv_player = None
        self.current_media_id = None
        self.subtitles = []
        self.current_subtitle_text = ""
        self.last_manual_seek_time = 0 
        self.video_duration = 0.0
        self.time_signal.connect(self._sync_progress)

        app_real_dir = os.path.dirname(sys.executable) if hasattr(sys, '_MEIPASS') else curr_dir
        self.settings = QSettings(os.path.join(app_real_dir, "config.ini"), QSettings.Format.IniFormat)
        
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.CORNER_RADIUS, self.CROP_INSET = 20, 10
        self.raw_pixmap = QPixmap(bg_image_path)
        self.aspect_ratio = self.raw_pixmap.height() / self.raw_pixmap.width()

        saved_geo = self.settings.value("geometry")
        if saved_geo: self.restoreGeometry(saved_geo)
        else: self.resize(380, int(380 * self.aspect_ratio))

        self.VIDEO_Y, self.VIDEO_H, self.SUB_Y = 0.311, 0.391, 0.715
        
        self.init_ui()
        self.show()
        QTimer.singleShot(100, self.init_mpv_engine)

        pos = self.settings.value("pos", type=QPoint)
        if pos: self.move(pos)
        else: QTimer.singleShot(200, self.init_to_bottom_right)

    def init_mpv_engine(self):
        try:
            cookie_path = get_cookie_path()
            ytdl_opts = f'cookies="{cookie_path}"' if os.path.exists(cookie_path) else ""

            self.mpv_player = mpv.MPV(
                wid=str(int(self.container.winId())),
                ytdl=True, osc=False, input_default_bindings=True,
                vo='gpu' if os.name == 'nt' else 'x11',
                ytdl_raw_options=ytdl_opts
            )
            self.mpv_player['keepaspect'] = False
            self.mpv_player['video-unscaled'] = False
            self.mpv_player['panscan'] = 1.0
            self.mpv_player['stretch-image-subs-to-screen'] = True

            self.mpv_player.observe_property(
                'time-pos',
                lambda name, value: self.time_signal.emit(value if value else 0.0)
            )
            self.mpv_player.observe_property('duration', self._on_duration_update)
        except: 
            pass

    def _on_duration_update(self, name, value):
        if value is not None: self.video_duration = float(value)

    def _sync_progress(self, sec):
        if self.video_duration > 0 and not self.slider_dragging:
            # 如果刚手动跳转完，给 1 秒缓冲时间，不自动同步
            if time.time() - self.last_manual_seek_time < 1.0: return
            
            val = int((sec / self.video_duration) * 1000)
            self.slider.blockSignals(True)
            self.slider.setValue(val)
            self.slider.blockSignals(False)
            
        self.update_subtitles(int(sec * 1000))

    def update_subtitles(self, ms):
        matched = [s['text'] for s in self.subtitles if s['start'] <= ms <= s['end']]
        if matched:
            text = "\n".join(matched)
            if text != self.current_subtitle_text:
                self.current_subtitle_text = text
                f = QFont("Microsoft YaHei", max(10, int(self.width()/24)), QFont.Weight.Bold)
                self.sub_label.setFont(f); self.sub_label.setText(text); self.sub_label.adjustSize()
                self.sub_label.move((self.width()-self.sub_label.width())//2, int(self.height()*self.SUB_Y))
            self.sub_label.show(); self.sub_label.raise_()
        else: 
            self.current_subtitle_text = ""
            self.sub_label.hide()

    def init_ui(self):
        self.start_game_hitbox = QPushButton(self)
        self.start_game_hitbox.setStyleSheet("background:transparent; border:none;")
        self.start_game_hitbox.clicked.connect(self.open_file_dialog)
        
        self.reset_hitbox = QPushButton(self)
        self.reset_hitbox.setStyleSheet("background:transparent; border:none;")
        self.reset_hitbox.clicked.connect(self.reset_to_select)

        self.container = QFrame(self)
        self.container.setStyleSheet("background: transparent;")
        self.container.hide()

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, 1000); self.slider.hide()
        self.slider.setStyleSheet("""
            QSlider { height: 32px; background: transparent; margin-top: -2px;}
            QSlider::groove:horizontal { height: 2px; background: rgba(255,255,255,40); }
            QSlider::handle:horizontal { background: gold; width: 12px; height: 12px; border-radius: 6px; margin: -5px 0; }
        """)
        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderMoved.connect(self.on_slider_moved)
        self.slider.sliderReleased.connect(self.on_slider_released)

        self.time_label = QLabel(self); self.time_label.hide()
        self.time_label.setStyleSheet("color:white; background:rgba(0,0,0,180); border:1px solid gold; border-radius:4px; padding:4px 8px; font-weight:bold; font-family:Consolas;")

        self.sub_label = QLabel(self); self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter); self.sub_label.setWordWrap(True)
        shadow = QGraphicsDropShadowEffect(); shadow.setBlurRadius(4); shadow.setColor(QColor(0,0,0,255)); shadow.setOffset(2,2)
        self.sub_label.setGraphicsEffect(shadow)
        self.sub_label.setStyleSheet("color: #FFFF00; font-weight:bold; background:transparent;")
        self.sub_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); self.sub_label.hide()

        self.close_btn = QPushButton(self)
        self.close_btn.setStyleSheet("background:transparent; border:none;")
        self.close_btn.clicked.connect(self.close)

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        v_rect = QRect(0, int(h * self.VIDEO_Y), w, int(h * self.VIDEO_H))
        self.container.setGeometry(v_rect)
        self.slider.setGeometry(10, v_rect.bottom(), w - 20, 24)
        self.sub_label.setGeometry(10, int(h * self.SUB_Y), w - 20, int(h * 0.12))
        
        BG_W, BG_H = 1114, 1412
        self.start_game_hitbox.setGeometry(int(w*302/BG_W), int(h*1217/BG_H), int(w*527/BG_W), int(h*134/BG_H))
        self.reset_hitbox.setGeometry(int(w*0.85), int(h*0.94), int(w*0.15), int(h*0.06))
        btn_sz = int(w * 0.12)
        self.close_btn.setGeometry(w - btn_sz, 0, btn_sz, btn_sz)
        
        super().resizeEvent(event)
        self.slider.raise_()

    def on_slider_pressed(self): 
        self.slider_dragging = True
        if self.mpv_player: 
            self.was_playing = not self.mpv_player.pause
            self.mpv_player.pause = True
        self.time_label.show()

    def on_slider_moved(self, val):
        if self.video_duration <= 0 or not self.mpv_player: return
        target_s = (val / 1000.0) * self.video_duration
        
        # --- 实时视频预览核心逻辑 ---
        try:
            # 使用 'keyframes' 查找模式，实现流畅预览且不卡顿
            self.mpv_player.command('seek', target_s, 'absolute', 'keyframes')
        except:
            pass

        # 更新时间标签
        self.time_label.setText(f"{self.format_time(target_s*1000)} / {self.format_time(self.video_duration*1000)}")
        self.time_label.adjustSize()
        self.time_label.move((self.width()-self.time_label.width())//2, self.container.y()-45)
        self.time_label.raise_()

    def on_slider_released(self):
        self.slider_dragging = False
        self.last_manual_seek_time = time.time()
        if self.mpv_player:
            target_s = (self.slider.value() / 1000.0) * self.video_duration
            # 最终跳转使用 'exact' 保证位置精确
            try:
                self.mpv_player.command('seek', target_s, 'absolute', 'exact')
            except:
                pass
            
            # 如果之前在播放，则恢复播放
            if self.was_playing:
                self.mpv_player.pause = False
        self.time_label.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            if self.slider.isVisible() and self.slider.geometry().contains(pos): 
                self.slider.event(event)
                return
            if self.container.isVisible() and self.container.geometry().contains(pos):
                if self.mpv_player: self.mpv_player.pause = not self.mpv_player.pause
                return
            if QRect(0,0,45,45).contains(pos):
                self.is_resizing = True
                self.anchor_br = self.geometry().bottomRight()
            else: 
                self.window().windowHandle().startSystemMove()

    def mouseMoveEvent(self, event):
        if not event.buttons():
            pos = event.pos()
            if QRect(0,0,45,45).contains(pos): self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif self.container.isVisible() and self.container.geometry().contains(pos): self.setCursor(Qt.CursorShape.PointingHandCursor)
            elif self.slider.isVisible() and self.slider.geometry().contains(pos): self.setCursor(Qt.CursorShape.SizeHorCursor)
            else: self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self.is_resizing:
            curr = event.globalPosition().toPoint()
            new_w = max(280, self.anchor_br.x() - curr.x())
            self.setGeometry(self.anchor_br.x()-new_w, self.anchor_br.y()-int(new_w*self.aspect_ratio), new_w, int(new_w*self.aspect_ratio))

    def mouseReleaseEvent(self, event): 
        self.is_resizing = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), self.CORNER_RADIUS, self.CORNER_RADIUS)
        p.setClipPath(path)
        src = QRect(10, 10, self.raw_pixmap.width()-20, self.raw_pixmap.height()-20)
        p.drawPixmap(self.rect(), self.raw_pixmap, src)

    def open_file_dialog(self):
        clip = QApplication.clipboard().text().strip()
        bv_match = re.search(r'(BV[1-9A-HJ-NP-Za-km-z]{10})', clip)
        if bv_match:
            self.play_media(f"https://www.bilibili.com/video/{bv_match.group(1)}")
            QApplication.clipboard().clear()
        else:
            fp, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video (*.mp4 *.mkv *.avi *.flv *.webm)")
            if fp: self.play_media(os.path.abspath(fp))

    def play_media(self, target):
        if not self.mpv_player: return
        self.save_current_progress()
        self.current_media_id = hashlib.md5(target.encode("utf-8")).hexdigest()
        self.subtitles = parse_subtitles(os.path.splitext(target)[0]+".srt") if not target.startswith("http") else []
        last_pos = int(self.settings.value(f"History/{self.current_media_id}", 0))
        self.mpv_player['start'] = str(last_pos / 1000.0) if last_pos > 1000 else "0"
        self.mpv_player.play(target)
        self.container.show()
        self.slider.show()

    def save_current_progress(self):
        if self.mpv_player and self.current_media_id:
            pos, dur = self.mpv_player.time_pos, self.mpv_player.duration
            if pos:
                val = int(pos * 1000) if not (dur and pos >= dur - 3) else 0
                self.settings.setValue(f"History/{self.current_media_id}", val)
                self.settings.sync()

    def reset_to_select(self):
        if self.mpv_player: 
            self.save_current_progress()
            self.mpv_player.command('stop')
        self.container.hide()
        self.slider.hide()
        self.sub_label.hide()

    def closeEvent(self, event):
        self.save_current_progress()
        if self.mpv_player: self.mpv_player.terminate()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("pos", self.pos())
        self.settings.sync()
        os._exit(0)

    def init_to_bottom_right(self):
        s = QApplication.primaryScreen().availableGeometry()
        self.move(s.x() + s.width() - self.width() - 15, s.y() + s.height() - self.height() - 15)

    def format_time(self, ms):
        if ms is None: return "00:00"
        s = int(ms // 1000)
        m, s = divmod(s, 60); h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


if __name__ == '__main__':
    app = QApplication(sys.argv)
    bg = get_resource_path("ad_bg.png")
    player = AdPopupPlayer(bg)
    sys.exit(app.exec())
