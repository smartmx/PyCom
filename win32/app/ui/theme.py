# -*- coding: utf-8 -*-
"""浅色/深色主题：QSS + 颜色角色表。"""
from __future__ import annotations

from PySide2.QtGui import QColor, QPalette

ROLES = {
    "light": {
        "bg": "#FFFFFF", "fg": "#1A1A1A",
        "tx": "#1565C0", "rx": "#1B7A1B",
        "ts": "#9E9E9E", "hl_bg": "#FFE082",
        "sel": "#B3D4FC",
    },
    "dark": {
        "bg": "#1E1E1E", "fg": "#D4D4D4",
        "tx": "#6CB6FF", "rx": "#7FD18C",
        "ts": "#6E6E6E", "hl_bg": "#8A6D00",
        "sel": "#264F78",
    },
}

_QSS = """
/* 浅色=经典 BCB 紧凑风，深色=现代深色 */
QWidget { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimSun", sans-serif;
  font-size: 9pt; color: %(fg)s; }
QMainWindow, QDialog { background: %(win)s; color: %(fg)s; }
QMenuBar { background: %(bar)s; color: %(fg)s; }
QMenuBar::item { padding: 3px 8px; background: transparent; color: %(fg)s; }
QMenu { background: %(menu)s; color: %(fg)s; selection-background-color: %(sel)s; }
QMenu::item { padding: 3px 8px 3px 14px; }   /* 右 8px：让行尾 ● 靠近菜单右缘 */
QMenu[submenu="true"]::item { padding-right: 24px; }  /* 含子菜单的菜单：▶ 需缓冲 */
/* 勾选标记 = 行尾实心圆 `●`（2026-10-16 终版定稿）：真机 Win7 分数缩放
   （125%%/150%%）下一切勾号图形都不可用——原生 indicator 勾号发虚、自绘
   位图缺笔/发虚、条目垫图标还会把弹出菜单高度算短（底边被裁），行首前置
   标记（几何字符/全角字符）真机字宽下也无法对齐。`●` 放行尾：条目前缀
   不动、汉字天然对齐；列对齐用 Qt 制表符右对齐机制（`文字\t标记`，快捷键
   提示即此布局），标记前垫全角空格（间距 ≥ 两空格）。indicator 必须归零：
   勾号列不存在，标记列完全由文本构成。标记在菜单弹出前按勾选态写进条目
   文本。本文件与主线的差异见 docs/04-Win7-32位适配说明.md */
QMenu::indicator { width: 0px; height: 0px; }
QLabel, QCheckBox, QRadioButton { color: %(fg)s; }
QTabBar::tab { background: %(bar)s; color: %(fg)s; padding: 4px 12px; }
QTabBar::tab:selected { color: %(accent)s; border-bottom: 2px solid %(accent)s; }
QTableView, QPlainTextEdit, QLineEdit, QSpinBox, QComboBox {
  background: %(input)s; color: %(fg)s; border: 1px solid %(border)s;
  border-radius: 2px; padding: 1px 4px; selection-background-color: %(sel)s; }
QComboBox QAbstractItemView { background: %(menu)s; color: %(fg)s;
  selection-background-color: %(sel)s; }
QTableView QHeaderView::section { background: %(bar)s; color: %(fg)s; border: none;
  border-right: 1px solid %(border)s; border-bottom: 1px solid %(border)s;
  padding: 2px 6px; }
QPushButton {
  background: %(btn)s; color: %(fg)s; border: 1px solid %(border)s;
  border-radius: 2px; padding: 3px 10px; }
QPushButton:hover { border-color: %(accent)s; color: %(accent)s; }
QPushButton:pressed { background: %(press)s; }
QPushButton:checked { background: %(accent)s; color: #FFFFFF; border-color: %(accent)s; }
QStatusBar { background: %(bar)s; color: %(fg)s; }
QStatusBar::item { border: none; }
QLabel#sbSeg { border: 1px solid %(border)s; border-radius: 1px;
  background: %(input)s; color: %(fg)s; padding: 1px 6px; }
QSplitter::handle { background: %(border)s; }
QCheckBox { spacing: 4px; }
QProgressBar { border: 1px solid %(border)s; border-radius: 2px;
  background: %(input)s; text-align: center; color: %(fg)s; }
QProgressBar::chunk { background: %(accent)s; }
QScrollBar:vertical { background: %(bar)s; width: 10px; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: %(accent)s; }
QToolTip { background: %(input)s; color: %(fg)s; border: 1px solid %(border)s; }
"""

_VARS = {
    "light": dict(win="#F0F0F0", bar="#F0F0F0", menu="#FFFFFF", input="#FFFFFF",
                  btn="#F0F0F0", press="#D8D8D8", border="#A0A0A0",
                  accent="#0A6EBD", fg="#1A1A1A", sel="#B3D4FC"),
    "dark": dict(win="#252526", bar="#2D2D30", menu="#1F1F1F", input="#1E1E1E",
                 btn="#333337", press="#3F3F46", border="#3F3F46",
                 accent="#0E639C", fg="#D4D4D4", sel="#264F78"),
}


def qss(name: str) -> str:
    return _QSS % _VARS[name]


def palette(name: str):
    """深色：显式 QPalette，让原生绘制元素（面板立体边框、状态栏分隔框、
    勾选框指示器等）跟随主题；浅色：返回 None（调用方用系统标准色板）。"""
    if name != "dark":
        return None
    v = _VARS["dark"]
    p = QPalette()
    for role, col in ((QPalette.Window, v["win"]),
                      (QPalette.WindowText, v["fg"]),
                      (QPalette.Base, v["input"]),
                      (QPalette.AlternateBase, v["win"]),
                      (QPalette.Text, v["fg"]),
                      (QPalette.Button, v["btn"]),
                      (QPalette.ButtonText, v["fg"]),
                      (QPalette.BrightText, "#FFFFFF"),
                      (QPalette.ToolTipBase, v["input"]),
                      (QPalette.ToolTipText, v["fg"]),
                      (QPalette.PlaceholderText, "#7A7A7A"),
                      (QPalette.Highlight, v["accent"]),
                      (QPalette.HighlightedText, "#FFFFFF"),
                      (QPalette.Light, "#4A4A50"),
                      (QPalette.Midlight, "#3A3A40"),
                      (QPalette.Mid, v["border"]),
                      (QPalette.Dark, "#2A2A2E"),
                      (QPalette.Shadow, "#0F0F11")):
        p.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#6E6E6E"))
    p.setColor(QPalette.Disabled, QPalette.Base, QColor("#232326"))
    p.setColor(QPalette.Disabled, QPalette.Button, QColor("#2B2B2E"))
    return p


def colors(name: str) -> dict[str, QColor]:
    return {k: QColor(v) for k, v in ROLES[name].items()}
