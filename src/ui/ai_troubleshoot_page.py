"""
AI 排障页面模块
实现知识库问答和AI智能排障功能
"""

import os
import re
import json
import sys
import time
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QLineEdit,
    QScrollArea, QFrame, QListWidget, QListWidgetItem,
    QDialog, QDialogButtonBox, QMenu, QApplication, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QIcon

from utils import get_config_dir, get_ai_config_path, normalize_ai_configs
from .dialog_icons import dialog_icon

# FAQ2SH Constants
FAQ2SH_PROMPT = """你是FAQ查询脚本生成器，只输出纯.sh文件文本内容，无任何额外解释、符号、代码块。
严格遵守以下强制规则：
1. 固定格式：
#!/bin/bash
# 问题：【业务问题】
# 原因：【故障原因】
# 关键词：【业务关键词】
【仅查询命令】
# 结果判断：【根据脚本执行结果，说明如何确认是该故障原因】

2. 核心铁律：只提取FAQ中问题定位的查询内容，**彻底忽略、完全屏蔽所有解决方法、修改、清理、重启、启动类操作**
3. 绝对禁止命令/操作：docker exec、cd、rm、kill、nohup、&、vi、vim、sed、重启、启动、修改、删除、写入、切换目录
4. 强制命令替换：top → top -b -n 1；docker stats → docker stats --no-stream
5. 仅保留纯只读、非交互式查询命令，无任何操作、无风险、不影响客户环境
6. 禁止重复命令，禁止任何容器操作相关命令
7. 输出前自动二次校验，确保无风险命令，100%适配客户生产环境
8. 结果判断注释需贴合脚本查询结果与故障原因，清晰说明判断依据，不涉及任何操作步骤

FAQ文档内容如下：
---
{content}
---

请直接输出完整的Shell脚本代码。"""

DEFAULT_REQUEST_PARAMS = {
    "temperature": 0.7,
    "max_tokens": 4096,
}

MAX_RETRIES = 3
RETRY_DELAY = 2
REQUEST_TIMEOUT = 120

# FAQ2SH namespace map for docx parsing
FAQ2SH_NSMAP = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
}


class AICollectingDialog(QDialog):
    """知识库导入时的等待弹窗。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI采集中")
        self.setWindowIcon(dialog_icon("robot.svg"))
        self.setModal(True)
        self.setFixedSize(300, 140)
        self._dot_count = 0
        self.setStyleSheet("""
            QDialog {
                background: #ffffff;
                border-radius: 10px;
            }
            QLabel#collectTitle {
                color: #0f172a;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#collectHint {
                color: #64748b;
                font-size: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)

        self.title_label = QLabel("AI采集中...")
        self.title_label.setObjectName("collectTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        hint = QLabel("正在分析知识库内容，请稍候")
        hint.setObjectName("collectHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._animate)
        self.timer.start(380)

    def _animate(self):
        self._dot_count = (self._dot_count + 1) % 4
        self.title_label.setText("AI采集中" + "." * self._dot_count)


class FAQ2SHConvertWorker(QThread):
    finished = pyqtSignal(str, str)

    def __init__(self, api_config: dict, content: str):
        super().__init__()
        self.api_config = api_config
        self.content = content

    def run(self):
        try:
            raw_script = faq2sh_convert(self.api_config, self.content)
            script = faq2sh_clean_script(raw_script)
            self.finished.emit(script, "")
        except Exception as exc:
            self.finished.emit("", str(exc))


def build_openai_chat_url(base_url: str) -> str:
    """Build a compatible chat-completions URL for OpenAI-like providers."""
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return "/chat/completions"
    if cleaned.endswith("/chat/completions"):
        return cleaned
    if cleaned.endswith("/v1"):
        return f"{cleaned}/chat/completions"
    return f"{cleaned}/v1/chat/completions"


def faq2sh_read_docx(file_path: str) -> str:
    """读取docx文件内容为纯文本"""
    text_parts = []
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            if 'word/document.xml' not in z.namelist():
                raise RuntimeError("不是有效的docx文件(缺少word/document.xml)")

            tree = ET.parse(z.open('word/document.xml'))
            root = tree.getroot()

            for prefix, uri in FAQ2SH_NSMAP.items():
                ET.register_namespace(prefix, uri)

            for p in root.iter('{%s}p' % FAQ2SH_NSMAP['w']):
                texts = []
                for t in p.iter('{%s}t' % FAQ2SH_NSMAP['w']):
                    if t.text:
                        texts.append(t.text)
                para_text = ''.join(texts).strip()
                if para_text:
                    text_parts.append(para_text)

            for tbl in root.iter('{%s}tbl' % FAQ2SH_NSMAP['w']):
                for row in tbl.iter('{%s}tr' % FAQ2SH_NSMAP['w']):
                    cells = []
                    for cell in row.iter('{%s}tc' % FAQ2SH_NSMAP['w']):
                        cell_texts = []
                        for t in cell.iter('{%s}t' % FAQ2SH_NSMAP['w']):
                            if t.text:
                                cell_texts.append(t.text)
                        cell_text = ''.join(cell_texts).strip()
                        if cell_text:
                            cells.append(cell_text)
                    if cells:
                        text_parts.append(" | ".join(cells))

    except zipfile.BadZipFile:
        raise RuntimeError(f"文件不是有效的ZIP/docx格式 [{file_path}]")
    except Exception as e:
        raise RuntimeError(f"读取docx文件失败 [{file_path}]: {type(e).__name__}: {e}")

    if not text_parts:
        raise RuntimeError(f"docx文件内容为空 [{file_path}]")
    return "\n".join(text_parts)


def faq2sh_extract_script_from_response(result) -> str:
    """从API响应中提取脚本内容"""
    if isinstance(result, dict):
        if "choices" in result and result["choices"]:
            choice = result["choices"][0]
            if "message" in choice and "content" in choice["message"]:
                return choice["message"]["content"]
            if "text" in choice:
                return choice["text"]
        if "response" in result:
            return result["response"]
        if "output" in result:
            if isinstance(result["output"], dict) and "text" in result["output"]:
                return result["output"]["text"]
            if isinstance(result["output"], str):
                return result["output"]
        if "result" in result:
            return str(result["result"])
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def faq2sh_convert(api_config: dict, content: str) -> str:
    """调用AI API进行FAQ到shell脚本的转换，支持多种API类型"""
    if not api_config:
        raise ValueError("AI配置未加载")
    
    # 获取API配置
    api_type_raw = api_config.get('api_type', 'openai')
    api_key = api_config.get('api_key', '')
    base_url = api_config.get('base_url', '')
    model = api_config.get('model', '')
    
    # API类型映射
    api_type_map = {
        0: "openai",
        1: "anthropic",
        2: "azure",
        3: "ollama",
        5: "openai",
        6: "openai",
        7: "openai",
        8: "openai",
        9: "openai",
        10: "openai",
        12: "openai",
        13: "openai",
        14: "openai"
    }
    
    if isinstance(api_type_raw, int):
        api_type = api_type_map.get(api_type_raw, "openai")
    else:
        api_type = api_type_raw
    
    prompt = FAQ2SH_PROMPT.format(content=content)
    
    import urllib.request
    import urllib.error
    
    try:
        if api_type == 'openai' or api_type == 'azure':
            # OpenAI兼容格式
            url = build_openai_chat_url(base_url)
            
            messages = [
                {"role": "system", "content": "你是一个专业的Shell脚本编写专家。"},
                {"role": "user", "content": prompt}
            ]
            
            data = {
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 4096
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
            
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                result = json.loads(response.read().decode('utf-8'))
                return faq2sh_extract_script_from_response(result)
        
        elif api_type == 'anthropic':
            # Anthropic格式
            url = f"{base_url}/messages"
            
            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "anthropic-dangerous-direct-browser-access": "true"
            }
            
            data = {
                "model": model,
                "max_tokens": 4096,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            }
            
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result['content'][0]['text']
        
        elif api_type == 'ollama':
            # Ollama格式
            url = f"{base_url}/chat"
            
            data = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是一个专业的Shell脚本编写专家。"},
                    {"role": "user", "content": prompt}
                ],
                "stream": False
            }
            
            headers = {"Content-Type": "application/json"}
            
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result['message']['content']
        
        else:
            raise ValueError(f"不支持的API类型: {api_type}")
    
    except urllib.error.HTTPError as e:
        code = e.code
        if code in (401, 403):
            raise RuntimeError(f"认证失败(HTTP {code}) - API Key无效或没有权限")
        elif code == 404:
            raise RuntimeError(f"接口不存在(HTTP 404) - 请检查Base URL路径是否正确")
        elif code == 500:
            raise RuntimeError(f"服务器内部错误(HTTP 500) - API服务端异常，请稍后重试")
        else:
            error_body = e.read().decode('utf-8', errors='ignore')
            try:
                error_json = json.loads(error_body)
                error_msg = error_json.get('error', {}).get('message', error_body)
            except:
                error_msg = error_body
            raise RuntimeError(f"HTTP错误({code}) - {error_msg[:200]}")
    except Exception as e:
        error_msg = str(e)
        if '10054' in error_msg or '远程主机强迫关闭' in error_msg:
            raise RuntimeError(f"连接被远程服务器关闭 - 请检查API配置是否正确或稍后重试")
        if 'timed out' in error_msg.lower() or 'timeout' in error_msg.lower():
            raise RuntimeError(f"请求超时 - API服务器响应太慢或网络延迟过高")
        if 'ConnectionRefusedError' in error_msg or '连接被拒绝' in error_msg:
            raise RuntimeError(f"网络连接失败 - 无法访问API服务器，请检查Base URL和网络状态")
        if 'SSL' in error_msg or 'certificate' in error_msg.lower():
            raise RuntimeError(f"SSL证书错误 - 服务器证书无效或不受信任")
        raise RuntimeError(f"API调用异常: {error_msg}")


def faq2sh_clean_script(script_text: str) -> str:
    """清洗脚本文本，移除代码块标记"""
    script = script_text.strip()
    markers = ["```shell", "```bash", "```sh"]
    for m in markers:
        if script.startswith(m):
            script = script[len(m):].lstrip("\n")
            break
    else:
        if script.startswith("```"):
            script = script[3:].lstrip("\n")
    if script.endswith("```"):
        script = script[:-3].rstrip("\n")
    return script


class FAQ2SHConverter(QThread):
    """FAQ转Shell脚本的工作线程"""
    progress = pyqtSignal(str)  # 进度信息
    finished = pyqtSignal(int, int, str)  # (成功数, 失败数, 错误信息)
    
    def __init__(self, faq_entries: list, api_config: dict):
        super().__init__()
        self.faq_entries = faq_entries
        self.api_config = api_config or {}
        self.success_count = 0
        self.fail_count = 0
    
    def run(self):
        """执行FAQ到shell脚本的转换"""
        api_url = self.api_config.get('base_url', '')
        api_key = self.api_config.get('api_key', '')
        model = self.api_config.get('model', '')
        
        if not api_url or not api_key or not model:
            self.finished.emit(0, len(self.faq_entries), "AI配置不完整，请检查API配置")
            return
        
        for i, faq in enumerate(self.faq_entries):
            question = faq.get('question', '')
            self.progress.emit(f"正在转换: {question[:30]}...")
            
            try:
                # 调用AI转换
                raw_script = faq2sh_convert(self.api_config, faq.get('content', ''))
                script = faq2sh_clean_script(raw_script)
                
                # 更新FAQ条目
                faq['commands'] = script
                self.success_count += 1
            except Exception as e:
                self.fail_count += 1
                self.progress.emit(f"转换失败: {question[:30]}... - {str(e)}")
        
        self.finished.emit(self.success_count, self.fail_count, "")


