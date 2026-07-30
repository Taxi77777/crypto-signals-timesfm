@echo off
echo ╔══════════════════════════════════════════════╗
echo ║   INSTITUTIONAL HUNTER PRO — MEXC BOT       ║
echo ║   Stratégie : 15m LVN + Fisher Crossover    ║
echo ╚══════════════════════════════════════════════╝
echo.

:: Vérifier Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python non installé. Télécharger : https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Installer les dépendances
echo [1/3] Installation des dépendances...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [ERREUR] Impossible d'installer les dépendances.
    pause
    exit /b 1
)

:: Vérifier les clés API
python -c "from config import MEXC_API_KEY, MEXC_SECRET_KEY; print('CLE OK' if MEXC_API_KEY and MEXC_SECRET_KEY else 'MODE LECTURE SEULE (sans clé API)')"

echo.
echo [2/3] Vérification de la connexion MEXC...
python -c "import mexc_api as api; t=api.get_ticker('BTC_USDT'); print(f'BTC: {t.get(\"last\",\"?\")}'  if t else 'Connexion OK (pas de data)')"

echo.
echo [3/3] Démarrage du bot...
echo.
echo  Appuyez sur CTRL+C pour arrêter proprement.
echo.

python bot.py
pause
