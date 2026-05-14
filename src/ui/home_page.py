"""Application guide home page."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
)


class HomePage(QWidget):
    """首页：软件配置和使用引导。"""

    start_clicked = pyqtSignal()
    ai_config_clicked = pyqtSignal()
    server_config_clicked = pyqtSignal()
    inspection_clicked = pyqtSignal()
    troubleshoot_clicked = pyqtSignal()
    system_guard_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(28, 24, 28, 24)
        hero_layout.setSpacing(10)

        title = QLabel("使用指导：")
        title.setObjectName("heroTitle")
        hero_layout.addWidget(title)

        subtitle = QLabel(
            "AI 智能运维工具由安恒终端安全产品线自研，高度应用 AI 能力，可实现软件产品自动化巡检、"
            "全时段运行状态监控、AI 智能故障排错，以及故障日志一键收集，精准溯源问题根源，"
            "以智能化赋能运维提质增效，保障系统稳定可靠运行。"
        )
        subtitle.setObjectName("heroSubtitle")
        subtitle.setWordWrap(True)
        hero_layout.addWidget(subtitle)

        layout.addWidget(hero)

        section_title = QLabel("使用步骤")
        section_title.setObjectName("sectionTitle")
        layout.addWidget(section_title)

        grid = QGridLayout()
        grid.setSpacing(14)
        steps = [
            ("01", "配置大模型", "选择大模型厂商，填写 API Key、Base URL 和模型名称，用于AI分析、报告生成和排障。", "去配置", self.ai_config_clicked),
            ("02", "添加服务器", "在配置管理中添加产品和服务器，填写SSH连接信息，并可立即连接测试。", "去添加", self.server_config_clicked),
            ("03", "选择巡检项", "确认当前产品的默认巡检项、产品脚本和自定义脚本，取消不需要执行的项目。", "去选择", self.inspection_clicked),
            ("04", "执行AI巡检", "连接服务器后执行巡检，查看巡检输出和预览，并导出AI分析报告。", "去巡检", self.inspection_clicked),
            ("05", "AI智能排障", "服务器连接后输入故障现象，结合知识库、本地搜索和远程命令辅助定位问题。", "去排障", self.troubleshoot_clicked),
            ("06", "系统监护", "设置自动巡检任务，到点自动连接服务器并生成巡检报告。", "去设置", self.system_guard_clicked),
        ]

        for index, step in enumerate(steps):
            card = self._step_card(*step)
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addStretch()

        self.apply_styles()

    def _step_card(self, number: str, title: str, desc: str, button_text: str, signal) -> QFrame:
        card = QFrame()
        card.setObjectName("stepCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        badge = QLabel(number)
        badge.setObjectName("stepBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step_title = QLabel(title)
        step_title.setObjectName("stepTitle")
        header.addWidget(badge)
        header.addWidget(step_title)
        header.addStretch()
        card_layout.addLayout(header)

        body = QLabel(desc)
        body.setWordWrap(True)
        body.setObjectName("stepDesc")
        card_layout.addWidget(body)

        button_row = QHBoxLayout()
        button_row.addStretch()
        btn = QPushButton(button_text)
        btn.setObjectName("stepButton")
        btn.setFixedSize(96, 34)
        btn.clicked.connect(signal)
        button_row.addWidget(btn)
        card_layout.addLayout(button_row)
        return card

    def apply_styles(self):
        self.setStyleSheet("""
            QWidget {
                background: #f8fafc;
            }
            QFrame#hero {
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
            }
            QLabel#heroTitle {
                font-size: 28px;
                font-weight: 700;
                color: #0f766e;
            }
            QLabel#heroSubtitle {
                font-size: 15px;
                color: #475569;
                line-height: 1.5;
            }
            QLabel#sectionTitle {
                font-size: 18px;
                font-weight: 700;
                color: #111827;
                padding: 4px 0;
            }
            QFrame#stepCard {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            QFrame#stepCard:hover {
                border-color: #50C9C3;
                background: #f8fffe;
            }
            QLabel#stepBadge {
                min-width: 34px;
                max-width: 34px;
                min-height: 28px;
                max-height: 28px;
                border-radius: 6px;
                background: #e6fffb;
                color: #0f766e;
                font-weight: 700;
                font-size: 13px;
            }
            QLabel#stepTitle {
                font-size: 16px;
                font-weight: 700;
                color: #111827;
            }
            QLabel#stepDesc {
                font-size: 13px;
                color: #64748b;
                line-height: 1.5;
            }
            QPushButton#secondaryButton,
            QPushButton#stepButton {
                background: #ffffff;
                color: #0f766e;
                border: 1px solid #50C9C3;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#secondaryButton:hover,
            QPushButton#stepButton:hover {
                background: #ecfdf5;
                border-color: #3BA8A2;
            }
        """)
