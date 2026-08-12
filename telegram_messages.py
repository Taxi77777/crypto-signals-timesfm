"""
╔══════════════════════════════════════════════════════════════════╗
║  telegram_messages.py — MESSAGES TELEGRAM LISIBLES PAR TOUS      ║
║                                                                   ║
║  Remplace les messages techniques par des explications en         ║
║  francais clair, avec la traduction en argent reel.               ║
║                                                                   ║
║  Installation : poser ce fichier a la racine du repo, a cote de   ║
║  bot_once.py, puis dans le code qui envoie les alertes :          ║
║                                                                   ║
║      from telegram_messages import msg_signal, msg_cloture        ║
║      send_telegram(msg_signal(signal))                            ║
║      send_telegram(msg_cloture(trade_ferme))                      ║
╚══════════════════════════════════════════════════════════════════╝
"""
from config import (
    LEVERAGE, TAKER_FEE_PCT, TRAILING_TRIGGER, TRAILING_STEP,
    MAX_HOLD_HOURS, TIMESFM_MIN_CONFIDENCE, MIN_CONSENSUS_PCT,
    PAPER_MODE,
)

# Cout aller-retour des frais, exprime en % de la marge engagee
FRAIS_SUR_MARGE_PCT = TAKER_FEE_PCT * 2.0 * LEVERAGE


def _pct(depuis: float, vers: float) -> float:
    """Variation en pourcentage entre deux prix."""
    if not depuis:
        return 0.0
    return (vers - depuis) / depuis * 100.0


def _duree(minutes: int) -> str:
    """1156 -> '19 h 16 min'"""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} h {m} min"
    if h:
        return f"{h} h"
    return f"{m} min"


def _prix(v: float) -> str:
    """Affiche un prix sans zeros inutiles, quelle que soit la crypto."""
    if v >= 1000:
        return f"{v:,.2f}".replace(",", " ")
    if v >= 1:
        return f"{v:.4f}"
    return f"{v:.6f}"


