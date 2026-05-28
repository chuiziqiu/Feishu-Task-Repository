"""
QR Code Scanner Application with Built-in Browser
=================================================
A PyQt5 application that scans QR codes via webcam and opens
decoded URLs in an embedded browser (QWebEngineView).
"""

import sys
import os
import json
import cv2
import numpy as np
from datetime import datetime
from urllib.parse import urlparse

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QDialog,
    QComboBox, QGroupBox, QMessageBox, QStackedWidget, QSplitter,
    QToolBar, QAction, QStatusBar, QLineEdit, QSizePolicy, QFrame
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QMutex, QMutexLocker, QTimer, QSize, QUrl
)
from PyQt5.QtGui import QImage, QPixmap, QIcon, QFont, QColor, QPalette

from pyzbar import pyzbar
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings


# ─── Constants ──────────────────────────────────────────────────────────────
APP_NAME = "QR Scanner"
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_history.json")
MAX_HISTORY = 50
CAMERA_TEST_RANGE = 10


# ─── Camera Manager (Singleton with mutex to prevent conflicts) ─────────────
class CameraManager:
    """
    Singleton camera manager that ensures only one camera is open at a time.
    Uses QMutex for thread-safe access.
    """
    _instance = None
    _mutex = QMutex()

    def __new__(cls):
        with QMutexLocker(cls._mutex):
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._camera = None
                cls._instance._active_index = -1
                cls._instance._lock = QMutex()
            return cls._instance

    @classmethod
    def enumerate_cameras(cls):
        """Test camera indices and return list of available cameras."""
        available = []
        for i in range(CAMERA_TEST_RANGE):
            cap = None
            try:
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        available.append(i)
            except Exception:
                pass
            finally:
                if cap is not None:
                    cap.release()
        return available

    def open_camera(self, index=0):
        """Open a camera by index. Returns True on success."""
        with QMutexLocker(self._lock):
            # Close existing camera first
            self._close_internal()

            try:
                self._camera = cv2.VideoCapture(index)
                if not self._camera.isOpened():
                    self._camera = None
                    return False
                # Set reasonable resolution
                self._camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self._camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self._active_index = index
                return True
            except Exception as e:
                print(f"Camera open error: {e}")
                self._camera = None
                return False

    def read_frame(self):
        """Read a frame from the active camera. Returns (success, frame)."""
        with QMutexLocker(self._lock):
            if self._camera is None or not self._camera.isOpened():
                return False, None
            try:
                ret, frame = self._camera.read()
                return ret, frame
            except Exception:
                return False, None

    def close(self):
        """Close the active camera."""
        with QMutexLocker(self._lock):
            self._close_internal()

    def _close_internal(self):
        """Internal close (caller must hold lock)."""
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None
            self._active_index = -1

    def is_open(self):
        with QMutexLocker(self._lock):
            return self._camera is not None and self._camera.isOpened()

    @property
    def active_index(self):
        return self._active_index


