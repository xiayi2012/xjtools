"""Home workspace for server inspection and troubleshooting."""
import sys
from pathlib import Path
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QScrollArea


class HomePage(QWidget):
    start_clicked = pyqtSignal()
    ai_config_clicked = pyqtSignal()
    server_config_clicked = pyqtSignal()
    inspection_clicked = pyqtSignal()
    troubleshoot_clicked = pyqtSignal()
    system_guard_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('home_page')
        self.init_ui()

    def init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        surface = QWidget()
        surface.setObjectName('homeSurface')
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(32, 26, 32, 28)
        layout.setSpacing(16)
        title = QLabel('工作台')
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        groups = [
            ('配置与连接', [
                ('connect.svg', '设备管理', '添加服务器、管理连接，开始远程运维。', '管理设备', self.server_config_clicked, 'server'),
                ('AI.svg', '大模型配置', '配置模型服务，用于巡检分析与报告生成。', '配置模型', self.ai_config_clicked, 'model'),
            ]),
            ('巡检与排障', [
                ('troubleshoot.svg', 'AI 排障', '结合故障现象与知识库，辅助定位问题。', '进入排障', self.troubleshoot_clicked, 'troubleshoot'),
                ('xunjian2.svg', '批量巡检', '勾选多台设备，逐台检查并分别查看巡检结果。', '选择设备', self.inspection_clicked, 'inspect'),
                ('jiaoben.svg', '巡检项目', '选择默认检查项、产品脚本与自定义脚本。', '选择项目', self.inspection_clicked, 'scripts'),
                ('calendar.svg', '定时巡检', '设置执行计划，自动检查并留存巡检报告。', '设置计划', self.system_guard_clicked, 'schedule'),
            ]),
        ]
        for label, entries in groups:
            section = QLabel(label)
            section.setObjectName('sectionTitle')
            layout.addWidget(section)
            grid = QGridLayout()
            grid.setSpacing(16)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            for index, entry in enumerate(entries):
                grid.addWidget(self._action_card(*entry), index // 2, index % 2)
            layout.addLayout(grid)
        layout.addStretch(1)
        scroll.setWidget(surface)
        outer.addWidget(scroll)
        self.apply_styles()

    @staticmethod
    def _icon_pixmap(filename):
        root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
        path = root / 'icons' / filename
        screen = QGuiApplication.primaryScreen()
        scale = screen.devicePixelRatio() if screen else 1
        pixmap = QPixmap(round(26 * scale), round(26 * scale))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        if path.exists():
            svg = path.read_bytes().replace(b'#ffffff', b'#127c78').replace(b'#FFFFFF', b'#127c78')
            renderer = QSvgRenderer(svg)
            painter = QPainter(pixmap)
            renderer.render(painter, QRectF(0, 0, 26, 26))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), QColor('#127c78'))
            painter.end()
        return pixmap

    def _action_card(self, icon, title, description, button_text, signal, key):
        featured = key == 'inspect'
        card = QFrame()
        card.setObjectName('actionCard')
        card.setProperty('featured', featured)
        card.setMinimumHeight(155)
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 16)
        box.setSpacing(9)
        heading = QHBoxLayout()
        heading.setSpacing(12)
        badge = QLabel()
        badge.setObjectName('iconTile')
        badge.setFixedSize(42, 42)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(self._icon_pixmap(icon))
        heading.addWidget(badge)
        name = QLabel(title)
        name.setObjectName('cardTitle')
        heading.addWidget(name)
        heading.addStretch()
        box.addLayout(heading)
        body = QLabel(description)
        body.setObjectName('cardDescription')
        body.setWordWrap(True)
        box.addWidget(body)
        box.addStretch(1)
        row = QHBoxLayout()
        row.addStretch()
        button = QPushButton(button_text + '  →')
        button.setObjectName(key + 'Button')
        button.setProperty('homeAction', True)
        button.setProperty('featured', featured)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(32)
        button.setAccessibleName(button_text)
        button.clicked.connect(signal)
        row.addWidget(button)
        box.addLayout(row)
        return card

    def apply_styles(self):
        self.setStyleSheet('''
            QWidget#home_page, QWidget#homeSurface, QScrollArea { background: #f5f7fa; }
            QLabel { background: transparent; border: none; font-family: "Microsoft YaHei UI"; }
            QLabel#pageTitle { color: #172b36; font-size: 26px; font-weight: 700; }
            QLabel#sectionTitle { color: #52616e; font-size: 13px; font-weight: 600; }
            QFrame#actionCard { background: #ffffff; border: 1px solid #e2e8ee; border-radius: 14px; }
            QFrame#actionCard:hover { border-color: #87c5c0; }
            QFrame#actionCard[featured="true"] { background: #eff9f7; border-color: #b2dad4; }
            QLabel#iconTile { background: #e9f5f2; border-radius: 11px; }
            QLabel#cardTitle { color: #203440; font-size: 17px; font-weight: 600; }
            QLabel#cardDescription { color: #6b7885; font-size: 12px; }
            QPushButton[homeAction="true"] {
                background: transparent; color: #137c76; border: 1px solid #d0e3df;
                border-radius: 7px; padding: 0 13px; font-family: "Microsoft YaHei UI";
                font-size: 12px; font-weight: 600;
            }
            QPushButton[homeAction="true"]:hover { background: #e9f5f2; border-color: #7fbbb4; }
            QPushButton[homeAction="true"]:pressed { background: #d8eeea; }
            QPushButton[homeAction="true"]:focus { border: 2px solid #147d76; }
            QPushButton[homeAction="true"][featured="true"] {
                background: #147d76; border-color: #147d76; color: #ffffff;
            }
            QPushButton[homeAction="true"][featured="true"]:hover { background: #106c65; }
        ''')
