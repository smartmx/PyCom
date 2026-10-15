# -*- coding: utf-8 -*-
"""HEX 文本 <-> bytes 工具（容错：空格/逗号/0x 前缀/中英文标点混排）。"""
from __future__ import annotations

import re

_WORD = re.compile(r"(?:0[xX])?([0-9a-fA-F]{1,2})")


def parse_hex(text: str) -> bytes:
    out = bytearray()
    for tok in re.split(r"[\s,;，,；\-]+", text.strip()):
        if not tok:
            continue
        m = _WORD.fullmatch(tok)
        if not m:
            raise ValueError("无法解析的 HEX 词元: %r" % tok)
        out.append(int(m.group(1), 16))
    return bytes(out)


def to_hex(data: bytes, sep: str = " ") -> str:
    return sep.join("%02X" % b for b in data)


def try_parse_hex(text: str) -> bytes | None:
    """宽松解析：失败返回 None（用于 HEX 发送框容错）。"""
    try:
        return parse_hex(text)
    except ValueError:
        return None
