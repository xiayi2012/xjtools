"""Qt rack front elevations and device details; emits requests, never opens SSH."""

from uuid import uuid4
import json

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QRectF, QMimeData, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QDrag, QCursor
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QDialog, QFormLayout, QLineEdit, QSpinBox, QDialogButtonBox, QApplication, QToolTip,
    QToolButton, QMenu, QProgressBar,
)
from qfluentwidgets import FluentIcon as FIF

from core.rack_inventory import partition_devices
from .hardware_art import draw_cabinet, draw_server, EmptyRackIllustration
from .hardware_motion import MotionValue

UNIT_PITCH = 30
DEVICE_MIME = 'application/x-xjtools-device'

WORKSPACE_STYLE = '''
    QFrame#rackStage, QScrollArea#deviceInspector {background:#ffffff; border:1px solid #dfe8ed; border-radius:10px;}
    QWidget#inspectorContent {background:#ffffff;}
    QWidget#rackCanvas {background:#eff4f7;}
    QFrame#pendingDock {background:#f8fbfc; border:0; border-bottom:1px solid #e5edf1;}
    QLabel[sectionTitle="true"] {font-size:13px; font-weight:600; color:#2b4958;}
    QLabel[muted="true"] {font-size:11px; color:#80929d;}
    QFrame#inspectionSummary {background:#f5f9fa; border:1px solid #e8eff2; border-radius:7px;}
    QPushButton[inspectorAction="true"] {background:#ffffff; color:#405d6b; border:1px solid #dae5ea; border-radius:6px; font-size:12px; padding:0 6px;}
    QPushButton[inspectorAction="true"]:hover {background:#eef8f5; border-color:#76bdaf; color:#147d70;}
    QPushButton#rackAction_inspection {background:#159b8c; color:white; border-color:#159b8c; font-weight:600;}
    QPushButton#rackAction_inspection:hover {background:#118678;}
    QPushButton[inspectorAction="true"]:disabled {background:#f8fafb; color:#a4b1b9; border-color:#e9eff2;}
    QToolButton#rackMenu {background:transparent; border:0; border-radius:4px;}
    QToolButton#rackMenu:hover {background:#dfece8;}
    QToolButton#rackMenu::menu-indicator {image:none;}
    QScrollBar:vertical {background:transparent; width:6px; margin:2px 0;}
    QScrollBar:horizontal {background:transparent; height:6px; margin:0 2px;}
    QScrollBar::handle {background:#bacad3; border-radius:3px; min-height:25px; min-width:25px;}
    QScrollBar::add-line, QScrollBar::sub-line {width:0; height:0;}
    QScrollBar::add-page, QScrollBar::sub-page {background:transparent;}
'''


def device_mime(server_id, token):
    mime = QMimeData()
    mime.setData(DEVICE_MIME, json.dumps({'id': server_id, 'token': token}).encode('utf-8'))
    return mime


def dragged_device(mime, page):
    if page is None or not mime.hasFormat(DEVICE_MIME):
        return None
    payload = bytes(mime.data(DEVICE_MIME))
    if len(payload) > 1024:
        return None
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get('token') != page.drag_token or not isinstance(data.get('id'), str):
            return None
        return page.drag_device(data['id'])
    except (ValueError, UnicodeError):
        return None


class NewRackDropTarget(EmptyRackIllustration):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setAcceptDrops(True)
        self.setToolTip('将待上架服务器拖到此处：自动创建 42U 机柜并从 U01 上架')
        self.highlighted = False

    def can_drop(self, mime):
        server = dragged_device(mime, self.page)
        if server is None or self.page.racks:
            return False
        height = server.get('rack_height_u', 1)
        return isinstance(height, int) and not isinstance(height, bool) and 1 <= height <= 42

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        self.highlighted = self.can_drop(event.mimeData())
        if self.highlighted:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()
        self.update()

    def dragLeaveEvent(self, event):
        self.highlighted = False
        self.update()
        event.accept()

    def dropEvent(self, event):
        server = dragged_device(event.mimeData(), self.page)
        accepted = self.can_drop(event.mimeData()) and self.page.create_rack_for_server(server['id'])
        self.highlighted = False
        self.update()
        if accepted:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.highlighted:
            painter = QPainter(self)
            painter.setPen(QPen(QColor('#38bda8'), 3))
            painter.setBrush(QColor(41, 160, 137, 35))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), 8, 8)


