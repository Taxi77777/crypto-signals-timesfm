@echo off
cd /d "%~dp0"
call MES_IDENTIFIANTS.bat
set PAPER_MODE=true
set PYTHONUNBUFFERED=1
echo ---------- %DATE% %TIME% ---------- >> bot_local.log
py -3.11 bot_once.py >> bot_local.log 2>&1
