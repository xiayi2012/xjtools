"""
聊天组件模块
实现人机对话功能，支持FAQ匹配
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QTextEdit, QLineEdit,
    QFrame, QFileDialog, QMessageBox, QListWidget,
    QListWidgetItem, QDialog, QComboBox, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QTextCursor

import os
import json
from pathlib import Path
from datetime import datetime

from qfluentwidgets import FluentIcon as FIF, PrimaryPushButton

from utils import get_config_dir as _get_config_dir
from .dialog_icons import dialog_icon


class ChatMessage(QFrame):
    """聊天消息组件"""
    
    def __init__(self, message: str, is_user: bool = True, timestamp: str = ""):
        super().__init__()
        self.is_user = is_user
        self.init_ui(message, timestamp)
    
    def init_ui(self, message: str, timestamp: str):
        """初始化UI"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        
        if self.is_user:
            # 用户消息靠右
            layout.addStretch()
            
            msg_frame = QFrame()
            msg_frame.setObjectName("userMsgFrame")
            msg_layout = QVBoxLayout(msg_frame)
            msg_layout.setSpacing(4)
            
            msg_label = QLabel(message)
            msg_label.setObjectName("userMsgLabel")
            msg_label.setWordWrap(True)
            msg_label.setMaximumWidth(500)
            
            time_label = QLabel(timestamp)
            time_label.setObjectName("msgTime")
            time_label.setStyleSheet("font-size: 10px; color: #9ca3af;")
            
            msg_layout.addWidget(msg_label)
            msg_layout.addWidget(time_label)
            
            layout.addWidget(msg_frame)
        else:
            # AI消息靠左
            icon_label = QLabel("🤖")
            icon_label.setStyleSheet("font-size: 20px;")
            layout.addWidget(icon_label)
            
            msg_frame = QFrame()
            msg_frame.setObjectName("aiMsgFrame")
            msg_layout = QVBoxLayout(msg_frame)
            msg_layout.setSpacing(4)
            
            msg_label = QLabel(message)
            msg_label.setObjectName("aiMsgLabel")
            msg_label.setWordWrap(True)
            msg_label.setMaximumWidth(500)
            
            time_label = QLabel(timestamp)
            time_label.setObjectName("msgTime")
            time_label.setStyleSheet("font-size: 10px; color: #9ca3af;")
            
            msg_layout.addWidget(msg_label)
            msg_layout.addWidget(time_label)
            
            layout.addWidget(msg_frame)
            layout.addStretch()
        
        self.apply_styles()
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            QFrame#userMsgFrame {
                background: #50C9C3;
                border-radius: 12px;
                padding: 10px 15px;
            }
            QFrame#aiMsgFrame {
                background: #f3f4f6;
                border-radius: 12px;
                padding: 10px 15px;
            }
            QLabel#userMsgLabel {
                color: white;
                font-size: 14px;
                line-height: 1.4;
            }
            QLabel#aiMsgLabel {
                color: #1f2937;
                font-size: 14px;
                line-height: 1.4;
            }
        """)


class FaqManagerDialog(QDialog):
    """FAQ管理对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FAQ管理")
        self.setWindowIcon(dialog_icon("book.svg"))
        self.setMinimumSize(700, 500)
        self.setModal(True)
        
        self.faqs = []
        self.init_ui()
        self.load_faqs()
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # 说明标签
        hint_label = QLabel("FAQ用于回答用户关于服务器的常见问题。支持上传JSON/TXT文件，每行一个问答对，格式: 问题|答案")
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #6b7280; font-size: 12px; padding: 5px; background: #f9fafb; border-radius: 6px;")
        layout.addWidget(hint_label)
        
        # 顶部按钮
        btn_layout = QHBoxLayout()
        
        upload_btn = QPushButton("上传FAQ文件")
        upload_btn.clicked.connect(self.upload_faq_file)
        btn_layout.addWidget(upload_btn)
        
        add_btn = QPushButton("添加FAQ")
        add_btn.clicked.connect(self.add_faq)
        btn_layout.addWidget(add_btn)
        
        btn_layout.addStretch()
        
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self.clear_faqs)
        btn_layout.addWidget(clear_btn)
        
        layout.addLayout(btn_layout)
        
        # FAQ列表
        self.faq_list = QListWidget()
        self.faq_list.setObjectName("faqList")
        layout.addWidget(self.faq_list, 1)
        
        # 底部按钮
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()
        
        close_btn = QPushButton("关闭")
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(self.close)
        footer_layout.addWidget(close_btn)
        
        layout.addLayout(footer_layout)
        
        self.apply_styles()
    
    def load_faqs(self):
        """加载FAQ列表"""
        config_file = Path(_get_config_dir()) / "faqs.json"
        
        if not config_file.exists():
            return
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                self.faqs = json.load(f)
            
            self.update_faq_list()
        except Exception as e:
            QMessageBox.warning(self, "加载失败", f"无法加载FAQ: {str(e)}")
    
    def save_faqs(self):
        """保存FAQ列表"""
        config_dir = Path(_get_config_dir())
        config_dir.mkdir(exist_ok=True)
        
        config_file = config_dir / "faqs.json"
        
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.faqs, f, indent=2, ensure_ascii=False)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"无法保存FAQ: {str(e)}")
    
    def update_faq_list(self):
        """更新FAQ列表"""
        self.faq_list.clear()
        
        for faq in self.faqs:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, faq)
            
            # 显示问题（答案太长时截断）
            question = faq.get("question", "")
            answer = faq.get("answer", "")
            display = f"Q: {question}\nA: {answer[:50]}..." if len(answer) > 50 else f"Q: {question}\nA: {answer}"
            
            item.setText(display)
            self.faq_list.addItem(item)
    
    def upload_faq_file(self):
        """上传FAQ文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择FAQ文件",
            "",
            "文本文件 (*.txt);;JSON文件 (*.json)"
        )
        
        if not file_path:
            return
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 解析FAQ文件
            if file_path.endswith('.json'):
                # JSON格式
                data = json.loads(content)
                if isinstance(data, list):
                    self.faqs.extend(data)
                elif isinstance(data, dict) and 'faqs' in data:
                    self.faqs.extend(data['faqs'])
            else:
                # TXT格式：每行 "问题|答案"
                for line in content.split('\n'):
                    line = line.strip()
                    if '|' in line:
                        parts = line.split('|', 1)
                        if len(parts) == 2:
                            self.faqs.append({
                                "question": parts[0].strip(),
                                "answer": parts[1].strip()
                            })
            
            self.save_faqs()
            self.update_faq_list()
            
            QMessageBox.information(self, "上传成功", f"成功导入 {len(self.faqs)} 条FAQ")
        
        except Exception as e:
            QMessageBox.warning(self, "上传失败", f"无法解析FAQ文件: {str(e)}")
    
    def add_faq(self):
        """添加FAQ"""
        dialog = FaqEditDialog(self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            faq = dialog.get_faq()
            self.faqs.append(faq)
            self.save_faqs()
            self.update_faq_list()
    
    def clear_faqs(self):
        """清空FAQ"""
        reply = QMessageBox.question(
            self,
            "确认清空",
            "确定要清空所有FAQ吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.faqs = []
            self.save_faqs()
            self.update_faq_list()
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            QListWidget#faqList {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 10px;
                font-size: 13px;
            }
            QListWidget#faqList::item {
                padding: 10px;
                border-bottom: 1px solid #f3f4f6;
            }
            QListWidget#faqList::item:selected {
                background: #f0fdf4;
            }
        """)