def plain_label(text, parent=None):
    label = QLabel(str(text), parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class RackDialog(QDialog):
    def __init__(self, rack=None, parent=None):
        super().__init__(parent)
        self.rack = dict(rack or {})
        self.setWindowTitle('编辑机柜' if rack else '新增机柜')
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        self.code = QLineEdit(self.rack.get('code', ''))
        self.code.setPlaceholderText('例如 A01，同一系统内唯一')
        self.name = QLineEdit(self.rack.get('name', ''))
        self.name.setPlaceholderText('例如 核心业务机柜')
        self.room = QLineEdit(self.rack.get('room', ''))
        self.room.setPlaceholderText('例如 一层机房')
        self.units = QSpinBox()
        self.units.setRange(1, 60)
        self.units.setSuffix(' U')
        self.units.setValue(self.rack.get('units', 42))
        for title, field in [('机柜编号', self.code), ('机柜名称', self.name), ('机房', self.room), ('容量', self.units)]:
            field.setMinimumHeight(32)
            form.addRow(title, field)
        self.error = plain_label('')
        self.error.setStyleSheet('color:#b45309')
        form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def validate(self):
        if not self.code.text().strip() or not self.name.text().strip():
            self.error.setText('请填写机柜编号和名称')
            return
        self.accept()

    def get_info(self):
        return dict(id=self.rack.get('id') or str(uuid4()), code=self.code.text().strip(),
                    name=self.name.text().strip(), room=self.room.text().strip(), units=self.units.value())


class RackFace(QFrame):
    device_selected = pyqtSignal(str)
    empty_selected = pyqtSignal(str, int)

    def __init__(self, rack, devices, parent=None, page=None):
        super().__init__(parent)
        self.page = page
        self.rack, self.devices = rack, devices
        self.device_buttons, self.empty_buttons = {}, {}
        self.setObjectName('rackFace')
        self.setFixedWidth(310)
        self.setStyleSheet('''
            QFrame#rackFace {background:#e4edf3; border:2px solid #afc2d0; border-radius:7px;}
            QLabel {color:#7893a5; background:transparent; font:11px 'Consolas';}
            QPushButton[empty="true"] {background:#f1f6f9; color:#97adbd; border:1px solid #cbd9e3;
                border-radius:2px; text-align:center; font-size:10px; padding:0;}
            QPushButton[empty="true"]:hover {background:#e0f2ed; color:#288f7e; border-color:#72bfae;}
        ''')
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(2)
        units = rack['units']
        occupied = set()
        for server in devices:
            start, height = server['rack_start_u'], server['rack_height_u']
            occupied.update(range(start, start + height))
            button = RackDeviceButton(server, page=page)
            button.setProperty('device', True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setObjectName('rackDevice_' + server['id'])
            button.setMinimumWidth(240)
            button.setFixedHeight(height * UNIT_PITCH - 2)
            button.setToolTip(f'{server.get("name") or server.get("host", "设备")}\n'
                              f'{server.get("host", "")} · {server.get("product", "")}\n'
                              f'U{start:02}–U{start+height-1:02} · {height}U\n点击查看详情')
            button.clicked.connect(lambda checked=False, sid=server['id']: self.device_selected.emit(sid))
            layout.addWidget(button, units - start - height + 1, 1, height, 1)
            self.device_buttons[server['id']] = button
        for u in range(units, 0, -1):
            row = units - u
            layout.setRowMinimumHeight(row, UNIT_PITCH - 2)
            number = QLabel(f'{u:02}')
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            number.setFixedWidth(23)
            layout.addWidget(number, row, 0)
            screw = QLabel('•')
            screw.setFixedWidth(10)
            layout.addWidget(screw, row, 2)
            if u not in occupied:
                button = QPushButton('＋')
                button.setProperty('empty', True)
                button.setFixedHeight(UNIT_PITCH - 2)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setToolTip(f'在 {rack["code"]} / U{u:02} 添加设备')
                button.clicked.connect(lambda checked=False, unit=u: self.empty_selected.emit(rack['id'], unit))
                layout.addWidget(button, row, 1)
                self.empty_buttons[u] = button
        layout.setContentsMargins(8, 40, 8, 28)
        self.setFixedHeight(68 + units * UNIT_PITCH - 2)
        self.setAcceptDrops(page is not None)
        self.drop_preview = QLabel(self)
        self.drop_preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drop_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.drop_preview.setWordWrap(True)
        self.drop_preview.hide()
        self.update_status(None)

    def drop_position(self, point):
        if not 32 <= point.x() <= self.width() - 18:
            return None
        row = int((point.y() - 40) // UNIT_PITCH)
        return self.rack['units'] - row if 0 <= row < self.rack['units'] else None

    def dragEnterEvent(self, event):
        if dragged_device(event.mimeData(), self.page):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        server = dragged_device(event.mimeData(), self.page)
        start = self.drop_position(event.position().toPoint())
        if server is None or start is None:
            self.drop_preview.hide()
            event.ignore()
            return
        candidate, error = self.page.placement_candidate(server['id'], self.rack['id'], start)
        height = candidate['rack_height_u']
        if not isinstance(height, int) or isinstance(height, bool) or height < 1:
            height = 1
        end = start + height - 1
        top = max(40, 40 + (self.rack['units'] - end) * UNIT_PITCH)
        visible_height = min(height, self.rack['units'] - start + 1) * UNIT_PITCH - 2
        self.drop_preview.setGeometry(32, top, self.width() - 50, visible_height)
        self.drop_preview.setText(error if error else f'U{start:02}–U{end:02} · 松开上架')
        color, background = ('#dc7373', 'rgba(88, 28, 28, 215)') if error else ('#50dac1', 'rgba(20, 83, 72, 215)')
        self.drop_preview.setStyleSheet(f'border:2px solid {color}; color:white; background:{background}; border-radius:3px; font-size:10px;')
        self.drop_preview.show()
        self.drop_preview.raise_()
        if error:
            event.ignore()
        else:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()

    def dragLeaveEvent(self, event):
        self.drop_preview.hide()
        event.accept()

    def dropEvent(self, event):
        server = dragged_device(event.mimeData(), self.page)
        start = self.drop_position(event.position().toPoint())
        self.drop_preview.hide()
        if server is not None and start is not None:
            _, error = self.page.placement_candidate(server['id'], self.rack['id'], start)
            if not error and self.page.place_server(server['id'], self.rack['id'], start):
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
                return
            if error:
                QToolTip.showText(self.mapToGlobal(event.position().toPoint()), error, self)
        event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        draw_cabinet(painter, QRectF(self.rect()), f'{self.rack.get("code", "")}  /  19" RACK')

    def update_status(self, connected_id, selected_id=None, states=None):
        for server in self.devices:
            button = self.device_buttons[server['id']]
            selected = selected_id == server['id']
            button.setChecked(selected)
            button.connected = server['id'] == connected_id
            state = (states or {}).get(server['id'], '')
            button.device_state = state
            lamp = '●' if server['id'] == connected_id else '○'
            name = server.get('name') or server.get('host', '设备')
            # Elision uses the actual widget width after the fixed-size rack is laid out.
            text = f'{lamp}  {name}   {server.get("host", "")}'
            if state:
                text += f'  · {state}'
            button.setText(button.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, 211))
            button.setAccessibleName(text + '，点击查看详情')


class RackDeviceButton(QPushButton):
    def __init__(self, server=None, parent=None, page=None):
        super().__init__(parent)
        self.server = server or {}
        self.connected = False
        self._device_state = ''
        self.hover_motion = MotionValue(self)
        self.selection_motion = MotionValue(self)
        self.activity_motion = MotionValue(self)
        self.arrival_motion = MotionValue(self, 1.0, 260)
        self._arrival_pending = False
        self.toggled.connect(lambda selected: self.selection_motion.animate_to(float(selected)))
        self.page = page
        self.press_position = None
        self.drag_started = False
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def device_state(self):
        return self._device_state

    @device_state.setter
    def device_state(self, state):
        if state == self._device_state:
            return
        self._device_state = state
        self.activity_motion.stop()
        self.activity_motion.level = 0
        if self.isVisible() and state in ('连接中', '巡检中'):
            self.activity_motion.breathe()
        self.update()

    def enterEvent(self, event):
        self.hover_motion.animate_to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_motion.animate_to(0)
        super().leaveEvent(event)

    def reveal(self):
        self._arrival_pending = True
        self.arrival_motion.level = 0

    def showEvent(self, event):
        super().showEvent(event)
        if self._arrival_pending:
            self._arrival_pending = False
            self.arrival_motion.animate_to(1)
        if self.device_state in ('连接中', '巡检中'):
            self.activity_motion.breathe()

    def hideEvent(self, event):
        for motion in (self.hover_motion, self.selection_motion, self.activity_motion, self.arrival_motion):
            motion.stop()
        self.hover_motion.level = 0
        self.selection_motion.level = float(self.isChecked())
        self.arrival_motion.level = 1
        super().hideEvent(event)

    def mousePressEvent(self, event):
        self.drag_started = False
        self.press_position = event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self.page is not None and self.press_position is not None
                and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self.press_position).manhattanLength() >= QApplication.startDragDistance()
                and self.page.drag_device(self.server.get('id')) is not None):
            self.drag_started = True
            self.press_position = None
            self.setDown(False)
            drag = QDrag(self)
            drag.setMimeData(device_mime(self.server['id'], self.page.drag_token))
            drag.setPixmap(self.grab())
            drag.setHotSpot(QPoint(min(self.width() // 2, 100), self.height() // 2))
            page = self.page
            view = page.rack_view
            page.drag_active = True
            if view:
                view.drag_scroll.start(40)
            try:
                drag.exec(Qt.DropAction.MoveAction)
            finally:
                page.drag_active = False
                if view:
                    view.drag_scroll.stop()
                # Rebuilding while QDrag.exec owns the source would delete a live native drag.
                QTimer.singleShot(0, page.update_server_list)
                drag.deleteLater()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.press_position = None
        if self.drag_started:
            self.drag_started = False
            self.setDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(.4 + .6 * self.arrival_motion.level)
        painter.translate(0, (1 - self.arrival_motion.level) * -5)
        draw_server(painter, QRectF(self.rect()), self.server.get('name') or self.server.get('host', '服务器'),
                    self.server.get('host', ''), self.connected, self.isChecked(), self.underMouse() or self.hasFocus(),
                    self.device_state, self.hover_motion.level, self.selection_motion.level, self.activity_motion.level)
        if self.property('matched'):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor('#e4ba67'), 2))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 3, 3)


