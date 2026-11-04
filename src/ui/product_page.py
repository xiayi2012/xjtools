"""
产品选择页面模块
设备资产、连接和机柜位置管理。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QScrollArea, QFrame,
    QDialog, QLineEdit, QSpinBox, QCheckBox, QMenu, QToolButton, QButtonGroup,
    QComboBox, QMessageBox, QSizePolicy, QFileDialog, QTimeEdit, QTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QTime, QSize
from qfluentwidgets import PrimaryPushButton, ToolButton, FluentIcon as FIF

import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import base64
import hashlib
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from utils import get_config_dir as _get_config_dir
from .dialog_icons import dialog_icon
from core.rack_inventory import InventoryStore, placement_error, recommend_slot
from .rack_view import RackView, RackDialog


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def get_config_dir() -> Path:
    return Path(_get_config_dir())


def get_encryption_key() -> bytes:
    config_dir = get_config_dir()
    key_file = config_dir / ".key"
    
    if key_file.exists():
        return key_file.read_bytes()
    
    salt = hashlib.sha256(os.environ.get('COMPUTERNAME', 'default').encode()).digest()[:16]
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    machine_id = os.environ.get('COMPUTERNAME', 'default')
    key = base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))
    
    key_file.write_bytes(key)
    
    return key


def encrypt_password(password: str) -> str:
    """加密密码"""
    try:
        key = get_encryption_key()
        f = Fernet(key)
        return base64.urlsafe_b64encode(f.encrypt(password.encode())).decode()
    except Exception:
        return base64.b64encode(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """解密密码"""
    try:
        key = get_encryption_key()
        f = Fernet(key)
        return f.decrypt(base64.urlsafe_b64decode(encrypted.encode())).decode()
    except Exception:
        try:
            return base64.b64decode(encrypted.encode()).decode()
        except Exception:
            return ""


def normalize_server_record(server: dict) -> dict:
    normalized = dict(server)
    password = normalized.pop("password", "")
    if password and not normalized.get("encrypted_password"):
        normalized["encrypted_password"] = encrypt_password(password)
    return normalized


class AddServerDialog(QDialog):
    """添加/编辑服务器对话框"""
    
    def __init__(self, server_data: dict = None, parent=None, racks=None, servers=None, placement=None):
        super().__init__(parent)
        self.server_data = server_data or {}
        self.is_edit = bool(server_data)
        self.racks = racks if racks is not None else getattr(parent, 'racks', [])
        self.servers = servers if servers is not None else getattr(parent, 'servers', [])
        
        self.setWindowTitle("编辑服务器" if self.is_edit else "添加服务器")
        self.setWindowIcon(dialog_icon("product.svg"))
        self.setMinimumWidth(450)
        self.setMinimumHeight(380)
        self.setModal(True)
        
        self.init_ui()
        
        if self.is_edit:
            self.load_server_data()
        position = placement or self.server_data
        index = self.rack_combo.findData(position.get('rack_id'))
        self.rack_combo.setCurrentIndex(max(0, index))
        self.rack_start.setValue(position.get('rack_start_u', 1) or 1)
        self.rack_height.setValue(position.get('rack_height_u', 1) or 1)
        self.update_position_preview()
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        
        # 产品选择
        product_layout = QHBoxLayout()
        product_label = QLabel("产品:")
        product_label.setMinimumWidth(70)
        product_layout.addWidget(product_label)
        
        self.product_combo = QComboBox()
        self.product_combo.setMinimumHeight(32)
        self.product_combo.setEditable(True)
        self.product_combo.addItems(["准入系统", "EDR", "零信任"])
        product_layout.addWidget(self.product_combo, 1)
        layout.addLayout(product_layout)
        
        # 服务器名称
        name_layout = QHBoxLayout()
        name_label = QLabel("名称:")
        name_label.setMinimumWidth(70)
        name_layout.addWidget(name_label)
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("服务器备注名(可选)")
        self.name_input.setMinimumHeight(32)
        name_layout.addWidget(self.name_input, 1)
        layout.addLayout(name_layout)
        
        # 主机地址
        host_layout = QHBoxLayout()
        host_label = QLabel("主机地址:")
        host_label.setMinimumWidth(70)
        host_layout.addWidget(host_label)
        
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("例如: 192.168.1.100")
        self.host_input.setMinimumHeight(32)
        host_layout.addWidget(self.host_input, 1)
        layout.addLayout(host_layout)
        
        # 端口
        port_layout = QHBoxLayout()
        port_label = QLabel("端口:")
        port_label.setMinimumWidth(70)
        port_layout.addWidget(port_label)
        
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(22)
        self.port_input.setMinimumHeight(32)
        port_layout.addWidget(self.port_input)
        port_layout.addStretch(1)
        layout.addLayout(port_layout)
        
        # 用户名
        user_layout = QHBoxLayout()
        user_label = QLabel("用户名:")
        user_label.setMinimumWidth(70)
        user_layout.addWidget(user_label)
        
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("例如: root")
        self.username_input.setMinimumHeight(32)
        user_layout.addWidget(self.username_input, 1)
        layout.addLayout(user_layout)
        
        # 密码
        pwd_layout = QHBoxLayout()
        pwd_label = QLabel("密码:")
        pwd_label.setMinimumWidth(70)
        pwd_layout.addWidget(pwd_label)
        
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("输入密码")
        self.password_input.setMinimumHeight(32)
        pwd_layout.addWidget(self.password_input, 1)
        layout.addLayout(pwd_layout)
        
        # 支持切换root
        self.switch_root_checkbox = QCheckBox("需要切换root")
        self.switch_root_checkbox.setChecked(False)
        layout.addWidget(self.switch_root_checkbox)

        self.rack_combo = QComboBox()
        self.rack_combo.addItem('暂不上架', None)
        for rack in self.racks:
            self.rack_combo.addItem(f'{rack.get("code", "")} · {rack.get("name", "")} ({rack.get("units")}U)', rack['id'])
        layout.addWidget(QLabel('机柜位置'))
        layout.addWidget(self.rack_combo)
        position_row = QHBoxLayout()
        self.rack_start = QSpinBox()
        self.rack_start.setRange(1, 60)
        self.rack_start.setPrefix('起始 U ')
        self.rack_height = QSpinBox()
        self.rack_height.setRange(1, 60)
        self.rack_height.setPrefix('高度 ')
        self.rack_height.setSuffix(' U')
        self.recommend_btn = QPushButton('推荐空位')
        self.recommend_btn.clicked.connect(self.recommend_position)
        for field in (self.rack_start, self.rack_height, self.recommend_btn):
            position_row.addWidget(field)
        layout.addLayout(position_row)
        self.position_preview = QLabel()
        self.position_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.position_preview.setWordWrap(True)
        layout.addWidget(self.position_preview)
        self.rack_combo.currentIndexChanged.connect(self.update_position_preview)
        self.rack_start.valueChanged.connect(self.update_position_preview)
        self.rack_height.valueChanged.connect(self.update_position_preview)
        

        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.ok_btn = PrimaryPushButton("确定")
        self.ok_btn.setMinimumHeight(35)
        self.ok_btn.setFixedWidth(100)
        self.ok_btn.clicked.connect(self.validate_and_accept)
        btn_layout.addWidget(self.ok_btn)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(35)
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
    
    def load_server_data(self):
        """加载服务器数据到表单"""
        if not self.server_data:
            return
        
        self.product_combo.setCurrentText(self.server_data.get("product", ""))
        self.name_input.setText(self.server_data.get("name", ""))
        self.host_input.setText(self.server_data.get("host", ""))
        self.port_input.setValue(self.server_data.get("port", 22))
        self.username_input.setText(self.server_data.get("username", ""))
        
        encrypted = self.server_data.get("encrypted_password", "")
        if encrypted:
            password = decrypt_password(encrypted)
            self.password_input.setText(password)
        elif self.server_data.get("password"):
            self.password_input.setText(self.server_data.get("password", ""))
        
        self.switch_root_checkbox.setChecked(self.server_data.get("switch_root", True))
    
    def validate_and_accept(self):
        """验证并接受"""
        product = self.product_combo.currentText().strip()
        host = self.host_input.text().strip()
        username = self.username_input.text().strip()
        password = self.password_input.text()
        
        if not product:
            QMessageBox.warning(self, "输入错误", "请选择或输入产品名称")
            return
        
        if not host:
            QMessageBox.warning(self, "输入错误", "请输入主机地址")
            return
        
        if not username:
            QMessageBox.warning(self, "输入错误", "请输入用户名")
            return
        
        if not password and not self.is_edit:
            QMessageBox.warning(self, "输入错误", "请输入密码")
            return
        
        error = placement_error(self.position_info(), self.racks, self.servers)
        if error:
            QMessageBox.warning(self, '位置无效', error)
            return
        self.accept()

    def position_info(self):
        return dict(id=self.server_data.get('id'), rack_id=self.rack_combo.currentData(),
                    rack_start_u=self.rack_start.value(), rack_height_u=self.rack_height.value())

    def update_position_preview(self, *args):
        positioned = bool(self.rack_combo.currentData())
        for field in (self.rack_start, self.rack_height, self.recommend_btn):
            field.setEnabled(positioned)
        start, height = self.rack_start.value(), self.rack_height.value()
        error = placement_error(self.position_info(), self.racks, self.servers)
        self.position_preview.setText(error if error else (f'占用 U{start:02}–U{start+height-1:02}，共 {height}U（起始 U 为底部）' if positioned else '保存在待上架区，可稍后安排位置'))
        self.position_preview.setStyleSheet('color:#b45309;' if error else 'color:#687e89;')

    def recommend_position(self):
        rack = next((r for r in self.racks if r.get('id') == self.rack_combo.currentData()), None)
        if rack:
            start = recommend_slot(rack, self.servers, self.rack_height.value(), self.server_data.get('id'))
            if start is None:
                QMessageBox.warning(self, '无可用位置', '该机柜没有足够的连续空位，请选择其他机柜或减少高度')
            else:
                self.rack_start.setValue(start)
    
    def get_server_info(self) -> dict:
        """获取服务器信息"""
        password = self.password_input.text()
        encrypted_password = encrypt_password(password) if password else self.server_data.get("encrypted_password", "")
        return {
            **self.server_data,
            "product": self.product_combo.currentText().strip(),
            "name": self.name_input.text().strip(),
            "host": self.host_input.text().strip(),
            "port": self.port_input.value(),
            "username": self.username_input.text().strip(),
            "encrypted_password": encrypted_password,
            "switch_root": self.switch_root_checkbox.isChecked(),
            "id": self.server_data.get("id", None) or f"{self.host_input.text().strip()}_{self.port_input.value()}_{self.username_input.text().strip()}",
            'rack_id': self.rack_combo.currentData(),
            'rack_start_u': self.rack_start.value(),
            'rack_height_u': self.rack_height.value(),
        }
    
    def open_script_library(self):
        """打开脚本库对话框"""
        from .script_library import ScriptLibraryDialog
        product = self.product_combo.currentText().strip() or "通用"
        dialog = ScriptLibraryDialog(product, self)
        dialog.exec()


class ScheduleInspectionDialog(QDialog):
    """定时巡检设置弹窗。"""

    def __init__(self, servers: list, parent=None):
        super().__init__(parent)
        self.servers = servers
        self.setWindowTitle("定时巡检设置")
        self.setWindowIcon(dialog_icon("xunjian.svg"))
        self.setMinimumWidth(760)
        self.resize(760, 430)
        self.init_ui()
        self.load_schedule()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        title = QLabel("定时巡检")
        title.setObjectName("scheduleTitle")
        layout.addWidget(title)

        form_card = QFrame()
        form_card.setObjectName("scheduleCard")
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(18, 16, 18, 16)
        form_layout.setSpacing(12)

        server_row = QHBoxLayout()
        server_label = QLabel("服务器:")
        server_label.setObjectName("formLabel")
        server_label.setMinimumWidth(112)
        server_row.addWidget(server_label)
        self.server_combo = QComboBox()
        self.server_combo.setMinimumHeight(36)
        for server in self.servers:
            text = f"{server.get('name') or server.get('host')} ({server.get('host')}:{server.get('port', 22)})"
            self.server_combo.addItem(text, server.get("id"))
        self.server_combo.currentIndexChanged.connect(self.load_schedule)
        server_row.addWidget(self.server_combo, 1)
        form_layout.addLayout(server_row)

        enable_row = QHBoxLayout()
        enable_row.addSpacing(112)
        self.enabled_check = QCheckBox("启用该服务器的定时巡检")
        enable_row.addWidget(self.enabled_check)
        enable_row.addStretch()
        form_layout.addLayout(enable_row)

        frequency_row = QHBoxLayout()
        frequency_label = QLabel("巡检频率:")
        frequency_label.setObjectName("formLabel")
        frequency_label.setMinimumWidth(112)
        frequency_row.addWidget(frequency_label)
        self.frequency_combo = QComboBox()
        self.frequency_combo.setMinimumHeight(36)
        self.frequency_combo.addItem("每天", "daily")
        self.frequency_combo.addItem("每周", "weekly")
        self.frequency_combo.addItem("每月", "monthly")
        self.frequency_combo.currentIndexChanged.connect(self._update_frequency_fields)
        frequency_row.addWidget(self.frequency_combo, 1)
        form_layout.addLayout(frequency_row)

        self.daily_mode_row_widget = QWidget()
        daily_mode_row = QHBoxLayout(self.daily_mode_row_widget)
        daily_mode_row.setContentsMargins(0, 0, 0, 0)
        daily_mode_label = QLabel("方式:")
        daily_mode_label.setObjectName("formLabel")
        daily_mode_label.setMinimumWidth(112)
        daily_mode_row.addWidget(daily_mode_label)
        self.daily_mode_combo = QComboBox()
        self.daily_mode_combo.setMinimumHeight(36)
        self.daily_mode_combo.addItem("间隔分钟", "interval_minutes")
        self.daily_mode_combo.addItem("固定时间点", "times")
        self.daily_mode_combo.currentIndexChanged.connect(self._update_frequency_fields)
        daily_mode_row.addWidget(self.daily_mode_combo, 1)
        form_layout.addWidget(self.daily_mode_row_widget)

        self.minute_interval_row_widget = QWidget()
        minute_interval_row = QHBoxLayout(self.minute_interval_row_widget)
        minute_interval_row.setContentsMargins(0, 0, 0, 0)
        minute_label = QLabel("间隔分钟:")
        minute_label.setObjectName("formLabel")
        minute_label.setMinimumWidth(112)
        minute_interval_row.addWidget(minute_label)
        self.interval_minutes_spin = QSpinBox()
        self.interval_minutes_spin.setRange(1, 1440)
        self.interval_minutes_spin.setValue(60)
        self.interval_minutes_spin.setSuffix(" 分钟")
        self.interval_minutes_spin.setMinimumHeight(36)
        minute_interval_row.addWidget(self.interval_minutes_spin)
        minute_interval_row.addStretch()
        form_layout.addWidget(self.minute_interval_row_widget)

        self.weekly_row_widget = QWidget()
        weekly_row = QHBoxLayout(self.weekly_row_widget)
        weekly_row.setContentsMargins(0, 0, 0, 0)
        weekly_label = QLabel("每周日期:")
        weekly_label.setObjectName("formLabel")
        weekly_label.setMinimumWidth(112)
        weekly_row.addWidget(weekly_label)
        self.weekday_checks = []
        for value, text in enumerate(["周一", "周二", "周三", "周四", "周五", "周六", "周日"], 1):
            check = QCheckBox(text)
            check.setProperty("weekday", value)
            self.weekday_checks.append(check)
            weekly_row.addWidget(check)
        weekly_row.addStretch()
        form_layout.addWidget(self.weekly_row_widget)

        self.monthly_mode_row_widget = QWidget()
        monthly_mode_row = QHBoxLayout(self.monthly_mode_row_widget)
        monthly_mode_row.setContentsMargins(0, 0, 0, 0)
        monthly_mode_label = QLabel("每月方式:")
        monthly_mode_label.setObjectName("formLabel")
        monthly_mode_label.setMinimumWidth(112)
        monthly_mode_row.addWidget(monthly_mode_label)
        self.monthly_mode_combo = QComboBox()
        self.monthly_mode_combo.setMinimumHeight(36)
        self.monthly_mode_combo.addItem("每月指定日期", "month_days")
        self.monthly_mode_combo.addItem("每隔几天", "interval_days")
        self.monthly_mode_combo.currentIndexChanged.connect(self._update_frequency_fields)
        monthly_mode_row.addWidget(self.monthly_mode_combo, 1)
        form_layout.addWidget(self.monthly_mode_row_widget)

        self.monthly_row_widget = QWidget()
        monthly_row = QHBoxLayout(self.monthly_row_widget)
        monthly_row.setContentsMargins(0, 0, 0, 0)
        monthly_label = QLabel("每月日期:")
        monthly_label.setObjectName("formLabel")
        monthly_label.setMinimumWidth(112)
        monthly_row.addWidget(monthly_label)
        self.month_days_input = QLineEdit()
        self.month_days_input.setMinimumHeight(36)
        self.month_days_input.setPlaceholderText("例如：1，15，31")
        monthly_row.addWidget(self.month_days_input, 1)
        monthly_hint = QLabel("如果当月没有该日期，将在当月最后一天执行")
        monthly_hint.setStyleSheet("color: #64748b; font-size: 12px;")
        monthly_row.addWidget(monthly_hint)
        form_layout.addWidget(self.monthly_row_widget)

        self.interval_days_row_widget = QWidget()
        interval_days_row = QHBoxLayout(self.interval_days_row_widget)
        interval_days_row.setContentsMargins(0, 0, 0, 0)
        interval_days_label = QLabel("间隔天数:")
        interval_days_label.setObjectName("formLabel")
        interval_days_label.setMinimumWidth(112)
        interval_days_row.addWidget(interval_days_label)
        self.interval_days_spin = QSpinBox()
        self.interval_days_spin.setRange(1, 365)
        self.interval_days_spin.setValue(1)
        self.interval_days_spin.setSuffix(" 天")
        self.interval_days_spin.setMinimumHeight(36)
        interval_days_row.addWidget(self.interval_days_spin)
        interval_days_row.addStretch()
        form_layout.addWidget(self.interval_days_row_widget)

        self.times_row_widget = QWidget()
        times_outer_layout = QVBoxLayout(self.times_row_widget)
        times_outer_layout.setContentsMargins(0, 0, 0, 0)
        times_outer_layout.setSpacing(10)
        times_row = QHBoxLayout()
        times_row.setContentsMargins(0, 0, 0, 0)
        times_label = QLabel("时间点:")
        times_label.setObjectName("formLabel")
        times_label.setMinimumWidth(112)
        times_row.addWidget(times_label)
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setTime(QTime(2, 0))
        self.time_edit.setMinimumHeight(36)
        times_row.addWidget(self.time_edit)
        add_time_btn = QPushButton("添加时间点")
        add_time_btn.setObjectName("secondaryBtn")
        add_time_btn.setMinimumHeight(36)
        add_time_btn.clicked.connect(self.add_time_tag)
        times_row.addWidget(add_time_btn)
        times_row.addStretch()
        times_outer_layout.addLayout(times_row)
        self.time_tags = []
        self.time_tag_container = QFrame()
        self.time_tag_container.setObjectName("timeTagContainer")
        self.time_tag_container.setMinimumHeight(72)
        self.time_tag_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.time_tag_layout = QHBoxLayout(self.time_tag_container)
        self.time_tag_layout.setContentsMargins(112, 12, 10, 12)
        self.time_tag_layout.setSpacing(10)
        self.time_tag_layout.addStretch()
        times_outer_layout.addWidget(self.time_tag_container)
        form_layout.addWidget(self.times_row_widget)

        dir_row = QHBoxLayout()
        dir_label = QLabel("报告目录:")
        dir_label.setObjectName("formLabel")
        dir_label.setMinimumWidth(112)
        dir_row.addWidget(dir_label)
        self.dir_input = QLineEdit()
        self.dir_input.setMinimumHeight(36)
        self.dir_input.setText(str(get_config_dir() / "scheduled_reports"))
        dir_row.addWidget(self.dir_input, 1)
        browse_btn = QPushButton("选择")
        browse_btn.setObjectName("secondaryBtn")
        browse_btn.setMinimumHeight(36)
        browse_btn.clicked.connect(self.choose_dir)
        dir_row.addWidget(browse_btn)
        form_layout.addLayout(dir_row)
        layout.addWidget(form_card)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = PrimaryPushButton("保存")
        ok_btn.setFixedWidth(100)
        ok_btn.setMinimumHeight(34)
        ok_btn.clicked.connect(self.save_and_accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.setFixedWidth(100)
        cancel_btn.setMinimumHeight(34)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self.setStyleSheet("""
            QDialog { background: #f8fafc; }
            QLabel#scheduleTitle {
                color: #0f766e;
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#scheduleSubtitle {
                color: #64748b;
                font-size: 13px;
            }
            QFrame#scheduleCard {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
            QLabel#formLabel {
                color: #374151;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#scheduleHint {
                color: #0f766e;
                background: #f0fdfa;
                border: 1px solid #ccfbf1;
                padding: 10px;
                border-radius: 6px;
            }
            QLineEdit, QComboBox, QTimeEdit, QSpinBox {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 0 10px;
                color: #111827;
            }
            QLineEdit:focus, QComboBox:focus, QTimeEdit:focus, QSpinBox:focus {
                border-color: #50C9C3;
            }
            QFrame#timeTagContainer {
                background: #f8fafc;
                border: 1px dashed #cbd5e1;
                border-radius: 8px;
            }
            QPushButton#timeTag {
                background: #f0fdfa;
                border: 1px solid #99f6e4;
                border-radius: 14px;
                color: #0f766e;
                padding: 4px 10px;
                font-weight: 600;
                min-height: 26px;
            }
            QPushButton#timeTag:hover {
                background: #ccfbf1;
                border-color: #50C9C3;
            }
            QPushButton#secondaryBtn {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                color: #374151;
                padding: 0 14px;
            }
            QPushButton#secondaryBtn:hover {
                background: #f0fdfa;
                border-color: #50C9C3;
                color: #0f766e;
            }
        """)

    def schedules_path(self) -> Path:
        return get_config_dir() / "schedules.json"

    def log_path(self) -> Path:
        return get_config_dir() / "scheduled_inspection.log"

    def read_schedules(self) -> list:
        path = self.schedules_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("schedules", [])
        except Exception:
            return []

    def write_schedules(self, schedules: list):
        path = self.schedules_path()
        path.parent.mkdir(exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schedules": schedules}, f, indent=2, ensure_ascii=False)

    def current_server_id(self) -> str:
        return self.server_combo.currentData() or ""

    def _update_frequency_fields(self):
        schedule_type = self.frequency_combo.currentData() or "daily"
        daily_mode = self.daily_mode_combo.currentData() or "interval_minutes"
        monthly_mode = self.monthly_mode_combo.currentData() or "month_days"
        self.daily_mode_row_widget.setVisible(schedule_type == "daily")
        self.minute_interval_row_widget.setVisible(schedule_type == "daily" and daily_mode == "interval_minutes")
        self.weekly_row_widget.setVisible(schedule_type == "weekly")
        self.monthly_mode_row_widget.setVisible(schedule_type == "monthly")
        self.monthly_row_widget.setVisible(schedule_type == "monthly" and monthly_mode == "month_days")
        self.interval_days_row_widget.setVisible(schedule_type == "monthly" and monthly_mode == "interval_days")
        self.times_row_widget.setVisible(not (schedule_type == "daily" and daily_mode == "interval_minutes"))
        needs_time_tags = not (schedule_type == "daily" and daily_mode == "interval_minutes")
        self.setMinimumHeight(560 if needs_time_tags else 430)
        self.resize(self.width(), 560 if needs_time_tags else 430)

    def add_time_tag(self):
        self._add_time_tag(self.time_edit.time().toString("HH:mm"))

    def _add_time_tag(self, time_text: str):
        parsed = QTime.fromString(time_text, "HH:mm")
        if not parsed.isValid():
            return
        normalized = parsed.toString("HH:mm")
        if normalized in self.time_tags:
            return
        self.time_tags.append(normalized)
        self.time_tags.sort()
        self.refresh_time_tags()

    def remove_time_tag(self, time_text: str):
        self.time_tags = [tag for tag in self.time_tags if tag != time_text]
        self.refresh_time_tags()

    def refresh_time_tags(self):
        while self.time_tag_layout.count():
            item = self.time_tag_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        if not self.time_tags:
            empty = QLabel("未添加固定时间点")
            empty.setStyleSheet("color: #94a3b8; font-size: 12px;")
            self.time_tag_layout.addWidget(empty)
        for time_text in self.time_tags:
            tag_btn = QPushButton(f"{time_text}  ×")
            tag_btn.setObjectName("timeTag")
            tag_btn.setToolTip("点击删除该时间点")
            tag_btn.clicked.connect(lambda _=False, t=time_text: self.remove_time_tag(t))
            self.time_tag_layout.addWidget(tag_btn)
        self.time_tag_layout.addStretch()

    def _parse_times(self) -> list:
        times = sorted({str(tag).strip() for tag in self.time_tags if str(tag).strip()})
        if not times:
            QMessageBox.warning(self, "提示", "请至少添加一个固定时间点")
            return []
        return times

    def _parse_month_days(self) -> list:
        raw = self.month_days_input.text().replace("，", ",").replace("；", ",").replace(";", ",")
        days = []
        for item in raw.split(","):
            text = item.strip()
            if not text:
                continue
            try:
                value = int(text)
            except ValueError:
                QMessageBox.warning(self, "日期格式错误", f"每月日期“{text}”不是有效数字")
                return []
            if not 1 <= value <= 31:
                QMessageBox.warning(self, "日期格式错误", "每月日期必须在 1 到 31 之间")
                return []
            if value not in days:
                days.append(value)
        if not days:
            QMessageBox.warning(self, "提示", "请至少填写一个每月执行日期")
        return sorted(days)

    def _selected_weekdays(self) -> list:
        return [int(check.property("weekday")) for check in self.weekday_checks if check.isChecked()]

    def load_schedule(self):
        server_id = self.current_server_id()
        schedule = next((s for s in self.read_schedules() if s.get("server_id") == server_id), {})
        self.enabled_check.setChecked(schedule.get("enabled", False))
        schedule_type = schedule.get("schedule_type", "daily")
        if schedule_type == "interval_days":
            schedule_type = "monthly"
            schedule["monthly_mode"] = "interval_days"
        type_index = self.frequency_combo.findData(schedule_type)
        self.frequency_combo.setCurrentIndex(type_index if type_index >= 0 else 0)
        daily_mode = schedule.get("daily_mode", "times" if schedule.get("times") else "interval_minutes")
        daily_index = self.daily_mode_combo.findData(daily_mode)
        self.daily_mode_combo.setCurrentIndex(daily_index if daily_index >= 0 else 0)
        self.interval_minutes_spin.setValue(int(schedule.get("interval_minutes", 60) or 60))
        weekdays = schedule.get("weekdays")
        if not isinstance(weekdays, list):
            weekdays = [1]
        for check in self.weekday_checks:
            check.setChecked(int(check.property("weekday")) in weekdays)
        monthly_mode = schedule.get("monthly_mode", "month_days")
        monthly_index = self.monthly_mode_combo.findData(monthly_mode)
        self.monthly_mode_combo.setCurrentIndex(monthly_index if monthly_index >= 0 else 0)
        times = schedule.get("times")
        if not isinstance(times, list) or not times:
            times = [schedule.get("time", "02:00")]
        self.time_tags = []
        for time_text in times:
            self._add_time_tag(str(time_text))
        self.refresh_time_tags()
        self.interval_days_spin.setValue(int(schedule.get("interval_days", 1) or 1))
        month_days = schedule.get("month_days")
        if not isinstance(month_days, list) or not month_days:
            month_days = [schedule.get("month_day", 1)]
        self.month_days_input.setText("，".join(str(day) for day in month_days))
        self.dir_input.setText(schedule.get("report_dir") or str(get_config_dir() / "scheduled_reports"))
        self._update_frequency_fields()

    def choose_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择巡检报告保存目录", self.dir_input.text())
        if directory:
            self.dir_input.setText(directory)

    def show_log(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("定时巡检日志")
        dialog.setWindowIcon(dialog_icon("xunjian.svg"))
        dialog.resize(760, 520)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        if self.log_path().exists():
            try:
                text.setPlainText(self.log_path().read_text(encoding="utf-8"))
            except Exception as exc:
                text.setPlainText(f"读取日志失败: {exc}")
        else:
            text.setPlainText("暂无定时巡检日志")
        layout.addWidget(text)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def save_and_accept(self):
        if self.server_combo.count() == 0:
            QMessageBox.warning(self, "提示", "请先添加服务器")
            return
        server_id = self.current_server_id()
        schedule_type = self.frequency_combo.currentData() or "daily"
        daily_mode = self.daily_mode_combo.currentData() or "interval_minutes"
        monthly_mode = self.monthly_mode_combo.currentData() or "month_days"
        times = []
        if not (schedule_type == "daily" and daily_mode == "interval_minutes"):
            times = self._parse_times()
            if not times:
                return
        else:
            times = ["00:00"]
        weekdays = self._selected_weekdays()
        if schedule_type == "weekly" and not weekdays:
            QMessageBox.warning(self, "提示", "请至少选择一个每周执行日期")
            return
        month_days = [1]
        if schedule_type == "monthly" and monthly_mode == "month_days":
            month_days = self._parse_month_days()
            if not month_days:
                return
        existing_schedules = self.read_schedules()
        old_schedule = next((s for s in existing_schedules if s.get("server_id") == server_id), {})
        schedules = [s for s in existing_schedules if s.get("server_id") != server_id]
        schedules.append({
            "server_id": server_id,
            "enabled": self.enabled_check.isChecked(),
            "schedule_type": schedule_type,
            "daily_mode": daily_mode,
            "monthly_mode": monthly_mode,
            "time": times[0],
            "times": times,
            "interval_minutes": self.interval_minutes_spin.value(),
            "weekdays": weekdays,
            "interval_days": self.interval_days_spin.value(),
            "month_day": month_days[0],
            "month_days": month_days,
            "start_date": old_schedule.get("start_date") or datetime.now().strftime("%Y-%m-%d"),
            "report_dir": self.dir_input.text().strip() or str(get_config_dir() / "scheduled_reports"),
            "last_run_date": "",
            "last_run_keys": [],
        })
        self.write_schedules(schedules)
        self.accept()


class DeviceActionsMixin:
    """Shared device actions for list and grid views."""

    def _create_actions_button(self):
        button = QToolButton(self)
        button.setObjectName("deviceActionsButton")
        button.setIcon(FIF.SETTING.icon())
        button.setFixedSize(32, 32)
        button.setToolTip("设备操作")
        button.setAccessibleName("设备操作")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setStyleSheet("""
            QToolButton { background: transparent; border: 1px solid transparent; border-radius: 6px; }
            QToolButton:hover, QToolButton:pressed { background: #e9f5f2; border-color: #b9dbd5; }
            QToolButton:focus { border-color: #147d76; }
            QToolButton::menu-indicator { image: none; }
        """)
        menu = QMenu(button)
        menu.setStyleSheet("""
            QMenu { background: white; color: #243744; border: 1px solid #dce5e9; padding: 5px; }
            QMenu::item { padding: 9px 24px; font-size: 13px; }
            QMenu::item:selected { background: #e9f5f2; color: #147d76; }
            QMenu::item:disabled { color: #a1a8b0; background: transparent; }
            QMenu::separator { height: 1px; background: #edf0f3; margin: 4px 8px; }
        """)
        self.root_action = menu.addAction("切换 root")
        self.root_action.triggered.connect(self._on_switch_root_clicked)
        menu.addSeparator()
        menu.addAction("编辑").triggered.connect(lambda: self.edit_clicked.emit(self.server))
        menu.addAction("删除").triggered.connect(lambda: self.delete_clicked.emit(self.server))
        menu.addSeparator()
        menu.addAction("知识库").triggered.connect(self._on_knowledge_base_clicked)
        menu.addAction("脚本库").triggered.connect(self._on_script_library_clicked)
        menu.aboutToShow.connect(self._update_root_action)
        button.setMenu(menu)
        self.actions_button = button
        return button

    def _update_root_action(self):
        allowed = (self.is_connected and not self.is_root
                   and self.server.get("username", "").strip().lower() != "root"
                   and self.server.get("switch_root", True)
                   and self.server.get("product", "") != "办公智盾")
        self.root_action.setEnabled(bool(allowed))
        self.root_action.setToolTip("当前命令以 root 执行" if self.is_connected and self.is_root else "")


class ServerListItem(DeviceActionsMixin, QFrame):
    """服务器列表项组件"""

    connect_clicked = pyqtSignal(dict)
    edit_clicked = pyqtSignal(dict)
    delete_clicked = pyqtSignal(dict)
    switch_root_clicked = pyqtSignal(dict)
    monitor_clicked = pyqtSignal(dict)

    def __init__(self, server: dict):
        super().__init__()
        self.server = server
        self.is_connected = False
        self.is_root = False  # 是否已经是root
        self.init_ui()
        self.set_connected(False, False)  # 初始化按钮状态

    def _on_connect_clicked(self):
        """连接按钮点击"""
        self.connect_clicked.emit(self.server)  # 连接服务器
    
    def _on_disconnect_clicked(self):
        """断开连接按钮点击"""
        self.connect_clicked.emit(self.server)  # 断开连接（复用同一个信号）
    
    def _on_switch_root_clicked(self):
        """切换root按钮点击"""
        if self.root_action.isEnabled():
            self.switch_root_clicked.emit(self.server)
    
    def _on_script_library_clicked(self):
        """打开脚本库"""
        from .script_library import ScriptLibraryDialog
        product = self.server.get("product", "通用")
        dialog = ScriptLibraryDialog(product, self)
        # 连接信号：脚本库更新后刷新显示
        dialog.scripts_updated.connect(self._on_scripts_updated)
        dialog.exec()
    
    def _on_scripts_updated(self):
        """脚本库更新后的回调"""
        # 通知主窗口刷新AI巡检页面
        main_window = self.window()
        if hasattr(main_window, 'inspection_page'):
            main_window.inspection_page.load_scripts()
    
    def _on_knowledge_base_clicked(self):
        """打开知识库管理"""
        from .ai_troubleshoot_page import KnowledgeBaseManagerDialog
        # 加载知识库
        faqs = self._load_product_faqs(self.server.get("product", "通用"))
        dialog = KnowledgeBaseManagerDialog(faqs, self.server.get("product", "通用"), self)
        # 连接信号：知识库更新后刷新显示
        dialog.knowledge_base_updated.connect(self._on_knowledge_base_updated)
        dialog.exec()
    
    def _on_knowledge_base_updated(self):
        """知识库更新后的回调"""
        # 通知主窗口刷新AI排障页面
        main_window = self.window()
        if hasattr(main_window, 'ai_troubleshoot_page'):
            main_window.ai_troubleshoot_page.load_faqs()
    
    def _load_product_faqs(self, product: str) -> list:
        import json
        config_dir = get_config_dir()
        config_file = config_dir / "faqs.json"
        
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                if isinstance(data, dict):
                    product_faqs = data.get(product, [])
                    generic_faqs = data.get("通用", [])
                    return product_faqs + generic_faqs
                elif isinstance(data, list):
                    # 按产品过滤FAQ
                    product_faqs = [f for f in data if f.get('product', '') == product]
                    return product_faqs
            except Exception as e:
                print(f"[WARN] Failed to load FAQs: {e}")
        return []
    
    def set_connected(self, connected: bool, is_root: bool = False):
        """设置连接状态"""
        self.is_connected = bool(connected)
        self.is_root = bool(connected and (is_root or self.server.get("username", "").strip().lower() == "root"))
        self._update_root_action()
        
        if connected:
            # 已连接：连接按钮显示"已连接"（灰色不可点击），断开按钮可见可点击
            self.connect_btn.setText("已连接")
            self.connect_btn.setEnabled(False)
            self.connect_btn.setStyleSheet("""
                QPushButton#connectBtn {
                    background: #e5e7eb;
                    color: #9ca3af;
                    border: none;
                    border-radius: 4px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self.disconnect_btn.setVisible(True)
            self.disconnect_btn.setStyleSheet("""
                QPushButton#disconnectBtn {
                    background: #fef2f2;
                    color: #ef4444;
                    border: none;
                    border-radius: 4px;
                    font-size: 12px;
                    font-weight: bold;
                }
                QPushButton#disconnectBtn:hover {
                    background: #fee2e2;
                }
            """)
            self.setStyleSheet("""
                QFrame#serverListItem {
                    background: #f0fdf4;
                    border: 1px solid #10b981;
                    border-radius: 6px;
                }
            """)
        else:
            # 未连接：连接按钮显示"连接"（可点击），断开按钮隐藏
            self.connect_btn.setText("连接")
            self.connect_btn.setEnabled(True)
            self.connect_btn.setStyleSheet("""
                QPushButton#connectBtn {
                    background: #50C9C3;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-size: 12px;
                    font-weight: bold;
                }
                QPushButton#connectBtn:hover {
                    background: #3eb8b3;
                }
            """)
            self.disconnect_btn.setVisible(False)
            self.setStyleSheet("""
                QFrame#serverListItem {
                    background: white;
                    border: 1px solid #e5e7eb;
                    border-radius: 6px;
                }
                QFrame#serverListItem:hover {
                    border-color: #50C9C3;
                    background: #f0fdf4;
                }
            """)

    def init_ui(self):
        """初始化 UI"""
        self.setObjectName("serverListItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(56)
        self.setMaximumHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)

        # 产品标签
        product_label = QLabel(self.server.get("product", ""))
        product_label.setObjectName("productLabel")
        product_label.setFixedWidth(70)
        product_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        product_label.setStyleSheet("""
            background: #50C9C3;
            color: white;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 11px;
            font-weight: bold;
        """)
        layout.addWidget(product_label)

        # 服务器信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(1)

        # 名称/主机
        name = self.server.get("name") or self.server.get("host", "")
        name_label = ClickableLabel(name)
        name_label.setObjectName("serverName")
        name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        name_label.setToolTip("点击打开服务器实时监控")
        name_label.clicked.connect(lambda: self.monitor_clicked.emit(self.server))
        name_label.setStyleSheet("""
            QLabel#serverName {
                font-size: 13px;
                font-weight: bold;
                color: #0f766e;
                text-decoration: underline;
            }
            QLabel#serverName:hover { color: #0d9488; }
        """)
        info_layout.addWidget(name_label)

        # 主机:端口 用户名
        host = self.server.get("host", "")
        port = self.server.get("port", 22)
        username = self.server.get("username", "")
        detail_label = QLabel(f"{host}:{port} | {username}")
        detail_label.setObjectName("serverDetail")
        detail_label.setStyleSheet("font-size: 11px; color: #6b7280;")
        info_layout.addWidget(detail_label)

        layout.addLayout(info_layout, 1)

        # 连接按钮
        self.connect_btn = QPushButton("连接")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.setFixedWidth(70)
        self.connect_btn.setFixedHeight(26)
        self.connect_btn.setStyleSheet("""
            QPushButton#connectBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton#connectBtn:hover {
                background: #3eb8b3;
            }
        """)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        layout.addWidget(self.connect_btn)
        
        # 断开连接按钮（连接成功后可点击）
        self.disconnect_btn = QPushButton("断开")
        self.disconnect_btn.setObjectName("disconnectBtn")
        self.disconnect_btn.setFixedWidth(70)
        self.disconnect_btn.setFixedHeight(26)
        self.disconnect_btn.setVisible(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        layout.addWidget(self.disconnect_btn)
        
        layout.addWidget(self._create_actions_button())


class ServerGridItem(DeviceActionsMixin, QFrame):
    """服务器网格项组件"""
    
    connect_clicked = pyqtSignal(dict)
    edit_clicked = pyqtSignal(dict)
    delete_clicked = pyqtSignal(dict)
    switch_root_clicked = pyqtSignal(dict)
    monitor_clicked = pyqtSignal(dict)

    def __init__(self, server: dict):
        super().__init__()
        self.server = server
        self.is_connected = False
        self.is_root = False
        self.init_ui()
        self.set_connected(False, False)  # 初始化按钮状态

    def _on_connect_clicked(self):
        """连接按钮点击"""
        self.connect_clicked.emit(self.server)  # 连接服务器
    
    def _on_disconnect_clicked(self):
        """断开连接按钮点击"""
        self.connect_clicked.emit(self.server)  # 断开连接（复用同一个信号）
    
    def _on_switch_root_clicked(self):
        """切换root按钮点击"""
        if self.root_action.isEnabled():
            self.switch_root_clicked.emit(self.server)
    
    def _on_script_library_clicked(self):
        """打开脚本库"""
        from .script_library import ScriptLibraryDialog
        product = self.server.get("product", "通用")
        dialog = ScriptLibraryDialog(product, self)
        dialog.exec()
    
    def _on_knowledge_base_clicked(self):
        """打开知识库管理"""
        from .ai_troubleshoot_page import KnowledgeBaseManagerDialog
        # 加载知识库
        faqs = self._load_product_faqs(self.server.get("product", "通用"))
        dialog = KnowledgeBaseManagerDialog(faqs, self.server.get("product", "通用"), self)
        # 连接信号：知识库更新后刷新显示
        dialog.knowledge_base_updated.connect(self._on_knowledge_base_updated)
        dialog.exec()
    
    def _on_knowledge_base_updated(self):
        """知识库更新后的回调"""
        # 通知主窗口刷新AI排障页面
        main_window = self.window()
        if hasattr(main_window, 'ai_troubleshoot_page'):
            main_window.ai_troubleshoot_page.load_faqs()
    
    def _load_product_faqs(self, product: str) -> list:
        import json
        config_dir = get_config_dir()
        config_file = config_dir / "faqs.json"
        
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                if isinstance(data, dict):
                    product_faqs = data.get(product, [])
                    generic_faqs = data.get("通用", [])
                    return product_faqs + generic_faqs
                elif isinstance(data, list):
                    # 按产品过滤FAQ
                    product_faqs = [f for f in data if f.get('product', '') == product]
                    return product_faqs
            except Exception as e:
                print(f"[WARN] Failed to load FAQs: {e}")
        return []
    
    def set_connected(self, connected: bool, is_root: bool = False):
        """设置连接状态"""
        self.is_connected = bool(connected)
        self.is_root = bool(connected and (is_root or self.server.get("username", "").strip().lower() == "root"))
        self._update_root_action()
        
        if connected:
            # 已连接：连接按钮显示"已连接"（灰色不可点击）
            self.connect_btn.setText("已连接")
            self.connect_btn.setEnabled(False)
            self.connect_btn.setStyleSheet("""
                QPushButton#connectBtn {
                    background: #e5e7eb;
                    color: #9ca3af;
                    border: none;
                    border-radius: 4px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self.setStyleSheet("""
                QFrame#serverGridItem {
                    background: #f0fdf4;
                    border: 2px solid #10b981;
                    border-radius: 8px;
                }
            """)
        else:
            # 未连接：连接按钮显示"连接"（可点击）
            self.connect_btn.setText("连接")
            self.connect_btn.setEnabled(True)
            self.connect_btn.setStyleSheet("""
                QPushButton#connectBtn {
                    background: #50C9C3;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-size: 12px;
                    font-weight: bold;
                }
                QPushButton#connectBtn:hover {
                    background: #3eb8b3;
                }
            """)
            self.setStyleSheet("""
                QFrame#serverGridItem {
                    background: white;
                    border: 2px solid #e5e7eb;
                    border-radius: 8px;
                }
                QFrame#serverGridItem:hover {
                    border-color: #50C9C3;
                }
            """)

    def init_ui(self):
        """初始化 UI"""
        self.setObjectName("serverGridItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(200, 160)
        self.setMaximumSize(250, 190)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)

        # 顶部：产品标签 + 菜单按钮
        header_layout = QHBoxLayout()
        
        product_label = QLabel(self.server.get("product", ""))
        product_label.setObjectName("productLabel")
        product_label.setStyleSheet("""
            background: #50C9C3;
            color: white;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: bold;
        """)
        header_layout.addWidget(product_label)
        
        header_layout.addStretch()
        
        header_layout.addWidget(self._create_actions_button())

        layout.addLayout(header_layout)

        # 服务器名称/主机
        name = self.server.get("name") or self.server.get("host", "")
        name_label = ClickableLabel(name)
        name_label.setObjectName("serverName")
        name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        name_label.setToolTip("点击打开服务器实时监控")
        name_label.clicked.connect(lambda: self.monitor_clicked.emit(self.server))
        name_label.setStyleSheet("""
            QLabel#serverName {
                font-size: 13px;
                font-weight: bold;
                color: #0f766e;
                text-decoration: underline;
            }
            QLabel#serverName:hover { color: #0d9488; }
        """)
        name_label.setWordWrap(True)
        layout.addWidget(name_label)

        # 主机:端口
        host = self.server.get("host", "")
        port = self.server.get("port", 22)
        host_label = QLabel(f"{host}:{port}")
        host_label.setObjectName("serverHost")
        host_label.setStyleSheet("font-size: 11px; color: #6b7280;")
        layout.addWidget(host_label)
        
        # 用户名
        username = self.server.get("username", "")
        user_label = QLabel(f"用户: {username}")
        user_label.setObjectName("serverUser")
        user_label.setStyleSheet("font-size: 10px; color: #9ca3af;")
        layout.addWidget(user_label)
        
        layout.addStretch()

        # 底部：只保留连接按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(5)
        
        # 连接按钮
        self.connect_btn = QPushButton("连接")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.setFixedHeight(28)
        self.connect_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.connect_btn.setFixedWidth(60)
        self.connect_btn.setStyleSheet("""
            QPushButton#connectBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton#connectBtn:hover {
                background: #3eb8b3;
            }
        """)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        btn_layout.addWidget(self.connect_btn)
        
        # 添加stretch使按钮靠左对齐
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
    


class ServerDetailItem(ServerGridItem):
    """Compact inspector card, sharing the existing device actions and root state."""

    def init_ui(self):
        self.setObjectName('serverDetailItem')
        self.setMinimumHeight(112)
        self.setMaximumHeight(124)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        header = QHBoxLayout()
        name = QLabel(self.server.get('name') or self.server.get('host', '设备'))
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setWordWrap(True)
        name.setStyleSheet('font-size:14px; font-weight:600; color:#223b47;')
        header.addWidget(name, 1)
        header.addWidget(self._create_actions_button())
        layout.addLayout(header)
        address = QLabel(f'{self.server.get("host", "")}:{self.server.get("port", 22)}')
        address.setTextFormat(Qt.TextFormat.PlainText)
        address.setStyleSheet('font:11px "Consolas"; color:#6b7f8b;')
        layout.addWidget(address)
        bottom = QHBoxLayout()
        product = QLabel(f'{self.server.get("product") or "通用"} · {self.server.get("username", "")}')
        product.setTextFormat(Qt.TextFormat.PlainText)
        product.setStyleSheet('font-size:11px; color:#6b7f8b;')
        bottom.addWidget(product, 1)
        self.connect_btn = QPushButton('连接')
        self.connect_btn.setObjectName('detailConnectBtn')
        self.connect_btn.setFixedSize(64, 27)
        self.connect_btn.clicked.connect(lambda: self.connect_clicked.emit(self.server))
        bottom.addWidget(self.connect_btn)
        layout.addLayout(bottom)

    def set_connected(self, connected, is_root=False):
        self.is_connected = bool(connected)
        self.is_root = bool(connected and (is_root or self.server.get('username', '').strip().lower() == 'root'))
        self._update_root_action()
        self.connect_btn.setText('断开' if connected else '连接')
        self.setStyleSheet('''
            QFrame#serverDetailItem {background:#f7fafb; border:1px solid #e1e9ed; border-radius:8px;}
            QPushButton#detailConnectBtn {border:1px solid #b9dcd5; border-radius:5px; background:#e6f5f1; color:#137c70; font-size:11px;}
            QPushButton#detailConnectBtn:hover {background:#d2eee7; border-color:#55b5a7;}
        ''')


class ProductSection(QWidget):
    """产品分组组件"""
    
    # 信号定义
    item_connect = pyqtSignal(dict)
    item_edit = pyqtSignal(dict)
    item_delete = pyqtSignal(dict)
    item_switch_root = pyqtSignal(dict)
    item_monitor = pyqtSignal(dict)
    
    def __init__(self, product_name: str, servers: list, parent=None):
        super().__init__(parent)
        self.product_name = product_name
        self.servers = servers
        self.server_items = []
        self.is_grid_view = False
        self.grid_widget = None
        
        self.init_ui()
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(8)
        
        # 产品标题
        title_layout = QHBoxLayout()
        
        title_label = QLabel(self.product_name)
        title_label.setStyleSheet("""
            font-size: 16px;
            font-weight: bold;
            color: #374151;
            padding: 5px 0;
        """)
        title_layout.addWidget(title_label)
        
        count_label = QLabel(f"({len(self.servers)})")
        count_label.setStyleSheet("font-size: 14px; color: #9ca3af;")
        title_layout.addWidget(count_label)
        
        title_layout.addStretch()
        
        layout.addLayout(title_layout)
        
        # 服务器列表容器
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setSpacing(8)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        layout.addWidget(self.container)
    
    def update_view(self, is_grid: bool):
        """更新视图模式"""
        if self.is_grid_view == is_grid:
            # 如果视图没变但可能是第一次调用，先清除
            if not self.server_items:
                self._clear_items()
            else:
                return
        
        self.is_grid_view = is_grid
        self._clear_items()
        
        if is_grid:
            # 网格视图 - 左对齐
            self.grid_widget = QWidget()
            grid_layout = QGridLayout(self.grid_widget)
            grid_layout.setSpacing(12)
            grid_layout.setContentsMargins(0, 0, 0, 0)
            grid_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            
            for i, server in enumerate(self.servers):
                row = i // 3
                col = i % 3
                
                item = ServerGridItem(server)
                item.connect_clicked.connect(self.on_item_connect)
                item.edit_clicked.connect(self.on_item_edit)
                item.delete_clicked.connect(self.on_item_delete)
                item.switch_root_clicked.connect(self.on_item_switch_root)
                item.monitor_clicked.connect(self.on_item_monitor)
                
                grid_layout.addWidget(item, row, col)
                self.server_items.append(item)
            
            self.container_layout.addWidget(self.grid_widget)
        else:
            # 列表视图
            for server in self.servers:
                item = ServerListItem(server)
                item.connect_clicked.connect(self.on_item_connect)
                item.edit_clicked.connect(self.on_item_edit)
                item.delete_clicked.connect(self.on_item_delete)
                item.switch_root_clicked.connect(self.on_item_switch_root)
                item.monitor_clicked.connect(self.on_item_monitor)
                
                self.container_layout.addWidget(item)
                self.server_items.append(item)
    
    def _clear_items(self):
        """清除现有项"""
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.server_items.clear()
    
    def on_item_connect(self, server: dict):
        """连接按钮点击"""
        self.item_connect.emit(server)
    
    def on_item_edit(self, server: dict):
        """编辑按钮点击"""
        self.item_edit.emit(server)
    
    def on_item_delete(self, server: dict):
        """删除按钮点击"""
        self.item_delete.emit(server)
    
    def on_item_switch_root(self, server: dict):
        """切换root按钮点击"""
        self.item_switch_root.emit(server)

    def on_item_monitor(self, server: dict):
        """服务器名称点击，打开实时监控"""
        self.item_monitor.emit(server)
    
    def update_connected_status(self, connected_server_id: str = None, is_root: bool = False):
        """更新连接状态"""
        for item in self.server_items:
            if hasattr(item, 'server'):
                server = item.server
            elif hasattr(item, 'server'):
                server = item.server
            else:
                continue
            is_connected = bool(connected_server_id and connected_server_id == server.get("id"))
            item.set_connected(is_connected, is_root and is_connected)


class ProductPage(QWidget):
    """设备管理页面"""

    # 信号定义
    server_immediate_connect = pyqtSignal(dict, str)
    server_disconnect_request = pyqtSignal(dict)
    server_switch_root_request = pyqtSignal(dict)
    schedules_updated = pyqtSignal()
    go_to_next_step = pyqtSignal()  # 下一步按钮点击信号
    device_operation_requested = pyqtSignal(dict, str)
    resume_inspection_requested = pyqtSignal()

    def __init__(self):
        """初始化产品选择页面"""
        super().__init__()
        self.servers = []
        self.connected_server_id = None
        self.is_root = False
        self.is_grid_view = False
        self.view_mode = 'list'
        self.racks = []
        self.rack_view = None
        self.selected_device_id = None
        self.device_states = {}
        self.drag_token = str(uuid4())
        self.drag_active = False
        self._pending_arrival_id = None
        self.inspection_history = {}
        self.inventory_error = ''
        self.inspection_dialog = None
        self.inspection_active = False
        self.product_sections = []
        self.init_ui()
        self.load_servers()
        try:
            history_path = get_config_dir() / 'inspection_history.json'
            if history_path.exists():
                data = json.loads(history_path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    self.inspection_history = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.stats_label.setText('巡检历史读取失败；设备配置不受影响')

    def init_ui(self):
        """初始化 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # 标题区域
        title_layout = QHBoxLayout()
        title_layout.setSpacing(10)

        self.title_label = QLabel("设备管理")
        self.title_label.setObjectName("pageTitle")
        self.title_label.setStyleSheet('font-size:23px; font-weight:600; color:#203b49; padding:0;')
        title_layout.addWidget(self.title_label)

        title_layout.addStretch()
        
        # 左右分段图标选择器：明确展示当前视图。
        self.view_switcher = QFrame()
        self.view_switcher.setObjectName("viewSwitcher")
        switcher_layout = QHBoxLayout(self.view_switcher)
        switcher_layout.setContentsMargins(3, 3, 3, 3)
        switcher_layout.setSpacing(2)
        self.view_button_group = QButtonGroup(self)
        self.view_button_group.setExclusive(True)
        for is_grid, name, label, icon in [
            (False, 'listViewButton', '列表视图', FIF.MENU),
            (True, 'gridViewButton', '方块视图', FIF.TILES),
        ]:
            button = QToolButton()
            button.setObjectName(name)
            button.setProperty('viewChoice', True)
            button.setIcon(icon.icon())
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(36, 28)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setChecked(self.is_grid_view == is_grid)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked, grid=is_grid: self.set_view_mode(grid))
            self.view_button_group.addButton(button, int(is_grid))
            switcher_layout.addWidget(button)
        rack_button = QToolButton()
        rack_button.setObjectName('rackViewButton')
        rack_button.setProperty('viewChoice', True)
        rack_button.setIcon(dialog_icon('rack.svg'))
        rack_button.setFixedSize(36, 28)
        rack_button.setCheckable(True)
        rack_button.setToolTip('机柜视图')
        rack_button.setAccessibleName('机柜视图')
        rack_button.clicked.connect(lambda: self.set_view_mode('rack'))
        self.view_button_group.addButton(rack_button, 2)
        switcher_layout.insertWidget(0, rack_button)
        self.add_rack_btn = QPushButton('新增机柜')
        self.add_rack_btn.setObjectName('addRackBtn')
        self.add_rack_btn.setIcon(dialog_icon('rack.svg'))
        self.add_rack_btn.setMinimumHeight(36)
        self.add_rack_btn.clicked.connect(lambda: self.edit_rack())
        title_layout.addWidget(self.add_rack_btn)
        
        # 添加服务器按钮
        self.schedule_btn = QPushButton("定时巡检")
        self.schedule_btn.setObjectName("scheduleBtn")
        self.schedule_btn.setMinimumHeight(36)
        self.schedule_btn.setMinimumWidth(100)
        self.schedule_btn.clicked.connect(self.on_schedule_inspection)
        title_layout.addWidget(self.schedule_btn)
        self.schedule_btn.hide()

        self.add_server_btn = PrimaryPushButton("添加设备")
        self.add_server_btn.setIcon(FIF.ADD.icon(color=Qt.GlobalColor.white))
        self.add_server_btn.setObjectName("addServerBtn")
        self.add_server_btn.setMinimumHeight(36)
        self.add_server_btn.setMinimumWidth(100)
        self.add_server_btn.clicked.connect(self.on_add_server)
        title_layout.addWidget(self.add_server_btn)

        layout.addLayout(title_layout)

        filter_panel = QFrame()
        self.filter_panel = filter_panel
        filter_panel.setObjectName('deviceFilterPanel')
        filter_row = QHBoxLayout(filter_panel)
        filter_row.setContentsMargins(10, 8, 10, 8)
        filter_row.setSpacing(10)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('搜索设备名称、IP、产品或机柜')
        self.search_input.setObjectName('deviceSearch')
        self.search_input.setMinimumHeight(34)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.update_server_list)
        self.room_combo = QComboBox()
        self.room_combo.setObjectName('roomFilter')
        self.room_combo.addItem('全部机房', None)
        self.room_combo.setMinimumHeight(34)
        self.room_combo.setMinimumWidth(150)
        self.room_combo.currentIndexChanged.connect(self.update_server_list)
        filter_row.addWidget(self.search_input, 1)
        filter_row.addWidget(self.room_combo)
        filter_row.addWidget(self.view_switcher)
        layout.addWidget(filter_panel)

        # 服务器列表滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("serverScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("""
            QScrollArea#serverScrollArea {
                border: none;
                background: transparent;
            }
            QScrollArea#serverScrollArea > QWidget {
                background: transparent;
            }
        """)

        self.scroll_widget = QWidget()
        self.main_layout = QVBoxLayout(self.scroll_widget)
        self.main_layout.setSpacing(15)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll_area, 1)

        # 底部统计和下一步按钮
        footer_layout = QHBoxLayout()
        
        self.stats_label = QLabel("共 0 台服务器")
        self.stats_label.setObjectName("statsLabel")
        self.stats_label.setStyleSheet('font-size:11px; color:#7b8d97;')
        footer_layout.addWidget(self.stats_label)
        
        footer_layout.addStretch()
        self.resume_inspection_btn = QPushButton('查看巡检')
        self.resume_inspection_btn.setMinimumHeight(32)
        self.resume_inspection_btn.hide()
        self.resume_inspection_btn.clicked.connect(self.resume_inspection_requested)
        footer_layout.addWidget(self.resume_inspection_btn)
        
        # 下一步按钮（连接成功后可见）
        self.next_step_btn = PrimaryPushButton("进入巡检")
        self.next_step_btn.setObjectName("nextStepBtn")
        self.next_step_btn.setMinimumHeight(36)
        self.next_step_btn.setFixedWidth(120)
        self.next_step_btn.setVisible(False)
        self.next_step_btn.clicked.connect(self.on_next_step)
        footer_layout.addWidget(self.next_step_btn)
        
        layout.addLayout(footer_layout)

        self.apply_styles()

    def show_device_inspection(self, inspection_page, server):
        if self.inspection_dialog is None:
            self.inspection_dialog = QDialog(self)
            self.inspection_dialog.setMinimumSize(900, 600)
            self.inspection_dialog.resize(1080, 720)
            panel_layout = QVBoxLayout(self.inspection_dialog)
            panel_layout.setContentsMargins(12, 12, 12, 8)
            header = QHBoxLayout()
            self.inspection_context = QLabel()
            self.inspection_context.setTextFormat(Qt.TextFormat.PlainText)
            self.inspection_context.setStyleSheet('font-size:14px; font-weight:600; color:#355968;')
            header.addWidget(self.inspection_context, 1)
            hide_button = QPushButton('收起窗口')
            hide_button.clicked.connect(self.close_device_inspection)
            header.addWidget(hide_button)
            panel_layout.addLayout(header)
            panel_layout.addWidget(inspection_page, 1)
            self.inspection_dialog.finished.connect(self._inspection_hidden)
        title = f'一键巡检 · {server.get("name") or "设备"} · {server.get("host", "")}'
        self.inspection_dialog.setWindowTitle(title)
        self.inspection_context.setText(title)
        self.inspection_active = True
        self.resume_inspection_btn.hide()
        inspection_page.show()
        self.inspection_dialog.show()
        self.inspection_dialog.raise_()
        self.inspection_dialog.activateWindow()

    def _inspection_hidden(self, *args):
        self.inspection_active = False
        self.resume_inspection_btn.setVisible(bool(self.connected_server_id))

    def close_device_inspection(self):
        if self.inspection_dialog:
            self.inspection_dialog.hide()
        self._inspection_hidden()

    def toggle_view(self):
        """切换视图模式"""
        self.set_view_mode(not self.is_grid_view)

    def set_view_mode(self, is_grid):
        """选择视图；重复点击当前选项不会重建列表。"""
        mode = ('grid' if is_grid else 'list') if isinstance(is_grid, bool) else is_grid
        if mode not in ('rack', 'list', 'grid') or self.view_mode == mode:
            return
        self.view_mode = mode
        self.is_grid_view = mode == 'grid'
        self.view_button_group.button({'list': 0, 'grid': 1, 'rack': 2}[mode]).setChecked(True)
        self.update_server_list()
        if not self.inventory_error:
            try:
                self.inventory_store().save(self.racks, self.servers, mode)
            except (ValueError, OSError):
                self.stats_label.setText('视图已切换；偏好未保存，请检查设备位置或配置权限')

    def inventory_store(self):
        return InventoryStore(get_config_dir() / 'servers.json')

    def commit_inventory(self, servers, racks=None):
        if self.inventory_error:
            QMessageBox.warning(self, '配置不可写', self.inventory_error + '\n请先修复原配置并重启程序')
            return False
        racks = self.racks if racks is None else racks
        safe = [normalize_server_record(s) for s in servers]
        try:
            self.inventory_store().save(racks, safe, self.view_mode)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, '保存失败', str(error))
            return False
        self.servers, self.racks = safe, list(racks)
        current_room = self.room_combo.currentData()
        self.room_combo.blockSignals(True)
        self.room_combo.clear()
        self.room_combo.addItem('全部机房', None)
        for room in sorted({r.get('room', '') for r in self.racks}):
            self.room_combo.addItem(room or '未分配机房', room)
        self.room_combo.setCurrentIndex(max(0, self.room_combo.findData(current_room)))
        self.room_combo.blockSignals(False)
        if not self.drag_active:
            self.update_server_list()
        return True

    def drag_device(self, server_id):
        if self.inventory_error or self.device_states.get(server_id) in ('连接中', '等待巡检', '巡检中'):
            return None
        return next((s for s in self.servers if s.get('id') == server_id), None)

    def placement_candidate(self, server_id, rack_id, start, racks=None):
        server = self.drag_device(server_id)
        if server is None:
            return None, '设备不存在或正在连接 / 巡检，请稍后重试'
        candidate = dict(server, rack_id=rack_id, rack_start_u=start,
                         rack_height_u=server.get('rack_height_u', 1))
        error = placement_error(candidate, self.racks if racks is None else racks, self.servers)
        return candidate, error

    def place_server(self, server_id, rack_id, start, racks=None):
        candidate, error = self.placement_candidate(server_id, rack_id, start, racks)
        if error:
            QMessageBox.warning(self, '无法上架', error)
            return False
        previous_selection = self.selected_device_id
        self.selected_device_id = server_id
        self._pending_arrival_id = server_id
        result = self.commit_inventory([candidate if s.get('id') == server_id else s for s in self.servers], racks)
        if not result:
            self.selected_device_id = previous_selection
            self._pending_arrival_id = None
        return result

    def create_rack_for_server(self, server_id):
        # First drop creates the enclosure and placement in one atomic transaction.
        if self.racks:
            return False
        rack = dict(id=str(uuid4()), code='A01', name='默认机柜', room='', units=42)
        return self.place_server(server_id, rack['id'], 1, [rack])

    def load_servers(self):
        """加载服务器列表"""
        try:
            data = self.inventory_store().load()
            self.servers = [normalize_server_record(server) for server in data['servers']]
            self.racks = data['racks']
            mode = data.get('preferred_view', 'rack')
            self.view_mode = mode if mode in ('rack', 'list', 'grid') else 'rack'
            self.is_grid_view = self.view_mode == 'grid'
            self.view_button_group.button({'list': 0, 'grid': 1, 'rack': 2}[self.view_mode]).setChecked(True)
            self.room_combo.blockSignals(True)
            for room in sorted({r.get('room', '') for r in self.racks}):
                self.room_combo.addItem(room or '未分配机房', room)
            self.room_combo.blockSignals(False)
        except (OSError, ValueError) as error:
            self.inventory_error = f'设备配置读取失败：{error}'
        self.update_server_list()
    
    
    def update_server_list(self, *args):
        """更新服务器列表"""
        # 清除现有分组
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.product_sections.clear()
        self.rack_view = None
        if self.view_mode == 'rack':
            self.rack_view = RackView(self, self.selected_device_id)
            self.rack_view.action_requested.connect(self.on_rack_action)
            self.rack_view.empty_selected.connect(self.add_at_position)
            self.rack_view.rack_edit.connect(self.edit_rack)
            self.rack_view.rack_delete.connect(self.delete_rack)
            self.main_layout.addWidget(self.rack_view, 1)
            self.stats_label.setText(self.inventory_error or f'{len(self.racks)} 个机柜 · {len(self.servers)} 台设备')
            return
        
        # 按产品分组
        products = {}
        for server in self.servers:
            query = self.search_input.text().strip().casefold()
            if query and query not in ' '.join(str(server.get(k, '')) for k in ('name', 'host', 'product', 'username')).casefold():
                continue
            room = self.room_combo.currentData()
            rack = next((r for r in self.racks if r.get('id') == server.get('rack_id')), {})
            if room is not None and rack.get('room', '') != room:
                continue
            product = server.get("product", "未分类")
            if product not in products:
                products[product] = []
            products[product].append(server)
        
        # 创建产品分组
        for product_name, servers in products.items():
            section = ProductSection(product_name, servers, self)
            section.update_view(self.is_grid_view)
            section.update_connected_status(self.connected_server_id, self.is_root)
            # 连接信号
            section.item_connect.connect(self.on_server_connect)
            section.item_edit.connect(self.on_edit_server)
            section.item_delete.connect(self.on_delete_server)
            section.item_switch_root.connect(self.on_switch_root)
            section.item_monitor.connect(self.on_monitor_server)
            self.main_layout.addWidget(section)
            self.product_sections.append(section)
        
        # 添加stretch使内容顶部对齐
        self.main_layout.addStretch()
        
        # 更新统计
        self.stats_label.setText(f"共 {len(self.servers)} 台服务器")
    
    def on_server_connect(self, server: dict):
        """连接/断开服务器"""
        self.selected_device_id = server.get('id')
        if self.connected_server_id and self.connected_server_id == server.get("id"):
            # 断开连接
            self.server_disconnect_request.emit(server)
        else:
            # 连接服务器 - 使用保存的密码直接连接
            password = decrypt_password(server.get("encrypted_password", ""))
            if password:
                self.server_immediate_connect.emit(server, password)
            else:
                from qfluentwidgets import MessageBox
                MessageBox("连接失败", "无法获取保存的密码，请重新编辑服务器", self).exec()
    
    def on_switch_root(self, server: dict):
        """切换root请求"""
        self.server_switch_root_request.emit(server)

    def on_monitor_server(self, server: dict):
        """打开服务器实时监控弹窗。"""
        from .server_monitor import ServerMonitorDialog
        dialog = ServerMonitorDialog(server, self)
        dialog.exec()

    def on_schedule_inspection(self):
        """打开定时巡检设置。"""
        dialog = ScheduleInspectionDialog(self.servers, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.schedules_updated.emit()
            QMessageBox.information(self, "保存成功", "定时巡检设置已保存")
    
    def on_edit_server(self, server: dict):
        """编辑服务器"""
        if self.device_states.get(server.get('id')) in ('连接中', '等待巡检', '巡检中'):
            QMessageBox.warning(self, '设备操作中', '请等待该设备的连接或巡检结束后再编辑')
            return
        dialog = AddServerDialog(server, parent=self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            server_info = dialog.get_server_info()
            
            if self.device_states.get(server.get('id')) in ('连接中', '等待巡检', '巡检中'):
                QMessageBox.warning(self, '设备操作中', '该设备正在连接或巡检，请稍后重新编辑')
                return
            if server.get('id') == self.connected_server_id and any(server_info.get(k) != server.get(k) for k in ('host', 'port', 'username', 'product')):
                QMessageBox.warning(self, '请先断开连接', '修改当前设备的主机、端口或用户前，请先断开连接')
                return
            self.commit_inventory([server_info if s.get('id') == server.get('id') else s for s in self.servers])
    
    def on_delete_server(self, server: dict):
        """删除服务器"""
        from qfluentwidgets import MessageBox
        if self.device_states.get(server.get('id')) in ('连接中', '等待巡检', '巡检中'):
            QMessageBox.warning(self, '设备操作中', '请等待该设备的连接或巡检结束后再删除')
            return
        if server.get('id') == self.connected_server_id:
            QMessageBox.warning(self, '请先断开连接', '删除当前已连接设备前，请先断开连接')
            return
        
        reply = MessageBox(
            "确认删除",
            f"确定要删除服务器 {server.get('name') or server.get('host')} 吗？",
            self
        )
        
        if reply.exec():
            self.commit_inventory([s for s in self.servers if s.get('id') != server.get('id')])
    
    def on_add_server(self, checked=False, placement=None):
        """添加服务器"""
        dialog = AddServerDialog(parent=self, placement=placement)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            server_info = dialog.get_server_info()
            
            if any(s.get('id') == server_info.get('id') for s in self.servers):
                QMessageBox.warning(self, '设备已存在', '该主机、端口和用户名已存在，请编辑现有设备')
                return
            self.selected_device_id = server_info['id']
            self.commit_inventory(self.servers + [server_info])

    def add_at_position(self, rack_id, start):
        self.on_add_server(placement=dict(rack_id=rack_id, rack_start_u=start))

    def edit_rack(self, rack=None):
        dialog = RackDialog(rack, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            info = dialog.get_info()
            racks = [info if r.get('id') == info['id'] else r for r in self.racks]
            if not any(r.get('id') == info['id'] for r in self.racks):
                racks.append(info)
            self.commit_inventory(self.servers, racks)

    def delete_rack(self, rack):
        if any(s.get('rack_id') == rack.get('id') for s in self.servers):
            QMessageBox.warning(self, '机柜非空', '请先迁移或下架机柜中的设备，再删除机柜')
            return
        if QMessageBox.question(self, '删除机柜', f'确定删除机柜 {rack.get("code")}？') == QMessageBox.StandardButton.Yes:
            self.commit_inventory(self.servers, [r for r in self.racks if r.get('id') != rack.get('id')])

    def on_rack_action(self, server, action):
        if action == 'edit':
            self.on_edit_server(server)
        elif action == 'delete':
            self.on_delete_server(server)
        elif action == 'root':
            self.on_switch_root(server)
        elif action == 'connect':
            self.on_server_connect(server)
        elif action == 'report':
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl
            path = self.inspection_history.get(server.get('id'), {}).get('report_path', '')
            if not path or not Path(path).is_file():
                QMessageBox.warning(self, '报告不存在', '报告尚未导出或文件已移动，请重新导出')
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
        else:
            self.device_operation_requested.emit(server, action)

    def set_device_state(self, server_id, state=''):
        if server_id:
            if state:
                self.device_states[server_id] = state
            else:
                self.device_states.pop(server_id, None)
        if self.rack_view:
            self.rack_view.update_connected_status(self.connected_server_id, self.is_root)
    
    def set_connected_server(self, server_id: str = None, is_root: bool = False):
        """设置当前连接的服务器"""
        self.connected_server_id = server_id if server_id else None
        self.is_root = is_root
        
        # 更新所有分组的连接状态
        for section in self.product_sections:
            section.update_connected_status(self.connected_server_id, is_root)
        if self.rack_view:
            self.rack_view.update_connected_status(self.connected_server_id, is_root)
        
        # 显示/隐藏下一步按钮
        if self.connected_server_id and not self.inspection_active:
            self.next_step_btn.setVisible(True)
        else:
            self.next_step_btn.setVisible(False)
    
    def on_next_step(self):
        """下一步按钮点击"""
        self.go_to_next_step.emit()
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            QFrame#deviceFilterPanel {background:white; border:1px solid #e0e8ed; border-radius:9px;}
            QLineEdit#deviceSearch {background:#f6f9fb; color:#263f4c; border:1px solid #e4ebef; border-radius:6px; padding:0 10px; font-size:12px;}
            QLineEdit#deviceSearch:focus {border-color:#53b8a9; background:white;}
            QComboBox#roomFilter {background:#f6f9fb; color:#415b68; border:1px solid #e4ebef; border-radius:6px; padding:0 10px; font-size:12px;}
            QComboBox#roomFilter::drop-down {border:0; width:24px;}
            QPushButton#addRackBtn {background:white; border:1px solid #d9e4e9; color:#385563; border-radius:6px; padding:0 12px; font-size:12px;}
            QPushButton#addRackBtn:hover {background:#f0f8f6; border-color:#7fc5b8;}
            /* 页面标题 */
            QLabel#pageTitle {
                font-size: 24px;
                font-weight: bold;
                color: #1f2937;
                padding: 5px;
            }
            
            /* 视图切换按钮 */
            QFrame#viewSwitcher {
                background: #eef2f4;
                border: 1px solid #dce5e9;
                border-radius: 8px;
            }
            QToolButton[viewChoice="true"] {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
            }
            QToolButton[viewChoice="true"]:hover { background: #e0ebe8; }
            QToolButton[viewChoice="true"]:checked {
                background: #d6eee8;
                border-color: #79b9ae;
            }
            QToolButton[viewChoice="true"]:focus { border-color: #147d76; }
            
            /* 按钮样式 */
            QPushButton#addServerBtn {
                background: #159b8c;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 16px;
            }
            QPushButton#addServerBtn:hover {
                background: #3eb8b3;
            }

            QPushButton#scheduleBtn {
                background: white;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 16px;
            }
            QPushButton#scheduleBtn:hover {
                background: #f0fdfa;
                color: #0f766e;
                border-color: #50C9C3;
            }
            
            /* 下一步按钮 */
            QPushButton#nextStepBtn {
                background: #10b981;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 0 20px;
            }
            QPushButton#nextStepBtn:hover {
                background: #059669;
            }
            
            QPushButton#faqBtn {
                background: white;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 16px;
            }
            QPushButton#faqBtn:hover {
                background: #f3f4f6;
                border-color: #50C9C3;
            }
            
            /* 网格项按钮 */
            QPushButton#connectBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton#connectBtn:hover {
                background: #3eb8b3;
            }
            
            QPushButton#editBtn, QPushButton#deleteBtn, QPushButton#menuBtn, QPushButton#scriptBtn {
                background: #f3f4f6;
                color: #374151;
                border: none;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton#editBtn:hover, QPushButton#menuBtn:hover, QPushButton#scriptBtn:hover {
                background: #e5e7eb;
            }
            QPushButton#deleteBtn:hover {
                background: #fef2f2;
                color: #ef4444;
            }
        """)
