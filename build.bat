@echo off
chcp 65001 >nul
echo ============================================
echo   QR Scanner - 构建 EXE 文件
echo ============================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.7+
    pause
    exit /b 1
)

REM Install dependencies
echo [1/3] 安装依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

REM Find pyzbar DLL path
echo.
echo [2/3] 定位 pyzbar 文件...
for /f "delims=" %%i in ('python -c "import pyzbar; import os; print(os.path.dirname(pyzbar.__file__))"') do set PYZBAR_DIR=%%i
echo pyzbar 路径: %PYZBAR_DIR%

REM Build with PyInstaller
echo.
echo [3/3] 正在构建 EXE 文件...（这可能需要几分钟）
echo.

pyinstaller --noconfirm --clean ^
    --onefile ^
    --windowed ^
    --name "QRScanner" ^
    --add-data "%PYZBAR_DIR%\libiconv.dll;." ^
    --add-data "%PYZBAR_DIR%\libzbar64-0.dll;." ^
    --hidden-import "pyzbar" ^
    --hidden-import "pyzbar.pyzbar" ^
    --hidden-import "PyQt5.QtWebEngineWidgets" ^
    --hidden-import "PyQt5.QtWebEngine" ^
    main.py

if errorlevel 1 (
    echo.
    echo [错误] 构建失败！
    pause
    exit /b 1
)

echo.
echo ============================================
echo   构建成功！
echo   EXE 文件位置: dist\QRScanner.exe
echo ============================================
echo.

REM Copy settings template
if not exist "dist\settings.json" (
    echo {} > "dist\settings.json"
)

echo 提示: 运行前请确保摄像头已连接
echo.
pause
