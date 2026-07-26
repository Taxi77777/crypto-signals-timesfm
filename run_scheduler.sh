#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Ordonnanceur du bot — boucle alignée sur la CLÔTURE des bougies 15m.
#
# Pourquoi une boucle et pas cron : le Volume Climax lit la dernière bougie
# CLÔTURÉE. Se réveiller à hh:00 / hh:15 / hh:30 / hh:45 pile signifie lire une
# bougie qui vient de se fermer, avec des données complètes. On ajoute un léger
# décalage pour laisser à yfinance le temps de publier la bougie.
#
# Un verrou empêche deux exécutions simultanées : indispensable, le bot ouvre
# des positions réelles et deux instances pourraient doubler un trade.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

LOCK=/tmp/crypto-signals.lock
OFFSET=${CANDLE_CLOSE_OFFSET_SEC:-20}   # secondes après la clôture de bougie

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "Ordonnanceur démarré — exécution à chaque clôture 15m (+${OFFSET}s)"

while true; do
    # Secondes jusqu'au prochain multiple de 15 minutes
    now_epoch=$(date +%s)
    next=$(( (now_epoch / 900 + 1) * 900 + OFFSET ))
    sleep_for=$(( next - now_epoch ))
    log "Prochaine exécution dans ${sleep_for}s (à $(date -d "@${next}" '+%H:%M:%S'))"
    sleep "$sleep_for"

    if [ -e "$LOCK" ]; then
        log "⚠️ Exécution précédente encore en cours (verrou présent) → créneau sauté"
        continue
    fi

    touch "$LOCK"
    log "▶ Lancement de run_once.py"
    start=$(date +%s)
    python /app/run_once.py 2>&1
    rc=$?
    dur=$(( $(date +%s) - start ))
    rm -f "$LOCK"

    if [ $rc -eq 0 ]; then
        log "✅ Terminé en ${dur}s"
    else
        log "❌ Échec (code $rc) après ${dur}s — voir logs/signals.log"
    fi
done
