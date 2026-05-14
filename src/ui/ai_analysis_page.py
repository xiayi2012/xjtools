"""
AI 分析页面模块
实现将巡检结果发送给AI大模型进行分析
"""

import os
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QGroupBox, QTextEdit,
    QComboBox, QLineEdit, QScrollArea, QMessageBox,
    QSplitter, QFrame, QTabWidget, QTextBrowser, QDialog, QDialogButtonBox,
    QSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QFont, QColor
from qfluentwidgets import PrimaryPushButton, MessageBox

from utils import get_config_dir as _get_config_dir
from .dialog_icons import dialog_icon


def get_ai_config_path() -> str:
    config_dir = Path(_get_config_dir())
    config_dir.mkdir(exist_ok=True)
    return str(config_dir / "ai_config.json")


class AIAnalysisWorker(QThread):
    """AI 分析工作线程"""
    
    analysis_progress = pyqtSignal(str)  # 进度信息
    analysis_completed = pyqtSignal(str, str)  # (result, error_msg)
    
    def __init__(self, api_config: dict, inspection_data: str, prompt: str):
        super().__init__()
        self.api_config = api_config
        self.inspection_data = inspection_data
        self.prompt = prompt
    
    def run(self):
        """执行AI分析"""
        try:
            self.analysis_progress.emit("正在连接AI服务...")
            
            # 根据不同的API类型调用不同的处理方法
            api_type = self.api_config.get('api_type', 'openai')
            
            self.analysis_progress.emit(f"API类型: {api_type}")
            self.analysis_progress.emit(f"Base URL: {self.api_config.get('base_url', '未设置')}")
            self.analysis_progress.emit(f"模型: {self.api_config.get('model', '未设置')}")
            
            if api_type == 'openai':
                result = self._call_openai_api()
            elif api_type == 'claude':
                result = self._call_claude_api()
            elif api_type == 'azure':
                result = self._call_azure_api()
            elif api_type == 'local':
                result = self._call_local_api()
            else:
                result = self._call_openai_api()  # 默认使用OpenAI格式
            
            self.analysis_completed.emit(result, "")
            
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            print(f"[ERROR] AI分析异常:\n{error_detail}")
            error_msg = str(e) if str(e) else f"未知错误: {type(e).__name__}"
            self.analysis_completed.emit("", f"AI分析失败: {error_msg}")
    
    def _call_openai_api(self) -> str:
        """调用 OpenAI API"""
        import urllib.request
        import urllib.error
        import json
        
        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', 'https://api.openai.com/v1')
        model = self.api_config.get('model', 'gpt-3.5-turbo')
        
        self.analysis_progress.emit("正在发送请求到 AI 服务...")
        
        # 构建请求
        url = f"{base_url}/chat/completions"
        
        # 调试信息：显示请求配置（隐藏API Key）
        masked_key = api_key[:8] + "****" + api_key[-4:] if len(api_key) > 12 else "****"
        self.analysis_progress.emit(f"请求URL: {url}")
        self.analysis_progress.emit(f"模型: {model}")
        self.analysis_progress.emit(f"API Key: {masked_key}")
        
        messages = [
            {
                "role": "system",
                "content": self.prompt
            },
            {
                "role": "user",
                "content": f"请分析以下巡检结果：\n\n{self.inspection_data}"
            }
        ]
        
        data = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4000
        }
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        self.analysis_progress.emit("等待AI响应...")
        
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode('utf-8'))
                content = result['choices'][0]['message']['content']
                return content
        except urllib.error.HTTPError as e:
            # 读取服务器返回的详细错误信息
            error_body = e.read().decode('utf-8', errors='ignore')
            try:
                error_json = json.loads(error_body)
                error_msg = error_json.get('error', {}).get('message', error_body)
                error_code = error_json.get('error', {}).get('code', e.code)
            except:
                error_msg = error_body
                error_code = e.code
            
            # 构建详细的错误信息
            detail_error = f"HTTP {e.code} 错误\n"
            detail_error += f"错误代码: {error_code}\n"
            detail_error += f"错误信息: {error_msg}\n"
            detail_error += f"请求URL: {url}\n"
            detail_error += f"模型: {model}\n"
            
            # 针对常见错误提供解决建议
            if e.code == 401:
                detail_error += "\n【可能原因】\n"
                detail_error += "1. API Key 无效或已过期\n"
                detail_error += "2. API Key 格式错误（包含多余空格）\n"
                detail_error += "3. 账户未激活或余额不足\n"
                detail_error += "\n【解决建议】\n"
                detail_error += "1. 登录平台重新获取 API Key\n"
                detail_error += "2. 检查 API Key 是否有空格或换行符\n"
                detail_error += "3. 确认账户状态和余额"
            elif e.code == 403:
                detail_error += "\n【可能原因】\n"
                detail_error += "1. 没有访问该模型的权限\n"
                detail_error += "2. API Key 类型不匹配\n"
                detail_error += "\n【解决建议】\n"
                detail_error += "1. 确认您的 API Key 有权限访问该模型\n"
                detail_error += "2. 尝试使用其他模型名称"
            elif e.code == 404:
                detail_error += "\n【可能原因】\n"
                detail_error += "1. API 地址（Base URL）错误\n"
                detail_error += "2. 模型名称不存在\n"
                detail_error += "\n【解决建议】\n"
                detail_error += "1. 检查 Base URL 是否正确\n"
                detail_error += "2. 确认模型名称是否正确"
            elif e.code == 429:
                detail_error += "\n【可能原因】\n"
                detail_error += "1. 请求频率超限\n"
                detail_error += "2. 达到 API 调用配额上限\n"
                detail_error += "\n【解决建议】\n"
                detail_error += "1. 等待一段时间后重试\n"
                detail_error += "2. 检查账户的 API 调用配额"
            
            raise Exception(detail_error)
    
    def _call_claude_api(self) -> str:
        """调用 Claude API"""
        import urllib.request
        import json
        
        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', 'https://api.anthropic.com/v1')
        model = self.api_config.get('model', 'claude-3-sonnet-20240229')
        
        self.analysis_progress.emit("正在发送请求到 Claude...")
        
        url = f"{base_url}/messages"
        
        data = {
            "model": model,
            "max_tokens": 4000,
            "system": self.prompt,
            "messages": [
                {
                    "role": "user",
                    "content": f"请分析以下巡检结果：\n\n{self.inspection_data}"
                }
            ]
        }
        
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        self.analysis_progress.emit("等待AI响应...")
        
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode('utf-8'))
            content = result['content'][0]['text']
            return content
    
    def _call_azure_api(self) -> str:
        """调用 Azure OpenAI API"""
        import urllib.request
        import json
        
        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', '')
        deployment = self.api_config.get('deployment', '')
        api_version = self.api_config.get('api_version', '2024-02-15-preview')
        
        self.analysis_progress.emit("正在发送请求到 Azure OpenAI...")
        
        url = f"{base_url}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        
        messages = [
            {
                "role": "system",
                "content": self.prompt
            },
            {
                "role": "user",
                "content": f"请分析以下巡检结果：\n\n{self.inspection_data}"
            }
        ]
        
        data = {
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4000
        }
        
        headers = {
            "Content-Type": "application/json",
            "api-key": api_key
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        self.analysis_progress.emit("等待AI响应...")
        
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode('utf-8'))
            content = result['choices'][0]['message']['content']
            return content
    
    def _call_local_api(self) -> str:
        """调用本地模型API (如 Ollama, vLLM 等)"""
        import urllib.request
        import json
        
        base_url = self.api_config.get('base_url', 'http://localhost:11434/api')
        model = self.api_config.get('model', 'llama2')
        
        self.analysis_progress.emit("正在发送请求到本地模型...")
        
        url = f"{base_url}/chat"
        
        data = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": self.prompt
                },
                {
                    "role": "user",
                    "content": f"请分析以下巡检结果：\n\n{self.inspection_data}"
                }
            ],
            "stream": False
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        self.analysis_progress.emit("等待AI响应...")
        
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read().decode('utf-8'))
            content = result['message']['content']
            return content


class ExportDialog(QDialog):
    """导出格式选择对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择导出格式")
        self.setWindowIcon(dialog_icon("baogao.svg"))
        self.setModal(True)
        self.setMinimumWidth(350)
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 20)
        
        # 标题
        title_label = QLabel("请选择导出格式")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #50C9C3; padding: 5px;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # 格式选项
        self.format_combo = QComboBox()
        self.format_combo.setObjectName("exportFormatCombo")
        self.format_combo.addItems(["HTML 网页格式 (*.html)", "PDF 文档格式 (*.pdf)"])
        self.format_combo.setMinimumHeight(40)
        layout.addWidget(self.format_combo)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)
        
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("dialogCancelButton")
        self.cancel_btn.setMinimumHeight(40)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        
        self.export_btn = PrimaryPushButton("导出")
        self.export_btn.setObjectName("dialogExportButton")
        self.export_btn.setMinimumHeight(40)
        self.export_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.export_btn)
        
        layout.addLayout(btn_layout)
        
        # 应用样式
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                border-radius: 8px;
            }
            QComboBox#exportFormatCombo {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 10px 15px;
                font-size: 14px;
                background: white;
                color: #374151;
            }
            QComboBox#exportFormatCombo:hover {
                border-color: #9ca3af;
            }
            QComboBox#exportFormatCombo:focus {
                border-color: #2563eb;
            }
            QComboBox#exportFormatCombo::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox#exportFormatCombo::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #6b7280;
                margin-right: 10px;
            }
            QComboBox QAbstractItemView {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 5px;
                background: white;
                selection-background-color: #dbeafe;
                selection-color: #1e40af;
                font-size: 13px;
            }
            QPushButton#dialogCancelButton {
                background: #6b7280;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 25px;
            }
            QPushButton#dialogCancelButton:hover {
                background: #4b5563;
            }
            QPushButton#dialogExportButton {
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 25px;
            }
            QPushButton#dialogExportButton:hover {
                background: #1d4ed8;
            }
        """)
    
    def get_format(self):
        """获取选择的格式"""
        current_text = self.format_combo.currentText()
        if "PDF" in current_text:
            return "pdf"
        return "html"


