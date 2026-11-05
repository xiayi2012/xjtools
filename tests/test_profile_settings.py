import json
from types import SimpleNamespace
import pytest

from PyQt6.QtWidgets import QDialog, QMessageBox, QApplication

from test_device_operation_routing import window


def test_settings_move_out_of_main_navigation_and_shortcuts_reuse_window(window, qtbot):
    window.show()
    window.switch_page(1)
    qtbot.waitUntil(lambda: window.stackedWidget.currentWidget() is window.product_page)
    assert window.navigationInterface.widget('profile_menu') is window.profile_avatar
    assert window.stackedWidget.indexOf(window.ai_config_page) == -1
    assert window.stackedWidget.indexOf(window.system_guard_page) == -1
    assert 'ai_config_page' not in window.navigationInterface.panel.items
    assert 'system_guard_page' not in window.navigationInterface.panel.items
    window.home_page.ai_config_clicked.emit()
    assert window.settings_window.isVisible()
    assert window.settings_window.pages.currentWidget() is window.ai_config_page
    assert window.stackedWidget.currentWidget() is window.product_page
    window.ai_config_page.edit_model_profile(0)
    window.ai_config_page.name_input.setText('尚未保存的名称')
    settings = window.settings_window
    window.home_page.system_guard_clicked.emit()
    assert window.settings_window is settings
    assert settings.pages.currentWidget() is window.system_guard_page
    settings.close()
    assert not settings.isVisible()
    assert window.schedule_timer.isActive()
    window.switch_page(6, force=True)
    assert settings.isVisible() and settings.pages.currentWidget() is window.ai_config_page
    assert window.ai_config_page.name_input.text() == '尚未保存的名称'
    window.is_connected = True
    window.switch_page(5, force=True)
    qtbot.waitUntil(lambda: window.stackedWidget.currentWidget() is window.log_extract_page)
    assert window.stackedWidget.currentWidget() is window.log_extract_page


def test_profile_menu_and_local_name_persist(window, monkeypatch, tmp_path):
    assert [a.text() for a in window.profile_menu.actions()] == ['个人中心', '设置', '退出登录']
    window.profile_menu.actions()[0].trigger()
    dialog = window.profile_dialog
    assert isinstance(dialog, QDialog) and dialog.isVisible()
    dialog.name_input.setText('')
    assert not dialog.save_btn.isEnabled()
    dialog.name_input.setText('运维同学')
    dialog.save_btn.click()
    assert not dialog.isVisible()
    assert window.profile_avatar.name == '运维同学'
    saved = json.loads((tmp_path / 'profile.json').read_text(encoding='utf-8'))
    assert saved['display_name'] == '运维同学'
    window.open_profile()
    assert window.profile_dialog.name_input.text() == '运维同学'


def test_profile_save_failure_keeps_dialog_open(window, monkeypatch):
    window.open_profile()
    dialog = window.profile_dialog
    dialog.name_input.setText('需要保留')
    def fail(*args):
        raise OSError('write failed')
    monkeypatch.setattr('ui.settings_window.atomic_json_write', fail)
    dialog.save_btn.click()
    assert dialog.isVisible() and dialog.name_input.text() == '需要保留'
    assert window.profile_avatar.name == '本地用户'


def test_logout_exits_app_and_keeps_running_task_guard(window, monkeypatch):
    quit_calls = []
    monkeypatch.setattr(QApplication, 'quit', lambda: quit_calls.append(True))
    window._force_quit = False
    window.scheduled_worker = SimpleNamespace(isRunning=lambda: True)
    window.profile_menu.actions()[2].trigger()
    assert not quit_calls and not window._force_quit
    window.scheduled_worker = None
    window.ai_config_page._test_worker = SimpleNamespace(isRunning=lambda: True)
    window.profile_menu.actions()[2].trigger()
    assert not quit_calls and not window._force_quit
    window.ai_config_page._test_worker = None
    window.profile_menu.actions()[2].trigger()
    assert quit_calls == [True] and window._force_quit


def test_log_navigation_still_requires_connection_after_settings_move(window, qtbot, monkeypatch):
    warnings = []
    monkeypatch.setattr('qfluentwidgets.MessageBox.exec', lambda self: warnings.append(True))
    window.show()
    window.stackedWidget.setCurrentWidget(window.log_extract_page)
    qtbot.waitUntil(lambda: window.stackedWidget.currentWidget() is window.product_page)
    assert warnings and not window.is_connected


@pytest.mark.parametrize('compact', [False, True])
def test_avatar_menu_stays_close_above_avatar(window, qtbot, compact):
    window.show()
    qtbot.wait(100)
    if compact:
        window.navigationInterface.toggle()
        qtbot.wait(300)
    window.show_profile_menu()
    qtbot.wait(300)
    avatar = window.profile_avatar
    menu = window.profile_menu
    anchor = avatar.mapToGlobal(avatar.rect().topLeft())
    surface_top = menu.view.mapToGlobal(menu.view.rect().topLeft())
    surface_bottom = surface_top.y() + menu.view.height()
    assert 4 <= anchor.y() - surface_bottom <= 10
    assert abs(surface_top.x() - anchor.x()) <= 2
    menu.hide()
