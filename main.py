# -*- coding: utf-8 -*-
"""PyCom 入口。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox

from app.core.settings import Settings
from app.paths import base_dir
from app.ui import theme
from app.ui.mainwindow import MainWindow


class _NoWheelInputGuard(QObject):
    """全局禁止鼠标滚轮修改下拉框/数字输入框（防止误触改参数）。

    只拦截事件目标为 QComboBox / QAbstractSpinBox 本体的滚轮；下拉列表展开后
    滚动列表、文本区/表格滚动等的目标不是这两类控件，不受影响。
    覆盖主窗口与所有对话框（含将来新增的同类控件）。
    """

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox)):
            ev.ignore()
            return True
        return False


def _app_icon() -> QIcon:
    """应用图标：用 QSvgRenderer 把 svg 渲染为多尺寸（不依赖 svg 图片插件）。
    单文件（onefile）打包时 datas 解包在 sys._MEIPASS 下，需一并查找。"""
    base = base_dir()
    candidates = [
        os.path.join(base, "assets", "pycom-icon.svg"),
        os.path.join(base, "pycom-icon.svg"),
        os.path.join(os.path.dirname(base), "图标", "pycom-icon.svg"),
    ]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.insert(0, os.path.join(meipass, "assets", "pycom-icon.svg"))
    path = next((c for c in candidates if os.path.exists(c)), None)
    icon = QIcon()
    if path:
        renderer = QSvgRenderer(path)
        if renderer.isValid():
            for size in (16, 24, 32, 48, 64, 128, 256):
                pm = QPixmap(size, size)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                renderer.render(p, QRect(0, 0, size, size))
                p.end()
                icon.addPixmap(pm)
    return icon


def _set_windows_app_id() -> None:
    """Windows 任务栏图标修复：进程需有独立 AppUserModelID，
    否则任务栏按宿主 python.exe 分组并显示其图标（标题栏图标不受影响）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "smartmx.PyCom")
    except Exception:
        pass


def main() -> int:
    _set_windows_app_id()                # 必须在创建 QApplication/窗口之前
    app = QApplication(sys.argv)
    app.setApplicationName("PyCom")
    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    app.installEventFilter(_NoWheelInputGuard(app))   # 须保留引用：父对象为 app
    base = base_dir()
    cfg_path = os.path.join(base, "pycom.json")
    cfg = Settings(cfg_path)
    app.setStyleSheet(theme.qss(cfg.d["theme"]))
    win = MainWindow(cfg)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
