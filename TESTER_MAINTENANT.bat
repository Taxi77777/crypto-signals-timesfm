@echo off
title Test du bot - un cycle complet
cd /d "%~dp0"
call MES_IDENTIFIANTS.bat
set PAPER_MODE=true
set PYTHONUNBUFFERED=1
echo.
echo ================================================================
echo   TEST : un cycle complet, affiche a l'ecran.
echo   Le premier lancement telecharge le modele TimesFM (~1 min).
echo ================================================================
echo.
py -3.11 bot_once.py
echo.
echo ================================================================
pause
