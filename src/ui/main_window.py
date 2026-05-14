"""
主窗口模块
基于 PyQt6 + qfluentwidgets 实现GUI 主窗口
删除连接管理页面，直接在首页连接服务器
"""

import os
import sys
import json
import re
import traceback
import time
import calendar
from datetime import datetime

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QLabel, QMessageBox, QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QThread, QTimer
from PyQt6.QtGui import QIcon, QPixmap, QFont, QColor, QAction

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition,
    PrimaryPushButton, PushButton, ToolButton,
    NavigationInterface, qrouter, MessageBox
)
from qfluentwidgets import FluentIcon as FIF

from .product_page import ProductPage
from .home_page import HomePage
from .inspection_page import InspectionPage, ExportReportWorker
from .ai_troubleshoot_page import AITroubleshootPage
from .ai_config_page import AIConfigPage
from .log_extract_page import LogExtractPage
from .system_guard_page import SystemGuardPage
from .connection_page import ConnectionWorker
from src.core.ssh_connector import SSHClientWrapper
from src.scripts.loader import get_loader

from pathlib import Path
import base64
import hashlib
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from utils import get_config_dir as _get_config_dir, get_ai_config_path, normalize_ai_configs


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


class ScheduledInspectionWorker(QThread):
    finished_with_result = pyqtSignal(bool, str, str)

    PRODUCT_NAME_TO_ID = {
        "办公智盾": "office_shield",
        "准入系统": "access_control",
        "EDR": "edr",
        "零信任": "zero_trust",
    }

    def __init__(self, server: dict, report_dir: str, log_path: Path):
        super().__init__()
        self.server = server
        self.report_dir = report_dir
        self.log_path = log_path

    def run(self):
        ssh = SSHClientWrapper()
        try:
            self._log(f"开始定时巡检: server={self.server.get('name') or self.server.get('host')}")
            password = decrypt_password(self.server.get("encrypted_password", ""))
            if not password:
                self._log("失败: 无法读取服务器密码")
                self.finished_with_result.emit(False, "无法读取服务器密码，请重新编辑服务器配置", "")
                return

            self._log(f"连接SSH: {self.server.get('host')}:{self.server.get('port', 22)} user={self.server.get('username')}")
            ok, message = ssh.connect(
                host=self.server.get("host", ""),
                port=int(self.server.get("port", 22)),
                username=self.server.get("username", ""),
                password=password,
            )
            if not ok:
                self._log(f"失败: SSH连接失败: {message}")
                self.finished_with_result.emit(False, f"SSH连接失败：{message}", "")
                return
            self._log("SSH连接成功")

            product_name = self.server.get("product") or "服务器"
            product_id = self.PRODUCT_NAME_TO_ID.get(product_name, product_name)
            scripts = self._load_default_scripts(product_id, product_name)
            if not scripts:
                self._log("失败: 未找到可执行的巡检项")
                self.finished_with_result.emit(False, "未找到可执行的巡检项", "")
                return
            self._log(f"加载巡检项: {len(scripts)} 项")

            builtin_outputs = {}
            custom_outputs = {}
            for script in scripts:
                name = script.get("Name") or script.get("name") or "未知巡检项"
                command = script.get("command") or script.get("content") or script.get("script_content") or ""
                if not command.strip():
                    self._log(f"跳过空命令: {name}")
                    continue
                self._log(f"执行巡检项: {name}")
                output_parts = []
                try:
                    for line in ssh.execute_stream(command):
                        if line:
                            output_parts.append(line)
                except Exception as exc:
                    self._log(f"巡检项异常: {name}: {exc}")
                    output_parts.append(f"\n执行异常: {exc}\n")
                if script.get("_type") == "custom":
                    custom_outputs[name] = "".join(output_parts)
                else:
                    builtin_outputs[name] = "".join(output_parts)

            html = self._build_ai_inspection_report_html(builtin_outputs, custom_outputs, product_name)
            report_path = self._write_report(html, product_name)
            self._log(f"报告导出成功: {report_path}")
            self.finished_with_result.emit(True, "定时巡检完成", report_path)
        except Exception as exc:
            self._log(f"失败: 定时巡检异常: {exc}")
            self._log(traceback.format_exc())
            self.finished_with_result.emit(False, f"定时巡检异常：{exc}", "")
        finally:
            try:
                ssh.disconnect()
            except Exception:
                pass

    def _log(self, message: str):
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception:
            pass

    def _load_default_scripts(self, product_id: str, product_name: str) -> list:
        scripts = self._load_ai_inspection_builtin_items()

        scripts_dir = get_config_dir() / "scripts"
        index_file = scripts_dir / "index.json"
        if index_file.exists():
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    for script in json.load(f):
                        script_product = script.get("product", "通用")
                        if script_product in (product_name, "", "通用"):
                            scripts.append({
                                "Name": script.get("name", "自定义脚本"),
                                "command": script.get("content", ""),
                                "category": "自定义",
                                "_type": "custom",
                            })
            except Exception:
                pass
        return scripts

    def _load_ai_inspection_builtin_items(self) -> list:
        """Load the same built-in inspection items defined by InspectionPage."""
        import ast
        import inspect
        import textwrap

        try:
            source = textwrap.dedent(inspect.getsource(InspectionPage._populate_inspect_list))
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and target.attr == "_all_items"
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                        ):
                            items = ast.literal_eval(node.value)
                            return [
                                {
                                    "Name": item.get("Name", item.get("name", "未知巡检项")),
                                    "command": item.get("command", ""),
                                    "category": item.get("category", ""),
                                    "_type": "builtin",
                                }
                                for item in items
                                if item.get("checked", True)
                            ]
        except Exception as exc:
            self._log(f"读取AI巡检页面巡检项失败，使用脚本加载器兜底: {exc}")

        loader = get_loader()
        fallback = []
        for item in loader.get_checks_as_list(""):
            fallback.append({
                "Name": item.get("name", "未知巡检项"),
                "command": item.get("command", ""),
                "category": item.get("category", ""),
                "_type": "builtin",
            })
        return fallback

    def _build_ai_inspection_report_html(self, builtin_outputs: dict, custom_outputs: dict, product_name: str) -> str:
        """Use the same HTML template as the AI inspection export page."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        inspection_items = []

        for name, output in {**builtin_outputs, **custom_outputs}.items():
            inspection_items.append({
                "name": name,
                "status": self._detect_item_status(output),
                "output": output or "",
            })

        formatted_data = self._format_inspection_data_for_ai(
            builtin_outputs, custom_outputs, product_name, timestamp
        )
        api_config = self._load_ai_config()
        if not api_config:
            raise Exception("AI配置未找到，请先在大模型配置页面配置API")
        configs = normalize_ai_configs(api_config)
        if not configs:
            raise Exception("AI配置未找到，请先在大模型配置页面配置API")
        first_api_type = configs[0].get("api_type", "openai")
        if not configs[0].get("api_key") and first_api_type not in ("local", "ollama"):
            raise Exception("AI API Key 未配置，请先在大模型配置页面配置API")

        prompt = self._get_ai_prompt(product_name)
        self._log("开始调用AI API生成定时巡检分析报告")
        ai_fragment = self._call_ai_with_retry(api_config, formatted_data, prompt)
        self._log(f"AI API调用完成，返回长度: {len(ai_fragment or '')}")

        report_builder = InspectionPage.__new__(InspectionPage)
        return report_builder._generate_ai_report_html(ai_fragment, product_name, timestamp, inspection_items)

    def _call_ai_with_retry(self, api_config: dict, formatted_data: str, prompt: str) -> str:
        attempts = [
            ("完整巡检数据", formatted_data),
            ("完整巡检数据重试", formatted_data),
            ("压缩巡检摘要", ExportReportWorker._prepare_inspection_data(formatted_data, limit=9000)),
        ]
        configs = normalize_ai_configs(api_config)
        last_error = ""
        for cfg_index, cfg in enumerate(configs, 1):
            api_type = cfg.get("api_type", "openai")
            model_name = cfg.get("model") or cfg.get("name") or f"大模型{cfg_index}"
            for index, (label, data) in enumerate(attempts, 1):
                try:
                    self._log(f"AI API调用尝试 模型{cfg_index}/{len(configs)} {model_name}, 数据尝试{index}/{len(attempts)}: {label}, 数据长度={len(data)}")
                    ai_worker = ExportReportWorker(cfg, data, prompt)
                    if api_type == "openai":
                        return ai_worker._call_openai_api()
                    if api_type == "claude":
                        return ai_worker._call_claude_api()
                    if api_type == "azure":
                        return ai_worker._call_azure_api()
                    if api_type in ("local", "ollama"):
                        return ai_worker._call_local_api()
                    return ai_worker._call_openai_api()
                except Exception as exc:
                    last_error = f"{model_name}: {exc}"
                    self._log(f"AI API调用失败: {last_error}")
                    if index < len(attempts):
                        time.sleep(2 * index)
        raise Exception(f"所有大模型调用失败: {last_error}")

    @staticmethod
    def _format_inspection_data_for_ai(builtin_outputs: dict, custom_outputs: dict, product_name: str, timestamp: str) -> str:
        lines = [
            f"产品: {product_name}",
            f"检查时间: {timestamp}",
            "",
            "=== 原始巡检输出 ===",
        ]
        for title, outputs in (("内置巡检项", builtin_outputs), ("自定义脚本", custom_outputs)):
            if not outputs:
                continue
            lines.append("")
            lines.append(f"--- {title} ---")
            for name, output in outputs.items():
                lines.append("")
                lines.append("=" * 60)
                lines.append(str(name))
                lines.append("=" * 60)
                lines.append(str(output or "").strip() or "无输出")
        return "\n".join(lines)

    @staticmethod
    def _load_ai_config() -> dict:
        config_path = Path(get_ai_config_path())
        if not config_path.exists():
            return {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            api_type_map = {
                0: "openai", 1: "claude", 2: "azure", 3: "local",
                5: "openai", 6: "openai", 7: "openai", 8: "openai", 9: "openai",
                10: "openai", 12: "openai", 13: "openai", 14: "openai"
            }
            api_type_raw = config.get("api_type", "openai")
            if isinstance(api_type_raw, int):
                config["api_type"] = api_type_map.get(api_type_raw, "openai")
            return config
        except Exception:
            return {}

    @staticmethod
    def _get_ai_prompt(product_name: str) -> str:
        return f"""你是一个专业的Linux服务器巡检分析专家。请基于以下巡检原始输出生成AI分析和安全建议。

