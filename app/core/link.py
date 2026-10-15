# -*- coding: utf-8 -*-
"""连接抽象基类：信号驱动，主线程事件循环。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Link(QObject):
    rx = Signal(bytes)              # 收到数据
    tx_done = Signal(int)           # 已发出 n 字节
    state_changed = Signal(str)     # 'open' | 'closed' | 'reconnecting' | 'error: ...'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.auto_reconnect = False
        self.reconnect_ms = 2000

    # 子类实现
    def open(self) -> bool: ...
    def close(self) -> None: ...
    def write(self, data: bytes) -> int: ...
    def is_open(self) -> bool: ...
    def describe(self) -> str: ...      # 状态栏参数描述
