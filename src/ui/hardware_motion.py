"""Small paint-only transitions; parents own and dispose all animation objects."""

from PyQt6.QtCore import QEasingCurve, QVariantAnimation


class MotionValue(QVariantAnimation):
    def __init__(self, widget, level=0.0, duration=160):
        super().__init__(widget)
        self.level = level
        self.setDuration(duration)
        self.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.valueChanged.connect(self._changed)
        self.valueChanged.connect(widget.update)

    def _changed(self, value):
        if value is not None:
            self.level = float(value)

    def animate_to(self, level):
        self.stop()
        self.setStartValue(self.level)
        self.setEndValue(float(level))
        self.start()

    def breathe(self):
        self.stop()
        self.setDuration(1600)
        self.setLoopCount(-1)
        self.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.setStartValue(0.0)
        self.setKeyValueAt(.5, 1.0)
        self.setEndValue(0.0)
        self.start()
