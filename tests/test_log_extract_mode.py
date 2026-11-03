import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QInputDialog, QAbstractButton

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ui import log_extract_page


def test_log_rules_available_immediately_and_after_product_changes(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(log_extract_page, 'get_config_dir', lambda: tmp_path)
    page = log_extract_page.LogExtractPage()
    qtbot.addWidget(page)
    page.show()
    assert page.tabs.isTabVisible(0)
    assert page.tabs.currentWidget() is page.extract_tab
    assert page.ops_panel.isVisible()
    assert isinstance(page.mode_label, QLabel)
    assert not page.findChildren(QAbstractButton, 'modeBadge')
    rules = [{'path': '/var/log', 'name': '*.log'}]
    page.set_product('EDR')
    page.save_current_rules(rules)
    assert page.current_rules() == rules
    page.set_product('准入系统')
    assert page.ops_panel.isVisible() and page.tabs.isTabVisible(0)
    page.set_product('EDR')
    assert page.current_rules() == rules
    assert page.ops_panel.isVisible()
    page.tabs.setCurrentWidget(page.logs_tab)
    assert page.tabs.currentWidget() is page.logs_tab
    page.tabs.setCurrentWidget(page.extract_tab)
    assert page.ops_panel.isVisible()


def test_title_no_longer_opens_password_prompt(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(log_extract_page, 'get_config_dir', lambda: tmp_path)
    prompts = []
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: (prompts.append(True) or '', False))
    page = log_extract_page.LogExtractPage()
    qtbot.addWidget(page)
    page.show()
    for _ in range(3):
        qtbot.mouseClick(page.title_label, Qt.MouseButton.LeftButton)
    assert not prompts