要求：
1. 先评估整体健康状况，再列出关键异常和警告。
2. 对每个异常或警告说明影响、可能原因和建议排查方向。
3. 建议必须稳健保守，避免直接给出删除数据、格式化磁盘、强制终止关键进程、关闭安全能力等敏感操作。
4. 输出为简洁HTML片段，只包含分析内容，不要生成完整HTML文档。

当前产品：{product_name}
"""

    @staticmethod
    def _detect_item_status(output: str) -> str:
        text = str(output or "")
        status_lines = [
            line.strip()
            for line in text.splitlines()
            if re.search(r'^(状态|结论|结果|检测结果)\s*[:：]', line.strip())
        ]
        status_text = "\n".join(status_lines)
        normal_tokens = ["✅", "正常", "success", "ok"]
        error_tokens = ["🔴", "异常", "高风险", "错误:", "失败:", "故障", "critical", "error:", "failed"]
        warning_tokens = ["⚠️", "警告", "中风险", "注意", "风险", "warning:", "warn:"]

        if status_text:
            lowered = status_text.lower()
            has_normal = any(token.lower() in lowered for token in normal_tokens)
            if any(token.lower() in lowered for token in error_tokens) and not has_normal:
                return "error"
            if any(token.lower() in lowered for token in warning_tokens) and not has_normal:
                return "warning"
            return "normal"

        # 没有明确状态行时，只把命令本身执行失败视为异常，避免扫描日志内容造成误报。
        execution_error_patterns = [
            r'执行异常\s*[:：]',
            r'执行错误\s*[:：]',
            r'ssh.*失败',
            r'command not found',
            r'permission denied',
            r'no such file or directory',
        ]
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in execution_error_patterns):
            return "error"
        return "normal"

    @staticmethod
    def _build_scheduled_ai_fragment(issue_items: list, warning_items: list, normal_items: list, timestamp: str) -> str:
        def rows(items: list, empty: str) -> str:
            if not items:
                return f"<li>{empty}</li>"
            return "".join(
                f"<li><strong>{item.get('name', '未知')}</strong>：{ScheduledInspectionWorker._first_meaningful_line(item.get('output', ''))}</li>"
                for item in items[:8]
            )

        overall = "整体状态正常"
        if issue_items:
            overall = f"发现 {len(issue_items)} 个异常项，建议优先关注。"
        elif warning_items:
            overall = f"发现 {len(warning_items)} 个警告项，建议持续观察。"

        return f"""
            <h3>整体健康状况评估</h3>
            <p>{overall} 本报告由定时巡检自动生成，生成时间：{timestamp}。</p>
            <h3>关键异常</h3>
            <ul>{rows(issue_items, "未发现异常项")}</ul>
            <h3>警告项目</h3>
            <ul>{rows(warning_items, "未发现警告项")}</ul>
            <h3>安全建议</h3>
            <ul>
                <li>优先核对异常和警告项的业务影响，结合变更窗口处理。</li>
                <li>对资源使用率、磁盘空间、服务状态等项目保持持续观察。</li>
                <li>避免在未确认影响范围前执行删除、格式化、强制终止进程等高风险操作。</li>
            </ul>
        """

    @staticmethod
    def _first_meaningful_line(output: str) -> str:
        for raw_line in str(output or "").splitlines():
            line = raw_line.strip()
            if not line or set(line) <= set("=-_*# "):
                continue
            if line.startswith("[DEBUG") or line.lower().startswith(("filesystem", "user pid", "pid ")):
                continue
            return line[:160]
        return "详见巡检概要和命令执行结果"

    def _write_report(self, html: str, product_name: str) -> str:
        safe_product = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in product_name)
        report_dir = Path(self.report_dir or str(get_config_dir() / "scheduled_reports"))
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        host = self.server.get("host", "server").replace(".", "_").replace(":", "_")
        report_path = report_dir / f"{safe_product}_{host}_定时巡检报告_{timestamp}.html"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)
        return str(report_path)


class MainWindow(FluentWindow):
    """主窗口类"""
    
    # 信号定义
    connection_status_changed = pyqtSignal(bool, str)
    
    def __init__(self):
        """初始化主窗口"""
        super().__init__()
        self.current_page_index = 0
        self.is_connected = False
        self.connection_info = {}
        self.selected_product_name = ""  # 存储选择的产品名称
        self.selected_product_id = ""
        self.connection_worker = None
        self.scheduled_worker = None
        self.pending_server = None
        self.ssh_client = None
        self._force_quit = False
        self.tray_icon = None
        
        self.init_ui()
    
    def init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("AI 智能运维")
        self.setMinimumSize(1200, 800)
        
        # 创建各个页面
        self.home_page = HomePage()
        self.product_page = ProductPage()
        self.inspection_page = InspectionPage()
        self.system_guard_page = SystemGuardPage()
        self.log_extract_page = LogExtractPage()
        self.ai_troubleshoot_page = AITroubleshootPage()
        self.ai_config_page = AIConfigPage()
        
        # 设置页面对象名称
        self.home_page.setObjectName("home_page")
        self.product_page.setObjectName("product_page")
        self.inspection_page.setObjectName("inspection_page")
        self.system_guard_page.setObjectName("system_guard_page")
        self.log_extract_page.setObjectName("log_extract_page")
        self.ai_troubleshoot_page.setObjectName("ai_troubleshoot_page")
        self.ai_config_page.setObjectName("ai_config_page")
        
        # 添加子界面到导航
        self.addSubInterface(self.home_page, FIF.HOME, "首页")
        self.addSubInterface(self.product_page, FIF.IOT, "配置管理")
        self.addSubInterface(self.ai_troubleshoot_page, FIF.CHAT, "AI 排障")
        self.addSubInterface(self.inspection_page, FIF.PLAY, "AI 巡检")
        self.addSubInterface(self.system_guard_page, FIF.CALENDAR, "定时巡检")
        self.addSubInterface(self.log_extract_page, FIF.DOCUMENT, "日志收集")
        self.addSubInterface(self.ai_config_page, FIF.SETTING, "大模型配置")
        
        # 设置导航选中状态
        self.navigationInterface.setCurrentItem(self.home_page.objectName())
        
        # 连接页面切换拦截 - 防止直接通过导航栏访问受限页面
        self.stackedWidget.currentChanged.connect(self._on_page_changed)
        
        # 连接信号
        self.home_page.start_clicked.connect(lambda: self.switch_page(1, force=True))
        self.home_page.ai_config_clicked.connect(lambda: self.switch_page(6, force=True))
        self.home_page.server_config_clicked.connect(lambda: self.switch_page(1, force=True))
        self.home_page.inspection_clicked.connect(lambda: self.switch_page(3))
        self.home_page.system_guard_clicked.connect(lambda: self.switch_page(4, force=True))
        self.home_page.troubleshoot_clicked.connect(lambda: self.switch_page(2))
        self.product_page.server_immediate_connect.connect(self.on_server_immediate_connect)
        self.product_page.server_disconnect_request.connect(self.on_server_disconnect_request)
        self.product_page.server_switch_root_request.connect(self.on_server_switch_root_request)
        self.product_page.schedules_updated.connect(self.reload_scheduled_inspections)
        self.system_guard_page.schedules_updated.connect(self.reload_scheduled_inspections)
        self.product_page.go_to_next_step.connect(self.on_go_to_next_step)
        
        # 初始化窗口
        self.init_navigation()
        
        # 设置主题色
        self.setCustomBackgroundColor(QColor(255, 255, 255), QColor(32, 32, 32))

        self.schedule_timer = QTimer(self)
        self.schedule_timer.timeout.connect(self.check_scheduled_inspections)
        self.schedule_timer.start(60000)
        QTimer.singleShot(3000, self.check_scheduled_inspections)
        self.init_tray_icon()

    def init_tray_icon(self):
        """初始化系统托盘，关闭窗口时最小化到托盘。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon_path = Path(__file__).resolve().parents[2] / "icons" / "tray_green.ico"
        if not icon_path.exists():
            icon_path = Path(__file__).resolve().parents[2] / "icons" / "app.ico"
        icon = QIcon(str(icon_path)) if icon_path.exists() else self.windowIcon()
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("AI智能运维")

        tray_menu = QMenu(self)
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_from_tray)
        tray_menu.addAction(show_action)
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.exit_from_tray)
        tray_menu.addAction(exit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_from_tray()

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def exit_from_tray(self):
        self._force_quit = True
        if self.tray_icon:
            self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if self._force_quit or not self.tray_icon:
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray_icon.showMessage("AI智能运维", "程序已最小化到托盘，右键托盘图标可退出。", QSystemTrayIcon.MessageIcon.Information, 2500)

    def reload_scheduled_inspections(self):
        """定时巡检配置更新后立即重新检查一次。"""
        self._log_schedule("定时巡检配置已更新，立即检查一次")
        self.check_scheduled_inspections()

    def _schedule_log_path(self) -> Path:
        return get_config_dir() / "scheduled_inspection.log"

    def _log_schedule(self, message: str):
        try:
            log_path = self._schedule_log_path()
            log_path.parent.mkdir(exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception:
            pass

    def _load_servers_for_schedule(self) -> list:
        config_file = get_config_dir() / "servers.json"
        if not config_file.exists():
            return []
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f).get("servers", [])
        except Exception:
            return []

    def _load_schedules(self) -> list:
        config_file = get_config_dir() / "schedules.json"
        if not config_file.exists():
            return []
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f).get("schedules", [])
        except Exception:
            return []

    def _save_schedules(self, schedules: list):
        config_file = get_config_dir() / "schedules.json"
        config_file.parent.mkdir(exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump({"schedules": schedules}, f, indent=2, ensure_ascii=False)

    def _schedule_times(self, schedule: dict) -> list:
        if schedule.get("schedule_type") == "daily" and schedule.get("daily_mode") == "interval_minutes":
            return [datetime.now().strftime("%H:%M")]
        times = schedule.get("times")
        if isinstance(times, list) and times:
            return sorted({str(t).strip() for t in times if str(t).strip()})
        legacy_time = str(schedule.get("time", "")).strip()
        return [legacy_time] if legacy_time else []

    def _schedule_due_date(self, schedule: dict, now: datetime) -> bool:
        schedule_type = schedule.get("schedule_type", "daily")
        if schedule_type == "weekly":
            weekdays = schedule.get("weekdays")
            if not isinstance(weekdays, list) or not weekdays:
                weekdays = [1]
            return now.isoweekday() in {int(day) for day in weekdays}
        if schedule_type == "monthly":
            monthly_mode = schedule.get("monthly_mode", "month_days")
            if monthly_mode == "interval_days":
                interval_days = max(1, int(schedule.get("interval_days", 1) or 1))
                start_date_text = schedule.get("start_date") or now.strftime("%Y-%m-%d")
                if not schedule.get("start_date"):
                    schedule["start_date"] = start_date_text
                try:
                    start_date = datetime.strptime(start_date_text, "%Y-%m-%d").date()
                except Exception:
                    start_date = now.date()
                    schedule["start_date"] = now.strftime("%Y-%m-%d")
                return (now.date() - start_date).days % interval_days == 0
            month_days = schedule.get("month_days")
            if not isinstance(month_days, list) or not month_days:
                month_days = [schedule.get("month_day", 1)]
            last_day = calendar.monthrange(now.year, now.month)[1]
            due_days = {min(max(1, int(day)), last_day) for day in month_days}
            return now.day in due_days
        if schedule_type == "interval_days":
            interval_days = max(1, int(schedule.get("interval_days", 1) or 1))
            start_date_text = schedule.get("start_date") or now.strftime("%Y-%m-%d")
            if not schedule.get("start_date"):
                schedule["start_date"] = start_date_text
            try:
                start_date = datetime.strptime(start_date_text, "%Y-%m-%d").date()
            except Exception:
                start_date = now.date()
                schedule["start_date"] = now.strftime("%Y-%m-%d")
            return (now.date() - start_date).days % interval_days == 0
        return True

    def _next_due_time(self, schedule: dict, now: datetime) -> tuple[str, str] | tuple[None, str]:
        if not self._schedule_due_date(schedule, now):
            return None, "未到执行日期"
        current_time = now.strftime("%H:%M")
        today = now.strftime("%Y-%m-%d")
        last_run_keys = schedule.get("last_run_keys")
        if not isinstance(last_run_keys, list):
            last_run_keys = []
        if schedule.get("schedule_type") == "daily" and schedule.get("daily_mode") == "interval_minutes":
            interval = max(1, int(schedule.get("interval_minutes", 60) or 60))
            minute_of_day = now.hour * 60 + now.minute
            if minute_of_day % interval != 0:
                return None, f"未到分钟间隔 interval={interval} now={current_time}"
            run_key = f"{today} {current_time}"
            if run_key in last_run_keys:
                return None, "当前分钟已执行"
            return current_time, run_key
        for schedule_time in self._schedule_times(schedule):
            run_key = f"{today} {schedule_time}"
            if current_time < schedule_time:
                return None, f"未到执行时间 schedule={schedule_time} now={current_time}"
            if run_key in last_run_keys:
                continue
            return schedule_time, run_key
        return None, "当前日期时间点均已执行"

    def check_scheduled_inspections(self):
        """检查是否有到点的定时巡检任务。"""
        if self.scheduled_worker and self.scheduled_worker.isRunning():
            self._log_schedule("跳过检查: 已有定时巡检正在执行")
            return

        now = datetime.now()
        schedules = self._load_schedules()
        if not schedules:
            self._log_schedule("跳过检查: 未配置定时巡检")
            return

        servers = {server.get("id"): server for server in self._load_servers_for_schedule()}
        for schedule in schedules:
            server_id = schedule.get("server_id")
            if not schedule.get("enabled"):
                self._log_schedule(f"跳过任务: server_id={server_id} 未启用")
                continue
            schedule_time, run_key_or_reason = self._next_due_time(schedule, now)
            if not schedule_time:
                self._log_schedule(f"跳过任务: server_id={server_id} {run_key_or_reason}")
                continue

            server = servers.get(server_id)
            if not server:
                self._log_schedule(f"跳过任务: 找不到服务器配置 server_id={server_id}")
                continue

            today = now.strftime("%Y-%m-%d")
            schedule["last_run_date"] = today
            last_run_keys = schedule.get("last_run_keys")
            if not isinstance(last_run_keys, list):
                last_run_keys = []
            last_run_keys.append(run_key_or_reason)
            schedule["last_run_keys"] = last_run_keys[-500:]
            self._save_schedules(schedules)
            self._log_schedule(f"触发定时巡检: server={server.get('name') or server.get('host')} schedule={schedule_time} now={now.strftime('%H:%M')}")
            self.start_scheduled_inspection(server, schedule)
            break

    def start_scheduled_inspection(self, server: dict, schedule: dict):
        """启动一个后台定时巡检。"""
        self.scheduled_worker = ScheduledInspectionWorker(server, schedule.get("report_dir", ""), self._schedule_log_path())
        self.scheduled_worker.finished_with_result.connect(self.on_scheduled_inspection_finished)
        self.scheduled_worker.start()
        self.connection_btn.setToolTip(f"正在执行定时巡检：{server.get('name') or server.get('host')}")

    def on_scheduled_inspection_finished(self, success: bool, message: str, report_path: str):
        if success:
            self._log_schedule(f"定时巡检完成: {report_path}")
            self.connection_btn.setToolTip(f"{message}，报告已导出：{report_path}")
        else:
            self._log_schedule(f"定时巡检失败: {message}")
            self.connection_btn.setToolTip(message)

    def init_navigation(self):
        """初始化导航设置"""
        # 设置导航栏宽度（窄一点）
        self.navigationInterface.setExpandWidth(180)
        
        # 设置标题栏字体大小
        self.titleBar.titleLabel.setStyleSheet("font-size: 18px; font-weight: bold;")

        # qfluentwidgets 自定义标题栏不会稳定显示 setWindowIcon，手动在标题左侧放置主题图标
        title_icon_path = Path(__file__).resolve().parents[2] / "icons" / "robot.svg"
        if title_icon_path.exists():
            self.title_icon_label = QLabel(self)
            self.title_icon_label.setFixedSize(24, 24)
            self.title_icon_label.setPixmap(QIcon(str(title_icon_path)).pixmap(22, 22))
            self.title_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title_index = self.titleBar.hBoxLayout.indexOf(self.titleBar.titleLabel)
            self.titleBar.hBoxLayout.insertWidget(max(title_index, 0), self.title_icon_label)
        
        # 在标题栏标题后面添加连接状态按钮
        self.connection_btn = PrimaryPushButton()
        self.connection_btn.setText("● 未连接")
        self.connection_btn.setFixedHeight(28)
        self.connection_btn.setEnabled(False)
        self.connection_btn.setStyleSheet("""
            QPushButton {
                background-color: #f3f4f6;
                color: #6b7280;
                border: 1px solid #e5e7eb;
                border-radius: 0px;
                font-size: 12px;
                font-weight: bold;
                padding: 0 10px;
            }
            QPushButton:disabled {
                background-color: #f3f4f6;
                color: #6b7280;
                border: 1px solid #e5e7eb;
            }
        """)
        self.connection_btn.clicked.connect(self.disconnect_and_go_home)
        self.connection_btn.installEventFilter(self)
        title_index = self.titleBar.hBoxLayout.indexOf(self.titleBar.titleLabel)
        self.titleBar.hBoxLayout.insertWidget(title_index + 1 if title_index >= 0 else 2, self.connection_btn)
    
    def eventFilter(self, obj, e):
        """事件过滤器 - 处理连接按钮悬停"""
        if hasattr(self, 'connection_btn'):
            if obj == self.connection_btn and e.type() == 10:  # Enter
                if self.is_connected:
                    self.connection_btn.setText("断开连接")
            elif obj == self.connection_btn and e.type() == 11:  # Leave
                if self.is_connected:
                    self.connection_btn.setText("● 已连接")
        return super().eventFilter(obj, e)
    
    def _on_page_changed(self, index: int):
        """页面切换拦截 - 未连接时阻止访问受限页面"""
        # 页面索引: 0=首页, 1=配置管理, 2=AI排障, 3=AI巡检, 4=定时巡检, 5=日志收集, 6=大模型配置
        # 首页、配置管理、定时巡检、大模型配置不需要连接，AI排障/AI巡检/日志收集需要连接
        if not self.is_connected and index in (2, 3, 5):
            from qfluentwidgets import MessageBox
            MessageBox("提示", "请先连接服务器", self).exec()
            # 切换回配置管理页
            self.stackedWidget.setCurrentIndex(1)
            self.navigationInterface.setCurrentItem(self.product_page.objectName())
    
    def switch_page(self, index: int, force: bool = False):
        """切换页面"""
        page_names = [
            self.home_page,
            self.product_page,
            self.ai_troubleshoot_page,
            self.inspection_page,
            self.system_guard_page,
            self.log_extract_page,
            self.ai_config_page
        ]
        
        # 检查页面是否可访问（未连接时不能访问AI排障、AI巡检和日志收集）
        if not force and not self.is_connected and index in (2, 3, 5):
            from qfluentwidgets import MessageBox
            MessageBox("提示", "请先连接服务器", self).exec()
            return
        
        if 0 <= index < len(page_names):
            self.stackedWidget.setCurrentWidget(page_names[index])
            self.current_page_index = index
            # 更新导航选中状态
            self.navigationInterface.setCurrentItem(page_names[index].objectName())
    
    def update_navigation_enabled(self):
        """更新导航项的启用状态"""
        # 由于NavigationInterface没有setItemEnable方法，使用switch_page的限制来实现
        pass
    
    def on_server_immediate_connect(self, server: dict, password: str):
        """立即连接服务器（不需要跳转页面）"""
        self.pending_server = server
        host = server.get("host", "")
        port = server.get("port", 22)
        username = server.get("username", "")
        product = server.get("product", "")
        
        # 判断是否使用OTP（办公智盾默认使用）
        use_otp = product == "办公智盾"
        
        # 创建连接worker
        connection_params = {
            'host': host,
            'port': port,
            'username': username,
            'password': password
        }
        
        self.connection_worker = ConnectionWorker(connection_params, use_otp=use_otp)
        self.connection_worker.connection_result.connect(self.on_connection_result)
        self.connection_worker.otp_challenge.connect(self.on_otp_challenge)
        self.connection_worker.start()
    
    def on_otp_challenge(self, challenge_text: str):
        """处理OTP挑战"""
        from .connection_page import OTPPasswordDialog, QDialog
        
        dialog = OTPPasswordDialog(challenge_text, self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            otp_password = dialog.get_password()
            if self.connection_worker:
                self.connection_worker.set_otp_response(otp_password)
        else:
            if self.connection_worker:
                self.connection_worker.set_otp_response("")
    
    def on_connection_result(self, success: bool, message: str, connection_info: dict):
        """连接结果处理"""
        if success:
            self.ssh_client = connection_info.get('ssh_client')
            self.connection_info = connection_info
            self.is_connected = True
            
            # 更新导航启用状态
            self.update_navigation_enabled()
            
            # 更新产品页面的连接状态
            # 优先使用pending_server中的id，因为connection_info中没有id字段
            server_id = self.pending_server.get("id") if self.pending_server else connection_info.get("id")
            self.product_page.set_connected_server(server_id)
            
            # 更新按钮状态
            self.connection_btn.setEnabled(True)
            self.connection_btn.setText("● 已连接")
            self.connection_btn.setStyleSheet("""
                QPushButton {
                    background-color: #50C9C3;
                    color: white;
                    border: 1px solid #50C9C3;
                    border-radius: 0px;
                    font-size: 12px;
                    font-weight: bold;
                    padding: 0 10px;
                }
                QPushButton:hover {
                    background-color: #3eb8b3;
                    border-color: #3eb8b3;
                }
                QPushButton:pressed {
                    background-color: #2a9d97;
                    border-color: #2a9d97;
                }
            """)
            
            # 设置当前产品到后续页面
            product_name = self.pending_server.get("product", "") if self.pending_server else ""
            self.selected_product_name = product_name
            
            # 设置 SSH 客户端到巡检页面
            self.inspection_page.set_ssh_client(self.ssh_client)
            self.inspection_page.set_product(product_name)
            self.log_extract_page.set_ssh_client(self.ssh_client)
            self.log_extract_page.set_product(product_name)
            
            # 设置 SSH 客户端和产品到AI排障页面
            # 使用服务器host作为唯一标识，用于隔离不同服务器的聊天记录
            server_host = self.pending_server.get("host", "") if self.pending_server else ""
            self.ai_troubleshoot_page.set_ssh_client(self.ssh_client, server_host)
            self.ai_troubleshoot_page.set_product(product_name)
            
            # 检查是否需要切换root
            if self.pending_server and self.pending_server.get("switch_root", True):
                self.after_connect_switch_root()
        else:
            QMessageBox.warning(self, "连接失败", message)
    
    def after_connect_switch_root(self):
        """连接后自动切换root"""
        from .connection_page import RootPasswordDialog, QDialog as QtQDialog
        
        dialog = RootPasswordDialog(self)
        
        if dialog.exec() == QtQDialog.DialogCode.Accepted:
            root_password = dialog.get_password()
            
            if self.ssh_client and hasattr(self.ssh_client, 'execute'):
                try:
                    cmd = f'echo "{root_password}" | sudo -S whoami 2>/dev/null'
                    return_code, output, error = self.ssh_client.execute(cmd)
                    
                    if return_code == 0 and output.strip() == 'root':
                        QMessageBox.information(self, "切换成功", "已成功切换到root用户")
                        self.is_root = True
                        # 更新连接信息的用户名为root
                        if self.connection_info:
                            self.connection_info['username'] = 'root'
                        # 更新UI - 隐藏切换root按钮
                        self.product_page.set_connected_server(self.connected_server_id, is_root=True)
                    else:
                        QMessageBox.warning(self, "切换失败", "无法切换到root用户，请检查密码")
                except Exception as e:
                    QMessageBox.warning(self, "切换失败", f"切换root时出错: {str(e)}")
    
    def on_server_disconnect_request(self, server: dict):
        """处理服务器断开请求"""
        self.disconnect_and_go_home()
    
    def on_server_switch_root_request(self, server: dict):
        """处理切换root请求（从列表按钮触发）"""
        # 直接调用切换root逻辑
        self.after_connect_switch_root()
    
    def disconnect_and_go_home(self):
        """断开连接并返回服务器管理页"""
        if self.ssh_client:
            try:
                self.ssh_client.disconnect()
            except:
                pass
        
        self.is_connected = False
        self.connection_info = {}
        self.ssh_client = None
        self.pending_server = None
        
        # 更新按钮状态
        self.connection_btn.setText("● 未连接")
        self.connection_btn.setToolTip("")
        self.connection_btn.setEnabled(False)
        self.connection_btn.setStyleSheet("""
            QPushButton {
                background-color: #f3f4f6;
                color: #6b7280;
                border: 1px solid #e5e7eb;
                border-radius: 0px;
                font-size: 12px;
                font-weight: bold;
                padding: 0 10px;
            }
            QPushButton:disabled {
                background-color: #f3f4f6;
                color: #6b7280;
                border: 1px solid #e5e7eb;
            }
        """)
        
        # 重置产品页面的连接状态
        self.product_page.set_connected_server(None)
        
        # 重置所有页面的当前产品
        self.selected_product = None
        self.selected_product_name = ""
        self.selected_product_id = ""
        self.inspection_page.set_product("")
        self.log_extract_page.set_ssh_client(None)
        self.log_extract_page.set_product("")
        
        # 返回服务器管理页
        self.switch_page(0)
    
    def on_go_to_next_step(self):
        """处理首页下一步按钮点击"""
        self.switch_page(3)  # AI巡检在索引3
    
    def on_inspection_items_confirmed(self, inspection_config: dict):
        """巡检项确认处理"""
        scripts = inspection_config.get("scripts", [])
        custom_scripts = inspection_config.get("custom_scripts", [])
        product_scripts = inspection_config.get("product_scripts", [])
        
        if self.ssh_client:
            self.inspection_page.set_ssh_client(self.ssh_client)
            self.log_extract_page.set_ssh_client(self.ssh_client)
        
        self.inspection_page.set_product(self.selected_product_name)
        self.log_extract_page.set_product(self.selected_product_name)
        self.inspection_page.set_inspection_config(scripts, custom_scripts, product_scripts)
        self.switch_page(3)  # AI巡检在索引3
    
    def setCustomBackgroundColor(self, light, dark):
        """设置自定义背景色"""
        super().setCustomBackgroundColor(light, dark)
