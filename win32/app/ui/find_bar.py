# -*- coding: utf-8 -*-
"""查找条：文本/HEX 模式搜索接收区，命中计数与上/下一个跳转。"""
from __future__ import annotations

import re

import bisect

from PySide2.QtCore import Qt, QTimer, Signal
from PySide2.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QWidget)

from app.core.hexutil import try_parse_hex


class FindBar(QWidget):
    closed = Signal()

    def __init__(self, on_matches, on_jump, parent=None):
        """on_matches(matches:dict) / on_jump(delta:int) 由主窗口注入。"""
        super().__init__(parent)
        self._on_matches = on_matches
        self._on_jump = on_jump
        self._hits: list[tuple[int, int, int]] = []     # (绝对行号, col, len)
        self._cur = -1
        self._debounce = QTimer(self)     # 输入防抖：停止输入 250ms 后才全量扫描
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._search)
        self._rescan = QTimer(self)       # 数据/裁剪/模式变化后的节流重算（合并高频接收）
        self._rescan.setSingleShot(True)
        self._rescan.setInterval(500)
        self._rescan.timeout.connect(self._search)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("查找接收区内容…（文本或 HEX，如 EF 01）")
        self.edit.textChanged.connect(self._debounce.start)
        self.edit.returnPressed.connect(self._on_enter)
        self.btn_prev = QPushButton("上一个")
        self.btn_next = QPushButton("下一个")
        self.btn_prev.clicked.connect(self._jump_prev)
        self.btn_next.clicked.connect(self._jump_next)
        self.cb_case = QCheckBox("区分大小写")
        self.cb_hex = QCheckBox("HEX")
        self.cb_hex.toggled.connect(self._search)
        self.cb_case.toggled.connect(self._search)
        self.label = QLabel("0 个结果")
        btn_close = QPushButton("×")
        btn_close.setFixedWidth(28)
        btn_close.clicked.connect(self.hide)
        for w in (self.edit, self.btn_prev, self.btn_next, self.cb_case,
                  self.cb_hex, self.label, btn_close):
            lay.addWidget(w)
        lay.setStretch(0, 1)
        self.hide()

    def activate(self):
        self.show()
        self.edit.setFocus()
        self.edit.selectAll()
        # 隐藏期间的数据/裁剪变化不会触发重算：重开时补一次节流重扫
        if self.edit.text():
            self.notify_data_changed()

    def hide(self):
        self._debounce.stop()
        self._rescan.stop()
        super().hide()

    def _pending(self) -> bool:
        """输入防抖或重算节流尚未落定。"""
        return self._debounce.isActive() or self._rescan.isActive()

    def _search_now(self):
        """立即完成当前查询（取消防抖/重算定时器），返回是否有命中。"""
        self._debounce.stop()
        self._rescan.stop()
        self._search()
        return bool(self._hits)

    def _on_enter(self):
        if self._hits or self._pending():
            self._jump(1)                    # _jump 内部会先落定未完成的查询
        else:
            self._search_now()

    def _jump_next(self):
        self._jump(1)

    def _jump_prev(self):
        self._jump(-1)

    def notify_data_changed(self):
        """接收新数据/裁剪/显示模式变化后节流重算（仅查找条可见且有查询时）。"""
        if self.isVisible() and self.edit.text() and not self._debounce.isActive():
            self._rescan.start()

    def _search(self):
        text = self.edit.text()
        matches: dict[int, list[tuple[int, int]]] = {}
        hits = []
        builder = self._builder() if text else None
        if builder is not None:
            # 收集参与搜索的行文本；行号一律换算成 base+k 绝对值——
            # RxView 的高亮/跳转都按绝对行号索引，裁剪后（base>0）
            # 写相对下标会落到偏移行
            texts = []
            for i in range(builder.base, builder.line_count()):
                l = builder.line_at(i)
                if l is not None:
                    texts.append(l.text)
            # 统一搜索域：拼接解码文本，行分隔符参与匹配（可跨行）
            # - 文本模式：直接搜内容字符（大小写按勾选）
            # - HEX 搜索 + HEX 显示：搜带空格的 HEX 文本（跨 16 字节行边界的
            #   "0F 10" 也能命中）；HEX 搜索 + ASCII 显示：HEX 字节按显示编码
            #   解码为内容字符再搜（字符列精确，多字节字符不错位）
            if self.cb_hex.isChecked():
                needle_b = try_parse_hex(text)
                if not needle_b:
                    self.label.setText("HEX 格式错误")
                    self._hits = []
                    self._cur = -1
                    self._on_matches({})
                    return
                if builder.hex_mode:
                    pattern = " ".join("%02X" % b for b in needle_b)
                    flags = 0                          # 字节精确：大小写敏感
                    sep = " "
                else:
                    try:
                        pattern = needle_b.decode(builder.encoding, "replace")
                    except LookupError:
                        pattern = needle_b.decode("gbk", "replace")
                    flags = 0
                    sep = "\n"
            else:
                pattern = text
                flags = 0 if self.cb_case.isChecked() else re.IGNORECASE
                sep = "\n"
            concat = sep.join(texts)
            starts = []
            off = 0
            for t in texts:
                starts.append(off)
                off += len(t) + 1                  # +1 为行分隔符
            abs0 = builder.base
            for m in re.finditer(re.escape(pattern), concat, flags):
                pos, end = m.start(), m.end()
                k = max(0, bisect.bisect_right(starts, pos) - 1)
                first = None                       # 逻辑匹配锚点=首段（跳转用）
                while pos < end and k < len(texts):
                    line_end = starts[k] + len(texts[k])
                    seg_end = min(end, line_end)
                    col = pos - starts[k]
                    mlen = seg_end - pos
                    if mlen > 0:
                        matches.setdefault(abs0 + k, []).append((col, mlen))
                        if first is None:
                            first = (abs0 + k, col, mlen)
                    pos = seg_end + 1              # 跳过行分隔符
                    k += 1
                if first is not None:
                    hits.append(first)             # 跨行匹配只按 1 个结果计
        self._hits = hits
        self._cur = -1
        self.label.setText("%d 个结果" % len(hits))
        self._on_matches(matches)
        if hits:
            self._jump(1)

    def _jump(self, delta: int):
        if self._pending():
            # 输入防抖/重算节流未落定：先立即完成当前查询；
            # _search 成功后已自动跳到首个命中，直接返回
            self._search_now()
            return
        if not self._hits:
            return
        builder = self._builder()
        if builder is not None:
            b = builder.base
            # 命中行可能已被裁剪/清空：失效则按当前数据重算；
            # _search 成功后已自动跳到首个新命中，直接返回避免二次推进
            if any(ln < b or ln >= builder.line_count() for ln, _, _ in self._hits):
                self._search_now()
                return
        self._cur = (self._cur + delta) % len(self._hits)
        line_idx = self._hits[self._cur][0]
        self.label.setText("%d/%d" % (self._cur + 1, len(self._hits)))
        self._on_jump(line_idx)

    def set_builder_provider(self, fn):
        self._builder = fn
