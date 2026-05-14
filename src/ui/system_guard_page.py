"""System guard page for scheduled inspection management."""

import json
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget, QDialog
from qfluentwidgets import PrimaryPushButton

from utils import get_config_dir
from .product_page import ScheduleInspectionDialog, normalize_server_record


class SystemGuardPage(QWidget):
    """定时巡检页面：管理自动巡检计划与运行日志。"""

    schedules_updated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("system_guard_page")
        self.init_ui()
        self.refresh_summary()
        self.refresh_log()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 20, 32, 24)
        layout.setSpacing(12)

        header = QFrame()
        header.setObjectName("headerCard")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 12, 20, 12)
        header_layout.setSpacing(16)

        title_box = QVBoxLayout()
        title_box.setSpacing(6)
        title = QLabel("定时巡检")
        title.setObjectName("pageTitle")
        title_box.addWidget(title)
        header_layout.addLayout(title_box, 1)

        self.configure_btn = PrimaryPushButton("设置定时巡检")
        self.configure_btn.setFixedSize(130, 38)
        self.configure_btn.clicked.connect(self.open_schedule_dialog)
        header_layout.addWidget(self.configure_btn)

        self.refresh_btn = QPushButton("刷新日志")
        self.refresh_btn.setObjectName("secondaryBtn")
        self.refresh_btn.setFixedSize(100, 38)
        self.refresh_btn.clicked.connect(self.refresh_all)
        header_layout.addWidget(self.refresh_btn)
        layout.addWidget(header)

        summary = QFrame()
        summary.setObjectName("summaryCard")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(18, 12, 18, 12)
        summary_layout.setSpacing(18)
        self.server_count_label = QLabel("服务器：0")
        self.server_count_label.setObjectName("summaryItem")
        self.enabled_count_label = QLabel("已启用计划：0")
        self.enabled_count_label.setObjectName("summaryItem")
        self.report_dir_label = QLabel("报告目录：未设置")
        self.report_dir_label.setObjectName("summaryItem")
        summary_layout.addWidget(self.server_count_label)
        summary_layout.addWidget(self.enabled_count_label)
        summary_layout.addWidget(self.report_dir_label, 1)
        layout.addWidget(summary)

        log_card = QFrame()
        log_card.setObjectName("logCard")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(18, 14, 18, 16)
        log_layout.setSpacing(10)
        log_title = QLabel("定时巡检日志")
        log_title.setObjectName("sectionTitle")
        log_layout.addWidget(log_title)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("logText")
        log_layout.addWidget(self.log_text, 1)
        layout.addWidget(log_card, 1)

        self.setStyleSheet("""
            QWidget#system_guard_page { background: #f8fafc; }
            QFrame#headerCard, QFrame#summaryCard, QFrame#logCard {
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
            }
            QLabel#pageTitle {
                color: #0f766e;
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#pageSubtitle {
                color: #64748b;
                font-size: 13px;
            }
            QLabel#sectionTitle {
                color: #0f172a;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#summaryItem {
                color: #334155;
                font-size: 13px;
                font-weight: 600;
            }
            QTextEdit#logText {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                color: #111827;
                padding: 10px;
                font-family: Consolas, "Microsoft YaHei";
                font-size: 12px;
            }
            QPushButton#secondaryBtn {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                color: #334155;
                font-weight: 600;
            }
            QPushButton#secondaryBtn:hover {
                background: #f0fdfa;
                border-color: #50C9C3;
                color: #0f766e;
            }
        """)

    def servers_path(self) -> Path:
        return Path(get_config_dir()) / "servers.json"

    def schedules_path(self) -> Path:
        return Path(get_config_dir()) / "schedules.json"

    def log_path(self) -> Path:
        return Path(get_config_dir()) / "scheduled_inspection.log"

    def load_servers(self) -> list:
        path = self.servers_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [normalize_server_record(server) for server in data.get("servers", [])]
        except Exception:
            return []

    def load_schedules(self) -> list:
        path = self.schedules_path()
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("schedules", [])
        except Exception:
            return []

    def refresh_summary(self):
        servers = self.load_servers()
        schedules = self.load_schedules()
        enabled = [s for s in schedules if s.get("enabled")]
        report_dir = next((s.get("report_dir") for s in enabled if s.get("report_dir")), "")
        self.server_count_label.setText(f"服务器：{len(servers)}")
        self.enabled_count_label.setText(f"已启用计划：{len(enabled)}")
        self.report_dir_label.setText(f"报告目录：{report_dir or '未设置'}")

    def refresh_log(self):
        path = self.log_path()
        if not path.exists():
            self.log_text.setPlainText("暂无定时巡检日志")
            return
        try:
            self.log_text.setPlainText(path.read_text(encoding="utf-8"))
            self.log_text.moveCursor(QTextCursor.MoveOperation.End)
        except Exception as exc:
            self.log_text.setPlainText(f"读取日志失败：{exc}")

    def refresh_all(self):
        self.refresh_summary()
        self.refresh_log()

    def showEvent(self, event):
        self.refresh_all()
        super().showEvent(event)

    def open_schedule_dialog(self):
        dialog = ScheduleInspectionDialog(self.load_servers(), self)
        dialog.setWindowTitle("定时巡检设置")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.schedules_updated.emit()
            self.refresh_all()
