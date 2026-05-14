import sys
import os
import json
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QRadioButton, QButtonGroup, QLabel, QLineEdit, QPushButton,
    QFileDialog, QTextEdit, QMessageBox, QGroupBox, QFormLayout,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QTextCursor


LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
NSMAP = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
}


# ============================================================
# 【内置转换提示词 - 用户可在此处自行修改】
# 修改下方 CONVERSION_PROMPT 变量的内容即可自定义转换规则
# ============================================================
CONVERSION_PROMPT = """你是FAQ查询脚本生成器，只输出纯.sh文件文本内容，无任何额外解释、符号、代码块。
严格遵守以下强制规则：
1. 固定格式：
#!/bin/bash
# 问题：【业务问题】
# 原因：【故障原因】
# 关键词：【业务关键词】
【查询命令，每行一个】
# 结果判断：【根据脚本执行结果，说明如何确认是该故障原因】

2. 查询命令要求：
   - 只输出只读查询命令，如：df、free、top、ps、cat、ls、grep、find、stat、netstat、ss、ip、curl、wget --spider
   - 禁止任何修改、删除、重启、启动操作
   - 禁止危险命令：rm、kill、shutdown、reboot、init、systemctl restart/start/stop
   - docker命令只允许：docker ps、docker stats、docker logs、docker inspect、docker images

3. 命令格式要求：
   - top 必须用：top -b -n 1
   - docker stats 必须用：docker stats --no-stream
   - 交互式命令必须加非交互参数

4. 从FAQ中提取所有问题定位相关的查询命令，不要遗漏

5. 【重要】安全确认：输出前必须再次检查所有命令，确保：
   - 不会修改任何文件或配置
   - 不会删除任何数据
   - 不会重启或停止任何服务
   - 不会对客户生产环境造成任何影响
   - 所有命令都是只读查询，可以安全在生产环境执行

FAQ文档内容如下：
---
{content}
---

请直接输出完整的Shell脚本代码，必须包含查询命令。"""


# ============================================================
# AI API 默认请求参数模板 - 用户可在此处自行修改默认参数
# 注意：此处使用Python字典格式，不是JSON字符串
# ============================================================
DEFAULT_REQUEST_PARAMS = {
    "temperature": 0.7,
    "max_tokens": 4096,
}


# ============================================================
# 【AI接口默认配置 - 用户可在此处固定配置项】
# ============================================================
DEFAULT_API_URL = "http://10.50.108.51:8003/v1/chat/completions"
DEFAULT_API_KEY = "qwen3:32b"
DEFAULT_MODEL_NAME = "qwen3:32b"
DEFAULT_CUSTOM_PARAMS = ""


MAX_RETRIES = 3
RETRY_DELAY = 2
REQUEST_TIMEOUT = 120


