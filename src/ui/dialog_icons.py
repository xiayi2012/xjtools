"""Shared window icons for dialogs."""

from pathlib import Path

from PyQt6.QtGui import QIcon


def dialog_icon(name: str) -> QIcon:
    icon_path = Path(__file__).resolve().parents[2] / "icons" / name
    return QIcon(str(icon_path))
