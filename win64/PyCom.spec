# -*- mode: python ; coding: utf-8 -*-
# 一次构建两个产物（共用同一份 Analysis）：
#   dist/PyCom/PyCom.exe   目录版（推荐：启动快，需带 _internal/ 整个文件夹）
#   dist/PyCom.exe         单文件版（便携：单个 exe 拷到哪都能跑，启动稍慢）
import os

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('PySide6.QtSerialPort')

datas = [('assets/pycom-icon.svg', 'assets'), ('assets/pycom.ico', 'assets')]

# ---- 体积裁剪：纯 QWidgets 应用用不到的重型 Qt 组件整包剔除（约省 45MB）----
# 软件 OpenGL 回退、QML/Quick 栈、QtPdf、虚拟键盘、Qt 自带界面翻译：
# 这些都属于按需加载组件（我们只用 Widgets 光栅绘制 + 原生输入法），
# 剔除后不影响启动与运行。UPX 未安装时 DLL 本身无法再压缩，这里是主要杠杆。
_DROP_DLLS = {
    "opengl32sw.dll",          # 19.7MB 软件 OpenGL（Widgets 用光栅，不请求 GL）
    "Qt6OpenGL.dll", "Qt6Pdf.dll",
    "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlMeta.dll",
    "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll",
    "Qt6VirtualKeyboard.dll",
    # ---- 第二轮：TLS/加密栈（应用无任何 ssl/hashlib 导入，已 grep 确认）----
    "libcrypto-3-x64.dll", "libcrypto-3.dll",     # OpenSSL（Python _ssl/_hashlib 链接）
    "libssl-3-x64.dll", "libssl-3.dll",
    "qdirect2d.dll",           # 备用平台插件（只用 qwindows）
    "ucrtbase.dll",            # UCRT：Win10+ 系统自带（Python 3.13 本就要求 Win10+）
}
_DROP_DIRS = (
    os.path.join("PySide6", "translations"),               # 7MB .qm（未装 QTranslator）
    os.path.join("PySide6", "plugins", "platforminputcontexts"),  # 虚拟键盘输入法插件
    os.path.join("PySide6", "plugins", "generic"),         # TUIO 触摸协议插件
    os.path.join("PySide6", "plugins", "tls"),             # Qt TLS 后端（应用无 TLS 需求）
    os.path.join("PySide6", "plugins", "imageformats"),    # 位图图像插件（PNG 内置于 Qt6Gui）
    os.path.join("PySide6", "plugins", "networkinformation"),     # QNetworkInformation
)
_DROP_FILES = (os.path.join("PySide6", "plugins", "imageformats", "qtiff.dll"),)
# Python 侧 ssl/_ssl/_hashlib 与上面被剔除的 OpenSSL DLL 配套（app 已 grep 无导入）
_EXCLUDE_MODULES = ["ssl", "_ssl", "_hashlib"]


def _keep(entry):
    dest = entry[0].replace("/", os.sep)
    if os.path.basename(dest) in _DROP_DLLS:
        return False
    if dest in _DROP_FILES:
        return False
    if os.path.basename(dest).startswith("api-ms-win-crt-"):
        return False               # UCRT 转发 DLL：Win10+ 由系统解析
    return not any(dest.startswith(d + os.sep) for d in _DROP_DIRS)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDE_MODULES,
    noarchive=False,
    optimize=0,
)
a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]
pyz = PYZ(a.pure)

# 只产出**单文件**便携版（规则：不再打包目录版）：
# binaries/datas 全部内联进 exe，运行时解包到 sys._MEIPASS。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PyCom',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
