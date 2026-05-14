"""
日志收集页面
"""

import json
import html
import os
import tarfile
import time
from ftplib import FTP
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QListWidget, QListWidgetItem, QTextEdit, QMessageBox, QInputDialog,
    QLineEdit, QFileDialog, QSplitter, QDialog, QFormLayout, QSpinBox,
    QDialogButtonBox, QTabWidget, QScrollArea, QAbstractItemView
)
from qfluentwidgets import PrimaryPushButton

from utils import get_config_dir as _get_config_dir


def get_config_dir() -> Path:
    return Path(_get_config_dir())


def _shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


class ClickableTitle(QLabel):
    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class OpsModeButton(QPushButton):
    def enterEvent(self, event):
        self.setText("退出运维模式")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setText("运维模式")
        super().leaveEvent(event)


class LogExtractWorker(QThread):
    finished = pyqtSignal(bool, str, str)

    def __init__(self, ssh_client, product_name: str, rules: list, output_dir: Path, ftp_config: dict | None = None):
        super().__init__()
        self.ssh_client = ssh_client
        self.product_name = product_name or "通用"
        self.rules = rules
        self.output_dir = output_dir
        self.ftp_config = ftp_config or {}

    def run(self):
        try:
            if not self.ssh_client:
                self.finished.emit(False, "未连接服务器，请先连接服务器", "")
                return
            if not self.rules:
                self.finished.emit(False, "未配置该产品的日志收集规则", "")
                return

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_product = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in self.product_name)
            remote_base = f"/tmp/aiops_logs_{safe_product}_{timestamp}"
            remote_list = f"{remote_base}.lst"
            remote_archive = f"{remote_base}.tar.gz"

            checks = []
            commands = [f": > {_shell_quote(remote_list)}"]
            for idx, rule in enumerate(self.rules, 1):
                path = (rule.get("path") or "").strip()
                name = (rule.get("name") or "").strip()
                if not path or not name:
                    continue
                checks.append(
                    f"if [ ! -d {_shell_quote(path)} ]; then echo '__MISSING_PATH__|{idx}|{path}'; exit 11; fi"
                )
                checks.append(
                    f"if ! find {_shell_quote(path)} -maxdepth 1 -type f -name {_shell_quote(name)} | grep -q .; "
                    f"then echo '__MISSING_FILE__|{idx}|{path}|{name}'; exit 12; fi"
                )
                commands.append(
                    f"find {_shell_quote(path)} -maxdepth 1 -type f -name {_shell_quote(name)} "
                    f"-print >> {_shell_quote(remote_list)} 2>/dev/null"
                )
            commands.append(f"tar -czf {_shell_quote(remote_archive)} -T {_shell_quote(remote_list)}")
            commands.append(f"echo {_shell_quote(remote_archive)}")
            command = " && ".join(checks + commands)

            code, output, error = self.ssh_client.execute(command, timeout=300)
            if code != 0:
                detail = (output or error or "").strip()
                if "__MISSING_PATH__" in detail:
                    parts = detail.split("|")
                    detail = f"日志路径不存在：{parts[-1]}"
                elif "__MISSING_FILE__" in detail:
                    parts = detail.split("|")
                    if len(parts) >= 4:
                        detail = f"路径 {parts[2]} 下未找到日志文件：{parts[3]}"
                self.finished.emit(False, detail or "服务器打包日志失败", "")
                return

            self.output_dir.mkdir(parents=True, exist_ok=True)
            local_path = self.output_dir / f"{safe_product}_日志_{timestamp}.tar.gz"
            if self.ftp_config:
                ok, message = self._download_by_ftp(remote_archive, local_path)
            else:
                ok, message = self.ssh_client.download_file(remote_archive, str(local_path))
            self.ssh_client.execute(f"rm -f {_shell_quote(remote_archive)} {_shell_quote(remote_list)}", timeout=30)
            if not ok:
                self.finished.emit(False, message, "")
                return
            self.finished.emit(True, f"日志收集完成：{local_path}", str(local_path))
        except Exception as exc:
            self.finished.emit(False, f"日志收集异常：{exc}", "")

    def _download_by_ftp(self, remote_path: str, local_path: Path) -> tuple[bool, str]:
        try:
            host = self.ftp_config.get("host") or getattr(self.ssh_client, "_host", "")
            port = int(self.ftp_config.get("port", 21) or 21)
            username = self.ftp_config.get("username", "")
            password = self.ftp_config.get("password", "")
            remote_root = (self.ftp_config.get("remote_root") or "/").rstrip("/")
            ftp_path = remote_path
            if remote_root and remote_root != "/" and ftp_path.startswith(remote_root):
                ftp_path = ftp_path[len(remote_root):] or Path(remote_path).name
            with FTP() as ftp:
                ftp.connect(host, port, timeout=60)
                ftp.login(username, password)
                with open(local_path, "wb") as f:
                    ftp.retrbinary(f"RETR {ftp_path}", f.write)
            return True, f"FTP下载成功：{local_path}"
        except Exception as exc:
            return False, f"FTP下载失败：{exc}"


