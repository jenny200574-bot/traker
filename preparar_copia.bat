@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================
echo   Dropi Tracker - Preparar copia a otro PC
echo ================================================
echo.
echo Cierra la app si esta abierta, luego continua.
echo.

if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" -c "import database as db; db.checkpoint_db(); print('Memoria consolidada en dropi_tracker.db')"
) else (
    python -c "import database as db; db.checkpoint_db(); print('Memoria consolidada en dropi_tracker.db')" 2>nul
)

echo.
echo Copia TODA esta carpeta (USB, OneDrive o zip):
echo   %~dp0
echo.
echo Lleva si o si:
echo   - dropi_tracker.db          (productos, bodegas, historial)
echo   - dropi_tracker.db-wal      (si existe, no lo borres)
echo   - dropi_tracker.db-shm      (si existe, no lo borres)
echo   - settings.json             (login y horarios)
echo   - backups_historial\        (historial antiguo)
echo   - todos los .py, .bat y requirements.txt
echo.
echo NO hace falta copiar:
echo   - venv\
echo   - __pycache__\
echo   - scratch\
echo.
echo En el otro PC:
echo   1. Instala Python 3.10+ (Add to PATH) si no lo tiene
echo   2. Doble clic en run.bat  (instala el entorno solo)
echo.
pause
