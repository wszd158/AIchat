@echo off
chcp 65001 >nul

echo Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH.
    echo Please install Python 3.8 or higher from https://www.python.org/downloads/
    powershell -Command "Read-Host 'Press Enter to exit...'"
    exit /b 1
)

echo Checking required packages...
python -c "import streamlit" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing required packages...
    pip install streamlit openai httpx
    if %errorlevel% neq 0 (
        echo Failed to install required packages. Please check your internet connection and try again.
        powershell -Command "Read-Host 'Press Enter to exit...'"
        exit /b 1
    )
)

echo Starting AI Chat Arena...
streamlit run chat_arena.py

powershell -Command "Read-Host 'Press Enter to exit...'"