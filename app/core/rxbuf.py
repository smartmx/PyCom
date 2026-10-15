# -*- coding: utf-8 -*-
"""接收环形字节缓冲：上限默认 128MB，溢出丢最旧。"""
from __future__ import annotations

DEFAULT_CAP = 128 * 1024 * 1024


class RxBuffer:
    def __init__(self, cap: int = DEFAULT_CAP):
        self._cap = max(1024, int(cap))
        self._buf = bytearray()
        self._base = 0          # _buf[0] 的绝对偏移
        self.total_rx = 0       # 累计接收字节数（不清屏）
        self.dropped = 0        # 因溢出被丢弃的字节数

    # ---- 基本属性 ----
    @property
    def cap(self) -> int:
        return self._cap

    def set_cap(self, cap: int) -> None:
        self._cap = max(1024, int(cap))
        self._trim()

    def __len__(self) -> int:
        return len(self._buf)

    @property
    def start_abs(self) -> int:
        """当前保留的最旧字节绝对偏移。"""
        return self._base

    @property
    def end_abs(self) -> int:
        return self._base + len(self._buf)

    # ---- 写入 ----
    def append(self, data: bytes) -> None:
        if not data:
            return
        self._buf += data
        self.total_rx += len(data)
        self._trim()

    def _trim(self) -> None:
        excess = len(self._buf) - self._cap
        if excess > 0:
            del self._buf[:excess]
            self._base += excess
            self.dropped += excess

    def clear(self) -> None:
        self._buf.clear()
        self._base = 0        # 显示层随之清空，偏移重置

    # ---- 读取 ----
    def read(self, start_abs: int, n: int) -> bytes:
        """按绝对偏移读（越界部分自动截断）。"""
        lo = max(start_abs, self._base)
        hi = min(start_abs + n, self.end_abs)
        if hi <= lo:
            return b""
        return bytes(self._buf[lo - self._base: hi - self._base])

    def tail(self, n: int) -> bytes:
        n = min(n, len(self._buf))
        return bytes(self._buf[len(self._buf) - n:])
