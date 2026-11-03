"""Qt rack front elevations and device details; emits requests, never opens SSH."""

from uuid import uuid4
import json

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QRectF, QMimeData, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QDrag, QCursor
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QDialog, QFormLayout, QLineEdit, QSpinBox, QDialogButtonBox, QApplication, QToolTip,
)

from core.rack_inventory import partition_devices
from .hardware_art import draw_cabinet, draw_server, EmptyRackIllustration

UNIT_PITCH = 30
DEVICE_MIME = 'application/x-xjtools-device'


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
            QFrame#rackFace {background:#26343e; border:2px solid #17232b; border-radius:7px;}
            QLabel {color:#9baeb9; background:transparent; font:11px 'Consolas';}
            QPushButton[empty="true"] {background:#1e2a32; color:#72838d; border:1px solid #34434d;
                border-radius:2px; text-align:center; font-size:10px; padding:0;}
            QPushButton[empty="true"]:hover {background:#314d55; color:#a1eee0; border-color:#50c9c3;}
            QPushButton[device="true"] {color:#e7eff3; background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #52616c,stop:0.5 #384953,stop:1 #2f3f49); border:1px solid #788b97;
                border-left:5px solid #96a8b1; border-right:5px solid #96a8b1; border-radius:3px;
                text-align:left; font:11px 'Microsoft YaHei'; padding:2px 8px;}
            QPushButton[device="true"]:hover {border-color:#50c9c3; background:#3c5861;}
            QPushButton[matched="true"] {border-color:#e4ba67; color:#ffedbf;}
            QPushButton[device="true"]:checked {border-color:#50c9c3; background:#264e54; color:#c6fff5;}
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
        self.device_state = ''
        self.page = page
        self.press_position = None
        self.drag_started = False
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

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
        draw_server(painter, QRectF(self.rect()), self.server.get('name') or self.server.get('host', '服务器'),
                    self.server.get('host', ''), self.connected, self.isChecked(), self.underMouse() or self.hasFocus(),
                    self.device_state)
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.rack_scroll = QScrollArea()
        self.rack_scroll.setWidgetResizable(True)
        self.rack_scroll.setStyleSheet('QScrollArea {border:0; background:transparent;}')
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 2, 0)
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
            title.setStyleSheet('font-size:15px; font-weight:600; color:#263f4b;')
            title_row.addWidget(title, 1)
            edit = QPushButton('编辑')
            edit.setFixedWidth(44)
            edit.clicked.connect(lambda checked=False, r=rack: self.rack_edit.emit(r))
            title_row.addWidget(edit)
            delete = QPushButton('删除')
            delete.setFixedWidth(44)
            delete.clicked.connect(lambda checked=False, r=rack: self.rack_delete.emit(r))
            title_row.addWidget(delete)
            col.addLayout(title_row)
            used = sum(s['rack_height_u'] for s in servers)
            caption = plain_label(f'{rack.get("room") or "未分配机房"} · {rack["units"]}U  |  已用 {used}U / 空闲 {rack["units"]-used}U')
            caption.setStyleSheet('font-size:11px; color:#687e89; padding:3px 0 6px;')
            col.addWidget(caption)
            face = RackFace(rack, servers, page=page)
            for server in servers:
                button = face.device_buttons[server['id']]
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
                empty_layout = QVBoxLayout(empty)
                empty_layout.setContentsMargins(8, 0, 8, 8)
                empty_layout.addWidget(NewRackDropTarget(page))
                caption = plain_label('拖动服务器到此处，自动创建 42U 机柜并从 U01 上架')
                caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
                caption.setStyleSheet('font-size:12px; color:#687e89;')
                empty_layout.addWidget(caption)
                create = QPushButton('创建第一个机柜')
                create.setObjectName('createFirstRack')
                create.setMinimumHeight(36)
                create.clicked.connect(lambda: page.edit_rack())
                empty_layout.addWidget(create)
                row.addWidget(empty)
            else:
                row.addWidget(plain_label('暂无匹配机柜，请调整搜索和机房筛选。'))
        for title, devices in [('待上架设备', unassigned), ('位置待修复 · 请编辑设备位置', broken)]:
            devices = [s for s in devices if matches(s)]
            if not devices:
                continue
            label = plain_label(f'{title}（{len(devices)}） · 按住服务器拖入机柜')
            label.setStyleSheet('font-size:14px; font-weight:600; padding-top:12px; color:#44616d;')
            body.addWidget(label)
            tray = QGridLayout()
            tray.setAlignment(Qt.AlignmentFlag.AlignLeft)
            for index, server in enumerate(devices):
                button = RackDeviceButton(server, page=page)
                button.setObjectName('unassignedDevice_' + str(server.get('id')))
                button.setFixedSize(260, 54)
                button.setToolTip(f'{server.get("name") or server.get("host", "设备")}\n{server.get("host", "")}\n按住拖动到机柜上架，单击查看详情')
                button.setAccessibleName(button.toolTip())
                button.clicked.connect(lambda checked=False, sid=server.get('id'): self.select_device(sid))
                tray.addWidget(button, index // 2, index % 2)
                self.unassigned_buttons[server.get('id')] = button
            body.addLayout(tray)
        body.addLayout(row)
        body.addStretch()
        self.rack_scroll.setWidget(content)
        layout.addWidget(self.rack_scroll, 1)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFixedWidth(274)
        self.detail_scroll.setStyleSheet('QScrollArea {background:#f6f9fa; border:1px solid #dce6e9; border-radius:8px;}')
        self.detail = QWidget()
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setContentsMargins(12, 14, 12, 14)
        self.detail_layout.setSpacing(12)
        self.detail_scroll.setWidget(self.detail)
        layout.addWidget(self.detail_scroll)
        self.setMinimumHeight(540)
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
                item.widget().deleteLater()
        self.detail_item = None
        server = next((s for s in self.page.servers if s.get('id') == server_id), None)
        self.detail_layout.addWidget(plain_label('设备详情'))
        if server is None:
            self.detail_layout.addWidget(plain_label('点击机柜中的设备查看详情和运维操作；点击空槽添加设备。'))
            self.detail_layout.addStretch()
            return
        from .product_page import ServerGridItem
        item = ServerGridItem(server)
        item.setMaximumSize(16777215, 240)
        item.setMinimumHeight(190)
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
        self.detail_layout.addWidget(plain_label(position))
        state = self.page.device_states.get(server_id) or ('已连接' if server_id == self.page.connected_server_id else '未连接')
        self.detail_layout.addWidget(plain_label(f'SSH：{state}'))
        summary = self.page.inspection_history.get(server_id)
        if summary:
            text = f'最近巡检：{summary.get("time", "")}\n{summary.get("message", "")}\n'
            text += ' / '.join(f'{name} {summary.get("counts", {}).get(code, 0)}' for code, name in
                               [('normal', '正常'), ('warning', '警告'), ('error', '异常'), ('unknown', '未获取')])
        else:
            text = '最近巡检：尚未巡检'
        history_label = plain_label(text)
        history_label.setStyleSheet('color:#687e89; font-size:11px;')
        self.detail_layout.addWidget(history_label)
        if server_id == self.page.connected_server_id:
            disconnect = QPushButton('断开连接')
            disconnect.setMinimumHeight(32)
            disconnect.clicked.connect(lambda: self.action_requested.emit(server, 'connect'))
            self.detail_layout.addWidget(disconnect)
        for label, action in [('一键巡检', 'inspection'), ('AI 排障', 'troubleshoot'), ('日志收集', 'logs'),
                              ('实时监控', 'monitor'), ('调整位置 / 下架', 'edit'), ('查看最近报告', 'report')]:
            button = QPushButton(label)
            button.setMinimumHeight(33)
            button.setObjectName('rackAction_' + action)
            button.clicked.connect(lambda checked=False, a=action: self.action_requested.emit(server, a))
            if action == 'report':
                button.setEnabled(bool(summary and summary.get('report_path')))
            self.detail_layout.addWidget(button)
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
