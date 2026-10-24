@echo off
rem PyCom 打包脚本（Python 3.13 + PySide6/Qt6 + PyInstaller 6.22.2，Win10+）
rem 一次构建两个产物（共用一份 Analysis）：
rem   dist\PyCom\PyCom.exe   目录版（exe + _internal\，免解压、启动快）
rem   dist\PyCom.exe         单文件版（便携，拷单个 exe 即可运行）
rem 首次使用先装依赖：pip install PySide6 pyinstaller
cd /d "%~dp0.."
pyinstaller --noconfirm --clean PyCom.spec
echo.
echo 输出: dist\PyCom\PyCom.exe + _internal\   （目录版，整文件夹拷贝）
echo       dist\PyCom.exe                      （单文件便携版）
pause
