@echo off
chcp 65001
echo 正在安装依赖...
pip install -r requirements.txt
echo.
echo 正在打包EXE文件...
pyinstaller --noconfirm --clean --windowed --name 照片管理器 --onefile photo_manager.py
echo.
echo 打包完成！EXE文件位于 dist\照片管理器.exe
pause