class ConfigDialog(QDialog):
    """AI模型配置对话框"""
    
    def __init__(self, api_type: int, api_key: str, base_url: str, model: str, prompt: str, 
                 api_timeout: int = 600, api_max_retries: int = 3, api_retry_delay: int = 5, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 模型配置")
        self.setWindowIcon(dialog_icon("AI.svg"))
        self.setModal(True)
        self.setMinimumWidth(550)
        self.setMinimumHeight(550)
        self.api_type = api_type
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.prompt = prompt
        self.api_timeout = api_timeout
        self.api_max_retries = api_max_retries
        self.api_retry_delay = api_retry_delay
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(25, 20, 25, 20)
        
        # 标题
        title_label = QLabel("AI 模型配置")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #50C9C3; padding: 8px;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # API配置组
        api_group = QGroupBox("API 配置")
        api_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
                color: #374151;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        api_layout = QGridLayout(api_group)
        api_layout.setSpacing(12)
        
        # API 类型
        api_layout.addWidget(QLabel("API 类型:"), 0, 0)
        self.api_type_combo = QComboBox()
        self.api_type_combo.addItems([
            "OpenAI",
            "Claude (Anthropic)",
            "Azure OpenAI",
            "本地模型 (Ollama等)",
            "--- 国产大模型 ---",
            "MiniMax",
            "智谱AI (ChatGLM)",
            "百川智能 (Baichuan)",
            "DeepSeek (深度求索)",
            "通义千问 (Qwen/阿里云)"
        ])
        self.api_type_combo.setCurrentIndex(self.api_type)
        self.api_type_combo.currentIndexChanged.connect(self.on_api_type_changed)
        self.api_type_combo.setMinimumHeight(36)
        api_layout.addWidget(self.api_type_combo, 0, 1)
        
        # API Key
        api_layout.addWidget(QLabel("API Key:"), 1, 0)
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("输入您的 API Key")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setText(self.api_key)
        self.api_key_input.setMinimumHeight(36)
        api_layout.addWidget(self.api_key_input, 1, 1)
        
        # Base URL
        api_layout.addWidget(QLabel("Base URL:"), 2, 0)
        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("API 基础地址")
        self.base_url_input.setText(self.base_url)
        self.base_url_input.setMinimumHeight(36)
        api_layout.addWidget(self.base_url_input, 2, 1)
        
        # Model
        api_layout.addWidget(QLabel("模型:"), 3, 0)
        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("模型名称")
        self.model_input.setText(self.model)
        self.model_input.setMinimumHeight(36)
        api_layout.addWidget(self.model_input, 3, 1)
        
        # API 超时时间
        api_layout.addWidget(QLabel("超时时间(秒):"), 4, 0)
        self.api_timeout_input = QSpinBox()
        self.api_timeout_input.setRange(30, 3600)
        self.api_timeout_input.setValue(getattr(self, 'api_timeout', 600))
        self.api_timeout_input.setSuffix(" 秒")
        self.api_timeout_input.setMinimumHeight(36)
        api_layout.addWidget(self.api_timeout_input, 4, 1)
        
        # API 最大重试次数
        api_layout.addWidget(QLabel("最大重试次数:"), 5, 0)
        self.api_max_retries_input = QSpinBox()
        self.api_max_retries_input.setRange(1, 10)
        self.api_max_retries_input.setValue(getattr(self, 'api_max_retries', 3))
        self.api_max_retries_input.setMinimumHeight(36)
        api_layout.addWidget(self.api_max_retries_input, 5, 1)
        
        # API 重试延迟
        api_layout.addWidget(QLabel("重试延迟(秒):"), 6, 0)
        self.api_retry_delay_input = QSpinBox()
        self.api_retry_delay_input.setRange(1, 60)
        self.api_retry_delay_input.setValue(getattr(self, 'api_retry_delay', 5))
        self.api_retry_delay_input.setSuffix(" 秒")
        self.api_retry_delay_input.setMinimumHeight(36)
        api_layout.addWidget(self.api_retry_delay_input, 6, 1)
        
        layout.addWidget(api_group)
        
        # 提示词配置组
        prompt_group = QGroupBox("分析提示词")
        prompt_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
                color: #374151;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        prompt_layout = QVBoxLayout(prompt_group)
        
        self.prompt_input = QTextEdit()
        self.prompt_input.setPlaceholderText("输入AI分析提示词...")
        self.prompt_input.setMinimumHeight(150)
        self.prompt_input.setText(self.prompt)
        prompt_layout.addWidget(self.prompt_input)
        
        layout.addWidget(prompt_group, 1)  # stretch factor 1
        
        # 提示信息
        hint_label = QLabel("💡 提示：API Key 将被加密保存在本地")
        hint_label.setStyleSheet("color: #6b7280; font-size: 12px; padding: 3px;")
        layout.addWidget(hint_label)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)
        
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("dialogCancelButton")
        self.cancel_btn.setMinimumHeight(40)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        
        self.save_btn = PrimaryPushButton("保存配置")
        self.save_btn.setObjectName("dialogSaveButton")
        self.save_btn.setMinimumHeight(40)
        self.save_btn.clicked.connect(self.save_config)
        btn_layout.addWidget(self.save_btn)
        
        layout.addLayout(btn_layout)
        
        # 应用样式
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                border-radius: 12px;
            }
            QLabel {
                font-size: 14px;
                color: #374151;
            }
            QLineEdit {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #8b5cf6;
                outline: none;
            }
            QComboBox {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 14px;
                background: white;
            }
            QComboBox:focus {
                border-color: #8b5cf6;
            }
            QTextEdit {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
            }
            QTextEdit:focus {
                border-color: #8b5cf6;
            }
            QPushButton#dialogCancelButton {
                background: #6b7280;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 25px;
            }
            QPushButton#dialogCancelButton:hover {
                background: #4b5563;
            }
            QPushButton#dialogSaveButton {
                background: #8b5cf6;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 25px;
            }
            QPushButton#dialogSaveButton:hover {
                background: #7c3aed;
            }
        """)
    
    def on_api_type_changed(self, index: int):
        """API类型变更处理"""
        api_types = {
            0: {"base_url": "https://api.openai.com/v1", "model": "gpt-3.5-turbo"},
            1: {"base_url": "https://api.anthropic.com/v1", "model": "claude-3-sonnet-20240229"},
            2: {"base_url": "", "model": "gpt-35-turbo"},
            3: {"base_url": "http://localhost:11434/api", "model": "llama2"},
            5: {"base_url": "https://api.minimaxi.com/v1", "model": "MiniMax-M2.5"},
            6: {"base_url": "https://open.bigmodel.cn/api/paas/v4/", "model": "glm-4-plus"},
            7: {"base_url": "https://api.baichuan-ai.com/v1", "model": "Baichuan4"},
            8: {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
            9: {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"}
        }
        
        if index == 4:
            return
        
        config = api_types.get(index, api_types[0])
        self.base_url_input.setText(config["base_url"])
        self.model_input.setText(config["model"])
    
    def save_config(self):
        """保存配置并关闭对话框"""
        self.api_type = self.api_type_combo.currentIndex()
        self.api_key = self.api_key_input.text().replace('`', '').strip()
        self.base_url = self.base_url_input.text().replace('`', '').strip()
        self.model = self.model_input.text().replace('`', '').strip()
        self.prompt = self.prompt_input.toPlainText()
        self.accept()
    
    def get_config(self):
        """获取配置"""
        return {
            "api_type": self.api_type,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "prompt": self.prompt,
            "api_timeout": self.api_timeout,
            "api_max_retries": self.api_max_retries,
            "api_retry_delay": self.api_retry_delay
        }


class AIAnalysisPage(QWidget):
    """AI 分析页面"""
    
    # 信号定义
    export_report_clicked = pyqtSignal(dict)
    
    def __init__(self):
        """初始化 AI 分析页面"""
        super().__init__()
        self.inspection_data = ""
        self.product_name = ""
        self.current_product = ""  # 当前产品名称，用于生成对应的提示词
        self.ai_worker = None
        self.faqs = []
        # API超时和重试配置
        self.api_timeout = 600
        self.api_max_retries = 3
        self.api_retry_delay = 5
        self.init_ui()
    
    def init_ui(self):
        """初始化 UI - 使用标签页布局"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        
        # 标题栏
        header_layout = QHBoxLayout()
        title_label = QLabel("AI 智能分析")
        title_label.setObjectName("pageTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        # 配置按钮
        self.config_btn = QPushButton("⚙️ 配置")
        self.config_btn.setObjectName("configButton")
        self.config_btn.setMinimumHeight(45)
        self.config_btn.setMinimumWidth(120)
        self.config_btn.clicked.connect(self.show_config_dialog)
        header_layout.addWidget(self.config_btn)
        
        # 操作按钮放在标题栏
        self.analyze_btn = QPushButton("开始分析")
        self.analyze_btn.setObjectName("analyzeButton")
        self.analyze_btn.setMinimumHeight(45)
        self.analyze_btn.setMinimumWidth(120)
        self.analyze_btn.clicked.connect(self.start_analysis)
        header_layout.addWidget(self.analyze_btn)
        
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setMinimumHeight(45)
        self.stop_btn.setMinimumWidth(120)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_analysis)
        header_layout.addWidget(self.stop_btn)
        
        self.export_btn = QPushButton("导出")
        self.export_btn.setObjectName("exportButton")
        self.export_btn.setMinimumHeight(45)
        self.export_btn.setMinimumWidth(120)
        self.export_btn.clicked.connect(self.show_export_dialog)
        
        header_layout.addWidget(self.export_btn)
        
        layout.addLayout(header_layout)
        
        # 创建配置控件（供配置对话框使用）
        self.api_type_combo = QComboBox()
        self.api_type_combo.addItems([
            "OpenAI", "Claude (Anthropic)", "Azure OpenAI", "本地模型 (Ollama等)",
            "--- 国产大模型 ---", "MiniMax", "智谱AI (ChatGLM)", "百川智能 (Baichuan)",
            "DeepSeek (深度求索)", "通义千问 (Qwen/阿里云)"
        ])
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.base_url_input = QLineEdit()
        self.model_input = QLineEdit()
        self.prompt = ""
        
        # 当前产品标签
        self.product_label = QLabel("当前产品: 未选择")
        self.product_label.setObjectName("productLabel")
        self.product_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.product_label)
        
        # 进度显示
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #50C9C3; font-size: 11px; padding: 2px;")
        layout.addWidget(self.progress_label)
        
        # 使用标签页组织内容
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("aiTabWidget")
        
        # 标签页1: 巡检数据（默认显示）
        data_tab = self._create_data_tab()
        self.tab_widget.addTab(data_tab, "巡检数据")
        
        # 标签页2: AI分析结果
        result_tab = self._create_result_tab()
        self.tab_widget.addTab(result_tab, "分析结果")
        
        layout.addWidget(self.tab_widget, 1)
        
        # 应用样式
        self.apply_styles()
        
        # 加载保存的配置
        self.load_config()
    
    def _create_data_tab(self) -> QWidget:
        """创建巡检数据标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(8)
        
        # 数据状态
        self.data_status_label = QLabel("未加载巡检数据")
        self.data_status_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        layout.addWidget(self.data_status_label)
        
        # 数据输入
        self.data_input = QTextEdit()
        self.data_input.setPlaceholderText("巡检数据将自动填充，或手动输入...")
        layout.addWidget(self.data_input, 1)
        
        return tab
    
    def _create_result_tab(self) -> QWidget:
        """创建分析结果标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(8)
        
        self.result_output = QTextBrowser()
        self.result_output.setObjectName("resultOutput")
        self.result_output.setOpenExternalLinks(True)
        self.result_output.setPlaceholderText("AI分析结果将在这里显示...")
        layout.addWidget(self.result_output, 1)
        
        return tab
    
    def _create_chat_tab(self) -> QWidget:
        """创建知识库问答标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # FAQ文件上传区域
        upload_layout = QHBoxLayout()
        
        upload_label = QLabel("知识库文件:")
        upload_layout.addWidget(upload_label)
        
        self.upload_path_label = QLabel("未选择文件")
        self.upload_path_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        self.upload_path_label.setMaximumWidth(400)
        self.upload_path_label.setWordWrap(True)
        upload_layout.addWidget(self.upload_path_label, 1)
        
        self.upload_btn = QPushButton("上传文件")
        self.upload_btn.setObjectName("uploadBtn")
        self.upload_btn.setMinimumHeight(45)
        self.upload_btn.setMinimumWidth(120)
        self.upload_btn.clicked.connect(self.on_upload_faq_file)
        upload_layout.addWidget(self.upload_btn)
        
        self.clear_faq_btn = QPushButton("清空知识库")
        self.clear_faq_btn.setObjectName("clearFaqBtn")
        self.clear_faq_btn.setMinimumHeight(45)
        self.clear_faq_btn.setMinimumWidth(120)
        self.clear_faq_btn.clicked.connect(self.on_clear_faqs)
        upload_layout.addWidget(self.clear_faq_btn)
        
        layout.addLayout(upload_layout)
        
        # 支持格式提示
        format_label = QLabel("支持格式: Word (.docx, .doc), PDF (.pdf), Markdown (.md), TXT (.txt), JSON (.json)")
        format_label.setStyleSheet("color: #9ca3af; font-size: 11px; padding: 2px;")
        layout.addWidget(format_label)
        
        # 分割线
        separator = QFrame()
        separator.setObjectName("separator")
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background: #e5e7eb;")
        separator.setFixedHeight(1)
        layout.addWidget(separator)
        
        # 聊天区域
        self.chat_status_label = QLabel("知识库已加载: 0 条问答")
        self.chat_status_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        layout.addWidget(self.chat_status_label)
        
        # 聊天消息区域
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatScrollArea")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setStyleSheet("""
            QScrollArea#chatScrollArea {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background: white;
            }
        """)
        
        self.chat_widget = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_widget)
        self.chat_layout.setSpacing(10)
        self.chat_layout.setContentsMargins(10, 10, 10, 10)
        self.chat_layout.addStretch()
        
        self.chat_scroll.setWidget(self.chat_widget)
        layout.addWidget(self.chat_scroll, 1)
        
        # 聊天输入区域
        input_layout = QHBoxLayout()
        
        self.chat_input = QLineEdit()
        self.chat_input.setObjectName("chatInput")
        self.chat_input.setPlaceholderText("输入问题，AI将基于知识库回答...")
        self.chat_input.setMinimumHeight(45)
        self.chat_input.returnPressed.connect(self.on_send_chat_message)
        input_layout.addWidget(self.chat_input, 1)
        
        self.chat_send_btn = QPushButton("发送")
        self.chat_send_btn.setObjectName("chatSendBtn")
        self.chat_send_btn.setMinimumHeight(45)
        self.chat_send_btn.setMinimumWidth(120)
        self.chat_send_btn.setStyleSheet("""
            QPushButton#chatSendBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton#chatSendBtn:hover {
                background: #3eb8b3;
            }
        """)
        self.chat_send_btn.clicked.connect(self.on_send_chat_message)
        input_layout.addWidget(self.chat_send_btn)
        
        layout.addLayout(input_layout)
        
        # 加载现有FAQs
        self.load_faqs()
        
        return tab
    
    def load_faqs(self):
        """加载FAQ知识库"""
        import json
        from pathlib import Path
        
        config_file = Path(_get_config_dir()) / "faqs.json"
        
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.faqs = data
                    elif isinstance(data, dict) and 'faqs' in data:
                        self.faqs = data['faqs']
                    else:
                        self.faqs = []
            except Exception as e:
                print(f"[WARN] Failed to load FAQs: {e}")
                self.faqs = []
        else:
            self.faqs = []
        
        if hasattr(self, 'chat_status_label'):
            self.chat_status_label.setText(f"知识库已加载: {len(self.faqs)} 条问答")
    
    def save_faqs(self):
        """保存FAQ知识库"""
        import json
        from pathlib import Path
        
        config_dir = Path(_get_config_dir())
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "faqs.json"
        
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.faqs, f, indent=2, ensure_ascii=False)
            print(f"[DEBUG] save_faqs: saved {len(self.faqs)} FAQs to {config_file}")
        except Exception as e:
            print(f"[ERROR] save_faqs: failed to save FAQs: {e}")
    
    def on_upload_faq_file(self):
        """上传FAQ文件"""
        from PyQt6.QtWidgets import QFileDialog
        import os
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择知识库文件",
            "",
            "支持的文件 (*.docx *.doc *.pdf *.md *.txt *.json *.xlsx *.xls *.csv);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        file_name = os.path.basename(file_path)
        self.upload_path_label.setText(f"正在处理: {file_name}")
        
        try:
            added_count = 0
            skipped_duplicates = 0
            
            # 根据文件类型解析
            if file_path.endswith('.json'):
                added_count, skipped_duplicates = self._load_json_faq(file_path)
            elif file_path.endswith('.md'):
                added_count, skipped_duplicates = self._load_md_faq(file_path)
            elif file_path.endswith('.txt'):
                added_count, skipped_duplicates = self._load_txt_faq(file_path)
            elif file_path.endswith('.docx') or file_path.endswith('.doc'):
                added_count, skipped_duplicates = self._load_word_faq(file_path)
            elif file_path.endswith('.pdf'):
                added_count, skipped_duplicates = self._load_pdf_faq(file_path)
            elif file_path.endswith('.xlsx') or file_path.endswith('.xls'):
                added_count, skipped_duplicates = self._load_excel_faq(file_path)
            elif file_path.endswith('.csv'):
                added_count, skipped_duplicates = self._load_csv_faq(file_path)
            else:
                self.upload_path_label.setText("未选择文件")
                QMessageBox.warning(self, "不支持的格式", "请上传 Word、PDF、Excel、CSV、Markdown、TXT 或 JSON 格式的文件")
                return
            
            self.save_faqs()
            self.load_faqs()
            faq_count = len(self.faqs)
            self.upload_path_label.setText(f"已加载: {faq_count} 条")
            self.chat_status_label.setText(f"知识库已加载: {faq_count} 条问答")
            if faq_count == 0:
                MessageBox("警告", "文件解析完成但未找到有效的问答对，请检查文件格式。\n\n支持的格式：\n• JSON: [{\"question\":\"...\", \"answer\":\"...\"}]\n• Markdown: Q: 问题\nA: 答案\n• TXT: 问题|答案", self).exec()
            elif skipped_duplicates > 0:
                MessageBox("上传成功", f"已导入 {added_count} 条知识库\n重复跳过 {skipped_duplicates} 条\n当前共有 {faq_count} 条问答", self).exec()
            else:
                MessageBox("上传成功", f"已成功加载知识库文件，当前共有 {faq_count} 条问答", self).exec()
            
        except Exception as e:
            import traceback
            self.upload_path_label.setText("处理失败")
            QMessageBox.warning(self, "上传失败", f"处理文件时出错: {str(e)}\n\n{traceback.format_exc()}")
    
    def _is_duplicate_faq(self, question: str) -> bool:
        """检查是否已存在相同的问答"""
        for faq in self.faqs:
            if faq.get('question', '').strip() == question.strip():
                return True
        return False
    
    def _load_json_faq(self, file_path: str) -> tuple:
        """加载JSON格式FAQ，返回(新增数, 重复数)"""
        import json
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        added_count = 0
        skipped = 0
        new_faqs = []
        
        if isinstance(data, list):
            for faq in data:
                q = faq.get('question', '').strip()
                if q and self._is_duplicate_faq(q):
                    skipped += 1
                else:
                    new_faqs.append(faq)
                    added_count += 1
        elif isinstance(data, dict):
            if 'faqs' in data:
                for faq in data['faqs']:
                    q = faq.get('question', '').strip()
                    if q and self._is_duplicate_faq(q):
                        skipped += 1
                    else:
                        new_faqs.append(faq)
                        added_count += 1
            elif 'qnas' in data:
                for faq in data['qnas']:
                    q = faq.get('question', '').strip()
                    if q and self._is_duplicate_faq(q):
                        skipped += 1
                    else:
                        new_faqs.append(faq)
                        added_count += 1
            elif 'questions' in data:
                for q in data['questions']:
                    if 'question' in q and 'answer' in q:
                        question = q['question'].strip()
                        if self._is_duplicate_faq(question):
                            skipped += 1
                        else:
                            new_faqs.append({'question': question, 'answer': q['answer']})
                            added_count += 1
        
        self.faqs.extend(new_faqs)
        print(f"[DEBUG] _load_json_faq: added {added_count}, skipped {skipped}")
        return added_count, skipped
    
    def _load_md_faq(self, file_path: str) -> tuple:
        """加载Markdown格式FAQ，返回(新增数, 重复数)"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        import re
        added_count = 0
        skipped = 0
        new_faqs = []
        
        # 尝试 Q: / A: 格式
        pattern1 = r'Q:\s*(.+?)\nA:\s*(.+?)(?=\nQ:|\n## |$)'
        matches1 = re.findall(pattern1, content, re.DOTALL)
        for q, a in matches1:
            q, a = q.strip(), a.strip()
            if q and self._is_duplicate_faq(q):
                skipped += 1
            else:
                new_faqs.append({'question': q, 'answer': a})
                added_count += 1
        
        # 尝试 ## 标题 格式
        if not matches1:
            sections = re.split(r'\n(?=## )', content)
            for section in sections:
                lines = section.strip().split('\n', 1)
                if len(lines) >= 2:
                    question = lines[0].lstrip('#').strip()
                    answer = lines[1].strip()
                    if question and answer:
                        if self._is_duplicate_faq(question):
                            skipped += 1
                        else:
                            new_faqs.append({'question': question, 'answer': answer})
                            added_count += 1
        
        self.faqs.extend(new_faqs)
        return added_count, skipped
    
    def _load_txt_faq(self, file_path: str) -> tuple:
        """加载TXT格式FAQ，返回(新增数, 重复数)"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        added_count = 0
        skipped = 0
        new_faqs = []
        
        for line in content.split('\n'):
            line = line.strip()
            if '|' in line:
                parts = line.split('|', 1)
                if len(parts) == 2:
                    q, a = parts[0].strip(), parts[1].strip()
                    if q and self._is_duplicate_faq(q):
                        skipped += 1
                    else:
                        new_faqs.append({'question': q, 'answer': a})
                        added_count += 1
            elif line:
                for sep in ['\t', ';;', '::']:
                    if sep in line:
                        parts = line.split(sep, 1)
                        if len(parts) == 2:
                            q, a = parts[0].strip(), parts[1].strip()
                            if q and self._is_duplicate_faq(q):
                                skipped += 1
                            else:
                                new_faqs.append({'question': q, 'answer': a})
                                added_count += 1
                        break
        
        self.faqs.extend(new_faqs)
        return added_count, skipped
    
    def _load_word_faq(self, file_path: str) -> tuple:
        """加载Word格式FAQ，返回(新增数, 重复数)"""
        try:
            from docx import Document
        except ImportError:
            QMessageBox.warning(self, "缺少库", "请先安装 python-docx 库: pip install python-docx")
            return 0, 0
        
        doc = Document(file_path)
        current_question = None
        current_answer = []
        added_count = 0
        skipped = 0
        new_faqs = []
        
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                if current_question and current_answer:
                    answer_text = '\n'.join(current_answer).strip()
                    if answer_text:
                        if self._is_duplicate_faq(current_question):
                            skipped += 1
                        else:
                            new_faqs.append({'question': current_question, 'answer': answer_text})
                            added_count += 1
                    current_question = None
                    current_answer = []
                continue
            
            if text.endswith('?') or text.startswith('Q:') or text.startswith('Q：'):
                if current_question and current_answer:
                    answer_text = '\n'.join(current_answer).strip()
                    if answer_text:
                        if self._is_duplicate_faq(current_question):
                            skipped += 1
                        else:
                            new_faqs.append({'question': current_question, 'answer': answer_text})
                            added_count += 1
                current_question = text.lstrip('Q:').lstrip('Q：').strip()
                current_answer = []
            elif current_question:
                current_answer.append(text)
        
        if current_question and current_answer:
            answer_text = '\n'.join(current_answer).strip()
            if answer_text:
                if self._is_duplicate_faq(current_question):
                    skipped += 1
                else:
                    new_faqs.append({'question': current_question, 'answer': answer_text})
                    added_count += 1
        
        self.faqs.extend(new_faqs)
        return added_count, skipped
    
    def _load_pdf_faq(self, file_path: str) -> tuple:
        """加载PDF格式FAQ，返回(新增数, 重复数)"""
        try:
            import fitz
        except ImportError:
            QMessageBox.warning(self, "缺少库", "请先安装 PyMuPDF 库: pip install pymupdf")
            return 0, 0
        
        doc = fitz.open(file_path)
        text_content = []
        
        for page in doc:
            text_content.append(page.get_text())
        
        doc.close()
        
        return self._load_txt_faq_from_text('\n'.join(text_content))
    
    def _load_txt_faq_from_text(self, content: str) -> tuple:
        """从文本内容加载FAQ，返回(新增数, 重复数)"""
        import re
        added_count = 0
        skipped = 0
        new_faqs = []
        
        # 格式1: Q: 问题\nA: 答案
        pattern1 = r'Q:\s*(.+?)\nA:\s*(.+?)(?=\nQ:|\n## |$)'
        matches1 = re.findall(pattern1, content, re.DOTALL)
        for q, a in matches1:
            q, a = q.strip(), a.strip()
            if q and self._is_duplicate_faq(q):
                skipped += 1
            else:
                new_faqs.append({'question': q, 'answer': a})
                added_count += 1
        
        # 格式2: 问号结尾的行
        if not matches1:
            lines = content.split('\n')
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line and (line.endswith('?') or line.endswith('？')):
                    question = line.rstrip('?').rstrip('？').strip()
                    i += 1
                    answer_lines = []
                    while i < len(lines) and lines[i].strip() and not lines[i].strip().endswith('?') and not lines[i].strip().endswith('？'):
                        answer_lines.append(lines[i].strip())
                        i += 1
                    if question and answer_lines:
                        if self._is_duplicate_faq(question):
                            skipped += 1
                        else:
                            new_faqs.append({'question': question, 'answer': ' '.join(answer_lines)})
                            added_count += 1
                i += 1
        
        self.faqs.extend(new_faqs)
        return added_count, skipped
    
    def _load_excel_faq(self, file_path: str) -> tuple:
        """加载Excel格式FAQ，返回(新增数, 重复数)"""
        try:
            import openpyxl
        except ImportError:
            QMessageBox.warning(self, "缺少库", "请先安装 openpyxl 库")
            return 0, 0
        
        added_count = 0
        skipped = 0
        new_faqs = []
        
        wb = openpyxl.load_workbook(file_path)
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            headers = [cell.value for cell in sheet[1]]
            
            field_map = {'question': None, 'answer': None}
            for idx, header in enumerate(headers):
                if header:
                    h = str(header).lower()
                    if '问题' in h or 'question' in h:
                        field_map['question'] = idx
                    elif '答案' in h or 'answer' in h:
                        field_map['answer'] = idx
            
            for row in sheet.iter_rows(min_row=2, values_only=True):
                q = row[field_map['question']] if field_map['question'] is not None else None
                a = row[field_map['answer']] if field_map['answer'] is not None else None
                if q and a:
                    q, a = str(q).strip(), str(a).strip()
                    if q and self._is_duplicate_faq(q):
                        skipped += 1
                    else:
                        new_faqs.append({'question': q, 'answer': a})
                        added_count += 1
        
        self.faqs.extend(new_faqs)
        return added_count, skipped
    
    def _load_csv_faq(self, file_path: str) -> tuple:
        """加载CSV格式FAQ"""
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        return self._load_txt_faq_from_text(content)
    
    def on_clear_faqs(self):
        """清空知识库"""
        reply = MessageBox(
            "确认清空",
            "确定要清空当前知识库吗？",
            self
        )
        
        if reply.exec():
            self.faqs = []
            self.save_faqs()
            self.load_faqs()
            MessageBox("已清空", "知识库已清空", self).exec()
    
    def on_send_chat_message(self):
        """发送聊天消息"""
        message = self.chat_input.text().strip()
        if not message:
            return
        
        if not self.faqs:
            QMessageBox.warning(self, "知识库为空", "请先上传知识库文件")
            return
        
        self.chat_input.clear()
        
        # 添加用户消息
        self._add_chat_message(message, is_user=True)
        
        # 使用FAQ匹配回答
        answer = self._find_faq_answer(message)
        if answer:
            self._add_chat_message(answer, is_user=False)
        else:
            self._add_chat_message("抱歉，我在知识库中没有找到相关答案。\n\n您可以：\n1. 检查问题措辞\n2. 上传更完整的知识库文件\n3. 联系管理员添加相关问答", is_user=False)
    
    def _find_faq_answer(self, question: str) -> str:
        """在FAQ知识库中查找最匹配的回答（模糊匹配）"""
        if not self.faqs:
            return None
        
        question_lower = question.lower()
        best_match = None
        best_score = 0
        
        # 预处理问题：分词
        q_words = self._tokenize(question_lower)
        
        for faq in self.faqs:
            faq_question = faq.get('question', '').lower()
            faq_words = self._tokenize(faq_question)
            
            # 计算多种相似度
            scores = []
            
            # 1. Jaccard相似度（词集合重叠）
            jaccard_score = self._jaccard_similarity(q_words, faq_words)
            scores.append(('jaccard', jaccard_score))
            
            # 2. 子串匹配分数
            substring_score = self._substring_similarity(question_lower, faq_question)
            scores.append(('substring', substring_score))
            
            # 3. 编辑距离相似度（Levenshtein）
            edit_score = self._edit_similarity(question_lower, faq_question)
            scores.append(('edit', edit_score))
            
            # 4. 关键词命中分数
            keyword_score = self._keyword_match_score(q_words, faq_words)
            scores.append(('keyword', keyword_score))
            
            # 综合分数（加权平均）
            final_score = (
                jaccard_score * 0.25 +
                substring_score * 0.30 +
                edit_score * 0.25 +
                keyword_score * 0.20
            )
            
            print(f"[DEBUG] FAQ: '{faq_question[:30]}...' scores: jaccard={jaccard_score:.2f}, substring={substring_score:.2f}, edit={edit_score:.2f}, keyword={keyword_score:.2f}, final={final_score:.2f}")
            
            if final_score > best_score:
                best_score = final_score
                best_match = faq.get('answer', '')
        
        print(f"[DEBUG] Best match score: {best_score:.2f}")
        
        # 设置阈值，低于阈值的返回None
        threshold = 0.25
        if best_score < threshold:
            return None
        
        return best_match
    
    def _tokenize(self, text: str) -> set:
        """分词：将文本分割成词集合"""
        import re
        # 去除标点符号，分割成词
        text = re.sub(r'[^\w\s]', ' ', text)
        words = text.split()
        # 过滤掉太短的词和停用词
        stopwords = {'的', '了', '是', '在', '和', '与', '或', '的', '我', '你', '他', '她', '它', '这', '那', '有', '没有', '什么', '怎么', '如何', '吗', '呢', '吧', '啊', '哦', '嗯', '就', '都', '也', '还', '又', '很', '太', '更', '最', '非常', '一个', '一', '二', '三'}
        return {w for w in words if len(w) >= 2 and w not in stopwords}
    
    def _jaccard_similarity(self, words1: set, words2: set) -> float:
        """Jaccard相似度：两个集合的交集除以并集"""
        if not words1 or not words2:
            return 0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0
    
    def _substring_similarity(self, text1: str, text2: str) -> float:
        """子串相似度：检查一个文本是否包含另一个文本的关键部分"""
        # 较短的文本
        shorter = text1 if len(text1) <= len(text2) else text2
        longer = text2 if shorter == text1 else text1
        
        # 检查较短文本是否在较长文本中
        if shorter in longer:
            return 0.9
        
        # 检查关键部分（取较长的n-gram）是否匹配
        n = min(3, len(shorter) // 2)
        if n < 1:
            return 0
        
        # 提取n-gram
        ngrams1 = set(text1[i:i+n] for i in range(len(text1)-n+1))
        ngrams2 = set(text2[i:i+n] for i in range(len(text2)-n+1))
        
        if not ngrams1 or not ngrams2:
            return 0
        
        overlap = len(ngrams1 & ngrams2)
        total = len(ngrams1 | ngrams2)
        
        return overlap / total if total > 0 else 0
    
    def _edit_similarity(self, text1: str, text2: str) -> float:
        """编辑距离相似度：基于Levenshtein距离"""
        if not text1 or not text2:
            return 0
        
        # 如果完全相同
        if text1 == text2:
            return 1.0
        
        # 如果一个是另一个的子串
        if text1 in text2 or text2 in text1:
            shorter_len = min(len(text1), len(text2))
            longer_len = max(len(text1), len(text2))
            return shorter_len / longer_len
        
        # 计算编辑距离
        distance = self._levenshtein_distance(text1, text2)
        max_len = max(len(text1), len(text2))
        
        return 1 - (distance / max_len) if max_len > 0 else 0
    
    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """计算两个字符串之间的Levenshtein编辑距离"""
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)
        
        if len(s2) == 0:
            return len(s1)
        
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        return previous_row[-1]
    
    def _keyword_match_score(self, words1: set, words2: set) -> float:
        """关键词命中分数：计算两个词集合的重叠程度"""
        if not words1 or not words2:
            return 0
        
        # 找出在FAQ问题中出现的用户问题关键词
        matches = words1 & words2
        
        if not matches:
            return 0
        
        # 计算匹配比例（基于FAQ问题）
        return len(matches) / len(words2) if words2 else 0
    
    def _add_chat_message(self, message: str, is_user: bool):
        """添加聊天消息"""
        from datetime import datetime
        
        msg_frame = QFrame()
        msg_frame.setObjectName("chatMessage")
        
        if is_user:
            msg_frame.setStyleSheet("""
                QFrame#chatMessage {
                    background: #50C9C3;
                    border-radius: 10px;
                    padding: 8px;
                }
            """)
            layout = QHBoxLayout(msg_frame)
            layout.addStretch()
            label = QLabel(message)
            label.setWordWrap(True)
            label.setStyleSheet("color: white; font-size: 13px;")
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignRight)
        else:
            msg_frame.setStyleSheet("""
                QFrame#chatMessage {
                    background: #f3f4f6;
                    border-radius: 10px;
                    padding: 8px;
                }
            """)
            layout = QHBoxLayout(msg_frame)
            label = QLabel(message)
            label.setWordWrap(True)
            label.setStyleSheet("color: #1f2937; font-size: 13px;")
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignLeft)
            layout.addStretch()
        
        # 插入到stretch之前
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg_frame)
        
        # 滚动到底部
        QTimer.singleShot(100, self._scroll_chat_to_bottom)
    
    def _scroll_chat_to_bottom(self):
        """滚动聊天到底部"""
        self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()
        )
    
    def get_default_prompt(self) -> str:
        """根据产品名称获取对应的分析提示词"""
        # 产品专用提示词映射
        product_prompts = {
            "办公智盾": """你是一个专业的办公智盾服务器巡检分析专家，擅长Linux服务器基线检查和运维分析。请分析以下巡检结果，提供详细的分析报告。

