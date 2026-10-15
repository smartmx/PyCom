# -*- coding: utf-8 -*-
"""显示行构建器：把收发字节流增量格式化为可渲染的行记录。

特性：
- HEX / ASCII 两种模式（ASCII 支持任意编码，增量解码跨行保真）
- 分包显示：空闲超过 split_ms 切包，包首带时间戳前缀
- 持续流：UI tick 增量续写当前包（实时显示、pending 有上限），不丢实时性
- 方向交替立即切包；ASCII 模式下未结束的行跨包延续
- 显示行表有上限：超限裁剪最旧行（防 OOM），外部游标用 base 换算绝对行号
"""
from __future__ import annotations

import codecs
import time
from dataclasses import dataclass

HEX_ROW = 16          # HEX 模式每行字节数
_PEND_MAX = 1 << 20   # pending 缓冲上限（字节）：持续流下超限立即落行


@dataclass
class Line:
    dir: str            # 'tx' | 'rx'
    ts: float | None    # 包起始时间戳
    prefix: str         # "[HH:MM:SS.mmm] "（包首行才有）
    text: str           # 可见文本（不含换行）
    start_abs: int      # 本行首个字节在接收缓冲的绝对偏移（hex 行=16 字节跨度）
    nbytes: int         # 本行对应字节数
    written: int = 0    # 已转存到日志的字符数（0=整行未写）
    nl_done: bool = False   # 该行在日志中是否已写行终止符


def ts_prefix(ts: float) -> str:
    t = time.localtime(ts)
    ms = int((ts % 1) * 1000)
    return "[%02d:%02d:%02d.%03d] " % (t.tm_hour, t.tm_min, t.tm_sec, ms)


def dir_marker(direction: str) -> str:
    """行首方向标识（对齐 SSCOM）：接收"收←◆"，发送"发→◇"。"""
    return "收←◆" if direction == "rx" else "发→◇"


