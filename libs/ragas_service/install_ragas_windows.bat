@echo off
REM Script to help install ragas on Windows
REM This script checks for build tools and provides installation guidance

echo ========================================
echo Ragas Installation Helper for Windows
echo ========================================
echo.

REM Check if Visual C++ Build Tools are installed
echo Checking for Microsoft Visual C++ Build Tools...
where cl.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Visual C++ Build Tools found!
    echo.
    echo Attempting to install ragas...
    pip install ragas
    if %ERRORLEVEL% EQU 0 (
        echo.
        echo ========================================
        echo SUCCESS! Ragas has been installed.
        echo ========================================
        python -c "import ragas; print('Verification: Ragas imported successfully!')"
    ) else (
        echo.
        echo [ERROR] Installation failed. Please check the error messages above.
    )
) else (
    echo [WARNING] Visual C++ Build Tools not found.
    echo.
    echo ========================================
    echo INSTALLATION REQUIRED
    echo ========================================
    echo.
    echo Ragas requires Microsoft Visual C++ Build Tools to install.
    echo.
    echo Please follow these steps:
    echo.
    echo 1. Download Microsoft C++ Build Tools from:
    echo    https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo.
    echo 2. Run the installer and select:
    echo    - "C++ build tools" workload
    echo    - "Windows 10/11 SDK" (make sure it's checked)
    echo.
    echo 3. After installation, RESTART your terminal/command prompt
    echo.
    echo 4. Run this script again or run: pip install ragas
    echo.
    echo ========================================
    echo.
    echo Alternative: You can also use Conda or WSL to install ragas.
    echo See INSTALL_WINDOWS.md for more options.
    echo.
    pause
)


