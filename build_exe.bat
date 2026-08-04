@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  Seedance Frame Doctor - Windows build script
echo ============================================
echo.

REM --- Check ffmpeg.exe / ffprobe.exe are present ---
if not exist "ffmpeg.exe" (
    echo [ERROR] ffmpeg.exe not found in this folder.
    echo Run setup_and_build.bat instead -- it handles this automatically.
    echo.
    pause
    exit /b 1
)
if not exist "ffprobe.exe" (
    echo [ERROR] ffprobe.exe not found in this folder.
    echo Run setup_and_build.bat instead -- it handles this automatically.
    echo.
    pause
    exit /b 1
)
echo [OK] ffmpeg.exe and ffprobe.exe found.
echo.

REM --- Use the isolated venv (created by setup_and_build.bat) ---
set VENV_DIR=.venv

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [ERROR] No virtual environment found at %VENV_DIR%.
    echo Run setup_and_build.bat first -- it creates everything from scratch.
    echo.
    pause
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate the virtual environment at %VENV_DIR%.
    echo.
    pause
    exit /b 1
)
echo [OK] Activated venv: %VENV_DIR%
echo.

echo Building... this can take a minute or two.
echo Full log is also being written to build_log.txt
echo.

set RIFE_BUNDLE_ARGS=
python -c "import rife_ncnn_vulkan_python" >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%P in ('python -c "import rife_ncnn_vulkan_python, os; print(os.path.dirname(rife_ncnn_vulkan_python.__file__))"') do set RIFE_PKG_DIR=%%P
    if "!RIFE_PKG_DIR!"=="" (
        echo [WARNING] Could not resolve RIFE package dir for bundling.
        echo Building without AI mode ^(blend/mci still included^).
    ) else (
        set RIFE_BUNDLE_ARGS=--collect-all rife_ncnn_vulkan_python --hidden-import PIL --add-data "!RIFE_PKG_DIR!\models;rife_ncnn_vulkan_python\models"
        echo [OK] rife_ncnn_vulkan_python found -- bundling AI interpolation mode.
    )
) else (
    echo rife_ncnn_vulkan_python not installed in the venv -- building
    echo without AI mode ^(blend/mci modes still work fine^).
)
echo.

pyinstaller --noconfirm --onefile --windowed --name SeedanceFrameDoctor --add-binary "ffmpeg.exe;." --add-binary "ffprobe.exe;." %RIFE_BUNDLE_ARGS% gui_app.py > build_log.txt 2>&1
set BUILD_RESULT=%ERRORLEVEL%

type build_log.txt

if %BUILD_RESULT% NEQ 0 (
    echo.
    echo [ERROR] Build failed. Full output is above and saved in build_log.txt
    echo.
    pause
    exit /b 1
)

if not exist "dist\SeedanceFrameDoctor.exe" (
    echo.
    echo [ERROR] PyInstaller reported success but dist\SeedanceFrameDoctor.exe
    echo is missing. Check build_log.txt for what went wrong.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Build complete: dist\SeedanceFrameDoctor.exe
echo ============================================
echo.
pause