class AITroubleshootWorker(QThread):
    """AI分析工作线程"""
    finished = pyqtSignal(str, str, str, str, str, str, list, bool)  # (answer, error_message, result_analysis, faq_question, faq_cause, user_question, command_results, is_match)
    
    def __init__(self, faq: dict, user_question: str, ssh_client, api_config: dict):
        super().__init__()
        self.faq = faq
        self.user_question = user_question
        self.ssh_client = ssh_client
        self.api_config = api_config
    
    def run(self):
        """执行FAQ分析和命令"""
        try:
            answer, result_analysis, faq_question, faq_cause, command_results, is_match = self._execute_analysis()
            self.finished.emit(answer, "", result_analysis, faq_question, faq_cause, self.user_question, command_results, is_match)
        except Exception as e:
            self.finished.emit("", str(e), "", "", "", "", [], False)
    
    def _execute_analysis(self) -> tuple:
        """执行FAQ分析和命令"""
        faq_question = self.faq.get('question', '')
        faq_cause = self.faq.get('cause', '')
        commands = self.faq.get('commands', '')
        faq_result_analysis = self.faq.get('result_analysis', '')
        execution_mode = self.faq.get('execution_mode', '远程')  # 默认远程
        
        # 执行FAQ中的命令
        command_results = []
        command_output_text = ""
        
        if commands:
            command_lines = [line.strip() for line in commands.split('\n') if line.strip() and not line.strip().startswith('#')]
            
            for cmd in command_lines:
                # 判断是本地还是远程命令
                is_local = False
                if cmd.startswith('本地:') or cmd.startswith('local:'):
                    is_local = True
                    cmd = re.sub(r'^(本地:|local:)\s*', '', cmd)
                
                command_output_text += f"\n$ {cmd}\n"
                cmd_output = ""
                
                try:
                    if is_local or execution_mode in ['本地', 'local']:
                        # 本地执行
                        result = subprocess.run(
                            cmd,
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                        if result.stdout:
                            cmd_output = result.stdout
                            command_output_text += result.stdout
                        if result.stderr:
                            cmd_output += f"[Stderr] {result.stderr}"
                            command_output_text += f"[Stderr] {result.stderr}"
                        if result.returncode != 0 and not result.stdout and not result.stderr:
                            cmd_output = f"[Exit code: {result.returncode}]"
                            command_output_text += f"[Exit code: {result.returncode}]"
                    elif self.ssh_client and hasattr(self.ssh_client, 'execute'):
                        # 远程执行
                        return_code, output, error = self.ssh_client.execute(cmd)
                        if output:
                            cmd_output = output
                            command_output_text += output
                        if error:
                            cmd_output += f"[Error] {error}"
                            command_output_text += f"[Error] {error}\n"
                except subprocess.TimeoutExpired:
                    cmd_output = "[Timeout] Command execution timed out"
                    command_output_text += "[Timeout] Command execution timed out"
                except Exception as e:
                    cmd_output = f"[Exception] {str(e)}"
                    command_output_text += f"[Exception] {str(e)}\n"
                
                command_results.append({"command": cmd, "output": cmd_output})
        
        # 使用LLM分析命令执行结果，返回(是否匹配, AI分析结论)
        is_match, ai_analysis = self._evaluate_match(command_results, faq_question, faq_cause, faq_result_analysis)
        
        # 构建完整报告
        report = f"""【问题描述】
{faq_question}

【可能原因】
{faq_cause}

【命令执行结果】
{command_output_text if command_output_text else '无'}

【AI智能分析】
{ai_analysis}

【最终结论】
{'符合该问题' if is_match else '不符合该问题原因，需要进一步排查'}
"""
        
        return report, ai_analysis, faq_question, faq_cause, command_results, is_match
    
    def _evaluate_match(self, command_results: list, faq_question: str, faq_cause: str, result_analysis: str) -> tuple:
        """使用LLM分析命令执行结果，返回(是否匹配, AI分析结论)"""
        if not result_analysis and not command_results:
            return False, "无法进行分析：缺少判断依据或命令执行结果"
        
        if not self.api_config:
            # 没有API配置时，使用简单判断
            return self._simple_evaluate_match(command_results, result_analysis)

        configs = normalize_ai_configs(self.api_config)
        if self.api_config.get("models") and configs:
            original_config = self.api_config
            last_hint = ""
            failure_markers = ("API调用失败", "API请求超时", "网络错误", "调用失败")
            try:
                for cfg in configs:
                    self.api_config = cfg
                    is_match, analysis = self._evaluate_match(command_results, faq_question, faq_cause, result_analysis)
                    if not any(marker in analysis for marker in failure_markers):
                        return is_match, analysis
                    last_hint = f"{cfg.get('name', cfg.get('model', '备用模型'))}: {analysis}"
            finally:
                self.api_config = original_config

            is_match, simple_analysis = self._simple_evaluate_match(command_results, result_analysis)
            return is_match, f"{simple_analysis}\n\n（注：所有大模型调用失败：{last_hint}，已使用本地简单分析兜底）"
        
        # 构建命令结果摘要
        result_summary = ""
        for cmd_result in command_results:
            cmd = cmd_result.get('command', '')
            output = cmd_result.get('output', '')[:800]  # 限制输出长度
            result_summary += f"命令: {cmd}\n输出:\n{output}\n\n"
        
        prompt = f"""你是一个专业的系统故障诊断助手。请根据FAQ中的【问题描述】、【可能原因】和【判断依据】来分析【命令执行结果】，给出专业的分析过程和结论。

【FAQ问题描述】
{faq_question}

【可能原因】
{faq_cause}

【判断依据】
{result_analysis}

【命令执行结果】
{result_summary}

请按以下格式进行分析和回复：

**分析过程**
（详细说明你如何根据判断依据来分析每个命令的输出结果）

**结论**
（根据分析过程，给出明确的结论。如果符合该故障原因，回复"符合"；如果不符合，回复"不符合"）

**说明**
（简要说明判断的理由）"""
        
        try:
            import urllib.request
            import urllib.error
            
            api_type_raw = self.api_config.get('api_type', 'openai')
            api_key = self.api_config.get('api_key', '')
            base_url = self.api_config.get('base_url', '')
            model = self.api_config.get('model', '')
            
            # API类型映射
            api_type_map = {
                0: "openai", 1: "anthropic", 2: "azure", 3: "ollama",
                5: "openai", 6: "openai", 7: "openai", 8: "openai", 9: "openai", 10: "openai", 12: "openai", 13: "openai", 14: "openai"
            }
            api_type = api_type_map.get(api_type_raw, "openai") if isinstance(api_type_raw, int) else api_type_raw
            
            timeout = self.api_config.get('api_timeout', 120) if isinstance(self.api_config, dict) else 120
            if api_type == 'openai' or api_type == 'azure':
                url = build_openai_chat_url(base_url)
                data = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1000
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
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    llm_response = result['choices'][0]['message']['content'].strip()
            
            elif api_type == 'ollama':
                url = f"{base_url}/chat"
                data = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False
                }
                headers = {"Content-Type": "application/json"}
                req = urllib.request.Request(
                    url,
                    data=json.dumps(data).encode('utf-8'),
                    headers=headers,
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    llm_response = result['message']['content'].strip()
            else:
                is_match, simple_analysis = self._simple_evaluate_match(command_results, result_analysis)
                return is_match, simple_analysis
            
            # 解析LLM响应
            is_match = "符合" in llm_response and "不符合" not in llm_response
            return is_match, llm_response
            
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='ignore')
            try:
                error_json = json.loads(error_body)
                error_msg = error_json.get('error', {}).get('message', error_body)
            except Exception:
                error_msg = error_body
            print(f"[ERROR] _evaluate_match HTTP failed: {e.code} {error_msg}")
            is_match, simple_analysis = self._simple_evaluate_match(command_results, result_analysis)
            hint = f"API调用失败：HTTP {e.code} - {error_msg[:200] or '服务端未返回详细错误'}"
            return is_match, f"{simple_analysis}\n\n（注：{hint}，已使用本地简单分析兜底）"
        except Exception as e:
            print(f"[ERROR] _evaluate_match failed: {e}")
            is_match, simple_analysis = self._simple_evaluate_match(command_results, result_analysis)
            error_msg = str(e)
            if 'timed out' in error_msg.lower() or 'timeout' in error_msg.lower():
                hint = f"API请求超时（当前超时{timeout}秒），可在AI大模型配置中增加超时时间"
            else:
                hint = f"API调用失败：{error_msg or type(e).__name__}"
            return is_match, f"{simple_analysis}\n\n（注：{hint}，已使用本地简单分析兜底）"
    
    def _simple_evaluate_match(self, command_results: list, result_analysis: str) -> tuple:
        """简单的关键词匹配判断"""
        if not result_analysis or not command_results:
            return False, "无法进行分析：缺少判断依据或命令执行结果"
        
        # 将判断依据转为小写
        criteria_lower = result_analysis.lower()
        
        # 常见的问题指标
        problem_indicators = {
            '磁盘占满': ['100%', 'used 100', '磁盘已满', 'disk full'],
            '高负载': ['load average', 'load:', 'cpu idle', 'idle 0', '负载高'],
            '内存不足': ['mem available', 'available 0', '内存不足', 'out of memory'],
            '进程异常': ['not found', 'failed', 'error', '异常', '失败'],
            '服务异常': ['inactive', 'dead', 'failed', '未运行'],
        }
        
        matched_issues = []
        
        # 检查命令输出中是否包含问题指标
        for cmd_result in command_results:
            output = cmd_result.get('output', '').lower()
            
            # 检查是否匹配问题指标
            for issue_type, indicators in problem_indicators.items():
                if issue_type in criteria_lower:
                    for indicator in indicators:
                        if indicator.lower() in output:
                            matched_issues.append(issue_type)
        
        if matched_issues:
            analysis_text = f"**结论**：符合\n\n**说明**：检测到以下匹配指标：{', '.join(set(matched_issues))}"
            return True, analysis_text
        else:
            analysis_text = "**结论**：不符合\n\n**说明**：命令执行结果未匹配判断依据中的条件"
            return False, analysis_text
    
    def _call_llm(self, prompt: str, data: str) -> str:
        """调用LLM获取结论"""
        if not self.api_config:
            return "AI配置未加载，请在AI大模型配置页面配置API"

        configs = normalize_ai_configs(self.api_config)
        if self.api_config.get("models") and configs:
            original_config = self.api_config
            last_error = ""
            failure_markers = ("API Key未设置", "网络错误", "调用失败", "不支持的API类型")
            try:
                for cfg in configs:
                    self.api_config = cfg
                    result = self._call_llm(prompt, data)
                    if result and not any(marker in result for marker in failure_markers):
                        return result
                    last_error = f"{cfg.get('name', cfg.get('model', '备用模型'))}: {result}"
            finally:
                self.api_config = original_config
            return f"所有大模型调用失败: {last_error}"
        
        api_type_raw = self.api_config.get('api_type', 'openai')
        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', '')
        model = self.api_config.get('model', '')
        
        api_type_map = {
            0: "openai", 1: "anthropic", 2: "azure", 3: "ollama",
            5: "openai", 6: "openai", 7: "openai", 8: "openai", 9: "openai", 10: "openai", 12: "openai", 13: "openai", 14: "openai"
        }
        api_type = api_type_map.get(api_type_raw, "openai") if isinstance(api_type_raw, int) else api_type_raw
        
        if not api_key:
            return "API Key未设置，请在AI大模型配置页面配置"
        
        import urllib.request
        import urllib.error
        
        try:
            if api_type == 'openai' or api_type == 'azure':
                url = build_openai_chat_url(base_url)
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": data}
                ]
                req_data = {
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
                    data=json.dumps(req_data).encode('utf-8'),
                    headers=headers,
                    method='POST'
                )
                timeout = self.api_config.get('api_timeout', 600)
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result['choices'][0]['message']['content']
            
            elif api_type == 'ollama':
                url = f"{base_url}/chat"
                req_data = {
                    "model": model,
                    "messages": [{"role": "user", "content": f"{prompt}\n\n{data}"}],
                    "stream": False
                }
                headers = {"Content-Type": "application/json"}
                req = urllib.request.Request(
                    url,
                    data=json.dumps(req_data).encode('utf-8'),
                    headers=headers,
                    method='POST'
                )
                timeout = self.api_config.get('api_timeout', 600)
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result['message']['content']
            
            else:
                return f"不支持的API类型: {api_type}"
        
        except urllib.error.URLError as e:
            return f"网络错误: {str(e)}"
        except Exception as e:
            return f"调用失败: {str(e)}"
    
    def _call_api(self, prompt: str, data: str) -> str:
        """调用AI API"""
        if not self.api_config:
            return "AI配置未加载，请在AI大模型配置页面配置API"

        configs = normalize_ai_configs(self.api_config)
        if self.api_config.get("models") and configs:
            original_config = self.api_config
            last_error = ""
            failure_markers = (
                "API调用失败", "API调用异常", "API Key未设置", "认证失败",
                "请求超时", "网络错误", "网络连接失败", "连接被远程服务器关闭",
                "接口不存在", "服务器内部错误", "不支持的API类型",
            )
            try:
                for cfg in configs:
                    self.api_config = cfg
                    result = self._call_api(prompt, data)
                    if result and not any(marker in result for marker in failure_markers):
                        return result
                    last_error = f"{cfg.get('name', cfg.get('model', '备用模型'))}: {result}"
            finally:
                self.api_config = original_config
            return f"所有大模型调用失败: {last_error}"
        
        api_type_raw = self.api_config.get('api_type', 'openai')
        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', '')
        model = self.api_config.get('model', '')
        
        api_type_map = {
            0: "openai", 1: "anthropic", 2: "azure", 3: "ollama",
            5: "openai", 6: "openai", 7: "openai", 8: "openai", 9: "openai", 10: "openai", 12: "openai", 13: "openai", 14: "openai"
        }
        api_type = api_type_map.get(api_type_raw, "openai") if isinstance(api_type_raw, int) else api_type_raw
        
        if not api_key:
            return "API Key未设置，请在AI大模型配置页面配置"
        
        import urllib.request
        import urllib.error
        
        try:
            if api_type == 'openai' or api_type == 'azure':
                url = build_openai_chat_url(base_url)
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"请分析以下数据：\n\n{data}"}
                ]
                req_data = {
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
                    data=json.dumps(req_data).encode('utf-8'),
                    headers=headers,
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=self.api_config.get('api_timeout', 120)) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result['choices'][0]['message']['content']
            
            elif api_type == 'ollama':
                url = f"{base_url}/chat"
                req_data = {
                    "model": model,
                    "messages": [{"role": "user", "content": f"{prompt}\n\n{data}"}],
                    "stream": False
                }
                headers = {"Content-Type": "application/json"}
                req = urllib.request.Request(
                    url,
                    data=json.dumps(req_data).encode('utf-8'),
                    headers=headers,
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=self.api_config.get('api_timeout', 120)) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result['message']['content']
            
            return "不支持的API类型"
        
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (401, 403):
                return f"认证失败(HTTP {code}) - API Key无效或没有权限"
            elif code == 404:
                return f"接口不存在(HTTP 404) - 请检查Base URL路径是否正确"
            elif code == 500:
                return f"服务器内部错误(HTTP 500) - API服务端异常，请稍后重试"
            else:
                return f"HTTP错误({code}) - {e.read().decode('utf-8', errors='ignore')[:200]}"
        except Exception as e:
            error_msg = str(e)
            if '10054' in error_msg or '远程主机强迫关闭' in error_msg:
                return f"连接被远程服务器关闭 - 请检查API配置是否正确或稍后重试"
            if 'timed out' in error_msg.lower() or 'timeout' in error_msg.lower():
                return f"请求超时 - API服务器响应太慢或网络延迟过高"
            if 'ConnectionRefusedError' in error_msg or '连接被拒绝' in error_msg:
                return f"网络连接失败 - 无法访问API服务器，请检查Base URL和网络状态"
            return f"API调用异常: {error_msg}"
    
    def _analyze_results(self, command_results: list) -> dict:
        """分析命令结果"""
        findings = []
        warnings = []
        
        for item in command_results:
            output = item.get("output", "")
            cmd = item.get("command", "")
            
            if "df -h" in cmd or "df" in cmd:
                for line in output.split('\n'):
                    if '%' in line and '/dev' in line:
                        parts = line.split()
                        if len(parts) >= 5:
                            try:
                                usage = int(parts[4].replace('%', ''))
                                mount = parts[-1]
                                if usage >= 90:
                                    findings.append(f"磁盘告警: {mount} 使用率 {usage}% (严重)")
                                elif usage >= 80:
                                    findings.append(f"磁盘警告: {mount} 使用率 {usage}%")
                            except:
                                pass
            
            if "load" in cmd.lower() or "uptime" in cmd:
                import re
                loads = re.findall(r'(\d+\.\d+)', output)
                if loads:
                    try:
                        if float(loads[0]) > 10:
                            findings.append(f"CPU负载过高: {loads[0]}")
                        elif float(loads[0]) > 5:
                            findings.append(f"CPU负载较高: {loads[0]}")
                    except:
                        pass
        
        return {"findings": findings, "warnings": warnings, "command_results": command_results}
    
    def _generate_preliminary(self, analysis: dict) -> str:
        """生成初步分析"""
        lines = []
        for f in analysis.get("findings", []):
            lines.append(f"  - {f}")
        return "\n".join(lines) if lines else "未发现明显异常"
    
    def _generate_report(self, question, cause, keywords, result_text, analysis, ai_response, result_analysis=None) -> str:
        """生成分析报告"""
        lines = []
        lines.append("=" * 50)
        lines.append("AI 智能分析报告")
        lines.append("=" * 50)
        lines.append(f"\n【问题描述】\n{question}")
        
        if cause:
            lines.append(f"\n【可能原因】\n{cause}")
        
        if keywords:
            lines.append(f"\n【相关关键词】\n{keywords}")
        
        if ai_response:
            lines.append(f"\n【AI智能分析】\n{ai_response}")
        
        findings = analysis.get("findings", [])
        if findings:
            lines.append(f"\n【系统检测发现】")
            for f in findings:
                lines.append(f"  - {f}")
        
        lines.append(f"\n【命令执行结果】\n{result_text}")
        
        return "\n".join(lines)
from PyQt6.QtGui import QFont, QColor
from qfluentwidgets import PrimaryPushButton, MessageBox, ScrollArea


class FAQPreviewDialog(QDialog):
    """FAQ预览对话框"""
    
    def __init__(self, faq: dict, parent=None):
        super().__init__(parent)
        self.faq = faq
        self.setup_ui()
    
    def setup_ui(self):
        """设置UI"""
        self.setWindowTitle("FAQ 预览")
        self.setWindowIcon(dialog_icon("book.svg"))
        self.setMinimumSize(600, 500)
        self.setStyleSheet("""
            QDialog {
                background: white;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 标题
        title = QLabel("FAQ 详情")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1f2937;")
        layout.addWidget(title)
        
        # 创建可滚动区域
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background: #f9fafb;
            }
        """)
        
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(12)
        content_layout.setContentsMargins(15, 15, 15, 15)
        
        # 问题
        question = self.faq.get('question', '')
        if question:
            content_layout.addWidget(self._create_section("问题描述", question, "#50C9C3"))
        
        # 原因
        cause = self.faq.get('cause', '')
        if cause:
            content_layout.addWidget(self._create_section("可能原因", cause, "#F59E0B"))
        
        # 关键词
        keywords = self.faq.get('keywords', '')
        if keywords:
            content_layout.addWidget(self._create_section("关键词", keywords, "#8B5CF6"))
        
        # 命令
        commands = self.faq.get('commands', '')
        if commands:
            content_layout.addWidget(self._create_section("执行命令", commands, "#EF4444", is_code=True))
        
        # 结果判断
        result_analysis = self.faq.get('result_analysis', '')
        if result_analysis:
            content_layout.addWidget(self._create_section("结果判断", result_analysis, "#10B981"))
        
        # 答案
        answer = self.faq.get('answer', '')
        if answer:
            content_layout.addWidget(self._create_section("答案", answer, "#6366F1"))
        
        content_layout.addStretch()
        
        scroll.setWidget(content_widget)
        layout.addWidget(scroll, 1)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        close_btn = QPushButton("关闭")
        close_btn.setMinimumSize(100, 36)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #e5e7eb;
                color: #374151;
                border: none;
                border-radius: 6px;
                font-size: 14px;
            }
            QPushButton:hover {
                background: #d1d5db;
            }
        """)
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
    
    def _create_section(self, title: str, content: str, color: str, is_code: bool = False) -> QFrame:
        """创建内容区块"""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background: white;
                border-left: 4px solid %s;
                border-radius: 4px;
                padding: 10px;
            }
        """ % color)
        
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 5, 10, 5)
        
        title_label = QLabel(title)
        title_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {color};")
        layout.addWidget(title_label)
        
        content_label = QLabel(content)
        content_label.setWordWrap(True)
        if is_code:
            content_label.setStyleSheet("font-family: Consolas, Monaco, monospace; font-size: 12px; color: #374151; background: #f3f4f6; padding: 8px; border-radius: 4px;")
        else:
            content_label.setStyleSheet("font-size: 13px; color: #4b5563;")
        
        layout.addWidget(content_label)
        
        return frame


