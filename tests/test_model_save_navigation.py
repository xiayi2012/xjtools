import json
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QMessageBox, QPushButton, QLabel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui import ai_config_page
from ui.product_page import ProductPage


@pytest.mark.parametrize('adding', [False, True])
def test_saved_model_returns_to_list_and_cancel_preserves_saved_values(qtbot, monkeypatch, tmp_path, adding):
    config_path = tmp_path / 'ai_config.json'
    monkeypatch.setattr(ai_config_page, 'get_ai_config_path', lambda: str(config_path))
    modals = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: modals.append(args))
    page = ai_config_page.AIConfigPage()
    qtbot.addWidget(page)
    page.show()
    if adding:
        page.add_model_profile()
    else:
        page.edit_model_profile(0)
    index = page.current_model_index
    page.name_input.setText('保存后的模型')
    page.save_config()
    assert not page._editing_model
    assert page.model_list_widget.isVisible() and page.add_model_btn.isVisible()
    assert not page.edit_panel.isVisible() and not page.btn_container.isVisible()
    assert not modals
    assert json.loads(config_path.read_text(encoding='utf-8'))['models'][index]['name'] == '保存后的模型'
    page.edit_model_profile(index)
    page.name_input.setText('未保存修改')
    page.close_editor()
    assert page.model_configs[index]['name'] == '保存后的模型'


def test_save_failure_keeps_model_editor_open(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(ai_config_page, 'get_ai_config_path', lambda: str(tmp_path / 'config.json'))
    page = ai_config_page.AIConfigPage()
    qtbot.addWidget(page)
    page.show()
    page.edit_model_profile(0)
    page.name_input.setText('需要保留的修改')
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    monkeypatch.setattr(ai_config_page, 'get_ai_config_path', lambda: str(tmp_path))
    page.save_config()
    assert warnings and page._editing_model
    assert page.name_input.text() == '需要保留的修改'


def test_inspection_and_troubleshooting_order_and_routes(qtbot, monkeypatch, tmp_path):
    from ui.main_window import MainWindow
    monkeypatch.setattr(ai_config_page, 'get_ai_config_path', lambda: str(tmp_path / 'config.json'))
    monkeypatch.setattr(ProductPage, 'load_servers', lambda self: None)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(50)
    try:
        assert window.stackedWidget.widget(2) is window.inspection_page
        assert window.stackedWidget.widget(3) is window.ai_troubleshoot_page
        assert window.navigationInterface.widget('inspection_page').text() == '一键巡检'
        assert window.inspection_page.findChild(QLabel, 'pageTitle').text() == '一键巡检'
        left = window.home_page.findChild(QPushButton, 'troubleshootButton')
        right = window.home_page.findChild(QPushButton, 'inspectButton')
        assert left.mapTo(window.home_page, QPoint()).x() < right.mapTo(window.home_page, QPoint()).x()
        window.is_connected = True
        qtbot.mouseClick(right, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: window.stackedWidget.currentWidget() is window.inspection_page)
        window.home_page.troubleshoot_clicked.emit()
        qtbot.waitUntil(lambda: window.stackedWidget.currentWidget() is window.ai_troubleshoot_page)
        server = dict(id='B', host='192.0.2.2')
        window.product_page.servers = [server]
        window.product_page.set_connected_server('B')
        from types import SimpleNamespace
        window.ssh_client = SimpleNamespace(is_connected=True)
        monkeypatch.setattr(window.device_inspection_page, 'start_inspection', lambda: None)
        window.on_go_to_next_step()
        qtbot.waitUntil(lambda: window.stackedWidget.currentWidget() is window.product_page)
    finally:
        window._force_quit = True
