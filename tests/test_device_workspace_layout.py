import sys
from pathlib import Path

from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QToolButton
from PyQt6.QtTest import QSignalSpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui.product_page import ProductPage
from test_rack_inventory import RACK, device


def test_pending_devices_stay_outside_cabinet_scroll(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    page = ProductPage()
    qtbot.addWidget(page)
    page.commit_inventory([device(start=1), dict(id='loose', host='192.0.2.2')], [{**RACK, 'units': 42}])
    page.resize(980, 700)
    page.show()
    view = page.rack_view
    loose = view.unassigned_buttons['loose']
    assert not view.rack_scroll.isAncestorOf(loose)
    before = loose.mapTo(page, loose.rect().topLeft())
    view.rack_scroll.verticalScrollBar().setValue(300)
    assert loose.mapTo(page, loose.rect().topLeft()) == before


def test_detail_actions_are_compact_and_connect_button_can_disconnect(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    page = ProductPage()
    qtbot.addWidget(page)
    server = {**device(), 'host': '192.0.2.1', 'username': 'root', 'product': 'EDR'}
    page.commit_inventory([server], [RACK])
    page.resize(980, 700)
    page.show()
    page.rack_view.select_device(server['id'])
    page.set_connected_server(server['id'], True)
    item = page.rack_view.detail_item
    assert item.maximumHeight() <= 130
    assert item.is_root and not item.root_action.isEnabled()
    signal = QSignalSpy(page.rack_view.action_requested)
    item.connect_btn.click()
    assert signal[0][1] == 'connect'
    inspection = page.rack_view.findChild(QPushButton, 'rackAction_inspection')
    troubleshoot = page.rack_view.findChild(QPushButton, 'rackAction_troubleshoot')
    assert inspection.mapTo(page, inspection.rect().topLeft()).y() == troubleshoot.mapTo(page, troubleshoot.rect().topLeft()).y()


def test_details_open_on_click_and_close_without_disconnect(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    page = ProductPage()
    qtbot.addWidget(page)
    page.commit_inventory([device(), dict(id='loose', host='192.0.2.2')], [RACK])
    page.resize(980, 700)
    page.show()
    view = page.rack_view
    assert view.detail_scroll.isHidden()
    qtbot.wait(20)
    before = view.rack_scroll.width()
    view.faces[0].device_buttons['d1'].click()
    assert not view.detail_scroll.isHidden()
    page.set_connected_server('d1', True)
    qtbot.waitUntil(lambda: view.rack_scroll.width() < before)
    open_width = view.rack_scroll.width()
    close = view.findChild(QToolButton, 'closeDeviceDetails')
    assert close is not None
    close.click()
    qtbot.waitUntil(lambda: view.rack_scroll.width() > open_width)
    assert view.detail_scroll.isHidden() and page.selected_device_id is None
    assert page.connected_server_id == 'd1' and page.is_root
    page.set_device_state('d1', '巡检中')
    page.update_server_list()
    assert page.rack_view.detail_scroll.isHidden()
    page.rack_view.unassigned_buttons['loose'].click()
    assert not page.rack_view.detail_scroll.isHidden()
    assert page.rack_view.selected_id == 'loose'


def test_drag_placement_does_not_open_closed_inspector(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    page = ProductPage()
    qtbot.addWidget(page)
    page.commit_inventory([dict(id='loose')], [RACK])
    assert page.place_server('loose', 'r1', 1)
    assert page.rack_view.detail_scroll.isHidden()