请从以下几个方面进行分析：
1. **整体健康状况评估**：综合评估服务器的整体状态
2. **性能分析**：CPU、内存、磁盘I/O、网络等性能指标分析
3. **安全基线检查**：对照等保要求检查服务器安全配置
4. **风险识别**：识别潜在的问题和风险点
5. **优化建议**：提供针对性的优化建议
6. **整改建议**：针对不符合项提供具体的整改方案

请用中文输出，格式清晰，重点突出。对于每项检查结果，请给出"符合"/"不符合"/"告警"的明确判断。""",

            "准入系统": """你是一个专业的准入控制系统服务器巡检分析专家，擅长Linux服务器网络准入控制和合规检查分析。请分析以下巡检结果，提供详细的分析报告。

请从以下几个方面进行分析：
1. **整体健康状况评估**：综合评估服务器的整体状态
2. **网络准入状态**：检查准入控制器的运行状态和终端连接情况
3. **合规性检查**：检查终端是否满足安全合规要求
4. **性能分析**：CPU、内存、网络等性能指标分析
5. **风险识别**：识别潜在的网络安全风险
6. **优化建议**：提供针对性的优化建议

请用中文输出，格式清晰，重点突出。""",

            "EDR": """你是一个专业的EDR（终端检测与响应）服务器巡检分析专家，擅长Linux服务器威胁检测和行为分析。请分析以下巡检结果，提供详细的分析报告。

