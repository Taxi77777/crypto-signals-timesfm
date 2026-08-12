@echo off
title Rapport de calibration
cd /d "%~dp0"
py -3.11 signal_quality.py
echo.
pause
