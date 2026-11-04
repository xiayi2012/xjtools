import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog
from PyQt6.QtTest import QSignalSpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui.batch_inspection_page import BatchInspectionWorker
from test_device_operation_routing import window


def test_batch_uses_separate_connections_and_continues_after_failure(qtbot):
    clients = []
    class Client:
        def __init__(self):
            clients.append(self)
            self.closed = False
        def connect(self, **params):
            self.host = params['host']
            return self.host != 'bad', 'denied'
        def execute_stream(self, *a, **k):
            yield f'主机: {self.host}\n采集状态: 完成\n'
        def disconnect(self):
            self.closed = True
    worker = BatchInspectionWorker([dict(id=x, host=x) for x in ('a', 'bad', 'b')],
        [dict(id='host', Name='主机', command='hostname')], client_factory=Client, password_reader=lambda s: 'test')
    results = []
    worker.device_finished.connect(results.append)
    worker.start()
    qtbot.waitUntil(lambda: len(results) == 3)
    worker.wait()
    assert [r['device']['id'] for r in results] == ['a', 'bad', 'b']
    assert [r['status'] for r in results] == ['completed', 'failed', 'completed']
    assert len(clients) == 3 and all(c.closed for c in clients)
    assert 'a' in results[0]['script_outputs']['builtin']['主机']
    assert 'b' in results[2]['script_outputs']['builtin']['主机']


def test_cancelled_batch_marks_remaining_devices(qtbot):
    worker = BatchInspectionWorker([dict(id=x) for x in ('a', 'b')], [])
    results = []
    worker.device_finished.connect(results.append)
    worker.stop()
    worker.start()
    qtbot.waitUntil(lambda: len(results) == 2)
    worker.wait()
    assert all(r['status'] == 'cancelled' for r in results)


def test_batch_navigation_needs_no_connected_device(window, qtbot):
    window.show()
    window.switch_page(2)
    qtbot.waitUntil(lambda: window.stackedWidget.currentWidget() is window.inspection_page)
    assert window.navigationInterface.widget('inspection_page').text() == '批量巡检'


def test_device_action_opens_dialog_and_keeps_racks_visible(window, monkeypatch):
    window.is_connected = True
    window.ssh_client = SimpleNamespace(is_connected=True)
    window.product_page.set_connected_server('a')
    monkeypatch.setattr(window.device_inspection_page, 'start_inspection', lambda: None)
    window.on_device_operation(dict(id='a', host='192.0.2.1'), 'inspection')
    popup = window.product_page.inspection_dialog
    assert isinstance(popup, QDialog) and popup.isVisible()
    assert window.product_page.scroll_area.isVisibleTo(window.product_page)
    popup.close()
    assert not popup.isVisible()
    assert not window.product_page.inspection_active


def test_batch_freezes_only_checked_devices(window, monkeypatch):
    page = window.inspection_page
    window.product_page.servers = [dict(id=x, host=x) for x in ('a', 'b')]
    page.refresh_devices()
    page.device_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    captured = []
    class Worker:
        def __init__(self, servers, checks):
            captured.extend(servers)
            self.device_started = SimpleNamespace(connect=lambda f: None)
            self.device_finished = SimpleNamespace(connect=lambda f: None)
            self.progress = SimpleNamespace(connect=lambda f: None)
            self.batch_finished = SimpleNamespace(connect=lambda f: None)
        def start(self):
            pass
        def isRunning(self):
            return False
    monkeypatch.setattr('ui.batch_inspection_page.BatchInspectionWorker', Worker)
    page.start_inspection()
    assert [s['id'] for s in captured] == ['a']
    assert window.product_page.device_states['a'] == '等待巡检'
    assert window.product_page.drag_device('a') is None
    window.product_page.servers[0]['host'] = 'changed'
    assert captured[0]['host'] == 'a'


def test_failed_report_preserves_reason_and_waiting_row_clears_result(window, monkeypatch, tmp_path):
    page = window.inspection_page
    window.product_page.servers = [dict(id=x, host=x) for x in ('a', 'b')]
    page.refresh_devices()
    page.results['a'] = dict(device=dict(id='a', host='a'), task_id='run-a', time='2026-11-04',
        status='failed', message='SSH 连接失败：denied', script_outputs={'builtin': {}, 'custom': {}})
    page._display_result('a')
    assert not page.export_ai_btn.isEnabled()
    path = tmp_path / 'failed.txt'
    monkeypatch.setattr(page, '_choose_raw_export_format', lambda: 'txt')
    monkeypatch.setattr('ui.batch_inspection_page.QFileDialog.getSaveFileName', lambda *a: (str(path), ''))
    page.on_export_clicked()
    assert 'denied' in path.read_text(encoding='utf-8') and 'run-a' in path.read_text(encoding='utf-8')
    page._display_result('b')
    assert page.task_id is None and not page.export_btn.isEnabled()
    assert 'denied' not in page.output_text.toPlainText()


def test_batch_results_and_history_remain_bound_to_device(window, qtbot, monkeypatch, tmp_path):
    class Client:
        def connect(self, **params):
            self.host = params['host']
            return True, ''
        def execute_stream(self, *a, **k):
            yield f'主机: {self.host}\n采集状态: 完成\n'
        def disconnect(self):
            pass
    import ui.batch_inspection_page as module
    real_worker = module.BatchInspectionWorker
    monkeypatch.setattr(module, 'BatchInspectionWorker', lambda servers, checks: real_worker(servers, checks,
                         client_factory=Client, password_reader=lambda s: 'test'))
    window.product_page.servers = [dict(id=x, host=x) for x in ('a', 'b')]
    page = window.inspection_page
    page.refresh_devices()
    page.select_devices(True)
    page.start_inspection()
    qtbot.waitUntil(lambda: len(page.results) == 2 and not page.running)
    page.inspection_worker.wait()
    assert set(window.product_page.inspection_history) == {'a', 'b'}
    for i, device_id in enumerate(('a', 'b')):
        page.device_table.setCurrentCell(i, 1)
        assert page.task_device['id'] == device_id
        assert page.task_id == window.product_page.inspection_history[device_id]['task_id']
    monkeypatch.setattr(module.QFileDialog, 'getExistingDirectory', lambda *a: str(tmp_path))
    page.export_all_reports()
    assert len(list(tmp_path.glob('*.txt'))) == 2
    for device_id in ('a', 'b'):
        path = Path(window.product_page.inspection_history[device_id]['report_path'])
        assert path.exists() and f'地址：{device_id}:' in path.read_text(encoding='utf-8')


def test_cancel_before_start_retains_previous_report(window):
    old = dict(task_id='old-run', report_path='previous.html')
    window.product_page.inspection_history['b'] = dict(old)
    window.on_batch_targets_queued(['b'])
    window.inspection_page._device_finished(dict(device=dict(id='b'), task_id='cancelled-run',
        time='2026-11-04', checks=[], status='cancelled', started=False, message='未执行',
        script_outputs={'builtin': {}, 'custom': {}}))
    assert window.product_page.inspection_history['b'] == old
    assert 'b' not in window.product_page.device_states


def test_pending_single_connection_blocks_batch_start(window, monkeypatch):
    window.operation_router.request(dict(id='b'), 'inspection', None)
    assert not window.inspection_page.start_guard()
    window.product_page.servers = [dict(id='a', host='a')]
    window.inspection_page.refresh_devices()
    window.inspection_page.select_devices(True)
    window.inspection_page.start_inspection()
    assert not window.inspection_page.running and window.inspection_page.inspection_worker is None
