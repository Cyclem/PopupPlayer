import sys
import os
import re
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QLabel,
                             QFileDialog, QSlider, QVBoxLayout, QFrame)
from PyQt6.QtCore import Qt, QUrl, QTimer, QRect, QPoint, QSettings
from PyQt6.QtGui import QPixmap, QPainterPath, QPainter, QColor, QFont
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def parse_srt(file_path):
    subtitles = []
    if not os.path.exists(file_path): return subtitles
    for enc in ['utf-8', 'gbk', 'utf-8-sig']:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                content = f.read().replace('\r\n', '\n')
            break
        except: continue
    if not content: return subtitles
    try:
        blocks = content.split('\n\n')
        for block in blocks:
            lines = [l for l in block.split('\n') if l.strip()]
            if len(lines) >= 3:
                times = re.findall(r'(\d+):(\d+):(\d+),(\d+)', lines[1])
                if len(times) == 2:
                    start_ms = int(times[0][0])*3600000 + int(times[0][1])*60000 + int(times[0][2])*1000 + int(times[0][3])
                    end_ms = int(times[1][0])*3600000 + int(times[1][1])*60000 + int(times[1][2])*1000 + int(times[1][3])
                    text = "\n".join(lines[2:])
                    subtitles.append({'start': start_ms, 'end': end_ms, 'text': text})
    except: pass
    return subtitles

