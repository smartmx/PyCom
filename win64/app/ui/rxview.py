# -*- coding: utf-8 -*-
"""自绘接收视图：只渲染可视区，128MB 缓冲不卡。

- 时间戳行内前缀（对齐原版：正文顶格，时间戳仅前缀于包首行）+ 按方向着色（发送=tx 色，接收=rx 色）
- 搜索命中黄底高亮（match 结构: {line_idx: [(col, len), ...]}）
- 行内折行（按可用宽度），滚动按行
- 30fps 合并刷新；底部粘滞（在底部时新数据自动跟随）
- 鼠标选择复制：拖选正文 → Ctrl+C / Ctrl+Insert / 右键「复制所选」（选区按
  逻辑行列坐标记录；右键可切换"复制/保存含时间戳和收发标记"——打开时行首
  注记随行带出，关闭时只复制正文）
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QKeySequence,
                           QPalette, QPainter, QPen)
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QMenu

from app.core.display import LineBuilder, dir_marker
from app.core.rxbuf import RxBuffer
from app.paths import base_dir


class RxView(QAbstractScrollArea):
    cleared = Signal()          # 清屏时通知（日志写入游标需重置）
    pause_auto_scroll = Signal()  # 用户回看（滚轮上翻/拖选/拖滚动条）：请求取消自动滚屏勾选

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
        self.copy_with_anno = True              # 复制含时间戳和收发标记（右键菜单切换，随参数持久化）
        self.save_with_anno = True              # 保存文本含时间戳和收发标记（同上）
        self._sel_anchor = None                 # 选区锚点 (绝对行号, 逻辑列)
        self._sel_focus = None                  # 选区焦点（拖动端）
        self._selecting = False                 # 左键拖拽进行中
        self._sel_cursor_x = 0                  # 拖选中光标位置（自动滚屏用）
        self._sel_cursor_y = 0
        self._autosel_timer = QTimer(self)
        self._autosel_timer.setInterval(16)     # ~60fps，滚动更顺滑
        self._autosel_timer.timeout.connect(self._sel_autoscroll)
        self._autosel_t = 0.0                   # 上次滚屏时刻（perf_counter 秒）
        self._autosel_accum = 0.0               # 亚像素滚屏余量
        self.setViewportMargins(4, 2, 4, 2)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.verticalScrollBar().sliderReleased.connect(self._on_slider_released)
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

    def plain_text(self, with_anno: bool | None = None) -> str:
        """全部文本。with_anno=None 跟随显示设置（分包勾选时包首行为
        [时间戳前缀]方向标识，续行只有文本）；True/False 显式指定——
        复制/保存选项与显示无关（前缀一直随行记录，见 display.set_split）。"""
        anno = self.builder.split if with_anno is None else bool(with_anno)
        if not anno:
            return "\n".join(l.text for l in self.builder.visible())
        return "\n".join(l.prefix + (dir_marker(l.dir) if l.prefix else "") + l.text
                         for l in self.builder.visible())

    # ---- 鼠标选择复制 ----
    def has_selection(self) -> bool:
        """存在非空选区（锚点与焦点不重合）。"""
        return (self._sel_anchor is not None and self._sel_focus is not None
                and self._sel_anchor != self._sel_focus)

    def _selected_text(self) -> str:
        """选区文本：按逻辑行取 text 片段，跨行以换行连接。列坐标从正文起始
        计算；勾选"复制含时间戳和收发标记"时，从行首（列 0）起被选中的行
        带出其 [时间戳] 前缀与收发标识——等价"复制屏幕上看得见的行首注记"。
        半途起选（列 > 0）的行不带注记（用户没选到行首）。"""
        if not self.has_selection():
            return ""
        anno = self.copy_with_anno
        (i0, c0), (i1, c1) = sorted((self._sel_anchor, self._sel_focus))
        parts = []
        for i in range(i0, i1 + 1):
            line = self.builder.line_at(i)
            if line is None:
                continue
            s = c0 if i == i0 else 0
            e = c1 if i == i1 else len(line.text)
            seg = line.text[s:max(s, e)]
            if anno and s == 0 and e > 0 and line.prefix:
                seg = line.prefix + dir_marker(line.dir) + seg
            parts.append(seg)
        return "\n".join(parts)

    def copy_selection(self):
        if self.has_selection():
            QApplication.clipboard().setText(self._selected_text())

    _SEL_MARGIN = 24                       # 拖选距上下边 24px 内开始自动滚屏
    _SEL_SPEED = 14.0                      # 越界每 1px 追加的速度（px/s），基准 60

    def _sel_autoscroll(self):
        """选区拖拽的边缘自动滚屏：光标越出/贴近上下边时按深度加速滚动，
        并按当前光标位置更新选区焦点——可跨屏选择屏幕外内容。
        16ms 细分定时 + 时间步长 + 亚像素余量累积，滚动连续不顿挫。"""
        if not self._selecting:
            self._autosel_timer.stop()
            return
        rect = self.viewport().rect()
        margin = self._SEL_MARGIN
        sb = self.verticalScrollBar()
        y = self._sel_cursor_y
        now = time.perf_counter()
        dt = 1 / 60 if not self._autosel_t else min(0.1, now - self._autosel_t)
        self._autosel_t = now
        if y < rect.top() + margin:
            depth = rect.top() + margin - y
            sign = -1
        elif y > rect.bottom() - margin:
            depth = y - (rect.bottom() - margin)
            sign = 1
        else:
            self._autosel_t = 0.0
            return
        self._autosel_accum += (60.0 + depth * self._SEL_SPEED) * dt
        move = int(self._autosel_accum)
        if move:
            self._autosel_accum -= move
            sb.setValue(max(sb.minimum(),
                            min(sb.maximum(), sb.value() + sign * move)))
        self._sel_focus = self._pos_to_point(
            QPoint(self._sel_cursor_x,
                   max(rect.top() + 2, min(y, rect.bottom()))))
        self.viewport().update()

    def _pos_to_point(self, pos):
        """视口坐标 → (绝对行号, 逻辑列)。与 paintEvent 完全同源的几何：
        行号按上方各行实际占用的视觉行数（折行）回推，列含折行段偏移
        （col = 段号×每段列数 + 段内列）；行首时间戳/方向标识区视作列 0。"""
        lh = self._line_h()
        rect = self.viewport().rect()
        top = self.verticalScrollBar().value()
        yoff = pos.y() - (rect.top() + 2)
        cols_px = max(8, self.viewport().width() - 8)
        # 自首个可见行向下累计视觉行，定位 y 落在哪一逻辑行的第几折行段
        idx, n, row_y = top, self.builder.line_count(), 0
        while idx < n:
            line = self.builder.line_at(idx)
            if line is None:
                break
            fm = self._fms[line.dir if line.dir in self._fonts else "rx"]
            cols = max(8, cols_px // max(1, fm.horizontalAdvance("M")))
            n_rows = (len(line.text) + cols - 1) // cols or 1
            if yoff < row_y + n_rows * lh:
                break
            row_y += n_rows * lh
            idx += 1
        idx = max(self.builder.base, min(idx, n - 1))
        line = self.builder.line_at(idx)
        if line is None:
            return (idx, 0)
        fm = self._fms[line.dir if line.dir in self._fonts else "rx"]
        cw = max(1, fm.horizontalAdvance("M"))
        cols = max(8, cols_px // cw)
        n_rows = (len(line.text) + cols - 1) // cols or 1
        r = max(0, min((yoff - row_y) // lh, n_rows - 1))
        base = 4
        if r == 0 and self.builder.split and line.prefix:
            base += fm.horizontalAdvance(line.prefix)
            base += fm.horizontalAdvance(dir_marker(line.dir))
        col = r * cols + max(0, (pos.x() - base) // cw)
        return (idx, max(0, min(col, len(line.text))))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            pt = ev.position().toPoint()
            self._sel_cursor_x, self._sel_cursor_y = pt.x(), pt.y()
            self._sel_anchor = self._sel_focus = self._pos_to_point(pt)
            self._selecting = True
            self._autosel_timer.stop()
            self.viewport().update()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._selecting and ev.buttons() & Qt.LeftButton:
            pt = ev.position().toPoint()
            self._sel_cursor_x, self._sel_cursor_y = pt.x(), pt.y()
            self._sel_focus = self._pos_to_point(pt)
            if self._sel_focus != self._sel_anchor:
                self._pause_auto_scroll()   # 拖拽形成选区＝回看，取消自动滚屏勾选
            rect = self.viewport().rect()
            margin = self._SEL_MARGIN
            if pt.y() < rect.top() + margin or pt.y() > rect.bottom() - margin:
                if not self._autosel_timer.isActive():
                    self._autosel_t = 0.0
                    self._autosel_accum = 0.0
                self._autosel_timer.start()   # 拖到上下边缘：自动滚屏续选
            else:
                self._autosel_timer.stop()
                self._autosel_t = 0.0
            self.viewport().update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._selecting = False
            self._autosel_timer.stop()
            self.viewport().update()
        super().mouseReleaseEvent(ev)

    def keyPressEvent(self, ev):
        if self.has_selection() and (ev.matches(QKeySequence.Copy)
                                     or (ev.key() == Qt.Key_Insert
                                         and ev.modifiers() & Qt.ControlModifier)):
            self.copy_selection()
            return
        super().keyPressEvent(ev)

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
        self._stick = self._auto_scroll and v >= self.verticalScrollBar().maximum()

    def _on_slider_released(self):
        """拖动滚动条松手：不在底部＝回看历史 → 取消自动滚屏勾选；拖到底松手
        ＝回到底部跟随 → 勾选保持（与"勾选=跟随状态"一致，方向无关）。"""
        sb = self.verticalScrollBar()
        if sb.value() < sb.maximum():
            self._pause_auto_scroll()

    def _pause_auto_scroll(self):
        """用户主动回看（滚轮上翻/拖拽选择/拖动滚动条）：请求主窗口取消勾选。
        取消后新数据只入缓冲、视图停住，可安心选择复制；重新勾选立即跳回底部
        （set_auto_scroll(True)），无需等新数据。"""
        if self._auto_scroll:
            self.pause_auto_scroll.emit()

    def wheelEvent(self, ev):
        if ev.angleDelta().y() > 0:     # 滚轮上翻查看历史
            self._pause_auto_scroll()
        super().wheelEvent(ev)

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
        width = self.viewport().width() - 8
        row_y = self.viewport().rect().top() + 2   # 行首 y：按上方折行数逐行下推
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
            # 折行：只构造当前视口需要的行段（超长行不再整行展开）；
            # 折行段占满本行实际的视觉行数后，下一逻辑行才接着往下排
            n_rows = (len(line.text) + cols - 1) // cols or 1
            yy0 = row_y
            if yy0 > self.viewport().rect().bottom():
                break                    # 后续行只会更靠下
            for r in range(n_rows):
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
                if self.has_selection():
                    # 选区高亮：与搜索命中同样按等宽字格映射到折行段
                    (i0, c0), (i1, c1) = sorted((self._sel_anchor,
                                                 self._sel_focus))
                    if i0 <= i <= i1:
                        s_sel = c0 if i == i0 else 0
                        e_sel = c1 if i == i1 else len(line.text)
                        a0 = max(s_sel, s0)
                        a1 = min(e_sel, s0 + len(seg))
                        if a1 > a0:
                            p.fillRect(QRect(base + (a0 - s0) * cw, yy,
                                             (a1 - a0) * cw, lh - 1),
                                       self.viewport().palette().color(
                                           QPalette.Highlight))
                p.setPen(QPen(color))
                p.drawText(base, yy + asc, seg)
            row_y = yy0 + n_rows * lh        # 下一逻辑行从本行实际占用的视觉行后接着排

    # ---- 右键菜单 ----
    _MARK_TAB = "\t\u3000●"        # 勾选标记=行尾 ●（同主窗口菜单约定，见 docs/04 §3.2）

    def _build_menu(self) -> QMenu:
        """构建右键菜单（每次弹出重建；勾选项按当前状态写 ● 标记）。"""
        m = QMenu(self)
        self._act_sel = m.addAction("复制所选")
        self._act_sel.setEnabled(self.has_selection())
        self._act_copy = m.addAction("复制全部文本")
        m.addSeparator()
        self._act_save = m.addAction("保存接收字节到文件…")
        m.addSeparator()
        # 复制/保存是否带出时间戳与收发标识：与显示设置独立，随参数持久化
        self._act_canno = m.addAction(
            "复制含时间戳和收发标记" + (self._MARK_TAB if self.copy_with_anno else ""))
        self._act_sanno = m.addAction(
            "保存含时间戳和收发标记" + (self._MARK_TAB if self.save_with_anno else ""))
        m.addSeparator()
        self._act_clear = m.addAction("清空显示")
        return m

    def _menu_action(self, act) -> None:
        """执行右键菜单选中项（act 来自 _build_menu；None=菜单被取消）。"""
        if act is self._act_sel:
            self.copy_selection()
        elif act is self._act_copy:
            QApplication.clipboard().setText(
                self.plain_text(self.copy_with_anno))
        elif act is self._act_canno:
            self.copy_with_anno = not self.copy_with_anno
        elif act is self._act_sanno:
            self.save_with_anno = not self.save_with_anno
        elif act is self._act_save:
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
        elif act is self._act_clear:
            self.clear_all()

    def _menu(self, pos):
        m = self._build_menu()
        self._menu_action(m.exec(self.viewport().mapToGlobal(pos)))

    def clear_all(self):
        if self.pre_clear_hook:
            self.pre_clear_hook()        # 清屏前先落盘（含未收尾行）
        self.builder.clear()
        self.buf.clear()
        self._matches = {}
        self._sel_anchor = self._sel_focus = None
        self._selecting = False
        self._update_range()
        self.viewport().update()
        self.cleared.emit()
