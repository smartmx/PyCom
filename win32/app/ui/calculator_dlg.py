# -*- coding: utf-8 -*-
"""转换计算器：DEC / HEX / BIN / ASCII / float32/64（大小端）联动互转。

以 QWidget 形式内嵌在主窗口"扩展"区的转换计算器页签中。
"""
from __future__ import annotations

import struct

from PySide2.QtWidgets import (QGridLayout, QLabel, QLineEdit, QVBoxLayout,
                               QWidget)


class CalcField(QLineEdit):
    def __init__(self, name, convert, parent=None):
        super().__init__(parent)
        self.name = name
        self.convert = convert      # text -> int | None


class CalculatorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = False
        lay = QVBoxLayout(self)

        grid = QGridLayout()
        self.fields: dict[str, QLineEdit] = {}
        self._specs = [
            ("十进制 DEC", self._from_dec, self._to_dec),
            ("十六进制 HEX", self._from_hex, self._to_hex),
            ("二进制 BIN", self._from_bin, self._to_bin),
            ("ASCII 字符", self._from_ascii, self._to_ascii),
            ("float32 LE", self._from_f32le, self._to_f32le),
            ("float32 BE", self._from_f32be, self._to_f32be),
            ("float64 LE", self._from_f64le, self._to_f64le),
            ("float64 BE", self._from_f64be, self._to_f64be),
        ]
        for r, (name, from_fn, to_fn) in enumerate(self._specs):
            grid.addWidget(QLabel(name), r, 0)
            edit = QLineEdit()
            edit.textEdited.connect(lambda t, f=from_fn, n=name: self._recompute(f, n, t))
            self.fields[name] = edit
            grid.addWidget(edit, r, 1)
        lay.addLayout(grid)
        lay.addStretch(1)
        self.fields["十进制 DEC"].setText("65")
        # setText 不触发 textEdited：显式联动一次，打开即同步全部字段
        self._recompute(self._from_dec, "十进制 DEC", "65")

    # ---- 各字段 -> 整数值（float 视为其二进制位模式） ----
    def _from_dec(self, t):
        try:
            return int(t, 10)
        except ValueError:
            return None

    def _from_hex(self, t):
        try:
            return int(t.replace(" ", ""), 16)
        except ValueError:
            return None

    def _from_bin(self, t):
        try:
            return int(t.replace(" ", ""), 2)
        except ValueError:
            return None

    def _from_ascii(self, t):
        b = t.encode("latin-1", "ignore")
        return int.from_bytes(b, "big") if b else None

    def _from_f32le(self, t):
        try:
            return struct.unpack("<I", struct.pack("<f", float(t)))[0]
        except (ValueError, OverflowError):
            return None

    def _from_f32be(self, t):
        try:
            return struct.unpack(">I", struct.pack(">f", float(t)))[0]
        except (ValueError, OverflowError):
            return None

    def _from_f64le(self, t):
        try:
            return struct.unpack("<Q", struct.pack("<d", float(t)))[0]
        except ValueError:
            return None

    def _from_f64be(self, t):
        try:
            return struct.unpack(">Q", struct.pack(">d", float(t)))[0]
        except ValueError:
            return None

    # ---- 整数值 -> 各字段文本 ----
    def _to_dec(self, v):
        return str(v)

    def _to_hex(self, v):
        return "%X" % v

    def _to_bin(self, v):
        return bin(v)[2:]

    def _to_ascii(self, v):
        n = max(1, (v.bit_length() + 7) // 8)
        try:
            return v.to_bytes(n, "big").decode("latin-1")
        except OverflowError:
            return "…"

    def _to_f32le(self, v):
        try:
            return repr(struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0])
        except struct.error:
            return "…"

    def _to_f32be(self, v):
        try:
            return repr(struct.unpack(">f", struct.pack(">I", v & 0xFFFFFFFF))[0])
        except struct.error:
            return "…"

    def _to_f64le(self, v):
        try:
            return repr(struct.unpack("<d", struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF))[0])
        except struct.error:
            return "…"

    def _to_f64be(self, v):
        try:
            return repr(struct.unpack(">d", struct.pack(">Q", v & 0xFFFFFFFFFFFFFFFF))[0])
        except struct.error:
            return "…"

    def _recompute(self, from_fn, src_name, text):
        if self._lock:
            return
        v = from_fn(text)
        if v is None or v < 0:
            return
        self._lock = True
        for name, _f, to_fn in self._specs:
            if name != src_name:
                self.fields[name].setText(to_fn(v))
        self._lock = False