class LocalSearchDialog(QDialog):
    """本地知识库搜索对话框"""
    
    def __init__(self, faqs: list, parent=None):
        super().__init__(parent)
        self.faqs = faqs
        self.init_ui()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("本地知识库搜索")
        self.setWindowIcon(dialog_icon("book.svg"))
        self.setMinimumSize(1000, 700)
        self.setStyleSheet("""
            QDialog {
                background: white;
            }
            QLabel {
                color: #374151;
            }
            QLineEdit {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 2px solid #50C9C3;
            }
            QListWidget {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 5px;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 10px;
                border-bottom: 1px solid #f3f4f6;
            }
            QListWidget::item:selected {
                background: #50C9C3;
                color: white;
            }
            QTextEdit {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 标题
        title = QLabel("本地知识库")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1f2937;")
        layout.addWidget(title)
        
        # 搜索框
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索知识库...")
        self.search_input.textChanged.connect(self.on_search)
        search_layout.addWidget(self.search_input, 1)
        layout.addLayout(search_layout)
        
        # 列表和详情区域
        content_layout = QHBoxLayout()
        content_layout.setSpacing(15)
        
        # 左侧：搜索结果列表
        self.result_list = QListWidget()
        self.result_list.itemClicked.connect(self.on_item_clicked)
        content_layout.addWidget(self.result_list, 1)
        
        # 右侧：详情区域
        detail_frame = QFrame()
        detail_frame.setStyleSheet("""
            QFrame {
                background: #f9fafb;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        detail_layout = QVBoxLayout(detail_frame)
        
        detail_title = QLabel("详情")
        detail_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151;")
        detail_layout.addWidget(detail_title)
        
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        detail_layout.addWidget(self.detail_text, 1)
        
        content_layout.addWidget(detail_frame, 2)
        
        layout.addLayout(content_layout, 1)
        
        # 初始加载
        self.load_results()
    
    def load_results(self, search_text=""):
        """加载搜索结果"""
        self.result_list.clear()
        
        for faq in self.faqs:
            question = faq.get('question', '')
            cause = faq.get('cause', '')
            
            # 搜索过滤
            if search_text:
                if search_text.lower() not in question.lower() and search_text.lower() not in cause.lower():
                    continue
            
            # 截断显示
            display_text = question[:60] + "..." if len(question) > 60 else question
            self.result_list.addItem(display_text)
    
    def on_search(self, text: str):
        """搜索"""
        self.load_results(text)
    
    def on_item_clicked(self, item: QListWidgetItem):
        """点击列表项，显示详情"""
        index = self.result_list.row(item)
        
        # 重新获取对应的FAQ（考虑搜索过滤）
        search_text = self.search_input.text()
        matched_faqs = []
        for faq in self.faqs:
            question = faq.get('question', '')
            cause = faq.get('cause', '')
            if search_text:
                if search_text.lower() not in question.lower() and search_text.lower() not in cause.lower():
                    continue
            matched_faqs.append(faq)
        
        if 0 <= index < len(matched_faqs):
            faq = matched_faqs[index]
            
            # 构建详情文本
            detail = f"""【问题描述】
{faq.get('question', '无')}

【可能原因】
{faq.get('cause', '无')}

【判断依据】
{faq.get('result_analysis', '无')}

【答案/处理建议】
{faq.get('answer', '无')}

【诊断命令】
{faq.get('commands', '无')}
"""
            self.detail_text.setPlainText(detail)


class KnowledgeBaseManagerDialog(QDialog):
    """知识库管理对话框 - 增删改查"""
    
    # 添加信号：知识库已更新
    knowledge_base_updated = pyqtSignal()
    
    def __init__(self, faqs: list, current_product: str, parent=None):
        super().__init__(parent)
        self.faqs = faqs
        self.current_product = current_product
        self.current_index = -1
        self.api_config = None  # AI配置
        self.load_ai_config()
        self.setup_ui()
        self.load_faqs()
    
    def load_ai_config(self):
        """加载AI配置"""
        import json
        config_file = get_ai_config_path()

        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    self.api_config = json.load(f)
                
                # 清理配置值中的反引号和多余空格
                if self.api_config:
                    if 'base_url' in self.api_config:
                        self.api_config['base_url'] = self.api_config['base_url'].replace('`', '').strip()
                    if 'api_key' in self.api_config:
                        self.api_config['api_key'] = self.api_config['api_key'].replace('`', '').strip()
                    if 'model' in self.api_config:
                        self.api_config['model'] = self.api_config['model'].replace('`', '').strip()
            except Exception as e:
                print(f"[AI Config] Failed to load config: {e}")
                self.api_config = None
        else:
            self.api_config = None

    def _book_icon(self) -> QIcon:
        """知识库弹窗统一使用书本图标。"""
        icon_path = Path(__file__).resolve().parents[2] / "icons" / "book.svg"
        return QIcon(str(icon_path))
    
    def save_faqs(self):
        """保存FAQ知识库 - 保留其他产品的FAQ"""
        config_dir = Path(get_config_dir())
        config_file = config_dir / "faqs.json"
        
        try:
            # 先读取现有的所有FAQ
            all_faqs = []
            if config_file.exists():
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                        if isinstance(existing_data, list):
                            # 保留其他产品的FAQ
                            all_faqs = [f for f in existing_data if f.get('product', '') != self.current_product]
                except Exception:
                    pass
            
            # 合并当前产品的FAQ和其他产品的FAQ
            all_faqs.extend(self.faqs)
            
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(all_faqs, f, indent=2, ensure_ascii=False)
            print(f"[DEBUG] save_faqs: saved {len(self.faqs)} FAQs for {self.current_product}, total {len(all_faqs)} FAQs")
            
            # 发出信号通知界面刷新
            self.knowledge_base_updated.emit()
        except Exception as e:
            print(f"[ERROR] save_faqs: {e}")
    
    def setup_ui(self):
        """设置UI"""
        self.setWindowTitle("知识库管理")
        self.setWindowIcon(self._book_icon())
        self.setMinimumSize(900, 600)
        self.setStyleSheet("""
            QDialog {
                background: white;
            }
            QLabel {
                color: #374151;
            }
            QPushButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #40b0aa;
            }
            QPushButton:disabled {
                background: #d1d5db;
            }
            QLineEdit, QTextEdit {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 2px solid #50C9C3;
            }
            QListWidget {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 5px;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 10px;
                border-bottom: 1px solid #f3f4f6;
            }
            QListWidget::item:selected {
                background: #50C9C3;
                color: white;
            }
        """)
        
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # 左侧：列表区域
        left_panel = QFrame()
        left_panel.setStyleSheet("""
            QFrame {
                background: #f9fafb;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(10)
        
        # 搜索框
        search_layout = QHBoxLayout()
        search_label = QLabel("搜索:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索问题...")
        self.search_input.textChanged.connect(self.on_search)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input, 1)
        left_layout.addLayout(search_layout)
        
        # FAQ列表
        self.faq_list = QListWidget()
        left_layout.addWidget(self.faq_list, 1)
        
        # 导入/导出按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        
        self.import_btn = QPushButton("导入")
        self.import_btn.setStyleSheet("""
            QPushButton {
                background: #3b82f6;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #2563eb;
            }
        """)
        self.import_btn.clicked.connect(self.on_import)
        btn_layout.addWidget(self.import_btn)
        
        self.export_btn = QPushButton("导出")
        self.export_btn.setStyleSheet("""
            QPushButton {
                background: #10b981;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #059669;
            }
        """)
        self.export_btn.clicked.connect(self.on_export)
        btn_layout.addWidget(self.export_btn)
        
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background: #ef4444;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #dc2626;
            }
        """)
        self.delete_btn.clicked.connect(self.on_delete_faq)
        btn_layout.addWidget(self.delete_btn)
        
        self.view_btn = QPushButton("查看详情")
        self.view_btn.setStyleSheet("""
            QPushButton {
                background: #8b5cf6;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #7c3aed;
            }
        """)
        self.view_btn.clicked.connect(self.on_view_detail)
        btn_layout.addWidget(self.view_btn)

        common_kb_btn_style = """
            QPushButton {
                background: #ffffff;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f8fafc;
                border-color: #50C9C3;
                color: #0f766e;
            }
        """
        for button in (self.import_btn, self.export_btn, self.view_btn):
            button.setMinimumHeight(34)
            button.setStyleSheet(common_kb_btn_style)
        self.delete_btn.setMinimumHeight(34)
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background: #ffffff;
                color: #b91c1c;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #fff7f7;
                border-color: #fca5a5;
            }
        """)
        
        left_layout.addLayout(btn_layout)
        
        main_layout.addWidget(left_panel, 1)
    
    def load_faqs(self):
        """加载FAQ列表"""
        self.faq_list.clear()
        for faq in self.faqs:
            question = faq.get('question', '')[:50]
            self.faq_list.addItem(question)
    
    def on_search(self, text: str):
        """搜索FAQ"""
        self.faq_list.clear()
        if not text:
            for faq in self.faqs:
                question = faq.get('question', '')[:50]
                self.faq_list.addItem(question)
        else:
            for faq in self.faqs:
                question = faq.get('question', '')
                if text.lower() in question.lower():
                    self.faq_list.addItem(question[:50])
    
    def on_delete_faq(self):
        """删除选中的FAQ"""
        from PyQt6.QtWidgets import QMessageBox
        
        current_row = self.faq_list.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "未选中", "请先在列表中选择要删除的知识库条目")
            return
        
        # 获取当前显示的问题（可能是搜索结果）
        question_text = self.faq_list.item(current_row).text()
        
        # 在完整列表中找到对应的FAQ索引
        faq_index = -1
        for i, faq in enumerate(self.faqs):
            if faq.get('question', '')[:50] == question_text:
                faq_index = i
                break
        
        if faq_index < 0:
            QMessageBox.warning(self, "错误", "未找到要删除的知识库条目")
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除以下知识库吗？\n\n{question_text}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            removed_faq = self.faqs.pop(faq_index)
            print(f"[DEBUG] Deleted FAQ: {removed_faq.get('question', '')[:50]}")
            self.save_faqs()
            self.load_faqs()
            QMessageBox.information(self, "删除成功", "知识库已删除")
    
    def on_view_detail(self):
        """查看选中FAQ的详细内容"""
        from PyQt6.QtWidgets import QMessageBox
        
        current_row = self.faq_list.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "未选中", "请先在列表中选择要查看的知识库条目")
            return
        
        question_text = self.faq_list.item(current_row).text()
        
        selected_faq = None
        for faq in self.faqs:
            if faq.get('question', '')[:50] == question_text:
                selected_faq = faq
                break
        
        if not selected_faq:
            QMessageBox.warning(self, "错误", "未找到要查看的知识库条目")
            return
        
        detail_dialog = QDialog(self)
        detail_dialog.setWindowTitle("知识库详情")
        detail_dialog.setWindowIcon(self._book_icon())
        detail_dialog.resize(760, 620)
        detail_dialog.setMinimumSize(680, 520)
        detail_dialog.setStyleSheet("""
            QDialog {
                background: #f3f7fb;
            }
            QLabel#detailTitle {
                color: #0f172a;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#detailSubtitle {
                color: #64748b;
                font-size: 12px;
            }
            QFrame#detailHeader {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
            }
            QFrame#detailCard {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
            QLabel#fieldLabel {
                color: #334155;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#fieldValue {
                color: #1f2937;
                font-size: 13px;
                line-height: 1.5;
            }
            QLabel#codeValue {
                color: #111827;
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 10px;
                font-family: Consolas, "Microsoft YaHei";
                font-size: 12px;
            }
            QPushButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 9px 22px;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #40b0aa;
            }
        """)
        
        outer_layout = QVBoxLayout(detail_dialog)
        outer_layout.setSpacing(12)
        outer_layout.setContentsMargins(18, 18, 18, 14)

        header = QFrame()
        header.setObjectName("detailHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 14, 18, 14)
        header_layout.setSpacing(12)

        icon_label = QLabel("📖")
        icon_label.setStyleSheet("font-size: 28px;")
        header_layout.addWidget(icon_label)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(2)
        title_label = QLabel("知识库详情")
        title_label.setObjectName("detailTitle")
        subtitle_label = QLabel(f"所属产品：{selected_faq.get('product', self.current_product) or self.current_product or '未指定'}")
        subtitle_label.setObjectName("detailSubtitle")
        header_text_layout.addWidget(title_label)
        header_text_layout.addWidget(subtitle_label)
        header_layout.addLayout(header_text_layout, 1)
        outer_layout.addWidget(header)
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                border: none;
                background: #e5e7eb;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #d1d5db;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
        """)
        
        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content_widget)
        layout.setSpacing(12)
        layout.setContentsMargins(2, 2, 10, 2)

        def normalize_value(value):
            if value is None:
                return ""
            if isinstance(value, list):
                return "\n".join(str(v) for v in value if str(v).strip())
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False, indent=2)
            return str(value).strip()

        def add_field(label_text, value, code=False):
            value_text = normalize_value(value)
            if not value_text:
                return
            if code:
                wrapped_lines = []
                for raw_line in value_text.splitlines():
                    line = raw_line
                    while len(line) > 110:
                        wrapped_lines.append(line[:110])
                        line = line[110:]
                    wrapped_lines.append(line)
                value_text = "\n".join(wrapped_lines)

            card = QFrame()
            card.setObjectName("detailCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(8)

            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            card_layout.addWidget(label)

            value_label = QLabel(value_text)
            value_label.setObjectName("codeValue" if code else "fieldValue")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card_layout.addWidget(value_label)

            layout.addWidget(card)
        
        fields = [
            ("问题", selected_faq.get('question', '')),
            ("原因", selected_faq.get('cause', '')),
            ("诊断命令", selected_faq.get('commands', '')),
            ("结果判断", selected_faq.get('result_analysis', '')),
            ("关键词", selected_faq.get('keywords', '')),
            ("所属产品", selected_faq.get('product', '')),
        ]
        
        for label_text, value in fields:
            add_field(label_text, value, code=(label_text == "诊断命令"))
        
        layout.addStretch()
        
        scroll_area.setWidget(content_widget)
        outer_layout.addWidget(scroll_area, 1)
        
        # 关闭按钮放在底部
        btn_container = QWidget()
        btn_container.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(detail_dialog.accept)
        btn_layout.addWidget(close_btn)
        outer_layout.addWidget(btn_container)
        
        detail_dialog.exec()
    
    def on_import(self):
        """导入知识库文件"""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择知识库文件",
            "",
            "支持的文件 (*.docx *.doc *.pdf *.md *.txt *.json *.xlsx *.xls *.csv);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        # DOCX文件需要特殊处理：使用FAQ2SH转换
        if file_path.endswith('.docx') or file_path.endswith('.doc'):
            self._import_docx_with_faq2sh(file_path)
            return
        
        added = 0
        skipped = 0
        existing_questions = {faq.get('question', '').strip().lower() for faq in self.faqs}
        
        try:
            if file_path.endswith('.json'):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                if isinstance(data, list):
                    for faq in data:
                        question = faq.get('question', '').strip()
                        if question.lower() in existing_questions:
                            skipped += 1
                            continue
                        faq['product'] = self.current_product
                        self.faqs.append(faq)
                        existing_questions.add(question.lower())
                        added += 1
                elif isinstance(data, dict) and 'faqs' in data:
                    for faq in data['faqs']:
                        question = faq.get('question', '').strip()
                        if question.lower() in existing_questions:
                            skipped += 1
                            continue
                        faq['product'] = self.current_product
                        self.faqs.append(faq)
                        existing_questions.add(question.lower())
                        added += 1
                elif isinstance(data, dict) and 'question' in data:
                    # 单条FAQ
                    question = data.get('question', '').strip()
                    if question.lower() not in existing_questions:
                        data['product'] = self.current_product
                        self.faqs.append(data)
                        added = 1
                    else:
                        skipped = 1
                    
            elif file_path.endswith('.txt'):
                # TXT格式：按【问题：】或【问题】标题分隔
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                import re
                
                # 标准化换行
                content = content.replace('\r\n', '\n').replace('\r', '\n')
                
                # 定义字段提取模式
                field_patterns = {
                    'question': [r'问题[：:]\s*【(.+?)】', r'问题[：:]\s*(.+?)(?=\n#|\n$)'],
                    'cause': [r'原因[：:]\s*【(.+?)】', r'原因[：:]\s*(.+?)(?=\n#|\n$)'],
                    'result_analysis': [r'结果判断[：:]\s*【(.+?)】', r'判断依据[：:]\s*【(.+?)】'],
                }
                
                # 按问题标题分割 - 匹配"问题：【...】"或"问题：..."
                question_splits = re.split(r'(?=\n*问题[：:])', content)
                
                for entry in question_splits:
                    entry = entry.strip()
                    if not entry or '问题' not in entry:
                        continue
                    
                    question = ''
                    cause = ''
                    commands = ''
                    result_analysis = ''
                    answer = ''
                    
                    # 提取问题
                    for pattern in field_patterns['question']:
                        match = re.search(pattern, entry, re.DOTALL)
                        if match:
                            question = match.group(1).strip()
                            break
                    
                    # 提取原因
                    for pattern in field_patterns['cause']:
                        match = re.search(pattern, entry, re.DOTALL)
                        if match:
                            cause = match.group(1).strip()
                            break
                    
                    # 提取结果判断
                    for pattern in field_patterns['result_analysis']:
                        match = re.search(pattern, entry, re.DOTALL)
                        if match:
                            result_analysis = match.group(1).strip()
                            break
                    
                    # 提取命令 - 在#结果判断之前的行（排除#开头的标题行）
                    lines = entry.split('\n')
                    command_lines = []
                    in_result_section = False
                    
                    for line in lines:
                        line_stripped = line.strip()
                        # 跳过标题行
                        if line_stripped.startswith('#') and '结果判断' not in line_stripped:
                            continue
                        if '结果判断' in line_stripped:
                            in_result_section = True
                            continue
                        if in_result_section:
                            continue
                        # 收集命令（不包含#的行）
                        if line_stripped and not line_stripped.startswith('#'):
                            # 过滤掉空行和只有标题的行
                            if line_stripped and not line_stripped.startswith('问题') and not line_stripped.startswith('原因') and not line_stripped.startswith('关键词'):
                                command_lines.append(line_stripped)
                    
                    commands = '\n'.join(command_lines).strip()
                    
                    # 只有有问题的条目才添加，且检查重复
                    if question:
                        question_lower = question.strip().lower()
                        if question_lower in existing_questions:
                            skipped += 1
                            continue
                        faq_entry = {
                            'question': question,
                            'cause': cause,
                            'commands': commands,
                            'result_analysis': result_analysis,
                            'answer': answer,
                            'product': self.current_product
                        }
                        self.faqs.append(faq_entry)
                        existing_questions.add(question_lower)
                        added += 1
            
            if added > 0 or skipped > 0:
                self.save_faqs()
                self.load_faqs()
                msg = f"成功导入 {added} 条知识库"
                if skipped > 0:
                    msg += f"\n重复跳过 {skipped} 条"
                QMessageBox.information(self, "导入完成", msg)
            elif skipped > 0:
                QMessageBox.warning(self, "导入完成", f"知识库已存在，重复跳过 {skipped} 条")
            else:
                QMessageBox.warning(self, "导入失败", "未找到有效的知识库条目")
                
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"导入时出错:\n{str(e)}")
    
    def _import_docx_with_faq2sh(self, file_path: str):
        """使用FAQ2SH工具导入Word文档"""
        from PyQt6.QtWidgets import QMessageBox
        
        # 检查AI配置
        if not self.api_config:
            QMessageBox.warning(self, "AI配置缺失", "请先在AI大模型配置页面配置API信息")
            return
        
        api_url = self.api_config.get('base_url', '')
        api_key = self.api_config.get('api_key', '')
        model = self.api_config.get('model', '')
        
        if not api_url or not api_key or not model:
            QMessageBox.warning(self, "AI配置不完整", "请检查API地址、密钥和模型是否已配置")
            return
        
        try:
            # 读取docx内容
            content = faq2sh_read_docx(file_path)
            if not content.strip():
                QMessageBox.warning(self, "文档为空", "Word文档内容为空")
                return
            
            # 先提取问题，检查是否重复
            existing_questions = {faq.get('question', '').strip().lower() for faq in self.faqs}
            import re
            question_match = re.search(r'问题[：:]\s*【(.+?)】', content)
            if not question_match:
                question_match = re.search(r'问题[：:]\s*[:：]\s*(.+?)(?=\n)', content)
            
            if question_match:
                extracted_question = question_match.group(1).strip()
                if extracted_question.lower() in existing_questions:
                    QMessageBox.warning(self, "知识库已存在",
                        f"该知识库已存在，无需重复导入:\n\n{extracted_question[:50]}...")
                    return

            # 调用FAQ2SH API转换
            wait_dialog = AICollectingDialog(self)
            result = {"script": "", "error": ""}
            self._faq2sh_worker = FAQ2SHConvertWorker(self.api_config, content)

            def on_convert_finished(script_text: str, error_text: str):
                result["script"] = script_text
                result["error"] = error_text
                wait_dialog.accept()

            self._faq2sh_worker.finished.connect(on_convert_finished)
            self._faq2sh_worker.start()
            wait_dialog.exec()

            if result["error"]:
                QMessageBox.warning(self, "导入失败", f"FAQ2SH转换失败:\n{result['error']}")
                return

            script = result["script"]
            
            if not script.strip():
                QMessageBox.warning(self, "转换失败", "AI返回的脚本内容为空")
                return
            
            # 解析生成的shell脚本
            faq_entry = self._parse_faq2sh_script(script)
            if faq_entry:
                # 再次检查解析后的问题是否重复
                final_question = faq_entry.get('question', '').strip().lower()
                if final_question in existing_questions:
                    QMessageBox.warning(self, "知识库已存在", 
                        f"该知识库已存在，无需重复导入:\n\n{final_question[:50]}...")
                    return
                
                faq_entry['product'] = self.current_product
                faq_entry['content'] = content  # 保留原始内容
                
                # 如果commands为空，从content中提取问题定位部分的命令
                if not faq_entry.get('commands', '').strip():
                    extracted_commands = self._extract_commands_from_content(content)
                    if extracted_commands:
                        faq_entry['commands'] = extracted_commands
                
                self.faqs.append(faq_entry)
                self.save_faqs()
                self.load_faqs()
                QMessageBox.information(self, "导入成功", 
                    f"已成功导入知识库并生成Shell脚本\n\n问题: {faq_entry.get('question', 'N/A')[:50]}...")
            else:
                QMessageBox.warning(self, "解析失败", "无法解析生成的Shell脚本格式")
            
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"FAQ2SH转换失败:\n{str(e)}")
    
    def _parse_faq2sh_script(self, script: str) -> dict:
        """解析FAQ2SH生成的Shell脚本，提取FAQ字段"""
        import re
        
        faq = {
            'question': '',
            'cause': '',
            'commands': '',
            'result_analysis': '',
            'answer': ''
        }
        
        # 提取问题
        question_match = re.search(r'#\s*问题[：:]\s*【(.+?)】', script)
        if not question_match:
            question_match = re.search(r'#\s*问题[：:]\s*(.+?)(?=\n)', script)
        if question_match:
            faq['question'] = question_match.group(1).strip()
        
        # 提取原因
        cause_match = re.search(r'#\s*原因[：:]\s*【(.+?)】', script)
        if not cause_match:
            cause_match = re.search(r'#\s*原因[：:]\s*(.+?)(?=\n)', script)
        if cause_match:
            faq['cause'] = cause_match.group(1).strip()
        
        # 提取关键词
        keywords_match = re.search(r'#\s*关键词[：:]\s*【(.+?)】', script)
        if keywords_match:
            faq['keywords'] = keywords_match.group(1).strip()
        
        # 提取结果判断
        result_match = re.search(r'#\s*结果判断[：:]\s*(.+?)(?:\n|$)', script)
        if result_match:
            faq['result_analysis'] = result_match.group(1).strip()
        
        # 提取命令（shebang之后、结果判断之前的所有非注释行）
        lines = script.split('\n')
        command_lines = []
        found_shebang = False
        
        for line in lines:
            line_stripped = line.strip()
            
            # 找到shebang
            if line_stripped.startswith('#!/bin/bash'):
                found_shebang = True
                continue
            
            if not found_shebang:
                continue
            
            # 遇到结果判断注释，停止
            if re.match(r'^#\s*结果判断', line_stripped):
                break
            
            # 跳过其他注释行（问题、原因、关键词等）
            if line_stripped.startswith('#'):
                continue
            
            # 收集命令行
            if line_stripped:
                command_lines.append(line_stripped)
        
        faq['commands'] = '\n'.join(command_lines).strip()
        
        return faq
    
    def _extract_commands_from_content(self, content: str) -> str:
        """从FAQ内容中提取问题定位部分的查询命令"""
        import re
        
        commands = []
        
        # 查找"问题定位"部分
        problem_section_match = re.search(r'问题定位[：:]\s*(.*?)(?=解决方法|效果确认|$)', content, re.DOTALL)
        if problem_section_match:
            problem_section = problem_section_match.group(1)
            
            # 提取常见的只读查询命令
            # df, free, top, ps, cat, ls, grep, find, stat, netstat, ss, ip, curl, docker ps/stats/logs/images/inspect
            command_patterns = [
                r'\b(df\s+-h)\b',
                r'\b(df)\b',
                r'\b(free\s+-h)\b',
                r'\b(free)\b',
                r'\b(top\s+-b\s+-n\s+\d+)\b',
                r'\b(top)\b',
                r'\b(ps\s+aux)\b',
                r'\b(ps\s+-ef)\b',
                r'\b(cat\s+\S+)\b',
                r'\b(ls\s+-la?\s*\S*)\b',
                r'\b(ls\s+\S+)\b',
                r'\b(grep\s+\S+)\b',
                r'\b(find\s+\S+)\b',
                r'\b(stat\s+\S+)\b',
                r'\b(netstat\s+\S+)\b',
                r'\b(ss\s+\S+)\b',
                r'\b(ip\s+\S+)\b',
                r'\b(curl\s+\S+)\b',
                r'\b(docker\s+ps)\b',
                r'\b(docker\s+stats\s+--no-stream)\b',
                r'\b(docker\s+stats)\b',
                r'\b(docker\s+logs\s+\S+)\b',
                r'\b(docker\s+images)\b',
                r'\b(docker\s+inspect\s+\S+)\b',
                r'\b(lscpu)\b',
                r'\b(lsmem)\b',
                r'\b(uname\s+-a)\b',
                r'\b(hostname)\b',
                r'\b(uptime)\b',
                r'\b(systemctl\s+status\s+\S+)\b',
                r'\b(journalctl\s+\S+)\b',
            ]
            
            for pattern in command_patterns:
                matches = re.findall(pattern, problem_section)
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0]
                    cmd = match.strip()
                    # 过滤掉危险命令
                    if cmd and not any(danger in cmd for danger in ['rm ', 'kill ', 'shutdown', 'reboot', 'vi ', 'vim ', 'nohup', 'exec -it']):
                        if cmd not in commands:
                            commands.append(cmd)
        
        return '\n'.join(commands)
    
    def on_export(self):
        """导出知识库"""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        
        if not self.faqs:
            QMessageBox.warning(self, "提示", "知识库为空，无需导出")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出知识库",
            f"knowledge_base_{self.current_product}.json",
            "JSON文件 (*.json)"
        )
        
        if file_path:
            if not file_path.endswith('.json'):
                file_path += '.json'
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(self.faqs, f, indent=2, ensure_ascii=False)
                QMessageBox.information(self, "导出成功", f"知识库已导出到:\n{file_path}")
            except Exception as e:
                QMessageBox.warning(self, "导出失败", f"导出时出错:\n{str(e)}")


