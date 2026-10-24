# -*- mode: python ; coding: utf-8 -*-
# Win7 32 位回迁版（Python 3.8 + PySide2/Qt5 + PyInstaller 6.22.2，与主线
# 同版本——2026-10-24 打包指令起由 5.13.2 升级，目录版获得与主线一致的
# exe + _internal/ 布局）。
# 一次构建两个产物（共用同一份 Analysis，2026-10-24 打包指令恢复双产物）：
#   dist/PyCom32/PyCom32.exe   目录版（免解压：exe + _internal/ 文件夹，
#                               DLL 与 Win7 专用 UCRT 全在 _internal/ 里，
#                               整文件夹拷贝、启动零解包，结构对齐主线）
#   dist/PyCom32.exe           单文件版（便携：单 exe，启动自解包，稍慢）
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('PySide2.QtSerialPort')

datas = [('assets/pycom-icon.svg', 'assets'), ('assets/pycom.ico', 'assets')]

# PySide2 的 QtCore.pyd/shiboken2.pyd 链接稳定 ABI 转发库 python3.dll
# （python3.dll 再转发到 python38.dll）。PyInstaller 5.x 默认只收 python38.dll，
# 缺 python3.dll 时启动即报 "DLL load failed while importing QtCore"，必须显式带上。
_python3_dll = os.path.join(sys.base_prefix, "python3.dll")
binaries = [(_python3_dll, ".")] if os.path.exists(_python3_dll) else []

# ---- Win7 专用 UCRT 随包（说明见 vendor/ucrt-x86/README.md）----
# Win7 系统不带 UCRT，必须随包；但 PyInstaller 默认从**开发机 System32** 抓
# （Win11 上抓到的是 10.0.26100，它导入 api-ms-win-core-fibers-l1-1-1 等
# Win8+ 才有的 API-Set，Win7 上 ucrtbase 一加载就失败、程序起不来）。
# 这里改用微软面向 Win7 SP1+ 的 app-local 再发行版（SDK 里**不带版本号**的
# Redist\ucrt\DLLs\x86，10.0.10240），并在下面 _keep 里丢弃别的来源的同名文件。
_ucrt_candidates = []
if os.environ.get("PYCOM_UCRT_DIR"):
    _ucrt_candidates.append(os.environ["PYCOM_UCRT_DIR"])
_ucrt_candidates.append(os.path.join(SPECPATH, "vendor", "ucrt-x86"))
_ucrt_candidates.append(os.path.join(
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    "Windows Kits", "10", "Redist", "ucrt", "DLLs", "x86"))
_UCRT_DIR = next((d for d in _ucrt_candidates
                  if d and os.path.isfile(os.path.join(d, "ucrtbase.dll"))), None)
if _UCRT_DIR is None:
    raise SystemExit(
        "未找到 Win7 专用 UCRT 目录（需要 ucrtbase.dll 10.0.10240 那份）。\n"
        "  查找顺序: PYCOM_UCRT_DIR → %s → %s\n"
        "  修复: 装 Windows SDK 后从 <SDK>\\Redist\\ucrt\\DLLs\\x86 拷 41 个 dll 到\n"
        "        win7\\vendor\\ucrt-x86\\，或设 PYCOM_UCRT_DIR 指向该目录。\n"
        "  注意: 别用 Redist 下带版本号的目录（10.0.22621.0 等），那是 Win10/11 那份。"
        % (os.path.join(SPECPATH, "vendor", "ucrt-x86"), _ucrt_candidates[-1]))
_UCRT_DIR_ABS = os.path.normcase(os.path.abspath(_UCRT_DIR))
binaries += [(os.path.join(_UCRT_DIR, f), ".")
             for f in sorted(os.listdir(_UCRT_DIR)) if f.lower().endswith(".dll")]

