# -*- coding: utf-8 -*-
"""自绘接收视图：只渲染可视区，128MB 缓冲不卡。

- 时间戳行内前缀（对齐原版：正文顶格，时间戳仅前缀于包首行）+ 按方向着色（发送=tx 色，接收=rx 色）
- 搜索命中黄底高亮（match 结构: {line_idx: [(col, len), ...]}）
- 行内折行（按可用宽度），滚动按行
- 30fps 合并刷新；底部粘滞（在底部时新数据自动跟随）
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QMenu

from app.core.display import LineBuilder, dir_marker
from app.core.rxbuf import RxBuffer
from app.paths import base_dir


class RxView(QAbstractScrollArea):
    cleared = Signal()          # 清屏时通知（日志写入游标需重置）

    def __init__(self, builder: LineBuilder, buf: RxBuffer, parent=None):
        super().__init__(parent)
        self.builder = builder
        self.buf = buf
        self._colors = {}
        self._ts_override: QColor | None = None   # 用户设置的时间戳颜色（空=随主题）
        self._fonts = {"rx": QFont("宋体", 10), "tx": QFont("宋体", 10)}
        self._fms = {k: QFontMetrics(f) for k, f in self._fonts.items()}
        self._font_colors = {"rx": None, "tx": None}   # 用户自定义颜色（空=随主题方向色）
        self._matches: dict[int, list[tuple[int, int]]] = {}
        self.pre_clear_hook = None              # 清屏前回调（主窗口落盘日志）；独立使用时安全
        self.save_bin_hook = None               # 右键保存原始字节回调（主窗口统一落盘+异常提示）
        self._auto_scroll = True                # 自动滚屏开关（主窗口勾选项）
        self._stick = True
        self.setViewportMargins(4, 2, 4, 2)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.viewport().update()

    # ---- 外部接口 ----
    def apply_theme(self, colors: dict, bg: str | None = None, fg: str | None = None):
        self._colors = colors
        bgc = QColor(bg) if bg else colors["bg"]
        fgc = QColor(fg) if fg else colors["fg"]
        self._bg, self._fg = bgc, fgc
        pal = self.viewport().palette()
        pal.setColor(self.viewport().backgroundRole(), bgc)
        self.viewport().setPalette(pal)
        self.viewport().update()

    def set_text_fonts(self, send: dict, recv: dict):
        """发送/接收各自字体：family/size/bold/underline/color(可空=随主题方向色)。"""
        for key, c in (("tx", send), ("rx", recv)):
            f = QFont(c.get("family") or "宋体", int(c.get("size") or 10))
            f.setBold(bool(c.get("bold")))
            f.setUnderline(bool(c.get("underline")))
            self._fonts[key] = f
            color = c.get("color")
            self._font_colors[key] = QColor(color) if color else None
        self._fms = {k: QFontMetrics(f) for k, f in self._fonts.items()}
        self.viewport().update()

    def set_ts_color(self, color: str | None):
        """设置时间戳字体颜色；None 恢复随主题。"""
        self._ts_override = QColor(color) if color else None
        self.viewport().update()

    def set_matches(self, matches: dict[int, list[tuple[int, int]]]):
        self._matches = matches
        self.viewport().update()

    def scroll_to_line(self, idx: int):
        sb = self.verticalScrollBar()
        lo = self.builder.base
        sb.setValue(max(lo, min(idx - 2, sb.maximum())))

    def plain_text(self) -> str:
        # 与屏幕一致：分包显示勾选时包首行为 [时间戳前缀]方向标识，
        # 续行只有文本；未勾选时为纯文本（对齐 SSCOM 参照图）
        if not self.builder.split:
            return "\n".join(l.text for l in self.builder.visible())
        return "\n".join(l.prefix + (dir_marker(l.dir) if l.prefix else "") + l.text
                         for l in self.builder.visible())

    # ---- 滚动/尺寸 ----
    def _line_h(self) -> int:
        # 行高统一取两种字体中较高者，保证网格对齐
        return max(self._fms["rx"].height(), self._fms["tx"].height()) + 2

    def _vis_lines(self) -> int:
        return max(1, self.viewport().height() // self._line_h())

    def set_auto_scroll(self, on: bool):
        """自动滚屏：关闭后新数据只入缓冲，视图停在当前位置不再下坠。"""
        self._auto_scroll = bool(on)
        if self._auto_scroll:
            self._stick = True                  # 重新开启立即跳回最新数据
            sb = self.verticalScrollBar()
            sb.setValue(sb.maximum())
        else:
            self._stick = False

    def _on_scroll(self, v):
        sb = self.verticalScrollBar()
        self._stick = self._auto_scroll and v >= sb.maximum()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_range()

    def _update_range(self):
        sb = self.verticalScrollBar()
        lo = self.builder.base               # 已裁剪行数：滚动下限从 base 起
        total = self.builder.line_count()
        sb.setRange(lo, max(lo, total - self._vis_lines()))
        if self._stick:
            sb.setValue(sb.maximum())

    def _tick(self):
        self.builder.maybe_flush(time.time())
        self._update_range()
        self.viewport().update()      # 合并刷新（open 行可能变化）

    # ---- 绘制 ----
    def paintEvent(self, ev):
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), self._bg)
        lh = self._line_h()
        top = self.verticalScrollBar().value()
        y = self.viewport().rect().top() + 2
        width = self.viewport().width() - 8
        for i in range(top, min(self.builder.line_count(), top + self._vis_lines() + 2)):
            line = self.builder.line_at(i)
            if line is None:
                if i < self.builder.line_count():
                    continue             # 行号落在已裁剪区间（base 之前）
                break
            key = line.dir if line.dir in self._fonts else "rx"
            font = self._fonts[key]
            fm = self._fms[key]
            asc = fm.ascent()   # drawText 的 y 是基线，须加 ascent 才能让行顶对齐 yy
            cw = max(1, fm.horizontalAdvance("M"))
            cols = max(8, width // cw)
            p.setFont(font)
            color = self._font_colors.get(key) or self._colors.get(
                "tx" if key == "tx" else "rx", self._fg)
            ts_color = self._ts_override or self._colors.get("ts", self._fg)
            hl = self._matches.get(i)
            # 折行：只构造当前视口需要的行段（超长行不再整行展开）
            n_rows = (len(line.text) + cols - 1) // cols or 1
            yy0 = y + (i - top) * lh
            r0 = 0
            if yy0 < self.viewport().rect().top():
                r0 = (self.viewport().rect().top() - yy0) // lh
            r1 = min(n_rows, r0 + self._vis_lines() + 2)
            for r in range(r0, r1):
                s0 = r * cols
                seg = line.text[s0:s0 + cols]
                yy = yy0 + r * lh
                if yy > self.viewport().rect().bottom():
                    break
                # 方向标识（收◆/发◇，对齐 SSCOM 参照图）：只在勾选"加时间戳和
                # 分包显示"时显示，且仅标在包首行（带时间戳前缀那行），续行不带
                anno = self.builder.split
                marker = (dir_marker(line.dir)
                          if (r == 0 and anno and line.prefix) else "")
                if r == 0 and anno and line.prefix:
                    p.setPen(QPen(ts_color))
                    p.drawText(4, yy + asc, line.prefix)
                    base = 4 + fm.horizontalAdvance(line.prefix)
                else:
                    base = 4
                if marker:
                    p.setPen(QPen(color))
                    p.drawText(base, yy + asc, marker)
                    base += fm.horizontalAdvance(marker)
                if hl:
                    for col, mlen in hl:
                        # 命中可能跨折行段：逐段计算局部列
                        seg_start = s0
                        seg_end = s0 + len(seg)
                        if col + mlen <= seg_start or col >= seg_end:
                            continue
                        c0 = max(col, seg_start) - seg_start
                        c1 = min(col + mlen, seg_end) - seg_start
                        rect = QRect(base + c0 * cw, yy,
                                     (c1 - c0) * cw, lh - 1)
                        p.fillRect(rect, self._colors["hl_bg"])
                p.setPen(QPen(color))
                p.drawText(base, yy + asc, seg)

    # ---- 右键菜单 ----
    def _menu(self, pos):
        m = QMenu(self)
        a_copy = m.addAction("复制全部文本")
        a_save = m.addAction("保存接收字节到文件…")
        a_clear = m.addAction("清空显示")
        act = m.exec(self.viewport().mapToGlobal(pos))
        if act == a_copy:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.plain_text())
        elif act == a_save:
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getSaveFileName(self, "保存接收字节",
                                                    os.path.join(base_dir(), "received.bin"),
                                                    "Binary (*.bin)")
            # 落盘统一走主窗口钩子（集中异常处理与提示），视图本身不直接写文件
            if path:
                if self.save_bin_hook:
                    self.save_bin_hook(path)
                else:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(self, "保存接收字节",
                                        "保存不可用（未挂接主窗口）")
        elif act == a_clear:
            self.clear_all()

    def clear_all(self):
        if self.pre_clear_hook:
            self.pre_clear_hook()        # 清屏前先落盘（含未收尾行）
        self.builder.clear()
        self.buf.clear()
        self._matches = {}
        self._update_range()
        self.viewport().update()
        self.cleared.emit()
