@echo off
rem PyCom32 打包脚本（Win7 32 位回迁版：Python 3.8 + PySide2/Qt5 + PyInstaller 6.22.2）
rem 一次构建两个产物（共用一份 Analysis）：
rem   dist\PyCom32\PyCom32.exe   目录版（exe + _internal\，结构对齐主线，免解压）
rem   dist\PyCom32.exe           单文件版（便携，32 位程序，Win7 32/64 位系统均可运行）
rem 首次使用先装依赖（32 位 Python 3.8 在 D:\Program Files\Python38-x32，非系统默认）:
rem   "D:\Program Files\Python38-x32\python.exe" -m pip install PySide2==5.15.2.1 pyinstaller==6.22.2
cd /d "%~dp0.."
set PY=D:\Program Files\Python38-x32\python.exe
"%PY%" -m PyInstaller --noconfirm --clean PyCom32.spec
echo.
echo 输出: dist\PyCom32\PyCom32.exe + _internal\   （目录版，整文件夹拷贝）
echo       dist\PyCom32.exe                        （单文件便携版）
echo 复核: python tools\audit_dist_win7.py dist\PyCom32.exe  ^&^&  python tools\audit_dist_win7.py dist\PyCom32  ^&^&  python tools\audit_dist_win7.py vendor\ucrt-x86
pause
