"""
连接页面模块
实现 SSH 连接配置和连接功能
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox,
    QCheckBox, QGroupBox, QTextEdit, QMessageBox,
    QFileDialog, QComboBox, QListWidget, QListWidgetItem,
    QSplitter, QFrame, QDialog, QDialogButtonBox, QSizePolicy,
    QInputDialog, QApplication
)
from qfluentwidgets import PrimaryPushButton, CheckBox
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QMutex, QMutexLocker
from PyQt6.QtGui import QFont

import sys
import os
import json
import base64
import hashlib
import secrets
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.core.ssh_connector import SSHClientWrapper
from .dialog_icons import dialog_icon
from .button_styles import install_button_styles, PRIMARY


class OTPPasswordDialog(QDialog):
    """OTP 密码输入对话框（用于动态密钥验证）"""
    
    def __init__(self, challenge_text: str, parent=None):
        """
        初始化 OTP 密码对话框
        
        Args:
            challenge_text: 服务器返回的挑战信息（包含dynamic key）
            parent: 父窗口
        """
        super().__init__(parent)
        self.setWindowTitle("OTP 验证")
        self.setWindowIcon(dialog_icon("connect.svg"))
        self.setMinimumWidth(450)
        self.setMinimumHeight(280)
        self.setModal(True)
        
        # 提取dynamic key后面的值
        self.dynamic_key = self._extract_dynamic_key(challenge_text)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # 标题
        title_label = QLabel("OTP 二次验证")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #1e3a8a;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # 说明
        instr_label = QLabel("请复制以下动态密钥计算OTP密码：")
        instr_label.setWordWrap(True)
        instr_label.setStyleSheet("color: #6b7280; font-size: 13px;")
        layout.addWidget(instr_label)
        
        # 显示动态密钥（带复制按钮）
        if self.dynamic_key:
            key_group = QGroupBox("动态密钥")
            key_layout = QHBoxLayout(key_group)
            
            # 动态密钥显示
            self.key_display = QLineEdit(self.dynamic_key)
            self.key_display.setReadOnly(True)
            self.key_display.setStyleSheet("""
                background: #fef3c7;
                color: #92400e;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 20px;
                font-weight: bold;
                padding: 12px;
                border: 2px solid #f59e0b;
                border-radius: 8px;
            """)
            key_layout.addWidget(self.key_display)
            
            # 复制按钮
            copy_btn = QPushButton("复制")
            copy_btn.setToolTip("一键复制动态密钥")
            copy_btn.setMinimumWidth(80)
            copy_btn.setStyleSheet("""
                QPushButton {
                    background: #50C9C3;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 10px 15px;
                }
                QPushButton:hover {
                    background: #3BA8A2;
                }
            """)
            copy_btn.clicked.connect(self.copy_dynamic_key)
            key_layout.addWidget(copy_btn)
            
            layout.addWidget(key_group)
        
        # OTP密码输入
        otp_group = QGroupBox("输入计算后的OTP密码")
        otp_layout = QVBoxLayout(otp_group)
        
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("输入计算出的一次性密码")
        self.password_input.setMinimumHeight(40)
        self.password_input.setStyleSheet("""
            font-size: 16px;
            padding: 8px 12px;
            border: 2px solid #3b82f6;
            border-radius: 8px;
        """)
        otp_layout.addWidget(self.password_input)
        
        # 显示/隐藏密码
        self.show_password_checkbox = QCheckBox("显示密码")
        self.show_password_checkbox.toggled.connect(
            lambda checked: self.password_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        otp_layout.addWidget(self.show_password_checkbox)
        
        layout.addWidget(otp_group)
        
        # 提示
        hint_label = QLabel("💡 提示：使用动态密钥和您的密钥计算OTP密码后输入")
        hint_label.setStyleSheet("color: #f59e0b; font-size: 11px;")
        layout.addWidget(hint_label)
        
        layout.addStretch()
        
        # 按钮
        ok_btn = PrimaryPushButton("提交验证")
        ok_btn.setMinimumHeight(40)
        ok_btn.clicked.connect(self.accept)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(40)
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)
        
        # 自动聚焦到密码输入框
        self.password_input.setFocus()
    
    def get_password(self) -> str:
        """获取用户输入的OTP密码"""
        return self.password_input.text()
    
    def _extract_dynamic_key(self, challenge_text: str) -> str:
        """从挑战文本中提取dynamic key后面的值"""
        if not challenge_text:
            return ""
        
        import re
        # 匹配 "dynamic key: xxx" 或 "dynamic key:xxx" 格式（不区分大小写）
        match = re.search(r'dynamic\s*key\s*:\s*(\S+)', challenge_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # 如果没有匹配到，返回原始文本（清理后的）
        return challenge_text.strip()
    
    def copy_dynamic_key(self):
        """复制动态密钥到剪贴板"""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(self.dynamic_key)
        
        # 显示复制成功的提示
        self.key_display.setStyleSheet("""
            background: #d1fae5;
            color: #065f46;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 20px;
            font-weight: bold;
            padding: 12px;
            border: 2px solid #10b981;
            border-radius: 8px;
        """)
        
        # 2秒后恢复原样
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, self._restore_key_style)
    
    def _restore_key_style(self):
        """恢复动态密钥显示样式"""
        self.key_display.setStyleSheet("""
            background: #fef3c7;
            color: #92400e;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 20px;
            font-weight: bold;
            padding: 12px;
            border: 2px solid #f59e0b;
            border-radius: 8px;
        """)


class RootPasswordDialog(QDialog):
    """启用当前连接的 sudo 命令模式。"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        install_button_styles(QApplication.instance())
        self.setObjectName('rootPasswordDialog')
        self.setStyleSheet(f'''
            QDialog#rootPasswordDialog {{background:#f8fafc; color:#3c5664;}}
            QDialog#rootPasswordDialog QLabel {{background:transparent; color:#3c5664;}}
            QLineEdit#sudoPasswordInput {{
                background:#ffffff; color:#263f4c; border:1px solid #cfdee6;
                border-radius:6px; padding:8px 12px; font-size:14px;
                selection-background-color:{PRIMARY}; selection-color:#ffffff;
            }}
            QLineEdit#sudoPasswordInput:hover {{border-color:#9dbbb9;}}
            QLineEdit#sudoPasswordInput:focus {{border-color:{PRIMARY};}}
        ''')
        self.setWindowTitle("启用 root 命令")
        self.setWindowIcon(dialog_icon("connect.svg"))
        self.setMinimumWidth(460)
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        
        title_layout = QHBoxLayout()
        title_label = QLabel("启用 root 命令")
        title_label.setStyleSheet("font-size:20px; font-weight:600; color:#263f4c;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        layout.addLayout(title_layout)
        
        # 说明
        instr_label = QLabel("输入当前登录用户的 sudo 密码，后续命令将以 root 执行。")
        instr_label.setWordWrap(True)
        instr_label.setStyleSheet("color:#647b89; font-size:13px;")
        layout.addWidget(instr_label)
        
        # 密码输入
        password_layout = QHBoxLayout()
        password_label = QLabel("sudo 密码")
        password_label.setStyleSheet("font-size:13px;")
        password_layout.addWidget(password_label)
        
        self.password_input = QLineEdit()
        self.password_input.setObjectName('sudoPasswordInput')
        password_label.setBuddy(self.password_input)
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("输入 sudo 密码")
        self.password_input.setMinimumHeight(40)
        self.password_input.textChanged.connect(self.on_password_changed)
        password_layout.addWidget(self.password_input, 1)
        layout.addLayout(password_layout)
        
        # 显示/隐藏密码
        self.show_password_checkbox = CheckBox("显示密码")
        self.show_password_checkbox.toggled.connect(
            lambda checked: self.password_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        layout.addWidget(self.show_password_checkbox)
        
        layout.addStretch()
        
        # 按钮
        ok_btn = PrimaryPushButton("启用")
        ok_btn.setMinimumWidth(88)
        ok_btn.setMinimumHeight(40)
        ok_btn.setDefault(True)
        ok_btn.setEnabled(False)  # 密码为空时禁用
        ok_btn.clicked.connect(self.accept)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumWidth(88)
        cancel_btn.setMinimumHeight(40)
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)
        
        # 保存按钮引用
        self.ok_btn = ok_btn
        
        # 自动聚焦到密码输入框
        self.password_input.setFocus()
    
    def on_password_changed(self, text):
        """密码输入变化时启用/禁用确认按钮"""
        self.ok_btn.setEnabled(len(text) > 0)
    
    def get_password(self) -> str:
        """获取用户输入的密码"""
        return self.password_input.text()


class OTPDialog(QDialog):
    """OTP 验证码输入对话框（兼容旧版本）"""
    
    def __init__(self, title: str, instructions: str, prompts: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or "OTP 验证")
        self.setWindowIcon(dialog_icon("connect.svg"))
        self.setMinimumWidth(400)
        self.setModal(True)
        
        self.inputs = []
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        if title:
            title_label = QLabel(f"🔐 {title}")
            title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e3a8a;")
            layout.addWidget(title_label)
        
        if instructions:
            instr_label = QLabel(instructions)
            instr_label.setWordWrap(True)
            instr_label.setStyleSheet("color: #6b7280; padding: 5px;")
            layout.addWidget(instr_label)
        
        for prompt_text, is_echo in prompts:
            prompt_label = QLabel(prompt_text)
            prompt_label.setStyleSheet("font-weight: bold;")
            layout.addWidget(prompt_label)
            
            input_field = QLineEdit()
            if not is_echo:
                input_field.setEchoMode(QLineEdit.EchoMode.Password)
            input_field.setPlaceholderText(f"请输入{prompt_text}")
            layout.addWidget(input_field)
            self.inputs.append(input_field)
        
        # 按钮
        ok_btn = PrimaryPushButton("确定")
        ok_btn.setMinimumHeight(40)
        ok_btn.clicked.connect(self.accept)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(40)
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)
    
    def get_answers(self) -> list:
        return [input_field.text() for input_field in self.inputs]


