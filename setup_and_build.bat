@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  Seedance Frame Doctor - one-button setup
echo  The only thing this assumes is already
echo  installed is Python.
echo ============================================
echo.

REM --- Step 0: confirm Python is available ---
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python from https://www.python.org/downloads/
    echo and make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
echo [OK] Python found.
echo.

REM --- Step 0b: create / activate an isolated virtual environment ---
REM We prefer Python 3.11 specifically because the bundled RIFE wheel
REM in vendor/ is built for cp311. The Windows Python Launcher (py.exe)
REM lets us request a specific version even if multiple are installed.
REM Fall back to whatever "python" points to if 3.11 is not available.
set VENV_DIR=.venv
set PYTHON_FOR_VENV=python

if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [OK] Virtual environment already exists at %VENV_DIR%, reusing it.
    goto :activate_venv
)

echo Looking for Python 3.11 ^(preferred for bundled RIFE wheel^)...
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 --version >nul 2>nul
    if not errorlevel 1 (
        set PYTHON_FOR_VENV=py -3.11
        echo [OK] Python 3.11 found via Windows launcher -- will use it for the venv.
    ) else (
        echo Python 3.11 not found via launcher. Checking direct path...
        if exist "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe" (
            set PYTHON_FOR_VENV=C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe
            echo [OK] Python 3.11 found at default install path.
        ) else (
            echo Python 3.11 not found -- using default Python.
            echo Note: the bundled RIFE wheel may not match; will try PyPI fallback.
        )
    )
) else (
    echo Windows Python Launcher ^(py.exe^) not found -- using default Python.
)

echo Creating an isolated virtual environment at %VENV_DIR% using %PYTHON_FOR_VENV%...
echo ^(this keeps our packages separate from your global Python/^)
echo ^(ComfyUI install -- avoids DLL/version conflicts entirely^)
%PYTHON_FOR_VENV% -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    echo.
    pause
    exit /b 1
)
echo [OK] Virtual environment created.

:activate_venv
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate the virtual environment.
    echo.
    pause
    exit /b 1
)
echo.

REM --- Step 1: install required Python packages, INSIDE the venv only ---
echo Installing/checking Python packages: opencv-python, numpy, pillow, pyinstaller
echo ^(this runs inside the venv only -- nothing leaks into global Python^)
echo.
python -m pip install --quiet --upgrade pip
python -m pip install --quiet opencv-python numpy pillow pyinstaller
if errorlevel 1 (
    echo [ERROR] pip install failed. See output above.
    echo.
    pause
    exit /b 1
)
echo [OK] Python packages ready.
echo.

REM --- Step 2: get ffmpeg.exe / ffprobe.exe if not already present ---
if exist "ffmpeg.exe" if exist "ffprobe.exe" (
    echo [OK] ffmpeg.exe and ffprobe.exe already present, skipping download.
    goto :rife_setup
)

echo ffmpeg.exe / ffprobe.exe not found next to this script.
echo Downloading the official gyan.dev release essentials build...
echo ^(~100 MB - this can take a few minutes on a slow connection.^)
echo ^(If a progress bar/percentage is shown below, it is NOT stuck -^)
echo ^(just let it run. It will time out on its own after 10 minutes^)
echo ^(if something is genuinely wrong.^)
echo.

set DL_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
if exist "ffmpeg_temp.zip" del /q "ffmpeg_temp.zip" >nul 2>nul

where curl >nul 2>nul
if not errorlevel 1 (
    echo Using curl...
    curl -L --fail --connect-timeout 20 --max-time 600 -o "ffmpeg_temp.zip" "%DL_URL%"
    set DL_RESULT=!ERRORLEVEL!
) else (
    echo curl not found, using PowerShell instead ^(progress bar may render^)...
    powershell -NoProfile -Command "$job = Start-Job -ScriptBlock { param($u,$o) Invoke-WebRequest -Uri $u -OutFile $o } -ArgumentList '%DL_URL%','ffmpeg_temp.zip'; if (Wait-Job $job -Timeout 600) { Receive-Job $job -ErrorAction Stop; Remove-Job $job } else { Stop-Job $job; Remove-Job $job; Write-Host 'Timed out after 600 seconds.'; exit 1 }"
    set DL_RESULT=!ERRORLEVEL!
)

if not "!DL_RESULT!"=="0" (
    echo.
    echo [ERROR] Download failed or timed out after 10 minutes.
    echo This usually means a slow/blocked connection to gyan.dev on
    echo this network ^(firewall, proxy, or antivirus^).
    echo.
    echo Workaround: download this URL manually in a browser instead:
    echo   %DL_URL%
    echo Then extract it, copy ffmpeg.exe and ffprobe.exe from its
    echo bin folder into this same folder, and rerun this script -- it
    echo will skip straight to the build step once it finds them.
    echo.
    pause
    exit /b 1
)

if not exist "ffmpeg_temp.zip" (
    echo [ERROR] Download reported success but ffmpeg_temp.zip is missing.
    echo.
    pause
    exit /b 1
)

for %%S in ("ffmpeg_temp.zip") do set DL_SIZE=%%~zS
if !DL_SIZE! LSS 1000000 (
    echo [ERROR] Downloaded file is only !DL_SIZE! bytes -- too small to
    echo be the real archive, likely an incomplete or failed download.
    echo Delete ffmpeg_temp.zip and rerun this script.
    echo.
    pause
    exit /b 1
)
echo [OK] Downloaded ffmpeg_temp.zip ^(!DL_SIZE! bytes^)
echo.

