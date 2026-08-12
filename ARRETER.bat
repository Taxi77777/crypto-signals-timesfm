@echo off
title Arret du bot
schtasks /Delete /TN "IHP Crypto Signals" /F
echo.
echo Tache planifiee supprimee. Le bot ne tournera plus.
pause
