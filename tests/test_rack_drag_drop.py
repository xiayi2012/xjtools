import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt, QPointF, QPoint, QEvent
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent, QDrag
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui.product_page import ProductPage
from ui.rack_view import device_mime, DEVICE_MIME, UNIT_PITCH
from test_rack_inventory import RACK, device


@pytest.fixture
def page(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('ui.product_page.get_config_dir', lambda: tmp_path)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: None)
    page = ProductPage()
    qtbot.addWidget(page)
    page.resize(980, 700)
    page.show()
    return page


def drag_events(target, mime, pos):
    enter = QDragEnterEvent(pos, Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, enter)
    move = QDragMoveEvent(pos, Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, move)
    drop = QDropEvent(QPointF(pos), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, drop)
    return enter, move, drop


def test_drag_to_slot_saves_only_placement_and_survives_reload(page, qtbot):
    loose = dict(id='loose', host='192.0.2.1', encrypted_password='cipher', rack_height_u=2)
    assert page.commit_inventory([device(), loose], [RACK])
    qtbot.wait(10)
    face = page.rack_view.faces[0]
    button = face.empty_buttons[7]
    pos = button.mapTo(face, button.rect().center())
    mime = device_mime('loose', page.drag_token)
    assert 'cipher' not in bytes(mime.data(DEVICE_MIME)).decode()
    enter, move, drop = drag_events(face, mime, pos)
    assert enter.isAccepted() and move.isAccepted() and drop.isAccepted()
    placed = next(s for s in page.servers if s['id'] == 'loose')
    assert placed == {**loose, 'rack_id': 'r1', 'rack_start_u': 7}
    assert page.inventory_store().load()['servers'][-1] == placed


def test_conflicting_and_out_of_bounds_drop_never_changes_data(page, qtbot):
    loose = dict(id='loose', rack_height_u=2)
    page.commit_inventory([device(), loose], [RACK])
    qtbot.wait(10)
    face = page.rack_view.faces[0]
    mime = device_mime('loose', page.drag_token)
    for u in [2, 12]:
        pos = face.empty_buttons[u].mapTo(face, face.empty_buttons[u].rect().center())
        enter, move, drop = drag_events(face, mime, pos)
        assert enter.isAccepted() and not move.isAccepted() and not drop.isAccepted()
        assert 'rack_id' not in page.servers[-1]


def test_cross_rack_and_self_overlap_move(page):
    racks = [RACK, {**RACK, 'id': 'r2', 'code': 'A02'}]
    page.commit_inventory([device()], racks)
    assert page.place_server('d1', 'r1', 4)
    assert page.place_server('d1', 'r2', 1)
    assert page.servers[0]['rack_id'] == 'r2'
    assert not page.place_server('missing', 'r2', 1)


def test_first_drop_creates_real_rack_with_existing_device(page, qtbot):
    page.commit_inventory([dict(id='old', name='old', host='192.0.2.2')], [])
    qtbot.wait(10)
    target = page.rack_view.findChild(QWidget, 'emptyRackIllustration')
    _, _, drop = drag_events(target, device_mime('old', page.drag_token), target.rect().center())
    assert drop.isAccepted()
    assert len(page.racks) == 1 and page.racks[0]['units'] == 42
    assert page.servers[0]['rack_id'] == page.racks[0]['id']
    assert page.servers[0]['rack_start_u'] == 1
    assert len(page.servers) == 1


def test_foreign_payload_and_busy_device_rejected(page, qtbot):
    page.commit_inventory([dict(id='old')], [RACK])
    qtbot.wait(10)
    face = page.rack_view.faces[0]
    pos = face.empty_buttons[1].mapTo(face, face.empty_buttons[1].rect().center())
    assert not drag_events(face, device_mime('old', 'foreign'), pos)[0].isAccepted()
    page.device_states['old'] = '连接中'
    assert not page.place_server('old', 'r1', 1)
    page.device_states['old'] = '巡检中'
    assert not page.place_server('old', 'r1', 1)
    assert not page.servers[0].get('rack_id')


def test_save_failure_keeps_original_position(page, monkeypatch):
    page.commit_inventory([device()], [RACK])
    monkeypatch.setattr('core.rack_inventory.os.replace', lambda *a: (_ for _ in ()).throw(OSError('locked')))
    assert not page.place_server('d1', 'r1', 7)
    assert page.servers[0]['rack_start_u'] == 3


def test_compact_sizes(page):
    page.commit_inventory([dict(id='old')], [])
    button = page.rack_view.unassigned_buttons['old']
    assert button.width() <= 300 and button.height() <= 60
    assert UNIT_PITCH <= 32


def test_mouse_drag_defers_widget_rebuild_until_drag_finishes(page, qtbot, monkeypatch):
    from PyQt6 import sip
    page.commit_inventory([dict(id='loose')], [RACK])
    qtbot.wait(10)
    old_view = page.rack_view
    source = old_view.unassigned_buttons['loose']
    face = old_view.faces[0]
    target = face.empty_buttons[7]
    def drag_exec(drag, actions):
        assert page.drag_active
        pos = target.mapTo(face, target.rect().center())
        assert drag_events(face, drag.mimeData(), pos)[2].isAccepted()
        assert page.rack_view is old_view and not sip.isdeleted(source)
        return Qt.DropAction.MoveAction
    monkeypatch.setattr(QDrag, 'exec', drag_exec)
    qtbot.mousePress(source, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(30, 12), QPointF(source.mapToGlobal(QPoint(30, 12))),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(source, move)
    assert page.servers[0]['rack_start_u'] == 7 and not page.drag_active
    qtbot.waitUntil(lambda: page.rack_view is not old_view)
    assert page.rack_view.selected_id == 'loose'


def test_cancelled_drag_keeps_device_unassigned(page, qtbot, monkeypatch):
    page.commit_inventory([dict(id='loose')], [RACK])
    qtbot.wait(10)
    source = page.rack_view.unassigned_buttons['loose']
    monkeypatch.setattr(QDrag, 'exec', lambda *args: Qt.DropAction.IgnoreAction)
    qtbot.mousePress(source, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(30, 12), QPointF(source.mapToGlobal(QPoint(30, 12))),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(source, move)
    assert not page.drag_active and not page.servers[0].get('rack_id')
