import sys
import os
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QLabel,
                             QFileDialog, QSlider, QVBoxLayout, QFrame)
from PyQt6.QtCore import Qt, QUrl, QTimer, QRect, QPoint, QSettings
from PyQt6.QtGui import QPixmap, QPainterPath, QPainter
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

class AdPopupPlayer(QWidget):
    def __init__(self, bg_image_path):
        super().__init__()

        # 1. 获取程序运行的当前目录
        # 如果是打包后的exe，则获取exe所在目录
        if hasattr(sys, '_MEIPASS'):
            # 打包环境
            app_dir = os.path.dirname(sys.executable)
        else:
            # 开发环境
            app_dir = os.path.dirname(os.path.abspath(__file__))

        # 2. 拼接出配置文件的完整路径
        ini_path = os.path.join(app_dir, "config.ini")

        # 3. 强制 QSettings 使用 INI 文件格式，并存到指定路径
        self.settings = QSettings(ini_path, QSettings.Format.IniFormat)

        # 1. 窗口基本属性
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.X11BypassWindowManagerHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # --- 图像处理参数 ---
        self.CORNER_RADIUS = 20
        self.CROP_INSET = 12
        self.raw_pixmap = QPixmap(bg_image_path)
        if self.raw_pixmap.isNull():
            print("错误: 无法加载背景图")
            sys.exit(1)
        self.aspect_ratio = self.raw_pixmap.height() / self.raw_pixmap.width()

        # 2. 确定初始尺寸 (逻辑：优先读配置，没有则用默认)
        saved_geometry = self.settings.value("geometry")
        if saved_geometry:
            self.restoreGeometry(saved_geometry)
        else:
            # 默认初始大小
            init_w = 380
            init_h = int(init_w * self.aspect_ratio)
            self.resize(init_w, init_h)

        # 视频区域占比
        self.v_y_rate = 0.27
        self.v_h_rate = 0.46

        # 3. UI 元素初始化 (与之前逻辑一致)
        self.start_game_hitbox = QPushButton(self)
        self.start_game_hitbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_game_hitbox.setStyleSheet("background: transparent; border: none;")
        self.start_game_hitbox.clicked.connect(self.open_file_dialog)

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
        self.slider.sliderMoved.connect(lambda pos: self.player.setPosition(pos))
        self.player.positionChanged.connect(lambda pos: self.slider.setValue(pos))
        self.player.durationChanged.connect(lambda dur: self.slider.setRange(0, dur))

        layout.addWidget(self.video_widget)
        layout.addWidget(self.slider)

        self.replace_btn = QPushButton("↺", self.player_container)
        self.replace_btn.setStyleSheet("background: rgba(0,0,0,100); color: white; border-radius: 15px;")
        self.replace_btn.setFixedSize(30, 30)
        self.replace_btn.clicked.connect(self.reset_to_select)

        self.close_btn = QPushButton(self)
        self.close_btn.setStyleSheet("background: transparent; border: none;")
        self.close_btn.clicked.connect(self.save_and_exit)

        # 4. 交互控制
        self.is_resizing = False
        self.is_moving = False

        # 5. 启动位置
        # 如果是第一次运行（没有保存过位置），则定位到右下角
        if not self.settings.value("pos"):
            QTimer.singleShot(50, self.init_to_bottom_right)
        else:
            # 如果有保存过位置，从配置中恢复
            saved_pos = self.settings.value("pos")
            self.move(saved_pos)

    # --- 核心逻辑：保存与退出 ---
    def save_and_exit(self):
        """退出前保存当前的状态"""
        self.settings.setValue("geometry", self.saveGeometry()) # 保存尺寸
        self.settings.setValue("pos", self.pos())              # 保存坐标
        self.player.stop()
        QApplication.quit()
        sys.exit(0)

    # 重写窗口关闭事件，防止非正常关闭没保存
    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("pos", self.pos())
        super().closeEvent(event)

    def init_to_bottom_right(self):
        screen = QApplication.primaryScreen().availableGeometry()
        px = screen.x() + screen.width() - self.width() - 15
        py = screen.y() + screen.height() - self.height() - 15
        self.move(px, py)

    # --- 视频逻辑 ---
    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择本地视频", "", "视频文件 (*.mp4 *.mkv *.avi *.flv)")
        if file_path:
            self.player.setSource(QUrl.fromLocalFile(os.path.abspath(file_path)))
            self.player_container.show()
            self.start_game_hitbox.hide()
            self.player.play()

    def reset_to_select(self):
        self.player.stop()
        self.player_container.hide()
        self.start_game_hitbox.show()

    # --- UI 动态刷新 ---
    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        v_rect = QRect(0, int(h * self.v_y_rate), w, int(h * self.v_h_rate))
        self.player_container.setGeometry(v_rect)
        self.replace_btn.move(v_rect.width() - 40, 10)
        self.start_game_hitbox.setGeometry(0, int(h * 0.75), w, int(h * 0.20))
        btn_size = int(w * 0.13)
        self.close_btn.setGeometry(w - btn_size, 0, btn_size, btn_size)
        super().resizeEvent(event)

    # --- 交互控制：左上角拉伸 ---
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
            new_width = self.anchor_br.x() - curr_pos.x()
            if new_width < 280: new_width = 280
            new_height = int(new_width * self.aspect_ratio)
            self.setGeometry(self.anchor_br.x() - new_width, self.anchor_br.y() - new_height, new_width, new_height)
        elif self.is_moving:
            delta = event.globalPosition().toPoint() - self.drag_start_pos
            self.move(self.start_geo.topLeft() + delta)

    def mouseReleaseEvent(self, event):
        self.is_resizing = self.is_moving = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), self.CORNER_RADIUS, self.CORNER_RADIUS)
        painter.setClipPath(path)
        src_rect = QRect(self.CROP_INSET, self.CROP_INSET, self.raw_pixmap.width() - self.CROP_INSET*2, self.raw_pixmap.height() - self.CROP_INSET*2)
        painter.drawPixmap(self.rect(), self.raw_pixmap, src_rect)

if __name__ == '__main__':
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication(sys.argv)
    bg = get_resource_path("ad_bg.png")
    player = AdPopupPlayer(bg)
    player.show()
    sys.exit(app.exec())