# ---- 体积裁剪（Qt5 版）:纯 QWidgets 应用用不到的重型 Qt 组件整包剔除 ----
# PySide2 完整 wheel 带全量 Qt5 模块（QML/Quick、3D、WebEngine、多媒体、定位等），
# 我们只用 Widgets 光栅绘制 + QtSerialPort + QtNetwork(TCP/UDP) + QtSvg。
# 注意与 Qt6 版的一点差异：
#   UCRT 必须随包，且要用上面那份 Win7 版（Qt6 版面向 Win10+、反而剔除 UCRT）。
# 目录版布局自 PyInstaller 6.x 起与主线一致：exe 在 dist/PyCom32/ 根、
# 其余（DLL/UCRT/PySide2）全在 dist/PyCom32/_internal/ 里。
# UPX 未安装时 DLL 本身无法再压缩，剔除列表是主要杠杆。
_DROP_DLL_EXACT = {
    "opengl32sw.dll",          # 20MB 软件 OpenGL（Widgets 用光栅，不请求 GL）
    "d3dcompiler_47.dll",      # Qt Quick/3D 的 D3D 着色器编译器
    "Qt5Pdf.dll", "Qt5VirtualKeyboard.dll", "Qt5OpenGL.dll",
    # TLS/加密：Qt5Network 仅在用 QSslSocket 时按需加载 OpenSSL；应用无 TLS
    # 需求，Python 侧 ssl/_ssl/_hashlib 也在下方 excludes 中一并排除
    "libcrypto-1_1-x64.dll", "libcrypto-1_1.dll",
    "libssl-1_1-x64.dll", "libssl-1_1.dll",
    "libeay32.dll", "ssleay32.dll",
    # 两个"引用了被剔除的 Qt 模块、永远加载不了"的插件（留着只会让依赖图报缺件）：
    # qwebgl→Qt5Quick/Qt5WebSockets（WebGL 平台插件），
    # qxdgdesktopportal→Qt5DBus（xdg 桌门户是 Linux 的东西）
    "qwebgl.dll", "qxdgdesktopportal.dll",
}
_DROP_DLL_PREFIX = (
    # 注意：Qt5Qml.dll 必须保留——PySide2 的 pyside2.abi3.dll 硬链接它，
    # 剔掉后 QtCore 导入即报 "DLL load failed"（Qt5Quick 大件仍可剔）。
    "Qt5Quick", "Qt5QmlModels", "Qt5QmlWorkerScript",
    "Qt53D",                                                # 3D 全家
    "Qt5WebEngine", "Qt5WebChannel", "Qt5WebSockets",      # WebEngine 巨型组件
    "Qt5Bluetooth", "Qt5Nfc", "Qt5Charts", "Qt5DataVisualization",
    "Qt5Gamepad", "Qt5Location", "Qt5Positioning",
    "Qt5Multimedia", "Qt5MultimediaWidgets",
    "Qt5RemoteObjects", "Qt5Scxml", "Qt5Sensors", "Qt5SerialBus",
    "Qt5Sql", "Qt5Test", "Qt5TextToSpeech", "Qt5Designer",
    "Qt5Help", "Qt5UiTools", "Qt5PrintSupport", "Qt5Xml", "Qt5DBus",
    "Qt5XmlPatterns",
)
_DROP_DIRS = (
    os.path.join("PySide2", "translations"),               # .qm 界面翻译（未装 QTranslator）
    os.path.join("PySide2", "plugins", "platforminputcontexts"),  # 虚拟键盘输入法插件
    os.path.join("PySide2", "plugins", "generic"),         # evdev/TUIO 等通用输入插件
    os.path.join("PySide2", "plugins", "bearer"),          # Qt5 网络承载管理插件
    os.path.join("PySide2", "plugins", "geoservices"),     # 地图服务（QtLocation 配套）
    os.path.join("PySide2", "plugins", "mediaservice"),    # 多媒体后端
    os.path.join("PySide2", "plugins", "sensorgestures"),
    os.path.join("PySide2", "plugins", "sensors"),
    os.path.join("PySide2", "plugins", "sqldrivers"),      # SQLite/ODBC 驱动
    os.path.join("PySide2", "plugins", "texttospeech"),
    os.path.join("PySide2", "plugins", "virtualkeyboard"),
    os.path.join("PySide2", "plugins", "webview"),
    os.path.join("PySide2", "plugins", "sceneparsers"),    # 3D 场景解析
    os.path.join("PySide2", "plugins", "imageformats"),    # 位图插件（PNG/GIF/JPEG 内置于 Qt5Gui）
    os.path.join("PySide2", "plugins", "position"),
    os.path.join("PySide2", "plugins", "audio"),
)
# Python 侧 ssl/_ssl/_hashlib 与上面被剔除的 OpenSSL DLL 配套（app 已 grep 无导入）
_EXCLUDE_MODULES = ["ssl", "_ssl", "_hashlib"]


def _keep(entry):
    dest = entry[0].replace("/", os.sep)
    base = os.path.basename(dest)
    low = base.lower()
    # UCRT / API-Set：只认上面指定的 Win7 版那份，丢弃 PyInstaller 从开发机
    # System32 抓来的同名文件（entry[1] 是源路径；entry[0] 是包内目标名）
    if low == "ucrtbase.dll" or low.startswith("api-ms-win-"):
        src = entry[1] or ""
        return os.path.normcase(os.path.abspath(src)).startswith(_UCRT_DIR_ABS)
    if base in _DROP_DLL_EXACT:
        return False
    if base.startswith(_DROP_DLL_PREFIX):
        return False
    return not any(dest.startswith(d + os.sep) for d in _DROP_DIRS)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDE_MODULES,
    noarchive=False,
)
a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]
pyz = PYZ(a.pure)

# ---- 目录版（免解压：2026-10-24 打包指令恢复双产物）----
# PyInstaller 6.x 目录版：exe 在 dist/PyCom32/ 根，DLL/资源（含 Win7 专用
# UCRT）全在 dist/PyCom32/_internal/ 子目录里（与主线布局一致），启动零解包；
# 分发时把 PyCom32.exe + _internal/ 整个文件夹一起拷贝。
exe_dir = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PyCom32',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/pycom.ico'],
)
coll = COLLECT(
    exe_dir,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='PyCom32',
)

# ---- 单文件版（便携：单个 exe 拷到哪都能跑，启动自解包到 %TEMP%）----
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PyCom32',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 显式关闭：UPX 壳会拖慢启动并显著提高杀软
                             # 启发式误报率（启动性能分析_20261021 §4.2）
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/pycom.ico'],
)
