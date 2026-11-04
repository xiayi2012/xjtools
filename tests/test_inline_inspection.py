from types import SimpleNamespace

from test_device_operation_routing import window


def test_device_inspection_stays_in_device_management(window, monkeypatch, qtbot):
    server = dict(id='B', name='node B', host='192.0.2.2')
    window.product_page.servers = [server]
    window.is_connected = True
    window.ssh_client = SimpleNamespace(is_connected=True)
    window.product_page.set_connected_server('B')
    window.switch_page(1)
    qtbot.waitUntil(lambda: window.stackedWidget.currentWidget() is window.product_page)
    calls = []
    monkeypatch.setattr(window.device_inspection_page, 'start_inspection', lambda: calls.append('start'))
    window.on_device_operation(server, 'inspection')
    assert calls == ['start']
    assert window.stackedWidget.currentWidget() is window.product_page
    assert window.product_page.inline_inspection.isVisibleTo(window.product_page)
    assert '192.0.2.2' in window.product_page.inspection_context.text()
    window.product_page.close_device_inspection()
    assert window.product_page.scroll_area.isVisibleTo(window.product_page)
    assert not window.product_page.inline_inspection.isVisibleTo(window.product_page)


def test_return_to_running_inspection_does_not_restart(window, monkeypatch):
    server = dict(id='B', host='192.0.2.2')
    window.is_connected = True
    window.ssh_client = SimpleNamespace(is_connected=True)
    window.product_page.set_connected_server('B')
    window.device_inspection_page.task_device = server
    window.device_inspection_page.inspection_worker = SimpleNamespace(isRunning=lambda: True)
    calls = []
    monkeypatch.setattr(window.device_inspection_page, 'start_inspection', lambda: calls.append('start'))
    window.on_device_operation(server, 'inspection')
    assert calls == []
    assert window.product_page.inline_inspection.isVisibleTo(window.product_page)
    assert window._has_active_device_task()


def test_other_entry_task_never_opens_stale_inline_results(window):
    server = dict(id='B', host='192.0.2.2')
    window.is_connected = True
    window.ssh_client = SimpleNamespace(is_connected=True)
    window.product_page.set_connected_server('B')
    window.device_inspection_page.task_device = server
    window.inspection_page.inspection_worker = SimpleNamespace(isRunning=lambda: True)
    window.on_device_operation(server, 'inspection')
    assert window.product_page.inline_inspection is None


def test_sidebar_cannot_start_second_task(window, monkeypatch):
    window.device_inspection_page.inspection_worker = SimpleNamespace(isRunning=lambda: True)
    warnings = []
    monkeypatch.setattr('ui.inspection_page.MessageBox', lambda *a: SimpleNamespace(exec=lambda: warnings.append(True)))
    window.inspection_page.start_inspection()
    assert warnings == [True] and window.inspection_page.inspection_worker is None


def test_inline_task_finishes_after_returning_to_racks(window, monkeypatch, qtbot):
    from PyQt6.QtCore import Qt
    server = dict(id='B', host='192.0.2.2')
    window.is_connected = True
    window.ssh_client = SimpleNamespace(is_connected=True, execute=lambda *a, **k: (0, 'ok', ''),
        execute_stream=lambda *a, **k: iter(['主机: demo\n采集状态: 完成\n']))
    window.product_page.servers = [server]
    window.product_page.set_connected_server('B')
    page = window.device_inspection_page
    page.set_device(server)
    page.inspect_list.blockSignals(True)
    for i in range(page.inspect_list.count()):
        page.inspect_list.item(i).setCheckState(Qt.CheckState.Checked if i == 0 else Qt.CheckState.Unchecked)
    page.inspect_list.blockSignals(False)
    window.on_device_operation(server, 'inspection')
    task_id = page.task_id
    window.product_page.close_device_inspection()
    qtbot.waitUntil(lambda: 'B' in window.product_page.inspection_history)
    page.inspection_worker.wait()
    window.resume_device_inspection()
    assert page.task_id == task_id
    assert window.product_page.inspection_active
    assert window.product_page.inspection_history['B']['task_id'] == task_id
