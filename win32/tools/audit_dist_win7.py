# -*- coding: utf-8 -*-
"""PyCom32 Win7 打包审计（PE 级，纯 stdlib，3.8+/3.13 通用）。

产物只有一个**单文件版** dist/PyCom32.exe（目录版已废弃），因此分两处审计：
  A. 对 dist/PyCom32.exe：验证是 32 位程序、引导器导入可解析；
     注意——DLL 与 UCRT 都内嵌在 exe 的压缩归档里，PE 层看不到，
     所以随包 UCRT 的"血统"要靠下面 B 对**打包输入**审计来把关。
  B. 对 win7/vendor/ucrt-x86（打包时随包的 UCRT 来源）：验证 41 个文件都能被
     Win7 解析、且没有转发到 kernelbase 之类 Win8+ 组件。

用法（退出码 0 = 无风险项，1 = 有风险项）：
    python tools\\audit_dist_win7.py                      # 默认审计 dist\\PyCom32.exe
    python tools\\audit_dist_win7.py dist\\PyCom32.exe     # 单文件产物
    python tools\\audit_dist_win7.py vendor\\ucrt-x86      # 打包输入：随包 UCRT
    python tools\\audit_dist_win7.py <某个目录>            # 任意"平铺 DLL 目录"
"""
from __future__ import annotations

import os
import struct
import sys

# 中文控制台（GBK 代码页）打不出 ✓/✗/⚠：保持原编码不变、仅把不可编码字符
# 降级为 ?——否则审计在默认中文终端上直接 UnicodeEncodeError 中断。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except AttributeError:
        pass

DEFAULT_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "dist", "PyCom32.exe")

IMAGE_FILE_MACHINE_I386 = 0x014C

# Win7 SP1（32 位）系统自带、且被 Qt5/Python 实际引用的系统 DLL。
# 不在表里又不在 dist 里的导入 = 运行期风险，脚本会单独列出人工复核。
WIN7_BASELINE = {
    "advapi32.dll", "avrt.dll", "bcrypt.dll", "cabinet.dll", "cfgmgr32.dll",
    "clbcatq.dll", "comctl32.dll", "comdlg32.dll", "credui.dll", "crypt32.dll",
    "cryptui.dll", "d2d1.dll", "d3d11.dll", "d3d9.dll", "dbghelp.dll",
    "dhcpcsvc.dll", "dnsapi.dll", "dsound.dll", "dwmapi.dll", "dwrite.dll",
    "dxgi.dll", "gdi32.dll", "gdiplus.dll", "glu32.dll", "imm32.dll",
    "iphlpapi.dll", "kernel32.dll", "ksuser.dll", "mf.dll", "mfplat.dll",
    "mfreadwrite.dll", "mpr.dll", "msimg32.dll", "msvcrt.dll", "netapi32.dll",
    "normaliz.dll", "ntdll.dll", "odbc32.dll", "ole32.dll", "oleacc.dll",
    "oleaut32.dll", "opengl32.dll", "pdh.dll", "powrprof.dll", "propsys.dll",
    "psapi.dll", "rpcrt4.dll", "rstrtmgr.dll", "secur32.dll", "setupapi.dll",
    "shdocvw.dll", "shell32.dll", "shlwapi.dll", "sspicli.dll", "t2embed.dll",
    "urlmon.dll", "user32.dll", "userenv.dll", "usp10.dll", "uxtheme.dll",
    "version.dll", "wer.dll", "winhttp.dll", "wininet.dll", "winmm.dll",
    "winspool.drv", "wintrust.dll", "ws2_32.dll", "wsock32.dll",
    "wtsapi32.dll", "winusb.dll",
    # VCRuntime 各版本：Qt5 自带 MSVCP140 系列，运行期随包提供
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll",
    "vcruntime140_1.dll", "msvcr100.dll", "msvcr120.dll",
}

# dist 里随包的 UCRT/API-Set 文件，转发目标若落在这些 Win8+ 组件上，
# 说明它是从 Win10/11 系统目录抓来的那份，Win7 上加载可能失败。
WIN8_PLUS_TARGETS = {"kernelbase.dll", "api-ms-win-core-path-l1-1-0.dll",
                     "bcryptprimitives.dll", "cryptbase.dll"}

