import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QDialog, QPushButton, QToolButton, QMessageBox
from PyQt6.QtTest import QSignalSpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui.product_page import ProductPage, ServerListItem, ServerGridItem


@pytest.fixture
def server():
    return dict(id='device-1', name='Test device', host='127.0.0.1', username='tester',
                product='EDR', switch_root=True)


@pytest.mark.parametrize('item_type', [ServerListItem, ServerGridItem])
def test_gear_menu_contains_actions_and_tracks_root(qtbot, server, item_type):
    item = item_type(server)
    qtbot.addWidget(item)
    gear = item.findChild(QToolButton, 'deviceActionsButton')
    assert gear is not None
    actions = [action for action in gear.menu().actions() if not action.isSeparator()]
    assert [action.text() for action in actions] == ['切换 root', '编辑', '删除', '知识库', '脚本库']
    assert not actions[0].isEnabled()
    assert not any(button.text() in ['编辑', '删除', '知识库', '脚本库', '切换root']
                   for button in item.findChildren(QPushButton))
    item.set_connected(True, False)
    assert actions[0].isEnabled()
    spy = QSignalSpy(item.switch_root_clicked)
    actions[0].trigger()
    assert len(spy) == 1 and spy[0][0] == server
    item.set_connected(True, True)
    assert not actions[0].isEnabled() and actions[0].isVisible()
    actions[0].trigger()
    assert len(spy) == 1
    item.set_connected(False)
    assert not actions[0].isEnabled()
    item.set_connected(True, False)
    assert actions[0].isEnabled()
    for action, signal in [(actions[1], item.edit_clicked), (actions[2], item.delete_clicked)]:
        emitted = QSignalSpy(signal)
        action.trigger()
        assert len(emitted) == 1 and emitted[0][0] == server


@pytest.mark.parametrize('item_type', [ServerListItem, ServerGridItem])
@pytest.mark.parametrize('extra', [{'username': 'root'}, {'switch_root': False}, {'product': '办公智盾'}])
def test_unavailable_root_switch_stays_disabled(qtbot, server, item_type, extra):
    item = item_type({**server, **extra})
    qtbot.addWidget(item)
    item.set_connected(True, False)
    assert not item.root_action.isEnabled()


def test_root_state_survives_refresh_and_view_switch(qtbot, monkeypatch, server):
    monkeypatch.setattr(ProductPage, 'load_servers', lambda self: None)
    page = ProductPage()
    qtbot.addWidget(page)
    page.servers = [server, {**server, 'id': 'device-2'}]
    page.update_server_list()
    page.set_connected_server('device-1', True)
    for grid in [False, True, False]:
        page.is_grid_view = grid
        page.update_server_list()
        first, second = page.product_sections[0].server_items
        assert first.is_root and not first.root_action.isEnabled()
        assert not second.is_root and not second.root_action.isEnabled()


@pytest.mark.parametrize('outcome', ['success', 'failed', 'cancelled'])
def test_root_switch_result_updates_only_on_success(qtbot, monkeypatch, server, outcome):
    from ui.main_window import MainWindow
    from ui import connection_page
    monkeypatch.setattr(ProductPage, 'load_servers', lambda self: None)
    window = MainWindow()
    qtbot.addWidget(window)
    window.product_page.servers = [server]
    window.product_page.update_server_list()
    window.product_page.set_connected_server(server['id'])
    window.connection_info = {'username': 'tester'}
    commands = []
    def execute(command):
        commands.append(command)
        return (0, 'root\n', '') if outcome == 'success' else (1, '', 'denied')
    window.ssh_client = SimpleNamespace(execute=execute, disconnect=lambda: None)
    monkeypatch.setattr(connection_page, 'RootPasswordDialog', lambda parent: SimpleNamespace(
        exec=lambda: QDialog.DialogCode.Rejected if outcome == 'cancelled' else QDialog.DialogCode.Accepted,
        get_password=lambda: 'test-password'))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    try:
        window.after_connect_switch_root()
        assert bool(warnings) == (outcome == 'failed')
        assert window.product_page.is_root == (outcome == 'success')
        assert window.product_page.product_sections[0].server_items[0].root_action.isEnabled() == (outcome != 'success')
        assert bool(commands) == (outcome != 'cancelled')
    finally:
        window._force_quit = True