# ─── Camera Thread ──────────────────────────────────────────────────────────
class CameraThread(QThread):
    """
    Background thread for camera capture and QR code scanning.
    Emits signals for frame display and QR detection.
    """
    frame_ready = pyqtSignal(QImage)
    qr_detected = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    scanning_started = pyqtSignal()
    scanning_stopped = pyqtSignal()

    def __init__(self, camera_index=0, parent=None):
        super().__init__(parent)
        self.camera_index = camera_index
        self._running = False
        self._scanning = False
        self._last_qr = ""
        self._qr_cooldown = 0  # frames to skip after detection

    def run(self):
        """Main thread loop: capture frames and scan for QR codes."""
        manager = CameraManager()

        if not manager.open_camera(self.camera_index):
            self.error_occurred.emit(
                f"无法打开摄像头 {self.camera_index}。\n"
                "请检查：\n"
                "1. 摄像头是否已连接\n"
                "2. 摄像头是否被其他程序占用\n"
                "3. 在设置中选择正确的摄像头"
            )
            return

        self._running = True
        self.scanning_started.emit()
        frame_count = 0

        while self._running:
            ret, frame = manager.read_frame()
            if not ret or frame is None:
                self.error_occurred.emit("摄像头读取失败，请检查连接。")
                break

            frame_count += 1

            # QR scanning (skip a few frames after detection for cooldown)
            if self._scanning and self._qr_cooldown <= 0:
                decoded = self._decode_qr(frame)
                if decoded and decoded != self._last_qr:
                    self._last_qr = decoded
                    self._qr_cooldown = 30  # ~1 second at 30fps
                    self.qr_detected.emit(decoded)
                    self._scanning = False
                    continue

            if self._qr_cooldown > 0:
                self._qr_cooldown -= 1

            # Convert frame to QImage and emit
            qimage = self._frame_to_qimage(frame)
            if qimage is not None:
                self.frame_ready.emit(qimage)

            # Control frame rate (~30 fps)
            self.msleep(33)

        manager.close()
        self.scanning_stopped.emit()

    def _decode_qr(self, frame):
        """Decode QR codes from a frame. Returns URL string or None."""
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            decoded_objects = pyzbar.decode(gray)
            for obj in decoded_objects:
                data = obj.data.decode('utf-8')
                # Validate it looks like a URL
                if self._is_valid_url(data):
                    return data
        except Exception as e:
            # Silent fail for decode errors (no QR in frame is normal)
            pass
        return None

    def _is_valid_url(self, text):
        """Check if the decoded text is a valid URL."""
        try:
            result = urlparse(text)
            return all([result.scheme in ('http', 'https'), result.netloc])
        except Exception:
            return False

    def _frame_to_qimage(self, frame):
        """Convert OpenCV frame (BGR) to QImage."""
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            return QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        except Exception:
            return None

    def start_scanning(self):
        self._scanning = True
        self._last_qr = ""
        self._qr_cooldown = 0

    def stop_scanning(self):
        self._scanning = False

    def stop(self):
        self._running = False
        self._scanning = False
        self.wait(2000)


# ─── Settings Manager ───────────────────────────────────────────────────────
class SettingsManager:
    """Manages application settings with JSON persistence."""

    @staticmethod
    def load():
        """Load settings from file. Returns dict with defaults on failure."""
        defaults = {
            "camera_index": 0,
            "window_width": 1200,
            "window_height": 800
        }
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    defaults.update(saved)
        except (json.JSONDecodeError, IOError, OSError) as e:
            print(f"Settings load error: {e}")
        return defaults

    @staticmethod
    def save(settings):
        """Save settings to file."""
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            return True
        except (IOError, OSError) as e:
            print(f"Settings save error: {e}")
            return False