# ══════════════════════════════════════════════════════════════════
#  MESSAGE 1 — NOUVEAU SIGNAL
# ══════════════════════════════════════════════════════════════════
def msg_signal(signal, marge_usdt: float = None) -> str:
    """
    `signal` doit exposer : symbol, direction ('BUY'/'SELL'), entry, tp,
    et si possible liq, consensus_pct, n_exchanges, timesfm_direction,
    timesfm_confidence, volume_ratio.
    """
    sens        = "ACHAT" if signal.direction == "BUY" else "VENTE"
    icone       = "🟢" if signal.direction == "BUY" else "🔴"
    attendu     = "monter" if signal.direction == "BUY" else "baisser"
    paire       = str(signal.symbol).replace("_", "/")

    entree = float(signal.entry)
    tp     = float(getattr(signal, "tp", 0) or 0)
    liq    = float(getattr(signal, "liq", 0) or 0)

    # Si le prix de liquidation n'est pas fourni, on le reconstruit
    if liq <= 0:
        adverse = (100.0 / max(LEVERAGE, 1)) * 0.90 / 100.0
        liq = entree * (1 - adverse) if signal.direction == "BUY" else entree * (1 + adverse)

    gain_pct  = abs(_pct(entree, tp))
    perte_pct = abs(_pct(entree, liq))

    # Traduction en pourcentage de la marge engagee (effet du levier)
    gain_marge  = gain_pct * LEVERAGE - FRAIS_SUR_MARGE_PCT
    perte_marge = 100.0  # une liquidation efface la marge

    lignes = []
    lignes.append(f"{icone} SIGNAL D'{sens} — {paire}")
    if PAPER_MODE:
        lignes.append("SIMULATION — aucun argent n'est engage.")
    lignes.append("")
    lignes.append(f"Le bot estime que le prix va {attendu}.")
    lignes.append("")
    lignes.append("PRIX")
    lignes.append(f"  Entree ................ {_prix(entree)}")
    lignes.append(f"  Objectif de gain ...... {_prix(tp)}   ({gain_pct:+.2f} %)")
    lignes.append(f"  Perte totale a ........ {_prix(liq)}   ({-perte_pct:.2f} %)")
    lignes.append("")
    lignes.append(f"CE QUE CA DONNE AVEC LE LEVIER x{LEVERAGE}")
    if marge_usdt:
        lignes.append(f"  Marge engagee ......... {marge_usdt:.2f} USDT")
        lignes.append(f"  Si objectif atteint ... +{gain_marge:.0f} % "
                      f"(+{marge_usdt * gain_marge / 100:.2f} USDT)")
        lignes.append(f"  Si perte totale ....... -100 % "
                      f"(-{marge_usdt:.2f} USDT)")
    else:
        lignes.append(f"  Si objectif atteint ... +{gain_marge:.0f} % de la marge")
        lignes.append(f"  Si perte totale ....... -100 % de la marge")
    lignes.append(f"  (le levier multiplie le mouvement du prix par {LEVERAGE})")
    lignes.append("")
    lignes.append("POURQUOI CE SIGNAL")

    cons = float(getattr(signal, "consensus_pct", 0) or 0)
    nex  = getattr(signal, "n_exchanges", None)
    if cons:
        detail_ex = f" sur {nex} exchanges interroges" if nex else ""
        lignes.append(f"  - Les carnets d'ordres sont d'accord a {cons:.0f} %{detail_ex}")
        lignes.append(f"    (il en faut au moins {MIN_CONSENSUS_PCT:.0f} %)")

    tconf = getattr(signal, "timesfm_confidence", None)
    tdir  = getattr(signal, "timesfm_direction", None)
    if tconf is not None:
        conf_pct = tconf * 100 if tconf <= 1 else tconf
        sens_ia = "hausse" if str(tdir).upper() == "BUY" else "baisse"
        lignes.append(f"  - L'IA TimesFM prevoit une {sens_ia}, confiance {conf_pct:.0f} %")
        lignes.append(f"    (minimum exige : {TIMESFM_MIN_CONFIDENCE * 100:.0f} %)")

    vr = getattr(signal, "volume_ratio", None)
    if vr:
        lignes.append(f"  - Volume de la bougie : {vr:.2f}x sa moyenne "
                      f"({'suffisant' if vr >= 1 else 'faible'})")

    lignes.append("")
    lignes.append("CE QUI PEUT FERMER LA POSITION")
    lignes.append(f"  - L'objectif de gain est touche")
    lignes.append(f"  - Le stop suiveur : apres +{TRAILING_TRIGGER} % de gain, le bot")
    lignes.append(f"    verrouille le profit et sort si le prix recule de {TRAILING_STEP} %")
    lignes.append(f"  - La perte totale est atteinte")
    lignes.append(f"  - {MAX_HOLD_HOURS:.0f} h se sont ecoulees sans rien toucher")

    return "\n".join(lignes)


# ══════════════════════════════════════════════════════════════════
#  MESSAGE 2 — POSITION FERMEE
# ══════════════════════════════════════════════════════════════════
_RAISONS = {
    "TAKE_PROFIT": (
        "OBJECTIF ATTEINT",
        "Le prix a touche l'objectif de gain. C'est le meilleur scenario.",
    ),
    "TRAILING_STOP": (
        "GAIN SECURISE",
        "Le prix est monte au-dela de +{trigger} %, puis a recule de {step} %. "
        "Le bot a pris le gain au lieu de le laisser repartir.",
    ),
    "LIQUIDATION": (
        "PERTE TOTALE",
        "Le prix est alle contre la position jusqu'au seuil de liquidation. "
        "La marge engagee est perdue en totalite. C'est le risque du levier "
        "sans stop-loss.",
    ),
    "STOP_LOSS": (
        "STOP DE PROTECTION",
        "Le stop-loss a ete touche, la perte a ete limitee.",
    ),
    "EXPIRATION": (
        "DELAI ECOULE",
        "Ni l'objectif ni le seuil de perte n'ont ete touches en {hold:.0f} h. "
        "Le bot ferme la position pour ne pas la garder indefiniment.",
    ),
}