# 打包实证踩坑清单：这些文件缺一个都会让产物在目标机上起不来
REQUIRED_PRESENT = [
    "ucrtbase.dll", "python3.dll", "python38.dll",
    "api-ms-win-crt-runtime-l1-1-0.dll",
    "api-ms-win-crt-stdio-l1-1-0.dll",
    "api-ms-win-crt-heap-l1-1-0.dll",
    os.path.join("PySide2", "Qt5Core.dll"),
    os.path.join("PySide2", "Qt5Gui.dll"),
    os.path.join("PySide2", "Qt5Network.dll"),
    os.path.join("PySide2", "Qt5SerialPort.dll"),
    os.path.join("PySide2", "Qt5Qml.dll"),      # pyside2.abi3.dll 硬链它
    os.path.join("PySide2", "pyside2.abi3.dll"),
    os.path.join("PySide2", "plugins", "platforms", "qwindows.dll"),
]

PE_EXT = (".exe", ".dll", ".pyd")


# ---------------- PE 解析 ----------------
class PE(object):
    def __init__(self, path):
        with open(path, "rb") as fh:
            self.data = fh.read()
        d = self.data
        if d[:2] != b"MZ":
            raise ValueError("不是 PE 文件")
        self.pe_off = struct.unpack_from("<I", d, 0x3C)[0]
        if d[self.pe_off:self.pe_off + 4] != b"PE\0\0":
            raise ValueError("PE 签名缺失")
        self.machine, self.nsec = struct.unpack_from("<HH", d, self.pe_off + 4)
        self.opt_off = self.pe_off + 24
        self.magic = struct.unpack_from("<H", d, self.opt_off)[0]
        self.plus = self.magic == 0x20B                 # PE32+
        self.dd_off = self.opt_off + (112 if self.plus else 96)
        self.sec_off = self.opt_off + struct.unpack_from(
            "<H", d, self.pe_off + 20)[0]
        self.sections = []
        for i in range(self.nsec):
            o = self.sec_off + 40 * i
            name = d[o:o + 8].rstrip(b"\0").decode("latin-1")
            vsz, va, rsz, raw = struct.unpack_from("<IIII", d, o + 8)
            self.sections.append((name, va, max(vsz, rsz), raw))

    def rva_to_off(self, rva):
        for _name, va, vsz, raw in self.sections:
            if va <= rva < va + vsz:
                return raw + (rva - va)
        return None

    def cstr(self, rva):
        off = self.rva_to_off(rva)
        if off is None:
            return ""
        end = self.data.find(b"\0", off)
        return self.data[off:end].decode("latin-1", "replace")

    def _dir(self, index):
        rva, size = struct.unpack_from("<II", self.data, self.dd_off + 8 * index)
        return rva, size

    def imports(self):
        """返回导入的 DLL 名集合（不含延迟加载）。"""
        rva, _size = self._dir(1)
        if not rva:
            return set()
        names = set()
        off = self.rva_to_off(rva)
        while off:
            desc = struct.unpack_from("<IIIII", self.data, off)
            if not any(desc):
                break
            if desc[3]:
                names.add(self.cstr(desc[3]).lower())
            off += 20
        return names

    def export_forwarders(self):
        """返回 {导出名: 转发目标字符串}，只取真正的转发项。"""
        rva, size = self._dir(0)
        if not rva:
            return {}
        base = self.rva_to_off(rva)
        if base is None:
            return {}
        nfun, nname = struct.unpack_from("<II", self.data, base + 20)
        afun, aname = struct.unpack_from("<II", self.data, base + 28)
        out = {}
        for i in range(nname):
            noff = self.rva_to_off(aname) + 4 * i
            name = self.cstr(struct.unpack_from("<I", self.data, noff)[0])
            foff = self.rva_to_off(afun) + 2 * i
            frva = struct.unpack_from("<H", self.data, foff)[0]
            # 函数 RVA 落在导出目录内部 = 转发项（内容是 "DLL.Func"）
            if rva <= frva < rva + size:
                out[name] = self.cstr(frva)
        if not out and nfun and nname:
            # 有导出但无转发项：仍报告数量供判断（真实实现）
            out["<无转发，真实导出 %d 个>" % nfun] = ""
        return out


