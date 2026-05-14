"""Server real-time monitor dialog."""

import os
import re
import time
from collections import deque
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
)

from src.core.ssh_connector import SSHClientWrapper
from .dialog_icons import dialog_icon
from .product_page import decrypt_password


class MonitorChart(QWidget):
    """Small line chart for 0-100 percentage metrics."""

    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.color = QColor(color)
        self.values = deque(maxlen=40)
        self.setMinimumHeight(150)

    def add_value(self, value: float):
        self.values.append(max(0.0, min(100.0, float(value))))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(12, 12, -12, -12)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect, 8, 8)

        painter.setPen(QPen(QColor("#e5e7eb"), 1))
        chart = rect.adjusted(12, 32, -12, -28)
        painter.drawLine(chart.left(), chart.top(), chart.left(), chart.bottom())
        painter.drawLine(chart.left(), chart.bottom(), chart.right(), chart.bottom())
        for i in range(1, 4):
            y = chart.bottom() - chart.height() * i / 4
            painter.drawLine(chart.left(), int(y), chart.right(), int(y))

        painter.setPen(QColor("#111827"))
        painter.drawText(rect.adjusted(12, 8, -12, -8), Qt.AlignmentFlag.AlignTop, self.title)

        if not self.values:
            painter.setPen(QColor("#9ca3af"))
            painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, "等待数据...")
            return

        latest = self.values[-1]
        painter.setPen(QColor("#374151"))
        painter.drawText(rect.adjusted(12, 8, -12, -8), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, f"{latest:.1f}%")

        max_value = max(self.values) if self.values else 0.0
        scale_max = max(10.0, min(100.0, max_value * 1.35))
        if scale_max <= 20:
            scale_text = "范围 0-10%" if scale_max <= 10 else "范围 0-20%"
            scale_max = 10.0 if scale_max <= 10 else 20.0
        elif scale_max <= 50:
            scale_text = "范围 0-50%"
            scale_max = 50.0
        else:
            scale_text = "范围 0-100%"
            scale_max = 100.0

        painter.setPen(QColor("#9ca3af"))
        painter.drawText(rect.adjusted(12, 8, -12, -8), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, scale_text)

        step = chart.width() / max(1, len(self.values) - 1)
        points = []
        for idx, val in enumerate(self.values):
            x = chart.left() + idx * step
            y = chart.bottom() - (min(val, scale_max) / scale_max) * chart.height()
            points.append((x, y))

        painter.setPen(QPen(self.color, 2))
        if len(points) < 2:
            painter.setBrush(self.color)
            painter.drawEllipse(int(points[0][0]) - 3, int(points[0][1]) - 3, 6, 6)
        else:
            for left, right in zip(points, points[1:]):
                painter.drawLine(int(left[0]), int(left[1]), int(right[0]), int(right[1]))
            painter.setBrush(self.color)
            painter.drawEllipse(int(points[-1][0]) - 3, int(points[-1][1]) - 3, 6, 6)


class MonitorPieChart(QWidget):
    """Pie chart for a single 0-100 percentage metric."""

    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.color = QColor(color)
        self.value = None
        self.setMinimumHeight(190)

    def add_value(self, value: float):
        self.value = max(0.0, min(100.0, float(value)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(12, 12, -12, -12)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect, 8, 8)

        painter.setPen(QColor("#111827"))
        painter.drawText(rect.adjusted(12, 8, -12, -8), Qt.AlignmentFlag.AlignTop, self.title)

        pie_size = min(rect.width() - 64, rect.height() - 58)
        pie_rect = rect.adjusted(0, 0, 0, 0)
        pie_rect.setWidth(pie_size)
        pie_rect.setHeight(pie_size)
        pie_rect.moveCenter(rect.center())
        pie_rect.moveTop(rect.top() + 42)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e5e7eb"))
        painter.drawPie(pie_rect, 0, 360 * 16)

        if self.value is None:
            painter.setPen(QColor("#9ca3af"))
            painter.drawText(pie_rect, Qt.AlignmentFlag.AlignCenter, "等待数据...")
            return

        painter.setBrush(self.color)
        painter.drawPie(pie_rect, 90 * 16, int(-self.value / 100.0 * 360 * 16))

        inner = pie_rect.adjusted(22, 22, -22, -22)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(inner)

        painter.setPen(QColor("#111827"))
        font = painter.font()
        font.setPointSize(18)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, f"{self.value:.1f}%")