class FaqEditDialog(QDialog):
    """FAQ编辑对话框"""
    
    def __init__(self, faq: dict = None, parent=None):
        super().__init__(parent)
        self.faq = faq or {}
        self.is_edit = bool(faq)
        
        self.setWindowTitle("编辑FAQ" if self.is_edit else "添加FAQ")
        self.setWindowIcon(dialog_icon("book.svg"))
        self.setMinimumWidth(500)
        self.setModal(True)
        
        self.init_ui()
        
        if self.is_edit:
            self.question_input.setText(self.faq.get("question", ""))
            self.answer_input.setText(self.faq.get("answer", ""))
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # 问题
        q_layout = QHBoxLayout()
        q_label = QLabel("问题:")
        q_label.setMinimumWidth(60)
        q_layout.addWidget(q_label)
        
        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText("输入常见问题")
        self.question_input.setMinimumHeight(35)
        q_layout.addWidget(self.question_input, 1)
        layout.addLayout(q_layout)
        
        # 答案
        a_layout = QHBoxLayout()
        a_label = QLabel("答案:")
        a_label.setMinimumWidth(60)
        a_layout.addWidget(a_label)
        
        self.answer_input = QTextEdit()
        self.answer_input.setPlaceholderText("输入答案内容")
        self.answer_input.setMinimumHeight(120)
        a_layout.addWidget(self.answer_input, 1)
        layout.addLayout(a_layout)
        
        layout.addStretch()
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        ok_btn = PrimaryPushButton("确定")
        ok_btn.setMinimumWidth(100)
        ok_btn.clicked.connect(self.validate_and_accept)
        btn_layout.addWidget(ok_btn)
        
        layout.addLayout(btn_layout)
    
    def validate_and_accept(self):
        """验证并接受"""
        question = self.question_input.text().strip()
        answer = self.answer_input.toPlainText().strip()
        
        if not question:
            QMessageBox.warning(self, "输入错误", "请输入问题")
            return
        
        if not answer:
            QMessageBox.warning(self, "输入错误", "请输入答案")
            return
        
        self.accept()
    
    def get_faq(self) -> dict:
        """获取FAQ"""
        return {
            "question": self.question_input.text().strip(),
            "answer": self.answer_input.toPlainText().strip()
        }


