@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Arranque portable: usa el Python de esta carpeta.
REM Si copiaste el proyecto a otro PC, repara el entorno solo.

if not exist "%~dp0venv\Scripts\python.exe" goto SETUP
"%~dp0venv\Scripts\python.exe" -c "import customtkinter,requests,apscheduler,pandas,openpyxl,matplotlib,PIL" >nul 2>&1
if errorlevel 1 goto SETUP
goto RUN

:SETUP
echo Primera vez en este PC, o el entorno se movio. Preparando...
echo.
call "%~dp0entorno.bat"
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

:RUN
"%~dp0venv\Scripts\python.exe" "%~dp0app.py"
if errorlevel 1 (
    echo.
    echo ERROR: La aplicacion cerro con un error.
    echo Si es la primera vez, ejecuta install.bat.
    pause
)
