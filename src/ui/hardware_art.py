"""Resolution-independent hardware illustrations drawn with Qt, with no bitmap assets."""

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QWidget


def metal(painter, rect, light, dark, radius=3):
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0, QColor(light))
    gradient.setColorAt(0.48, QColor(dark))
    gradient.setColorAt(0.52, QColor(dark))
    gradient.setColorAt(1, QColor(light))
    painter.setBrush(gradient)
    painter.setPen(QPen(QColor('#a6b8c5'), 1))
    painter.drawRoundedRect(rect, radius, radius)


def screw(painter, x, y):
    painter.setPen(QPen(QColor('#758e9e'), 1))
    painter.setBrush(QColor('#ecf2f6'))
    painter.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
    painter.drawLine(int(x - 1), int(y), int(x + 1), int(y))


def draw_cabinet(painter, rect, title='19" EQUIPMENT RACK'):
    """Draw a steel enclosure around a clear central area for real widgets."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    metal(painter, rect.adjusted(1, 1, -1, -8), '#fafcfd', '#cdd9e2', 7)
    inner = rect.adjusted(7, 38, -7, -25)
    painter.fillRect(inner, QColor('#e3ebf1'))
    painter.setPen(QPen(QColor('#ffffff'), 1))
    painter.drawLine(int(rect.left() + 4), int(rect.top() + 12), int(rect.left() + 4), int(rect.bottom() - 18))
    painter.drawLine(int(rect.right() - 4), int(rect.top() + 12), int(rect.right() - 4), int(rect.bottom() - 18))
    painter.setPen(QColor('#4c687c'))
    painter.setFont(QFont('Consolas', 9, QFont.Weight.Bold))
    painter.drawText(rect.adjusted(14, 8, -14, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter, title)
    painter.setPen(QPen(QColor('#a7bac7'), 2))
    for i in range(12):
        x = int(rect.center().x() - 46 + i * 8)
        painter.drawLine(x, int(rect.top() + 26), x + 4, int(rect.top() + 26))
    for x in (rect.left() + 11, rect.right() - 11):
        screw(painter, x, rect.top() + 14)
        screw(painter, x, rect.bottom() - 18)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#9fb2c0'))
    for x in (rect.left() + 18, rect.right() - 38):
        painter.drawRoundedRect(QRectF(x, rect.bottom() - 10, 20, 8), 2, 2)


def draw_server(painter, rect, name='', address='', connected=False, selected=False, hovered=False, state='',
                hover_level=None, selection_level=None, activity=0.0):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    metal(painter, rect.adjusted(1, 1, -1, -1), '#f8fbfd', '#becdd8', 3)
    body = rect.adjusted(10, 3, -10, -3)
    metal(painter, body, '#ecf2f6', '#c9d5df', 2)
    emphasis = max(float(hovered) if hover_level is None else hover_level,
                   float(selected) if selection_level is None else selection_level)
    if emphasis > 0:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        accent = QColor('#159f91')
        accent.setAlphaF(emphasis)
        painter.setPen(QPen(accent, 2))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 3, 3)
    for x in (rect.left() + 5, rect.right() - 5):
        for y in (rect.top() + 7, rect.bottom() - 7):
            screw(painter, x, y)
    # A physical nameplate remains readable above the hot-swap hardware.
    compact = rect.height() < 38
    plate = QRectF(body.left() + 5, body.top() + 2, body.width() - 35, 12 if compact else 15)
    painter.fillRect(plate, QColor('#f7fafc'))
    painter.setFont(QFont('Microsoft YaHei', 8))
    painter.setPen(QColor('#2d4e63'))
    text = f'{name}  {address}'.strip()
    text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(plate.width() - 6))
    painter.drawText(plate.adjusted(3, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter, text)
    # Power ring: connectivity indicator only, no invented health readings.
    color = '#1b9f89' if connected else '#879eae'
    if state == '连接失败':
        color = '#c95f65'
    elif state in ('连接中', '巡检中'):
        color = '#ba8831'
    cx, cy = body.right() - 11, body.top() + 10
    if activity > 0:
        glow = QColor(color)
        glow.setAlphaF(activity * .3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(cx - 8, cy - 8, 16, 16))
    painter.setBrush(QColor('#f4f8fa'))
    painter.setPen(QPen(QColor(color), 1.5))
    painter.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
    painter.drawLine(int(cx), int(cy - 6), int(cx), int(cy - 1))
    hardware_top = body.top() + (17 if compact else 22)
    bottom = QRectF(body.left() + 5, hardware_top, body.width() - 10, max(3, body.bottom() - hardware_top - 3))
    # Three hot-swap drive columns, two rows on taller devices.
    drive_width = bottom.width() * 0.61
    rows = 2 if bottom.height() > 25 else 1
    for row in range(rows):
        for col in range(3):
            bay = QRectF(bottom.x() + col * drive_width / 3, bottom.y() + row * bottom.height() / rows,
                         drive_width / 3 - 3, bottom.height() / rows - 2)
            metal(painter, bay, '#edf3f7', '#b9c9d5', 1)
            painter.setPen(QPen(QColor('#7893a6'), 1))
            painter.drawLine(int(bay.left() + 4), int(bay.center().y()), int(bay.right() - 8), int(bay.center().y()))
            painter.fillRect(QRectF(bay.right() - 5, bay.top() + 3, 2, max(2, bay.height() - 6)), QColor('#6b828e'))
            painter.fillRect(QRectF(bay.left() + 3, bay.bottom() - 3, 2, 1), QColor('#79a28e'))
    grille = QRectF(bottom.x() + drive_width + 4, bottom.y(), bottom.width() - drive_width - 5, bottom.height() - 2)
    painter.fillRect(grille, QColor('#bdcdd8'))
    painter.setPen(QPen(QColor('#71818c'), 1))
    for x in range(int(grille.left() + 3), int(grille.right() - 2), 5):
        for y in range(int(grille.top() + 3), int(grille.bottom() - 1), 4):
            painter.drawLine(x, y, x + 1, y)


class EmptyRackIllustration(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('emptyRackIllustration')
        self.setMinimumSize(270, 330)
        self.setMaximumHeight(380)
        self.setToolTip('机柜外观示意，创建机柜后可安排真实设备位置')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate((self.width() - 230) / 2, 8)
        painter.scale(1, (self.height() - 18) / 330)
        draw_cabinet(painter, QRectF(0, 0, 230, 330))
        for i in range(10):
            y = 43 + i * 25
            painter.fillRect(QRectF(23, y, 184, 23), QColor('#eff4f7'))
            painter.setPen(QPen(QColor('#c2d1dc'), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(23, y, 184, 23))
            painter.setPen(QColor('#7b8f9b'))
            painter.setFont(QFont('Consolas', 7))
            painter.drawText(QRectF(5, y, 16, 23), Qt.AlignmentFlag.AlignCenter, f'{10-i:02}')
            for x in (18, 214):
                painter.fillRect(QRectF(x, y + 9, 3, 5), QColor('#94adbe'))
        painter.setPen(QColor('#728da0'))
        painter.setFont(QFont('Microsoft YaHei', 9))
        painter.drawText(QRectF(23, 135, 184, 45), Qt.AlignmentFlag.AlignCenter, '等待设备上架')
