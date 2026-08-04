@echo off
echo ╔══════════════════════════════════════════════════════════════╗
echo ║   INSTITUTIONAL HUNTER PRO v3.0 — MULTI-EXCHANGE OBI BOT    ║
║   Échanges : MEXC, Bitget, Bybit, OKX, Binance, Kraken       ║
echo ╚══════════════════════════════════════════════════════════════╝
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

echo.
echo [2/3] Test des APIs des 6 échanges...
python -c "from exchanges import get_multi_exchange_obi; res=get_multi_exchange_obi('BTC'); print(f'Consensus BTC: {res[\"consensus_pct\"]}% | {res[\"exchanges_ok\"]}/6 échanges connectés')"

echo.
echo [3/3] Démarrage du bot Multi-Échange...
echo.
echo  Appuyez sur CTRL+C pour arrêter le bot.
echo.

python bot.py
pause
