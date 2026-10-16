# -*- coding: utf-8 -*-
"""主线 win64/ 与回迁分支 win32/ 的同步核对工具（仓库版）。

两个分支的差异大部分是有意的 Qt5 适配（PySide6→PySide2、exec→exec_、
Qt6 完全限定枚举→简写等），注释差异也不算数。本脚本把这些已知改写归一化掉，
只暴露**真正的功能差异**，用于判断主线（win64/）新改动是否需要手工移植到
win32/。移植后重跑本脚本确认收敛。

用法（在本目录下执行）：
    python sync_check.py             # 列出残留差异块
    python sync_check.py -q          # 只列文件级摘要
    python sync_check.py --testcount # 核对文档里的测试条数陈述（两分支
                                     # CHANGELOG 最新一节与 README、readme、
                                     # docs）与 pytest --collect-only 实收
                                     # 是否一致；只查本目录树内的文档
"""
from __future__ import annotations

import difflib
import io
import os
import re
import subprocess
import sys
import tokenize

# 中文（GBK）控制台守卫：默认模式会打印源码行，行里可能带 GBK 不可编码字符
# （如 ▶）——只降级不可编码字符，不改编码（第十轮 A，与 tools 审计脚本同款）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except AttributeError:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
MAIN, BRANCH = "win64", "win32"
# 条数收集解释器：win64 取当前解释器（须已装 PySide6）；win32 固定 32 位
# Python 3.8（须已装 PySide2）。收集失败时报告退出码 1，不静默跳过。
WIN32_PY = r"D:\Program Files\Python38-x32\python.exe"
PAIRS = [
    "app/core/checksum.py", "app/core/display.py", "app/core/hexutil.py",
    "app/core/link.py", "app/core/multisend.py", "app/core/net_link.py",
    "app/core/rxbuf.py", "app/core/serial_link.py", "app/core/settings.py",
    "app/core/terminal.py", "app/core/txfile.py",
    "app/paths.py", "app/version.py",
    "app/ui/calculator_dlg.py", "app/ui/find_bar.py", "app/ui/mainwindow.py",
    "app/ui/rxview.py", "app/ui/theme.py",
    "main.py", "tests/test_core.py", "tests/test_gui_logic.py",
    "tools/make_ico.py",
]

_QT_ENUMS = ("QEvent|QFrame|Qt|QComboBox|QAbstractItemView|QNetworkInterface|"
             "QAbstractSocket|QStyle|QPalette|QSizePolicy|QFont|QDialog|"
             "QLineEdit|QTabWidget|QSerialPort|QIODevice|QMessageBox|"
             "QTableWidget|QHeaderView|QSplitter|QTextCursor|QPlainTextEdit|"
             "QCheckBox|QPushButton|QLabel|QToolButton")
_FULLY_QUALIFIED = re.compile(
    r"\b(" + _QT_ENUMS + r")\.([A-Za-z_][A-Za-z0-9_]*\.)+")


def strip_comments(text: str) -> list:
    """按字节真值去掉整行/行尾注释（走 tokenize，字符串里的 # 不受影响）。"""
    lines = text.splitlines()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and tok.start[0] == tok.end[0]:
                row, col = tok.start
                lines[row - 1] = lines[row - 1][:col]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return lines


def normalize(text: str) -> list:
    """抹掉注释与已知的、语义等价的分支适配改写。"""
    out = []
    for ln in strip_comments(text):
        s = ln.replace("PySide6", "PySideX").replace("PySide2", "PySideX")
        s = re.sub(r"\bexec\(\)", "RUN()", s)
        s = re.sub(r"\bexec_\(\)", "RUN()", s)
        s = s.replace(".exec_(", ".RUNARG(").replace(".exec(", ".RUNARG(")
        s = _FULLY_QUALIFIED.sub(r"\1.", s)
        out.append(s.strip())
    return out


# ------------------------------------------------------------ 测试条数核对 --
# 背景（审查第六轮 T1）：「改测试不改文档条数」的漂移一再复发，改为机制兜底：
# pytest --collect-only 数出实收，再扫文档里的「N 项」陈述逐一核对。
_NUM_ITEM = re.compile(r"([多少差]\s*)?(\d+)\s*项")
# 分式写法（98/98 单测）先归一成「N 项」再扫；分母任意、后随「单测/项」才认，
# 「8/22 文件」「2026/10/16」这类不受影响
_FRACTION_TESTS = re.compile(r"(?<![\d/])(\d+)\s*/\s*\d+\s*(?=单测|项)")
_TEST_CTX = re.compile(r"test|单测|全过", re.IGNORECASE)  # 行须有测试语义：
# 排除「审计 7 项风险」这类无关数字；有意不含裸「测试」二字——避免
# 「新增 3 项测试」这类非总述行被当成总数误报（第八轮备注 2 的权衡）


