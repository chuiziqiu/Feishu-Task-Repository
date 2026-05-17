import os
import sys
import shutil
import datetime
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QSplitter, QTreeWidget, QTreeWidgetItem,
    QListView, QTextEdit, QLabel, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QFileDialog, QMessageBox, QInputDialog, QMenu,
    QAbstractItemView, QComboBox, QSizePolicy, QHeaderView, QFrame,
    QScrollArea, QToolBar, QStatusBar, QStyle
)
from PySide6.QtCore import Qt, QSize, QThread, Signal, QFileSystemWatcher
from PySide6.QtGui import QIcon, QPixmap, QAction, QImage, QStandardItemModel, QStandardItem, QPainter, QColor, QFont

from PIL import Image, ExifTags

RAW_EXTENSIONS = {
    '.cr2', '.cr3', '.nef', '.arw', '.dng', '.raf', '.orf', '.rw2',
    '.pef', '.srw', '.x3f', '.raw', '.rwl', '.iiq', '.3fr', '.fff',
    '.mef', '.mos', '.nrw', '.qtk', '.r3d', '.sr2', '.srf', '.srw'
}

JPG_EXTENSIONS = {'.jpg', '.jpeg', '.jpe', '.jfif'}


def is_jpg_file(path):
    return Path(path).suffix.lower() in JPG_EXTENSIONS


def is_raw_file(path):
    return Path(path).suffix.lower() in RAW_EXTENSIONS


def is_image_file(path):
    return is_jpg_file(path) or is_raw_file(path)


