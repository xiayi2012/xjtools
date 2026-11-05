import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtTest import QSignalSpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui.batch_inspection_page import BatchInspectionWorker
from test_device_operation_routing import window


def test_wizard_requires_targets_then_checks_and_keeps_output_hidden(window, qtbot, monkeypatch):
    page = window.inspection_page
    window.product_page.servers = [dict(id=x, host=x) for x in ('a', 'b')]
    page.refresh_devices()
    window.show()
    window.switch_page(2)
    qtbot.wait(30)
    assert page.steps.currentIndex() == 0
    assert not page.next_btn.isEnabled()
    assert not page.inspect_list.isVisibleTo(page)
    assert not page.output_tabs.isVisibleTo(page)
    page.device_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert page.next_btn.isEnabled()
    page.next_btn.click()
    assert page.steps.currentIndex() == 1
    assert page.inspect_list.isVisibleTo(page)
    assert not page.device_table.isVisibleTo(page)
    assert not page.output_tabs.isVisibleTo(page)
    assert page.inspection_worker is None
    page._deselect_all()
    assert not page.next_btn.isEnabled()
    page.back_btn.click()
    assert page.steps.currentIndex() == 0
    assert page.device_table.item(0, 0).checkState() == Qt.CheckState.Checked
    page.next_btn.click()
    page._select_all()
    monkeypatch.setattr(BatchInspectionWorker, 'start', lambda self: None)
    page.next_btn.click()
    assert page.running and page.steps.currentIndex() == 2
    assert not page.inspect_list.isVisibleTo(page)
    assert not page.output_tabs.isVisibleTo(page)
    assert page.run_table.rowCount() == 1
    assert not page.export_all_btn.isEnabled()
    page.inspection_worker.device_progress.emit('a', 1, 2)
    assert page.run_table.cellWidget(0, 4).value() == 50
    assert not page.back_btn.isEnabled()
    page._device_finished(dict(device=dict(id='a', host='a'), task_id='a-task', time='today',
        status='completed', message='完成', script_outputs={'builtin': {'主机': 'a'}, 'custom': {}}))
    page._batch_finished(True, '巡检结束')
    assert page.run_table.cellWidget(0, 4).value() == 100
    assert page.export_all_btn.isEnabled()
    assert page.run_table.cellWidget(0, 5).findChild(QPushButton, 'deviceReportExport').isEnabled()


def test_removed_target_before_execution_returns_to_selection(window, monkeypatch):
    page = window.inspection_page
    window.product_page.servers = [dict(id='a', host='a')]
    page.refresh_devices()
    page.select_devices(True)
    page.next_btn.click()
    window.product_page.servers = []
    page.next_btn.click()
    assert page.steps.currentIndex() == 0
    assert not page.running and page.inspection_worker is None


def test_worker_reports_real_per_device_check_progress_and_continues(qtbot):
    class Client:
        def connect(self, **kwargs):
            return kwargs['host'] != 'bad', 'denied'
        def execute_stream(self, *args, **kwargs):
            yield 'ok'
        def disconnect(self):
            pass
    checks = [dict(Name=x, command='true') for x in ('one', 'two')]
    worker = BatchInspectionWorker([dict(id=x, host=x) for x in ('a', 'bad', 'b')], checks,
                                   client_factory=Client, password_reader=lambda s: 'test')
    updates, results = [], []
    worker.device_progress.connect(lambda *args: updates.append(args))
    worker.device_finished.connect(results.append)
    worker.run()
    assert [u[1:] for u in updates if u[0] == 'a'] == [(0, 2), (1, 2), (2, 2)]
    assert [u[1:] for u in updates if u[0] == 'bad'] == [(0, 2)]
    assert [u[1:] for u in updates if u[0] == 'b'] == [(0, 2), (1, 2), (2, 2)]
    assert [r['status'] for r in results] == ['completed', 'failed', 'completed']


def test_cancel_keeps_completed_check_count_and_does_not_export_unstarted(window):
    class Client:
        def connect(self, **kwargs):
            return True, ''
        def execute_stream(self, *args, **kwargs):
            yield 'ok'
        def disconnect(self):
            pass
    worker = BatchInspectionWorker([dict(id=x) for x in ('a', 'b')],
        [dict(Name=x, command='true') for x in ('one', 'two')],
        client_factory=Client, password_reader=lambda s: 'test')
    updates, results = [], []
    def progress(device_id, done, total):
        updates.append((device_id, done, total))
        if done == 1:
            worker.stop()
    worker.device_progress.connect(progress)
    worker.device_finished.connect(results.append)
    worker.run()
    assert ('a', 1, 2) in updates and ('a', 2, 2) not in updates
    assert results[0]['status'] == 'cancelled' and results[0]['started']
    assert results[1]['status'] == 'cancelled' and not results[1]['started']
    page = window.inspection_page
    page.results = {r['device']['id']: r for r in results}
    assert [r['device']['id'] for r in page._report_results()] == ['a']


def test_row_export_uses_clicked_device_and_batch_skips_unstarted(window, monkeypatch, tmp_path):
    page = window.inspection_page
    devices = [dict(id=x, host=x) for x in ('a', 'b', 'c')]
    page.run_checks = [dict(Name='主机', command='hostname')]
    for device in devices:
        key = device['id']
        page.results[key] = dict(device=device, task_id=key + '-task', time='today',
            status='cancelled' if key == 'c' else 'completed', message='结果 ' + key,
            started=key != 'c', script_outputs={'builtin': {'主机': 'host-' + key}, 'custom': {}})
    page._populate_run_table(devices)
    page._set_step(2)
    page._display_result('a')
    monkeypatch.setattr(page, '_choose_raw_export_format', lambda: 'txt')
    target = tmp_path / 'clicked-b.txt'
    monkeypatch.setattr('ui.batch_inspection_page.QFileDialog.getSaveFileName', lambda *a: (str(target), ''))
    saved = QSignalSpy(page.report_saved)
    page.run_table.cellWidget(1, 5).findChild(QPushButton, 'deviceReportExport').click()
    content = target.read_text(encoding='utf-8')
    assert 'b-task' in content and 'host-b' in content and 'host-a' not in content
    assert saved[-1][0]['device_id'] == 'b'
    monkeypatch.setattr('ui.batch_inspection_page.QFileDialog.getExistingDirectory', lambda *a: str(tmp_path))
    page.export_all_reports()
    assert len(list(tmp_path.glob('巡检报告_*.txt'))) == 2
    assert not page.run_table.cellWidget(2, 5).findChild(QPushButton, 'deviceReportExport').isEnabled()