请从以下几个方面进行分析：
1. **整体健康状况评估**：综合评估EDR服务器的整体状态
2. **终端防护状态**：检查EDR Agent的注册和在线情况
3. **威胁检测分析**：检查是否有可疑行为或告警事件
4. **性能分析**：CPU、内存、磁盘等性能指标分析
5. **风险识别**：识别潜在的终端安全风险
6. **处置建议**：针对检测到的威胁提供处置方案

请用中文输出，格式清晰，重点突出。""",

            "零信任": """你是一个专业的零信任安全架构服务器巡检分析专家，擅长Linux服务器身份认证和访问控制分析。请分析以下巡检结果，提供详细的分析报告。

请从以下几个方面进行分析：
1. **整体健康状况评估**：综合评估零信任服务器的整体状态
2. **身份认证状态**：检查身份认证服务的运行状态
3. **访问控制分析**：检查访问策略和权限配置
4. **持续验证机制**：检查信任评估和动态访问控制
5. **性能分析**：CPU、内存、网络等性能指标分析
6. **风险识别**：识别潜在的零信任安全风险
7. **优化建议**：提供针对性的优化建议

请用中文输出，格式清晰，重点突出。"""
        }

        # 获取当前产品对应的提示词，如果没有则使用默认提示词
        return product_prompts.get(self.current_product, """你是一个专业的Linux服务器巡检分析专家。请分析以下巡检结果，提供详细的分析报告。

