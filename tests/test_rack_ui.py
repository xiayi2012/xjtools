import sys
from pathlib import Path

from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui.product_page import ProductPage, AddServerDialog
from ui.rack_view import RackFace
from test_rack_inventory import RACK, device


def test_device_form_loads_position_and_recommends_free_slot(qtbot, monkeypatch):
    monkeypatch.setattr('ui.product_page.encrypt_password', lambda x: 'cipher')
    server = dict(device(), host='192.0.2.1', username='test', product='EDR')
    dialog = AddServerDialog(server, racks=[RACK], servers=[server])
    qtbot.addWidget(dialog)
    assert dialog.rack_combo.currentData() == 'r1'
    assert dialog.rack_start.value() == 3 and dialog.rack_height.value() == 2
    assert dialog.get_server_info()['rack_start_u'] == 3
    dialog.rack_combo.setCurrentIndex(0)
    assert dialog.get_server_info()['rack_id'] is None


def test_rack_face_selects_device_and_empty_slot(qtbot):
    face = RackFace(RACK, [device()])
    qtbot.addWidget(face)
    selected = QSignalSpy(face.device_selected)
    empty = QSignalSpy(face.empty_selected)
    face.device_buttons['d1'].click()
    face.empty_buttons[1].click()
    assert selected[0][0] == 'd1'
    assert empty[0] == ['r1', 1]
    assert 3 not in face.empty_buttons and 4 not in face.empty_buttons
    face.show()
    assert face.device_buttons['d1'].height() >= 50


def test_page_roundtrip_views_and_racks(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    page = ProductPage()
    qtbot.addWidget(page)
    assert page.view_mode == 'rack'
    assert page.commit_inventory([device()], [RACK])
    page.set_view_mode(True)
    assert page.view_mode == 'grid'
    page.set_view_mode('rack')
    page.rack_view.select_device('d1')
    assert page.rack_view.selected_id == 'd1'
    page.set_connected_server('d1', True)
    assert page.rack_view.detail_item.is_root
    page.set_view_mode(False)
    page.set_view_mode('rack')
    assert page.rack_view.detail_item.is_root
    page2 = ProductPage()
    qtbot.addWidget(page2)
    assert page2.racks == [RACK] and page2.servers[0]['rack_start_u'] == 3


def test_failed_save_leaves_inventory_unchanged(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    page = ProductPage()
    qtbot.addWidget(page)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: None)
    assert not page.commit_inventory([device()], [])
    assert page.servers == [] and page.racks == []


def test_connecting_target_cannot_be_edited_or_deleted(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    page = ProductPage()
    qtbot.addWidget(page)
    server = device()
    page.device_states['d1'] = '连接中'
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a))
    monkeypatch.setattr('ui.product_page.AddServerDialog', lambda *a, **k: (_ for _ in ()).throw(AssertionError('opened during connect')))
    page.on_edit_server(server)
    page.on_delete_server(server)
    assert len(warnings) == 2


def test_42u_selection_scrolls_device_into_view(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    page = ProductPage()
    qtbot.addWidget(page)
    page.commit_inventory([device(start=1)], [{**RACK, 'units': 42}])
    page.resize(980, 680)
    page.show()
    page.rack_view.select_device('d1')
    qtbot.waitUntil(lambda: page.rack_view.rack_scroll.verticalScrollBar().value() > 0)
