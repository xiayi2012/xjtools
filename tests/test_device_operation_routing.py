import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui.device_operations import DeviceOperationRouter


def test_router_requires_correct_target_and_consumes_once():
    router = DeviceOperationRouter()
    target = dict(id='B', host='192.0.2.2')
    route, token = router.request(target, 'inspection', None)
    assert route == 'connect'
    target['id'] = 'C'
    assert router.request(dict(id='C'), 'logs', None)[0] == 'blocked'
    assert router.complete('stale', True, 'B') is None
    assert router.matches(token)
    assert router.complete(token, True, 'A') is None
    assert not router.matches(token)
    route, token = router.request(dict(id='B'), 'inspection', None)
    assert router.complete(token, True, 'B') == (dict(id='B'), 'inspection')
    assert router.complete(token, True, 'B') is None


def test_busy_failure_and_switch_cancellation():
    router = DeviceOperationRouter()
    assert router.request(dict(id='B'), 'inspection', 'A', busy=True)[0] == 'blocked'
    assert router.request(dict(id='B'), 'inspection', 'A', confirm_switch=lambda: False)[0] == 'cancelled'
    assert not router.pending
    assert router.request(dict(id='B'), 'logs', 'B')[0] == 'ready'
    _, token = router.request(dict(id='B'), 'inspection', None)
    assert router.complete(token, False, None) is None
    assert not router.pending


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from ui.main_window import MainWindow
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    win = MainWindow()
    qtbot.addWidget(win)
    win._force_quit = True
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: None)
    yield win
    win.schedule_timer.stop()


def test_ready_action_runs_on_current_device_only(window, monkeypatch):
    window.is_connected = True
    window.ssh_client = SimpleNamespace(is_connected=True)
    window.product_page.set_connected_server('B')
    calls = []
    monkeypatch.setattr(window, 'switch_page', lambda idx: calls.append(idx))
    monkeypatch.setattr(window.device_inspection_page, 'start_inspection', lambda: calls.append('start'))
    window.on_device_operation(dict(id='B'), 'inspection')
    assert calls == [1, 'start']


def test_other_device_never_runs_before_connecting(window, monkeypatch):
    window.is_connected = True
    window.ssh_client = SimpleNamespace(is_connected=True, disconnect=lambda: None)
    window.product_page.set_connected_server('A')
    calls = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window, '_start_device_connection', lambda s, p, t: calls.append(('connect', s['id'])))
    monkeypatch.setattr(window.device_inspection_page, 'start_inspection', lambda: calls.append('start'))
    monkeypatch.setattr('ui.main_window.decrypt_password', lambda x: 'secret')
    window.on_device_operation(dict(id='B', host='192.0.2.2'), 'inspection')
    assert calls == [('connect', 'B')]


def test_busy_task_prevents_disconnect(window):
    calls = []
    window.ssh_client = SimpleNamespace(disconnect=lambda: calls.append('disconnect'))
    window.inspection_page.inspection_worker = SimpleNamespace(isRunning=lambda: True)
    assert not window.disconnect_and_go_home()
    assert calls == []


def test_connection_callback_starts_only_the_frozen_target(window, monkeypatch, qtbot):
    from PyQt6.QtCore import QObject, pyqtSignal
    class Worker(QObject):
        connection_result = pyqtSignal(bool, str, dict)
        otp_challenge = pyqtSignal(str)
        def __init__(self, *a, **k):
            super().__init__()
        def isRunning(self):
            return False
        def start(self):
            pass
    monkeypatch.setattr('ui.main_window.ConnectionWorker', Worker)
    monkeypatch.setattr('ui.main_window.decrypt_password', lambda x: 'test')
    target = dict(id='B', host='192.0.2.2', username='root', product='EDR', port=22)
    window.product_page.servers = [dict(target)]
    started = []
    monkeypatch.setattr(window.device_inspection_page, 'start_inspection', lambda: started.append(window.product_page.connected_server_id))
    window.on_device_operation(target, 'inspection')
    token = window.operation_router.pending[0]
    window.product_page.selected_device_id = 'A'
    client = SimpleNamespace(is_connected=True, disconnect=lambda: None)
    window.connection_worker.connection_result.emit(True, 'ok', dict(ssh_client=client, host=target['host'], port=22, username='root'))
    qtbot.waitUntil(lambda: bool(started))
    assert started == ['B']
    window._on_device_connection_result(token, True, '', dict(ssh_client=client))
    qtbot.wait(10)
    assert started == ['B']
    assert window.device_inspection_page.current_device['id'] == 'B'


