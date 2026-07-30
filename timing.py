"""
╔══════════════════════════════════════════════════════════════════╗
║          INSTITUTIONAL HUNTER PRO — MEXC BOT                    ║
║          timing.py — Synchronisation exacte sur clôture M15     ║
║                                                                  ║
║  Objectif : Ne JAMAIS rater le train.                            ║
║                                                                  ║
║  STRATÉGIE D'ENTRÉE ANTICIPÉE :                                  ║
║  ┌─────────────────────────────────────────────────────────┐    ║
║  │  Bougie T-2 (Étape 1) : LVN détecté                    │    ║
║  │  Bougie T-1 (Étape 2) : Test confirmé                  │    ║
║  │  → IMMÉDIATEMENT placer un ORDRE LIMITE au niveau LVN  │    ║
║  │  → Si prix touche le LVN = ENTRÉ EN PREMIER            │    ║
║  │  Bougie T (Étape 3)   : Confirmation (déjà dedans !)   │    ║
║  └─────────────────────────────────────────────────────────┘    ║
╚══════════════════════════════════════════════════════════════════╝
"""
import time
import math
import logging
from datetime import datetime, timezone

log = logging.getLogger('IHP-TIMING')

# Durée d'une bougie M15 en secondes
M15_SECONDS = 15 * 60  # 900 secondes


def get_m15_close_time() -> float:
    """
    Retourne le timestamp UNIX de la prochaine clôture M15.
    Les M15 ferment à : 00:00, 00:15, 00:30, 00:45, 01:00...
    """
    now = time.time()
    # Arrondir au prochain multiple de 900s
    next_close = math.ceil(now / M15_SECONDS) * M15_SECONDS
    return next_close


def seconds_to_next_close() -> float:
    """Secondes restantes avant la prochaine clôture M15."""
    return get_m15_close_time() - time.time()


def is_candle_just_closed(tolerance_sec: float = 2.0) -> bool:
    """
    True si on est dans les X premières secondes après une clôture M15.
    C'est LE moment pour vérifier le signal Step 3.
    """
    now = time.time()
    last_close = math.floor(now / M15_SECONDS) * M15_SECONDS
    return (now - last_close) <= tolerance_sec


def is_approaching_close(seconds_before: float = 30.0) -> bool:
    """
    True si la clôture M15 est dans moins de X secondes.
    C'est le moment de placer les ordres limites anticipés.
    """
    return seconds_to_next_close() <= seconds_before


def wait_for_next_close(log_countdown: bool = True) -> None:
    """
    Bloque jusqu'à la prochaine clôture M15.
    Affiche un compte à rebours.
    """
    while True:
        remaining = seconds_to_next_close()
        if remaining <= 0.5:
            log.info("⏰ CLÔTURE M15 !")
            time.sleep(0.5)   # Laisser le temps à MEXC de mettre à jour
            return
        if log_countdown and remaining <= 10:
            log.info(f"  ⏱  Clôture dans {remaining:.1f}s...")
        time.sleep(0.2)


def m15_slot() -> str:
    """Retourne l'identifiant de la bougie M15 actuelle (ex: '09:15')."""
    now = datetime.now(timezone.utc)
    slot_min = (now.minute // 15) * 15
    return f"{now.hour:02d}:{slot_min:02d}"


class M15Timer:
    """
    Timer de précision pour la stratégie LVN.

    MODES :
    ─────────────────────────────────────────────────────────────
    MODE 1 — ORDRE LIMITE ANTICIPÉ (recommandé pour x40)
      Dès que Steps 1+2 confirmés → placer limite au LVN
      → Entré AVANT la clôture Step 3
      → Pas de retard, prix exact

    MODE 2 — ORDRE MARCHÉ À LA CLÔTURE
      Attendre la clôture Step 3, puis market order
      → Plus sûr mais 1-2s de délai après clôture
    ─────────────────────────────────────────────────────────────
    """

    def __init__(self):
        self._last_slot    = ""
        self._new_candle   = False

    def tick(self) -> dict:
        """
        Appeler à chaque itération principale du bot.
        Retourne un dict avec les infos de timing.
        """
        now       = time.time()
        remaining = seconds_to_next_close()
        slot      = m15_slot()

        # Détecter nouvelle bougie
        new_candle = (slot != self._last_slot and
                      (now % M15_SECONDS) < 3.0)
        if new_candle:
            self._last_slot = slot
            log.info(f"🕯️  Nouvelle bougie M15 : {slot}")

        return {
            'slot':               slot,
            'seconds_remaining':  remaining,
            'new_candle':         new_candle,
            'just_closed':        is_candle_just_closed(2.0),
            'approaching_close':  is_approaching_close(30.0),  # Dans 30s
            'pre_entry_window':   is_approaching_close(45.0),  # Dans 45s = fenêtre limite
            'pct_complete':       1.0 - (remaining / M15_SECONDS),
        }