请从以下几个方面进行分析：
1. **整体健康状况评估**：综合评估服务器的整体状态
2. **性能分析**：CPU、内存、磁盘I/O、网络等性能指标分析
3. **风险识别**：识别潜在的问题和风险点
4. **优化建议**：提供针对性的优化建议
5. **安全建议**：安全方面的建议和改进措施

请用中文输出，格式清晰，重点突出。""")
    
    def on_api_type_changed(self, index: int):
        """API类型变更处理"""
        api_types = {
            0: {"base_url": "https://api.openai.com/v1", "model": "gpt-3.5-turbo"},
            1: {"base_url": "https://api.anthropic.com/v1", "model": "claude-3-sonnet-20240229"},
            2: {"base_url": "", "model": "gpt-35-turbo"},
            3: {"base_url": "http://localhost:11434/api", "model": "llama2"},
            # index 4 是分隔符 "--- 国产大模型 ---"，跳过
            5: {"base_url": "https://api.minimaxi.com/v1", "model": "MiniMax-M2.5"},
            6: {"base_url": "https://open.bigmodel.cn/api/paas/v4/", "model": "glm-4-plus"},
            7: {"base_url": "https://api.baichuan-ai.com/v1", "model": "Baichuan4"},
            8: {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
            9: {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"}
        }
        
        # 如果选择的是分隔符，不更新配置
        if index == 4:
            return
        
        config = api_types.get(index, api_types[0])
        self.base_url_input.setText(config["base_url"])
        self.model_input.setText(config["model"])
    
    def save_config(self):
        """保存API配置"""
        import json
        import os
        
        config = {
            "api_type": self.api_type_combo.currentIndex(),
            "api_key": self.api_key_input.text().replace('`', '').strip(),
            "base_url": self.base_url_input.text().replace('`', '').strip(),
            "model": self.model_input.text().replace('`', '').strip(),
            "prompt": getattr(self, 'prompt', ''),
            "api_timeout": getattr(self, 'api_timeout', 600),
            "api_max_retries": getattr(self, 'api_max_retries', 3),
            "api_retry_delay": getattr(self, 'api_retry_delay', 5)
        }

        config_file = get_ai_config_path()

        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        QMessageBox.information(self, "成功", "配置已保存")

    def load_config(self):
        """加载保存的配置"""
        import json

        config_file = get_ai_config_path()
        
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                self.api_type_combo.setCurrentIndex(config.get("api_type", 0))
                self.api_key_input.setText(config.get("api_key", ""))
                self.base_url_input.setText(config.get("base_url", ""))
                self.model_input.setText(config.get("model", ""))
                self.prompt = config.get("prompt", "")
                
                # 加载API超时和重试配置为实例变量
                self.api_timeout = config.get("api_timeout", 600)
                self.api_max_retries = config.get("api_max_retries", 3)
                self.api_retry_delay = config.get("api_retry_delay", 5)
                
                # 如果有spinbox控件则更新其值
                if hasattr(self, 'api_timeout_input'):
                    self.api_timeout_input.setValue(self.api_timeout)
                if hasattr(self, 'api_max_retries_input'):
                    self.api_max_retries_input.setValue(self.api_max_retries)
                if hasattr(self, 'api_retry_delay_input'):
                    self.api_retry_delay_input.setValue(self.api_retry_delay)
            except Exception:
                pass
    
    def set_product(self, product_name: str):
        """设置当前产品"""
        self.current_product = product_name
        if hasattr(self, 'product_label'):
            if product_name:
                self.product_label.setText(f"当前产品: {product_name}")
            else:
                self.product_label.setText("当前产品: 未选择")
            self.product_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #50C9C3; background: #F0FAFA; padding: 8px; border-radius: 4px;")
    
    def set_inspection_data(self, data: dict):
        """设置巡检数据"""
        self.product_name = data.get("product_name", "")
        
        # 设置当前产品名称，用于生成对应的提示词
        self.current_product = self.product_name
        
        raw_output = data.get("output", "")
        
        # 格式化巡检数据
        from datetime import datetime
        formatted_data = f"产品: {self.product_name}\n"
        timestamp = data.get('timestamp') or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_data += f"检查时间: {timestamp}\n\n"
        formatted_data += "=== 原始巡检输出 ===\n"
        formatted_data += raw_output
        
        self.inspection_data = formatted_data
        self.data_input.setText(formatted_data)
        self.data_status_label.setText(f"已加载巡检数据 ({len(formatted_data)} 字符)")
        self.data_status_label.setStyleSheet("color: #10b981; font-size: 12px;")
        
        # 根据产品名称更新提示词
        self.prompt = self.get_default_prompt()
    
    def show_config_dialog(self):
        """显示配置对话框"""
        # 获取当前超时和重试配置
        api_timeout = getattr(self, 'api_timeout', 600)
        api_max_retries = getattr(self, 'api_max_retries', 3)
        api_retry_delay = getattr(self, 'api_retry_delay', 5)
        
        dialog = ConfigDialog(
            self.api_type_combo.currentIndex(),
            self.api_key_input.text(),
            self.base_url_input.text(),
            self.model_input.text(),
            getattr(self, 'prompt', ''),
            api_timeout,
            api_max_retries,
            api_retry_delay,
            self
        )
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            config = dialog.get_config()
            self.api_type_combo.setCurrentIndex(config["api_type"])
            self.api_key_input.setText(config["api_key"])
            self.base_url_input.setText(config["base_url"])
            self.model_input.setText(config["model"])
            self.prompt = config["prompt"]
            self.api_timeout = config["api_timeout"]
            self.api_max_retries = config["api_max_retries"]
            self.api_retry_delay = config["api_retry_delay"]
            # 自动保存配置
            self.save_config()
    
    def start_analysis(self):
        """开始AI分析"""
        # 验证配置
        if not self.api_key_input.text().strip() and self.api_type_combo.currentIndex() != 3:
            QMessageBox.warning(self, "警告", "请输入 API Key")
            return
        
        if not self.data_input.toPlainText().strip():
            QMessageBox.warning(self, "警告", "没有巡检数据可分析")
            return
        
        # 构建API配置
        # 国产大模型都兼容OpenAI API格式，统一映射为"openai"
        api_type_map = {
            0: "openai",      # OpenAI
            1: "claude",      # Claude
            2: "azure",       # Azure OpenAI
            3: "local",       # 本地模型
            # 4 是分隔符，跳过
            5: "openai",      # MiniMax (OpenAI兼容)
            6: "openai",      # 智谱AI (OpenAI兼容)
            7: "openai",      # 百川智能 (OpenAI兼容)
            8: "openai",      # DeepSeek (OpenAI兼容)
            9: "openai"       # 通义千问 (OpenAI兼容)
        }
        
        api_config = {
            "api_type": api_type_map.get(self.api_type_combo.currentIndex(), "openai"),
            "api_key": self.api_key_input.text().replace('`', '').strip(),
            "base_url": self.base_url_input.text().replace('`', '').strip(),
            "model": self.model_input.text().replace('`', '').strip()
        }
        
        # 开始分析
        self.analyze_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.result_output.clear()
        self.progress_label.setText("正在准备分析...")
        
        self.ai_worker = AIAnalysisWorker(
            api_config,
            self.data_input.toPlainText(),
            self.prompt
        )
        self.ai_worker.analysis_progress.connect(self.on_progress)
        self.ai_worker.analysis_completed.connect(self.on_analysis_completed)
        self.ai_worker.start()
    
    def stop_analysis(self):
        """停止分析"""
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.terminate()
            self.ai_worker.wait()
        
        self.analyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_label.setText("分析已停止")
    
    def on_progress(self, message: str):
        """处理进度信息"""
        self.progress_label.setText(message)
    
    def on_analysis_completed(self, result: str, error: str):
        """分析完成处理"""
        self.analyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        if error:
            self.result_output.setHtml(f'''
                <div style="color: #ef4444; padding: 20px;">
                    <h3>❌ 分析失败</h3>
                    <p>{error}</p>
                </div>
            ''')
            self.progress_label.setText("分析失败")
        else:
            # 将Markdown格式转换为HTML显示
            html_result = self.format_markdown_to_html(result)
            self.result_output.setHtml(html_result)
            self.progress_label.setText("分析完成")
    
    def format_markdown_to_html(self, text: str) -> str:
        """简单的Markdown转HTML"""
        import re
        
        # 转义HTML
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        # 标题
        text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
        text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
        text = re.sub(r'^# (.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
        
        # 粗体
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        
        # 列表
        text = re.sub(r'^\* (.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
        text = re.sub(r'^\- (.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
        
        # 换行
        text = text.replace('\n\n', '</p><p>')
        text = text.replace('\n', '<br>')
        
        return f'''
        <div style="font-family: 'Microsoft YaHei', sans-serif; line-height: 1.8; padding: 20px;">
            <p>{text}</p>
        </div>
            '''
    
    def show_export_dialog(self):
        """显示导出格式选择对话框"""
        result = self.result_output.toPlainText()
        if not result.strip():
            MessageBox("警告", "没有分析结果可导出", self).exec()
            return
        
        dialog = ExportDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            export_format = dialog.get_format()
            self.export_report(export_format)
    
    def export_report(self, export_format: str = "html"):
        """导出AI分析报告"""
        result = self.result_output.toPlainText()
        if not result.strip():
            MessageBox("警告", "没有分析结果可导出", self).exec()
            return
        
        from PyQt6.QtWidgets import QFileDialog
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 根据格式选择文件过滤器
        if export_format == "pdf":
            default_filename = f"AI分析报告_{self.product_name}_{timestamp}.pdf"
            file_filter = "PDF 文件 (*.pdf)"
        else:
            default_filename = f"AI分析报告_{self.product_name}_{timestamp}.html"
            file_filter = "HTML 文件 (*.html)"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存AI分析报告",
            default_filename,
            f"{file_filter};;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            if export_format == "pdf":
                self._export_to_pdf(result, file_path)
            else:
                html_content = self._generate_beautiful_html_report(result)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
            
            QMessageBox.information(self, "导出成功", f"报告已保存到:\n{file_path}")
            self._open_folder(file_path)
            
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"保存报告时出错:\n{str(e)}")
    
    def _export_to_pdf(self, ai_result: str, file_path: str):
        """导出为PDF格式"""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import re
        import os
        
        # 注册中文字体（Windows系统字体）
        try:
            # 尝试注册中文字体
            font_paths = [
                "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
                "C:/Windows/Fonts/simhei.ttf",  # 黑体
                "C:/Windows/Fonts/simsun.ttc",  # 宋体
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # Linux
                "/System/Library/Fonts/PingFang.ttc",  # macOS
            ]
            font_registered = False
            for font_path in font_paths:
                if os.path.exists(font_path):
                    pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
                    font_registered = True
                    break
            
            if not font_registered:
                # 如果找不到中文字体，使用内置字体
                ChineseFont = 'Helvetica'
            else:
                ChineseFont = 'ChineseFont'
        except Exception:
            ChineseFont = 'Helvetica'
        
        # 创建PDF文档
        doc = SimpleDocTemplate(
            file_path,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm
        )
        
        # 获取样式
        styles = getSampleStyleSheet()
        
        # 创建自定义样式（使用中文字体）
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontName=ChineseFont,
            fontSize=20,
            textColor=HexColor('#1e3a8a'),
            spaceAfter=20,
            alignment=TA_CENTER
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontName=ChineseFont,
            fontSize=14,
            textColor=HexColor('#3b82f6'),
            spaceAfter=10,
            spaceBefore=15
        )
        
        subheading_style = ParagraphStyle(
            'CustomSubHeading',
            parent=styles['Heading3'],
            fontName=ChineseFont,
            fontSize=12,
            textColor=HexColor('#1e3a8a'),
            spaceAfter=8,
            spaceBefore=10
        )
        
        body_style = ParagraphStyle(
            'CustomBody',
            parent=styles['Normal'],
            fontName=ChineseFont,
            fontSize=10,
            leading=16,
            spaceAfter=8
        )
        
        # 产品对应的颜色
        product_colors = {
            "办公智盾": "#50C9C3",
            "准入系统": "#107C10",
            "EDR": "#FF8C00",
            "零信任": "#50C9C3"
        }
        product_color = product_colors.get(self.product_name, "#50C9C3")
        
        # 构建PDF内容
        story = []
        
        # 标题
        story.append(Paragraph("AI智能分析报告", title_style))
        story.append(Spacer(1, 10))
        
        # 元信息
        meta_style = ParagraphStyle(
            'Meta',
            parent=styles['Normal'],
            fontName=ChineseFont,
            fontSize=10,
            textColor=HexColor('#666666'),
            alignment=TA_CENTER,
            spaceAfter=30
        )
        story.append(Paragraph(f"产品: {self.product_name} | 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", meta_style))
        story.append(Spacer(1, 20))
        
        # 解析AI结果并添加
        lines = ai_result.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 跳过 Markdown 表格的分隔行（大量=号）
            if re.match(r'^[\s|=~-]+$', line):
                continue
            
            # 标题处理
            if line.startswith('### '):
                story.append(Paragraph(line[4:], heading_style))
            elif line.startswith('## '):
                story.append(Paragraph(line[3:], subheading_style))
            elif line.startswith('# '):
                story.append(Paragraph(line[2:], title_style))
            # 列表项
            elif line.startswith('* ') or line.startswith('- '):
                story.append(Paragraph(f"- {line[2:]}", body_style))
            elif line[0].isdigit() and '. ' in line[:5]:
                idx = line.index('. ')
                story.append(Paragraph(f"- {line[idx+2:]}", body_style))
            # 正常文本
            else:
                # 处理粗体
                display_line = re.sub(r'\*+', '', line)
                story.append(Paragraph(display_line, body_style))
        
        # 生成PDF
        doc.build(story)
    
    def _generate_beautiful_html_report(self, ai_result: str) -> str:
        """生成精美的HTML分析报告 - 仿照分析报告.md格式"""
        from datetime import datetime
        import re
        
        # 预处理：过滤掉Markdown表格分隔行（大量=号）
        lines = ai_result.split('\n')
        filtered_lines = []
        for line in lines:
            if re.match(r'^[\s|:=~-]+$', line):
                continue
            if re.match(r'^[\s-]+$', line.strip('|').strip()):
                continue
            filtered_lines.append(line)
        ai_result = '\n'.join(filtered_lines)
        
        # 处理Markdown表格转换为HTML
        table_lines = []
        in_table = False
        table_html = ""
        result_parts = []
        
        for line in ai_result.split('\n'):
            if '|' in line and line.strip().startswith('|'):
                if not in_table:
                    in_table = True
                    table_lines = []
                table_lines.append(line)
            else:
                if in_table and len(table_lines) > 1:
                    # 构建表格
                    rows = table_lines[1:]  # 跳过表头
                    table_html = '<div class="table-wrapper"><table class="report-table">'
                    # 表头
                    headers = [h.strip() for h in table_lines[0].strip('|').split('|')]
                    table_html += '<thead><tr>' + ''.join(f'<th>{h}</th>' for h in headers) + '</tr></thead>'
                    # 表体
                    table_html += '<tbody>'
                    for row in rows:
                        cells = [c.strip() for c in row.strip('|').split('|')]
                        table_html += '<tr>' + ''.join(f'<td>{c}</td>' for c in cells) + '</tr>'
                    table_html += '</tbody></table></div>'
                    result_parts.append(table_html)
                    table_lines = []
                    in_table = False
                    table_html = ""
                result_parts.append(line)
        
        # 处理剩余的表格
        if in_table and len(table_lines) > 1:
            table_html = '<div class="table-wrapper"><table class="report-table">'
            headers = [h.strip() for h in table_lines[0].strip('|').split('|')]
            table_html += '<thead><tr>' + ''.join(f'<th>{h}</th>' for h in headers) + '</tr></thead>'
            table_html += '<tbody>'
            for row in table_lines[1:]:
                cells = [c.strip() for c in row.strip('|').split('|')]
                table_html += '<tr>' + ''.join(f'<td>{c}</td>' for c in cells) + '</tr>'
            table_html += '</tbody></table></div>'
            result_parts.append(table_html)
        
        # 合并并转换为HTML
        result_html = '\n'.join(result_parts)
        
        # 处理Markdown格式转换为HTML
        # 标题 - 使用标准样式
        result_html = re.sub(r'^### (.+)$', r'<div class="section-h3">\1</div>', result_html, flags=re.MULTILINE)
        result_html = re.sub(r'^## (.+)$', r'<div class="section-h2">\1</div>', result_html, flags=re.MULTILINE)
        result_html = re.sub(r'^# (.+)$', r'<div class="section-h1">\1</div>', result_html, flags=re.MULTILINE)
        
        # 粗体和斜体
        result_html = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', result_html)
        result_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', result_html)
        result_html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', result_html)
        
        # 列表
        result_html = re.sub(r'^\* (.+)$', r'<li>\1</li>', result_html, flags=re.MULTILINE)
        result_html = re.sub(r'^- (.+)$', r'<li>\1</li>', result_html, flags=re.MULTILINE)
        result_html = re.sub(r'^(\d+)\.\s*(.+)$', r'<li>\2</li>', result_html, flags=re.MULTILINE)
        
        # 段落和换行
        lines = result_html.split('\n')
        processed_lines = []
        in_list = False
        in_para = False
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if in_list:
                    processed_lines.append('</ul>')
                    in_list = False
                if in_para:
                    processed_lines.append('</div>')
                    in_para = False
                continue
            
            if stripped.startswith('<div class="section'):
                if in_list:
                    processed_lines.append('</ul>')
                    in_list = False
                if in_para:
                    processed_lines.append('</div>')
                    in_para = False
                processed_lines.append(stripped)
            elif stripped.startswith('<li>'):
                if not in_list:
                    processed_lines.append('<ul class="report-list">')
                    in_list = True
                if in_para:
                    processed_lines.append('</div>')
                    in_para = False
                processed_lines.append(stripped)
            elif stripped.startswith('<div class="table'):
                if in_list:
                    processed_lines.append('</ul>')
                    in_list = False
                if in_para:
                    processed_lines.append('</div>')
                    in_para = False
                processed_lines.append(stripped)
            elif stripped.startswith('<'):
                if in_list:
                    processed_lines.append('</ul>')
                    in_list = False
                if in_para:
                    processed_lines.append('</div>')
                    in_para = False
                processed_lines.append(stripped)
            else:
                if in_list:
                    processed_lines.append('</ul>')
                    in_list = False
                if not in_para:
                    processed_lines.append('<div class="report-para">')
                    in_para = True
                processed_lines.append(stripped)
        
        if in_list:
            processed_lines.append('</ul>')
        if in_para:
            processed_lines.append('</div>')
        
        result_html = '\n'.join(processed_lines)
        
        # 产品对应的颜色
        product_colors = {
            "办公智盾": "#3b82f6",
            "准入系统": "#10b981",
            "EDR": "#f59e0b",
            "零信任": "#8b5cf6"
        }
        product_color = product_colors.get(self.product_name, "#3b82f6")
        timestamp = datetime.now().strftime('%Y-%m-%d')
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI智能分析报告 - {self.product_name}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Microsoft YaHei', 'PingFang SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #f8fafc;
            color: #1e293b;
            line-height: 1.8;
            font-size: 14px;
        }}
        
        .container {{
            max-width: 1100px;
            margin: 0 auto;
            background: white;
            box-shadow: 0 2px 12px rgba(30, 58, 138, 0.08);
        }}
        
        .header {{
            background: linear-gradient(135deg, {product_color} 0%, #3BA8A2 100%);
            color: white;
            padding: 40px 50px;
        }}
        
        .header h1 {{
            font-size: 26px;
            margin-bottom: 8px;
            letter-spacing: 1px;
        }}
        
        .header .subtitle {{
            font-size: 14px;
            opacity: 0.9;
            margin-top: 5px;
        }}
        
        .header .meta {{
            display: flex;
            gap: 30px;
            margin-top: 20px;
            font-size: 13px;
            background: rgba(255,255,255,0.1);
            padding: 12px 20px;
            border-radius: 8px;
            display: inline-flex;
        }}
        
        .content {{
            padding: 40px 50px;
        }}
        
        .section-h1 {{
            font-size: 22px;
            font-weight: 700;
            color: #1e3a8a;
            margin: 30px 0 20px 0;
            padding-bottom: 12px;
            border-bottom: 3px solid {product_color};
        }}
        
        .section-h2 {{
            font-size: 18px;
            font-weight: 700;
            color: #334155;
            margin: 25px 0 15px 0;
            padding-left: 12px;
            border-left: 4px solid {product_color};
        }}
        
        .section-h3 {{
            font-size: 15px;
            font-weight: 600;
            color: #475569;
            margin: 20px 0 12px 0;
            padding-left: 10px;
            border-left: 3px solid #94a3b8;
        }}
        
        .report-para {{
            margin: 12px 0;
            text-align: justify;
            color: #334155;
        }}
        
        .report-list {{
            margin: 12px 0 12px 25px;
            padding-left: 0;
            list-style: none;
        }}
        
        .report-list li {{
            margin: 8px 0;
            padding-left: 20px;
            position: relative;
            color: #334155;
        }}
        
        .report-list li::before {{
            content: "•";
            color: {product_color};
            font-weight: bold;
            position: absolute;
            left: 0;
        }}
        
        .table-wrapper {{
            margin: 20px 0;
            overflow-x: auto;
        }}
        
        .report-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            background: white;
        }}
        
        .report-table th {{
            background: linear-gradient(135deg, {product_color} 0%, #3BA8A2 100%);
            color: white;
            padding: 12px 15px;
            text-align: left;
            font-weight: 600;
            white-space: nowrap;
        }}
        
        .report-table td {{
            padding: 10px 15px;
            border-bottom: 1px solid #e2e8f0;
            color: #334155;
        }}
        
        .report-table tr:nth-child(even) {{
            background: #f8fafc;
        }}
        
        .report-table tr:hover {{
            background: #f1f5f9;
        }}
        
        .footer {{
            background: #f8fafc;
            padding: 20px 50px;
            border-top: 1px solid #e2e8f0;
            text-align: center;
            font-size: 12px;
            color: #64748b;
        }}
        
        .footer strong {{
            color: {product_color};
        }}
        
        strong {{
            color: #1e3a8a;
            font-weight: 600;
        }}
        
        @media print {{
            body {{
                background: white;
            }}
            .container {{
                box-shadow: none;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>AI智能分析报告</h1>
            <div class="subtitle">基于巡检数据的智能分析结果</div>
            <div class="meta">
                <span>产品: {self.product_name}</span>
                <span>日期: {timestamp}</span>
                <span>生成时间: {datetime.now().strftime('%H:%M:%S')}</span>
            </div>
        </div>
        
        <div class="content">
            {result_html}
        </div>
        
        <div class="footer">
            <strong>linux-ssh-inspection-tool</strong> | 智能巡检分析系统
        </div>
    </div>
</body>
</html>"""
        
        return html
    
    def _open_folder(self, file_path: str):
        """打开文件所在文件夹"""
        import platform
        import subprocess
        
        folder = os.path.dirname(file_path)
        
        try:
            if platform.system() == 'Windows':
                os.startfile(folder)
            elif platform.system() == 'Darwin':  # macOS
                subprocess.run(['open', folder])
            else:  # Linux
                subprocess.run(['xdg-open', folder])
        except Exception as e:
            print(f"打开文件夹失败: {str(e)}")
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            QLabel#pageTitle {
                font-size: 18px;
                font-weight: bold;
                color: #50C9C3;
                padding: 5px;
            }
            
            QTabWidget#aiTabWidget {
                background: white;
            }
            
            QTabWidget#aiTabWidget::pane {
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                background: white;
            }
            
            QTabWidget#aiTabWidget::tab-bar {
                alignment: left;
            }
            
            QTabBar::tab {
                background: #F3F3F3;
                border: 1px solid #E1E1E1;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 12px;
            }
            
            QTabBar::tab:selected {
                background: white;
                border-bottom-color: white;
                font-weight: bold;
            }
            
            QTabBar::tab:hover {
                background: #E1E1E1;
            }
            
            QGroupBox {
                font-size: 12px;
                font-weight: bold;
                color: #1A1A1A;
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                margin-top: 6px;
                padding-top: 6px;
                background: white;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 6px;
                background: white;
            }
            
            QPushButton#configButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
                min-height: 36px;
                min-width: 100px;
            }
            
            QPushButton#configButton:hover {
                background: #3BA8A2;
            }
            
            QPushButton#analyzeButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
                min-height: 36px;
                min-width: 100px;
            }
            
            QPushButton#analyzeButton:hover {
                background: #3BA8A2;
            }
            
            QPushButton#analyzeButton:disabled {
                background: #A0A0A0;
                color: #666666;
            }
            
            QPushButton#stopButton {
                background: #D13438;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
                min-height: 36px;
                min-width: 100px;
            }
            
            QPushButton#stopButton:hover {
                background: #A02E2C;
            }
            
            QPushButton#stopButton:disabled {
                background: #A0A0A0;
                color: #666666;
            }
            
            QPushButton#exportButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
                min-height: 36px;
                min-width: 100px;
            }
            
            QPushButton#exportButton:hover {
                background: #3BA8A2;
            }
            
            QPushButton#exportButton:disabled {
                background: #A0A0A0;
                color: #666666;
            }
            
            QTextBrowser#resultOutput {
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                padding: 10px;
                background: white;
            }
        """)