echo Extracting...
if exist "ffmpeg_temp" rmdir /s /q "ffmpeg_temp"
powershell -NoProfile -Command "try { Expand-Archive -Path 'ffmpeg_temp.zip' -DestinationPath 'ffmpeg_temp' -Force } catch { exit 1 }"
if errorlevel 1 (
    echo [ERROR] Extraction failed. The downloaded zip may be corrupt.
    echo Delete ffmpeg_temp.zip and rerun this script.
    echo.
    pause
    exit /b 1
)
echo [OK] Extracted.
echo.

echo Locating ffmpeg.exe / ffprobe.exe inside the extracted folder...
set FOUND=0
for /d %%D in ("ffmpeg_temp\ffmpeg-*") do (
    if exist "%%D\bin\ffmpeg.exe" (
        copy /y "%%D\bin\ffmpeg.exe" "ffmpeg.exe" >nul
        copy /y "%%D\bin\ffprobe.exe" "ffprobe.exe" >nul
        set FOUND=1
    )
)

if "!FOUND!"=="0" (
    echo [ERROR] Could not locate ffmpeg.exe inside the extracted archive.
    echo.
    pause
    exit /b 1
)

echo [OK] ffmpeg.exe and ffprobe.exe placed next to this script.
del /q "ffmpeg_temp.zip" >nul 2>nul
rmdir /s /q "ffmpeg_temp" >nul 2>nul
echo.

:rife_setup
REM --- Step 2b: optional AI interpolation engine (RIFE) ---
REM Installed INSIDE the venv only. A real, verified copy is bundled
REM in vendor\ for Python 3.11/win64 (no network needed); for any
REM other Python version it falls back to a normal pip install.
echo Setting up RIFE ^(optional AI interpolation engine^)...
python -c "import rife_ncnn_vulkan_python" >nul 2>nul
if not errorlevel 1 (
    echo [OK] rife_ncnn_vulkan_python already installed, skipping.
    goto :rife_models
)

REM Detect Python version inside the venv. With the venv ACTIVATED,
REM "python" already refers to the venv interpreter, so we don't need
REM the path-quoting trick that broke previously.
for /f "delims=" %%V in ('python -c "import sys; print(str(sys.version_info.major) + str(sys.version_info.minor))"') do set PYTAG=%%V
echo Detected Python %PYTAG% ^(inside the venv^).

set BUNDLED_WHEEL=
if exist "vendor\rife_ncnn_vulkan_python_tntwise-1.4.5-cp%PYTAG%-cp%PYTAG%-win_amd64.whl" (
    set BUNDLED_WHEEL=vendor\rife_ncnn_vulkan_python_tntwise-1.4.5-cp%PYTAG%-cp%PYTAG%-win_amd64.whl
)

if not "!BUNDLED_WHEEL!"=="" (
    echo Installing bundled copy ^(no download needed^): !BUNDLED_WHEEL!
    python -m pip install --quiet "!BUNDLED_WHEEL!"
    if errorlevel 1 (
        echo [WARNING] Installing the bundled wheel failed. Continuing
        echo without AI mode -- blend/mci still work normally.
        echo.
        goto :build
    )
) else (
    echo No bundled wheel matches Python %PYTAG% ^(bundled one is for 3.11^).
    echo Falling back to a normal install from PyPI...
    python -m pip install --quiet rife-ncnn-vulkan-python-tntwise
    if errorlevel 1 (
        echo [WARNING] PyPI install failed. Continuing without AI mode.
        echo.
        goto :build
    )
)
echo [OK] rife_ncnn_vulkan_python installed.

:rife_models
REM Copy the bundled model weights into the installed package's models folder.
for /f "delims=" %%P in ('python -c "import rife_ncnn_vulkan_python, os; print(os.path.dirname(rife_ncnn_vulkan_python.__file__))"') do set RIFE_PKG_DIR=%%P
if "!RIFE_PKG_DIR!"=="" (
    echo [WARNING] Could not locate the installed rife_ncnn_vulkan_python.
    echo Continuing without AI mode.
    echo.
    goto :build
)
if not exist "!RIFE_PKG_DIR!\models" mkdir "!RIFE_PKG_DIR!\models"
xcopy /y /e /i "vendor\rife_model\*" "!RIFE_PKG_DIR!\models" >nul
if exist "!RIFE_PKG_DIR!\models\rife-v4.6\flownet.bin" (
    echo [OK] RIFE model files installed -- AI interpolation mode available.
) else (
    echo [WARNING] Could not place RIFE model files. Continuing without AI mode.
)
echo.

:build
REM --- Step 3: build the .exe, using the venv's own pyinstaller ---
echo Building the application...
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
        echo Bundling RIFE ^(AI interpolation^) into the build.
    )
) else (
    echo RIFE not installed -- building without AI mode ^(blend/mci still included^).
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
echo  ffmpeg/ffprobe ^(and RIFE, if installed^) are
echo  bundled inside it. This single .exe file can
echo  be copied/shared on its own -- no other
echo  install steps needed for the people you
echo  send it to. The .venv folder here is just a
echo  build tool and does NOT need to be shipped.
echo ============================================
echo.
pause
