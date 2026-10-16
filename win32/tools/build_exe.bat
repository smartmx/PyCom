@echo off
rem PyCom32 打包脚本（Win7 32 位回迁版：Python 3.8 + PySide2/Qt5 + PyInstaller 5.13.2）
rem 产物只有一个：dist\PyCom32.exe —— 单文件便携版，32 位程序，Win7 32/64 位系统均可运行。
rem 【规则】只打包单文件版，不再产出目录版（目录版已废弃）。
rem 首次使用先装依赖（32 位 Python 3.8 在 D:\Program Files\Python38-x32，非系统默认）:
rem   "D:\Program Files\Python38-x32\python.exe" -m pip install PySide2==5.15.2.1 pyinstaller==5.13.2
cd /d "%~dp0.."
set PY=D:\Program Files\Python38-x32\python.exe
"%PY%" -m PyInstaller --noconfirm --clean PyCom32.spec
echo.
echo 输出: dist\PyCom32.exe   （单文件便携版，拷单个 exe 即可用）
echo 复核: python tools\audit_dist_win7.py dist\PyCom32.exe  ^&^&  python tools\audit_dist_win7.py vendor\ucrt-x86
pause
