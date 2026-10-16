# -*- coding: utf-8 -*-
"""从 assets/pycom-icon.svg 生成 assets/pycom.ico（打包用）。

纯标准库写 ICO（Vista+ 支持内嵌 PNG 帧），渲染用 QtSvg，不依赖 Pillow。
用法: python tools/make_ico.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QBuffer, QByteArray, QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_pngs(svg_path: Path) -> list[tuple[int, bytes]]:
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise SystemExit("SVG 无效: %s" % svg_path)
    pngs = []
    for size in SIZES:
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        renderer.render(p, QRect(0, 0, size, size))
        p.end()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        pngs.append((size, bytes(ba)))
    return pngs


def build_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    """ICO 数据: 6 字节头 + 16 字节目录项/帧 + 内嵌 PNG 数据。"""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    entries = b""
    offset = 6 + 16 * len(pngs)
    body = b""
    for size, png in pngs:
        wh = 0 if size >= 256 else size           # 256 记为 0
        entries += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        body += png
    return header + entries + body


def main() -> int:
    app = QGuiApplication([])
    root = Path(__file__).resolve().parents[1]     # 项目根（pycom/）
    assets = root / "assets"                       # 固定写入项目内 assets 目录
    svg = assets / "pycom-icon.svg"
    out = assets / "pycom.ico"
    out.write_bytes(build_ico(render_pngs(svg)))
    print("已生成: %s (%d 字节, %d 帧)" % (out, out.stat().st_size, len(SIZES)))
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
