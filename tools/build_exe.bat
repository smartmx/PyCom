@echo off
rem PyCom 打包脚本（需先 pip install pyinstaller）
rem 一次产出两个产物：dist\PyCom\PyCom.exe（目录版，需带 _internal 整个文件夹）
rem                   dist\PyCom.exe（单文件便携版，拷单个 exe 即可运行）
cd /d "%~dp0.."
pyinstaller --noconfirm --clean PyCom.spec
if not exist dist\PyCom\assets mkdir dist\PyCom\assets
copy /y assets\pycom-icon.svg dist\PyCom\assets\pycom-icon.svg >nul
copy /y assets\pycom.ico dist\PyCom\assets\pycom.ico >nul
echo.
echo 输出: dist\PyCom\PyCom.exe   （目录版，启动快）
echo       dist\PyCom.exe        （单文件版，便携）
pause