class RackView(QWidget):
    action_requested = pyqtSignal(dict, str)
    empty_selected = pyqtSignal(str, int)
    rack_edit = pyqtSignal(dict)
    rack_delete = pyqtSignal(dict)

    def __init__(self, page, selected_id=None):
        super().__init__(page)
        self.page = page
        self.selected_id = None
        self.selection_timer = QTimer(self)
        self.selection_timer.setSingleShot(True)
        self.selection_timer.timeout.connect(self.scroll_to_selected)
        self.drag_scroll = QTimer(self)
        self.drag_scroll.timeout.connect(self.auto_scroll_drag)
        self.faces = []
        self.unassigned_buttons = {}
        self.detail_item = None
        self.setStyleSheet(WORKSPACE_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        stage = QFrame()
        stage.setObjectName('rackStage')
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)
        stage_header = QHBoxLayout()
        stage_header.setContentsMargins(16, 13, 16, 12)
        heading = plain_label('机柜工作区')
        heading.setProperty('sectionTitle', True)
        stage_header.addWidget(heading)
        stage_header.addStretch()
        count = plain_label(f'{len(page.racks)} 个机柜')
        count.setProperty('muted', True)
        stage_header.addWidget(count)
        stage_layout.addLayout(stage_header)
        self.rack_scroll = QScrollArea()
        self.rack_scroll.setWidgetResizable(True)
        self.rack_scroll.setStyleSheet('QScrollArea {border:0; background:transparent;}')
        content = QWidget()
        content.setObjectName('rackCanvas')
        body = QVBoxLayout(content)
        body.setContentsMargins(14, 16, 14, 14)
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        valid, unassigned, broken = partition_devices(page.racks, page.servers)
        query = page.search_input.text().strip().casefold()
        room = page.room_combo.currentData()
        matches = lambda s: not query or query in ' '.join(str(s.get(k, '')) for k in ('name', 'host', 'product', 'username')).casefold()
        for rack in page.racks:
            if not isinstance(rack.get('units'), int) or not 1 <= rack['units'] <= 60:
                continue
            servers = [s for s in valid if s.get('rack_id') == rack.get('id')]
            if room is not None and rack.get('room', '') != room:
                continue
            if query and not any(matches(s) for s in servers) and query not in f'{rack.get("code", "")} {rack.get("name", "")}'.casefold():
                continue
            column = QWidget()
            column.setFixedWidth(310)
            col = QVBoxLayout(column)
            col.setContentsMargins(0, 0, 0, 0)
            title_row = QHBoxLayout()
            title = plain_label(f'{rack.get("code", "")}  /  {rack.get("name", "")}')
            title.setStyleSheet('font-size:13px; font-weight:600; color:#2a4856;')
            title_row.addWidget(title, 1)
            menu_button = QToolButton()
            menu_button.setObjectName('rackMenu')
            menu_button.setIcon(FIF.SETTING.icon())
            menu_button.setFixedSize(28, 28)
            menu_button.setToolTip('机柜管理')
            menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(menu_button)
            menu.addAction('编辑机柜').triggered.connect(lambda checked=False, r=rack: self.rack_edit.emit(r))
            menu.addAction('删除机柜').triggered.connect(lambda checked=False, r=rack: self.rack_delete.emit(r))
            menu_button.setMenu(menu)
            title_row.addWidget(menu_button)
            col.addLayout(title_row)
            used = sum(s['rack_height_u'] for s in servers)
            caption = plain_label(f'{rack.get("room") or "未分配机房"} · {rack["units"]}U  |  已用 {used}U / 空闲 {rack["units"]-used}U')
            caption.setStyleSheet('font-size:10px; color:#80939e; padding:0 0 3px;')
            col.addWidget(caption)
            capacity_bar = QProgressBar()
            capacity_bar.setRange(0, rack['units'])
            capacity_bar.setValue(used)
            capacity_bar.setTextVisible(False)
            capacity_bar.setFixedHeight(3)
            capacity_bar.setStyleSheet('QProgressBar {background:#dbe5ea; border:0; border-radius:1px;} QProgressBar::chunk {background:#2baa98; border-radius:1px;}')
            col.addWidget(capacity_bar)
            face = RackFace(rack, servers, page=page)
            for server in servers:
                button = face.device_buttons[server['id']]
                if page._pending_arrival_id == server['id']:
                    button.reveal()
                    page._pending_arrival_id = None
                button.setProperty('matched', bool(query and matches(server)))
                button.style().unpolish(button)
                button.style().polish(button)
            face.device_selected.connect(self.select_device)
            face.empty_selected.connect(self.empty_selected)
            col.addWidget(face)
            col.addStretch()
            row.addWidget(column)
            self.faces.append(face)
        if not self.faces:
            if not page.racks:
                empty = QWidget()
                empty_layout = QHBoxLayout(empty)
                empty_layout.setContentsMargins(2, 8, 2, 8)
                empty_layout.addWidget(NewRackDropTarget(page))
                empty_copy = QVBoxLayout()
                empty_copy.setSpacing(12)
                empty_copy.addStretch()
                empty_title = plain_label('暂无机柜')
                empty_title.setStyleSheet('font-size:16px; font-weight:600; color:#2c4a59;')
                empty_copy.addWidget(empty_title)
                caption = plain_label('拖入设备即可创建 42U 机柜。')
                caption.setStyleSheet('font-size:11px; color:#81949f;')
                empty_copy.addWidget(caption)
                create = QPushButton('创建第一个机柜')
                create.setObjectName('createFirstRack')
                create.setFixedHeight(34)
                create.setProperty('inspectorAction', True)
                create.clicked.connect(lambda: page.edit_rack())
                empty_copy.addWidget(create)
                empty_copy.addStretch()
                empty_layout.addLayout(empty_copy)
                row.addWidget(empty)
            else:
                row.addWidget(plain_label('暂无匹配机柜，请调整搜索和机房筛选。'))
        pending = [s for s in unassigned + broken if matches(s)]
        if pending:
            self.pending_dock = QFrame()
            self.pending_dock.setObjectName('pendingDock')
            pending_layout = QVBoxLayout(self.pending_dock)
            pending_layout.setContentsMargins(14, 10, 14, 8)
            pending_layout.setSpacing(6)
            dock_header = QHBoxLayout()
            dock_title = plain_label(f'待上架  {len(pending)}')
            dock_title.setProperty('sectionTitle', True)
            dock_header.addWidget(dock_title)
            dock_header.addStretch()
            hint = plain_label('拖动设备上架')
            hint.setProperty('muted', True)
            dock_header.addWidget(hint)
            pending_layout.addLayout(dock_header)
            dock_scroll = QScrollArea()
            dock_scroll.setWidgetResizable(False)
            dock_scroll.setFixedHeight(70)
            dock_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            dock_scroll.setStyleSheet('QScrollArea {border:0; background:transparent;}')
            tray_widget = QWidget()
            tray = QHBoxLayout(tray_widget)
            tray.setContentsMargins(0, 0, 0, 4)
            tray.setSpacing(10)
            for server in pending:
                button = RackDeviceButton(server, page=page)
                button.setObjectName('unassignedDevice_' + str(server.get('id')))
                button.setFixedSize(260, 54)
                repair = '位置待修复 · ' if server in broken else ''
                button.setToolTip(f'{repair}{server.get("name") or server.get("host", "设备")}\n{server.get("host", "")}\n按住拖动到机柜上架，单击查看详情')
                button.setAccessibleName(button.toolTip())
                button.clicked.connect(lambda checked=False, sid=server.get('id'): self.select_device(sid))
                tray.addWidget(button)
                self.unassigned_buttons[server.get('id')] = button
            tray_widget.adjustSize()
            dock_scroll.setWidget(tray_widget)
            pending_layout.addWidget(dock_scroll)
            stage_layout.addWidget(self.pending_dock)
        body.addLayout(row)
        body.addStretch()
        self.rack_scroll.setWidget(content)
        stage_layout.addWidget(self.rack_scroll, 1)
        layout.addWidget(stage, 1)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFixedWidth(274)
        self.detail_scroll.setObjectName('deviceInspector')
        self.detail = QWidget()
        self.detail.setObjectName('inspectorContent')
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setContentsMargins(14, 14, 14, 14)
        self.detail_layout.setSpacing(9)
        self.detail_scroll.setWidget(self.detail)
        layout.addWidget(self.detail_scroll)
        self.setMinimumHeight(480)
        self.select_device(selected_id)

    def select_device(self, server_id):
        changed = server_id != self.selected_id
        self.selected_id = server_id
        self.page.selected_device_id = server_id
        for face in self.faces:
            face.update_status(self.page.connected_server_id, server_id, self.page.device_states)
        for sid, button in self.unassigned_buttons.items():
            button.connected = sid == self.page.connected_server_id
            button.device_state = self.page.device_states.get(sid, '')
            button.setChecked(sid == server_id)
            button.update()
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.detail_item = None
        server = next((s for s in self.page.servers if s.get('id') == server_id), None)
        heading = plain_label('设备详情')
        heading.setProperty('sectionTitle', True)
        self.detail_layout.addWidget(heading)
        if server is None:
            self.detail_layout.addStretch()
            icon = QLabel()
            icon.setPixmap(FIF.IOT.icon().pixmap(36, 36))
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.detail_layout.addWidget(icon)
            title = plain_label('选择一台设备')
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setStyleSheet('font-size:14px; font-weight:600; color:#486574;')
            self.detail_layout.addWidget(title)
            self.detail_layout.addStretch()
            return
        from .product_page import ServerDetailItem
        item = ServerDetailItem(server)
        item.set_connected(server_id == self.page.connected_server_id, self.page.is_root if server_id == self.page.connected_server_id else False)
        for signal, action in [(item.connect_clicked, 'connect'), (item.edit_clicked, 'edit'),
                               (item.delete_clicked, 'delete'), (item.switch_root_clicked, 'root'),
                               (item.monitor_clicked, 'monitor')]:
            signal.connect(lambda s, a=action: self.action_requested.emit(s, a))
        self.detail_layout.addWidget(item)
        self.detail_item = item
        rack = next((r for r in self.page.racks if r.get('id') == server.get('rack_id')), None)
        if rack and isinstance(server.get('rack_start_u'), int) and isinstance(server.get('rack_height_u'), int):
            start, height = server['rack_start_u'], server['rack_height_u']
            position = f'{rack.get("code")} / U{start:02}–U{start+height-1:02} / {height}U'
        else:
            position = '待上架 / 位置待修复' if server.get('rack_id') else '待上架'
        state = self.page.device_states.get(server_id) or ('已连接' if server_id == self.page.connected_server_id else '未连接')
        metadata = QWidget()
        meta_layout = QVBoxLayout(metadata)
        meta_layout.setContentsMargins(2, 3, 2, 3)
        meta_layout.setSpacing(5)
        position_title = plain_label('机柜位置')
        position_title.setProperty('muted', True)
        meta_layout.addWidget(position_title)
        position_row = QHBoxLayout()
        position_label = plain_label(position)
        position_label.setStyleSheet('font-size:11px; color:#3f5b69;')
        position_row.addWidget(position_label, 1)
        state_badge = plain_label('● ' + state)
        state_badge.setStyleSheet('color:#138a79; font-size:10px;' if server_id == self.page.connected_server_id else 'color:#82959f; font-size:10px;')
        position_row.addWidget(state_badge)
        meta_layout.addLayout(position_row)
        self.detail_layout.addWidget(metadata)
        summary = self.page.inspection_history.get(server_id)
        if summary:
            text = f'{summary.get("time", "")}\n'
            text += ' / '.join(f'{name} {summary.get("counts", {}).get(code, 0)}' for code, name in
                               [('normal', '正常'), ('warning', '警告'), ('error', '异常'), ('unknown', '未获取')])
        else:
            text = '尚未巡检'
        history = QFrame()
        history.setObjectName('inspectionSummary')
        history_layout = QVBoxLayout(history)
        history_layout.setContentsMargins(10, 9, 10, 9)
        history_layout.setSpacing(4)
        history_title = plain_label('最近巡检')
        history_title.setStyleSheet('font-size:11px; font-weight:600; color:#506b78;')
        history_layout.addWidget(history_title)
        history_label = plain_label(text)
        history_label.setStyleSheet('color:#8a9ca6; font-size:10px;')
        history_layout.addWidget(history_label)
        self.detail_layout.addWidget(history)
        actions = QWidget()
        action_layout = QGridLayout(actions)
        action_layout.setContentsMargins(0, 3, 0, 0)
        action_layout.setSpacing(8)
        action_layout.setColumnStretch(0, 1)
        action_layout.setColumnStretch(1, 1)
        for index, (label, action) in enumerate([('一键巡检', 'inspection'), ('AI 排障', 'troubleshoot'),
                                                ('日志收集', 'logs'), ('实时监控', 'monitor'),
                                                ('调整位置 / 下架', 'edit'), ('最近报告', 'report')]):
            if action == 'inspection' and state == '巡检中':
                label = '查看巡检进度'
            button = QPushButton(label)
            button.setFixedHeight(36 if index < 4 else 30)
            button.setObjectName('rackAction_' + action)
            button.setProperty('inspectorAction', True)
            if action == 'edit':
                button.setToolTip('修改机柜、U 位，或选择暂不上架')
            button.clicked.connect(lambda checked=False, a=action: self.action_requested.emit(server, a))
            if action == 'report':
                button.setEnabled(bool(summary and summary.get('report_path')))
            action_layout.addWidget(button, index // 2, index % 2)
        self.detail_layout.addWidget(actions)
        self.detail_layout.addStretch()
        if changed:
            self.selection_timer.start(0)

    def scroll_to_selected(self):
        for face in self.faces:
            button = face.device_buttons.get(self.selected_id)
            if button:
                self.rack_scroll.ensureWidgetVisible(button, 10, 50)
                return

    def auto_scroll_drag(self):
        viewport = self.rack_scroll.viewport()
        point = viewport.mapFromGlobal(QCursor.pos())
        if not viewport.rect().adjusted(-15, -15, 15, 15).contains(point):
            return
        for bar, value, limit in [(self.rack_scroll.verticalScrollBar(), point.y(), viewport.height()),
                                  (self.rack_scroll.horizontalScrollBar(), point.x(), viewport.width())]:
            if value < 35:
                bar.setValue(bar.value() - 24)
            elif value > limit - 35:
                bar.setValue(bar.value() + 24)

    def update_connected_status(self, connected_id, is_root=False):
        self.select_device(self.selected_id)
