@echo off
REM  Open the whole thing: one server and two chat windows.
REM
REM      run.bat            two windows, aya and keisha
REM      run.bat fresh      the same, but wipe the database first
REM
REM  Close the server window to stop everything. The clients will notice and
REM  sit in RETRYING until it comes back, which is worth watching.

REM change the current directory to the run.bat directory 
cd /d "%~dp0"   

if not exist ".venv\Scripts\python.exe" (
  echo   The virtual environment is missing. Run this first:
  echo.
  echo     python -m venv .venv
  echo     .venv\Scripts\activate
  echo     pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

if /i "%~1"=="fresh" (
  echo   Starting from an empty database.
  if exist im.db del /q im.db
  if exist im.db-wal del /q im.db-wal
  if exist im.db-shm del /q im.db-shm
  if exist keys rmdir /s /q keys
)

echo   Starting the server...
start "Semaphore server" cmd /k ".venv\Scripts\python.exe -m im.server --db im.db"

REM  Give it a moment to bind the port before anybody dials it.
timeout /t 2 /nobreak >nul

echo   Opening two chat windows...
REM  pythonw, so each window has no console behind it.
start "" ".venv\Scripts\pythonw.exe" -m im.client --view tk --user aya --password demo --register
timeout /t 1 /nobreak >nul
start "" ".venv\Scripts\pythonw.exe" -m im.client --view tk --user keisha --password demo --register

echo.
echo   Two windows should be open, signed in as aya and keisha.
echo   In one of them: New Message, type the other name, then talk.
echo.
echo   To see what the server actually stored:
echo       .venv\Scripts\python.exe -m demo.peek im.db
echo.