class FileLogger:
    def __init__(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(LOG_DIR, f"convert_{ts}.log")

    def write(self, msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def get_path(self):
        return self.log_file


def read_docx(file_path):
    text_parts = []
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            if 'word/document.xml' not in z.namelist():
                raise RuntimeError("不是有效的docx文件(缺少word/document.xml)")

            tree = ET.parse(z.open('word/document.xml'))
            root = tree.getroot()

            for prefix, uri in NSMAP.items():
                ET.register_namespace(prefix, uri)

            for p in root.iter('{%s}p' % NSMAP['w']):
                texts = []
                for t in p.iter('{%s}t' % NSMAP['w']):
                    if t.text:
                        texts.append(t.text)
                para_text = ''.join(texts).strip()
                if para_text:
                    text_parts.append(para_text)

            table_count = 0
            for tbl in root.iter('{%s}tbl' % NSMAP['w']):
                table_count += 1
                for row in tbl.iter('{%s}tr' % NSMAP['w']):
                    cells = []
                    for cell in row.iter('{%s}tc' % NSMAP['w']):
                        cell_texts = []
                        for t in cell.iter('{%s}t' % NSMAP['w']):
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


def call_ai_api(api_url, api_key, model_name, custom_params_str, content, logger=None):
    prompt = CONVERSION_PROMPT.format(content=content)

    if logger:
        logger.write(f"[API] 准备发送请求到: {api_url}")
        logger.write(f"[API] 模型: {model_name}, 内容长度: {len(prompt)} 字符")

    if custom_params_str.strip():
        try:
            params = json.loads(custom_params_str.strip())
            if logger:
                logger.write(f"[API] 使用用户自定义请求参数")
        except json.JSONDecodeError as e:
            raise ValueError(f"自定义请求参数JSON格式错误: {e}")
        params["model"] = model_name
        if "messages" not in params:
            params["messages"] = [
                {"role": "system", "content": "你是一个专业的Shell脚本编写专家。"},
                {"role": "user", "content": prompt}
            ]
        else:
            for msg in params["messages"]:
                if msg.get("role") == "user":
                    msg["content"] = prompt
                    break
            else:
                params["messages"].append({"role": "user", "content": prompt})
    else:
        params = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "你是一个专业的Shell脚本编写专家。"},
                {"role": "user", "content": prompt}
            ],
        }
        params.update(DEFAULT_REQUEST_PARAMS)
        if logger:
            logger.write(f"[API] 使用默认请求参数, 额外参数: {list(DEFAULT_REQUEST_PARAMS.keys())}")

    headers = {
        "Content-Type": "application/json"
    }
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if logger:
                logger.write(f"[API] 第{attempt}次尝试请求... (超时:{REQUEST_TIMEOUT}s)")
            resp = requests.post(api_url.strip(), headers=headers, json=params, timeout=REQUEST_TIMEOUT)

            if logger:
                logger.write(f"[API] HTTP状态码: {resp.status_code}, Content-Type: {resp.headers.get('Content-Type', '(无)')}")

            resp.raise_for_status()
            try:
                result = resp.json()
            except Exception as json_err:
                raw_text = resp.text[:800] if hasattr(resp, 'text') else '(无法获取响应内容)'
                detail = f"API返回非JSON格式响应:\n原始响应(前800字符):\n{raw_text}\n\n解析错误: {json_err}"
                if logger:
                    logger.write(f"[API] 错误: {detail}")
                raise ValueError(detail)
            script = _extract_script_from_response(result)
            if not script.strip():
                err_msg = "AI返回内容为空"
                if logger:
                    logger.write(f"[API] 错误: {err_msg}")
                    logger.write(f"[API] 原始响应结构: {list(result.keys()) if isinstance(result, dict) else type(result)}")
                raise ValueError(err_msg)
            if logger:
                logger.write(f"[API] 成功! 脚本长度: {len(script)} 字符")
            return script
        except requests.exceptions.Timeout:
            last_error = f"API请求超时(第{attempt}次)"
            if logger:
                logger.write(f"[API] 超时: {last_error}")
        except requests.exceptions.ConnectionError as e:
            last_error = f"网络连接失败: {e}(第{attempt}次)"
            if logger:
                logger.write(f"[API] 连接失败: {last_error}")
        except requests.exceptions.HTTPError as e:
            body = e.response.text[:500] if hasattr(e.response, 'text') else '(无响应体)'
            last_error = f"HTTP {e.response.status_code}(第{attempt}次): {body}"
            if logger:
                logger.write(f"[API] HTTP错误: {last_error}")
        except ValueError:
            raise
        except Exception as e:
            last_error = f"异常: {type(e).__name__}: {e}(第{attempt}次)"
            if logger:
                logger.write(f"[API] 异常: {last_error}")

        if attempt < MAX_RETRIES:
            if logger:
                logger.write(f"[API] 等待 {RETRY_DELAY}s 后重试...")
            time.sleep(RETRY_DELAY)

    final_err = f"API调用失败(已重试{MAX_RETRIES}次): {last_error}"
    if logger:
        logger.write(f"[API] 最终失败: {final_err}")
    raise RuntimeError(final_err)


def _extract_script_from_response(result):
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


def clean_shell_script(script_text):
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


