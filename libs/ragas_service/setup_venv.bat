@echo off
REM Setup script for creating a virtual environment and installing dependencies
REM for the RAG evaluation service (Windows)

echo ==========================================
echo Setting up virtual environment for RAG Evaluation Service
echo ==========================================

REM Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%venv"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    exit /b 1
)

REM Create virtual environment if it doesn't exist
if not exist "%VENV_DIR%" (
    echo Creating virtual environment at %VENV_DIR%...
    python -m venv "%VENV_DIR%"
    echo ✓ Virtual environment created
) else (
    echo Virtual environment already exists at %VENV_DIR%
)

REM Activate virtual environment
echo Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"

REM Upgrade pip
echo Upgrading pip...
python -m pip install --upgrade pip

REM Install requirements
echo Installing required packages...
pip install -r "%SCRIPT_DIR%requirements.txt"

echo.
echo ==========================================
echo Setup complete!
echo ==========================================
echo.
echo To activate the virtual environment, run:
echo   %VENV_DIR%\Scripts\activate.bat
echo.
echo To deactivate, run:
echo   deactivate
echo.

pause




