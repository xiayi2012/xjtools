"""Shared appearance for inventory forms."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QGroupBox, QFormLayout

from .button_styles import install_button_styles


def style_inventory_dialog(dialog):
    install_button_styles(QApplication.instance())
    dialog.setObjectName('inventoryFormDialog')
    icons = Path(__file__).resolve().parents[2] / 'icons'
    dialog.setStyleSheet('''
        QDialog#inventoryFormDialog {background:#f8fafc;}
        QLabel {color:#3c5664; background:transparent; font-size:13px;}
        QLabel#formDialogTitle {color:#263f4c; font-size:20px; font-weight:600;}
        QGroupBox {background:#ffffff; color:#355968; border:1px solid #dfe8ed;
            border-radius:8px; margin-top:12px; padding:6px 4px 4px; font-size:13px; font-weight:600;}
        QGroupBox::title {subcontrol-origin:margin; subcontrol-position:top left;
            left:14px; padding:0 5px;}
        QLineEdit, QComboBox, QSpinBox {background:#ffffff; color:#263f4c;
            border:1px solid #cfdee6; border-radius:6px; padding:7px 10px;
            min-height:20px; font-size:13px; selection-background-color:#148576; selection-color:white;}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover {border-color:#9dbbb9;}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {border-color:#148576;}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {background:#eef2f5; color:#7b8b96; border-color:#dfe6eb;}
        QSpinBox QLineEdit, QComboBox QLineEdit {border:0; padding:0; min-height:0; background:transparent;}
        QComboBox {padding-right:28px;}
        QComboBox::drop-down {border:0; width:26px;}
        QComboBox::down-arrow {image:url("@DOWN@"); width:10px; height:7px;}
        QComboBox QAbstractItemView {background:white; color:#263f4c; border:1px solid #cfdee6;
            selection-background-color:#e2f1ec; selection-color:#147868; padding:4px; outline:none;}
        QSpinBox {padding-right:25px;}
        QSpinBox::up-button, QSpinBox::down-button {width:22px; border:0; background:#edf3f5;}
        QSpinBox::up-arrow {image:url("@UP@"); width:8px; height:5px;}
        QSpinBox::down-arrow {image:url("@DOWN@"); width:8px; height:5px;}
        QSpinBox::up-arrow:disabled, QSpinBox::down-arrow:disabled {image:none;}
    '''.replace('@DOWN@', (icons / 'chevron-down.svg').as_posix())
       .replace('@UP@', (icons / 'chevron-up.svg').as_posix()))


def form_title(text):
    title = QLabel(text)
    title.setObjectName('formDialogTitle')
    return title


def form_section(title):
    section = QGroupBox(title)
    form = QFormLayout(section)
    form.setContentsMargins(14, 16, 14, 12)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(10)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return section, form
