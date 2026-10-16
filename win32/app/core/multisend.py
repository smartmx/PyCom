# -*- coding: utf-8 -*-
"""多条字符串发送：表格模型 + 循环发送调度。

对齐原版 SSCOM 语义：
- 每行带"点击发送"按钮（主窗口经 setIndexWidget 注入；双击按钮可编辑其文字=注释）
- "HEX"勾选框（整格点击切换）：勾选 = HEX 数据
- "顺序"号 > 0 的条目参与循环发送，按顺序号升序；0 = 循环时跳过
- 每条发送后等待该条的"延时ms"再发下一条；末条发完回到第一条
"""
from __future__ import annotations

from PySide2.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QTimer, Signal

COLS = ["点击发送", "HEX", "内容", "顺序", "延时ms"]
COL_SEND = 0
COL_HEX = 1
COL_TEXT = 2
COL_ORDER = 3
COL_INTERVAL = 4

ORDER_MAX = 9999             # 与 Settings 归一化同一套上下界
INTERVAL_MAX = 3_600_000     # 防 QTimer __int64 溢出（Shiboken OverflowError）


class MultiSendModel(QAbstractTableModel):
    def __init__(self, rows: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = rows or []

    # ---- Qt 模型 ----
    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[s]
        return None

    def flags(self, idx: QModelIndex) -> Qt.ItemFlags:
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if idx.column() == COL_HEX:
            return f        # HEX 列：整格点击切换（主窗口 clicked 处理），无原生勾选框
        f |= Qt.ItemIsEditable
        return f

    def data(self, idx: QModelIndex, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        r = self.rows[idx.row()]
        c = idx.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            if c == COL_SEND:
                return r.get("note") or "发送"
            if c == COL_TEXT:
                return r["text"]
            if c == COL_ORDER:
                return str(r.get("order", 0))     # 字符串：编辑器为纯文本框（无上下箭头）
            if c == COL_INTERVAL:
                return str(r.get("interval", 1000))
            return None
        if role == Qt.CheckStateRole and c == COL_HEX:
            return Qt.Checked if r["hex"] else Qt.Unchecked
        return None

    def setData(self, idx: QModelIndex, value, role=Qt.EditRole) -> bool:
        if not idx.isValid():
            return False
        r = self.rows[idx.row()]
        c = idx.column()
        if c == COL_SEND and role == Qt.EditRole:
            r["note"] = str(value)
        elif c == COL_HEX and role == Qt.CheckStateRole:
            r["hex"] = value == Qt.Checked
        elif c == COL_TEXT and role == Qt.EditRole:
            r["text"] = str(value)
        elif c == COL_ORDER and role == Qt.EditRole:
            try:
                r["order"] = min(max(int(str(value)), 0), ORDER_MAX)
            except ValueError:
                return False
        elif c == COL_INTERVAL and role == Qt.EditRole:
            try:
                r["interval"] = min(max(int(str(value)), 0), INTERVAL_MAX)
            except ValueError:
                return False
        else:
            return False
        self.dataChanged.emit(idx, idx)
        return True

    # ---- 行管理 ----
    def add_row(self, row: dict | None = None) -> None:
        if len(self.rows) >= 100:
            return
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows))
        self.rows.append(row or {"note": "", "hex": False, "text": "",
                                 "order": 0, "interval": 1000})
        self.endInsertRows()

    def remove_row(self, r: int) -> None:
        if 0 <= r < len(self.rows):
            self.beginRemoveRows(QModelIndex(), r, r)
            del self.rows[r]
            self.endRemoveRows()


class MultiSendLoop(QObject):
    """循环发送调度器：每轮按"顺序"升序发送 order>0 的条目，
    每条发送后等待该条的延时；rows_fn 实时取当前表格内容。"""
    send_row = Signal(dict)
    stopped = Signal()          # 无可发送条目（order>0）时自行停止，供主窗口同步复选框

    def __init__(self, rows_fn, parent=None):
        super().__init__(parent)
        self.rows_fn = rows_fn            # -> list[dict]
        self._idx = 0
        self._active = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._send_next)

    def start_loop(self) -> bool:
        """开始循环。没有可发送条目（无 order>0）时返回 False 且不启动。"""
        self._idx = 0
        self._active = True
        return self._send_next()

    def stop(self) -> None:
        self._active = False
        self._timer.stop()

    def is_active(self) -> bool:
        return self._active

    def _send_next(self) -> bool:
        if not self._active:
            return False
        seq = sorted((r for r in self.rows_fn() if int(r.get("order") or 0) > 0),
                     key=lambda r: int(r.get("order") or 0))
        if not seq:
            was_active = self._active
            self._active = False
            if was_active:
                self.stopped.emit()      # 运行中条目被清空/全部 order=0
            return False
        raw = seq[self._idx % len(seq)]
        self._idx = (self._idx + 1) % len(seq)
        # 兜底限幅副本：order/interval 可能来自非 Settings 来源（手写行、外部表），
        # emit(dict) 按 QVariantMap 转换、QTimer.start 按 __int64 取值，都容不下超大整数
        row = dict(raw)
        row["order"] = min(max(int(row.get("order") or 0), 0), ORDER_MAX)
        row["interval"] = min(max(int(row.get("interval") or 0), 0), INTERVAL_MAX)
        self.send_row.emit(row)
        self._timer.start(max(1, int(row["interval"])))
        return True