def convert_single_file(docx_path, output_dir, api_url, api_key, model_name, custom_params, log_callback, logger):
    filename = os.path.basename(docx_path)
    try:
        log_callback(f"正在读取: {filename}")
        logger.write(f"[FILE] 开始读取: {filename} ({os.path.getsize(docx_path)} bytes)")
        content = read_docx(docx_path)
        logger.write(f"[FILE] 读取成功, 文本长度: {len(content)} 字符")
        logger.write(f"[FILE] 文本预览: {repr(content[:200])}")

        log_callback(f"正在调用AI转换: {filename}")
        script_raw = call_ai_api(api_url, api_key, model_name, custom_params, content, logger)
        script = clean_shell_script(script_raw)
        logger.write(f"[FILE] 清洗后脚本长度: {len(script)} 字符")

        sh_filename = os.path.splitext(filename)[0] + ".sh"
        sh_path = os.path.join(output_dir, sh_filename)
        with open(sh_path, "w", encoding="utf-8") as f:
            f.write(script)
        logger.write(f"[FILE] 已保存: {sh_path} ({os.path.getsize(sh_path)} bytes)")
        log_callback(f"转换成功: {filename} -> {sh_filename}")
        return True
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        logger.write(f"[ERROR] 转换失败: {filename} - {err_msg}")
        log_callback(f"转换失败: {filename} - {e}")
        return False


class ConvertWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int, int)

    def __init__(self, files, output_dir, api_url, api_key, model_name, custom_params):
        super().__init__()
        self.files = files
        self.output_dir = output_dir
        self.api_url = api_url
        self.api_key = api_key
        self.model_name = model_name
        self.custom_params = custom_params
        self.logger = FileLogger()

    def run(self):
        self._log(f"详细日志文件: {self.logger.get_path()}")
        self._log("=" * 50)
        success_count = 0
        fail_count = 0
        total = len(self.files)
        self.logger.write(f"[START] 开始批量转换, 共 {total} 个文件")
        self.logger.write(f"[CONFIG] API: {self.api_url}")
        self.logger.write(f"[CONFIG] 模型: {self.model_name}")
        self.logger.write(f"[CONFIG] 输出目录: {self.output_dir}")

        for i, f in enumerate(self.files, 1):
            self.logger.write(f"\n{'='*40}")
            self.logger.write(f"[PROGRESS] 处理第 {i}/{total} 个文件: {f}")
            ok = convert_single_file(
                f, self.output_dir, self.api_url, self.api_key,
                self.model_name, self.custom_params, self._log, self.logger
            )
            if ok:
                success_count += 1
            else:
                fail_count += 1

        self.logger.write(f"\n{'='*40}")
        self.logger.write(f"[END] 全部完成! 成功:{success_count} 失败:{fail_count} 总计:{total}")
        self.finished_signal.emit(success_count, fail_count)

    def _log(self, msg):
        self.log_signal.emit(msg)