def get_file_size_str(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


class ThumbnailLoader(QThread):
    thumbnail_loaded = Signal(str, QPixmap)

    def __init__(self, file_path, size=150):
        super().__init__()
        self.file_path = file_path
        self.size = size

    def run(self):
        try:
            pixmap = QPixmap(self.file_path)
            if pixmap.isNull():
                image = Image.open(self.file_path)
                if image.mode not in ('RGB', 'RGBA'):
                    image = image.convert('RGBA')
                data = image.tobytes("raw", "RGBA")
                qimage = QImage(data, image.size[0], image.size[1], QImage.Format_RGBA8888)
                pixmap = QPixmap.fromImage(qimage)

            if not pixmap.isNull():
                pixmap = pixmap.scaled(
                    self.size, self.size,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.thumbnail_loaded.emit(self.file_path, pixmap)
        except Exception:
            pass


STATUS_SUFFIXES = {
    "已返图": "_已返图",
    "未返图": "_未返图"
}


def get_folder_base_name(folder_path):
    folder_name = os.path.basename(folder_path)
    for status, suffix in STATUS_SUFFIXES.items():
        if folder_name.endswith(suffix):
            return folder_name[:-len(suffix)]
    return folder_name


def get_folder_status(folder_path):
    folder_name = os.path.basename(folder_path)
    for status, suffix in STATUS_SUFFIXES.items():
        if folder_name.endswith(suffix):
            return status
    return ""


class FolderTreeWidget(QTreeWidget):
    folder_selected = Signal(str)
    folder_renamed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabel("文件夹目录")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.itemClicked.connect(self.on_item_clicked)
        self.setEditTriggers(QTreeWidget.NoEditTriggers)
        self.setUniformRowHeights(True)

        self.expanded_paths = set()
        self._loading = False
        self._visited_paths = set()

        self.itemExpanded.connect(self.on_item_expanded)
        self.itemCollapsed.connect(self.on_item_collapsed)

    def on_item_expanded(self, item):
        try:
            path = item.data(0, Qt.UserRole)
            if path:
                self.expanded_paths.add(path)
                if item.childCount() == 0:
                    self.load_children(item)
        except Exception as e:
            print(f"展开项出错: {e}")

    def on_item_collapsed(self, item):
        try:
            path = item.data(0, Qt.UserRole)
            if path:
                self.expanded_paths.discard(path)
        except Exception as e:
            print(f"折叠项出错: {e}")

    def get_expanded_paths(self, item=None):
        paths = set()
        try:
            if item is None:
                for i in range(self.topLevelItemCount()):
                    paths.update(self.get_expanded_paths(self.topLevelItem(i)))
            else:
                if item.isExpanded():
                    path = item.data(0, Qt.UserRole)
                    if path:
                        paths.add(path)
                for i in range(item.childCount()):
                    paths.update(self.get_expanded_paths(item.child(i)))
        except Exception as e:
            print(f"获取展开路径出错: {e}")
        return paths

    def restore_expanded_paths(self, paths, item=None):
        try:
            if item is None:
                for i in range(self.topLevelItemCount()):
                    self.restore_expanded_paths(paths, self.topLevelItem(i))
            else:
                path = item.data(0, Qt.UserRole)
                if path and path in paths:
                    item.setExpanded(True)
                    if item.childCount() == 0:
                        self.load_children(item)
                for i in range(item.childCount()):
                    self.restore_expanded_paths(paths, item.child(i))
        except Exception as e:
            print(f"恢复展开路径出错: {e}")

    def find_sibling_folders(self, folder_path):
        siblings = []
        try:
            parent_path = os.path.dirname(folder_path)
            base_name = get_folder_base_name(folder_path)

            if not parent_path:
                return siblings

            for entry in os.listdir(parent_path):
                entry_path = os.path.join(parent_path, entry)
                try:
                    if os.path.isdir(entry_path) and entry_path != folder_path:
                        entry_base = get_folder_base_name(entry_path)
                        if entry_base == base_name:
                            siblings.append(entry_path)
                except:
                    continue
        except Exception as e:
            print(f"查找兄弟文件夹出错: {e}")
        return siblings

    def find_corresponding_folders(self, folder_path):
        folders = [folder_path]
        try:
            parent_path = os.path.dirname(folder_path)
            base_name = get_folder_base_name(folder_path)
            grandparent_path = os.path.dirname(parent_path)
            parent_name = os.path.basename(parent_path)

            if parent_name in ['jpg', 'raw']:
                for other_parent in ['jpg', 'raw']:
                    if other_parent != parent_name:
                        other_parent_path = os.path.join(grandparent_path, other_parent)
                        if os.path.isdir(other_parent_path):
                            try:
                                for entry in os.listdir(other_parent_path):
                                    entry_path = os.path.join(other_parent_path, entry)
                                    try:
                                        if os.path.isdir(entry_path):
                                            entry_base = get_folder_base_name(entry_path)
                                            if entry_base == base_name:
                                                if entry_path not in folders:
                                                    folders.append(entry_path)
                                    except:
                                        continue
                            except:
                                pass
        except Exception as e:
            print(f"查找对应文件夹出错: {e}")
        return folders

    def add_root_folder(self, folder_path):
        try:
            folder_path = os.path.abspath(folder_path)
            for i in range(self.topLevelItemCount()):
                if self.topLevelItem(i).data(0, Qt.UserRole) == folder_path:
                    return

            root_item = QTreeWidgetItem([os.path.basename(folder_path)])
            root_item.setData(0, Qt.UserRole, folder_path)
            root_item.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
            self.addTopLevelItem(root_item)

            self.expanded_paths.add(folder_path)
            self.populate_tree(root_item, folder_path)
            root_item.setExpanded(True)
        except Exception as e:
            print(f"添加根文件夹出错: {e}")
            raise

    def safe_is_dir(self, path):
        try:
            return os.path.isdir(path)
        except:
            return False

    def safe_list_dir(self, path):
        try:
            return sorted(os.listdir(path))
        except Exception:
            return []

    def has_subdirs(self, path):
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_dir():
                            return True
                    except:
                        continue
        except:
            return False
        return False

    def populate_tree(self, parent_item, folder_path, depth=0, force_expand=False):
        if depth > 15:
            return

        try:
            parent_item.takeChildren()
            entries = self.safe_list_dir(folder_path)

            dir_icon = self.style().standardIcon(QStyle.SP_DirIcon)

            for entry in entries:
                try:
                    entry_path = os.path.join(folder_path, entry)

                    if not self.safe_is_dir(entry_path):
                        continue

                    try:
                        real_path = os.path.realpath(entry_path)
                        if real_path in self._visited_paths:
                            continue
                    except:
                        pass

                    child_item = QTreeWidgetItem([entry])
                    child_item.setData(0, Qt.UserRole, entry_path)
                    child_item.setIcon(0, dir_icon)
                    parent_item.addChild(child_item)

                    should_expand = force_expand or (entry_path in self.expanded_paths)
                    if should_expand:
                        try:
                            self._visited_paths.add(os.path.realpath(entry_path))
                        except:
                            pass
                        child_item.setExpanded(True)
                        self.populate_tree(child_item, entry_path, depth + 1, force_expand)
                    else:
                        if self.has_subdirs(entry_path):
                            child_item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)

                except Exception as e:
                    continue

        except Exception as e:
            print(f"填充目录树出错 ({folder_path}): {e}")

    def load_children(self, item):
        try:
            folder_path = item.data(0, Qt.UserRole)
            if folder_path and item.childCount() == 0:
                try:
                    self._visited_paths.add(os.path.realpath(folder_path))
                except:
                    pass
                self.populate_tree(item, folder_path)
        except Exception as e:
            print(f"加载子文件夹出错: {e}")

    def refresh_tree(self):
        try:
            expanded_paths = self.get_expanded_paths()
            self._visited_paths.clear()

            for i in range(self.topLevelItemCount()):
                root_item = self.topLevelItem(i)
                folder_path = root_item.data(0, Qt.UserRole)
                try:
                    self._visited_paths.add(os.path.realpath(folder_path))
                except:
                    pass
                self.populate_tree(root_item, folder_path)

            self.restore_expanded_paths(expanded_paths)
        except Exception as e:
            print(f"刷新目录树出错: {e}")

    def on_item_clicked(self, item):
        folder_path = item.data(0, Qt.UserRole)
        if folder_path and os.path.isdir(folder_path):
            self.folder_selected.emit(folder_path)

    def show_context_menu(self, position):
        item = self.itemAt(position)
        if not item:
            return

        folder_path = item.data(0, Qt.UserRole)
        if not folder_path:
            return

        current_status = get_folder_status(folder_path)

        menu = QMenu(self)

        if current_status != "已返图":
            mark_returned = QAction("标记为已返图", self)
            mark_returned.triggered.connect(lambda: self.mark_status(folder_path, "已返图"))
            menu.addAction(mark_returned)

        if current_status != "未返图":
            mark_not_returned = QAction("标记为未返图", self)
            mark_not_returned.triggered.connect(lambda: self.mark_status(folder_path, "未返图"))
            menu.addAction(mark_not_returned)

        if current_status:
            clear_mark = QAction("清除标记", self)
            clear_mark.triggered.connect(lambda: self.mark_status(folder_path, ""))
            menu.addAction(clear_mark)

        menu.exec(self.viewport().mapToGlobal(position))

    def mark_status(self, folder_path, status):
        folders_to_rename = self.find_corresponding_folders(folder_path)

        success_count = 0
        renamed_paths = []

        for folder in folders_to_rename:
            try:
                parent_path = os.path.dirname(folder)
                base_name = get_folder_base_name(folder)

                if status:
                    new_name = base_name + STATUS_SUFFIXES[status]
                else:
                    new_name = base_name

                new_path = os.path.join(parent_path, new_name)

                if new_path != folder:
                    if os.path.exists(new_path):
                        counter = 1
                        while os.path.exists(os.path.join(parent_path, f"{new_name}_{counter}")):
                            counter += 1
                        new_path = os.path.join(parent_path, f"{new_name}_{counter}")

                    shutil.move(folder, new_path)
                    renamed_paths.append((folder, new_path))
                    success_count += 1
            except Exception as e:
                if hasattr(self, 'log_widget') and self.log_widget:
                    self.log_widget.log(f"重命名失败 {os.path.basename(folder)}: {str(e)}", "error")

        if success_count > 0:
            for old_path, new_path in renamed_paths:
                self.folder_renamed.emit(old_path, new_path)

            self.refresh_tree()

            if status:
                message = f"已为 {success_count} 个文件夹标记: {status}"
            else:
                message = f"已清除 {success_count} 个文件夹的标记"
            if hasattr(self, 'log_widget') and self.log_widget:
                self.log_widget.log(message, "success")


class ThumbnailLoaderThreadPool:
    def __init__(self, max_threads=8):
        self.max_threads = max_threads
        self.active_threads = []
        self.pending_tasks = []
        self._callback_map = {}

    def add_task(self, file_path, callback, size=150):
        self._callback_map[file_path] = callback
        self.pending_tasks.append((file_path, size))
        self._process_next()

    def _process_next(self):
        while len(self.active_threads) < self.max_threads and self.pending_tasks:
            file_path, size = self.pending_tasks.pop(0)
            loader = ThumbnailLoader(file_path, size)
            loader.thumbnail_loaded.connect(self._on_loaded)
            loader.finished.connect(lambda: self._on_thread_finished(loader))
            self.active_threads.append(loader)
            loader.start()

    def _on_loaded(self, file_path, pixmap):
        if file_path in self._callback_map:
            callback = self._callback_map[file_path]
            try:
                callback(file_path, pixmap)
            except Exception as e:
                print(f"缩略图回调出错: {e}")

    def _on_thread_finished(self, thread):
        if thread in self.active_threads:
            self.active_threads.remove(thread)
        self._process_next()

    def clear(self):
        self.pending_tasks.clear()
        self._callback_map.clear()
        for thread in self.active_threads:
            try:
                thread.quit()
                thread.requestInterruption()
            except:
                pass
        self.active_threads.clear()


class ThumbnailListView(QListView):
    files_selected = Signal(list)
    file_double_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setIconSize(QSize(150, 150))
        self.setResizeMode(QListView.Adjust)
        self.setSpacing(10)
        self.setMovement(QListView.Static)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideMiddle)
        self.setBatchSize(50)
        self.setLayoutMode(QListView.Batched)

        self.model = QStandardItemModel(self)
        self.setModel(self.model)

        self.thumbnail_cache = {}
        self.thread_pool = ThumbnailLoaderThreadPool(max_threads=4)

        self.doubleClicked.connect(self.on_double_clicked)
        self.selectionModel().selectionChanged.connect(self.on_selection_changed)

        self._raw_thumbnail = None

    def on_double_clicked(self, index):
        try:
            item = self.model.itemFromIndex(index)
            if item:
                file_path = item.data(Qt.UserRole)
                if file_path:
                    self.file_double_clicked.emit(file_path)
        except Exception as e:
            print(f"双击出错: {e}")

    def on_selection_changed(self, selected, deselected):
        try:
            selected_files = []
            for index in self.selectedIndexes():
                item = self.model.itemFromIndex(index)
                if item:
                    file_path = item.data(Qt.UserRole)
                    if file_path:
                        selected_files.append(file_path)
            self.files_selected.emit(selected_files)
        except Exception as e:
            print(f"选择变化出错: {e}")

    def get_raw_thumbnail(self):
        if self._raw_thumbnail is None:
            self._raw_thumbnail = self.create_raw_thumbnail()
        return self._raw_thumbnail

    def create_raw_thumbnail(self):
        try:
            pixmap = QPixmap(150, 150)
            pixmap.fill(QColor(80, 80, 80))

            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)

            painter.setPen(QColor(200, 200, 200))
            font = QFont("Arial", 14, QFont.Bold)
            painter.setFont(font)
            painter.drawText(pixmap.rect(), Qt.AlignCenter, "RAW")

            font = QFont("Arial", 8)
            painter.setFont(font)
            painter.setPen(QColor(150, 150, 150))
            painter.drawText(pixmap.rect().adjusted(0, 30, 0, 0), Qt.AlignCenter, "无预览")

            painter.end()
            return pixmap
        except Exception as e:
            print(f"创建RAW缩略图出错: {e}")
            return QPixmap(150, 150)

    def load_thumbnails(self, folder_path):
        try:
            self.thread_pool.clear()
            self.model.clear()
            self.thumbnail_cache = {}

            if not folder_path or not os.path.isdir(folder_path):
                return

            raw_thumbnail = self.get_raw_thumbnail()
            placeholder_pixmap = QPixmap(150, 150)
            placeholder_pixmap.fill(Qt.lightGray)
            placeholder_icon = QIcon(placeholder_pixmap)

            try:
                files = sorted(os.listdir(folder_path))
            except Exception as e:
                print(f"列出文件出错: {e}")
                return

            items_to_add = []
            jpg_files_to_load = []

            for file_name in files:
                try:
                    file_path = os.path.join(folder_path, file_name)
                    if not os.path.isfile(file_path):
                        continue

                    if is_jpg_file(file_path) or is_raw_file(file_path):
                        item = QStandardItem(file_name)
                        item.setData(file_path, Qt.UserRole)
                        item.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
                        item.setEditable(False)

                        if is_raw_file(file_path):
                            item.setIcon(QIcon(raw_thumbnail))
                        else:
                            item.setIcon(placeholder_icon)
                            jpg_files_to_load.append(file_path)

                        items_to_add.append(item)
                except Exception as e:
                    continue

            if items_to_add:
                self.model.appendColumn(items_to_add)

            for file_path in jpg_files_to_load:
                self.thread_pool.add_task(file_path, self.on_thumbnail_loaded, 150)

        except Exception as e:
            print(f"加载缩略图出错: {e}")

    def on_thumbnail_loaded(self, file_path, pixmap):
        try:
            self.thumbnail_cache[file_path] = pixmap
            for i in range(self.model.rowCount()):
                item = self.model.item(i)
                if item and item.data(Qt.UserRole) == file_path:
                    item.setIcon(QIcon(pixmap))
                    break
        except Exception as e:
            print(f"更新缩略图出错: {e}")


class ImagePreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("预览大小:"))

        self.size_combo = QComboBox()
        self.size_combo.addItems(["小", "中", "大"])
        self.size_combo.setCurrentIndex(1)
        self.size_combo.currentIndexChanged.connect(self.update_preview_size)
        toolbar.addWidget(self.size_combo)
        toolbar.addStretch()

        layout.addLayout(toolbar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignCenter)

        self.image_label = QLabel("请选择图片")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: #333; color: #888; padding: 20px;")
        self.image_label.setMinimumSize(200, 200)

        self.scroll_area.setWidget(self.image_label)
        layout.addWidget(self.scroll_area, 1)

        self.current_pixmap = None
        self.current_file = None
        self.preview_sizes = [300, 500, 800]

    def update_preview_size(self):
        if self.current_pixmap:
            self.display_pixmap()

    def create_raw_placeholder(self, file_path):
        pixmap = QPixmap(400, 300)
        pixmap.fill(Qt.darkGray)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setPen(QColor(200, 200, 200))
        font = QFont("Arial", 16, QFont.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "RAW 文件")

        font = QFont("Arial", 10)
        painter.setFont(font)
        painter.setPen(QColor(150, 150, 150))
        file_name = os.path.basename(file_path)
        painter.drawText(pixmap.rect().adjusted(0, 40, 0, 0), Qt.AlignCenter, file_name)

        painter.end()
        return pixmap

    def load_image(self, file_path):
        self.current_file = file_path

        if not is_jpg_file(file_path):
            self.current_pixmap = self.create_raw_placeholder(file_path)
            self.display_pixmap()
            return

        try:
            self.current_file = file_path
            pixmap = QPixmap(file_path)
            if pixmap.isNull():
                image = Image.open(file_path)
                if image.mode not in ('RGB', 'RGBA'):
                    image = image.convert('RGBA')
                data = image.tobytes("raw", "RGBA")
                qimage = QImage(data, image.size[0], image.size[1], QImage.Format_RGBA8888)
                pixmap = QPixmap.fromImage(qimage)
            self.current_pixmap = pixmap
            self.display_pixmap()
        except Exception as e:
            self.image_label.setText(f"无法加载图片\n{str(e)}")
            self.current_pixmap = None

    def display_pixmap(self):
        if not self.current_pixmap:
            return
        size_index = self.size_combo.currentIndex()
        max_size = self.preview_sizes[size_index]
        scaled = self.current_pixmap.scaled(
            max_size, max_size,
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())


