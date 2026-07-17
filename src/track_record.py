"""
src/track_record.py — Carnet de notes des 5 IA (pondération dynamique)
Chaque IA est notée sur ses prédictions passées ; son vote est pondéré
par son taux de réussite réel (par paire, sinon global).
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TRACK_FILE   = "track_record.json"
PENDING_FILE = "pending_predictions.json"
DEFAULT_WEIGHT = 3   # poids d'une IA sans historique suffisant

_track_cache = None


def load_track(force: bool = False) -> dict:
    global _track_cache
    if _track_cache is not None and not force:
        return _track_cache
    try:
        with open(TRACK_FILE, encoding="utf-8") as f:
            _track_cache = json.load(f)
    except Exception:
        _track_cache = {"models": {}}
    return _track_cache


def save_track(track: dict):
    global _track_cache
    track["updated"] = datetime.now(timezone.utc).isoformat()
    with open(TRACK_FILE, "w", encoding="utf-8") as f:
        json.dump(track, f, indent=2)
    _track_cache = track


def record_result(track: dict, model: str, symbol: str, correct: bool):
    """Enregistre le resultat d'une prediction BUY/SELL verifiee."""
    m = track.setdefault("models", {}).setdefault(
        model, {"global": {"wins": 0, "total": 0}, "per_pair": {}}
    )
    for bucket in (m["global"], m["per_pair"].setdefault(symbol, {"wins": 0, "total": 0})):
        bucket["total"] += 1
        if correct:
            bucket["wins"] += 1


def get_weight(track: dict, model: str, symbol: str) -> int:
    """
    Poids du vote selon le taux de reussite historique :
      >=60% -> 5 | >=55% -> 4 | >=52% -> 3 | sinon -> 2 (plancher, jamais 0)
    Par paire si >=30 predictions, sinon global si >=100, sinon poids par defaut.
    """
    m = track.get("models", {}).get(model)
    if not m:
        return DEFAULT_WEIGHT
    stats = None
    pp = m.get("per_pair", {}).get(symbol)
    if pp and pp.get("total", 0) >= 30:
        stats = pp
    elif m.get("global", {}).get("total", 0) >= 100:
        stats = m["global"]
    if not stats:
        return DEFAULT_WEIGHT
    acc = stats["wins"] / stats["total"]
    # Plancher a 2 : les 5 IA votent TOUJOURS (couplees au Supertrend).
    # Le carnet ne fait qu'AMPLIFIER les meilleures, jamais bacillonner.
    if acc >= 0.60: return 5
    if acc >= 0.55: return 4
    if acc >= 0.52: return 3
    return 2


def accuracy_summary(track: dict) -> str:
    lines = []
    for model, m in sorted(track.get("models", {}).items()):
        g = m.get("global", {})
        if g.get("total", 0) > 0:
            lines.append(f"{model}: {g['wins']}/{g['total']} ({g['wins']/g['total']*100:.1f}%)")
    return " | ".join(lines) if lines else "aucun historique"


# ── Predictions en attente de verification (apprentissage continu) ────────────

def load_pending() -> list:
    try:
        with open(PENDING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_pending(pending: list):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending[-2000:], f)   # borne la taille