from utils import get_config_dir as _get_config_dir


def get_config_dir() -> Path:
    return Path(_get_config_dir())


def get_encryption_key() -> bytes:
    config_dir = get_config_dir()
    config_dir.mkdir(exist_ok=True)
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
        # 如果加密失败，使用简单base64编码（兼容性）
        return base64.b64encode(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """解密密码"""
    try:
        key = get_encryption_key()
        f = Fernet(key)
        return f.decrypt(base64.urlsafe_b64decode(encrypted.encode())).decode()
    except Exception:
        # 尝试base64解码（旧格式兼容）
        try:
            return base64.b64decode(encrypted.encode()).decode()
        except Exception:
            return ""


class ConnectionWorker(QThread):
    """连接工作线程（支持OTP认证）"""
    
    connection_result = pyqtSignal(bool, str, dict)
    otp_challenge = pyqtSignal(str)  # challenge_text
    
    def __init__(self, connection_params: dict, use_otp: bool = False):
        super().__init__()
        self.connection_params = connection_params
        self.use_otp = use_otp
        self.otp_response = None
        self.otp_mutex = QMutex()
        import threading
        self.otp_event = threading.Event()
    
    def run(self):
        """执行连接"""
        print(f"[DEBUG] ConnectionWorker.run() started, use_otp={self.use_otp}")
        print(f"[DEBUG] connection_params host={self.connection_params.get('host')}")
        
        ssh = SSHClientWrapper()
        
        if self.use_otp:
            # 使用OTP回调方式连接
            def otp_callback(challenge_text):
                print(f"[DEBUG] OTP callback invoked with challenge: {repr(challenge_text[:100] if challenge_text else None)}")
                self.otp_event.clear()
                # 发送OTP请求信号到UI线程
                self.otp_challenge.emit(challenge_text)
                
                # 等待用户输入（最多5分钟）
                self.otp_event.wait(300)
                
                # 获取用户响应
                with QMutexLocker(self.otp_mutex):
                    response = self.otp_response
                    self.otp_response = None
                
                if response is None:
                    raise Exception("OTP 验证超时或已取消")
                
                return response
            
            success, message = ssh.connect_with_otp_callback(
                otp_challenge_callback=otp_callback,
                **{k: v for k, v in self.connection_params.items() if k != 'use_otp'}
            )
        else:
            # 普通连接
            success, message = ssh.connect(**self.connection_params)
        
        if success:
            self.connection_result.emit(True, message, {
                'ssh_client': ssh,
                **self.connection_params
            })
        else:
            self.connection_result.emit(False, message, {})
    
    def set_otp_response(self, otp_password: str):
        """设置OTP响应并唤醒等待线程"""
        with QMutexLocker(self.otp_mutex):
            self.otp_response = otp_password
        if self.otp_event:
            self.otp_event.set()


class ConnectionPage(QWidget):
    """连接页面"""
    
    # 信号定义
    connection_success = pyqtSignal(dict)
    connection_failed = pyqtSignal(str)
    request_navigate = pyqtSignal(int)  # 请求导航到指定页面
    
    def __init__(self):
        """初始化连接页面"""
        super().__init__()
        self.ssh_client = None
        self.connection_worker = None
        self.current_product = ""  # 当前产品名称
        self.init_ui()
    
    def set_product(self, product_name: str):
        """设置当前产品，清空表单，重新加载历史连接"""
        self.current_product = product_name
        
        # 更新产品标签
        if hasattr(self, 'product_label'):
            if product_name:
                self.product_label.setText(f"当前产品: {product_name}")
            else:
                self.product_label.setText("当前产品: 未选择")
            self.product_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #50C9C3; background: #F0FAFA; padding: 8px; border-radius: 4px;")
        
        # 清空连接表单
        if hasattr(self, 'host_input'):
            self.host_input.clear()
        if hasattr(self, 'port_input'):
            self.port_input.setValue(22)
        if hasattr(self, 'username_input'):
            self.username_input.clear()
        if hasattr(self, 'password_input'):
            self.password_input.clear()
        
        # 清空状态文本
        if hasattr(self, 'status_text'):
            self.status_text.clear()
            self.status_text.append(f"已切换到产品: {product_name}")
            self.status_text.append("请输入服务器连接信息")
        
        # 重新加载历史连接（按产品过滤）
        if hasattr(self, 'history_list'):
            self.load_connection_history()
    
    @property
    def use_otp(self) -> bool:
        """根据产品判断是否使用OTP"""
        # 办公智盾默认使用OTP
        return self.current_product == "办公智盾"
    
    def init_ui(self):
        """初始化 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 20)
        layout.setSpacing(15)
        
        # 标题
        title_label = QLabel("SSH 连接配置")
        title_label.setObjectName("pageTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(title_label)
        
        # 当前产品标签
        self.product_label = QLabel("当前产品: 未选择")
        self.product_label.setObjectName("productLabel")
        self.product_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.product_label)
        
        # 使用分割器实现左右布局
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(0)  # 不显示拖动条
        splitter.setChildrenCollapsible(False)  # 不允许折叠
        splitter.setSizes([600, 300])
        
        # 左侧：连接配置组
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 30, 0, 0)
        left_layout.setSpacing(12)
        
        config_group = QGroupBox("服务器配置")
        config_group.setObjectName("configGroup")
        config_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # 外部垂直布局 - 用于将按钮推到顶部
        config_outer_layout = QVBoxLayout(config_group)
        
        # 内部表单布局 - 用于输入字段
        config_layout = QFormLayout()
        config_layout.setSpacing(18)
        config_layout.setVerticalSpacing(18)
        config_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        # 主机地址和端口同一行
        host_port_layout = QHBoxLayout()
        host_port_layout.setSpacing(10)
        
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("例如: 192.168.1.100")
        self.host_input.setMinimumHeight(32)
        self.host_input.setMinimumWidth(220)
        host_port_layout.addWidget(self.host_input, 1)
        
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(22)
        self.port_input.setMinimumHeight(32)
        self.port_input.setMinimumWidth(50)
        self.port_input.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        host_port_layout.addWidget(self.port_input)
        
        config_layout.addRow("主机地址:", host_port_layout)
        
        # 用户名
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("例如: root")
        self.username_input.setMinimumHeight(32)
        config_layout.addRow("用户名:", self.username_input)
        
        # 密码输入（默认使用密码认证）
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("输入密码")
        self.password_input.setMinimumHeight(32)
        config_layout.addRow("密码:", self.password_input)
        
        # 保存连接信息
        self.save_connection_checkbox = QCheckBox("保存连接信息")
        self.save_connection_checkbox.setChecked(True)
        self.save_connection_checkbox.setObjectName("saveConnectionCheckBox")
        config_layout.addRow("", self.save_connection_checkbox)
        
        config_outer_layout.addLayout(config_layout)
        
        # 弹性空间 - 将按钮推到顶部
        config_outer_layout.addStretch(1)
        
        # 按钮区域 - 放在服务器配置框内底部
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)
        
        self.connect_btn = QPushButton("连接服务器")
        self.connect_btn.setObjectName("connectButton")
        self.connect_btn.setMinimumHeight(36)
        self.connect_btn.clicked.connect(self.connect_to_server)
        button_layout.addWidget(self.connect_btn)
        
        self.disconnect_btn = QPushButton("断开连接")
        self.disconnect_btn.setObjectName("disconnectButton")
        self.disconnect_btn.setMinimumHeight(36)
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self.disconnect_from_server)
        button_layout.addWidget(self.disconnect_btn)
        
        # 切换为root按钮 - 连接成功后才可用
        self.switch_root_btn = QPushButton("切换为root")
        self.switch_root_btn.setObjectName("switchRootButton")
        self.switch_root_btn.setMinimumHeight(36)
        self.switch_root_btn.setEnabled(False)
        self.switch_root_btn.clicked.connect(self.switch_to_root)
        button_layout.addWidget(self.switch_root_btn)
        
        # 下一步按钮 - 连接成功后才可用
        self.next_step_btn = QPushButton("下一步")
        self.next_step_btn.setObjectName("nextStepButton")
        self.next_step_btn.setMinimumHeight(36)
        self.next_step_btn.setEnabled(False)
        self.next_step_btn.clicked.connect(self.go_to_next_step)
        button_layout.addWidget(self.next_step_btn)
        
        config_outer_layout.addLayout(button_layout)  # 按钮添加到外部布局底部
        
        left_layout.addWidget(config_group, 1)  # stretch factor 1
        
        # 历史连接列表 - 单独模块，在服务器配置下方
        history_group = QGroupBox("历史连接")
        history_group.setObjectName("historyGroup")
        history_layout = QVBoxLayout(history_group)
        history_layout.setContentsMargins(5, 10, 5, 5)
        history_layout.setSpacing(5)
        
        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_list.setMinimumHeight(80)
        self.history_list.setMaximumHeight(120)
        self.history_list.itemClicked.connect(self.on_history_item_clicked)
        history_layout.addWidget(self.history_list)
        
        left_layout.addWidget(history_group)
        
        splitter.addWidget(left_widget)
        
        # 右侧：连接状态
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 30, 0, 0)
        right_layout.setSpacing(10)
        
        status_group = QGroupBox("连接状态")
        status_group.setObjectName("statusGroup")
        status_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        status_layout = QVBoxLayout(status_group)
        
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setPlaceholderText("连接状态将在这里显示...")
        status_layout.addWidget(self.status_text, 1)  # stretch factor 1
        
        right_layout.addWidget(status_group, 1)  # stretch factor 1
        
        splitter.addWidget(right_widget)
        
        layout.addWidget(splitter, 1)  # stretch factor 1
        
        # 加载历史连接
        self.load_connection_history()
        
        # 应用样式
        self.apply_styles()
    
    def connect_to_server(self):
        """连接到服务器"""
        # 验证输入
        host = self.host_input.text().strip()
        if not host:
            QMessageBox.warning(self, "输入错误", "请输入主机地址")
            return
        
        username = self.username_input.text().strip()
        if not username:
            QMessageBox.warning(self, "输入错误", "请输入用户名")
            return
        
        password = self.password_input.text()
        if not password:
            QMessageBox.warning(self, "输入错误", "请输入密码")
            return
        
        # 构建连接参数
        connection_params = {
            'host': host,
            'port': self.port_input.value(),
            'username': username,
            'password': password
        }
        
        # 禁用连接按钮
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("连接中...")
        
        # 更新状态
        self.status_text.append(f"正在连接到 {host}:{self.port_input.value()}...")
        
        # 根据产品判断是否使用OTP模式
        use_otp = self.use_otp
        
        self.connection_worker = ConnectionWorker(connection_params, use_otp=use_otp)
        self.connection_worker.connection_result.connect(self.on_connection_result)
        self.connection_worker.otp_challenge.connect(self.on_otp_challenge)
        self.connection_worker.start()
    
    def on_otp_challenge(self, challenge_text: str):
        """处理OTP挑战 - 服务器返回的动态密钥"""
        # 显示挑战信息
        self.status_text.append(f"\n{'='*50}")
        self.status_text.append(f"🔐 服务器返回OTP挑战")
        self.status_text.append(f"{'='*50}")
        
        # 解析并显示dynamic key
        if challenge_text:
            # 显示挑战内容（截取关键部分）
            lines = challenge_text.strip().split('\n')
            for line in lines:
                line = line.strip()
                if line and not line.startswith('~') and not line.startswith('$'):
                    self.status_text.append(f"  {line}")
        
        self.status_text.append(f"{'='*50}")
        self.status_text.append(f"请计算OTP密码并输入：")
        
        # 显示OTP输入对话框
        dialog = OTPPasswordDialog(challenge_text, self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            otp_password = dialog.get_password()
            if self.connection_worker:
                self.connection_worker.set_otp_response(otp_password)
                self.status_text.append("✓ 已提交OTP密码，正在验证...")
        else:
            # 用户取消
            self.status_text.append("✗ 用户取消验证")
            if self.connection_worker:
                self.connection_worker.set_otp_response("")
    
    def on_connection_result(self, success: bool, message: str, connection_info: dict):
        """连接结果处理"""
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("连接服务器")
        
        if success:
            self.ssh_client = connection_info.get('ssh_client')
            self.connection_info = connection_info  # 保存连接信息
            self.status_text.append(f"✓ {message}")
            self.status_text.append(f"  主机: {connection_info.get('host')}:{connection_info.get('port')}")
            self.status_text.append(f"  用户: {connection_info.get('username')}")
            
            # 验证连接状态 - 执行ls命令测试
            if self.ssh_client and hasattr(self.ssh_client, 'execute'):
                try:
                    # 执行ls命令测试
                    ls_result = self.ssh_client.execute("ls -la /")
                    
                    if ls_result[0] == 0:
                        self.status_text.append(f"  ✓ 连接验证成功")
                    else:
                        # 尝试简单命令
                        whoami_result = self.ssh_client.execute("whoami")
                        if whoami_result[0] == 0:
                            self.status_text.append(f"  ✓ 连接验证成功")
                        else:
                            self.status_text.append(f"  ⚠️ 连接验证返回码: {ls_result[0]}")
                except Exception as e:
                    self.status_text.append(f"  ✗ 连接验证异常: {str(e)}")
            
            # 启用断开按钮、切换root按钮和下一步按钮
            self.disconnect_btn.setEnabled(True)
            self.switch_root_btn.setEnabled(True)
            self.switch_root_btn.setText("切换为root")  # 重置按钮文字（防止重连时还是"已Root"）
            self.next_step_btn.setEnabled(True)
            
            # 发送连接成功信号
            self.connection_success.emit(connection_info)
            
            # 保存连接信息
            if self.save_connection_checkbox.isChecked():
                self.save_connection_info(connection_info)
        else:
            self.status_text.append(f"✗ {message}")
            # 如果使用OTP且失败，显示提示
            if self.use_otp:
                if "不支持OTP" in message or "keyboard-interactive" in message:
                    self.status_text.append(f"\n⚠️ 提示：服务器可能未配置OTP认证")
                    self.status_text.append(f"   请确认服务器已启用 keyboard-interactive 认证方式")
            self.connection_failed.emit(message)
    
    def disconnect_from_server(self):
        """断开服务器连接"""
        if self.ssh_client:
            self.ssh_client.disconnect()
            self.ssh_client = None
        
        self.disconnect_btn.setEnabled(False)
        self.switch_root_btn.setEnabled(False)
        self.switch_root_btn.setText("切换为root")  # 重置按钮文字
        self.next_step_btn.setEnabled(False)
        self.status_text.append("已断开连接")

    def switch_to_root(self):
        if not self.ssh_client:
            return
        dialog = RootPasswordDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        ok, error = self.ssh_client.enable_sudo(dialog.get_password())
        if ok:
            self.status_text.append('已启用 root 命令')
            self.switch_root_btn.setEnabled(False)
            self.switch_root_btn.setText('root 命令已启用')
        else:
            self.status_text.append(error)

    def go_to_next_step(self):
        """跳转到下一步（脚本选择页面）"""
        # 发送导航请求信号，切换到脚本选择页面（索引2）
        self.request_navigate.emit(2)
    
    def save_connection_info(self, connection_info: dict):
        """保存连接信息（密码加密保存）"""
        config_dir = get_config_dir()
        config_dir.mkdir(exist_ok=True)
        
        config_file = config_dir / "connections.json"
        
        # 读取现有配置
        connections = []
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    connections = json.load(f)
            except Exception:
                connections = []
        
        # 添加新连接（保存加密后的密码，包含产品信息）
        new_connection = {
            'host': connection_info.get('host'),
            'port': connection_info.get('port'),
            'username': connection_info.get('username'),
            'auth_method': '密码认证',
            'product': self.current_product  # 关联到当前产品
        }
        
        # 保存加密后的密码
        password = self.password_input.text()
        if password:
            new_connection['encrypted_password'] = encrypt_password(password)
        
        # 检查是否已存在（按产品+主机+端口+用户名）
        exists = False
        for i, conn in enumerate(connections):
            if (conn.get('host') == new_connection['host'] and 
                conn.get('port') == new_connection['port'] and
                conn.get('username') == new_connection['username'] and
                conn.get('product') == new_connection['product']):
                connections[i] = new_connection
                exists = True
                break
        
        if not exists:
            connections.append(new_connection)
        
        # 保存配置
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(connections, f, indent=2, ensure_ascii=False)
            self.status_text.append("✓ 连接信息已保存（密码已加密）")
        except Exception as e:
            self.status_text.append(f"✗ 保存连接信息失败: {str(e)}")
        
        # 刷新历史列表
        self.load_connection_history()
    
    def load_connection_history(self):
        """加载历史连接列表"""
        self.history_list.clear()
        
        config_file = get_config_dir() / "connections.json"
        
        if not config_file.exists():
            return
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                all_connections = json.load(f)
            
            # 按当前产品过滤
            for conn in all_connections:
                conn_product = conn.get('product', '')
                # 只显示当前产品下的连接（旧数据没有product字段会被过滤掉）
                if self.current_product and conn_product != self.current_product:
                    continue
                # 如果没有设置当前产品，则只显示也没有产品的旧数据
                if not self.current_product and conn_product:
                    continue
                
                host = conn.get('host', '')
                port = conn.get('port', 22)
                username = conn.get('username', '')
                auth_method = conn.get('auth_method', '密码认证')
                has_password = 'encrypted_password' in conn and conn['encrypted_password']
                
                display_text = f"{username}@{host}:{port}"
                tooltip = f"认证方式: {auth_method}"
                if conn_product:
                    tooltip += f"\n产品: {conn_product}"
                if has_password:
                    tooltip += "\n✓ 已保存密码"
                
                # 创建自定义item widget
                item_widget = QWidget()
                item_layout = QHBoxLayout(item_widget)
                item_layout.setContentsMargins(5, 2, 5, 2)
                item_layout.setSpacing(10)
                
                # 连接信息标签
                info_label = QLabel(display_text)
                info_label.setStyleSheet("font-size: 13px; color: #374151;")
                info_label.setToolTip(tooltip)
                item_layout.addWidget(info_label, 1)
                
                # 选择按钮
                select_btn = QPushButton("选择")
                select_btn.setObjectName("selectHistoryBtn")
                select_btn.setFixedSize(50, 24)
                select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                select_btn.setStyleSheet("""
                    QPushButton#selectHistoryBtn {
                        background-color: #50C9C3;
                        color: white;
                        border: none;
                        border-radius: 0px;
                        font-size: 12px;
                        font-weight: bold;
                    }
                    QPushButton#selectHistoryBtn:hover {
                        background-color: #3eb8b3;
                    }
                    QPushButton#selectHistoryBtn:pressed {
                        background-color: #2a9d97;
                    }
                """)
                select_btn.clicked.connect(lambda checked, c=conn: self.select_history_connection(c))
                item_layout.addWidget(select_btn)
                
                # 删除按钮
                delete_btn = QPushButton("删除")
                delete_btn.setObjectName("deleteHistoryBtn")
                delete_btn.setFixedSize(50, 24)
                delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                delete_btn.setStyleSheet("""
                    QPushButton#deleteHistoryBtn {
                        background-color: #6b7280;
                        color: white;
                        border: none;
                        border-radius: 0px;
                        font-size: 12px;
                        font-weight: bold;
                    }
                    QPushButton#deleteHistoryBtn:hover {
                        background-color: #ef4444;
                    }
                    QPushButton#deleteHistoryBtn:pressed {
                        background-color: #dc2626;
                    }
                """)
                delete_btn.clicked.connect(lambda checked, c=conn: self.delete_history_connection(c))
                item_layout.addWidget(delete_btn)
                
                # 创建list item并设置自定义widget
                list_item = QListWidgetItem()
                list_item.setData(Qt.ItemDataRole.UserRole, conn)
                self.history_list.addItem(list_item)
                self.history_list.setItemWidget(list_item, item_widget)
        except Exception:
            pass
    
    def on_history_item_clicked(self, item: QListWidgetItem):
        """点击历史连接项 - 可选择或什么都不做"""
        pass  # 点击按钮处理，不需要点击item本身做任何事
    
    def select_history_connection(self, conn_data: dict):
        """选择历史连接"""
        if not conn_data:
            return
        
        # 填充表单
        self.host_input.setText(conn_data.get('host', ''))
        self.port_input.setValue(conn_data.get('port', 22))
        self.username_input.setText(conn_data.get('username', ''))
        
        # 解密并填充密码
        encrypted_password = conn_data.get('encrypted_password', '')
        if encrypted_password:
            try:
                password = decrypt_password(encrypted_password)
                self.password_input.setText(password)
            except Exception:
                pass
    
    def delete_history_connection(self, conn_data: dict):
        """删除指定的历史连接"""
        if not conn_data:
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除连接 {conn_data.get('username', '')}@{conn_data.get('host', '')}:{conn_data.get('port', 22)} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        config_file = get_config_dir() / "connections.json"
        
        if not config_file.exists():
            return
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                connections = json.load(f)
            
            connections = [
                conn for conn in connections
                if not (conn.get('host') == conn_data.get('host') and
                       conn.get('port') == conn_data.get('port') and
                       conn.get('username') == conn_data.get('username') and
                       conn.get('product') == conn_data.get('product'))
            ]
            
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(connections, f, indent=2, ensure_ascii=False)
            
            self.load_connection_history()
            self.status_text.append("已删除选中的连接记录")
            
        except Exception as e:
            QMessageBox.warning(self, "删除失败", f"无法删除: {str(e)}")
    
    def delete_selected_history(self):
        """删除选中的历史连接（兼容旧调用）"""
        current_row = self.history_list.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "提示", "请先选择要删除的连接")
            return
        
        list_item = self.history_list.item(current_row)
        conn_data = list_item.data(Qt.ItemDataRole.UserRole)
        self.delete_history_connection(conn_data)
    
    def clear_all_history(self):
        """清空所有历史连接"""
        import json
        from pathlib import Path
        
        if self.history_list.count() == 0:
            QMessageBox.information(self, "提示", "历史记录为空")
            return
        
        # 确认清空
        reply = QMessageBox.question(
            self,
            "确认清空",
            "确定要清空所有历史连接记录吗？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        config_file = get_config_dir() / "connections.json"
        
        try:
            if config_file.exists():
                with open(config_file, 'w', encoding='utf-8') as f:
                    json.dump([], f)
            
            self.history_list.clear()
            self.status_text.append("已清空所有历史连接记录")
            
        except Exception as e:
            QMessageBox.warning(self, "清空失败", f"无法清空: {str(e)}")
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            /* 配置组 */
            QGroupBox#configGroup {
                font-size: 14px;
                font-weight: bold;
                color: #1A1A1A;
                border: 1px solid #E1E1E1;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            
            /* 页面标题 */
            QLabel#pageTitle {
                font-size: 28px;
                font-weight: bold;
                color: #50C9C3;
                padding: 10px;
            }
            
            QGroupBox#configGroup::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            
            /* 输入框 */
            QLineEdit, QSpinBox, QComboBox {
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 14px;
            }
            
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border-color: #50C9C3;
                outline: none;
            }
            
            /* 表单标签 - 使标签字体更大、与输入框对齐 */
            QFormLayout {
                font-size: 14px;
            }
            
            QFormLayout::item {
                spacing: 18px;
            }
            
            QFormLayout > QWidget > QLabel {
                font-size: 14px;
                min-height: 38px;
                padding-right: 10px;
            }
            
            /* 保存连接信息复选框 */
            QCheckBox#saveConnectionCheckBox {
                spacing: 8px;
                font-size: 13px;
                padding: 4px;
            }
            
            QCheckBox#saveConnectionCheckBox::indicator {
                width: 20px;
                height: 20px;
                border-radius: 4px;
                border: 2px solid #A0A0A0;
                background: white;
            }
            
            QCheckBox#saveConnectionCheckBox::indicator:checked {
                border: 2px solid #50C9C3;
                background: #50C9C3;
            }
            
            QCheckBox#saveConnectionCheckBox::indicator:hover {
                border-color: #50C9C3;
            }
            
            /* 连接按钮 */
            QPushButton#connectButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
                min-height: 36px;
            }
            
            QPushButton#connectButton:hover {
                background: #3BA8A2;
            }
            
            QPushButton#connectButton:disabled {
                background: #A0A0A0;
            }
            
            /* 断开按钮 */
            QPushButton#disconnectButton {
                background: #5B5B5B;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
                min-height: 36px;
            }
            
            QPushButton#disconnectButton:hover {
                background: #3BA8A2;
            }
            
            QPushButton#disconnectButton:disabled {
                background: #A0A0A0;
            }
            
            /* 清空历史按钮 */
            QPushButton#clearHistoryButton {
                background: #5B5B5B;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
                min-height: 36px;
            }
            
            QPushButton#clearHistoryButton:hover {
                background: #3D3D3D;
            }
            
            QPushButton#clearHistoryButton:disabled {
                background: #A0A0A0;
            }
            
            /* 切换root按钮 */
            QPushButton#switchRootButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
                min-height: 36px;
            }
            
            QPushButton#switchRootButton:hover {
                background: #3BA8A2;
            }
            
            QPushButton#switchRootButton:disabled {
                background: #A0A0A0;
                color: #ffffff;
            }
            
            /* 下一步按钮 */
            QPushButton#nextStepButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
                min-height: 36px;
            }
            
            QPushButton#nextStepButton:hover {
                background: #3BA8A2;
            }
            
            QPushButton#nextStepButton:disabled {
                background: #A0A0A0;
            }
            
            /* 状态组 */
            QGroupBox#statusGroup {
                font-size: 14px;
                font-weight: bold;
                color: #1A1A1A;
                border: 1px solid #E1E1E1;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            
            QGroupBox#statusGroup::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            
            /* 状态文本框 */
            QTextEdit {
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                padding: 10px;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                background: #FAFAFA;
            }
            
            /* 历史连接组 */
            QGroupBox#historyGroup {
                font-size: 14px;
                font-weight: bold;
                color: #1A1A1A;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            
            QGroupBox#historyGroup::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            
            /* 历史连接列表 */
            QListWidget#historyList {
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                padding: 5px;
                font-size: 13px;
                background: white;
                alternate-background-color: #f9fafb;
            }
            
            QListWidget#historyList::item {
                padding: 8px;
                border-bottom: 1px solid #f3f4f6;
            }
            
            QListWidget#historyList::item:selected {
                background: #eff6ff;
                color: #1e3a8a;
            }
            
            /* 历史连接选择按钮 */
            QPushButton#selectHistoryBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 0px;
                font-size: 12px;
                padding: 4px 8px;
            }
            QPushButton#selectHistoryBtn:hover {
                background: #3eb8b3;
            }
            
            /* 历史连接删除按钮 */
            QPushButton#deleteHistoryBtn {
                background: #6b7280;
                color: white;
                border: none;
                border-radius: 0px;
                font-size: 12px;
                padding: 4px 8px;
            }
            QPushButton#deleteHistoryBtn:hover {
                background: #ef4444;
            }
            
            QListWidget#historyList::item:hover {
                background: #f3f4f6;
            }
            
            /* 历史操作按钮 */
            QPushButton#refreshHistoryButton,
            QPushButton#deleteHistoryButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 15px;
                font-size: 12px;
            }
            
            QPushButton#refreshHistoryButton:hover,
            QPushButton#deleteHistoryButton:hover {
                background: #4a5059;
            }
            
            QPushButton#clearHistoryButton {
                background: #5c6370;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 15px;
                font-size: 12px;
            }
            
            QPushButton#clearHistoryButton:hover {
                background: #4a5059;
            }
        """)
