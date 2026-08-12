@echo off
title Installation - Institutional Hunter Pro (local)
cd /d "%~dp0"
set LOG=installation.log
echo ================================================== > %LOG%
echo DEBUT INSTALLATION %DATE% %TIME% >> %LOG%
echo ================================================== >> %LOG%

echo.
echo ================================================================
echo   ETAPE 1/3 : verification de Python
echo ================================================================
echo --- ETAPE 1 : python --- >> %LOG%
set PY=
py -3.11 --version >> %LOG% 2>&1 && set PY=py -3.11
if not defined PY py -3.12 --version >> %LOG% 2>&1 && set PY=py -3.12
if not defined PY py -3.13 --version >> %LOG% 2>&1 && set PY=py -3.13
if not defined PY python --version >> %LOG% 2>&1 && set PY=python
if not defined PY (
  echo   Python introuvable. >> %LOG%
  echo   Python est introuvable. Installe-le depuis python.org
  echo ECHEC ETAPE 1 >> %LOG%
  pause
  exit /b 1
)
echo   Interpreteur retenu : %PY%
echo   Interpreteur retenu : %PY% >> %LOG%

echo.
echo ================================================================
echo   ETAPE 2/3 : installation des dependances
echo   PyTorch fait environ 200 Mo, compte 5 a 10 minutes.
echo   Ne ferme pas cette fenetre.
echo ================================================================
echo --- ETAPE 2 : dependances --- >> %LOG%
%PY% -m pip install --upgrade pip >> %LOG% 2>&1
%PY% -m pip install -r requirements.txt >> %LOG% 2>&1
if errorlevel 1 (
  echo   ECHEC : voir installation.log
  echo ECHEC ETAPE 2 >> %LOG%
  pause
  exit /b 1
)
echo   Dependances installees.
echo   OK ETAPE 2 >> %LOG%

echo.
echo ================================================================
echo   ETAPE 3/3 : tache planifiee, toutes les 5 minutes
echo ================================================================
echo --- ETAPE 3 : tache planifiee --- >> %LOG%
schtasks /Delete /TN "IHP Crypto Signals" /F >> %LOG% 2>&1
schtasks /Create /TN "IHP Crypto Signals" /SC MINUTE /MO 5 /F /TR "wscript.exe \"%~dp0LANCER_SILENCIEUX.vbs\"" >> %LOG% 2>&1
if errorlevel 1 (
  echo   Creation de la tache impossible : voir installation.log
  echo ECHEC ETAPE 3 >> %LOG%
  pause
  exit /b 1
)
schtasks /Query /TN "IHP Crypto Signals" >> %LOG% 2>&1
echo   Tache planifiee creee.
echo INSTALLATION TERMINEE AVEC SUCCES >> %LOG%

echo.
echo ================================================================
echo   INSTALLATION TERMINEE
echo   Remplis MES_IDENTIFIANTS.bat puis lance TESTER_MAINTENANT.bat
echo ================================================================
pause
