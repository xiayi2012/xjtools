"""Settings window and local profile editor."""

import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QStackedWidget, QLineEdit, QPushButton, QMessageBox,
)

from core.rack_inventory import atomic_json_write
from .dialog_icons import dialog_icon


def load_display_name(path):
    try:
        profile = json.loads(Path(path).read_text(encoding='utf-8'))
        name = profile.get('display_name', '')
        return name.strip()[:24] if isinstance(name, str) and name.strip() else '本地用户'
    except (OSError, ValueError, AttributeError):
        return '本地用户'


class SettingsWindow(QDialog):
    def __init__(self, model_page, schedule_page, parent=None):
        super().__init__(parent)
        self.setWindowTitle('设置')
        self.setWindowIcon(dialog_icon('robot.svg'))
        self.setObjectName('settingsWindow')
        self.resize(1120, 800)
        self.setMinimumSize(1020, 720)
        self.setStyleSheet('''
            QDialog#settingsWindow {background:#f5f7fa;}
            QListWidget#settingsCategories {background:white; color:#3c5664; border:1px solid #dfe8ed;
                border-radius:8px; padding:8px; outline:none; font-size:14px;}
            QListWidget#settingsCategories::item {padding:12px 8px; border-radius:5px; margin:3px 0;}
            QListWidget#settingsCategories::item:hover {background:#edf5f2;}
            QListWidget#settingsCategories::item:selected {background:#e2f1ec; color:#147868;}
        ''')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.categories = QListWidget()
        self.categories.setObjectName('settingsCategories')
        self.categories.setFixedWidth(155)
        for text in ('大模型配置', '定时巡检配置'):
            self.categories.addItem(QListWidgetItem(text))
        layout.addWidget(self.categories)
        self.pages = QStackedWidget()
        self.pages.addWidget(model_page)
        self.pages.addWidget(schedule_page)
        layout.addWidget(self.pages, 1)
        self.categories.currentRowChanged.connect(self._select_category)
        self.categories.setCurrentRow(0)

    def _select_category(self, index):
        if index < 0:
            return
        self.pages.setCurrentIndex(index)
        page = self.pages.currentWidget()
        if hasattr(page, 'refresh_all'):
            page.refresh_all()

    def show_section(self, section='models'):
        self.categories.setCurrentRow(1 if section == 'schedules' else 0)
        self._select_category(self.categories.currentRow())
        self.showNormal()
        self.raise_()
        self.activateWindow()


class ProfileDialog(QDialog):
    profile_saved = pyqtSignal(str)

    def __init__(self, path, name, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle('个人中心')
        self.setWindowIcon(dialog_icon('robot.svg'))
        self.setObjectName('profileDialog')
        self.setMinimumWidth(440)
        self.setStyleSheet('''
            QDialog#profileDialog {background:#f8fafc;}
            QLabel {background:transparent; color:#3c5664; font-size:13px;}
            QLineEdit {background:white; color:#263f4c; border:1px solid #cfdee6;
                border-radius:6px; padding:8px 12px; font-size:14px;}
            QLineEdit:focus {border-color:#148576;}
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        self.avatar = QLabel(name[:1])
        self.avatar.setFixedSize(64, 64)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet('background:#e2f1ec; color:#147868; border-radius:32px; font-size:26px; font-weight:600;')
        layout.addWidget(self.avatar, alignment=Qt.AlignmentFlag.AlignHCenter)
        title = QLabel('个人中心')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet('font-size:20px; font-weight:600; color:#263f4c;')
        layout.addWidget(title)
        kind = QLabel('本地用户')
        kind.setAlignment(Qt.AlignmentFlag.AlignCenter)
        kind.setStyleSheet('color:#647b89;')
        layout.addWidget(kind)
        label = QLabel('显示名称')
        layout.addWidget(label)
        self.name_input = QLineEdit(name)
        self.name_input.setMaxLength(24)
        label.setBuddy(self.name_input)
        layout.addWidget(self.name_input)
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.save_btn = QPushButton('保存')
        self.save_btn.setObjectName('saveBtn')
        self.save_btn.clicked.connect(self.save_profile)
        self.name_input.textChanged.connect(lambda text: self.save_btn.setEnabled(bool(text.strip())))
        footer.addWidget(self.save_btn)
        for button in (cancel, self.save_btn):
            button.setMinimumSize(88, 36)
        layout.addLayout(footer)

    def save_profile(self):
        name = self.name_input.text().strip()
        if not name:
            return
        try:
            atomic_json_write(self.path, {'display_name': name})
        except OSError as exc:
            QMessageBox.warning(self, '保存失败', str(exc))
            return
        self.avatar.setText(name[:1])
        self.profile_saved.emit(name)
        self.accept()