# ─── History Manager ────────────────────────────────────────────────────────
class HistoryManager:
    """Manages scan history with JSON persistence."""

    @staticmethod
    def load():
        """Load history from file."""
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            pass
        return []

    @staticmethod
    def save(history):
        """Save history to file."""
        try:
            # Keep only MAX_HISTORY entries
            history = history[:MAX_HISTORY]
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except (IOError, OSError) as e:
            print(f"History save error: {e}")

    @staticmethod
    def add_entry(url):
        """Add a URL to history and return updated list."""
        history = HistoryManager.load()
        entry = {
            "url": url,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        # Remove duplicate if exists
        history = [h for h in history if h.get("url") != url]
        history.insert(0, entry)
        history = history[:MAX_HISTORY]
        HistoryManager.save(history)
        return history


# ─── Settings Dialog ────────────────────────────────────────────────────────
class SettingsDialog(QDialog):
    """Settings dialog for camera selection and app configuration."""

    camera_changed = pyqtSignal(int)

    def __init__(self, current_camera_index=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(400)
        self.setModal(True)
        self.current_camera_index = current_camera_index

        self._init_ui()
        self._load_cameras()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ── Camera Settings Group ──
        camera_group = QGroupBox("摄像头设置")
        camera_layout = QVBoxLayout()

        # Camera selection
        cam_row = QHBoxLayout()
        cam_label = QLabel("选择摄像头:")
        cam_label.setMinimumWidth(100)
        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(200)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self._load_cameras)
        cam_row.addWidget(cam_label)
        cam_row.addWidget(self.camera_combo)
        cam_row.addWidget(self.refresh_btn)
        camera_layout.addLayout(cam_row)

        camera_group.setLayout(camera_layout)
        layout.addWidget(camera_group)

        # ── About Group ──
        about_group = QGroupBox("关于")
        about_layout = QVBoxLayout()
        about_text = QLabel(
            f"<b>{APP_NAME}</b><br>"
            "版本: 1.0.0<br><br>"
            "功能: 通过摄像头扫描二维码，<br>"
            "使用内置浏览器访问网址。"
        )
        about_layout.addWidget(about_text)
        about_group.setLayout(about_layout)
        layout.addWidget(about_group)

        # ── Buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.apply_btn = QPushButton("应用")
        self.apply_btn.setMinimumWidth(80)
        self.apply_btn.clicked.connect(self._apply_settings)

        self.ok_btn = QPushButton("确定")
        self.ok_btn.setMinimumWidth(80)
        self.ok_btn.clicked.connect(self._ok_settings)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setMinimumWidth(80)
        self.cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(self.ok_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        # Style
        self.setStyleSheet("""
            QDialog {
                background-color: #f5f5f5;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QPushButton {
                background-color: #4A90D9;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #357ABD;
            }
            QPushButton:pressed {
                background-color: #2868A8;
            }
            QComboBox {
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 4px 8px;
                background: white;
            }
        """)

    def _load_cameras(self):
        """Enumerate available cameras and populate dropdown."""
        self.camera_combo.clear()
        self.camera_combo.addItem("正在搜索摄像头...", -1)
        self.camera_combo.setEnabled(False)
        QApplication.processEvents()

        cameras = CameraManager.enumerate_cameras()

        self.camera_combo.clear()
        if not cameras:
            self.camera_combo.addItem("未检测到摄像头", -1)
        else:
            for idx in cameras:
                self.camera_combo.addItem(f"摄像头 {idx}", idx)
            # Select current camera
            for i in range(self.camera_combo.count()):
                if self.camera_combo.itemData(i) == self.current_camera_index:
                    self.camera_combo.setCurrentIndex(i)
                    break

        self.camera_combo.setEnabled(True)

    def _apply_settings(self):
        """Apply settings and emit signal if camera changed."""
        new_index = self.camera_combo.currentData()
        if new_index is not None and new_index != self.current_camera_index:
            self.current_camera_index = new_index
            self.camera_changed.emit(new_index)
            QMessageBox.information(self, "设置", f"已切换到摄像头 {new_index}")

    def _ok_settings(self):
        """Apply and close."""
        self._apply_settings()
        self.accept()

    def get_camera_index(self):
        return self.current_camera_index


# ─── Welcome Page ───────────────────────────────────────────────────────────
class WelcomePage(QWidget):
    """Welcome page shown on startup."""

    start_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        # Spacer top
        layout.addStretch(2)

        # Title
        title = QLabel(APP_NAME)
        title.setFont(QFont("Microsoft YaHei", 36, QFont.Bold))
        title.setStyleSheet("color: #4A90D9;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Subtitle
        subtitle = QLabel("扫描二维码 · 内置浏览器访问")
        subtitle.setFont(QFont("Microsoft YaHei", 14))
        subtitle.setStyleSheet("color: #666;")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(40)

        # Instructions
        instructions = QLabel(
            "使用说明:\n"
            "1. 点击「开始扫描」按钮启动摄像头\n"
            "2. 将二维码对准摄像头\n"
            "3. 识别成功后自动在内置浏览器中打开网址\n"
            "4. 左侧可查看扫描历史"
        )
        instructions.setFont(QFont("Microsoft YaHei", 11))
        instructions.setStyleSheet("color: #555; line-height: 1.8;")
        instructions.setAlignment(Qt.AlignCenter)
        layout.addWidget(instructions)

        layout.addSpacing(30)

        # Start button
        self.start_btn = QPushButton("📷  开始扫描")
        self.start_btn.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        self.start_btn.setMinimumSize(250, 60)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #4A90D9;
                color: white;
                border: none;
                border-radius: 30px;
                padding: 15px 40px;
            }
            QPushButton:hover {
                background-color: #357ABD;
            }
            QPushButton:pressed {
                background-color: #2868A8;
            }
        """)
        self.start_btn.clicked.connect(self.start_requested.emit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.start_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addStretch(2)

        # Background
        self.setStyleSheet("background-color: #FAFAFA;")


# ─── Main Window ────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.settings = SettingsManager.load()
        self.camera_thread = None
        self._is_scanning = False

        self._init_ui()
        self._load_history()
        self._show_welcome()

    def _init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1000, 700)
        self.resize(
            self.settings.get("window_width", 1200),
            self.settings.get("window_height", 800)
        )

        # ── Central widget with stacked layout ──
        self.central_stack = QStackedWidget()
        self.setCentralWidget(self.central_stack)

        # ── Welcome Page ──
        self.welcome_page = WelcomePage()
        self.welcome_page.start_requested.connect(self._start_scanning)
        self.central_stack.addWidget(self.welcome_page)

        # ── Main Scanner Page ──
        self.scanner_page = QWidget()
        self._init_scanner_page()
        self.central_stack.addWidget(self.scanner_page)

        # ── Toolbar ──
        self._init_toolbar()

        # ── Status Bar ──
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪 - 点击「开始扫描」启动摄像头")

        # ── Styles ──
        self._apply_styles()

    def _init_toolbar(self):
        """Create the top toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setStyleSheet("""
            QToolBar {
                background-color: #4A90D9;
                border: none;
                padding: 4px;
                spacing: 8px;
            }
            QToolBar QLabel {
                color: white;
                font-size: 16px;
                font-weight: bold;
                padding: 0 10px;
            }
        """)
        self.addToolBar(toolbar)

        # Title label
        title_label = QLabel(f"📷 {APP_NAME}")
        toolbar.addWidget(title_label)

        toolbar.addSeparator()

        # Navigation buttons for browser
        self.back_btn = QAction("◀ 后退", self)
        self.back_btn.triggered.connect(self._browser_back)
        toolbar.addAction(self.back_btn)

        self.forward_btn = QAction("前进 ▶", self)
        self.forward_btn.triggered.connect(self._browser_forward)
        toolbar.addAction(self.forward_btn)

        self.reload_btn = QAction("🔄 刷新", self)
        self.reload_btn.triggered.connect(self._browser_reload)
        toolbar.addAction(self.reload_btn)

        # URL bar
        toolbar.addSeparator()
        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("输入网址后按回车...")
        self.url_bar.setMinimumWidth(300)
        self.url_bar.setStyleSheet("""
            QLineEdit {
                background: white;
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
            }
        """)
        self.url_bar.returnPressed.connect(self._navigate_url_bar)
        toolbar.addWidget(self.url_bar)

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        # Back to scanner button
        self.scan_again_btn = QAction("📷 重新扫描", self)
        self.scan_again_btn.triggered.connect(self._start_scanning)
        toolbar.addAction(self.scan_again_btn)

        # Settings button
        self.settings_btn = QAction("⚙ 设置", self)
        self.settings_btn.triggered.connect(self._open_settings)
        toolbar.addAction(self.settings_btn)

    def _init_scanner_page(self):
        """Initialize the scanner page layout."""
        layout = QHBoxLayout(self.scanner_page)
        layout.setContentsMargins(5, 5, 5, 5)

        # ── Left Panel (Camera + History) ──
        left_panel = QWidget()
        left_panel.setMaximumWidth(380)
        left_panel.setMinimumWidth(300)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Camera preview label
        self.camera_preview = QLabel("摄像头预览")
        self.camera_preview.setAlignment(Qt.AlignCenter)
        self.camera_preview.setMinimumHeight(280)
        self.camera_preview.setMaximumHeight(350)
        self.camera_preview.setStyleSheet("""
            QLabel {
                background-color: #1a1a2e;
                color: #aaa;
                border: 2px solid #4A90D9;
                border-radius: 8px;
                font-size: 14px;
            }
        """)
        left_layout.addWidget(self.camera_preview)

        # Scan status
        self.scan_status = QLabel("● 扫描中...")
        self.scan_status.setStyleSheet("color: #4A90D9; font-weight: bold; padding: 4px;")
        self.scan_status.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(self.scan_status)

        # History section
        history_label = QLabel("📋 扫描历史")
        history_label.setStyleSheet("font-weight: bold; font-size: 13px; padding: 4px; color: #333;")
        left_layout.addWidget(history_label)

        self.history_list = QListWidget()
        self.history_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
                background: white;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 6px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:hover {
                background-color: #e8f0fe;
            }
            QListWidget::item:selected {
                background-color: #4A90D9;
                color: white;
            }
        """)
        self.history_list.itemClicked.connect(self._history_item_clicked)
        left_layout.addWidget(self.history_list)

        # Clear history button
        clear_btn = QPushButton("🗑 清除历史")
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
        """)
        clear_btn.clicked.connect(self._clear_history)
        left_layout.addWidget(clear_btn)

        # ── Right Panel (Browser) ──
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Browser view
        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("about:blank"))
        self.browser.urlChanged.connect(self._on_url_changed)
        self.browser.loadFinished.connect(self._on_load_finished)

        # Enable JavaScript and other settings
        browser_settings = self.browser.settings()
        browser_settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        browser_settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        browser_settings.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, True)

        right_layout.addWidget(self.browser)

        # Add panels to splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([350, 850])
        layout.addWidget(splitter)

    def _apply_styles(self):
        """Apply global styles."""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QStatusBar {
                background-color: #e8e8e8;
                color: #333;
                font-size: 12px;
            }
        """)

    # ─── Scanning Control ───────────────────────────────────────────────
    def _start_scanning(self):
        """Start camera scanning."""
        if self._is_scanning:
            return

        self.central_stack.setCurrentWidget(self.scanner_page)
        self.scan_status.setText("● 扫描中...请将二维码对准摄像头")
        self.scan_status.setStyleSheet("color: #27ae60; font-weight: bold; padding: 4px;")
        self.status_bar.showMessage("正在扫描...请将二维码对准摄像头")
        self.camera_preview.setText("正在启动摄像头...")

        # Create and start camera thread
        camera_index = self.settings.get("camera_index", 0)
        self.camera_thread = CameraThread(camera_index)
        self.camera_thread.frame_ready.connect(self._update_preview)
        self.camera_thread.qr_detected.connect(self._on_qr_detected)
        self.camera_thread.error_occurred.connect(self._on_camera_error)
        self.camera_thread.scanning_started.connect(self._on_scanning_started)
        self.camera_thread.scanning_stopped.connect(self._on_scanning_stopped)
        self.camera_thread.start()
        self.camera_thread.start_scanning()

        self._is_scanning = True

    def _stop_scanning(self):
        """Stop camera scanning."""
        if self.camera_thread is not None:
            self.camera_thread.stop_scanning()
            self.camera_thread.stop()
            self.camera_thread = None
        self._is_scanning = False

    def _update_preview(self, qimage):
        """Update camera preview with new frame."""
        pixmap = QPixmap.fromImage(qimage)
        # Scale to fit the preview label while maintaining aspect ratio
        scaled = pixmap.scaled(
            self.camera_preview.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.camera_preview.setPixmap(scaled)

    def _on_qr_detected(self, url):
        """Handle QR code detection."""
        self._stop_scanning()

        self.scan_status.setText(f"✓ 已识别: {url[:40]}...")
        self.scan_status.setStyleSheet("color: #27ae60; font-weight: bold; padding: 4px;")
        self.status_bar.showMessage(f"二维码识别成功: {url}")

        # Add to history
        HistoryManager.add_entry(url)
        self._load_history()

        # Navigate browser
        self.browser.setUrl(QUrl(url))
        self.url_bar.setText(url)

    def _on_camera_error(self, error_msg):
        """Handle camera errors."""
        self._stop_scanning()
        self.scan_status.setText("✗ 摄像头错误")
        self.scan_status.setStyleSheet("color: #e74c3c; font-weight: bold; padding: 4px;")
        self.camera_preview.setText("摄像头错误\n请检查设置")
        self.status_bar.showMessage("摄像头错误")

        QMessageBox.critical(self, "摄像头错误", error_msg)

    def _on_scanning_started(self):
        """Called when scanning thread starts."""
        self.status_bar.showMessage("摄像头已启动，正在扫描...")

    def _on_scanning_stopped(self):
        """Called when scanning thread stops."""
        if self._is_scanning:
            self._is_scanning = False
        self.camera_preview.setText("摄像头已关闭")

    # ─── Browser Navigation ─────────────────────────────────────────────
    def _browser_back(self):
        if self.browser.history().canGoBack():
            self.browser.back()

    def _browser_forward(self):
        if self.browser.history().canGoForward():
            self.browser.forward()

    def _browser_reload(self):
        self.browser.reload()

    def _navigate_url_bar(self):
        """Navigate to URL entered in the URL bar."""
        url = self.url_bar.text().strip()
        if not url:
            return
        # Add http:// if no scheme
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url
        self.url_bar.setText(url)
        self.browser.setUrl(QUrl(url))

    def _on_url_changed(self, url):
        """Update URL bar when browser URL changes."""
        self.url_bar.setText(url.toString())

    def _on_load_finished(self, ok):
        """Update status bar when page load finishes."""
        if ok:
            self.status_bar.showMessage(f"页面加载完成: {self.url_bar.text()}")
        else:
            self.status_bar.showMessage("页面加载失败")

    # ─── History ────────────────────────────────────────────────────────
    def _load_history(self):
        """Load scan history into the list widget."""
        self.history_list.clear()
        history = HistoryManager.load()
        for entry in history:
            url = entry.get("url", "")
            time = entry.get("time", "")
            item = QListWidgetItem()
            item.setText(f"🔗 {url}\n    {time}")
            item.setData(Qt.UserRole, url)
            item.setToolTip(url)
            self.history_list.addItem(item)

    def _history_item_clicked(self, item):
        """Handle click on history item."""
        url = item.data(Qt.UserRole)
        if url:
            self.browser.setUrl(QUrl(url))
            self.url_bar.setText(url)
            self.status_bar.showMessage(f"正在访问: {url}")

    def _clear_history(self):
        """Clear scan history."""
        reply = QMessageBox.question(
            self, "确认", "确定要清除所有扫描历史吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            HistoryManager.save([])
            self.history_list.clear()
            self.status_bar.showMessage("历史已清除")

    # ─── Settings ───────────────────────────────────────────────────────
    def _open_settings(self):
        """Open the settings dialog."""
        current_cam = self.settings.get("camera_index", 0)
        dialog = SettingsDialog(current_cam, self)
        dialog.camera_changed.connect(self._on_camera_changed)
        dialog.exec_()

    def _on_camera_changed(self, new_index):
        """Handle camera change from settings."""
        self.settings["camera_index"] = new_index
        SettingsManager.save(self.settings)

        # Restart scanning with new camera if currently scanning
        if self._is_scanning:
            self._stop_scanning()
            QTimer.singleShot(500, self._start_scanning)

    # ─── Welcome ────────────────────────────────────────────────────────
    def _show_welcome(self):
        """Show the welcome page."""
        self.central_stack.setCurrentWidget(self.welcome_page)

    # ─── Window Events ──────────────────────────────────────────────────
    def closeEvent(self, event):
        """Handle window close: stop camera and save settings."""
        self._stop_scanning()

        # Save window size
        self.settings["window_width"] = self.width()
        self.settings["window_height"] = self.height()
        SettingsManager.save(self.settings)

        event.accept()
