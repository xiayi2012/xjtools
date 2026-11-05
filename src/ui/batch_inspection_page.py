"""Batch inspection with a separate, short-lived SSH connection per device."""

from copy import deepcopy
from datetime import datetime
from threading import Event
from uuid import uuid4
from pathlib import Path
import re

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, QFileDialog,
    QWidget, QStackedWidget, QProgressBar,
)

from core.ssh_connector import SSHClientWrapper
from scripts.inspection_catalog import format_check_output
from .inspection_page import InspectionPage


def saved_password(server):
    from .product_page import decrypt_password
    return decrypt_password(server.get('encrypted_password', ''))


class BatchInspectionWorker(QThread):
    device_started = pyqtSignal(dict)
    device_finished = pyqtSignal(dict)
    device_progress = pyqtSignal(str, int, int)
    progress = pyqtSignal(int, int)
    batch_finished = pyqtSignal(bool, str)

    def __init__(self, servers, checks, client_factory=None, password_reader=None):
        super().__init__()
        self.servers, self.checks = deepcopy(servers), deepcopy(checks)
        self.client_factory = client_factory or SSHClientWrapper
        self.password_reader = password_reader or saved_password
        self.cancel = Event()
        self.client = None

    def stop(self):
        self.cancel.set()
        if self.client and hasattr(self.client, 'request_stop'):
            self.client.request_stop()

    def run(self):
        for index, server in enumerate(self.servers):
            device = {k: server[k] for k in ('id', 'name', 'host', 'port', 'product') if k in server}
            result = dict(device=device, task_id=str(uuid4()), time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                          checks=[c.get('Name', c.get('name', '')) for c in self.checks],
                          script_outputs={'builtin': {}, 'custom': {}}, success=False, status='failed', message='', started=False)
            try:
                if self.cancel.is_set():
                    result.update(status='cancelled', message='已取消，未执行')
                else:
                    result['started'] = True
                    self.device_started.emit(device)
                    self.device_progress.emit(device['id'], 0, len(self.checks))
                    if server.get('product') == '办公智盾':
                        raise ValueError('此设备需要交互式 OTP，请从设备详情单独巡检')
                    password = self.password_reader(server)
                    if not password:
                        raise ValueError('无法读取保存的密码，请编辑设备配置')
                    self.client = self.client_factory()
                    ok, message = self.client.connect(host=server.get('host', ''), port=server.get('port', 22),
                                                      username=server.get('username', ''), password=password)
                    if not ok:
                        raise ValueError(f'SSH 连接失败：{message}')
                    for check_index, check in enumerate(self.checks):
                        if self.cancel.is_set():
                            break
                        name = check.get('Name', check.get('name', ''))
                        parts = []
                        try:
                            for line in self.client.execute_stream(check.get('command', ''), timeout=check.get('timeout', 20)):
                                if self.cancel.is_set():
                                    break
                                if line:
                                    parts.append(line)
                        except Exception as exc:
                            parts.append(f'\n执行错误: {exc}\n')
                        if self.cancel.is_set():
                            parts.append('\n执行错误: 用户停止采集，数据不完整\n')
                        output = format_check_output(check, ''.join(parts))
                        section = 'custom' if check.get('is_custom_script') else 'builtin'
                        result['script_outputs'][section][name] = output
                        if not self.cancel.is_set():
                            self.device_progress.emit(device['id'], check_index + 1, len(self.checks))
                    if self.cancel.is_set():
                        result.update(status='cancelled', message='已停止，保留已采集结果')
                    else:
                        result.update(status='completed', success=True, message='巡检执行完成，请核对各项结论')
            except Exception as exc:
                result['message'] = str(exc)
                if self.cancel.is_set():
                    result.update(status='cancelled', message='已停止')
            finally:
                if self.client:
                    try:
                        self.client.disconnect()
                    except Exception:
                        pass
                    self.client = None
            self.device_finished.emit(result)
            self.progress.emit(index + 1, len(self.servers))
        self.batch_finished.emit(not self.cancel.is_set(), '批量巡检已停止' if self.cancel.is_set() else '批量巡检执行结束')


