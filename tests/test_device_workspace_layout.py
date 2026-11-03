import sys
from pathlib import Path

from PyQt6.QtWidgets import QPushButton
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