def test_connection_failure_and_stale_results_never_start(window, monkeypatch):
    started, closed = [], []
    monkeypatch.setattr(window.device_inspection_page, 'start_inspection', lambda: started.append(True))
    _, token = window.operation_router.request(dict(id='B'), 'inspection', None)
    window._on_device_connection_result(token, False, 'denied', {})
    assert not window.operation_router.pending and not started
    window._on_device_connection_result(token, True, '', dict(ssh_client=SimpleNamespace(disconnect=lambda: closed.append(True))))
    assert closed == [True] and not started


def test_connection_revalidates_asset_before_accepting(window, monkeypatch):
    target = dict(id='B', host='192.0.2.2', username='root', port=22)
    window.product_page.servers = [{**target, 'host': '192.0.2.99'}]
    _, token = window.operation_router.request(target, 'inspection', None)
    closed = []
    client = SimpleNamespace(disconnect=lambda: closed.append(True))
    window._on_device_connection_result(token, True, '', dict(ssh_client=client))
    assert closed == [True]
    assert not window.is_connected and not window.operation_router.pending


def test_history_and_report_follow_task_device_not_selected_card(window):
    window.product_page.selected_device_id = 'A'
    window.on_device_inspection_completed(dict(device=dict(id='B', name='nodeB', host='192.0.2.2'),
        task_id='run1', message='完成', script_outputs={'builtin': {'item': '状态: 正常：测试'}}))
    assert 'B' in window.product_page.inspection_history
    assert 'A' not in window.product_page.inspection_history
    window.on_device_report_saved(dict(device_id='B', task_id='older', path='old.html'))
    assert 'report_path' not in window.product_page.inspection_history['B']
    window.on_device_report_saved(dict(device_id='B', task_id='run1', path='new.html'))
    assert window.product_page.inspection_history['B']['report_path'] == 'new.html'


def test_same_product_device_switch_clears_previous_outputs(window):
    page = window.device_inspection_page
    page.set_device(dict(id='A', product='EDR'))
    page.builtin_outputs['old'] = 'old output'
    page.output_text.setPlainText('old output')
    page.set_device(dict(id='B', product='EDR'))
    assert page.builtin_outputs == {} and page.output_text.toPlainText() == ''


def test_report_header_uses_frozen_device_and_escapes_markup(window):
    page = window.device_inspection_page
    page.task_device = dict(id='B', name='<nodeB>', host='192.0.2.2', port=22)
    html = page._generate_ai_report_html('', 'EDR', '2026-11-03', [])
    assert '192.0.2.2' in html and '&lt;nodeB&gt;' in html
    assert '<nodeB>' not in html


def test_inspect_to_history_and_raw_report_end_to_end(window, qtbot, monkeypatch, tmp_path):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QFileDialog
    from scripts.inspection_catalog import classify_output
    page = window.device_inspection_page
    target = dict(id='B', name='nodeB', host='192.0.2.2', port=22)
    client = SimpleNamespace(is_connected=True, execute=lambda *a, **k: (0, 'connection_test_ok', ''),
                             execute_stream=lambda *a, **k: iter(['主机: test\n', '采集状态: 完成\n']))
    window.ssh_client = client
    window.is_connected = True
    window.product_page.set_connected_server('B')
    page.set_ssh_client(client)
    page.set_device(target)
    page.inspect_list.blockSignals(True)
    for idx in range(page.inspect_list.count()):
        page.inspect_list.item(idx).setCheckState(Qt.CheckState.Checked if idx == 0 else Qt.CheckState.Unchecked)
    page.inspect_list.blockSignals(False)
    page.start_inspection()
    first_worker = page.inspection_worker
    page.start_inspection()
    assert page.inspection_worker is first_worker
    qtbot.waitUntil(lambda: 'B' in window.product_page.inspection_history)
    first_worker.wait()
    summary = window.product_page.inspection_history['B']
    assert summary['host'] == '192.0.2.2'
    assert sum(summary['counts'].values()) == 1
    assert summary['counts'][classify_output(next(iter(page.builtin_outputs.values())))] == 1
    output = tmp_path / 'report.txt'
    monkeypatch.setattr(page, '_choose_raw_export_format', lambda: 'txt')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(output), ''))
    monkeypatch.setattr('subprocess.run', lambda *a, **k: None)
    page.on_export_clicked()
    assert '192.0.2.2' in output.read_text(encoding='utf-8')
    assert window.product_page.inspection_history['B']['report_path'] == str(output.resolve())