def msg_cloture(trade: dict) -> str:
    """
    `trade` = dictionnaire produit par paper_engine.update_paper_positions().
    """
    sens   = "achat" if trade["direction"] == "BUY" else "vente"
    paire  = str(trade["symbol"]).replace("_", "/")
    pnl    = float(trade["pnl_usdt"])
    marge  = float(trade.get("pnl_on_margin_pct", 0))
    gagne  = pnl > 0
    icone  = "✅" if gagne else "❌"

    titre, explication = _RAISONS.get(
        trade["exit_reason"], (trade["exit_reason"], "")
    )
    explication = explication.format(
        trigger=TRAILING_TRIGGER, step=TRAILING_STEP, hold=MAX_HOLD_HOURS
    )

    lignes = []
    lignes.append(f"{icone} POSITION FERMEE — {paire} ({sens})")
    if PAPER_MODE:
        lignes.append("SIMULATION — aucun argent n'a ete engage.")
    lignes.append("")
    lignes.append(f"RAISON : {titre}")
    if explication:
        lignes.append(f"  {explication}")
    lignes.append("")
    lignes.append("RESULTAT")
    lignes.append(f"  Le prix a bouge de ....... {trade['move_pct']:+.2f} %")
    lignes.append(f"  Gain / perte ............. {pnl:+.4f} USDT")
    lignes.append(f"  Soit sur la marge ........ {marge:+.1f} %")
    lignes.append(f"  Duree .................... {_duree(trade['duration_min'])}")
    lignes.append("")
    lignes.append("POUR COMPRENDRE LE CALCUL")
    lignes.append(f"  Le prix a bouge de {trade['move_pct']:+.2f} %, "
                  f"le levier x{LEVERAGE} multiplie par {LEVERAGE}")
    lignes.append(f"  = {trade['move_pct'] * LEVERAGE:+.1f} % de la marge")
    lignes.append(f"  moins {FRAIS_SUR_MARGE_PCT:.1f} % de frais aller-retour")
    lignes.append(f"  = {marge:+.1f} % au final")

    return "\n".join(lignes)


# ══════════════════════════════════════════════════════════════════
#  MESSAGE 3 — BILAN PERIODIQUE (a envoyer 1x par jour)
# ══════════════════════════════════════════════════════════════════
def msg_bilan(stats: dict) -> str:
    """`stats` = sortie de paper_engine.compute_stats()."""
    n = stats.get("n_trades", 0)
    if n == 0:
        return ("BILAN — aucune position fermee pour l'instant.\n"
                f"Positions en cours : {stats.get('n_open', 0)}")

    pf = stats.get("profit_factor")
    lignes = []
    lignes.append(f"BILAN — {n} positions fermees")
    lignes.append("")
    lignes.append(f"  Reussite ......... {stats['win_rate']:.0f} % "
                  f"({stats['n_wins']} gagnees / {stats['n_losses']} perdues)")
    lignes.append(f"  Resultat total ... {stats['total_pnl']:+.4f} USDT")
    lignes.append(f"  Par trade ........ {stats['expectancy']:+.4f} USDT en moyenne")
    if pf:
        juge = "rentable" if pf > 1 else "perdant"
        lignes.append(f"  Profit factor .... {pf:.2f}  ({juge})")
        lignes.append(f"    (gains totaux divises par pertes totales ;")
        lignes.append(f"     au-dessus de 1 = la strategie gagne de l'argent)")
    lignes.append(f"  Pire creux ....... -{stats['max_drawdown']:.4f} USDT")
    lignes.append("")
    lignes.append("  COMMENT LES POSITIONS SE SONT FERMEES")
    trad = {
        "TAKE_PROFIT": "objectif atteint",
        "TRAILING_STOP": "gain securise",
        "LIQUIDATION": "perte totale",
        "STOP_LOSS": "stop de protection",
        "EXPIRATION": "delai de 24 h ecoule",
    }
    for raison, nb in sorted(stats.get("exit_reasons", {}).items(),
                             key=lambda x: -x[1]):
        lignes.append(f"    {trad.get(raison, raison)} : {nb}")
    lignes.append("")
    lignes.append(f"  Positions encore ouvertes : {stats.get('n_open', 0)}")

    if n < 30:
        lignes.append("")
        lignes.append(f"  ⚠️ {n} trades, c'est trop peu pour conclure quoi que ce soit.")
        lignes.append("     Il en faut au moins 30, idealement 100.")

    return "\n".join(lignes)