class LogExtractPage(QWidget):
    """日志收集页面。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("log_extract_page")
        self.ssh_client = None
        self.product_name = "通用"
        self.ops_mode = False
        self._title_clicks = []
        self.worker = None
        self.ftp_config = {}
        self.init_ui()
        self.refresh_rule_cards()
        self.refresh_log_list()

    def config_path(self) -> Path:
        return get_config_dir() / "log_extract_rules.json"

    def output_dir(self) -> Path:
        return get_config_dir() / "extracted_logs"

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        self.title_label = ClickableTitle("日志收集")
        self.title_label.setObjectName("pageTitle")
        self.title_label.clicked.connect(self._on_title_clicked)
        title_box.addWidget(self.title_label)
        self.product_label = QLabel("当前产品：通用")
        self.product_label.setObjectName("pageSubtitle")
        title_box.addWidget(self.product_label)
        header.addLayout(title_box)
        header.addStretch()
        self.mode_label = OpsModeButton("运维模式")
        self.mode_label.setObjectName("modeBadge")
        self.mode_label.setFixedSize(118, 30)
        self.mode_label.setToolTip("退出运维模式")
        self.mode_label.clicked.connect(lambda: self.set_ops_mode(False))
        self.mode_label.hide()
        header.addWidget(self.mode_label)
        layout.addLayout(header)

        action_row = QHBoxLayout()
        self.extract_btn = PrimaryPushButton("一键提取日志")
        self.extract_btn.setFixedSize(140, 40)
        self.extract_btn.clicked.connect(self.extract_logs)
        action_row.addWidget(self.extract_btn)
        self.open_dir_btn = QPushButton("打开目录")
        self.open_dir_btn.setObjectName("secondaryBtn")
        self.open_dir_btn.setFixedSize(100, 40)
        self.open_dir_btn.clicked.connect(self.choose_or_open_dir)
        action_row.addWidget(self.open_dir_btn)
        self.ftp_btn = QPushButton("添加FTP账号")
        self.ftp_btn.setObjectName("secondaryBtn")
        self.ftp_btn.setFixedSize(120, 40)
        self.ftp_btn.clicked.connect(self.configure_ftp_account)
        self.ftp_btn.hide()
        action_row.addWidget(self.ftp_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.tabs = QTabWidget()
        self.extract_tab = QWidget()
        extract_layout = QVBoxLayout(self.extract_tab)
        extract_layout.setContentsMargins(0, 0, 0, 0)
        extract_layout.setSpacing(12)

        self.ops_panel = QFrame()
        self.ops_panel.setObjectName("opsPanel")
        ops_layout = QVBoxLayout(self.ops_panel)
        ops_layout.setContentsMargins(16, 14, 16, 14)
        ops_layout.setSpacing(12)
        ops_header = QHBoxLayout()
        ops_title = QLabel("日志收集规则")
        ops_title.setObjectName("sectionTitle")
        ops_header.addWidget(ops_title)
        ops_header.addStretch()
        add_btn = PrimaryPushButton("添加规则")
        add_btn.setFixedSize(100, 34)
        add_btn.clicked.connect(self.add_rule)
        ops_header.addWidget(add_btn)
        ops_layout.addLayout(ops_header)

        self.rule_scroll = QScrollArea()
        self.rule_scroll.setWidgetResizable(True)
        self.rule_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rule_container = QWidget()
        self.rule_layout = QVBoxLayout(self.rule_container)
        self.rule_layout.setContentsMargins(0, 0, 0, 0)
        self.rule_layout.setSpacing(8)
        self.rule_scroll.setWidget(self.rule_container)
        self.rule_scroll.setMinimumHeight(360)
        ops_layout.addWidget(self.rule_scroll, 1)
        self.ops_panel.hide()
        extract_layout.addWidget(self.ops_panel, 1)

        self.logs_tab = QWidget()
        logs_layout = QVBoxLayout(self.logs_tab)
        logs_layout.setContentsMargins(0, 0, 0, 0)
        logs_layout.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.log_list = QListWidget()
        self.log_list.setObjectName("logList")
        self.log_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.log_list.itemSelectionChanged.connect(self.preview_selected_log)
        splitter.addWidget(self.log_list)
        right_panel = QFrame()
        right_panel.setObjectName("previewPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(12)
        self.preview_text = QTextEdit()
        self.preview_text.setObjectName("logPreview")
        self.preview_text.setReadOnly(True)
        right_layout.addWidget(self.preview_text, 1)
        btn_row = QHBoxLayout()
        delete_btn = QPushButton("删除选中")
        delete_btn.setObjectName("dangerBtn")
        delete_btn.clicked.connect(self.delete_selected_log)
        btn_row.addWidget(delete_btn)
        btn_row.addStretch()
        right_layout.addLayout(btn_row)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        logs_layout.addWidget(splitter, 1)

        self.tabs.addTab(self.extract_tab, "日志收集")
        self.tabs.addTab(self.logs_tab, "日志列表与预览")
        layout.addWidget(self.tabs, 1)
        self.tabs.setTabVisible(0, False)
        self.tabs.setCurrentWidget(self.logs_tab)

        self.setStyleSheet("""
            QWidget#log_extract_page { background: #f8fafc; }
            QLabel#pageTitle { font-size: 24px; font-weight: 700; color: #0f172a; }
            QLabel#pageSubtitle { font-size: 13px; color: #64748b; }
            QPushButton#modeBadge {
                background: #ffffff; color: #334155; border: 1px solid #cbd5e1;
                border-radius: 8px; padding: 4px 10px; font-weight: 600;
            }
            QPushButton#modeBadge:hover {
                background: #f0fdfa; border-color: #50C9C3; color: #0f766e;
            }
            QFrame#opsPanel, QFrame#ruleCard {
                background: #ffffff; border: 1px solid #dbe3ee; border-radius: 8px;
            }
            QLabel#sectionTitle { font-size: 15px; font-weight: 700; color: #0f172a; }
            QLabel#hintBox {
                color: #0f766e; background: #f0fdfa; border: 1px solid #ccfbf1;
                padding: 12px; border-radius: 6px;
            }
            QLabel#ruleTitle { font-size: 14px; font-weight: 700; color: #0f172a; }
            QLabel#ruleMeta { font-size: 12px; color: #64748b; }
            QListWidget, QTextEdit, QLineEdit {
                background: #ffffff; border: 1px solid #d1d5db; border-radius: 6px;
                color: #111827; padding: 6px;
            }
            QListWidget#logList {
                padding: 8px; outline: none;
            }
            QListWidget#logList::item {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
                padding: 10px 12px;
                margin: 4px 2px;
                color: #0f172a;
            }
            QListWidget#logList::item:hover {
                background: #f0fdfa;
                border-color: #99f6e4;
            }
            QListWidget#logList::item:selected {
                background: #50C9C3;
                border-color: #50C9C3;
                color: #ffffff;
            }
            QFrame#previewPanel {
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
            }
            QTextEdit#logPreview {
                border: none;
                padding: 0;
                background: transparent;
                color: #111827;
            }
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: #ffffff; color: #475569; border: 1px solid #dbe3ee;
                padding: 8px 18px; margin-right: 4px; border-top-left-radius: 6px; border-top-right-radius: 6px;
            }
            QTabBar::tab:selected { background: #50C9C3; color: #ffffff; border-color: #50C9C3; }
            QPushButton#secondaryBtn {
                background: #ffffff; border: 1px solid #d1d5db; border-radius: 6px;
                color: #374151; font-weight: 600;
            }
            QPushButton#secondaryBtn:hover {
                background: #f0fdfa; border-color: #50C9C3; color: #0f766e;
            }
            QPushButton#dangerBtn {
                background: #fff7ed; border: 1px solid #fed7aa; border-radius: 6px;
                color: #c2410c; font-weight: 600; padding: 8px 18px;
            }
            QPushButton#dangerBtn:hover { background: #ffedd5; border-color: #fb923c; }
        """)

    def set_ssh_client(self, ssh_client):
        self.ssh_client = ssh_client
        is_otp = bool(
            ssh_client and (
                getattr(ssh_client, "_use_otp_connection", False)
                or getattr(ssh_client, "_use_shell_execution", False)
            )
        )
        self.ftp_btn.setVisible(is_otp)

    def set_product(self, product_name: str):
        if product_name != self.product_name:
            self.set_ops_mode(False)
        self.product_name = product_name or "通用"
        self.product_label.setText(f"当前产品：{self.product_name}")
        self.refresh_rule_cards()
        self.refresh_log_list()

    def set_ops_mode(self, enabled: bool):
        self.ops_mode = enabled
        self.ops_panel.setVisible(enabled)
        self.mode_label.setVisible(enabled)
        self.tabs.setTabVisible(0, enabled)
        if enabled:
            self.tabs.setCurrentWidget(self.extract_tab)
        elif self.tabs.currentWidget() == self.extract_tab:
            self.tabs.setCurrentWidget(self.logs_tab)

    def _on_title_clicked(self):
        now = time.time()
        self._title_clicks = [t for t in self._title_clicks if now - t < 2]
        self._title_clicks.append(now)
        if len(self._title_clicks) >= 3:
            self._title_clicks.clear()
            password, ok = QInputDialog.getText(self, "进入运维模式", "请输入运维密码:", QLineEdit.EchoMode.Password)
            if ok and password in {"26515", "我爱畅哥", "520wc"}:
                self.set_ops_mode(True)
            elif ok:
                QMessageBox.warning(self, "密码错误", "运维密码不正确")

    def read_config(self) -> dict:
        path = self.config_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def write_config(self, data: dict):
        path = self.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def current_rules(self) -> list:
        data = self.read_config()
        return data.get(self.product_name) or data.get("通用") or []

    def save_current_rules(self, rules: list):
        data = self.read_config()
        data[self.product_name] = rules
        self.write_config(data)
        self.refresh_rule_cards()

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def refresh_rule_cards(self):
        if not hasattr(self, "rule_layout"):
            return
        self.clear_layout(self.rule_layout)
        rules = self.current_rules()
        if not rules:
            empty = QLabel("当前产品未配置日志收集规则")
            empty.setObjectName("ruleMeta")
            self.rule_layout.addWidget(empty)
            self.rule_layout.addStretch()
            return
        for idx, rule in enumerate(rules):
            card = QFrame()
            card.setObjectName("ruleCard")
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            text_box = QVBoxLayout()
            title = QLabel(rule.get("name", "日志文件"))
            title.setObjectName("ruleTitle")
            text_box.addWidget(title)
            meta = QLabel(rule.get("path", "未配置路径"))
            meta.setObjectName("ruleMeta")
            text_box.addWidget(meta)
            row.addLayout(text_box, 1)
            edit_btn = QPushButton("编辑")
            edit_btn.setObjectName("secondaryBtn")
            edit_btn.setFixedSize(64, 32)
            edit_btn.clicked.connect(lambda _=False, i=idx: self.edit_rule(i))
            row.addWidget(edit_btn)
            del_btn = QPushButton("删除")
            del_btn.setObjectName("dangerBtn")
            del_btn.setFixedSize(64, 32)
            del_btn.clicked.connect(lambda _=False, i=idx: self.delete_rule(i))
            row.addWidget(del_btn)
            self.rule_layout.addWidget(card)
        self.rule_layout.addStretch()

    def validate_rule(self, path: str, name: str) -> str:
        if not path or not name:
            return "请填写日志路径和日志文件名"
        if not path.startswith("/"):
            return "日志路径必须填写服务器上的绝对路径，例如 /var/log"
        blocked_path_chars = set("\r\n;&|<>`$")
        blocked_name_chars = set("\r\n/\\;&|<>`$")
        if any(ch in blocked_path_chars for ch in path):
            return "日志路径不能包含换行或命令拼接字符"
        if any(ch in blocked_name_chars for ch in name):
            return "日志文件名不能包含路径分隔符或命令拼接字符"
        if name.strip("*?.[]") == "":
            return "日志文件名不能只填写通配符"
        return ""

    def rule_dialog(self, rule: dict | None = None) -> dict | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("日志收集规则")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        path_input = QLineEdit()
        path_input.setText((rule or {}).get("path", ""))
        path_input.setPlaceholderText("/var/log 或 /opt/app/logs")
        name_input = QLineEdit()
        name_input.setText((rule or {}).get("name", ""))
        name_input.setPlaceholderText("*.log 或 app.log")
        form.addRow("日志路径:", path_input)
        form.addRow("日志文件名:", name_input)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            path = path_input.text().strip()
            name = name_input.text().strip()
            error = self.validate_rule(path, name)
            if not error:
                return {"path": path, "name": name}
            QMessageBox.warning(dialog, "规则校验失败", error)

    def add_rule(self):
        rule = self.rule_dialog()
        if not rule:
            return
        rules = self.current_rules()
        rules.append(rule)
        self.save_current_rules(rules)

    def edit_rule(self, index: int):
        rules = self.current_rules()
        if not 0 <= index < len(rules):
            return
        rule = self.rule_dialog(rules[index])
        if not rule:
            return
        rules[index] = rule
        self.save_current_rules(rules)

    def delete_rule(self, index: int):
        rules = self.current_rules()
        if not 0 <= index < len(rules):
            return
        if QMessageBox.question(self, "删除规则", "确定删除该日志收集规则吗？") != QMessageBox.StandardButton.Yes:
            return
        del rules[index]
        self.save_current_rules(rules)

    def extract_logs(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "提示", "日志正在提取中，请稍候")
            return
        rules = self.current_rules()
        ftp_config = self.ftp_config if self.ftp_btn.isVisible() and self.ftp_config else {}
        if self.ftp_btn.isVisible() and not ftp_config:
            QMessageBox.warning(self, "缺少FTP账号", "当前为OTP服务器，请先添加FTP账号用于下载日志压缩包")
            return
        self.extract_btn.setEnabled(False)
        self.extract_btn.setText("提取中...")
        self.worker = LogExtractWorker(self.ssh_client, self.product_name, rules, self.output_dir(), ftp_config)
        self.worker.finished.connect(self._on_extract_finished)
        self.worker.start()

    def _on_extract_finished(self, success: bool, message: str, path: str):
        self.extract_btn.setEnabled(True)
        self.extract_btn.setText("一键提取日志")
        self.refresh_log_list()
        if success:
            QMessageBox.information(self, "提取完成", message)
            self.tabs.setCurrentWidget(self.logs_tab)
        else:
            QMessageBox.warning(self, "提取失败", message)

    def refresh_log_list(self):
        self.log_list.clear()
        self.output_dir().mkdir(parents=True, exist_ok=True)
        for path in sorted(self.output_dir().glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setSizeHint(QSize(0, 50))
            self.log_list.addItem(item)
        if self.log_list.count() == 0:
            self.preview_text.setHtml(self.preview_empty_html("暂无已提取日志"))

    def selected_log_path(self) -> Path | None:
        item = self.log_list.currentItem()
        if not item:
            return None
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def preview_selected_log(self):
        path = self.selected_log_path()
        if not path or not path.exists():
            self.preview_text.setHtml(self.preview_empty_html("请选择一个日志压缩包"))
            return
        members_html = ""
        preview_html = ""
        try:
            with tarfile.open(path, "r:gz") as archive:
                members = [m for m in archive.getmembers() if m.isfile()]
                members_html = "".join(
                    f"<li><span>{html.escape(member.name)}</span><em>{member.size // 1024} KB</em></li>"
                    for member in members[:80]
                )
                first_text = next((m for m in members if m.size <= 1024 * 1024), None)
                if first_text:
                    file_obj = archive.extractfile(first_text)
                    if file_obj:
                        text = file_obj.read(12000).decode("utf-8", errors="replace")
                        preview_html = (
                            f"<h3>预览：{html.escape(first_text.name)}</h3>"
                            f"<pre>{html.escape(text)}</pre>"
                        )
        except Exception as exc:
            preview_html = f"<div class='error'>预览失败：{html.escape(str(exc))}</div>"
        if not members_html:
            members_html = "<li><span>压缩包内暂无可预览文件</span><em></em></li>"
        self.preview_text.setHtml(self.build_preview_html(path, members_html, preview_html))

    def preview_empty_html(self, message: str) -> str:
        return f"""
        <div style="height: 100%; color: #64748b; font-size: 14px; padding: 18px;">
            {html.escape(message)}
        </div>
        """

    def build_preview_html(self, path: Path, members_html: str, preview_html: str) -> str:
        return f"""
        <style>
            body {{ font-family: "Microsoft YaHei", Arial, sans-serif; color: #0f172a; }}
            h2 {{ margin: 0 0 8px 0; font-size: 18px; }}
            h3 {{ margin: 18px 0 8px 0; font-size: 14px; color: #0f766e; }}
            .meta {{ color: #64748b; font-size: 12px; margin-bottom: 14px; }}
            ul {{ list-style: none; padding: 0; margin: 0; }}
            li {{
                display: flex; justify-content: space-between; gap: 12px;
                padding: 8px 10px; margin-bottom: 6px;
                border: 1px solid #e2e8f0; border-radius: 6px; background: #f8fafc;
            }}
            li span {{ color: #111827; }}
            li em {{ color: #64748b; font-style: normal; white-space: nowrap; }}
            pre {{
                white-space: pre-wrap; word-break: break-word; margin: 0;
                padding: 12px; border: 1px solid #e2e8f0; border-radius: 6px;
                background: #f8fafc; color: #111827; font-family: Consolas, monospace;
                font-size: 12px; line-height: 1.5;
            }}
            .error {{
                padding: 12px; border-radius: 6px; background: #fff7ed;
                border: 1px solid #fed7aa; color: #c2410c;
            }}
        </style>
        <h2>{html.escape(path.name)}</h2>
        <div class="meta">大小：{path.stat().st_size // 1024} KB</div>
        <h3>压缩包内容</h3>
        <ul>{members_html}</ul>
        {preview_html}
        """

    def delete_selected_log(self):
        items = self.log_list.selectedItems()
        paths = [Path(item.data(Qt.ItemDataRole.UserRole)) for item in items]
        paths = [path for path in paths if path.exists()]
        if not paths:
            return
        if len(paths) == 1:
            message = f"确定删除 {paths[0].name} 吗？"
        else:
            message = f"确定删除选中的 {len(paths)} 个日志文件吗？"
        if QMessageBox.question(self, "删除日志", message) != QMessageBox.StandardButton.Yes:
            return
        for path in paths:
            path.unlink(missing_ok=True)
        self.refresh_log_list()

    def choose_or_open_dir(self):
        self.output_dir().mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(self.output_dir()))
        except Exception:
            QFileDialog.getExistingDirectory(self, "已提取日志目录", str(self.output_dir()))

    def configure_ftp_account(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("添加FTP账号")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        host_input = QLineEdit()
        host_input.setText(self.ftp_config.get("host") or getattr(self.ssh_client, "_host", ""))
        port_spin = QSpinBox()
        port_spin.setRange(1, 65535)
        port_spin.setValue(int(self.ftp_config.get("port", 21) or 21))
        user_input = QLineEdit()
        user_input.setText(self.ftp_config.get("username", ""))
        password_input = QLineEdit()
        password_input.setEchoMode(QLineEdit.EchoMode.Password)
        password_input.setText(self.ftp_config.get("password", ""))
        root_input = QLineEdit()
        root_input.setText(self.ftp_config.get("remote_root", "/"))
        root_input.setPlaceholderText("FTP可访问的远程根目录，例如 / 或 /tmp")
        form.addRow("FTP地址:", host_input)
        form.addRow("端口:", port_spin)
        form.addRow("用户名:", user_input)
        form.addRow("密码:", password_input)
        form.addRow("远程根目录:", root_input)
        layout.addLayout(form)
        hint = QLabel("OTP服务器无法稳定使用SFTP时，可配置FTP账号下载服务器端生成的日志压缩包。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not host_input.text().strip() or not user_input.text().strip():
            QMessageBox.warning(self, "配置不完整", "请填写FTP地址和用户名")
            return
        self.ftp_config = {
            "host": host_input.text().strip(),
            "port": port_spin.value(),
            "username": user_input.text().strip(),
            "password": password_input.text(),
            "remote_root": root_input.text().strip() or "/",
        }
        QMessageBox.information(self, "保存成功", "FTP账号已添加")