def collect_tests(py: str, cwd: str):
    """用给定解释器收集测试数；只 import 不执行用例（offscreen，不建窗口）。"""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        p = subprocess.run(
            [py, "-m", "pytest", "tests", "--collect-only", "-q"],
            cwd=cwd, env=env, capture_output=True, encoding="utf-8",
            errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    hit = None
    for hit in re.finditer(r"(\d+) tests? collected", p.stdout):
        pass
    return int(hit.group(1)) if hit else None


def latest_section(text: str) -> str:
    """CHANGELOG 只核对最新一节：历史版本的条数是当时的事实，不按当前值对。"""
    parts = re.split(r"(?m)^## ", text)
    return parts[1] if len(parts) > 1 else text


def scan_numbers(text: str, label: str, allowed: set, problems: list) -> int:
    """报出 allowed 之外的「N 项」测试条数陈述；返回核对了的陈述数。"""
    text = _FRACTION_TESTS.sub(r"\1 项", text)
    checked = 0
    for i, ln in enumerate(text.splitlines(), 1):
        if not _TEST_CTX.search(ln):
            continue
        for m in _NUM_ITEM.finditer(ln):
            if m.group(1):        # 「多/少/差 N 项」是差值陈述，不是总数
                continue
            checked += 1
            n = int(m.group(2))
            if n not in allowed:
                problems.append("%s:%d  %d 不在允许集 %s ｜ %s"
                                % (label, i, n, sorted(allowed),
                                   ln.strip()[:70]))
    return checked


def cmd_testcount() -> int:
    print("== 测试条数核对：pytest 实收 vs 文档陈述 ==")
    counts = {}
    for branch, py in ((MAIN, sys.executable), (BRANCH, WIN32_PY)):
        n = collect_tests(py, os.path.join(ROOT, branch))
        if n is None:
            print("[!] %s：测试收集失败（解释器 %s 不可用或缺对应 PySide），"
                  "条数核对无法进行" % (branch, py))
            return 1
        counts[branch] = n
        print("[i] %s 实收 %d 项" % (branch, n))
    problems = []
    checked = 0
    for branch in (MAIN, BRANCH):
        # 分支内文档只认本分支实收：win64 的 README 误写 105 也要能报出来
        for rel in ("CHANGELOG.md", "README.md"):
            p = os.path.join(ROOT, branch, rel)
            if not os.path.isfile(p):
                continue
            text = io.open(p, encoding="utf-8").read()
            if rel == "CHANGELOG.md":
                text = latest_section(text)
            checked += scan_numbers(text, "%s/%s" % (branch, rel),
                                    {counts[branch]}, problems)
    root_docs = ["readme.md"]
    docs_dir = os.path.join(ROOT, "docs")
    if os.path.isdir(docs_dir):
        root_docs += ["docs/" + f for f in sorted(os.listdir(docs_dir))
                      if f.endswith(".md")]
    allowed_root = {counts[MAIN], counts[BRANCH]}
    for rel in root_docs:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            continue
        checked += scan_numbers(io.open(p, encoding="utf-8").read(),
                                rel, allowed_root, problems)
    if problems:
        for p in problems:
            print("[!] " + p)
        print("\n条数核对：%d 处漂移（共核对 %d 处陈述）" % (len(problems), checked))
        return 1
    print("条数核对：%d 处陈述与实收一致" % checked)
    return 0


def main() -> int:
    if "--testcount" in sys.argv:
        return cmd_testcount()
    quiet = "-q" in sys.argv
    divergent = 0
    for rel in PAIRS:
        a = os.path.join(ROOT, MAIN, rel)
        b = os.path.join(ROOT, BRANCH, rel)
        if not (os.path.exists(a) and os.path.exists(b)):
            print("缺少文件: %s (%s=%s %s=%s)" % (
                rel, MAIN, os.path.exists(a), BRANCH, os.path.exists(b)))
            divergent += 1
            continue
        na = normalize(io.open(a, encoding="utf-8", errors="replace").read())
        nb = normalize(io.open(b, encoding="utf-8", errors="replace").read())
        if na == nb:
            print("[=] %s" % rel)
            continue
        blocks = [op for op in difflib.SequenceMatcher(
            None, na, nb, autojunk=False).get_opcodes() if op[0] != "equal"]
        real = 0
        details = []
        for tag, i1, i2, j1, j2 in blocks:
            ml, wl = na[i1:i2], nb[j1:j2]
            comment_only = all(
                (not l) or l.startswith("#") for l in ml + wl)
            if comment_only:
                continue
            real += 1
            details.append((tag, i1, i2, ml, wl))
        if not real:
            print("[c] %s 仅注释差异" % rel)
            continue
        divergent += 1
        print("[!] %s 残留 %d 处实质差异" % (rel, real))
        if quiet:
            continue
        for tag, i1, i2, ml, wl in details:
            print("    %s:%d-%d  %s" % (MAIN, i1 + 1, i2, tag))
            for l in ml[:6]:
                print("      - " + l[:150])
            for l in wl[:6]:
                print("      + " + l[:150])
    print("\n有实质差异的文件：%d / %d" % (divergent, len(PAIRS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
