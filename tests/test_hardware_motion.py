import sys
from pathlib import Path

from PyQt6.QtCore import QAbstractAnimation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui.rack_view import RackDeviceButton


def test_busy_animation_stops_when_hidden_without_moving_device(qtbot):
    button = RackDeviceButton(dict(id='a', host='192.0.2.1'))
    qtbot.addWidget(button)
    button.setFixedSize(260, 54)
    button.show()
    original = button.geometry()
    button.device_state = '巡检中'
    qtbot.waitUntil(lambda: button.activity_motion.state() == QAbstractAnimation.State.Running)
    qtbot.wait(30)
    assert button.geometry() == original and button.isEnabled()
    button.hide()
    assert button.activity_motion.state() == QAbstractAnimation.State.Stopped


def test_hover_and_selection_do_not_change_click_area(qtbot):
    button = RackDeviceButton(dict(id='a'))
    qtbot.addWidget(button)
    button.setFixedSize(260, 54)
    button.show()
    original = button.rect()
    button.hover_motion.animate_to(1)
    button.setChecked(True)
    qtbot.waitUntil(lambda: button.selection_motion.level > .9)
    assert button.rect() == original and button.isChecked()
