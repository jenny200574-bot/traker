@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Crea o repara el entorno Python DENTRO de esta carpeta.
REM Asi la app viaja con su memoria y se reinstala sola en otro PC.

set "VENV_DIR=%~dp0venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

echo.
echo ================================================
echo   Dropi Tracker - Entorno portable
echo ================================================
echo.

if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import customtkinter,requests,apscheduler,pandas,openpyxl,matplotlib,PIL" >nul 2>&1
    if not errorlevel 1 (
        echo Entorno local listo.
        exit /b 0
    )
    echo El entorno de otro PC no sirve aqui. Recreando...
    rmdir /s /q "%VENV_DIR%" 2>nul
)

call :find_python
if not defined PY (
    echo ERROR: Esta PC no tiene Python 3.10 o superior.
    echo Instala Python desde https://www.python.org/downloads/
    echo Marca la opcion "Add python.exe to PATH".
    echo Luego vuelve a ejecutar install.bat o run.bat.
    exit /b 1
)

echo Usando: %PY%
%PY% --version
echo.
echo Creando entorno local en:
echo   %VENV_DIR%
echo.

%PY% -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo ERROR: No se pudo crear el entorno local.
    exit /b 1
)

echo Instalando dependencias...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo ERROR: Fallo la instalacion de dependencias.
    exit /b 1
)

echo.
echo Entorno portable listo.
exit /b 0

:find_python
set "PY="
REM Preferir 3.10 (version original de esta app), luego 3.12 / 3.11, luego cualquier 3.10+
py -3.10 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3.10"
    goto :eof
)
py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3.12"
    goto :eof
)
py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3.11"
    goto :eof
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    goto :eof
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) and 'WindowsApps' not in sys.executable else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :eof
)
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) and 'WindowsApps' not in sys.executable else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=python3"
    goto :eof
)
goto :eof