EXIF_TRANSLATIONS = {
    'Make': '相机品牌',
    'Model': '相机型号',
    'LensMake': '镜头品牌',
    'LensModel': '镜头型号',
    'DateTime': '拍摄时间',
    'DateTimeOriginal': '原始拍摄时间',
    'DateTimeDigitized': '数字化时间',
    'ExposureTime': '快门速度',
    'FNumber': '光圈值',
    'ISOSpeedRatings': 'ISO感光度',
    'FocalLength': '焦距',
    'FocalLengthIn35mmFilm': '35mm等效焦距',
    'ExposureProgram': '曝光模式',
    'MeteringMode': '测光模式',
    'Flash': '闪光灯',
    'WhiteBalance': '白平衡',
    'ColorSpace': '色彩空间',
    'Orientation': '方向',
    'XResolution': '水平分辨率',
    'YResolution': '垂直分辨率',
    'ResolutionUnit': '分辨率单位',
    'Software': '处理软件',
    'Artist': '作者',
    'Copyright': '版权',
    'ImageDescription': '图像描述',
    'ExposureBiasValue': '曝光补偿',
    'MaxApertureValue': '最大光圈',
    'SubjectDistance': '拍摄距离',
    'LightSource': '光源',
    'Saturation': '饱和度',
    'Sharpness': '锐度',
    'Contrast': '对比度',
}