class AdPopupPlayer(QWidget):
    def __init__(self, bg_image_path):
        super().__init__()

        # 配置文件
        app_dir = os.path.dirname(sys.executable) if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.abspath(__file__))
        self.settings = QSettings(os.path.join(app_dir, "config.ini"), QSettings.Format.IniFormat)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # 图像处理与抗锯齿参数
        self.CORNER_RADIUS, self.CROP_INSET = 20, 10
        self.raw_pixmap = QPixmap(bg_image_path)
        self.aspect_ratio = self.raw_pixmap.height() / self.raw_pixmap.width()

        # 恢复尺寸或初始化
        saved_geo = self.settings.value("geometry")
        if saved_geo: self.restoreGeometry(saved_geo)
        else: self.resize(380, int(380 * self.aspect_ratio))

        # --- 针对新素材图的比例参数 ---
        self.v_y_rate = 0.28  # 视频下移，露出标题
        self.v_h_rate = 0.45  # 视频高度比例
        self.subtitles = []

        self.init_ui()

        if not self.settings.value("pos"): QTimer.singleShot(50, self.init_to_bottom_right)
        else: self.move(self.settings.value("pos"))

    def init_ui(self):
        # 1. 载入视频点击区 (精确匹配下移后的金色按钮)
        self.start_game_hitbox = QPushButton(self)
        self.start_game_hitbox.setStyleSheet("background: transparent; border: none;")
        self.start_game_hitbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_game_hitbox.clicked.connect(self.open_file_dialog)

        # 2. 更换/重置点击区 (覆盖右下角“广告”字样)
        self.reset_hitbox = QPushButton(self)
        self.reset_hitbox.setStyleSheet("background: transparent; border: none;")
        self.reset_hitbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_hitbox.clicked.connect(self.reset_to_select)

        # 3. 播放器容器
        self.player_container = QFrame(self)
        self.player_container.hide()
        layout = QVBoxLayout(self.player_container)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(0)
        self.video_widget = QVideoWidget()
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setStyleSheet("height: 10px;")
        self.slider.sliderMoved.connect(lambda pos: self.player.setPosition(pos))
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(lambda dur: self.slider.setRange(0, dur))
        layout.addWidget(self.video_widget)
        layout.addWidget(self.slider)

        # 4. 字幕标签 (放置在视频下方与按钮上方的黑底区域)
        self.sub_label = QLabel(self)
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_label.setStyleSheet("color: #FFFF00; font-weight: bold; background: transparent;")
        self.sub_label.setWordWrap(True)
        self.sub_label.hide()

        # 5. 总关闭按钮 (右上角 X)
        self.close_btn = QPushButton(self)
        self.close_btn.setStyleSheet("background: transparent; border: none;")
        self.close_btn.clicked.connect(self.save_and_exit)

        self.is_resizing = self.is_moving = False

    def resizeEvent(self, event):
        w, h = self.width(), self.height()

        # 1. 视频区域
        v_rect = QRect(0, int(h * self.v_y_rate), w, int(h * self.v_h_rate))
        self.player_container.setGeometry(v_rect)

        # 2. 字幕区域 (视频下方 73% - 78% 处)
        font_size = max(10, int(w / 26))
        self.sub_label.setFont(QFont("Microsoft YaHei", font_size))
        self.sub_label.setGeometry(10, int(h * 0.73), w - 20, int(h * 0.05))
        self.sub_label.raise_()

        # 3. “开始游戏”感应区 (适应最下方金色图标：78% - 93% 处)
        btn_w = int(w * 0.58)
        btn_x = (w - btn_w) // 2
        btn_y = int(h * 0.85)
        btn_h = int(h * 0.15)
        self.start_game_hitbox.setGeometry(btn_x, btn_y, btn_w, btn_h)

        # 4. “广告”重置感应区 (最右下角)
        self.reset_hitbox.setGeometry(int(w * 0.85), int(h * 0.93), int(w * 0.15), int(h * 0.07))

        # 5. 关闭按钮
        btn_sz = int(w * 0.12)
        self.close_btn.setGeometry(w - btn_sz, 0, btn_sz, btn_sz)

        super().resizeEvent(event)

    def on_position_changed(self, position):
        self.slider.setValue(position)
        current_text = ""
        for sub in self.subtitles:
            if sub['start'] <= position <= sub['end']:
                current_text = sub['text']
                break
        if current_text:
            self.sub_label.setText(current_text)
            self.sub_label.show()
            self.sub_label.raise_()
        else:
            self.sub_label.hide()

    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video (*.mp4 *.mkv *.avi)")
        if file_path:
            srt_path = os.path.splitext(file_path)[0] + ".srt"
            self.subtitles = parse_srt(srt_path)
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

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if QRect(0, 0, 45, 45).contains(event.pos()):
                self.is_resizing = True
                self.anchor_br = self.geometry().bottomRight()
            else:
                self.is_moving = True
                self.drag_start_pos = event.globalPosition().toPoint()
                self.start_geo = self.geometry()

    def mouseMoveEvent(self, event):
        if not event.buttons():
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if QRect(0, 0, 45, 45).contains(event.pos()) else Qt.CursorShape.ArrowCursor)
            return
        if self.is_resizing:
            curr_pos = event.globalPosition().toPoint()
            new_w = max(280, self.anchor_br.x() - curr_pos.x())
            new_h = int(new_w * self.aspect_ratio)
            self.setGeometry(self.anchor_br.x() - new_w, self.anchor_br.y() - new_h, new_w, new_h)
        elif self.is_moving:
            delta = event.globalPosition().toPoint() - self.drag_start_pos
            self.move(self.start_geo.topLeft() + delta)

    def mouseReleaseEvent(self, event): self.is_resizing = self.is_moving = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), self.CORNER_RADIUS, self.CORNER_RADIUS)
        p.setClipPath(path)
        src = QRect(self.CROP_INSET, self.CROP_INSET, self.raw_pixmap.width()-self.CROP_INSET*2, self.raw_pixmap.height()-self.CROP_INSET*2)
        p.drawPixmap(self.rect(), self.raw_pixmap, src)

    def init_to_bottom_right(self):
        s = QApplication.primaryScreen().availableGeometry()
        self.move(s.x() + s.width() - self.width() - 15, s.y() + s.height() - self.height() - 15)

if __name__ == '__main__':
    # 自动适配平台环境
    if sys.platform.startswith('linux'):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    app = QApplication(sys.argv)
    bg = get_resource_path("ad_bg.png")
    player = AdPopupPlayer(bg)
    player.show()
    sys.exit(app.exec())