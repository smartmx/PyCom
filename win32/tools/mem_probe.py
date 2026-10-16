# -*- coding: utf-8 -*-
"""32 位地址空间实测：把真实接收链路（环形缓冲 + 增量行表）灌到指定体积，
报告进程提交量/工作集峰值，判断"显示缓冲上限"各档在 32 位进程里是否安全。
人工观察用，不进测试套件（跑得慢，且依赖空闲内存）。

背景：32 位进程用户地址空间只有 2GB，菜单里 512M 档是主线（64 位）留下的；
win7 分支整体 32 位后，这一档是否还站得住需要实测，不能靠猜。

用法（必须用 32 位解释器）：
    cd win7
    "D:\\Program Files\\Python38-x32\\python.exe" tools\\mem_probe.py [档位MB ...]
不给参数时测 128 / 256 / 512 三档。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

# 中文控制台（GBK 代码页）打不出 ⚠ 等字符时降级为 ?，不中断输出。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except AttributeError:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # 无人值守探测

MB = 1024 * 1024


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def mem():
    """(提交量MB, 峰值提交量MB, 工作集MB)。32 位下 c_size_t=4 字节，正好。"""
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(),
                                            ctypes.byref(pmc), pmc.cb)
    return (pmc.PagefileUsage / MB, pmc.PeakPagefileUsage / MB,
            pmc.WorkingSetSize / MB)


def main():
    sizes = [int(a) for a in sys.argv[1:]] or [128, 256, 512]
    from PySide2.QtWidgets import QApplication
    from app.core.settings import Settings
    from app.ui.mainwindow import MainWindow

    app = QApplication([])
    if sys.maxsize > 2 ** 32:
        print("⚠ 当前解释器是 64 位，测不到 32 位地址空间限制！")
    print("解释器: %s (%d 位)" % (sys.executable, 32 if sys.maxsize <= 2 ** 32 else 64))
    print("%-8s %-10s %-12s %-12s %-10s %s"
          % ("档位", "灌入", "提交量", "峰值提交", "工作集", "结果"))
    for size_mb in sizes:
        cfg = Settings(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "_mem_probe.json"))
        cfg.d["buffer_cap"] = size_mb * MB
        cfg.d["split_show"] = False           # 纯行表，不带时间戳/方向标识
        win = MainWindow(cfg)
        win.show()
        app.processEvents()
        base = mem()[0]
        chunk = b"".join(b"%08d hello world 0123456789ABCDEF\r\n" % i
                         for i in range(16384))          # ≈768KB，带真实换行
        target = 0
        peak = base
        t0 = time.time()
        while target < size_mb * MB:
            try:
                win._on_rx(chunk)
            except MemoryError:
                print("    MemoryError：灌入 %dMB 时分配失败" % (target // MB))
                break
            target += len(chunk)
            if target % (8 * MB) < len(chunk):
                peak = max(peak, mem()[0])
        app.processEvents()
        used, peak2, ws = mem()
        dt = time.time() - t0
        ok = "可行" if used < 900 else ("偏紧" if used < 1200 else "危险")
        print("%-8s %-10s %-12s %-12s %-10s %s（%.1fs, %.0fMB/s）"
              % ("%dM" % size_mb, "%dMB" % (target // MB),
                 "%.0fMB" % used, "%.0fMB" % max(peak, peak2),
                 "%.0fMB" % ws, ok, dt, target / MB / max(dt, 1e-6)))
        print("        缓冲实占 %dMB / 上限 %dMB，行表 %d 行，丢弃 %dMB"
              % (len(win.buf) // MB, win.buf.cap // MB, len(win.builder.lines),
                 win.buf.dropped // MB))
        win.close()
        del win
        app.processEvents()
    try:
        os.remove(cfg.path)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
