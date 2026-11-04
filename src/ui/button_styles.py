"""Shared button colors for pages, dialogs and dynamically created controls."""

from weakref import WeakKeyDictionary, WeakSet

from PyQt6 import sip
from PyQt6.QtCore import QObject, QEvent, QTimer
from PyQt6.QtGui import QIcon, QColor, QPainter
from PyQt6.QtWidgets import QPushButton, QToolButton
from qfluentwidgets import PrimaryPushButton, setThemeColor

PRIMARY = '#148576'
DISABLED_TEXT = '#7b8b96'
PRIMARY_IDS = {'addServerBtn', 'saveConfigButton', 'saveBtn', 'startButton', 'sendBtn',
               'chatSendBtn', 'dialogSaveButton', 'proceedButton', 'nextStepBtn', 'nextStepButton'}
DANGER_IDS = {'dangerBtn', 'cardDangerButton', 'confirmDeleteButton', 'deleteBtn',
              'deleteAllHistoryButton', 'deleteHistoryButton', 'deleteHistoryBtn', 'clearFaqBtn', 'stopButton'}
SECONDARY_IDS = {'cancelBtn', 'cancelDeleteButton', 'dialogCancelButton', 'closeBtn', 'addRackBtn',
                 'testConnectionButton', 'exportButton', 'exportAiButton', 'exportAiReportBtn'}


def button_role(button):
    if type(button).__name__ == 'RackDeviceButton':
        return None
    if isinstance(button, QToolButton):
        if button.property('viewChoice'):
            return 'toggle'
        return 'icon' if type(button) is QToolButton else None
    if not isinstance(button, QPushButton):
        return None
    explicit = button.property('buttonRole')
    if explicit in ('primary', 'secondary', 'danger', 'icon', 'toggle'):
        return explicit
    name, text = button.objectName(), button.text().replace('&', '').strip()
    if name in DANGER_IDS or text.startswith(('删除', '移除', '停止', '清空')):
        return 'danger'
    if name in SECONDARY_IDS or text.startswith(('取消', '返回', '关闭', '收起')):
        return 'secondary'
    if (name in PRIMARY_IDS or isinstance(button, PrimaryPushButton)
            or text.startswith(('开始', '保存', '确认', '发送')) or text in ('确定', '连接', '启用', 'OK')):
        return 'primary'
    return 'secondary'


def button_css(role):
    background, text, border, hover, pressed = {
        'primary': (PRIMARY, '#ffffff', PRIMARY, '#107b6d', '#0d6c60'),
        'danger': ('#fff3f3', '#a83e46', '#e5b8bc', '#fde6e8', '#fbd9dd'),
        'secondary': ('#ffffff', '#3c5664', '#cfdee6', '#edf5f8', '#dfebf0'),
        'toggle': ('transparent', '#3c5664', 'transparent', '#e5efed', '#d5e7e2'),
        'icon': ('transparent', '#3c5664', 'transparent', '#e9f1f5', '#dce8ee'),
    }[role]
    radius = 5 if role in ('icon', 'toggle') else 6
    padding = '0px' if role in ('icon', 'toggle') else '4px 10px'
    return f'''
        QPushButton, QToolButton {{background:{background}; color:{text}; border:1px solid {border}; border-radius:{radius}px; padding:{padding};}}
        QPushButton:hover, QToolButton:hover {{background:{hover};}}
        QPushButton:pressed, QToolButton:pressed {{background:{pressed};}}
        QPushButton:checked, QToolButton:checked {{background:#dcefe9; color:#147868; border-color:#75b9ab;}}
        QPushButton:focus, QToolButton:focus {{border-color:#429e91;}}
        QPushButton:disabled, QToolButton:disabled {{background:#eef2f5; color:{DISABLED_TEXT}; border-color:#dfe6eb;}}
        QToolButton::menu-indicator {{image:none;}}
    '''


class ButtonStyleController(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.pending = WeakSet()
        self.signatures = WeakKeyDictionary()
        self.icons = WeakKeyDictionary()
        self.icon_states = WeakKeyDictionary()
        self.applying = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.flush)

    def schedule(self, button):
        self.pending.add(button)
        if not self.timer.isActive():
            self.timer.start(0)

    def eventFilter(self, watched, event):
        if self.applying or not isinstance(watched, (QPushButton, QToolButton)):
            return False
        kind = event.type()
        if kind in (QEvent.Type.Show, QEvent.Type.Polish, QEvent.Type.StyleChange, QEvent.Type.EnabledChange):
            if button_role(watched):
                self.schedule(watched)
        elif kind in (QEvent.Type.Paint, QEvent.Type.DynamicPropertyChange):
            role = button_role(watched)
            if role and self.signatures.get(watched) != role:
                self.schedule(watched)
        return False

    def flush(self):
        buttons = list(self.pending)
        self.pending.clear()
        self.applying = True
        try:
            for button in buttons:
                if sip.isdeleted(button):
                    continue
                role = button_role(button)
                if role is None:
                    continue
                css = button_css(role)
                self.signatures[button] = role
                if button.styleSheet() != css:
                    button.setStyleSheet(css)
                icon_state = (role, button.isEnabled())
                if self.icon_states.get(button) != icon_state and not button.icon().isNull():
                    self.icon_states[button] = icon_state
                    original = self.icons.setdefault(button, button.icon())
                    icon = QIcon()
                    foreground = '#ffffff' if role == 'primary' else '#a83e46' if role == 'danger' else '#3c5664'
                    if not button.isEnabled():
                        foreground = DISABLED_TEXT
                    for mode, color in [(QIcon.Mode.Normal, foreground),
                                        (QIcon.Mode.Disabled, DISABLED_TEXT)]:
                        ratio = button.devicePixelRatioF()
                        pixmap = original.pixmap(button.iconSize() * ratio)
                        pixmap.setDevicePixelRatio(ratio)
                        painter = QPainter(pixmap)
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                        painter.fillRect(pixmap.rect(), QColor(color))
                        painter.end()
                        icon.addPixmap(pixmap, mode)
                    button.setIcon(icon)
        finally:
            self.applying = False


def install_button_styles(app):
    controller = getattr(app, '_button_style_controller', None)
    if controller is None:
        setThemeColor(PRIMARY)
        controller = ButtonStyleController(app)
        app._button_style_controller = controller
        app.installEventFilter(controller)
        for widget in app.allWidgets():
            if isinstance(widget, (QPushButton, QToolButton)) and button_role(widget):
                controller.schedule(widget)
    return controller