def audit(root):
    root = os.path.abspath(root)
    single = os.path.isfile(root)
    dist = os.path.dirname(root) if single else root
    # "UCRT 目录"模式：平铺一堆 dll（打包输入 vendor/ucrt-x86），没有 exe
    ucrt_only = (not single and os.path.isdir(root)
                 and os.path.exists(os.path.join(root, "ucrtbase.dll"))
                 and not any(n.lower().endswith(".exe")
                             for n in os.listdir(root)))
    print("审计目标: %s%s" % (root, "   （UCRT 随包输入目录）" if ucrt_only else ""))
    print("=" * 72)
    if single:
        print("注：单文件版的 DLL/UCRT 内嵌在 exe 的压缩归档里，PE 层看不到；")
        print("    随包 UCRT 的血统请对打包输入跑：audit_dist_win7.py vendor\\ucrt-x86")

    files = []
    if single:
        files.append(root)
    else:
        for dirpath, _dirs, names in os.walk(root):
            for n in sorted(names):
                if n.lower().endswith(PE_EXT):
                    files.append(os.path.join(dirpath, n))

    local = {os.path.basename(p).lower() for p in files}
    if not single and os.path.isdir(root):
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                local.add(n.lower())

    risks = []
    # 1) 位数
    bad_bits = []
    all_imports = {}
    pe_cache = {}
    for p in files:
        try:
            pe = PE(p)
        except Exception as exc:                    # noqa: BLE001
            risks.append("%s 无法解析: %s" % (os.path.basename(p), exc))
            continue
        pe_cache[p] = pe
        if pe.machine != IMAGE_FILE_MACHINE_I386:
            bad_bits.append("%s machine=0x%04x" % (os.path.basename(p),
                                                   pe.machine))
        all_imports[p] = pe.imports()
    print("[1] PE 文件 %d 个，非 32 位: %d" % (len(files), len(bad_bits)))
    for b in bad_bits:
        print("    ✗ " + b)
        risks.append("非 32 位: " + b)

    # 2) 必备文件（只对"平铺目录"有意义：单文件版内嵌、UCRT 输入目录只查 UCRT）
    print("[2] 必备文件检查（打包踩坑清单）")
    if not single and not ucrt_only:
        for rel in REQUIRED_PRESENT:
            ok = os.path.exists(os.path.join(root, rel))
            print("    %s %s" % ("✓" if ok else "✗", rel))
            if not ok:
                risks.append("缺必备文件: " + rel)

    # 3) 导入解析
    extern = {}
    for p, names in all_imports.items():
        for n in names:
            if n not in local:
                extern.setdefault(n, []).append(os.path.basename(p))
    unknown = sorted(n for n in extern if n not in WIN7_BASELINE)
    print("[3] 导入解析：外部依赖 %d 个，其中非 Win7 基线 %d 个"
          % (len(extern), len(unknown)))
    for n in sorted(extern):
        tag = "系统基线" if n in WIN7_BASELINE else "⚠ 未知"
        print("    %s %-34s ← %s" % (tag, n, ", ".join(sorted(set(
            extern[n]))[:3])))
    for n in unknown:
        risks.append("导入既不在 dist 也不在 Win7 基线表: %s (被 %s 引用)"
                     % (n, ", ".join(sorted(set(extern[n]))[:3])))

    # 4) 随包 UCRT 血统：转发目标是否落到 Win8+ 组件
    print("[4] 随包 UCRT / API-Set 血统检查（转发目标）")
    if not single:
        for name in sorted(local):
            if not (name.startswith("api-ms-win-") or name == "ucrtbase.dll"):
                continue
            p = os.path.join(root, name)
            if not os.path.exists(p):
                continue
            try:
                pe = pe_cache.get(p) or PE(p)
            except Exception:                       # noqa: BLE001
                continue
            fwd = pe.export_forwarders()
            targets = {v.split(".")[0].lower() for v in fwd.values() if v}
            hits = sorted(t for t in targets
                          if t in WIN8_PLUS_TARGETS or t.startswith("api-ms-"))
            imp = sorted(pe.imports())
            bad_imp = [i for i in imp if i in WIN8_PLUS_TARGETS]
            # 一个"文件不存在"的转发目标 = Win7 上必崩；Win11 系统目录那份
            # 的典型特征就是转发到 kernelbase / 其它 api-ms-win-core-*
            note = []
            if hits:
                note.append("转发→ " + ", ".join(hits[:3]))
            if bad_imp:
                note.append("自身导入 Win8+ " + ", ".join(bad_imp[:3]))
            flag = "⚠" if (hits or bad_imp) else "✓"
            print("    %s %-40s %s" % (flag, name, "; ".join(note) or "无 Win8+ 依赖"))
            if hits or bad_imp:
                risks.append("%s 依赖 Win8+ 组件（%s）→ Win7 上可能加载失败"
                             % (name, "; ".join(note)))

    print("=" * 72)
    if risks:
        print("结论：发现 %d 项风险" % len(risks))
        for r in risks:
            print("  - " + r)
        return 1
    print("结论：未发现 Win7 兼容性风险项")
    return 0


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIST
    if not os.path.exists(target):
        print("路径不存在: %s" % target)
        return 2
    return audit(target)


if __name__ == "__main__":
    raise SystemExit(main())