EXPOSURE_PROGRAMS = {
    0: '未定义',
    1: '手动',
    2: '程序自动',
    3: '光圈优先',
    4: '快门优先',
    5: '创意',
    6: '运动',
    7: '肖像',
    8: '风景',
}

METERING_MODES = {
    0: '未知',
    1: '平均',
    2: '中央重点平均',
    3: '点测光',
    4: '多区域',
    5: '评价',
    6: '局部',
}

FLASH_MODES = {
    0: '未触发',
    1: '触发',
    5: '触发+防红眼',
    7: '触发+防红眼',
    9: '自动触发',
    13: '自动触发+防红眼',
    16: '未触发(强制关闭)',
    24: '触发(强制打开)',
    25: '触发+反射光',
    29: '触发+反射光+防红眼',
    31: '触发+防红眼+反射光',
}

WHITE_BALANCE_MODES = {
    0: '自动',
    1: '手动',
    2: '日光',
    3: '荧光灯',
    4: '钨丝灯',
    5: '闪光灯',
    6: '阴天',
    7: '阴影',
}

ORIENTATIONS = {
    1: '正常',
    2: '水平翻转',
    3: '旋转180度',
    4: '垂直翻转',
    5: '水平翻转+逆时针90度',
    6: '顺时针90度',
    7: '水平翻转+顺时针90度',
    8: '逆时针90度',
}

COLOR_SPACES = {
    1: 'sRGB',
    2: 'Adobe RGB',
    65535: '未calibrated',
}


def format_exposure_time(value):
    try:
        if isinstance(value, tuple):
            value = value[0] / value[1]
        if value >= 1:
            return f"{value}s"
        else:
            return f"1/{int(round(1/value))}s"
    except:
        return str(value)


def format_f_number(value):
    try:
        if isinstance(value, tuple):
            value = value[0] / value[1]
        return f"f/{value:.1f}"
    except:
        return str(value)


def format_focal_length(value):
    try:
        if isinstance(value, tuple):
            value = value[0] / value[1]
        return f"{value:.0f}mm"
    except:
        return str(value)


def format_datetime(value):
    try:
        if isinstance(value, str):
            parts = value.split()
            if len(parts) == 2:
                date = parts[0].replace(':', '-')
                return f"{date} {parts[1]}"
        return str(value)
    except:
        return str(value)


def format_exposure_bias(value):
    try:
        if isinstance(value, tuple):
            value = value[0] / value[1]
        sign = '+' if value >= 0 else ''
        return f"{sign}{value:.1f} EV"
    except:
        return str(value)


class FilePropertiesWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("文件属性")
        title.setStyleSheet("font-weight: bold; padding: 5px;")
        layout.addWidget(title)

        self.properties_text = QTextEdit()
        self.properties_text.setReadOnly(True)
        self.properties_text.setMinimumHeight(200)
        layout.addWidget(self.properties_text)

    def show_properties(self, file_path):
        if not file_path or not os.path.exists(file_path):
            self.properties_text.clear()
            return

        try:
            stat = os.stat(file_path)
            path_obj = Path(file_path)

            info = "【基本信息】\n"
            info += f"文件名: {path_obj.name}\n"
            info += f"文件大小: {get_file_size_str(stat.st_size)}\n"
            info += f"文件类型: {path_obj.suffix.upper().lstrip('.')} 文件\n"
            info += f"文件路径: {file_path}\n"
            info += f"创建时间: {datetime.datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S')}\n"
            info += f"修改时间: {datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}\n"

            if is_jpg_file(file_path):
                try:
                    image = Image.open(file_path)
                    info += f"\n【图像信息】\n"
                    info += f"图像尺寸: {image.size[0]} × {image.size[1]} 像素\n"
                    info += f"宽高比: {image.size[0]/image.size[1]:.2f}:1\n"

                    exif_data = image._getexif()
                    if exif_data:
                        exif_dict = {}
                        for tag_id, value in exif_data.items():
                            tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                            exif_dict[tag] = value

                        camera_info = []
                        if 'Make' in exif_dict:
                            camera_info.append(str(exif_dict['Make']).strip('\x00 '))
                        if 'Model' in exif_dict:
                            camera_info.append(str(exif_dict['Model']).strip('\x00 '))
                        if camera_info:
                            info += f"相机: {' '.join(camera_info)}\n"

                        lens_info = []
                        if 'LensMake' in exif_dict:
                            lens_info.append(str(exif_dict['LensMake']).strip('\x00 '))
                        if 'LensModel' in exif_dict:
                            lens_info.append(str(exif_dict['LensModel']).strip('\x00 '))
                        if lens_info:
                            info += f"镜头: {' '.join(lens_info)}\n"

                        info += f"\n【拍摄参数】\n"

                        if 'ExposureTime' in exif_dict:
                            info += f"快门速度: {format_exposure_time(exif_dict['ExposureTime'])}\n"

                        if 'FNumber' in exif_dict:
                            info += f"光圈: {format_f_number(exif_dict['FNumber'])}\n"

                        if 'ISOSpeedRatings' in exif_dict:
                            iso = exif_dict['ISOSpeedRatings']
                            if isinstance(iso, tuple):
                                iso = iso[0]
                            info += f"ISO: {iso}\n"

                        if 'FocalLength' in exif_dict:
                            info += f"焦距: {format_focal_length(exif_dict['FocalLength'])}\n"

                        if 'FocalLengthIn35mmFilm' in exif_dict:
                            info += f"35mm等效焦距: {exif_dict['FocalLengthIn35mmFilm']}mm\n"

                        if 'ExposureProgram' in exif_dict:
                            prog = exif_dict['ExposureProgram']
                            info += f"曝光模式: {EXPOSURE_PROGRAMS.get(prog, str(prog))}\n"

                        if 'ExposureBiasValue' in exif_dict:
                            info += f"曝光补偿: {format_exposure_bias(exif_dict['ExposureBiasValue'])}\n"

                        if 'MeteringMode' in exif_dict:
                            mode = exif_dict['MeteringMode']
                            info += f"测光模式: {METERING_MODES.get(mode, str(mode))}\n"

                        if 'Flash' in exif_dict:
                            flash = exif_dict['Flash']
                            info += f"闪光灯: {FLASH_MODES.get(flash, str(flash))}\n"

                        if 'WhiteBalance' in exif_dict:
                            wb = exif_dict['WhiteBalance']
                            info += f"白平衡: {WHITE_BALANCE_MODES.get(wb, str(wb))}\n"

                        if 'DateTimeOriginal' in exif_dict:
                            info += f"拍摄时间: {format_datetime(exif_dict['DateTimeOriginal'])}\n"
                        elif 'DateTime' in exif_dict:
                            info += f"拍摄时间: {format_datetime(exif_dict['DateTime'])}\n"

                        if 'ColorSpace' in exif_dict:
                            cs = exif_dict['ColorSpace']
                            info += f"色彩空间: {COLOR_SPACES.get(cs, str(cs))}\n"

                        if 'Orientation' in exif_dict:
                            orient = exif_dict['Orientation']
                            info += f"拍摄方向: {ORIENTATIONS.get(orient, str(orient))}\n"

                        if 'Software' in exif_dict:
                            info += f"处理软件: {str(exif_dict['Software']).strip()}\n"

                        info += "\n【所有EXIF数据】\n"
                        for tag_id, value in sorted(exif_dict.items()):
                            if isinstance(value, bytes):
                                try:
                                    value = value.decode('utf-8', errors='ignore').strip()
                                except:
                                    value = str(value)
                            if not isinstance(value, (int, float, str)):
                                value = str(value)
                            if len(str(value)) > 100:
                                value = str(value)[:100] + "..."

                            cn_name = EXIF_TRANSLATIONS.get(tag_id, tag_id)
                            if cn_name != tag_id:
                                info += f"{cn_name} ({tag_id}): {value}\n"
                            else:
                                info += f"{tag_id}: {value}\n"

                    else:
                        info += "\n无EXIF信息\n"

                except Exception as e:
                    info += f"\n读取图像信息失败: {str(e)}\n"
            elif is_raw_file(file_path):
                info += "\n【RAW文件】\n"
                info += "RAW格式文件不支持预览和EXIF信息读取\n"
                info += "请使用专业RAW处理软件查看详细信息\n"

            self.properties_text.setText(info)
        except Exception as e:
            self.properties_text.setText(f"无法获取属性: {str(e)}")