class AITroubleshootPage(QWidget):
    """AI 排障页面"""
    
    def __init__(self):
        """初始化 AI 排障页面"""
        super().__init__()
        self.faqs = []
        self.ssh_client = None  # SSH客户端用于执行命令
        self.chat_history = []   # 聊天记录 [{"role": "user"/"ai", "content": str}]
        self.last_ai_response = ""  # 保存最后一次AI回复用于复制
        self.api_config = None  # AI API配置
        self.current_product = ""  # 当前产品名称
        self.server_identifier = ""  # 当前服务器标识，用于隔离不同服务器的聊天记录
        self.init_ui()
        self.load_ai_config()
        self.load_faqs()  # 初始加载知识库
    
    def set_ssh_client(self, ssh_client, server_host: str = ""):
        """设置SSH客户端，清空聊天记录（切换服务器时）"""
        # 如果切换了服务器，清空聊天记录
        if server_host and self.server_identifier and server_host != self.server_identifier:
            self._clear_conversation_state()
        self.ssh_client = ssh_client
        self.server_identifier = server_host
    
    def set_product(self, product_name: str):
        """设置当前产品，加载该产品的知识库"""
        product_changed = self.current_product != product_name
        self.current_product = product_name
        if product_changed:
            self._clear_conversation_state()
        self.load_faqs()

    def _clear_conversation_state(self):
        """清空当前排障对话和报告缓存，避免跨产品/服务器残留。"""
        self.chat_history = []
        self.last_ai_response = ""
        self.current_ai_report = ""
        self.current_ai_report_meta = {}
        if hasattr(self, 'chat_input'):
            self.chat_input.clear()
        self._clear_chat_display()
    
    def _clear_chat_display(self):
        """清空聊天显示区域"""
        if not hasattr(self, 'chat_layout'):
            return
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()
    
    def init_ui(self):
        """初始化 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # 标题栏
        header_layout = QHBoxLayout()
        title_label = QLabel("AI 智能排障")
        title_label.setObjectName("pageTitle")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #1f2937;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        self.chat_send_btn = PrimaryPushButton("AI分析")
        self.chat_send_btn.setObjectName("troubleActionBtn")
        self.chat_send_btn.setFixedHeight(42)
        self.chat_send_btn.setMinimumWidth(116)
        self.chat_send_btn.setStyleSheet("""
            PrimaryPushButton#troubleActionBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 14px;
                padding: 0 18px;
            }
            PrimaryPushButton#troubleActionBtn:hover {
                background: #3BA8A2;
            }
        """)
        self.chat_send_btn.clicked.connect(self.on_send_chat_message)
        header_layout.addWidget(self.chat_send_btn)
        
        self.kb_btn = QPushButton("本地搜索")
        self.kb_btn.setObjectName("troubleSecondaryBtn")
        self.kb_btn.setFixedHeight(42)
        self.kb_btn.setMinimumWidth(116)
        self.kb_btn.setStyleSheet("""
            QPushButton#troubleSecondaryBtn {
                background: white;
                color: #1f2937;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
                padding: 0 18px;
            }
            QPushButton#troubleSecondaryBtn:hover {
                background: #f8fafc;
                border-color: #50C9C3;
            }
        """)
        self.kb_btn.clicked.connect(self.on_local_search)
        header_layout.addWidget(self.kb_btn)
        
        layout.addLayout(header_layout)
        
        # 知识库状态
        self.status_label = QLabel("知识库已加载: 0 条问答")
        self.status_label.setStyleSheet("color: #6b7280; font-size: 12px; padding: 5px;")
        layout.addWidget(self.status_label)
        
        # 聊天输入区域（移到标题下方）
        input_layout = QHBoxLayout()
        input_layout.setSpacing(10)
        
        self.chat_input = QLineEdit()
        self.chat_input.setObjectName("chatInput")
        self.chat_input.setPlaceholderText("输入问题，按回车或点击上方AI分析按钮...")
        self.chat_input.setMinimumHeight(45)
        self.chat_input.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.chat_input.setStyleSheet("""
            QLineEdit#chatInput {
                background: white;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 0 14px;
                color: #111827;
                font-size: 14px;
            }
            QLineEdit#chatInput:focus {
                border: 2px solid #50C9C3;
            }
        """)
        self.chat_input.returnPressed.connect(self.on_send_chat_message)
        input_layout.addWidget(self.chat_input, 1)
        
        layout.addLayout(input_layout)
        
        # 聊天消息区域
        chat_panel = self._create_chat_panel()
        layout.addWidget(chat_panel, 1)
        
        # 底部导出按钮区域
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)
        
        bottom_layout.addStretch()
        
        # 复制结果按钮
        self.copy_result_btn = QPushButton("复制结果")
        self.copy_result_btn.setObjectName("troubleFooterBtn")
        self.copy_result_btn.setFixedHeight(40)
        self.copy_result_btn.setMinimumWidth(108)
        self.copy_result_btn.setStyleSheet("""
            QPushButton#troubleFooterBtn {
                background: white;
                color: #374151;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton#troubleFooterBtn:hover {
                background: #f8fafc;
                border-color: #50C9C3;
            }
        """)
        self.copy_result_btn.clicked.connect(self.on_copy_last_result)
        bottom_layout.addWidget(self.copy_result_btn)
        
        # 导出记录按钮
        self.export_chat_btn = QPushButton("导出记录")
        self.export_chat_btn.setObjectName("troubleFooterBtn")
        self.export_chat_btn.setFixedHeight(40)
        self.export_chat_btn.setMinimumWidth(108)
        self.export_chat_btn.setStyleSheet("""
            QPushButton#troubleFooterBtn {
                background: white;
                color: #374151;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton#troubleFooterBtn:hover {
                background: #f8fafc;
                border-color: #50C9C3;
            }
        """)
        self.export_chat_btn.clicked.connect(self.on_export_chat_history)
        bottom_layout.addWidget(self.export_chat_btn)
        
        # 导出AI报告按钮
        self.export_ai_report_btn = PrimaryPushButton("导出AI报告")
        self.export_ai_report_btn.setObjectName("exportAiReportBtn")
        self.export_ai_report_btn.setFixedHeight(40)
        self.export_ai_report_btn.setMinimumWidth(116)
        self.export_ai_report_btn.setStyleSheet("""
            PrimaryPushButton#exportAiReportBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 18px;
            }
            PrimaryPushButton#exportAiReportBtn:hover {
                background: #3BA8A2;
            }
        """)
        self.export_ai_report_btn.clicked.connect(self.on_export_ai_report)
        bottom_layout.addWidget(self.export_ai_report_btn)
        
        layout.addLayout(bottom_layout)
        self._apply_button_size_policy(self)

    def _apply_button_size_policy(self, root: QWidget):
        """统一当前页面按钮的基础尺寸。"""
        for button in root.findChildren(QPushButton):
            button.setMinimumHeight(40)
            if button.minimumWidth() < 100:
                button.setMinimumWidth(100)
    
    def _create_chat_panel(self) -> QWidget:
        """创建聊天面板（仅消息区域）"""
        panel = QFrame()
        panel.setObjectName("chatPanel")
        panel.setStyleSheet("""
            QFrame#chatPanel {
                background: white;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
        """)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 标题行
        title_layout = QHBoxLayout()
        title = QLabel("AI分析结果")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; padding: 5px;")
        title_layout.addWidget(title)
        title_layout.addStretch()
        
        layout.addLayout(title_layout)
        
        # 聊天消息区域
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatScrollArea")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setStyleSheet("""
            QScrollArea#chatScrollArea {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background: #f9fafb;
            }
        """)
        
        self.chat_widget = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_widget)
        self.chat_layout.setSpacing(10)
        self.chat_layout.setContentsMargins(10, 10, 10, 10)
        self.chat_layout.addStretch()
        
        self.chat_scroll.setWidget(self.chat_widget)
        layout.addWidget(self.chat_scroll, 1)
        
        return panel
    
    def load_faqs(self):
        """加载FAQ知识库 - 按产品过滤"""
        config_file = Path(get_config_dir()) / "faqs.json"
        
        print(f"[DEBUG] load_faqs: current_product={self.current_product}")
        
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    print(f"[DEBUG] load_faqs: data type={type(data).__name__}")
                    
                    # 检查是否是按产品组织的字典 {"产品A": [...], "产品B": [...]}
                    if isinstance(data, dict):
                        print(f"[DEBUG] load_faqs: dict keys={list(data.keys())}")
                        if self.current_product and self.current_product in data:
                            self.faqs = data[self.current_product]
                            print(f"[DEBUG] load_faqs: matched product, count={len(self.faqs)}")
                        elif "通用" in data:
                            self.faqs = data["通用"]
                            print(f"[DEBUG] load_faqs: fallback to 通用, count={len(self.faqs)}")
                        else:
                            # 合并所有产品的FAQ
                            all_faqs = []
                            for product_faqs in data.values():
                                if isinstance(product_faqs, list):
                                    all_faqs.extend(product_faqs)
                            self.faqs = all_faqs
                            print(f"[DEBUG] load_faqs: merged all, count={len(self.faqs)}")
                    elif isinstance(data, list):
                        print(f"[DEBUG] load_faqs: list length={len(data)}")
                        # 按产品过滤FAQ
                        if self.current_product:
                            # 显示匹配产品的FAQ
                            product_faqs = [f for f in data if f.get('product', '') == self.current_product]
                            print(f"[DEBUG] load_faqs: product_faqs count={len(product_faqs)}")
                            self.faqs = product_faqs
                        else:
                            # 没有选择产品时，显示全部FAQ
                            self.faqs = data
                            print(f"[DEBUG] load_faqs: no product filter, showing all")
                    else:
                        self.faqs = []
            except Exception as e:
                print(f"[WARN] Failed to load FAQs: {e}")
                self.faqs = []
        else:
            self.faqs = []
            print(f"[DEBUG] load_faqs: config file not exists")
        
        # 更新状态标签
        if hasattr(self, 'status_label'):
            self.status_label.setText(f"知识库已加载: {len(self.faqs)} 条问答")
    
    def save_faqs(self):
        """保存FAQ知识库 - 保留其他产品的FAQ"""
        config_dir = Path(get_config_dir())
        config_file = config_dir / "faqs.json"
        
        try:
            all_faqs = []
            if config_file.exists():
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    all_faqs = [f for f in data if f.get('product', '') != self.current_product]
                elif isinstance(data, dict):
                    for product, faqs in data.items():
                        if product != self.current_product and isinstance(faqs, list):
                            all_faqs.extend(faqs)
            
            all_faqs.extend(self.faqs)
            
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(all_faqs, f, indent=2, ensure_ascii=False)
            print(f"[DEBUG] save_faqs: saved {len(self.faqs)} FAQs for {self.current_product}, total {len(all_faqs)} FAQs")
        except Exception as e:
            print(f"[ERROR] save_faqs: {e}")
    
    def on_local_search(self):
        """本地知识库搜索"""
        dialog = LocalSearchDialog(self.faqs, self)
        dialog.exec()
    
    def on_upload_faq_file(self):
        """上传FAQ文件"""
        from PyQt6.QtWidgets import QFileDialog
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择知识库文件",
            "",
            "支持的文件 (*.docx *.doc *.pdf *.md *.txt *.json *.xlsx *.csv);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        file_name = os.path.basename(file_path)
        
        try:
            added_count = 0
            if file_path.endswith('.json'):
                added_count = self._load_json_faq(file_path)
            elif file_path.endswith('.md'):
                added_count = self._load_md_faq(file_path)
            elif file_path.endswith('.txt'):
                added_count = self._load_txt_faq(file_path)
            elif file_path.endswith('.docx') or file_path.endswith('.doc'):
                added_count = self._load_word_faq(file_path)
            elif file_path.endswith('.pdf'):
                added_count = self._load_pdf_faq(file_path)
            elif file_path.endswith('.xlsx') or file_path.endswith('.xls'):
                added_count = self._load_excel_faq(file_path)
            elif file_path.endswith('.csv'):
                added_count = self._load_csv_faq(file_path)
            else:
                QMessageBox.warning(self, "不支持的格式", "请上传 Word、PDF、Excel、CSV、Markdown、TXT 或 JSON 格式的文件")
                return
            
            self.save_faqs()
            self.load_faqs()
            
            if added_count > 0:
                MessageBox("上传成功", f"已成功加载知识库文件，新增 {added_count} 条问答", self).exec()
            else:
                MessageBox("警告", "文件解析完成但未找到有效的问答对", self).exec()
            
        except Exception as e:
            import traceback
            QMessageBox.warning(self, "上传失败", f"处理文件时出错:\n{str(e)}")
    
    def _load_json_faq(self, file_path: str) -> int:
        """加载JSON格式FAQ"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        added_count = 0
        if isinstance(data, list):
            for faq in data:
                faq['product'] = self.current_product
                self.faqs.append(faq)
                added_count += 1
        elif isinstance(data, dict):
            if 'faqs' in data:
                for faq in data['faqs']:
                    faq['product'] = self.current_product
                    self.faqs.append(faq)
                    added_count += 1
            elif 'qnas' in data:
                for faq in data['qnas']:
                    faq['product'] = self.current_product
                    self.faqs.append(faq)
                    added_count += 1
        
        return added_count
    
    def _load_md_faq(self, file_path: str) -> int:
        """加载Markdown格式FAQ"""
        import re
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 尝试新的脚本格式
        if '#!/bin/bash' in content or ('# 问题：' in content and '# 关键词：' in content):
            return self._load_script_faq(content)
        
        added_count = 0
        
        # 尝试 Q: / A: 格式
        pattern = r'Q:\s*(.+?)\nA:\s*(.+?)(?=\nQ:|\n## |$)'
        matches = re.findall(pattern, content, re.DOTALL)
        for q, a in matches:
            self.faqs.append({'question': q.strip(), 'answer': a.strip(), 'product': self.current_product})
            added_count += 1
        
        # 尝试 ## 标题 格式
        if added_count == 0:
            sections = re.split(r'\n(?=## )', content)
            for section in sections:
                lines = section.strip().split('\n', 1)
                if len(lines) >= 2:
                    question = lines[0].lstrip('#').strip()
                    answer = lines[1].strip()
                    if question and answer:
                        self.faqs.append({'question': question, 'answer': answer, 'product': self.current_product})
                        added_count += 1
        
        return added_count
    
    def _load_txt_faq(self, file_path: str) -> int:
        """加载TXT格式FAQ"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 尝试新的脚本格式（#!/bin/bash 开头，包含 # 问题：# 原因：# 关键词：等注释）
        if '#!/bin/bash' in content or ('# 问题：' in content and '# 关键词：' in content):
            return self._load_script_faq(content)
        
        added_count = 0
        for line in content.split('\n'):
            line = line.strip()
            if '|' in line:
                parts = line.split('|', 1)
                if len(parts) == 2:
                    self.faqs.append({'question': parts[0].strip(), 'answer': parts[1].strip(), 'product': self.current_product})
                    added_count += 1
            elif line:
                for sep in ['\t', ';;', '::']:
                    if sep in line:
                        parts = line.split(sep, 1)
                        if len(parts) == 2:
                            self.faqs.append({'question': parts[0].strip(), 'answer': parts[1].strip(), 'product': self.current_product})
                            added_count += 1
                            break
        
        return added_count
    
    def _load_script_faq(self, content: str) -> int:
        """加载脚本格式FAQ（#!/bin/bash，包含问题、原因、关键词、命令、结果判断）
        
        格式示例：
        #!/bin/bash
        # 问题：【办公智盾-登录界面验证码加载失败】
        # 原因：【升级服务超时时间过短，日志堆积导致磁盘占满】
        # 关键词：【验证码加载失败，磁盘占满，日志堆积，超时时间】
        df -h
        lscpu
        # 结果判断：【如果df -h显示磁盘使用率接近100%...】
        """
        added_count = 0
        import re
        
        # 按 # 问题： 分割，每个问题一块
        # 匹配 # 问题：【xxx】
        question_pattern = r'#\s*问题：\s*【(.+?)】'
        cause_pattern = r'#\s*原因：\s*【(.+?)】'
        keywords_pattern = r'#\s*关键词：\s*【(.+?)】'
        result_pattern = r'#\s*结果判断：\s*【(.+?)】'
        
        # 分割内容为块
        blocks = re.split(r'(?=#\s*问题：)', content)
        
        for block in blocks:
            if not block.strip():
                continue
            
            # 提取各项
            question_match = re.search(question_pattern, block)
            cause_match = re.search(cause_pattern, block)
            keywords_match = re.search(keywords_pattern, block)
            result_match = re.search(result_pattern, block)
            
            if not question_match:
                continue
            
            question = question_match.group(1).strip()
            cause = cause_match.group(1).strip() if cause_match else ''
            keywords = keywords_match.group(1).strip() if keywords_match else ''
            result_analysis = result_match.group(1).strip() if result_match else ''
            
            # 提取命令（去掉注释行）
            command_lines = []
            for line in block.split('\n'):
                line_stripped = line.strip()
                # 跳过注释行（但保留没有 # 的行）
                if line_stripped.startswith('#'):
                    continue
                if line_stripped:
                    command_lines.append(line_stripped)
            
            commands = '\n'.join(command_lines)
            
            faq = {
                'question': question,
                'cause': cause,
                'keywords': keywords,
                'commands': commands,
                'result_analysis': result_analysis,
                'product': self.current_product
            }
            
            self.faqs.append(faq)
            added_count += 1
            print(f"[FAQ] Loaded script FAQ: {question[:30]}... commands={len(command_lines)}")
        
        return added_count
    
    def _load_word_faq(self, file_path: str) -> int:
        """加载Word格式FAQ"""
        try:
            from docx import Document
        except ImportError:
            QMessageBox.warning(self, "缺少库", "请先安装 python-docx 库")
            return 0
        
        print(f"[FAQ] _load_word_faq: starting to load {file_path}")
        
        doc = Document(file_path)
        added_count = 0
        product = self.current_product or "通用"
        
        print(f"[FAQ] _load_word_faq: document has {len(doc.paragraphs)} paragraphs, {len(doc.tables)} tables")
        
        # 方法1: 从表格中提取FAQ
        for table_idx, table in enumerate(doc.tables):
            print(f"[FAQ] Processing table {table_idx}")
            for row_idx, row in enumerate(table.rows):
                cells = [cell.text.strip() for cell in row.cells]
                if len(cells) >= 2:
                    question = cells[0]
                    answer = cells[1] if len(cells) > 1 else ""
                    if question and answer and len(question) > 2 and len(answer) > 2:
                        self.faqs.append({
                            'question': question,
                            'answer': answer,
                            'product': product
                        })
                        added_count += 1
                        print(f"[FAQ] Table FAQ added: {question[:30]}...")
        
        print(f"[FAQ] _load_word_faq: after table processing, added_count={added_count}")
        
        # 方法2: 从段落中提取FAQ
        current_question = None
        current_answer = []
        
        for para_idx, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            if not text:
                if current_question and current_answer:
                    answer_text = '\n'.join(current_answer).strip()
                    if answer_text:
                        self.faqs.append({
                            'question': current_question,
                            'answer': answer_text,
                            'product': product
                        })
                        added_count += 1
                        print(f"[FAQ] Paragraph FAQ added: {current_question[:30]}...")
                    current_question = None
                    current_answer = []
                continue
            
            # 检测问题开始
            is_question = False
            question_text = None
            
            if text.endswith('?') or '?' in text:
                question_text = text
                is_question = True
            elif text.startswith('Q:') or text.startswith('Q：'):
                question_text = text.lstrip('Q:').lstrip('Q：').strip()
                is_question = True
            elif text.startswith('问：') or text.startswith('问题：'):
                question_text = text.lstrip('问：').lstrip('问题：').strip()
                is_question = True
            elif text.startswith('1.') or text.startswith('2.') or text.startswith('3.'):
                import re
                match = re.match(r'^\d+[.、]\s*(.+)', text)
                if match:
                    question_text = match.group(1).strip()
                    is_question = True
            elif text.startswith('【') and '】' in text:
                import re
                match = re.match(r'^【([^】]+)】\s*(.*)', text)
                if match:
                    question_text = match.group(1).strip() + ' ' + match.group(2).strip()
                    is_question = True
            
            if is_question and question_text:
                if current_question and current_answer:
                    answer_text = '\n'.join(current_answer).strip()
                    if answer_text:
                        self.faqs.append({
                            'question': current_question,
                            'answer': answer_text,
                            'product': product
                        })
                        added_count += 1
                current_question = question_text
                current_answer = []
            elif current_question:
                current_answer.append(text)
        
        # 保存最后一个
        if current_question and current_answer:
            answer_text = '\n'.join(current_answer).strip()
            if answer_text:
                self.faqs.append({
                    'question': current_question,
                    'answer': answer_text,
                    'product': product
                })
                added_count += 1
        
        print(f"[FAQ] _load_word_faq: total added {added_count} FAQs, product={product}")
        return added_count
    
    def _load_pdf_faq(self, file_path: str) -> int:
        """加载PDF格式FAQ"""
        try:
            import fitz
        except ImportError:
            QMessageBox.warning(self, "缺少库", "请先安装 PyMuPDF 库")
            return 0
        
        doc = fitz.open(file_path)
        text_content = []
        
        for page in doc:
            text_content.append(page.get_text())
        
        doc.close()
        
        # 合并所有文本，用TXT方式解析
        return self._load_txt_faq_from_text('\n'.join(text_content))
    
    def _load_excel_faq(self, file_path: str) -> int:
        """加载Excel格式FAQ"""
        try:
            import openpyxl
        except ImportError:
            QMessageBox.warning(self, "缺少库", "请先安装 openpyxl 库")
            return 0
        
        added_count = 0
        wb = openpyxl.load_workbook(file_path)
        
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            headers = [cell.value for cell in sheet[1]]
            
            field_map = {'question': None, 'cause': None, 'commands': None, 
                        'result_analysis': None, 'answer': None, 'keywords': None}
            
            for idx, header in enumerate(headers):
                if not header:
                    continue
                header_lower = str(header).lower().strip()
                if '问题' in header or 'question' in header_lower or '标题' in header:
                    field_map['question'] = idx
                elif '原因' in header or 'cause' in header_lower:
                    field_map['cause'] = idx
                elif '命令' in header or 'command' in header_lower or '检测' in header:
                    field_map['commands'] = idx
                elif '结果' in header or '判断' in header or 'result' in header_lower:
                    field_map['result_analysis'] = idx
                elif '答案' in header or 'answer' in header_lower or 'solution' in header_lower:
                    field_map['answer'] = idx
                elif '关键词' in header or 'keyword' in header_lower:
                    field_map['keywords'] = idx
            
            for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
                faq_data = {'product': self.current_product}
                for field, col_idx in field_map.items():
                    if col_idx is not None and col_idx < len(row):
                        value = row[col_idx]
                        if value is not None:
                            faq_data[field] = str(value).strip()
                if 'question' in faq_data and faq_data['question']:
                    self.faqs.append(faq_data)
                    added_count += 1
        
        return added_count
    
    def _load_csv_faq(self, file_path: str) -> int:
        """加载CSV格式FAQ"""
        try:
            import csv
        except ImportError:
            QMessageBox.warning(self, "缺少库", "请先安装 csv 库")
            return 0
        
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        
        return self._load_txt_faq_from_text(content)
    
    def _load_txt_faq_from_text(self, content: str) -> int:
        """从文本内容加载FAQ - 统一格式转换"""
        import re
        
        added_count = 0
        content = content.strip()
        
        # 格式1: Q: / A: 格式
        pattern1 = r'Q:\s*(.+?)\nA:\s*(.+?)(?=\nQ:|\n\d+\.|\n## |\Z)'
        matches1 = re.findall(pattern1, content, re.DOTALL)
        for q, a in matches1:
            q, a = q.strip(), a.strip()
            if q and a:
                self.faqs.append({'question': q, 'answer': a, 'product': self.current_product})
                added_count += 1
        
        # 格式2: 脚本格式
        if added_count == 0:
            added_count = self._load_script_faq(content)
        
        # 格式3: 通用分隔格式
        if added_count == 0:
            for sep in ['\n\n', '\n---\n', '\n###\n', '\n答案：\n', '\n答：\n']:
                if sep in content:
                    parts = content.split(sep)
                    for part in parts:
                        part = part.strip()
                        if not part:
                            continue
                        lines = part.split('\n')
                        question = lines[0].strip() if lines else ''
                        answer = '\n'.join(lines[1:]).strip() if len(lines) > 1 else ''
                        if question and answer:
                            self.faqs.append({'question': question, 'answer': answer, 'product': self.current_product})
                            added_count += 1
                    if added_count > 0:
                        break
        
        print(f"[FAQ] _load_txt_faq_from_text: parsed {added_count} FAQs")
        return added_count
    
    def _generate_ai_analysis_report(self, question: str, cause: str, keywords: str, 
                                      command_output: str, analysis: dict, ai_response: str = "") -> str:
        """生成AI分析报告"""
        lines = []
        lines.append("=" * 60)
        lines.append("AI 智能分析报告")
        lines.append("=" * 60)
        
        # 问题信息
        lines.append(f"\n【问题描述】\n{question}")
        
        # 可能原因
        if cause:
            lines.append(f"\n【可能原因】\n{cause}")
        
        # 关键词
        if keywords:
            lines.append(f"\n【相关关键词】\n{keywords}")
        
        # AI智能分析结果
        if ai_response:
            lines.append(f"\n【AI智能分析】\n{ai_response}")
        
        # 关键发现
        findings = analysis.get("findings", [])
        warnings = analysis.get("warnings", [])
        
        if findings:
            lines.append(f"\n【系统检测发现】")
            for finding in findings:
                lines.append(f"  - {finding}")
        
        if warnings:
            lines.append(f"\n【警告信息】")
            for warning in warnings:
                lines.append(f"  - {warning}")
        
        # 命令执行结果摘要
        lines.append(f"\n【命令执行结果】")
        lines.append(f"共执行 {len([r for r in analysis.get('command_results', [])])} 个命令")
        
        # 智能结论
        lines.append(f"\n【智能结论】")
        conclusion = self._generate_intelligent_conclusion(analysis, command_output)
        lines.append(conclusion)
        
        # 解决方案 - 使用FAQ中的结果判断依据，不生成具体命令
        lines.append(f"\n【解决方案】")
        result_analysis = analysis.get("result_analysis", "")
        if result_analysis:
            lines.append(result_analysis)
        else:
            lines.append("暂无解决方案")
        
        lines.append("\n" + "=" * 60)
        lines.append(f"报告生成时间: {self._get_timestamp()}")
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def _generate_intelligent_conclusion(self, analysis: dict, command_output: str) -> str:
        """根据分析结果生成智能结论"""
        findings = analysis.get("findings", [])
        warnings = analysis.get("warnings", [])
        
        # 检查是否有严重问题
        severe_issues = [f for f in findings if "严重" in f or "告警" in f]
        if severe_issues:
            return f"检测到 {len(severe_issues)} 个严重问题，系统需要立即处理。建议优先解决磁盘空间、内存溢出等可能导致服务中断的问题。"
        
        # 检查是否有警告
        warning_issues = [f for f in findings if "警告" in f or "较高" in f]
        if warning_issues:
            return f"检测到 {len(warning_issues)} 个潜在问题，系统运行存在风险。建议关注负载情况和资源使用率，及时进行优化。"
        
        # 检查是否有进程问题
        process_issues = [f for f in findings if "高CPU" in f or "高内存" in f]
        if process_issues:
            return f"发现 {len(process_issues)} 个异常进程，可能影响系统性能。建议检查并优化相关进程。"
        
        # 检查命令输出是否正常
        if "error" in command_output.lower() or "exception" in command_output.lower():
            return "命令执行过程中出现错误，建议检查系统配置和网络连接状态。"
        
        # 默认正常结论
        return "系统检查未发现明显异常，各项指标运行正常。如果问题仍然存在，建议联系运维人员进一步排查。"
    
    def _generate_suggestions(self, analysis: dict, command_output: str) -> list:
        """生成处理建议"""
        suggestions = []
        findings = analysis.get("findings", [])
        
        # 基于发现的问题生成建议
        if any("磁盘" in f for f in findings):
            suggestions.append("清理磁盘空间，删除不必要的日志和临时文件")
            suggestions.append("考虑扩展磁盘容量或清理旧数据")
        
        if any("内存" in f for f in findings):
            suggestions.append("检查内存泄漏问题，重启异常服务")
            suggestions.append("优化应用程序内存使用配置")
        
        if any("CPU" in f or "负载" in f for f in findings):
            suggestions.append("检查系统负载来源，优化高负载进程")
            suggestions.append("考虑扩容或负载均衡")
        
        if any("高CPU" in f or "高内存" in f for f in findings):
            suggestions.append("定位并优化异常进程")
            suggestions.append("设置进程监控告警")
        
        if "docker" in command_output.lower():
            suggestions.append("检查Docker容器状态和资源使用")
        
        # 添加通用建议
        if not suggestions:
            suggestions.append("系统运行正常，保持当前状态即可")
        else:
            suggestions.append("持续监控系统状态，定期巡检")
        
        return suggestions[:5]  # 最多5条建议
    
    def on_export_ai_report(self):
        """导出AI分析报告为HTML"""
        if not hasattr(self, 'current_ai_report') or not self.current_ai_report:
            MessageBox("提示", "没有可导出的AI分析报告，请先进行问答", self).exec()
            return
        
        import os
        from datetime import datetime
        from PyQt6.QtWidgets import QFileDialog
        
        # 让用户选择保存位置
        html_file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出AI分析报告",
            f"AI排障报告_{self.current_product}_{self._get_timestamp()}.html",
            "HTML文件 (*.html);;所有文件 (*.*)"
        )
        
        if not html_file_path:
            return
        
        # 确保扩展名是.html
        if not html_file_path.endswith('.html'):
            html_file_path += '.html'
        
        # 获取FAQ数据
        faq_meta = getattr(self, 'current_ai_report_meta', {})
        faq_question = faq_meta.get('question', '')
        faq_cause = faq_meta.get('cause', '')
        user_question = faq_meta.get('user_question', '')
        result_analysis = faq_meta.get('result_analysis', '')
        
        # 直接从meta获取命令执行结果
        command_results = faq_meta.get('command_results', [])
        
        # 从meta获取LLM判断的匹配结果
        is_match = faq_meta.get('is_match', False)
        
        # 解析AI分析结论，提取分析过程、结论、说明
        import re
        analysis_process = ""
        analysis_conclusion = ""
        analysis_explanation = ""
        
        # 如果result_analysis为空，使用默认值
        if not result_analysis:
            analysis_process = "未提供分析过程"
            analysis_conclusion = "系统检测完成，建议根据命令输出进行排查"
            analysis_explanation = "系统检测完成，建议根据命令输出进行排查"
        else:
            # 提取分析过程 (支持多种格式)
            process_match = re.search(r'\*\*分析过程\*\*\s*[:：]\s*(.*?)(?=\n\*\*|\Z)', result_analysis, re.DOTALL)
            if process_match:
                raw_process = process_match.group(1).strip()
                # 过滤掉AI思考过程（推理过程、反思等）
                lines = raw_process.split('\n')
                filtered_lines = []
                skip_patterns = ['让我', '我来', '首先', '然后', '接下来', '所以', '因此',
                                'considering', 'thinking', 'thought', 'analyzing', 'let me',
                                '这符合', '符合该', '综上所述', '总结', '总之', '最终']
                in_meaningful_section = False
                for line in lines:
                    line_stripped = line.strip()
                    # 检查是否进入实质性分析内容（检测到具体数据）
                    if any(k in line_stripped.lower() for k in ['检测', '发现', '输出', '使用率', '内存', 'cpu', '磁盘', '进程']):
                        in_meaningful_section = True
                    
                    # 如果还没有进入实质性内容，尝试匹配跳过
                    if not in_meaningful_section:
                        should_skip = False
                        for pattern in skip_patterns:
                            if pattern.lower() in line_stripped.lower():
                                should_skip = True
                                break
                        if should_skip:
                            continue
                        # 跳过纯推理步骤（如"1. 首先检查"）
                        if re.match(r'^\d+\.\s*(首先|然后|接着|最后|检查|分析)', line_stripped):
                            continue
                    
                    # 如果包含实质性内容的关键字，保留
                    if any(k in line_stripped.lower() for k in ['检测', '发现', '输出', '使用率', '内存', 'cpu', '磁盘', '进程', '命令', '结果', '指标', '超过', '高于', '低于']):
                        filtered_lines.append(line)
                        in_meaningful_section = True
                    elif in_meaningful_section and line_stripped:
                        # 在实质性内容之后，继续添加后续行（但跳过总结）
                        if not any(s in line_stripped for s in ['符合', '结论', '因此', '所以', '综上所述']):
                            filtered_lines.append(line)
                
                analysis_process = '\n'.join(filtered_lines).strip()
                if not analysis_process or len(analysis_process) < 10:
                    analysis_process = raw_process
            else:
                analysis_process = result_analysis
            
            # 提取结论 (支持中文冒号)
            conclusion_match = re.search(r'\*\*结论\*\*\s*[:：]\s*(.*?)(?=\n\*\*|\Z)', result_analysis, re.DOTALL)
            if conclusion_match:
                raw_conclusion = conclusion_match.group(1).strip()
                # 清理结论中的匹配判断文字，保留实际结论
                if '符合' in raw_conclusion or '不匹配' in raw_conclusion:
                    # 提取结论中除了"符合/不符合"之外的部分
                    cleaned = re.sub(r'^(符合|不符合)[，,]?\s*', '', raw_conclusion)
                    analysis_conclusion = cleaned if cleaned else raw_conclusion
                else:
                    analysis_conclusion = raw_conclusion
            else:
                analysis_conclusion = result_analysis[:300] if len(result_analysis) > 300 else result_analysis
            
            # 提取说明
            explanation_match = re.search(r'\*\*说明\*\*\s*[:：]?\s*(.*?)(?=\n\*\*|\Z)', result_analysis, re.DOTALL)
            if explanation_match:
                analysis_explanation = explanation_match.group(1).strip()
            else:
                # 如果没有**说明**，直接使用result_analysis作为最终结论
                analysis_explanation = result_analysis.strip()
        
        conclusion_text = analysis_explanation
        
        # 生成时间
        generate_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 构建命令执行结果HTML
        command_output_html = ""
        for cmd_info in command_results:
            cmd = cmd_info.get('command', '')
            output = cmd_info.get('output', '')
            escaped_output = self._escape_html(output)
            command_output_html += f"""
            <div class="command-block">
                <div class="command-header">
                    <span class="command-label">$</span>
                    <span class="command-text">{self._escape_html(cmd)}</span>
                </div>
                <pre class="command-output">{escaped_output}</pre>
            </div>
            """
        
        if not command_output_html:
            command_output_html = '<div class="empty-text">无命令执行结果</div>'
        
        # 根据匹配结果选择样式
        if is_match:
            conclusion_class = "conclusion-match"
            conclusion_icon = "✓"
            conclusion_color = "#4caf50"
        else:
            conclusion_class = "conclusion-not-match"
            conclusion_icon = "✗"
            conclusion_color = "#f44336"
        
        # 生成HTML内容
        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI智能排障报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Helvetica, Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{ max-width: 1000px; margin: 0 auto; }}
        .report-card {{
            background: #fff;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
            margin-bottom: 20px;
        }}
        .report-header {{
            background: linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%);
            color: #fff;
            padding: 30px 40px;
            text-align: center;
        }}
        .report-header h1 {{ font-size: 28px; font-weight: 600; letter-spacing: 2px; margin-bottom: 8px; }}
        .report-header .subtitle {{ font-size: 14px; opacity: 0.8; }}
        
        .report-body {{ padding: 30px 40px; }}
        
        /* 信息行样式 */
        .info-row {{
            display: flex;
            padding: 12px 0;
            border-bottom: 1px solid #eee;
        }}
        .info-row:last-child {{ border-bottom: none; }}
        .info-label {{
            width: 120px;
            flex-shrink: 0;
            font-weight: 600;
            color: #666;
            font-size: 14px;
        }}
        .info-value {{ flex: 1; color: #333; font-size: 14px; word-break: break-all; text-align: left; }}
        
        .section {{
            margin-bottom: 28px;
            background: #f8f9fa;
            border-radius: 12px;
            padding: 20px;
        }}
        .section:last-child {{ margin-bottom: 0; }}
        
        .section-title {{
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 18px;
            font-weight: 600;
            color: #1e3a5f;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 2px solid #dee2e6;
        }}
        .section-icon {{
            width: 36px;
            height: 36px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
        }}
        .icon-info {{ background: #e3f2fd; color: #1976d2; }}
        .icon-fault {{ background: #fff3e0; color: #f57c00; }}
        .icon-analysis {{ background: #f3e5f5; color: #7b1fa2; }}
        .icon-key {{ background: #e8f5e9; color: #388e3c; }}
        .icon-final {{ background: #ffebee; color: #d32f2f; }}
        
        .content-box {{
            background: #fff;
            border-radius: 8px;
            padding: 16px;
            font-size: 14px;
            line-height: 1.8;
            color: #333;
        }}
        
        .command-block {{
            background: #1e1e1e;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 12px;
        }}
        .command-block:last-child {{ margin-bottom: 0; }}
        .command-header {{
            background: #2d2d2d;
            padding: 10px 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .command-label {{ color: #4fc3f7; font-weight: 600; }}
        .command-text {{ color: #b0bec5; font-size: 13px; }}
        .command-output {{
            background: #1e1e1e;
            color: #c9d1d9;
            padding: 16px;
            font-size: 13px;
            line-height: 1.5;
            overflow-x: auto;
            white-space: pre-wrap;
            word-break: break-all;
            margin: 0;
        }}
        
        /* 最终结论样式 */
        .conclusion-box {{
            border-radius: 12px;
            padding: 24px;
            text-align: left;
        }}
        .conclusion-match {{
            background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
            border: 3px solid #4caf50;
        }}
        .conclusion-not-match {{
            background: #ffffff;
            border: 2px solid #e5e7eb;
            border-left: 5px solid #64748b;
        }}
        
        .conclusion-text {{
            font-size: 14px;
            line-height: 1.8;
            color: #333;
            text-align: left;
        }}
        
        .empty-text {{
            color: #999;
            font-style: italic;
            text-align: center;
            padding: 20px;
        }}
        
        .report-footer {{
            text-align: center;
            padding: 20px;
            color: rgba(255,255,255,0.7);
            font-size: 12px;
        }}
        
        @media (max-width: 768px) {{
            body {{ padding: 10px; }}
            .report-header {{ padding: 20px; }}
            .report-body {{ padding: 20px; }}
            .report-header h1 {{ font-size: 22px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="report-card">
            <div class="report-header">
                <h1>📋 AI智能排障报告</h1>
                <div class="subtitle">基于FAQ知识库的自动化故障诊断</div>
            </div>
            <div class="report-body">
                
                <!-- 1. 基础信息 -->
                <div class="section">
                    <div class="section-title">
                        <div class="section-icon icon-info">📊</div>
                        基础信息
                    </div>
                    <div class="content-box">
                        <div class="info-row">
                            <div class="info-label">生成时间</div>
                            <div class="info-value">{generate_time}</div>
                        </div>
                        <div class="info-row">
                            <div class="info-label">用户提问</div>
                            <div class="info-value">{self._escape_html(user_question)}</div>
                        </div>
                    </div>
                </div>
                
                <!-- 2. 故障信息 -->
                <div class="section">
                    <div class="section-title">
                        <div class="section-icon icon-fault">⚠️</div>
                        故障信息
                    </div>
                    <div class="content-box">
                        <div class="info-row">
                            <div class="info-label">知识库问题</div>
                            <div class="info-value">{self._escape_html(faq_question)}</div>
                        </div>
                        <div class="info-row">
                            <div class="info-label">可能原因</div>
                            <div class="info-value">{self._escape_html(faq_cause)}</div>
                        </div>
                    </div>
                </div>
                
                <!-- 3. AI智能分析 -->
                <div class="section">
                    <div class="section-title">
                        <div class="section-icon icon-analysis">🤖</div>
                        AI智能分析
                    </div>
                    <div class="content-box">
                        <div class="info-row">
                            <div class="info-label">分析过程</div>
                            <div class="info-value">{self._markdown_to_html(analysis_process)}</div>
                        </div>
                    </div>
                </div>
                
                <!-- 5. 最终结论 -->
                <div class="section">
                    <div class="section-title">
                        <div class="section-icon icon-final">🎯</div>
                        最终结论
                    </div>
                    <div class="conclusion-box {conclusion_class}">
                        <div class="conclusion-text">{self._markdown_to_html(analysis_explanation)}</div>
                    </div>
                </div>
                
                <!-- 6. 命令原始输出 -->
                <div class="section">
                    <div class="section-title">
                        <div class="section-icon icon-analysis">💻</div>
                        命令原始输出
                    </div>
                    <div class="content-box" style="padding: 0; background: transparent;">
                        {command_output_html}
                    </div>
                </div>
                
            </div>
        </div>
        <div class="report-footer">
            AI智能排障报告 · 由AI排障系统生成
        </div>
    </div>
</body>
</html>"""
        
        # 写入HTML文件
        try:
            with open(html_file_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
        except Exception as e:
            MessageBox("错误", f"保存文件失败:\n{str(e)}", self).exec()
            return
        
        MessageBox("导出成功", f"AI分析报告已保存到:\n{html_file_path}", self).exec()
        
        # 自动打开所在文件夹
        folder = os.path.dirname(html_file_path)
        if folder and os.path.exists(folder):
            import platform
            if platform.system() == 'Windows':
                os.startfile(folder)
    
    def _escape_html(self, text: str) -> str:
        """HTML特殊字符转义"""
        if not text:
            return ""
        return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))
    
    def _markdown_to_html(self, text: str) -> str:
        """将Markdown格式文本转换为HTML，无序号、左对齐"""
        if not text:
            return ""
        
        import re
        
        # 先转义所有HTML标签，防止XSS和显示问题
        def escape_html(s):
            return (s
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;"))
        
        lines = text.split('\n')
        result_lines = []
        i = 0
        
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # 跳过空行
            if not stripped:
                result_lines.append('')
                i += 1
                continue
            
            # 处理代码块（Triple backticks）- 支持 ```css, ```html, ```python 等
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
                result_lines.append(f'<pre style="background: #f0f0f0; padding: 10px; border-radius: 4px; overflow-x: auto; margin: 6px 0;"><code>{escaped_code}</code></pre>')
                i += 1  # 跳过结束的```
                continue
            
            # 处理无序列表项
            list_match = re.match(r'^[-*]\s+(.*)$', stripped)
            if list_match:
                content = list_match.group(1)
                formatted = self._format_inline_markdown(content)
                result_lines.append(f'<p style="margin: 6px 0; padding-left: 0; text-align: left;">{formatted}</p>')
                i += 1
                continue
            
            # 处理有序列表项
            ordered_list_match = re.match(r'^\d+\.\s+(.*)$', stripped)
            if ordered_list_match:
                content = ordered_list_match.group(1)
                formatted = self._format_inline_markdown(content)
                result_lines.append(f'<p style="margin: 6px 0; padding-left: 0; text-align: left;">{formatted}</p>')
                i += 1
                continue
            
            # 非列表行 - 直接格式化输出
            formatted = self._format_inline_markdown(stripped)
            result_lines.append(f'<p style="margin: 6px 0; padding-left: 0; text-align: left;">{formatted}</p>')
            i += 1
        
        return '\n'.join(result_lines)
    
    def _format_inline_markdown(self, text: str) -> str:
        """处理行内格式（粗体、斜体、代码）- 自动转义HTML"""
        import re
        
        # 先转义HTML，防止显示问题
        text = (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))
        
        # 代码块（行内）
        def code_replace(m):
            return f'<code style="background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-family: monospace;">{m.group(1)}</code>'
        text = re.sub(r'`([^`]+)`', code_replace, text)
        
        # 粗体 **text** 或 __text__
        def bold_replace(m):
            return f'<strong style="font-weight: 600;">{m.group(1)}</strong>'
        text = re.sub(r'\*\*([^*]+)\*\*|__([^_]+)__', bold_replace, text)
        
        # 斜体 *text* 或 _text_
        def italic_replace(m):
            return f'<em style="font-style: italic;">{m.group(1) or m.group(2)}</em>'
        text = re.sub(r'\*([^*]+)\*|_([^_]+)_', italic_replace, text)
        
        return text
    
    def _generate_html_report(self, text_content: str) -> str:
        """将文本内容转换为HTML"""
        import html
        from datetime import datetime
        
        # 安全检查
        if not text_content or not isinstance(text_content, str):
            text_content = "没有可导出的内容"
        
        # 解析文本内容，提取各部分
        sections = []
        current_section = None
        current_content = []
        
        for line in text_content.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # 检测标题
            if line.startswith('=') and line.endswith('=') and len(line) > 5:
                continue
            
            # 跳过AI思考过程，不导出
            if line in ['【问题描述】', '【可能原因】', '【相关关键词】',
                       '【系统检测发现】', '【警告信息】', '【命令执行结果】', '【智能结论】',
                       '【处理建议】', '【结果判断依据】', '【结果判断】']:
                # 保存上一个section
                if current_section:
                    sections.append((current_section, '\n'.join(current_content)))
                current_section = line.replace('【', '').replace('】', '')
                current_content = []
            else:
                current_content.append(line)
        
        # 保存最后一个section
        if current_section and current_content:
            sections.append((current_section, '\n'.join(current_content)))
        
        # 根据不同类型生成HTML
        def escape_html(text):
            return html.escape(text, quote=True).replace('\n', '<br>')
        
        sections_html = []
        for title, content in sections:
            content = escape_html(content)
            
            # 根据标题类型选择样式
            if '命令' in title or '输出' in title:
                section_class = 'section'
                content_class = 'section-command'
            elif '警告' in title or '错误' in title:
                section_class = 'section section-error'
                content_class = 'section-content'
            elif '结论' in title or '建议' in title:
                section_class = 'section section-success'
                content_class = 'section-content'
            elif '原因' in title:
                section_class = 'section section-warning'
                content_class = 'section-content'
            else:
                section_class = 'section'
                content_class = 'section-content'
            
            sections_html.append('''
            <div class="''' + section_class + '''">
                <div class="section-title">【''' + title + '''】</div>
                <div class="''' + content_class + '''">''' + content + '''</div>
            </div>
            ''')
        
        # 生成时间戳
        timestamp = datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')
        
        # 直接构建HTML字符串，避免format()问题
        html_content = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI智能分析报告</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            max-width: 900px;
            margin: 0 auto;
            padding: 40px 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }
        .container {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #50C9C3 0%, #3eb8b3 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header h1 {
            margin: 0;
            font-size: 28px;
            font-weight: bold;
        }
        .header .subtitle {
            margin-top: 10px;
            font-size: 14px;
            opacity: 0.9;
        }
        .content {
            padding: 30px;
        }
        .section {
            margin-bottom: 25px;
            padding: 20px;
            border-radius: 10px;
            background: #f8f9fa;
            border-left: 4px solid #50C9C3;
        }
        .section-title {
            font-size: 16px;
            font-weight: bold;
            color: #50C9C3;
            margin-bottom: 12px;
        }
        .section-content {
            font-size: 14px;
            color: #374151;
            line-height: 1.8;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .section-command {
            background: #1e1e1e;
            color: #d4d4d4;
            font-family: "SF Mono", Monaco, Consolas, monospace;
            padding: 15px;
            border-radius: 8px;
            overflow-x: auto;
            font-size: 13px;
            line-height: 1.6;
        }
        .section-warning {
            background: #fffbeb;
            border-left-color: #f59e0b;
        }
        .section-warning .section-title {
            color: #f59e0b;
        }
        .section-success {
            background: #ecfdf5;
            border-left-color: #10b981;
        }
        .section-success .section-title {
            color: #10b981;
        }
        .section-error {
            background: #fef2f2;
            border-left-color: #ef4444;
        }
        .section-error .section-title {
            color: #ef4444;
        }
        .footer {
            background: #f8f9fa;
            padding: 20px;
            text-align: center;
            color: #6b7280;
            font-size: 12px;
        }
        h2, h3 { margin: 0 0 15px 0; }
        ul, ol { margin: 10px 0; padding-left: 25px; }
        li { margin-bottom: 8px; }
        code {
            background: #f3f4f6;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 13px;
            color: #e11d48;
        }
        strong { color: #1f2937; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>AI 智能分析报告</h1>
            <div class="subtitle">自动生成 | ''' + timestamp + '''</div>
        </div>
        <div class="content">
            ''' + '\n'.join(sections_html) + '''
        </div>
        <div class="footer">
            本报告由AI智能分析系统自动生成
        </div>
    </div>
</body>
</html>'''
        
        return html_content
    
    def on_send_chat_message(self):
        """发送聊天消息"""
        message = self.chat_input.text().strip()
        if not message:
            return
        
        if not self.faqs:
            self._add_fallback_card("请先上传知识库文件")
            return
        
        self.chat_input.clear()
        
        # 添加用户消息
        self._add_chat_message(message, is_user=True)
        self.chat_history.append({"role": "user", "content": message})
        
        # 使用AI匹配FAQ
        best_faq = self._find_best_faq_by_keywords(message)
        
        if not best_faq:
            self._add_fallback_card("未在知识库中找到匹配的问题现象，请联系产品组处理。")
            self.chat_history.append({"role": "ai", "content": "未在知识库中找到匹配的问题现象，请联系产品组处理。"})
            self.last_ai_response = "未在知识库中找到匹配的问题现象，请联系产品组处理。"
            return
        
        # 展示匹配到的知识库信息
        faq_question = best_faq.get('question', '未知问题')
        faq_cause = best_faq.get('cause', '')
        commands = best_faq.get('commands', '')
        execution_mode = best_faq.get('execution_mode', '远程')
        
        # 解析待执行命令列表
        command_lines = []
        if commands:
            for line in commands.split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    command_lines.append(line)
        
        # 展示匹配信息卡片
        self._add_match_info_card(faq_question, faq_cause, command_lines, execution_mode)
        
        # 弹出确认对话框
        confirm_dialog = QDialog(self)
        confirm_dialog.setWindowTitle("确认排障")
        confirm_dialog.setWindowIcon(dialog_icon("robot.svg"))
        confirm_dialog.setFixedSize(560, 380)
        confirm_dialog.setStyleSheet("""
            QDialog {
                background: #f8fafc;
            }
            QFrame#confirmHeader, QFrame#confirmContent {
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 10px;
            }
            QLabel#confirmTitle {
                font-size: 20px;
                font-weight: 700;
                color: #0f172a;
            }
            QLabel#confirmSubTitle {
                font-size: 12px;
                color: #64748b;
            }
            QLabel#confirmContentLabel {
                font-size: 13px;
                color: #334155;
                line-height: 1.6;
            }
            code {
                color: #0f766e;
                background: #f0fdfa;
            }
        """)
        
        dlg_layout = QVBoxLayout(confirm_dialog)
        dlg_layout.setSpacing(12)
        dlg_layout.setContentsMargins(18, 18, 18, 16)
        
        # 标题
        header_card = QFrame()
        header_card.setObjectName("confirmHeader")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(12)

        icon_label = QLabel()
        icon_label.setPixmap(dialog_icon("robot.svg").pixmap(30, 30))
        header_layout.addWidget(icon_label)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        dlg_title = QLabel("确认排障")
        dlg_title.setObjectName("confirmTitle")
        dlg_subtitle = QLabel("即将根据匹配到的知识库执行只读诊断命令")
        dlg_subtitle.setObjectName("confirmSubTitle")
        title_box.addWidget(dlg_title)
        title_box.addWidget(dlg_subtitle)
        header_layout.addLayout(title_box, 1)
        dlg_layout.addWidget(header_card)
        
        # 内容
        content_parts = [f"📋 <b>问题：</b>{faq_question}"]
        if faq_cause:
            content_parts.append(f"🔍 <b>可能原因：</b>{faq_cause}")
        if command_lines:
            cmd_text = "<br>".join(f"　　<code>{cmd}</code>" for cmd in command_lines)
            content_parts.append(f"💻 <b>将执行以下命令（{execution_mode}）：</b><br>{cmd_text}")
        else:
            content_parts.append("💻 无待执行命令")
        
        content_card = QFrame()
        content_card.setObjectName("confirmContent")
        content_layout = QVBoxLayout(content_card)
        content_layout.setContentsMargins(16, 14, 16, 14)
        content_layout.setSpacing(8)

        content_label = QLabel("<br>".join(content_parts))
        content_label.setObjectName("confirmContentLabel")
        content_label.setWordWrap(True)
        content_label.setTextFormat(Qt.TextFormat.RichText)
        content_layout.addWidget(content_label)
        dlg_layout.addWidget(content_card, 1)
        
        dlg_layout.addStretch()
        
        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(88, 36)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #ffffff; color: #334155; border: 1px solid #cbd5e1;
                border-radius: 8px; font-size: 13px; font-weight: 600;
            }
            QPushButton:hover { background: #f8fafc; border-color: #50C9C3; color: #0f766e; }
        """)
        cancel_btn.clicked.connect(confirm_dialog.reject)
        btn_layout.addWidget(cancel_btn)
        
        confirm_btn = QPushButton("确认执行")
        confirm_btn.setFixedSize(108, 36)
        confirm_btn.setStyleSheet("""
            QPushButton {
                background: #50C9C3; color: white; border: none;
                border-radius: 8px; font-size: 13px; font-weight: 700;
            }
            QPushButton:hover { background: #3eb8b3; }
        """)
        confirm_btn.clicked.connect(confirm_dialog.accept)
        btn_layout.addWidget(confirm_btn)
        
        dlg_layout.addLayout(btn_layout)
        
        if confirm_dialog.exec() != QDialog.DialogCode.Accepted:
            self._add_fallback_card("已取消排障")
            return
        
        # 用户确认后，添加加载中的消息
        loading_widget, loading_label = self._add_loading_message(self._ai_thinking_text())
        
        # 启动后台工作线程
        self.ai_worker = AITroubleshootWorker(
            best_faq, message, self.ssh_client, self.api_config
        )
        self.ai_worker.finished.connect(lambda answer, err, result_analysis, faq_question, faq_cause, user_question, command_results, is_match: self._on_ai_finished(
            answer, err, result_analysis, faq_question, faq_cause, user_question, command_results, is_match, loading_widget, loading_label
        ))
        self.ai_worker.start()
    
    def _on_ai_finished(self, answer: str, error: str, result_analysis: str, faq_question: str, faq_cause: str, user_question: str, command_results: list, is_match: bool, loading_widget=None, loading_label=None):
        """AI分析完成回调"""
        if loading_widget:
            loading_widget.setParent(None)
        
        if error:
            self._add_fallback_card(f"分析出错：{error}")
            return
        
        if answer:
            if "API调用失败" in answer or "API调用异常" in answer or "API Key未设置" in answer or "AI配置未加载" in answer:
                self._add_fallback_card(f"API调用失败，无法进行AI分析：{answer}")
                self.chat_history.append({"role": "ai", "content": answer})
                self.last_ai_response = answer
                return
            
            # 显示AI分析过程
            if result_analysis:
                self._add_analysis_result_card(result_analysis)
                self.chat_history.append({"role": "ai", "content": result_analysis})
            
            # 输出结论卡片
            self._add_conclusion_card(is_match, faq_question, faq_cause)
            conclusion_text = f"结论：{'符合该问题现象 - ' + faq_question if is_match else '不符合该问题原因，需要进一步排查'}"
            self.last_ai_response = result_analysis if result_analysis else conclusion_text
            self.chat_history.append({"role": "ai", "content": conclusion_text})
            
            self.current_ai_report = answer
            self.current_ai_report_meta = {
                "question": faq_question,
                "cause": faq_cause,
                "user_question": user_question,
                "timestamp": self._get_timestamp(),
                "result_analysis": result_analysis,
                "command_results": command_results,
                "is_match": is_match
            }
        else:
            self._add_fallback_card("分析失败，请联系产品组处理。")
            self.chat_history.append({"role": "ai", "content": "分析失败，请联系产品组处理。"})
            self.last_ai_response = "分析失败，请联系产品组处理。"
    
    def _add_ai_report_card(self, answer: str, user_question: str):
        """以表格+图标形式显示AI分析报告"""
        # 解析报告内容
        report_data = self._parse_report(answer)
        
        # 创建报告卡片
        card = QFrame()
        card.setObjectName("aiReportCard")
        card.setStyleSheet("""
            QFrame#aiReportCard {
                background: white;
                border-radius: 12px;
                border: 1px solid #e5e7eb;
                padding: 15px;
            }
        """)
        
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(10)
        card_layout.setContentsMargins(10, 10, 10, 10)
        
        # 标题行
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        
        # 图标标签 - 使用emoji
        icon_label = QLabel("📋")
        icon_label.setStyleSheet("font-size: 20px;")
        title_row.addWidget(icon_label)
        
        title_label = QLabel("AI 智能分析报告")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1f2937;")
        title_row.addWidget(title_label)
        title_row.addStretch()
        
        card_layout.addLayout(title_row)
        
        # 问题描述
        if report_data.get('question'):
            row = self._create_report_row("🔍", "问题", report_data['question'], "#3b82f6")
            card_layout.addLayout(row)
        
        # 可能原因
        if report_data.get('cause'):
            row = self._create_report_row("⚠️", "原因", report_data['cause'], "#f59e0b")
            card_layout.addLayout(row)
        
        # 系统检测发现
        findings = report_data.get('findings', [])
        if findings:
            findings_text = "\n".join([f"• {f}" for f in findings])
            row = self._create_report_row("🔧", "检测", findings_text, "#10b981")
            card_layout.addLayout(row)
        
        # 命令执行结果（表格形式）
        if report_data.get('commands'):
            cmd_frame = self._create_command_table(report_data['commands'])
            card_layout.addWidget(cmd_frame)
        
        # 添加到聊天布局
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, card)
        QTimer.singleShot(100, self._scroll_chat_to_bottom)
    
    def _create_report_row(self, icon: str, label: str, content: str, color: str) -> QHBoxLayout:
        """创建报告行"""
        row = QHBoxLayout()
        row.setSpacing(10)
        
        # 图标
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 16px; min-width: 24px;")
        row.addWidget(icon_lbl)
        
        # 标签
        label_lbl = QLabel(label)
        label_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {color}; min-width: 50px;")
        row.addWidget(label_lbl)
        
        # 内容（可复制）
        content_lbl = QLabel(content)
        content_lbl.setWordWrap(True)
        content_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        content_lbl.customContextMenuRequested.connect(lambda e, lbl=content_lbl: self._show_context_menu(e, lbl))
        content_lbl.setStyleSheet("font-size: 13px; color: #374151; background: #f9fafb; padding: 8px; border-radius: 6px;")
        row.addWidget(content_lbl, 1)
        
        return row
    
    def _create_command_table(self, commands: list) -> QFrame:
        """创建命令表格"""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background: #1e1e1e;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        
        layout = QVBoxLayout(frame)
        layout.setSpacing(5)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 表格标题
        title = QLabel("📊 命令执行结果")
        title.setStyleSheet("color: #50C9C3; font-size: 13px; font-weight: bold;")
        layout.addWidget(title)
        
        # 命令行
        for cmd_info in commands:
            cmd_row = QHBoxLayout()
            cmd_row.setSpacing(10)
            
            cmd_label = QLabel(f"$ {cmd_info.get('command', '')}")
            cmd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cmd_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            cmd_label.customContextMenuRequested.connect(lambda e, lbl=cmd_label: self._show_context_menu(e, lbl))
            cmd_label.setStyleSheet("color: #4ade80; font-family: Consolas, Monaco, monospace; font-size: 12px;")
            cmd_row.addWidget(cmd_label)
            cmd_row.addStretch()
            layout.addLayout(cmd_row)
            
            output_label = QLabel(cmd_info.get('output', ''))
            output_label.setWordWrap(True)
            output_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            output_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            output_label.customContextMenuRequested.connect(lambda e, lbl=output_label: self._show_context_menu(e, lbl))
            output_label.setStyleSheet("color: #d4d4d4; font-family: Consolas, Monaco, monospace; font-size: 12px; background: #2d2d2d; padding: 5px; border-radius: 4px;")
            layout.addWidget(output_label)
        
        return frame
    
    def _parse_report(self, answer: str) -> dict:
        """解析AI报告文本，提取各部分"""
        data = {
            'question': '',
            'cause': '',
            'findings': [],
            'ai_analysis': '',
            'commands': []
        }
        
        current_section = None
        current_content = []
        current_cmd_output = []
        in_command = False
        
        for line in answer.split('\n'):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            
            # 检测标题
            if '【问题描述】' in line:
                if current_section == 'question' and current_content:
                    data['question'] = ' '.join(current_content)
                current_section = 'question'
                current_content = []
                in_command = False
                continue
            elif '【可能原因】' in line:
                if current_section == 'question' and current_content:
                    data['question'] = ' '.join(current_content)
                current_section = 'cause'
                current_content = []
                in_command = False
                continue
            elif '【系统检测发现】' in line:
                if current_section == 'cause' and current_content:
                    data['cause'] = ' '.join(current_content)
                current_section = 'findings'
                current_content = []
                in_command = False
                continue
            elif '【AI智能分析】' in line:
                if current_section == 'findings' and current_content:
                    data['findings'] = [c.lstrip('- ').lstrip('• ') for c in current_content]
                current_section = 'ai_analysis'
                current_content = []
                in_command = False
                continue
            elif '【命令执行结果】' in line:
                if current_section == 'ai_analysis' and current_content:
                    data['ai_analysis'] = ' '.join(current_content)
                current_section = 'commands'
                current_content = []
                in_command = False
                continue
            
            if current_section == 'commands':
                # 解析命令
                if line_stripped.startswith('$ '):
                    # 保存上一个命令的输出
                    if data['commands'] and current_cmd_output:
                        data['commands'][-1]['output'] = '\n'.join(current_cmd_output).strip()
                        current_cmd_output = []
                    # 新命令
                    cmd = line_stripped[2:].strip()
                    data['commands'].append({'command': cmd, 'output': ''})
                    in_command = True
                elif line_stripped.startswith('[Error]') or line_stripped.startswith('[Exception]'):
                    current_cmd_output.append(line_stripped)
                elif in_command and data['commands']:
                    current_cmd_output.append(line_stripped)
            elif current_section:
                current_content.append(line_stripped)
        
        # 保存最后一个命令的输出
        if data['commands'] and current_cmd_output:
            data['commands'][-1]['output'] = '\n'.join(current_cmd_output).strip()
        
        # 保存最后一部分
        if current_section == 'question' and current_content:
            data['question'] = ' '.join(current_content)
        elif current_section == 'cause' and current_content:
            data['cause'] = ' '.join(current_content)
        elif current_section == 'findings' and current_content:
            data['findings'] = [c.lstrip('- ').lstrip('• ') for c in current_content]
        elif current_section == 'ai_analysis' and current_content:
            data['ai_analysis'] = ' '.join(current_content)
        
        return data
    
    def _find_faq_answer(self, question: str) -> str:
        """在FAQ知识库中查找最匹配的回答（使用AI匹配）"""
        if not self.faqs:
            return None
        
        # 使用AI匹配最佳FAQ
        best_faq = self._find_best_faq_by_ai(question)
        
        if not best_faq:
            return None
        
        # 如果FAQ包含命令，则执行命令并返回结果
        commands = best_faq.get('commands', '')
        if commands:
            return self._execute_faq_commands(best_faq, question)
        
        return best_faq.get('answer', '')
    
    def _find_best_faq_by_ai(self, user_question: str) -> dict:
        """使用AI在知识库中查找最匹配的FAQ"""
        if not self.api_config:
            # 如果没有AI配置，使用关键词匹配作为后备
            return self._find_best_faq_by_keywords(user_question)
        
        # 构建FAQ列表供AI参考
        faq_list_text = ""
        for i, faq in enumerate(self.faqs):
            q = faq.get('question', '')
            k = faq.get('keywords', '')
            faq_list_text += f"[{i}] 问题: {q}\n  关键词: {k}\n"
        
        prompt = f"""你是一个专业的智能问答匹配系统。用户的提问可能包含口语化表达、简写、拼写错误等，你需要从下面的FAQ列表中找到最匹配的一条。

FAQ列表：
{faq_list_text}

用户的提问是：{user_question}

请根据以下规则进行匹配：
1. 理解用户提问的真实意图，不要字面匹配
2. 注意同义词替换（如"登录"=" sign in"="登陆"）
3. 注意关键词匹配（"验证码"相关的问题应该匹配包含"验证码"关键词的FAQ）
4. 匹配多个关键词时匹配度更高

请只返回一个数字，即最匹配的FAQ编号，如果有多个可能的匹配，选择最相关的一个。
如果没有找到任何相关的FAQ，请返回 "-1"。

你的回答只包含一个数字，不要包含其他内容。"""
        
        try:
            # 使用简单的API调用获取AI响应
            import urllib.request
            import urllib.error
            
            api_type_raw = self.api_config.get('api_type', 'openai')
            api_key = self.api_config.get('api_key', '')
            base_url = self.api_config.get('base_url', '')
            model = self.api_config.get('model', '')
            
            # API类型映射
            api_type_map = {
                0: "openai", 1: "anthropic", 2: "azure", 3: "ollama",
                5: "openai", 6: "openai", 7: "openai", 8: "openai", 9: "openai", 10: "openai", 12: "openai", 13: "openai", 14: "openai"
            }
            api_type = api_type_map.get(api_type_raw, "openai") if isinstance(api_type_raw, int) else api_type_raw
            
            if api_type == 'openai' or api_type == 'azure':
                url = build_openai_chat_url(base_url)
                messages = [
                    {"role": "user", "content": prompt}
                ]
                data = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": 10
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
                with urllib.request.urlopen(req, timeout=self.api_config.get('api_timeout', 60)) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    ai_response = result['choices'][0]['message']['content'].strip()
            
            elif api_type == 'ollama':
                url = f"{base_url}/chat"
                data = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False
                }
                headers = {"Content-Type": "application/json"}
                req = urllib.request.Request(
                    url,
                    data=json.dumps(data).encode('utf-8'),
                    headers=headers,
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=self.api_config.get('api_timeout', 60)) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    ai_response = result['message']['content'].strip()
            else:
                return self._find_best_faq_by_keywords(user_question)
            
            # 解析AI响应
            if ai_response == "-1":
                return None
            
            # 提取数字
            import re
            match = re.search(r'-?\d+', ai_response)
            if match:
                index = int(match.group())
                if 0 <= index < len(self.faqs):
                    return self.faqs[index]
            
            return self._find_best_faq_by_keywords(user_question)
        
        except Exception as e:
            print(f"[AI Match] Failed: {e}")
            # 出错时使用关键词匹配作为后备
            return self._find_best_faq_by_keywords(user_question)
    
    def _find_best_faq_by_keywords(self, user_question: str) -> dict:
        question_lower = user_question.lower()
        best_score = 0
        best_faq = None
        
        q_words = self._tokenize(question_lower)
        q_words_extended = self._expand_keywords(q_words)
        
        for faq in self.faqs:
            faq_question = faq.get('question', '').lower()
            faq_keywords = faq.get('keywords', '').lower()
            
            if not faq_keywords:
                content = faq.get('content', '')
                kw_match = re.search(r'关键词[：:]\s*(.+)', content)
                if kw_match:
                    faq_keywords = kw_match.group(1).strip().lower()
            
            faq_keyword_words = set(faq_keywords.replace('，', ',').replace('、', ',').split(',')) if faq_keywords else set()
            faq_keyword_words = {w.strip() for w in faq_keyword_words if w.strip()}
            
            faq_question_words = set(self._tokenize(faq_question))
            faq_words_extended = self._expand_keywords(faq_question_words | faq_keyword_words)
            
            jaccard_score = self._jaccard_similarity(q_words_extended, faq_words_extended)
            substring_score = self._substring_similarity(question_lower, faq_question)
            edit_score = self._edit_similarity(question_lower, faq_question)
            keyword_score = self._keyword_match_score(q_words_extended, faq_keyword_words) if faq_keyword_words else 0
            
            core_keywords = {"验证码", "登录", "网络", "服务", "系统", "磁盘", "内存", "CPU", "负载", "进程", "Docker", "docker"}
            core_match = self._has_core_keyword_match(q_words, faq_question_words | faq_keyword_words, core_keywords)
            
            question_score = (
                jaccard_score * 0.30 +
                substring_score * 0.30 +
                edit_score * 0.20 +
                core_match * 0.20
            )
            
            keyword_match_rate = 0.0
            if faq_keyword_words:
                matched = q_words_extended & faq_keyword_words
                keyword_match_rate = len(matched) / len(faq_keyword_words) if faq_keyword_words else 0
            
            final_score = question_score * 0.5 + keyword_score * 0.25 + keyword_match_rate * 0.25
            
            print(f"[DEBUG] FAQ match: question='{faq_question[:30]}' score={final_score:.3f} (q={question_score:.3f} kw={keyword_score:.3f} kmr={keyword_match_rate:.3f})")
            
            if final_score > best_score:
                best_score = final_score
                best_faq = faq
        
        if best_faq is not None:
            threshold = 0.10 if len(self.faqs) <= 3 else 0.15
            if best_score >= threshold:
                return best_faq
            
            if self._has_any_keyword_match(q_words, question_lower):
                return best_faq
            
            if len(self.faqs) == 1 and best_score > 0:
                return best_faq
        
        return None
    
    def _expand_keywords(self, words: set) -> set:
        """扩展关键词，同义词、近义词替换"""
        # 同义词词典
        synonyms = {
            # 验证码相关
            "验证码": {"验证码", "CAPTCHA", "图形验证码", "登录验证码", "网站验证码", "图片验证码", "手机验证码", "短信验证码"},
            "不显示": {"不显示", "无法显示", "显示不出来", "加载失败", "加载异常", "加载错误", "不加载", "无法加载", "加载慢", "显示异常"},
            "显示": {"显示", "展示", "出现", "加载"},
            "失败": {"失败", "错误", "异常", "异常", "故障", "问题"},
            
            # 登录相关
            "登录": {"登录", "登陆", "signin", "sign-in", "logon", "log-in", "认证", "验证"},
            "无法登录": {"无法登录", "登录失败", "登陆失败", "登录异常", "登录不了", "登录不上"},
            
            # 网络相关
            "网络": {"网络", "网卡", "网络连接", "网络状态", "ping", "telnet", "连通性"},
            "连接": {"连接", "连通", "不通", "无法连接", "连接失败"},
            
            # 服务相关
            "服务": {"服务", "service", "进程", "进程", "docker", "容器"},
            "服务状态": {"服务状态", "服务运行", "服务停止", "服务异常", "服务重启"},
            
            # 系统相关
            "系统": {"系统", "操作系统", "server", "服务器"},
            "磁盘": {"磁盘", "硬盘", "存储", "disk", "space"},
            "内存": {"内存", "mem", "RAM"},
            "cpu": {"CPU", "cpu", "处理器", "负载", "load"},
            "负载": {"负载", "load", "load average", "高负载"},
            
            # 进程相关
            "进程": {"进程", "process", "CPU", "内存"},
            "高进程": {"高CPU", "高内存", "进程异常", "进程占用"},
        }
        
        expanded = set(words)
        for word in words:
            # 添加同义词
            if word in synonyms:
                expanded.update(synonyms[word])
            # 检查是否匹配同义词的键
            for key, values in synonyms.items():
                if word in values:
                    expanded.add(key)
                    expanded.update(values)
        
        return expanded
    
    def _has_core_keyword_match(self, q_words: set, faq_words: set, core_keywords: set) -> float:
        """检查是否有核心关键词匹配"""
        q_core = q_words & core_keywords
        faq_core = faq_words & core_keywords
        
        if not q_core or not faq_core:
            return 0
        
        # 有核心词匹配
        if q_core & faq_core:
            return 1.0
        
        # 检查扩展后是否匹配
        q_expanded = self._expand_keywords(q_words)
        faq_expanded = self._expand_keywords(faq_words)
        
        if q_expanded & faq_expanded & core_keywords:
            return 1.0
        
        return 0
    
    def _has_any_keyword_match(self, q_words: set, question_lower: str) -> bool:
        """检查是否有任何关键词匹配"""
        for faq in self.faqs:
            faq_question = faq.get('question', '').lower()
            faq_keywords = faq.get('keywords', '').lower()
            
            # 直接检查子串
            for word in q_words:
                if len(word) >= 2 and word in faq_question:
                    return True
                if len(word) >= 2 and word in faq_keywords:
                    return True
            
            # 检查FAQ关键词
            faq_keyword_words = set(faq_keywords.replace('，', ',').replace('、', ',').split(',')) if faq_keywords else set()
            if q_words & faq_keyword_words:
                return True
        
        return False
    
    def _execute_faq_commands(self, faq: dict, user_question: str) -> str:
        """执行FAQ中的命令并通过AI返回分析结果"""
        commands = faq.get('commands', '')
        cause = faq.get('cause', '')
        keywords = faq.get('keywords', '')
        result_analysis = faq.get('result_analysis', '')
        
        if not self.ssh_client or not hasattr(self.ssh_client, 'execute'):
            return f"【{faq.get('question', '')}】\n\n原因: {cause}\n\n命令未执行: 未连接服务器\n\n结果判断: {result_analysis}"
        
        # 执行命令
        command_lines = [line.strip() for line in commands.split('\n') if line.strip() and not line.strip().startswith('#')]
        
        if not command_lines:
            return f"【{faq.get('question', '')}】\n\n原因: {cause}\n\n该FAQ没有配置执行命令\n\n结果判断: {result_analysis}"
        
        result_text = ""
        command_results = []  # 保存每个命令的输出用于分析
        
        for cmd in command_lines:
            result_text += f"\n$ {cmd}\n"
            cmd_output = ""
            try:
                return_code, output, error = self.ssh_client.execute(cmd)
                if output:
                    cmd_output = output
                    result_text += output
                if error:
                    cmd_output += f"[Error] {error}"
                    result_text += f"[Error] {error}"
            except Exception as e:
                cmd_output = f"[Exception] {str(e)}"
                result_text += f"[Exception] {str(e)}"
            
            command_results.append({"command": cmd, "output": cmd_output})
        
        # 智能分析结果
        analysis = self._analyze_command_results(command_results, result_analysis)
        
        # 构建AI分析提示词 - 严格遵循FAQ内容
        prompt = """你是一个专业的系统运维诊断助手。你的职责是根据FAQ中定义的问题、原因、判断依据来分析和验证问题。

【重要规则】
1. 严格按照FAQ提供的【预设原因】和【结果判断依据】进行分析
2. 不要自行推断或发散到FAQ未提及的原因
3. 只基于【执行的命令】的实际输出来验证问题
4. 如果命令输出与FAQ预设不符，基于实际输出给出结论
5. 结论必须引用具体的命令输出作为证据
6、除了特定命令和名词，其他输出全部使用中文，务必使用中文，重要！！！！

【输出格式】
请按以下格式回复：
1. 问题确认 - 基于FAQ描述和命令输出，确认问题是否匹配
2. 原因验证 - 基于FAQ预设原因和实际命令输出，验证原因是否正确
3. 命令解读 - 列出关键命令及其输出结果
4. 结论 - 基于FAQ判断依据和实际输出，给出明确结论
5. 建议 - 仅基于FAQ中提到的处理方法，不要自行发挥
"""
        
        faq_question = faq.get('question', '')
        preliminary = self._generate_preliminary_analysis(analysis)
        data_for_analysis = f"""【FAQ问题描述】
{faq_question}

【预设的可能原因】
{cause}

【预设的关键词】
{keywords}

【预设的结果判断依据】
{result_analysis}

【用户实际提问】
{user_question}

【执行的命令及结果】
{result_text}

【系统初步检测结果】
{preliminary}

请严格按照上述FAQ内容进行分析，不要发散到其他原因或方法。
"""
        
        # 调用AI API进行真实分析
        ai_response = self._call_ai_api(prompt, data_for_analysis)
        
        # 生成完整的AI分析报告
        return self._generate_ai_analysis_report(
            user_question, cause, keywords,
            result_text, analysis, ai_response
        )
    
    def _analyze_command_results(self, command_results: list, result_analysis: str) -> dict:
        """分析命令执行结果，提取关键发现"""
        findings = []
        warnings = []
        
        for item in command_results:
            output = item.get("output", "")
            cmd = item.get("command", "")
            
            # 磁盘使用率检查
            if "df -h" in cmd or "df" in cmd:
                findings.extend(self._analyze_disk_usage(output))
            
            # 内存检查
            if "mem" in cmd.lower() or "free" in cmd:
                findings.extend(self._analyze_memory_usage(output))
            
            # CPU负载检查
            if "cpu" in cmd.lower() or "load" in cmd.lower():
                findings.extend(self._analyze_cpu_load(output))
            
            # 进程检查
            if "ps" in cmd or "process" in cmd.lower():
                findings.extend(self._analyze_processes(output))
            
            # Docker检查
            if "docker" in cmd.lower():
                findings.extend(self._analyze_docker_status(output))
        
        return {
            "findings": findings,
            "warnings": warnings,
            "result_analysis": result_analysis,
            "command_results": command_results
        }
    
    def _generate_preliminary_analysis(self, analysis: dict) -> str:
        """生成初步分析结果（供AI参考）"""
        findings = analysis.get("findings", [])
        warnings = analysis.get("warnings", [])
        command_results = analysis.get("command_results", [])
        
        lines = []
        
        if findings:
            lines.append("系统检测发现：")
            for f in findings:
                lines.append(f"  - {f}")
        
        if warnings:
            lines.append("警告信息：")
            for w in warnings:
                lines.append(f"  - {w}")
        
        if command_results:
            lines.append(f"共执行 {len(command_results)} 个命令")
        
        return "\n".join(lines) if lines else "未发现明显异常"
    
    def _analyze_disk_usage(self, output: str) -> list:
        """分析磁盘使用情况"""
        findings = []
        lines = output.split('\n')
        for line in lines:
            if '%' in line and ('dev' in line or '/dev' in line):
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        usage_pct = int(parts[4].replace('%', ''))
                        mount_point = parts[-1] if len(parts) > 5 else parts[-1]
                        if usage_pct >= 90:
                            findings.append(f"磁盘告警: {mount_point} 使用率 {usage_pct}% (严重)")
                        elif usage_pct >= 80:
                            findings.append(f"磁盘警告: {mount_point} 使用率 {usage_pct}%")
                    except:
                        pass
        return findings
    
    def _analyze_memory_usage(self, output: str) -> list:
        """分析内存使用情况"""
        findings = []
        lines = output.split('\n')
        for line in lines:
            if 'Mem:' in line or '内存' in line:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        # 尝试提取内存使用率
                        for i, p in enumerate(parts):
                            if p.endswith('Gi') or p.endswith('Mi'):
                                total_idx = i - 2 if i >= 2 else 0
                                if total_idx >= 0 and 'total' not in parts[total_idx].lower():
                                    pass  # 简化处理
                    except:
                        pass
        return findings
    
    def _analyze_cpu_load(self, output: str) -> list:
        """分析CPU负载"""
        findings = []
        if 'load average' in output or '负载' in output:
            # 提取负载值
            import re
            loads = re.findall(r'(\d+\.\d+)', output.split('load average')[-1] if 'load average' in output else output)
            if loads:
                try:
                    load_1min = float(loads[0])
                    if load_1min > 10:
                        findings.append(f"CPU负载过高: {load_1min}")
                    elif load_1min > 5:
                        findings.append(f"CPU负载较高: {load_1min}")
                except:
                    pass
        return findings
    
    def _analyze_processes(self, output: str) -> list:
        """分析进程状态"""
        findings = []
        lines = output.split('\n')
        for line in lines:
            # 检测高CPU或内存进程
            parts = line.split()
            if len(parts) >= 9:
                try:
                    cpu = float(parts[2]) if parts[2].replace('.', '').isdigit() else 0
                    mem = float(parts[3]) if parts[3].replace('.', '').isdigit() else 0
                    if cpu > 80:
                        proc_name = parts[-1] if parts else "unknown"
                        findings.append(f"高CPU进程: {proc_name} ({cpu}%)")
                    if mem > 80:
                        proc_name = parts[-1] if parts else "unknown"
                        findings.append(f"高内存进程: {proc_name} ({mem}%)")
                except:
                    pass
        return findings
    
    def _analyze_docker_status(self, output: str) -> list:
        """分析Docker容器状态"""
        findings = []
        if 'docker' in output.lower():
            if 'error' in output.lower() or 'Error' in output:
                findings.append("Docker状态异常")
            if 'running' in output.lower():
                findings.append("Docker容器运行中")
        return findings
    
    def _format_analysis_result(self, question: str, cause: str, command_output: str, analysis: dict) -> str:
        """格式化分析结果为易读的文本"""
        lines = []
        lines.append("=" * 50)
        lines.append(f"【问题】{question}")
        lines.append("=" * 50)
        
        # 原因
        if cause:
            lines.append(f"\n【可能原因】\n{cause}")
        
        # 关键发现
        findings = analysis.get("findings", [])
        if findings:
            lines.append(f"\n【关键发现】")
            for finding in findings:
                lines.append(f"  - {finding}")
        
        # 警告
        warnings = analysis.get("warnings", [])
        if warnings:
            lines.append(f"\n【警告】")
            for warning in warnings:
                lines.append(f"  - {warning}")
        
        # 命令输出
        lines.append(f"\n【命令输出】\n{command_output}")
        
        # 结果判断
        result_analysis = analysis.get("result_analysis", "")
        if result_analysis:
            lines.append(f"\n【结果判断】\n{result_analysis}")
        
        # 建议
        if findings or warnings:
            lines.append(f"\n【处理建议】")
            if any("告警" in f or "警告" in f for f in findings):
                lines.append("  1. 建议立即处理上述问题")
            if "负载" in str(findings):
                lines.append("  2. 关注系统负载情况")
            lines.append("  3. 如需帮助，请联系运维人员")
        
        return "\n".join(lines)
    
    def _tokenize(self, text: str) -> set:
        """分词"""
        import re
        text = re.sub(r'[^\w\s]', ' ', text)
        words = text.split()
        stopwords = {'的', '了', '是', '在', '和', '与', '或', '的', '我', '你', '他', '她', '它', '这', '那', '有', '没有', '什么', '怎么', '如何', '吗', '呢', '吧', '啊', '哦', '嗯', '就', '都', '也', '还', '又', '很', '太', '更', '最', '非常', '一个', '一', '二', '三'}
        return {w for w in words if len(w) >= 2 and w not in stopwords}
    
    def _jaccard_similarity(self, words1: set, words2: set) -> float:
        if not words1 or not words2:
            return 0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0
    
    def _substring_similarity(self, text1: str, text2: str) -> float:
        shorter = text1 if len(text1) <= len(text2) else text2
        longer = text2 if shorter == text1 else text1
        
        if shorter in longer:
            return 0.9
        
        n = min(3, len(shorter) // 2)
        if n < 1:
            return 0
        
        ngrams1 = set(text1[i:i+n] for i in range(len(text1)-n+1))
        ngrams2 = set(text2[i:i+n] for i in range(len(text2)-n+1))
        
        if not ngrams1 or not ngrams2:
            return 0
        
        overlap = len(ngrams1 & ngrams2)
        total = len(ngrams1 | ngrams2)
        
        return overlap / total if total > 0 else 0
    
    def _edit_similarity(self, text1: str, text2: str) -> float:
        if not text1 or not text2:
            return 0
        if text1 == text2:
            return 1.0
        if text1 in text2 or text2 in text1:
            shorter_len = min(len(text1), len(text2))
            longer_len = max(len(text1), len(text2))
            return shorter_len / longer_len
        
        distance = self._levenshtein_distance(text1, text2)
        max_len = max(len(text1), len(text2))
        
        return 1 - (distance / max_len) if max_len > 0 else 0
    
    def _levenshtein_distance(self, s1: str, s2: str) -> int:
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
        if not words1 or not words2:
            return 0
        matches = words1 & words2
        if not matches:
            return 0
        return len(matches) / len(words2) if words2 else 0
    
    def _show_context_menu(self, pos, label):
        """显示自定义右键菜单"""
        menu = QMenu(label)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1f2937;
                color: #50C9C3;
                border: 1px solid #50C9C3;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #374151;
            }
        """)
        
        copy_action = menu.addAction("复制")
        copy_action.triggered.connect(lambda: self._copy_label_text(label))
        select_all_action = menu.addAction("全选")
        select_all_action.triggered.connect(lambda: label.selectAll())
        
        menu.exec(label.mapToGlobal(pos))
    
    def _copy_label_text(self, label):
        """复制标签文本"""
        clipboard = QApplication.clipboard()
        selected = label.selectedText()
        clipboard.setText(selected if selected else label.text())
    
    def _add_loading_message(self, text: str):
        """添加加载中的消息（带动画）"""
        msg_frame = QFrame()
        msg_frame.setObjectName("chatMessage")
        msg_frame.setStyleSheet("""
            QFrame#chatMessage {
                background: #f3f4f6;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        
        layout = QHBoxLayout(msg_frame)
        label = QLabel(text)
        label.setProperty("loadingBaseText", text)
        label.setWordWrap(True)
        label.setStyleSheet("color: #50C9C3; font-size: 13px; font-weight: bold;")
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg_frame)
        QTimer.singleShot(100, self._scroll_chat_to_bottom)
        
        # 启动动画
        self._animate_loading(label, 0)
        
        return msg_frame, label
    
    def _animate_loading(self, label: QLabel, count: int):
        """动画：添加省略号"""
        if count < 3:
            dots = "." * (count + 1)
            base_text = label.property("loadingBaseText") or "AI思考中"
            if "\n" in base_text:
                first_line, rest = base_text.split("\n", 1)
                label.setText(f"{first_line}{dots}\n{rest}")
            else:
                label.setText(f"{base_text}{dots}")
            count += 1
            QTimer.singleShot(500, lambda: self._animate_loading(label, count))

    def _ai_thinking_text(self) -> str:
        configs = normalize_ai_configs(self.api_config or {})
        if configs:
            model_name = configs[0].get("model") or configs[0].get("name")
            if model_name:
                return f"AI思考中\n当前使用模型：{model_name}"
        return "AI思考中"
    
    def _add_chat_message(self, message: str, is_user: bool):
        """添加聊天消息"""
        msg_frame = QFrame()
        msg_frame.setObjectName("chatMessage")
        
        if is_user:
            msg_frame.setStyleSheet("""
                QFrame#chatMessage {
                    background: #ecfeff;
                    border: 1px solid #99f6e4;
                    border-radius: 10px;
                    padding: 10px;
                }
            """)
            layout = QHBoxLayout(msg_frame)
            layout.setContentsMargins(12, 10, 12, 10)
            label = QLabel(message)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            label.setStyleSheet("color: #134e4a; font-size: 13px;")
            label.installEventFilter(self)
            label.customContextMenuRequested.connect(lambda e, lbl=label: self._show_context_menu(e, lbl))
            label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            layout.addWidget(label, 1, Qt.AlignmentFlag.AlignLeft)
        else:
            msg_frame.setStyleSheet("""
                QFrame#chatMessage {
                    background: #f3f4f6;
                    border-radius: 10px;
                    padding: 10px;
                }
            """)
            layout = QHBoxLayout(msg_frame)
            label = QLabel(message)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setStyleSheet("color: #1f2937; font-size: 13px;")
            label.installEventFilter(self)
            label.customContextMenuRequested.connect(lambda e, lbl=label: self._show_context_menu(e, lbl))
            label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignLeft)
            layout.addStretch()
        
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg_frame)
        QTimer.singleShot(100, self._scroll_chat_to_bottom)
    
    def _add_match_info_card(self, faq_question: str, faq_cause: str, command_lines: list, execution_mode: str):
        """添加匹配信息卡片（蓝色边框）"""
        card = QFrame()
        card.setObjectName("matchInfoCard")
        card.setStyleSheet("""
            QFrame#matchInfoCard {
                background: #ffffff;
                border: 1px solid #bfdbfe;
                border-left: 4px solid #3b82f6;
                border-radius: 8px;
                padding: 12px 16px;
            }
        """)
        
        layout = QVBoxLayout(card)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 10, 12, 10)
        
        # 标题行
        title_label = QLabel("📚 匹配到知识库")
        title_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #1e40af; border: none;")
        layout.addWidget(title_label)
        
        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #dbeafe; border: none; max-height: 1px;")
        layout.addWidget(sep)
        
        # 问题名称
        q_label = QLabel(f"📋 <b>问题：</b>{faq_question}")
        q_label.setWordWrap(True)
        q_label.setStyleSheet("font-size: 13px; color: #1f2937; border: none;")
        q_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(q_label)
        
        # 可能原因
        if faq_cause:
            c_label = QLabel(f"🔍 <b>可能原因：</b>{faq_cause}")
            c_label.setWordWrap(True)
            c_label.setStyleSheet("font-size: 13px; color: #1f2937; border: none;")
            c_label.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(c_label)
        
        # 待执行命令
        if command_lines:
            cmd_header = QLabel(f"💻 <b>将执行以下命令（{execution_mode}）：</b>")
            cmd_header.setStyleSheet("font-size: 13px; color: #1f2937; border: none;")
            cmd_header.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(cmd_header)
            
            for cmd in command_lines:
                cmd_label = QLabel(f"　<code style='background:#f1f5f9; padding:2px 6px; border-radius:3px; font-family:Consolas,monospace; font-size:12px; color:#334155;'>{cmd}</code>")
                cmd_label.setWordWrap(True)
                cmd_label.setStyleSheet("font-size: 12px; color: #334155; border: none; margin-left: 8px;")
                cmd_label.setTextFormat(Qt.TextFormat.RichText)
                layout.addWidget(cmd_label)
        else:
            no_cmd = QLabel("💻 无待执行命令")
            no_cmd.setStyleSheet("font-size: 13px; color: #9ca3af; border: none;")
            layout.addWidget(no_cmd)
        
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, card)
        QTimer.singleShot(100, self._scroll_chat_to_bottom)

    def _add_analysis_result_card(self, result_analysis: str):
        """添加美化后的分析过程卡片。"""
        card = QFrame()
        card.setObjectName("analysisResultCard")
        card.setStyleSheet("""
            QFrame#analysisResultCard {
                background: #ffffff;
                border: 1px solid #c7d2fe;
                border-left: 4px solid #6366f1;
                border-radius: 10px;
                padding: 14px 16px;
            }
        """)

        layout = QVBoxLayout(card)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 12, 14, 12)

        title = QLabel("分析过程与结论说明")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #3730a3; border: none;")
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #e0e7ff; border: none; max-height: 1px;")
        layout.addWidget(sep)

        body = QLabel(self._markdown_to_html(result_analysis))
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        body.setStyleSheet("""
            QLabel {
                color: #1f2937;
                font-size: 13px;
                line-height: 1.6;
                border: none;
            }
            strong {
                color: #111827;
            }
        """)
        layout.addWidget(body)

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, card)
        QTimer.singleShot(100, self._scroll_chat_to_bottom)
    
    def _add_conclusion_card(self, is_match: bool, faq_question: str = "", faq_cause: str = ""):
        """添加结论卡片"""
        card = QFrame()
        card.setObjectName("conclusionCard")
        
        if is_match:
            card.setStyleSheet("""
                QFrame#conclusionCard {
                    background: #f0fdf4;
                    border: 1px solid #86efac;
                    border-left: 4px solid #22c55e;
                    border-radius: 8px;
                    padding: 12px 16px;
                }
            """)
        else:
            card.setStyleSheet("""
                QFrame#conclusionCard {
                    background: #fffbeb;
                    border: 1px solid #fcd34d;
                    border-left: 4px solid #f59e0b;
                    border-radius: 8px;
                    padding: 12px 16px;
                }
            """)
        
        layout = QVBoxLayout(card)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 10, 12, 10)
        
        if is_match:
            title = QLabel("结论说明：符合该问题现象")
            title.setStyleSheet("font-size: 14px; font-weight: bold; color: #166534; border: none;")
            layout.addWidget(title)
            
            if faq_question:
                q_label = QLabel(f"<b>问题：</b>{faq_question}")
                q_label.setWordWrap(True)
                q_label.setStyleSheet("font-size: 13px; color: #1f2937; border: none;")
                q_label.setTextFormat(Qt.TextFormat.RichText)
                layout.addWidget(q_label)
            
            if faq_cause:
                c_label = QLabel(f"<b>原因：</b>{faq_cause}")
                c_label.setWordWrap(True)
                c_label.setStyleSheet("font-size: 13px; color: #1f2937; border: none;")
                c_label.setTextFormat(Qt.TextFormat.RichText)
                layout.addWidget(c_label)
        else:
            title = QLabel("结论说明：不符合该问题原因")
            title.setStyleSheet("font-size: 14px; font-weight: bold; color: #92400e; border: none;")
            layout.addWidget(title)
            
            hint = QLabel("需要进一步排查，如无法定位问题，请联系产品组处理。")
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 13px; color: #78716c; border: none;")
            layout.addWidget(hint)
        
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, card)
        QTimer.singleShot(100, self._scroll_chat_to_bottom)
    
    def _add_fallback_card(self, message: str):
        """添加兜底回复卡片（灰色）"""
        card = QFrame()
        card.setObjectName("fallbackCard")
        card.setStyleSheet("""
            QFrame#fallbackCard {
                background: #f9fafb;
                border: 1px solid #d1d5db;
                border-left: 4px solid #9ca3af;
                border-radius: 8px;
                padding: 12px 16px;
            }
        """)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        
        label = QLabel(message)
        label.setWordWrap(True)
        label.setStyleSheet("font-size: 13px; color: #4b5563; border: none;")
        layout.addWidget(label)
        
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, card)
        QTimer.singleShot(100, self._scroll_chat_to_bottom)
    
    def _scroll_chat_to_bottom(self):
        """滚动聊天到底部"""
        self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()
        )
    
    def on_copy_last_result(self):
        """复制最后一次AI回复到剪贴板"""
        if not self.last_ai_response:
            MessageBox("提示", "没有可复制的AI回复", self).exec()
            return
        
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(self.last_ai_response)
        MessageBox("已复制", "AI回复已复制到剪贴板", self).exec()
    
    def on_export_chat_history(self):
        """导出FAQ详情和命令执行结果"""
        if not self.chat_history:
            MessageBox("提示", "没有可导出的内容", self).exec()
            return
        
        from PyQt6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出记录",
            f"AI排障记录_{self.current_product}_{self._get_timestamp()}.txt",
            "文本文件 (*.txt);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write(f"AI排障记录\n")
                f.write(f"产品: {self.current_product}\n")
                f.write(f"导出时间: {self._get_timestamp()}\n")
                f.write("=" * 60 + "\n\n")
                
                for msg in self.chat_history:
                    if msg.get("role") == "ai":
                        # 从current_ai_report_meta获取FAQ原始数据
                        faq_meta = getattr(self, 'current_ai_report_meta', {})
                        if not faq_meta:
                            continue
                        
                        faq_question = faq_meta.get('question', '')
                        faq_cause = faq_meta.get('cause', '')
                        user_question = faq_meta.get('user_question', '')
                        result_analysis = faq_meta.get('result_analysis', '')
                        
                        # 解析当前AI报告获取命令执行结果
                        current_report = getattr(self, 'current_ai_report', '')
                        report_data = self._parse_report(current_report) if current_report else {'commands': []}
                        
                        # 用户提问
                        if user_question:
                            f.write("【用户提问】\n")
                            f.write(f"{user_question}\n\n")
                        
                        # 问题描述 - 使用FAQ的问题描述
                        if faq_question:
                            f.write("【问题描述】\n")
                            f.write(f"{faq_question}\n\n")
                        
                        # 可能原因 - 从FAQ获取
                        if faq_cause:
                            f.write("【可能原因】\n")
                            f.write(f"{faq_cause}\n\n")
                        
                        # 命令执行结果
                        commands = report_data.get('commands', [])
                        if commands:
                            f.write("【命令执行结果】\n")
                            for cmd_info in commands:
                                cmd = cmd_info.get('command', '')
                                output = cmd_info.get('output', '')
                                f.write(f"$ {cmd}\n")
                                if output:
                                    f.write(f"{output}\n")
                            f.write("\n")
                        
                        # 结果判断
                        if result_analysis:
                            f.write("【结果判断】\n")
                            f.write(f"{result_analysis}\n\n")
                        
                        f.write("=" * 40 + "\n\n")
            
            MessageBox("导出成功", f"记录已保存到:\n{file_path}", self).exec()
        except Exception as e:
            MessageBox("导出失败", f"导出时出错:\n{str(e)}", self).exec()
    
    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def load_ai_config(self):
        """加载AI配置"""
        config_file = get_ai_config_path()

        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    self.api_config = json.load(f)
                
                # 清理配置值中的反引号和多余空格
                if self.api_config:
                    if 'base_url' in self.api_config:
                        self.api_config['base_url'] = self.api_config['base_url'].replace('`', '').strip()
                    if 'api_key' in self.api_config:
                        self.api_config['api_key'] = self.api_config['api_key'].replace('`', '').strip()
                    if 'model' in self.api_config:
                        self.api_config['model'] = self.api_config['model'].replace('`', '').strip()
                
                print(f"[AI Config] Loaded config from {config_file}")
            except Exception as e:
                print(f"[AI Config] Failed to load config: {e}")
                self.api_config = None
        else:
            print(f"[AI Config] Config file not found: {config_file}")
            self.api_config = None
    
    def _call_ai_api(self, prompt: str, data_for_analysis: str) -> str:
        """调用AI API进行分析"""
        if not self.api_config:
            return "AI配置未加载，请先在AI大模型配置页面配置API"

        configs = normalize_ai_configs(self.api_config)
        if self.api_config.get("models") and configs:
            last_error = ""
            original_config = self.api_config
            failure_markers = (
                "API调用失败", "API调用异常", "API Key未设置", "认证失败",
                "请求超时", "网络错误", "网络连接失败", "连接被远程服务器关闭",
                "接口不存在", "服务器内部错误", "不支持的API类型",
            )
            try:
                for cfg in configs:
                    try:
                        self.api_config = cfg
                        result = self._call_ai_api(prompt, data_for_analysis)
                        if result and not any(marker in result for marker in failure_markers):
                            return result
                        last_error = f"{cfg.get('name', cfg.get('model', '备用模型'))}: {result}"
                    except Exception as exc:
                        last_error = f"{cfg.get('name', cfg.get('model', '备用模型'))}: {exc}"
                return f"所有大模型调用失败: {last_error}"
            finally:
                self.api_config = original_config
        
        # 获取API类型，可能存储为整数或字符串
        api_type_raw = self.api_config.get('api_type', 'openai')
        api_key = self.api_config.get('api_key', '')
        base_url = self.api_config.get('base_url', '')
        model = self.api_config.get('model', '')
        
        # API类型映射（整数索引到字符串类型）
        api_type_map = {
            0: "openai",      # OpenAI
            1: "anthropic",   # Claude
            2: "azure",       # Azure OpenAI
            3: "ollama",      # 本地模型
            5: "openai",      # MiniMax (OpenAI兼容)
            6: "openai",      # 智谱AI (OpenAI兼容)
            7: "openai",      # 百川智能 (OpenAI兼容)
            8: "openai",      # DeepSeek (OpenAI兼容)
            9: "openai",      # 通义千问 (OpenAI兼容)
            10: "openai",     # 硅基流动 (OpenAI兼容)
            12: "openai",     # OpenAI 官方
            13: "openai",     # DeepSeek 官方
            14: "openai"      # 阿里云百炼兼容
        }
        
        # 转换API类型（如果存储为整数）
        if isinstance(api_type_raw, int):
            api_type = api_type_map.get(api_type_raw, "openai")
        else:
            api_type = api_type_raw
        
        if not api_key:
            return "API Key未设置，请在AI大模型配置页面配置"
        
        import urllib.request
        import urllib.error
        
        try:
            if api_type == 'openai' or api_type == 'azure':
                # OpenAI兼容格式
                url = build_openai_chat_url(base_url)
                
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"请分析以下数据：\n\n{data_for_analysis}"}
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
                
                with urllib.request.urlopen(req, timeout=self.api_config.get('api_timeout', 120)) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result['choices'][0]['message']['content']
            
            elif api_type == 'anthropic':
                # Anthropic格式
                url = f"{base_url}/messages"
                
                headers = {
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "anthropic-dangerous-direct-browser-access": "true"
                }
                
                data = {
                    "model": model,
                    "max_tokens": 4000,
                    "messages": [
                        {"role": "user", "content": f"{prompt}\n\n{data_for_analysis}"}
                    ]
                }
                
                req = urllib.request.Request(
                    url,
                    data=json.dumps(data).encode('utf-8'),
                    headers=headers,
                    method='POST'
                )
                
                with urllib.request.urlopen(req, timeout=self.api_config.get('api_timeout', 120)) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result['content'][0]['text']
            
            elif api_type == 'ollama':
                # Ollama格式
                url = f"{base_url}/chat"
                
                data = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": data_for_analysis}
                    ],
                    "stream": False
                }
                
                headers = {"Content-Type": "application/json"}
                
                req = urllib.request.Request(
                    url,
                    data=json.dumps(data).encode('utf-8'),
                    headers=headers,
                    method='POST'
                )
                
                with urllib.request.urlopen(req, timeout=self.api_config.get('api_timeout', 120)) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result['message']['content']
            
            else:
                return f"不支持的API类型: {api_type}"
        
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (401, 403):
                return f"认证失败(HTTP {code}) - API Key无效或没有权限"
            elif code == 404:
                return f"接口不存在(HTTP 404) - 请检查Base URL路径是否正确"
            elif code == 500:
                return f"服务器内部错误(HTTP 500) - API服务端异常，请稍后重试"
            else:
                error_body = e.read().decode('utf-8', errors='ignore')
                try:
                    error_json = json.loads(error_body)
                    error_msg = error_json.get('error', {}).get('message', error_body)
                except:
                    error_msg = error_body
                return f"HTTP错误({code}) - {error_msg[:200]}"
        except Exception as e:
            error_msg = str(e)
            if '10054' in error_msg or '远程主机强迫关闭' in error_msg:
                return f"连接被远程服务器关闭 - 请检查API配置是否正确或稍后重试"
            if 'timed out' in error_msg.lower() or 'timeout' in error_msg.lower():
                return f"请求超时 - API服务器响应太慢或网络延迟过高"
            if 'ConnectionRefusedError' in error_msg or '连接被拒绝' in error_msg:
                return f"网络连接失败 - 无法访问API服务器，请检查Base URL和网络状态"
            return f"API调用异常: {error_msg}"
