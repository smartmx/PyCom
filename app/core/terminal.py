# -*- coding: utf-8 -*-
"""终端仿真：按键 -> 字节流映射与 0D 补 0A。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent

# 特殊键 -> ANSI/控制序列（Qt6 的 Qt.Key 枚举，勿用手写旧值）
SPECIAL = {
    Qt.Key.Key_Up: b"[A",
    Qt.Key.Key_Down: b"[B",
    Qt.Key.Key_Right: b"[C",
    Qt.Key.Key_Left: b"[D",
    Qt.Key.Key_Home: b"[H",
    Qt.Key.Key_End: b"[F",
    Qt.Key.Key_PageUp: b"[5~",
    Qt.Key.Key_PageDown: b"[6~",
    Qt.Key.Key_Delete: b"[3~",
    Qt.Key.Key_Insert: b"[2~",
    Qt.Key.Key_Backspace: b"",
}


def key_to_bytes(ev: QKeyEvent, encoding: str = "gbk") -> bytes | None:
    """把按键事件转换为要发送的字节；无对应字节（如纯修饰键）返回 None。"""
    if ev.key() in SPECIAL:
        return SPECIAL[ev.key()]
    text = ev.text()
    if not text:
        return None
    try:
        return text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return text.encode("gbk", "replace")


def cr_add_lf(data: bytes, enabled: bool) -> bytes:
    """0D 后自动补 0A。"""
    if not enabled:
        return data
    out = bytearray()
    for i, b in enumerate(data):
        out.append(b)
        # 0D 后已跟 0A（已有 CRLF）时不重复补，避免产生多余空行
        if b == 0x0D and not (i + 1 < len(data) and data[i + 1] == 0x0A):
            out.append(0x0A)
    return bytes(out)