class LogWidget(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumHeight(150)

    def log(self, message, level="info"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        color_map = {
            "info": "#222",
            "success": "#0a7d0a",
            "error": "#c62828",
            "warning": "#ef6c00"
        }
        color = color_map.get(level, "#222")
        self.append(f'<span style="color: {color};">[{timestamp}] {message}</span>')


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("照片管理器")
        self.resize(1400, 900)

        self.current_folder = None
        self.selected_files = []

        self.create_ui()
        self.create_connections()

    def create_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        toolbar = QToolBar("工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        add_folder_action = QAction("添加文件夹", self)
        add_folder_action.triggered.connect(self.add_folder)
        toolbar.addAction(add_folder_action)

        toolbar.addSeparator()

        classify_action = QAction("一键分类", self)
        classify_action.triggered.connect(self.auto_classify)
        toolbar.addAction(classify_action)

        new_category_action = QAction("新建分类", self)
        new_category_action.triggered.connect(self.new_category)
        toolbar.addAction(new_category_action)

        sync_action = QAction("一键同步分类", self)
        sync_action.triggered.connect(self.sync_categories)
        toolbar.addAction(sync_action)

        main_splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(5)

        left_title = QLabel("目录结构")
        left_title.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(left_title)

        self.folder_tree = FolderTreeWidget()
        left_layout.addWidget(self.folder_tree, 1)

        left_panel.setMinimumWidth(200)
        main_splitter.addWidget(left_panel)

        middle_panel = QWidget()
        middle_layout = QVBoxLayout(middle_panel)
        middle_layout.setContentsMargins(5, 5, 5, 5)
        middle_layout.setSpacing(5)

        middle_title = QLabel("照片预览")
        middle_title.setStyleSheet("font-weight: bold;")
        middle_layout.addWidget(middle_title)

        self.thumbnail_view = ThumbnailListView()
        middle_layout.addWidget(self.thumbnail_view, 1)

        log_title = QLabel("操作日志")
        log_title.setStyleSheet("font-weight: bold;")
        middle_layout.addWidget(log_title)

        self.log_widget = LogWidget()
        middle_layout.addWidget(self.log_widget)

        self.folder_tree.log_widget = self.log_widget

        middle_panel.setMinimumWidth(400)
        main_splitter.addWidget(middle_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(5)

        self.preview_widget = ImagePreviewWidget()
        right_layout.addWidget(self.preview_widget, 1)

        self.properties_widget = FilePropertiesWidget()
        right_layout.addWidget(self.properties_widget)

        right_panel.setMinimumWidth(300)
        main_splitter.addWidget(right_panel)

        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 3)
        main_splitter.setStretchFactor(2, 1)
        main_splitter.setSizes([250, 700, 450])

        main_layout.addWidget(main_splitter, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

    def create_connections(self):
        self.folder_tree.folder_selected.connect(self.on_folder_selected)
        self.thumbnail_view.files_selected.connect(self.on_files_selected)
        self.thumbnail_view.file_double_clicked.connect(self.open_file)

    def add_folder(self):
        try:
            folder_path = QFileDialog.getExistingDirectory(self, "选择文件夹")
            if folder_path:
                folder_path = str(folder_path).strip()
                if os.path.isdir(folder_path):
                    self.folder_tree.add_root_folder(folder_path)
                    self.log_widget.log(f"已添加文件夹: {folder_path}", "success")
                    self.status_bar.showMessage(f"已添加: {folder_path}")
                else:
                    self.log_widget.log(f"无效的文件夹路径: {folder_path}", "error")
                    QMessageBox.warning(self, "错误", f"无效的文件夹路径: {folder_path}")
        except Exception as e:
            self.log_widget.log(f"添加文件夹失败: {str(e)}", "error")
            QMessageBox.critical(self, "错误", f"添加文件夹失败: {str(e)}")

    def on_folder_selected(self, folder_path):
        self.current_folder = folder_path
        self.thumbnail_view.load_thumbnails(folder_path)
        self.status_bar.showMessage(f"当前文件夹: {folder_path}")

    def on_files_selected(self, files):
        self.selected_files = files
        if files:
            self.preview_widget.load_image(files[0])
            self.properties_widget.show_properties(files[0])
            self.status_bar.showMessage(f"已选择 {len(files)} 个文件")
        else:
            self.preview_widget.image_label.setText("请选择图片")
            self.preview_widget.current_pixmap = None
            self.properties_widget.show_properties(None)

    def open_file(self, file_path):
        try:
            if sys.platform == 'win32':
                os.startfile(file_path)
            elif sys.platform == 'darwin':
                subprocess.run(['open', file_path])
            else:
                subprocess.run(['xdg-open', file_path])
            self.log_widget.log(f"已打开: {os.path.basename(file_path)}", "info")
        except Exception as e:
            self.log_widget.log(f"打开文件失败: {str(e)}", "error")

    def find_classify_target_folder(self, current_path):
        check_path = current_path
        while check_path:
            has_files = False
            has_jpg = False
            has_raw = False

            try:
                for entry in os.listdir(check_path):
                    entry_path = os.path.join(check_path, entry)
                    if os.path.isfile(entry_path):
                        suffix = Path(entry).suffix.lower()
                        if suffix in JPG_EXTENSIONS:
                            has_jpg = True
                        elif suffix in RAW_EXTENSIONS:
                            has_raw = True
                        has_files = True
                    elif entry in ['jpg', 'raw', '其他'] and os.path.isdir(entry_path):
                        return check_path

                if has_files and (has_jpg or has_raw):
                    return check_path
            except:
                pass

            parent = os.path.dirname(check_path)
            if parent == check_path:
                break
            check_path = parent

        return current_path

    def auto_classify(self):
        if not self.current_folder:
            QMessageBox.warning(self, "提示", "请先选择一个文件夹")
            return

        target_folder = self.find_classify_target_folder(self.current_folder)

        jpg_folder = os.path.join(target_folder, "jpg")
        raw_folder = os.path.join(target_folder, "raw")
        other_folder = os.path.join(target_folder, "其他")

        os.makedirs(jpg_folder, exist_ok=True)
        os.makedirs(raw_folder, exist_ok=True)
        os.makedirs(other_folder, exist_ok=True)

        jpg_count = 0
        raw_count = 0
        other_count = 0

        try:
            for file_name in os.listdir(target_folder):
                file_path = os.path.join(target_folder, file_name)
                if not os.path.isfile(file_path):
                    continue

                suffix = Path(file_name).suffix.lower()
                dest_folder = None

                if suffix in JPG_EXTENSIONS:
                    dest_folder = jpg_folder
                    jpg_count += 1
                elif suffix in RAW_EXTENSIONS:
                    dest_folder = raw_folder
                    raw_count += 1
                else:
                    dest_folder = other_folder
                    other_count += 1

                if dest_folder:
                    dest_path = os.path.join(dest_folder, file_name)
                    if os.path.exists(dest_path):
                        base, ext = os.path.splitext(file_name)
                        counter = 1
                        while os.path.exists(os.path.join(dest_folder, f"{base}_{counter}{ext}")):
                            counter += 1
                        dest_path = os.path.join(dest_folder, f"{base}_{counter}{ext}")

                    shutil.move(file_path, dest_path)

            self.folder_tree.refresh_tree()
            self.thumbnail_view.load_thumbnails(self.current_folder)

            message = f"分类完成！JPG: {jpg_count}张, RAW: {raw_count}张, 其他: {other_count}个"
            if target_folder != self.current_folder:
                message += f"\n(自动定位到: {os.path.basename(target_folder)})"
            self.log_widget.log(message, "success")
            QMessageBox.information(self, "完成", message)

        except Exception as e:
            self.log_widget.log(f"分类失败: {str(e)}", "error")
            QMessageBox.critical(self, "错误", f"分类失败: {str(e)}")

    def new_category(self):
        if not self.selected_files:
            QMessageBox.warning(self, "提示", "请先选择要分类的JPG文件")
            return

        jpg_files = [f for f in self.selected_files if is_jpg_file(f)]
        if not jpg_files:
            QMessageBox.warning(self, "提示", "请选择JPG格式的文件")
            return

        folder_name, ok = QInputDialog.getText(self, "新建分类", "请输入文件夹名称:")
        if not ok:
            return

        if not folder_name.strip():
            folder_name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        parent_folder = os.path.dirname(jpg_files[0])
        new_folder = os.path.join(parent_folder, folder_name.strip())

        try:
            os.makedirs(new_folder, exist_ok=True)
            moved_count = 0

            for file_path in jpg_files:
                file_name = os.path.basename(file_path)
                dest_path = os.path.join(new_folder, file_name)
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(file_name)
                    counter = 1
                    while os.path.exists(os.path.join(new_folder, f"{base}_{counter}{ext}")):
                        counter += 1
                    dest_path = os.path.join(new_folder, f"{base}_{counter}{ext}")

                shutil.move(file_path, dest_path)
                moved_count += 1

            self.folder_tree.refresh_tree()
            self.thumbnail_view.load_thumbnails(parent_folder)

            message = f"已移动 {moved_count} 个文件到 '{folder_name}' 文件夹"
            self.log_widget.log(message, "success")
            QMessageBox.information(self, "完成", message)

        except Exception as e:
            self.log_widget.log(f"新建分类失败: {str(e)}", "error")
            QMessageBox.critical(self, "错误", f"新建分类失败: {str(e)}")

    def find_sync_target(self, current_path):
        check_path = current_path
        while check_path:
            jpg_folder = os.path.join(check_path, "jpg")
            raw_folder = os.path.join(check_path, "raw")
            if os.path.exists(jpg_folder) and os.path.exists(raw_folder):
                return check_path, jpg_folder, raw_folder

            parent = os.path.dirname(check_path)
            if parent == check_path:
                break
            check_path = parent

        return None, None, None

    def get_sync_scope(self, current_path, jpg_folder, raw_folder):
        current_base = os.path.basename(current_path)
        current_parent = os.path.dirname(current_path)
        current_parent_base = os.path.basename(current_parent)

        scope_jpg = None
        scope_raw = None

        if current_parent_base == "jpg":
            rel_path = os.path.relpath(current_path, jpg_folder)
            scope_jpg = current_path
            scope_raw = os.path.join(raw_folder, rel_path)
        elif current_parent_base == "raw":
            rel_path = os.path.relpath(current_path, raw_folder)
            scope_jpg = os.path.join(jpg_folder, rel_path)
            scope_raw = current_path
        elif current_base == "jpg":
            scope_jpg = current_path
            scope_raw = raw_folder
        elif current_base == "raw":
            scope_jpg = jpg_folder
            scope_raw = current_path
        else:
            scope_jpg = jpg_folder
            scope_raw = raw_folder

        return scope_jpg, scope_raw

    def sync_categories(self):
        if not self.current_folder:
            QMessageBox.warning(self, "提示", "请先选择一个文件夹")
            return

        parent_folder, jpg_folder, raw_folder = self.find_sync_target(self.current_folder)

        if not parent_folder:
            QMessageBox.warning(self, "提示", "未找到包含jpg和raw文件夹的父目录\n请先执行一键分类")
            return

        scope_jpg, scope_raw = self.get_sync_scope(self.current_folder, jpg_folder, raw_folder)

        if not os.path.exists(scope_jpg):
            QMessageBox.warning(self, "提示", "未找到对应jpg文件夹")
            return

        if not os.path.exists(scope_raw):
            os.makedirs(scope_raw, exist_ok=True)

        synced_count = 0
        try:
            for root, dirs, files in os.walk(scope_jpg):
                rel_path = os.path.relpath(root, jpg_folder)
                if rel_path == '.':
                    continue

                target_raw_dir = os.path.join(raw_folder, rel_path)
                os.makedirs(target_raw_dir, exist_ok=True)

                for file_name in files:
                    if is_jpg_file(file_name):
                        base_name = os.path.splitext(file_name)[0]
                        for ext in RAW_EXTENSIONS:
                            raw_file = os.path.join(raw_folder, base_name + ext)
                            if os.path.exists(raw_file):
                                dest_path = os.path.join(target_raw_dir, base_name + ext)
                                if os.path.exists(dest_path):
                                    counter = 1
                                    while os.path.exists(os.path.join(target_raw_dir, f"{base_name}_{counter}{ext}")):
                                        counter += 1
                                    dest_path = os.path.join(target_raw_dir, f"{base_name}_{counter}{ext}")

                                shutil.move(raw_file, dest_path)
                                synced_count += 1
                                self.log_widget.log(f"同步: {base_name}{ext} -> {rel_path}", "info")
                                break

            self.folder_tree.refresh_tree()
            message = f"同步完成！共移动 {synced_count} 个RAW文件"
            if scope_jpg != jpg_folder or scope_raw != raw_folder:
                message += f"\n(同步范围: {os.path.basename(scope_jpg)})"
            self.log_widget.log(message, "success")
            QMessageBox.information(self, "完成", message)

        except Exception as e:
            self.log_widget.log(f"同步失败: {str(e)}", "error")
            QMessageBox.critical(self, "错误", f"同步失败: {str(e)}")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