class FAQ2SHConverter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("FAQ转Shell脚本工具")
        self.setMinimumWidth(720)
        self.setMinimumHeight(700)
        self.resize(780, 780)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(18, 14, 18, 14)

        mode_group = QGroupBox("转换模式")
        mode_layout = QHBoxLayout(mode_group)
        self.mode_btn_group = QButtonGroup(self)
        self.radio_batch = QRadioButton("批量转换（文件夹）")
        self.radio_single = QRadioButton("单个文件转换")
        self.radio_batch.setChecked(True)
        self.mode_btn_group.addButton(self.radio_batch, 0)
        self.mode_btn_group.addButton(self.radio_single, 1)
        mode_layout.addWidget(self.radio_batch)
        mode_layout.addWidget(self.radio_single)
        mode_layout.addStretch()
        main_layout.addWidget(mode_group)

        file_group = QGroupBox("文件选择")
        file_layout = QHBoxLayout(file_group)
        self.file_label = QLabel("输入文件夹：")
        self.file_input = QLineEdit()
        self.file_input.setPlaceholderText("请选择输入文件夹路径")
        self.file_btn = QPushButton("浏览...")
        self.file_btn.setFixedWidth(80)
        self.file_btn.clicked.connect(self._on_browse_input)
        file_layout.addWidget(self.file_label)
        file_layout.addWidget(self.file_input, 1)
        file_layout.addWidget(self.file_btn)
        main_layout.addWidget(file_group)

        output_group = QGroupBox("输出配置")
        output_layout = QHBoxLayout(output_group)
        output_layout.addWidget(QLabel("输出文件夹："))
        self.output_input = QLineEdit()
        self.output_input.setPlaceholderText("请选择输出文件夹路径")
        self.output_btn = QPushButton("浏览...")
        self.output_btn.setFixedWidth(80)
        self.output_btn.clicked.connect(self._on_browse_output)
        output_layout.addWidget(self.output_input, 1)
        output_layout.addWidget(self.output_btn)
        main_layout.addWidget(output_group)

        ai_group = QGroupBox("AI接口配置")
        ai_form = QFormLayout(ai_group)
        ai_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.api_url_input = QLineEdit()
        self.api_url_input.setPlaceholderText("例如: https://api.openai.com/v1/chat/completions")
        self.api_url_input.setText(DEFAULT_API_URL)
        ai_form.addRow("API地址：", self.api_url_input)

        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("sk-xxxxxxxxxxxxxxxx")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setText(DEFAULT_API_KEY)
        ai_form.addRow("API密钥：", self.api_key_input)

        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("例如: gpt-4o / deepseek-chat")
        self.model_input.setText(DEFAULT_MODEL_NAME)
        ai_form.addRow("模型名称：", self.model_input)

        self.custom_params_input = QTextEdit()
        self.custom_params_input.setPlaceholderText(
            "自定义请求参数JSON（可选，留空使用默认模板）\n"
            "需包含 messages 字段，其中 user 角色的 content 将被替换为转换提示词"
        )
        self.custom_params_input.setFixedHeight(90)
        if DEFAULT_CUSTOM_PARAMS:
            self.custom_params_input.setText(DEFAULT_CUSTOM_PARAMS)
        ai_form.addRow("请求参数：", self.custom_params_input)

        main_layout.addWidget(ai_group)

        self.convert_btn = QPushButton("开始转换")
        self.convert_btn.setFixedHeight(42)
        self.convert_btn.setStyleSheet("""
            QPushButton {
                background-color: #4A90D9;
                color: white;
                font-size: 15px;
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #3A7BC8;
            }
            QPushButton:pressed {
                background-color: #2E6AB0;
            }
            QPushButton:disabled {
                background-color: #B0C4DE;
                color: #E0E0E0;
            }
        """)
        self.convert_btn.clicked.connect(self._on_start_convert)
        main_layout.addWidget(self.convert_btn)

        log_group = QGroupBox("处理日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #FAFAFA;
                border: 1px solid #D0D0D0;
                border-radius: 4px;
            }
        """)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_group, 1)

        self.mode_btn_group.idToggled.connect(self._on_mode_changed)

    def _on_mode_changed(self, btn_id, checked):
        if not checked:
            return
        if btn_id == 0:
            self.file_label.setText("输入文件夹：")
            self.file_input.setPlaceholderText("请选择输入文件夹路径")
            self.file_input.clear()
        else:
            self.file_label.setText("单个文件：")
            self.file_input.setPlaceholderText("请选择单个docx文件")
            self.file_input.clear()

    def _on_browse_input(self):
        if self.radio_batch.isChecked():
            path = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择docx文件", "", "Word文档 (*.docx)"
            )
        if path:
            self.file_input.setText(path)

    def _on_browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if path:
            self.output_input.setText(path)

    def _log(self, msg):
        self.log_text.append(msg)
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)

    def _validate_inputs(self):
        input_path = self.file_input.text().strip()
        output_dir = self.output_input.text().strip()
        api_url = self.api_url_input.text().strip()
        model_name = self.model_input.text().strip()

        if not input_path:
            QMessageBox.warning(self, "参数缺失", "请选择输入路径")
            return None
        if not output_dir:
            QMessageBox.warning(self, "参数缺失", "请选择输出文件夹")
            return None
        if not api_url:
            QMessageBox.warning(self, "参数缺失", "请填写API地址")
            return None
        if not model_name:
            QMessageBox.warning(self, "参数缺失", "请填写模型名称")
            return None

        if self.radio_batch.isChecked():
            if not os.path.isdir(input_path):
                QMessageBox.warning(self, "路径错误", "输入路径不是有效的文件夹")
                return None
            docx_files = [
                os.path.join(input_path, f)
                for f in os.listdir(input_path)
                if f.lower().endswith(".docx") and not f.startswith("~$")
            ]
            if not docx_files:
                QMessageBox.warning(self, "无文件", "所选文件夹中未找到docx文件")
                return None
        else:
            if not os.path.isfile(input_path):
                QMessageBox.warning(self, "路径错误", "所选文件不存在")
                return None
            if not input_path.lower().endswith(".docx"):
                QMessageBox.warning(self, "格式错误", "请选择.docx格式的文件")
                return None
            docx_files = [input_path]

        if not os.path.isdir(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception as e:
                QMessageBox.warning(self, "路径错误", f"无法创建输出文件夹: {e}")
                return None

        custom_params = self.custom_params_input.toPlainText().strip()
        if custom_params:
            try:
                json.loads(custom_params)
            except json.JSONDecodeError as e:
                QMessageBox.warning(self, "参数错误", f"自定义请求参数JSON格式错误:\n{e}")
                return None

        return docx_files, output_dir, api_url, self.api_key_input.text().strip(), model_name, custom_params

    def _on_start_convert(self):
        result = self._validate_inputs()
        if result is None:
            return

        docx_files, output_dir, api_url, api_key, model_name, custom_params = result

        self.log_text.clear()
        self._log(f"共发现 {len(docx_files)} 个docx文件，开始转换...")
        self.convert_btn.setEnabled(False)
        self.convert_btn.setText("转换中...")

        self.worker = ConvertWorker(
            docx_files, output_dir, api_url, api_key, model_name, custom_params
        )
        self.worker.log_signal.connect(self._log)
        self.worker.finished_signal.connect(self._on_convert_finished)
        self.worker.start()

    def _on_convert_finished(self, success_count, fail_count):
        self.convert_btn.setEnabled(True)
        self.convert_btn.setText("开始转换")
        total = success_count + fail_count
        self._log(f"\n转换完成！成功: {success_count}, 失败: {fail_count}, 总计: {total}")

        if self.worker and hasattr(self.worker, 'logger'):
            self._log(f"\n详细日志已保存至: {self.worker.logger.get_path()}")

        if fail_count == 0:
            QMessageBox.information(
                self, "转换完成",
                f"全部转换成功！\n共处理 {total} 个文件。\n\n详细日志: {self.worker.logger.get_path() if self.worker else ''}"
            )
        else:
            QMessageBox.warning(
                self, "转换完成",
                f"转换完成，部分失败。\n成功: {success_count}, 失败: {fail_count}\n\n详细日志: {self.worker.logger.get_path() if self.worker else ''}"
            )


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    palette = app.palette()
    palette.setColor(palette.ColorRole.Window, QColor(245, 245, 245))
    palette.setColor(palette.ColorRole.WindowText, QColor(51, 51, 51))
    palette.setColor(palette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(palette.ColorRole.AlternateBase, QColor(240, 240, 240))
    palette.setColor(palette.ColorRole.Button, QColor(235, 235, 235))
    palette.setColor(palette.ColorRole.ButtonText, QColor(51, 51, 51))
    palette.setColor(palette.ColorRole.Text, QColor(51, 51, 51))
    palette.setColor(palette.ColorRole.Highlight, QColor(74, 144, 217))
    palette.setColor(palette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    window = FAQ2SHConverter()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
