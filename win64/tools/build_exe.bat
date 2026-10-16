@echo off
rem PyCom 打包脚本（需先 pip install pyinstaller）
rem 产物只有一个：dist\PyCom.exe —— 单文件便携版，拷单个 exe 即可运行。
rem 【规则】只打包单文件版，不再产出目录版（目录版已废弃）。
cd /d "%~dp0.."
pyinstaller --noconfirm --clean PyCom.spec
echo.
echo 输出: dist\PyCom.exe   （单文件便携版，拷单个 exe 即可用）
pause
