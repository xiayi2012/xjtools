"""
大模型配置页面模块
提供AI模型API配置的独立页面
"""

import os
import json
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QGroupBox, QLineEdit,
    QComboBox, QTextEdit, QMessageBox, QSplitter,
    QFrame, QScrollArea, QDialog, QSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from qfluentwidgets import PrimaryPushButton, InfoBar

from utils import get_config_dir, get_ai_config_path
from .dialog_icons import dialog_icon


class AIConfigPage(QWidget):
    """大模型配置页面"""

    PROVIDER_OPTIONS = [
        ("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
        ("anthropic", "Claude (Anthropic)", "https://api.anthropic.com/v1", "claude-3-5-sonnet-latest"),
        ("azure", "Azure OpenAI", "", "gpt-35-turbo"),
        ("ollama", "本地模型 (Ollama等)", "http://localhost:11434/v1", "llama3"),
        ("mimo", "小米 MiMo", "https://api.xiaomimimo.com/v1", "mimo-v2-flash"),
        ("minimax", "MiniMax 国内", "https://api.minimaxi.com/v1", "MiniMax-M2.7"),
        ("minimax_global", "MiniMax 国外", "https://api.minimax.io/v1", "MiniMax-M2.7"),
        ("zhipu", "智谱AI (ChatGLM)", "https://open.bigmodel.cn/api/paas/v4", "glm-4-plus"),
        ("baichuan", "百川智能 (Baichuan)", "https://api.baichuan-ai.com/v1", "Baichuan4"),
        ("deepseek", "DeepSeek 官方 / 国内", "https://api.deepseek.com", "deepseek-chat"),
        ("qwen", "通义千问 / 阿里云百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        ("siliconflow", "硅基流动 (SiliconFlow)", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3"),
        ("openai_global", "OpenAI 官方 / 国外", "https://api.openai.com/v1", "gpt-4o-mini"),
    ]
    PROVIDER_DEFAULTS = {
        key: {"base_url": base_url, "model": model}
        for key, _label, base_url, model in PROVIDER_OPTIONS
    }
    PROVIDER_MODELS = {
        "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
        "openai_global": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
        "anthropic": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"],
        "azure": ["gpt-35-turbo", "gpt-4o-mini", "gpt-4o"],
        "ollama": ["llama3", "llama3.1", "qwen2.5", "deepseek-r1"],
        "mimo": ["mimo-v2-flash", "mimo-v2-pro", "mimo-v2-omni", "xiaomi/mimo-v2-flash"],
        "minimax": ["MiniMax-M2.7", "MiniMax-M1"],
        "minimax_global": ["MiniMax-M2.7", "MiniMax-M1"],
        "zhipu": ["glm-4-plus", "glm-4-flash", "glm-4-air"],
        "baichuan": ["Baichuan4", "Baichuan3-Turbo"],
        "deepseek": ["deepseek-chat", "deepseek-reasoner"],
        "qwen": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen-long"],
        "siliconflow": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-72B-Instruct"],
    }
    LEGACY_PROVIDER_KEY = {
        0: "openai",
        1: "anthropic",
        2: "azure",
        3: "ollama",
        5: "minimax",
        6: "zhipu",
        7: "baichuan",
        8: "deepseek",
        9: "qwen",
        10: "siliconflow",
        12: "openai_global",
        13: "deepseek",
        14: "qwen",
    }
    PROVIDER_API_TYPE = {
        "anthropic": "claude",
        "azure": "azure",
        "ollama": "ollama",
    }
    
    def __init__(self):
        """初始化 AI 配置页面"""
        super().__init__()
        self.setObjectName("aiConfigPage")
        self.config = {}
        self.model_configs = []
        self.current_model_index = 0
        self._loading_model = False
        self._editing_model = False
        self._edit_snapshot = None
        self._edit_snapshot_index = 0
        self.model_list_layout = None
        self.init_ui()
        self.load_config()

        # 连接大模型厂商变化信号，自动填充默认参数
        self.api_type_combo.currentIndexChanged.connect(self._on_api_type_changed)
    
    def init_ui(self):
        """初始化 UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(32, 24, 32, 24)
        main_layout.setSpacing(18)
        
        # ===== 标题区域 =====
        title_layout = QHBoxLayout()
        title_layout.setSpacing(12)
        
        # 标题文字
        title_box = QVBoxLayout()
        title_label = QLabel("大模型配置")
        title_label.setObjectName("pageTitle")
        title_box.addWidget(title_label)
        
        subtitle_label = QLabel("配置AI服务接口参数，用于巡检报告生成、智能排障和结果分析")
        subtitle_label.setObjectName("pageSubtitle")
        title_box.addWidget(subtitle_label)
        
        title_layout.addLayout(title_box)
        title_layout.addStretch()
        
        main_layout.addLayout(title_layout)
        
        # ===== 主内容区域 =====
        content_layout = QHBoxLayout()
        content_layout.setSpacing(20)
        
        # ----- 左侧：API配置 -----
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setStyleSheet("background: transparent;")
        
        left_content = QWidget()
        left_content.setStyleSheet("background: transparent;")
        left_vbox = QVBoxLayout(left_content)
        left_vbox.setContentsMargins(0, 0, 0, 0)
        left_vbox.setSpacing(16)
        
        # API配置卡片
        api_card = self._create_api_card()
        left_vbox.addWidget(api_card)
        
        # 保存按钮和测试按钮
        self.btn_container = QWidget()
        self.btn_container.setStyleSheet("background: transparent;")
        btn_hbox = QHBoxLayout(self.btn_container)
        btn_hbox.setContentsMargins(0, 0, 0, 0)
        btn_hbox.setSpacing(10)
        
        self.test_btn = QPushButton("连接测试")
        self.test_btn.setObjectName("testConnectionButton")
        self.test_btn.setFixedSize(140, 42)
        self.test_btn.clicked.connect(self.test_connection)
        btn_hbox.addWidget(self.test_btn)
        
        self.save_btn = PrimaryPushButton("保存配置")
        self.save_btn.setObjectName("saveConfigButton")
        self.save_btn.setFixedSize(140, 42)
        self.save_btn.clicked.connect(self.save_config)
        btn_hbox.addWidget(self.save_btn)

        self.cancel_edit_btn = QPushButton("返回列表")
        self.cancel_edit_btn.setObjectName("testConnectionButton")
        self.cancel_edit_btn.setFixedSize(140, 42)
        self.cancel_edit_btn.clicked.connect(self.close_editor)
        btn_hbox.addWidget(self.cancel_edit_btn)

        btn_hbox.addStretch()
        self._apply_action_button_styles()
        self.btn_container.hide()
        
        left_vbox.addWidget(self.btn_container)
        left_vbox.addStretch()
        
        left_scroll.setWidget(left_content)
        content_layout.addWidget(left_scroll, 1)
        
        main_layout.addLayout(content_layout, 1)
        self.apply_styles()
    
    def _create_api_card(self) -> QWidget:
        """创建API配置卡片"""
        card = QFrame()
        card.setObjectName("apiCard")
        card.setFrameShape(QFrame.Shape.NoFrame)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(18)
        
        # 卡片标题
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        
        title = QLabel("大模型接口配置")
        title.setObjectName("cardTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.add_model_btn = QPushButton("添加大模型")
        self.add_model_btn.setObjectName("smallActionButton")
        self.add_model_btn.setFixedSize(110, 40)
        self.add_model_btn.clicked.connect(self.add_model_profile)
        header_layout.addWidget(self.add_model_btn)
        
        layout.addLayout(header_layout)
        
        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: #e5e7eb; max-height: 1px;")
        layout.addWidget(line)

        self.model_list_widget = QWidget()
        self.model_list_widget.setObjectName("modelListWidget")
        self.model_list_layout = QVBoxLayout(self.model_list_widget)
        self.model_list_layout.setContentsMargins(0, 0, 0, 0)
        self.model_list_layout.setSpacing(10)
        layout.addWidget(self.model_list_widget)

        self.edit_panel = QWidget()
        self.edit_panel.setObjectName("editPanel")
        edit_layout = QVBoxLayout(self.edit_panel)
        edit_layout.setContentsMargins(0, 8, 0, 0)
        edit_layout.setSpacing(14)

        # 表单项网格
        form_layout = QGridLayout()
        form_layout.setHorizontalSpacing(16)
        form_layout.setVerticalSpacing(14)
        form_layout.setColumnMinimumWidth(0, 100)
        form_layout.setColumnStretch(0, 0)
        form_layout.setColumnStretch(1, 1)
        
        row = 0

        # 配置名称
        form_layout.addWidget(self._create_form_label("配置名称"), row, 0)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如：主模型、备用模型1")
        self.name_input.setMinimumHeight(40)
        form_layout.addWidget(self.name_input, row, 1)
        row += 1
        
        # 大模型厂商
        form_layout.addWidget(self._create_form_label("大模型厂商"), row, 0)
        self.api_type_combo = QComboBox()
        for key, label, _base_url, _model in self.PROVIDER_OPTIONS:
            self.api_type_combo.addItem(label, key)
        self.api_type_combo.setMinimumHeight(40)
        form_layout.addWidget(self.api_type_combo, row, 1)
        row += 1
        
        # API Key
        form_layout.addWidget(self._create_form_label("API Key"), row, 0)
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("输入您的 API Key")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setMinimumHeight(40)
        form_layout.addWidget(self.api_key_input, row, 1)
        row += 1
        
        # Base URL
        form_layout.addWidget(self._create_form_label("Base URL"), row, 0)
        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("选择大模型厂商后自动带出，可按需修改")
        self.base_url_input.setMinimumHeight(40)
        self.base_url_input.editingFinished.connect(self._on_base_url_changed)
        form_layout.addWidget(self.base_url_input, row, 1)
        row += 1
        
        # Model
        form_layout.addWidget(self._create_form_label("模型名称"), row, 0)
        self.model_input = QComboBox()
        self.model_input.setEditable(True)
        self.model_input.setPlaceholderText("选择或输入模型名称")
        self.model_input.setMinimumHeight(40)
        form_layout.addWidget(self.model_input, row, 1)
        row += 1
        
        # Timeout
        form_layout.addWidget(self._create_form_label("超时时间"), row, 0)
        timeout_widget = QWidget()
        timeout_layout = QHBoxLayout(timeout_widget)
        timeout_layout.setContentsMargins(0, 0, 0, 0)
        timeout_layout.setSpacing(6)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(30, 600)
        self.timeout_spin.setValue(300)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setMinimumHeight(40)
        self.timeout_spin.setToolTip("API请求超时时间，网络较慢时可适当增大")
        timeout_layout.addWidget(self.timeout_spin)
        timeout_hint = QLabel("网络较慢时可适当增大")
        timeout_hint.setStyleSheet("font-size: 11px; color: #9ca3af;")
        timeout_layout.addWidget(timeout_hint)
        form_layout.addWidget(timeout_widget, row, 1)
        
        edit_layout.addLayout(form_layout)
        
        self.edit_panel.hide()
        layout.addWidget(self.edit_panel)

        return card
    
    def _create_form_label(self, text: str) -> QLabel:
        """创建表单标签"""
        label = QLabel(text)
        label.setObjectName("formLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        label.setContentsMargins(0, 0, 10, 0)
        return label

    def apply_styles(self):
        self.setStyleSheet("""
            QWidget#aiConfigPage {
                background: #f8fafc;
            }
            QLabel#pageTitle {
                font-size: 24px;
                font-weight: 700;
                color: #0f172a;
            }
            QLabel#pageSubtitle {
                font-size: 13px;
                color: #64748b;
                margin-top: 2px;
            }
            QFrame#apiCard {
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
            }
            QLabel#cardTitle {
                font-size: 16px;
                font-weight: 700;
                color: #111827;
            }
            QLabel#formLabel {
                font-size: 13px;
                color: #374151;
                font-weight: 600;
            }
            QLabel#hintText {
                font-size: 12px;
                color: #94a3b8;
            }
            QLabel#sectionHint {
                font-size: 13px;
                color: #475569;
                font-weight: 600;
            }
            QFrame#modelCard {
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
            }
            QFrame#modelCardSelected {
                background: #ecfeff;
                border: 1px solid #50C9C3;
                border-radius: 8px;
            }
            QWidget#editPanel {
                background: transparent;
            }
            QLabel#modelCardTitle {
                font-size: 14px;
                font-weight: 700;
                color: #0f172a;
            }
            QLabel#modelCardMeta {
                font-size: 12px;
                color: #64748b;
            }
            QLabel#modelCardBadge {
                font-size: 12px;
                font-weight: 700;
                color: #0f766e;
                background: #ccfbf1;
                border-radius: 10px;
                padding: 2px 8px;
            }
            QLabel#modelCardStatus {
                font-size: 12px;
                font-weight: 700;
                color: #0f766e;
                background: #ccfbf1;
                border: 1px solid #5eead4;
                border-radius: 10px;
                padding: 2px 8px;
            }
            QLineEdit, QComboBox, QSpinBox {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                background: #ffffff;
                color: #0f172a;
                padding: 0 10px;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid #50C9C3;
            }
            QPushButton#testConnectionButton,
            QPushButton#saveConfigButton {
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 700;
                min-width: 140px;
                max-width: 140px;
                min-height: 42px;
                max-height: 42px;
            }
            QPushButton#smallActionButton {
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 700;
                min-height: 40px;
                max-height: 40px;
            }
            QPushButton#cardActionButton {
                background: #f8fafc;
                color: #0f766e;
                border: 1px solid #99f6e4;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 700;
                min-width: 58px;
                max-width: 58px;
                min-height: 30px;
                max-height: 30px;
            }
            QPushButton#cardActionButton:hover {
                background: #ccfbf1;
                color: #0f766e;
                border: 1px solid #50C9C3;
            }
            QPushButton#cardActionButton:pressed {
                background: #99f6e4;
                color: #115e59;
            }
            QPushButton#cardActionButton:disabled {
                background: #ccfbf1;
                color: #0f766e;
                border: 1px solid #50C9C3;
            }
            QPushButton#cardDangerButton {
                background: #fff7ed;
                color: #c2410c;
                border: 1px solid #fed7aa;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 700;
                min-width: 58px;
                max-width: 58px;
                min-height: 30px;
                max-height: 30px;
            }
            QPushButton#cardDangerButton:hover {
                background: #fed7aa;
                color: #9a3412;
                border: 1px solid #fb923c;
            }
            QPushButton#cardDangerButton:pressed {
                background: #fdba74;
                color: #7c2d12;
            }
            QPushButton#testConnectionButton:hover,
            QPushButton#saveConfigButton:hover,
            QPushButton#smallActionButton:hover {
                background: #3BA8A2;
            }
            QPushButton#testConnectionButton:pressed,
            QPushButton#saveConfigButton:pressed,
            QPushButton#smallActionButton:pressed {
                background: #2f8f8a;
            }
            QPushButton#testConnectionButton:disabled,
            QPushButton#saveConfigButton:disabled,
            QPushButton#smallActionButton:disabled {
                background: #94a3b8;
            }
        """)

    def _apply_action_button_styles(self):
        button_style = """
            QPushButton {
                background-color: #50C9C3;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #3BA8A2;
            }
            QPushButton:pressed {
                background-color: #2f8f8a;
            }
            QPushButton:disabled {
                background-color: #94a3b8;
                color: #ffffff;
            }
        """
        self.test_btn.setStyleSheet(button_style)
        self.save_btn.setStyleSheet(button_style)
        if hasattr(self, "cancel_edit_btn"):
            self.cancel_edit_btn.setStyleSheet(button_style)
        if hasattr(self, "add_model_btn"):
            self.add_model_btn.setStyleSheet(button_style)

    def _api_type_for_provider(self, provider_key: str) -> str:
        return self.PROVIDER_API_TYPE.get(provider_key, "openai")

    def _provider_key_from_config(self, config: dict) -> str:
        provider_key = config.get("provider_key")
        if provider_key:
            return provider_key
        api_type_value = config.get("api_type", 0)
        if isinstance(api_type_value, str):
            return {
                "openai": "openai",
                "claude": "anthropic",
                "anthropic": "anthropic",
                "azure": "azure",
                "local": "ollama",
                "ollama": "ollama",
                "mimo": "mimo",
                "minimax": "minimax",
                "minimax_global": "minimax_global",
            }.get(api_type_value, "openai")
        return self.LEGACY_PROVIDER_KEY.get(api_type_value, "openai")

    def _models_for_provider(self, provider_key: str) -> list[str]:
        return self.PROVIDER_MODELS.get(provider_key, [])

    def _infer_provider_from_base_url(self, base_url: str) -> str | None:
        url = (base_url or "").lower()
        if "xiaomimimo.com" in url:
            return "mimo"
        if "minimaxi.com" in url:
            return "minimax"
        if "minimax.io" in url:
            return "minimax_global"
        if "siliconflow.cn" in url:
            return "siliconflow"
        if "deepseek.com" in url:
            return "deepseek"
        if "dashscope.aliyuncs.com" in url:
            return "qwen"
        if "bigmodel.cn" in url:
            return "zhipu"
        if "baichuan-ai.com" in url:
            return "baichuan"
        if "anthropic.com" in url:
            return "anthropic"
        if "localhost:11434" in url or "127.0.0.1:11434" in url:
            return "ollama"
        if "openai.com" in url:
            return "openai_global"
        return None

    def _set_model_options(self, provider_key: str, selected_model: str = ""):
        current_model = (selected_model or self.model_input.currentText() or "").replace("`", "").strip()
        models = self._models_for_provider(provider_key)
        self.model_input.blockSignals(True)
        self.model_input.clear()
        self.model_input.addItems(models)
        if current_model:
            self.model_input.setCurrentText(current_model)
        elif models:
            self.model_input.setCurrentIndex(0)
        self.model_input.blockSignals(False)

    def _default_model_config(self, index: int = 0) -> dict:
        defaults = self.PROVIDER_DEFAULTS.get("openai", {})
        return {
            "name": "主模型" if index == 0 else f"备用模型{index}",
            "api_type": "openai",
            "provider_key": "openai",
            "api_key": "",
            "base_url": defaults.get("base_url", ""),
            "model": defaults.get("model", ""),
            "api_timeout": 300,
            "enabled": True,
        }

    def _current_form_config(self) -> dict:
        provider_key = self.api_type_combo.currentData() or "openai"
        name = self.name_input.text().strip() or "主模型"
        if 0 <= self.current_model_index < len(self.model_configs):
            name = name or self.model_configs[self.current_model_index].get("name", "主模型")
        return {
            "name": name,
            "api_type": self._api_type_for_provider(provider_key),
            "provider_key": provider_key,
            "api_key": self.api_key_input.text().replace('`', '').strip(),
            "base_url": self.base_url_input.text().replace('`', '').strip(),
            "model": self.model_input.currentText().replace('`', '').strip(),
            "api_timeout": self.timeout_spin.value(),
            "enabled": True,
        }

    def _save_current_form_to_memory(self):
        if self._loading_model:
            return
        if not self.model_configs:
            self.model_configs = [self._current_form_config()]
            self.current_model_index = 0
            return
        if 0 <= self.current_model_index < len(self.model_configs):
            cfg = self._current_form_config()
            self.model_configs[self.current_model_index] = cfg

    def _load_model_to_form(self, index: int):
        if not (0 <= index < len(self.model_configs)):
            return
        self._loading_model = True
        cfg = self.model_configs[index]
        self.name_input.setText(cfg.get("name", "主模型" if index == 0 else f"备用模型{index}"))
        self._set_provider_key(cfg.get("provider_key", "openai"))
        self.api_key_input.setText(cfg.get("api_key", ""))
        self.base_url_input.setText(cfg.get("base_url", ""))
        self._set_model_options(cfg.get("provider_key", "openai"), cfg.get("model", ""))
        self.timeout_spin.setValue(int(cfg.get("api_timeout", 300) or 300))
        self._loading_model = False

    def _provider_label(self, provider_key: str) -> str:
        for key, label, _base_url, _model in self.PROVIDER_OPTIONS:
            if key == provider_key:
                return label
        return provider_key or "OpenAI"

    def _clear_layout(self, layout):
        while layout and layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                self._clear_layout(child_layout)

    def _refresh_model_cards(self, selected_index: int = 0):
        if self.model_list_layout is None:
            return
        self._clear_layout(self.model_list_layout)
        if not self.model_configs:
            empty_label = QLabel("暂无大模型配置，请点击右上角“添加大模型”。")
            empty_label.setObjectName("hintText")
            self.model_list_layout.addWidget(empty_label)
            return

        selected_index = max(0, min(selected_index, len(self.model_configs) - 1))
        for idx, cfg in enumerate(self.model_configs):
            card = QFrame()
            card.setObjectName("modelCardSelected" if idx == 0 else "modelCard")
            card.setFrameShape(QFrame.Shape.NoFrame)

            row_layout = QHBoxLayout(card)
            row_layout.setContentsMargins(14, 12, 14, 12)
            row_layout.setSpacing(12)

            if idx > 0:
                badge = QLabel(f"备用{idx}")
                badge.setObjectName("modelCardBadge")
                badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                row_layout.addWidget(badge)

            text_box = QVBoxLayout()
            text_box.setSpacing(4)
            display_title = cfg.get("model") or cfg.get("name") or "未填写模型"
            if display_title in ("主模型", "大模型") or display_title.startswith("备用模型"):
                display_title = cfg.get("model") or "未填写模型"
            title = QLabel(display_title)
            title.setObjectName("modelCardTitle")
            text_box.addWidget(title)
            provider = self._provider_label(cfg.get("provider_key", "openai"))
            model = cfg.get("model", "未填写模型")
            base_url = cfg.get("base_url", "未填写 Base URL")
            meta = QLabel(f"{provider} · {model} · {base_url}")
            meta.setObjectName("modelCardMeta")
            meta.setWordWrap(True)
            text_box.addWidget(meta)
            row_layout.addLayout(text_box, 1)

            if idx == 0:
                status = QLabel("当前使用")
                status.setObjectName("modelCardStatus")
                status.setAlignment(Qt.AlignmentFlag.AlignCenter)
                row_layout.addWidget(status)

            use_btn = QPushButton("使用中" if idx == 0 else "选用")
            use_btn.setObjectName("cardActionButton")
            use_btn.setEnabled(idx != 0)
            use_btn.clicked.connect(lambda _checked=False, i=idx: self.use_model_profile(i))
            row_layout.addWidget(use_btn)

            edit_btn = QPushButton("编辑")
            edit_btn.setObjectName("cardActionButton")
            edit_btn.clicked.connect(lambda _checked=False, i=idx: self.edit_model_profile(i))
            row_layout.addWidget(edit_btn)

            delete_btn = QPushButton("删除")
            delete_btn.setObjectName("cardDangerButton")
            delete_btn.clicked.connect(lambda _checked=False, i=idx: self.delete_model_profile(i))
            delete_btn.setEnabled(len(self.model_configs) > 1)
            row_layout.addWidget(delete_btn)

            self.model_list_layout.addWidget(card)

    def _set_editor_visible(self, visible: bool):
        self._editing_model = visible
        if hasattr(self, "edit_panel"):
            self.edit_panel.setVisible(visible)
        if hasattr(self, "btn_container"):
            self.btn_container.setVisible(visible)
        if hasattr(self, "model_list_widget"):
            self.model_list_widget.setVisible(not visible)
        if hasattr(self, "add_model_btn"):
            self.add_model_btn.setVisible(not visible)

    def _begin_edit_session(self):
        if self._edit_snapshot is None:
            self._edit_snapshot = [dict(item) for item in self.model_configs]
            self._edit_snapshot_index = self.current_model_index

    def _discard_edit_session(self):
        if self._edit_snapshot is not None:
            self.model_configs = [dict(item) for item in self._edit_snapshot]
            self.current_model_index = max(0, min(self._edit_snapshot_index, len(self.model_configs) - 1))
            self._edit_snapshot = None
        self._refresh_model_cards(self.current_model_index)
        if self.model_configs:
            self._load_model_to_form(self.current_model_index)

    def _commit_edit_session(self):
        self._edit_snapshot = None
        self._edit_snapshot_index = self.current_model_index

    def close_editor(self):
        if self._editing_model:
            self._discard_edit_session()
        self._set_editor_visible(False)

    def edit_model_profile(self, index: int):
        if self._loading_model or index < 0:
            return
        self._begin_edit_session()
        self.current_model_index = index
        self._load_model_to_form(index)
        self._set_editor_visible(True)

    def use_model_profile(self, index: int):
        if index <= 0 or index >= len(self.model_configs):
            return
        if self._editing_model:
            self._save_current_form_to_memory()
        selected = self.model_configs.pop(index)
        self.model_configs.insert(0, selected)
        self.current_model_index = 0
        self._load_model_to_form(0)
        self._set_editor_visible(False)
        self._refresh_model_cards(0)
        self._write_config(show_message=False)

    def add_model_profile(self):
        self._begin_edit_session()
        new_index = len(self.model_configs)
        base = self._default_model_config(new_index)
        self.model_configs.append(base)
        self.current_model_index = len(self.model_configs) - 1
        self._refresh_model_cards(self.current_model_index)
        self._load_model_to_form(self.current_model_index)
        self._set_editor_visible(True)

    def _confirm_delete_model(self, name: str) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("删除大模型")
        dialog.setWindowIcon(dialog_icon("AI.svg"))
        dialog.setModal(True)
        dialog.setFixedSize(420, 210)
        dialog.setStyleSheet("""
            QDialog {
                background: #ffffff;
                border-radius: 8px;
            }
            QLabel#confirmTitle {
                font-size: 18px;
                font-weight: 700;
                color: #0f172a;
            }
            QLabel#confirmText {
                font-size: 13px;
                color: #475569;
                line-height: 1.5;
            }
            QLabel#confirmName {
                font-size: 13px;
                font-weight: 700;
                color: #0f766e;
                background: #ecfeff;
                border: 1px solid #99f6e4;
                border-radius: 6px;
                padding: 8px 10px;
            }
            QPushButton {
                border-radius: 6px;
                font-size: 13px;
                font-weight: 700;
                min-width: 96px;
                min-height: 36px;
            }
            QPushButton#cancelDeleteButton {
                background: #f8fafc;
                color: #334155;
                border: 1px solid #cbd5e1;
            }
            QPushButton#cancelDeleteButton:hover {
                background: #e2e8f0;
            }
            QPushButton#confirmDeleteButton {
                background: #ef4444;
                color: #ffffff;
                border: 1px solid #ef4444;
            }
            QPushButton#confirmDeleteButton:hover {
                background: #dc2626;
                border: 1px solid #dc2626;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        title = QLabel("确认删除大模型配置？")
        title.setObjectName("confirmTitle")
        layout.addWidget(title)

        text = QLabel("删除后该配置将从备用列表中移除，后续不会参与失败自动切换。")
        text.setObjectName("confirmText")
        text.setWordWrap(True)
        layout.addWidget(text)

        name_label = QLabel(name)
        name_label.setObjectName("confirmName")
        layout.addWidget(name_label)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelDeleteButton")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        confirm_btn = QPushButton("删除")
        confirm_btn.setObjectName("confirmDeleteButton")
        confirm_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(confirm_btn)
        layout.addLayout(btn_layout)

        return dialog.exec() == QDialog.DialogCode.Accepted

    def delete_model_profile(self, index: int | None = None):
        if len(self.model_configs) <= 1:
            QMessageBox.information(self, "提示", "至少保留一个大模型配置")
            return
        if index is None:
            index = self.current_model_index
        if index < 0:
            return
        name = self.model_configs[index].get("name", "大模型")
        if not self._confirm_delete_model(name):
            return
        was_editing_deleted = index == self.current_model_index
        del self.model_configs[index]
        if was_editing_deleted:
            self.current_model_index = 0
            self._set_editor_visible(False)
        elif index < self.current_model_index:
            self.current_model_index -= 1
        self.current_model_index = max(0, min(self.current_model_index, len(self.model_configs) - 1))
        self._refresh_model_cards(self.current_model_index)
        self._load_model_to_form(self.current_model_index)
        self._write_config(show_message=False)
    
    def load_config(self):
        """加载保存的配置"""
        self._edit_snapshot = None
        config_file = get_ai_config_path()

        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)

                raw_models = self.config.get("models")
                if isinstance(raw_models, list) and raw_models:
                    self.model_configs = []
                    for idx, item in enumerate(raw_models):
                        if not isinstance(item, dict):
                            continue
                        model_config = dict(item)
                        provider_key = self._provider_key_from_config(model_config)
                        model_config["provider_key"] = provider_key
                        model_config["api_type"] = self._api_type_for_provider(provider_key)
                        model_config.setdefault("name", "主模型" if idx == 0 else f"备用模型{idx}")
                        model_config.setdefault("enabled", True)
                        self.model_configs.append(model_config)
                else:
                    provider_key = self._provider_key_from_config(self.config)
                    self.model_configs = [{
                        "name": "主模型",
                        "api_type": self._api_type_for_provider(provider_key),
                        "provider_key": provider_key,
                        "api_key": self.config.get("api_key", ""),
                        "base_url": self.config.get("base_url", ""),
                        "model": self.config.get("model", ""),
                        "api_timeout": self.config.get("api_timeout", 300),
                        "enabled": True,
                    }]
                if not self.model_configs:
                    self.model_configs = [self._default_model_config(0)]
                self.current_model_index = 0
                self._refresh_model_cards(0)
                self._load_model_to_form(0)
            except Exception as e:
                print(f"[AI Config] Failed to load config: {e}")
                self.model_configs = [self._default_model_config(0)]
                self._refresh_model_cards(0)
                self._load_model_to_form(0)
        else:
            self.model_configs = [self._default_model_config(0)]
            self._refresh_model_cards(0)
            self._load_model_to_form(0)

    def save_config(self):
        """保存配置"""
        if not self._write_config(show_message=True):
            return
        self._commit_edit_session()
        self._set_editor_visible(False)

    def _write_config(self, show_message: bool = True) -> bool:
        """写入配置文件。列表第一项即当前选用模型。"""
        self._save_current_form_to_memory()
        if not self.model_configs:
            self.model_configs = [self._current_form_config()]
        primary = dict(self.model_configs[0])
        config = {
            "api_type": primary.get("api_type", "openai"),
            "provider_key": primary.get("provider_key", "openai"),
            "api_key": primary.get("api_key", ""),
            "base_url": primary.get("base_url", ""),
            "model": primary.get("model", ""),
            "api_timeout": primary.get("api_timeout", 300),
            "models": self.model_configs,
        }

        config_file = get_ai_config_path()

        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self._refresh_model_cards(self.current_model_index)
            if show_message:
                InfoBar.success(title="保存成功", content="大模型配置已保存", parent=self, duration=2000)
            return True
        except Exception as e:
            if show_message:
                QMessageBox.warning(self, "保存失败", f"保存配置失败:\n{str(e)}")
            return False

    def test_connection(self):
        """测试AI API连接"""
        import urllib.request
        import urllib.error
        import ssl

        self._save_current_form_to_memory()
        api_key = self.api_key_input.text().replace('`', '').strip()
        base_url = self.base_url_input.text().replace('`', '').strip()
        model = self.model_input.currentText().replace('`', '').strip()

        if not api_key:
            QMessageBox.warning(self, "缺少配置", "请先输入 API Key")
            return
        if not base_url:
            QMessageBox.warning(self, "缺少配置", "请先输入 Base URL")
            return
        if not model:
            QMessageBox.warning(self, "缺少配置", "请先输入模型名称")
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中...")

        result_dialog = QDialog(self)
        result_dialog.setWindowTitle("连接测试结果")
        result_dialog.setWindowIcon(dialog_icon("AI.svg"))
        result_dialog.setMinimumSize(500, 400)
        result_dialog.setStyleSheet("""
            QDialog { background: white; }
            QLabel { font-size: 14px; color: #374151; }
            QTextEdit {
                border: 1px solid #e5e7eb; border-radius: 6px;
                padding: 10px; font-size: 13px; background: #f9fafb;
            }
            QPushButton {
                background: #50C9C3; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-size: 13px;
            }
            QPushButton:hover { background: #3BA8A2; }
        """)

        layout = QVBoxLayout(result_dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        status_label = QLabel("正在测试API连接...")
        layout.addWidget(status_label)

        result_text = QTextEdit()
        result_text.setReadOnly(True)
        layout.addWidget(result_text, 1)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(result_dialog.accept)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        result_dialog.show()

        from PyQt6.QtCore import QThread
        
        class TestWorker(QThread):
            finished = pyqtSignal(bool, str, str)
            
            def __init__(self, api_key, base_url, model):
                super().__init__()
                self.api_key = api_key
                self.base_url = base_url
                self.model = model
            
            def run(self):
                import urllib.request
                import urllib.error
                import ssl
                try:
                    clean_base_url = self.base_url.strip().rstrip('/')
                    if clean_base_url.endswith('/chat/completions'):
                        url = clean_base_url
                    else:
                        if 'siliconflow.cn' in clean_base_url and not clean_base_url.endswith('/v1'):
                            clean_base_url = clean_base_url + '/v1'
                        url = clean_base_url + '/chat/completions'
                    data = json.dumps({
                        "model": self.model,
                        "messages": [{"role": "user", "content": "你是谁？请简短回答。"}],
                        "max_tokens": 100,
                        "temperature": 0.7
                    }).encode('utf-8')
                    req = urllib.request.Request(
                        url, data=data,
                        headers={
                            'Content-Type': 'application/json',
                            'Authorization': f'Bearer {self.api_key}'
                        },
                        method='POST'
                    )
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                        response_data = response.read().decode('utf-8')
                        result = json.loads(response_data)
                        if 'choices' in result and len(result['choices']) > 0:
                            ai_reply = result['choices'][0]['message']['content']
                            model_info = result.get('model', self.model)
                            self.finished.emit(True, f"{url}|{model_info}|{ai_reply}", "")
                        else:
                            self.finished.emit(False, "", f"返回格式异常: {response_data[:200]}")
                except urllib.error.HTTPError as e:
                    error_body = ""
                    try:
                        error_body = e.read().decode('utf-8')
                    except:
                        pass
                    if e.code in (401, 403):
                        error_desc = f"认证失败(HTTP {e.code}) - API Key无效或没有权限"
                    elif e.code == 404:
                        error_desc = "接口不存在(HTTP 404) - 请检查Base URL路径是否正确"
                    elif e.code == 500:
                        error_desc = "服务器内部错误(HTTP 500) - API服务端异常，请稍后重试"
                    else:
                        error_desc = f"HTTP错误({e.code}) - {e.reason}"
                    self.finished.emit(False, "", f"{error_desc}|{error_body[:500] if error_body else '无详细错误信息'}")
                except urllib.error.URLError as e:
                    self.finished.emit(False, "", f"网络错误|{str(e.reason)}")
                except Exception as e:
                    error_msg = str(e)
                    if 'timed out' in error_msg.lower() or 'timeout' in error_msg.lower():
                        self.finished.emit(False, "", "请求超时|API服务器响应太慢或网络延迟过高")
                    elif 'SSL' in error_msg or 'certificate' in error_msg.lower():
                        self.finished.emit(False, "", "SSL证书错误|服务器证书无效或不受信任")
                    else:
                        self.finished.emit(False, "", f"测试异常|{error_msg}")
        
        def on_test_result(success, data, error):
            self.test_btn.setEnabled(True)
            self.test_btn.setText("连接测试")
            
            if success:
                parts = data.split('|', 2)
                url = parts[0] if len(parts) > 0 else ''
                model_info = parts[1] if len(parts) > 1 else model
                ai_reply = parts[2] if len(parts) > 2 else ''
                
                status_label.setText("连接成功")
                status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #10b981;")
                result_text.setHtml(f"""
                <div style="padding: 10px;">
                    <p><b>API地址:</b> {url}</p>
                    <p><b>模型:</b> {model_info}</p>
                    <hr style="margin: 10px 0;">
                    <p><b>AI回复:</b></p>
                    <pre style="background: #ecfdf5; padding: 10px; border-radius: 6px; white-space: pre-wrap;">{ai_reply}</pre>
                </div>
                """)
            else:
                parts = error.split('|', 1)
                error_type = parts[0] if len(parts) > 0 else '未知错误'
                error_detail = parts[1] if len(parts) > 1 else ''
                
                status_label.setText("连接失败")
                status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #ef4444;")
                result_text.setHtml(f"""
                <div style="padding: 10px;">
                    <p><b>错误类型:</b> {error_type}</p>
                    <p><b>详细信息:</b></p>
                    <pre style="background: #fef2f2; padding: 10px; border-radius: 6px; white-space: pre-wrap; max-height: 200px; overflow-y: auto;">{error_detail}</pre>
                    <hr style="margin: 10px 0;">
                    <p><b>常见原因:</b></p>
                    <ul>
                        <li>API Key 无效或过期</li>
                        <li>Base URL 地址不正确</li>
                        <li>模型名称不存在</li>
                        <li>网络连接问题</li>
                    </ul>
                </div>
                """)
        
        worker = TestWorker(api_key, base_url, model)
        worker.finished.connect(on_test_result)
        worker.start()
        self._test_worker = worker

    def _set_provider_key(self, provider_key: str):
        for index in range(self.api_type_combo.count()):
            if self.api_type_combo.itemData(index) == provider_key:
                self.api_type_combo.blockSignals(True)
                self.api_type_combo.setCurrentIndex(index)
                self.api_type_combo.blockSignals(False)
                return
        self.api_type_combo.blockSignals(True)
        self.api_type_combo.setCurrentIndex(0)
        self.api_type_combo.blockSignals(False)

    def _on_api_type_changed(self, index: int):
        """当大模型厂商改变时，自动填充默认参数"""
        if self._loading_model:
            return
        provider_key = self.api_type_combo.itemData(index)
        defaults = self.PROVIDER_DEFAULTS.get(provider_key)
        if not defaults:
            return

        self.base_url_input.setText(defaults["base_url"])
        self._set_model_options(provider_key, defaults["model"])

    def _on_base_url_changed(self):
        """手动修改 Base URL 后，根据地址特征刷新可选模型。"""
        if self._loading_model:
            return
        provider_key = self._infer_provider_from_base_url(self.base_url_input.text())
        if not provider_key:
            return
        self._set_provider_key(provider_key)
        default_model = self.PROVIDER_DEFAULTS.get(provider_key, {}).get("model", "")
        self._set_model_options(provider_key, default_model)
