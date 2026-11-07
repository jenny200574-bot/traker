@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================
echo   Dropi Tracker - Instalador portable
echo ================================================
echo.
echo Instala las librerias DENTRO de esta carpeta.
echo La memoria (dropi_tracker.db, settings.json, backups)
echo no se toca: viaja con la carpeta a cualquier PC.
echo.

call "%~dp0entorno.bat"
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

echo.
echo ================================================
echo   Listo. Ejecuta run.bat para abrir la app.
echo ================================================
echo.
if /i not "%1"=="nopause" pause
exit /b 0