class LineBuilder:
    def __init__(self, encoding: str = "gbk", hex_mode: bool = False,
                 split: bool = True, split_ms: int = 20,
                 max_lines: int = 500_000):
        self.lines: list[Line] = []
        self.max_lines = max(1000, max_lines)
        self._base = 0                    # 已裁剪的旧行数（绝对行号 = _base + 下标）
        self.encoding = encoding
        self.hex_mode = hex_mode
        self.split = split
        self.split_ms = split_ms
        self._dec: dict[str, object] = {}
        self._pend_dir: str | None = None
        self._pend_buf = bytearray()
        self._pend_ts: float | None = None
        self._pend_abs = 0
        self._packet_stamped = False      # 当前包是否已打时间戳前缀
        self._cr_hold = False             # 跨 chunk 的 \r 挂起（等下一chunk判断\r\n）
        self._open_max_chars = 100_000    # 单行长度上限：超限强制折行（防内存/渲染爆炸）
        self._last_feed_ts: float | None = None
        self._open: Line | None = None        # ASCII 模式未收尾的行

    @property
    def base(self) -> int:
        """已裁剪的旧行数：外部游标（日志/搜索）应使用绝对行号。"""
        return self._base

    # ---- 配置变更 ----
    def set_encoding(self, enc: str) -> None:
        if enc == self.encoding:
            return
        # 旧编码里缓存的半截多字节序列先落成文字（替换字符），避免切码丢字节
        tail = ""
        for d in self._dec.values():
            try:
                tail += d.decode(b"", final=True)
            except Exception:
                pass
        self._dec.clear()
        self.encoding = enc
        if tail:
            self._emit_text(self._pend_dir or "rx", self._pend_ts, "", tail,
                            self._pend_abs)

    def set_hex_mode(self, on: bool) -> None:
        self.hex_mode = on
        self._close_open()
        self.flush()

    def set_split(self, on: bool, ms: int | None = None) -> None:
        self.split = on
        if ms is not None:
            self.split_ms = ms

    def clear(self) -> None:
        self.lines.clear()
        self._base = 0
        self._packet_stamped = False
        self._cr_hold = False
        self._dec.clear()
        self._pend_dir = None
        self._pend_buf.clear()
        self._pend_ts = None
        self._last_feed_ts = None
        self._open = None

    def visible(self) -> list[Line]:
        """行表 + 未收尾的活动行（搜索/导出用；渲染请用 line_count/line_at 零拷贝路径）。"""
        if self._open is not None and (self._open.text or self._open.prefix):
            return self.lines + [self._open]
        return self.lines

    def line_count(self) -> int:
        if self._open is not None and (self._open.text or self._open.prefix):
            return self._base + len(self.lines) + 1
        return self._base + len(self.lines)

    def line_at(self, i: int) -> Line | None:
        """按绝对行号取行（绝对行号 = 裁剪基址 + 列表下标）。"""
        j = i - self._base
        if 0 <= j < len(self.lines):
            return self.lines[j]
        if j == len(self.lines) and self._open is not None:
            return self._open
        return None

    def _marker(self, direction: str, has_prefix: bool) -> str:
        """方向标识：仅分包模式且该行是包首行（带时间戳前缀）时显示（对齐 SSCOM
        参照图：[15:06:06.856]收←◆首行 之后的续行不再带标识）。"""
        return dir_marker(direction) if (self.split and has_prefix) else ""

    def open_line_text(self) -> str | None:
        """未收尾行（[ts]收←◆文本，SSCOM 顺序；未勾选分包显示时为纯文本）。无则 None。"""
        if self._open is not None and (self._open.text or self._open.prefix):
            prefix = self._open.prefix if self.split else ""
            return prefix + self._marker(self._open.dir, bool(prefix)) + self._open.text
        return None

    def open_line_delta(self) -> str | None:
        """未收尾行"尚未转存"的尾部文本（首段含时间戳前缀与方向标识）；无则 None。
        只读：调用方成功写入日志后须再调 commit_open() 推进游标（失败可重试）。"""
        o = self._open
        if o is None:
            return None
        written = o.written
        if len(o.text) <= written:
            return None
        prefix = o.prefix if (written == 0 and self.split) else ""
        marker = self._marker(o.dir, bool(prefix)) if written == 0 else ""
        return prefix + marker + o.text[written:]

    def commit_open(self, nl_written: bool = False) -> None:
        """日志成功写入后推进未收尾行的已写游标；nl_written=True 表示行终止符
        也已写入（行完成后 poll 不再补换行）。"""
        if self._open is not None:
            self._open.written = len(self._open.text)
            if nl_written:
                self._open.nl_done = True

    # ---- 输入 ----
    def feed(self, direction: str, data: bytes, ts: float, abs_start: int) -> None:
        if not data:
            return
        if self._pend_dir is not None and direction != self._pend_dir:
            self.flush()
        if self._pend_dir is None:
            self._pend_dir = direction
            self._pend_ts = ts
            self._pend_abs = abs_start
            self._packet_stamped = False
        self._pend_buf += data
        self._last_feed_ts = ts
        if len(self._pend_buf) >= _PEND_MAX:
            self._consume_pending()      # pending 上限保护：立即落行

    def maybe_flush(self, now: float) -> bool:
        """由 UI 定时调用。持续流增量续写当前包（实时显示、pending 有上限）；
        空闲超时或超限时结算当前包。返回是否产生了新行。

        注意：持续流会把 pending 消费为空但保留包上下文，因此空闲判断不能以
        pending 非空为前提，否则包状态永远不结算、下一包丢失时间戳前缀。"""
        n0 = self.line_count()
        idle = (self._last_feed_ts is not None
                and now - self._last_feed_ts >= self.split_ms / 1000.0)
        if self._pend_buf:
            if idle or len(self._pend_buf) >= _PEND_MAX:
                self.flush()              # 空闲/超限：结算当前包
            else:
                self._consume_pending()   # 持续流：增量解码并续写当前行
        elif idle and (self._pend_dir is not None or self._cr_hold):
            # pending 已被持续流消费完：空闲时仍要结束包状态
            # （重置 pend 时间戳/戳记、结算挂起 \r），下一包才能重新打前缀
            self.flush()
        return self.line_count() != n0

    def flush(self) -> None:
        """结算当前包：解码剩余 pending（首个新行带包前缀），包结束。"""
        self._consume_pending()
        if self._cr_hold:                 # 包结束时挂起的 \r 落为换行
            self._cr_hold = False
            # 包内只有挂起 \r 时（如单个 CR 的空行/心跳包），本包时间戳尚未
            # 消费：此处补上，保证该空行同样带 [时间戳] 与方向标识（对齐
            # 有文本包的语义；否则该行既无前缀也无标识）
            prefix = ""
            if self.split and self._pend_ts is not None and not self._packet_stamped:
                prefix = ts_prefix(self._pend_ts)
                self._packet_stamped = True
            self._emit_text(self._pend_dir or "rx", self._pend_ts, prefix, "\n",
                            self._pend_abs)
        self._pend_dir = None
        self._pend_ts = None

    # ---- 内部 ----
    def _decoder(self, direction: str):
        d = self._dec.get(direction)
        if d is None:
            d = codecs.getincrementaldecoder(self.encoding)(errors="replace")
            self._dec[direction] = d
        return d

    def _consume_pending(self) -> None:
        """把 pending 解码并入行：包内首个新行打时间戳前缀（仅一次）；
        完成后清空 pending 但保留包上下文，供持续流继续续写。"""
        if not self._pend_buf:
            return
        direction = self._pend_dir or "rx"
        stamp = (self.split and self._pend_ts is not None and not self._packet_stamped)
        prefix = ts_prefix(self._pend_ts) if stamp else ""
        data = bytes(self._pend_buf)
        abs0 = self._pend_abs
        self._pend_buf.clear()
        self._pend_abs += len(data)

        if self.hex_mode:
            self._close_open()
            for i in range(0, len(data), HEX_ROW):
                row = data[i:i + HEX_ROW]
                self.lines.append(Line(direction, self._pend_ts,
                                       prefix if i == 0 else "",
                                       " ".join("%02X" % b for b in row),
                                       abs0 + i, len(row)))
                self._trim_lines()
            if stamp:
                self._packet_stamped = True
        else:
            decoded = self._decoder(direction).decode(data, final=False)
            # 跨 chunk 的 \r 挂起：上段尾部 \r 与本段头部 \n 合并为单个换行
            raw = ("\r" + decoded) if self._cr_hold else decoded
            self._cr_hold = raw.endswith("\r")
            if self._cr_hold:
                raw = raw[:-1]
            # CR-only 行尾兼容：单 \r 也视为换行
            text = raw.replace("\r\n", "\n").replace("\r", "\n")
            if text:
                self._emit_text(direction, self._pend_ts, prefix, text, abs0)
                if stamp:
                    self._packet_stamped = True     # 只有真正产出文本才消耗戳记
            self._trim_lines()

    def _emit_text(self, direction: str, ts: float | None, prefix: str,
                   text: str, abs0: int) -> None:
        """把解码文本按 \n 分段并入行表。

        - 以 \n 收尾的段为完整行（真实空行也保留）
        - 未以 \n 结束的尾段留在 _open 等下包续接
        - 文本以 \n 结尾时不留空游标：否则下一包首行会被空游标吞掉，
          其时间戳前缀丢失、分包边界消失（分包显示失效的根因）
        """
        if not text:
            return
        if self._open is not None and self._open.dir != direction:
            self._close_open()
        segs = text.split("\n")
        for i, seg in enumerate(segs):
            seg = seg.rstrip("\r")
            terminated = i < len(segs) - 1
            first_piece = (i == 0)         # 仅本段首片可带包前缀
            # 行上限硬切分：按"当前行剩余容量"推进，任何行都不超上限
            # （既有 open 行 + 超大新段相加时同样受约束，防止拼出超长行）
            while True:
                open_len = len(self._open.text) if self._open is not None else 0
                room = self._open_max_chars - open_len
                if len(seg) <= room:
                    break
                take = max(0, room)
                head, seg = seg[:take], seg[take:]
                if head:
                    if self._open is not None:
                        self._open.text += head
                    else:
                        self._open = Line(direction, ts,
                                          prefix if first_piece else "", head, abs0, 0)
                        first_piece = False
                    self._close_open(keep_empty=True)      # 满行强制成行
                else:
                    # room == 0（open 已满）：结算当前行后重开，seg 下一轮再切
                    self._close_open(keep_empty=True)
            if self._open is None:
                if not seg and not terminated:
                    break                  # 行首且无内容：不产生空游标
                self._open = Line(direction, ts,
                                  prefix if first_piece else "", seg, abs0, 0)
            else:
                self._open.text += seg
            if terminated:
                self._close_open(keep_empty=True)

    def _close_open(self, keep_empty: bool = False) -> None:
        if self._open is not None:
            # 空的活动行有两种来源：真实空行（数据里连续换行）要保留；
            # 包尾换行留下的"新行游标"在方向/模式切换时是伪影，丢弃
            if keep_empty or self._open.text or self._open.prefix:
                self.lines.append(self._open)
                self._trim_lines()
            self._open = None

    def _trim_lines(self) -> None:
        """行数超上限时裁掉最旧的 1/8，并推进裁剪基址。"""
        if len(self.lines) <= self.max_lines:
            return
        cut = max(1, self.max_lines // 8)
        del self.lines[:cut]
        self._base += cut