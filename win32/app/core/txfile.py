# -*- coding: utf-8 -*-
"""发送文件：256 字节分块流式读取发送，可配置每块延时（对齐原版 N1051 选项）。

部分写入处理：link.write 返回 n < len(chunk) 时，未接受的尾部存入 _pend，
下一 tick 优先补发 pending；只有 pending 清空后才从文件读取下一块。
finished 仅在 _pos == 文件大小且无 pending 余量时发出。"""
from __future__ import annotations

import os

from PySide2.QtCore import QObject, QTimer, Signal

CHUNK = 256


class FileSender(QObject):
    progress = Signal(int, int)        # done, total
    finished = Signal()
    failed = Signal(str)

    def __init__(self, link, parent=None):
        super().__init__(parent)
        self.link = link
        self._f = None                 # 打开的文件句柄（流式读取，不整文件入内存）
        self._total = 0
        self._pos = 0                  # 已读取/已发送字节数
        self._pend = b""               # 部分写入时未接受的尾部（先补发再读新块）
        self._delay_ms = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._send_chunk)

    def start(self, path: str, delay_ms: int = 0) -> bool:
        self.stop()                    # 兜底：清理上一次可能残留的句柄/定时器/余量
        if self.link is None or not self.link.is_open():
            self.failed.emit("连接未打开")
            return False
        try:
            self._f = open(path, "rb")
            self._total = os.fstat(self._f.fileno()).st_size
        except OSError as ex:
            self._f = None
            self.failed.emit("读取失败: %s" % ex)
            return False
        self._pos = 0
        self._pend = b""
        self._delay_ms = max(0, delay_ms)
        self.progress.emit(0, self._total)
        if self._delay_ms == 0:
            self._timer.start(0)       # 连续发送（事件循环节流）
        else:
            self._timer.start(self._delay_ms)
        return True

    def stop(self) -> None:
        self._timer.stop()
        self._close_file()

    def is_active(self) -> bool:
        return self._timer.isActive()

    def _close_file(self) -> None:
        if self._f is not None:
            try:
                self._f.close()
            except OSError:
                pass
            self._f = None

    def _send_chunk(self) -> None:
        if self.link is None or not self.link.is_open():
            self._timer.stop()
            self._close_file()
            self.failed.emit("连接已断开")
            return
        if not self._pend:
            # pending 清空后才读取下一块（文件游标推进与已发送量一致）
            try:
                self._pend = self._f.read(CHUNK) if self._f is not None else b""
            except (OSError, ValueError):
                self._timer.stop()
                self._close_file()
                self.failed.emit("读取失败")
                return
        if not self._pend:
            self._timer.stop()
            self._close_file()
            self.progress.emit(self._total, self._total)
            self.finished.emit()
            return
        n = self.link.write(self._pend)
        if n <= 0:
            self._timer.stop()
            self._close_file()
            self.failed.emit("写入失败（无连接或对端未接收）")
            return
        self._pend = self._pend[n:]
        self._pos += n
        self.progress.emit(min(self._pos, self._total), self._total)