class ServerMonitorWorker(QThread):
    data_ready = pyqtSignal(float, float, float, str)
    status_changed = pyqtSignal(str)

    def __init__(self, server: dict, interval_seconds: int = 3):
        super().__init__()
        self.server = server
        self.interval_seconds = interval_seconds
        self._running = True
        self.ssh = None

    def run(self):
        password = decrypt_password(self.server.get("encrypted_password", ""))
        if not password:
            self.status_changed.emit("无法读取服务器密码，请重新编辑服务器配置")
            return

        self.ssh = SSHClientWrapper()
        ok, message = self.ssh.connect(
            host=self.server.get("host", ""),
            port=int(self.server.get("port", 22)),
            username=self.server.get("username", ""),
            password=password,
        )
        if not ok:
            self.status_changed.emit(f"连接失败：{message}")
            return

        self.status_changed.emit("监控中")
        command = (
            "cpu=$(top -bn1 | awk -F'[, ]+' '/Cpu\\(s\\)|%Cpu/ {for(i=1;i<=NF;i++) if($i==\"id\") print 100-$(i-1); exit}'); "
            "mem=$(free | awk '/Mem:/ {if($2>0) printf \"%.1f\", $3*100/$2; else print 0}'); "
            "disk=$(df -P / | awk 'NR==2 {gsub(\"%\",\"\",$5); print $5}'); "
            "echo CPU=${cpu:-0}; echo MEM=${mem:-0}; echo DISK=${disk:-0}"
        )

        while self._running:
            try:
                code, output, error = self.ssh.execute(command)
                if code == 0:
                    cpu = self._read_metric(output, "CPU")
                    mem = self._read_metric(output, "MEM")
                    disk = self._read_metric(output, "DISK")
                    self.data_ready.emit(cpu, mem, disk, time.strftime("%H:%M:%S"))
                else:
                    self.status_changed.emit(f"采集失败：{error or output}")
            except Exception as exc:
                self.status_changed.emit(f"采集异常：{exc}")
            for _ in range(self.interval_seconds * 10):
                if not self._running:
                    break
                self.msleep(100)

        try:
            self.ssh.disconnect()
        except Exception:
            pass

    def stop(self):
        self._running = False
        try:
            if self.ssh and hasattr(self.ssh, "request_stop"):
                self.ssh.request_stop()
        except Exception:
            pass

    @staticmethod
    def _read_metric(output: str, name: str) -> float:
        match = re.search(rf"{name}=([0-9.]+)", output or "")
        if not match:
            return 0.0
        try:
            return float(match.group(1))
        except Exception:
            return 0.0


class ServerMonitorDialog(QDialog):
    def __init__(self, server: dict, parent=None):
        super().__init__(parent)
        self.server = server
        self.worker = None
        self.setWindowTitle(f"实时监控 - {server.get('name') or server.get('host')}")
        self.setWindowIcon(dialog_icon("product.svg"))
        self.resize(760, 560)
        self.init_ui()
        self.start_monitor()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(self.server.get("name") or self.server.get("host") or "服务器监控")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #111827;")
        layout.addWidget(title)

        detail = QLabel(f"{self.server.get('host', '')}:{self.server.get('port', 22)} | {self.server.get('username', '')}")
        detail.setStyleSheet("color: #6b7280; font-size: 13px;")
        layout.addWidget(detail)

        self.status_label = QLabel("正在连接...")
        self.status_label.setStyleSheet("color: #0f766e; background: #f0fdfa; padding: 8px; border-radius: 6px;")
        layout.addWidget(self.status_label)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.cpu_chart = MonitorChart("CPU 使用率", "#50C9C3")
        self.mem_chart = MonitorPieChart("内存使用率", "#2563eb")
        self.disk_chart = MonitorPieChart("根分区磁盘使用率", "#f59e0b")
        grid.addWidget(self.cpu_chart, 0, 0)
        grid.addWidget(self.mem_chart, 0, 1)
        grid.addWidget(self.disk_chart, 1, 0, 1, 2)
        layout.addLayout(grid, 1)

        bottom = QHBoxLayout()
        self.last_update_label = QLabel("最后更新：--")
        self.last_update_label.setStyleSheet("color: #6b7280;")
        bottom.addWidget(self.last_update_label)
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedHeight(34)
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        self.setStyleSheet("""
            QDialog { background: #f8fafc; }
            QPushButton {
                background: white;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 0 18px;
                color: #374151;
            }
            QPushButton:hover { border-color: #50C9C3; color: #0f766e; }
        """)

    def start_monitor(self):
        self.worker = ServerMonitorWorker(self.server)
        self.worker.data_ready.connect(self.on_data_ready)
        self.worker.status_changed.connect(self.status_label.setText)
        self.worker.start()

    def on_data_ready(self, cpu: float, mem: float, disk: float, ts: str):
        self.cpu_chart.add_value(cpu)
        self.mem_chart.add_value(mem)
        self.disk_chart.add_value(disk)
        self.last_update_label.setText(f"最后更新：{ts}")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1500)
        super().closeEvent(event)