class PasswordInputDialog(QDialog):
    """密码输入对话框"""
    
    def __init__(self, host: str = "", parent=None):
        super().__init__(parent)
        self.host = host
        self.password = ""
        
        self.setWindowTitle("输入密码")
        self.setWindowIcon(dialog_icon("connect.svg"))
        self.setMinimumWidth(400)
        self.setModal(True)
        
        self.init_ui()
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # 主机信息
        if self.host:
            host_label = QLabel(f"主机: {self.host}")
            host_label.setStyleSheet("font-size: 14px; color: #374151;")
            layout.addWidget(host_label)
        
        # 密码输入
        pwd_layout = QHBoxLayout()
        pwd_label = QLabel("密码:")
        pwd_label.setMinimumWidth(60)
        pwd_layout.addWidget(pwd_label)
        
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("输入服务器密码")
        self.password_input.setMinimumHeight(35)
        self.password_input.returnPressed.connect(self.accept)
        pwd_layout.addWidget(self.password_input, 1)
        layout.addLayout(pwd_layout)
        
        layout.addStretch()
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        ok_btn = PrimaryPushButton("连接")
        ok_btn.setMinimumWidth(100)
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        
        layout.addLayout(btn_layout)
        
        self.apply_styles()
    
    def get_password(self) -> str:
        """获取输入的密码"""
        return self.password_input.text()
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            QDialog {
                background: white;
            }
            QLabel {
                font-size: 14px;
                color: #374151;
            }
            QLineEdit {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #50C9C3;
                outline: none;
            }
        """)


class ChatWidget(QWidget):
    """聊天组件"""
    
    # 信号定义
    message_sent = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.faqs = []
        self.current_server = None
        self.init_ui()
        self.load_faqs()
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 标题栏
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(15, 10, 15, 10)
        
        title_label = QLabel("💬 AI 助手")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1f2937;")
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        # FAQ管理按钮
        faq_btn = QPushButton("FAQ")
        faq_btn.setObjectName("faqBtn")
        faq_btn.setFixedSize(50, 28)
        faq_btn.clicked.connect(self.show_faq_manager)
        header_layout.addWidget(faq_btn)
        
        layout.addLayout(header_layout)
        
        # 消息区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("chatScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("""
            QScrollArea#chatScrollArea {
                border: none;
                background: transparent;
            }
        """)
        
        self.messages_widget = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_widget)
        self.messages_layout.setSpacing(10)
        self.messages_layout.setContentsMargins(15, 10, 15, 10)
        self.messages_layout.addStretch()
        
        self.scroll_area.setWidget(self.messages_widget)
        layout.addWidget(self.scroll_area, 1)
        
        # 提示标签
        self.hint_label = QLabel("连接服务器后，可以输入自然语言描述您的问题，AI将尝试匹配FAQ回答")
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setWordWrap(True)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint_label)
        
        # 输入区域
        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(15, 10, 15, 10)
        input_layout.setSpacing(10)
        
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入您的问题...")
        self.input_field.setMinimumHeight(40)
        self.input_field.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.input_field, 1)
        
        send_btn = QPushButton("发送")
        send_btn.setObjectName("sendBtn")
        send_btn.setFixedWidth(80)
        send_btn.setMinimumHeight(40)
        send_btn.clicked.connect(self.send_message)
        input_layout.addWidget(send_btn)
        
        layout.addLayout(input_layout)
        
        self.apply_styles()
    
    def load_faqs(self):
        config_file = Path(_get_config_dir()) / "faqs.json"
        
        if not config_file.exists():
            self.faqs = []
            return
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                self.faqs = json.load(f)
        except:
            self.faqs = []
    
    def set_server(self, server: dict):
        """设置当前服务器"""
        self.current_server = server
        if server:
            self.hint_label.setText(f"已连接 {server.get('name') or server.get('host')}，可以开始对话")
            self.hint_label.setStyleSheet("""
                QLabel#hintLabel {
                    font-size: 12px;
                    color: #10b981;
                    padding: 8px;
                    background: #f0fdf4;
                    border-radius: 6px;
                }
            """)
        else:
            self.hint_label.setText("连接服务器后，可以输入自然语言描述您的问题，AI将尝试匹配FAQ回答")
            self.hint_label.setStyleSheet("""
                QLabel#hintLabel {
                    font-size: 12px;
                    color: #6b7280;
                    padding: 8px;
                    background: #f9fafb;
                    border-radius: 6px;
                }
            """)
    
    def send_message(self):
        """发送消息"""
        message = self.input_field.text().strip()
        
        if not message:
            return
        
        if not self.current_server:
            QMessageBox.warning(self, "提示", "请先连接服务器")
            return
        
        # 添加用户消息
        self.add_message(message, is_user=True)
        
        # 清空输入框
        self.input_field.clear()
        
        # 发送信号
        self.message_sent.emit(message)
        
        # 模拟AI响应（后续接入真实AI）
        QTimer.singleShot(500, lambda: self.auto_reply(message))
    
    def auto_reply(self, message: str):
        """自动回复（基于FAQ匹配）"""
        self.load_faqs()
        
        # 简单的FAQ匹配
        matched = None
        message_lower = message.lower()
        
        for faq in self.faqs:
            question = faq.get("question", "").lower()
            
            # 简单的包含匹配
            if any(word in message_lower for word in question.split() if len(word) > 2):
                matched = faq
                break
        
        if matched:
            self.add_message(f"根据FAQ匹配回答:\n\n{matched.get('answer', '抱歉，未找到相关答案')}", is_user=False)
        else:
            self.add_message(
                "抱歉，我无法理解您的问题。\n\n"
                "您可以:\n"
                "1. 尝试重新描述您的问题\n"
                "2. 点击「FAQ管理」添加更多问答对\n"
                "3. 联系管理员获取帮助",
                is_user=False
            )
    
    def add_message(self, message: str, is_user: bool = True):
        """添加消息"""
        timestamp = datetime.now().strftime("%H:%M")
        
        msg_widget = ChatMessage(message, is_user, timestamp)
        
        # 在stretch之前插入
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, msg_widget)
        
        # 滚动到底部
        QTimer.singleShot(100, self.scroll_to_bottom)
    
    def scroll_to_bottom(self):
        """滚动到底部"""
        scroll_bar = self.scroll_area.verticalScrollBar()
        if scroll_bar:
            scroll_bar.setValue(scroll_bar.maximum())
    
    def show_faq_manager(self):
        """显示FAQ管理器"""
        dialog = FaqManagerDialog(self)
        dialog.exec()
        
        # 重新加载FAQ
        self.load_faqs()
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            QPushButton#faqBtn {
                background: #f3f4f6;
                color: #374151;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton#faqBtn:hover {
                background: #e5e7eb;
            }
            QLineEdit {
                border: 1px solid #e5e7eb;
                border-radius: 20px;
                padding: 10px 15px;
                font-size: 14px;
                background: white;
            }
            QLineEdit:focus {
                border-color: #50C9C3;
                outline: none;
            }
            QPushButton#sendBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 20px;
            }
            QPushButton#sendBtn:hover {
                background: #3eb8b3;
            }
        """)
