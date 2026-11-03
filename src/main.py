#!/usr/bin/env python3
"""
AI 智能运维助手 - 主入口
"""

import sys
import os
import io
from contextlib import redirect_stdout

# 添加 src 目录到 Python 路径
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

with redirect_stdout(io.StringIO()):
    from qfluentwidgets import setThemeColor
    from ui.main_window import MainWindow

print("欢迎使用AI智能巡检排障工具")

__author__ = "xiayi"
__hidden_credit__ = "by xiayi"


def main():
    """主函数"""
    app = QApplication(sys.argv)
    app.setApplicationName("AI智能Linux运维系统")
    app.setApplicationVersion("3.3.0")
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(__file__), "..", "icons", "xunjian2.svg")))

    # 设置 Fluent 主题色
    setThemeColor("#50C9C3")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
