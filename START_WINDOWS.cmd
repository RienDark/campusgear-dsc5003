@echo off
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 or newer must be installed and added to PATH.
  pause
  exit /b 1
)
echo CampusGear - local synthetic-data demonstration
echo Open http://127.0.0.1:8765 after the server starts.
echo Press Ctrl+C in this window to stop.
python app.py --port 8765 --db data/campusgear.db
pause