class BatchInspectionPage(InspectionPage):
    targets_queued = pyqtSignal(list)
    device_released = pyqtSignal(str)
    def __init__(self, servers_provider):
        super().__init__()
        self.servers_provider = servers_provider
        self.results = {}
        self.running = False
        self.ready_to_start = False
        self.run_checks = []
        self.selected_ids = []
        self.run_devices = []
        self.device_percentages = {}
        self.findChild(QLabel, 'pageTitle').setText('批量巡检')
        self.findChild(QLabel, 'pageTitle').setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.findChild(QLabel, 'pageTitle').setStyleSheet('font-size:23px; font-weight:600; color:#203b49;')
        self.product_label.hide()
        self.start_btn.setText('开始批量巡检')
        self.stop_btn.setText('停止批量巡检')
        self._build_steps()
        self.refresh_devices()

    def _build_steps(self):
        root = self.layout()
        title = self.findChild(QLabel, 'pageTitle')
        selector = self.inspect_list.parentWidget()
        splitter = selector.parentWidget()
        while root.count():
            item = root.takeAt(0)
            if item.widget():
                item.widget().hide()
        splitter.hide()
        self.output_tabs.hide()
        for button in (self.start_btn, self.stop_btn, self.export_btn, self.export_ai_btn):
            button.hide()
        title.show()
        root.addWidget(title)
        self.step_labels = []
        step_header = QHBoxLayout()
        step_header.setSpacing(12)
        for text in ('1  选择设备', '2  选择巡检项', '3  巡检与报告'):
            label = QLabel(text)
            label.setMinimumHeight(40)
            step_header.addWidget(label, 1)
            self.step_labels.append(label)
        root.addLayout(step_header)
        self.steps = QStackedWidget()
        root.addWidget(self.steps, 1)
        panel = QFrame()
        panel.setObjectName('batchDevicesPanel')
        panel.setStyleSheet('''
            QFrame#batchDevicesPanel {background:white; border:1px solid #e0e8ed; border-radius:7px;}
            QLabel {color:#506c7a; font-size:11px;}
            QTableWidget {background:white; color:#304f5e; border:1px solid #e4ebef; gridline-color:#edf2f5; font-size:12px;}
            QTableWidget::item:selected {background:#e5f4ef; color:#157d70;}
            QHeaderView::section {background:#f3f7f9; color:#57717e; border:0; border-bottom:1px solid #dfe8ec; padding:6px; font-size:11px;}
        ''')
        layout = QVBoxLayout(panel)
        toolbar = QHBoxLayout()
        self.selection_summary = QLabel('勾选需要巡检的设备')
        toolbar.addWidget(self.selection_summary, 1)
        self.device_buttons = []
        for label, callback in [('全选设备', lambda: self.select_devices(True)),
                                ('取消选择', lambda: self.select_devices(False)), ('刷新设备', self.refresh_devices)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
            self.device_buttons.append(button)
        self.export_all_btn = QPushButton('导出全部报告')
        self.export_all_btn.setProperty('buttonRole', 'primary')
        self.export_all_btn.setEnabled(False)
        self.export_all_btn.clicked.connect(self.export_all_reports)
        layout.addLayout(toolbar)
        self.device_table = QTableWidget(0, 5)
        self.device_table.setHorizontalHeaderLabels(['选择', '设备名称', '地址', '产品', '执行状态'])
        self.device_table.setColumnHidden(4, True)
        self.device_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.device_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.device_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.device_table.verticalHeader().hide()
        self.device_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.device_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.device_table.setMinimumHeight(110)
        self.device_table.currentCellChanged.connect(self._row_selected)
        self.device_table.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.device_table, 1)
        self.steps.addWidget(panel)
        checks_page = QWidget()
        checks_layout = QVBoxLayout(checks_page)
        checks_layout.setContentsMargins(0, 0, 0, 0)
        self.checks_summary = QLabel()
        self.checks_summary.setStyleSheet('color:#506c7a; font-size:13px;')
        checks_layout.addWidget(self.checks_summary)
        checks_layout.addWidget(selector, 1)
        selector.show()
        self.steps.addWidget(checks_page)
        self.inspect_list.itemChanged.connect(self._sync_navigation)
        for button in (self.select_all_btn, self.deselect_all_btn):
            button.clicked.connect(self._sync_navigation)
        execution_page = QFrame()
        execution_page.setStyleSheet(panel.styleSheet())
        execution_layout = QVBoxLayout(execution_page)
        self.batch_status = QLabel('等待开始巡检')
        execution_layout.addWidget(self.batch_status)
        self.run_table = QTableWidget(0, 6)
        self.run_table.setHorizontalHeaderLabels(['设备名称', '地址', '产品', '执行状态', '巡检进度', '报告'])
        self.run_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.run_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.run_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.run_table.verticalHeader().hide()
        header = self.run_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.run_table.currentCellChanged.connect(self._run_row_selected)
        execution_layout.addWidget(self.run_table, 1)
        self.steps.addWidget(execution_page)
        footer = QHBoxLayout()
        self.back_btn = QPushButton('上一步')
        self.back_btn.clicked.connect(self._previous_step)
        footer.addWidget(self.back_btn)
        footer.addStretch()
        footer.addWidget(self.export_all_btn)
        self.stop_btn.setMinimumHeight(38)
        footer.addWidget(self.stop_btn)
        self.start_btn.setText('开始巡检')
        self.start_btn.setMinimumHeight(38)
        footer.addWidget(self.start_btn)
        self.next_btn = QPushButton('下一步')
        self.next_btn.setObjectName('nextStepButton')
        self.next_btn.clicked.connect(self._next_step)
        footer.addWidget(self.next_btn)
        for button in (self.back_btn, self.export_all_btn, self.next_btn):
            button.setMinimumHeight(38)
            button.setMinimumWidth(110)
        root.addLayout(footer)
        self._set_step(0)

    def _selected_device_ids(self):
        return [self.device_table.item(i, 0).data(Qt.ItemDataRole.UserRole)
                for i in range(self.device_table.rowCount())
                if self.device_table.item(i, 0).checkState() == Qt.CheckState.Checked]

    def _select_all(self):
        super()._select_all()
        self._sync_navigation()

    def _deselect_all(self):
        super()._deselect_all()
        self._sync_navigation()

    def _selection_changed(self, *args):
        self.selection_summary.setText(f'共 {self.device_table.rowCount()} 台设备，已选择 {len(self._selected_device_ids())} 台')
        self._sync_navigation()

    def _export_busy(self):
        worker = getattr(self, 'export_worker', None)
        return bool(worker and worker.isRunning())

    def _set_step(self, index):
        self.steps.setCurrentIndex(index)
        for i, label in enumerate(self.step_labels):
            label.setStyleSheet('padding:0 14px; border-radius:6px; font-size:13px; ' +
                ('background:#e2f1ec; color:#147868; font-weight:600;' if i == index
                 else 'background:#eef2f5; color:#647b89;'))
        self._sync_navigation()

    def _sync_navigation(self, *args):
        if not hasattr(self, 'steps'):
            return
        step, busy = self.steps.currentIndex(), self._export_busy()
        self.back_btn.setVisible(step > 0)
        self.back_btn.setEnabled(not self.running and not busy)
        self.back_btn.setText('重新选择设备' if step == 2 and not self.ready_to_start else '上一步')
        self.next_btn.setVisible(step < 2)
        self.next_btn.setText('下一步')
        checks = len(self._get_checked_inspection_items())
        self.next_btn.setEnabled(not self.running and not busy and
                                 bool(self._selected_device_ids() if step == 0 else checks))
        self.checks_summary.setText(f'已选择 {len(self.selected_ids)} 台设备 · 已勾选 {checks} 个巡检项')
        self.stop_btn.setVisible(step == 2 and self.running)
        self.start_btn.setVisible(step == 2 and self.ready_to_start)
        self.start_btn.setEnabled(self.ready_to_start and not self.running and not busy)
        self.export_all_btn.setVisible(step == 2)
        self.export_all_btn.setEnabled(not self.running and not busy and bool(self._report_results()))
        for row in range(self.run_table.rowCount()):
            device_id = self.run_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            result = self.results.get(device_id, {})
            for button in self.run_table.cellWidget(row, 5).findChildren(QPushButton):
                enabled = bool(result and result.get('started', True))
                if button.property('analysis'):
                    enabled = enabled and result.get('status') == 'completed'
                button.setEnabled(not self.running and not busy and enabled)

    def _next_step(self):
        if self.running or self._export_busy():
            return
        if self.steps.currentIndex() == 0:
            self.selected_ids = self._selected_device_ids()
            if self.selected_ids:
                self._set_step(1)
        elif self.steps.currentIndex() == 1:
            inputs = self._inspection_inputs()
            if inputs is None:
                return
            servers, checks = inputs
            self.results = {}
            self.run_checks = deepcopy(checks)
            self.run_devices = deepcopy(servers)
            self.task_id, self.task_device = None, {}
            self._clear_inspection_state()
            self._populate_run_table(servers)
            self.ready_to_start = True
            self.batch_status.setText(f'已选择 {len(servers)} 台设备、{len(checks)} 个巡检项，点击“开始巡检”执行')
            self._set_step(2)

    def _previous_step(self):
        if not self.running and not self._export_busy():
            if self.steps.currentIndex() == 2 and self.ready_to_start:
                self.ready_to_start = False
                self._set_step(1)
                return
            self._set_step(0)
            self.refresh_devices()

    def _populate_run_table(self, servers):
        self.run_table.setRowCount(0)
        self.run_table.setRowCount(len(servers))
        for row, server in enumerate(servers):
            device_id = server['id']
            for column, text in enumerate([server.get('name') or server.get('host', ''),
                    f'{server.get("host", "")}:{server.get("port", 22)}', server.get('product', ''), '等待执行']):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, device_id)
                self.run_table.setItem(row, column, item)
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(0)
            progress.setFormat('%p%')
            progress.setStyleSheet('''QProgressBar {background:#edf3f5; color:#263f4c; border:0;
                border-radius:4px; text-align:center; margin:8px 5px; min-height:18px;}
                QProgressBar::chunk {background:#a7d7c8; border-radius:4px;}''')
            self.run_table.setCellWidget(row, 4, progress)
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(5, 4, 5, 4)
            for text, analysis in [('导出', False), ('分析导出', True)]:
                button = QPushButton(text)
                button.setObjectName('deviceAnalysisExport' if analysis else 'deviceReportExport')
                button.setProperty('analysis', analysis)
                button.setEnabled(False)
                button.clicked.connect(lambda checked=False, target=device_id, ai=analysis: self._export_device(target, ai))
                actions_layout.addWidget(button)
            self.run_table.setCellWidget(row, 5, actions)
            self.run_table.setRowHeight(row, 48)

    def _run_row_for(self, device_id):
        return next((i for i in range(self.run_table.rowCount())
                     if self.run_table.item(i, 0).data(Qt.ItemDataRole.UserRole) == device_id), None)

    def _run_row_selected(self, row, *args):
        if row >= 0 and self.run_table.item(row, 0) and not self._export_busy():
            self._display_result(self.run_table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def _export_device(self, device_id, analysis=False):
        if self.running or self._export_busy():
            return
        result = self.results.get(device_id)
        if not result or not result.get('started', True):
            return
        self._display_result(device_id)
        if analysis:
            if result['status'] != 'completed':
                return
            self.on_export_ai_report()
            if self._export_busy():
                self.export_worker.finished.connect(self._sync_navigation)
                self._sync_navigation()
        else:
            self.on_export_clicked()

    def _report_results(self):
        return [result for result in self.results.values() if result.get('started', True)]

    def refresh_devices(self):
        if self.running or (getattr(self, 'export_worker', None) and self.export_worker.isRunning()):
            return
        current = self.device_table.currentRow()
        selected_id = self.device_table.item(current, 0).data(Qt.ItemDataRole.UserRole) if current >= 0 else None
        checked = {self.device_table.item(i, 0).data(Qt.ItemDataRole.UserRole) for i in range(self.device_table.rowCount())
                   if self.device_table.item(i, 0).checkState() == Qt.CheckState.Checked}
        self.device_table.blockSignals(True)
        servers = list(self.servers_provider())
        self.device_table.setRowCount(len(servers))
        for row, server in enumerate(servers):
            item = QTableWidgetItem('')
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(Qt.CheckState.Checked if server['id'] in checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, server['id'])
            self.device_table.setItem(row, 0, item)
            for column, text in enumerate([server.get('name') or server.get('host', ''),
                    f'{server.get("host", "")}:{server.get("port", 22)}', server.get('product', ''),
                    self.results.get(server['id'], {}).get('message', '待选择')], 1):
                self.device_table.setItem(row, column, QTableWidgetItem(text))
        self.device_table.blockSignals(False)
        selected_row = self._row_for(selected_id)
        self.device_table.setCurrentCell(-1 if selected_row is None else selected_row, 1)
        if selected_row is None:
            self.device_table.clearSelection()
        self._display_result(selected_id if selected_row is not None else None)
        self._selection_changed()

    def select_devices(self, checked):
        if not self.running:
            self.device_table.blockSignals(True)
            try:
                for row in range(self.device_table.rowCount()):
                    self.device_table.item(row, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            finally:
                self.device_table.blockSignals(False)
            self._selection_changed()

    def _inspection_inputs(self):
        ids = set(self.selected_ids if self.steps.currentIndex() in (1, 2) else self._selected_device_ids())
        servers = deepcopy([s for s in self.servers_provider() if s.get('id') in ids])
        if len(servers) != len(ids):
            self.ready_to_start = False
            self._set_step(0)
            self.refresh_devices()
            QMessageBox.warning(self, '设备列表已变化', '部分已选设备已移除，请重新选择设备')
            return
        checks = deepcopy(self.run_checks) if self.ready_to_start else self._get_checked_inspection_items()
        if not servers or not checks:
            QMessageBox.warning(self, '请选择巡检目标', '请至少勾选一台设备和一个巡检项')
            return
        return servers, checks

    def start_inspection(self):
        if self.running or self._export_busy() or (getattr(self, 'start_guard', None) and not self.start_guard()):
            QMessageBox.warning(self, '任务进行中', '请先等待当前任务完成或停止后再启动批量巡检')
            return
        inputs = self._inspection_inputs()
        if inputs is None:
            return
        servers, checks = inputs
        ids = {server['id'] for server in servers}
        self.results = {}
        self.run_checks = deepcopy(checks)
        self.task_id = None
        self.task_device = {}
        self._clear_inspection_state()
        self.running = True
        self.ready_to_start = False
        self.run_devices = deepcopy(servers)
        self.device_percentages = {server['id']: 0 for server in servers}
        self._populate_run_table(servers)
        self._set_step(2)
        self.batch_status.setText(f'共 {len(servers)} 台设备，每台执行 {len(checks)} 个巡检项')
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.export_ai_btn.setEnabled(False)
        self.export_all_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, len(servers))
        self.progress_bar.setValue(0)
        for button in self.device_buttons:
            button.setEnabled(False)
        for row in range(self.device_table.rowCount()):
            item = self.device_table.item(row, 0)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.device_table.item(row, 4).setText('等待执行' if item.data(Qt.ItemDataRole.UserRole) in ids else '未参与')
        self.inspection_worker = BatchInspectionWorker(servers, checks)
        self.inspection_worker.device_started.connect(self._device_started)
        self.inspection_worker.device_finished.connect(self._device_finished)
        self.inspection_worker.device_progress.connect(self._device_progress)
        self.inspection_worker.progress.connect(self._progress)
        self.inspection_worker.batch_finished.connect(self._batch_finished)
        self.targets_queued.emit([s['id'] for s in servers])
        self.inspection_worker.start()

    def stop_inspection(self):
        if self.inspection_worker and self.inspection_worker.isRunning():
            self.inspection_worker.stop()
            self.stop_btn.setEnabled(False)
            self.batch_status.setText('正在停止，等待当前连接或命令返回…')

    def _row_for(self, device_id):
        return next((i for i in range(self.device_table.rowCount())
                     if self.device_table.item(i, 0).data(Qt.ItemDataRole.UserRole) == device_id), None)

    def _device_started(self, device):
        row = self._row_for(device['id'])
        if row is not None:
            self.device_table.item(row, 4).setText('巡检中')
        run_row = self._run_row_for(device['id'])
        if run_row is not None:
            self.run_table.item(run_row, 3).setText('连接中')
        self.inspection_started.emit(device)

    def _device_progress(self, device_id, completed, total):
        row = self._run_row_for(device_id)
        if row is None:
            return
        percent = min(100, max(0, completed * 100 // total)) if total else 0
        self.device_percentages[device_id] = percent
        bar = self.run_table.cellWidget(row, 4)
        bar.setValue(percent)
        bar.setToolTip(f'已完成 {completed} / {total} 个巡检项')
        if completed:
            self.run_table.item(row, 3).setText('巡检中')

    def _device_finished(self, result):
        device_id = result['device']['id']
        self.results[device_id] = result
        row = self._row_for(device_id)
        if row is not None:
            status = {'completed': '已完成', 'failed': '失败', 'cancelled': '已取消'}[result['status']]
            self.device_table.item(row, 4).setText(status)
            self.device_table.item(row, 4).setToolTip(result['message'])
        run_row = self._run_row_for(device_id)
        if run_row is not None:
            status = {'completed': '已完成', 'failed': '失败', 'cancelled': '已停止' if result.get('started') else '未执行'}[result['status']]
            self.run_table.item(run_row, 3).setText(status)
            self.run_table.item(run_row, 3).setToolTip(result['message'])
            if result['status'] == 'completed':
                self.device_percentages[device_id] = 100
                self.run_table.cellWidget(run_row, 4).setValue(100)
        self.device_released.emit(device_id)
        if result.get('started', True):
            self.inspection_completed.emit(result)
        current = self.device_table.currentRow()
        if current < 0 and row is not None:
            self.device_table.setCurrentCell(row, 1)
        elif current >= 0 and self.device_table.item(current, 0).data(Qt.ItemDataRole.UserRole) == device_id:
            self._display_result(device_id)

    def _progress(self, completed, total):
        self.progress_bar.setValue(completed)
        self.batch_status.setText(f'已处理 {completed} / {total} 台设备')

    def _batch_finished(self, success, message):
        self.running = False
        counts = {status: sum(result['status'] == status for result in self.results.values())
                  for status in ('completed', 'failed', 'cancelled')}
        self.batch_status.setText(f'{message} · 完成 {counts["completed"]} 台 · 失败 {counts["failed"]} 台 · 停止/取消 {counts["cancelled"]} 台')
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        for button in self.device_buttons:
            button.setEnabled(True)
        for row in range(self.device_table.rowCount()):
            item = self.device_table.item(row, 0)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        self._row_selected(self.device_table.currentRow())
        self._sync_navigation()

    def _row_selected(self, row, *args):
        if row < 0:
            return
        if getattr(self, 'export_worker', None) and self.export_worker.isRunning():
            previous = self._row_for(getattr(self, 'task_device', {}).get('id'))
            if previous is not None:
                self.device_table.blockSignals(True)
                self.device_table.setCurrentCell(previous, 1)
                self.device_table.blockSignals(False)
            return
        self._display_result(self.device_table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def _display_result(self, device_id):
        result = self.results.get(device_id)
        if not result:
            self.task_id = None
            self.task_device = {}
            self.builtin_outputs, self.custom_script_outputs = {}, {}
            self.output_text.setPlainText('等待执行结果' if device_id else '')
            self.preview_text.setPlainText(self.output_text.toPlainText())
            self.export_btn.setEnabled(False)
            self.export_ai_btn.setEnabled(False)
            return
        self.task_device = deepcopy(result['device'])
        self.product_name = self.task_device.get('product') or '通用'
        self.task_id, self.task_time = result['task_id'], result['time']
        self.task_checks = deepcopy(self.run_checks)
        self.builtin_outputs = dict(result['script_outputs']['builtin'])
        self.custom_script_outputs = dict(result['script_outputs']['custom'])
        text = f'设备：{self.task_device.get("name") or device_id} · {self.task_device.get("host", "")}\n时间：{result["time"]}\n{result["message"]}\n\n'
        text += '\n\n'.join(f'{name}\n{output}' for outputs in result['script_outputs'].values() for name, output in outputs.items())
        self.output_text.setPlainText(text)
        self.preview_text.setPlainText(text)
        self.output_tabs.setCurrentIndex(1)
        self.export_btn.setEnabled(not self.running and result.get('started', True))
        self.export_ai_btn.setEnabled(not self.running and result['status'] == 'completed')

    def on_export_clicked(self):
        if self.running or not getattr(self, 'task_id', None):
            return
        format_name = self._choose_raw_export_format()
        if not format_name:
            return
        suffix = '.md' if format_name == 'md' else '.txt'
        name = re.sub(r'[<>:"/\\|?*]', '_', str(self.task_device.get('name') or self.task_device.get('host') or '设备'))
        path, _ = QFileDialog.getSaveFileName(self, '导出所选设备巡检报告', f'巡检报告_{name}{suffix}',
                                             'Markdown (*.md)' if suffix == '.md' else '文本报告 (*.txt)')
        if not path:
            return
        if not path.lower().endswith(suffix):
            path += suffix
        try:
            Path(path).write_text(f'巡检时间：{self.task_time}\n任务标识：{self.task_id}\n\n' + self.output_text.toPlainText(), encoding='utf-8')
            self._report_saved(path)
            self.batch_status.setText('报告已保存：' + path)
        except OSError as exc:
            QMessageBox.warning(self, '报告保存失败', str(exc))
            return
        self._open_report_folder(path)

    def _open_report_folder(self, report_path):
        try:
            self._open_folder(str(report_path))
        except OSError as exc:
            QMessageBox.warning(self, '无法打开文件夹', f'报告已保存，但无法打开所在文件夹。\n{exc}')

    def export_all_reports(self):
        results = self._report_results()
        if self.running or self._export_busy() or not results:
            return
        directory = QFileDialog.getExistingDirectory(self, '选择批量报告保存目录')
        if not directory:
            return
        try:
            for result in results:
                device = result['device']
                name = re.sub(r'[<>:"/\\|?*]', '_', str(device.get('name') or device.get('host') or device['id']))[:60]
                path = Path(directory) / f'巡检报告_{name}_{result["task_id"]}.txt'
                text = f'设备：{device.get("name") or device["id"]}\n地址：{device.get("host", "")}:{device.get("port", 22)}\n'
                text += f'巡检时间：{result["time"]}\n任务标识：{result["task_id"]}\n状态：{result["message"]}\n\n'
                text += '\n\n'.join(f'{name}\n{output}' for outputs in result['script_outputs'].values() for name, output in outputs.items())
                path.write_text(text, encoding='utf-8')
                self.report_saved.emit(dict(device_id=device['id'], task_id=result['task_id'], path=str(path.resolve())))
            self.batch_status.setText(f'已导出 {len(results)} 份设备报告：{directory}')
        except OSError as exc:
            QMessageBox.warning(self, '报告保存失败', f'部分报告可能已经保存，请核对目录。\n{exc}')
            return
        self._open_report_folder(path)
