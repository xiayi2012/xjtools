import sys
from pathlib import Path

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QPushButton, QToolButton

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui.button_styles import install_button_styles, button_role
from ui.rack_view import RackDeviceButton


def contrast(a, b):
    def luminance(color):
        values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in color.getRgbF()[:3]]
        return .2126 * values[0] + .7152 * values[1] + .0722 * values[2]
    low, high = sorted([luminance(a), luminance(b)])
    return (high + .05) / (low + .05)


def test_primary_disabled_and_late_style_override(qtbot):
    install_button_styles(QApplication.instance())
    button = QPushButton('保存')
    qtbot.addWidget(button)
    button.resize(120, 36)
    button.show()
    button.setStyleSheet('background:#8b5cf6; color:white;')
    qtbot.waitUntil(lambda: '#148576' in button.styleSheet())
    normal = button.palette()
    assert contrast(normal.color(QPalette.ColorRole.Button), normal.color(QPalette.ColorRole.ButtonText)) >= 4.5
    button.setEnabled(False)
    qtbot.wait(10)
    disabled = button.palette()
    assert contrast(disabled.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button),
                    disabled.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText)) >= 3


def test_cancel_stop_and_custom_hardware_keep_correct_roles(qtbot):
    for label, role in [('取消', 'secondary'), ('停止批量巡检', 'danger'), ('删除', 'danger'), ('开始巡检', 'primary')]:
        button = QPushButton(label)
        qtbot.addWidget(button)
        assert button_role(button) == role
    hardware = RackDeviceButton(dict(id='a'))
    qtbot.addWidget(hardware)
    assert button_role(hardware) is None
    view = QToolButton()
    qtbot.addWidget(view)
    view.setProperty('viewChoice', True)
    assert button_role(view) == 'toggle'


def test_send_button_updates_when_it_becomes_stop(qtbot):
    install_button_styles(QApplication.instance())
    button = QPushButton('发送')
    button.setObjectName('sendBtn')
    qtbot.addWidget(button)
    button.show()
    qtbot.waitUntil(lambda: '#148576' in button.styleSheet())
    button.setText('停止')
    qtbot.waitUntil(lambda: '#a83e46' in button.styleSheet())
    button.setText('发送')
    qtbot.waitUntil(lambda: '#148576' in button.styleSheet())
