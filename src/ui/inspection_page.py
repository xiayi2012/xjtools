"""
巡检页面模块
实现AI巡检执行和实时输出显示
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QGroupBox, QCheckBox,
    QTextEdit, QProgressBar, QFileDialog, QMessageBox,
    QSplitter, QScrollArea, QTabWidget, QTextBrowser, QListWidget, QListWidgetItem,
    QAbstractItemView, QProgressDialog, QApplication, QDialog, QGraphicsDropShadowEffect,
    QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QSize
from PyQt6.QtGui import QFont, QColor, QTextCharFormat, QBrush, QMovie, QIcon
from qfluentwidgets import MessageBox
from scripts.inspection_catalog import get_builtin_checks, format_check_output, classify_output, STATUS_LABELS
import sys
import socket
import json
import os
import subprocess
from copy import deepcopy
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from utils import get_config_dir, get_ai_config_path, normalize_ai_configs


class InspectionWorker(QThread):
    """巡检工作线程"""
    
    output_received = pyqtSignal(str, str)  # (script_name, output)
    script_output = pyqtSignal(str, str)    # (script_name, output_line)
    script_completed = pyqtSignal(str, bool, str)  # (script_name, success, error_msg)
    inspection_finished = pyqtSignal(bool, str)
    
    def __init__(self, ssh_client, scripts: list):
        super().__init__()
        self.ssh_client = ssh_client
        self.scripts = scripts
        self.is_running = False
    
    def run(self):
        """执行巡检"""
        self.is_running = True
        builtin_output = {}
        custom_output = {}
        
        
        try:
            for script in self.scripts:
                if not self.is_running:
                    break
                
                script_name = script.get('Name', script.get('name', 'Unknown'))
                script_type = script.get('_type', 'builtin')
                output_lines = []
                
                
                self.script_output.emit(script_name, f"\n{'='*60}\n")
                if script_type == 'custom':
                    self.script_output.emit(script_name, f"📜 自定义脚本: {script_name}\n")
                else:
                    self.script_output.emit(script_name, f"📋 巡检项: {script_name}\n")
                self.script_output.emit(script_name, f"{'='*60}\n\n")
                
                # 执行命令
                command = script.get('command', script.get('content', ''))

                
                error_msg = ""
                try:
                    for line in self.ssh_client.execute_stream(command, timeout=script.get("timeout", 300)):
                        if line and (
                            "连接已断开" in line
                            or "未连接到服务器" in line
                            or "请重新连接服务器" in line
                            or "OTP Shell已不可用" in line
                        ):
                            error_msg = line.replace("错误：", "").strip()
                            self.script_output.emit(script_name, f"\n❌ {error_msg}\n")
                            self.script_completed.emit(script_name, False, error_msg)
                            self.is_running = False
                            break

                        # 空行是心跳信号，用于检查 is_running
                        if line == "":
                            if not self.is_running:
                                # 请求SSH客户端停止
                                if hasattr(self.ssh_client, 'request_stop'):
                                    self.ssh_client.request_stop()
                                break
                            continue  # 跳过心跳，不计入输出
                        
                        if not self.is_running:
                            # 请求SSH客户端停止
                            if hasattr(self.ssh_client, 'request_stop'):
                                self.ssh_client.request_stop()
                            break
                        output_lines.append(line)
                        self.script_output.emit(script_name, line)
                except Exception as e:
                    error_msg = str(e)
                    self.script_output.emit(script_name, f"\n❌ 执行错误: {error_msg}\n")
                    failed_output = ''.join(output_lines) + f"\n执行错误: {error_msg}\n"
                    target = custom_output if script_type == 'custom' else builtin_output
                    target[script_name] = format_check_output(script, failed_output)
                    self.script_output.emit(script_name, target[script_name])
                    self.script_completed.emit(script_name, False, error_msg)
                    continue
                
                if not self.is_running:
                    break
                
                raw_output = ''.join(output_lines)
                output_text = format_check_output(script, raw_output)
                if output_text != raw_output.strip():
                    self.script_output.emit(script_name, output_text[len(raw_output.strip()):])
                
                # 根据类型分别存储
                if script_type == 'custom':
                    custom_output[script_name] = output_text
                else:
                    builtin_output[script_name] = output_text
                
                self.script_completed.emit(script_name, True, "")
            
            # 发送合并的输出
            all_output = {
                "builtin": builtin_output,
                "custom": custom_output
            }
            self.output_received.emit("all", json.dumps(all_output, ensure_ascii=False, indent=2))
            
            if self.is_running:
                self.inspection_finished.emit(True, "巡检完成")
            else:
                final_message = error_msg if 'error_msg' in locals() and error_msg else "巡检已停止"
                self.inspection_finished.emit(False, final_message)
            
        except Exception as e:
            self.inspection_finished.emit(False, f"巡检异常: {str(e)}")
        
        finally:
            # 清除停止请求
            if hasattr(self.ssh_client, 'clear_stop_request'):
                self.ssh_client.clear_stop_request()
    
    def stop(self):
        """停止巡检"""
        self.is_running = False
        # 请求SSH客户端停止
        if hasattr(self.ssh_client, 'request_stop'):
            self.ssh_client.request_stop()


class LoadingDialog(QDialog):
    """自定义加载对话框，带进度展示和停止按钮"""

    stop_requested = pyqtSignal()

    def __init__(self, message="正在分析…", parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出AI分析报告")
        robot_icon = Path(__file__).resolve().parents[2] / "icons" / "robot.svg"
        if robot_icon.exists():
            self.setWindowIcon(QIcon(str(robot_icon)))
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(True)
        self.setFixedSize(420, 280)
        self._allow_close_without_stop = False

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(10)

        self.robot_label = QLabel("🤖", self)
        self.robot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(48)
        self.robot_label.setFont(font)
        self.robot_label.setFixedHeight(60)

        self.message_label = QLabel(message, self)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setStyleSheet("""
            color: #333333;
            font-size: 14px;
            font-weight: bold;
        """)
        self.message_label.setWordWrap(True)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                background: #f3f4f6;
                height: 20px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #50C9C3, stop:1 #3BA8A2);
                border-radius: 5px;
            }
        """)

        self.step_label = QLabel("", self)
        self.step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step_label.setStyleSheet("color: #6b7280; font-size: 12px;")

        self.time_label = QLabel("已用时间：0 秒", self)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setStyleSheet("color: #6b7280; font-size: 12px;")

        self.stop_btn = QPushButton("⏹ 停止分析")
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background: #ef4444;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #dc2626;
            }
        """)
        self.stop_btn.clicked.connect(self._on_stop_clicked)

        layout.addWidget(self.robot_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.message_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.step_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.time_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.stop_btn, 0, Qt.AlignmentFlag.AlignCenter)

        self._elapsed = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_time)
        self._stop_clicked = False

    def showEvent(self, event):
        self._timer.start(1000)
        super().showEvent(event)

    def closeEvent(self, event):
        self._timer.stop()
        if not self._allow_close_without_stop and not self._stop_clicked:
            self._stop_clicked = True
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("已停止")
            self.stop_requested.emit()
        super().closeEvent(event)

    def _update_time(self):
        self._elapsed += 1
        self.time_label.setText(f"已用时间：{self._elapsed} 秒")

    def _on_stop_clicked(self):
        self._stop_clicked = True
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("已停止")
        self.stop_requested.emit()

    def setProgress(self, current, total, step_name=""):
        if current >= total:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
        else:
            self.progress_bar.setRange(0, 0)
        if step_name:
            if step_name.startswith("正在分析"):
                self.message_label.setText("正在分析")
                model_name = step_name.split("：", 1)[1] if "：" in step_name else ""
                self.step_label.setText(f"当前使用模型：{model_name}" if model_name else "")
            else:
                self.step_label.setText(step_name)
        else:
            self.step_label.setText("正在处理，请稍候")

    def setMessage(self, message):
        self.message_label.setText(message)


class ExportReportWorker(QThread):

    export_completed = pyqtSignal(str, str)
    progress_updated = pyqtSignal(int, int, str)

    @staticmethod
    def _format_api_error(e) -> str:
        import urllib.error
        if isinstance(e, urllib.error.HTTPError):
            code = e.code
            error_body = ''
            try:
                error_body = e.read().decode('utf-8', errors='ignore').strip()
            except:
                pass
            detail = error_body
            if error_body:
                try:
                    import json
                    error_json = json.loads(error_body)
                    error_obj = error_json.get("error", error_json)
                    if isinstance(error_obj, dict):
                        detail = (
                            error_obj.get("message")
                            or error_obj.get("msg")
                            or error_obj.get("detail")
                            or error_body
                        )
                    else:
                        detail = str(error_obj)
                except:
                    detail = error_body
            detail = detail or getattr(e, "reason", "") or "未知原因"
            if code in (401, 403):
                return f"认证失败(HTTP {code}) - {detail}"
            elif code == 404:
                return f"接口不存在(HTTP 404) - {detail}"
            elif code == 500:
                return f"服务器内部错误(HTTP 500) - {detail}"
            elif code == 429:
                return f"请求频率超限(HTTP 429) - {detail}"
            else:
                return f"HTTP错误({code}) - {detail}"
        error_msg = str(e)
        if '10054' in error_msg or '远程主机强迫关闭' in error_msg:
            return f"连接被远程服务器关闭 - 请检查API配置是否正确或稍后重试"
        if 'timed out' in error_msg.lower() or 'timeout' in error_msg.lower():
            return f"请求超时 - API服务器响应太慢或网络延迟过高"
        if 'ConnectionRefusedError' in error_msg or '连接被拒绝' in error_msg:
            return f"网络连接失败 - 无法访问API服务器，请检查Base URL和网络状态"
        if 'Name or service not known' in error_msg or 'getaddrinfo failed' in error_msg:
            return f"DNS解析失败 - 请检查Base URL是否正确"
        if 'SSL' in error_msg or 'certificate' in error_msg.lower():
            return f"SSL证书错误 - 服务器证书无效或不受信任"
        return f"网络错误: {error_msg}"

    @staticmethod
    def _clean_ai_response(content: str) -> str:
        import re
        content = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', content, flags=re.DOTALL)
        content = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', content, flags=re.DOTALL)
        content = content.strip()
        return content

    @staticmethod
    def _build_chat_completions_url(base_url: str) -> str:
        base_url = (base_url or "https://api.openai.com/v1").strip().rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        if "siliconflow.cn" in base_url and not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        return f"{base_url}/chat/completions"

    @staticmethod
    def _prepare_inspection_data(inspection_data: str, limit: int = 18000) -> str:
        if len(inspection_data) <= limit:
            return inspection_data
        head_len = limit // 2
        tail_len = limit - head_len
        omitted = len(inspection_data) - limit
        return (
            inspection_data[:head_len]
            + f"\n\n...[巡检输出过长，已省略中间 {omitted} 字符，保留开头和结尾用于分析]...\n\n"
            + inspection_data[-tail_len:]
        )

    @staticmethod
    def _extract_error_detail(text: str, fallback: str = "未知原因") -> str:
        text = (text or "").strip()
        if not text:
            return fallback
        try:
            import json
            data = json.loads(text)
            error_obj = data.get("error", data)
            if isinstance(error_obj, dict):
                return (
                    error_obj.get("message")
                    or error_obj.get("msg")
                    or error_obj.get("detail")
                    or text
                )
            return str(error_obj)
        except Exception:
            return text

    @staticmethod
    def _extract_openai_content(result: dict) -> str:
        try:
            return result["choices"][0]["message"]["content"]
        except Exception:
            raise Exception(f"API返回格式异常: {str(result)[:500]}")

    def __init__(self, api_config: dict, inspection_data: str, prompt: str):
        super().__init__()
        self.api_config = api_config
        self.inspection_data = inspection_data
        self.prompt = prompt
        self._stop_requested = False
        self._api_timeout = api_config.get('api_timeout', 300)

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            if self._stop_requested:
                self.export_completed.emit("", "已停止分析")
                return

            configs = normalize_ai_configs(self.api_config)
            if not configs:
                raise Exception("AI配置未找到，请先在AI大模型配置页面配置API")

            result = ""
            errors = []
            for config_index, config in enumerate(configs, 1):
                self.api_config = config
                self._api_timeout = config.get('api_timeout', 300)
                model_name = config.get("model") or config.get("name") or f"大模型{config_index}"
                self.progress_updated.emit(1, 3, f"连接AI服务：{model_name}")
                api_type = config.get('api_type', 'openai')

                if self._stop_requested:
                    self.export_completed.emit("", "已停止分析")
                    return

                self.progress_updated.emit(2, 3, f"正在分析：{model_name}")

                try:
                    if api_type == 'openai':
                        result = self._call_openai_api()
                    elif api_type == 'claude':
                        result = self._call_claude_api()
                    elif api_type == 'azure':
                        result = self._call_azure_api()
                    elif api_type == 'local' or api_type == 'ollama':
                        result = self._call_local_api()
                    else:
                        result = self._call_openai_api()
                    break
                except Exception as exc:
                    errors.append(f"{model_name}: {exc}")
                    if config_index >= len(configs):
                        raise Exception("所有大模型调用失败：" + "；".join(errors))

            if self._stop_requested:
                self.export_completed.emit("", "已停止分析")
                return

            self.progress_updated.emit(3, 3, "分析完成")
            self.export_completed.emit(result, "")

        except Exception as e:
            import traceback
            error_msg = str(e) if str(e) else f"未知错误: {type(e).__name__}"
            self.export_completed.emit("", f"生成报告失败: {error_msg}")

    def _call_openai_api(self) -> str:
        """调用 OpenAI API"""
        import json

        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', 'https://api.openai.com/v1')
        model = self.api_config.get('model', 'gpt-3.5-turbo')

        url = self._build_chat_completions_url(base_url)
        safe_inspection_data = self._prepare_inspection_data(self.inspection_data)

        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": f"请分析以下巡检结果：\n\n{safe_inspection_data}"}
        ]

        data = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": min(int(self.api_config.get("max_tokens", 3500) or 3500), 3500)
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        try:
            import requests
            response = requests.post(url, headers=headers, json=data, timeout=min(self._api_timeout, 120))
            if response.status_code >= 400:
                detail = self._extract_error_detail(response.text, response.reason)
                raise Exception(f"HTTP {response.status_code} 错误: {detail}")
            result = response.json()
            return self._clean_ai_response(self._extract_openai_content(result))
        except Exception as e:
            if "HTTP " in str(e) or "API返回格式异常" in str(e):
                raise
            raise Exception(f"API请求失败: {type(e).__name__}: {str(e)}")

    def _call_claude_api(self) -> str:
        """调用 Claude API"""
        import urllib.request
        import urllib.error
        import json

        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', 'https://api.anthropic.com/v1')
        model = self.api_config.get('model', 'claude-3-sonnet-20240229')
        safe_inspection_data = self._prepare_inspection_data(self.inspection_data)

        url = f"{base_url}/messages"

        data = {
            "model": model,
            "max_tokens": min(int(self.api_config.get("max_tokens", 3500) or 3500), 3500),
            "system": self.prompt,
            "messages": [{"role": "user", "content": f"请分析以下巡检结果：\n\n{safe_inspection_data}"}]
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

        try:
            with urllib.request.urlopen(req, timeout=min(self._api_timeout, 120)) as response:
                result = json.loads(response.read().decode('utf-8'))
                return self._clean_ai_response(result['content'][0]['text'])
        except (urllib.error.HTTPError, socket.error, OSError) as e:
            raise Exception(self._format_api_error(e))

    def _call_azure_api(self) -> str:
        """调用 Azure OpenAI API"""
        import urllib.request
        import urllib.error
        import json

        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', '')
        deployment = self.api_config.get('model', 'gpt-35-turbo')
        api_version = "2024-02-01"
        safe_inspection_data = self._prepare_inspection_data(self.inspection_data)

        if not base_url:
            raise Exception("Azure API地址未配置")

        url = f"{base_url}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": f"请分析以下巡检结果：\n\n{safe_inspection_data}"}
        ]

        data = {
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": min(int(self.api_config.get("max_tokens", 3500) or 3500), 3500)
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

        try:
            with urllib.request.urlopen(req, timeout=min(self._api_timeout, 120)) as response:
                result = json.loads(response.read().decode('utf-8'))
                return ExportReportWorker._clean_ai_response(result['choices'][0]['message']['content'])
        except (urllib.error.HTTPError, socket.error, OSError) as e:
            raise Exception(ExportReportWorker._format_api_error(e))

    def _call_local_api(self) -> str:
        import urllib.request
        import json

        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', 'http://localhost:11434')
        model = self.api_config.get('model', 'llama3')
        safe_inspection_data = self._prepare_inspection_data(self.inspection_data)

        url = f"{base_url}/chat"

        data = {
            "model": model,
            "messages": [{"role": "user", "content": self.prompt + "\n\n" + safe_inspection_data}],
            "stream": False
        }

        headers = {"Content-Type": "application/json"}

        req = urllib.request.Request(
            url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=min(self._api_timeout, 120)) as response:
                result = json.loads(response.read().decode('utf-8'))
                return ExportReportWorker._clean_ai_response(result['message']['content'])
        except (urllib.error.HTTPError, socket.error, OSError) as e:
            raise Exception(ExportReportWorker._format_api_error(e))


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
            model_name = self.api_config.get("model") or self.api_config.get("name") or "大模型"
            self.analysis_progress.emit(f"正在连接AI服务：{model_name}")
            
            api_type = self.api_config.get('api_type', 'openai')
            
            if api_type == 'openai':
                result = self._call_openai_api()
            elif api_type == 'claude':
                result = self._call_claude_api()
            elif api_type == 'azure':
                result = self._call_azure_api()
            elif api_type == 'local':
                result = self._call_local_api()
            else:
                result = self._call_openai_api()
            
            self.analysis_completed.emit(result, "")
            
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
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
        
        self.analysis_progress.emit(f"正在分析\n当前使用模型：{model}")
        
        url = f"{base_url}/chat/completions"
        
        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": f"请分析以下巡检结果：\n\n{self.inspection_data}"}
        ]
        
        data = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 16000
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
        
        self.analysis_progress.emit(f"正在分析\n当前使用模型：{model}")

        try:
            with urllib.request.urlopen(req, timeout=self._api_timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
                content = ExportReportWorker._clean_ai_response(result['choices'][0]['message']['content'])
                return content
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='ignore')
            try:
                error_json = json.loads(error_body)
                error_msg = error_json.get('error', {}).get('message', error_body)
            except:
                error_msg = error_body
            raise Exception(f"HTTP {e.code} 错误: {error_msg}")
    
    def _call_claude_api(self) -> str:
        """调用 Claude API"""
        import urllib.request
        import json
        
        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', 'https://api.anthropic.com/v1')
        model = self.api_config.get('model', 'claude-3-sonnet-20240229')
        
        self.analysis_progress.emit(f"正在分析\n当前使用模型：{model}")
        
        url = f"{base_url}/messages"
        
        data = {
            "model": model,
            "max_tokens": 16000,
            "system": self.prompt,
            "messages": [{"role": "user", "content": f"请分析以下巡检结果：\n\n{self.inspection_data}"}]
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
        
        self.analysis_progress.emit(f"正在分析\n当前使用模型：{model}")
        
        try:
            with urllib.request.urlopen(req, timeout=self._api_timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
                content = result['content'][0]['text']
                return content
        except (urllib.error.HTTPError, socket.error, OSError) as e:
            raise Exception(ExportReportWorker._format_api_error(e))
    
    def _call_azure_api(self) -> str:
        """调用 Azure OpenAI API"""
        import urllib.request
        import json
        
        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', '')
        deployment = self.api_config.get('deployment', '')
        api_version = self.api_config.get('api_version', '2024-02-15-preview')
        
        self.analysis_progress.emit(f"正在分析\n当前使用模型：{deployment or 'Azure部署'}")
        
        url = f"{base_url}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        
        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": f"请分析以下巡检结果：\n\n{self.inspection_data}"}
        ]
        
        data = {"messages": messages, "temperature": 0.7, "max_tokens": 16000}
        
        headers = {"Content-Type": "application/json", "api-key": api_key}
        
        req = urllib.request.Request(
            url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST'
        )
        
        self.analysis_progress.emit(f"正在分析\n当前使用模型：{deployment or 'Azure部署'}")
        
        try:
            with urllib.request.urlopen(req, timeout=self._api_timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
                content = ExportReportWorker._clean_ai_response(result['choices'][0]['message']['content'])
                return content
        except (urllib.error.HTTPError, socket.error, OSError) as e:
            raise Exception(ExportReportWorker._format_api_error(e))
    
    def _call_local_api(self) -> str:
        """调用本地模型 (Ollama等)"""
        import urllib.request
        import json
        
        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', 'http://localhost:11434')
        model = self.api_config.get('model', 'llama3')
        
        self.analysis_progress.emit(f"正在分析\n当前使用模型：{model}")
        
        url = f"{base_url}/chat"
        
        data = {
            "model": model,
            "messages": [{"role": "user", "content": f"请分析以下巡检结果：\n\n{self.inspection_data}"}],
            "stream": False
        }
        
        headers = {"Content-Type": "application/json"}
        
        req = urllib.request.Request(
            url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST'
        )
        
        self.analysis_progress.emit(f"正在分析\n当前使用模型：{model}")
        
        try:
            with urllib.request.urlopen(req, timeout=self._api_timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
                content = ExportReportWorker._clean_ai_response(result['message']['content'])
                return content
        except (urllib.error.HTTPError, socket.error, OSError) as e:
            raise Exception(ExportReportWorker._format_api_error(e))


class InspectionPage(QWidget):
    """巡检页面"""
    
    # 信号定义
    inspection_completed = pyqtSignal(dict)
    inspection_started = pyqtSignal(dict)
    report_saved = pyqtSignal(dict)
    
    def __init__(self):
        """初始化巡检页面"""
        super().__init__()
        self.ssh_client = None
        self.inspection_worker = None
        self.ai_worker = None  # AI分析线程
        self.ai_result = ""   # AI分析结果
        self.scripts = []  # 内置检查项列表
        self.selected_scripts = []
        self.custom_scripts = []  # 自定义脚本列表
        self.product_scripts = []  # 产品特定检查项列表
        self.builtin_outputs = {}  # 内置检查项输出 {name: output}
        self.custom_script_outputs = {}  # 自定义脚本输出 {name: output}
        self.custom_script_checks = {}  # 自定义脚本复选框 {name: checkbox}
        self.custom_script_tabs = {}  # 自定义脚本标签页 {name: QTextEdit}
        self.init_ui()
    
    def init_ui(self):
        """初始化 UI - 合并版：选择巡检项 + 执行巡检"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # 标题
        title_label = QLabel("一键巡检")
        title_label.setObjectName("pageTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # 当前产品标签
        self.product_label = QLabel("当前产品: 未选择")
        self.product_label.setObjectName("productLabel")
        self.product_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.product_label)
        
        # 主内容区域：水平分割 - 左侧选择列表，右侧输出
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setSizes([400, 600])  # 左侧400，右侧600
        
        # 左侧：选择巡检项区域
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        
        # 全局批量操作按钮
        global_btn_layout = QHBoxLayout()
        global_btn_layout.setSpacing(10)
        
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setObjectName("selectAllGlobalBtn")
        self.select_all_btn.setMinimumHeight(36)
        self.select_all_btn.setMinimumWidth(80)
        self.select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_btn.clicked.connect(self._select_all)
        global_btn_layout.addWidget(self.select_all_btn)
        
        self.deselect_all_btn = QPushButton("取消全选")
        self.deselect_all_btn.setObjectName("deselectAllGlobalBtn")
        self.deselect_all_btn.setMinimumHeight(36)
        self.deselect_all_btn.setMinimumWidth(80)
        self.deselect_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deselect_all_btn.clicked.connect(self._deselect_all)
        global_btn_layout.addWidget(self.deselect_all_btn)
        
        global_btn_layout.addStretch()
        left_layout.addLayout(global_btn_layout)
        
        # 巡检项列表（可拖动排序，可勾选）
        self.inspect_list = QListWidget()
        self.inspect_list.setObjectName("inspectList")
        self.inspect_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.inspect_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.inspect_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.inspect_list.setSpacing(6)
        self.inspect_list.itemChanged.connect(self._on_item_changed)
        left_layout.addWidget(self.inspect_list)
        
        # 右侧：输出显示区域（使用TabWidget展示巡检输出和AI分析结果）
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        
        # 使用TabWidget管理输出和AI结果
        self.output_tabs = QTabWidget()
        self.output_tabs.setObjectName("outputTabs")
        
        # Tab 1: 执行输出
        output_page = QWidget()
        output_layout = QVBoxLayout(output_page)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(0)
        
        output_label = QLabel("巡检输出")
        output_label.setObjectName("outputPanelTitle")
        output_label.setStyleSheet("font-weight: bold; font-size: 14px; padding: 8px 10px; color: #334155;")
        output_layout.addWidget(output_label)
        
        self.output_list = QListWidget()
        self.output_list.setObjectName("outputList")
        self.output_list.setFont(QFont("Consolas", 10))
        self.output_list.setSpacing(6)
        self.output_list.setViewportMargins(0, 0, 0, 0)
        self.output_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        output_layout.addWidget(self.output_list)
        
        self.output_tabs.addTab(output_page, "巡检输出")

        # Tab 2: 巡检预览（显示原始巡检输出）
        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)

        preview_label = QLabel("巡检预览")
        preview_label.setObjectName("outputPanelTitle")
        preview_label.setStyleSheet("font-weight: bold; font-size: 14px; padding: 8px 10px; color: #334155;")
        preview_layout.addWidget(preview_label)

        # 巡检预览使用output_text（原始输出），设为只读
        self.preview_text = QTextBrowser()
        self.preview_text.setObjectName("previewText")
        self.preview_text.setPlaceholderText("巡检完成后在此预览原始输出...")
        self.preview_text.setOpenExternalLinks(True)
        preview_layout.addWidget(self.preview_text)

        self.output_tabs.addTab(preview_page, "巡检预览")

        right_layout.addWidget(self.output_tabs)
        
        # 添加到splitter
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)  # 左侧可伸缩
        splitter.setStretchFactor(1, 2)  # 右侧占更多空间
        
        layout.addWidget(splitter, 1)  # stretch=1 让主内容区占满空间
        
        # 隐藏的output_text用于存储完整输出
        self.output_text = QTextEdit()
        self.output_text.setVisible(False)
        
        # 隐藏script_tabs和generic_output_tab（保留引用但不显示）
        self.script_tabs = QTabWidget()
        self.script_tabs.setVisible(False)
        self.generic_output_tab = QTextEdit()
        
        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        
        self.start_btn = QPushButton("开始巡检")
        self.start_btn.setObjectName("startButton")
        self.start_btn.setMinimumHeight(50)
        self.start_btn.setMinimumWidth(140)
        self.start_btn.clicked.connect(self.start_inspection)
        button_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setMinimumHeight(50)
        self.stop_btn.setMinimumWidth(140)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_inspection)
        button_layout.addWidget(self.stop_btn)
        
        self.export_btn = QPushButton("导出原始报告")
        self.export_btn.setObjectName("exportButton")
        self.export_btn.setMinimumHeight(50)
        self.export_btn.setMinimumWidth(140)
        self.export_btn.clicked.connect(self.on_export_clicked)
        button_layout.addWidget(self.export_btn)

        # 导出巡检报告按钮 - 巡检完成后可用，点击后调用AI美化原始输出
        self.export_ai_btn = QPushButton("分析并导出")
        self.export_ai_btn.setObjectName("exportAiButton")
        self.export_ai_btn.setMinimumHeight(50)
        self.export_ai_btn.setMinimumWidth(140)
        self.export_ai_btn.setEnabled(False)  # 巡检完成后才可用
        self.export_ai_btn.clicked.connect(self.on_export_ai_report)
        button_layout.addWidget(self.export_ai_btn)

        button_layout.addStretch()
        
        layout.addLayout(button_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progressBar")
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.apply_styles()
        
        # 初始化巡检项列表
        self._all_items = []  # 所有巡检项
        self._populate_inspect_list()
    
    def _populate_inspect_list(self):
        """填充巡检项列表（从选择巡检项页面合并）"""
        self._all_items = get_builtin_checks()

        for item_data in self._all_items:
            item = QListWidgetItem(f"{item_data['Name']} [{item_data['category']}]")
            item.setData(Qt.ItemDataRole.UserRole, item_data)
            item.setToolTip(item_data.get("description", ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if item_data['checked'] else Qt.CheckState.Unchecked)
            item.setForeground(QBrush(QColor("#333333")))
            self.inspect_list.addItem(item)
        self._rebuild_output_checklist()
    
    def _on_item_changed(self, item: QListWidgetItem):
        """列表项状态改变"""
        item_data = item.data(Qt.ItemDataRole.UserRole)
        if item_data:
            item_data['checked'] = (item.checkState() == Qt.CheckState.Checked)
        self._move_unchecked_items_to_bottom()
        self._rebuild_output_checklist()

    def _move_unchecked_items_to_bottom(self):
        """取消勾选的巡检项自动移动到底部。"""
        if getattr(self, "_reordering_inspect_items", False):
            return
        self._reordering_inspect_items = True
        self.inspect_list.blockSignals(True)
        try:
            items = []
            while self.inspect_list.count():
                items.append(self.inspect_list.takeItem(0))
            checked_items = [it for it in items if it.checkState() == Qt.CheckState.Checked]
            unchecked_items = [it for it in items if it.checkState() != Qt.CheckState.Checked]
            for list_item in checked_items + unchecked_items:
                self.inspect_list.addItem(list_item)
        finally:
            self.inspect_list.blockSignals(False)
            self._reordering_inspect_items = False
    
    def _select_all(self):
        """全局全选"""
        self.inspect_list.blockSignals(True)
        for i in range(self.inspect_list.count()):
            item = self.inspect_list.item(i)
            item.setCheckState(Qt.CheckState.Checked)
            item_data = item.data(Qt.ItemDataRole.UserRole)
            if item_data:
                item_data['checked'] = True
        self.inspect_list.blockSignals(False)
        self._move_unchecked_items_to_bottom()
        self._rebuild_output_checklist()
    
    def _deselect_all(self):
        """全局取消全选"""
        self.inspect_list.blockSignals(True)
        for i in range(self.inspect_list.count()):
            item = self.inspect_list.item(i)
            item.setCheckState(Qt.CheckState.Unchecked)
            item_data = item.data(Qt.ItemDataRole.UserRole)
            if item_data:
                item_data['checked'] = False
        self.inspect_list.blockSignals(False)
        self._move_unchecked_items_to_bottom()
        self._rebuild_output_checklist()
    
    def set_product(self, product_name: str):
        """设置当前产品"""
        product_changed = getattr(self, 'product_name', '') != product_name
        self.product_name = product_name
        if hasattr(self, 'product_label'):
            if product_name:
                self.product_label.setText(f"当前产品: {product_name}")
            else:
                self.product_label.setText("当前产品: 未选择")
            self.product_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #50C9C3; background: #F0FAFA; padding: 8px; border-radius: 4px;")
        
        if product_changed:
            self._clear_inspection_state()

        # 加载该产品的自定义脚本，并刷新左右两侧巡检项清单
        self._load_custom_scripts(product_name)
        
        # 重置按钮状态
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(True)
        self.progress_bar.setVisible(False)

    def _clear_inspection_state(self):
        """清空当前巡检输出、预览和导出缓存，避免跨产品残留。"""
        self.builtin_outputs = {}
        self.custom_script_outputs = {}
        self.list_item_for_script = {}
        self.list_widget_for_script = {}
        if hasattr(self, 'output_text'):
            self.output_text.clear()
        if hasattr(self, 'preview_text'):
            self.preview_text.clear()
        if hasattr(self, 'output_list'):
            self.output_list.clear()
        if hasattr(self, 'generic_output_tab'):
            self.generic_output_tab.clear()
        if hasattr(self, 'export_ai_btn'):
            self.export_ai_btn.setEnabled(False)
        if hasattr(self, 'output_tabs'):
            self.output_tabs.setCurrentIndex(0)
    
    def _load_custom_scripts(self, product_name: str):
        """加载该产品的自定义脚本到 inspect_list"""
        import json
        from pathlib import Path
        
        # 获取自定义脚本
        self.custom_scripts = []
        scripts_dir = Path(get_config_dir()) / "scripts"
        index_file = scripts_dir / "index.json"
        if index_file.exists():
            try:
                with open(index_file, 'r', encoding='utf-8') as f:
                    all_scripts = json.load(f)
                for script in all_scripts:
                    script_product = script.get('product', '通用')
                    if script_product == product_name or script_product == "" or script_product == "通用":
                        self.custom_scripts.append({
                            "Name": script.get('name', '未知脚本'),
                            "command": "",
                            "checked": True,
                            "category": "自定义",
                            "is_custom_script": True,
                            "script_content": script.get('content', ''),
                            "script_name": script.get('name', '')
                        })
            except Exception as e:
                print(f"加载自定义脚本失败: {e}")
        
        # 更新 inspect_list
        self._refresh_inspect_list()
    
    def _refresh_inspect_list(self):
        """刷新巡检项列表"""
        self.inspect_list.blockSignals(True)
        self.inspect_list.clear()
        
        # 添加内置检查项
        for item_data in self._all_items:
            item = QListWidgetItem(f"{item_data['Name']} [{item_data['category']}]")
            item.setData(Qt.ItemDataRole.UserRole, item_data)
            item.setToolTip(item_data.get("description", ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if item_data['checked'] else Qt.CheckState.Unchecked)
            item.setForeground(QBrush(QColor("#333333")))
            self.inspect_list.addItem(item)
        
        # 添加产品特定检查项
        for script in self.product_scripts:
            item = QListWidgetItem(f"{script['Name']} [{script['category']}]")
            item.setData(Qt.ItemDataRole.UserRole, script)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            item.setForeground(QBrush(QColor("#50C9C3")))  # 主题色
            self.inspect_list.addItem(item)
        
        # 添加自定义脚本
        for script in self.custom_scripts:
            item = QListWidgetItem(f"{script['Name']} [{script['category']}]")
            item.setData(Qt.ItemDataRole.UserRole, script)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            item.setForeground(QBrush(QColor("#333333")))
            self.inspect_list.addItem(item)
        
        self.inspect_list.blockSignals(False)
        self._rebuild_output_checklist()
    
    def set_inspection_config(self, scripts: list, custom_scripts: list, product_scripts: list = None):
        if self.inspection_worker is not None:
            if self.inspection_worker.isRunning():
                self.inspection_worker.stop()
                
        self.scripts = scripts or []
        self.custom_scripts = custom_scripts or []
        self.product_scripts = product_scripts or []
        self._clear_inspection_state()
        self.builtin_outputs = {}
        self.custom_script_outputs = {}
        
        self._sync_inspect_list_from_config()
        
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
    
    def _sync_inspect_list_from_config(self):
        self.inspect_list.blockSignals(True)
        self.inspect_list.clear()
        
        for s in self.scripts:
            name = s.get('name', s.get('Name', '未知'))
            item_data = {
                'Name': name,
                'name': name,
                'command': s.get('command', ''),
                'checked': True,
                'category': s.get('category', '通用'),
            }
            if s.get('_type'):
                item_data['_type'] = s['_type']
            item = QListWidgetItem(f"{name} [{item_data['category']}]")
            item.setData(Qt.ItemDataRole.UserRole, item_data)
            item.setToolTip(item_data.get("description", ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            item.setForeground(QBrush(QColor("#333333")))
            self.inspect_list.addItem(item)
        
        for s in self.product_scripts:
            name = s.get('name', s.get('Name', '未知'))
            item_data = {
                'Name': name,
                'name': name,
                'command': s.get('command', ''),
                'checked': True,
                'category': s.get('category', '产品脚本'),
                'is_product_script': True,
                'script_id': s.get('script_id', ''),
            }
            if s.get('_type'):
                item_data['_type'] = s['_type']
            item = QListWidgetItem(f"{name} [产品脚本]")
            item.setData(Qt.ItemDataRole.UserRole, item_data)
            item.setToolTip(item_data.get("description", ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            item.setForeground(QBrush(QColor("#50C9C3")))
            self.inspect_list.addItem(item)
        
        for s in self.custom_scripts:
            name = s.get('name', s.get('Name', '未知'))
            item_data = {
                'Name': name,
                'name': name,
                'command': s.get('command', ''),
                'checked': True,
                'category': '自定义',
                'is_custom_script': True,
                'script_content': s.get('script_content', s.get('content', '')),
                'script_name': s.get('script_name', name),
            }
            if s.get('_type'):
                item_data['_type'] = s['_type']
            item = QListWidgetItem(f"{name} [自定义]")
            item.setData(Qt.ItemDataRole.UserRole, item_data)
            item.setToolTip(item_data.get("description", ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            item.setForeground(QBrush(QColor("#9333ea")))
            self.inspect_list.addItem(item)
        
        self.inspect_list.blockSignals(False)
        self._rebuild_output_checklist()
    
    def _analyze_output(self, name: str, output: str) -> str:
        """Use the same evidence-based conclusion as scheduled inspection."""
        return format_check_output(name, output)

    def _format_kb(self, kb: int) -> str:
        """格式化KB为人类可读格式"""
        if kb >= 1024*1024:
            return f"{kb/(1024*1024):.1f} GB"
        elif kb >= 1024:
            return f"{kb/1024:.1f} MB"
        else:
            return f"{kb} KB"
    
    def _write_inspection_item(self, f, name: str, output: str, is_md: bool):
        """写入单个巡检项到文件"""
        clean_output = self._analyze_output(name, output)
        if is_md:
            f.write(f"## {name}\n\n")
            f.write(f"```\n{clean_output}\n```\n\n")
        else:
            f.write(f"\n{'='*60}\n")
            f.write(f"{name}\n")
            f.write(f"{'='*60}\n\n")
            f.write(f"{clean_output}\n\n")
    
    def on_export_clicked(self):
        """导出报告按钮点击处理 - 直接保存为txt/md"""
        from PyQt6.QtWidgets import QFileDialog
        from datetime import datetime
        import re
        import os
        import subprocess
        import shlex

        output_text = self.output_text.toPlainText()
        if not output_text.strip() and not self.builtin_outputs and not self.custom_script_outputs:
            MessageBox("提示", "没有可导出的巡检数据", self)
            return

        # 如果 builtin_outputs / custom_script_outputs 为空，尝试从 output_text 中提取
        builtin_out = dict(self.builtin_outputs)
        custom_out = dict(self.custom_script_outputs)

        if not builtin_out and not custom_out and output_text.strip():
            self._parse_output_text_fallback(output_text, builtin_out, custom_out)

        export_format = self._choose_raw_export_format()
        if not export_format:
            return
        if export_format == "txt":
            ext = ".txt"
            filter_type = "文本文件 (*.txt)"
        else:
            ext = ".md"
            filter_type = "Markdown文件 (*.md)"

        # 生成默认文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        product = getattr(self, 'product_label', None)
        product_name = product.text().replace("当前产品: ", "") if product else "巡检"
        default_name = f"巡检报告_{product_name}_{timestamp}{ext}"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存巡检报告", default_name,
            f"{filter_type};;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        # 确保文件有正确的扩展名
        if not file_path.endswith(ext):
            file_path += ext
        
        try:
            is_md = file_path.endswith('.md')
            
            with open(file_path, 'w', encoding='utf-8') as f:
                target = getattr(self, 'task_device', {})
                if target:
                    f.write(f'设备: {target.get("name", "")} ({target.get("host", "")}:{target.get("port", 22)})\n')
                    f.write(f'任务标识: {getattr(self, "task_id", "")}\n\n')
                if is_md:
                    f.write(f"# Linux SSH 巡检报告\n\n")
                    f.write(f"**产品:** {product_name}\n\n")
                    f.write(f"**生成时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                    f.write("---\n\n")
                else:
                    f.write(f"Linux SSH 巡检报告\n")
                    f.write(f"产品: {product_name}\n")
                    f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write("=" * 50 + "\n\n")
                
                written_items = set()  # 用于去重
                
                if builtin_out:
                    for name, output in builtin_out.items():
                        if output and output.strip() and name not in written_items:
                            written_items.add(name)
                            self._write_inspection_item(f, name, output, is_md)
                
                if custom_out:
                    for name, output in custom_out.items():
                        if output and output.strip() and name not in written_items:
                            written_items.add(name)
                            self._write_inspection_item(f, name, output, is_md)
            
            MessageBox("成功", f"巡检报告已保存到:\n{file_path}", self)
            
            self._report_saved(file_path)
            # 自动打开文件夹并选中文件
            real_path = os.path.realpath(file_path)
            folder = os.path.dirname(real_path)
            if os.path.isdir(folder):
                if os.name == 'nt':  # Windows
                    subprocess.run(['explorer', '/select,', real_path], shell=True)
                elif os.name == 'posix':
                    if sys.platform == 'darwin':  # macOS
                        subprocess.run(['open', folder])
                    else:  # Linux
                        subprocess.run(['xdg-open', folder])
        except Exception as e:
            MessageBox("错误", f"保存失败: {str(e)}", self)

    def _choose_raw_export_format(self) -> str | None:
        """选择原始报告导出版本，右上角 X 可直接取消。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("选择导出版本")
        dialog.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        dialog.setModal(True)
        dialog.setFixedSize(360, 180)
        dialog.setStyleSheet("""
            QDialog { background: #f8fafc; }
            QLabel#exportVersionTitle {
                color: #0f172a;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#exportVersionHint {
                color: #64748b;
                font-size: 13px;
            }
            QPushButton#formatBtn {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                color: #334155;
                font-weight: 600;
            }
            QPushButton#formatBtn:hover {
                background: #f0fdfa;
                border-color: #50C9C3;
                color: #0f766e;
            }
        """)

        selected = {"value": None}
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title = QLabel("选择导出版本")
        title.setObjectName("exportVersionTitle")
        layout.addWidget(title)

        hint = QLabel("请选择导出原始巡检报告的文件格式。")
        hint.setObjectName("exportVersionHint")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        txt_btn = QPushButton("TXT 文本")
        txt_btn.setObjectName("formatBtn")
        txt_btn.setFixedHeight(40)
        md_btn = QPushButton("MD 格式")
        md_btn.setObjectName("formatBtn")
        md_btn.setFixedHeight(40)
        txt_btn.clicked.connect(lambda: (selected.__setitem__("value", "txt"), dialog.accept()))
        md_btn.clicked.connect(lambda: (selected.__setitem__("value", "md"), dialog.accept()))
        btn_row.addWidget(txt_btn)
        btn_row.addWidget(md_btn)
        layout.addLayout(btn_row)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return selected["value"]


    def _load_ai_config(self) -> dict:
        """加载AI配置"""
        # 尝试从程序内部目录加载
        config_file = get_ai_config_path()

        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)

                # API类型映射
                api_type_map = {
                    0: "openai", 1: "claude", 2: "azure", 3: "local",
                    5: "openai", 6: "openai", 7: "openai", 8: "openai", 9: "openai",
                    10: "openai", 12: "openai", 13: "openai", 14: "openai"
                }
                api_type_raw = config.get('api_type', 0)
                config['api_type'] = api_type_map.get(api_type_raw, "openai")

                # 清理配置值中的反引号和多余空格
                if 'base_url' in config:
                    original = config['base_url']
                    config['base_url'] = config['base_url'].replace('`', '').replace('\u0060', '').replace('\uff40', '').strip()
                if 'api_key' in config:
                    config['api_key'] = config['api_key'].replace('`', '').replace('\u0060', '').replace('\uff40', '').strip()
                if 'model' in config:
                    config['model'] = config['model'].replace('`', '').replace('\u0060', '').replace('\uff40', '').strip()

                return config
            except Exception as e:
                print(f"[AI Config] Failed to load: {e}")
        return None
    
    def _get_ai_prompt(self) -> str:
        return """你是企业级Linux运维巡检专家。请基于“原始巡检输出”生成简洁的中文HTML分析片段。

重要要求：
1. 只输出 AI 分析与安全建议片段，不要生成完整巡检报告。
2. 不要输出 doctype/html/head/body/style/script/代码块。
3. 不要重复生成报告头部、统计卡片、巡检概要、巡检项列表、关键异常表格，这些由本地模板生成。
4. 内容只包含两个小节：<h3>AI分析</h3> 和 <h3>安全建议</h3>。
5. AI分析基于原始巡检输出总结整体状态、异常原因线索和需要关注的指标；没有数据就写“未获取到相关数据”。
6. 安全建议只给低风险建议，例如只读检查、备份、核对配置、联系管理员、在变更窗口处理。
7. 禁止给出删除数据、格式化磁盘、重启系统、停止业务服务、强杀进程、清空防火墙、修改权限为777等敏感操作命令。
8. 风险等级只使用“异常”和“警告”，不要使用“高风险”“中风险”等等级词。

推荐输出结构：
<h3>AI分析</h3>
<ul><li>...</li></ul>
<h3>安全建议</h3>
<ul><li>...</li></ul>
"""
        return """你是企业级Linux运维巡检专家。请基于巡检数据生成简洁的中文HTML片段。

输出要求：
1. 只输出HTML片段，不要输出doctype/html/head/body/代码块。
2. 使用少量内联style，标签必须闭合。
3. 不要写建议，只描述事实、风险和结论。
4. 不要推测，没有数据就写“未获取到相关数据”。
5. 控制篇幅，优先输出异常和关键指标。
6. 风险等级只使用“异常”和“警告”，不要使用“高风险”“中风险”等等级词。

报告结构：
<section>系统基本信息：主机名、系统版本、运行时间、检查时间</section>
<section>关键异常/风险：最多8条，用表格列出级别、检查项、问题</section>
<section>资源占用：CPU TOP5、内存 TOP5，没有则写未获取到相关数据</section>
<section>服务/容器状态：只列异常、停止、近期重启项，最多10条</section>
<section>AI分析与安全建议：先总结整体状态，再给低风险建议</section>

安全限制：
- 建议只能包含只读检查、备份、核对配置、联系管理员、在变更窗口处理等低风险动作。
- 禁止给出删除数据、格式化磁盘、重启系统、停止业务服务、强杀进程、清空防火墙、修改权限为777等敏感操作命令。

请直接输出HTML内容片段。"""

    def _format_markdown_to_html(self, text: str) -> str:
        """简单的Markdown转HTML"""
        import re

        def escape_html(s):
            """转义HTML标签，防止XSS和显示问题"""
            return (s
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;"))

        lines = text.split('\n')

        # 预处理：处理缩进的代码块和CSS块
        processed_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 检测缩进的代码块（4个空格或更多缩进）
            if line.startswith('    ') or line.startswith('\t'):
                # 这是缩进代码块 - 收集整个块
                code_lines = [line]
                i += 1
                while i < len(lines) and (lines[i].startswith('    ') or lines[i].startswith('\t') or lines[i].strip() == ''):
                    if lines[i].strip() == '':
                        code_lines.append(lines[i])
                    else:
                        code_lines.append(lines[i])
                    i += 1
                # 将代码块作为预格式化代码块显示
                code_content = '\n'.join(code_lines)
                # 移除缩进前缀
                code_content = re.sub(r'^ {4}', '', code_content, flags=re.MULTILINE)
                escaped_code = escape_html(code_content)
                processed_lines.append(f'<pre style="background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; margin: 10px 0; font-family: monospace; font-size: 13px; border-left: 3px solid #ccc;"><code>{escaped_code}</code></pre>')
                continue

            # 处理行内缩进的标题（以#开头但有前导空格）
            if re.match(r'^ {0,3}(#{1,6})\s+(.+)$', line):
                processed_lines.append(line)
                i += 1
                continue

            # 跳过CSS/样式相关的行（以.开头且包含{的被认为是CSS选择器）
            if stripped.startswith('.') and '{' in stripped:
                # 这是CSS代码块 - 收集整个CSS块
                css_lines = [stripped]
                i += 1
                while i < len(lines):
                    next_stripped = lines[i].strip()
                    if next_stripped == '}':
                        css_lines.append(next_stripped)
                        i += 1
                        break
                    css_lines.append(next_stripped)
                    i += 1
                # 将CSS作为预格式化代码块显示
                css_content = '\n'.join(css_lines)
                escaped_css = escape_html(css_content)
                processed_lines.append(f'<pre style="background: #2d2d2d; color: #f8f8f2; padding: 10px; border-radius: 4px; overflow-x: auto; margin: 10px 0; font-family: monospace; font-size: 12px;"><code>{escaped_css}</code></pre>')
                continue

            processed_lines.append(line)
            i += 1

        lines = processed_lines
        result_lines = []
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 处理代码块（Triple backticks）
            code_block_match = re.match(r'^```(\w*)\s*$', stripped)
            if code_block_match:
                # 开始收集代码块内容
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith('```'):
                    code_lines.append(lines[i])
                    i += 1
                # 转义并添加代码块
                code_content = '\n'.join(code_lines)
                escaped_code = escape_html(code_content)
                result_lines.append(f'<pre style="background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; margin: 10px 0; font-family: monospace; font-size: 13px; border-left: 3px solid #0078D4;"><code>{escaped_code}</code></pre>')
                i += 1  # 跳过结束的```
                continue

            # 跳过空行
            if not stripped:
                result_lines.append('')
                i += 1
                continue

            # 跳过看起来像单独CSS属性的行（如: color: #fff; 或 margin: 0;）
            css_prop_match = re.match(r'^[\w-]+\s*:\s*[^;]+;?\s*$', stripped)
            if css_prop_match and not stripped.startswith('*'):
                # 作为代码显示
                result_lines.append(f'<pre style="background: #f0f0f0; padding: 5px; border-radius: 4px; margin: 5px 0; font-family: monospace; font-size: 12px;"><code>{escape_html(stripped)}</code></pre>')
                i += 1
                continue

            # 标题
            h3_match = re.match(r'^### (.+)$', stripped)
            if h3_match:
                content = escape_html(h3_match.group(1))
                result_lines.append(f'<h3 style="color: #374151; font-size: 16px; margin: 12px 0 8px;">{content}</h3>')
                i += 1
                continue

            h2_match = re.match(r'^## (.+)$', stripped)
            if h2_match:
                content = escape_html(h2_match.group(1))
                result_lines.append(f'<h2 style="color: #374151; font-size: 18px; margin: 15px 0 10px;">{content}</h2>')
                i += 1
                continue

            h1_match = re.match(r'^# (.+)$', stripped)
            if h1_match:
                content = escape_html(h1_match.group(1))
                result_lines.append(f'<h1 style="color: #374151; font-size: 20px; margin: 15px 0 10px;">{content}</h1>')
                i += 1
                continue

            # 处理无序列表项
            list_match = re.match(r'^[-*]\s+(.*)$', stripped)
            if list_match:
                content = self._format_inline_markdown_inspection(list_match.group(1))
                result_lines.append(f'<li style="margin: 5px 0;">{content}</li>')
                i += 1
                continue

            # 处理有序列表项
            ordered_match = re.match(r'^\d+\.\s+(.*)$', stripped)
            if ordered_match:
                content = self._format_inline_markdown_inspection(ordered_match.group(1))
                result_lines.append(f'<li style="margin: 5px 0;">{content}</li>')
                i += 1
                continue

            # 普通段落 - 转义HTML但保留markdown格式
            # 先检查是否包含未转义的HTML标签
            if re.search(r'<[a-zA-Z][^>]*>', stripped):
                formatted = self._format_inline_markdown_inspection(stripped)
                result_lines.append(f'<p style="margin: 8px 0; line-height: 1.8;">{formatted}</p>')
            else:
                formatted = self._format_inline_markdown_inspection(stripped)
                result_lines.append(f'<p style="margin: 8px 0; line-height: 1.8;">{formatted}</p>')
            i += 1

        return '\n'.join(result_lines)

    def _format_inline_markdown_inspection(self, text: str) -> str:
        """处理行内格式（粗体、斜体、代码）- 输入已经过HTML转义"""
        import re

        # 代码块（行内）
        def code_replace(m):
            return f'<code style="background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-family: monospace;">{m.group(1)}</code>'
        text = re.sub(r'`([^`]+)`', code_replace, text)

        # 粗体 **text** 或 __text__
        def bold_replace(m):
            return f'<strong style="font-weight: 600;">{m.group(1) or m.group(2)}</strong>'
        text = re.sub(r'\*\*(.+?)\*\*|__(.+?)__', bold_replace, text)

        # 斜体 *text* 或 _text_
        def italic_replace(m):
            return f'<em style="font-style: italic;">{m.group(1) or m.group(2)}</em>'
        text = re.sub(r'\*(.+?)\*|_(.+?)_', italic_replace, text)

        return text
    
    def on_export_ai_report(self):
        """导出AI美化巡检报告"""

        # 使用原始巡检输出进行AI美化
        raw_output = self.output_text.toPlainText().strip()
        if not raw_output:
            MessageBox("警告", "没有巡检数据可导出", self).exec()
            return

        # 加载AI配置
        api_config = self._load_ai_config()
        if not api_config:
            MessageBox("警告", "请先在AI大模型配置页面配置API", self).exec()
            return
        if not api_config.get('api_key'):
            MessageBox("警告", "请先在AI大模型配置页面配置API", self).exec()
            return

        # 格式化巡检数据
        from datetime import datetime
        product_name = getattr(self, 'product_name', '未知产品')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        formatted_data = f"产品: {product_name}\n"
        target = getattr(self, 'task_device', {})
        formatted_data += f'设备: {target.get("name", "")} ({target.get("host", "")})\n'
        formatted_data += f"检查时间: {timestamp}\n\n"
        formatted_data += "=== 原始巡检输出 ===\n"
        formatted_data += raw_output

        # 获取分析提示词
        prompt = self._get_ai_prompt()

        # 显示加载对话框
        self._export_cancelled = False
        self.loading_dialog = LoadingDialog("正在分析", self)
        self.loading_dialog.stop_requested.connect(self._on_stop_analysis)
        self.loading_dialog.show()
        QApplication.processEvents()

        # 创建工作线程
        self.export_worker = ExportReportWorker(api_config, formatted_data, prompt)
        self.export_worker.export_completed.connect(self._on_export_completed)
        self.export_worker.progress_updated.connect(self._on_progress_updated)
        self.export_worker.start()

    def _on_progress_updated(self, current, total, step_name):
        """进度更新回调"""
        if hasattr(self, 'loading_dialog') and self.loading_dialog:
            self.loading_dialog.setProgress(current, total, step_name)

    def _on_stop_analysis(self):
        """停止分析"""
        self._export_cancelled = True
        if hasattr(self, 'export_worker') and self.export_worker:
            self.export_worker.request_stop()
            try:
                self.export_worker.export_completed.disconnect(self._on_export_completed)
            except Exception:
                pass
            try:
                self.export_worker.progress_updated.disconnect(self._on_progress_updated)
            except Exception:
                pass
        if hasattr(self, 'loading_dialog') and self.loading_dialog:
            self.loading_dialog.close()
            self.loading_dialog = None

    def _on_export_completed(self, result: str, error_msg: str):
        """导出完成回调"""

        if getattr(self, '_export_cancelled', False):
            return

        try:
            if hasattr(self, 'loading_dialog') and self.loading_dialog:
                self.loading_dialog._allow_close_without_stop = True
                self.loading_dialog.close()
        except Exception as e:
            pass

        if error_msg:
            try:
                MessageBox("错误", error_msg, self).exec()
            except Exception as e:
                pass
            return

        if not result:
            try:
                MessageBox("错误", "AI返回为空，请稍后重试", self).exec()
            except Exception as e:
                pass
            return

        try:
            import re
            from datetime import datetime
            product_name = getattr(self, 'product_name', '未知产品')
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 生成文件
            timestamp_file = datetime.now().strftime("%Y%m%d_%H%M%S")
            default_filename = f"巡检报告_{product_name}_{timestamp_file}.html"

            file_path, _ = QFileDialog.getSaveFileName(
                self, "保存巡检报告",
                default_filename,
                "HTML 文件 (*.html);;所有文件 (*.*)"
            )

            if not file_path:
                return

            
            inspection_items = []
            for item_data in getattr(self, 'task_checks', None) or self._get_checked_inspection_items():
                name = item_data.get('Name', item_data.get('name', '未知'))
                output = self.builtin_outputs.get(name, self.custom_script_outputs.get(name, ''))
                inspection_items.append({'name': name, 'status': classify_output(output), 'output': output})

            html_content = self._generate_ai_report_html(result, product_name, timestamp, inspection_items)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            self._report_saved(file_path)

            MessageBox("成功", f"报告已保存到:\n{file_path}", self).exec()
            self._open_folder(file_path)

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            try:
                MessageBox("错误", f"生成报告失败: {str(e)}", self).exec()
            except Exception as me:
                pass


    def _extract_key_results(self, name: str, output: str, status: str) -> list:
        """从单个巡检项原始输出中提取真正适合列表展示的关键结果。"""
        import re

        item_name = str(name or '').replace('高风险', '异常').replace('中风险', '警告').strip()

        conclusion_lines = []
        capture_conclusion = False
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("【结论】"):
                capture_conclusion = True
                tail = line.replace("【结论】", "").strip()
                if tail:
                    conclusion_lines.append(tail)
                continue
            if capture_conclusion:
                if line.startswith("【") and line.endswith("】"):
                    break
                conclusion_lines.append(line)
                if len(conclusion_lines) >= 4:
                    break
        if conclusion_lines:
            return conclusion_lines[:4]

        lines = []
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = line.replace("高风险", "异常").replace("中风险", "警告")
            if set(line) <= set("=-_*# "):
                continue
            if line.startswith("[DEBUG") or line.startswith("DEBUG"):
                continue

            line = re.sub(r'^[\s📋✅⚠️🔴🟢🟡•\-]+', '', line).strip()
            if not line:
                continue

            # 这些行只是模板标签，不应作为列表摘要展示。
            label_only_patterns = [
                r'^巡检项\s*[:：]',
                r'^检查项\s*[:：]',
                r'^命令执行结果\s*[:：]?$',
                r'^原始输出\s*[:：]?$',
                r'^详细输出\s*[:：]?$',
                r'^Filesystem\s+',
                r'^文件系统\s+',
                r'^USER\s+PID\s+',
                r'^PID\s+',
            ]
            if any(re.search(pattern, line, re.IGNORECASE) for pattern in label_only_patterns):
                continue

            if item_name and line in {item_name, f'巡检项: {item_name}', f'巡检项：{item_name}'}:
                continue

            if len(line) > 180:
                line = line[:180] + "..."
            lines.append(line)

        # 对“主机名/系统版本”等单值巡检，优先取真正结果值，而不是说明文字。
        plain_value_names = ['\u4e3b\u673a\u540d', '\u7cfb\u7edf\u7248\u672c', '\u64cd\u4f5c\u7cfb\u7edf', '\u5185\u6838\u7248\u672c', '\u7cfb\u7edf\u67b6\u6784']
        if any(key in item_name for key in plain_value_names):
            for line in lines:
                named_value = re.search(r'^(?:\u4e3b\u673a\u540d|\u7cfb\u7edf\u7248\u672c|\u64cd\u4f5c\u7cfb\u7edf|\u5185\u6838\u7248\u672c|\u7cfb\u7edf\u67b6\u6784)\s*(?:\u4e3a|\u662f|[:\uff1a])\s*(.+)$', line)
                if named_value:
                    return [named_value.group(1).strip()]
                if ':' not in line and '\uff1a' not in line and len(line) <= 120:
                    return [line]
            if lines:
                value_match = re.search(r'[:\uff1a]\s*(.+)$', lines[0])
                if value_match:
                    return [value_match.group(1).strip()]

        status_lines = [
            line for line in lines
            if re.search(r'^(状态|结论|结果|检测结果)\s*[:：]', line) or any(token in line for token in ["正常", "异常", "警告", "失败"])
        ]
        if status_lines:
            return status_lines[:4]

        if any(key in item_name for key in ["登录用户", "登陆用户"]):
            users = []
            for line in lines:
                parts = line.split()
                if parts:
                    users.append(parts[0])
            users = list(dict.fromkeys(users))
            if users:
                return [f"当前登录用户 {len(users)} 个：{', '.join(users[:8])}"]
            return ["当前无登录用户记录"]

        if any(key in item_name for key in ["登录记录", "登陆记录"]):
            valid_lines = [line for line in lines if line and not line.lower().startswith(("wtmp", "reboot", "shutdown"))]
            if valid_lines:
                return [f"最近登录记录 {len(valid_lines)} 条，最新记录：{valid_lines[0][:120]}"]
            return ["未获取到登录记录"]

        priority_keywords = [
            "异常", "错误", "失败", "故障", "警告", "风险", "error", "failed", "warning",
            "load average", "Mem:", "Swap:", "Filesystem", "%", "running", "exited", "inactive",
            "active", "uptime", "NAME", "STATUS", "USER", "PID", "CPU", "MEM"
        ]
        picked = []
        for line in lines:
            if any(kw.lower() in line.lower() for kw in priority_keywords):
                picked.append(line)
            if len(picked) >= 4:
                break

        if not picked and lines:
            picked = [f"已获取巡检结果，共 {len(lines)} 行，详情中查看命令执行结果"]

        if not picked:
            status_text = {
                "normal": "本项未达到默认告警阈值",
                "error": "未获取到有效输出", "warning": "输出为空或无法解析",
                "unknown": "未获取到有效输出，无法判定",
                "info": "仅采集信息，需结合业务基线核对",
                "not_applicable": "当前环境不适用此项检查",
            }.get(status, "未获取到有效输出，无法判定")
            picked = [status_text]

        return picked

    @staticmethod
    def _baseline_process_rows(output: str, metric: str):
        """Parse the bounded ps table by header, retaining names that contain spaces."""
        field = '%CPU' if metric == 'cpu' else '%MEM'
        columns = None
        rows = []
        for line in str(output or '').splitlines():
            parts = line.split()
            if 'PID' in parts and field in parts and 'COMMAND' in parts and 'PPID' in parts:
                columns = parts
                continue
            if columns is None:
                continue
            parts = line.split(None, len(columns) - 1)
            if len(parts) != len(columns) or not parts[0].isdigit():
                continue
            try:
                value = float(parts[columns.index(field)])
            except ValueError:
                continue
            if value >= 0:
                rows.append({'process': parts[columns.index('COMMAND')][:48], metric: value})
        if columns is None:
            return None
        return sorted(rows, key=lambda row: row[metric], reverse=True)[:10]

    def _extract_cpu_top_rows(self, name: str, output: str) -> list:
        """提取CPU占用TOP进程，返回[{process, cpu}]。"""
        import os
        import re
        name_text = str(name or "").lower()
        raw_name = str(name or "")
        if "内存" in raw_name or "mem" in name_text:
            return []
        if not ("top" in name_text or "进程" in raw_name):
            return []
        if "每颗" in raw_name or "每核" in raw_name:
            return []

        baseline = self._baseline_process_rows(output, 'cpu')
        if baseline is not None:
            return baseline

        rows = []
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if not line or line.lower().startswith(("user ", "pid ", "%cpu", "top -", "tasks:", "%cpu")):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue

            cpu = None
            cmd = ""

            # ps aux: USER PID %CPU %MEM ... COMMAND
            if len(parts) >= 11 and re.match(r"^\d+(\.\d+)?$", parts[2]):
                cpu = parts[2]
                cmd = parts[10]
            # ps -eo pid,comm,%cpu: PID COMMAND %CPU
            elif len(parts) >= 3 and re.match(r"^\d+(\.\d+)?$", parts[-1]):
                cpu = parts[-1]
                cmd = parts[-2]
            # top batch: PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND
            elif len(parts) >= 12 and re.match(r"^\d+(\.\d+)?$", parts[8]):
                cpu = parts[8]
                cmd = parts[11]

            if cpu is None:
                continue

            try:
                cpu_value = float(cpu)
            except Exception:
                continue

            process = os.path.basename(cmd.strip()) or cmd.strip()
            process = process[:48]
            rows.append({"process": process, "cpu": cpu_value})

        rows.sort(key=lambda row: row["cpu"], reverse=True)
        return rows[:10]

    def _extract_cpu_usage_summary(self, name: str, output: str) -> dict:
        """提取整体CPU使用率摘要，适用于每颗CPU使用率等非进程TOP项。"""
        import re
        raw_name = str(name or "")
        if not ("每颗CPU" in raw_name or "CPU使用率" in raw_name):
            return {}
        if "TOP" in raw_name.upper() or "进程" in raw_name:
            return {}

        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if "%Cpu" not in line and "%cpu" not in line.lower():
                continue
            values = {}
            for key in ("us", "sy", "ni", "id", "wa", "hi", "si", "st"):
                match = re.search(rf"([0-9]+(?:\.[0-9]+)?)\s*{key}\b", line, re.IGNORECASE)
                if match:
                    values[key] = float(match.group(1))
            if values:
                idle = values.get("id", 0.0)
                used = max(0.0, min(100.0, 100.0 - idle))
                return {"used": used, **values}
        return {}

    def _extract_memory_top_rows(self, name: str, output: str) -> list:
        """提取内存占用TOP进程，返回[{process, mem}]。"""
        import os
        import re
        name_text = str(name or "").lower()
        if not any(key in name_text for key in ["内存", "mem"]):
            return []

        baseline = self._baseline_process_rows(output, 'mem')
        if baseline is not None:
            return baseline

        rows = []
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            lower = line.lower()
            if not line or lower.startswith(("user ", "pid ", "%mem", "top -", "tasks:")):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue

            mem = None
            cmd = ""
            if len(parts) >= 11 and re.match(r"^\d+(\.\d+)?$", parts[3]):
                mem = parts[3]
                cmd = parts[10]
            elif len(parts) >= 3 and re.match(r"^\d+(\.\d+)?$", parts[-1]):
                mem = parts[-1]
                cmd = parts[-2]

            if mem is None:
                continue
            try:
                mem_value = float(mem)
            except Exception:
                continue
            process = os.path.basename(cmd.strip()) or cmd.strip()
            rows.append({"process": process[:48], "mem": mem_value})

        rows.sort(key=lambda row: row["mem"], reverse=True)
        return rows[:10]

    def _extract_memory_usage_summary(self, name: str, output: str) -> dict:
        """提取内存总体使用情况。"""
        import re
        raw_name = str(name or "")
        if "内存使用情况" not in raw_name:
            return {}

        total = used = pct = None
        for line in (output or "").splitlines():
            total_match = re.search(r"总内存\s*[:：]\s*([0-9.]+)\s*([A-Za-z]+)?", line)
            used_match = re.search(r"已使用\s*[:：]\s*([0-9.]+)\s*([A-Za-z]+)?(?:\s*\(([0-9.]+)%\))?", line)
            if total_match:
                total = f"{total_match.group(1)}{total_match.group(2) or ''}"
            if used_match:
                used = f"{used_match.group(1)}{used_match.group(2) or ''}"
                if used_match.group(3):
                    pct = float(used_match.group(3))
            if line.strip().startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 3:
                    total = total or parts[1]
                    used = used or parts[2]
                    try:
                        pct = float(parts[2]) / float(parts[1]) * 100
                    except Exception:
                        pass
        if total or used or pct is not None:
            return {"total": total or "未知", "used": used or "未知", "pct": pct}
        return {}

    def _extract_swap_summary(self, name: str, output: str) -> dict:
        """提取交换分区Swap行。"""
        if "交换分区" not in str(name or "") and "swap" not in str(name or "").lower():
            return {}
        for line in (output or "").splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("swap:"):
                parts = stripped.split()
                return {
                    "raw": stripped,
                    "total": parts[1] if len(parts) > 1 else "未知",
                    "used": parts[2] if len(parts) > 2 else "未知",
                    "free": parts[3] if len(parts) > 3 else "未知",
                }
        return {}

    def _extract_disk_usage_rows(self, name: str, output: str) -> list:
        """提取df -h磁盘使用率结果。"""
        if "磁盘使用率" not in str(name or ""):
            return []
        rows = []
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("📋") or line.startswith("="):
                continue
            lower = line.lower()
            if lower.startswith("filesystem") or line.startswith("文件系统"):
                continue
            parts = line.split()
            if len(parts) >= 6 and parts[-2].endswith("%"):
                rows.append({
                    "filesystem": parts[0],
                    "size": parts[1],
                    "used": parts[2],
                    "avail": parts[3],
                    "use": parts[4],
                    "mount": " ".join(parts[5:]),
                })
        return rows

    def _extract_process_runtime_rows(self, name: str, output: str) -> list:
        """提取进程运行时间检查结果，避免摘要中展示超长命令行。"""
        import os
        import re
        if "进程运行时间" not in str(name or ""):
            return []
        rows = []
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("📋") or line.lower().startswith("pid "):
                continue
            parts = line.split(None, 3)
            if len(parts) < 4 or not re.match(r"^\d+$", parts[0]):
                continue
            command = parts[3].strip()
            process = os.path.basename(command.split()[0]) if command else ""
            rows.append({
                "pid": parts[0],
                "etime": parts[1],
                "time": parts[2],
                "process": (process or command)[:56],
            })
        def etime_seconds(value: str) -> int:
            days = 0
            rest = value
            if "-" in value:
                day_text, rest = value.split("-", 1)
                try:
                    days = int(day_text)
                except Exception:
                    days = 0
            parts = [int(p) for p in rest.split(":") if p.isdigit()]
            if len(parts) == 3:
                h, m, s = parts
            elif len(parts) == 2:
                h, m, s = 0, parts[0], parts[1]
            else:
                h, m, s = 0, 0, parts[0] if parts else 0
            return days * 86400 + h * 3600 + m * 60 + s

        rows.sort(key=lambda row: etime_seconds(row["etime"]), reverse=True)
        return rows[:10]

    def _extract_unmounted_disk_summary(self, name: str, output: str) -> list:
        """提取未挂载磁盘结论。"""
        if "未挂载磁盘" not in str(name or ""):
            return []
        unmounted = []
        mountable_types = {"part", "lvm", "crypt", "raid0", "raid1", "raid5", "raid6", "raid10"}
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("NAME") or line.startswith("📋"):
                continue
            parts = line.split()
            if len(parts) >= 3:
                disk_name = parts[0].lstrip("├─└─`|- ")
                if not disk_name:
                    continue
                disk_type = parts[2]
                mountpoint = parts[3] if len(parts) >= 4 else ""
                if disk_type in mountable_types and not mountpoint:
                    unmounted.append(f"{disk_name} ({parts[1]})")
        if unmounted:
            return [f"发现未挂载磁盘：{', '.join(unmounted)}"]
        return ["无未挂载磁盘"]

    def _extract_system_time_rows(self, name: str, output: str) -> list:
        """提取系统时间和标准时间等关键信息。"""
        if "系统时间" not in str(name or ""):
            return []
        rows = []
        wanted = ("系统时间", "标准时间", "UTC", "时差", "Time zone", "System clock synchronized", "NTP service")
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if any(key in line for key in wanted):
                rows.append(line)
        return rows[:8]

    def _parse_docker_container_rows(self, name: str, output: str) -> list:
        """解析 docker ps -a 输出。"""
        import re
        if "Docker容器" not in str(name or ""):
            return []
        rows = []
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("CONTAINER ID") or line.startswith("📋"):
                continue
            if any(text in line.lower() for text in ("command not found", "permission denied", "cannot connect", "permission denied")):
                continue
            parts = re.split(r"\s{2,}", line)
            if len(parts) < 5:
                continue
            if len(parts) >= 7:
                container_id, image, command, created, status_text, ports, name_text = parts[:7]
            else:
                container_id = parts[0]
                image = parts[1] if len(parts) > 1 else ""
                command = parts[2] if len(parts) > 2 else ""
                created = parts[3] if len(parts) > 3 else ""
                status_text = parts[4] if len(parts) > 4 else ""
                ports = ""
                name_text = parts[5] if len(parts) > 5 else container_id
            lower_status = status_text.lower()
            if lower_status.startswith("up"):
                state = "运行中"
            elif lower_status.startswith("exited"):
                state = "已退出"
            elif lower_status.startswith("restarting"):
                state = "重启中"
            elif lower_status.startswith("paused"):
                state = "已暂停"
            elif lower_status.startswith("created"):
                state = "已创建未运行"
            elif "dead" in lower_status:
                state = "异常"
            else:
                state = status_text or "未知"
            rows.append({
                "id": container_id,
                "image": image,
                "command": command.strip('"'),
                "created": created,
                "status": status_text,
                "state": state,
                "ports": ports,
                "name": name_text,
            })
        return rows

    def _parse_docker_image_rows(self, name: str, output: str) -> list:
        """解析 docker images 输出。"""
        import re
        if "Docker镜像" not in str(name or ""):
            return []
        ansi_re = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
        rows = []
        for raw_line in (output or "").splitlines():
            line = ansi_re.sub('', raw_line).strip()
            if not line or line.startswith("📋"):
                continue
            upper_line = line.upper()
            if upper_line.startswith("REPOSITORY") or upper_line.startswith("IMAGE ID") or upper_line.startswith("IMAGE "):
                continue
            if any(text in line.lower() for text in ("command not found", "permission denied", "cannot connect")):
                continue
            parts = re.split(r"\s{2,}", line)
            if len(parts) < 5:
                continue

            # 兼容 nerdctl/docker system df 等输出:
            # IMAGE  ID  DISK USAGE  CONTENT SIZE  EXTRA
            if ":" in parts[0] and not re.fullmatch(r"[0-9a-f]{12,64}", parts[0], re.IGNORECASE):
                repository, tag = parts[0].rsplit(":", 1)
                rows.append({
                    "repository": repository or parts[0],
                    "tag": tag or "latest",
                    "id": parts[1],
                    "created": parts[4] if len(parts) > 4 else "",
                    "size": parts[2],
                })
                continue

            rows.append({
                "repository": parts[0],
                "tag": parts[1],
                "id": parts[2],
                "created": parts[3],
                "size": parts[4],
            })
        return rows

    def _format_detail_output_for_report(self, name: str, output: str) -> str:
        """为HTML报告详情区整理输出，表格类按关键字段排序。"""
        docker_containers = self._parse_docker_container_rows(name, output)
        if docker_containers:
            lines = ["容器名\t镜像\t状态\t运行状态\t创建时间"]
            lines.extend(
                f"{row['name']}\t{row['image']}\t{row['status']}\t{row['state']}\t{row['created']}"
                for row in docker_containers
            )
            return "\n".join(lines)

        docker_images = self._parse_docker_image_rows(name, output)
        if docker_images:
            lines = ["镜像仓库\t标签\t镜像ID\t创建时间\t大小"]
            lines.extend(
                f"{row['repository']}\t{row['tag']}\t{row['id']}\t{row['created']}\t{row['size']}"
                for row in docker_images[:30]
            )
            return "\n".join(lines)

        time_rows = self._extract_system_time_rows(name, output)
        if time_rows:
            return "\n".join(time_rows)

        cpu_usage = self._extract_cpu_usage_summary(name, output)
        if cpu_usage:
            lines = ["指标\t百分比"]
            for label, key in (("用户态", "us"), ("系统态", "sy"), ("空闲", "id"), ("IO等待", "wa"), ("软中断", "si"), ("整体使用率", "used")):
                value = cpu_usage.get(key)
                if value is not None:
                    lines.append(f"{label}\t{value:.1f}%")
            return "\n".join(lines)

        cpu_rows = self._extract_cpu_top_rows(name, output)
        if cpu_rows:
            lines = ["序号\t进程名\tCPU占用"]
            lines.extend(
                f"{idx}\t{row['process']}\t{row['cpu']:.1f}%"
                for idx, row in enumerate(cpu_rows, 1)
            )
            return "\n".join(lines)

        memory_rows = self._extract_memory_top_rows(name, output)
        if memory_rows:
            lines = ["序号\t进程名\t内存占用"]
            lines.extend(
                f"{idx}\t{row['process']}\t{row['mem']:.1f}%"
                for idx, row in enumerate(memory_rows, 1)
            )
            return "\n".join(lines)

        swap_summary = self._extract_swap_summary(name, output)
        if swap_summary:
            return "\n".join([
                "类型\t总量\t已用\t可用",
                f"Swap\t{swap_summary['total']}\t{swap_summary['used']}\t{swap_summary['free']}",
            ])

        unmounted_summary = self._extract_unmounted_disk_summary(name, output)
        if unmounted_summary:
            return "\n".join(unmounted_summary)

        if "磁盘使用率" in str(name or ""):
            rows = self._extract_disk_usage_rows(name, output)
            if rows:
                def disk_percent(row):
                    try:
                        return int(str(row.get("use", "0")).rstrip("%"))
                    except Exception:
                        return 0
                rows = sorted(rows, key=disk_percent, reverse=True)
                lines = ["Filesystem\tSize\tUsed\tAvail\tUse%\tMounted on"]
                lines.extend(
                    f"{row['filesystem']}\t{row['size']}\t{row['used']}\t{row['avail']}\t{row['use']}\t{row['mount']}"
                    for row in rows
                )
                return "\n".join(lines)

        if "进程运行时间" in str(name or ""):
            rows = self._extract_process_runtime_rows(name, output)
            if rows:
                lines = ["PID\tELAPSED\tCPU_TIME\tPROCESS"]
                lines.extend(
                    f"{row['pid']}\t{row['etime']}\t{row['time']}\t{row['process']}"
                    for row in rows
                )
                return "\n".join(lines)

        return output

    def _build_key_result_html(self, name: str, output: str, status: str) -> tuple:
        """生成概要文本和关键结果HTML。"""
        import html as _html
        if not str(output or '').strip():
            summary = '未获取到有效输出，无法判定'
            return summary, f'<ul class="key-result-list"><li>{summary}</li></ul>'
        docker_containers = self._parse_docker_container_rows(name, output)
        if docker_containers:
            running = sum(1 for row in docker_containers if row["state"] == "运行中")
            exited = sum(1 for row in docker_containers if row["state"] == "已退出")
            restarting = sum(1 for row in docker_containers if row["state"] in ("重启中", "异常"))
            paused = sum(1 for row in docker_containers if row["state"] == "已暂停")
            created = sum(1 for row in docker_containers if row["state"] == "已创建未运行")
            total = len(docker_containers)
            if "运行时间" in str(name or ""):
                if restarting:
                    summary = f"共 {total} 个容器，{restarting} 个存在重启中或异常状态，建议优先核对。"
                elif exited or paused or created:
                    summary = f"共 {total} 个容器，运行中 {running} 个，非运行状态 {exited + paused + created} 个。"
                else:
                    summary = f"共 {total} 个容器，全部处于运行中，未发现明显运行时间异常。"
            else:
                summary = f"共 {total} 个容器，运行中 {running} 个，已退出 {exited} 个，重启中/异常 {restarting} 个。"
            table_rows = ''.join(
                f"<tr><td>{idx}</td><td>{_html.escape(row['name'])}</td><td>{_html.escape(row['image'])}</td><td>{_html.escape(row['state'])}</td><td>{_html.escape(row['status'])}</td></tr>"
                for idx, row in enumerate(docker_containers, 1)
            )
            return summary, f'''
                <table class="key-result-table">
                    <thead><tr><th>序号</th><th>容器名</th><th>镜像</th><th>运行状态</th><th>状态详情</th></tr></thead>
                    <tbody>{table_rows}</tbody>
                </table>
            '''

        if "Docker容器" in str(name or ""):
            output_text = str(output or "").strip()
            if not output_text or output_text.startswith("CONTAINER ID"):
                return "未发现 Docker 容器。", '<ul class="key-result-list"><li>未发现 Docker 容器</li></ul>'

        docker_images = self._parse_docker_image_rows(name, output)
        if docker_images:
            total = len(docker_images)
            dangling = sum(1 for row in docker_images if row["repository"] == "<none>" or row["tag"] == "<none>")
            repositories = len({row["repository"] for row in docker_images if row["repository"] != "<none>"})
            summary = f"共 {total} 个 Docker 镜像，涉及 {repositories} 个镜像仓库，悬空镜像 {dangling} 个。"
            table_rows = ''.join(
                f"<tr><td>{idx}</td><td>{_html.escape(row['repository'])}</td><td>{_html.escape(row['tag'])}</td><td>{_html.escape(row['id'])}</td><td>{_html.escape(row['size'])}</td></tr>"
                for idx, row in enumerate(docker_images[:30], 1)
            )
            return summary, f'''
                <table class="key-result-table">
                    <thead><tr><th>序号</th><th>镜像仓库</th><th>标签</th><th>镜像ID</th><th>大小</th></tr></thead>
                    <tbody>{table_rows}</tbody>
                </table>
            '''

        if "Docker镜像" in str(name or ""):
            output_text = str(output or "").strip()
            if not output_text or output_text.startswith("REPOSITORY"):
                return "未发现 Docker 镜像。", '<ul class="key-result-list"><li>未发现 Docker 镜像</li></ul>'

        memory_usage = self._extract_memory_usage_summary(name, output)
        if memory_usage:
            pct = memory_usage.get("pct")
            if pct is None:
                level = "状态需结合详情判断"
                pct_text = "未知"
            elif pct >= 90:
                level = "异常"
                pct_text = f"{pct:.1f}%"
            elif pct > 70:
                level = "警告"
                pct_text = f"{pct:.1f}%"
            else:
                level = "正常"
                pct_text = f"{pct:.1f}%"
            summary = f"内存使用率 {pct_text}，状态：{level}"
            return summary, f'''
                <ul class="key-result-list">
                    <li>总内存：{_html.escape(memory_usage.get("total", "未知"))}</li>
                    <li>已使用：{_html.escape(memory_usage.get("used", "未知"))}</li>
                    <li>评判标准：大于70%属于警告，大于90%属于异常</li>
                </ul>
            '''

        swap_summary = self._extract_swap_summary(name, output)
        if swap_summary:
            summary = f"Swap 总量 {swap_summary['total']}，已使用 {swap_summary['used']}，可用 {swap_summary['free']}"
            return summary, f'''
                <table class="key-result-table">
                    <thead><tr><th>类型</th><th>总量</th><th>已用</th><th>可用</th></tr></thead>
                    <tbody><tr><td>Swap</td><td>{_html.escape(swap_summary['total'])}</td><td>{_html.escape(swap_summary['used'])}</td><td>{_html.escape(swap_summary['free'])}</td></tr></tbody>
                </table>
            '''

        unmounted_summary = self._extract_unmounted_disk_summary(name, output)
        if unmounted_summary:
            summary = unmounted_summary[0]
            key_list = ''.join(f'<li>{_html.escape(result)}</li>' for result in unmounted_summary)
            return summary, f'<ul class="key-result-list">{key_list}</ul>'

        cpu_usage = self._extract_cpu_usage_summary(name, output)
        if cpu_usage:
            summary = f"CPU使用率 {cpu_usage['used']:.1f}%"
            rows = [
                ("用户态", cpu_usage.get("us")),
                ("系统态", cpu_usage.get("sy")),
                ("空闲", cpu_usage.get("id")),
                ("IO等待", cpu_usage.get("wa")),
                ("软中断", cpu_usage.get("si")),
            ]
            table_rows = ''.join(
                f"<tr><td>{label}</td><td>{value:.1f}%</td></tr>"
                for label, value in rows if value is not None
            )
            return summary, f'''
                <table class="key-result-table">
                    <thead><tr><th>指标</th><th>百分比</th></tr></thead>
                    <tbody>{table_rows}</tbody>
                </table>
            '''

        time_rows = self._extract_system_time_rows(name, output)
        if time_rows:
            server_time = next((row for row in time_rows if row.startswith("系统时间")), "")
            standard_time = next((row for row in time_rows if row.startswith("标准时间")), "")
            diff_time = next((row for row in time_rows if row.startswith("时差")), "")
            if server_time and standard_time:
                summary = f"{server_time}，{standard_time}"
                if diff_time:
                    summary += f"，{diff_time}"
            else:
                summary = "；".join(time_rows[:2])
            key_list = ''.join(f'<li>{_html.escape(row)}</li>' for row in time_rows)
            return summary, f'<ul class="key-result-list">{key_list}</ul>'

        disk_rows = self._extract_disk_usage_rows(name, output)
        if disk_rows:
            def disk_percent(row):
                try:
                    return int(str(row.get("use", "0")).rstrip("%"))
                except Exception:
                    return 0
            max_row = max(disk_rows, key=disk_percent)
            disk_rows = sorted(disk_rows, key=disk_percent, reverse=True)
            summary = f"共发现 {len(disk_rows)} 个磁盘挂载项，最高使用率 {max_row['use']}（{max_row['mount']}）"
            table_rows = ''.join(
                f"<tr><td>{_html.escape(row['filesystem'])}</td><td>{_html.escape(row['size'])}</td><td>{_html.escape(row['used'])}</td><td>{_html.escape(row['avail'])}</td><td>{_html.escape(row['use'])}</td><td>{_html.escape(row['mount'])}</td></tr>"
                for row in disk_rows
            )
            return summary, f'''
                <table class="key-result-table">
                    <thead><tr><th>文件系统</th><th>容量</th><th>已用</th><th>可用</th><th>使用率</th><th>挂载点</th></tr></thead>
                    <tbody>{table_rows}</tbody>
                </table>
            '''

        memory_rows = self._extract_memory_top_rows(name, output)
        if memory_rows:
            summary = f"内存占用最高进程：{memory_rows[0]['process']} ({memory_rows[0]['mem']:.1f}%)"
            table_rows = ''.join(
                f"<tr><td>{idx}</td><td>{_html.escape(row['process'])}</td><td>{row['mem']:.1f}%</td></tr>"
                for idx, row in enumerate(memory_rows, 1)
            )
            return summary, f'''
                <table class="key-result-table">
                    <thead><tr><th>序号</th><th>进程名</th><th>内存占用</th></tr></thead>
                    <tbody>{table_rows}</tbody>
                </table>
            '''

        runtime_rows = self._extract_process_runtime_rows(name, output)
        if runtime_rows:
            longest = runtime_rows[0]["etime"]
            summary = f"运行时间最长进程已运行 {longest}，未发现明确异常，详细命令见展开内容"
            table_rows = ''.join(
                f"<tr><td>{_html.escape(row['pid'])}</td><td>{_html.escape(row['etime'])}</td><td>{_html.escape(row['time'])}</td><td>{_html.escape(row['process'])}</td></tr>"
                for row in runtime_rows
            )
            return summary, f'''
                <table class="key-result-table">
                    <thead><tr><th>PID</th><th>运行时长</th><th>CPU时间</th><th>进程名</th></tr></thead>
                    <tbody>{table_rows}</tbody>
                </table>
            '''

        cpu_rows = self._extract_cpu_top_rows(name, output)
        if cpu_rows:
            summary = f"CPU占用最高进程：{cpu_rows[0]['process']} ({cpu_rows[0]['cpu']:.1f}%)"
            table_rows = ''.join(
                f"<tr><td>{idx}</td><td>{_html.escape(row['process'])}</td><td>{row['cpu']:.1f}%</td></tr>"
                for idx, row in enumerate(cpu_rows, 1)
            )
            return summary, f'''
                <table class="key-result-table">
                    <thead><tr><th>序号</th><th>进程名</th><th>CPU占用</th></tr></thead>
                    <tbody>{table_rows}</tbody>
                </table>
            '''

        key_results = self._extract_key_results(name, output, status)
        summary = key_results[0] if key_results else "未获取到关键结果"
        if len(summary) > 120:
            summary = summary[:120] + "..."
        key_list = ''.join(f'<li>{_html.escape(result)}</li>' for result in key_results)
        return summary, f'<ul class="key-result-list">{key_list}</ul>'

    def _sanitize_ai_advice_html(self, html_content: str) -> str:
        """过滤 AI 建议中的敏感操作，保留低风险分析说明。"""
        import re
        sensitive_patterns = [
            r"rm\s+-rf", r"mkfs\b", r"dd\s+if=", r":\(\)\s*\{", r"chmod\s+777",
            r"chown\s+-R", r"iptables\s+-F", r"firewall-cmd\s+--permanent\s+--remove",
            r"\breboot\b", r"\bshutdown\b", r"systemctl\s+(stop|restart|disable)",
            r"service\s+\S+\s+(stop|restart)", r"kill\s+-9", r"truncate\s+-s\s+0",
        ]
        sensitive_re = re.compile("|".join(sensitive_patterns), re.IGNORECASE)

        def replace_li_if_sensitive(match):
            text = match.group(0)
            if sensitive_re.search(text):
                return '<li class="safe-advice">涉及敏感操作的建议已隐藏，请先备份并在变更窗口由管理员确认后处理。</li>'
            return text

        def replace_p_if_sensitive(match):
            text = match.group(0)
            if sensitive_re.search(text):
                return '<p class="safe-advice">涉及敏感操作的建议已隐藏，请先备份并在变更窗口由管理员确认后处理。</p>'
            return text

        def replace_tr_if_sensitive(match):
            text = match.group(0)
            if sensitive_re.search(text):
                return '<tr><td colspan="6" class="safe-advice">涉及敏感操作的建议已隐藏，请先备份并在变更窗口由管理员确认后处理。</td></tr>'
            return text

        html_content = re.sub(r"<li\b[^>]*>.*?</li>", replace_li_if_sensitive, html_content, flags=re.DOTALL | re.IGNORECASE)
        html_content = re.sub(r"<p\b[^>]*>.*?</p>", replace_p_if_sensitive, html_content, flags=re.DOTALL | re.IGNORECASE)
        html_content = re.sub(r"<tr\b[^>]*>.*?</tr>", replace_tr_if_sensitive, html_content, flags=re.DOTALL | re.IGNORECASE)
        return html_content

    def _extract_ai_analysis_fragment(self, html_content: str) -> str:
        """从 AI 返回内容中提取分析建议片段，避免把完整报告嵌入模板。"""
        import re
        import html as _html

        content = (html_content or '').strip()
        if not content:
            return ''

        # 有些模型会把HTML片段整体转义，导致报告中直接露出大量<li>标签。
        for _ in range(2):
            if any(token in content for token in ('&lt;li', '&lt;/li&gt;', '&lt;ul', '&lt;/ul&gt;', '&#60;li')):
                content = _html.unescape(content)
            else:
                break

        heading_patterns = [
            r'<h[1-6][^>]*>\s*(?:🤖\s*)?AI\s*分析与安全建议\s*</h[1-6]>([\s\S]*)',
            r'<h[1-6][^>]*>\s*(?:🤖\s*)?AI\s*分析\s*</h[1-6]>([\s\S]*)',
            r'<section[^>]*>\s*(?:AI\s*)?分析与安全建议[:：]?\s*([\s\S]*?)</section>',
        ]
        for pattern in heading_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                content = match.group(1).strip()
                break

        duplicate_block_patterns = [
            r'<header[\s\S]*?</header>',
            r'<footer[\s\S]*?</footer>',
            r'<div[^>]*class="[^"]*(?:hero|meta|stats|stat|summary|footer)[^"]*"[^>]*>[\s\S]*?</div>',
            r'<h[1-6][^>]*>\s*(?:系统)?巡检报告\s*</h[1-6]>',
            r'<h[1-6][^>]*>\s*(?:巡检结果|巡检项关键结果|巡检概要|关键异常与警告|关键异常|统计概览|检查项统计)\s*</h[1-6]>[\s\S]*?(?=<h[1-6][^>]*>|$)',
            r'<ol[^>]*class="[^"]*result-list[^"]*"[^>]*>[\s\S]*?</ol>',
            r'<table[^>]*class="[^"]*(?:issues-table|stats-table)[^"]*"[^>]*>[\s\S]*?</table>',
        ]
        for pattern in duplicate_block_patterns:
            content = re.sub(pattern, '', content, flags=re.IGNORECASE)

        content = re.sub(r'</?(?:main|article|section)[^>]*>', '', content, flags=re.IGNORECASE)
        content = re.sub(r'<div[^>]*class="[^"]*(?:page|panel|container|report-card|report-body)[^"]*"[^>]*>', '', content, flags=re.IGNORECASE)
        content = re.sub(r'</div>\s*$', '', content, flags=re.IGNORECASE).strip()
        content = self._repair_ai_list_html(content)
        return content

    def _repair_ai_list_html(self, html_content: str) -> str:
        """修复AI片段中的列表标签，避免<li>裸露或残缺。"""
        import re
        import html as _html

        content = (html_content or '').strip()
        if not content:
            return ''

        for _ in range(2):
            if any(token in content for token in ('&lt;li', '&lt;/li&gt;', '&lt;ul', '&lt;/ul&gt;', '&#60;li')):
                content = _html.unescape(content)

        li_count = len(re.findall(r'<li\b', content, flags=re.IGNORECASE))
        ul_count = len(re.findall(r'<ul\b|<ol\b', content, flags=re.IGNORECASE))
        if li_count and not ul_count:
            content = f'<ul>{content}</ul>'

        content = re.sub(r'<li\b([^>]*)>\s*', r'<li\1>', content, flags=re.IGNORECASE)
        content = re.sub(r'\s*</li>', '</li>', content, flags=re.IGNORECASE)
        content = re.sub(r'(</li>)(?=<li\b)', r'\1\n', content, flags=re.IGNORECASE)
        content = re.sub(r'<li\b[^>]*>\s*</li>', '', content, flags=re.IGNORECASE)

        open_ul = len(re.findall(r'<ul\b', content, flags=re.IGNORECASE))
        close_ul = len(re.findall(r'</ul>', content, flags=re.IGNORECASE))
        if open_ul > close_ul:
            content += '</ul>' * (open_ul - close_ul)

        open_ol = len(re.findall(r'<ol\b', content, flags=re.IGNORECASE))
        close_ol = len(re.findall(r'</ol>', content, flags=re.IGNORECASE))
        if open_ol > close_ol:
            content += '</ol>' * (open_ol - close_ol)

        return content

    def _generate_ai_report_html(self, ai_result: str, product: str, timestamp: str, inspection_items: list = None) -> str:
        import re
        from datetime import datetime
        from html import escape
        target = getattr(self, 'task_device', {})
        target_meta = escape(f'{target.get("name") or ""} · {target.get("host") or ""}:{target.get("port", 22)}') if target else '未绑定设备'
        
        inspection_items = inspection_items or []
        total_items = len(inspection_items)
        error_items = [i for i in inspection_items if i.get('status') == 'error']
        warning_items = [i for i in inspection_items if i.get('status') == 'warning']
        normal_items = [i for i in inspection_items if i.get('status') == 'normal']
        
        issue_count = len(error_items)
        warning_count = len(warning_items)
        normal_count = len(normal_items)
        

        ai_result = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', ai_result, flags=re.DOTALL)
        ai_result = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', ai_result, flags=re.DOTALL)
        ai_result = ai_result.strip()

        # 检查AI是否返回了完整的HTML报告
        ai_result_stripped = ai_result.strip()

        # 先尝试从markdown代码块中提取HTML内容
        # 匹配 ```html 或 ``` 开头的代码块
        code_block_patterns = [
            r'^```(?:html)?\s*\n(.*?)\n```$',  # 整个内容被代码块包裹
            r'^```(?:html)?\s*\n(.*)',          # 以代码块开头（可能没有结束）
        ]
        for pattern in code_block_patterns:
            code_block_match = re.match(pattern, ai_result_stripped, re.DOTALL | re.IGNORECASE)
            if code_block_match:
                extracted = code_block_match.group(1).strip()
                # 检查提取的内容是否是HTML
                if extracted.startswith('<!DOCTYPE') or extracted.startswith('<html') or extracted.startswith('<'):
                    ai_result_stripped = extracted
                    ai_result = ai_result_stripped
                    break

        # 检查是否是完全的HTML文档
        is_full_html = ai_result_stripped.startswith('<!DOCTYPE') or \
                       ai_result_stripped.startswith('<html') or \
                       ai_result_stripped.startswith('<!doctype')

        # 检查是否包含HTML body标签
        has_body_tag = bool(re.search(r'<body[^>]*>', ai_result, re.IGNORECASE))

        # 检查是否包含HTML标签（可能是HTML片段）
        contains_html_tags = bool(re.search(r'<html[^>]*>|<body[^>]*>|<div[^>]*>|<table[^>]*>|<p[^>]*>', ai_result, re.IGNORECASE))

        result_html = ""

        if is_full_html or has_body_tag:
            body_match = re.search(r'<body[^>]*>(.*?)</body>', ai_result, re.DOTALL | re.IGNORECASE)
            if body_match:
                result_html = body_match.group(1)
            else:
                html_match = re.search(r'<html[^>]*>(.*?)</html>', ai_result, re.DOTALL | re.IGNORECASE)
                if html_match:
                    result_html = html_match.group(1)
                else:
                    result_html = ai_result
        elif contains_html_tags:
            body_match = re.search(r'<body[^>]*>(.*?)</body>', ai_result, re.DOTALL | re.IGNORECASE)
            if body_match:
                result_html = body_match.group(1)
            else:
                result_html = ai_result
        else:
            result_html = self._format_markdown_to_html(ai_result)

        # 清理嵌套的完整HTML文档结构
        for _ in range(3):
            nested_body = re.search(r'<body[^>]*>(.*?)</body>', result_html, re.DOTALL | re.IGNORECASE)
            if nested_body:
                result_html = nested_body.group(1)
                continue
            nested_html = re.search(r'<html[^>]*>(.*?)</html>', result_html, re.DOTALL | re.IGNORECASE)
            if nested_html:
                result_html = nested_html.group(1)
                continue
            break

        # 删除<!DOCTYPE>、<html>、<head>、<meta>、<title>等文档标签
        result_html = re.sub(r'<!DOCTYPE[^>]*>', '', result_html, flags=re.IGNORECASE)
        result_html = re.sub(r'</?html[^>]*>', '', result_html, flags=re.IGNORECASE)
        result_html = re.sub(r'</?head[^>]*>', '', result_html, flags=re.IGNORECASE)
        result_html = re.sub(r'<meta[^>]*/?>', '', result_html, flags=re.IGNORECASE)
        result_html = re.sub(r'<title[^>]*>.*?</title>', '', result_html, flags=re.DOTALL | re.IGNORECASE)
        result_html = re.sub(r'</?body[^>]*>', '', result_html, flags=re.IGNORECASE)

        # 删除<style>标签及其内容（内联样式已在元素上）
        result_html = re.sub(r'<style[^>]*>.*?</style>', '', result_html, flags=re.DOTALL | re.IGNORECASE)

        # 删除<script>标签及其内容
        result_html = re.sub(r'<script[^>]*>.*?</script>', '', result_html, flags=re.DOTALL | re.IGNORECASE)

        # 删除"检查项统计"/"巡检统计概览"/"统计卡片"章节
        result_html = re.sub(r'<div[^>]*class="[^"]*stat[^"]*"[^>]*>[\s\S]*?</div>\s*</div>\s*</div>', '', result_html, flags=re.IGNORECASE)
        result_html = re.sub(r'<div[^>]*>[\s]*检查项统计[\s\S]*?(?=<(?:div[^>]*class="section"|/div\s*</div\s*</div>|<div[^>]*class="section"))', '', result_html, flags=re.IGNORECASE)
        result_html = re.sub(r'<div[^>]*class="[^"]*card[^"]*"[^>]*>[\s\S]*?巡检统计概览[\s\S]*?</div>[\s\S]*?</div>[\s\S]*?</div>', '', result_html)
        result_html = re.sub(r'<h[23][^>]*>[\s]*(?:巡检统计概览|检查项统计|统计卡片)[\s\S]*?(?=<(?:h[23]|/div|/body))', '', result_html, flags=re.IGNORECASE)
        result_html = re.sub(r'<div[^>]*class="[^"]*section[^"]*"[^>]*>\s*<div[^>]*class="[^"]*section-title[^"]*"[^>]*>\s*(?:检查项统计|巡检统计概览|统计卡片)\s*</div>[\s\S]*?</div>\s*</div>', '', result_html, flags=re.IGNORECASE)
        result_html = self._sanitize_ai_advice_html(result_html)
        result_html = result_html.replace("高风险", "异常").replace("中风险", "警告")
        result_html = self._extract_ai_analysis_fragment(result_html)

        result_html = re.sub(r'<pre\s+style="[^"]*"', '<pre', result_html)
        result_html = re.sub(r'<code\s+style="[^"]*"', '<code', result_html)
        result_html = re.sub(r'<pre\s+style=\'[^\']*\'', '<pre', result_html)
        result_html = re.sub(r'<code\s+style=\'[^\']*\'', '<code', result_html)

        # 修复截断的HTML：补全未闭合的标签
        open_tags = re.findall(r'<(div|table|thead|tbody|tr|td|th|ul|ol|li|section|header|footer|span|p)[\s>]', result_html, re.IGNORECASE)
        close_tags = re.findall(r'</(div|table|thead|tbody|tr|td|th|ul|ol|li|section|header|footer|span|p)>', result_html, re.IGNORECASE)
        tag_stack = []
        for tag in open_tags:
            tag_stack.append(tag.lower())
        for tag in close_tags:
            tag_lower = tag.lower()
            if tag_stack and tag_stack[-1] == tag_lower:
                tag_stack.pop()
            else:
                for i in range(len(tag_stack) - 1, -1, -1):
                    if tag_stack[i] == tag_lower:
                        tag_stack.pop(i)
                        break
        if tag_stack:
            # 删除最后一个未完成的标签（可能只有开头没有内容）
            last_open_pos = result_html.rfind('<')
            if last_open_pos > len(result_html) - 50:
                partial_tag = result_html[last_open_pos:]
                if not re.search(r'>\s*$', partial_tag):
                    result_html = result_html[:last_open_pos]
                    tag_stack.pop()
            # 补全剩余未闭合的标签
            for tag in reversed(tag_stack):
                result_html += f'</{tag}>'

        issue_keywords = []
        warning_keywords = []

        # 按行分析，提取真正的问题描述行
        lines = ai_result.split('\n')
        for line in lines:
            stripped = line.strip()
            # 跳过空行
            if not stripped:
                continue
            # 跳过标题行（#开头）
            if stripped.startswith('#'):
                continue
            # 跳过代码块标记
            if stripped.startswith('```') or stripped.startswith('|'):
                continue
            # 跳过只有"异常项目"、"警告项目"这样的行
            if stripped in ['异常项目', '警告项目', '异常项', '警告项']:
                continue
            # 跳过说明性文字
            if any(stripped.startswith(p) for p in ['异常项目：', '异常项目:', '警告项目：', '警告项目:', '影响：', '影响:', '建议：', '建议:']):
                continue
            # 跳过太短的行
            if len(stripped) < 10:
                continue

            # 检查是否包含异常关键词（必须带冒号才是真正的描述）
            has_issue_kw = any(kw in stripped for kw in ['异常：', '错误：', '失败：', '故障：', '异常:', '错误:', '失败:', '故障:'])
            has_warning_kw = any(kw in stripped for kw in ['警告：', '注意：', '风险：', '警告:', '注意:', '风险:'])

            if has_issue_kw or has_warning_kw:
                # 清理并截断
                clean_line = re.sub(r'^[#*\-\s]+\*?\s*', '', stripped).strip()
                # 移除前缀关键词（包括异常项、警告项等）
                clean_line = re.sub(r'^(异常项|异常|错误项|错误|失败项|失败|故障项|故障|警告项|警告|注意项|注意|风险项|风险)[:：\s]*', '', clean_line)
                # 移除可能残留的emoji
                clean_line = re.sub(r'^[❌🔴⚠️🔶⚡]+', '', clean_line).strip()

                # 跳过清理后太短的
                if clean_line and len(clean_line) > 5:
                    if has_issue_kw:
                        issue_keywords.append(clean_line[:200])
                    else:
                        warning_keywords.append(clean_line[:200])

        # 去重并限制数量
        issue_keywords = list(dict.fromkeys(issue_keywords))[:10]
        warning_keywords = list(dict.fromkeys(warning_keywords))[:10]
        # 巡检项状态只根据命令输出中的明确状态行判断。
        # AI 分析文字只展示在“AI分析与安全建议”区域，避免建议语句把正常项误标为异常。
        ai_issue_keywords = []
        ai_warning_keywords = []

        if not inspection_items:
            issue_count = warning_count = total_items = normal_count = 0
        else:
            def normalize_text(value: str) -> str:
                return re.sub(r'[\s\W_]+', '', str(value or '').lower())

            def plain_text(value: str) -> str:
                text = re.sub(r'<[^>]+>', ' ', str(value or ''))
                text = re.sub(r'&nbsp;|&ensp;|&emsp;', ' ', text)
                return re.sub(r'\s+', ' ', text)

            def item_match_terms(item_name: str) -> list:
                cleaned = re.sub(r'\s*[\(（][^()（）]*(?:>=|≥|超过|风险|严重|异常|警告|%)[^()（）]*[\)）]', '', item_name)
                terms = [item_name, cleaned]
                alias_rules = [
                    (['内存', 'memory', 'mem'], ['内存', 'memory', 'mem']),
                    (['磁盘', 'disk', '文件系统'], ['磁盘', 'disk', '文件系统', '分区']),
                    (['CPU', 'cpu', '负载'], ['CPU', 'cpu', '负载']),
                    (['交换分区', 'swap'], ['交换分区', 'swap']),
                    (['登录', '登陆', '用户'], ['登录', '登陆', '用户', 'login']),
                    (['端口', '监听'], ['端口', '监听', 'port']),
                    (['网络', '网卡', 'DNS', '路由'], ['网络', '网卡', 'DNS', '路由', 'network']),
                    (['Docker', 'docker'], ['Docker', 'docker', '容器']),
                    (['SSL', '证书'], ['SSL', '证书', 'cert']),
                    (['进程'], ['进程', 'process']),
                ]
                name_lower = item_name.lower()
                for probes, aliases in alias_rules:
                    if any(probe.lower() in name_lower for probe in probes):
                        terms.extend(aliases)
                return [term for term in dict.fromkeys(terms) if term and len(term.strip()) >= 2]

            def ai_context_status_for_item(item_name: str, ai_text: str) -> str:
                segments = [
                    seg.strip()
                    for seg in re.split(r'(?:<li\b[^>]*>|</li>|<p\b[^>]*>|</p>|<tr\b[^>]*>|</tr>|[。！？!?；;\n])', ai_text)
                    if seg and seg.strip()
                ]
                error_tokens = ['异常', '错误', '失败', '故障', '严重', '高风险', 'critical', 'error', 'failed']
                warning_tokens = ['警告', '注意', '偏高', '偏低', '中风险', 'warning', 'warn']
                weak_advice_tokens = ['建议', '避免', '防止', '排查', '确认', '关注', '观察', '可能', '如果', '可']
                for term in item_match_terms(item_name):
                    term_lower = term.lower()
                    for segment in segments:
                        segment_lower = segment.lower()
                        if term_lower not in segment_lower:
                            continue
                        has_error = any(token.lower() in segment_lower for token in error_tokens)
                        has_warning = any(token.lower() in segment_lower for token in warning_tokens)
                        has_explicit_status = bool(re.search(r'(异常|警告|错误|失败|高风险|中风险)\s*[:：]', segment))
                        has_evidence_value = bool(re.search(r'(\d+(?:\.\d+)?\s*%|超过|大于|>=|≥|状态\s*[:：])', segment))
                        is_advice_only = any(token in segment for token in weak_advice_tokens) and not has_explicit_status and not has_evidence_value
                        if is_advice_only:
                            continue
                        if has_error and (has_explicit_status or has_evidence_value):
                            return 'error'
                        if has_warning and (has_explicit_status or has_evidence_value):
                            return 'warning'
                return ''

            def mark_or_add_ai_items(keywords: list, status: str):
                for keyword in keywords:
                    keyword_norm = normalize_text(keyword)
                    matched = False
                    for item in inspection_items:
                        item_name = str(item.get('name', ''))
                        item_summary = self._build_key_result_html(
                            item_name,
                            item.get('output', ''),
                            item.get('status', 'normal')
                        )[0]
                        item_norm = normalize_text(item_name + item_summary)
                        if item_norm and (
                            item_norm in keyword_norm
                            or keyword_norm in item_norm
                            or any(part and part in keyword_norm for part in re.split(r'[\s_:/()（）-]+', item_name) if len(part) >= 2)
                        ):
                            current_status = item.get('status', 'normal')
                            if status == 'error' or current_status == 'normal':
                                item['status'] = status
                            matched = True
                            break
                    if not matched:
                        inspection_items.append({
                            'name': 'AI识别异常' if status == 'error' else 'AI识别警告',
                            'status': status,
                            'output': keyword
                        })

            if ai_issue_keywords or ai_warning_keywords:
                mark_or_add_ai_items(ai_issue_keywords, 'error')
                mark_or_add_ai_items(ai_warning_keywords, 'warning')

                total_items = len(inspection_items)
                error_items = [i for i in inspection_items if i.get('status') == 'error']
                warning_items = [i for i in inspection_items if i.get('status') == 'warning']
                normal_items = [i for i in inspection_items if i.get('status') == 'normal']
                issue_count = len(error_items)
                warning_count = len(warning_items)
                normal_count = len(normal_items)

            issue_keywords = [
                f"{str(item.get('name', '未知')).replace('高风险', '异常').replace('中风险', '警告')}: {self._build_key_result_html(item.get('name', ''), item.get('output', ''), item.get('status', 'error'))[0]}"
                for item in error_items
            ]
            warning_keywords = [
                f"{str(item.get('name', '未知')).replace('高风险', '异常').replace('中风险', '警告')}: {self._build_key_result_html(item.get('name', ''), item.get('output', ''), item.get('status', 'warning'))[0]}"
                for item in warning_items
            ]

        # 生成问题表格HTML
        issues_table_html = ''
        if issue_keywords:
            import html as _html
            issues_table_html = '''
                <div class="issues-section">
                    <h3 style="color: #ef4444; margin: 20px 0 10px;">⚠️ 异常项目</h3>
                    <table class="issues-table">
                        <thead>
                            <tr>
                                <th style="width:50px">序号</th>
                                <th>问题描述</th>
                            </tr>
                        </thead>
                        <tbody>
            '''
            for i, issue in enumerate(issue_keywords, 1):
                issue_desc = _html.escape(issue)
                issues_table_html += f'''
                            <tr>
                                <td class="issue-num">{i}</td>
                                <td class="issue-desc">{issue_desc}</td>
                            </tr>
            '''
            issues_table_html += '''
                        </tbody>
                    </table>
                </div>
            '''

        inspection_list_html = ''
        if inspection_items:
            import html as _html
            inspection_list_html = '''
                <div class="inspection-list-section">
                    <h3 class="block-title">📋 巡检概要</h3>
                    <p class="block-hint">每一行显示巡检项概要；点击巡检内容后展开命令执行结果。长输出会自动截取关键字段展示。</p>
                    <div class="inspection-accordion">
            '''
            for i, item in enumerate(inspection_items, 1):
                status = item.get('status', 'normal')
                if status == 'error':
                    status_icon = '🔴'
                    status_text = '异常'
                    status_class = 'status-error'
                elif status == 'warning':
                    status_icon = '🟡'
                    status_text = '警告'
                    status_class = 'status-warning'
                else:
                    status_icon = '🟢'
                    status_text = '正常'
                    status_class = 'status-normal'
                display_name = str(item.get('name', '未知')).replace("高风险", "异常").replace("中风险", "警告")
                name = _html.escape(display_name)
                raw_output = item.get('output', '')
                summary_text, key_result_html = self._build_key_result_html(item.get('name', ''), raw_output, status)
                summary_display = _html.escape(summary_text)
                normalized_output = (raw_output.strip() or '无输出').replace("高风险", "异常").replace("中风险", "警告")
                detail_text = _html.escape(normalized_output)
                inspection_list_html += f'''
                        <details class="inspection-item {status_class}">
                            <summary>
                                <span class="item-index">{i}</span>
                                <span class="item-status">{status_icon} {status_text}</span>
                                <span class="item-name">{name}</span>
                                <span class="item-summary">{summary_display}</span>
                                <span class="expand-tip">展开详情</span>
                            </summary>
                            <div class="item-body">
                                <div class="key-result-title">巡检概要</div>
                                {key_result_html}
                                <div class="detail-title">命令执行结果</div>
                                <pre class="raw-output">{detail_text}</pre>
                            </div>
                        </details>
                '''
            inspection_list_html += '''
                    </div>
                </div>
            '''

        if warning_keywords:
            import html as _html
            issues_table_html += '''
                <div class="issues-section">
                    <h3 style="color: #f59e0b; margin: 20px 0 10px;">⚡ 警告项目</h3>
                    <table class="issues-table warning-table">
                        <thead>
                            <tr>
                                <th style="width:50px">序号</th>
                                <th>问题描述</th>
                            </tr>
                        </thead>
                        <tbody>
            '''
            for i, warn in enumerate(warning_keywords, 1):
                warn_desc = _html.escape(warn)
                issues_table_html += f'''
                            <tr>
                                <td class="issue-num">{i}</td>
                                <td class="issue-desc">{warn_desc}</td>
                            </tr>
            '''
            issues_table_html += '''
                        </tbody>
                    </table>
                </div>
            '''

        import html as _html

        unknown_count = sum(i.get('status') == 'unknown' for i in inspection_items)
        info_count = sum(i.get('status') == 'info' for i in inspection_items)
        na_count = sum(i.get('status') == 'not_applicable' for i in inspection_items)
        if issue_count > 0:
            overall_class = 'bad'
            overall_text = '异常'
            overall_desc = f'发现 {issue_count} 个异常、{warning_count} 个警告，请优先处理异常项并关注相关影响范围。'
        elif warning_count > 0:
            overall_class = 'warn'
            overall_text = '警告'
            overall_desc = f'发现 {warning_count} 个警告项，请结合业务影响核查；该结果不代表系统整体可用性。'
        elif unknown_count or total_items == 0 or normal_count == 0:
            overall_class = 'neutral'
            overall_text = '待核查'
            overall_desc = '存在未获取数据或缺少可判定检查；信息清单和不适用项不计为正常。'
        else:
            overall_class = 'ok'
            overall_text = '已判定项未见异常'
            overall_desc = '规则判定项未达到告警阈值；信息采集仍需结合业务基线人工核对。'

        def normalize_display_text(text):
            return str(text or '').replace('高风险', '异常').replace('中风险', '警告')

        def normalize_item_title(text):
            cleaned = normalize_display_text(text)
            cleaned = re.sub(r'\s*[\(（][^()（）]*(?:>=|≥|超过|风险|严重|异常|警告|%)[^()（）]*[\)）]', '', cleaned)
            return cleaned.strip()

        critical_items_html = ''
        if issue_keywords or warning_keywords:
            critical_items_html = '<h2>关键异常与警告</h2><ul class="text-list critical-list">'
            for i, issue in enumerate(issue_keywords, 1):
                critical_items_html += f'<li><b class="badge bad">异常</b><span>{_html.escape(normalize_display_text(issue))}</span></li>'
            for i, warn in enumerate(warning_keywords, 1):
                critical_items_html += f'<li><b class="badge warn">警告</b><span>{_html.escape(normalize_display_text(warn))}</span></li>'
            critical_items_html += '</ul>'
        else:
            critical_items_html = '<h2>关键异常与警告</h2><div class="empty-state">已判定项中未发现异常和警告；未获取、仅采集和不适用项不能据此视为正常。</div>'

        inspection_list_html = '<h2>巡检结果</h2>'
        if inspection_items:
            inspection_list_html += '<ol class="result-list">'
            for i, item in enumerate(inspection_items, 1):
                status = item.get('status', 'normal')
                if status == 'error':
                    item_class = 'bad'
                    status_text = '异常'
                    impact_text = '影响判断：需优先关注'
                elif status == 'warning':
                    item_class = 'warn'
                    status_text = '警告'
                    impact_text = '影响判断：建议关注'
                elif status == 'normal':
                    item_class = 'ok'
                    status_text = '正常'
                    impact_text = '影响判断：本项未达到默认告警阈值'
                else:
                    item_class = 'neutral'
                    status_text = STATUS_LABELS.get(status, '未获取')
                    impact_text = '影响判断：需结合适用环境与业务基线核对'

                display_name = normalize_item_title(item.get('name', '未知'))
                raw_output = normalize_display_text(item.get('output', ''))
                summary_text, key_result_html = self._build_key_result_html(display_name, raw_output, status)
                summary_display = _html.escape(normalize_display_text(summary_text))
                sorted_detail_output = self._format_detail_output_for_report(display_name, raw_output)
                detail_text = _html.escape(sorted_detail_output.strip() or '无输出')

                inspection_list_html += f'''
                    <li class="result-item {item_class}">
                        <details>
                            <summary>
                                <div class="idx">{i}</div>
                                <div class="summary-main">
                                    <div class="item-title">
                                        <span>{_html.escape(display_name)}</span>
                                        <b class="badge {item_class}">{status_text}</b>
                                    </div>
                                    <div class="result">{summary_display}</div>
                                    <div class="impact">{impact_text}</div>
                                </div>
                            </summary>
                            <div class="expanded-result">
                                <div class="detail-label">详细信息</div>
                                {key_result_html}
                                <div class="detail-label">命令执行结果</div>
                                <pre class="detail-output">{detail_text}</pre>
                            </div>
                        </details>
                    </li>
                '''
            inspection_list_html += '</ol>'
        else:
            inspection_list_html += '<div class="empty-state">暂无巡检项明细。</div>'

        ai_content_html = result_html.strip() or '<p>AI 未返回可展示的分析内容。</p>'
        ai_content_html = normalize_display_text(ai_content_html)
        ai_content_html = self._repair_ai_list_html(ai_content_html)

        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>巡检报告 - {product}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
            color: #1f2937;
            background: #eef2f7;
            line-height: 1.65;
        }}
        .page {{ max-width: 1120px; margin: 0 auto; padding: 28px 20px 40px; }}
        .hero {{
            background: #12324a;
            color: #fff;
            padding: 28px 32px;
            border-radius: 8px 8px 0 0;
        }}
        .hero h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
        .meta {{ color: #d8e4ee; font-size: 14px; }}
        .panel {{
            background: #fff;
            border: 1px solid #dfe7ef;
            border-top: 0;
            padding: 28px 32px;
            border-radius: 0 0 8px 8px;
            box-shadow: 0 10px 30px rgba(31,41,55,.08);
        }}
        .summary {{
            border-left: 5px solid #2563eb;
            background: #f8fafc;
            padding: 16px 18px;
            margin-bottom: 22px;
            border-radius: 6px;
        }}
        .summary.ok {{ border-left-color: #16a34a; }}
        .summary.warn {{ border-left-color: #d97706; }}
        .summary.bad {{ border-left-color: #dc2626; background: #fff1f2; }}
        .stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 22px 0 26px; }}
        .stat {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; background: #fbfdff; }}
        .stat strong {{ display: block; font-size: 28px; line-height: 1; margin-bottom: 8px; }}
        .stat span {{ color: #64748b; font-size: 13px; }}
        .ok strong, .badge.ok {{ color: #166534; }}
        .warn strong, .badge.warn {{ color: #92400e; }}
        .bad strong, .badge.bad {{ color: #991b1b; }}
        h2 {{ font-size: 20px; margin: 28px 0 14px; }}
        .result-list {{ list-style: none; margin: 0; padding: 0; }}
        .result-item {{
            border: 1px solid #e5e7eb;
            border-left-width: 5px;
            border-radius: 8px;
            padding: 14px;
            margin-bottom: 12px;
            background: #fff;
            transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease, background .18s ease;
        }}
        .result-item:hover {{ border-color: #93c5fd; box-shadow: 0 8px 22px rgba(31,41,55,.10); transform: translateY(-1px); }}
        .result-item summary {{ display: grid; grid-template-columns: 44px 1fr; gap: 14px; cursor: pointer; list-style: none; }}
        .result-item summary::-webkit-details-marker {{ display: none; }}
        .result-item.ok {{ border-left-color: #16a34a; }}
        .result-item.warn {{ border-left-color: #d97706; }}
        .result-item.bad {{ border-left-color: #dc2626; background: #fff1f2; }}
        .result-item.bad:hover {{ background: #ffe4e6; border-color: #fca5a5; }}
        .idx {{
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: #eef2f7;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            color: #475569;
        }}
        .item-title {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; font-weight: 700; margin-bottom: 6px; }}
        .badge {{ min-width: 48px; text-align: center; border-radius: 999px; padding: 2px 10px; font-size: 12px; display: inline-block; }}
        .badge.ok {{ background: #dcfce7; }}
        .badge.warn {{ background: #fef3c7; }}
        .badge.bad {{ background: #fee2e2; }}
        .badge.neutral, .summary.neutral {{ background: #f1f5f9; color: #475569; }}
        .result {{ color: #334155; white-space: pre-wrap; overflow-wrap: anywhere; }}
        .impact {{ margin-top: 6px; color: #64748b; font-size: 13px; }}
        .expanded-result {{ margin: 12px 0 0 58px; }}
        .detail-label {{ margin: 12px 0 8px; color: #475569; font-weight: 700; font-size: 13px; }}
        .detail-output {{
            margin: 0;
            padding: 12px;
            background: #0f172a;
            color: #e5e7eb;
            border-radius: 6px;
            overflow: auto;
            max-height: 360px;
            white-space: pre-wrap;
            font-size: 12px;
        }}
        .key-result {{ margin: 0; color: #334155; }}
        .key-result ul {{ margin: 0; padding-left: 18px; }}
        .key-result li {{ margin: 4px 0; }}
        .key-result-table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin: 4px 0 10px; }}
        .key-result-table th, .key-result-table td {{ border: 1px solid #e5e7eb; padding: 7px 9px; text-align: left; }}
        .key-result-table th {{ background: #f8fafc; color: #475569; }}
        .text-list {{ margin: 0; padding-left: 20px; }}
        .text-list li {{ margin: 8px 0; }}
        .critical-list {{ padding-left: 0; list-style: none; }}
        .critical-list li {{ display: flex; gap: 10px; align-items: flex-start; }}
        .ai-content {{
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 16px 18px;
            background: #fbfdff;
            overflow-wrap: anywhere;
        }}
        .ai-content table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
        .ai-content th, .ai-content td {{ border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; }}
        .ai-content th {{ background: #f8fafc; }}
        .ai-content pre, .ai-content code {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
        .empty-state {{ color: #64748b; background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 14px 16px; }}
        .safety-note {{ margin-top: 20px; padding: 14px 16px; background: #fff7ed; border: 1px solid #fed7aa; border-radius: 8px; color: #7c2d12; }}
        .footer {{ color: #64748b; font-size: 12px; text-align: center; margin-top: 18px; }}
        @media (max-width: 760px) {{
            .page {{ padding: 12px; }}
            .hero, .panel {{ padding: 20px; }}
            .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
            .item-title {{ display: block; }}
            .badge {{ margin-top: 6px; }}
            .expanded-result {{ margin-left: 0; }}
        }}
    </style>
</head>
<body>
    <main class="page">
        <header class="hero">
            <h1>系统巡检报告</h1>
            <div class="meta">产品：{product}&nbsp;&nbsp;检查时间：{timestamp}</div>
            <div class="meta">目标设备：{target_meta}</div>
        </header>
        <section class="panel">
            <div class="summary {overall_class}">
                <strong>整体状态：{overall_text}</strong><br>{overall_desc}
            </div>
            <div class="stats">
                <div class="stat"><strong>{total_items}</strong><span>巡检项总数</span></div>
                <div class="stat ok"><strong>{normal_count}</strong><span>正常</span></div>
                <div class="stat warn"><strong>{warning_count}</strong><span>警告</span></div>
                <div class="stat bad"><strong>{issue_count}</strong><span>异常</span></div>
                <div class="stat"><strong>{unknown_count}</strong><span>未获取</span></div>
                <div class="stat"><strong>{info_count}</strong><span>仅采集</span></div>
                <div class="stat"><strong>{na_count}</strong><span>不适用</span></div>
            </div>
            {inspection_list_html}
            {critical_items_html}
            <h2>AI分析与安全建议</h2>
            <div class="ai-content">{ai_content_html}</div>
            <div class="safety-note">安全边界：报告建议以排查、备份、验证和变更审批为主，避免直接执行删除、格式化、强制停止服务等敏感操作。</div>
        </section>
        <div class="footer">Linux 运维巡检报告 · {product} · {timestamp}</div>
    </main>
</body>
</html>'''


    def _open_folder(self, file_path: str):
        """打开文件所在文件夹"""
        import platform
        folder = os.path.dirname(file_path)
        if platform.system() == 'Windows':
            os.startfile(folder)
        elif platform.system() == 'Darwin':
            subprocess.run(['open', folder])
        else:
            subprocess.run(['xdg-open', folder])

    def _parse_output_text_fallback(self, text: str, builtin_out: dict, custom_out: dict):
        """
        从 output_text 文本中按脚本名称提取输出（兜底方案）。
        当 streaming/JSON 合并都未能正确填充时使用。
        """
        import re


        # 诊断：检查有哪些 header 模式实际存在于文本中
        for pattern, stype in [(r'📜\s*自定义脚本', 'custom'), (r'📋\s*巡检项', 'builtin'), (r'={3,}', 'separator')]:
            matches = re.findall(pattern, text)

        lines = text.split('\n')
        current_script = None
        current_type = 'builtin'
        current_lines = []

        script_headers = [
            (r'📜\s*自定义脚本[：:]\s*(.+)', 'custom'),
            (r'📋\s*巡检项[：:]\s*(.+)', 'builtin'),
            (r'={10,}.*={10,}', None),          # 分隔线 → 重置
        ]

        def flush():
            if current_script and current_lines:
                content = '\n'.join(current_lines).strip()
                if content:
                    if current_type == 'custom':
                        custom_out[current_script] = content
                    else:
                        builtin_out[current_script] = content

        for line in lines:
            line_stripped = line.strip()
            matched = False
            for pattern, stype in script_headers[:2]:
                m = re.match(pattern, line_stripped)
                if m:
                    flush()
                    current_script = m.group(1).strip()
                    current_type = stype or 'builtin'
                    current_lines = []
                    matched = True
                    break
            if not matched:
                # 检查分隔线：遇到分隔线时如果已有当前脚本，也 flush
                if re.match(r'=+', line_stripped) and current_script:
                    # 分隔线之间可能有内容，暂不 flush，等遇到下一个脚本头
                    pass
                else:
                    if current_script:
                        current_lines.append(line)

        flush()  # 最后 flush


    def set_ssh_client(self, ssh_client):
        """设置 SSH 客户端"""
        self.ssh_client = ssh_client

    def set_device(self, device):
        """Clear results even when switching between devices of the same product."""
        previous = getattr(self, 'current_device', {}).get('id')
        if previous != device.get('id'):
            self._clear_inspection_state()
            self.task_device = {}
            self.task_id = None
            self.task_checks = []
        self.current_device = deepcopy({k: device[k] for k in ('id', 'name', 'host', 'port', 'product') if k in device})

    def _report_saved(self, path):
        self.report_saved.emit(dict(device_id=getattr(self, 'task_device', {}).get('id'),
                                   task_id=getattr(self, 'task_id', None), path=str(Path(path).resolve())))

    def _get_checked_inspection_items(self):
        checked_items = []
        for i in range(self.inspect_list.count()):
            item = self.inspect_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                item_data = item.data(Qt.ItemDataRole.UserRole)
                if item_data:
                    checked_items.append(item_data)
        return checked_items

    def _rebuild_output_checklist(self):
        """同步右侧巡检输出待执行清单。"""
        if not hasattr(self, "output_list"):
            return
        if self.inspection_worker is not None and self.inspection_worker.isRunning():
            return

        self.output_list.clear()
        self.list_item_for_script = {}
        self.list_widget_for_script = {}

        for item_data in self._get_checked_inspection_items():
            name = item_data.get('Name', item_data.get('name', '未知'))
            item = QListWidgetItem(name)
            item.setData(1, name)
            item.setToolTip("待巡检")
            item.setSizeHint(QSize(0, 44))
            self.output_list.addItem(item)
            self.list_item_for_script[name] = item

            label = self._create_output_status_label(name, "idle")
            self.output_list.setItemWidget(item, label)
            self.list_widget_for_script[name] = label

    def start_inspection(self):
        """开始巡检"""
        if (self.inspection_worker and self.inspection_worker.isRunning()) or (getattr(self, 'export_worker', None) and self.export_worker.isRunning()):
            return
        if getattr(self, 'start_guard', None) and not self.start_guard():
            MessageBox('巡检任务进行中', '请先等待当前运维任务完成或停止后，再开始新的巡检。', self).exec()
            return
        # 从 inspect_list 获取用户选择的巡检项
        selected_items = self._get_checked_inspection_items()
        

        if not self.ssh_client:
            MessageBox("连接错误", "请先连接到服务器", self)
            return

        if not selected_items:
            MessageBox("选择错误", "请至少选择一个巡检项", self)
            return

        # 清空上一轮输出和预览，右侧待巡检清单保持同步显示
        self.builtin_outputs.clear()
        self.custom_script_outputs.clear()
        self.output_text.clear()
        self.preview_text.clear()
        if hasattr(self, 'generic_output_tab'):
            self.generic_output_tab.clear()
        if hasattr(self, 'export_ai_btn'):
            self.export_ai_btn.setEnabled(False)
        
        # 合并所有要执行的脚本（selected_items 已经包含了 inspect_list 中的所有选中项，包括自定义脚本）
        all_scripts = []
        for script in selected_items:
            s = dict(script)
            # 判断脚本类型
            if script.get('is_custom_script'):
                s['_type'] = 'custom'
                # 自定义脚本需要用 command 或 content
                if not s.get('command') and s.get('script_content'):
                    s['command'] = s.get('script_content')
            elif script.get('is_product_script'):
                s['_type'] = 'product_script'
            else:
                s['_type'] = 'builtin'
            all_scripts.append(s)
        

        if not all_scripts:
            MessageBox("选择错误", "请至少选择一个巡检项", self)
            return
        
        # 验证SSH连接是否有效
        self.output_text.clear()
        self.preview_text.clear()
        self.output_text.append("正在验证SSH连接...")
        try:
            # 检查ssh_client是否有必要的方法
            if hasattr(self.ssh_client, 'is_connected') and hasattr(self.ssh_client, 'execute'):
                # 检查连接状态
                if not self.ssh_client.is_connected:
                    self.output_text.append("✗ SSH连接状态: 未连接")
                    MessageBox("连接错误", "SSH连接已断开", self)
                    return
                
                # 执行测试命令
                test_result = self.ssh_client.execute("echo connection_test_ok")
                exit_code = test_result[0]
                output = test_result[1] if len(test_result) > 1 else ""
                error = test_result[2] if len(test_result) > 2 else ""
                
                
                if exit_code == 0:
                    self.output_text.append("✓ SSH连接正常")
                else:
                    self.output_text.append(f"⚠️ SSH连接测试返回码: {exit_code}")
                    if error:
                        self.output_text.append(f"   错误信息: {error[:200]}")
                    # 不直接返回，尝试继续执行
                    self.output_text.append("   尝试继续执行巡检...")
            else:
                self.output_text.append(f"⚠️ SSH客户端对象类型: {type(self.ssh_client).__name__}")
                self.output_text.append("   尝试继续执行巡检...")
        except Exception as e:
            self.output_text.append(f"⚠️ SSH连接测试异常: {str(e)[:200]}")
            self.output_text.append("   尝试继续执行巡检...")
        
        # 清空输出
        self.output_text.append("")
        self.output_text.append(f"开始巡检（{len(selected_items)}个巡检项）")
        self.output_text.append("=" * 50)
        self.custom_script_outputs.clear()  # 清空脚本输出
        self.builtin_outputs.clear()  # 清空内置检查项输出
        
        # 清空并重建巡检项列表
        self.output_list.clear()
        self.list_item_for_script = {}  # 存储脚本名对应的list item
        self.list_widget_for_script = {}
        for idx, s in enumerate(all_scripts):
            script_name = s.get('Name', s.get('name', '未知'))
            script_type = s.get('_type', 'builtin')
            item_text = f"{script_name}"
            item = QListWidgetItem(item_text)
            item.setData(1, script_name)  # 存储脚本名
            item.setSizeHint(QSize(0, 44))
            self.output_list.addItem(item)
            self.list_item_for_script[script_name] = item
            label = self._create_output_status_label(script_name, "idle")
            self.output_list.setItemWidget(item, label)
            self.list_widget_for_script[script_name] = label
        
        # 更新 UI 状态
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 不确定进度
        
        # 启动巡检线程
        self.task_device = deepcopy(getattr(self, 'current_device', {}))
        self.task_id = str(uuid4())
        self.task_checks = deepcopy(all_scripts)
        self.task_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._task_summary_emitted = False
        self.inspection_started.emit(self.task_device)
        self.inspection_worker = InspectionWorker(self.ssh_client, all_scripts)
        self.inspection_worker.output_received.connect(self.on_all_output_received)
        self.inspection_worker.script_output.connect(self.on_script_output_received)
        self.inspection_worker.script_completed.connect(self.on_script_item_completed)
        self.inspection_worker.inspection_finished.connect(self.on_inspection_finished)
        self.inspection_worker.start()

    def _create_output_status_label(self, text: str, status: str, error_msg: str = "") -> QWidget:
        container = QWidget()
        container.setObjectName("outputStatusItem")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setMinimumHeight(40)
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(0)

        text_label = QLabel(text)
        text_label.setObjectName("statusText")
        text_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        text_label.setTextFormat(Qt.TextFormat.PlainText)
        text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        text_label.setMinimumHeight(38)
        layout.addWidget(text_label)

        container._status_text_label = text_label
        self._apply_output_status_label_style(container, status, error_msg)
        return container

    def _apply_output_status_label_style(self, widget: QWidget, status: str, error_msg: str = ""):
        styles = {
            "idle": ("#FFFFFF", "#334155", "#e5e7eb"),
            "pending": ("#FFFFFF", "#334155", "#e5e7eb"),
            "success": ("#50C9C3", "#FFFFFF", "#3BA8A2"),
            "failed": ("#FEE2E2", "#991B1B", "#fecaca"),
            "error": ("#FEE2E2", "#991B1B", "#fecaca"),
            "warning": ("#FEF3C7", "#92400E", "#FDE68A"),
            "unknown": ("#F1F5F9", "#475569", "#CBD5E1"),
            "info": ("#EFF6FF", "#1E40AF", "#BFDBFE"),
            "not_applicable": ("#F8FAFC", "#64748B", "#E2E8F0"),
        }
        bg, fg, border = styles.get(status, styles["idle"])
        widget.setStyleSheet(f"""
            QWidget#outputStatusItem {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QLabel#statusText {{
                background: transparent;
                color: {fg};
                border: none;
                padding: 0;
                font-size: 13px;
                font-family: "Microsoft YaHei", "Consolas";
                font-weight: bold;
            }}
        """)
        widget.setToolTip(error_msg or ("巡检完成" if status == "success" else "等待巡检"))

    def on_script_item_completed(self, script_name: str, success: bool, error_msg: str):
        """单个巡检项完成，高亮显示"""
        if script_name in self.list_item_for_script:
            item = self.list_item_for_script[script_name]
            # 标记为已完成 - 使用UserRole存储状态
            item.setData(Qt.ItemDataRole.UserRole, "completed" if success else "failed")
            item.setToolTip("巡检完成" if success else (error_msg or "巡检失败"))
            # 设置前缀
            base_text = item.text().lstrip("✓✗⏳ ").strip()
            raw = self.builtin_outputs.get(script_name, self.custom_script_outputs.get(script_name, ''))
            health = classify_output(raw) if success else 'unknown'
            state_label = STATUS_LABELS.get(health, '未获取')
            item.setText(f"{base_text} · {state_label}")
            item.setToolTip(error_msg or state_label)
            label = self.list_widget_for_script.get(script_name)
            if label:
                text_label = getattr(label, "_status_text_label", None)
                if text_label:
                    text_label.setText(item.text())
                self._apply_output_status_label_style(label, "success" if health == "normal" else health, error_msg or state_label)
            # 强制重绘 - 使用repaint确保视觉更新
            self.output_list.repaint()
            # 自动滚动到当前项
            self.output_list.scrollToItem(item)
        else:
            pass
    
    def on_all_output_received(self, key: str, output: str):
        """处理总输出"""
        if key == "all":
            try:
                import json
                data = json.loads(output)
                if isinstance(data, dict):
                    # 新格式: {"builtin": {...}, "custom": {...}}
                    # 合并 JSON 数据与已通过流式接收的数据（取较长者，确保不丢失任何内容）
                    if "builtin" in data:
                        json_builtin = data["builtin"] or {}
                        for k, v in json_builtin.items():
                            cur = self.builtin_outputs.get(k, "")
                            # JSON 值为非空时：若已积累的数据为空或 JSON 更长，则使用 JSON
                            if v and (not cur or len(str(v)) >= len(str(cur))):
                                self.builtin_outputs[k] = v
                    if "custom" in data:
                        json_custom = data["custom"] or {}
                        for k, v in json_custom.items():
                            cur = self.custom_script_outputs.get(k, "")
                            if v and (not cur or len(str(v)) >= len(str(cur))):
                                self.custom_script_outputs[k] = v
            except Exception as e:
                pass
    
    def on_script_output_received(self, script_name: str, line: str):
        """处理单个脚本的输出行"""
        # 根据脚本类型分别存储到对应的字典中
        # 需要通过 script_name 反查其类型
        script_type = 'builtin'  # 默认builtin

        # 在 scripts 和 custom_scripts 中查找脚本来确定类型
        for s in (self.scripts or []):
            if s.get('Name') == script_name:
                script_type = s.get('_type', 'builtin')
                break
        for s in (self.custom_scripts or []):
            if s.get('Name') == script_name:
                script_type = s.get('_type', 'custom')
                break

        if script_type == 'custom':
            if script_name not in self.custom_script_outputs:
                self.custom_script_outputs[script_name] = ""
            self.custom_script_outputs[script_name] += line
        else:
            # builtin 脚本输出
            if script_name not in self.builtin_outputs:
                self.builtin_outputs[script_name] = ""
            self.builtin_outputs[script_name] += line

        # 更新左侧主输出区域
        self.output_text.append(line)

        # 同时更新对应的标签页
        if script_name in self.custom_script_tabs:
            tab = self.custom_script_tabs[script_name]
            tab.append(line)

        if line and script_name not in self.builtin_outputs and script_name not in self.custom_script_outputs:
            dest = "builtin_outputs" if script_type == "builtin" else "custom_script_outputs"
    
    def stop_inspection(self):
        """停止巡检"""
        if self.inspection_worker:
            self.inspection_worker.stop()
            self.inspection_worker.wait()
        
        self.on_inspection_finished(False, "巡检已停止")
    
    def append_output(self, text: str):
        """追加输出"""
        self.output_text.append(text)
    
    def on_inspection_finished(self, success: bool, message: str):
        """巡检完成处理"""
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(True)
        self.progress_bar.setVisible(False)

        # 巡检完成后启用导出巡检报告按钮
        if success:
            self.export_ai_btn.setEnabled(True)

        self.output_text.append(f"\n{'='*60}")
        self.output_text.append(f"巡检状态: {message}")
        self.output_text.append(f"{'='*60}")

        # 更新巡检预览内容，并自动跳转到巡检预览标签页
        self.preview_text.setPlainText(self.output_text.toPlainText())
        self.output_tabs.setCurrentIndex(1)
         
        # 收集检查项的单独输出（无论成功还是停止都要发送）
        inspection_data = {
            'device': deepcopy(getattr(self, 'task_device', {})),
            'task_id': getattr(self, 'task_id', None),
            'time': getattr(self, 'task_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            'message': message,
            'success': success,
            'checks': [s.get('Name', s.get('name', '')) for s in getattr(self, 'task_checks', [])],
            "output": self.output_text.toPlainText(),  # 原始总输出
            "script_outputs": {
                "builtin": self.builtin_outputs,  # 内置检查项输出
                "custom": self.custom_script_outputs  # 自定义脚本输出
            }
        }
        if not getattr(self, '_task_summary_emitted', False):
            self._task_summary_emitted = True
            self.inspection_completed.emit(inspection_data)
    
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            /* 页面标题 */
            QLabel#pageTitle {
                font-size: 28px;
                font-weight: bold;
                color: #50C9C3;
                padding: 10px;
            }
            
            /* 滚动区域 */
            QScrollArea#scriptScrollArea {
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                background: white;
            }
            
            /* 分组框 */
            QGroupBox {
                font-size: 13px;
                font-weight: bold;
                color: #1A1A1A;
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
                background: white;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
                background: white;
            }
            
            /* 复选框 - 更明显的勾选样式 */
            QCheckBox {
                spacing: 10px;
                font-size: 13px;
                padding: 8px;
                margin: 2px 0;
            }
            
            QCheckBox::indicator {
                width: 22px;
                height: 22px;
                border-radius: 4px;
            }
            
            QCheckBox::indicator:unchecked {
                border: 2px solid #A0A0A0;
                background: white;
            }
            
            QCheckBox::indicator:checked {
                border: 2px solid #50C9C3;
                background: #50C9C3;
            }
            
            QCheckBox:hover {
                background: #F3F3F3;
                border-radius: 4px;
            }

            QListWidget#inspectList {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 8px;
                outline: none;
                color: #0f172a;
                font-size: 13px;
            }

            QListWidget#inspectList::item {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
                padding: 8px 10px;
                margin: 2px 0;
                min-height: 30px;
            }

            QListWidget#inspectList::item:hover {
                background: #f0fdfa;
                border-color: #99f6e4;
            }

            QListWidget#inspectList::item:selected {
                background: #e6fffb;
                border-color: #50C9C3;
                color: #0f766e;
            }
            
            /* 上传按钮 */
            QPushButton#uploadButton {
                background: #5B5B5B;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 20px;
                font-size: 12px;
            }
            
            QPushButton#uploadButton:hover {
                background: #3D3D3D;
            }
            
            /* 自定义脚本标签 */
            QLabel#customScriptsLabel {
                font-size: 11px;
                color: #5B5B5B;
                padding: 5px;
            }
            
            /* 输出组 */
            QGroupBox#outputGroup {
                font-size: 13px;
                font-weight: bold;
                color: #1A1A1A;
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
            }
            
            /* 输出文本框 */
            QTextEdit#outputText {
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                padding: 10px;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 11px;
                background: #1E293B;
                color: #E2E8F0;
            }
            
            /* 进度条 */
            QProgressBar#progressBar {
                border: 1px solid #E1E1E1;
                border-radius: 4px;
                text-align: center;
                height: 20px;
            }
            
            QProgressBar#progressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                          stop:0 #50C9C3, stop:1 #3BA8A2);
                border-radius: 3px;
            }
            
            /* 开始按钮 */
            QPushButton#startButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 12px 25px;
            }
            
            QPushButton#startButton:hover {
                background: #3BA8A2;
            }
            
            QPushButton#startButton:disabled {
                background: #A0A0A0;
            }
            
            /* 停止按钮 */
            QPushButton#stopButton {
                background: #D13438;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 12px 25px;
            }
            
            QPushButton#stopButton:hover {
                background: #A02E2C;
            }
            
            QPushButton#stopButton:disabled {
                background: #A0A0A0;
            }
            
            /* 导出按钮 */
            QPushButton#exportButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 12px 25px;
            }
            
            QPushButton#exportButton:hover {
                background: #3BA8A2;
            }
            
            QPushButton#exportButton:disabled {
                background: #A0A0A0;
            }
            
            /* 下一步按钮 */
            QPushButton#nextButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 12px 25px;
            }
            
            QPushButton#nextButton:hover {
                background: #3BA8A2;
            }
            
            /* 巡检项列表 */
            QTabWidget#outputTabs::pane {
                border: 1px solid #dbe3ee;
                border-radius: 10px;
                background: white;
                top: -1px;
            }

            QTabWidget#outputTabs QTabBar::tab {
                background: #f8fafc;
                color: #475569;
                border: 1px solid #dbe3ee;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 9px 18px;
                margin-right: 4px;
                font-size: 13px;
                font-weight: bold;
            }

            QTabWidget#outputTabs QTabBar::tab:selected {
                background: white;
                color: #0f766e;
                border-top: 3px solid #50C9C3;
                padding-top: 7px;
            }

            QTabWidget#outputTabs QTabBar::tab:hover:!selected {
                background: #eef2f7;
            }

            QLabel#outputPanelTitle {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                margin: 8px 8px 6px 8px;
            }

            QListWidget#outputList {
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                background: #f8fafc;
                padding: 8px;
                font-size: 13px;
                margin: 0 8px 8px 8px;
            }
            
            QListWidget#outputList::item {
                border: none;
                padding: 0;
                margin: 0;
                background: transparent;
            }
            
            QListWidget#outputList::item:selected {
                background: transparent;
                border: none;
            }
            
            QListWidget#outputList::item:hover {
                background: transparent;
            }

            QTextBrowser#previewText {
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                background: #0f172a;
                color: #e2e8f0;
                padding: 12px;
                margin: 0 8px 8px 8px;
                font-family: 'Consolas', 'Microsoft YaHei', monospace;
                font-size: 12px;
                line-height: 1.5;
            }
        """)
